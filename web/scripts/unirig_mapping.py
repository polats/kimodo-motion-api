"""Derive a kimodo-skeleton joint mapping from a UniRig-rigged GLB.

UniRig emits anonymous bone names (`bone_0` … `bone_45`) but the topology
is reliably humanoid: a root with three children (spine + two legs), a
spine chain ending at a 3-child splitter (neck + two clavicles), and
4-bone chains for each arm and leg.

This script walks the GLB's skin.joints graph, identifies the SMPL-X-22
joints by topology + sign of x translation, and writes a JSON object
shaped like::

    {
      "pelvis":         "bone_0",
      "spine1":         "bone_1",
      ...
      "left_hip":       "bone_42",
      "right_hip":      "bone_38",
      ...
    }

The output drops directly into a kimodo character-registry record's
`mapping` field — the web frontend's `addCharacter()` accepts a literal
mapping object without going through `MAPPING_BUILDERS`.

Usage::

    python web/scripts/unirig_mapping.py path/to/rig.glb            # print to stdout
    python web/scripts/unirig_mapping.py path/to/rig.glb -o out.json
"""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path


# kimodo's 22-joint SMPL-X-style skeleton. Order matches rigs.js
# SMPLX_REST_WORLD comments. Sign convention from rigs.js:
#   +X = left side of the body, -X = right.
KIMODO_JOINTS = [
    "pelvis",
    "left_hip", "right_hip", "spine1",
    "left_knee", "right_knee", "spine2",
    "left_ankle", "right_ankle", "spine3",
    "left_foot", "right_foot",
    "neck", "left_collar", "right_collar", "head",
    "left_shoulder", "right_shoulder",
    "left_elbow", "right_elbow",
    "left_wrist", "right_wrist",
]


# ── GLB parsing ───────────────────────────────────────────────────────

def read_glb_json(glb_path: Path) -> dict:
    """Return the JSON chunk of a binary GLB. Raises on malformed input."""
    data = glb_path.read_bytes()
    if data[:4] != b"glTF":
        raise ValueError(f"{glb_path}: not a GLB (missing magic)")
    chunk_len = struct.unpack_from("<I", data, 12)[0]
    chunk_type = data[16:20]
    if chunk_type != b"JSON":
        raise ValueError(f"{glb_path}: first chunk is not JSON")
    return json.loads(data[20:20 + chunk_len])


# ── Graph helpers ─────────────────────────────────────────────────────

def _build_parent(nodes: list[dict]) -> dict[int, int]:
    parent: dict[int, int] = {}
    for ni, n in enumerate(nodes):
        for ci in n.get("children", []) or []:
            parent[ci] = ni
    return parent


def _x_of(node: dict) -> float:
    return (node.get("translation") or [0.0, 0.0, 0.0])[0]


def _walk_chain(nodes: list[dict], joint_set: set[int], start: int, length: int) -> list[int]:
    """Walk first-child chain from `start` through `length` nodes (inclusive).
    Stops short if a chain is broken or branches earlier than expected."""
    chain = [start]
    cur = start
    for _ in range(length - 1):
        kids = [c for c in (nodes[cur].get("children") or []) if c in joint_set]
        if not kids:
            break
        cur = kids[0]
        chain.append(cur)
    return chain


# ── Labeler ───────────────────────────────────────────────────────────

class TopologyError(RuntimeError):
    """Raised when the skin doesn't look like a humanoid SMPL-X-22 rig."""


