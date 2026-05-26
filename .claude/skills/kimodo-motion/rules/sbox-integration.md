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

## Previewing kimodo motion on an s&box citizen in the web viewer

The baker above is the **production** path (offline, Blender-solved, clean). To
*preview* kimodo motion on an s&box citizen **live** in the `/kata` web viewer,
you cannot use the citizen's native rig — it does not live-retarget cleanly:

- Bind is an **A-pose** (arms ~45° down); kimodo motion is authored relative to
  SMPL-X's near-**T** rest, so the live retargeter's `'rest'`/`'frame'` align
  modes contort the arms (this is exactly what the baker solves with its virtual
  T-pose conjugation — see "Bake math" in `baker/README.md`).
- Citizen bone local axes are head→tail **+X** (not the convention the retargeter
  assumes), plus split-skinning **twist bones** not parented under their main bone.
- The raw FBX loads **lying down** (Source 2 is Z-up; three's `FBXLoader` doesn't
  reorient into the Y-up world) and **huge** (cm, not metres).

**The fix that works: re-rig the citizen MESH onto a clean standard skeleton,
posed to a T, via UniRig.** Then it retargets like any clean T-pose rig
(`alignMode:'rest'`, the same path Mixamo uses).

### Pipeline (run per body — sausage `citizen_lod2`, `…male_REF`, `…female_REF`)

1. **T-pose + extract** → `web/scripts/tpose_citizen_mesh.py` (headless Blender):
   imports the citizen FBX, poses the arm chain to a virtual T (same logic as
   `baker/bake.py`'s `capture_virtual_tpose_quats`), **bakes** that pose into the
   geometry, **applies each sub-mesh's object transform *before* joining** — the
   citizen FBX mixes cm/m sub-mesh scales, and joining without this flattens the
   whole thing into a 9×9 blob — normalizes to 1.8 m, exports a bare GLB.
   `blender -b -P web/scripts/tpose_citizen_mesh.py -- <citizen.fbx> <out.glb>`
   (sanity-check the output is ~`x1.8 y0.3 z1.8`, i.e. an upright T-pose human).
2. **Auto-rig** via the UniRig service (`POST /rig`, multipart `file`):
   `curl -F file=@out.glb http://localhost:8081/rig -o rigged.glb`.
   ⚠ UniRig shares the 24 GB GPU with kimodo; its skin step **OOMs** unless you
   free VRAM first: `docker stop text-encoder` (kimodo's ~15 GB encoder), rig,
   then `docker start text-encoder`.
3. **Register** → `python web/scripts/import_unirig_glb.py rigged.glb --id
   unirig_citizen_male --label "sbox Citizen Male (UniRig)"` — copies to
   `web/public/models/` and writes `.kimodo-characters/<id>.json` with a 22-joint
   `mapping` (`unirig_mapping.py` labels UniRig's anonymous `bone_N` by topology +
   X-sign, so the rig must be upright/standard for it to label correctly).
4. **Show it** → add the id to `CURATED_MODEL_IDS` in `web/src/kata.js` (the /kata
   MODEL drawer), or it surfaces via `GET /characters` automatically.

**Why the T-pose is non-negotiable:** UniRig fits its skeleton's rest to the
*input* pose. Rig an A-pose mesh → rest is A-pose → same mismatch → contorted.
Rig a T-pose mesh → rest ≈ SMPL-X → clean. Verify the rigged output is upright
(Y-up: head at top, feet at bottom, wrists lateral, left=+X/right=−X).

### Citizen model facts (the LOD / head trap)

- `citizen.vmdl` is **modular**: a body (torso/legs/hands/feet) + a **Head
  bodygroup** + clothing. Heads are **separate models** (`citizen_human/heads/…`).
- The Head bodygroup has LODs **0, 2, 3, 4 — there is no LOD1**. So
  `citizen_lod1.fbx` (the "sausage") is **headless**; use **`citizen_lod2.fbx`**
  (abstract body + `CitizenHead_LOD2`) or the full `citizen_human_{male,female}_REF.fbx`.
- Units are **cm** (×0.01 → metres); FBX is **Z-up**. Source FBXs ship in the
  s&box install under `…/addons/citizen/Assets/models/citizen…`.

### Web-viewer gotchas
- Clips generated right after the diffusion model lazy-loads can be saved with
  **empty `bone_names`**; the Animator falls back to kimodo's canonical 22-joint
  order (`Object.keys(KIMODO_PARENT)` in `web/src/rigs.js`) so retargeting still
  resolves. (Fix the API side too if other consumers read the JSON.)
- Loading the native FBX directly needs `FBXLoader` + a `unifySkeletons` pass
  (citizen = several meshes sharing one skeleton) + twist-bone syncing — the
  T-pose→UniRig GLB avoids all of that.

## Reference implementation

woid (in the `sbox-public` repo) is the worked example: `KimodoSequencePlayer`
(takeover + root motion), `ReadingLayer` (masked overlay), `HoldableProp`
(`UseClip`/`UseMode`/`UseBothArms`), and `examples/woid/docs/object-interactions.md`.
