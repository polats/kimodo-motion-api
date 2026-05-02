# Mixamo pipeline (characters + animations)

End-to-end flow for getting Mixamo characters and animations into the
demo without Blender. All conversion is done by the standalone
`FBX2glTF` binary; everything else is plain Python + the existing
FastAPI server.

## Architecture

```
Mixamo cloud catalog
  │
  ├─ characters: T-pose FBX
  │     ↓ (kimodo/scripts/mixamo.py)
  │     web/public/models/mixamo_<slug>.glb
  │     ↓
  │     .kimodo-characters/<id>.json   ←  CharacterRegistry
  │
  └─ animations: rigged-on-Y-Bot motion FBX
        ↓ (kimodo/scripts/mixamo.py)
        web/public/animations/mixamo/<slug>.glb
        ↓
        .kimodo-mixamo-animations/<id>.json   ←  MixamoAnimationRegistry

FastAPI exposes:
  GET    /characters
  DELETE /characters/{id}
  GET    /mixamo/search                     (character search)
  POST   /mixamo/import                     (download + register)
  GET    /mixamo/animations                 (list)
  DELETE /mixamo/animations/{id}
  GET    /mixamo/animations/search          (motion search)
  POST   /mixamo/animations/import          (download + register)

Frontend (web/src/main.js):
  - Bootstraps from /characters and /mixamo/animations
  - Searchable pickers for both
  - Mixamo characters driven by either:
      kimodo motions  → animator.js retargeter
      Mixamo motions  → three.js AnimationMixer
```

## Prerequisites

1. **Mixamo token** — log in at https://www.mixamo.com, then in DevTools
   Console:
   ```js
   JSON.parse(localStorage.getItem("persist:root")).access_token.replace(/"/g,'')
   ```
   Save to repo `.env` as `MIXAMO_TOKEN=<value>`. Tokens expire — refresh
   when 401s start showing up.

2. **FBX2glTF binary** — see `web/scripts/tools/README.md` for the curl
   one-liner. Drop the binary at `web/scripts/tools/FBX2glTF`.

3. **Motion API running** with the env loaded:
   ```bash
   docker exec -d demo bash -c "set -a; source /workspace/.env; set +a; \
     SERVER_PORT=7862 python -m kimodo.scripts.run_motion_api > /tmp/motion_api.log 2>&1"
   ```

## Bulk import

```bash
# 30 diverse characters (idempotent)
python web/scripts/bulk_import_mixamo_characters.py

# 30 Sims-style animations (idempotent)
python web/scripts/bulk_import_mixamo_animations.py

# 30 kimodo text-to-motion clips (idempotent, slow ~15min)
python web/scripts/bulk_generate_kimodo_animations.py
```

All three scripts are safe to re-run; each skips entries already in the
respective registry/store.

## Single-character / single-animation import via UI

Click **+ Mixamo** next to the Character picker → search → click a
result. The backend downloads + converts + registers, the frontend
appends to the picker and selects the new entry.

(Animation import via UI isn't wired yet — use
`bulk_import_mixamo_animations.py` for now, or POST to
`/mixamo/animations/import` directly.)

## Gotchas hit during development

### Mixamo `gms_hash` wants `params=""` (not `"0,0,0"`)

The catacombs reference script hard-codes `params: "0,0,0"` in the
animation export payload. Mixamo currently rejects that with `Error
while generating the animation` for any motion whose `params` array
contains only `["Overdrive", 0]`. Fix: pass an empty string when there
are no non-Overdrive params, otherwise comma-join the actual param
default values. See `_build_gms_hash` in `kimodo/scripts/mixamo.py`.

### Mixamo motion exports flake; retry the second result

Even with a correct payload, Mixamo's job pipeline returns `failed`
sometimes for no obvious reason. The bulk-import script tries the second
search hit if the first export fails.

### FBX2glTF emits two animation clips per motion FBX

The exported GLB ships an empty `Take 001` clip alongside the real
`mixamo.com` clip. Frontend picks the first clip with non-zero tracks.

### Three.js GLTFLoader sanitizes node names

`mixamorig:Hips` becomes `mixamorig_Hips` after import. The animator's
`_normName` strips both `.` and `:`, so a mapping table written with
either form matches.

### Character mesh data is shared L/R via mirror rotation (Blender Studio)

Documented at length in `BLENDER_STUDIO_RIGID_GLBS.md` — same
build-time bake fix is unrelated to Mixamo but lives in the same dir.

## File layout

```
kimodo/scripts/
  mixamo.py                  # Mixamo API client + import_character + import_motion
  character_registry.py      # Disk-backed character config registry
  animation_registry.py      # Disk-backed Mixamo animation registry
  run_motion_api.py          # Endpoints
web/scripts/
  import_mixamo_glb.py       # CLI: one-off character import (legacy, also reusable)
  bulk_import_mixamo_characters.py
  bulk_import_mixamo_animations.py
  bulk_generate_kimodo_animations.py
  seed_character_registry.py # Seed registry from existing GLBs (one-time)
  tools/
    FBX2glTF                 # binary (gitignored)
    README.md                # how to fetch
  SIMS_ANIMATIONS.md         # 50-entry animation catalog
  MIXAMO_PIPELINE.md         # this file
  BLENDER_STUDIO_RIGID_GLBS.md
web/public/
  models/mixamo_*.glb        # imported character meshes (gitignored)
  animations/mixamo/*.glb    # imported animation tracks (gitignored)
.kimodo-characters/<id>.json           # character registry (gitignored)
.kimodo-mixamo-animations/<id>.json    # animation registry (gitignored)
.mixamo-cache/                         # raw FBX downloads (gitignored)
```
