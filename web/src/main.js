import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { Animator } from './animator.js'
import { CHARACTERS, getCharacter, mixamoMapping } from './rigs.js'

// Mapping kinds the backend may return when importing a remote character.
// Keep in sync with kimodo/scripts/mixamo.py.
const MAPPING_BUILDERS = { mixamo: mixamoMapping }

const KIMODO_URL = import.meta.env.VITE_KIMODO_URL || 'http://localhost:7862'
const SMPLX_HEIGHT = 1.7  // approx height of the SMPL-X neutral mesh, used for stride scaling

const statusEl = document.getElementById('status')
const promptEl = document.getElementById('prompt')
const btnEl = document.getElementById('generate')
const secondsEl = document.getElementById('seconds')
const secondsLabelEl = document.getElementById('seconds-label')
const characterEl = document.getElementById('character')
const alignEl = document.getElementById('align')
const savedEl = document.getElementById('saved')
const deleteEl = document.getElementById('delete')

secondsEl.addEventListener('input', () => {
  secondsLabelEl.textContent = `${parseFloat(secondsEl.value).toFixed(1)} s`
})

function appendCharacterOption(c) {
  const opt = document.createElement('option')
  opt.value = c.id
  opt.textContent = c.label
  characterEl.appendChild(opt)
}

function addCharacter(charConfig) {
  // Hydrate mapping if it came from the server as a kind name.
  if (typeof charConfig.mapping === 'string' || charConfig.mappingKind) {
    const kind = charConfig.mappingKind || charConfig.mapping
    const builder = MAPPING_BUILDERS[kind]
    if (!builder) throw new Error(`Unknown mapping kind: ${kind}`)
    charConfig = { ...charConfig, mapping: builder() }
  }
  if (!CHARACTERS.find(c => c.id === charConfig.id)) {
    CHARACTERS.push(charConfig)
    appendCharacterOption(charConfig)
  }
  return charConfig
}

// Snapshot built-in character ids before any imports so we can tell them
// apart later when persisting (we only save imports, not the bundled rigs).
const BUILTIN_CHAR_IDS = new Set(CHARACTERS.map(c => c.id))

for (const c of CHARACTERS) appendCharacterOption(c)

async function refreshServerCharacters() {
  // Source of truth lives on the server. Pull on startup, on import, on delete.
  try {
    const r = await fetch(`${KIMODO_URL}/characters`)
    if (!r.ok) throw new Error(`API ${r.status}: ${await r.text()}`)
    const { characters } = await r.json()
    for (const c of characters) {
      if (BUILTIN_CHAR_IDS.has(c.id)) continue
      try { addCharacter(c) } catch (e) { console.warn('skip char', c.id, e) }
    }
  } catch (e) {
    console.warn('character registry fetch failed', e)
  }
}

function setStatus(text) { statusEl.textContent = text }

// --- Scene ----------------------------------------------------------------
const renderer = new THREE.WebGLRenderer({ antialias: true })
renderer.setPixelRatio(window.devicePixelRatio)
renderer.setSize(window.innerWidth, window.innerHeight)
renderer.outputColorSpace = THREE.SRGBColorSpace
document.getElementById('app').appendChild(renderer.domElement)

const scene = new THREE.Scene()
scene.background = new THREE.Color(0x1a1a1a)

const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.1, 100)
camera.position.set(0, 1.4, 3.0)

const controls = new OrbitControls(camera, renderer.domElement)
controls.target.set(0, 1.0, 0)
controls.update()

scene.add(new THREE.HemisphereLight(0xffffff, 0x222233, 1.2))
const dir = new THREE.DirectionalLight(0xffffff, 1.5)
dir.position.set(3, 5, 2)
scene.add(dir)

const floor = new THREE.Mesh(
  new THREE.PlaneGeometry(20, 20),
  new THREE.MeshStandardMaterial({ color: 0x2a2a2a, roughness: 1.0 }),
)
floor.rotation.x = -Math.PI / 2
scene.add(floor)
scene.add(new THREE.GridHelper(10, 20, 0x333333, 0x333333))

window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight
  camera.updateProjectionMatrix()
  renderer.setSize(window.innerWidth, window.innerHeight)
})

// --- Avatar ---------------------------------------------------------------
let animator = null
let currentRoot = null  // the THREE.Group/Object3D for the loaded character
let currentMotion = null

