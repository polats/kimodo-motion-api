"""Rig a decompiled s&box garment onto the citizen's UniRig (bone_N) skeleton so
it can be worn in the /kata viewer and animate with the body.

KEY INSIGHT (from the s&box clothing docs): every garment is authored against and
"skinned to" the REF FBX (citizen_human_male_REF.fbx) — that is the authoritative
rig. So we drive the garment to the virtual-T with the REF armature itself (its
rest pose == the garment's authoring rest, verified to the mm), which binds
identity-at-rest. Driving it with VRF's *reconstructed* body skeleton collapses
the mesh because that skeleton's rest/frames differ.

Inputs (the garment's _m_human variant matches the male REF rig):
  body_unirig.glb  - ground truth: bone_N skeleton, already T-posed + normalized
                     (weight-transfer SOURCE + landmark reference)
  citizen_human_male_REF.fbx - the authoritative authoring rig (the DRIVER)
  garment.glb      - VRF decompile of the garment's _m_human variant

Steps:
  1. reframe the garment into the REF armature's object space, bind by vertex-group
     name (identity at rest), pose the REF arm chain to virtual-T; bake.
  2. landmark-align the garment to the UniRig body (pelvis+head, uniform).
  3. transfer the UniRig body's bone_N weights onto the garment, rebind to the
     UniRig armature, export the garment alone.

Usage:
  blender -b -P clothing_rig.py -- <body_unirig.glb> <citizen_human_male_REF.fbx> <garment.glb> <out.glb>
"""
import bpy, sys
from mathutils import Vector, Matrix

argv = sys.argv[sys.argv.index("--") + 1:]
BODY_U, REF_FBX, GARMENT, OUT = argv[:4]

# arm-chain virtual-T logic — verbatim from tpose_citizen_mesh.py
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

def imp_glb(fn):
    b = set(bpy.context.scene.objects); bpy.ops.import_scene.gltf(filepath=fn)
    return [o for o in bpy.context.scene.objects if o not in b]
def imp_fbx(fn):
    b = set(bpy.context.scene.objects); bpy.ops.import_scene.fbx(filepath=fn, use_anim=False)
    return [o for o in bpy.context.scene.objects if o not in b]
def arms(objs):  return [o for o in objs if o.type == "ARMATURE"]
def meshes(objs): return [o for o in objs if o.type == "MESH"]
def head_world(a, b): return a.matrix_world @ a.data.bones[b].head_local
def sole(o): bpy.ops.object.select_all(action="DESELECT"); o.select_set(True); bpy.context.view_layer.objects.active = o

bpy.ops.wm.read_factory_settings(use_empty=True)

# --- imports ---
u = imp_glb(BODY_U);  armU = arms(u)[0];  bodyMeshU = meshes(u)[0]
r = imp_fbx(REF_FBX); armR = arms(r)[0]
refBodyMeshes = meshes(r)   # KEEP: posed to T, they reproduce the body's normalize transform
g = imp_glb(GARMENT)
gms = meshes(g); gMesh = max(gms, key=lambda o: len(o.data.vertices))
# bake the garment armature's transform into the mesh, then drop its (VRF) rig
sole(gMesh)
if gMesh.parent: bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
for md in list(gMesh.modifiers):
    if md.type == "ARMATURE": gMesh.modifiers.remove(md)
for o in list(g):
    if o is not gMesh:
        try: bpy.data.objects.remove(o, do_unlink=True)
        except Exception: pass
nmiss = [v.name for v in gMesh.vertex_groups if v.name not in armR.data.bones]
print(f"[rig] U bones={len(armU.data.bones)} REF bones={len(armR.data.bones)} "
      f"garment verts={len(gMesh.data.vertices)} vgroups={len(gMesh.vertex_groups)} "
      f"groups-not-in-REF={nmiss if nmiss else 'NONE'}")

# --- 1. reframe garment into REF armature's object space (world preserved), bind, pose to T ---
Wr = armR.matrix_world.copy(); delta = Wr.inverted() @ gMesh.matrix_world
for v in gMesh.data.vertices: v.co = delta @ v.co
gMesh.matrix_world = Wr                       # now shares REF object frame -> identity rest bind
mod = gMesh.modifiers.new(name="REF", type="ARMATURE"); mod.object = armR

bpy.context.view_layer.objects.active = armR
bpy.ops.object.mode_set(mode="POSE")
pbs = armR.pose.bones
for c, _ in CHAIN:
    if c in pbs: pbs[c].rotation_mode = "QUATERNION"
