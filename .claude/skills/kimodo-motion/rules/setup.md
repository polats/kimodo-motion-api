# Setup & running the server

Kimodo runs as a small **docker compose** stack. This is the supported path for
this skill; a pip/venv install is possible but you manage CUDA/torch yourself.

## The compose stack (`docker-compose.yaml`)

Two services, both from the `kimodo:1.0` image (built from the repo `Dockerfile`):

| Service | Port | Command | Notes |
|---|---|---|---|
| `text-encoder` | 9550 | `run_text_encoder_server` | Runs the text encoder on **CPU** (`TEXT_ENCODER_DEVICE=cpu`) so the diffusion model gets the whole GPU. Has a healthcheck; `demo` waits for it. |
| `demo` | **7862** | `run_motion_api` | The **motion API** — the canonical service this skill talks to (`KIMODO_URL`). Also maps 7860 (gradio demo) / 7861 for ad-hoc `docker exec` launches. |

`kimodo-serve` runs `docker compose up -d` and polls `/animations` until the API
answers (the model loads into VRAM on boot, so the first start takes ~10–40s).

```
kimodo-serve            # up + wait healthy
kimodo-serve --status   # containers + API reachability
kimodo-serve --logs     # tail the motion API container
kimodo-serve --down     # stop
```

## Hardware

- **GPU:** ~17 GB VRAM for the full model on GPU. The compose file already puts
  the **text encoder on CPU**, which is what lets a 24 GB card (e.g. RTX 3090)
  run the diffusion model comfortably. `kimodo-doctor` warns under ~17 GB.
- **No/low GPU:** generation will be very slow; not recommended.

## Config the tools read

- **`KIMODO_DIR`** — a kimodo checkout (has `docker-compose.yaml` + `baker/`).
  Autodetected (walk up from the skill → `~/projects/kimodo` → siblings); set it
  if the skill is installed away from the repo. The repo is bind-mounted into the
  containers at `/workspace`, and `.kimodo-animations/` (the clip store) lives at
  its root.
- **`KIMODO_URL`** — motion API base URL, default `http://127.0.0.1:7862`. Point
  it elsewhere if you forwarded the port or run the API on another host.
- **`.env`** (repo root) — holds e.g. `MIXAMO_TOKEN` (Mixamo import) and
  `SERVER_PORT`. Not required for plain generation.

## First run / building the image

If `kimodo:1.0` isn't built yet, `docker compose up` builds it from the
`Dockerfile` on first invocation (slow — multi-GB). `kimodo-doctor` reports
whether the image exists.

## The pip / venv alternative (not the default here)

The repo is a normal Python package with console entry points
(`pyproject.toml`): `kimodo_gen` (CLI generate), `kimodo_demo` (gradio demo on
:7860), `kimodo_textencoder`, `kimodo_convert`. Install with the repo's
instructions (`pip install -e .` + model/torch deps + checkpoints). You then run
`kimodo_demo` / `kimodo_gen` directly instead of the container. The skill's tools
assume the docker stack; for pip you'd call those entry points yourself.

## Other surfaces (for reference)

- **Gradio demo** (interactive authoring UI) on :7860 —
  `docker exec demo python -m kimodo.demo`. Loads a second copy of the diffusion
  model into VRAM, so it competes with the motion API on a 24 GB card; launch on
  demand, not by default.
- **`run_simple_app`** on :7861 — ad-hoc, via `docker exec`.
