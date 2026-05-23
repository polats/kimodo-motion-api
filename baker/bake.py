"""
Headless Blender baker: kimodo SMPL-X motion JSON -> citizen-compatible FBX clip.

Usage:
    blender --background --python bake.py -- \
        --citizen-fbx /path/to/citizen_REF.fbx \
        --clip /path/to/<clip>.json \
        --out /path/to/out.fbx

The output FBX contains only the citizen armature with baked per-frame bone
rotations on the bones listed in CITIZEN_MAPPING. Twist/helper/IK bones are
left at bind so the engine's AnimConstraintList drives them at runtime.
"""

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Quaternion, Vector


# kimodo SMPL-X joint -> citizen bone. Mirrors kimodo/web/src/rigs.js citizenMapping().
CITIZEN_MAPPING = {
    "pelvis": "pelvis",
    "left_hip": "leg_upper_L", "right_hip": "leg_upper_R",
    "spine1": "spine_0",
    "left_knee": "leg_lower_L", "right_knee": "leg_lower_R",
    "spine2": "spine_1",
    "left_ankle": "ankle_L", "right_ankle": "ankle_R",
    "spine3": "spine_2",
    "left_foot": "ball_L", "right_foot": "ball_R",
    "neck": "neck_0",
    "left_collar": "clavicle_L", "right_collar": "clavicle_R",
    "head": "head",
    "left_shoulder": "arm_upper_L", "right_shoulder": "arm_upper_R",
    "left_elbow": "arm_lower_L", "right_elbow": "arm_lower_R",
    "left_wrist": "hand_L", "right_wrist": "hand_R",
}

# SMPL-X body height in meters; pelvis translation is scaled by char_height/SMPLX_HEIGHT
# to handle rigs of different scale (citizen FBX is in cm so we'll detect at load).
SMPLX_HEIGHT_M = 1.66


def parse_args():
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []
    p = argparse.ArgumentParser()
    p.add_argument("--citizen-fbx", required=True, type=Path)
    p.add_argument("--clip", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--clip-name", default=None,
                   help="Take/action name in the FBX. Defaults to clip filename stem.")
    return p.parse_args(argv)


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_citizen(fbx_path: Path):
    bpy.ops.import_scene.fbx(filepath=str(fbx_path), use_anim=False)
    armatures = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    if not armatures:
        raise RuntimeError(f"No armature in {fbx_path}")
    if len(armatures) > 1:
        print(f"[bake] WARN: {len(armatures)} armatures, using first ({armatures[0].name})")
    return armatures[0]


def delete_meshes(armature):
    for o in list(bpy.context.scene.objects):
        if o.type == "MESH":
            bpy.data.objects.remove(o, do_unlink=True)


def capture_rest_world_quats(armature):
    """Map bone name -> rest-pose world-space quaternion (Blender Quaternion w,x,y,z)."""
    out = {}
    world = armature.matrix_world
    for bone in armature.data.bones:
        # bone.matrix_local is the rest pose in armature space.
        m = world @ bone.matrix_local
        out[bone.name] = m.to_quaternion()
    return out


def kimodo_quat_to_blender(xyzw):
    """kimodo global_quats_xyzw -> Blender Quaternion(w, x, y, z)."""
    return Quaternion((xyzw[3], xyzw[0], xyzw[1], xyzw[2]))


