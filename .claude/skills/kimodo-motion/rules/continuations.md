# Continuations & katas (starting a move from a frame)

To build a combo/kata, generate each move so it **starts from a specific frame of
an existing clip** instead of from the rest pose. These endpoints have no CLI
wrapper — call them with curl/python against `KIMODO_URL`.

## `POST /generate_continue` — a move that flows on from another

```json
{ "source_id": "<clip id>", "prompt": "steps forward and punches",
  "seconds": 2.2, "source_frame": -1, "stitch": false, "end_on_peak": null }
```

- **`source_frame`** — which frame of the source to branch from (`-1` = last).
  A mid-frame branches a variation off the *middle* of a move.
- **`stitch: false`** (default) → the new move is its own clip, re-rooted to XZ
  origin, starting at the branch pose. Its record carries
  `continues_from: {source_id, frame}` — the tree edge the `/kata` viewer reads.
- **`stitch: true`** → also prepend the source up to the branch frame → one
  combined clip (e.g. to bake a whole kata as a single sequence).
- **`end_on_peak`** — `"punch"`/`"kick"`: end the move mid-action (see below).

### ⚠️ Do NOT seed a heading (the bug we chased for hours)
`/generate_continue` pins **only frame 0** to the source pose (a single full-body
constraint, re-rooted to XZ origin) and leaves the rest free. **That full-body
pin already sets the starting orientation** — the body faces the branch pose's
way on its own.

It is tempting to also pass `first_heading_angle` (the branch pose's facing) to
"keep it pointing right." **Don't.** Seeding a heading makes the model add a large
**backward** root translation on non-locomotion moves — a punch slid ~2.4 m
backward *with* the heading vs ~0.6 m (≈ in place) *without* it. Walks travel
forward either way, so the regression hides until a stance/strike move moonwalks.
The correct `_build_start_constraint` (in `kimodo/scripts/run_motion_api.py`)
passes **no `first_heading_angle`**. The reproduction was: original code (no
heading) built clean katas → adding a heading to "fix walk direction" broke every
in-place move. If branched moves drift backward, this is the first thing to check.

## Ending a move mid-action — `end_on_peak`
`"punch"` trims to the frame the wrist is most extended; `"kick"` to the frame a
foot is highest. The next continuation then begins from that extended/airborne
pose, so the kata flows strike-to-strike instead of resetting to a stance between
moves. **Caveat:** this model's kicks are weak — a "high kick" foot often only
reaches ~0.1 m, so `end_on_peak:"kick"` endings read as low/mid kicks.

## `POST /generate_sequence` — a whole kata in one diffusion (`multi_prompt`)

```json
{ "prompts": ["ready stance", "step and punch", "front kick"],
  "seconds": 2.5, "save_segments": true }
```

Generates every move in ONE denoising pass with smooth transitions, so headings
and momentum stay globally coherent. `save_segments: true` slices it into per-move
tree nodes (chained via `continues_from`), same shape as `/generate_continue`
output. Trade-off: it **carries momentum** — seed it with "walk forward" and every
move keeps walking forward. Use it for a single clean continuous take; use
per-move `/generate_continue` for interactive authoring and branching from any
frame.

## `POST /stitch_path` — play a root→leaf path as one motion

```json
{ "ids": ["root", "child", "grandchild"], "save": false }
```

Concatenates the path, carrying world position + heading across each join, and
**cuts each parent at the child's `continues_from.frame`** so mid-frame branches
stitch cleanly. This is what the `/kata` viewer plays in "path" mode.

## Verifying direction — trust the stitched view, not isolated clips
Each move's root is **unconstrained after frame 0**, so a standalone re-rooted
clip can *look* like it moonwalks even when the generation is fine. What actually
misled us:
- A **follow-camera or a pinned pelvis** makes a correct forward walk look like a
  backward treadmill — the planted foot slides under the centred body. Judge with
  a **static camera** (the `/kata` viewer defaults to one).
- Judge a kata from the **stitched path** (`/stitch_path` / `/kata` "play path"),
  not each clip re-rooted to origin on its own.
- To check one clip numerically, compare net XZ displacement to **facing from the
  toes** (`foot − ankle`), not a hip-heading formula — `net·facing > 0` = forward.
  This metric is **meaningless for turning moves** (facing changes mid-clip).

## Reference scripts (repo root)
- `kata_library.py` — builds a deep Shotokan move tree via `/generate_continue`.
- `dynamic_katas.py` — katas that end mid-action via `end_on_peak`.
- `render_traj.py` / `debug_heading_carry.py` — top-down trajectory + toe-facing
  checks used to debug the heading bug above.
- `/kata` viewer (`web/kata.html`) — browse the tree, play/scrub a path, plus a
  reusable actions library.
