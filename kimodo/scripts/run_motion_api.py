# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""HTTP API for kimodo motion generation. Decouples generation from any viewer.

POST /generate { prompt: str, seconds: float = 5, num_steps: int | None = None }
  -> { fps, num_frames, bone_names, local_quats_wxyz [T,J,4], root_positions [T,3] }
  (num_steps overrides the diffusion step count; default = NUM_DENOISING_STEPS)

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

from kimodo.constraints import FullBodyConstraintSet, compute_global_heading, load_constraints_lst
from kimodo.motion_rep.feature_utils import compute_heading_angle
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
    # Truncate the clip at its dynamic peak so it ENDS mid-action (not grounded):
    # "kick" = frame the foot is highest, "punch" = frame the arm is most extended.
    end_on_peak: str | None = None
    num_steps: int | None = None  # override diffusion steps (default = NUM_DENOISING_STEPS)
    # Raw constraints.json list (fullbody / left-hand|right-hand|left-foot|right-foot /
    # root2d, mixed). Takes precedence over seam_pose. Frame indices are 0-based into
    # the generated clip (length = seconds * fps). See kimodo.constraints.load_constraints_lst.
    constraints: list | None = None
    # Override post-processing (foot-lock IK + exact constraint snapping; needs the
    # motion_correction package). Default: on iff any constraint is present.
    post_processing: bool | None = None


class GenerateSequenceRequest(BaseModel):
    # A sequence of prompts generated as ONE continuous motion (e.g. a kata):
    # each segment after the first starts from the previous segment's end pose,
    # stitched with `num_transition_frames`. Saved as a normal store record, so
    # the viewers (kimodo web, woid) display it like any other animation.
    prompts: list[str]
    # Per-segment duration: one value for all segments, or a list matching prompts.
    seconds: float | list[float] = DEFAULT_SECONDS
    num_transition_frames: int = 5
    num_steps: int | None = None  # override diffusion steps (default = NUM_DENOISING_STEPS)
    # If True, also slice the continuous motion into one tree NODE per prompt
    # (each re-rooted to origin, chained via continues_from) so the kata appears
    # as individually-viewable moves in the /kata tree. Returns {"nodes": [...]}.
    save_segments: bool = False


class GenerateContinueRequest(BaseModel):
    # Generate a move that CONTINUES from a frame of an existing clip: the new
    # motion's frame 0 is pinned to that pose (start-only), so it flows on from
    # there. With `stitch` (default), the source is prepended so the result is
    # ONE combined clip — the building block of a move tree (a shared opening
    # that branches into variations: kick → {left punch, right punch}).
    source_id: str            # clip to continue from
    prompt: str               # the next move
    seconds: float = DEFAULT_SECONDS
    source_frame: int = -1    # frame of the source to continue from (-1 = last)
    # False (default): save the new move as its OWN clip, beginning at the source's
    # end pose (a separate, individually-viewable tree node). True: also prepend
    # source[:source_frame+1] → one combined clip (for exporting a whole kata).
    stitch: bool = False
    # Truncate the new move at its dynamic peak ("kick"/"punch") so it ends
    # mid-action — the next continuation then starts from a non-grounded pose.
    end_on_peak: str | None = None
    # Post-processing (foot-skate + constraint tightening) makes the frame-0 seam
    # exact but is slow. Off is much faster (good for bulk library builds); the
    # constraint still guides frame 0 and the path stitch realigns the join.
    post_processing: bool = True
    num_steps: int | None = None  # override diffusion steps (default = NUM_DENOISING_STEPS)


class StitchPathRequest(BaseModel):
    # Concatenate a PATH of existing clips (e.g. a root→leaf kata path) into one
    # continuous motion, carrying world position AND heading forward across joins
    # so the character flows through the whole kata without resetting position.
    # Since each clip's frame 0 == its parent's end pose, joins are seamless.
    ids: list[str]
    save: bool = False        # also persist as a store record (else just return for playback)


class RotateClipRequest(BaseModel):
    # Bake a yaw (about the world Y at the XZ origin) into a clip's data and save
    # it as a new clip — so "facing" is part of the animation, not a side param.
    id: str
    degrees: float = 0.0


