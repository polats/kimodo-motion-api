// Kata move-tree viewer (/kata). Shows the kata moves (clips linked by
// `continues_from`) as a zoomable, collapsible d3 graph. Two modes:
//   • View move: click a node → play just that move.
//   • Play path: click a node → play the whole root→node path as one continuous
//     motion (server stitches the clips, carrying position+heading), so the
//     character flows through the kata without resetting between moves.
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { FBXLoader } from 'three/addons/loaders/FBXLoader.js'
import * as d3 from 'd3'
import { Animator } from './animator.js'
import { CHARACTERS, getCharacter } from './rigs.js'

const KIMODO_URL = import.meta.env.VITE_KIMODO_URL || 'http://localhost:7862'
const SMPLX_HEIGHT = 1.7
const statusEl = document.getElementById('status')
const setStatus = (t) => { statusEl.textContent = t }
let mode = 'path' // 'path' (play whole path to node) | 'move' (just that move); toggled by re-clicking a node
let scrubbing = false, curFps = 30, curFrames = 0

// --- Scene ----------------------------------------------------------------
const app = document.getElementById('app')   // viewer area (right of the panel); the character centers here
const renderer = new THREE.WebGLRenderer({ antialias: true })
renderer.setPixelRatio(window.devicePixelRatio)
renderer.outputColorSpace = THREE.SRGBColorSpace
app.appendChild(renderer.domElement)
const scene = new THREE.Scene()
scene.background = new THREE.Color(0x1a1a1a)
const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100)
camera.position.set(0, 1.5, 3.4)
const controls = new OrbitControls(camera, renderer.domElement)
controls.target.set(0, 1.0, 0); controls.update()

// Size the canvas to the VIEWER element (not the window), so the character sits
// centred in the visible area instead of behind the left panel.
function resizeViewer() {
  const w = app.clientWidth || 1, h = app.clientHeight || 1
  renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix()
}
window.addEventListener('resize', resizeViewer)
new ResizeObserver(resizeViewer).observe(app)
resizeViewer()

// Camera controls: follow-the-character lock + reset to the default view.
const HOME_POS = new THREE.Vector3(0, 1.5, 3.4)
const HOME_TGT = new THREE.Vector3(0, 1.0, 0)
let camFollow = false, camPrev = null
const _camTmp = new THREE.Vector3()
const followBtn = document.getElementById('cam-follow')
const resetBtn = document.getElementById('cam-reset')
followBtn.onclick = () => { camFollow = !camFollow; followBtn.classList.toggle('on', camFollow); camPrev = null }
resetBtn.onclick = () => { camera.position.copy(HOME_POS); controls.target.copy(HOME_TGT); camera.up.set(0, 1, 0); controls.update(); camPrev = null }

// Axis gizmo: snap the camera to look down X / Y / Z (toggles +/- on repeat press).
const axisSign = { x: -1, z: -1 }
function alignAxis(a) {
  const dist = camera.position.distanceTo(controls.target) || 3.4
  const s = (axisSign[a] *= -1)
  camera.position.copy(controls.target).add(new THREE.Vector3(a === 'x' ? s : 0, 0, a === 'z' ? s : 0).multiplyScalar(dist))
  camera.up.set(0, 1, 0)
  controls.update()
}
for (const a of ['x', 'z']) document.getElementById('axis-' + a).onclick = () => alignAxis(a)

scene.add(new THREE.HemisphereLight(0xffffff, 0x222233, 1.2))
const dir = new THREE.DirectionalLight(0xffffff, 1.5); dir.position.set(3, 5, 2); scene.add(dir)
const floor = new THREE.Mesh(new THREE.PlaneGeometry(40, 40), new THREE.MeshStandardMaterial({ color: 0x2a2a2a, roughness: 1 }))
floor.rotation.x = -Math.PI / 2; scene.add(floor)
scene.add(new THREE.GridHelper(40, 80, 0x333333, 0x303030))
// Floor direction arrows at the origin so facing is readable. The character is
// re-rooted facing +Z, so the blue +Z arrow points "forward".
for (const [dirv, color] of [[[1, 0, 0], 0xe05555], [[0, 0, 1], 0x5a9bff]]) {
  scene.add(new THREE.ArrowHelper(new THREE.Vector3(...dirv), new THREE.Vector3(0, 0.02, 0), 1.4, color, 0.28, 0.16))
}

// --- Character + animator (smplx neutral) ---------------------------------
let animator = null, charRoot = null   // charRoot is yawed live for the rotation control
let charSkinned = null                 // the skinned mesh whose skeleton the animator drives (clothing binds to it)
let currentMotion = null               // last clip set on the animator (re-applied after a model swap)
let currentCharId = 'unirig_citizen'   // default model (the UniRig-rigged sbox citizen)
const gltfLoader = new GLTFLoader()
const fbxLoader = new FBXLoader()
// FBX returns the Object3D root directly; GLB wraps it in gltf.scene.
async function loadCharacterRoot(url) {
  if (url.toLowerCase().endsWith('.fbx')) return await fbxLoader.loadAsync(url)
  return (await gltfLoader.loadAsync(url)).scene
}
// Multi-mesh FBX (sbox citizen = 8 SkinnedMeshes, each its own copy of the same
// skeleton) → point every mesh at the first mesh's skeleton so one rig drives all.
// Returns the mesh whose (richest) skeleton should drive the rig, or null.
function unifySkeletons(root) {
  const meshes = []; root.traverse(o => { if (o.isSkinnedMesh) meshes.push(o) })
  if (meshes.length < 2) return null
  // Pick the richest skeleton as master so every mesh's skinIndex stays in range.
  const masterMesh = meshes.reduce((a, b) => b.skeleton.bones.length > a.skeleton.bones.length ? b : a, meshes[0])
  const master = masterMesh.skeleton
  for (const mesh of meshes) {
    // Only rebind a mesh whose skeleton matches master's bone count — rebinding
    // onto a smaller skeleton leaves skinIndex references dangling (→ crash).
    if (mesh.skeleton !== master && mesh.skeleton.bones.length === master.bones.length) {
      mesh.bind(master, mesh.bindMatrix)
    }
  }
  return masterMesh
}
async function loadCharacter(cfg) {
  if (charRoot) {   // swap: drop the previous mesh first
    detachClothing()   // its bones are about to be disposed
    scene.remove(charRoot)
    charRoot.traverse(o => { o.geometry?.dispose?.(); if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => m.dispose?.()) })
    charRoot = null; animator = null; charSkinned = null
  }
  const root = await loadCharacterRoot(cfg.url)
  root.scale.setScalar(cfg.scale ?? 1.0)
  scene.add(root); charRoot = root
  let skinned = unifySkeletons(root)   // mesh whose (richest) skeleton drives the rig
  root.traverse(o => { if (o.isSkinnedMesh) { if (!skinned) skinned = o; o.frustumCulled = false } })
  root.updateMatrixWorld(true)
  const box = new THREE.Box3().setFromObject(root)
  const groundOffsetY = -box.min.y
  root.position.y += groundOffsetY
  if (cfg.mapping?.pelvis) {
    const norm = (n) => n.replace(/[.:]/g, '')
    const pn = norm(cfg.mapping.pelvis)
    let pelvis = null
    root.traverse(o => { if (!pelvis && o.name && norm(o.name) === pn) pelvis = o })
    if (pelvis) { pelvis.position.x = 0; pelvis.position.z = 0; root.updateMatrixWorld(true) }
  }
  const strideScale = (box.max.y - box.min.y) / SMPLX_HEIGHT
  const target = (cfg.skinned && skinned) ? skinned : root
  charSkinned = target.isSkinnedMesh ? target : null   // clothing binds its bones to this skeleton
  animator = new Animator(target, {
    mapping: cfg.mapping, blends: cfg.blends || {}, scale: strideScale, groundOffsetY,
    alignMode: target.isSkinnedMesh ? 'rest' : 'none',
  })
  // Citizen-style rigs split skinning across unparented "*_twist#" bones; pair
  // each with its main bone so syncTwistBones() can drive the deformation.
  animator.twistPairs = []
  const twistRe = /^(.+?)_twist\d+$/
  const drivable = target.isSkinnedMesh ? target.skeleton.bones : []
  for (const tb of drivable) {
    const mm = twistRe.exec(tb.name); if (!mm) continue
    const mainBone = animator.bonesByName[animator._normName(mm[1])]
    if (mainBone && mainBone !== tb) animator.twistPairs.push({ twist: tb, main: mainBone })
  }
  if (currentMotion) { animator.setMotion(currentMotion, { loop: true }); setScrubRange(currentMotion) }   // keep playing after a model swap
  await applyClothingForCurrentBody()   // re-dress with the new body's variant (if anything is worn)
  renderClothingDrawer()                // refresh availability for the new model
}
// Copy each main bone's world rotation onto its twist bone (in the twist's parent
// space) — needed for citizen rigs whose twist bones aren't under the main bone.
const _twP = new THREE.Vector3(), _twS = new THREE.Vector3(), _twQ = new THREE.Quaternion(), _twPQ = new THREE.Quaternion()
function syncTwistBones() {
  const a = animator; if (!a?.twistPairs?.length) return
  for (const { twist, main } of a.twistPairs) {
    main.updateMatrixWorld(true)
    main.matrixWorld.decompose(_twP, _twQ, _twS)
    if (twist.parent) {
      twist.parent.updateMatrixWorld(true)
      twist.parent.matrixWorld.decompose(_twP, _twPQ, _twS)
      twist.quaternion.copy(_twPQ.invert().multiply(_twQ))
    } else twist.quaternion.copy(_twQ)
  }
}

