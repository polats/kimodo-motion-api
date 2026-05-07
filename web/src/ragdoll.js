// Rapier physics integration.
//
// Step 1: a single dynamic capsule synced to a three.js mesh (smoke test).
// Step 2: a real ragdoll built from a character's current pose — one body
//         per mapped kimodo bone, connected by spherical joints. No motion
//         transfer yet, so the figure starts at rest and falls under gravity.

import * as THREE from 'three'
import RAPIER from '@dimforge/rapier3d-compat'
import { KIMODO_CHILD, KIMODO_PARENT } from './rigs.js'

let world = null
let ready = false
const syncFns = []  // run after each batch of world.step() calls

export async function initPhysics() {
  if (ready) return
  await RAPIER.init()  // bundled WASM in the -compat build
  world = new RAPIER.World({ x: 0, y: -9.81, z: 0 })

  // Visual floor in main.js sits at y=0; match it with a thick static cuboid.
  // Membership group 1, collides with everything (default 0xFFFF filter).
  const groundDesc = RAPIER.ColliderDesc.cuboid(50, 0.05, 50)
    .setTranslation(0, -0.05, 0)
    .setFriction(0.9)
  world.createCollider(groundDesc)

  ready = true
}

export function isReady() { return ready }
export function getWorld() { return world }

let accumulator = 0
const FIXED_DT = 1 / 60

export function stepPhysics(deltaSeconds) {
  if (!ready) return
  accumulator += Math.min(deltaSeconds, 0.1)  // clamp huge deltas (tab unhide)
  while (accumulator >= FIXED_DT) {
    world.step()
    accumulator -= FIXED_DT
  }
  for (const fn of syncFns) fn()
}

// --- Test capsule (step 1) ------------------------------------------------

export function spawnTestCapsule(scene, position = { x: 0, y: 2.5, z: 0 }) {
  if (!ready) throw new Error('initPhysics() must complete first')

  const halfHeight = 0.25
  const radius = 0.15

  const bodyDesc = RAPIER.RigidBodyDesc.dynamic()
    .setTranslation(position.x, position.y, position.z)
    .setRotation({ x: 0.2, y: 0, z: 0.1, w: 1 })
  const body = world.createRigidBody(bodyDesc)

  const colliderDesc = RAPIER.ColliderDesc.capsule(halfHeight, radius)
    .setRestitution(0.2)
    .setFriction(0.8)
  world.createCollider(colliderDesc, body)

  const geom = new THREE.CapsuleGeometry(radius, halfHeight * 2, 6, 12)
  const mat = new THREE.MeshStandardMaterial({ color: 0xe28a4a, roughness: 0.6 })
  const mesh = new THREE.Mesh(geom, mat)
  scene.add(mesh)

  syncFns.push(() => {
    const t = body.translation()
    const r = body.rotation()
    mesh.position.set(t.x, t.y, t.z)
    mesh.quaternion.set(r.x, r.y, r.z, r.w)
  })
  return { body, mesh }
}

// --- Ragdoll (step 2) -----------------------------------------------------

const UP = new THREE.Vector3(0, 1, 0)

// Group used when self-collision is toggled OFF: every ragdoll collider goes
// into group 2 with a filter that excludes group 2 (so bones don't collide
// with each other) but includes everything else (so the floor still does).
const RAGDOLL_GROUP_NO_SELF = (0x0002 << 16) | 0xFFFD

// Default tuning. Each entry is a knob we expect to revisit as we iterate
// on ragdoll quality. Exposed via the UI panel and overridable per-build.
export const RAGDOLL_DEFAULTS = {
  selfCollide: true,        // bones collide with each other (vs phasing through)
  jointContactsOff: true,   // suppress contacts only between joint-connected pairs
  damping: true,            // angular + linear damping to settle wild spinning
  pelvisAsHipBelt: true,    // pelvis = horizontal capsule between hip joints (vs tiny pelvis→spine1 stub)
  minBodyLength: true,      // floor every capsule at 8cm so no body is a near-pebble
  hingeKneesElbows: true,   // knees + elbows are 1-axis hinges with angle limits (vs free spherical)
  rigidExtremities: true,   // head/hands/feet rigidly fixed to parent (no 360° flopping)
  rigidTorso: true,         // spine + neck + collars fixed to parent → torso is one rigid block
  debug: false,             // wireframe capsules over the skin
}

const MIN_BODY_LEN = 0.08
const ANGULAR_DAMPING = 5.0
const LINEAR_DAMPING = 0.3
const HINGE_BONES = new Set(['left_knee', 'right_knee', 'left_elbow', 'right_elbow'])
const RIGID_EXTREMITY_BONES = new Set(['head', 'left_wrist', 'right_wrist', 'left_foot', 'right_foot'])
// Spine + neck + collars: in real anatomy these flex, but Rapier's spherical
// joints have no angular limits, so any free 3-DoF joint here lets the torso
// corkscrew or the neck spin. Fixing them is the cheap substitute for cone
// limits — the torso becomes one rigid mass that flops at the hips/shoulders.
const RIGID_TORSO_BONES = new Set(['spine1', 'spine2', 'spine3', 'neck', 'left_collar', 'right_collar'])

export class Ragdoll {
  constructor({ animator, root, scene, options = {} }) {
    if (!ready) throw new Error('initPhysics() must complete first')
    this.animator = animator
    this.root = root
    this.scene = scene
    this.options = { ...RAGDOLL_DEFAULTS, ...options }
    this.debug = this.options.debug

    this.bodies = []          // { kName, body, bone, bodyOriginToHead, bodyToBoneRot }
    this.joints = []
    this.debugMeshes = []
    this.boneSnapshots = []   // for restore on dispose: { bone, position, quaternion }
    this._dfsOrdered = null

    this._build()
    this._syncFn = () => this._sync()
    syncFns.push(this._syncFn)
  }

