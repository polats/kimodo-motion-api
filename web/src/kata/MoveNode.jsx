// A kata move as a React Flow node: prompt + duration + end-on-peak, Generate,
// an in-node 3D preview with a frame scrubber, and "+ branch @ fN" which spawns
// a child node pre-wired to continue from the scrubbed frame.
import { memo, useContext, useState } from 'react'
import { Handle, Position } from 'reactflow'
import NodePreview from './NodePreview.jsx'
import { KataCtx } from './ctx.js'

const fld = { width: '100%', background: '#26262b', color: '#e3e3e8', border: '1px solid #3a3a40', borderRadius: 5, padding: '4px 6px', fontSize: 12, boxSizing: 'border-box' }

function MoveNode({ id, data, selected }) {
  const ctx = useContext(KataCtx)
  const [tip, setTip] = useState(false)
  const c = data.clip
  const maxF = c ? c.num_frames - 1 : 0
  const scrub = data.scrub

  return (
    <div className="nowheel" style={{ width: 244, background: '#202024', border: `2px solid ${selected ? '#6fb98c' : (c ? '#46464e' : '#5a4a3a')}`, borderRadius: 10, color: '#e3e3e8', fontSize: 12, overflow: 'hidden' }}>
      <Handle type="target" position={Position.Top} style={{ background: '#888' }} />
      <div style={{ padding: '7px 9px', background: '#2a2a30', fontWeight: 600, display: 'flex', justifyContent: 'space-between', gap: 6 }}>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{data.title || 'new move'}</span>
        <span style={{ color: c ? '#7ec77e' : '#b98a5a', fontSize: 10, whiteSpace: 'nowrap' }}>
          {c ? `${c.num_frames}f` : (data.parentFrame != null ? `↳f${data.parentFrame}` : 'root')}
        </span>
      </div>
      <div style={{ padding: 9, display: 'flex', flexDirection: 'column', gap: 6 }}>
        <textarea className="nodrag" rows={2} placeholder="motion prompt, e.g. throws a front kick"
          value={data.prompt} onChange={e => ctx.update(id, { prompt: e.target.value })}
          style={{ ...fld, resize: 'vertical' }} />
        <div className="nodrag nowheel" style={{ fontSize: 11, color: '#bbb' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span>Duration</span><span style={{ color: '#e3e3e8' }}>{Number(data.seconds).toFixed(1)}s</span>
          </div>
          <input type="range" min="0.1" max="5" step="0.1" value={data.seconds}
            onChange={e => ctx.update(id, { seconds: Number(e.target.value) })} style={{ width: '100%' }} />
        </div>
        <div className="nodrag" style={{ position: 'relative' }}>
          <div style={{ fontSize: 11, color: '#bbb', marginBottom: 3 }}>
            End on peak{' '}
            <span style={{ color: '#8ab', cursor: 'help', borderBottom: '1px dotted #8ab' }}
              onMouseEnter={() => setTip(true)} onMouseLeave={() => setTip(false)}>ⓘ</span>
          </div>
          {tip && (
            <div style={{ position: 'absolute', zIndex: 20, top: 16, left: 0, width: 214, background: '#0e0e12', color: '#dcdce0', border: '1px solid #4a4a52', borderRadius: 6, padding: '7px 9px', fontSize: 11, lineHeight: 1.4, boxShadow: '0 4px 14px #000a' }}>
              Trim the clip to end <b>mid-action</b> instead of a standing pose — so the next branched move starts from a non-grounded pose.
              <br /><b>Kick</b>: frame a foot is highest. <b>Punch</b>: frame the arm is most extended. <b>Full</b>: whole move (returns to standing).
            </div>
          )}
          <div style={{ display: 'flex', gap: 10, fontSize: 11 }}>
            {[['', 'Full'], ['kick', 'Kick'], ['punch', 'Punch']].map(([v, lbl]) => (
              <label key={v} style={{ display: 'flex', alignItems: 'center', gap: 3, cursor: 'pointer' }}>
                <input type="radio" name={`peak-${id}`} checked={(data.endOnPeak || '') === v}
                  onChange={() => ctx.update(id, { endOnPeak: v || undefined })} />
                {lbl}
              </label>
            ))}
          </div>
        </div>
        <button className="nodrag" onClick={() => ctx.generate(id)} disabled={data.busy || !data.prompt?.trim()}
          style={{ ...fld, cursor: 'pointer', background: data.busy ? '#555' : '#3a5', color: '#06210f', fontWeight: 700, border: 'none' }}>
          {data.busy ? 'generating…' : (c ? 're-generate' : 'generate')}
        </button>

        {c && <>
          <div style={{ height: 180, background: '#0e0e10', borderRadius: 6, overflow: 'hidden' }}>
            <NodePreview motion={c} frame={scrub} />
          </div>
          <input className="nodrag nowheel" type="range" min={0} max={maxF} value={scrub ?? 0}
            onChange={e => ctx.update(id, { scrub: Number(e.target.value) })} style={{ width: '100%' }} />
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <button className="nodrag" title={Number.isFinite(scrub) ? 'resume looped playback' : 'playing — drag the slider to pause on a frame'}
              onClick={() => ctx.update(id, { scrub: undefined })}
              style={{
                ...fld, width: 'auto', cursor: 'pointer', whiteSpace: 'nowrap',
                background: Number.isFinite(scrub) ? '#26262b' : '#2d4a35',
                color: Number.isFinite(scrub) ? '#e3e3e8' : '#7ec77e',
                borderColor: Number.isFinite(scrub) ? '#3a3a40' : '#3a6',
              }}>
              {Number.isFinite(scrub) ? '▶ play' : '❚❚ playing'}
            </button>
            <span style={{ fontSize: 10, color: '#aaa', minWidth: 52 }}>{Number.isFinite(scrub) ? `f${scrub}/${maxF}` : 'looping'}</span>
            <button className="nodrag" onClick={() => ctx.branch(id)} style={{ ...fld, flex: 1, cursor: 'pointer', borderColor: '#6fb98c' }}>
              + branch @ f{Number.isFinite(scrub) ? scrub : maxF}
            </button>
          </div>
        </>}
      </div>
      <Handle type="source" position={Position.Bottom} style={{ background: '#888' }} />
    </div>
  )
}
export default memo(MoveNode)
