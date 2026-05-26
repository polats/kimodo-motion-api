// Small in-node 3D preview of a kimodo motion, using kimodo's own Animator +
// rigs + GLB assets (fully standalone). `frame` (a number) freezes on that
// frame for scrubbing; undefined = looped playback.
//
// Plays the clip's REAL root translation (no anchoring). The camera is STATIC by
// default so a forward walk reads truthfully — the body travels across the frame
// and planted feet stay put. (A camera locked on the centred character makes a
// world-stationary foot appear to slide backward — the same moonwalk illusion as
// anchoring the pelvis. Hence "lock" is opt-in, not the default.)
//   • ground trail  — green polyline of the pelvis path (where it went)
//   • start marker   — orange dot at the path origin (where it began)
//   • ⌖ recenter     — reset the camera to the default framing
//   • 🎥/🔒 lock     — toggle follow-the-character (treadmill view)
import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { Animator } from '../animator.js'
import { CHARACTERS } from '../rigs.js'

const SMPLX_HEIGHT = 1.7
const CHAR = CHARACTERS.find(c => c.id === 'male_stylized_skinned')
  || CHARACTERS.find(c => c.id === 'smplx') || CHARACTERS[0]
const HOME_POS = new THREE.Vector3(1.9, 1.4, 3.0)
const HOME_TGT = new THREE.Vector3(0, 0.9, 0)

