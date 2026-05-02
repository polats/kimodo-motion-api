"""Generate 30 kimodo text-to-motion clips and store them via the
existing /generate endpoint. Idempotent: skips a prompt if a record with
the same prompt + duration already exists in the store.

Run:
    python web/scripts/bulk_generate_kimodo_animations.py
"""
from __future__ import annotations

import os
import sys
import time
import urllib.request
import json

KIMODO_URL = os.environ.get("KIMODO_URL", "http://localhost:7862")

# 30 prompts spanning common in-game motions: locomotion, gestures,
# dance, sports, transitions. Tweak as you like — duplicates of an
# existing (prompt, seconds) tuple are auto-skipped.
PROMPTS = [
    ("a person walks forward", 4.0),
    ("a person walks backward", 4.0),
    ("a person runs forward", 3.0),
    ("a person sprints", 3.0),
    ("a person jogs in a circle", 5.0),
    ("a person jumps in place", 3.0),
    ("a person hops on one leg", 3.0),
    ("a person crouches and stands up", 3.0),
    ("a person sits on the ground", 4.0),
    ("a person lies down then stands back up", 5.0),
    ("a person waves with their right hand", 3.0),
    ("a person waves with both hands", 3.0),
    ("a person claps their hands", 3.0),
    ("a person points forward", 3.0),
    ("a person bows respectfully", 3.0),
    ("a person shrugs their shoulders", 3.0),
    ("a person dances happily", 5.0),
    ("a person breakdances", 5.0),
    ("a person does a salsa dance", 5.0),
    ("a person does the floss dance", 4.0),
    ("a person does jumping jacks", 4.0),
    ("a person does pushups", 4.0),
    ("a person stretches their arms above their head", 4.0),
    ("a person throws a ball overhand", 3.0),
    ("a person kicks forward", 3.0),
    ("a person punches with their right fist", 3.0),
    ("a person blocks an incoming attack", 3.0),
    ("a person trips and falls forward", 3.0),
    ("a person tiptoes carefully", 4.0),
    ("a person celebrates with arms raised", 3.0),
]


def _http_get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read())


def _http_post_json(url: str, body: dict, timeout: int = 600) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def existing_signatures() -> set[tuple[str, float]]:
    try:
        d = _http_get_json(f"{KIMODO_URL}/animations")
    except Exception:
        return set()
    out = set()
    for a in d.get("animations", []):
        if "prompt" in a and "seconds" in a:
            # Round to one decimal so 4.0 == 4.0001.
            out.add((a["prompt"].strip(), round(float(a["seconds"]), 1)))
    return out


def main():
    have = existing_signatures()
    print(f"Already in animation store: {len(have)} clips")
    n_added = n_skipped = n_failed = 0
    for prompt, seconds in PROMPTS:
        sig = (prompt.strip(), round(seconds, 1))
        if sig in have:
            print(f"  - skip: {prompt}")
            n_skipped += 1
            continue
        print(f"  → generating: {prompt} ({seconds}s) ...", end=" ", flush=True)
        t0 = time.time()
        try:
            r = _http_post_json(
                f"{KIMODO_URL}/generate",
                {"prompt": prompt, "seconds": seconds})
            print(f"OK ({time.time()-t0:.1f}s, {r.get('num_frames')} frames)")
            n_added += 1
        except Exception as e:
            print(f"FAILED ({e})")
            n_failed += 1
    print(f"\nAdded {n_added}, skipped {n_skipped}, failed {n_failed}.")


if __name__ == "__main__":
    main()
