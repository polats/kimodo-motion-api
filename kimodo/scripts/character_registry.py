"""Disk-backed registry of imported characters (Mixamo etc.).

The web frontend ships with a static CHARACTERS list (built-ins in
rigs.js). Anything imported at runtime — Mixamo characters, future
sources — lives here so it persists across reloads, devices, and
clients without depending on browser localStorage.

Format: one JSON file per record under KIMODO_CHARACTERS_PATH (default
./.kimodo-characters/). Each record is the character config the
frontend expects, plus bookkeeping fields (id, source, created_at).

A "config" looks like:
    {
      "id": "mixamo_zombiegirl_w_kurniawan",
      "label": "Zombiegirl W Kurniawan (Mixamo)",
      "url": "/models/mixamo_zombiegirl_w_kurniawan.glb",
      "skinned": true,
      "mappingKind": "mixamo",
      "source": "mixamo",
      "source_id": "<mixamo product id>",
      "created_at": 1700000000
    }
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional


def _default_root() -> Path:
    return Path(os.environ.get("KIMODO_CHARACTERS_PATH", ".kimodo-characters"))


class CharacterRegistry:
    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root or _default_root())
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, char_id: str) -> Path:
        # Defensive: only allow safe id characters in path components.
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in char_id)
        return self.root / f"{safe}.json"

    def save(self, config: dict[str, Any]) -> dict[str, Any]:
        if "id" not in config:
            raise ValueError("character config requires an 'id'")
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

    def get(self, char_id: str) -> Optional[dict[str, Any]]:
        p = self._path(char_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except Exception:
            return None

    def delete(self, char_id: str) -> bool:
        p = self._path(char_id)
        if p.exists():
            p.unlink()
            return True
        return False
