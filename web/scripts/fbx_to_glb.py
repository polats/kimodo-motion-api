"""Convert FBX (any version) to GLB via Blender's CLI.

Run with:
    blender --background --python web/scripts/fbx_to_glb.py -- INPUT.fbx OUTPUT.glb
or for batch:
    blender --background --python web/scripts/fbx_to_glb.py -- IN1.fbx OUT1.glb IN2.fbx OUT2.glb

Pairs of (input, output) after the `--`.
"""
import sys
import os

import bpy

# Args after `--` are ours.
argv = sys.argv
if "--" in argv:
    argv = argv[argv.index("--") + 1:]
else:
    argv = []

if len(argv) < 2 or len(argv) % 2 != 0:
    print("usage: blender -b -P fbx_to_glb.py -- in1.fbx out1.glb [in2.fbx out2.glb ...]")
    sys.exit(2)

pairs = list(zip(argv[0::2], argv[1::2]))
for src, dst in pairs:
    if not os.path.exists(src):
        raise SystemExit(f"missing input: {src}")

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=src)
    bpy.ops.export_scene.gltf(
        filepath=dst,
        export_format="GLB",
        export_apply=True,
        export_yup=True,
        export_skins=True,
        export_animations=False,  # we apply our own motion
    )
    print(f"OK: {src} -> {dst}")
