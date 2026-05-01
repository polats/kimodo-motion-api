import * as THREE from 'three'
import { SMPLX_REST_WORLD, KIMODO_CHILD, KIMODO_PARENT, KIMODO_TWIST_REF } from './rigs.js'

// Alignment modes for the retargeter:
//   'frame'     — full per-bone rest-frame alignment (primary axis + twist ref).
//                 Best when meshes are skinned. May exaggerate joint gaps on
//                 non-skinned mesh chains because each bone rotates independently.
//   'direction' — axis-only alignment (primary direction). Looser correction;
//                 keeps non-skinned mesh chains visually continuous.
//   'none'      — pass kimodo's world rotations through unchanged. Only correct
//                 for rigs whose rest pose already matches SMPL-X (e.g., the
//                 baked SMPL-X exporter GLB).
export const ALIGN_MODES = ['frame', 'direction', 'none']

// Build an orthonormal rotation matrix from a primary axis + a reference
// direction (the secondary axis lies in the plane spanned by primary and
// reference, perpendicular to primary). Used to compute a per-bone "rest
// frame" from kimodo's SMPL-X rest data and from the target rig's loaded rest
// pose; aligning these frames gives a full retargeting rotation including
// twist, not just axis direction.
function frameFromPrimaryRef(primary, reference) {
  const x = primary.clone().normalize()
  const z = new THREE.Vector3().crossVectors(x, reference).normalize()
  // Degenerate (primary || reference): fall back to world up to choose a side.
  if (z.lengthSq() < 1e-6) {
    const fallback = Math.abs(x.y) < 0.99
      ? new THREE.Vector3(0, 1, 0)
      : new THREE.Vector3(1, 0, 0)
    z.crossVectors(x, fallback).normalize()
  }
  const y = new THREE.Vector3().crossVectors(z, x).normalize()
  return new THREE.Matrix4().makeBasis(x, y, z)
}

// Drives a SkinnedMesh from kimodo motion, retargeting onto whatever rig the
// SkinnedMesh has. Motion JSON shape (server response):
//   { fps, num_frames, bone_names: string[],
//     local_quats_wxyz: [T][J][4] (w,x,y,z) — for SMPL-X-equivalent rigs only,
//     global_quats_xyzw: [T][J][4] (x,y,z,w) — world-space, retarget-ready,
//     root_positions:    [T][3]            — world meters, kimodo frame }
//
// We use `global_quats_xyzw` and rest-pose-aware retargeting so the same motion
// drives both the SMPL-X exporter mesh and any Mixamo character (or anything
// else with a working bone mapping in rigs.js).
//
// The math:
//   Q_target_world(t)  =  Q_kimodo_world(t)  ·  Q_target_rest_world
//   Q_target_local(t)  =  Q_target_parent_world(t).invert()  ·  Q_target_world(t)
//
// Kimodo's canonical rest is identity world-orientation per joint (by
// construction in our SMPL-X exporter), so no kimodo-side rest correction.
//
// `groundOffsetY` is added to the scene root (already done in main.js) — we
// only translate the pelvis bone by `root_positions * scale - groundOffsetY`
// so the kimodo coordinate frame (feet at Y=0) lines up with three.js world.
export class Animator {
  // `target` can be either a SkinnedMesh (we use its skeleton.bones) OR an
  // arbitrary Object3D root (we collect descendant objects by name). The
  // retargeting math is identical — three.js Bone and Object3D share the
  // quaternion/position/matrixWorld surface.
  constructor(target, { mapping, scale = 1.0, groundOffsetY = 0, alignMode = 'frame' } = {}) {
    this.target = target
    this.mapping = mapping
    this.scale = scale
    this.groundOffsetY = groundOffsetY
    this.alignMode = alignMode

    let drivable
    if (target.isSkinnedMesh) {
      drivable = target.skeleton.bones
    } else {
      // Walk the subtree, collect every named Object3D.
      drivable = []
      target.traverse(o => { if (o.name) drivable.push(o) })
    }
    // three.js strips '.' from node names (reserved for animation binding paths
    // like '.position' or '.morphTargetInfluences[0]'). Normalize both sides on
    // lookup so rig configs can use the original Blender naming with .L/.R.
    const norm = (n) => n.replace(/\./g, '')
    this.bonesByName = Object.fromEntries(drivable.map(b => [norm(b.name), b]))
    this._normName = norm

    // Capture rest world transforms once, after letting parent transforms settle.
    target.updateMatrixWorld(true)
    this.restWorldQuats = new Map()
    this.restWorldPos = new Map()
    const tmpPos = new THREE.Vector3()
    const tmpScl = new THREE.Vector3()
    for (const name of Object.keys(this.bonesByName)) {
      const b = this.bonesByName[name]
      const p = new THREE.Vector3()
      const q = new THREE.Quaternion()
      b.matrixWorld.decompose(p, q, tmpScl)
      this.restWorldQuats.set(b.name, q)
      this.restWorldPos.set(b.name, p)
    }

    // Resolve mapping → ordered list (kimodoIdx, targetBone) in DFS order so
    // parents update before children when we apply per frame.
    this.pairs = this._buildPairs()
    this.pelvisBone = this.bonesByName[this._normName(this.mapping.pelvis)] || null
    this.pelvisRest = this.pelvisBone ? this.pelvisBone.position.clone() : null

    this.motion = null
    this.frame = -1
    this.elapsed = 0
    this.lastTime = 0
    this.playing = false
  }