  _build() {
    const { mapping, bonesByName, _normName } = this.animator
    this.root.updateMatrixWorld(true)

    // Resolve each kimodo joint → { bone, current world pos, current world rot }.
    const tmpScl = new THREE.Vector3()
    const data = {}
    for (const [kName, tName] of Object.entries(mapping)) {
      if (!tName) continue
      const bone = bonesByName[_normName(tName)]
      if (!bone) continue
      const p = new THREE.Vector3()
      const q = new THREE.Quaternion()
      bone.matrixWorld.decompose(p, q, tmpScl)
      data[kName] = { bone, worldPos: p, worldQuat: q }
      this.boneSnapshots.push({
        bone,
        position: bone.position.clone(),
        quaternion: bone.quaternion.clone(),
      })
    }

    const nearestMappedChild = (kName) => {
      let c = KIMODO_CHILD[kName]
      while (c && !data[c]) c = KIMODO_CHILD[c]
      return c
    }
    const nearestMappedParent = (kName) => {
      let p = KIMODO_PARENT[kName]
      while (p && !data[p]) p = KIMODO_PARENT[p]
      return p
    }

    // One body per mapped bone. Capsule extends from the bone's head toward
    // its nearest mapped child (or, for terminals, continues the parent's
    // direction so the body has plausible orientation and thickness).
    const allKNames = Object.keys(this.animator.mapping).filter(k => this.animator.mapping[k])
    const foundKNames = Object.keys(data)
    const missingKNames = allKNames.filter(k => !foundKNames.includes(k))
    console.log(`[ragdoll] mapped joints: ${allKNames.length}, found in rig: ${foundKNames.length}`)
    if (missingKNames.length) console.log(`[ragdoll] mapped but not found in rig:`, missingKNames)
    console.log(`[ragdoll] joints found:`, foundKNames)

    const bodiesByKName = {}
    for (const [kName, info] of Object.entries(data)) {
      let direction, length, midpoint, radius

      if (kName === 'pelvis' && this.options.pelvisAsHipBelt && data.left_hip && data.right_hip) {
        // Horizontal capsule between hip joints — anatomically the hip belt,
        // and gives the pelvis enough mass/extent to act as a stable root.
        const lh = data.left_hip.worldPos
        const rh = data.right_hip.worldPos
        const v = new THREE.Vector3().subVectors(rh, lh)
        length = Math.max(v.length(), MIN_BODY_LEN)
        direction = v.normalize()
        midpoint = new THREE.Vector3().addVectors(lh, rh).multiplyScalar(0.5)
        radius = Math.max(0.06, length * 0.4)  // chunkier than a limb segment
      } else {
        const childK = nearestMappedChild(kName)
        if (childK) {
          const v = new THREE.Vector3().subVectors(data[childK].worldPos, info.worldPos)
          length = v.length()
          if (length < 1e-4) length = 0.05  // degenerate pair (heads coincide)
          direction = v.normalize()
        } else {
          const parentK = nearestMappedParent(kName)
          if (parentK) {
            direction = new THREE.Vector3()
              .subVectors(info.worldPos, data[parentK].worldPos).normalize()
          } else {
            direction = UP.clone()
          }
          length = 0.08
        }
        if (this.options.minBodyLength) length = Math.max(length, MIN_BODY_LEN)
        radius = Math.max(0.025, Math.min(0.09, length * 0.28))
        midpoint = new THREE.Vector3()
          .copy(info.worldPos)
          .add(direction.clone().multiplyScalar(length / 2))
      }

      const halfLength = length / 2
      const halfHeight = Math.max(0.001, halfLength - radius)

      // Capsule's local +Y must align with `direction`. setFromUnitVectors
      // handles the anti-parallel case (legs pointing -Y) by picking an
      // arbitrary perpendicular axis — fine for step 2 (no joint limits).
      const bodyQuat = new THREE.Quaternion().setFromUnitVectors(UP, direction)

      const bodyDesc = RAPIER.RigidBodyDesc.dynamic()
        .setTranslation(midpoint.x, midpoint.y, midpoint.z)
        .setRotation({ x: bodyQuat.x, y: bodyQuat.y, z: bodyQuat.z, w: bodyQuat.w })
      if (this.options.damping) {
        bodyDesc.setAngularDamping(ANGULAR_DAMPING)
        bodyDesc.setLinearDamping(LINEAR_DAMPING)
      }
      const body = world.createRigidBody(bodyDesc)

      const colDesc = RAPIER.ColliderDesc.capsule(halfHeight, radius)
        .setFriction(0.8)
        .setRestitution(0.05)
        .setDensity(1000)
      if (!this.options.selfCollide) {
        colDesc.setCollisionGroups(RAGDOLL_GROUP_NO_SELF)
      }
      world.createCollider(colDesc, body)

      // For sync: cache the constant offset/rotation that takes the body's
      // frame back to the bone's world frame. Bone-head position in body-local
      // is just (0, -halfLength, 0) (since body sits at midpoint along +Y),
      // but we compute it generally to be robust to refactors.
      const bodyToBoneRot = bodyQuat.clone().invert().multiply(info.worldQuat)
      const bodyOriginToHead = new THREE.Vector3()
        .subVectors(info.worldPos, midpoint)
        .applyQuaternion(bodyQuat.clone().invert())

      bodiesByKName[kName] = body
      this.bodies.push({ kName, body, bone: info.bone, bodyOriginToHead, bodyToBoneRot })

      if (this.debug) {
        const geom = new THREE.CapsuleGeometry(radius, Math.max(halfHeight * 2, 0.001), 4, 8)
        // depthTest off + high renderOrder → visible through the skinned mesh,
        // so we can see capsules even when they're inside the body silhouette.
        const mat = new THREE.MeshBasicMaterial({
          color: 0x4a90e2, wireframe: true, depthTest: false, transparent: true, opacity: 0.9,
        })
        const mesh = new THREE.Mesh(geom, mat)
        mesh.renderOrder = 999
        this.scene.add(mesh)
        this.debugMeshes.push({ body, mesh })
      }
      console.log(`[ragdoll] body for ${kName.padEnd(14)} len=${length.toFixed(3)} radius=${radius.toFixed(3)}`)
    }

    // Joint at each bone's head, anchoring it to its nearest mapped ancestor.
    // Pelvis has no parent → free root body. Knees and elbows are 1-axis
    // hinges (revolute) when the toggle is on; everything else is spherical
    // (3-axis ball joint, no limits yet).
    for (const { kName, body } of this.bodies) {
      const parentK = nearestMappedParent(kName)
      if (!parentK) continue
      const parentBody = bodiesByKName[parentK]
      if (!parentBody) continue

      const headWorld = data[kName].worldPos
      const localChild = this._worldToBodyLocal(headWorld, body)
      const localParent = this._worldToBodyLocal(headWorld, parentBody)

      const isRigidExt = this.options.rigidExtremities && RIGID_EXTREMITY_BONES.has(kName)
      const isRigidTorso = !isRigidExt && this.options.rigidTorso && RIGID_TORSO_BONES.has(kName)
      const useFixed = isRigidExt || isRigidTorso
      const useHinge = !useFixed && this.options.hingeKneesElbows && HINGE_BONES.has(kName)
      let joint
      if (useFixed) {
        // Fixed joint: locks all 6 DoF. The two identity frames mean "preserve
        // whatever relative orientation the bodies have at joint creation",
        // which is the rest-pose alignment we just captured.
        const id = { x: 0, y: 0, z: 0, w: 1 }
        const jointDesc = RAPIER.JointData.fixed(localParent, id, localChild, id)
        joint = world.createImpulseJoint(jointDesc, parentBody, body, true)
      } else if (useHinge) {
        // Hinge axis: world-X is "side-to-side" for an upright character. A
        // knee bending around world-X swings the calf in the YZ plane (the
        // foot moves forward/back); same for an elbow. Convert to the parent
        // body's local frame at build time, since Rapier expresses the axis
        // there and the joint rotates with the parent.
        const axisWorld = new THREE.Vector3(1, 0, 0)
        const pr = parentBody.rotation()
        const parentQInv = new THREE.Quaternion(pr.x, pr.y, pr.z, pr.w).invert()
        const axisLocal = axisWorld.clone().applyQuaternion(parentQInv)
        const axis = { x: axisLocal.x, y: axisLocal.y, z: axisLocal.z }
        const jointDesc = RAPIER.JointData.revolute(localParent, localChild, axis)
        joint = world.createImpulseJoint(jointDesc, parentBody, body, true)
        // Symmetric ±π/2 limits keep the joint from hyperextending or twisting
        // freely. Asymmetric anatomical limits (knee 0..150°, elbow 0..150°)
        // need known character facing — punt that until we expose facing.
        if (typeof joint.setLimits === 'function') {
          joint.setLimits(-Math.PI * 0.5, Math.PI * 0.5)
        }
      } else {
        const jointDesc = RAPIER.JointData.spherical(localParent, localChild)
        joint = world.createImpulseJoint(jointDesc, parentBody, body, true)
      }

      if (this.options.jointContactsOff) {
        // Adjacent bodies overlap at the shared anchor by construction — let
        // them pass through each other while still colliding with non-adjacent
        // parts (torso vs hand, knee vs other knee, etc.).
        joint.setContactsEnabled(false)
      }
      this.joints.push(joint)
    }
  }

