# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""HTTP API for kimodo motion generation. Decouples generation from any viewer.

POST /generate { prompt: str, seconds: float = 5 }
  -> { fps, num_frames, bone_names, local_quats_wxyz [T,J,4], root_positions [T,3] }

GET  /info
  -> static metadata about the loaded model.

Run inside the demo container:
    SERVER_PORT=7862 python -m kimodo.scripts.run_motion_api

Reads TEXT_ENCODER_URL from env the same way the rest of kimodo does.
"""

import math
import os
import threading

import numpy as np
import torch
import uvicorn
import viser.transforms as tf
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from kimodo.constraints import FullBodyConstraintSet, compute_global_heading
from kimodo.model.load_model import load_model
from kimodo.scripts.animation_store import make_store
from kimodo.scripts.character_registry import CharacterRegistry
from kimodo.scripts.animation_registry import MixamoAnimationRegistry

MODEL_NAME = os.environ.get("KIMODO_MODEL", "kimodo-smplx-rp")
NUM_DENOISING_STEPS = int(os.environ.get("KIMODO_DENOISING_STEPS", "20"))
DEFAULT_SECONDS = 5.0
MAX_SECONDS = 10.0


class SeamPose(BaseModel):
    # Reference to a single frame of a previously-generated animation. Used to
    # pin frame 0 and frame N-1 of a new generation to the same full-body pose
    # via a FullBodyConstraintSet, so the resulting motion loops cleanly.
    #
    # `direction` is a 2-element [x, z] unit vector in the seam's LOCAL frame
    # (forward = +Z, right = +X) describing the desired loop translation.
    # When None, both endpoints share the same XZ → in-place loop. When set,
    # the second endpoint is offset along this direction (rotated into world
    # frame by the seam's heading) so the model produces a translating loop.
    anim_id: str
    frame_idx: int
    direction: list[float] | None = None


class GenerateRequest(BaseModel):
    prompt: str
    seconds: float = DEFAULT_SECONDS
    seam_pose: SeamPose | None = None


def _passthrough(iterable, *args, **kwargs):
    return iterable


def build_app() -> FastAPI:
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    print(f"Loading {MODEL_NAME} on {device}...")
    model = load_model(MODEL_NAME, device=device)
    fps = float(model.motion_rep.fps)
    skeleton = model.motion_rep.skeleton
    bone_names = [name for name, _ in skeleton.bone_order_names_with_parents]
    print(f"Model loaded. fps={fps}, joints={len(bone_names)}")

    # Serialize generation across requests; one GPU.
    gen_lock = threading.Lock()

    store = make_store()
    print(f"Animation store: {type(store).__name__}")

    char_registry = CharacterRegistry()
    print(f"Character registry: {char_registry.root}")
    mx_anim_registry = MixamoAnimationRegistry()
    print(f"Mixamo animation registry: {mx_anim_registry.root}")

    app = FastAPI(title="Kimodo Motion API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/info")
    def info() -> dict:
        return {
            "model": MODEL_NAME,
            "fps": fps,
            "bone_names": bone_names,
            "max_seconds": MAX_SECONDS,
            "default_seconds": DEFAULT_SECONDS,
        }

    def _build_record(prompt: str, seconds: float, num_frames: int, local_quats_wxyz, global_quats_xyzw, root_positions, posed_joints, seam_pose: SeamPose | None) -> dict:
        record = {
            "prompt": prompt,
            "seconds": seconds,
            "fps": fps,
            "num_frames": num_frames,
            "model": MODEL_NAME,
            "bone_names": bone_names,
            # Local rotations (relative to parent), wxyz. Sufficient for SMPL-X rigs that
            # share kimodo's rest pose. Use global_quats_xyzw for retargeting to any rig.
            "local_quats_wxyz": local_quats_wxyz.tolist(),
            # Global (world-space) rotations, xyzw (three.js native order). Required for
            # retargeting kimodo motion onto rigs with a different rest pose (e.g. Mixamo).
            "global_quats_xyzw": global_quats_xyzw.tolist(),
            "root_positions": root_positions.tolist(),
            # World-space joint positions [T, J, 3]. Persisted so any frame of any
            # animation can later serve as a FullBodyConstraintSet seam pose.
            "posed_joints": posed_joints.tolist(),
        }
        if seam_pose is not None:
            record["seam_pose"] = {"anim_id": seam_pose.anim_id, "frame_idx": seam_pose.frame_idx}
        return record

    def _build_seam_constraint(seam: SeamPose, num_frames: int, seconds: float) -> FullBodyConstraintSet:
        rec = store.get(seam.anim_id)
        if rec is None:
            raise HTTPException(404, f"seam_pose anim_id '{seam.anim_id}' not found")
        if "posed_joints" not in rec:
            raise HTTPException(
                400,
                f"seam_pose source '{seam.anim_id}' has no posed_joints — regenerate it",
            )
        T = int(rec["num_frames"])
        f = int(seam.frame_idx)
        if not 0 <= f < T:
            raise HTTPException(400, f"seam_pose frame_idx {f} out of range [0, {T})")

        joints = torch.tensor(rec["posed_joints"][f], device=device, dtype=torch.float32)  # [J, 3]
        quats_xyzw = np.asarray(rec["global_quats_xyzw"][f], dtype=np.float32)  # [J, 4]
        rot_mats_np = tf.SO3.from_quaternion_xyzw(quats_xyzw).as_matrix()  # [J, 3, 3]
        rots = torch.tensor(rot_mats_np, device=device, dtype=torch.float32)

        # Translate the seam's joints so the pelvis sits at world XZ origin.
        # The source clip's seam pose is in absolute world coordinates of
        # whatever clip it came from — we want the new motion to start at
        # the origin so it integrates cleanly with renderers that anchor
        # avatars at their own positions.
        root_idx = skeleton.root_idx
        src_root_xz = joints[root_idx, [0, 2]].clone()  # [2]
        joints_at_origin = joints.clone()
        joints_at_origin[:, 0] -= src_root_xz[0]
        joints_at_origin[:, 2] -= src_root_xz[1]

        # When the caller asks for a translating loop, build the world-frame
        # XZ offset by rotating the user-supplied seam-LOCAL direction into
        # the seam's actual world heading. Without a direction the second
        # endpoint shares XZ with the first → an in-place loop (idle, wave).
        #
        # Coordinate notes — kimodo's compute_heading_angle is
        # atan2(Δhip_z, -Δhip_x), so heading angle θ=0 means facing +Z and
        # θ=π/2 means facing +X. The returned (cos, sin) therefore maps:
        #     world_forward = (sin θ, cos θ)
        #     world_right   = (cos θ, -sin θ)
        # so a seam-local (dx, dz) becomes world XZ
        #     (dx·cos θ + dz·sin θ,  -dx·sin θ + dz·cos θ).
        joints_end = joints_at_origin.clone()
        if seam.direction is not None and len(seam.direction) == 2:
            dx_local = float(seam.direction[0])
            dz_local = float(seam.direction[1])
            mag = math.hypot(dx_local, dz_local)
            if mag > 1e-6:
                dx_local /= mag
                dz_local /= mag
                heading_2d = compute_global_heading(
                    joints_at_origin.unsqueeze(0), skeleton
                )[0]  # [2] = (cos θ, sin θ)
                cos_h = float(heading_2d[0])
                sin_h = float(heading_2d[1])
                world_x = dx_local * cos_h + dz_local * sin_h
                world_z = -dx_local * sin_h + dz_local * cos_h
                # Distance heuristic: 1 m/s × seconds. Just needs to be
                # large enough that the model doesn't squeeze motion to
                # zero; the prompt drives actual cadence/speed.
                loop_distance = max(0.5, float(seconds) * 1.0)
                joints_end[:, 0] += world_x * loop_distance
                joints_end[:, 2] += world_z * loop_distance

        frame_indices = torch.tensor([0, num_frames - 1], device=device, dtype=torch.long)
        joints_stack = torch.stack([joints_at_origin, joints_end], dim=0)  # [2, J, 3]
        rots_stack = torch.stack([rots, rots], dim=0)  # [2, J, 3, 3]

        return FullBodyConstraintSet(
            skeleton=skeleton,
            frame_indices=frame_indices,
            global_joints_positions=joints_stack,
            global_joints_rots=rots_stack,
        )

    @app.post("/generate")
    def generate(req: GenerateRequest) -> dict:
        if not req.prompt or not req.prompt.strip():
            raise HTTPException(400, "prompt is empty")
        seconds = max(0.5, min(MAX_SECONDS, float(req.seconds)))
        num_frames = int(round(seconds * fps))

        constraint_lst = None
        if req.seam_pose is not None:
            # Per-sample list of constraint sets; we only generate one sample.
            constraint_lst = [[_build_seam_constraint(req.seam_pose, num_frames, seconds)]]

        with gen_lock:
            with torch.no_grad():
                out = model(
                    [req.prompt],
                    num_frames,
                    NUM_DENOISING_STEPS,
                    constraint_lst=constraint_lst,
                    # Enable post-processing only for constrained runs:
                    # foot-skate cleanup + constraint enforcement tightens the
                    # seam pose match (frame 0 / frame N-1) so the loop wrap
                    # doesn't visibly pop. Unconstrained runs keep the prior
                    # behavior to avoid changing existing clips' character.
                    post_processing=constraint_lst is not None,
                    progress_bar=_passthrough,
                )

        local_rot_mats = out["local_rot_mats"][0].detach().cpu().numpy()  # [T, J, 3, 3]
        global_rot_mats = out["global_rot_mats"][0].detach().cpu().numpy()  # [T, J, 3, 3]
        root_positions = out["root_positions"][0].detach().cpu().numpy()  # [T, 3]
        posed_joints = out["posed_joints"][0].detach().cpu().numpy()  # [T, J, 3]
        local_quats_wxyz = tf.SO3.from_matrix(local_rot_mats).wxyz  # [T, J, 4]
        global_quats_wxyz = tf.SO3.from_matrix(global_rot_mats).wxyz
        # wxyz -> xyzw for the global field (three.js native order).
        global_quats_xyzw = global_quats_wxyz[..., [1, 2, 3, 0]]

        record = _build_record(
            req.prompt.strip(),
            seconds,
            int(local_rot_mats.shape[0]),
            local_quats_wxyz,
            global_quats_xyzw,
            root_positions,
            posed_joints,
            req.seam_pose,
        )
        try:
            record["id"] = store.save(record)
        except Exception as e:
            # Don't fail the request if persistence breaks; log and return without id.
            print(f"Warning: failed to save animation: {type(e).__name__}: {e}")
        return record

    @app.get("/animations")
    def list_animations() -> dict:
        try:
            return {"animations": store.list()}
        except Exception as e:
            raise HTTPException(500, f"list failed: {type(e).__name__}: {e}")

    @app.get("/animations/{anim_id}")
    def get_animation(anim_id: str) -> dict:
        rec = store.get(anim_id)
        if rec is None:
            raise HTTPException(404, f"animation '{anim_id}' not found")
        return rec

    @app.delete("/animations/{anim_id}")
    def delete_animation(anim_id: str) -> dict:
        if not store.delete(anim_id):
            raise HTTPException(404, f"animation '{anim_id}' not found")
        return {"deleted": anim_id}

    @app.get("/characters")
    def list_characters() -> dict:
        return {"characters": char_registry.list()}

    @app.delete("/characters/{char_id}")
    def delete_character(char_id: str) -> dict:
        if not char_registry.delete(char_id):
            raise HTTPException(404, f"character '{char_id}' not found")
        return {"deleted": char_id}

    @app.get("/mixamo/search")
    def mixamo_search(q: str, limit: int = 24) -> dict:
        from kimodo.scripts.mixamo import search_characters, MixamoError
        try:
            return {"results": search_characters(q, limit=limit)}
        except MixamoError as e:
            raise HTTPException(502, str(e))

    class MixamoImportRequest(BaseModel):
        id: str
        name: str

    @app.post("/mixamo/import")
    def mixamo_import(req: MixamoImportRequest) -> dict:
        from kimodo.scripts.mixamo import import_character, MixamoError
        try:
            config = import_character(req.id, req.name)
        except MixamoError as e:
            raise HTTPException(502, str(e))
        # Persist to the registry so the next /characters call sees it.
        config["source"] = "mixamo"
        config["source_id"] = req.id
        return char_registry.save(config)

    @app.get("/mixamo/animations/search")
    def mixamo_anim_search(q: str, limit: int = 24) -> dict:
        from kimodo.scripts.mixamo import search_motions, MixamoError
        try:
            return {"results": search_motions(q, limit=limit)}
        except MixamoError as e:
            raise HTTPException(502, str(e))

    @app.post("/mixamo/animations/import")
    def mixamo_anim_import(req: MixamoImportRequest) -> dict:
        from kimodo.scripts.mixamo import import_motion, MixamoError
        try:
            config = import_motion(req.id, req.name)
        except MixamoError as e:
            raise HTTPException(502, str(e))
        return mx_anim_registry.save(config)

    @app.get("/mixamo/animations")
    def mixamo_anim_list() -> dict:
        return {"animations": mx_anim_registry.list()}

    @app.delete("/mixamo/animations/{anim_id}")
    def mixamo_anim_delete(anim_id: str) -> dict:
        if not mx_anim_registry.delete(anim_id):
            raise HTTPException(404, f"animation '{anim_id}' not found")
        return {"deleted": anim_id}

    return app


def main() -> None:
    port = int(os.environ.get("SERVER_PORT", 7862))
    app = build_app()
    print(f"Motion API listening on http://0.0.0.0:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