def bake(args):
    print(f"[bake] reading {args.clip}")
    clip = json.loads(args.clip.read_text())
    fps = clip["fps"]
    num_frames = clip["num_frames"]
    bone_names = clip["bone_names"]
    g_quats = clip["global_quats_xyzw"]  # [T][J][4]
    root_positions = clip.get("root_positions")  # [T][3] meters
    print(f"[bake] clip: fps={fps} frames={num_frames} joints={len(bone_names)}")

    reset_scene()
    print(f"[bake] importing {args.citizen_fbx}")
    armature = import_citizen(args.citizen_fbx)
    delete_meshes(armature)

    # Force evaluation so matrix_world is current.
    bpy.context.view_layer.update()

    rest_world = capture_rest_world_quats(armature)

    # Figure out scale: kimodo positions are meters. Detect citizen armature
    # height in its source units (cm) and compute stride scale.
    pelvis_bone = armature.data.bones.get(CITIZEN_MAPPING["pelvis"])
    if pelvis_bone is None:
        raise RuntimeError("No pelvis bone in armature")
    head_bone = armature.data.bones.get(CITIZEN_MAPPING["head"])
    if head_bone is None:
        raise RuntimeError("No head bone in armature")
    pelvis_h = (armature.matrix_world @ pelvis_bone.head_local).z
    head_h = (armature.matrix_world @ head_bone.head_local).z
    char_height = head_h - pelvis_h  # approx
    pelvis_world_rest = (armature.matrix_world @ pelvis_bone.head_local)
    # If the FBX is in cm (typical for citizen), char_height ~ 150ish; in meters ~1.5.
    pelvis_scale = char_height / SMPLX_HEIGHT_M if char_height > 0 else 1.0
    print(f"[bake] char_height={char_height:.3f} pelvis_scale={pelvis_scale:.4f}")

    # Build kimodo joint -> citizen pose bone, skipping any unmapped or missing bones.
    pose_bones = armature.pose.bones
    pairs = []  # list of (kimodo_idx, pose_bone, rest_world_q)
    for k_idx, k_name in enumerate(bone_names):
        c_name = CITIZEN_MAPPING.get(k_name)
        if not c_name:
            continue
        pb = pose_bones.get(c_name)
        if pb is None:
            print(f"[bake] WARN: citizen bone '{c_name}' missing (kimodo {k_name})")
            continue
        rwq = rest_world.get(c_name)
        if rwq is None:
            continue
        pairs.append((k_idx, pb, rwq))
    print(f"[bake] mapped {len(pairs)} bones")

    # Pre-compute parent-world quaternion lookup: for each paired bone, identify
    # its (possibly non-mapped) parent. We'll need parent's world rotation each
    # frame to convert our target world rotation into bone-local.
    # We evaluate parent transforms by walking the pose bone hierarchy each frame.

    # Make sure the armature is selected & active so action assignment sticks.
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)

    # Reset to rest before keyframing.
    for pb in pose_bones:
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = Quaternion((1, 0, 0, 0))
        pb.location = Vector((0, 0, 0))

    # Create an action.
    take_name = args.clip_name or args.clip.stem
    action = bpy.data.actions.new(name=take_name)
    armature.animation_data_create()
    armature.animation_data.action = action

    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = num_frames
    scene.render.fps = int(round(fps))

    print(f"[bake] keyframing {num_frames} frames...")
    armature_world_q = armature.matrix_world.to_quaternion()
    armature_world_inv_q = armature_world_q.inverted()
    pelvis_name = CITIZEN_MAPPING["pelvis"]

    for f in range(num_frames):
        scene.frame_set(f + 1)
        # Bones are processed parent-first. For each bone, we want its world
        # rotation to equal Q_kimodo · rest_world. We set this via the pose
        # bone's armature-space matrix (pose_bone.matrix), letting Blender
        # back-solve matrix_basis (the delta from rest) for us. Setting
        # matrix_basis directly via rotation_quaternion would conflate the
        # full local rotation with the delta and double-apply the rest pose.
        for k_idx, pb, rest_wq in _hierarchy_sorted(pairs, armature):
            # Parent must be evaluated first.
            bpy.context.view_layer.update()
            q_kimodo = kimodo_quat_to_blender(g_quats[f][k_idx])
            target_world_q = q_kimodo @ rest_wq
            # Convert world rotation into armature space.
            target_armature_q = armature_world_inv_q @ target_world_q
            # Preserve the bone's natural position (post-parent), only override
            # the rotation. Scale stays at 1.
            natural_loc = pb.matrix.to_translation()
            pb.matrix = Matrix.LocRotScale(natural_loc, target_armature_q, Vector((1, 1, 1)))
            bpy.context.view_layer.update()
            pb.keyframe_insert("rotation_quaternion", frame=f + 1)

        # Pelvis translation from kimodo root_positions (meters).
        if root_positions is not None:
            pelvis_pb = pose_bones.get(pelvis_name)
            if pelvis_pb is not None:
                rp = root_positions[f]
                # kimodo: right-handed Y-up meters. Convert to armature space
                # (Blender is Z-up so swap Y/Z) then scale by char_height/SMPLX.
                target_world_pos = Vector((rp[0], -rp[2], rp[1])) * (char_height / SMPLX_HEIGHT_M)
                delta_world = target_world_pos - pelvis_world_rest
                # Pose location is in pelvis-bind-local frame (matrix_basis.location).
                pelvis_rest_wq = rest_world[pelvis_name]
                pelvis_pb.location = pelvis_rest_wq.inverted() @ delta_world
                pelvis_pb.keyframe_insert("location", frame=f + 1)

        bpy.context.view_layer.update()

    print(f"[bake] exporting {args.out}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # Re-add the armature only (mesh already deleted) to the export.
    bpy.ops.object.select_all(action="DESELECT")
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.export_scene.fbx(
        filepath=str(args.out),
        use_selection=True,
        object_types={"ARMATURE"},
        bake_anim=True,
        bake_anim_use_all_bones=True,
        bake_anim_use_nla_strips=False,
        bake_anim_use_all_actions=False,
        bake_anim_force_startend_keying=True,
        bake_anim_step=1.0,
        bake_anim_simplify_factor=0.0,
        add_leaf_bones=False,
        # Blender re-rolls bones to its Y-down-bone convention on FBX import.
        # We export with that same convention so the bind matrices round-trip
        # exactly; citizen's native +X-down-bone is not preserved by Blender.
        # primary_bone_axis="X" here would corrupt bind orientation. Verified
        # with passthrough test: only Y-down-bone produces clean rest in sbox.
        primary_bone_axis="Y",
        secondary_bone_axis="X",
        # Source 2 is Z-up, -Y forward. Blender is natively Z-up so no axis
        # transform is needed. bake_space_transform=True adds a 90° X rotation
        # to the armature object that the engine doesn't compensate for; keep
        # it False so data + header both remain in Blender's native frame.
        axis_forward="-Y",
        axis_up="Z",
        bake_space_transform=False,
        apply_unit_scale=True,
        apply_scale_options="FBX_SCALE_NONE",
        path_mode="AUTO",
    )
    print(f"[bake] done: {args.out}")


def _hierarchy_sorted(pairs, armature):
    """Yield pairs in hierarchy order so parent-world is current before child evaluates."""
    rank = {}
    def depth(b):
        if b.name in rank:
            return rank[b.name]
        d = 0 if b.parent is None else depth(b.parent) + 1
        rank[b.name] = d
        return d
    for pb in armature.data.bones:
        depth(pb)
    return sorted(pairs, key=lambda t: rank.get(t[1].name, 0))


if __name__ == "__main__":
    args = parse_args()
    bake(args)
