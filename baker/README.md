# kimodo → s&box citizen baker

Headless Blender pipeline that converts kimodo SMPL-X motion clips into
s&box-compatible FBX animations, plus a single Base-Model vmdl that wraps
the clip library.

Per-clip output: one `.fbx` containing the citizen armature + baked bone
rotations (no mesh). Twist/helper/IK bones are left at bind so the engine's
constraint system drives them at runtime.

Catalog output: one `kimodo_anims.vmdl` with `base_model_name =
"models/citizen_human/citizen_human_male.vmdl"` (or `citizen/citizen.vmdl`)
and one `AnimFile` per clip.

## Requirements

- Blender 3.6+ on `PATH` (verified with 5.1)
- Python 3.10+ for `gen_vmdl.py` (no extra deps)
- Citizen reference FBX — ships with the s&box install at
  `~/.local/share/Steam/steamapps/common/sbox/addons/citizen/Assets/models/citizen_human/citizen_human_male_REF.fbx`

## Quick start

```bash
CITIZEN=~/.local/share/Steam/steamapps/common/sbox/addons/citizen/Assets/models/citizen_human/citizen_human_male_REF.fbx
OUTDIR=~/projects/sbox-public/examples/woid/Assets/models/kimodo

# Batch bake the curated clip set
python batch_bake.py \
    --citizen-fbx "$CITIZEN" \
    --kimodo-dir  ~/projects/kimodo/.kimodo-animations \
    --out-dir     "$OUTDIR"

# Generate the wrapper vmdl
python gen_vmdl.py \
    --clip-dir   "$OUTDIR" \
    --out        "$OUTDIR/kimodo_anims.vmdl" \
    --base-model "models/citizen_human/citizen_human_male.vmdl"
```

## Pipeline architecture

```
kimodo motion JSON ──────► bake.py (headless Blender)
                          │
                          ├── Import citizen_human_male_REF.fbx
                          ├── Capture A-pose bind matrices (rest_armature)
                          ├── Pose arm chain to virtual T-pose, capture per-bone
                          │   matrix_basis (comp) and pose_bone.matrix (T_virt)
                          ├── Reset pose so exported bind matches citizen's
                          │   actual A-pose (sbox skinning requires this)
                          ├── For each frame, set matrix_basis per bone via
                          │   conjugation formula (see "Bake math" below)
                          └── Export FBX 7.4 binary, armature only

vmdl wrapper ──────────► gen_vmdl.py
                          │
                          ├── Walk *.fbx in clip dir
                          ├── Emit one AnimFile per FBX (start/end=-1, take="")
                          └── Set base_model_name + ModelModifier_ScaleAndMirror
```

## Bake math (current — `rest`-mode equivalent with virtual T-pose)

For each citizen bone mapped to a kimodo joint:

```
matrix_basis(t) = comp · (T_ref⁻¹ · local_q(t) · T_ref)
```

Where:
- `local_q(t)` = kimodo `local_quats_wxyz[t][joint]` (joint rotation in its
  own local frame; SMPL-X canonical rest is identity, so local_q at rest = I)
- `T_ref` = armature-space rotation reference used for the conjugation:
  - **Non-arm bones**: `bone.matrix_local.to_quaternion()` (actual A-pose bind)
  - **Arm chain bones**: `pose_bone.matrix.to_quaternion()` captured after
    posing the bone to virtual T-pose (so motion is conjugated through the
    T-pose frame, not A-pose)
- `comp` = matrix_basis delta from bind to virtual T-pose:
  - **Non-arm bones**: identity → formula collapses to pure conjugation,
    leaving these bones unchanged
  - **Arm chain bones**: `pose_bone.rotation_quaternion` after the virtual
    T-pose posing pass

At `local_q = identity`: `matrix_basis = comp` → bone displays at T-pose
(arms in clean horizontal T). During motion: motion delta is reinterpreted
in T-pose-bone-local frame and applied on top of T-pose start.

### Virtual T-pose construction

Walk the arm chain head-to-leaf (clavicle → arm_upper → arm_lower). For each
bone, compute the rotation that aligns its current head→child-head direction
onto a horizontal-lateral target direction, set `pose_bone.matrix`
accordingly, force a depsgraph update, then move to the next bone. Capture
both `rotation_quaternion` (matrix_basis = comp) and `matrix.to_quaternion()`
(armature-space = T_virt) at the end.

Reset the pose afterwards so the **exported FBX bind matrices stay at
citizen's actual A-pose** — sbox base_model skinning requires the bind to
match what the citizen mesh's skin weights expect.

### Pelvis translation

`root_positions` is meters at SMPL-X scale. Scale by `char_height /
SMPLX_HEIGHT_M`, swap Y/Z (kimodo Y-up vs Blender Z-up), express as
bone-local delta from pelvis rest, keyframe `pelvis_pb.location`.

## Gotchas (the trail we walked)

A chronological list of every gotcha we hit and what fixed it. If you ever
need to redo this for a new character, refer here first.

### 1. The wrapper vmdl needs its own `ModelModifier_ScaleAndMirror`

Citizen's source FBX is in centimeters; the citizen.vmdl applies
`ScaleAndMirror(scale=0.3937)` to convert to engine inches. **Inheritance via
`base_model_name` does NOT propagate this modifier to externally-referenced
AnimFile entries.** Without it, bone translations in our anim FBX arrive 2.54x
larger than the citizen mesh, stretching the character.

**Fix:** `gen_vmdl.py` emits a `ModelModifierList` with the same scale.

### 2. Source 2 wants Z-up FBXs

Initial Y-up exports (`axis_up="Y"`) produced FBX files where the engine
silently dropped all animation data. Z-up makes the engine ingest the file.