// --- Clothing -------------------------------------------------------------
// A garment GLB is skinned to the citizen's bone_N skeleton (decompiled from
// s&box .vmdl_c, re-rigged via the REF rig — see web/scripts/clothing_rig.py).
// It has its own copy of that skeleton; each frame we copy the citizen's driven
// bone transforms onto the garment's matching bones (by name), so it deforms
// with the body. Each garment is rigged to a specific body (matching bone_N).
// Per garment: one rigged GLB per citizen body (each skinned to that body's
// bone_N). The garment is body-agnostic in the UI ("worn"); we resolve the GLB
// for whichever citizen is currently loaded — so it applies to all three, and
// follows the body when you swap models.
const CLOTHING = [
  { id: 'jacket', label: 'Windbreaker (jacket)', color: 0x7a8a3a, glb: {
    unirig_citizen: '/clothing/jacket_sausage.glb',
    unirig_citizen_male: '/clothing/jacket_male.glb',
    unirig_citizen_female: '/clothing/jacket_female.glb',
  } },
]
const clothingUrlFor = (item, charId) => item.glb?.[charId] || null
let clothingScene = null, clothingPairs = null, clothingWornId = null
function detachClothing() {
  if (clothingScene && charRoot) charRoot.remove(clothingScene)
  if (clothingScene) clothingScene.traverse(o => { if (o.isMesh) { o.geometry?.dispose?.(); } })
  clothingScene = null; clothingPairs = null   // keep clothingWornId so it re-attaches after a model swap
}
// Attach the worn garment's variant for the CURRENT body (no model swap).
async function applyClothingForCurrentBody() {
  detachClothing()
  if (!clothingWornId || !charSkinned) return
  const item = CLOTHING.find(c => c.id === clothingWornId); if (!item) return
  const url = clothingUrlFor(item, currentCharId)
  if (!url) return   // this garment isn't rigged for the current body (e.g. SMPL-X)
  const sc = (await gltfLoader.loadAsync(url)).scene
  let mesh = null; sc.traverse(o => { if (o.isSkinnedMesh) { mesh = o; o.frustumCulled = false } })
  if (!mesh) { setStatus('clothing has no skinned mesh'); return }
  if (item.color != null) mesh.material = new THREE.MeshStandardMaterial({ color: item.color, roughness: 0.85, metalness: 0.0 })
  const cit = new Map(charSkinned.skeleton.bones.map(b => [b.name, b]))
  clothingPairs = mesh.skeleton.bones.map(jb => ({ j: jb, c: cit.get(jb.name) })).filter(p => p.c)
  charRoot.add(sc); clothingScene = sc
}
const _clP = new THREE.Vector3()
function syncClothing() {   // drive the garment's skeleton from the citizen's (idempotent per frame)
  if (!clothingPairs) return
  for (const { j, c } of clothingPairs) { j.quaternion.copy(c.quaternion); j.position.copy(c.position); j.scale.copy(c.scale) }
}

// --- API ------------------------------------------------------------------
async function listAnimations() {
  const r = await fetch(`${KIMODO_URL}/animations`)
  if (!r.ok) throw new Error(`list ${r.status}`)
  return (await r.json()).animations || []
}
async function fetchAnimation(id) {
  const r = await fetch(`${KIMODO_URL}/animations/${encodeURIComponent(id)}`)
  if (!r.ok) throw new Error(`fetch ${id} ${r.status}`)
  return await r.json()
}
async function stitchPath(ids) {
  const r = await fetch(`${KIMODO_URL}/stitch_path`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids, save: false }),
  })
  if (!r.ok) throw new Error(`stitch_path ${r.status}`)
  return await r.json()
}

