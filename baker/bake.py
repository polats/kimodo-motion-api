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

import math

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


# Citizen rest is A-pose (arms ~30° down); SMPL-X motion is authored against a
# near-T-pose canonical rest. We compute a per-bone A→T compensation that
# matches kimodo's runtime "frame" alignment mode: a primary axis (joint →
# child) plus a SECONDARY reference axis pins both the bone direction AND its
# roll around that direction. A direction-only fix produces correct arm
# position but wrong wrist orientation (palms facing the wrong way).
ARM_CHAIN_BONES = {
    "clavicle_L", "clavicle_R",
    "arm_upper_L", "arm_upper_R",
    "arm_lower_L", "arm_lower_R",
}

# SMPL-X canonical rest world positions (verbatim from kimodo/web/src/rigs.js
# SMPLX_REST_WORLD). All 22 joints, meters, identity rest world per joint by
# kimodo's exporter convention.
SMPLX_REST_WORLD = {
    "pelvis":         (0.0031, -0.3514,  0.0120),
    "left_hip":       (0.0613, -0.4442, -0.0140),
    "right_hip":     (-0.0601, -0.4553, -0.0092),
    "spine1":         (0.0004, -0.2415, -0.0156),
    "left_knee":      (0.1160, -0.8229, -0.0234),
    "right_knee":    (-0.1044, -0.8177, -0.0260),
    "spine2":         (0.0098, -0.1097, -0.0215),
    "left_ankle":     (0.0726, -1.2260, -0.0552),
    "right_ankle":   (-0.0889, -1.2284, -0.0462),
    "spine3":         (-0.0015, -0.0574,  0.0069),
    "left_foot":      (0.1198, -1.2840,  0.0630),
    "right_foot":    (-0.1277, -1.2868,  0.0728),
    "neck":           (-0.0137,  0.1077, -0.0247),
    "left_collar":    (0.0448,  0.0275, -0.0003),
    "right_collar":  (-0.0492,  0.0269, -0.0065),
    "head":           (0.0111,  0.2682, -0.0040),
    "left_shoulder":  (0.1641,  0.0852, -0.0158),
    "right_shoulder":(-0.1518,  0.0804, -0.0191),
    "left_elbow":     (0.4182,  0.0131, -0.0582),
    "right_elbow":   (-0.4229,  0.0439, -0.0456),
    "left_wrist":     (0.6702,  0.0363, -0.0607),
    "right_wrist":   (-0.6722,  0.0394, -0.0609),
}

# Primary axis per joint: which joint to use as "child" for bone direction.
KIMODO_CHILD = {
    "pelvis": "spine1",
    "left_collar": "left_shoulder", "right_collar": "right_shoulder",
    "left_shoulder": "left_elbow",  "right_shoulder": "right_elbow",
    "left_elbow": "left_wrist",     "right_elbow": "right_wrist",
}

# Secondary axis (twist reference): another joint used to fully define the
# bone's local frame. Verbatim from kimodo/web/src/rigs.js KIMODO_TWIST_REF.
# For arms: reference = spine2 (constrains rotation around the arm axis).
KIMODO_TWIST_REF = {
    "left_collar":    "spine2", "right_collar":   "spine2",
    "left_shoulder":  "spine2", "right_shoulder": "spine2",
    "left_elbow":     "spine2", "right_elbow":    "spine2",
}

# Reverse map: citizen bone → kimodo joint.
CITIZEN_TO_KIMODO = {v: k for k, v in {
    "pelvis": "pelvis",
    "left_hip": "leg_upper_L", "right_hip": "leg_upper_R",
    "spine1": "spine_0", "left_knee": "leg_lower_L", "right_knee": "leg_lower_R",
    "spine2": "spine_1", "left_ankle": "ankle_L", "right_ankle": "ankle_R",
    "spine3": "spine_2", "left_foot": "ball_L", "right_foot": "ball_R",
    "neck": "neck_0", "left_collar": "clavicle_L", "right_collar": "clavicle_R",
    "head": "head", "left_shoulder": "arm_upper_L", "right_shoulder": "arm_upper_R",
    "left_elbow": "arm_lower_L", "right_elbow": "arm_lower_R",
    "left_wrist": "hand_L", "right_wrist": "hand_R",
}.items()}