class HitboxDef(BaseModel):
    # An attack hitbox on a striking limb. jointA/jointB are SMPL-X joint names
    # (== a clip's bone_names); jointB == jointA (or null) means a sphere, otherwise
    # a capsule along the limb. start/end are the active-frame window (inclusive).
    # reach (m) slides the anchor outward past jointA, away from its parent joint,
    # toward the striking surface (wrist→fist, ankle→toe) — lets the fist be hit
    # without a finger bone, staying in the 22-joint space so it bakes portably.
    jointA: str
    jointB: str | None = None
    radius: float = 0.08
    reach: float = 0.0
    start: int = 0
    end: int = 0
    damage: float = 25.0
    tags: list[str] = []


class TimingPoint(BaseModel):
    # DAW-automation-style breakpoint for a move's playback time-remap.
    # x = normalized real-time through the move (0..1); y = normalized clip
    # position (0..1); c = curvature of the segment from this point to the next
    # (-1..1, 0 = linear). A monotonic curve retimes the move (slow wind-up →
    # fast strike → ease-out); a flat segment (equal y) = a freeze/hold.
    x: float
    y: float
    c: float = 0.0


class TimingDef(BaseModel):
    points: list[TimingPoint] = []


class SetHitboxRequest(BaseModel):
    id: str
    hitboxes: list[HitboxDef] = []
    # Optional, additive: a per-move playback time-remap curve. Absent/None ⇒ the
    # field is left untouched on the clip (linear playback). Pass an empty-points
    # TimingDef to clear it.
    timing: TimingDef | None = None


def _passthrough(iterable, *args, **kwargs):
    return iterable


