import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { Animator } from './animator.js'

const KIMODO_URL = import.meta.env.VITE_KIMODO_URL || 'http://localhost:7862'
const MODEL_URL = '/models/smplx_neutral.glb'

const statusEl = document.getElementById('status')
const promptEl = document.getElementById('prompt')
const btnEl = document.getElementById('generate')
const secondsEl = document.getElementById('seconds')
const secondsLabelEl = document.getElementById('seconds-label')
const savedEl = document.getElementById('saved')
const deleteEl = document.getElementById('delete')

secondsEl.addEventListener('input', () => {
  secondsLabelEl.textContent = `${parseFloat(secondsEl.value).toFixed(1)} s`
})

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

// Light + floor
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

async function loadAvatar() {
  setStatus('Loading avatar…')
  const gltf = await new GLTFLoader().loadAsync(MODEL_URL)
  scene.add(gltf.scene)
  let skinned = null
  gltf.scene.traverse(o => { if (o.isSkinnedMesh) skinned = o })
  if (!skinned) throw new Error('No SkinnedMesh in GLB')
  // Helps with skinned-mesh frustum culling glitches.
  skinned.frustumCulled = false

  // Anchor feet to floor (Y=0). SMPL-X v_template is centered on the pelvis so
  // the rest mesh extends below Y=0; lift the whole rig by -minY.
  gltf.scene.updateMatrixWorld(true)
  const restBox = new THREE.Box3().setFromObject(skinned)
  const groundOffsetY = -restBox.min.y
  gltf.scene.position.y = groundOffsetY

  animator = new Animator(skinned, { groundOffsetY })
  setStatus('Ready. Type a prompt and Generate.')
}

// --- Generate -------------------------------------------------------------
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

btnEl.addEventListener('click', async () => {
  const prompt = promptEl.value.trim()
  if (!prompt || !animator) return
  const seconds = parseFloat(secondsEl.value)
  btnEl.disabled = true
  setStatus(`Generating ${seconds.toFixed(1)}s…`)
  try {
    const motion = await generate(prompt, seconds)
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

// --- Loop -----------------------------------------------------------------
function tick() {
  animator?.update()
  controls.update()
  renderer.render(scene, camera)
  requestAnimationFrame(tick)
}
tick()

loadAvatar().catch(e => { console.error(e); setStatus(`Load error: ${e.message}`) })
refreshSaved().catch(() => {})
