"""
Generate a kimodo-format motion JSON whose every frame is the SMPL-X rest pose
(identity world rotations). Used as a verification gate for the baker: if a
character baked with this clip stands in T-pose in ModelDoc, the bake math is
correct.
"""

import argparse
import json
from pathlib import Path


BONES = [
    "pelvis", "left_hip", "right_hip", "spine1", "left_knee", "right_knee",
    "spine2", "left_ankle", "right_ankle", "spine3", "left_foot", "right_foot",
    "neck", "left_collar", "right_collar", "head", "left_shoulder",
    "right_shoulder", "left_elbow", "right_elbow", "left_wrist", "right_wrist",
]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--frames", type=int, default=2)
    p.add_argument("--fps", type=float, default=30.0)
    args = p.parse_args()

    identity_xyzw = [0.0, 0.0, 0.0, 1.0]
    identity_wxyz = [1.0, 0.0, 0.0, 0.0]
    clip = {
        "prompt": "tpose_verification",
        "seconds": args.frames / args.fps,
        "fps": args.fps,
        "num_frames": args.frames,
        "model": "synthetic",
        "bone_names": BONES,
        "local_quats_wxyz": [[identity_wxyz for _ in BONES] for _ in range(args.frames)],
        "global_quats_xyzw": [[identity_xyzw for _ in BONES] for _ in range(args.frames)],
        "root_positions": [[0.0, 0.0, 0.0] for _ in range(args.frames)],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(clip))
    print(f"[tpose] wrote {args.out} ({args.frames} frames)")


if __name__ == "__main__":
    main()
