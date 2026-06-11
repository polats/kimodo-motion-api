// Texture generation via the deployed tiny-army FLUX.2-klein ZeroGPU space
// (polats/tiny-army-klein-zerogpu). Calls go through vite's /hfspace proxy
// (see vite.config.js), which injects the HF token server-side and keeps the
// whole exchange same-origin — so no CORS, no token in the bundle, and the
// resulting image loads into a WebGL texture without cross-origin taint.
//
// The space exposes a Gradio endpoint: /generate(Prompt: str, Seed: int) → image.
// Gradio's HTTP protocol is two steps: POST returns an event_id, then GET on
// that id streams server-sent events until a final `complete` event carries the
// result FileData (with a .url).

const BASE = '/hfspace'

// Parse a Gradio SSE body for the final result. The stream looks like:
//   event: heartbeat\n data: null\n\n  ... event: complete\n data: [ {...} ]\n\n
function parseSSE(text) {
  const blocks = text.split('\n\n')
  let lastComplete = null
  let lastError = null
  for (const block of blocks) {
    const lines = block.split('\n')
    let event = 'message'
    let data = ''
    for (const line of lines) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) data += line.slice(5).trim()
    }
    if (event === 'complete' && data) lastComplete = data
    if (event === 'error' && data) lastError = data
  }
  if (lastError) throw new Error(`space error: ${lastError}`)
  if (!lastComplete) throw new Error('no result in stream')
  return JSON.parse(lastComplete)
}

// Rewrite an absolute space URL (https://<space>.hf.space/gradio_api/file=...)
// to the same-origin proxy path so the browser fetches it through vite.
function toProxyUrl(url) {
  if (!url) return url
  const m = url.match(/^https?:\/\/[^/]+(\/.*)$/)
  return m ? `${BASE}${m[1]}` : url
}

// Generate a texture image and return a same-origin URL ready for THREE.TextureLoader.
// opts: { seed = 42, signal }. Throws on failure/timeout.
export async function generateSkinTexture(prompt, opts = {}) {
  const seed = Number.isFinite(opts.seed) ? opts.seed : 42
  const signal = opts.signal

  const post = await fetch(`${BASE}/gradio_api/call/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ data: [prompt, seed] }),
    signal,
  })
  if (!post.ok) throw new Error(`generate POST ${post.status}: ${await post.text()}`)
  const { event_id: eventId } = await post.json()
  if (!eventId) throw new Error('no event_id from space')

  // ZeroGPU cold start + queue can take a while; the GET holds open until done.
  const res = await fetch(`${BASE}/gradio_api/call/generate/${eventId}`, { signal })
  if (!res.ok) throw new Error(`generate GET ${res.status}: ${await res.text()}`)
  const data = parseSSE(await res.text())

  const file = Array.isArray(data) ? data[0] : data
  const url = file && (file.url || file.path)
  if (!url) throw new Error('result had no image url')
  return toProxyUrl(url)
}