for c, ch in CHAIN:
    if c not in pbs or ch not in pbs:
        print("[rig] WARN REF missing", c, ch); continue
    kp = Vector(SMPLX_REST_WORLD[CITIZEN_TO_KIMODO[c]])
    kpc = Vector(SMPLX_REST_WORLD[CITIZEN_TO_KIMODO[ch]])
    target = (kpc - kp).normalized()
    if c == "arm_lower_L": target = Vector((1.0, 0.0, 0.0))
    elif c == "arm_lower_R": target = Vector((-1.0, 0.0, 0.0))
    elif c in ("clavicle_L", "clavicle_R", "arm_upper_L", "arm_upper_R"):
        target = Vector((target.x, 0.0, target.z)).normalized()
    bpy.context.view_layer.update()
    cur, chb = pbs[c], pbs[ch]
    cur_dir = (chb.head - cur.head).normalized()
    rot = cur_dir.rotation_difference(target)
    m = cur.matrix.copy()
    cur.matrix = Matrix.LocRotScale(m.to_translation(), rot @ m.to_quaternion(), m.to_scale())
    bpy.context.view_layer.update()
bpy.ops.object.mode_set(mode="OBJECT")

# --- 2. fit by REPRODUCING the body's own normalize transform -----------------
# The garment is authored to the REF body, and the UniRig body is that SAME body
# after tpose_citizen_mesh.py scaled it to 1.8 m (by total T-posed height) and
# centred its bbox at the origin. Both the REF body meshes and the garment are now
# posed to T in the same world space, so applying the body's exact scale+centre to
# the garment lands it on the UniRig body — no landmark guessing, works for every
# body and every garment.
TARGET_H = 1.8
deps = bpy.context.evaluated_depsgraph_get()
pts = []
for m in refBodyMeshes:
    ev = m.evaluated_get(deps); me = ev.to_mesh(); mw = m.matrix_world
    for v in me.vertices: pts.append(mw @ v.co)
    ev.to_mesh_clear()
xs = [p.x for p in pts]; ys = [p.y for p in pts]; zs = [p.z for p in pts]
mid = Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2))
s = TARGET_H / (max(zs) - min(zs))

# bake the T deform into the garment, detach, bake object transform, drop native groups
sole(gMesh)
bpy.ops.object.modifier_apply(modifier=mod.name)
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
for vg in list(gMesh.vertex_groups): gMesh.vertex_groups.remove(vg)

# apply s*(p - mid) — the body's normalize transform — to the garment
M = Matrix.Translation(-(s * mid)) @ Matrix.Diagonal((s, s, s, 1.0))
gMesh.matrix_world = M @ gMesh.matrix_world
sole(gMesh)
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
bpy.data.objects.remove(armR, do_unlink=True)
for m in refBodyMeshes:
    try: bpy.data.objects.remove(m, do_unlink=True)
    except Exception: pass
bb = [v.co for v in gMesh.data.vertices]
print(f"[rig] body normalize: scale={s:.4f} height={(max(zs)-min(zs)):.3f} | garment z "
      f"{min(p.z for p in bb):.3f}..{max(p.z for p in bb):.3f}")

# --- 3. transfer bone_N weights from body, bind garment to UniRig armature ---
for b in armU.data.bones:
    if b.name not in gMesh.vertex_groups: gMesh.vertex_groups.new(name=b.name)
bpy.ops.object.select_all(action="DESELECT")
gMesh.select_set(True); bodyMeshU.select_set(True)
bpy.context.view_layer.objects.active = bodyMeshU      # active = source
bpy.ops.object.data_transfer(data_type="VGROUP_WEIGHTS", vert_mapping="POLYINTERP_NEAREST",
                             layers_select_src="ALL", layers_select_dst="NAME", mix_mode="REPLACE")
bpy.ops.object.select_all(action="DESELECT")
gMesh.select_set(True); armU.select_set(True)
bpy.context.view_layer.objects.active = armU
bpy.ops.object.parent_set(type="ARMATURE")

bpy.data.objects.remove(bodyMeshU, do_unlink=True)
print("[rig] export objs:", [o.name for o in bpy.context.scene.objects if o.type in ("MESH", "ARMATURE")])
bpy.ops.export_scene.gltf(filepath=OUT, export_format="GLB",
                          export_skins=True, export_animations=False, export_morph=False)
print("[rig] wrote", OUT)
