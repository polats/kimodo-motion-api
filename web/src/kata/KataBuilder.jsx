// Standalone kata builder: a React Flow canvas of move nodes. Each node generates
// its motion via the kimodo API; "+ branch @ fN" spawns a child pre-wired to
// continue from the scrubbed frame. Edges = continuations.
import { useCallback, useEffect, useMemo, useRef } from 'react'
import ReactFlow, { Background, Controls, useNodesState, useEdgesState, addEdge, ReactFlowProvider } from 'reactflow'
import 'reactflow/dist/style.css'
import MoveNode from './MoveNode.jsx'
import { KataCtx } from './ctx.js'
import { genRoot, genContinue, fetchAnim, genSequence } from './kimodoClient.js'

const nodeTypes = { move: MoveNode }
let _idc = 1
const nid = () => `m${Date.now().toString(36)}_${_idc++}`

// Persist the graph to localStorage WITHOUT the heavy clip JSON (just clipId);
// clips are re-fetched from the server on load (they persist there).
const LS_KEY = 'kata-builder-v1'
function serialize(nodes, edges) {
  return JSON.stringify({
    nodes: nodes.map(n => ({
      id: n.id, type: n.type, position: n.position,
      data: {
        title: n.data.title, prompt: n.data.prompt, seconds: n.data.seconds,
        endOnPeak: n.data.endOnPeak, parentId: n.data.parentId, parentFrame: n.data.parentFrame,
        clipId: n.data.clip?.id,
      },
    })),
    edges,
  })
}
function loadPersisted() {
  try {
    const g = JSON.parse(localStorage.getItem(LS_KEY) || 'null')
    if (!g) return null
    return { nodes: (g.nodes || []).map(n => ({ ...n, data: { ...n.data, clip: null } })), edges: g.edges || [] }
  } catch { return null }
}

