"""Convert a Mixamo FBX download into a GLB ready for the kimodo web demo.

Usage:
    python web/scripts/import_mixamo_glb.py path/to/character.fbx [character_id]

What it does:
    1. Runs the bundled FBX2glTF binary (web/scripts/tools/FBX2glTF) on
       the FBX, writing the GLB to web/public/models/<character_id>.glb.
    2. Prints the snippet to paste into web/src/rigs.js — one entry per
       Mixamo character, all using the shared mixamoMapping().

Why no Blender:
    Mixamo's standard skeleton (mixamorig:*) is the same across every
    character, so once the kimodo→mixamorig bone mapping is wired in
    rigs.js the runtime retargeter handles the rest. The only build-time
    work is FBX → GLB, which FBX2glTF does in one shot.

Tip:
    On mixamo.com, after picking a character, click "Download" → choose
    format "FBX Binary (.fbx)", FBX version 7.4 or 7.5, "T-Pose" for
    pose. No animation needed — kimodo drives the rig at runtime.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FBX2GLTF = REPO_ROOT / "web" / "scripts" / "tools" / "FBX2glTF"
OUT_DIR = REPO_ROOT / "web" / "public" / "models"


def slugify(name: str) -> str:
    s = "".join(c if c.isalnum() else "_" for c in name.lower())
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fbx", type=Path, help="Path to the Mixamo FBX file")
    ap.add_argument("character_id", nargs="?",
                    help="Slug for the GLB filename + rigs.js id "
                         "(default: derived from the FBX basename)")
    args = ap.parse_args()

    if not args.fbx.exists():
        sys.exit(f"error: {args.fbx} not found")
    if not FBX2GLTF.exists():
        sys.exit(
            f"error: FBX2glTF binary not found at {FBX2GLTF}\n"
            f"see {FBX2GLTF.parent}/README.md for download instructions"
        )

    char_id = args.character_id or slugify(args.fbx.stem)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_glb = OUT_DIR / f"mixamo_{char_id}.glb"

    # FBX2glTF writes <output>.glb — pass the path without the extension.
    out_stem = out_glb.with_suffix("")
    cmd = [str(FBX2GLTF), "--binary", "--input", str(args.fbx),
           "--output", str(out_stem)]
    print(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        sys.exit(f"FBX2glTF failed (exit {proc.returncode})")

    if not out_glb.exists():
        sys.exit(f"FBX2glTF reported success but {out_glb} is missing")

    label = args.fbx.stem.replace("_", " ").title()
    print(f"\n[ok] wrote {out_glb.relative_to(REPO_ROOT)}")
    print("\nAdd to web/src/rigs.js inside the CHARACTERS array:")
    print(f"""
  {{
    id: 'mixamo_{char_id}',
    label: '{label} (Mixamo)',
    url: '/models/{out_glb.name}',
    skinned: true,
    mapping: mixamoMapping(),
    scale: 1.0,
  }},""")


if __name__ == "__main__":
    main()
