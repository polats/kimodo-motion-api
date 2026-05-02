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


def convert_fbx_to_glb(fbx_path: Path, char_id: str | None = None,
                       out_dir: Path = OUT_DIR) -> Path:
    """Convert a Mixamo FBX to GLB. Returns the output path.

    Importable from server code so the frontend's "Import from Mixamo"
    flow can reuse the same conversion as the CLI."""
    fbx_path = Path(fbx_path)
    if not fbx_path.exists():
        raise FileNotFoundError(fbx_path)
    if not FBX2GLTF.exists():
        raise FileNotFoundError(
            f"FBX2glTF binary not found at {FBX2GLTF}; "
            f"see {FBX2GLTF.parent}/README.md")

    char_id = char_id or slugify(fbx_path.stem)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_glb = out_dir / f"mixamo_{char_id}.glb"
    out_stem = out_glb.with_suffix("")

    proc = subprocess.run(
        [str(FBX2GLTF), "--binary", "--input", str(fbx_path),
         "--output", str(out_stem)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not out_glb.exists():
        raise RuntimeError(
            f"FBX2glTF failed (exit {proc.returncode}):\n"
            f"{proc.stdout}\n{proc.stderr}")
    return out_glb


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fbx", type=Path, help="Path to the Mixamo FBX file")
    ap.add_argument("character_id", nargs="?",
                    help="Slug for the GLB filename + rigs.js id "
                         "(default: derived from the FBX basename)")
    args = ap.parse_args()

    try:
        out_glb = convert_fbx_to_glb(args.fbx, args.character_id)
    except Exception as e:
        sys.exit(f"error: {e}")
    char_id = args.character_id or slugify(args.fbx.stem)

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