function Flow() {
  const persisted = useMemo(() => loadPersisted(), [])
  const [nodes, setNodes, onNodesChange] = useNodesState(persisted?.nodes || [])
  const [edges, setEdges, onEdgesChange] = useEdgesState(persisted?.edges || [])
  const nodesRef = useRef(nodes); nodesRef.current = nodes

  const update = useCallback((id, patch) =>
    setNodes(ns => ns.map(n => (n.id === id ? { ...n, data: { ...n.data, ...patch } } : n))), [setNodes])

  const generate = useCallback(async (id) => {
    const node = nodesRef.current.find(n => n.id === id)
    if (!node || !node.data.prompt?.trim()) return
    update(id, { busy: true })
    try {
      let clip
      if (node.data.parentId) {
        const parent = nodesRef.current.find(n => n.id === node.data.parentId)
        if (!parent?.data.clip) throw new Error('parent not generated yet')
        clip = await genContinue({ sourceId: parent.data.clip.id, sourceFrame: node.data.parentFrame, prompt: node.data.prompt, seconds: node.data.seconds, endOnPeak: node.data.endOnPeak })
      } else {
        clip = await genRoot({ prompt: node.data.prompt, seconds: node.data.seconds, endOnPeak: node.data.endOnPeak })
      }
      update(id, { clip, busy: false, scrub: undefined, title: (node.data.prompt || 'move').slice(0, 26) })
    } catch (e) { update(id, { busy: false }); alert('generate failed: ' + e.message) }
  }, [update])

  const branch = useCallback((id) => {
    const node = nodesRef.current.find(n => n.id === id)
    if (!node?.data.clip) return
    const frame = Number.isFinite(node.data.scrub) ? node.data.scrub : (node.data.clip.num_frames - 1)
    const childId = nid()
    setNodes(ns => [...ns, {
      id: childId, type: 'move',
      position: { x: node.position.x + 40, y: node.position.y + 360 },
      data: { title: 'new move', prompt: '', seconds: 2.5, clip: null, parentId: id, parentFrame: frame },
    }])
    setEdges(es => addEdge({ id: `e_${id}_${childId}`, source: id, target: childId, label: `f${frame}`, style: { stroke: '#6fb98c' }, labelStyle: { fill: '#9fd' } }, es))
  }, [setNodes, setEdges])

  // Original kata flow: generate ONE multi_prompt sequence, then lay the sliced
  // segments out as a connected chain (continues_from → edges). Lets us compare
  // the builder's per-node continuation against the path that flowed cleanly.
  const importSequence = useCallback(async (prompts, seconds) => {
    const res = await genSequence({ prompts, seconds })
    const ids = res.nodes || []
    if (!ids.length) throw new Error('no segments returned')
    const clips = await Promise.all(ids.map(fetchAnim))
    const baseY = 60
    const newNodes = clips.map((clip, i) => ({
      id: clip.id, type: 'move',
      position: { x: 220, y: baseY + i * 540 },
      data: {
        title: (clip.prompt || 'move').slice(0, 26), prompt: clip.prompt || '',
        seconds: Math.max(0.1, Math.round((clip.num_frames / 30) * 10) / 10), clip,
        parentId: clip.continues_from?.source_id, parentFrame: clip.continues_from?.frame,
      },
    }))
    const newEdges = clips.filter(c => c.continues_from?.source_id).map(c => ({
      id: `e_${c.continues_from.source_id}_${c.id}`, source: c.continues_from.source_id, target: c.id,
      label: `f${c.continues_from.frame}`, style: { stroke: '#6fb98c' }, labelStyle: { fill: '#9fd' },
    }))
    setNodes(ns => [...ns, ...newNodes]); setEdges(es => [...es, ...newEdges])
  }, [setNodes, setEdges])

  const genKata = useCallback(async () => {
    const def = 'walk forward | front kick | right punch | turn 90 degrees left | left punch'
    const txt = window.prompt('Kata moves (separate with  |  ):', def)
    if (!txt) return
    const prompts = txt.split('|').map(s => s.trim()).filter(Boolean)
    if (!prompts.length) return
    try { await importSequence(prompts, 2.5) } catch (e) { alert('sequence failed: ' + e.message) }
  }, [importSequence])

  const addRoot = useCallback(() => {
    const id = nid()
    setNodes(ns => [...ns, { id, type: 'move', position: { x: 160 + Math.random() * 60, y: 60 + ns.length * 20 }, data: { title: 'new move', prompt: '', seconds: 2.5, clip: null } }])
  }, [setNodes])

  const clearAll = useCallback(() => {
    if (!window.confirm('Clear the kata graph? (generated clips stay saved on the server)')) return
    setNodes([]); setEdges([]); localStorage.removeItem(LS_KEY)
  }, [setNodes, setEdges])

  // Save on every change (small — no clip data); rehydrate clips once on load.
  useEffect(() => { localStorage.setItem(LS_KEY, serialize(nodes, edges)) }, [nodes, edges])
  useEffect(() => {
    for (const n of (persisted?.nodes || [])) {
      const cid = n.data?.clipId
      if (cid) fetchAnim(cid).then(clip => update(n.id, { clip })).catch(() => {})
    }
  }, []) // once on mount

  const ctx = useMemo(() => ({ update, generate, branch }), [update, generate, branch])

  return (
    <KataCtx.Provider value={ctx}>
      <div style={{ position: 'fixed', inset: 0, background: '#161618' }}>
        <div style={{ position: 'absolute', zIndex: 5, top: 10, left: 10, display: 'flex', gap: 8, alignItems: 'center', fontFamily: 'system-ui, sans-serif' }}>
          <button onClick={addRoot} style={{ padding: '8px 12px', borderRadius: 6, background: '#3a5', color: '#06210f', border: 'none', cursor: 'pointer', fontSize: 13, fontWeight: 700 }}>+ Root move</button>
          <button onClick={genKata} style={{ padding: '8px 12px', borderRadius: 6, background: '#5a4', color: '#0a210f', border: 'none', cursor: 'pointer', fontSize: 13, fontWeight: 700 }}>✨ Kata sequence</button>
          <button onClick={clearAll} style={{ padding: '8px 12px', borderRadius: 6, background: '#3a3a40', color: '#e3e3e8', border: '1px solid #4a4a52', cursor: 'pointer', fontSize: 13 }}>Clear</button>
          <span style={{ color: '#888', fontSize: 12 }}>kata builder — generate a move, scrub to a frame, branch a continuation · autosaves</span>
        </div>
        <ReactFlow
          nodes={nodes} edges={edges} nodeTypes={nodeTypes}
          onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
          onConnect={p => setEdges(es => addEdge(p, es))}
          onlyRenderVisibleElements minZoom={0.15} maxZoom={1.5} proOptions={{ hideAttribution: true }}
        >
          <Background color="#2a2a30" gap={20} />
          <Controls />
        </ReactFlow>
      </div>
    </KataCtx.Provider>
  )
}

export default function KataBuilder() {
  return <ReactFlowProvider><Flow /></ReactFlowProvider>
}