  _buildPairs() {
    // We don't have the kimodo bone order until setMotion(), so we resolve
    // when the first motion arrives. Stub.
    this.pairs = null
    return null
  }

  _resolvePairsForMotion(motion) {
    // For each kimodo joint, find the target bone (if mapped + present).
    const pairs = []
    const missing = []
    const unmapped = []
    const idxByKimodoName = Object.fromEntries(motion.bone_names.map((n, i) => [n, i]))

    for (let kIdx = 0; kIdx < motion.bone_names.length; kIdx++) {
      const kName = motion.bone_names[kIdx]
      const tName = this.mapping[kName]
      if (!tName) { unmapped.push(kName); continue }
      const bone = this.bonesByName[this._normName(tName)]
      if (!bone) { missing.push(`${kName}->${tName}`); continue }

      // Compute per-bone rest-frame alignment R_align that rotates target's
      // rest bone frame (primary = bone direction, secondary = perpendicular
      // toward a reference joint) onto kimodo's rest frame. This handles both
      // direction errors AND twist errors — the secondary axis pins the
      // rotation around the primary axis, which a vector-only alignment can't.
      const alignment = this._computeAlignment(kName, bone)
      pairs.push({
        kimodoIdx: kIdx,
        kimodoName: kName,
        bone,
        alignQ: alignment.alignQ,
        alignMode: alignment.mode,
        restQ: this.restWorldQuats.get(bone.name),
      })
    }

    // Second pass: terminals (head, hands, feet, spine2 in some rigs) have no
    // child to derive a primary axis from, so they got 'none' above. Inherit
    // their parent's alignQ so they stay consistent with the parent's frame —
    // this fixes hand/foot orientation without authoring per-joint corrections.
    const byKName = Object.fromEntries(pairs.map(p => [p.kimodoName, p]))
    for (const p of pairs) {
      if (p.alignMode !== 'none') continue
      let pName = KIMODO_PARENT[p.kimodoName]
      while (pName) {
        const parent = byKName[pName]
        if (parent && parent.alignMode !== 'none') {
          p.alignQ = parent.alignQ.clone()
          p.alignMode = `inherit:${parent.alignMode}`
          break
        }
        pName = KIMODO_PARENT[pName]
      }
    }
    const fullFrame = pairs.filter(p => p.alignMode === 'frame').map(p => p.kimodoName)
    const dirOnly = pairs.filter(p => p.alignMode === 'direction').map(p => p.kimodoName)
    const noAlign = pairs.filter(p => p.alignMode === 'none').map(p => p.kimodoName)
    console.log('[retarget]',
      'resolved:', pairs.length,
      'fullFrame:', fullFrame.length, fullFrame,
      'directionOnly:', dirOnly,
      'noAlign:', noAlign,
      'unmapped:', unmapped,
      'missing-in-rig:', missing)
    this.pairs = pairs
  }