def _frame_from_primary_ref(primary, reference):
    """Build a 3x3 orthonormal basis from a primary axis and a reference
    direction. Returns a Quaternion. Port of kimodo's frameFromPrimaryRef
    (animator.js:25). Columns of the basis: X=primary, Z=primary×ref, Y=Z×X.
    The basis is degenerate if primary || reference; we fall back to world up."""
    x = primary.normalized()
    z = x.cross(reference)
    if z.length < 1e-5:
        fallback = Vector((0, 1, 0)) if abs(x.y) < 0.99 else Vector((1, 0, 0))
        z = x.cross(fallback)
    z.normalize()
    y = z.cross(x).normalized()
    m = Matrix(((x.x, y.x, z.x),
                (x.y, y.y, z.y),
                (x.z, y.z, z.z)))
    return m.to_quaternion()


def compute_arm_compensation(armature):
    """Per-bone rotation (bone-local) that aligns each arm bone's rest
    direction onto the horizontal (XY) plane — i.e. moves citizen's A-pose
    arms into a virtual T-pose. Returns {bone_name: Quaternion}; identity
    for bones not in ARM_CHAIN_BONES.

    Method: take each bone's armature-space rest direction (tail - head),
    project to XY (drop Z to make it horizontal), use that as the target.
    The rotation aligning rest_dir → target_dir is computed in armature
    space then conjugated into bone-local for use in matrix_basis."""
    out = {}
    for bone in armature.data.bones:
        if bone.name not in ARM_CHAIN_BONES:
            continue
        k_name = CITIZEN_TO_KIMODO.get(bone.name)
        if k_name is None:
            continue
        child_k = KIMODO_CHILD.get(k_name)
        ref_k = KIMODO_TWIST_REF.get(k_name)
        if not child_k or not ref_k:
            continue

        # SMPL-X side: primary = child - joint, ref = ref - joint, all in
        # kimodo's canonical rest world (identity per joint).
        k0 = Vector(SMPLX_REST_WORLD[k_name])
        kC = Vector(SMPLX_REST_WORLD[child_k])
        kR = Vector(SMPLX_REST_WORLD[ref_k])
        k_prim = kC - k0
        k_ref = kR - k0

        # Citizen side: use head positions IN ARMATURE SPACE (the rest frame
        # we're computing against). The conjugation below transforms the
        # rotation from armature space into bone-local space for matrix_basis.
        child_c = armature.data.bones.get(CITIZEN_MAPPING[child_k])
        ref_c = armature.data.bones.get(CITIZEN_MAPPING[ref_k])
        if child_c is None or ref_c is None:
            continue
        t0 = bone.head_local
        tC = child_c.head_local
        tR = ref_c.head_local
        t_prim = tC - t0
        t_ref_v = tR - t0

        # Build full orthonormal basis at the joint for each rig (primary +
        # secondary). Alignment rotation (world) = kFrame · tFrame⁻¹.
        kFrame = _frame_from_primary_ref(k_prim, k_ref)
        tFrame = _frame_from_primary_ref(t_prim, t_ref_v)
        comp_world = kFrame @ tFrame.inverted()

        # Conjugate into bone-local space (matrix_basis frame).
        t_ref_q = bone.matrix_local.to_quaternion()
        out[bone.name] = t_ref_q.inverted() @ comp_world @ t_ref_q
    return out


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


def capture_rest_armature_quats(armature):
    """Map bone name -> armature-space rest quaternion. This is the bone's
    bind orientation relative to the armature object, NOT world space. CARL's
    Path B retargeting uses armature-local rest because it's frame-stable
    even when the armature object is parented/rotated."""
    return {
        bone.name: bone.matrix_local.to_quaternion()
        for bone in armature.data.bones
    }


