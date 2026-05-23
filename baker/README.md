# kimodo → s&box citizen baker

Headless Blender pipeline that converts kimodo SMPL-X motion clips into
s&box-compatible FBX animations, plus a single Base-Model vmdl that wraps
the clip library.

Per-clip output: one `.fbx` containing the citizen armature + baked bone
rotations (no mesh). Twist/helper/IK bones are left at bind so the engine's
constraint system drives them at runtime.

Catalog output: one `kimodo_anims.vmdl` with `base_model_name =
"models/citizen/citizen.vmdl"` and one `AnimFile` per clip.

## Requirements

- Blender 3.6+ on `PATH` (verified with 5.1)
- Python 3.10+ for `gen_vmdl.py` (no extra deps)
- Citizen reference FBX (`citizen_REF.fbx`) — ships with the s&box install at
  `~/.local/share/Steam/steamapps/common/sbox/addons/citizen/Assets/models/citizen/citizen_REF.fbx`

## Quick start

```bash
# Bake one clip
blender --background --python bake.py -- \
    --citizen-fbx ~/.local/share/Steam/steamapps/common/sbox/addons/citizen/Assets/models/citizen/citizen_REF.fbx \
    --clip ~/projects/kimodo/.kimodo-animations/345be7856ce3.json \
    --out  /tmp/wave_right_hand.fbx

# Then generate the vmdl that references all baked FBXs in a dir
python gen_vmdl.py \
    --clip-dir   ~/projects/sbox-public/examples/woid/Assets/models/kimodo \
    --out        ~/projects/sbox-public/examples/woid/Assets/models/kimodo/kimodo_anims.vmdl
```

## Verification gate

Before scaling up: bake a "T-pose" test clip (identity quaternions per joint)
and open the resulting vmdl in ModelDoc. If citizen stands in T-pose, the
rest-pose math is correct. If not, fix `bake.py:capture_rest_world_quats` and
the formula in the per-frame loop before processing real motion.

## Bake math

For each mapped bone:

```
Q_target_world(t) = Q_kimodo_world(t) · Q_target_rest_world
Q_target_local(t) = Q_target_parent_world(t)⁻¹ · Q_target_world(t)
```

Same formula as the kimodo runtime viewer (`web/src/animator.js`), but
computed once at bake time instead of per-frame in the browser.

Pelvis translation: `root_positions` are meters at SMPL-X scale. We scale by
`citizen_height_units / SMPLX_HEIGHT_M` and apply as a bone-local delta from
pelvis rest.

## Output FBX export settings

- `object_types = {"ARMATURE"}` — no mesh
- `add_leaf_bones = False` — matches citizen's bone count
- `primary_bone_axis = "X"`, `secondary_bone_axis = "Y"` — citizen convention
- `axis_forward = "-Z"`, `axis_up = "Y"` — Source 2 convention
- `bake_anim_force_startend_keying = True` — guarantees first/last frame
  have keys (some importers truncate the action otherwise)
