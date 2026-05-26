// Kata move-tree viewer (/kata). Shows the kata moves (clips linked by
// `continues_from`) as a zoomable, collapsible d3 graph. Two modes:
//   • View move: click a node → play just that move.
//   • Play path: click a node → play the whole root→node path as one continuous
//     motion (server stitches the clips, carrying position+heading), so the
//     character flows through the kata without resetting between moves.
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import * as d3 from 'd3'
import { Animator } from './animator.js'
import { CHARACTERS } from './rigs.js'

const KIMODO_URL = import.meta.env.VITE_KIMODO_URL || 'http://localhost:7862'
const SMPLX_HEIGHT = 1.7
const statusEl = document.getElementById('status')
const setStatus = (t) => { statusEl.textContent = t }
let mode = 'path' // 'path' (play whole path to node) | 'move' (just that move); toggled by re-clicking a node
let scrubbing = false, curFps = 30, curFrames = 0

// --- Scene ----------------------------------------------------------------
const renderer = new THREE.WebGLRenderer({ antialias: true })
renderer.setPixelRatio(window.devicePixelRatio)
renderer.setSize(window.innerWidth, window.innerHeight)
renderer.outputColorSpace = THREE.SRGBColorSpace
document.getElementById('app').appendChild(renderer.domElement)
const scene = new THREE.Scene()
scene.background = new THREE.Color(0x1a1a1a)
const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.1, 100)
camera.position.set(0, 1.5, 3.4)
const controls = new OrbitControls(camera, renderer.domElement)
controls.target.set(0, 1.0, 0); controls.update()

// Camera controls: follow-the-character lock + reset to the default view.
const HOME_POS = new THREE.Vector3(0, 1.5, 3.4)
const HOME_TGT = new THREE.Vector3(0, 1.0, 0)
let camFollow = false, camPrev = null
const _camTmp = new THREE.Vector3()
const followBtn = document.getElementById('cam-follow')
const resetBtn = document.getElementById('cam-reset')
followBtn.onclick = () => { camFollow = !camFollow; followBtn.classList.toggle('on', camFollow); camPrev = null }
resetBtn.onclick = () => { camera.position.copy(HOME_POS); controls.target.copy(HOME_TGT); controls.update(); camPrev = null }
scene.add(new THREE.HemisphereLight(0xffffff, 0x222233, 1.2))
const dir = new THREE.DirectionalLight(0xffffff, 1.5); dir.position.set(3, 5, 2); scene.add(dir)
const floor = new THREE.Mesh(new THREE.PlaneGeometry(40, 40), new THREE.MeshStandardMaterial({ color: 0x2a2a2a, roughness: 1 }))
floor.rotation.x = -Math.PI / 2; scene.add(floor)
scene.add(new THREE.GridHelper(40, 80, 0x333333, 0x303030))
window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight; camera.updateProjectionMatrix()
  renderer.setSize(window.innerWidth, window.innerHeight)
})

