# Generating motions

## The motion API (`POST /generate`)

`kimodo-gen` wraps this. Request body:

```json
{ "prompt": "a person waves hello with their right hand",
  "seconds": 5.0,
  "seam_pose": null }
```

- **`prompt`** (required) — free text describing one action.
- **`seconds`** — clip length, clamped to `[0.5, MAX_SECONDS]`. 30 fps, so
  `num_frames = round(seconds * 30)`.
- **`seam_pose`** (optional) — a pose constraint applied to frame 0 **and** the
  last frame, with foot-skate cleanup, so the clip **loops** without a visible
  pop. Use it for looping idles/locomotion; omit for one-shot gestures.

Response is the full record (motion arrays — ~1 MB) plus an `id`. The clip is
**auto-saved** server-side to `KIMODO_DIR/.kimodo-animations/<id>.json` (or
`KIMODO_STORE_PATH`), which is what the viewer and the s&box baker read.

```
kimodo-gen "a person sits down and crosses their legs" 4
kimodo-list                 # newest first
kimodo-list newspaper       # filter by prompt substring
kimodo-list --json          # raw
```

Other endpoints: `GET /animations`, `GET /animations/{id}`, `GET /characters`,
plus `/mixamo/*` (Mixamo search/import; needs `MIXAMO_TOKEN` in `.env`).

## Writing prompts that come out clean

- **One clear action per clip.** "raises a cup to their mouth and sips" is good;
  "cooks dinner while talking on the phone" is not.
- **Name the body part / side** — "right hand", "both hands", "left leg". The
  model respects handedness and you'll want it for one-handed overlays in-engine.
- **Describe the whole arc** if you want a beat to exist — e.g. for a reading
  clip, "holds a folded newspaper, raises it, opens it, and reads" puts the
  *open* beat in the clip; "reads a newspaper" may start already reading.
- **Keep it ~3–6s.** Long clips drift; short clips are tighter and loop better.
- **End a one-shot near its start pose** (e.g. a sip that returns the arm down)
  so a downstream blend-back lands cleanly.
- For locomotion, expect some drift/acceleration in long single clips — generate
  short and loop with `seam_pose`, or bake and handle root motion in-engine.

## Chaining moves (combos / katas)

To make a move **start from a frame of another clip** — for a combo, a kata, or
branching variations off a shared opening — use `/generate_continue`,
`/generate_sequence`, and `/stitch_path`. See **`rules/continuations.md`**
(including the `first_heading_angle` gotcha that drives in-place moves backward).

## Other ways to generate

- **CLI `kimodo_gen`** (pip path / `docker exec`) — generates and writes motion
  in research formats (NPZ/CSV/BVH/AMASS) in one command via `--output`. See
  `rules/exporting.md`. This is the route for non-engine formats.
- **Gradio demo** (:7860) — interactive prompt+preview UI for hand authoring;
  `docker exec demo python -m kimodo.demo` (see `rules/setup.md`).