// Mixamo animations (GLB with embedded animation tracks) are driven via
// three.js' AnimationMixer rather than the kimodo retargeter. Only one of
// {animator, mixamoMixer} should be playing at a time per character.
let mixamoMixer = null
let mixamoClock = null
let mixamoAnimations = []  // populated from /mixamo/animations on startup

const gltfLoader = new GLTFLoader()

function stopMixamoMixer() {
  if (mixamoMixer) {
    mixamoMixer.stopAllAction()
    mixamoMixer.uncacheRoot(currentRoot)
    mixamoMixer = null
    mixamoClock = null
  }
}

async function loadCharacter(charConfig) {
  setStatus(`Loading ${charConfig.label}…`)

  // Tear down any mixer or previous root before replacing.
  stopMixamoMixer()
  if (currentRoot) {
    scene.remove(currentRoot)
    currentRoot.traverse(o => {
      if (o.geometry) o.geometry.dispose()
      if (o.material) {
        if (Array.isArray(o.material)) o.material.forEach(m => m.dispose())
        else o.material.dispose()
      }
    })
    currentRoot = null
  }

  const gltf = await gltfLoader.loadAsync(charConfig.url)
  const root = gltf.scene

  root.scale.setScalar(charConfig.scale ?? 1.0)
  scene.add(root)

  let skinned = null
  root.traverse(o => {
    if (o.isSkinnedMesh && !skinned) skinned = o
    if (o.isSkinnedMesh) o.frustumCulled = false  // skinning + culling = glitches
  })

  // Anchor feet to Y=0 in scene, after scale + bones are realized.
  root.updateMatrixWorld(true)
  const restBox = new THREE.Box3().setFromObject(root)
  const groundOffsetY = -restBox.min.y
  root.position.y += groundOffsetY

  // Center the character at the scene origin (X=Z=0). The Blender Studio
  // GLBs have the pelvis baked at a non-zero world position (e.g. x=-5.18 for
  // female realistic), which would put the character off-screen at rest.
  // Re-center by adjusting the pelvis bone's local X/Z; the per-frame
  // animation loop will overwrite pelvis.position with kimodo's root_positions
  // once a clip plays, so this only affects the rest pose.
  let pelvisBone = null
  if (charConfig.mapping?.pelvis) {
    const norm = (n) => n.replace(/[.:]/g, '')
    const pelvisName = norm(charConfig.mapping.pelvis)
    root.traverse(o => {
      if (!pelvisBone && o.name && norm(o.name) === pelvisName) pelvisBone = o
    })
  }
  if (pelvisBone) {
    pelvisBone.position.x = 0
    pelvisBone.position.z = 0
    root.updateMatrixWorld(true)
  }

  // Stride scale: kimodo motion is in meters at ~SMPL-X height.
  const charHeight = restBox.max.y - restBox.min.y
  const strideScale = charHeight / SMPLX_HEIGHT

  // Drive a real SkinnedMesh if present; otherwise drive the parented Object3D
  // hierarchy directly (no skinning, rigid joints — fine for stylized base meshes).
  const target = (charConfig.skinned && skinned) ? skinned : root

  // Skinned → 'rest' (preserves rest-world quaternion, kimodo applies as delta).
  // Rigid → 'none' (rotations baked to identity in mesh data at build time,
  //                  so kimodo's world quat applies directly).
  const autoAlign = target.isSkinnedMesh ? 'rest' : 'none'
  alignEl.value = autoAlign

  animator = new Animator(target, {
    mapping: charConfig.mapping,
    blends: charConfig.blends || {},
    scale: strideScale,
    groundOffsetY,
    alignMode: autoAlign,
  })

  currentRoot = root
  setStatus(`Loaded ${charConfig.label}. ${currentMotion ? 'Replaying.' : 'Type a prompt and Generate.'}`)

  // Debug exposure for browser console.
  window.__root = root
  window.__scene = scene
  window.__camera = camera
  window.__controls = controls
  const allNames = []
  root.traverse(o => allNames.push(`${o.type}:${o.name || '(noname)'}`))
  console.log(`[loadCharacter] traversed ${allNames.length} objects in '${charConfig.label}':`, allNames)

  // Restore whatever was playing before the character change.
  if (currentMotion && currentMotion._kind === 'mixamo') {
    playMixamoAnimation(currentMotion.config).catch(e => {
      console.error(e)
      setStatus(`Mixamo replay failed: ${e.message}`)
    })
  } else if (currentMotion) {
    animator.setMotion(currentMotion, { loop: true })
  }
}