  _computeAlignment(kName, bone) {
    const alignQ = new THREE.Quaternion() // identity
    if (this.alignMode === 'none') return { alignQ, mode: 'none' }

    const childKName = KIMODO_CHILD[kName]
    const refKName = KIMODO_TWIST_REF[kName]
    if (!childKName) return { alignQ, mode: 'none' }

    const childTName = this.mapping[childKName]
    const childBone = childTName ? this.bonesByName[this._normName(childTName)] : null
    const refTName = refKName ? this.mapping[refKName] : null
    const refBone = refTName ? this.bonesByName[this._normName(refTName)] : null

    const k0 = SMPLX_REST_WORLD[kName]
    const kC = SMPLX_REST_WORLD[childKName]
    const kR = refKName ? SMPLX_REST_WORLD[refKName] : null
    const t0 = this.restWorldPos.get(bone.name)
    const tC = childBone ? this.restWorldPos.get(childBone.name) : null
    const tR = refBone ? this.restWorldPos.get(refBone.name) : null
    if (!k0 || !kC || !t0 || !tC) return { alignQ, mode: 'none' }

    const kPrim = new THREE.Vector3(kC[0] - k0[0], kC[1] - k0[1], kC[2] - k0[2])
    const tPrim = new THREE.Vector3().subVectors(tC, t0)

    // 'direction' mode: only align the primary axis. Cheaper, doesn't constrain
    // twist around the bone, but doesn't introduce as much rotation that can
    // pull rigid mesh chunks apart.
    if (this.alignMode === 'direction' || !kR || !tR) {
      alignQ.setFromUnitVectors(tPrim.normalize(), kPrim.normalize())
      return { alignQ, mode: 'direction' }
    }

    // 'frame' mode: full primary + twist alignment.
    const kRef = new THREE.Vector3(kR[0] - k0[0], kR[1] - k0[1], kR[2] - k0[2])
    const tRef = new THREE.Vector3().subVectors(tR, t0)
    const kFrame = frameFromPrimaryRef(kPrim, kRef)
    const tFrame = frameFromPrimaryRef(tPrim, tRef)
    const align = new THREE.Matrix4().multiplyMatrices(kFrame, tFrame.clone().invert())
    alignQ.setFromRotationMatrix(align)
    return { alignQ, mode: 'frame' }
  }

  setAlignMode(mode) {
    if (!ALIGN_MODES.includes(mode)) return
    this.alignMode = mode
    if (this.motion) this._resolvePairsForMotion(this.motion)
  }

  setMotion(motion, { loop = true } = {}) {
    if (!motion.global_quats_xyzw) {
      console.warn('Motion missing global_quats_xyzw; retargeting will not work for non-SMPL-X rigs.')
    }
    this.motion = motion
    this.loop = loop
    this.frame = -1
    this.elapsed = 0
    this.lastTime = performance.now() / 1000
    this.playing = true
    this._resolvePairsForMotion(motion)
  }

  stop() {
    this.playing = false
  }

  update() {
    if (!this.playing || !this.motion || !this.pairs) return
    const now = performance.now() / 1000
    const dt = now - this.lastTime
    this.lastTime = now
    this.elapsed += dt

    const m = this.motion
    const target = Math.floor(this.elapsed * m.fps)
    let f
    if (this.loop) {
      f = ((target % m.num_frames) + m.num_frames) % m.num_frames
    } else {
      f = Math.min(target, m.num_frames - 1)
      if (target >= m.num_frames) this.playing = false
    }
    if (f === this.frame) return
    this.frame = f

    const tmpQk = new THREE.Quaternion()
    const tmpQworld = new THREE.Quaternion()
    const tmpQparent = new THREE.Quaternion()
    const tmpPos = new THREE.Vector3()
    const tmpScl = new THREE.Vector3()

    if (m.global_quats_xyzw) {
      // World-space retargeting with per-bone rest-direction alignment.
      // Q_target_world = Q_kimodo_world · R_align[j]
      // where R_align rotates target's rest bone direction onto SMPL-X's.
      for (const { kimodoIdx, bone, alignQ } of this.pairs) {
        const q = m.global_quats_xyzw[f][kimodoIdx]
        tmpQk.set(q[0], q[1], q[2], q[3])
        tmpQworld.copy(tmpQk).multiply(alignQ)

        if (bone.parent) {
          bone.parent.matrixWorld.decompose(tmpPos, tmpQparent, tmpScl)
          tmpQparent.invert()
          bone.quaternion.copy(tmpQparent).multiply(tmpQworld)
        } else {
          bone.quaternion.copy(tmpQworld)
        }
        bone.updateMatrixWorld(true)
      }
    } else if (m.local_quats_wxyz) {
      // Fallback: legacy SMPL-X-only path (assumes target rest matches kimodo).
      for (const { kimodoIdx, bone } of this.pairs) {
        const q = m.local_quats_wxyz[f][kimodoIdx]
        bone.quaternion.set(q[1], q[2], q[3], q[0])
      }
    }

    // Pelvis translation. kimodo's root_positions is in meters, world frame
    // with feet at Y=0. Scale to character size, lift by groundOffsetY (the
    // amount we shifted the scene to anchor rest feet on Y=0).
    if (this.pelvisBone && m.root_positions) {
      const [x, y, z] = m.root_positions[f]
      const s = this.scale
      // Note: pelvis local position controls the skeleton root within the
      // scene. We pre-anchored the scene root to Y=groundOffsetY, so to get
      // pelvis world Y == kimodo y, we subtract that out.
      this.pelvisBone.position.set(
        x * s,
        y * s - this.groundOffsetY,
        z * s,
      )
    }
  }
}
