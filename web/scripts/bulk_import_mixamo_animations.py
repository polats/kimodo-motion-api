"""Bulk-import 30 Mixamo animations from the Sims-style list
(SIMS_ANIMATIONS.md). Idempotent.

Run:
    python web/scripts/bulk_import_mixamo_animations.py
"""
from __future__ import annotations

import os
import sys
import time
import urllib.parse
import urllib.request
import json

KIMODO_URL = os.environ.get("KIMODO_URL", "http://localhost:7862")

# Top 30 from SIMS_ANIMATIONS.md, with the Mixamo search query each one
# resolves to. Slug is what gets used as the registry id (after a
# `mixamo_anim_` prefix the server adds).
ENTRIES = [
    ("idle",                "Idle"),
    ("idle_breathing",      "Breathing Idle"),
    ("idle_happy",          "Happy Idle"),
    ("walk",                "Walking"),
    ("walk_slow",           "Slow Walk"),
    ("walk_strut",          "Strut Walking"),
    ("walk_in_place",       "Walking In Place"),
    ("run",                 "Running"),
    ("run_in_place",        "Running In Place"),
    ("jump",                "Jumping"),
    ("sit_idle",            "Sitting Idle"),
    ("sit_to_stand",        "Stand To Sit"),
    ("sleep_idle",          "Sleeping Idle"),
    ("eating",              "Eating"),
    ("drinking",            "Drinking"),
    ("wave_hello",          "Waving"),
    ("clap",                "Clapping"),
    ("laugh",               "Laughing"),
    ("cheer",               "Cheering"),
    ("cry",                 "Crying"),
    ("shrug",               "Shrugging"),
    ("nod",                 "Nodding"),
    ("shake_head",          "Shaking Head No"),
    ("talking",             "Talking"),
    ("dance_hip_hop",       "Hip Hop Dancing"),
    ("dance_salsa",         "Salsa Dancing"),
    ("read_book",           "Reading Book"),
    ("typing",              "Typing"),
    ("falling",             "Falling"),
    ("death",               "Death"),
]


def _http_get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read())


def _http_post_json(url: str, body: dict, timeout: int = 180) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def existing_ids() -> set[str]:
    try:
        d = _http_get_json(f"{KIMODO_URL}/mixamo/animations")
    except Exception:
        return set()
    return {a["id"] for a in d.get("animations", [])}


def slugify(name: str) -> str:
    s = "".join(c if c.isalnum() else "_" for c in name.lower())
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")


def main():
    have = existing_ids()
    print(f"Already in registry: {len(have)}")
    n_added = n_skipped = n_failed = 0

    for slug, query in ENTRIES:
        try:
            url = f"{KIMODO_URL}/mixamo/animations/search?q={urllib.parse.quote(query)}&limit=3"
            results = _http_get_json(url).get("results", [])
            if not results:
                print(f"  ✗ no result for '{query}'")
                n_failed += 1
                continue
            r = results[0]
            anticipated_id = f"mixamo_anim_{slugify(r['name'])}"
            if anticipated_id in have:
                print(f"  - skip {r['name']} (already imported)")
                n_skipped += 1
                continue
            print(f"  → importing {r['name']} ...", end=" ", flush=True)
            cfg = _http_post_json(
                f"{KIMODO_URL}/mixamo/animations/import",
                {"id": r["id"], "name": r["name"]})
            have.add(cfg["id"])
            print(f"OK ({cfg['id']})")
            n_added += 1
        except Exception as e:
            # Mixamo motion exports flake; try the second result if the first fails.
            if 'results' in dir() and len(results) > 1:
                try:
                    r2 = results[1]
                    print(f"  retry with {r2['name']} ...", end=" ", flush=True)
                    cfg = _http_post_json(
                        f"{KIMODO_URL}/mixamo/animations/import",
                        {"id": r2["id"], "name": r2["name"]})
                    have.add(cfg["id"])
                    print(f"OK ({cfg['id']})")
                    n_added += 1
                    continue
                except Exception as e2:
                    e = e2
            print(f"  ✗ {query}: {e}")
            n_failed += 1
        time.sleep(1)

    print(f"\nAdded {n_added}, skipped {n_skipped}, failed {n_failed}.")


if __name__ == "__main__":
    main()