  _worldToBodyLocal(worldPoint, body) {
    const t = body.translation()
    const r = body.rotation()
    const v = new THREE.Vector3(worldPoint.x - t.x, worldPoint.y - t.y, worldPoint.z - t.z)
    const qInv = new THREE.Quaternion(r.x, r.y, r.z, r.w).invert()
    v.applyQuaternion(qInv)
    return { x: v.x, y: v.y, z: v.z }
  }

  _findSkeletonRoot() {
    const target = this.animator.target
    if (!target.isSkinnedMesh) return target
    const bones = target.skeleton.bones
    const set = new Set(bones)
    for (const b of bones) {
      if (!set.has(b.parent)) return b
    }
    return bones[0]
  }

  _buildDfsOrder() {
    const byBone = new Map(this.bodies.map(b => [b.bone, b]))
    const ordered = []
    const sceneRoot = this._findSkeletonRoot()
    const traverse = (obj) => {
      const rec = byBone.get(obj)
      if (rec) ordered.push(rec)
      for (const c of obj.children) traverse(c)
    }
    traverse(sceneRoot)
    // Fallback: any bones not reached (e.g., not under skeleton root).
    for (const rec of this.bodies) {
      if (!ordered.includes(rec)) ordered.push(rec)
    }
    return ordered
  }