def capture_virtual_tpose_quats(armature, char_height):
    """Pose citizen's arm chain to the SMPL-X canonical rest direction, then
    capture each bone's armature-space rotation in that posed state. Reset the
    pose afterwards so the exported FBX bind matrices stay at citizen's actual
    A-pose (required for sbox base_model skinning).

    This is the same trick kimodo applies to its skinned variants in
    web/src/rigs.js:133-135 — "pose arms to SMPL-X rest direction" — but we
    only USE the posed orientation in the retargeting math, we don't bake it
    into the bind. Used in place of bone.matrix_local for arm chain bones
    when building the conjugation T_ref in our Path B math.

    Returns the same dict shape as capture_rest_armature_quats: bone_name ->
    Quaternion. Non-arm bones use their actual A-pose rest unchanged.
    """
    # Default comp = identity; only arm chain bones get a real comp.
    out = {}
    pose_bones = armature.pose.bones

    # Walk the arm chain head-to-leaf so each parent is posed before its child
    # (so child's pose_bone.matrix reflects the parent's T-pose orientation).
    chain_in_order = [
        ("clavicle_L", "arm_upper_L"), ("clavicle_R", "arm_upper_R"),
        ("arm_upper_L", "arm_lower_L"), ("arm_upper_R", "arm_lower_R"),
        ("arm_lower_L", "hand_L"),     ("arm_lower_R", "hand_R"),
    ]

    # Stride scale converts SMPL-X meters → citizen armature units.
    scale = char_height / SMPLX_HEIGHT_M

    saved_quats = {}
    for c_name, _ in chain_in_order:
        pb = pose_bones[c_name]
        pb.rotation_mode = "QUATERNION"
        saved_quats[c_name] = pb.rotation_quaternion.copy()

    for c_name, c_child_name in chain_in_order:
        k_name = CITIZEN_TO_KIMODO.get(c_name)
        k_child = CITIZEN_TO_KIMODO.get(c_child_name)
        if k_name is None or k_child is None:
            continue
        kp_joint = Vector(SMPLX_REST_WORLD[k_name]) * scale
        kp_child = Vector(SMPLX_REST_WORLD[k_child]) * scale
        target_dir = (kp_child - kp_joint).normalized()
        # Force forearm target to pure horizontal lateral (independent of
        # SMPL-X). The SMPL-X-derived target was already near-horizontal so
        # Y-zero gave a tiny shift; bypassing SMPL-X entirely for the forearm
        # forces a real straightening of the elbow.
        if c_name == "arm_lower_L":
            target_dir = Vector((1.0, 0.0, 0.0))
        elif c_name == "arm_lower_R":
            target_dir = Vector((-1.0, 0.0, 0.0))
        elif c_name in ("clavicle_L", "clavicle_R", "arm_upper_L", "arm_upper_R"):
            # Upper arm + clavicle: zero Y for horizontal-only.
            target_dir = Vector((target_dir.x, 0.0, target_dir.z)).normalized()

        # Current direction: from this bone's head to its child's head, in
        # armature space (head_local IS armature space — head_local under
        # current pose). We need the LIVE positions, not bind, since parents
        # may already be posed.
        bpy.context.view_layer.update()
        cur_pb = pose_bones[c_name]
        child_pb = pose_bones[c_child_name]
        cur_pos = cur_pb.head
        child_pos = child_pb.head
        cur_dir = (child_pos - cur_pos).normalized()

        # Build the rotation that aligns cur_dir → target_dir in armature
        # space. Convert to bone-local by conjugating with the pose-bone's
        # current matrix (parent-relative rest with parent's pose absorbed).
        rot_armature = cur_dir.rotation_difference(target_dir)
        # Pose's matrix_basis frame: pose_bone.matrix is in armature space,
        # so to set "rotate by rot_armature in armature space" we right-mul.
        # pose_bone.matrix_basis = matrix_basis applied to (parent_pose · rest)
        # Simpler: use pose_bone.matrix = rotation @ pose_bone.matrix.
        m = cur_pb.matrix.copy()
        new_m = Matrix.LocRotScale(m.to_translation(),
                                   rot_armature @ m.to_quaternion(),
                                   m.to_scale())
        cur_pb.matrix = new_m
        bpy.context.view_layer.update()

    # Capture both: (a) matrix_basis (the bone's intrinsic delta from bind)
    # for use as comp; (b) pose_bone.matrix.to_quaternion() (armature-space
    # orientation at virtual T-pose) for use as the conjugation reference
    # T_virt. Both are needed: matrix_basis @ (T_virt⁻¹ @ local_q @ T_virt)
    # gives bone_world = local_q @ T_virt — T-pose start with motion applied
    # in world frame relative to T-pose orientation.
    for c_name, _ in chain_in_order:
        out[c_name] = {
            "comp": pose_bones[c_name].rotation_quaternion.copy(),
            "t_virt": pose_bones[c_name].matrix.to_quaternion(),
        }

    # Reset pose so the exported FBX bind matrices are unchanged.
    for c_name, saved_q in saved_quats.items():
        pose_bones[c_name].rotation_quaternion = saved_q
        pose_bones[c_name].location = Vector((0, 0, 0))
    bpy.context.view_layer.update()
    return out


