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

import os
import threading

import torch
import uvicorn
import viser.transforms as tf
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from kimodo.model.load_model import load_model
from kimodo.scripts.animation_store import make_store

MODEL_NAME = os.environ.get("KIMODO_MODEL", "kimodo-smplx-rp")
NUM_DENOISING_STEPS = int(os.environ.get("KIMODO_DENOISING_STEPS", "20"))
DEFAULT_SECONDS = 5.0
MAX_SECONDS = 10.0


class GenerateRequest(BaseModel):
    prompt: str
    seconds: float = DEFAULT_SECONDS


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

    def _build_record(prompt: str, seconds: float, num_frames: int, quats_wxyz, root_positions) -> dict:
        return {
            "prompt": prompt,
            "seconds": seconds,
            "fps": fps,
            "num_frames": num_frames,
            "model": MODEL_NAME,
            "bone_names": bone_names,
            "local_quats_wxyz": quats_wxyz.tolist(),
            "root_positions": root_positions.tolist(),
        }

    @app.post("/generate")
    def generate(req: GenerateRequest) -> dict:
        if not req.prompt or not req.prompt.strip():
            raise HTTPException(400, "prompt is empty")
        seconds = max(0.5, min(MAX_SECONDS, float(req.seconds)))
        num_frames = int(round(seconds * fps))

        with gen_lock:
            with torch.no_grad():
                out = model(
                    [req.prompt],
                    num_frames,
                    NUM_DENOISING_STEPS,
                    progress_bar=_passthrough,
                )

        local_rot_mats = out["local_rot_mats"][0].detach().cpu().numpy()  # [T, J, 3, 3]
        root_positions = out["root_positions"][0].detach().cpu().numpy()  # [T, 3]
        quats_wxyz = tf.SO3.from_matrix(local_rot_mats).wxyz  # [T, J, 4]

        record = _build_record(req.prompt.strip(), seconds, int(local_rot_mats.shape[0]), quats_wxyz, root_positions)
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

    return app


def main() -> None:
    port = int(os.environ.get("SERVER_PORT", 7862))
    app = build_app()
    print(f"Motion API listening on http://0.0.0.0:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
