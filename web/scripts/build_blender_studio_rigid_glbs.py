"""Build the four rigid Blender Studio character GLBs from the Human Base
Meshes Bundle source .blend.

Pipeline per variant (4: male/female × realistic/stylized):
  1. T-pose: rotate the arm chain (shoulder→elbow→wrist→hand) about the
     shoulder so the upper arm points laterally along world ±X. Half the
     rotation is also applied to the collar so the shoulder cap "rides along"
     instead of forming a sharp seam.
  2. Forearm straighten: rotate the forearm chain (elbow→hand) about the
     elbow so the forearm is colinear with the upper arm. Without this, the
     bundle's natural elbow droop (~18°) leaves arms curved when viewed from
     above, off-axis from kimodo's SMPL-X rest.
  3. Export the variant's collection as a GLB.

The source .blend is NOT modified — outputs are written to the model dir.

Run:
    blender --background path/to/human_base_meshes_bundle__rigid.blend \
        --python web/scripts/build_blender_studio_rigid_glbs.py -- \
        web/public/models
"""
import os
import sys

import bpy
import mathutils


# (gender, flavor, output_filename, name_format, collar_part, body_collection_substr)
# 3 of 4 variants follow {part}_{gender}_primitive_{flavor}; female-realistic
# inverts: {part}_primitive_female_realistic.
VARIANTS = [
    ("male",   "realistic", "male_primitive.glb",
     "{part}_male_primitive_realistic",   "shoulder", "Realistic"),
    ("female", "stylized",  "female_stylized.glb",
     "{part}_female_primitive_stylized",  "shoulder", "Stylized"),
    ("male",   "stylized",  "male_stylized.glb",
     "{part}_male_primitive_stylized",    "shoulder", "Stylized"),
    ("female", "realistic", "female_primitive.glb",
     "{part}_primitive_female_realistic", "shoulder", "Realistic"),
]

# Half of the arm rotation also goes to the collar — empirically gives a
# convincing shoulder cap on rigid (un-skinned) meshes without distorting the
# torso silhouette.
COLLAR_FRACTION = 0.5


def rotate_around(objs, pivot, R):
    T = (mathutils.Matrix.Translation(pivot)
         @ R.to_matrix().to_4x4()
         @ mathutils.Matrix.Translation(-pivot))
    for o in objs:
        o.matrix_world = T @ o.matrix_world


def descendants(obj, include_self=False):
    out = [obj] if include_self else []
    stack = list(obj.children)
    while stack:
        x = stack.pop()
        out.append(x)
        stack.extend(x.children)
    return out


def tpose_and_straighten(namefmt, side):
    """Rotate the arm chain to lateral T-pose, then straighten the forearm."""
    target = mathutils.Vector(((1.0 if side == "L" else -1.0), 0.0, 0.0))

    collar = bpy.data.objects[f"GEO-{namefmt.format(part='shoulder')}.{side}"]
    upper  = bpy.data.objects[f"GEO-{namefmt.format(part='arm_upper')}.{side}"]
    elbow  = bpy.data.objects[f"GEO-{namefmt.format(part='arm_lower')}.{side}"]
    hand   = bpy.data.objects[f"GEO-{namefmt.format(part='hand')}.{side}"]

    # Step 1a: rotate collar by COLLAR_FRACTION of the full upper-arm
    # correction. Computed against the pre-rotation arm direction so that
    # after the partial collar rotation moves the shoulder, the remaining
    # arm-only rotation lands the upper arm exactly on target.
    cur = (upper.matrix_world.translation - collar.matrix_world.translation)
    # Direction we want the collar→shoulder vector to *eventually* lie along
    # is whatever lateral direction the upper arm has after step 1b. Using
    # the upper-arm correction here is an approximation; it's fine because
    # COLLAR_FRACTION is a tunable cosmetic blend, not a constraint.
    cur_arm = (elbow.matrix_world.translation - upper.matrix_world.translation).normalized()
    R_full = cur_arm.rotation_difference(target)
    R_collar = mathutils.Quaternion().slerp(R_full, COLLAR_FRACTION)
    rotate_around([collar], collar.matrix_world.translation.copy(), R_collar)
    bpy.context.view_layer.update()

    # Step 1b: rotate the rest of the chain (upper + elbow + hand) about the
    # shoulder so the upper arm lands on target.
    cur_arm2 = (elbow.matrix_world.translation - upper.matrix_world.translation).normalized()
    R_arm = cur_arm2.rotation_difference(target)
    rotate_around(descendants(upper, include_self=True),
                  upper.matrix_world.translation.copy(), R_arm)
    bpy.context.view_layer.update()

    # Step 2: straighten forearm. Rotate elbow + hand about the elbow so the
    # elbow→wrist vector is colinear with the upper arm direction (= target).
    cur_fore = (hand.matrix_world.translation - elbow.matrix_world.translation).normalized()
    R_fore = cur_fore.rotation_difference(target)
    rotate_around(descendants(elbow, include_self=True),
                  elbow.matrix_world.translation.copy(), R_fore)
    bpy.context.view_layer.update()


def collection_for(gender, flavor_substr):
    """Find the body collection for a variant. Names in the bundle vary
    slightly ('Primitve'/'Primitive' typo, etc.), so substring-match."""
    pat = f"Body {gender.capitalize()}"
    for c in bpy.data.collections:
        if c.name.startswith(pat) and flavor_substr in c.name and "rimit" in c.name:
            return c
    return None


def bake_rest_to_mesh(coll):
    """Bake each mesh's rest rotation+scale into vertex data so all rests
    are identity. Bundle's right side has ~180° rotation as a mirror trick
    (shared mesh data); baking it makes the joint behave like an
    independent unit so the animator's per-bone world rotation (alignMode
    'none') applies cleanly to both sides.
    """
    bpy.ops.object.select_all(action="DESELECT")
    meshes = [o for o in coll.all_objects if o.type == "MESH"]
    for o in meshes:
        o.select_set(True)
    if not meshes:
        return
    bpy.context.view_layer.objects.active = meshes[0]
    # Bundle shares mesh data between L/R for the mirror trick;
    # transform_apply refuses multi-user data, so duplicate first.
    bpy.ops.object.make_single_user(object=False, obdata=True)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)


def export_collection(coll, out_path):
    bake_rest_to_mesh(coll)
    bpy.ops.object.select_all(action="DESELECT")
    for o in coll.all_objects:
        o.select_set(True)
    bpy.ops.export_scene.gltf(
        filepath=out_path, export_format="GLB",
        use_selection=True, export_yup=True,
        export_apply=True, export_skins=True, export_animations=False,
    )


def main():
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []
    if len(argv) != 1:
        print("usage: blender -b SRC.blend -P build_blender_studio_rigid_glbs.py -- OUT_DIR")
        sys.exit(2)
    out_dir = argv[0]
    os.makedirs(out_dir, exist_ok=True)

    for gender, flavor, glb, namefmt, _collar_part, flavor_substr in VARIANTS:
        coll = collection_for(gender, flavor_substr)
        if coll is None:
            print(f"[skip] no collection for {gender}/{flavor}")
            continue
        for side in ("L", "R"):
            tpose_and_straighten(namefmt, side)
        out_path = os.path.join(out_dir, glb)
        export_collection(coll, out_path)
        print(f"[ok] {gender}/{flavor} -> {out_path}")


if __name__ == "__main__":
    main()