def kimodo_local_quat_to_blender(wxyz):
    """kimodo local_quats_wxyz -> Blender Quaternion(w, x, y, z)."""
    return Quaternion((wxyz[0], wxyz[1], wxyz[2], wxyz[3]))


def bake(args):
    print(f"[bake] reading {args.clip}")
    clip = json.loads(args.clip.read_text())
    fps = clip["fps"]
    num_frames = clip["num_frames"]
    bone_names = clip["bone_names"]
    # Use local_quats_wxyz (parent-local rotations, identity-rest by kimodo
    # convention) so we can apply CARL's Path B basis-change formula:
    #   matrix_basis = T_ref⁻¹ · local_q · T_ref
    # where T_ref is citizen's bone rest in armature space. Using global_quats
    # would force us to handle world↔armature conversion separately AND lose
    # the parent-local structure the formula expects.
    l_quats = clip["local_quats_wxyz"]  # [T][J][4]
    root_positions = clip.get("root_positions")  # [T][3] meters
    print(f"[bake] clip: fps={fps} frames={num_frames} joints={len(bone_names)}")

    reset_scene()
    print(f"[bake] importing {args.citizen_fbx}")
    armature = import_citizen(args.citizen_fbx)
    delete_meshes(armature)

    # Force evaluation so matrix_world is current.
    bpy.context.view_layer.update()

    rest_armature = capture_rest_armature_quats(armature)

    # Figure out scale: kimodo positions are meters. Detect citizen armature
    # height in its source units (cm) and compute stride scale.
    pelvis_bone = armature.data.bones.get(CITIZEN_MAPPING["pelvis"])
    if pelvis_bone is None:
        raise RuntimeError("No pelvis bone in armature")
    head_bone = armature.data.bones.get(CITIZEN_MAPPING["head"])
    if head_bone is None:
        raise RuntimeError("No head bone in armature")
    # Measure pelvis→head distance in ARMATURE-LOCAL units (citizen FBX is in
    # cm, so this returns cm) AND compare against the SAME segment in SMPL-X
    # canonical (also pelvis→head in meters). Like-to-like gives the unit
    # conversion factor for translating kimodo's meter positions into the
    # bone's location-keyframe frame.
    pelvis_h_local = pelvis_bone.head_local.y  # Y is up in armature-local
    head_h_local = head_bone.head_local.y
    char_height = head_h_local - pelvis_h_local  # citizen pelvis→head, cm
    smplx_pelvis_to_head = (
        SMPLX_REST_WORLD["head"][1] - SMPLX_REST_WORLD["pelvis"][1]
    )
    pelvis_world_rest = (armature.matrix_world @ pelvis_bone.head_local)
    pelvis_scale = (
        char_height / smplx_pelvis_to_head if smplx_pelvis_to_head > 0 else 1.0
    )
    # kimodo's root_positions is the pelvis WORLD position in the clip
    # (typically pelvis ~1m off the ground, standing). Subtract the clip's
    # own first frame so the delta is measured from the clip's start —
    # citizen lands at its bind at t=0, then moves the right amount over
    # the clip. Computed per-clip in the per-frame loop below.
    print(f"[bake] char_height={char_height:.2f}cm  smplx_pelvis_to_head={smplx_pelvis_to_head:.3f}m  pelvis_scale={pelvis_scale:.2f}")

    # Virtual T-pose: for arm bones, replace the A-pose bind rotation with
    # what the bone would have if posed to SMPL-X canonical direction. This
    # closes the rest-pose mismatch (~46° on the upper arm) inside our
    # conjugation math; the exported FBX bind matrices are NOT modified, so
    # sbox base_model skinning still expects (and gets) citizen's actual
    # A-pose bind. Direction-only — roll is left to rotation_difference's
    # shortest-path choice, which may need refinement.
    pose_bones = armature.pose.bones
    rest_for_math = capture_virtual_tpose_quats(armature, char_height)
    for bn in sorted(rest_for_math.keys()):
        q = rest_for_math[bn]["comp"]
        ang = 2 * math.acos(min(1.0, abs(q.w))) * 180.0 / math.pi
        print(f"[bake] comp {bn}: {ang:.1f}°")

    # For arm bones: t_ref = T_virtual (armature-space rotation at virtual
    # T-pose), comp = matrix_basis to reach T-pose from bind.
    # For non-arm bones: t_ref = actual bind, comp = identity. Math reduces
    # to pure conjugation, leaving those bones unchanged.
    pairs = []  # list of (kimodo_idx, pose_bone, t_ref, t_ref_inv, comp)
    for k_idx, k_name in enumerate(bone_names):
        c_name = CITIZEN_MAPPING.get(k_name)
        if not c_name:
            continue
        pb = pose_bones.get(c_name)
        if pb is None:
            print(f"[bake] WARN: citizen bone '{c_name}' missing (kimodo {k_name})")
            continue
        info = rest_for_math.get(c_name)
        if info is not None:
            t_ref = info["t_virt"]
            comp = info["comp"]
        else:
            t_ref = rest_armature[c_name]
            comp = Quaternion()
        pairs.append((k_idx, pb, t_ref, t_ref.inverted(), comp))
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
    # Blender 5 layered actions require an explicit slot for keyframes to be
    # exported through the FBX writer. Without this, keyframe_insert puts data
    # into the action but bake_anim doesn't see it and exports identity rest.
    if hasattr(action, "slots") and len(action.slots) == 0:
        slot = action.slots.new("OBJECT", name="OBArmature")
        armature.animation_data.action_slot = slot

    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = num_frames
    scene.render.fps = int(round(fps))

    print(f"[bake] keyframing {num_frames} frames...")
    pelvis_name = CITIZEN_MAPPING["pelvis"]

    for f in range(num_frames):
        scene.frame_set(f + 1)
        # CARL Path B per-bone formula: matrix_basis = T_ref⁻¹ · local_q · T_ref
        # — conjugation that re-expresses kimodo's parent-local rotation into
        # citizen's bone-local frame, automatically handling bone-axis
        # convention differences (citizen +X-down-bone vs SMPL-X identity)
        # AND rest-pose differences (citizen A-pose vs SMPL-X T-pose).
        # No view_layer update needed inside the per-bone loop because
        # matrix_basis is parent-independent.
        for k_idx, pb, t_ref, t_ref_inv, comp in pairs:
            local_q = kimodo_local_quat_to_blender(l_quats[f][k_idx])
            # matrix_basis = comp @ (T_ref⁻¹ · local_q · T_ref)
            #   non-arm bones: comp = identity, T_ref = actual A-pose bind →
            #     pure conjugation (unchanged from working baseline).
            #   arm bones: comp = T-pose pose_bone delta, T_ref = armature-
            #     space T-pose orientation → bone displays at T-pose at rest
            #     and motion is conjugated through T-pose frame.
            basis_q = comp @ (t_ref_inv @ local_q @ t_ref)
            pb.rotation_quaternion = basis_q
            pb.keyframe_insert("rotation_quaternion", frame=f + 1)

        # Pelvis translation from kimodo root_positions (meters).
        # Set pose_bone.matrix preserving the rotation set earlier in this
        # loop. Letting Blender solve matrix_basis avoids manual frame math
        # that mapped kimodo Y into the wrong armature-local axis.
        if root_positions is not None:
            pelvis_pb = pose_bones.get(pelvis_name)
            if pelvis_pb is not None:
                rp = root_positions[f]
                rp0 = root_positions[0]
                # kimodo:  X=lateral, Y=vertical, Z=forward
                # citizen pelvis bone-local (from matrix_local columns):
                #   bone-X → armature Y (up)
                #   bone-Y → armature Z (forward)
                #   bone-Z → armature X (lateral)
                # pose_bone.location is in bone-local, so map accordingly so
                # the engine renders forward/up/lateral correctly.
                dx = rp[0] - rp0[0]
                dy = rp[1] - rp0[1]
                dz = rp[2] - rp0[2]
                pelvis_pb.location = Vector((dy, dz, dx)) * pelvis_scale
                pelvis_pb.keyframe_insert("location", frame=f + 1)

        bpy.context.view_layer.update()

    # Critical: reset scene to frame 1 before export. Without this,
    # bake_anim picks up the scene's current (last-frame) pose and writes
    # weird offsets that produce reversed-looking location keyframes on
    # re-import. With scene at frame 1 first, bake_anim walks frames in
    # order and writes correct values.
    scene.frame_set(1)
    bpy.context.view_layer.update()

    print(f"[bake] exporting {args.out}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
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
        bake_anim_force_startend_keying=False,
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


if __name__ == "__main__":
    args = parse_args()
    bake(args)
