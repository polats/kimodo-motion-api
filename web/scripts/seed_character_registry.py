"""Seed the on-disk character registry from GLBs already in
web/public/models/. Useful one-time after switching from the localStorage
persistence to the server-side registry, or to recover state.

For Mixamo GLBs (filename mixamo_*.glb) we register the file with the
mixamo mappingKind. The label is reconstructed from the slug.

Run:
    python web/scripts/seed_character_registry.py
"""
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from kimodo.scripts.character_registry import CharacterRegistry  # noqa: E402

MODELS = REPO_ROOT / "web" / "public" / "models"


def label_from_slug(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.split("_"))


def main():
    reg = CharacterRegistry()
    print(f"Registry root: {reg.root}")
    n_added = n_skipped = 0
    for glb in sorted(MODELS.glob("mixamo_*.glb")):
        slug = glb.stem[len("mixamo_"):]
        char_id = f"mixamo_{slug}"
        if reg.get(char_id):
            n_skipped += 1
            continue
        reg.save({
            "id": char_id,
            "label": f"{label_from_slug(slug)} (Mixamo)",
            "url": f"/models/{glb.name}",
            "skinned": True,
            "mappingKind": "mixamo",
            "source": "mixamo",
        })
        print(f"  + {char_id}")
        n_added += 1
    print(f"Done. Added {n_added}, skipped {n_skipped}.")


if __name__ == "__main__":
    main()
