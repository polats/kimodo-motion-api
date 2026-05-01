# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bake SMPL-X neutral mesh + 22-body-joint rig into a standalone GLB.

The output's bone names and ordering match `kimodo.skeleton.SMPLXSkeleton22`,
so kimodo motion outputs (`local_rot_mats[t, j]`) can be applied bone-for-bone
with no name mapping or rest-pose alignment.

Usage:
    python -m kimodo.scripts.export_smplx_glb \
        --smplx-npz kimodo/assets/skeletons/smplx22/SMPLX_NEUTRAL.npz \
        --output assets/smplx_neutral.glb

Requires SMPLX_NEUTRAL.npz from https://smpl-x.is.tue.mpg.de/ (license required).
"""

import argparse
import json
import struct
import warnings
from pathlib import Path

import numpy as np

from kimodo.skeleton.definitions import SMPLXSkeleton22
from kimodo.viz.smplx_skin import SMPLX_BODY_JOINT_NAME_MAP


def _load_npz(path: Path) -> dict:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=Warning)
        with np.load(path, allow_pickle=True) as f:
            return {k: f[k] for k in f.files}


def _collapse_weights(weights_full: np.ndarray, parents_full: np.ndarray, body_indices: np.ndarray) -> np.ndarray:
    """Fold weights from the full SMPL-X joint set (55) into the 22 body joints.

    For every full joint, walk up parents until we hit one in body_indices, and
    accumulate that column into the body joint's column. This means hand and
    face joint influence ends up on the wrist or head respectively.
    """
    n_verts, n_full = weights_full.shape
    n_body = body_indices.shape[0]
    full_to_body_col = np.full(n_full, -1, dtype=np.int64)
    body_index_to_col = {int(j): c for c, j in enumerate(body_indices)}

    for j in range(n_full):
        cur = j
        while cur != -1 and cur not in body_index_to_col:
            cur = int(parents_full[cur])
        if cur == -1:
            full_to_body_col[j] = body_index_to_col[int(body_indices[0])]
        else:
            full_to_body_col[j] = body_index_to_col[cur]

    out = np.zeros((n_verts, n_body), dtype=np.float32)
    for j in range(n_full):
        out[:, full_to_body_col[j]] += weights_full[:, j]
    return out


def _topk_weights(weights: np.ndarray, k: int = 4):
    """Per vertex, keep top-k joint weights and renormalize. Pad joint 0 if fewer than k."""
    n_verts, n_joints = weights.shape
    if n_joints <= k:
        idx = np.tile(np.arange(n_joints, dtype=np.uint16), (n_verts, 1))
        w = weights.astype(np.float32)
        idx = np.pad(idx, ((0, 0), (0, k - n_joints)), constant_values=0)
        w = np.pad(w, ((0, 0), (0, k - n_joints)), constant_values=0.0)
    else:
        idx = np.argpartition(-weights, k, axis=1)[:, :k]
        rows = np.arange(n_verts)[:, None]
        w = weights[rows, idx]
        order = np.argsort(-w, axis=1)
        idx = idx[rows, order].astype(np.uint16)
        w = w[rows, order].astype(np.float32)

    sums = w.sum(axis=1, keepdims=True)
    sums[sums < 1e-8] = 1.0
    w = w / sums
    return idx, w


def _vertex_normals(positions: np.ndarray, faces: np.ndarray) -> np.ndarray:
    n = np.zeros_like(positions)
    v = positions[faces]
    fn = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
    fn /= np.clip(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12, None)
    for i in range(3):
        np.add.at(n, faces[:, i], fn)
    n /= np.clip(np.linalg.norm(n, axis=1, keepdims=True), 1e-12, None)
    return n.astype(np.float32)


def _pack_chunk(data: bytes, kind: bytes) -> bytes:
    pad_byte = b" " if kind == b"JSON" else b"\x00"
    pad = (4 - (len(data) % 4)) % 4
    data = data + pad_byte * pad
    return struct.pack("<II", len(data), int.from_bytes(kind, "little")) + data


def export(smplx_npz: Path, output: Path, shape_pcas: int = 10, expression_pcas: int = 10) -> None:
    smplx = _load_npz(smplx_npz)
    v_template = np.asarray(smplx["v_template"], dtype=np.float32)
    faces = np.asarray(smplx["f"], dtype=np.uint32)
    weights_full = np.asarray(smplx["weights"], dtype=np.float32)
    j_regressor = np.asarray(smplx["J_regressor"], dtype=np.float32)
    kintree = np.asarray(smplx["kintree_table"], dtype=np.int64)
    parents_full = kintree[0].copy()
    parents_full[parents_full > 1_000_000_000] = -1

    shapedirs = np.asarray(smplx["shapedirs"], dtype=np.float32)  # [V, 3, 400]
    n_shape_total = 300
    if shape_pcas > n_shape_total:
        raise ValueError(f"shape_pcas={shape_pcas} > {n_shape_total}")
    if expression_pcas > shapedirs.shape[2] - n_shape_total:
        raise ValueError(f"expression_pcas={expression_pcas} > {shapedirs.shape[2] - n_shape_total}")

    joint2num = smplx["joint2num"]
    if isinstance(joint2num, np.ndarray):
        joint2num = joint2num.item()

    bone_order = SMPLXSkeleton22.bone_order_names_with_parents
    body_smplx_indices = np.array(
        [int(joint2num[SMPLX_BODY_JOINT_NAME_MAP[name]]) for name, _ in bone_order],
        dtype=np.int64,
    )

    # Rest joint positions in world space (neutral betas).
    joints_world = (j_regressor @ v_template)[body_smplx_indices].astype(np.float32)

    # Per-bone local translation = world position minus parent's world position.
    name_to_idx = {name: i for i, (name, _) in enumerate(bone_order)}
    parents_22 = np.array(
        [name_to_idx[parent] if parent is not None else -1 for _, parent in bone_order],
        dtype=np.int64,
    )
    local_translations = joints_world.copy()
    for i, p in enumerate(parents_22):
        if p >= 0:
            local_translations[i] = joints_world[i] - joints_world[p]

    # Inverse bind matrices: rest pose has identity rotation, world translation = joints_world.
    # IBM = inverse of bind matrix. Bind matrix is translation by joints_world[i].
    inv_bind = np.tile(np.eye(4, dtype=np.float32), (len(bone_order), 1, 1))
    inv_bind[:, :3, 3] = -joints_world

    # Skin weights collapsed onto 22 body joints, top-4 per vertex.
    weights_22 = _collapse_weights(weights_full, parents_full, body_smplx_indices)
    joints_idx, joints_w = _topk_weights(weights_22, k=4)

    normals = _vertex_normals(v_template, faces.astype(np.int64))

    # Build binary buffer with proper alignment per accessor.
    buf = bytearray()
    views = []  # (byteOffset, byteLength, target)

    def add_view(arr: np.ndarray, target: int | None = None) -> int:
        # glTF requires accessor offsets to be a multiple of the component size; pad to 4.
        while len(buf) % 4 != 0:
            buf.append(0)
        offset = len(buf)
        data = arr.tobytes()
        buf.extend(data)
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data)} | ({"target": target} if target else {}))
        return len(views) - 1

    ARRAY_BUFFER = 34962
    ELEMENT_ARRAY_BUFFER = 34963

    pos_view = add_view(v_template.astype(np.float32), ARRAY_BUFFER)
    nrm_view = add_view(normals, ARRAY_BUFFER)
    jnt_view = add_view(joints_idx.astype(np.uint16), ARRAY_BUFFER)
    wgt_view = add_view(joints_w.astype(np.float32), ARRAY_BUFFER)
    idx_view = add_view(faces.astype(np.uint32), ELEMENT_ARRAY_BUFFER)
    ibm_view = add_view(inv_bind.transpose(0, 2, 1).reshape(-1).astype(np.float32))  # column-major

    accessors = [
        {"bufferView": pos_view, "componentType": 5126, "count": int(v_template.shape[0]), "type": "VEC3",
         "min": v_template.min(0).tolist(), "max": v_template.max(0).tolist()},
        {"bufferView": nrm_view, "componentType": 5126, "count": int(normals.shape[0]), "type": "VEC3"},
        {"bufferView": jnt_view, "componentType": 5123, "count": int(joints_idx.shape[0]), "type": "VEC4"},
        {"bufferView": wgt_view, "componentType": 5126, "count": int(joints_w.shape[0]), "type": "VEC4"},
        {"bufferView": idx_view, "componentType": 5125, "count": int(faces.size), "type": "SCALAR"},
        {"bufferView": ibm_view, "componentType": 5126, "count": int(len(bone_order)), "type": "MAT4"},
    ]
    POS_ACC, NRM_ACC, JNT_ACC, WGT_ACC, IDX_ACC, IBM_ACC = range(6)

    # Morph targets: shape PCAs [0..shape_pcas) then expression PCAs [0..expression_pcas).
    # Each target is a [V, 3] vertex-position delta. shapedirs is [V, 3, 400] where
    # columns 0..299 are betas and 300..399 are expression.
    morph_targets = []
    morph_names = []
    for i in range(shape_pcas):
        delta = shapedirs[:, :, i].astype(np.float32)
        view_idx = add_view(delta, ARRAY_BUFFER)
        accessors.append({
            "bufferView": view_idx, "componentType": 5126, "count": int(delta.shape[0]), "type": "VEC3",
            "min": delta.min(0).tolist(), "max": delta.max(0).tolist(),
        })
        morph_targets.append({"POSITION": len(accessors) - 1})
        morph_names.append(f"shape_{i}")
    for i in range(expression_pcas):
        delta = shapedirs[:, :, n_shape_total + i].astype(np.float32)
        view_idx = add_view(delta, ARRAY_BUFFER)
        accessors.append({
            "bufferView": view_idx, "componentType": 5126, "count": int(delta.shape[0]), "type": "VEC3",
            "min": delta.min(0).tolist(), "max": delta.max(0).tolist(),
        })
        morph_targets.append({"POSITION": len(accessors) - 1})
        morph_names.append(f"expression_{i}")

    # Nodes: 22 joint nodes + 1 mesh node. Joint nodes index [0..21], mesh node index 22.
    nodes = []
    children_map = {i: [] for i in range(len(bone_order))}
    for i, p in enumerate(parents_22):
        if p >= 0:
            children_map[int(p)].append(i)
    for i, (name, _) in enumerate(bone_order):
        node = {"name": name, "translation": local_translations[i].tolist()}
        if children_map[i]:
            node["children"] = children_map[i]
        nodes.append(node)

    mesh_node_idx = len(nodes)
    nodes.append({"name": "smplx_mesh", "mesh": 0, "skin": 0})

    root_joint = next(i for i, (_, p) in enumerate(bone_order) if p is None)

    gltf = {
        "asset": {"version": "2.0", "generator": "kimodo.scripts.export_smplx_glb"},
        "scene": 0,
        "scenes": [{"nodes": [root_joint, mesh_node_idx]}],
        "nodes": nodes,
        "meshes": [{
            "name": "smplx",
            "primitives": [{
                "attributes": {
                    "POSITION": POS_ACC,
                    "NORMAL": NRM_ACC,
                    "JOINTS_0": JNT_ACC,
                    "WEIGHTS_0": WGT_ACC,
                },
                "indices": IDX_ACC,
                "mode": 4,
                **({"targets": morph_targets} if morph_targets else {}),
            }],
            **({
                "weights": [0.0] * len(morph_targets),
                "extras": {"targetNames": morph_names},
            } if morph_targets else {}),
        }],
        "skins": [{
            "name": "smplx_rig",
            "inverseBindMatrices": IBM_ACC,
            "skeleton": root_joint,
            "joints": list(range(len(bone_order))),
        }],
        "buffers": [{"byteLength": len(buf)}],
        "bufferViews": views,
        "accessors": accessors,
    }

    json_chunk = _pack_chunk(json.dumps(gltf, separators=(",", ":")).encode("utf-8"), b"JSON")
    bin_chunk = _pack_chunk(bytes(buf), b"BIN\x00")
    total_len = 12 + len(json_chunk) + len(bin_chunk)
    header = struct.pack("<III", 0x46546C67, 2, total_len)

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "wb") as f:
        f.write(header)
        f.write(json_chunk)
        f.write(bin_chunk)

    print(
        f"Wrote {output}  ({total_len/1024:.1f} KB, {v_template.shape[0]} verts, "
        f"{faces.shape[0]} faces, {len(bone_order)} bones, "
        f"{len(morph_targets)} morph targets [{shape_pcas} shape + {expression_pcas} expression])"
    )


def main():
    repo_root = Path(__file__).resolve().parents[2]
    default_npz = repo_root / "kimodo/assets/skeletons/smplx22/SMPLX_NEUTRAL.npz"
    default_out = repo_root / "assets/smplx_neutral.glb"

    p = argparse.ArgumentParser()
    p.add_argument("--smplx-npz", type=Path, default=default_npz)
    p.add_argument("--output", type=Path, default=default_out)
    p.add_argument("--shape-pcas", type=int, default=10, help="Number of body-shape PCA morph targets to bake (0..300).")
    p.add_argument("--expression-pcas", type=int, default=10, help="Number of facial-expression PCA morph targets to bake (0..100).")
    args = p.parse_args()

    if not args.smplx_npz.exists():
        raise SystemExit(
            f"SMPLX_NEUTRAL.npz not found at {args.smplx_npz}.\n"
            "Download it from https://smpl-x.is.tue.mpg.de/ (license required) and place it there."
        )
    export(args.smplx_npz, args.output, shape_pcas=args.shape_pcas, expression_pcas=args.expression_pcas)


if __name__ == "__main__":
    main()