  _sync() {
    if (!this._dfsOrdered) this._dfsOrdered = this._buildDfsOrder()

    const tmpQ = new THREE.Quaternion()
    const tmpV = new THREE.Vector3()
    const tmpM = new THREE.Matrix4()
    const tmpInv = new THREE.Matrix4()
    const tmpScl = new THREE.Vector3(1, 1, 1)
    const localP = new THREE.Vector3()
    const localQ = new THREE.Quaternion()
    const localScl = new THREE.Vector3()

    for (const { body, bone, bodyOriginToHead, bodyToBoneRot } of this._dfsOrdered) {
      const bt = body.translation()
      const br = body.rotation()
      const bodyQ = tmpQ.set(br.x, br.y, br.z, br.w)

      // Bone world transform = body transform composed with the constant
      // body→bone offset/rotation captured at build time.
      const boneWorldQ = new THREE.Quaternion().copy(bodyQ).multiply(bodyToBoneRot)
      const offset = bodyOriginToHead.clone().applyQuaternion(bodyQ)
      const boneWorldP = tmpV.set(bt.x + offset.x, bt.y + offset.y, bt.z + offset.z)

      // Convert world → local relative to the bone's parent in three.js graph.
      tmpM.compose(boneWorldP, boneWorldQ, tmpScl)
      const parent = bone.parent
      if (parent) {
        tmpInv.copy(parent.matrixWorld).invert()
        tmpM.premultiply(tmpInv)
      }
      tmpM.decompose(localP, localQ, localScl)
      bone.position.copy(localP)
      bone.quaternion.copy(localQ)
      // Preserve existing bone.scale.
      bone.updateMatrix()
      bone.updateMatrixWorld(true)
    }

    if (this.debug) {
      for (const { body, mesh } of this.debugMeshes) {
        const t = body.translation()
        const r = body.rotation()
        mesh.position.set(t.x, t.y, t.z)
        mesh.quaternion.set(r.x, r.y, r.z, r.w)
      }
    }
  }

  dispose() {
    // Restore bone local pose so the character snaps back to its pre-ragdoll
    // configuration. Animator/mixer can resume from there.
    for (const snap of this.boneSnapshots) {
      snap.bone.position.copy(snap.position)
      snap.bone.quaternion.copy(snap.quaternion)
      snap.bone.updateMatrix()
    }
    this.root.updateMatrixWorld(true)

    for (const j of this.joints) world.removeImpulseJoint(j, true)
    for (const { body } of this.bodies) world.removeRigidBody(body)
    for (const { mesh } of this.debugMeshes) {
      this.scene.remove(mesh)
      mesh.geometry.dispose()
      mesh.material.dispose()
    }

    const idx = syncFns.indexOf(this._syncFn)
    if (idx >= 0) syncFns.splice(idx, 1)

    this.bodies.length = 0
    this.joints.length = 0
    this.debugMeshes.length = 0
    this.boneSnapshots.length = 0
    this._dfsOrdered = null
  }
}

// --- Block ragdoll (alternate impl, modeled after mattvb91/rapierjs-ragdoll) -

// 10-body / 9-spherical-joint ragdoll. Bodies are oriented cuboids sized from
// the actual rig pose; the torso is one big block from hip-mid to shoulder-mid.
// Bones that don't have a corresponding body (spine1/2/3, neck, collars,
// wrists, ankles, feet) stay frozen at their build-time local pose — their
// world transforms still update because their parent chain is driven.

export const BLOCK_DEFAULTS = {
  selfCollide: true,
  jointContactsOff: true,
  damping: true,
  fixedHead: true,           // head rigidly attached to torso (vs free spherical neck)
  fixedHandsFeet: true,      // hands + feet rigidly attached to forearm + shin
  hingeKneesElbows: true,    // elbows + knees as 1-axis revolute joints with ±π/2 limits
  lockLimbTwist: true,       // shoulders + hips block rotation along the limb's long axis
  debug: false,
}

const BLOCK_HINGE_JOINTS = new Set(['armLowerL', 'armLowerR', 'shinL', 'shinR'])
const BLOCK_NOTWIST_JOINTS = new Set(['armUpperL', 'armUpperR', 'thighL', 'thighR'])
const BLOCK_FIXED_HANDS_FEET = new Set(['handL', 'handR', 'footL', 'footR'])

// Maps each block body name to the kimodo bone whose world transform it drives.
const BLOCK_BODY_TO_KBONE = {
  torso:     'pelvis',
  neck:      'neck',
  head:      'head',
  armUpperL: 'left_shoulder',
  armUpperR: 'right_shoulder',
  armLowerL: 'left_elbow',
  armLowerR: 'right_elbow',
  handL:     'left_wrist',
  handR:     'right_wrist',
  thighL:    'left_hip',
  thighR:    'right_hip',
  shinL:     'left_knee',
  shinR:     'right_knee',
  footL:     'left_ankle',
  footR:     'right_ankle',
}

function worldToBodyLocal(worldPoint, body) {
  const t = body.translation()
  const r = body.rotation()
  const v = new THREE.Vector3(worldPoint.x - t.x, worldPoint.y - t.y, worldPoint.z - t.z)
  const qInv = new THREE.Quaternion(r.x, r.y, r.z, r.w).invert()
  v.applyQuaternion(qInv)
  return { x: v.x, y: v.y, z: v.z }
}

export class BlockRagdoll {
  constructor({ animator, root, scene, options = {} }) {
    if (!ready) throw new Error('initPhysics() must complete first')
    this.animator = animator
    this.root = root
    this.scene = scene
    this.options = { ...BLOCK_DEFAULTS, ...options }
    this.debug = this.options.debug

    this.bodyMap = {}            // bodyName → { body, bone, bodyOriginToHead, bodyToBoneRot }
    this.joints = []
    this.debugMeshes = []
    this.boneSnapshots = []      // populated on activate(), used to restore on deactivate()
    this._dfsOrdered = null
    // Kinematic-to-dynamic switch: bodies start as KinematicPositionBased and
    // track the bones each frame via pushKinematic(). On activate() they flip
    // to Dynamic; Rapier preserves the velocity it had been measuring from
    // consecutive setNextKinematic* calls, so the ragdoll inherits the
    // animation's momentum at the moment of switch.
    this.activated = false

    this._build()
    this._syncFn = () => { if (this.activated) this._sync() }
    syncFns.push(this._syncFn)
  }