**Fix:** export with `axis_up="Z", axis_forward="-Y"`.

### 3. `bake_space_transform=True` adds an unwanted 90° X rotation

With this option, Blender bakes the axis-conversion transform into the
armature object's matrix_world, which the engine doesn't compensate for.
Result: head facing upward.

**Fix:** `bake_space_transform=False`. Citizen FBX is already Z-up natively,
so no conversion is needed.

### 4. `primary_bone_axis` MUST match Blender's import convention (Y), not citizen's native (X)

Blender's FBX importer re-rolls bones to its Y-down-bone convention. If we
export with `primary_bone_axis="X"` (citizen's authored convention), the
round-trip corrupts bind matrices and sbox renders the character grotesquely
even with no animation. We confirmed this with a no-anim passthrough test:
import citizen → export → wrap in vmdl → grotesque. Switching to
`primary_bone_axis="Y"` fixed it.

**Fix:** use Blender-native `primary_bone_axis="Y", secondary_bone_axis="X"`.

### 5. `pose_bone.rotation_quaternion` is the DELTA from rest, not the absolute local rotation

We initially computed the full bone-local rotation and assigned it to
`pose_bone.rotation_quaternion`. That property is the *matrix_basis delta*,
so we were double-applying the bind: identity input → bone at A-pose +
A-pose = grotesque distortion.

**Fix:** set `pose_bone.matrix` (armature-space target) and let Blender
back-solve matrix_basis. Or compute `matrix_basis = T_ref⁻¹ · local_q · T_ref`
directly (the conjugation form), which preserves identity-at-rest by
construction.

### 6. Use `local_quats_wxyz`, not `global_quats_xyzw`

kimodo exports both. The local quats are parent-local joint rotations,
which is what Blender's matrix_basis represents. Using global quats would
require chain composition we'd have to do manually.

**Fix:** read `clip["local_quats_wxyz"]` and convert with `Quaternion((w,x,y,z))`.

### 7. SMPL-X canonical rest has elevated clavicles and slight forearm-up

If you target SMPL-X canonical positions directly as your T-pose reference,
citizen's resulting "T-pose" inherits these quirks: shoulders shrugged,
elbows bent slightly. To get a clean horizontal T-pose, **zero the Y
component** of the target direction (drop the up/down tilt) and target pure
horizontal lateral.

For the forearm specifically, the SMPL-X canonical direction is already
nearly horizontal, so Y-zero alone produces only ~5° change. We hard-code
the forearm target to pure `(±1, 0, 0)` instead.

### 8. `comp` must be captured as `matrix_basis`, NOT `pose_bone.matrix`

When posing the arm chain to virtual T-pose, `pose_bone.matrix` reflects
parent's rotation propagated through the chain. Capturing it as `comp` and
re-applying as matrix_basis at runtime double-rotates the bone.

**Fix:** capture `pose_bone.rotation_quaternion` (= matrix_basis = the
bone's intrinsic delta from bind). Each bone's comp is then its OWN
contribution, not parent's propagated rotation.

### 9. Conjugation reference must match the bone's starting frame

The pure conjugation `matrix_basis = T_ref⁻¹ · local_q · T_ref` is correct
when the bone starts at bind (A-pose). When we add `comp` to start the bone
at T-pose, the conjugation reference must also be T-pose:

```
matrix_basis = comp · (T_virt⁻¹ · local_q · T_virt)
```

Using T_ref (A-pose bind) for the conjugation when comp puts the bone at
T-pose produces "palms facing wrong way" because motion is applied in the
wrong bone-local frame relative to where the bone actually is.

### 10. Blender 5 FBX `bake_anim` quirks

Blender 5's FBX bake_anim can silently drop animation data in some cases.
We hit this during a debugging detour but the final pipeline doesn't trip
it. If FBX export comes back with identity keyframes despite the action
having real data, check:
- All `keyframe_insert` calls are at unique frames
- `bake_anim_simplify_factor=0.0`
- `bake_anim_use_all_bones=True`
- `bake_anim_force_startend_keying=True`

`bpy.ops.nla.bake` is an alternative path but it crashes silently in
background mode (no UI context).

## Output FBX export settings (final)

```python
bpy.ops.export_scene.fbx(
    filepath=..., use_selection=True,
    object_types={"ARMATURE"},
    bake_anim=True,
    bake_anim_use_all_bones=True,
    bake_anim_use_nla_strips=False,
    bake_anim_use_all_actions=False,
    bake_anim_force_startend_keying=True,
    bake_anim_step=1.0,
    bake_anim_simplify_factor=0.0,
    add_leaf_bones=False,
    primary_bone_axis="Y",        # Blender-native, not citizen's X
    secondary_bone_axis="X",
    axis_forward="-Y",
    axis_up="Z",                  # Source 2 native
    bake_space_transform=False,   # don't add extra rotation to armature
    apply_unit_scale=True,
    apply_scale_options="FBX_SCALE_NONE",
)
```

## Verification gate

Bake the synthetic identity-pose clip `make_tpose_clip.py` produces. The
result (`tpose_check.fbx`) should display citizen in a clean horizontal
T-pose in ModelDoc. If you see A-pose, comp isn't being applied. If you see
distorted geometry, the bind round-trip is broken (probably
`primary_bone_axis` or `bake_space_transform`).

## Files

- `bake.py` — main baker, takes one kimodo JSON, produces one FBX
- `batch_bake.py` — curated multi-clip wrapper (edit `CURATED` dict to add clips)
- `gen_vmdl.py` — emits the wrapper vmdl from a directory of baked FBXs
- `make_tpose_clip.py` — synthetic identity-pose JSON for the verification gate