export default function NodePreview({ motion, frame }) {
  const mountRef = useRef(null)
  const animatorRef = useRef(null)
  const camRef = useRef(null)
  const controlsRef = useRef(null)
  const resetRef = useRef(true)       // re-frame + clear trail on (re)load / scrub / loop
  const followRef = useRef(false)     // false = static (truthful); true = lock on character
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState(null)
  const [follow, setFollow] = useState(false)

  useEffect(() => {
    const mount = mountRef.current
    if (!mount) return
    let disposed = false

    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    mount.appendChild(renderer.domElement)
    Object.assign(renderer.domElement.style, { width: '100%', height: '100%', display: 'block' })

    const scene = new THREE.Scene(); scene.background = new THREE.Color(0x1a1d22)
    scene.add(new THREE.HemisphereLight(0xffffff, 0x222233, 1.4))
    const dl = new THREE.DirectionalLight(0xffffff, 1.4); dl.position.set(2, 4, 3); scene.add(dl)
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 80); camera.position.copy(HOME_POS)
    camRef.current = camera
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true; controls.target.copy(HOME_TGT)
    controlsRef.current = controls
    // Large STATIC ground so the character can travel without running off it.
    scene.add(new THREE.GridHelper(60, 120, 0x3a3f4b, 0x24282f))

    // Ground trail of the pelvis path (world space) + start marker.
    const TRAIL_MAX = 400
    const trailArr = new Float32Array(TRAIL_MAX * 3)
    const trailGeo = new THREE.BufferGeometry()
    trailGeo.setAttribute('position', new THREE.BufferAttribute(trailArr, 3))
    trailGeo.setDrawRange(0, 0)
    const trail = new THREE.Line(trailGeo, new THREE.LineBasicMaterial({ color: 0x6fb98c }))
    trail.frustumCulled = false
    scene.add(trail)
    const startMarker = new THREE.Mesh(
      new THREE.SphereGeometry(0.05, 12, 12),
      new THREE.MeshBasicMaterial({ color: 0xffaa44 }))
    startMarker.visible = false
    scene.add(startMarker)

    const resize = () => {
      const w = mount.clientWidth || 220, h = mount.clientHeight || 180
      renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix()
    }
    resize(); const ro = new ResizeObserver(resize); ro.observe(mount)

    const tmp = new THREE.Vector3()
    let raf = 0, lastFrame = -1, camPrev = { x: 0, z: 0 }, trailN = 0
    const tick = () => {
      if (disposed) return
      raf = requestAnimationFrame(tick)
      const a = animatorRef.current
      a?.update()
      if (a?.pelvisBone) {
        a.pelvisBone.getWorldPosition(tmp)
        const looped = typeof a.frame === 'number' && a.frame < lastFrame
        if (resetRef.current || looped) {   // new clip / scrub / loop → clear trail, drop marker
          camPrev = { x: tmp.x, z: tmp.z }; trailN = 0; trailGeo.setDrawRange(0, 0)
          startMarker.position.set(tmp.x, 0.02, tmp.z); startMarker.visible = true
          resetRef.current = false
        }
        if (followRef.current) {   // opt-in: lock camera on the character (treadmill view)
          const dx = tmp.x - camPrev.x, dz = tmp.z - camPrev.z
          controls.target.x += dx; controls.target.z += dz
          camera.position.x += dx; camera.position.z += dz
        }
        camPrev = { x: tmp.x, z: tmp.z }
        lastFrame = a.frame
        if (trailN < TRAIL_MAX) {
          trailArr[trailN * 3] = tmp.x; trailArr[trailN * 3 + 1] = 0.02; trailArr[trailN * 3 + 2] = tmp.z
          trailN++; trailGeo.setDrawRange(0, trailN); trailGeo.attributes.position.needsUpdate = true
        }
      }
      controls.update()
      renderer.render(scene, camera)
    }
    raf = requestAnimationFrame(tick)

    new GLTFLoader().load(CHAR.url, (gltf) => {
      if (disposed) return
      const root = gltf.scene; root.scale.setScalar(CHAR.scale ?? 1.0); scene.add(root)
      let skinned = null
      root.traverse(o => { if (o.isSkinnedMesh) { if (!skinned) skinned = o; o.frustumCulled = false } })
      root.updateMatrixWorld(true)
      const box = new THREE.Box3().setFromObject(root)
      const groundOffsetY = -box.min.y; root.position.y += groundOffsetY
      const strideScale = (box.max.y - box.min.y) / SMPLX_HEIGHT
      const target = (CHAR.skinned && skinned) ? skinned : root
      animatorRef.current = new Animator(target, {
        mapping: CHAR.mapping, blends: CHAR.blends || {}, scale: strideScale, groundOffsetY,
        alignMode: target.isSkinnedMesh ? 'rest' : 'none',
      })
      setLoaded(true)
    }, undefined, (e) => setError(e?.message || 'model load failed'))

    return () => {
      disposed = true; cancelAnimationFrame(raf); ro.disconnect(); controls.dispose()
      scene.traverse(o => {
        if (o.geometry) o.geometry.dispose?.()
        if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => m.dispose?.())
      })
      renderer.dispose()
      if (renderer.domElement.parentNode === mount) mount.removeChild(renderer.domElement)
      animatorRef.current = null; camRef.current = null; controlsRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!loaded || !motion || !animatorRef.current) return
    animatorRef.current.setMotion(motion, { loop: true })
    resetRef.current = true
  }, [loaded, motion])

  useEffect(() => {
    if (!loaded || !motion || !animatorRef.current) return
    const a = animatorRef.current
    if (typeof frame === 'number') { a.elapsed = frame / (motion.fps || 30); a.frame = -1; a.playing = true; a.update(); a.playing = false }
    else { a.playing = true; a.lastTime = performance.now() / 1000 }
    resetRef.current = true
  }, [loaded, motion, frame])

  const recenter = () => {
    const cam = camRef.current, c = controlsRef.current
    if (!cam || !c) return
    cam.position.copy(HOME_POS); c.target.copy(HOME_TGT); c.update()
  }
  const toggleFollow = () => { const v = !followRef.current; followRef.current = v; setFollow(v) }

  const btn = { width: 24, height: 22, padding: 0, borderRadius: 5, border: '1px solid #4a4a52', background: '#1c1c22cc', color: '#cdd', cursor: 'pointer', fontSize: 12, lineHeight: '20px' }

  return (
    <div ref={mountRef} style={{ width: '100%', height: '100%', position: 'relative' }}>
      {loaded && (
        <div className="nodrag" style={{ position: 'absolute', top: 6, right: 6, display: 'flex', gap: 4, zIndex: 5 }}>
          <button className="nodrag" onClick={recenter} title="recenter — reset the camera to the default framing" style={btn}>⌖</button>
          <button className="nodrag" onClick={toggleFollow}
            title={follow ? 'camera locked on character — click to free it (locked view makes a forward walk look like a treadmill)' : 'lock the camera on the character (treadmill view)'}
            style={{ ...btn, background: follow ? '#2d4a35cc' : '#1c1c22cc', color: follow ? '#7ec77e' : '#cdd', borderColor: follow ? '#3a6' : '#4a4a52' }}>
            {follow ? '🔒' : '🎥'}
          </button>
        </div>
      )}
      {!loaded && !error && <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', color: '#888', fontSize: 11 }}>loading…</div>}
      {error && <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', color: '#c66', fontSize: 11 }}>{error}</div>}
    </div>
  )
}