  _build() {
    const { mapping, bonesByName, _normName } = this.animator
    this.root.updateMatrixWorld(true)

    // Resolve every kimodo bone we'll need to read positions from. We need a
    // few extras (left_wrist, right_wrist, left_ankle, right_ankle, neck) for
    // sizing limb segments, even though we don't drive them.
    const required = [
      'pelvis', 'head', 'neck',
      'left_hip', 'right_hip', 'left_knee', 'right_knee', 'left_ankle', 'right_ankle',
      'left_foot', 'right_foot',
      'left_shoulder', 'right_shoulder', 'left_elbow', 'right_elbow', 'left_wrist', 'right_wrist',
    ]
    const data = {}
    const tmpScl = new THREE.Vector3()
    for (const kName of required) {
      const tName = mapping[kName]
      if (!tName) continue
      const bone = bonesByName[_normName(tName)]
      if (!bone) continue
      const p = new THREE.Vector3()
      const q = new THREE.Quaternion()
      bone.matrixWorld.decompose(p, q, tmpScl)
      data[kName] = { bone, worldPos: p, worldQuat: q }
    }

    // Snapshots are taken at activate() time (not build time) so that
    // deactivate() restores to the pose the user clicked Ragdoll at, not the
    // pose the character was in when first loaded.

    const need = ['pelvis', 'head', 'left_hip', 'right_hip', 'left_shoulder', 'right_shoulder']
    const missing = need.filter(k => !data[k])
    if (missing.length) {
      console.error('[blockRagdoll] missing required bones:', missing)
      return
    }

    // --- Torso: cuboid spanning hip-mid → neck-base (almost reaches the head) ---
    const hipMid = new THREE.Vector3().addVectors(data.left_hip.worldPos, data.right_hip.worldPos).multiplyScalar(0.5)
    const shoulderMid = new THREE.Vector3().addVectors(data.left_shoulder.worldPos, data.right_shoulder.worldPos).multiplyScalar(0.5)
    // Top of torso is the neck base if mapped, else fall back to shoulder-mid.
    const torsoTop = data.neck ? data.neck.worldPos : shoulderMid
    const torsoCenter = new THREE.Vector3().addVectors(hipMid, torsoTop).multiplyScalar(0.5)
    const torsoUpRaw = new THREE.Vector3().subVectors(torsoTop, hipMid)
    const torsoHeight = Math.max(torsoUpRaw.length(), 0.2)
    const torsoUp = torsoUpRaw.normalize()
    const torsoWidth = Math.max(data.left_shoulder.worldPos.distanceTo(data.right_shoulder.worldPos) * 0.9, 0.25)
    const torsoDepth = 0.18
    const torsoQuat = new THREE.Quaternion().setFromUnitVectors(UP, torsoUp)
    this._addBody('torso', data.pelvis, torsoCenter, torsoQuat,
      [torsoWidth / 2, torsoHeight / 2, torsoDepth / 2])

    // --- Neck: short body bridging torso top → head ---
    if (data.neck && data.head) {
      this._addLimb('neck', data.neck, data.head, 0.07)
    }

    // --- Head: cube centered above the head bone position ---
    const headSize = 0.18
    // Head bone "up" in world: its local +Y rotated by its world quat.
    const headUp = new THREE.Vector3(0, 1, 0).applyQuaternion(data.head.worldQuat)
    const headCenter = data.head.worldPos.clone().add(headUp.multiplyScalar(headSize / 2))
    this._addBody('head', data.head, headCenter, data.head.worldQuat,
      [headSize / 2, headSize / 2, headSize / 2])

    // --- Limbs ---
    const armThick = 0.07
    const legThick = 0.09
    this._addLimb('armUpperL', data.left_shoulder,  data.left_elbow,  armThick)
    this._addLimb('armLowerL', data.left_elbow,     data.left_wrist,  armThick)
    this._addLimb('armUpperR', data.right_shoulder, data.right_elbow, armThick)
    this._addLimb('armLowerR', data.right_elbow,    data.right_wrist, armThick)
    this._addLimb('thighL',    data.left_hip,       data.left_knee,   legThick)
    this._addLimb('shinL',     data.left_knee,      data.left_ankle,  legThick)
    this._addLimb('thighR',    data.right_hip,      data.right_knee,  legThick)
    this._addLimb('shinR',     data.right_knee,     data.right_ankle, legThick)

    // --- Hands & feet ---
    // Adds inertial mass at the extremities so forearms and shins can't whip
    // around freely. Hands extend from the wrist forward along the forearm
    // direction (no fingertip bone in the rig); feet extend from ankle to toe.
    const handLen = 0.10
    const handThick = 0.06
    if (data.left_elbow && data.left_wrist) {
      const dir = new THREE.Vector3().subVectors(data.left_wrist.worldPos, data.left_elbow.worldPos)
      this._addExtension('handL', data.left_wrist, dir, handLen, handThick)
    }
    if (data.right_elbow && data.right_wrist) {
      const dir = new THREE.Vector3().subVectors(data.right_wrist.worldPos, data.right_elbow.worldPos)
      this._addExtension('handR', data.right_wrist, dir, handLen, handThick)
    }
    this._addFoot('footL', data.left_ankle,  data.left_foot)
    this._addFoot('footR', data.right_ankle, data.right_foot)

    // --- Joints (all spherical, anchored at the bone world position) ---
    // Head sits on the neck (when present), and the neck attaches to the
    // torso with a free spherical joint so the head+neck assembly can flex
    // a little under gravity instead of looking welded to the chest. If the
    // rig has no neck bone, head connects directly to torso.
    if (this.bodyMap.neck) {
      this._addJoint('neck', 'torso', data.neck.worldPos)
      this._addJoint('head', 'neck',  data.head.worldPos)
    } else {
      this._addJoint('head', 'torso', data.head.worldPos)
    }
    this._addJoint('armUpperL', 'torso',     data.left_shoulder.worldPos)
    this._addJoint('armUpperR', 'torso',     data.right_shoulder.worldPos)
    this._addJoint('armLowerL', 'armUpperL', data.left_elbow.worldPos)
    this._addJoint('armLowerR', 'armUpperR', data.right_elbow.worldPos)
    this._addJoint('thighL',    'torso',     data.left_hip.worldPos)
    this._addJoint('thighR',    'torso',     data.right_hip.worldPos)
    this._addJoint('shinL',     'thighL',    data.left_knee.worldPos)
    this._addJoint('shinR',     'thighR',    data.right_knee.worldPos)
    this._addJoint('handL',     'armLowerL', data.left_wrist.worldPos)
    this._addJoint('handR',     'armLowerR', data.right_wrist.worldPos)
    this._addJoint('footL',     'shinL',     data.left_ankle.worldPos)
    this._addJoint('footR',     'shinR',     data.right_ankle.worldPos)
  }

