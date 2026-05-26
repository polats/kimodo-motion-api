---
name: kimodo-motion
description: Generate humanoid animations from text with Kimodo (NVIDIA's SMPL-X text-to-motion diffusion model) and get them into a project. Use when the user wants to run the Kimodo server, generate/author a motion from a text prompt, chain moves into a combo/kata (start a move from a frame of another clip), browse or preview generated clips, export them (GLB/NPZ/BVH), or bake them into s&box. Triggers include "kimodo", "text to motion", "generate an animation", "make a walk/wave/sit animation", "kata", "combo", "continue/branch from a frame", "SMPL-X", "motion diffusion", "bake/import this animation", "kimodo server".
allowed-tools: Bash(.claude/skills/kimodo-motion/tools/*:*)
metadata:
  tags: kimodo, text-to-motion, smplx, animation, motion-diffusion, gamedev, sbox, docker, nvidia
---

# Kimodo text-to-motion

Generate humanoid animations from text prompts with [Kimodo](https://github.com/nvidia/kimodo)
(SMPL-X motion diffusion) and move them downstream — preview in a browser,
export to standard formats, or bake into the s&box citizen rig. This skill wraps
the server, the generation API, the web viewer, and the s&box baker, plus the
non-obvious gotchas (retargeting correctness, root motion, VRAM).

It is **docker-first**: the model runs in the `kimodo:1.0` container stack. A
pip/venv path exists (see `rules/setup.md`) but isn't the default here.

## When to use

- Running the Kimodo server / motion API; checking it's healthy.
- Generating a motion from text, iterating on prompts, browsing the library.
- Exporting a clip (GLB / NPZ / BVH / AMASS / robot CSV) or **baking into s&box**.
- Debugging "the animation looks wrong" (retargeting, root motion, loops).

## How to use

### On invocation
Run **`kimodo-doctor`** first — it verifies docker, the GPU/VRAM, the
`kimodo:1.0` image, the kimodo checkout, and whether the motion API is up. Fix
anything it flags (it points at `rules/setup.md`). Then `kimodo-serve`.

### Config
The tools find things via two env vars (both have sane defaults):
- **`KIMODO_DIR`** — path to a kimodo checkout (the dir with `docker-compose.yaml`
  + `baker/`). Autodetected by walking up from this skill, then `~/projects/kimodo`
  etc. Set it explicitly if the skill is installed away from the repo.
- **`KIMODO_URL`** — motion API base URL. Default `http://127.0.0.1:7862`.

### Tools
| Tool | What it does |
|---|---|
| `kimodo-doctor` | Verify docker, GPU/VRAM, image, checkout, ports. **Run first.** |
| `kimodo-serve` | `docker compose up -d` the stack, wait until the motion API answers. `--status`, `--logs`, `--down`. |
| `kimodo-gen "<prompt>" [secs]` | Generate one clip via `POST /generate`; prints the new id. Auto-saved server-side. |
| `kimodo-list [substr]` | List saved clips (newest first; optional prompt filter). `--json` for raw. |
| `kimodo-view` | Start the three.js web viewer at http://localhost:5173 (reads `KIMODO_URL`). `--down`. |
| `kimodo-bake <name> <id> --out <dir>` | Bake one clip into an s&box citizen FBX + `kim_<name>` sequence. Needs Blender + the citizen REF fbx. |

### Lookup index
Read the matching rule before going deep:

- **`rules/setup.md`** — docker compose stack (CPU text-encoder :9550 + motion API
  :7862, image `kimodo:1.0`, ~24GB GPU / CPU-encoder fallback), the pip
  alternative, `KIMODO_DIR`/`KIMODO_URL`, `.env`. **Read for install/run issues.**
- **`rules/generating.md`** — the `/generate` contract (`prompt`, `seconds`,
  `seam_pose` for clean loops), prompt-writing tips, the CLI (`kimodo_gen`) and
  gradio demo (:7860), listing/fetching. **Read when authoring clips.**
- **`rules/continuations.md`** — chaining moves into combos/katas: starting a move
  from a frame of another clip (`/generate_continue`), ending mid-action
  (`end_on_peak`), whole-kata `/generate_sequence`, `/stitch_path`, and the
  **`first_heading_angle` gotcha** (seeding a heading drives in-place moves
  backward). **Read before building a kata or branching from a frame.**
- **`rules/exporting.md`** — the format map: API JSON (viewer + baker), NPZ/BVH/
  AMASS/G1-CSV (research/robotics, via the CLI + `kimodo_convert`), SMPL-X mesh
  GLB. Which format for which target. **Read before exporting anywhere but s&box.**
- **`rules/sbox-integration.md`** — the baker (`ExtractMotion` root motion, curated
  set, `gen_vmdl`, `kim_` naming), in-engine playback (full-body takeover vs
  masked upper-body overlay) + the retargeting gotcha, **previewing kimodo
  motion on an s&box citizen in the web viewer via the T-pose→UniRig re-rig**
  (the native citizen rig won't live-retarget — A-pose bind, +X bone axes, LOD/head
  facts), **and decompiling + rigging s&box clothing onto the viewer citizen**
  (VRF `.vmdl_c`→glTF, the REF-rig bind, body-normalize fit, per-body variants,
  the CLOTHING drawer). **Read to use clips in s&box** — pair with `sbox-gamedev`.

### End-to-end workflow
1. `kimodo-doctor` → fix anything red.
2. `kimodo-serve` → wait for "ready".
3. `kimodo-gen "a person waves hello with their right hand" 4` → note the id.
4. `kimodo-view` → preview at http://localhost:5173 (iterate on the prompt).
5. Ship it:
   - **s&box:** `kimodo-bake wave <id> --out <project>/Assets/models/kimodo`,
     then recompile in-engine and play `kim_wave` (see `rules/sbox-integration.md`).
   - **elsewhere:** export via the CLI/converter (see `rules/exporting.md`).

## Notes
- The motion API loads the diffusion model into VRAM on first boot — the first
  `kimodo-serve` (or first request) is slow; subsequent ones are fast.
- Generated clips are saved as `<id>.json` under `KIMODO_DIR/.kimodo-animations/`
  (or `KIMODO_STORE_PATH`). That JSON is what the viewer and the baker consume.
