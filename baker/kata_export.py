#!/usr/bin/env python3
"""Export a kimodo kata tree → woid sidecar JSON.

The baker turns each move's motion into a kim_<slug> sequence, but it knows
nothing about the kata tree (continues_from), branch frames, or attack
hitboxes. This script bridges that: given the kata root and the id→slug map
used at bake time, it reads the kimodo store clips and emits a sidecar the
woid KataComboController loads.

Sidecar shape (Assets/kimodo/kata_<name>.json):
{
  "root": "kim_superman_punch",
  "moves": {
    "kim_superman_punch": {
      "id", "prompt", "num_frames", "fps",
      "parent": null|seq, "branch_frame": null|int,   # frame in PARENT we branched from
      "hitboxes": [ {jointA, jointB, radius_m, reach_m, start, end, damage, tags} ],
      "children": [ {"seq": "...", "branch_frame": 40}, ... ]   # sorted by branch_frame
    }, ...
  }
}

Distances stay in METERS (kimodo units); the C# side converts to world units.

Usage:
  python kata_export.py \
    --kimodo-dir ~/projects/kimodo/.kimodo-animations \
    --root 0d47f5469321 \
    --map 0d47f5469321:superman_punch,7cb5a0915b00:punch_right_hand,f980d71f994a:step_raise_knee \
    --prefix kim_ \
    --out ~/projects/sbox-public/examples/woid/Assets/kimodo/kata_superman.json
"""
import argparse
import json
from pathlib import Path


def load_clip(kdir: Path, clip_id: str) -> dict:
    p = kdir / f"{clip_id}.json"
    if not p.exists():
        raise SystemExit(f"clip not found: {p}")
    return json.loads(p.read_text())


def hb_out(h: dict) -> dict:
    return {
        "jointA": h.get("jointA"),
        "jointB": h.get("jointB"),
        "radius_m": float(h.get("radius", 0.08)),
        "reach_m": float(h.get("reach", 0.0)),
        "start": int(h.get("start", 0)),
        "end": int(h.get("end", 0)),
        "damage": float(h.get("damage", 25.0)),
        "tags": list(h.get("tags", [])),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kimodo-dir", required=True, type=Path)
    ap.add_argument("--root", required=True, help="root clip id (12-hex)")
    ap.add_argument("--map", required=True,
                    help="comma list of clipId:slug used at bake time")
    ap.add_argument("--prefix", default="kim_")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    id2slug = {}
    for pair in args.map.split(","):
        cid, slug = pair.split(":")
        id2slug[cid.strip()] = slug.strip()

    def seq_of(cid: str) -> str:
        if cid not in id2slug:
            raise SystemExit(f"clip {cid} is in the tree but missing from --map")
        return args.prefix + id2slug[cid]

    # All clips in the map form the kata; resolve parent/children via continues_from.
    clips = {cid: load_clip(args.kimodo_dir, cid) for cid in id2slug}
    moves = {}
    for cid, c in clips.items():
        cf = c.get("continues_from") or {}
        parent_id = cf.get("source_id")
        moves[seq_of(cid)] = {
            "id": cid,
            "prompt": c.get("prompt", ""),
            "num_frames": int(c.get("num_frames", 0)),
            "fps": float(c.get("fps", 30.0)),
            "parent": seq_of(parent_id) if parent_id in id2slug else None,
            "branch_frame": int(cf["frame"]) if parent_id in id2slug and cf.get("frame") is not None else None,
            "hitboxes": [hb_out(h) for h in (c.get("hitboxes") or [])],
            # Optional playback time-remap (DAW-envelope curve). Absent ⇒ linear.
            # points: [{x:realtime0..1, y:clip-pos0..1, c:segment curvature -1..1}]
            "timing": (c.get("timing") or {}).get("points") or None,
            "children": [],
        }

    # Fill children lists (sorted by branch frame so early→late maps to click timing).
    for seq, m in moves.items():
        kids = [
            {"seq": s, "branch_frame": mm["branch_frame"]}
            for s, mm in moves.items() if mm["parent"] == seq
        ]
        kids.sort(key=lambda k: (k["branch_frame"] is None, k["branch_frame"]))
        m["children"] = kids

    out = {"root": seq_of(args.root), "moves": moves}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2))
    print(f"[kata_export] wrote {args.out}")
    print(f"  root={out['root']}  moves={list(moves)}")
    for s, m in moves.items():
        kids = ", ".join(f"{k['seq']}@{k['branch_frame']}" for k in m["children"]) or "(leaf)"
        tw = f" tween={len(m['timing'])}pts" if m.get("timing") else ""
        print(f"   {s}: {m['num_frames']}f@{m['fps']} hb={len(m['hitboxes'])}{tw} -> {kids}")


if __name__ == "__main__":
    main()