// --- API ------------------------------------------------------------------
async function generate(prompt, seconds = 5) {
  const r = await fetch(`${KIMODO_URL}/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt, seconds }),
  })
  if (!r.ok) throw new Error(`API ${r.status}: ${await r.text()}`)
  return r.json()
}

async function listAnimations() {
  const r = await fetch(`${KIMODO_URL}/animations`)
  if (!r.ok) throw new Error(`API ${r.status}: ${await r.text()}`)
  const { animations } = await r.json()
  return animations
}

async function fetchAnimation(id) {
  const r = await fetch(`${KIMODO_URL}/animations/${encodeURIComponent(id)}`)
  if (!r.ok) throw new Error(`API ${r.status}: ${await r.text()}`)
  return r.json()
}

async function deleteAnimation(id) {
  const r = await fetch(`${KIMODO_URL}/animations/${encodeURIComponent(id)}`, { method: 'DELETE' })
  if (!r.ok) throw new Error(`API ${r.status}: ${await r.text()}`)
}

async function fetchMixamoAnimations() {
  try {
    const r = await fetch(`${KIMODO_URL}/mixamo/animations`)
    if (!r.ok) throw new Error(`API ${r.status}`)
    const { animations } = await r.json()
    mixamoAnimations = animations
  } catch (e) {
    console.warn('mixamo animations fetch failed', e)
    mixamoAnimations = []
  }
}

async function loadMixamoClip(animUrl) {
  const animGltf = await gltfLoader.loadAsync(animUrl)
  // Mixamo GLBs ship with an empty "Take 001" alongside the real "mixamo.com"
  // clip — pick the one with actual tracks.
  const clip =
    animGltf.animations.find(c => c.tracks && c.tracks.length > 0)
    || animGltf.animations[0]
  if (!clip) throw new Error('animation GLB has no clips')
  return clip
}

async function playMixamoAnimation(animConfig) {
  if (!currentRoot) throw new Error('no character loaded')
  // Stop kimodo retargeter and any previous mixer; one animation source at a time.
  if (animator) animator.playing = false
  stopMixamoMixer()

  const clip = await loadMixamoClip(animConfig.url)
  mixamoMixer = new THREE.AnimationMixer(currentRoot)
  const action = mixamoMixer.clipAction(clip)
  action.setLoop(THREE.LoopRepeat, Infinity)
  action.play()
  mixamoClock = new THREE.Clock()
  currentMotion = { _kind: 'mixamo', config: animConfig, clip }
}

function fmtAnim(a) {
  const dur = a.seconds ? `${a.seconds.toFixed(1)}s` : ''
  const title = (a.prompt || '(unnamed)').slice(0, 60)
  return `${title}${dur ? ` — ${dur}` : ''}`
}

async function refreshSaved(selectId) {
  let kimodoAnims = []
  try { kimodoAnims = await listAnimations() }
  catch (e) { console.warn('list failed', e) }

  // Pull Mixamo registry too so both sources show up in one place.
  await fetchMixamoAnimations()

  savedEl.innerHTML = ''
  const total = kimodoAnims.length + mixamoAnimations.length
  const placeholder = document.createElement('option')
  placeholder.value = ''
  placeholder.textContent = total
    ? `— ${total} animations (${kimodoAnims.length} kimodo / ${mixamoAnimations.length} mixamo) —`
    : '— no saved animations —'
  savedEl.appendChild(placeholder)

  if (kimodoAnims.length) {
    const kg = document.createElement('optgroup')
    kg.label = 'Kimodo (text-to-motion)'
    for (const a of kimodoAnims) {
      const opt = document.createElement('option')
      opt.value = a.id
      opt.textContent = fmtAnim(a)
      kg.appendChild(opt)
    }
    savedEl.appendChild(kg)
  }
  if (mixamoAnimations.length) {
    const mg = document.createElement('optgroup')
    mg.label = 'Mixamo'
    for (const a of mixamoAnimations) {
      const opt = document.createElement('option')
      opt.value = a.id
      opt.textContent = a.label
      mg.appendChild(opt)
    }
    savedEl.appendChild(mg)
  }
  if (selectId) savedEl.value = selectId
}

// --- UI wiring ------------------------------------------------------------
btnEl.addEventListener('click', async () => {
  const prompt = promptEl.value.trim()
  if (!prompt || !animator) return
  const seconds = parseFloat(secondsEl.value)
  btnEl.disabled = true
  setStatus(`Generating ${seconds.toFixed(1)}s…`)
  try {
    const motion = await generate(prompt, seconds)
    stopMixamoMixer()
    if (animator) animator.playing = true
    currentMotion = motion
    animator.setMotion(motion, { loop: true })
    setStatus(`Playing (${motion.num_frames} frames @ ${motion.fps} fps).`)
    await refreshSaved(motion.id)
  } catch (e) {
    console.error(e)
    setStatus(`Error: ${e.message}`)
  } finally {
    btnEl.disabled = false
  }
})

savedEl.addEventListener('change', async () => {
  const id = savedEl.value
  if (!id) return
  setStatus(`Loading saved…`)
  try {
    // Mixamo animation? (id starts with 'mixamo_anim_').
    if (id.startsWith('mixamo_anim_')) {
      const animConfig = mixamoAnimations.find(a => a.id === id)
      if (!animConfig) throw new Error(`Mixamo animation ${id} not found`)
      await playMixamoAnimation(animConfig)
      setStatus(`Playing Mixamo: ${animConfig.label}`)
      return
    }
    if (!animator) return
    const motion = await fetchAnimation(id)
    stopMixamoMixer()
    if (animator) animator.playing = true
    promptEl.value = motion.prompt || promptEl.value
    if (motion.seconds) {
      secondsEl.value = motion.seconds
      secondsLabelEl.textContent = `${motion.seconds.toFixed(1)} s`
    }
    currentMotion = motion
    animator.setMotion(motion, { loop: true })
    setStatus(`Playing saved (${motion.num_frames} frames @ ${motion.fps} fps).`)
  } catch (e) {
    console.error(e)
    setStatus(`Error: ${e.message}`)
  }
})

deleteEl.addEventListener('click', async () => {
  const id = savedEl.value
  if (!id) return
  if (!confirm('Delete this animation?')) return
  try {
    await deleteAnimation(id)
    await refreshSaved()
    setStatus('Deleted.')
  } catch (e) {
    console.error(e)
    setStatus(`Error: ${e.message}`)
  }
})

const charDeleteEl = document.getElementById('char-delete')

function refreshCharDeleteState() {
  charDeleteEl.disabled = BUILTIN_CHAR_IDS.has(characterEl.value)
}

characterEl.addEventListener('change', async () => {
  refreshCharDeleteState()
  const charConfig = getCharacter(characterEl.value)
  try {
    await loadCharacter(charConfig)
  } catch (e) {
    console.error(e)
    setStatus(`Load error: ${e.message}`)
  }
})

charDeleteEl.addEventListener('click', async () => {
  const id = characterEl.value
  if (BUILTIN_CHAR_IDS.has(id)) return
  const idx = CHARACTERS.findIndex(c => c.id === id)
  if (idx < 0) return
  if (!confirm(`Remove "${CHARACTERS[idx].label}" from your character list?`)) return
  try {
    const r = await fetch(`${KIMODO_URL}/characters/${encodeURIComponent(id)}`, { method: 'DELETE' })
    if (!r.ok) throw new Error(`API ${r.status}: ${await r.text()}`)
  } catch (e) {
    console.error(e)
    setStatus(`Delete failed: ${e.message}`)
    return
  }
  CHARACTERS.splice(idx, 1)
  // Rebuild the dropdown options.
  characterEl.innerHTML = ''
  for (const c of CHARACTERS) appendCharacterOption(c)
  characterEl.value = CHARACTERS[0].id
  refreshCharDeleteState()
  loadCharacter(CHARACTERS[0]).catch(e => setStatus(`Load error: ${e.message}`))
})

alignEl.addEventListener('change', () => {
  if (animator) {
    animator.setAlignMode(alignEl.value)
    setStatus(`Alignment: ${alignEl.value}`)
  }
})

// --- Searchable pickers (character + animation) ---------------------------
// Each picker is a button that opens a popover with a search box, source
// filter, and a virtualized-ish list. Selecting an item drives the
// hidden <select> that the rest of the app already listens on, so all
// existing change-handlers keep working unchanged.
function makePicker({ rootId, btnId, popId, searchId, filterId, listId,
                      hostSelect, getItems, formatItem, onSelect }) {
  const root = document.getElementById(rootId)
  const btn = document.getElementById(btnId)
  const pop = document.getElementById(popId)
  const search = document.getElementById(searchId)
  const filterRoot = document.getElementById(filterId)
  const listEl = document.getElementById(listId)
  let activeSource = 'all'
  let isOpen = false

  function open() {
    if (isOpen) return
    pop.classList.add('open')
    isOpen = true
    search.value = ''
    render()
    setTimeout(() => search.focus(), 0)
  }
  function close() {
    pop.classList.remove('open')
    isOpen = false
  }
  function toggle() { isOpen ? close() : open() }

  function render() {
    const q = search.value.trim().toLowerCase()
    const items = getItems().filter(it => {
      if (activeSource !== 'all' && it.source !== activeSource) return false
      if (!q) return true
      return it.label.toLowerCase().includes(q)
    })
    listEl.innerHTML = ''
    if (!items.length) {
      const e = document.createElement('div')
      e.className = 'empty'
      e.textContent = 'No matches.'
      listEl.appendChild(e)
      return
    }
    const currentValue = hostSelect.value
    for (const it of items) {
      const row = document.createElement('div')
      row.className = 'item' + (it.id === currentValue ? ' selected' : '')
      const label = document.createElement('span')
      label.textContent = formatItem ? formatItem(it) : it.label
      row.appendChild(label)
      if (it.source && it.source !== 'all') {
        const badge = document.createElement('span')
        badge.className = `badge ${it.source}`
        badge.textContent = it.source
        row.appendChild(badge)
      }
      row.addEventListener('click', () => {
        hostSelect.value = it.id
        hostSelect.dispatchEvent(new Event('change'))
        btn.textContent = it.label
        if (onSelect) onSelect(it)
        close()
      })
      listEl.appendChild(row)
    }
  }

  btn.addEventListener('click', toggle)
  search.addEventListener('input', render)
  for (const fbtn of filterRoot.querySelectorAll('button')) {
    fbtn.addEventListener('click', () => {
      activeSource = fbtn.dataset.source
      for (const x of filterRoot.querySelectorAll('button')) x.classList.toggle('active', x === fbtn)
      render()
    })
  }
  // Close when clicking outside.
  document.addEventListener('click', (e) => {
    if (!isOpen) return
    if (root.contains(e.target)) return
    close()
  })

  return {
    refresh: render,
    setLabel: (text) => { btn.textContent = text },
    close,
  }
}

// --- Mixamo import --------------------------------------------------------
const mixamoBtn = document.getElementById('mixamo-btn')
const mixamoPanel = document.getElementById('mixamo-panel')
const mixamoSearchEl = document.getElementById('mixamo-search')
const mixamoResultsEl = document.getElementById('mixamo-results')
const mixamoStatusEl = document.getElementById('mixamo-status')

mixamoBtn.addEventListener('click', () => {
  mixamoPanel.classList.toggle('open')
  if (mixamoPanel.classList.contains('open')) mixamoSearchEl.focus()
})

let searchSeq = 0
let searchDebounce = 0
mixamoSearchEl.addEventListener('input', () => {
  clearTimeout(searchDebounce)
  searchDebounce = setTimeout(runSearch, 300)
})

async function runSearch() {
  const q = mixamoSearchEl.value.trim()
  if (!q) {
    mixamoResultsEl.innerHTML = ''
    mixamoStatusEl.textContent = ''
    return
  }
  const seq = ++searchSeq
  mixamoStatusEl.textContent = `Searching “${q}”…`
  try {
    const r = await fetch(`${KIMODO_URL}/mixamo/search?q=${encodeURIComponent(q)}&limit=24`)
    if (!r.ok) throw new Error(`API ${r.status}: ${await r.text()}`)
    const { results } = await r.json()
    if (seq !== searchSeq) return  // a newer query superseded us
    renderResults(results)
    mixamoStatusEl.textContent = `${results.length} result${results.length === 1 ? '' : 's'}`
  } catch (e) {
    if (seq !== searchSeq) return
    console.error(e)
    mixamoStatusEl.textContent = `Error: ${e.message}`
  }
}

function renderResults(results) {
  mixamoResultsEl.innerHTML = ''
  for (const r of results) {
    const card = document.createElement('div')
    card.className = 'mx-card'
    card.title = `Click to import ${r.name}`
    const img = document.createElement('img')
    if (r.thumbnail) img.src = r.thumbnail
    img.alt = r.name
    img.loading = 'lazy'
    const name = document.createElement('div')
    name.className = 'name'
    name.textContent = r.name
    card.append(img, name)
    card.addEventListener('click', () => importCharacter(r, card))
    mixamoResultsEl.appendChild(card)
  }
}

async function importCharacter(result, card) {
  if (card.classList.contains('busy')) return
  card.classList.add('busy')
  mixamoStatusEl.textContent = `Importing ${result.name}… (Mixamo packaging can take 10–30s)`
  try {
    const r = await fetch(`${KIMODO_URL}/mixamo/import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: result.id, name: result.name }),
    })
    if (!r.ok) throw new Error(`API ${r.status}: ${await r.text()}`)
    const charConfig = await r.json()
    const added = addCharacter(charConfig)
    characterEl.value = added.id
    refreshCharDeleteState()
    await loadCharacter(added)
    mixamoStatusEl.textContent = `Imported ${result.name}.`
  } catch (e) {
    console.error(e)
    mixamoStatusEl.textContent = `Error: ${e.message}`
  } finally {
    card.classList.remove('busy')
  }
}

