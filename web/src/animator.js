import { Quaternion, Vector3 } from 'three'

// Drives a SkinnedMesh from kimodo motion data:
//   { fps, num_frames, bone_names, local_quats_wxyz: [T][J][4], root_positions: [T][3] }
//
// Notes:
// - kimodo emits quaternions in (w, x, y, z) order; three.js uses (x, y, z, w). We swap.
// - root_positions are world-space translations *of the pelvis*, not deltas from rest.
//   We assign them directly (overriding the GLB's rest pelvis position).
export class Animator {
  // groundOffsetY: amount the GLB scene was raised so its feet rest at world Y=0.
  // Kimodo's `root_positions` are in a frame where ground is at Y=0; we must subtract
  // this offset before assigning to the pelvis bone (which lives inside the offset scene).
  constructor(skinnedMesh, { groundOffsetY = 0 } = {}) {
    this.skinned = skinnedMesh
    this.bonesByName = Object.fromEntries(skinnedMesh.skeleton.bones.map(b => [b.name, b]))
    this.groundOffsetY = groundOffsetY
    this.motion = null
    this.frame = 0
    this.lastTime = 0
    this.elapsed = 0
    this.playing = false
  }

  setMotion(motion, { loop = true } = {}) {
    this.motion = motion
    this.loop = loop
    this.frame = 0
    this.elapsed = 0
    this.lastTime = performance.now() / 1000
    this.playing = true
  }

  stop() {
    this.playing = false
  }

  // Call from your render loop. dt in seconds.
  update() {
    if (!this.playing || !this.motion) return
    const now = performance.now() / 1000
    const dt = now - this.lastTime
    this.lastTime = now
    this.elapsed += dt

    const { fps, num_frames, bone_names, local_quats_wxyz, root_positions } = this.motion
    const target = Math.floor(this.elapsed * fps)
    let f = target
    if (this.loop) {
      f = ((target % num_frames) + num_frames) % num_frames
    } else if (target >= num_frames) {
      f = num_frames - 1
      this.playing = false
    }
    if (f === this.frame) return
    this.frame = f

    // Apply local-rotation per bone.
    for (let j = 0; j < bone_names.length; j++) {
      const bone = this.bonesByName[bone_names[j]]
      if (!bone) continue
      const q = local_quats_wxyz[f][j]
      bone.quaternion.set(q[1], q[2], q[3], q[0]) // wxyz -> xyzw
    }
    // Pelvis world translation — bone-space is local to its parent (the rig root group),
    // so for the root joint this is effectively world-space within the armature.
    const pelvis = this.bonesByName.pelvis
    if (pelvis) {
      const [x, y, z] = root_positions[f]
      pelvis.position.set(x, y - this.groundOffsetY, z)
    }
  }
}
