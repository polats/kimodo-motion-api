# Using Kimodo clips in s&box

The s&box export path: a motion-API JSON clip → a baked **citizen FBX** →
wrapped in one `kimodo_anims.vmdl` as a `kim_<name>` sequence the engine plays.
Pair this with the **`sbox-gamedev`** skill for the engine side.

## Baking (`kimodo-bake`, or `baker/` directly)

```
kimodo-bake wave <clip-id> --out <project>/Assets/models/kimodo
```

Under the hood (`baker/bake.py` per clip, `baker/batch_bake.py` for a curated
set, `baker/gen_vmdl.py` for the wrapper):

- **Input:** the citizen reference FBX (ships with s&box at
  `…/addons/citizen/Assets/models/citizen_human/citizen_human_male_REF.fbx`;
  autodetected, or `--citizen-fbx`).
- **Per clip:** import the citizen armature, retarget the SMPL-X rotations onto
  citizen bones in Blender, export an **armature-only FBX** (no mesh; twist/IK
  helpers left at bind so the engine's constraint system drives them).
- **`ExtractMotion` is the keystone for root motion** — `gen_vmdl.py` adds an
  `ExtractMotion` node per `AnimFile` (`root_bone_name = pelvis`,
  `extract_tx/ty = true`, `extract_tz = false`). Without it `RootMotion` is zero
  and the clip animates in place / the capsule never moves. Vertical bob is left
  in the mesh, so jumps still bob.
- **Output:** one `kimodo_anims.vmdl` (base model = citizen) with one `AnimFile`
  per FBX. `gen_vmdl` walks every `*.fbx` in `--out`, so re-running picks up the
  new clip alongside existing ones. **`kimodo-bake` regenerates the vmdl for
  you.** The editor auto-compiles the changed `.vmdl` to `.vmdl_c` while running.
- **Naming:** clips are prefixed **`kim_`** (so `wave` → sequence `kim_wave`).
  Filter them in-engine with that prefix vs the inherited citizen sequences.

`batch_bake.py` keeps a `CURATED` dict (name → clip id) for reproducible bakes —
add an entry there for clips you want regenerable, then re-run it.

## Playing a clip in-engine — two patterns

### 1. Full-body takeover — whole-body clips (dance, locomotion, a full sit)
Set `SkinnedModelRenderer.UseAnimGraph = false`, `Sequence.Name = "kim_x"`. The
sequence owns **every bone + the root**. In a live character this fights the
NavMeshAgent (owns position) and your movement/facing code, so **suspend them**
for the clip's duration (disable the agent + your controller; restore on stop).
Consume root motion each tick: read `SkinnedModelRenderer.RootMotion` (a local
delta), rotate into world space, feed through `CharacterController.MoveTo` so it
sweeps and collides. (woid: `KimodoSequencePlayer`.)

### 2. Masked upper-body overlay — "do X with your arms while still moving"
This is how a character reads/drinks **while walking or sitting**. Keep
`UseAnimGraph = true` (the animgraph drives legs/pelvis/locomotion) and override
**only upper-body bones** (`spine_2` + neck + head + one or both arms) via the
`GameObjectFlags.ProceduralBone` flag on those bone GameObjects, writing their
`LocalRotation` each frame. Unflagged bones keep animating normally, so it
composes instead of taking over. Blend a 0→1 weight in/out; at weight 0 write the
bone's own animgraph local (read via `TryGetBoneTransformAnimation`) so it can't
pop. (woid: `ReadingLayer`.)

## The retargeting gotcha (read this before "the arms look wrong")

**Do not re-retarget the raw kimodo JSON quaternions at runtime.** A direct
basis-conjugation retarget gets limb **roll** wrong — elbows twist inside-out.
The **baked sequence is already correctly retargeted** (Blender solved it).

So for the overlay, **sample the baked sequence, don't recompute it**: play
`kim_x` on a **hidden proxy** `SkinnedModelRenderer` (same model,
`UseAnimGraph = false`; `SceneObject.RenderingEnabled = false` so it ticks and
poses but never draws), then copy the proxy's **local** bone rotations onto the
live character's procedural bones. Local rotations are parent-relative, so they
compose with the live (facing-correct) torso for free. A non-rendered renderer
still advances its sequence and evaluates its pose.

Other gotchas:
- The bind-pose rotation of a bone is `Model.GetBoneTransform(name).Rotation`
  (a static model query) — don't walk `Bone.Parent` to the root; the root's
  parent is an invalid sentinel whose members throw.
- One-handed action (e.g. drinking) on a one-handed prop: mask to that arm +
  torso/head only, leaving the other arm on the animgraph.
- `RenderType` on a renderer is `ShadowRenderType` (On/Off/ShadowsOnly) — it's
  about shadows; use `SceneObject.RenderingEnabled = false` to actually hide a
  model that must keep updating.

## Reference implementation

woid (in the `sbox-public` repo) is the worked example: `KimodoSequencePlayer`
(takeover + root motion), `ReadingLayer` (masked overlay), `HoldableProp`
(`UseClip`/`UseMode`/`UseBothArms`), and `examples/woid/docs/object-interactions.md`.