// --- Character + animator (smplx neutral) ---------------------------------
let animator = null
const gltfLoader = new GLTFLoader()
async function loadCharacter(cfg) {
  const gltf = await gltfLoader.loadAsync(cfg.url)
  const root = gltf.scene
  root.scale.setScalar(cfg.scale ?? 1.0)
  scene.add(root)
  let skinned = null
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
  animator = new Animator(target, {
    mapping: cfg.mapping, blends: cfg.blends || {}, scale: strideScale, groundOffsetY,
    alignMode: target.isSkinnedMesh ? 'rest' : 'none',
  })
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
  if (previewingPrompt) { previewingPrompt = null; if (drawerOpen()) renderDrawer() }   // a kata move now plays, not a library action
  activeId = null; playSegs = null
  pathSet = new Set(mode === 'path' ? pathToRoot(id) : [])
  refreshNodeStyles()
  const name = shortLabel(CTX.byId.get(id)?.prompt)
  try {
    if (mode === 'move') {
      const m = await fetchAnimation(id)
      animator?.setMotion(m, { loop: true })
      playSegs = [{ id, start: 0, end: m.num_frames, lo: 0, hi: m.num_frames }]
      setScrubRange(m)
      setStatus(`move: ${name}`)
    } else {
      const path = pathToRoot(id)
      setStatus(`stitching ${path.length} moves…`)
      const m = await stitchPath(path)
      animator?.setMotion(m, { loop: true })
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
// prompt. Preview plays one in the viewer; regenerate makes a fresh take.
const ACTIONS = [
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
let actionClips = {}             // prompt -> latest standalone clip metadata
let previewingPrompt = null      // action currently shown in the viewer
let busyPrompt = null            // action currently regenerating
async function loadActionClips() {
  actionClips = {}
  for (const a of await listAnimations()) {                 // sorted newest-first by the store
    if (a.continues_from?.source_id) continue               // standalone moves only
    if (!actionClips[a.prompt]) actionClips[a.prompt] = a   // keep the most recent per prompt
  }
}
function drawerOpen() { return drawer.classList.contains('open') }
async function previewAction(act) {
  const clip = actionClips[act.prompt]; if (!clip) return
  previewingPrompt = act.prompt; renderDrawer()             // highlight the card right away
  const m = await fetchAnimation(clip.id)
  animator?.setMotion(m, { loop: true })
  selectedId = null; activeId = null; pathSet = new Set(); refreshNodeStyles()
  playSegs = [{ id: clip.id, start: 0, end: m.num_frames, lo: 0, hi: m.num_frames }]   // scrubber works; no tree node to highlight
  setScrubRange(m)
  setStatus(`previewing action: ${act.name}`)
}
async function regenAction(act) {
  busyPrompt = act.prompt; renderDrawer()                   // show the regenerating state on the card
  setStatus(`regenerating “${act.name}”…`)
  try {
    const r = await fetch(`${KIMODO_URL}/generate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ prompt: act.prompt, seconds: 2.2 }) })
    if (!r.ok) throw new Error(`generate ${r.status}`)
    await loadActionClips()
    busyPrompt = null
    await previewAction(act)                                // renders + auto-previews the fresh take
    setStatus(`✓ regenerated “${act.name}”`)
  } catch (e) { busyPrompt = null; renderDrawer(); setStatus('regen failed: ' + e.message) }
}
function renderDrawer() {
  const box = document.getElementById('actions-list'); if (!box) return
  box.innerHTML = ''
  for (const act of ACTIONS) {
    const has = !!actionClips[act.prompt]
    const busy = busyPrompt === act.prompt
    const playing = previewingPrompt === act.prompt && !busy
    const card = document.createElement('div'); card.className = 'action-card' + (playing ? ' playing' : '')
    const badge = playing ? ' <span class="badge">● previewing</span>'
      : busy ? ' <span class="badge gen"><span class="spin">⟳</span> regenerating…</span>' : ''
    card.innerHTML = `<div class="nm">${act.name}${badge}</div><div class="pr">${act.prompt}</div>`
    const row = document.createElement('div'); row.className = 'row'
    const prev = document.createElement('button'); prev.className = 'prev'
    prev.disabled = !has || busy
    prev.textContent = busy ? 'regenerating…' : !has ? 'not generated' : playing ? '❚❚ previewing' : '▶ preview'
    prev.onclick = () => previewAction(act)
    const reg = document.createElement('button'); reg.title = 'regenerate'; reg.disabled = busy
    reg.innerHTML = busy ? '<span class="spin">⟳</span>' : '↻'
    reg.onclick = () => regenAction(act)
    row.appendChild(prev); row.appendChild(reg); card.appendChild(row); box.appendChild(card)
  }
}
const drawer = document.getElementById('actions-drawer')
const drawerToggle = document.getElementById('drawer-toggle')
function openDrawer(on) {
  drawer.classList.toggle('open', on)
  drawerToggle.style.display = on ? 'none' : 'block'
  if (on) loadActionClips().then(renderDrawer)
}
drawerToggle.onclick = () => openDrawer(true)
document.getElementById('drawer-close').onclick = () => openDrawer(false)

// --- Boot -----------------------------------------------------------------
function tick() {
  animator?.update()
  // The move whose segment is playing: highlight + pulse it, and run a playhead
  // down its timeline bar tracking the exact frame on screen.
  let shown = false
  if (playSegs && animator && typeof animator.frame === 'number' && POS) {
    const f = animator.frame
    const seg = playSegs.find(s => f >= s.start && f < s.end) || playSegs[playSegs.length - 1]
    const p = seg && POS.get(seg.id)
    if (p) {
      if (seg.id !== activeId) { activeId = seg.id; refreshNodeStyles() }
      const own = seg.lo + (f - seg.start)                    // frame within this move
      const yHead = p.y + own * SCALE_Y
      playLine.attr('x1', p.x).attr('y1', p.y).attr('x2', p.x).attr('y2', yHead).style('display', null)
      playHead.attr('cx', p.x).attr('cy', yHead).style('display', null)
      // pulse only while actually playing; hold steady (enlarged) while scrubbing
      const r = animator.playing ? 9 + 3 * (0.5 + 0.5 * Math.sin(performance.now() / 110)) : 9
      gNode.selectAll('g.n circle').filter(d => d.id === activeId).attr('r', r)
      // keep the scrubber + label in sync with playback (unless the user is dragging)
      if (!scrubbing && animator.playing && curFrames) { scrub.value = f; frameLabel.textContent = `${f}/${curFrames - 1}` }
      shown = true
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
  const smplx = CHARACTERS.find(c => c.id === 'smplx') || CHARACTERS[0]
  await loadCharacter(smplx)
  tick()
  await refreshTree()
}
init().catch(e => { console.error(e); setStatus(`init failed: ${e.message}`) })