  _addFoot(bodyName, ankleInfo, toeInfo) {
    if (!ankleInfo || !toeInfo) {
      console.warn(`[blockRagdoll] skip ${bodyName} — missing ankle or toe`)
      return
    }
    // Project ankle→toe onto the ground plane so the foot sits horizontally,
    // not vertically along the bone segment (which dives toward the floor).
    const ankleToToe = new THREE.Vector3().subVectors(toeInfo.worldPos, ankleInfo.worldPos)
    const horiz = new THREE.Vector3(ankleToToe.x, 0, ankleToToe.z)
    let length = horiz.length()
    if (length < 0.05) {
      // Fallback: rig has toe directly under ankle. Pick a default forward.
      length = 0.18
      horiz.set(0, 0, 1)
    }
    horiz.normalize()
    const center = new THREE.Vector3().copy(ankleInfo.worldPos)
      .add(horiz.clone().multiplyScalar(length / 2))
    // Body local +Y aligned along the foot's forward direction.
    const quat = new THREE.Quaternion().setFromUnitVectors(UP, horiz)
    // Flat shape: longer than wide, much shorter vertically.
    const halfWidth = 0.035
    const halfHeight = 0.020
    this._addBody(bodyName, ankleInfo, center, quat,
      [halfWidth, length / 2, halfHeight])
  }

  _addExtension(bodyName, originInfo, directionWorld, length, thickness) {
    const lenRaw = directionWorld.length()
    if (lenRaw < 1e-4) {
      console.warn(`[blockRagdoll] skip ${bodyName} — zero direction`)
      return
    }
    const direction = directionWorld.clone().normalize()
    const center = new THREE.Vector3().copy(originInfo.worldPos)
      .add(direction.clone().multiplyScalar(length / 2))
    const quat = new THREE.Quaternion().setFromUnitVectors(UP, direction)
    this._addBody(bodyName, originInfo, center, quat,
      [thickness / 2, length / 2, thickness / 2])
  }

  _addLimb(bodyName, headInfo, tailInfo, thickness) {
    if (!headInfo || !tailInfo) {
      console.warn(`[blockRagdoll] skip ${bodyName} — missing endpoint bone`)
      return
    }
    const dirRaw = new THREE.Vector3().subVectors(tailInfo.worldPos, headInfo.worldPos)
    const length = dirRaw.length()
    if (length < 0.05) {
      console.warn(`[blockRagdoll] skip ${bodyName} — segment too short (${length.toFixed(3)})`)
      return
    }
    const direction = dirRaw.normalize()
    const center = new THREE.Vector3().copy(headInfo.worldPos)
      .add(direction.clone().multiplyScalar(length / 2))
    const quat = new THREE.Quaternion().setFromUnitVectors(UP, direction)
    this._addBody(bodyName, headInfo, center, quat,
      [thickness / 2, length / 2, thickness / 2])
  }

  _addBody(bodyName, boneInfo, center, quat, halfExtents) {
    // Kinematic on construction; flipped to Dynamic by activate(). Damping is
    // *not* applied on construction — it would reduce the velocity Rapier
    // tracks during kinematic motion, hurting momentum transfer at activation.
    // Damping is applied at activate() time instead.
    const bodyDesc = RAPIER.RigidBodyDesc.kinematicPositionBased()
      .setTranslation(center.x, center.y, center.z)
      .setRotation({ x: quat.x, y: quat.y, z: quat.z, w: quat.w })
    const body = world.createRigidBody(bodyDesc)

    const colDesc = RAPIER.ColliderDesc.cuboid(halfExtents[0], halfExtents[1], halfExtents[2])
      .setFriction(0.8)
      .setRestitution(0.05)
      .setDensity(1000)
    if (!this.options.selfCollide) colDesc.setCollisionGroups(RAGDOLL_GROUP_NO_SELF)
    world.createCollider(colDesc, body)

    // Sync data: const offsets that take the body's frame back to the bone's
    // world frame at build time. Same approach as the capsule ragdoll.
    const bodyOriginToHead = new THREE.Vector3()
      .subVectors(boneInfo.worldPos, center)
      .applyQuaternion(quat.clone().invert())
    const bodyToBoneRot = quat.clone().invert().multiply(boneInfo.worldQuat)

    // halfExtents stashed so setDebug() can rebuild the wireframe overlay
    // after construction.
    this.bodyMap[bodyName] = {
      body, bone: boneInfo.bone, bodyOriginToHead, bodyToBoneRot, halfExtents: halfExtents.slice(),
    }

    if (this.debug) this._addDebugMesh(body, halfExtents)

    console.log(`[blockRagdoll] ${bodyName.padEnd(11)} drives ${BLOCK_BODY_TO_KBONE[bodyName].padEnd(15)} half=[${halfExtents.map(v => v.toFixed(3)).join(', ')}]`)
  }