def label_unirig_skeleton(gltf: dict) -> dict[str, str]:
    nodes = gltf.get("nodes", [])
    skins = gltf.get("skins", [])
    if not skins:
        raise TopologyError("GLB has no skins — is this a UniRig output?")
    skin = skins[0]
    joints: list[int] = skin.get("joints") or []
    if not joints:
        raise TopologyError("skin has no joints")
    joint_set = set(joints)
    parent = _build_parent(nodes)

    # Root: a joint whose parent is not a joint (typically the Armature).
    roots = [j for j in joints if parent.get(j) not in joint_set]
    if len(roots) != 1:
        raise TopologyError(f"expected exactly one skeleton root, found {len(roots)}")
    root = roots[0]

    # Pelvis has 3 joint children: spine + 2 legs. Spine has x ≈ 0; legs flank.
    root_kids = [c for c in (nodes[root].get("children") or []) if c in joint_set]
    if len(root_kids) != 3:
        raise TopologyError(
            f"root '{nodes[root].get('name')}' has {len(root_kids)} joint children "
            "(humanoid expected 3: spine + 2 legs)"
        )
    root_kids.sort(key=lambda c: abs(_x_of(nodes[c])))
    spine_root, *legs = root_kids
    legs.sort(key=lambda c: -_x_of(nodes[c]))  # +X first → "left" by kimodo convention.
    left_hip_n, right_hip_n = legs

    # Each leg: hip → knee → ankle → foot.
    l_chain = _walk_chain(nodes, joint_set, left_hip_n, 4)
    r_chain = _walk_chain(nodes, joint_set, right_hip_n, 4)
    if len(l_chain) != 4 or len(r_chain) != 4:
        raise TopologyError(
            f"leg chains too short: left={len(l_chain)}, right={len(r_chain)} "
            "(expected 4 bones each: hip/knee/ankle/foot)"
        )
    l_hip, l_knee, l_ankle, l_foot = l_chain
    r_hip, r_knee, r_ankle, r_foot = r_chain

    # Spine chain: walk first-child until we hit a node with 3 joint
    # children (the splitter that branches into neck + 2 collars).
    spine_chain: list[int] = [spine_root]
    cur = spine_root
    while True:
        kids = [c for c in (nodes[cur].get("children") or []) if c in joint_set]
        if len(kids) >= 3:
            break
        if not kids:
            raise TopologyError(
                f"spine chain ends without finding a 3-child splitter "
                f"(reached '{nodes[cur].get('name')}' after {len(spine_chain)} bones)"
            )
        cur = kids[0]
        spine_chain.append(cur)
    if len(spine_chain) != 3:
        raise TopologyError(
            f"spine chain has {len(spine_chain)} bones before the splitter "
            "(SMPL-X expects spine1 + spine2 + spine3)"
        )
    spine1, spine2, spine3 = spine_chain

    # The splitter (= spine3) has 3 children: neck + 2 collars.
    splitter_kids = [c for c in (nodes[spine3].get("children") or []) if c in joint_set]
    splitter_kids.sort(key=lambda c: abs(_x_of(nodes[c])))
    neck_n, *collars = splitter_kids
    collars.sort(key=lambda c: -_x_of(nodes[c]))  # +X = left.
    left_collar_n, right_collar_n = collars

    # Head: walk first-child off the neck (typically a single bone).
    neck_chain = _walk_chain(nodes, joint_set, neck_n, 2)
    if len(neck_chain) < 2:
        raise TopologyError("neck has no head child")
    neck, head = neck_chain[0], neck_chain[1]

    # Each arm: collar → shoulder → elbow → wrist. Wrist may have finger
    # children — we ignore those.
    l_arm = _walk_chain(nodes, joint_set, left_collar_n, 4)
    r_arm = _walk_chain(nodes, joint_set, right_collar_n, 4)
    if len(l_arm) != 4 or len(r_arm) != 4:
        raise TopologyError(
            f"arm chains too short: left={len(l_arm)}, right={len(r_arm)} "
            "(expected 4 bones: collar/shoulder/elbow/wrist)"
        )
    l_collar, l_shoulder, l_elbow, l_wrist = l_arm
    r_collar, r_shoulder, r_elbow, r_wrist = r_arm

    # Resolve to bone names.
    name = lambda i: nodes[i].get("name") or f"node_{i}"
    return {
        "pelvis":          name(root),
        "spine1":          name(spine1),
        "spine2":          name(spine2),
        "spine3":          name(spine3),
        "neck":            name(neck),
        "head":            name(head),
        "left_hip":        name(l_hip),
        "left_knee":       name(l_knee),
        "left_ankle":      name(l_ankle),
        "left_foot":       name(l_foot),
        "right_hip":       name(r_hip),
        "right_knee":      name(r_knee),
        "right_ankle":     name(r_ankle),
        "right_foot":      name(r_foot),
        "left_collar":     name(l_collar),
        "left_shoulder":   name(l_shoulder),
        "left_elbow":      name(l_elbow),
        "left_wrist":      name(l_wrist),
        "right_collar":    name(r_collar),
        "right_shoulder":  name(r_shoulder),
        "right_elbow":     name(r_elbow),
        "right_wrist":     name(r_wrist),
    }


def label_glb(glb_path: Path) -> dict[str, str]:
    return label_unirig_skeleton(read_glb_json(glb_path))


# ── CLI ───────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("glb", type=Path, help="UniRig-rigged GLB")
    ap.add_argument("-o", "--output", type=Path, default=None,
                    help="write mapping JSON here (default: stdout)")
    args = ap.parse_args()

    mapping = label_glb(args.glb)
    blob = json.dumps(mapping, indent=2)
    if args.output:
        args.output.write_text(blob + "\n")
        print(f"wrote {args.output}")
    else:
        print(blob)

    # Sanity report.
    missing = [j for j in KIMODO_JOINTS if j not in mapping]
    if missing:
        print(f"\nWARNING: missing joints: {missing}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
