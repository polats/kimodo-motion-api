# Ragdoll (web)

Press a button to instantly turn the currently-animating character into a
physics ragdoll. Uses Rapier 3D (`@dimforge/rapier3d-compat`) on top of the
existing three.js + Vite app.

## Two implementations

There are two ragdoll classes in `src/ragdoll.js`. The UI exposes a
`capsule` / `block` radio in row-ragdoll that picks between them.

### `Ragdoll` (capsule)

- One capsule body **per kimodo bone** (22 bodies on a fully-mapped rig).
- Driven directly by the existing kimodo bone graph (`KIMODO_PARENT`,
  `KIMODO_CHILD`, `KIMODO_TWIST_REF` from `rigs.js`).
- Built on click; disposed on Reset.
- Most knobs apply only to this impl: `pelvis-as-hip-belt`,
  `min body length`, `rigid extremities`, `rigid torso`, `hinge knees+elbows`.

The capsule impl is kept around as the alternate / reference but the
**block impl is what we're iterating on.**

### `BlockRagdoll` (default)

15 oriented cuboid bodies, modeled after the
[mattvb91/rapierjs-ragdoll](https://github.com/mattvb91/rapierjs-ragdoll)
demo and adapted to our deeper bone hierarchy.

| Body          | Drives bone     | Source / shape                                |
|---------------|-----------------|-----------------------------------------------|
| `torso`       | `pelvis`        | hip-mid → neck-base; ~shoulder-spread × torso-depth |
| `neck`        | `neck`          | neck-base → head-base                         |
| `head`        | `head`          | 18 cm cube above the head bone position       |
| `armUpper{L,R}` | `*_shoulder`  | shoulder → elbow                              |
| `armLower{L,R}` | `*_elbow`     | elbow → wrist                                 |
| `hand{L,R}`   | `*_wrist`       | wrist + 10 cm forward along the forearm dir   |
| `thigh{L,R}`  | `*_hip`         | hip → knee                                    |
| `shin{L,R}`   | `*_knee`        | knee → ankle                                  |
| `foot{L,R}`   | `*_ankle`       | flat box, ankle → toe **projected onto the ground plane** |

Bones with no body (`spine1/2/3`, `*_collar`, `*_foot` toes) are
intermediates: their world transforms follow the driven parents naturally
because three.js composes `bone.matrixWorld = parent.matrixWorld *
bone.matrix` and we don't touch their local matrix during ragdoll.

#### Joint topology

| Joint                     | Type     | Why                                           |
|---------------------------|----------|-----------------------------------------------|
| `head` ↔ `neck`           | fixed*   | Stops 360° head spin (`fixedHead` toggle)     |
| `neck` ↔ `torso`          | spherical | The head+neck assembly's flex point           |
| `armUpper{L,R}` ↔ `torso` | spherical | Shoulder ball joint                           |
| `armLower{L,R}` ↔ `armUpper{L,R}` | revolute (±π/2) | Elbow hinge (`hingeKneesElbows`)   |
| `hand{L,R}` ↔ `armLower{L,R}` | fixed* | Stops independent hand rotation (`fixedHandsFeet`) |
| `thigh{L,R}` ↔ `torso`    | spherical | Hip ball joint                                |
| `shin{L,R}` ↔ `thigh{L,R}` | revolute (±π/2) | Knee hinge (`hingeKneesElbows`)        |
| `foot{L,R}` ↔ `shin{L,R}` | fixed*   | Stops foot flopping (`fixedHandsFeet`)        |

\* fixed when the toggle is on; spherical otherwise.

`joint.setContactsEnabled(false)` is set on every joint so adjacent
bodies (which overlap by construction at their shared anchor) don't fight
each other through contact resolution. Self-collision is enabled globally
so non-adjacent body parts (e.g., a hand vs the torso) push apart instead
of clipping through.

## Architecture: kinematic-to-dynamic switch

This is the load-bearing design choice for the block impl.

```
character loads ──▶ initPhysics() ──▶ build BlockRagdoll
                                      bodies = KinematicPositionBased
                                      activated = false
                                      
each frame (tracking mode):
  animator.update() / mixer.update()
  pushKinematic()  ─▶  for each body:
                          read bone.matrixWorld
                          solve for body's desired world transform
                          setNextKinematicTranslation/Rotation
  world.step()     ─▶  Rapier interpolates and tracks per-body velocity

click Ragdoll:
  activate()       ─▶  for each body:
                          setBodyType(Dynamic, true)
                       (Rapier preserves the velocity it was tracking)
  
each frame (ragdoll mode):
  world.step()     ─▶  solver integrates bodies under gravity + joints
  _sync()          ─▶  for each body in DFS order:
                          read body.translation + rotation
                          write to bone (local pos/quat via parent inverse)

click Reset:
  deactivate()     ─▶  restore bone snapshots; setBodyType(Kinematic, true)
                       tracking resumes next frame
```

Key consequences:

- **Velocity transfer is automatic.** Rapier has been measuring each body's
  inter-frame motion the whole time. When we flip to Dynamic, that velocity
  carries over. Ragdolling mid-run tumbles forward; ragdolling mid-fall keeps
  falling.
- **Damping is applied at activation, not construction.** Damping reduces
  velocity each step; if we damped during kinematic tracking, momentum
  transfer would bleed away.
- **Snapshots are taken at activate(), not build.** Reset restores to the
  pose the user clicked Ragdoll at, not the rest pose.
- **Physics steps every frame**, not just during ragdoll. Cost is sub-ms
  for 15 bodies / 14 joints — negligible.

## Options (defaults are good)

`BLOCK_DEFAULTS` in `src/ragdoll.js`. UI checkboxes in `index.html` row
`#row-ragdoll` mirror them.

| Option              | What it does                                                          |
|---------------------|------------------------------------------------------------------------|
| `selfCollide`       | Bones collide with each other (vs phasing through)                     |
| `jointContactsOff`  | Suppress contacts between joint-connected pairs                        |
| `damping`           | Apply angular (5.0) + linear (0.3) damping at activation               |
| `fixedHead`         | head ↔ neck is a fixed joint (vs spherical)                            |
| `fixedHandsFeet`    | hand ↔ forearm and foot ↔ shin are fixed joints                        |
| `hingeKneesElbows`  | Knees + elbows are revolute (1-axis) instead of spherical              |
| `lockLimbTwist`     | Reserved (set defined, no joint branch wired yet)                      |
| `debug`             | Wireframe box overlay; `setDebug()` makes this live-toggleable          |

Capsule-only options live next to these; they're hidden when `block` is
selected (`label[data-impl="capsule"]`).

## Files

- `src/ragdoll.js` — `initPhysics`, `Ragdoll`, `BlockRagdoll`, both default
  option tables, the test-capsule debug primitive, `worldToBodyLocal`
  helper.
- `src/main.js` — `setupBlockTracking`, `onCharacterChanged`, `onImplChange`,
  the tick-loop branching, the Ragdoll button toggle (`activate` vs
  `deactivate` for block; build/dispose for capsule), the live debug
  checkbox handler.
- `index.html` — `#row-ragdoll` panel, capsule/block radio, all checkboxes.

## Gotchas

- **GLB bone name normalization.** Three.js GLTFLoader strips `:` from
  Mixamo names (`mixamorig:Hips` → `mixamorigHips`). The animator's
  `_normName` does the same to mapping table values. The block ragdoll
  reuses `animator.bonesByName` and `animator._normName` so any bone the
  animator can find, the ragdoll can find.
- **Rapier deprecation warning** at init mentions "deprecated parameters
  for the initialization function." Internal to `rapier3d-compat`,
  doesn't affect behavior; ignore.
- **Bundle size.** `rapier3d-compat` inlines its WASM, taking the bundle
  from ~700 kB to ~2.9 MB. The non-compat build needs Vite WASM serving
  config; not done.
- **Joint anchors at coincident points.** Many parent/child bones in our
  rigs share a world position (pelvis-head ≈ spine1-head). For the capsule
  impl, this triggers a degenerate-length fallback handled via
  `pelvis-as-hip-belt` + `min body length`. The block impl avoids it by
  not modeling pelvis as a separate joint chain.
- **Spherical joints have no Rapier limits API in this version.**
  `SphericalImpulseJoint` doesn't extend `UnitImpulseJoint`, so no
  `setLimits` / `configureMotor*`. Only revolute and prismatic do. Cone
  limits would need `JointData.generic()` (more involved); we substitute
  with fixed joints + damping for problem joints.
- **Per-joint hinge axis is world-X.** We pick `(1,0,0)` and convert to
  parent-body-local at build time. Works for upright characters; would
  drift for sideways or upside-down rigs. Asymmetric anatomical limits
  (knee 0..150° in one direction, not symmetric ±π/2) need the character's
  forward direction; not detected yet.

## Future work

Listed roughly in order of impact / ease.

1. **Drop the test-capsule button** from the main UI (or hide behind a
   `?debug=physics` query). It was a step-1 sanity check; ragdoll works.
2. **Tune the neck-torso joint.** Right now it's a free spherical joint
   relying on the body's angular damping. If it feels too floppy, the
   options are: bumping `ANGULAR_DAMPING` higher just on the neck body,
   or replacing with a `JointData.generic()` with limited rotational axes.
3. **Anatomical hinge limits.** Knees and elbows are symmetric ±π/2.
   Real knees bend 0..150° one way only. Needs character-facing detection
   so we know which sign to use. Toggle-able.
4. **Cone limits on hips and shoulders.** Currently free spherical. Real
   shoulders are ~120° cone. Same Rapier limitation as the neck — needs
   `JointData.generic()` to pull off properly.
5. **Wire `lockLimbTwist` for hips/shoulders.** Set is defined but no
   joint branch consumes it. Would lock rotation around the limb's long
   axis (the most visually-jarring kind of unrealistic spin).
6. **Per-character ragdoll tuning.** Sizes / masses / damping in
   `BLOCK_DEFAULTS` are tuned for SMPL-X-scale humans (~1.7 m). Robots /
   stylized characters with different proportions might need overrides
   alongside `mapping` and `scale` in `rigs.js`.
7. **Capsule ragdoll: kinematic mode.** Currently only `BlockRagdoll`
   does kinematic-to-dynamic. The capsule impl still builds-on-click,
   which means it loses the running-momentum transfer. Same pattern would
   port over.
8. **Get-up animation / pose blending out.** Reset currently snaps back to
   the snapshotted pose. A real game would lerp from the ragdoll's final
   pose into a "stand up" animation.