  _addDebugMesh(body, halfExtents) {
    const geom = new THREE.BoxGeometry(halfExtents[0] * 2, halfExtents[1] * 2, halfExtents[2] * 2)
    const mat = new THREE.MeshBasicMaterial({
      color: 0x4ae28a, wireframe: true, depthTest: false, transparent: true, opacity: 0.9,
    })
    const mesh = new THREE.Mesh(geom, mat)
    mesh.renderOrder = 999
    // Initialize at the body's current transform so the mesh appears in the
    // right place immediately, before the next physics step / pushKinematic.
    const t = body.translation()
    const r = body.rotation()
    mesh.position.set(t.x, t.y, t.z)
    mesh.quaternion.set(r.x, r.y, r.z, r.w)
    this.scene.add(mesh)
    this.debugMeshes.push({ body, mesh })
  }

  setDebug(on) {
    on = !!on
    if (on === this.debug) return
    this.debug = on
    // Tear down existing meshes (whether on or off, we rebuild fresh).
    for (const { mesh } of this.debugMeshes) {
      this.scene.remove(mesh)
      mesh.geometry.dispose()
      mesh.material.dispose()
    }
    this.debugMeshes.length = 0
    if (!on) return
    for (const [, rec] of Object.entries(this.bodyMap)) {
      this._addDebugMesh(rec.body, rec.halfExtents)
    }
  }

  _addJoint(childName, parentName, worldAnchor) {
    const child = this.bodyMap[childName]
    const parent = this.bodyMap[parentName]
    if (!child || !parent) return
    const localChild = worldToBodyLocal(worldAnchor, child.body)
    const localParent = worldToBodyLocal(worldAnchor, parent.body)
    const useFixed =
      (this.options.fixedHead && childName === 'head') ||
      (this.options.fixedHandsFeet && BLOCK_FIXED_HANDS_FEET.has(childName))
    const useHinge = !useFixed && this.options.hingeKneesElbows && BLOCK_HINGE_JOINTS.has(childName)
    const useNoTwist = !useFixed && !useHinge && this.options.lockLimbTwist && BLOCK_NOTWIST_JOINTS.has(childName)
    let joint
    if (useFixed) {
      // Lock all 6 DoF — body moves rigidly with its parent (head/torso,
      // hand/forearm, foot/shin). Stops these segments rotating independently.
      const id = { x: 0, y: 0, z: 0, w: 1 }
      const desc = RAPIER.JointData.fixed(localParent, id, localChild, id)
      joint = world.createImpulseJoint(desc, parent.body, child.body, true)
    } else if (useHinge) {
      // Hinge axis: world-X transformed into the parent body's local frame at
      // build time. For an upright character this is "side-to-side", so knees
      // bend the calf forward/back and elbows bend the forearm forward/back —
      // not anatomically perfect, but stops 360° spin and hyperextension.
      const axisWorld = new THREE.Vector3(1, 0, 0)
      const pr = parent.body.rotation()
      const parentQInv = new THREE.Quaternion(pr.x, pr.y, pr.z, pr.w).invert()
      const axisLocal = axisWorld.clone().applyQuaternion(parentQInv)
      const axis = { x: axisLocal.x, y: axisLocal.y, z: axisLocal.z }
      const desc = RAPIER.JointData.revolute(localParent, localChild, axis)
      joint = world.createImpulseJoint(desc, parent.body, child.body, true)
      if (typeof joint.setLimits === 'function') {
        joint.setLimits(-Math.PI * 0.5, Math.PI * 0.5)
      }
    } else {
      const desc = RAPIER.JointData.spherical(localParent, localChild)
      joint = world.createImpulseJoint(desc, parent.body, child.body, true)
    }
    if (this.options.jointContactsOff) joint.setContactsEnabled(false)
    this.joints.push(joint)
  }

  _findSkeletonRoot() {
    const target = this.animator.target
    if (!target.isSkinnedMesh) return target
    const bones = target.skeleton.bones
    const set = new Set(bones)
    for (const b of bones) {
      if (!set.has(b.parent)) return b
    }
    return bones[0]
  }

  _buildDfsOrder() {
    const byBone = new Map()
    for (const [, rec] of Object.entries(this.bodyMap)) byBone.set(rec.bone, rec)
    const ordered = []
    const sceneRoot = this._findSkeletonRoot()
    const traverse = (obj) => {
      const rec = byBone.get(obj)
      if (rec) ordered.push(rec)
      for (const c of obj.children) traverse(c)
    }
    traverse(sceneRoot)
    for (const [, rec] of Object.entries(this.bodyMap)) {
      if (!ordered.includes(rec)) ordered.push(rec)
    }
    return ordered
  }

