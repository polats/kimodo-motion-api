"""
Batch bake a curated set of kimodo clips into a sbox addon directory.

Usage:
    python batch_bake.py --citizen-fbx ~/...citizen_REF.fbx \
                         --kimodo-dir ~/projects/kimodo/.kimodo-animations \
                         --out-dir   ~/projects/sbox-public/.../Assets/models/kimodo

Each clip's `prompt` is slugified into the output FBX name. Clips already
present (matching the same slug) are skipped unless --force is passed.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


# Curated kimodo clip IDs by their JSON filename stem. Chosen for variety.
CURATED = {
    "walk_forward": "dbb7d34029cc",
    "wave_right_hand": "345be7856ce3",
    "bow": "bdea6ebe2097",
    "celebrate": "cae9ff56105b",
    "clap": "ffcef27bc9cf",
    "dance_happy": "b73667b452dc",
    "jumping_jacks": "342711ffd11f",
    "punch_right": "f37a9cf3bb7b",
    "run_forward": "dd03d908f9a3",
    "shrug": "4f17bb90fb8b",
    "jump_in_place": "36fd6d258692",
    "point_forward": "f5de3080fdbf",
    "kick_forward": "8b9b58e5de2d",
}


def slugify(s):
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s.lower()).strip("_")
    return s[:48]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--citizen-fbx", required=True, type=Path)
    p.add_argument("--kimodo-dir", required=True, type=Path)
    p.add_argument("--out-dir", required=True, type=Path)
    p.add_argument("--bake-script", type=Path,
                   default=Path(__file__).parent / "bake.py")
    p.add_argument("--blender", default="blender")
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    ok, skip, fail = 0, 0, 0
    for name, clip_id in CURATED.items():
        src = args.kimodo_dir / f"{clip_id}.json"
        if not src.exists():
            print(f"[batch] MISSING {name} ({clip_id}.json)")
            fail += 1
            continue
        dst = args.out_dir / f"{name}.fbx"
        if dst.exists() and not args.force:
            print(f"[batch] skip {name} (already baked)")
            skip += 1
            continue
        cmd = [args.blender, "--background", "--python", str(args.bake_script), "--",
               "--citizen-fbx", str(args.citizen_fbx),
               "--clip", str(src),
               "--out", str(dst),
               "--clip-name", name]
        print(f"[batch] bake {name}…")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not dst.exists():
            print(f"[batch] FAIL {name}: {r.stderr[-400:]}")
            fail += 1
            continue
        ok += 1

    print(f"[batch] done: ok={ok} skip={skip} fail={fail}")


if __name__ == "__main__":
    main()
