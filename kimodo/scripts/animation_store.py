# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Persistence for generated motion clips.

Two backends:
  - LocalFsStore: writes one JSON file per record under a directory.
  - GcsStore: same, but in a Google Cloud Storage bucket.

Selection is driven by env: set KIMODO_GCS_BUCKET to use GCS, otherwise local fs
(KIMODO_STORE_PATH or ./.kimodo-animations/).

Records are JSON; small enough (~100 KB per 5 s clip) that JSON is fine. We split
metadata from the heavy quaternion payload so listing endpoints can return cheaply.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional, Protocol


_META_FIELDS = ("id", "prompt", "seconds", "fps", "num_frames", "model", "created_at", "continues_from", "hitboxes", "heading_offset")


def _split_meta(record: dict[str, Any]) -> dict[str, Any]:
    return {k: record[k] for k in _META_FIELDS if k in record}


class Store(Protocol):
    def save(self, record: dict[str, Any]) -> str: ...
    def list(self) -> list[dict[str, Any]]: ...
    def get(self, id: str) -> Optional[dict[str, Any]]: ...
    def delete(self, id: str) -> bool: ...


class LocalFsStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, id: str) -> Path:
        return self.root / f"{id}.json"

    def save(self, record: dict[str, Any]) -> str:
        if "id" not in record:
            record = {**record, "id": uuid.uuid4().hex[:12]}
        if "created_at" not in record:
            record["created_at"] = int(time.time())
        self._path(record["id"]).write_text(json.dumps(record))
        return record["id"]

    def list(self) -> list[dict[str, Any]]:
        out = []
        for p in sorted(self.root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                rec = json.loads(p.read_text())
            except Exception:
                continue
            out.append(_split_meta(rec))
        return out

    def get(self, id: str) -> Optional[dict[str, Any]]:
        p = self._path(id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except Exception:
            return None

    def delete(self, id: str) -> bool:
        p = self._path(id)
        if p.exists():
            p.unlink()
            return True
        return False


class GcsStore:
    """Lazily imports google-cloud-storage so the API still loads if it's missing."""

    def __init__(self, bucket_name: str, prefix: str = "animations/"):
        from google.cloud import storage  # type: ignore

        self._client = storage.Client()
        self._bucket = self._client.bucket(bucket_name)
        self._prefix = prefix.rstrip("/") + "/"

    def _blob(self, id: str):
        return self._bucket.blob(f"{self._prefix}{id}.json")

    def save(self, record: dict[str, Any]) -> str:
        if "id" not in record:
            record = {**record, "id": uuid.uuid4().hex[:12]}
        if "created_at" not in record:
            record["created_at"] = int(time.time())
        self._blob(record["id"]).upload_from_string(
            json.dumps(record),
            content_type="application/json",
        )
        return record["id"]

    def list(self) -> list[dict[str, Any]]:
        # Listing requires reading each blob (we want metadata, not the heavy payload).
        # For a small portfolio app this is fine; if records grow, switch to a manifest blob.
        blobs = list(self._client.list_blobs(self._bucket, prefix=self._prefix))
        records = []
        for b in blobs:
            try:
                rec = json.loads(b.download_as_bytes())
            except Exception:
                continue
            records.append(_split_meta(rec))
        records.sort(key=lambda r: r.get("created_at", 0), reverse=True)
        return records

    def get(self, id: str) -> Optional[dict[str, Any]]:
        b = self._blob(id)
        if not b.exists():
            return None
        try:
            return json.loads(b.download_as_bytes())
        except Exception:
            return None

    def delete(self, id: str) -> bool:
        b = self._blob(id)
        if not b.exists():
            return False
        b.delete()
        return True


def make_store() -> Store:
    bucket = os.environ.get("KIMODO_GCS_BUCKET", "").strip()
    if bucket:
        return GcsStore(bucket)
    path = Path(os.environ.get("KIMODO_STORE_PATH", "./.kimodo-animations"))
    return LocalFsStore(path)