// --- Short, readable node labels derived from the (long) generation prompt --
const TECH = [
  ['low sweeping down block', 'down block'], ['low down block', 'down block'], ['down block', 'down block'],
  ['combined high-and-low block', 'combined block'], ['combined block', 'combined block'],
  ['rising block', 'rising block'], ['high block', 'high block'],
  ['double knife-hand block', 'double knife-hand'], ['knife-hand block', 'knife-hand block'],
  ['knife-hand strike', 'knife-hand strike'],
  ['inside forearm block', 'inside block'], ['outside-to-inside forearm block', 'outside block'],
  ['spear-hand', 'spear-hand'],
  ['reverse cross punch', 'reverse punch'], ['reverse punch', 'reverse punch'],
  ['lunge punch', 'lunge punch'], ['straight punch', 'straight punch'], ['double punch', 'double punch'],
  ['uppercut', 'uppercut'], ['spinning back-fist', 'spinning back-fist'], ['back-fist', 'back-fist'],
  ['hammer-fist', 'hammer-fist'], ['upward elbow', 'upward elbow'], ['elbow', 'elbow'],
  ['lead hook', 'lead hook'], ['body hook', 'body hook'], ['hook punch', 'hook'], ['spinning hook kick', 'hook kick'],
  ['jab', 'jab'], ['cross', 'cross'],
  ['flying front kick', 'flying kick'], ['jumping front kick', 'jumping kick'],
  ['low front kick', 'low kick'], ['front snap kick', 'front kick'], ['front kick', 'front kick'],
  ['roundhouse', 'roundhouse'], ['side snap kick', 'side kick'], ['side kick', 'side kick'],
  ['back kick', 'back kick'], ['low kick', 'low kick'], ['sweep', 'leg sweep'], ['knee', 'knee strike'],
]
const STANCES = [
  ['horse-riding stance', 'horse stance'], ['horse stance', 'horse stance'],
  ['fighting stance', 'fighting stance'], ['ready stance', 'ready stance'], ['back stance', 'back stance'],
]
function shortLabel(prompt) {
  const s = (prompt || '').toLowerCase()
  const hits = [], usedKeys = []
  for (const [k, v] of TECH) {
    if (!s.includes(k) || hits.includes(v)) continue
    if (usedKeys.some(uk => uk.includes(k))) continue
    hits.push(v); usedKeys.push(k)
    if (hits.length >= 2) break
  }
  let label = hits.join(' + ')
  if (!label) { for (const [k, v] of STANCES) if (s.includes(k)) { label = v; break } }
  if (!label) {
    if (s.includes('punch')) label = 'punch'
    else if (s.includes('kick')) label = 'kick'
    else if (s.includes('block')) label = 'block'
    else label = s.replace(/^(a martial artist|the practitioner|the martial artist)\s+/, '').slice(0, 22)
  }
  if (/\bturn|turns|spins|pivot/.test(s)) label = '↻ ' + label
  if (s.includes('shout') || s.includes('kiai')) label += ' ⚡'
  return label || 'move'
}

// --- Kata tree (d3 graph, always fully expanded) --------------------------
let CTX = null  // { byId, childrenOf, parentOf, roots }
let currentRoot = null   // only this kata (root + its subtree) is drawn
let selectedId = null, pathSet = new Set()
let activeId = null      // node whose segment is currently playing (path mode)
let playSegs = null      // [{ id, start, end }] frame ranges in the stitched clip
let svg, gAll, gLink, gNode, gBar, gPlay, playLine, playHead, zoomBehavior, POS = null

// Timeline-tree layout: each move's outgoing edge is a vertical "duration bar"
// (SCALE_Y px per frame). A child attaches to its parent's bar at the frame it
// branched from — so the end-frame continuation sits at the bar's bottom (an
// unbroken vertical spine) and an earlier branch splits off sideways partway up.
const SCALE_Y = 1.35, COL_GAP = 185, TOP = 40

function buildTree(anims) {
  const byId = new Map(anims.map(a => [a.id, a]))
  const childrenOf = new Map(), parentOf = new Map(), inTree = new Set()
  for (const a of anims) {
    const src = a.continues_from?.source_id
    if (src && byId.has(src)) {
      inTree.add(a.id); inTree.add(src)
      parentOf.set(a.id, src)
      if (!childrenOf.has(src)) childrenOf.set(src, [])
      childrenOf.get(src).push(a.id)
    }
  }
  const roots = [...inTree].filter(id => !parentOf.has(id))
  return { byId, childrenOf, parentOf, roots }
}

function pathToRoot(id) {
  const path = []; let cur = id
  while (cur) { path.unshift(cur); cur = CTX.parentOf.get(cur) }
  return path
}

const numFrames = (id) => Number(CTX.byId.get(id)?.num_frames) || 60
const branchFrameOf = (id) => { const f = CTX.byId.get(id)?.continues_from?.frame; return f == null ? 0 : Number(f) }
const childrenSorted = (id) => (CTX.childrenOf.get(id) || []).slice().sort((a, b) => branchFrameOf(a) - branchFrameOf(b))
// the continuation that carries the spine = the child taken from the latest frame
const spineChild = (id) => { const ch = CTX.childrenOf.get(id) || []; return ch.length ? ch.reduce((m, c) => branchFrameOf(c) > branchFrameOf(m) ? c : m, ch[0]) : null }
function descendantsOf(id) {
  const out = new Set(), stack = [id]
  while (stack.length) { const x = stack.pop(); if (out.has(x)) continue; out.add(x); for (const c of (CTX.childrenOf.get(x) || [])) stack.push(c) }
  return out
}

// Node labels are the FULL move name (prompt, minus the boilerplate lead). They
// get truncated only when a branch column to the right would overlap them, so the
// text never runs across the branch lines.
const _measure = (() => { const c = document.createElement('canvas').getContext('2d'); c.font = '12px system-ui, sans-serif'; return c })()
const fullLabel = (id) => (CTX.byId.get(id)?.prompt || id).replace(/^(a martial artist|a person|the practitioner|the martial artist)\s+/i, '').trim()
function fitLabel(text, maxW) {
  if (!isFinite(maxW)) return text
  if (_measure.measureText(text).width <= maxW) return text
  let t = text
  while (t.length > 1 && _measure.measureText(t + '…').width > maxW) t = t.slice(0, -1)
  return t.replace(/\s+$/, '') + '…'
}
// Available label width = distance to the nearest right-hand column whose bar
// overlaps this node's row (∞ if nothing is to the right at this height).
function labelMaxWidth(id) {
  const me = POS.get(id); if (!me) return Infinity
  let blockX = Infinity
  for (const [oid, o] of POS) {
    if (oid === id || o.x <= me.x) continue
    if (me.y >= o.y - 9 && me.y <= o.y + o.nf * SCALE_Y + 9) blockX = Math.min(blockX, o.x)
  }
  return blockX === Infinity ? Infinity : Math.max(28, blockX - me.x - 16)
}
const nodeLabel = (id) => fitLabel(fullLabel(id), labelMaxWidth(id))

// Assign each node a column (x) and a y at its frame-0 (where it attaches to the
// parent's bar). Spine child stays in-column; each other branch takes a new column.
function computeLayout() {
  POS = new Map()
  let nextCol = 0
  const place = (id, col, yStart) => {
    POS.set(id, { x: col * COL_GAP, y: yStart, nf: numFrames(id) })
    const sp = spineChild(id)
    for (const c of childrenSorted(id)) {
      const cy = yStart + branchFrameOf(c) * SCALE_Y
      if (c === sp) place(c, col, cy)
      else { nextCol += 1; place(c, nextCol, cy) }
    }
  }
  if (currentRoot && CTX.byId.has(currentRoot)) place(currentRoot, 0, TOP)
}

function setupGraph() {
  const el = document.getElementById('tree-svg')
  el.innerHTML = ''
  svg = d3.select(el)
  gAll = svg.append('g')
  gBar = gAll.append('g')    // per-move timeline bars
  gLink = gAll.append('g')   // branch connectors
  gPlay = gAll.append('g')   // playback progress (over bars, under nodes)
  gNode = gAll.append('g')   // node circles + labels
  // progress overlay: bright line over the played portion of the active bar + a playhead dot
  playLine = gPlay.append('line').attr('stroke', '#fff3a0').attr('stroke-width', 5)
    .attr('stroke-linecap', 'round').style('display', 'none')
  playHead = gPlay.append('circle').attr('r', 4).attr('fill', '#ffffff').style('display', 'none')
  zoomBehavior = d3.zoom().scaleExtent([0.15, 2.5]).on('zoom', (e) => gAll.attr('transform', e.transform))
  svg.call(zoomBehavior)
}

