"""Pose the citizen to a clean T-pose (reusing the baker's virtual-T-pose
arm posing), bake it into the geometry, and export a bare unrigged GLB for UniRig.
Usage: blender --background --python tpose_citizen.py -- <src.fbx> <out.glb>
"""
import bpy, sys
from mathutils import Vector, Matrix

argv = sys.argv[sys.argv.index("--") + 1:]
src, out = argv[0], argv[1]

# SMPL-X canonical rest (arm joints) — verbatim from web/src/rigs.js.
SMPLX_REST_WORLD = {
    "left_collar": [0.0448, 0.0275, -0.0003], "right_collar": [-0.0492, 0.0269, -0.0065],
    "left_shoulder": [0.1641, 0.0852, -0.0158], "right_shoulder": [-0.1518, 0.0804, -0.0191],
    "left_elbow": [0.4182, 0.0131, -0.0582], "right_elbow": [-0.4229, 0.0439, -0.0456],
    "left_wrist": [0.6702, 0.0363, -0.0607], "right_wrist": [-0.6722, 0.0394, -0.0609],
}
CITIZEN_TO_KIMODO = {
    "clavicle_L": "left_collar", "arm_upper_L": "left_shoulder", "arm_lower_L": "left_elbow", "hand_L": "left_wrist",
    "clavicle_R": "right_collar", "arm_upper_R": "right_shoulder", "arm_lower_R": "right_elbow", "hand_R": "right_wrist",
}
CHAIN = [("clavicle_L", "arm_upper_L"), ("clavicle_R", "arm_upper_R"),
         ("arm_upper_L", "arm_lower_L"), ("arm_upper_R", "arm_lower_R"),
         ("arm_lower_L", "hand_L"), ("arm_lower_R", "hand_R")]

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=src, use_anim=False)
arm = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")

# --- pose the arm chain to a virtual T-pose (baker logic, no reset) ---
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode="POSE")
pbs = arm.pose.bones
for c_name, _ in CHAIN:
    if c_name in pbs:
        pbs[c_name].rotation_mode = "QUATERNION"
for c_name, c_child in CHAIN:
    if c_name not in pbs or c_child not in pbs:
        print("[tpose] WARN missing bone", c_name, c_child); continue
    kp = Vector(SMPLX_REST_WORLD[CITIZEN_TO_KIMODO[c_name]])
    kpc = Vector(SMPLX_REST_WORLD[CITIZEN_TO_KIMODO[c_child]])
    target = (kpc - kp).normalized()
    if c_name == "arm_lower_L": target = Vector((1.0, 0.0, 0.0))
    elif c_name == "arm_lower_R": target = Vector((-1.0, 0.0, 0.0))
    elif c_name in ("clavicle_L", "clavicle_R", "arm_upper_L", "arm_upper_R"):
        target = Vector((target.x, 0.0, target.z)).normalized()
    bpy.context.view_layer.update()
    cur, ch = pbs[c_name], pbs[c_child]
    cur_dir = (ch.head - cur.head).normalized()
    rot = cur_dir.rotation_difference(target)
    m = cur.matrix.copy()
    cur.matrix = Matrix.LocRotScale(m.to_translation(), rot @ m.to_quaternion(), m.to_scale())
    bpy.context.view_layer.update()
bpy.ops.object.mode_set(mode="OBJECT")

# --- bake the pose, then bake each mesh's own object transform so the sub-meshes
#     (which carry mismatched cm/m scales) end up in ONE consistent world space ---
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
for msh in meshes:
    bpy.context.view_layer.objects.active = msh
    if msh.data.shape_keys:
        for kb in list(msh.data.shape_keys.key_blocks):
            msh.shape_key_remove(kb)
    for mod in list(msh.modifiers):
        if mod.type == "ARMATURE":
            try: bpy.ops.object.modifier_apply(modifier=mod.name)   # bake T-pose deform
            except Exception as e: print("[tpose] apply fail", msh.name, e)
# detach from the armature keeping world position, then bake object transforms
bpy.ops.object.select_all(action="DESELECT")
for m in meshes: m.select_set(True)
bpy.context.view_layer.objects.active = meshes[0]
bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
bpy.data.objects.remove(arm, do_unlink=True)
for m in meshes:                      # bake each mesh's world matrix into its geometry
    bpy.ops.object.select_all(action="DESELECT")
    m.select_set(True); bpy.context.view_layer.objects.active = m
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

# --- join (now all consistent) + clean + normalize + center ---
bpy.ops.object.select_all(action="DESELECT")
for m in meshes: m.select_set(True)
bpy.context.view_layer.objects.active = meshes[0]
if len(meshes) > 1: bpy.ops.object.join()
obj = bpy.context.view_layer.objects.active
obj.data.materials.clear()
for vg in list(obj.vertex_groups): obj.vertex_groups.remove(vg)
bpy.ops.object.mode_set(mode="EDIT"); bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.remove_doubles(threshold=0.0005)
bpy.ops.mesh.quads_convert_to_tris(quad_method="BEAUTY", ngon_method="BEAUTY")
bpy.ops.object.mode_set(mode="OBJECT")

bpy.context.view_layer.update()
bb = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
h = max(v.z for v in bb) - min(v.z for v in bb)
if h > 0: obj.scale = (1.8 / h,) * 3
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
bpy.context.view_layer.update()
bb = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
c = [sum(v[i] for v in bb) / 8 for i in range(3)]
obj.location = (obj.location.x - c[0], obj.location.y - c[1], obj.location.z - c[2])
bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)
print(f"[tpose] verts={len(obj.data.vertices)} tris={len(obj.data.polygons)} src_height={h:.2f}")

bpy.ops.export_scene.gltf(filepath=out, export_format="GLB",
                          export_skins=False, export_animations=False, export_morph=False)
print("[tpose] wrote", out)
