"""Mixamo API proxy for the web frontend.

Mixamo's API requires a per-user Bearer token (grabbed from localStorage
after logging in to mixamo.com). It also blocks browser-origin requests
(no CORS), so the frontend can't talk to mixamo.com directly. This module
runs server-side: it reads MIXAMO_TOKEN from env (or .env / ~/.mixamo_token),
proxies search + character download, runs FBX2glTF on the result, and
returns the new character's GLB path so the frontend can register it.

Token bootstrap (one-time per user):
  1. Log in at https://www.mixamo.com
  2. Browser DevTools → Console:
       JSON.parse(localStorage.getItem("persist:root")).access_token.replace(/"/g,'')
  3. Save to .env: `MIXAMO_TOKEN=<token>`
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
TOKEN_ENV = "MIXAMO_TOKEN"
TOKEN_FILE = Path.home() / ".mixamo_token"
ENV_FILE = REPO_ROOT / ".env"

FBX2GLTF = REPO_ROOT / "web" / "scripts" / "tools" / "FBX2glTF"
GLB_OUT_DIR = REPO_ROOT / "web" / "public" / "models"
ANIM_OUT_DIR = REPO_ROOT / "web" / "public" / "animations" / "mixamo"

API_BASE = "https://www.mixamo.com/api/v1"
API_KEY = "mixamo2"
# Mixamo's "no character" reference body. Used as character_id when exporting
# character meshes, since the export endpoint requires one.
DEFAULT_CHARACTER = "4f5d21e1-4ccc-41f1-b35b-fb2547bd8493"  # Y Bot


class MixamoError(Exception):
    pass


def _token() -> str:
    tok = os.environ.get(TOKEN_ENV, "").strip()
    if tok:
        return tok
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith(f"{TOKEN_ENV}="):
                tok = line.split("=", 1)[1].strip().strip('"').strip("'")
                if tok:
                    return tok
    if TOKEN_FILE.exists():
        tok = TOKEN_FILE.read_text().strip().strip('"')
        if tok:
            return tok
    raise MixamoError(
        f"{TOKEN_ENV} not set. Grab a token from mixamo.com (DevTools → "
        f"Console → JSON.parse(localStorage.getItem('persist:root'))"
        f".access_token) and put it in .env or ~/.mixamo_token"
    )


def _api_get(path: str) -> dict:
    url = path if path.startswith("http") else f"{API_BASE}/{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {_token()}",
        "X-Api-Key": API_KEY,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:300]
        if e.code == 401:
            raise MixamoError("Mixamo token expired or invalid")
        raise MixamoError(f"Mixamo API HTTP {e.code}: {body}")


def _api_post(path: str, payload: dict) -> dict:
    url = f"{API_BASE}/{path}"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {_token()}",
        "X-Api-Key": API_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:300]
        if e.code == 401:
            raise MixamoError("Mixamo token expired or invalid")
        raise MixamoError(f"Mixamo API HTTP {e.code}: {body}")


def search_characters(query: str, limit: int = 24) -> list[dict]:
    """Search the Mixamo character catalog.

    Returns a thin list of {id, name, thumbnail} for the frontend.
    """
    q = urllib.parse.quote(query)
    data = _api_get(f"products?page=1&limit={limit}&type=Character&query={q}")
    out = []
    for p in data.get("results", []):
        out.append({
            "id": p["id"],
            "name": p.get("name", p["id"]),
            "thumbnail": p.get("thumbnail"),
        })
    return out


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", name.lower())
    return s.strip("_") or "character"


def _download_character_fbx(character_id: str, name: str, out_dir: Path) -> Path:
    """Trigger Mixamo's export job for a character T-pose, poll until ready,
    download the FBX and return the path."""
    # Tell Mixamo to package this character as a T-pose FBX.
    _api_post("animations/export", {
        "character_id": character_id,
        "type": "Character",
        "preferences": {
            "format": "fbx7_2019",
            "skin": "true",
            "fps": "30",
            "reducekf": "0",
            "pose": "tpose",
        },
        "product_name": name,
    })
    # Poll for job completion. Mixamo serves one job per character at a time.
    for _ in range(30):
        time.sleep(2)
        status = _api_get(f"characters/{character_id}/monitor")
        if status.get("status") == "completed":
            url = status.get("job_result")
            if not url:
                raise MixamoError("Mixamo export completed without a download URL")
            out_dir.mkdir(parents=True, exist_ok=True)
            slug = _slugify(name)
            fbx_path = out_dir / f"{slug}.fbx"
            with urllib.request.urlopen(url, timeout=120) as resp:
                fbx_path.write_bytes(resp.read())
            return fbx_path
        if status.get("status") == "failed":
            msg = status.get("job_result", {}).get("message", "unknown")
            raise MixamoError(f"Mixamo export failed: {msg}")
    raise MixamoError("Mixamo export timed out")


def _convert_fbx_to_glb(fbx_path: Path, slug: str) -> Path:
    if not FBX2GLTF.exists():
        raise MixamoError(
            f"FBX2glTF binary missing at {FBX2GLTF}; "
            f"see {FBX2GLTF.parent}/README.md")
    GLB_OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_glb = GLB_OUT_DIR / f"mixamo_{slug}.glb"
    out_stem = out_glb.with_suffix("")
    proc = subprocess.run(
        [str(FBX2GLTF), "--binary", "--input", str(fbx_path),
         "--output", str(out_stem)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not out_glb.exists():
        raise MixamoError(
            f"FBX2glTF failed (exit {proc.returncode}): "
            f"{proc.stderr[:300] or proc.stdout[:300]}")
    return out_glb


def import_character(character_id: str, name: str,
                     fbx_dir: Optional[Path] = None) -> dict:
    """End-to-end: download FBX from Mixamo, run FBX2glTF, return character
    config the frontend can append to its CHARACTERS array.
    """
    fbx_dir = fbx_dir or (REPO_ROOT / ".mixamo-cache")
    fbx_path = _download_character_fbx(character_id, name, fbx_dir)
    slug = _slugify(name)
    glb_path = _convert_fbx_to_glb(fbx_path, slug)
    return {
        "id": f"mixamo_{slug}",
        "label": f"{name} (Mixamo)",
        "url": f"/models/{glb_path.name}",
        "skinned": True,
        "mappingKind": "mixamo",
    }


# ---------------------------------------------------------------------------
# Animations
# ---------------------------------------------------------------------------

def search_motions(query: str, limit: int = 24) -> list[dict]:
    """Search the Mixamo motion catalog (animations rigged on the standard
    Mixamo skeleton). Returns a thin list of {id, name, thumbnail}."""
    q = urllib.parse.quote(query)
    data = _api_get(f"products?page=1&limit={limit}&type=Motion&query={q}")
    return [{
        "id": p["id"],
        "name": p.get("name", p["id"]),
        "thumbnail": p.get("thumbnail"),
    } for p in data.get("results", [])]


def _build_gms_hash(raw: dict) -> dict:
    """Normalize the gms_hash payload Mixamo expects in /animations/export.

    Mixamo's API returns each motion's `details.gms_hash` shape with a
    `params` *array* of [name, default] pairs. The export endpoint wants
    a flattened payload with each param's default value in a parallel
    "params" string of comma-separated numbers, plus `overdrive` lifted
    to a top-level int. Catacombs hard-codes `params: "0,0,0"`; we
    preserve the actual default values so non-trivial motions (which
    have e.g. Speed/Energy params) export cleanly.
    """
    trim = raw.get("trim", [0, 100])
    raw_params = raw.get("params") or []
    if isinstance(raw_params, list):
        # Drop "Overdrive" from the params array since it goes top-level.
        non_overdrive = [p for p in raw_params
                         if not (isinstance(p, list) and len(p) == 2
                                 and isinstance(p[0], str)
                                 and p[0].lower() == "overdrive")]
        param_str = ",".join(str(p[1]) for p in non_overdrive
                             if isinstance(p, list) and len(p) == 2)
    else:
        param_str = str(raw_params)
    return {
        "model-id": raw["model-id"],
        "mirror": raw.get("mirror", False),
        "trim": [int(trim[0]), int(trim[1])],
        "overdrive": 0,
        # Empty string is the correct value when the motion has no
        # parameters (e.g. plain "Walking"). The catacombs script's
        # hardcoded "0,0,0" rejects with "Error while generating animation"
        # against current Mixamo because it implies 3 non-existent params.
        "params": param_str,
        "arm-space": raw.get("arm-space", 0),
        "inplace": raw.get("inplace", False),
    }


def _download_motion_fbx(motion_id: str, name: str, character_id: str,
                         out_dir: Path, *, with_skin: bool = True) -> Path:
    """Trigger a Mixamo animation export job rigged on `character_id`,
    poll until ready, download the FBX. Returns the local path."""
    details = _api_get(
        f"products/{motion_id}?similar=0&character_id={character_id}")
    raw = details.get("details", {}).get("gms_hash")
    if not raw:
        raise MixamoError(f"motion {motion_id} has no gms_hash")
    _api_post("animations/export", {
        "gms_hash": [_build_gms_hash(raw)],
        "preferences": {
            "format": "fbx7_2019",
            # `skin: true` includes the mesh in the export, which is what we
            # want for the standalone Mixamo-animation GLBs (a Mixamo skeleton
            # alone won't load in three.js without a SkinnedMesh).
            "skin": "true" if with_skin else "false",
            "fps": "30",
            "reducekf": "0",
        },
        "character_id": character_id,
        "type": "Motion",
        "product_name": name,
    })
    for _ in range(30):
        time.sleep(2)
        status = _api_get(f"characters/{character_id}/monitor")
        if status.get("status") == "completed":
            url = status.get("job_result")
            if not url:
                raise MixamoError("Mixamo export completed without download URL")
            out_dir.mkdir(parents=True, exist_ok=True)
            slug = _slugify(name)
            fbx_path = out_dir / f"motion_{slug}.fbx"
            with urllib.request.urlopen(url, timeout=120) as resp:
                fbx_path.write_bytes(resp.read())
            return fbx_path
        if status.get("status") == "failed":
            msg = status.get("job_result", {}).get("message", "unknown")
            raise MixamoError(f"Mixamo motion export failed: {msg}")
    raise MixamoError("Mixamo motion export timed out")


def _convert_motion_fbx_to_glb(fbx_path: Path, slug: str) -> Path:
    if not FBX2GLTF.exists():
        raise MixamoError(
            f"FBX2glTF binary missing at {FBX2GLTF}; "
            f"see {FBX2GLTF.parent}/README.md")
    ANIM_OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_glb = ANIM_OUT_DIR / f"{slug}.glb"
    out_stem = out_glb.with_suffix("")
    proc = subprocess.run(
        [str(FBX2GLTF), "--binary",
         "--input", str(fbx_path),
         "--output", str(out_stem),
         # Keep one animation track per file. Animations are the *point*.
         "--anim-framerate", "bake30",
         # Don't compress vertex data; the file is already small.
         "--khr-materials-unlit"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not out_glb.exists():
        # Retry without the unlit flag — older FBX2glTF builds reject it.
        proc = subprocess.run(
            [str(FBX2GLTF), "--binary",
             "--input", str(fbx_path),
             "--output", str(out_stem)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0 or not out_glb.exists():
            raise MixamoError(
                f"FBX2glTF failed (exit {proc.returncode}): "
                f"{proc.stderr[:300] or proc.stdout[:300]}")
    return out_glb


def import_motion(motion_id: str, name: str,
                  character_id: str = DEFAULT_CHARACTER,
                  fbx_dir: Optional[Path] = None) -> dict:
    """End-to-end: download motion FBX, run FBX2glTF preserving animation
    tracks, return a config the frontend can register as a Mixamo
    animation."""
    fbx_dir = fbx_dir or (REPO_ROOT / ".mixamo-cache" / "motions")
    fbx_path = _download_motion_fbx(motion_id, name, character_id, fbx_dir)
    slug = _slugify(name)
    glb_path = _convert_motion_fbx_to_glb(fbx_path, slug)
    return {
        "id": f"mixamo_anim_{slug}",
        "label": name,
        "url": f"/animations/mixamo/{glb_path.name}",
        "source": "mixamo",
        "source_id": motion_id,
        "skeleton": "mixamo",
    }
