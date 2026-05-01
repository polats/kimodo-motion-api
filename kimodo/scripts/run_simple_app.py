# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Stripped-down kimodo viewer: type a prompt, see a 5-second SMPL-X animation.

Usage (inside the demo container, with the text-encoder service running):
    python -m kimodo.scripts.run_simple_app

Set SERVER_PORT to override the default 7860. Reads TEXT_ENCODER_URL from env
the same way kimodo's full demo does.
"""

import os
import threading
import time
import traceback

import torch
import viser

from kimodo.model.load_model import load_model
from kimodo.viz.playback import CharacterMotion
from kimodo.viz.scene import Character


MODEL_NAME = "kimodo-smplx-rp"
DEFAULT_PROMPT = "A person walks forward."
CLIP_SECONDS = 5.0
NUM_DENOISING_STEPS = 20


def _passthrough(iterable, *args, **kwargs):
    return iterable


def main() -> None:
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    print(f"Loading {MODEL_NAME} on {device}...")
    model = load_model(MODEL_NAME, device=device)
    skeleton = model.motion_rep.skeleton.to(device)
    fps = float(model.motion_rep.fps)
    num_frames = int(round(CLIP_SECONDS * fps))
    print(f"Model loaded. fps={fps}, clip={num_frames} frames")

    port = int(os.environ.get("SERVER_PORT", 7860))
    server = viser.ViserServer(host="0.0.0.0", port=port, label="Kimodo Simple")
    server.scene.set_up_direction("+y")
    server.scene.world_axes.visible = False

    state_lock = threading.Lock()
    client_state: dict[int, dict] = {}

    @server.on_client_connect
    def _on_connect(client: viser.ClientHandle) -> None:
        cid = client.client_id
        character = Character(
            name=f"character_{cid}",
            server=client,
            skeleton=skeleton,
            mesh_mode="smplx_skin",
            create_skeleton_mesh=False,
            create_skinned_mesh=True,
            visible_skinned_mesh=True,
        )

        # Hold initial rest pose so the character is visible before the first generation.
        rest_pos, rest_rot = character.get_pose()
        rest_motion = CharacterMotion(
            character,
            rest_pos[None].repeat(num_frames, 1, 1),
            rest_rot[None].repeat(num_frames, 1, 1, 1),
        )
        rest_motion.set_frame(0)

        with state_lock:
            client_state[cid] = {
                "character": character,
                "motion": rest_motion,
                "frame": 0,
                "playing": False,
            }

        with client.gui.add_folder("Prompt"):
            prompt_box = client.gui.add_text("Text", initial_value=DEFAULT_PROMPT)
            generate_btn = client.gui.add_button("Generate (5s)")
            status = client.gui.add_markdown("Ready.")

        @generate_btn.on_click
        def _on_generate(_) -> None:
            generate_btn.disabled = True
            status.content = "Generating..."
            try:
                with torch.no_grad():
                    out = model(
                        [prompt_box.value],
                        num_frames,
                        NUM_DENOISING_STEPS,
                        progress_bar=_passthrough,
                    )
                joints_pos = out["posed_joints"][0]
                joints_rot = out["global_rot_mats"][0]
                foot_contacts = out.get("foot_contacts")
                if foot_contacts is not None:
                    foot_contacts = foot_contacts[0]

                with state_lock:
                    s = client_state.get(cid)
                    if s is None:
                        return
                    s["motion"] = CharacterMotion(s["character"], joints_pos, joints_rot, foot_contacts)
                    s["frame"] = 0
                    s["playing"] = True
                status.content = "Playing (looped)."
            except Exception as e:
                status.content = f"Error: {type(e).__name__}: {e}"
                print(f"Generation failed for client {cid}:")
                traceback.print_exc()
            finally:
                generate_btn.disabled = False

    @server.on_client_disconnect
    def _on_disconnect(client: viser.ClientHandle) -> None:
        with state_lock:
            client_state.pop(client.client_id, None)

    def _playback_loop() -> None:
        period = 1.0 / fps
        next_tick = time.time()
        while True:
            with state_lock:
                snapshot = [(cid, s) for cid, s in client_state.items() if s["playing"] and s["motion"] is not None]
            for cid, s in snapshot:
                try:
                    s["motion"].set_frame(s["frame"])
                except Exception as e:
                    print(f"Playback error for client {cid}: {e}")
                    continue
                with state_lock:
                    st = client_state.get(cid)
                    if st is not None and st["motion"] is s["motion"]:
                        st["frame"] = (st["frame"] + 1) % st["motion"].length

            next_tick += period
            sleep_for = next_tick - time.time()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                # Fell behind — reset the clock to avoid spiraling.
                next_tick = time.time()

    threading.Thread(target=_playback_loop, daemon=True).start()
    print(f"Simple app running at http://localhost:{port}")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
