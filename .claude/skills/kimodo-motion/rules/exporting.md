# Exporting clips

There isn't one universal "export" button — the right path depends on the
target, and on the fact that two representations exist:

- **Motion-API JSON** — what `kimodo-gen` / the viewer / the s&box baker use
  (`.kimodo-animations/<id>.json`). Per-frame bone quaternions + root positions.
- **Kimodo NPZ** — the research/robotics representation the CLI and converters
  use. The CLI `kimodo_gen` writes this (and other formats) directly.

## Format map — pick by target

| Target | Format | How |
|---|---|---|
| **s&box** (citizen rig) | FBX + `kim_` vmdl | `kimodo-bake` → `rules/sbox-integration.md` |
| Browser preview | (none) | `kimodo-view` reads the JSON live |
| Blender / Maya / three.js (generic) | GLB / FBX | via NPZ → your DCC's SMPL-X importer; the SMPL-X **mesh** GLB is `export_smplx_glb` (see below) |
| Research (AMASS) | NPZ | CLI `kimodo_gen --output x.npz`, or `kimodo_convert` |
| Mocap tools | BVH | CLI `kimodo_gen` BVH output, or `kimodo_convert in.npz out.bvh` |
| Humanoid robots (Unitree G1) | CSV | CLI G1-CSV output / `kimodo_convert`; see the repo's robotics docs |

## The CLI: generate straight to NPZ/BVH/CSV

`kimodo_gen` (pip entry point, or `docker exec demo python -m kimodo.scripts.generate`)
**generates and writes** motion in standard formats in one go — `--output`
controls the stem (`x.npz`, `x.csv`, `x.bvh`, …). This is the path for any
non-s&box, non-viewer consumer. (It re-generates; it doesn't convert an existing
motion-API JSON clip.)

## `kimodo_convert` — convert between formats

`kimodo_convert <input> <output> [--from FMT] [--to FMT]` (entry point, or
`docker exec demo python -m kimodo.scripts.motion_convert`). Formats:
`kimodo` (NPZ), `amass`, `soma-bvh`, `g1-csv`. Use it to turn an NPZ into BVH for
a mocap tool, AMASS for research, or G1 CSV for a robot. Inside the container,
paths are under `/workspace` (the repo bind mount).

## `export_smplx_glb` — the SMPL-X *avatar*, not a clip

`export_smplx_glb` bakes the **SMPL-X body mesh** (with shape/expression morph
targets) to a GLB — i.e. the rigged character you'd drive, not a specific
animation. Use it when you need the SMPL-X avatar in a GLB pipeline; combine with
a motion via your DCC. It is **not** "export my generated clip to GLB".

## Rule of thumb

- Staying in s&box? Ignore all of the above — use `kimodo-bake`.
- Going to a DCC/engine that imports SMPL-X? Export motion as NPZ/BVH via the
  CLI and import with that tool's SMPL-X support.
- Going to research/robotics? NPZ / AMASS / BVH / G1-CSV via the CLI + converter.