function buildGraph() {
  setupGraph()
  computeLayout()
  updateGraph()
  const w = (document.getElementById('tree-svg').clientWidth || 460)
  svg.call(zoomBehavior.transform, d3.zoomIdentity.translate(w / 2, 24).scale(0.7))
}

// mode color: yellow = play whole path, blue = view this move alone
const modeColor = () => mode === 'path' ? '#ffe14a' : '#4ea8ff'
function nodeColor(id) {
  if (id === activeId) return modeColor()    // ONLY the node playing right now takes the mode color
  if (pathSet.has(id)) return '#e0a24a'      // on the playing path (incl. the leaf, until the playhead arrives)
  if (id === selectedId) return modeColor()  // single selected move (view-move mode, no path)
  if ((CTX.childrenOf.get(id) || []).length) return '#5fb98c'
  return '#8a8a93'
}
const nodeRadius = (id) => id === activeId ? 9 : id === selectedId ? 7 : 5
const barColor = (id) => id === activeId ? modeColor() : pathSet.has(id) ? '#e0a24a' : (id === selectedId ? modeColor() : '#3f3f48')
function refreshNodeStyles() {
  if (gNode) gNode.selectAll('g.n circle').attr('fill', d => nodeColor(d.id)).attr('r', d => nodeRadius(d.id))
  if (gBar) gBar.selectAll('line.bar').attr('stroke', d => barColor(d.id)).attr('stroke-width', d => d.id === activeId ? 5 : 3)
  if (gLink) gLink.selectAll('path.conn').attr('stroke', d => pathSet.has(d.id) ? '#e0a24a' : '#4a4a52')
}

function updateGraph() {
  const nodes = [...POS.keys()].map(id => ({ id, ...POS.get(id), label: nodeLabel(id) }))

  // timeline bars: each move's duration as a vertical line (length = frames)
  gBar.selectAll('line.bar').data(nodes, d => d.id).join('line').attr('class', 'bar')
    .attr('x1', d => d.x).attr('y1', d => d.y).attr('x2', d => d.x).attr('y2', d => d.y + d.nf * SCALE_Y)
    .attr('stroke-linecap', 'round')
    .attr('stroke', d => barColor(d.id)).attr('stroke-width', d => d.id === activeId ? 5 : 3)

  // connectors: from the parent bar at the branch-frame height to the child's bar top
  const conns = []
  for (const id of POS.keys()) {
    const p = CTX.parentOf.get(id)
    if (p && POS.has(p)) {
      const pp = POS.get(p), cc = POS.get(id)
      conns.push({ id, x1: pp.x, y: pp.y + branchFrameOf(id) * SCALE_Y, x2: cc.x })
    }
  }
  gLink.selectAll('path.conn').data(conns, d => d.id).join('path').attr('class', 'conn').attr('fill', 'none')
    .attr('stroke-width', 1.5).attr('stroke', d => pathSet.has(d.id) ? '#e0a24a' : '#4a4a52')
    .attr('d', d => `M${d.x1},${d.y} L${d.x2},${d.y}`)   // child bar top == this y, so a clean elbow

  const node = gNode.selectAll('g.n').data(nodes, d => d.id)
  const nEnter = node.enter().append('g').attr('class', 'n').style('cursor', 'pointer')
    .on('click', (e, d) => onNodeClick(d.id))
  nEnter.append('circle')
  nEnter.append('title')   // full move name on hover (handy when the label is truncated)
  nEnter.append('text').attr('dy', '0.32em').attr('x', 9).attr('font-size', '12px')
    .attr('fill', '#dcdce0').attr('paint-order', 'stroke').attr('stroke', '#1c1c1f').attr('stroke-width', 3)
  const nAll = nEnter.merge(node)
  nAll.attr('transform', d => `translate(${d.x},${d.y})`)
  nAll.select('circle').attr('fill', d => nodeColor(d.id)).attr('r', d => nodeRadius(d.id))
  nAll.select('text').text(d => d.label)
  nAll.select('title').text(d => fullLabel(d.id))
  node.exit().remove()
}

// First click on a node → play the whole path to it. Click the SAME node again
// → toggle to viewing just that move (and back). The tree stays fully expanded.
function onNodeClick(id) {
  if (id === selectedId) mode = (mode === 'path' ? 'move' : 'path')
  else mode = 'path'
  select(id)
}

async function select(id) {
  selectedId = id
  if (previewingPrompt) { previewingPrompt = null; previewClipId = null; if (drawerOpen()) renderDrawer() }   // a kata move now plays, not a library action
  if (charRoot) charRoot.rotation.y = 0
  activeId = null; playSegs = null
  pathSet = new Set(mode === 'path' ? pathToRoot(id) : [])
  refreshNodeStyles()
  const name = shortLabel(CTX.byId.get(id)?.prompt)
  try {
    if (mode === 'move') {
      const m = await fetchAnimation(id)
      animator?.setMotion(m, { loop: true }); currentMotion = m
      playSegs = [{ id, start: 0, end: m.num_frames, lo: 0, hi: m.num_frames }]
      setScrubRange(m)
      setStatus(`move: ${name}`)
    } else {
      const path = pathToRoot(id)
      setStatus(`stitching ${path.length} moves…`)
      const m = await stitchPath(path)
      animator?.setMotion(m, { loop: true }); currentMotion = m
      setScrubRange(m)
      // Frame ranges of each move within the stitched clip — must mirror the
      // server's cut: a clip keeps [lo..hi), where lo drops the seam frame 0 on
      // continuations and hi is cut at the NEXT clip's branch frame (so a parent
      // ends at the branch point, not its full length).
      playSegs = []; let f0 = 0
      path.forEach((nid, i) => {
        const nf = Number(CTX.byId.get(nid)?.num_frames) || 0
        const lo = i === 0 ? 0 : 1
        let hi = nf
        if (i < path.length - 1) {
          let bf = CTX.byId.get(path[i + 1])?.continues_from?.frame
          if (bf != null) { bf = bf < 0 ? nf + bf : bf; if (bf >= 0 && bf < nf) hi = bf + 1 }
        }
        const len = Math.max(0, hi - lo)
        playSegs.push({ id: nid, start: f0, end: f0 + len, lo, hi }); f0 += len
      })
      setStatus(`kata path (${path.length} moves, ${m.num_frames}f) — flows through, no reset`)
    }
  } catch (e) { setStatus(`error: ${e.message}`) }
}

// --- Kata selector (one root tree at a time) ------------------------------
const kataSel = document.getElementById('kata-select')
const kataLabel = (rootId) => {
  const p = (CTX.byId.get(rootId)?.prompt || rootId)
    .replace(/^(a martial artist|a person|the practitioner|the martial artist)\s+/i, '')
  return `${p.slice(0, 44)}  (${descendantsOf(rootId).size} moves)`
}
function populateKataSelect() {
  kataSel.innerHTML = ''
  for (const r of CTX.roots) {
    const o = document.createElement('option'); o.value = r; o.textContent = kataLabel(r); kataSel.appendChild(o)
  }
  kataSel.value = currentRoot
}
function showKataStatus() {
  setStatus(`${descendantsOf(currentRoot).size} moves. Click a node to ${mode === 'move' ? 'view that move' : 'play the path up to it'}.`)
}
kataSel.onchange = () => {
  currentRoot = kataSel.value
  selectedId = null; activeId = null; playSegs = null; pathSet = new Set()
  buildGraph()
  showKataStatus()
}

