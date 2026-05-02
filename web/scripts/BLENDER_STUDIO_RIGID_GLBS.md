# Blender Studio rigid GLB pipeline — gotchas

Notes from getting the four rigid Blender Studio characters (`*_primitive.glb`,
`*_stylized.glb`) to T-pose at rest *and* animate symmetrically when driven by
kimodo's per-joint world rotations. The script is
`build_blender_studio_rigid_glbs.py`.

## The bundle's mirror is a 180° rotation, not a reflection

Right-side meshes (`GEO-*.R`) share mesh data with their left-side counterparts
and are visually mirrored at rest by a ~180° rotation on the parent joint
object — *not* by a true reflection. Rotations preserve handedness;
reflections invert it. As long as nothing overrides the joint's rotation, this
looks fine at rest because limbs are roughly cylindrical and the handedness
flip is invisible. It breaks the moment something rewrites that rotation.

## Why `alignMode='none'` flipped the right side mid-animation

The web animator (`web/src/animator.js`) in `'none'` mode sets each joint's
quaternion directly from kimodo's per-frame world rotation. That overwrite
wipes out the bundle's 180° rest rotation, exposing the un-mirrored mesh data
underneath — at which point the right side animates as if it were a left side
in the wrong place. Symptoms: even on a relaxed-stand idle, the right arm
rises and the right leg lifts (visible while the left side stays put). On
SMPL-X the same animation looks like a person standing quietly, which is the
giveaway that kimodo's output is symmetric and the breakage is on the
character side.

## Why `alignMode='rest'` *also* didn't fix it

`'rest'` mode composes `Q_world = Q_kimodo · restQ`. For a true reflection
that composition would land on the mirrored pose; for a 180° rotation it
doesn't, because rotations and reflections aren't interchangeable. Tried it,
still wrong.

## The fix: bake rest rotations into mesh data

`bpy.ops.object.transform_apply(rotation=True, scale=True)` on the per-joint
mesh chunks. After bake every joint's rest rotation is identity and the 180°
"mirror" is encoded into the right-side mesh vertices. Visually identical at
rest; the animator's `'none'` override now lands cleanly on both sides.

This is conceptually wrong (the right side is still a 180°-rotated left arm
rather than a true reflection), but for cylindrical limbs the handedness
error is invisible, and kimodo's symmetric per-joint rotations land in
visually-correct places.

## Gotchas in the bake step

**Multi-user mesh data.** L and R sides share a mesh datablock. Blender
refuses `transform_apply` on multi-user data with "Cannot apply to a
multi user". Fix: `bpy.ops.object.make_single_user(object=False, obdata=True)`
on the selected hierarchy *before* applying transforms.

**Select the whole hierarchy.** `transform_apply` on a parented object alone
prints "Skipping ..., xforms can't be applied to objects with parents" and
silently does nothing. Selecting all parents and children together lets
Blender propagate the bake correctly through the parent links — children's
local translations/rotations are updated to preserve world positions.

**Don't try to make it a true reflection.** I tried staging
`scale=(1, 1, -1)` on right-side meshes before the bake to convert the 180°-Y
rotation into an X-axis reflection (vertex `(vx, vy, vz)` → `(-vx, vy, vz)`).
This breaks the rest pose visually — most of the bundle's right-side joints
have only *near*-180° rotations (small splay angles tilt them off pure-Y),
so the negative-scale + bake produces inconsistent geometry. Plain
`rotation=True, scale=True` is enough.

## Source-of-truth flow

The bundle's `backups/human_base_meshes_bundle__rigid.blend` is the source.
It has all the per-joint rigid mesh chunks intact. The script:

1. Opens `__original.blend`,
2. T-poses arms (collar half-rotation + arm chain rotation about the shoulder),
3. Straightens forearms (rotate elbow chain so it's colinear with upper arm),
4. Bakes rotations + scales into mesh data,
5. Exports each variant's collection as a separate GLB.

`__original.blend` itself is not modified. Re-run the script anytime; outputs
land in `web/public/models/`.

## Animator default for rigid

`web/src/main.js` auto-selects `'none'` for non-skinned characters. This
relies on the bake step having set all rest rotations to identity. If
someone exports rigid GLBs without the bake (e.g. by skipping the script),
expect the right-side animation flip.

## Things I burned time on, in order

1. Modifying GLB vertex positions in-place to straighten forearms — works,
   but disconnected from source. User flagged it: source-of-truth fix only.
2. Editing `__rigid.blend` and re-saving destructively. Avoid; use the
   `__rigid.blend1` autosave to recover if you do.
3. Forgetting that `bpy.ops.pose.armature_apply()` doesn't update mesh data
   on skinned meshes — only the bone rest + IBMs (which cancel each other,
   so the visual stays the same and the mesh shape stays curved).
4. Trying to fix the right-side mirror in the animator instead of in the
   build pipeline.

## Verifying

A quick eyeball check that catches most regressions:

- Front view at rest: clean T-pose, both arms lateral, both legs vertical.
- Top view at rest: arms run along a single horizontal line (no forward
  curve at the elbow).
- Play `just standing around` on Male Realistic (rigid) and SMPL-X
  side-by-side. Both should look like a person standing. If the rigid
  character raises its right arm or lifts its right leg, the bake or the
  animator default got broken.
