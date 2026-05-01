import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { Animator } from './animator.js'
import { CHARACTERS, getCharacter } from './rigs.js'

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

for (const c of CHARACTERS) {
  const opt = document.createElement('option')
  opt.value = c.id
  opt.textContent = c.label
  characterEl.appendChild(opt)
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

const gltfLoader = new GLTFLoader()

async function loadCharacter(charConfig) {
  setStatus(`Loading ${charConfig.label}…`)

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
    const pelvisName = charConfig.mapping.pelvis.replace(/\./g, '')
    root.traverse(o => {
      if (!pelvisBone && o.name && o.name.replace(/\./g, '') === pelvisName) pelvisBone = o
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
  animator = new Animator(target, {
    mapping: charConfig.mapping,
    scale: strideScale,
    groundOffsetY,
    alignMode: alignEl.value,
  })

  currentRoot = root
  setStatus(`Loaded ${charConfig.label}. ${currentMotion ? 'Replaying.' : 'Type a prompt and Generate.'}`)

  // Debug exposure for browser console.
  window.__root = root
  window.__scene = scene
  const allNames = []
  root.traverse(o => allNames.push(`${o.type}:${o.name || '(noname)'}`))
  console.log(`[loadCharacter] traversed ${allNames.length} objects in '${charConfig.label}':`, allNames)

  if (currentMotion) animator.setMotion(currentMotion, { loop: true })
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

function fmtAnim(a) {
  const dur = a.seconds ? `${a.seconds.toFixed(1)}s` : ''
  const title = (a.prompt || '(unnamed)').slice(0, 60)
  return `${title}${dur ? ` — ${dur}` : ''}`
}

async function refreshSaved(selectId) {
  let animations = []
  try { animations = await listAnimations() }
  catch (e) { console.warn('list failed', e); return }
  savedEl.innerHTML = ''
  const placeholder = document.createElement('option')
  placeholder.value = ''
  placeholder.textContent = animations.length
    ? `— ${animations.length} saved —`
    : '— no saved animations —'
  savedEl.appendChild(placeholder)
  for (const a of animations) {
    const opt = document.createElement('option')
    opt.value = a.id
    opt.textContent = fmtAnim(a)
    savedEl.appendChild(opt)
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
  if (!id || !animator) return
  setStatus(`Loading saved…`)
  try {
    const motion = await fetchAnimation(id)
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

characterEl.addEventListener('change', async () => {
  const charConfig = getCharacter(characterEl.value)
  try {
    await loadCharacter(charConfig)
  } catch (e) {
    console.error(e)
    setStatus(`Load error: ${e.message}`)
  }
})

alignEl.addEventListener('change', () => {
  if (animator) {
    animator.setAlignMode(alignEl.value)
    setStatus(`Alignment: ${alignEl.value}`)
  }
})

// --- Loop -----------------------------------------------------------------
function tick() {
  animator?.update()
  controls.update()
  renderer.render(scene, camera)
  requestAnimationFrame(tick)
}
tick()

// Initial load: SMPL-X (first in list).
characterEl.value = CHARACTERS[0].id
loadCharacter(CHARACTERS[0]).catch(e => { console.error(e); setStatus(`Load error: ${e.message}`) })
refreshSaved().catch(() => {})