async function refreshTree() {
  setStatus('loading moves…')
  const anims = await listAnimations()
  CTX = buildTree(anims)
  if (!CTX.roots.length) { setStatus('no kata moves yet'); return }
  if (!currentRoot || !CTX.byId.has(currentRoot)) currentRoot = CTX.roots[0]
  populateKataSelect()
  buildGraph()
  showKataStatus()
}

// --- Scrubber (scrub the current move or path) ----------------------------
const scrub = document.getElementById('frame-scrub')
const frameLabel = document.getElementById('frame-label')
const playBtn = document.getElementById('play-pause')
const updatePlayBtn = () => { playBtn.textContent = (animator && animator.playing) ? '❚❚' : '▶' }
const syncScrubColor = () => { scrub.style.accentColor = modeColor() }   // tint scrubber to the mode
function setPlaying(on) {
  if (!animator) return
  if (on) { animator.playing = true; animator.lastTime = performance.now() / 1000 }
  else animator.playing = false
  updatePlayBtn()
}
function setScrubRange(m) {
  curFps = m.fps || 30; curFrames = m.num_frames || 0
  scrub.max = Math.max(0, curFrames - 1); scrub.value = 0
  frameLabel.textContent = curFrames ? `0/${curFrames - 1}` : '—'
  syncScrubColor(); setPlaying(true)   // a freshly-loaded move/path starts playing
}
function seekTo(frame) {
  if (!animator) return
  animator.elapsed = frame / (curFps || 30)
  animator.frame = -1; animator.playing = true; animator.update(); animator.playing = false  // seek + hold
}
playBtn.onclick = () => setPlaying(!(animator && animator.playing))
// Scrubbing pauses playback and stays paused — press ▶ to resume.
scrub.oninput = () => { scrubbing = true; const f = Number(scrub.value); seekTo(f); frameLabel.textContent = `${f}/${curFrames - 1}`; updatePlayBtn() }
scrub.onchange = () => { scrubbing = false; updatePlayBtn() }