// --- Loop -----------------------------------------------------------------
function tick() {
  if (mixamoMixer && mixamoClock) {
    mixamoMixer.update(mixamoClock.getDelta())
  } else {
    animator?.update()
  }
  controls.update()
  renderer.render(scene, camera)
  requestAnimationFrame(tick)
}
tick()

// Wire up the two searchable pickers. Each one drives the hidden <select>
// (characterEl / savedEl) that the rest of the app already listens on.
const characterPicker = makePicker({
  rootId: 'character-picker',
  btnId: 'character-btn',
  popId: 'character-pop',
  searchId: 'character-search',
  filterId: 'character-filter',
  listId: 'character-list',
  hostSelect: characterEl,
  getItems: () => CHARACTERS.map(c => ({
    id: c.id,
    label: c.label,
    source: BUILTIN_CHAR_IDS.has(c.id) ? 'builtin' : 'mixamo',
  })),
})

const savedPicker = makePicker({
  rootId: 'saved-picker',
  btnId: 'saved-btn',
  popId: 'saved-pop',
  searchId: 'saved-search',
  filterId: 'saved-filter',
  listId: 'saved-list',
  hostSelect: savedEl,
  getItems: () => {
    // Mixamo animations only work on Mixamo characters (shared mixamorig:*
    // skeleton). Kimodo animations work on every character thanks to the
    // runtime retargeter. Hide the incompatible ones rather than play
    // them silently into bones that don't exist.
    const currentCharIsMixamo = (() => {
      const c = CHARACTERS.find(c => c.id === characterEl.value)
      // Both server-imported (mappingKind='mixamo') and built-in Mixamo
      // characters (rigs.js entries with mixamoMapping() resolved) qualify.
      // Use id prefix as the universal signal — every Mixamo character
      // registers under `mixamo_*`.
      return c && c.id.startsWith('mixamo_')
    })()
    const out = []
    for (const opt of savedEl.querySelectorAll('option')) {
      if (!opt.value) continue
      const isMixamo = opt.value.startsWith('mixamo_anim_')
      if (isMixamo && !currentCharIsMixamo) continue
      out.push({
        id: opt.value,
        label: opt.textContent,
        source: isMixamo ? 'mixamo' : 'kimodo',
      })
    }
    return out
  },
})

// Keep picker button labels in sync when the underlying selects change,
// and re-render the saved picker so Mixamo animations get hidden when
// the selected character isn't Mixamo.
characterEl.addEventListener('change', () => {
  const c = CHARACTERS.find(c => c.id === characterEl.value)
  if (c) characterPicker.setLabel(c.label)
  savedPicker.refresh()
})
savedEl.addEventListener('change', () => {
  const opt = savedEl.querySelector(`option[value="${savedEl.value}"]`)
  if (opt) savedPicker.setLabel(opt.textContent)
})

// Initial load: SMPL-X (first in list). Server-side imports get appended
// asynchronously and become available without forcing a page reload.
characterEl.value = CHARACTERS[0].id
characterPicker.setLabel(CHARACTERS[0].label)
refreshCharDeleteState()
loadCharacter(CHARACTERS[0]).catch(e => { console.error(e); setStatus(`Load error: ${e.message}`) })
refreshServerCharacters().catch(() => {})
refreshSaved().catch(() => {})
