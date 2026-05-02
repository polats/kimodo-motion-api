"""Disk-backed registry of *imported* animations (Mixamo etc.).

Distinct from kimodo's `animation_store` — that one stores text-to-motion
results from the model. This one stores externally-imported animations
that the frontend plays via three.js' AnimationMixer (e.g. Mixamo
animation GLBs that drive Mixamo characters directly, no retargeting).

Format mirrors CharacterRegistry: one JSON file per record under
KIMODO_ANIMATIONS_PATH (default ./.kimodo-mixamo-animations/).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional


def _default_root() -> Path:
    return Path(os.environ.get(
        "KIMODO_MIXAMO_ANIMATIONS_PATH", ".kimodo-mixamo-animations"))


class MixamoAnimationRegistry:
    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root or _default_root())
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, anim_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in anim_id)
        return self.root / f"{safe}.json"

    def save(self, config: dict[str, Any]) -> dict[str, Any]:
        if "id" not in config:
            raise ValueError("animation config requires an 'id'")
        if "created_at" not in config:
            config = {**config, "created_at": int(time.time())}
        self._path(config["id"]).write_text(json.dumps(config))
        return config

    def list(self) -> list[dict[str, Any]]:
        out = []
        for p in sorted(self.root.glob("*.json"),
                        key=lambda p: p.stat().st_mtime):
            try:
                out.append(json.loads(p.read_text()))
            except Exception:
                continue
        return out

    def get(self, anim_id: str) -> Optional[dict[str, Any]]:
        p = self._path(anim_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except Exception:
            return None

    def delete(self, anim_id: str) -> bool:
        p = self._path(anim_id)
        if p.exists():
            p.unlink()
            return True
        return False
