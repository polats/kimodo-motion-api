"""
Generate a Base-Model vmdl that wraps a directory of FBX animation clips.

Usage:
    python gen_vmdl.py \
        --clip-dir /path/to/Assets/models/kimodo \
        --out      /path/to/Assets/models/kimodo/kimodo_anims.vmdl \
        --base-model models/citizen/citizen.vmdl
"""

import argparse
from pathlib import Path
from textwrap import dedent


VMDL_HEADER = '<!-- kv3 encoding:text:version{e21c7f3c-8a33-41c5-9977-a76d3a32aa0d} format:modeldoc30:version{8c2d7a91-9c42-4bf0-883a-5a3b1762d4f1} -->\n'


def emit_anim_file(addon_rel_path: str, name: str) -> str:
    # framerate = 30 explicit (kimodo clips are baked at 30 fps). With
    # framerate = 0, the engine computes Sequence.Duration = 0 and the
    # sequence freezes on frame 1 — verified failure mode.
    #
    # ExtractMotion child: pulls the pelvis's HORIZONTAL translation out of
    # the bone and exposes it to the engine as SceneModel.RootMotion (a
    # per-frame Transform delta). This is how citizen's own run/walk work
    # (see citizen_ani_process_run.vmdl_prefab). Without it RootMotion is
    # always zero and the pelvis motion stays baked in the bone — meaning
    # the collider never follows and the mesh double-moves.
    #   extract_tx/ty = true  → horizontal becomes root motion (collider moves)
    #   extract_tz    = false → vertical bob stays in the mesh (jumps still bob)
    #   root_bone_name = pelvis → our root bone (citizen base skeleton)
    #   motion_type   = Single → extract the actual per-frame motion
    return dedent(f'''\
            {{
                _class = "AnimFile"
                name = "{name}"
                source_filename = "{addon_rel_path}"
                start_frame = -1
                end_frame = -1
                framerate = 30.0
                take = ""
                looping = true
                delta = false
                worldSpace = false
                children =
                [
                    {{
                        _class = "ExtractMotion"
                        extract_tx = true
                        extract_ty = true
                        extract_tz = false
                        extract_rz = false
                        linear = false
                        quadratic = false
                        root_bone_name = "pelvis"
                        motion_type = "Single"
                    }},
                ]
            }},''')


def find_addon_rel(fbx_path: Path, addon_assets_root: Path) -> str:
    """Make the FBX path relative to the addon's Assets/ root for ModelDoc."""
    try:
        rel = fbx_path.relative_to(addon_assets_root)
    except ValueError:
        raise SystemExit(
            f"FBX {fbx_path} is not under addon Assets root {addon_assets_root}"
        )
    return str(rel).replace("\\", "/")


def build_vmdl(clip_dir: Path, addon_assets_root: Path, base_model: str) -> str:
    fbxs = sorted(p for p in clip_dir.glob("*.fbx") if p.is_file())
    if not fbxs:
        raise SystemExit(f"No FBX files found in {clip_dir}")

    entries = []
    for fbx in fbxs:
        rel = find_addon_rel(fbx, addon_assets_root)
        # Prefix "kim_" so runtime code can reliably distinguish our clips
        # from inherited base_model (citizen) clips.
        name = f"kim_{fbx.stem}"
        entries.append(emit_anim_file(rel, name))

    body = "\n".join("\t\t\t\t" + e.replace("\n", "\n\t\t\t\t") for e in entries)

    # ScaleAndMirror at 0.3937 matches citizen.vmdl's modifier. Our externally
    # referenced animation FBXs come in cm; without this, bone translations
    # arrive 2.54x larger than the citizen mesh (which IS scaled), stretching
    # the character. The base_model_name inheritance doesn't seem to apply the
    # modifier to our AnimationList, so we declare it locally.
    return (
        VMDL_HEADER
        + "{\n"
        + '\trootNode = \n'
        + '\t{\n'
        + '\t\t_class = "RootNode"\n'
        + '\t\tchildren = \n'
        + '\t\t[\n'
        + '\t\t\t{\n'
        + '\t\t\t\t_class = "ModelModifierList"\n'
        + '\t\t\t\tchildren = \n'
        + '\t\t\t\t[\n'
        + '\t\t\t\t\t{\n'
        + '\t\t\t\t\t\t_class = "ModelModifier_ScaleAndMirror"\n'
        + '\t\t\t\t\t\tscale = 0.3937\n'
        + '\t\t\t\t\t\tmirror_x = false\n'
        + '\t\t\t\t\t\tmirror_y = false\n'
        + '\t\t\t\t\t\tmirror_z = false\n'
        + '\t\t\t\t\t\tflip_bone_forward = false\n'
        + '\t\t\t\t\t\tswap_left_and_right_bones = false\n'
        + '\t\t\t\t\t},\n'
        + '\t\t\t\t]\n'
        + '\t\t\t},\n'
        + '\t\t\t{\n'
        + '\t\t\t\t_class = "AnimationList"\n'
        + '\t\t\t\tchildren = \n'
        + '\t\t\t\t[\n'
        + body + "\n"
        + '\t\t\t\t]\n'
        + '\t\t\t},\n'
        + '\t\t]\n'
        + '\t\tmodel_archetype = ""\n'
        + '\t\tprimary_associated_entity = ""\n'
        + '\t\tanim_graph_name = ""\n'
        + f'\t\tbase_model_name = "{base_model}"\n'
        + '\t}\n'
        + "}\n"
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--clip-dir", required=True, type=Path,
                   help="Dir of baked .fbx clips. Must be under the addon's Assets/.")
    p.add_argument("--out", required=True, type=Path,
                   help="Output vmdl path.")
    p.add_argument("--addon-assets-root", type=Path, default=None,
                   help="Addon Assets/ root. Defaults to walking up from --clip-dir.")
    p.add_argument("--base-model", default="models/citizen/citizen.vmdl")
    args = p.parse_args()

    if args.addon_assets_root is None:
        cur = args.clip_dir.resolve()
        while cur != cur.parent:
            if cur.name == "Assets":
                args.addon_assets_root = cur
                break
            cur = cur.parent
        if args.addon_assets_root is None:
            raise SystemExit("Could not auto-detect Assets/ root; pass --addon-assets-root")
    args.addon_assets_root = args.addon_assets_root.resolve()
    args.clip_dir = args.clip_dir.resolve()

    text = build_vmdl(args.clip_dir, args.addon_assets_root, args.base_model)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)
    print(f"[gen_vmdl] wrote {args.out} ({sum(1 for _ in args.clip_dir.glob('*.fbx'))} clips, base={args.base_model})")


if __name__ == "__main__":
    main()