// --- Actions library drawer -----------------------------------------------
// Reusable standalone moves (continues_from = none) matched to store clips by
// prompt. Preview plays one in the viewer; regenerate makes a fresh take. The
// list is user-editable (add / edit prompt) and persisted in localStorage.
const DEFAULT_ACTIONS = [
  { name: 'Down block', prompt: 'a martial artist performs a low down block in a front stance' },
  { name: 'Rising block', prompt: 'a martial artist performs a rising block in a front stance' },
  { name: 'Knife-hand block', prompt: 'a martial artist performs a knife-hand block in a back stance' },
  { name: 'Lunge punch', prompt: 'a martial artist steps forward with a lunge punch' },
  { name: 'Reverse punch', prompt: 'a martial artist throws a reverse punch in a front stance' },
  { name: 'Back-fist', prompt: 'a martial artist throws a back-fist strike' },
  { name: 'Front kick', prompt: 'a martial artist throws a front snap kick' },
  { name: 'Side kick', prompt: 'a martial artist throws a side kick' },
  { name: 'Roundhouse', prompt: 'a martial artist throws a roundhouse kick' },
  { name: 'Turn / stance', prompt: 'a martial artist turns ninety degrees into a front stance' },
]
const ACTIONS_KEY = 'kata-actions-v1'
function loadActions() {
  try { const a = JSON.parse(localStorage.getItem(ACTIONS_KEY)); if (Array.isArray(a) && a.length) return a } catch {}
  return DEFAULT_ACTIONS.map((x, i) => ({ id: 'a' + i, seconds: 2.2, ...x }))
}
const saveActions = () => localStorage.setItem(ACTIONS_KEY, JSON.stringify(actions))
let actions = loadActions()
function deriveName(p) {   // a short title from the prompt (no separate name field)
  const s = p.replace(/^(a martial artist|a person|the practitioner|the martial artist)\s+/i, '').trim()
  const w = s.split(/\s+/).slice(0, 4).join(' ')
  return (w.charAt(0).toUpperCase() + w.slice(1)).slice(0, 28) || 'New move'
}
let adding = false, addSeconds = 2.2   // new-action gen flag + the add form's chosen duration
async function addAction(promptText, seconds, fromFrame) {
  const p = (promptText || '').trim(); if (!p) return
  const act = { id: 'a' + Date.now().toString(36), name: deriveName(p), prompt: p, seconds: seconds ?? 2.2 }
  actions.unshift(act); saveActions()   // newest first, just under the add form
  adding = true; renderDrawer()                 // card + add-button feedback appear at once
  // generate from scratch, or — if asked and something is previewed — from that paused frame
  try { await (fromFrame && previewClipId ? regenFromFrame(act) : regenAction(act)) }
  finally { adding = false; renderDrawer() }
}
function deleteAction(act) {
  actions = actions.filter(a => a.id !== act.id); saveActions()
  if (editingId === act.id) editingId = null
  if (previewingPrompt === act.prompt) { previewingPrompt = null; previewClipId = null }
  renderDrawer(); setStatus(`removed “${act.name}”`)
}
let editingId = null   // action whose prompt is being edited inline
let durOpen = new Set()   // action ids whose (collapsed) duration section is expanded
const startEdit = (act) => { editingId = act.id; renderDrawer() }
const cancelEdit = () => { editingId = null; renderDrawer() }
function commitEdit(act, value) {
  const v = (value || '').trim(); editingId = null
  if (v && v !== act.prompt) { act.prompt = v; saveActions(); setStatus(`updated “${act.name}” — press ↻ to regenerate it`) }
  renderDrawer()   // a changed prompt no longer matches the old clip → shows "not generated"
}
let actionClips = {}             // prompt -> latest standalone clip metadata
let clipById = {}                // id -> clip metadata (incl. continuations)
let previewingPrompt = null      // action currently shown in the viewer
let busyPrompt = null            // action currently regenerating
let aScrub = null, aLabel = null, aPlayBtn = null   // the previewing card's playback controls (synced in tick)
let previewClipId = null   // clip currently in the viewer — the source for "from preview frame"
// Fill the played portion of a scrubber (modern media-bar look).
function scrubFill(el) {
  const max = Number(el.max) || 1, pct = (Number(el.value) / max) * 100
  el.style.background = `linear-gradient(90deg, #5ad1ff ${pct}%, #34343c ${pct}%)`
}
async function loadActionClips() {
  actionClips = {}; clipById = {}
  for (const a of await listAnimations()) {                 // sorted newest-first by the store
    clipById[a.id] = a
    if (a.continues_from?.source_id) continue               // standalone moves match by prompt
    if (!actionClips[a.prompt]) actionClips[a.prompt] = a   // keep the most recent per prompt
  }
}
// An action's clip: an explicit clipId (e.g. a regenerate-from-frame result) wins,
// else the newest standalone clip matching its prompt.
const clipForAction = (act) => (act.clipId && clipById[act.clipId]) || actionClips[act.prompt]
function drawerOpen() { return drawer.classList.contains('open') }
async function previewAction(act) {
  const clip = clipForAction(act); if (!clip) return
  previewingPrompt = act.prompt; previewClipId = clip.id; renderDrawer()   // highlight + show playback controls right away
  const m = await fetchAnimation(clip.id)
  if (charRoot) charRoot.rotation.y = 0                                     // each preview starts unrotated
  animator?.setMotion(m, { loop: true }); currentMotion = m
  selectedId = null; activeId = null; pathSet = new Set(); refreshNodeStyles()
  playSegs = [{ id: clip.id, start: 0, end: m.num_frames, lo: 0, hi: m.num_frames }]   // drives the scrubber; no tree node to highlight
  setScrubRange(m); renderDrawer()                          // re-render so the card scrubber gets the right range
  setStatus(`previewing action: ${act.name}`)
}
async function regenAction(act) {
  busyPrompt = act.prompt; renderDrawer()                   // show the generating state on the card
  setStatus(`generating “${act.name}”…`)
  try {
    const r = await fetch(`${KIMODO_URL}/generate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ prompt: act.prompt, seconds: act.seconds ?? 2.2 }) })
    if (!r.ok) throw new Error(`generate ${r.status}`)
    act.clipId = (await r.json()).id; saveActions()         // pin this action to the new clip
    await loadActionClips()
    busyPrompt = null
    await previewAction(act)                                // renders + auto-previews the fresh take
    setStatus(`✓ generated “${act.name}”`)
  } catch (e) { busyPrompt = null; renderDrawer(); setStatus('generation failed: ' + e.message) }
}
// Generate this action so it STARTS from the frame currently paused in the
// preview — the source is whatever clip is being previewed (may be a DIFFERENT
// action), so you can chain one move's pose into another via /generate_continue.
async function regenFromFrame(act) {
  if (!previewClipId || !animator) return
  const frame = Math.round(animator.frame || 0)
  busyPrompt = act.prompt; renderDrawer()
  setStatus(`generating “${act.name}” from the previewed frame ${frame}…`)
  try {
    const r = await fetch(`${KIMODO_URL}/generate_continue`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_id: previewClipId, source_frame: frame, prompt: act.prompt, seconds: act.seconds ?? 2.2, stitch: false }),
    })
    if (!r.ok) throw new Error(`generate_continue ${r.status}`)
    act.clipId = (await r.json()).id; saveActions()
    await loadActionClips()
    busyPrompt = null
    await previewAction(act)
    setStatus(`✓ generated “${act.name}” from the previewed frame ${frame}`)
  } catch (e) { busyPrompt = null; renderDrawer(); setStatus('generation failed: ' + e.message) }
}
// Bake a yaw into the action's clip data (so facing is part of the animation) and
// re-point the action to the new clip. Resets the live rotation afterward.
async function saveRotation(act, deg) {
  const clip = clipForAction(act); if (!clip || !deg) return
  busyPrompt = act.prompt; renderDrawer()
  setStatus(`rotating “${act.name}” ${deg}°…`)
  try {
    const r = await fetch(`${KIMODO_URL}/rotate_clip`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: clip.id, degrees: deg }) })
    if (!r.ok) throw new Error(`rotate_clip ${r.status}`)
    act.clipId = (await r.json()).id; saveActions()
    if (charRoot) charRoot.rotation.y = 0
    await loadActionClips(); busyPrompt = null
    await previewAction(act)
    setStatus(`✓ baked ${deg}° into “${act.name}”`)
  } catch (e) { busyPrompt = null; renderDrawer(); setStatus('rotate failed: ' + e.message) }
}
function renderDrawer() {
  const box = document.getElementById('actions-list'); if (!box) return
  box.innerHTML = ''
  // Add form: one prompt field + button → creates AND generates in one press.
  const form = document.createElement('div'); form.className = 'add-form'
  const addTa = document.createElement('textarea'); addTa.className = 'add-input'; addTa.rows = 2
  addTa.placeholder = 'describe a new move, e.g. “a martial artist throws an elbow strike”'
  addTa.disabled = adding
  const submitAdd = () => { const v = addTa.value.trim(); if (v && !adding) addAction(v, addSeconds) }
  const submitAddFrame = () => { const v = addTa.value.trim(); if (v && !adding && previewClipId) addAction(v, addSeconds, true) }
  addTa.onkeydown = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitAdd() } }
  // buttons row: add (from scratch) + add-from-previewed-frame
  const arow = document.createElement('div'); arow.className = 'add-row'
  const add = document.createElement('button'); add.className = 'add-action'; add.disabled = adding
  add.innerHTML = adding ? '<span class="spin">⟳</span> generating…' : '+ Add &amp; generate'
  add.onclick = submitAdd
  const addf = document.createElement('button'); addf.className = 'add-action'; addf.disabled = adding || !previewClipId
  addf.textContent = '↳ from preview frame'
  addf.title = previewClipId ? 'add a move that starts from the frame currently paused in the preview' : 'preview an action and pause on a frame first'
  addf.onclick = submitAddFrame
  arow.appendChild(add); arow.appendChild(addf)
  // duration, below the buttons
  const adur = document.createElement('div'); adur.className = 'dur'
  const atag = document.createElement('span'); atag.className = 'dur-tag'; atag.textContent = '⏱'
  const arange = document.createElement('input'); arange.type = 'range'; arange.min = '0.5'; arange.max = '5'; arange.step = '0.1'
  arange.value = String(addSeconds); arange.disabled = adding
  const albl = document.createElement('span'); albl.className = 'dur-lbl'; albl.textContent = `${addSeconds.toFixed(1)}s`
  arange.oninput = () => { addSeconds = Number(arange.value); albl.textContent = `${addSeconds.toFixed(1)}s` }
  adur.appendChild(atag); adur.appendChild(arange); adur.appendChild(albl)
  form.appendChild(addTa); form.appendChild(arow); form.appendChild(adur); box.appendChild(form)
  aScrub = aLabel = aPlayBtn = null   // refreshed below if a previewing card renders
  let editTa = null
  for (const act of actions) {
    const has = !!clipForAction(act)
    const busy = busyPrompt === act.prompt
    const editing = editingId === act.id
    const playing = previewingPrompt === act.prompt && !busy && !editing
    const secs = act.seconds ?? 2.2
    const card = document.createElement('div'); card.className = 'action-card' + (playing ? ' playing' : '')
    const nm = document.createElement('div'); nm.className = 'nm'
    nm.innerHTML = `${act.name} <span class="dur-badge">${secs.toFixed(1)}s</span>`
      + (playing ? ' <span class="badge">● previewing</span>'
      : busy ? ' <span class="badge gen"><span class="spin">⟳</span> generating…</span>' : '')
    card.appendChild(nm)
    const durBadge = nm.querySelector('.dur-badge')

    if (editing) {
      const ta = document.createElement('textarea'); ta.className = 'pr-edit'; ta.rows = 3; ta.value = act.prompt
      ta.onkeydown = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); commitEdit(act, ta.value) } else if (e.key === 'Escape') cancelEdit() }
      const bar = document.createElement('div'); bar.className = 'edit-bar'
      const save = document.createElement('button'); save.textContent = '✓ save'; save.onclick = () => commitEdit(act, ta.value)
      const cancel = document.createElement('button'); cancel.textContent = 'cancel'; cancel.onclick = cancelEdit
      bar.appendChild(save); bar.appendChild(cancel)
      card.appendChild(ta); card.appendChild(bar); editTa = ta
    } else {
      // delete X — top-right corner of the card
      const x = document.createElement('button'); x.className = 'del-x'; x.textContent = '✕'; x.title = 'delete action'; x.disabled = busy
      x.onclick = () => deleteAction(act); card.appendChild(x)

      const pr = document.createElement('div'); pr.className = 'pr'
      const txt = document.createElement('span'); txt.className = 'pr-txt'; txt.textContent = act.prompt
      const ed = document.createElement('button'); ed.className = 'pr-edit-btn'; ed.textContent = '✎'; ed.title = 'edit prompt'; ed.disabled = busy
      ed.onclick = () => startEdit(act)
      pr.appendChild(txt); pr.appendChild(ed); card.appendChild(pr)

      // preview, or — while previewing this card — a media transport
      if (playing) {
        const max = Math.max(0, (curFrames || 1) - 1)
        const pp = document.createElement('button'); pp.className = 'pp'; pp.textContent = animator?.playing ? '❚❚' : '▶'
        pp.onclick = () => { setPlaying(!(animator && animator.playing)); pp.textContent = animator?.playing ? '❚❚' : '▶' }
        const lbl = document.createElement('span'); lbl.className = 'frame-lbl'; lbl.textContent = `${Math.round(animator?.frame || 0)}/${max}`
        const sc = document.createElement('input'); sc.type = 'range'; sc.className = 'play-scrub'; sc.min = 0; sc.max = max; sc.step = 1; sc.value = Math.round(animator?.frame || 0)
        sc.oninput = () => { scrubbing = true; const f = Number(sc.value); seekTo(f); pp.textContent = '▶'; lbl.textContent = `${f}/${max}`; scrubFill(sc) }
        sc.onchange = () => { scrubbing = false; pp.textContent = animator?.playing ? '❚❚' : '▶' }
        scrubFill(sc)
        const prow = document.createElement('div'); prow.className = 'play-row'
        prow.appendChild(pp); prow.appendChild(sc); prow.appendChild(lbl); card.appendChild(prow)
        aPlayBtn = pp; aScrub = sc; aLabel = lbl   // tick keeps these in sync with playback
      } else {
        const prev = document.createElement('button'); prev.className = 'prev-btn'; prev.disabled = !has || busy
        prev.textContent = busy ? 'generating…' : !has ? 'not generated' : '▶ preview'
        prev.onclick = () => previewAction(act); card.appendChild(prev)
      }

      // collapsible "options": the two regenerate buttons + duration
      const open = durOpen.has(act.id)
      const tog = document.createElement('button'); tog.className = 'dur-tog'
      tog.textContent = `${open ? '▾' : '▸'} options · ${secs.toFixed(1)}s`
      tog.onclick = () => { durOpen.has(act.id) ? durOpen.delete(act.id) : durOpen.add(act.id); renderDrawer() }
      card.appendChild(tog)
      if (open) {
        const rrow = document.createElement('div'); rrow.className = 'regen-row'
        const reg = document.createElement('button'); reg.className = 'rg'; reg.disabled = busy
        reg.innerHTML = busy ? '<span class="spin">⟳</span>' : '↻ regenerate'; reg.title = 'regenerate from scratch (rest pose)'
        reg.onclick = () => regenAction(act)
        const regf = document.createElement('button'); regf.className = 'rg'; regf.disabled = busy || !previewClipId
        regf.textContent = '↳ from preview frame'
        regf.title = previewClipId ? 'start this move from the frame currently paused in the preview (any action)' : 'preview an action and pause on a frame first'
        regf.onclick = () => regenFromFrame(act)
        rrow.appendChild(reg); rrow.appendChild(regf); card.appendChild(rrow)

        const dur = document.createElement('div'); dur.className = 'dur'
        const dtag = document.createElement('span'); dtag.className = 'dur-tag'; dtag.textContent = '⏱'
        const drange = document.createElement('input'); drange.type = 'range'; drange.min = '0.5'; drange.max = '5'; drange.step = '0.1'; drange.value = String(secs); drange.disabled = busy
        const dlbl = document.createElement('span'); dlbl.className = 'dur-lbl'; dlbl.textContent = `${secs.toFixed(1)}s`
        drange.oninput = () => { act.seconds = Number(drange.value); const t = `${act.seconds.toFixed(1)}s`; dlbl.textContent = t; if (durBadge) durBadge.textContent = t; tog.textContent = `▾ options · ${t}`; saveActions() }
        dur.appendChild(dtag); dur.appendChild(drange); dur.appendChild(dlbl); card.appendChild(dur)

        // rotation: yaw the character live (only while previewing this action), then
        // Save bakes it into the clip data. Save is enabled only once it's changed.
        const curDeg = (playing && charRoot) ? Math.round(charRoot.rotation.y * 180 / Math.PI) : 0
        const rotRow = document.createElement('div'); rotRow.className = 'rot-row'
        const rtag = document.createElement('span'); rtag.className = 'dur-tag'; rtag.textContent = '⟲'
        const rrange = document.createElement('input'); rrange.type = 'range'; rrange.min = '-180'; rrange.max = '180'; rrange.step = '1'; rrange.value = String(curDeg)
        rrange.disabled = !playing || busy
        rrange.title = playing ? 'rotate the character to face a direction' : 'preview this action to rotate it'
        const rlbl = document.createElement('span'); rlbl.className = 'dur-lbl'; rlbl.textContent = `${curDeg}°`
        const rsave = document.createElement('button'); rsave.className = 'rot-save'; rsave.textContent = 'save'; rsave.disabled = curDeg === 0 || busy
        rrange.oninput = () => { const d = Number(rrange.value); rlbl.textContent = `${d}°`; if (charRoot) charRoot.rotation.y = d * Math.PI / 180; rsave.disabled = d === 0 || busy }
        rsave.onclick = () => saveRotation(act, Number(rrange.value))
        rotRow.appendChild(rtag); rotRow.appendChild(rrange); rotRow.appendChild(rlbl); rotRow.appendChild(rsave); card.appendChild(rotRow)
      }
    }
    box.appendChild(card)
  }
  if (editTa) { editTa.focus(); editTa.select() }
}
const drawer = document.getElementById('actions-drawer')
const modelDrawer = document.getElementById('model-drawer')
const clothingDrawer = document.getElementById('clothing-drawer')
const leftTabs = document.getElementById('left-tabs')
const drawerToggleBtn = document.getElementById('drawer-toggle')
const modelToggleBtn = document.getElementById('model-toggle')
const clothingToggleBtn = document.getElementById('clothing-toggle')
const DRAWER_W = 330
// Tabs stay docked to the right edge of whichever drawer is open (so you can tab
// between them with one open), and highlight the active one.
function syncTabs() {
  const aOpen = drawer.classList.contains('open'), mOpen = modelDrawer.classList.contains('open'), cOpen = clothingDrawer.classList.contains('open')
  leftTabs.style.left = (aOpen || mOpen || cOpen) ? DRAWER_W + 'px' : '0'
  drawerToggleBtn.classList.toggle('active', aOpen)
  modelToggleBtn.classList.toggle('active', mOpen)
  clothingToggleBtn.classList.toggle('active', cOpen)
}
function openDrawer(on) {
  if (on) { modelDrawer.classList.remove('open'); clothingDrawer.classList.remove('open') }   // one drawer at a time
  drawer.classList.toggle('open', on)
  syncTabs()
  if (on) loadActionClips().then(renderDrawer)
  else { aScrub = aLabel = aPlayBtn = null }   // drop refs to removed card controls
}
drawerToggleBtn.onclick = () => openDrawer(!drawer.classList.contains('open'))

// --- Model picker drawer --------------------------------------------------
// A small curated set: the UniRig sbox citizens (sausage default + human male) +
// SMPL-X. The citizens come from the character registry; SMPL-X is a built-in.
const CURATED_MODEL_IDS = ['unirig_citizen', 'unirig_citizen_male', 'unirig_citizen_female', 'smplx']
let MODEL_OPTIONS = []
let registryLoaded = false
async function loadRegistryModels() {
  if (registryLoaded) return
  let regs = []
  try { regs = ((await (await fetch(`${KIMODO_URL}/characters`)).json()).characters) || [] }
  catch (e) { console.warn('character registry fetch failed', e) }
  const byId = new Map([...CHARACTERS, ...regs].map(c => [c.id, c]))
  MODEL_OPTIONS = CURATED_MODEL_IDS.map(id => byId.get(id)).filter(Boolean)
  registryLoaded = true
}
function openModel(on) {
  if (on) { drawer.classList.remove('open'); clothingDrawer.classList.remove('open') }
  modelDrawer.classList.toggle('open', on)
  syncTabs()
  if (on) loadRegistryModels().then(renderModelDrawer)
}
async function selectModel(id) {
  if (id === currentCharId) return
  const cfg = MODEL_OPTIONS.find(c => c.id === id) || getCharacter(id)
  setStatus(`loading model ${cfg.label || cfg.id}…`)
  currentCharId = id; renderModelDrawer()
  try { await loadCharacter(cfg); setStatus(`model: ${cfg.label || cfg.id}`) }
  catch (e) { setStatus('model load failed: ' + e.message) }
}
function renderModelDrawer() {
  const box = document.getElementById('model-list'); if (!box) return
  box.innerHTML = ''
  for (const c of MODEL_OPTIONS) {
    const b = document.createElement('button')
    b.className = 'model-opt' + (c.id === currentCharId ? ' on' : '')
    b.textContent = c.label || c.id
    b.onclick = () => selectModel(c.id)
    box.appendChild(b)
  }
}
modelToggleBtn.onclick = () => openModel(!modelDrawer.classList.contains('open'))

// --- Clothing drawer ------------------------------------------------------
function openClothing(on) {
  if (on) { drawer.classList.remove('open'); modelDrawer.classList.remove('open') }
  clothingDrawer.classList.toggle('open', on)
  syncTabs()
  if (on) renderClothingDrawer()
}
async function toggleClothing(item) {
  if (clothingWornId === item.id) { clothingWornId = null; detachClothing(); renderClothingDrawer(); setStatus('clothing: off'); return }
  if (!clothingUrlFor(item, currentCharId)) { setStatus(`${item.label} isn't available for this model`); return }
  clothingWornId = item.id
  setStatus(`loading ${item.label}…`)
  try { await applyClothingForCurrentBody(); setStatus(`clothing: ${item.label}`) }
  catch (e) { setStatus('clothing load failed: ' + e.message); clothingWornId = null }
  renderClothingDrawer()
}
function renderClothingDrawer() {
  const box = document.getElementById('clothing-list'); if (!box) return
  box.innerHTML = ''
  for (const c of CLOTHING) {
    const avail = !!clothingUrlFor(c, currentCharId)
    const b = document.createElement('button')
    b.className = 'model-opt' + (c.id === clothingWornId ? ' on' : '')
    b.textContent = (c.id === clothingWornId ? '✓ ' : '') + c.label + (avail ? '' : ' — n/a for this model')
    b.disabled = !avail && c.id !== clothingWornId
    b.style.opacity = avail ? '1' : '0.5'
    b.onclick = () => toggleClothing(c)
    box.appendChild(b)
  }
}
clothingToggleBtn.onclick = () => openClothing(!clothingDrawer.classList.contains('open'))

// Click outside an open drawer to dismiss it — but NOT on the tabs, and NOT on
// the 3D viewport (so orbiting the camera doesn't close the drawer).
document.addEventListener('pointerdown', (e) => {
  if (!drawer.classList.contains('open') && !modelDrawer.classList.contains('open') && !clothingDrawer.classList.contains('open')) return
  if (drawer.contains(e.target) || modelDrawer.contains(e.target) || clothingDrawer.contains(e.target) || leftTabs.contains(e.target) || app.contains(e.target)) return
  drawer.classList.remove('open'); modelDrawer.classList.remove('open'); clothingDrawer.classList.remove('open')
  syncTabs(); aScrub = aLabel = aPlayBtn = null
})

// --- Boot -----------------------------------------------------------------
function tick() {
  animator?.update()
  syncTwistBones()
  syncClothing()
  // The move whose segment is playing: highlight + pulse it, and run a playhead
  // down its timeline bar tracking the exact frame on screen.
  let shown = false
  if (playSegs && animator && typeof animator.frame === 'number') {
    const f = animator.frame
    const seg = playSegs.find(s => f >= s.start && f < s.end) || playSegs[playSegs.length - 1]
    if (seg) {
      const p = POS && POS.get(seg.id)
      if (p) {   // a kata tree node — pulse it + run the playhead down its bar
        if (seg.id !== activeId) { activeId = seg.id; refreshNodeStyles() }
        const own = seg.lo + (f - seg.start)
        const yHead = p.y + own * SCALE_Y
        playLine.attr('x1', p.x).attr('y1', p.y).attr('x2', p.x).attr('y2', yHead).style('display', null)
        playHead.attr('cx', p.x).attr('cy', yHead).style('display', null)
        const r = animator.playing ? 9 + 3 * (0.5 + 0.5 * Math.sin(performance.now() / 110)) : 9   // pulse while playing
        gNode.selectAll('g.n circle').filter(d => d.id === activeId).attr('r', r)
        shown = true
      }
      // keep the scrubbers (main panel + action card) in sync with playback
      if (!scrubbing && animator.playing && curFrames) {
        scrub.value = f; frameLabel.textContent = `${f}/${curFrames - 1}`
        if (aScrub) { aScrub.value = f; scrubFill(aScrub) }
        if (aLabel) aLabel.textContent = `${f}/${curFrames - 1}`
      }
      if (aPlayBtn) aPlayBtn.textContent = animator.playing ? '❚❚' : '▶'
    }
  }
  if (!shown && playHead) { playLine.style('display', 'none'); playHead.style('display', 'none') }
  // Follow lock: pan the camera + target by the character's ground movement.
  if (camFollow && animator?.pelvisBone) {
    animator.pelvisBone.getWorldPosition(_camTmp)
    if (camPrev) {
      const dx = _camTmp.x - camPrev.x, dz = _camTmp.z - camPrev.z
      camera.position.x += dx; camera.position.z += dz
      controls.target.x += dx; controls.target.z += dz
    }
    camPrev = { x: _camTmp.x, z: _camTmp.z }
  }
  controls.update()
  renderer.render(scene, camera)
  requestAnimationFrame(tick)
}
async function init() {
  setStatus('loading character…')
  await loadRegistryModels()   // so the default (registry) citizen config is available
  await loadCharacter(MODEL_OPTIONS.find(c => c.id === currentCharId) || getCharacter(currentCharId))
  tick()
  await refreshTree()
}
init().catch(e => { console.error(e); setStatus(`init failed: ${e.message}`) })
