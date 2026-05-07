"""Register a UniRig-rigged GLB into the kimodo character registry.

Mirrors `import_mixamo_glb.py` but for UniRig output (e.g. a Trellis GLB
piped through the unirig service). Differences:

  * No FBX→GLB conversion — UniRig already returns GLB.
  * Mapping is per-character (UniRig bones are anonymous), so we run the
    `unirig_mapping.py` labeler and embed the resulting joint table
    literally in the registry record's `mapping` field.

Usage::

    python web/scripts/import_unirig_glb.py path/to/rig.glb \\
        --id unirig_alice --label "Alice (UniRig)"

The GLB is copied into web/public/models/<id>.glb and a registry record
is written to .kimodo-characters/<id>.json. Re-running with the same id
overwrites both. Pass --no-overwrite to bail if the id already exists.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

# Resolve sibling labeler without depending on package install.
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import unirig_mapping  # type: ignore[import-not-found]  # noqa: E402

REPO_ROOT = SCRIPT_DIR.parents[1]
MODELS_DIR = REPO_ROOT / "web" / "public" / "models"
REGISTRY_DIR = REPO_ROOT / ".kimodo-characters"


def _slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "unirig"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("glb", type=Path, help="UniRig-rigged GLB to import")
    ap.add_argument("--id", required=False,
                    help="character id (default: derived from --label or filename)")
    ap.add_argument("--label", required=False,
                    help="human-readable label (default: derived from id)")
    ap.add_argument("--no-overwrite", action="store_true",
                    help="fail if a record with this id already exists")
    args = ap.parse_args()

    src = args.glb.resolve()
    if not src.is_file():
        print(f"error: {src} not found", file=sys.stderr)
        return 1

    # Derive id and label.
    base = src.stem
    char_id = args.id or f"unirig_{_slugify(base)}"
    if not char_id.startswith("unirig_"):
        char_id = f"unirig_{_slugify(char_id)}"
    label = args.label or f"{base.replace('_', ' ').title()} (UniRig)"

    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    record_path = REGISTRY_DIR / f"{char_id}.json"
    glb_dst = MODELS_DIR / f"{char_id}.glb"

    if args.no_overwrite and record_path.exists():
        print(f"error: registry record exists at {record_path}", file=sys.stderr)
        return 1

    # Build the joint mapping. Bail loudly if topology doesn't fit so the
    # operator gets a clear "this isn't a humanoid" signal rather than a
    # silently-broken character.
    try:
        mapping = unirig_mapping.label_glb(src)
    except unirig_mapping.TopologyError as exc:
        print(f"error: failed to derive joint mapping from {src}:", file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        return 1

    # Copy GLB into web/public/models/.
    shutil.copyfile(src, glb_dst)

    record = {
        "id": char_id,
        "label": label,
        "url": f"/models/{glb_dst.name}",
        "skinned": True,
        # No `mappingKind` — passing a literal `mapping` object lets
        # web/src/main.js' addCharacter() use it directly without a
        # MAPPING_BUILDERS lookup.
        "mapping": mapping,
        "source": "unirig",
        "source_glb": src.name,
        "created_at": int(time.time()),
    }
    record_path.write_text(json.dumps(record, indent=2) + "\n")

    print(f"wrote {record_path}")
    print(f"copied {glb_dst}")
    print(f"\nrestart or reload the web frontend to pick up '{char_id}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
