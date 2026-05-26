// Direct client for the kimodo motion API (CORS open).
const KIMODO_URL = import.meta.env.VITE_KIMODO_URL || 'http://localhost:7862'

async function post(path, body) {
  const r = await fetch(KIMODO_URL + path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${path} ${r.status}: ${(await r.text()).slice(0, 200)}`)
  return r.json()
}

export function genRoot({ prompt, seconds, endOnPeak }) {
  const b = { prompt, seconds: Number(seconds) || 2.5 }
  if (endOnPeak) b.end_on_peak = endOnPeak
  return post('/generate', b)
}

export function genContinue({ sourceId, sourceFrame, prompt, seconds, endOnPeak }) {
  const b = {
    source_id: sourceId, prompt, seconds: Number(seconds) || 2.5,
    source_frame: Number.isFinite(sourceFrame) ? sourceFrame : -1, stitch: false,
  }
  if (endOnPeak) b.end_on_peak = endOnPeak
  return post('/generate_continue', b)
}

export function stitchPath(ids) { return post('/stitch_path', { ids, save: false }) }

// Original kata flow: ONE multi_prompt generation sliced into per-move nodes,
// chained via continues_from. Returns { nodes: [id...] }.
export function genSequence({ prompts, seconds }) {
  return post('/generate_sequence', { prompts, seconds: Number(seconds) || 2.5, save_segments: true })
}

export async function fetchAnim(id) {
  const r = await fetch(`${KIMODO_URL}/animations/${encodeURIComponent(id)}`)
  if (!r.ok) throw new Error(`fetch ${id} ${r.status}`)
  return r.json()
}

export const KIMODO_BASE = KIMODO_URL