  _sync() {
    if (!this._dfsOrdered) this._dfsOrdered = this._buildDfsOrder()

    const tmpQ = new THREE.Quaternion()
    const tmpV = new THREE.Vector3()
    const tmpM = new THREE.Matrix4()
    const tmpInv = new THREE.Matrix4()
    const tmpScl = new THREE.Vector3(1, 1, 1)
    const localP = new THREE.Vector3()
    const localQ = new THREE.Quaternion()
    const localScl = new THREE.Vector3()

    for (const { body, bone, bodyOriginToHead, bodyToBoneRot } of this._dfsOrdered) {
      const bt = body.translation()
      const br = body.rotation()
      const bodyQ = tmpQ.set(br.x, br.y, br.z, br.w)

      const boneWorldQ = new THREE.Quaternion().copy(bodyQ).multiply(bodyToBoneRot)
      const offset = bodyOriginToHead.clone().applyQuaternion(bodyQ)
      const boneWorldP = tmpV.set(bt.x + offset.x, bt.y + offset.y, bt.z + offset.z)

      tmpM.compose(boneWorldP, boneWorldQ, tmpScl)
      const parent = bone.parent
      if (parent) {
        tmpInv.copy(parent.matrixWorld).invert()
        tmpM.premultiply(tmpInv)
      }
      tmpM.decompose(localP, localQ, localScl)
      bone.position.copy(localP)
      bone.quaternion.copy(localQ)
      bone.updateMatrix()
      bone.updateMatrixWorld(true)
    }

    if (this.debug) {
      for (const { body, mesh } of this.debugMeshes) {
        const t = body.translation()
        const r = body.rotation()
        mesh.position.set(t.x, t.y, t.z)
        mesh.quaternion.set(r.x, r.y, r.z, r.w)
      }
    }
  }

  // --- Kinematic tracking (called every frame while not activated) ---------

  pushKinematic() {
    if (this.activated) return
    this.root.updateMatrixWorld(true)

    const boneP = new THREE.Vector3()
    const boneQ = new THREE.Quaternion()
    const tmpScl = new THREE.Vector3()
    const bodyQ = new THREE.Quaternion()
    const offsetWorld = new THREE.Vector3()
    const bodyP = new THREE.Vector3()
    const inv = new THREE.Quaternion()

    for (const [, rec] of Object.entries(this.bodyMap)) {
      rec.bone.matrixWorld.decompose(boneP, boneQ, tmpScl)
      // Solve for body transform given bone transform, inverting the build-time
      // relationship: boneWorldQ = bodyQ * bodyToBoneRot,
      //               boneWorldP = bodyP + (bodyOriginToHead applied with bodyQ).
      inv.copy(rec.bodyToBoneRot).invert()
      bodyQ.copy(boneQ).multiply(inv)
      offsetWorld.copy(rec.bodyOriginToHead).applyQuaternion(bodyQ)
      bodyP.copy(boneP).sub(offsetWorld)
      rec.body.setNextKinematicTranslation({ x: bodyP.x, y: bodyP.y, z: bodyP.z })
      rec.body.setNextKinematicRotation({ x: bodyQ.x, y: bodyQ.y, z: bodyQ.z, w: bodyQ.w })
    }

    if (this.debug) {
      for (const { body, mesh } of this.debugMeshes) {
        const t = body.translation()
        const r = body.rotation()
        mesh.position.set(t.x, t.y, t.z)
        mesh.quaternion.set(r.x, r.y, r.z, r.w)
      }
    }
  }

  // --- Activate / deactivate (kinematic ↔ dynamic) -------------------------

  activate() {
    if (this.activated) return
    // Snapshot bone pose so deactivate() can restore the ragdoll start state.
    this._refreshSnapshots()
    for (const [, rec] of Object.entries(this.bodyMap)) {
      // Apply damping at activation so it doesn't bleed energy during
      // kinematic tracking (which would distort the velocity Rapier preserves).
      if (this.options.damping) {
        rec.body.setAngularDamping(ANGULAR_DAMPING)
        rec.body.setLinearDamping(LINEAR_DAMPING)
      }
      rec.body.setBodyType(RAPIER.RigidBodyType.Dynamic, true)
    }
    this.activated = true
  }

  deactivate() {
    if (!this.activated) return
    // Restore bone pose to the snapshot taken at activate(). The animation
    // (if any) will overwrite from there next frame.
    for (const snap of this.boneSnapshots) {
      snap.bone.position.copy(snap.position)
      snap.bone.quaternion.copy(snap.quaternion)
      snap.bone.updateMatrix()
    }
    this.root.updateMatrixWorld(true)
    for (const [, rec] of Object.entries(this.bodyMap)) {
      // Zero damping for clean kinematic tracking velocity, then back to
      // kinematic so pushKinematic() drives them on the next frame.
      rec.body.setAngularDamping(0)
      rec.body.setLinearDamping(0)
      rec.body.setBodyType(RAPIER.RigidBodyType.KinematicPositionBased, true)
    }
    this.activated = false
  }

  _refreshSnapshots() {
    this.boneSnapshots.length = 0
    const { mapping, bonesByName, _normName } = this.animator
    for (const [, tName] of Object.entries(mapping)) {
      if (!tName) continue
      const bone = bonesByName[_normName(tName)]
      if (!bone) continue
      this.boneSnapshots.push({
        bone,
        position: bone.position.clone(),
        quaternion: bone.quaternion.clone(),
      })
    }
  }

  dispose() {
    if (this.activated) {
      // Restore bones if we were ragdolling, so the animator picks up cleanly.
      for (const snap of this.boneSnapshots) {
        snap.bone.position.copy(snap.position)
        snap.bone.quaternion.copy(snap.quaternion)
        snap.bone.updateMatrix()
      }
      this.root.updateMatrixWorld(true)
    }

    for (const j of this.joints) world.removeImpulseJoint(j, true)
    for (const [, rec] of Object.entries(this.bodyMap)) world.removeRigidBody(rec.body)
    for (const { mesh } of this.debugMeshes) {
      this.scene.remove(mesh)
      mesh.geometry.dispose()
      mesh.material.dispose()
    }

    const idx = syncFns.indexOf(this._syncFn)
    if (idx >= 0) syncFns.splice(idx, 1)

    this.bodyMap = {}
    this.joints.length = 0
    this.debugMeshes.length = 0
    this.boneSnapshots.length = 0
    this._dfsOrdered = null
    this.activated = false
  }
}
