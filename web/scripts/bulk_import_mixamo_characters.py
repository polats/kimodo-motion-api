"""Bulk-import a curated list of 30 diverse Mixamo characters via the
running motion API at $KIMODO_URL (default http://localhost:7862).

Characters span genders, themes, and visual styles. For each entry we
search Mixamo, take the first hit, and import it. Idempotent — skips any
character whose slug is already in the registry.

Run:
    python web/scripts/bulk_import_mixamo_characters.py
"""
from __future__ import annotations

import os
import sys
import time
import urllib.parse
import urllib.request
import json

KIMODO_URL = os.environ.get("KIMODO_URL", "http://localhost:7862")

# Curated 30: each is a Mixamo search query that returns a usable character.
QUERIES = [
    "Y Bot",
    "X Bot",
    "Mutant",
    "Maw",
    "Knight",
    "Paladin",
    "Warrior",
    "Soldier",
    "Swat",
    "Vanguard",
    "Zombie",
    "Mutant Wolf",
    "Skeleton",
    "Bug Mutant",
    "Pumpkinhulk",
    "Robot",
    "Brute",
    "Big Vegas",
    "Erika",
    "Kachujin",
    "Michelle",
    "Megan",
    "Maria",
    "Amy",
    "Castle Guard",
    "Pirate",
    "Ninja",
    "Boy",
    "Goblin",
    "Mannequin",
    "Ely",
    "Jolleen",
    "Liam",
    "Adam",
    "Doozy",
    "Suit",
    "Whiteclown",
    "Warzombie",
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


def existing_slugs() -> set[str]:
    try:
        data = _http_get_json(f"{KIMODO_URL}/characters")
    except Exception as e:
        print(f"warn: GET /characters failed: {e}")
        return set()
    return {c["id"] for c in data.get("characters", [])}


def slugify(name: str) -> str:
    s = "".join(c if c.isalnum() else "_" for c in name.lower())
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")


def main():
    have = existing_slugs()
    print(f"Already in registry: {len(have)}")
    n_added = n_skipped = n_failed = 0
    for q in QUERIES:
        try:
            url = f"{KIMODO_URL}/mixamo/search?q={urllib.parse.quote(q)}&limit=1"
            results = _http_get_json(url).get("results", [])
            if not results:
                print(f"  ✗ no result for '{q}'")
                n_failed += 1
                continue
            r = results[0]
            anticipated_id = f"mixamo_{slugify(r['name'])}"
            if anticipated_id in have:
                print(f"  - skip {r['name']} (already imported)")
                n_skipped += 1
                continue
            print(f"  → importing {r['name']} ...", end=" ", flush=True)
            cfg = _http_post_json(
                f"{KIMODO_URL}/mixamo/import",
                {"id": r["id"], "name": r["name"]})
            have.add(cfg["id"])
            print(f"OK ({cfg['id']})")
            n_added += 1
        except Exception as e:
            print(f"  ✗ {q}: {e}")
            n_failed += 1
        time.sleep(1)  # be polite to Mixamo
    print(f"\nAdded {n_added}, skipped {n_skipped}, failed {n_failed}.")


if __name__ == "__main__":
    main()