def build_app() -> FastAPI:
    device = "cuda:0" if torch.cuda.is_available() else "cpu"

    # Lazy model state: the heavy diffusion model loads only when a GENERATION
    # endpoint first needs it (ensure_model). The server boots instantly, and
    # browsing (/animations) + path playback (/stitch_path, which is pure numpy)
    # never load it — so just using the viewer keeps VRAM free.
    model = None
    fps = 30.0
    skeleton = None
    bone_names = None
    _load_lock = threading.Lock()

    def ensure_model():
        nonlocal model, fps, skeleton, bone_names
        if model is None:
            with _load_lock:
                if model is None:  # double-checked under the lock
                    print(f"Lazy-loading {MODEL_NAME} on {device}...")
                    m = load_model(MODEL_NAME, device=device)
                    fps = float(m.motion_rep.fps)
                    skeleton = m.motion_rep.skeleton
                    bone_names = [name for name, _ in skeleton.bone_order_names_with_parents]
                    model = m
                    print(f"Model loaded. fps={fps}, joints={len(bone_names)}")
        return model

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
        ensure_model()  # report real fps/bone_names
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

    def _arrays_from_output(out) -> dict:
        """Model output dict -> the four per-frame arrays the store record holds."""
        local_rot_mats = out["local_rot_mats"][0].detach().cpu().numpy()  # [T, J, 3, 3]
        global_rot_mats = out["global_rot_mats"][0].detach().cpu().numpy()  # [T, J, 3, 3]
        return {
            "local_quats_wxyz": np.asarray(tf.SO3.from_matrix(local_rot_mats).wxyz),
            # wxyz -> xyzw for the global field (three.js native order).
            "global_quats_xyzw": np.asarray(tf.SO3.from_matrix(global_rot_mats).wxyz)[..., [1, 2, 3, 0]],
            "root_positions": out["root_positions"][0].detach().cpu().numpy(),   # [T, 3]
            "posed_joints": out["posed_joints"][0].detach().cpu().numpy(),       # [T, J, 3]
        }

    def _save_arrays(prompt_text: str, seconds: float, arr: dict, seam_pose: SeamPose | None, extra: dict | None = None) -> dict:
        """Persist a store record from the four per-frame arrays (same fields the
        viewers read). Shared by /generate, /generate_sequence, /generate_continue.
        `extra` is merged in (e.g. a move's `continues_from` parent link)."""
        record = _build_record(
            prompt_text, seconds, int(arr["local_quats_wxyz"].shape[0]),
            arr["local_quats_wxyz"], arr["global_quats_xyzw"], arr["root_positions"], arr["posed_joints"], seam_pose,
        )
        if extra:
            record.update(extra)
        try:
            record["id"] = store.save(record)
        except Exception as e:
            # Don't fail the request if persistence breaks; log and return without id.
            print(f"Warning: failed to save animation: {type(e).__name__}: {e}")
        return record

    def _save_record_from_output(out, prompt_text: str, seconds: float, seam_pose: SeamPose | None) -> dict:
        return _save_arrays(prompt_text, seconds, _arrays_from_output(out), seam_pose)

    def _truncate_at_peak(arr: dict, kind: str) -> dict:
        """Cut the clip so it ENDS at its dynamic peak — foot highest ('kick') or
        arm most forward-extended ('punch') — so the last frame is mid-action."""
        P = arr["posed_joints"]  # [T, 22, 3], Y up, XZ ground
        T = P.shape[0]
        if kind == "punch":
            lr = np.linalg.norm(P[:, 20, ::2] - P[:, 16, ::2], axis=1)  # L wrist↔shoulder, XZ
            rr = np.linalg.norm(P[:, 21, ::2] - P[:, 17, ::2], axis=1)  # R wrist↔shoulder, XZ
            metric = np.maximum(lr, rr)
        else:  # "kick" / default: the frame a foot is highest off the ground
            metric = np.maximum(P[:, 10, 1], P[:, 11, 1])
        # only consider the back half so we don't cut on an early wind-up
        lo = max(5, T // 3)
        peak = lo + int(np.argmax(metric[lo:])) if T > lo else T - 1
        e = min(T, peak + 1)
        return {k: v[:e] for k, v in arr.items()}

    def _resolve_frame(rec: dict, frame_idx: int) -> int:
        T = int(rec["num_frames"])
        f = frame_idx if frame_idx >= 0 else T + frame_idx
        if not 0 <= f < T:
            raise HTTPException(400, f"source_frame {frame_idx} out of range for clip of {T} frames")
        return f

    def _reroot_xz(arr: dict) -> dict:
        """Translate so the clip's first frame sits at XZ origin (height preserved)."""
        off = arr["root_positions"][0]
        rp = arr["root_positions"].copy(); rp[:, 0] -= off[0]; rp[:, 2] -= off[2]
        pj = arr["posed_joints"].copy(); pj[..., 0] -= off[0]; pj[..., 2] -= off[2]
        return {**arr, "root_positions": rp, "posed_joints": pj}

    def _build_start_constraint(source_id: str, frame_idx: int) -> FullBodyConstraintSet:
        """Pin ONLY frame 0 of the new motion to a source clip's frame pose
        (re-rooted to XZ origin), leaving the rest free → a continuation. This is
        the ORIGINAL implementation that built the kata library.

        Deliberately NO first_heading_angle: the full-body frame-0 pin already sets
        the start orientation, and seeding a heading injects a large backward root
        drift on non-locomotion moves (measured: 2.4m back with heading vs 0.6m
        without, on the same kick→punch). Don't re-add it."""
        rec = store.get(source_id)
        if rec is None:
            raise HTTPException(404, f"source_id '{source_id}' not found")
        if "posed_joints" not in rec:
            raise HTTPException(400, f"source '{source_id}' has no posed_joints — regenerate it")
        f = _resolve_frame(rec, frame_idx)

        joints = torch.tensor(rec["posed_joints"][f], device=device, dtype=torch.float32)  # [J, 3]
        quats_xyzw = np.asarray(rec["global_quats_xyzw"][f], dtype=np.float32)  # [J, 4]
        rots = torch.tensor(tf.SO3.from_quaternion_xyzw(quats_xyzw).as_matrix(), device=device, dtype=torch.float32)
        root_idx = skeleton.root_idx
        joints_at_origin = joints.clone()
        joints_at_origin[:, 0] -= joints[root_idx, 0]
        joints_at_origin[:, 2] -= joints[root_idx, 2]

        return FullBodyConstraintSet(
            skeleton=skeleton,
            frame_indices=torch.tensor([0], device=device, dtype=torch.long),
            global_joints_positions=joints_at_origin.unsqueeze(0),  # [1, J, 3]
            global_joints_rots=rots.unsqueeze(0),                   # [1, J, 3, 3]
        )

    def _stitch_arrays(src_rec: dict, cont: dict, upto_frame: int) -> dict:
        """Prepend source[:upto_frame+1] to the continuation. The continuation was
        re-rooted to XZ origin at the seam pose (heading preserved), so we only
        translate it by the source seam-frame's XZ to line the join up, then drop
        the continuation's duplicate frame 0."""
        s = {k: np.asarray(src_rec[k], dtype=np.float32) for k in
             ("local_quats_wxyz", "global_quats_xyzw", "root_positions", "posed_joints")}
        k = upto_frame + 1
        off = s["root_positions"][upto_frame]  # [3] world XZ of the join
        c_root = cont["root_positions"].copy();  c_root[:, 0] += off[0];     c_root[:, 2] += off[2]
        c_posed = cont["posed_joints"].copy();   c_posed[:, :, 0] += off[0]; c_posed[:, :, 2] += off[2]
        return {
            "local_quats_wxyz": np.concatenate([s["local_quats_wxyz"][:k], cont["local_quats_wxyz"][1:]], axis=0),
            "global_quats_xyzw": np.concatenate([s["global_quats_xyzw"][:k], cont["global_quats_xyzw"][1:]], axis=0),
            "root_positions": np.concatenate([s["root_positions"][:k], c_root[1:]], axis=0),
            "posed_joints": np.concatenate([s["posed_joints"][:k], c_posed[1:]], axis=0),
        }

    @app.post("/generate")
    def generate(req: GenerateRequest) -> dict:
        if not req.prompt or not req.prompt.strip():
            raise HTTPException(400, "prompt is empty")
        ensure_model()
        seconds = max(0.5, min(MAX_SECONDS, float(req.seconds)))
        num_frames = int(round(seconds * fps))

        constraint_lst = None
        if req.constraints:
            # Arbitrary constraints (fullbody/EE/root2d/mixed) from the request,
            # in the constraints.json format. One per-sample list (we gen one sample).
            constraint_lst = [load_constraints_lst(req.constraints, skeleton, device=device)]
        elif req.seam_pose is not None:
            # Per-sample list of constraint sets; we only generate one sample.
            constraint_lst = [[_build_seam_constraint(req.seam_pose, num_frames, seconds)]]

        post_proc = req.post_processing if req.post_processing is not None else (constraint_lst is not None)
        with gen_lock:
            with torch.no_grad():
                out = model(
                    [req.prompt],
                    num_frames,
                    int(req.num_steps) if req.num_steps else NUM_DENOISING_STEPS,
                    constraint_lst=constraint_lst,
                    # Post-processing (foot-skate cleanup + exact constraint snapping)
                    # defaults on for any constrained run; override via post_processing.
                    post_processing=post_proc,
                    progress_bar=_passthrough,
                )
        arr = _arrays_from_output(out)
        if req.end_on_peak:
            arr = _truncate_at_peak(arr, req.end_on_peak)
        return _save_arrays(req.prompt.strip(), seconds, arr, req.seam_pose)

    @app.post("/generate_sequence")
    def generate_sequence(req: GenerateSequenceRequest) -> dict:
        """Generate one continuous motion from a SEQUENCE of prompts (a kata):
        the model generates each segment and stitches them with smooth transitions
        (`multi_prompt`), so each move after the first starts from the previous
        move's end pose — not the default rest. Saved as a normal store record."""
        prompts = [p.strip() for p in (req.prompts or []) if p and p.strip()]
        if not prompts:
            raise HTTPException(400, "prompts is empty")
        ensure_model()

        # Per-segment durations: a single value for all, or a list matching prompts.
        if isinstance(req.seconds, list):
            secs = list(req.seconds)
            if len(secs) == 1:
                secs = secs * len(prompts)
            if len(secs) != len(prompts):
                raise HTTPException(400, f"seconds list ({len(secs)}) must match prompts ({len(prompts)}) or be a single value")
        else:
            secs = [float(req.seconds)] * len(prompts)
        secs = [max(0.5, min(MAX_SECONDS, float(s))) for s in secs]
        num_frames = [int(round(s * fps)) for s in secs]
        ntf = max(1, int(req.num_transition_frames))

        with gen_lock:
            with torch.no_grad():
                out = model(
                    prompts,
                    num_frames,
                    int(req.num_steps) if req.num_steps else NUM_DENOISING_STEPS,
                    num_samples=1,        # required for the multi_prompt path (bs = num_samples)
                    multi_prompt=True,
                    num_transition_frames=ntf,
                    post_processing=True,
                    progress_bar=_passthrough,
                )
        # `seconds` reported as the true clip length (transitions are absorbed, so
        # the actual frame count comes from the output inside the helper).
        if not req.save_segments:
            label = " → ".join(prompts)
            return _save_record_from_output(out, label, float(sum(secs)), None)

        # Slice the continuous motion into one re-rooted tree node per prompt,
        # chained via continues_from — fast way to build a deep kata as a tree.
        arr = _arrays_from_output(out)
        total = int(arr["local_quats_wxyz"].shape[0])
        bounds = [0]
        for nf in num_frames:
            bounds.append(min(total, bounds[-1] + nf))
        nodes, prev_id, prev_len = [], None, 0
        for i, p in enumerate(prompts):
            s, e = bounds[i], bounds[i + 1]
            if e <= s:
                continue
            seg = {k: np.array(v[s:e]) for k, v in arr.items()}
            off = seg["root_positions"][0].copy()  # re-root this move to XZ origin
            seg["root_positions"][:, 0] -= off[0]; seg["root_positions"][:, 2] -= off[2]
            seg["posed_joints"][..., 0] -= off[0]; seg["posed_joints"][..., 2] -= off[2]
            extra = {"continues_from": {"source_id": prev_id, "frame": prev_len - 1}} if prev_id else None
            rec = _save_arrays(p, (e - s) / fps, seg, None, extra=extra)
            prev_id = rec["id"]; prev_len = e - s
            nodes.append(rec["id"])
        return {"nodes": nodes, "count": len(nodes)}

    @app.post("/generate_continue")
    def generate_continue(req: GenerateContinueRequest) -> dict:
        """Generate a move that flows on from a frame of an existing clip (frame 0
        pinned to that pose). With stitch=True the source is prepended so the
        result is ONE selectable clip. Branch a shared opening into variations by
        calling this twice from the same source_id with different prompts."""
        if not req.prompt or not req.prompt.strip():
            raise HTTPException(400, "prompt is empty")
        ensure_model()
        src = store.get(req.source_id)
        if src is None:
            raise HTTPException(404, f"source_id '{req.source_id}' not found")
        f = _resolve_frame(src, req.source_frame)

        seconds = max(0.5, min(MAX_SECONDS, float(req.seconds)))
        num_frames = int(round(seconds * fps))
        constraint = _build_start_constraint(req.source_id, f)

        with gen_lock:
            with torch.no_grad():
                out = model(
                    [req.prompt.strip()],
                    num_frames,
                    int(req.num_steps) if req.num_steps else NUM_DENOISING_STEPS,
                    constraint_lst=[[constraint]],
                    post_processing=req.post_processing,   # enforce the frame-0 seam so the join doesn't pop
                    progress_bar=_passthrough,
                )
        cont = _arrays_from_output(out)
        if req.end_on_peak:
            cont = _truncate_at_peak(cont, req.end_on_peak)

        # Tree edge: which clip + frame this move flows on from.
        parent = {"continues_from": {"source_id": req.source_id, "frame": f}}

        if not req.stitch:
            return _save_arrays(req.prompt.strip(), float(cont["local_quats_wxyz"].shape[0]) / fps, cont, None, extra=parent)

        # Combined whole-kata clip (opt-in, e.g. for baking one sequence).
        arr = _stitch_arrays(src, cont, f)
        label = f"{src.get('prompt', req.source_id)} → {req.prompt.strip()}"
        return _save_arrays(label, float(arr["local_quats_wxyz"].shape[0]) / fps, arr, None, extra=parent)

    def _pose_heading(posed_frame) -> float:
        """Ground-plane heading (radians) from the hip vector — matches kimodo's
        compute_heading_angle = atan2(Δz, -Δx) with Δ = right_hip(2) - left_hip(1).
        Computed directly from joints so path stitching needs no model loaded."""
        d = posed_frame[2] - posed_frame[1]
        return math.atan2(float(d[2]), float(-d[0]))

    @app.post("/stitch_path")
    def stitch_path(req: StitchPathRequest) -> dict:
        """Concatenate a path of clips into one continuous motion. Each clip is
        rotated (yaw) + translated so its frame 0 lands on the running world pose,
        so the character walks through the whole path without resetting."""
        if not req.ids:
            raise HTTPException(400, "ids is empty")
        recs = []
        for cid in req.ids:
            r = store.get(cid)
            if r is None:
                raise HTTPException(404, f"clip '{cid}' not found")
            recs.append(r)

        outL, outG, outR, outP = [], [], [], []
        alpha = Tx = Tz = 0.0  # running world heading + XZ translation (set after clip 0)
        n = len(recs)
        for idx, r in enumerate(recs):
            L = np.asarray(r["local_quats_wxyz"], dtype=np.float32)
            G = np.asarray(r["global_quats_xyzw"], dtype=np.float32)  # xyzw
            R = np.asarray(r["root_positions"], dtype=np.float32)
            P = np.asarray(r["posed_joints"], dtype=np.float32)

            if idx > 0:
                # align this clip's start to the running heading, then add any baked yaw
                # (heading_offset) back as a deliberate turn so a baked rotation shows in
                # the stitched kata and carries forward instead of being normalized away.
                beta = alpha - _pose_heading(P[0]) + math.radians(float(r.get("heading_offset", 0) or 0))
                c, s = math.cos(beta), math.sin(beta)
                px, pz = float(R[0, 0]), float(R[0, 2])    # pivot = this clip's frame-0 root (XZ)
                # rotate XZ about the pivot, then translate the pivot to (Tx, Tz).
                def tf(x, z):
                    x0, z0 = x - px, z - pz
                    return (x0 * c + z0 * s) + Tx, (-x0 * s + z0 * c) + Tz
                R = R.copy(); P = P.copy()
                R[:, 0], R[:, 2] = (R[:, 0] - px) * c + (R[:, 2] - pz) * s + Tx, -(R[:, 0] - px) * s + (R[:, 2] - pz) * c + Tz
                P[..., 0], P[..., 2] = (P[..., 0] - px) * c + (P[..., 2] - pz) * s + Tx, -(P[..., 0] - px) * s + (P[..., 2] - pz) * c + Tz
                # rotate world (global) quats by the yaw: pre-multiply by qY(beta), xyzw.
                qy, qw = math.sin(beta / 2.0), math.cos(beta / 2.0)
                gx, gy, gz, gw = G[..., 0], G[..., 1], G[..., 2], G[..., 3]
                G = np.stack([qw * gx + qy * gz, qw * gy + qy * gw, qw * gz - qy * gx, qw * gw - qy * gy], axis=-1)

            lo = 0 if idx == 0 else 1   # drop frame 0 (duplicate of the parent's branch pose)
            # Cut this clip at the frame its CHILD in the path branched from, so the
            # parent ends exactly at the branch point instead of playing on past it.
            # (For an end-frame branch, branch frame == last frame → no trim.)
            hi = len(R)
            if idx < n - 1:
                cf = recs[idx + 1].get("continues_from")
                bf = cf.get("frame") if cf else None
                if bf is not None:
                    bf = int(bf if bf >= 0 else len(R) + bf)
                    if 0 <= bf < len(R):
                        hi = bf + 1
            outL.append(L[lo:hi]); outG.append(G[lo:hi]); outR.append(R[lo:hi]); outP.append(P[lo:hi])
            k = hi - 1   # last KEPT frame = the branch point the next clip continues from
            alpha = _pose_heading(P[k]); Tx = float(R[k, 0]); Tz = float(R[k, 2])

        arr = {
            "local_quats_wxyz": np.concatenate(outL, 0),
            "global_quats_xyzw": np.concatenate(outG, 0),
            "root_positions": np.concatenate(outR, 0),
            "posed_joints": np.concatenate(outP, 0),
        }
        label = " → ".join((r.get("prompt", "?")[:24]) for r in recs)
        n = int(arr["local_quats_wxyz"].shape[0])
        # Build the record from the SOURCE clips' metadata (fps/bone_names/model)
        # so stitching never needs the diffusion model loaded.
        s0 = recs[0]
        lfps = float(s0.get("fps", 30.0))
        record = {
            "prompt": label, "seconds": n / lfps, "fps": lfps, "num_frames": n,
            "model": s0.get("model"), "bone_names": s0.get("bone_names"),
            "local_quats_wxyz": arr["local_quats_wxyz"].tolist(),
            "global_quats_xyzw": arr["global_quats_xyzw"].tolist(),
            "root_positions": arr["root_positions"].tolist(),
            "posed_joints": arr["posed_joints"].tolist(),
        }
        if req.save:
            record["id"] = store.save(record)
        return record

    @app.post("/rotate_clip")
    def rotate_clip(req: RotateClipRequest) -> dict:
        """Yaw the whole clip about world-Y at the XZ origin (matches three.js
        root.rotation.y) and save it as a new clip — the rotation is baked into the
        arrays, so facing needs no extra parameter downstream."""
        rec = store.get(req.id)
        if rec is None:
            raise HTTPException(404, f"clip '{req.id}' not found")
        theta = math.radians(float(req.degrees))
        c, s = math.cos(theta), math.sin(theta)
        R = np.asarray(rec["root_positions"], np.float32).copy()
        P = np.asarray(rec["posed_joints"], np.float32).copy()
        G = np.asarray(rec["global_quats_xyzw"], np.float32).copy()   # xyzw
        L = np.asarray(rec["local_quats_wxyz"], np.float32).copy()    # wxyz
        # rotate XZ about the origin: x' = x c + z s, z' = -x s + z c
        rx, rz = R[:, 0].copy(), R[:, 2].copy()
        R[:, 0], R[:, 2] = rx * c + rz * s, -rx * s + rz * c
        px, pz = P[..., 0].copy(), P[..., 2].copy()
        P[..., 0], P[..., 2] = px * c + pz * s, -px * s + pz * c
        # premultiply every joint's global orientation by qY(theta) (xyzw)
        qy, qw = math.sin(theta / 2), math.cos(theta / 2)
        gx, gy, gz, gw = (G[..., i].copy() for i in range(4))
        G[..., 0] = qw * gx + qy * gz
        G[..., 1] = qw * gy + qy * gw
        G[..., 2] = qw * gz - qy * gx
        G[..., 3] = qw * gw - qy * gy
        # the root's local quat == its global; keep them consistent (wxyz)
        L[:, 0, 0], L[:, 0, 1], L[:, 0, 2], L[:, 0, 3] = G[:, 0, 3], G[:, 0, 0], G[:, 0, 1], G[:, 0, 2]
        arr = {"local_quats_wxyz": L, "global_quats_xyzw": G, "root_positions": R, "posed_joints": P}
        # Record the cumulative baked yaw so path stitching can honor it: stitch normally
        # re-aligns each move's heading to flow from the previous one (which would cancel a
        # constant yaw), so it adds this offset back as a deliberate turn that carries
        # forward. Also preserve continues_from so the move keeps its place in the tree.
        extra = {"heading_offset": float(rec.get("heading_offset", 0) or 0) + float(req.degrees)}
        if rec.get("continues_from"):
            extra["continues_from"] = rec["continues_from"]
        return _save_arrays(rec.get("prompt", req.id), float(L.shape[0]) / fps, arr, None, extra=extra)

    @app.post("/set_hitbox")
    def set_hitbox(req: SetHitboxRequest) -> dict:
        """Attach attack-hitbox metadata to a clip IN PLACE (same id, no new clip) so it
        doesn't disturb the kata tree. The viewer authors/visualizes it; the baker can
        later emit it as a sidecar for in-engine hit detection."""
        rec = store.get(req.id)
        if rec is None:
            raise HTTPException(404, f"clip '{req.id}' not found")
        rec["hitboxes"] = [h.model_dump() for h in req.hitboxes]
        # Additive: only touch `timing` when the request carries it, so callers that
        # only set hitboxes don't wipe an authored curve. Empty points clears it.
        if req.timing is not None:
            pts = [p.model_dump() for p in req.timing.points]
            if pts:
                rec["timing"] = {"points": pts}
            else:
                rec.pop("timing", None)
        store.save(rec)   # LocalFsStore keeps the existing id → overwrites in place
        return {"id": req.id, "hitboxes": rec["hitboxes"], "timing": rec.get("timing")}

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

    @app.get("/clothing")
    def list_clothing() -> dict:
        # Clothing manifests written by web/scripts/clothing_add.py: one per garment,
        # with per-body GLB urls + slot/layer metadata. The viewer's CLOTHING tab reads this.
        import json as _json
        from pathlib import Path as _Path
        root = _Path(os.environ.get("KIMODO_CLOTHING_PATH", ".kimodo-clothing"))
        items = []
        for p in sorted(root.glob("*.json")):
            try:
                items.append(_json.load(open(p)))
            except Exception:
                pass
        return {"clothing": items}

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
