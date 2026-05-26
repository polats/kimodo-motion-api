#!/usr/bin/env python3
"""Add s&box garment(s) to the /kata clothing library — one command per .clothing.

For each garment it: reads the .clothing (variants + slot metadata), decompiles the
right mesh variant per body via VRF, extracts the albedo, rigs each onto that body's
UniRig skeleton (web/scripts/clothing_rig.py), and writes a manifest the viewer reads.

  python3 web/scripts/clothing_add.py <path/to/x.clothing> [more.clothing ...]

Bodies & their (REF rig, UniRig target, .clothing variant field):
  sausage → citizen_REF.fbx,             unirig_citizen,        Model
  male    → citizen_human_male_REF.fbx,  unirig_citizen_male,   HumanAltModel
  female  → citizen_human_female_REF.fbx,unirig_citizen_female, HumanAltFemaleModel
           (falls back to HumanAltModel + male REF when no female variant — as s&box does)
"""
import json, os, re, subprocess, sys, glob
from pathlib import Path

KIMODO = Path(__file__).resolve().parents[2]
PUBLIC = KIMODO / "web" / "public"
CLOTHING_OUT = PUBLIC / "clothing"
MANIFEST_DIR = KIMODO / ".kimodo-clothing"
RIG = KIMODO / "web" / "scripts" / "clothing_rig.py"
TMP = Path("/tmp/clothing_add"); TMP.mkdir(exist_ok=True)

def _find_vrf():
    if os.environ.get("KIMODO_VRF"): return os.environ["KIMODO_VRF"]
    for c in (Path.home() / ".local/share/source2viewer/Source2Viewer-CLI", "/tmp/vrf/cli/Source2Viewer-CLI"):
        if Path(c).exists(): return str(c)
    return str(Path.home() / ".local/share/source2viewer/Source2Viewer-CLI")
VRF = _find_vrf()
INSTALL = Path("/home/paul/.local/share/Steam/steamapps/common/sbox/addons/citizen/Assets/models")
REF = {
    "sausage": INSTALL / "citizen/citizen_REF.fbx",
    "male":    INSTALL / "citizen_human/citizen_human_male_REF.fbx",
    "female":  INSTALL / "citizen_human/citizen_human_female_REF.fbx",
}
BODY_GLB = {
    "sausage": PUBLIC / "models/unirig_citizen.glb",
    "male":    PUBLIC / "models/unirig_citizen_male.glb",
    "female":  PUBLIC / "models/unirig_citizen_female.glb",
}
CHAR_ID = {"sausage": "unirig_citizen", "male": "unirig_citizen_male", "female": "unirig_citizen_female"}

# .clothing Category → our viewer slot (one garment per slot)
CATEGORY_SLOT = {
    "Hat": "head", "HatUniform": "head", "HatCostume": "head", "HatBeanie": "head", "HatFormal": "head",
    "GlassesEye": "face", "GlassesSun": "face",
    "TShirt": "torso_under", "Tops": "torso_under", "Shirt": "torso_under", "Knitwear": "torso_under",
    "Cardigan": "torso_under", "Tanktop": "torso_under", "Bra": "torso_under",
    "Coat": "torso_over", "Jacket": "torso_over", "Vest": "torso_over", "Gilet": "torso_over",
    "Hoodie": "torso_over", "Suit": "torso_over",
    "Jeans": "legs", "Trousers": "legs", "Shorts": "legs", "Skirt": "legs",
    "Shoes": "feet", "Trainers": "feet", "Boots": "feet", "Heels": "feet", "Slippers": "feet",
    "Gloves": "hands",
}
SLOT_LAYER = {"head": 3, "face": 4, "torso_under": 1, "torso_over": 2, "legs": 1, "feet": 1, "hands": 2}

def sh(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)

def load_clothing(path):
    # Source .clothing is JSON. Cloud packages ship compiled .clothing_c, but the
    # DATA block embeds the full source JSON as a string — pull it back out.
    if str(path).endswith(".clothing_c"):
        raw = open(path, "rb").read().decode("latin-1", "ignore")
        m = re.search(r'\{"HasHumanSkin".*?"__version":\s*\d+\s*\}', raw)
        if not m: raise ValueError(f"no embedded clothing JSON in {path}")
        return json.loads(m.group(0))
    return json.load(open(path))

def find_ci(root, name):   # case-insensitive find of a basename under root
    hits = glob.glob(str(Path(root) / "**" / name), recursive=True)
    if hits: return hits[0]
    low = name.lower()
    for p in glob.glob(str(Path(root) / "**" / "*"), recursive=True):
        if os.path.basename(p).lower() == low: return p
    return None

def vmdl_c_for(item_dir, vmdl_path):
    # install: <basename>.vmdl_c next to the .clothing; cloud (download/assets): the
    # file is content-addressed as <stem>.<hash>.vmdl_c, so match by stem too.
    if not vmdl_path: return None
    base = os.path.basename(vmdl_path)
    exact = find_ci(item_dir, base + "_c")
    if exact: return exact
    stem = os.path.splitext(base)[0]
    hits = glob.glob(str(Path(item_dir) / "**" / f"{stem}.*.vmdl_c"), recursive=True)
    return hits[0] if hits else None

def decompile(src, out_glb):
    out_glb = Path(out_glb)
    r = sh([VRF, "-i", src, "-o", out_glb, "-d", "--gltf_export_format", "glb", "--gltf_export_animations"])
    if not out_glb.exists():
        print("  ! VRF failed:", r.stdout[-300:], r.stderr[-300:]); return None
    return out_glb

def extract_albedo(item_dir, out_dir, model_stem):
    # find the colour map (…_color_…generated.vtex_c) and decompile to PNG. A folder can
    # hold several garments'/variants' textures, so prefer the one matching THIS model's
    # name (e.g. polo_shirt_white → polo_shirt_white_color…), and avoid the lens map for
    # glasses. VRF ignores the -o filename for textures and writes its own name into the
    # -o directory, so we decompile into a clean dir and pick up the PNG it produced.
    cands = glob.glob(str(Path(item_dir) / "**" / "*color*.vtex_c"), recursive=True) \
        or glob.glob(str(Path(item_dir) / "**" / "*albedo*.vtex_c"), recursive=True)
    if not cands: return None
    nolens = lambda xs: [c for c in xs if "lens" not in os.path.basename(c).lower()]
    ms = (model_stem or "").lower()
    pref = [c for c in cands if os.path.basename(c).lower().startswith(ms)] if ms else []
    chosen = nolens(pref) or pref
    if not chosen:
        # No name match. In a flat shared dir (download/assets) that means a neighbour's
        # texture — skip it. In a scoped package subdir (the item's own folder, incl. cloud
        # <pkg>/body|legs/) the only colour map present IS this garment's, so take it.
        if Path(item_dir).name == "assets":
            return None
        chosen = nolens(cands) or cands
    cands = chosen
    out_dir = Path(out_dir)
    if out_dir.exists():
        for f in out_dir.glob("**/*.png"): f.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)
    sh([VRF, "-i", cands[0], "-o", str(out_dir / "albedo.png"), "-d"])
    pngs = glob.glob(str(out_dir / "**" / "*.png"), recursive=True)
    return pngs[0] if pngs else None

def rig(body, ref_fbx, garment_glb, out_glb, tex_png):
    args = ["blender", "-b", "-P", RIG, "--", BODY_GLB[body], ref_fbx, garment_glb, out_glb, tex_png or ""]
    r = sh(args)
    ok = Path(out_glb).exists()
    for ln in r.stdout.splitlines():
        if ln.startswith("[rig]"): print("    " + ln)
    if not ok: print("  ! rig failed:", r.stdout[-400:])
    return ok

def add(clothing_path):
    cp = Path(clothing_path); item_dir = cp.parent
    d = load_clothing(cp)
    gid = re.sub(r"\.[0-9a-f]{8,}$", "", cp.stem)   # strip cloud content-hash: poncho.1002c5… → poncho
    gid = re.sub(r"\.clothing$", "", gid)            # .clothing_c stems keep no extra suffix
    label = d.get("Title") or gid.replace("_", " ").title()
    cat = d.get("Category", "")
    slot = CATEGORY_SLOT.get(cat)
    if not slot:
        print(f"[add] {gid}: unknown Category {cat!r} — skipping"); return
    print(f"[add] {gid}  ({label})  cat={cat} slot={slot}")

    model_stem = os.path.splitext(os.path.basename(d.get("Model") or gid))[0]
    tex = extract_albedo(item_dir, TMP / f"{gid}_tex", model_stem)
    print(f"  albedo: {os.path.basename(tex) if tex else 'none'}")

    glb = {}
    for body in ("sausage", "male", "female"):
        if body == "female" and d.get("HumanAltFemaleModel"):
            variant, ref_fbx = d["HumanAltFemaleModel"], REF["female"]
        elif body == "female":
            variant, ref_fbx = d.get("HumanAltModel"), REF["male"]      # female fallback → male variant+rig
        elif body == "male":
            variant, ref_fbx = d.get("HumanAltModel"), REF["male"]
        else:
            variant, ref_fbx = d.get("Model"), REF["sausage"]
        vc = vmdl_c_for(item_dir, variant)
        if not vc:
            print(f"  {body}: no vmdl_c for {variant} — skipping body"); continue
        gar = decompile(vc, TMP / f"{gid}_{body}.glb")
        if not gar: continue
        out = CLOTHING_OUT / f"{gid}_{body}.glb"
        if rig(body, ref_fbx, gar, out, tex):
            glb[CHAR_ID[body]] = f"/clothing/{gid}_{body}.glb"

    if not glb:
        print(f"  ! {gid}: no bodies rigged"); return
    manifest = {
        "id": gid, "label": label, "category": cat, "slot": slot,
        "layer": SLOT_LAYER.get(slot, 1),
        "slotsUnder": d.get("SlotsUnder", 0), "slotsOver": d.get("SlotsOver", 0),
        "hideBody": d.get("HideBody", 0), "glb": glb,
    }
    MANIFEST_DIR.mkdir(exist_ok=True)
    json.dump(manifest, open(MANIFEST_DIR / f"{gid}.json", "w"), indent=2)
    print(f"  ✓ {gid}: {len(glb)} bodies → manifest written")

if __name__ == "__main__":
    if not Path(VRF).exists():
        sys.exit(f"VRF CLI not found at {VRF} (set KIMODO_VRF)")
    CLOTHING_OUT.mkdir(parents=True, exist_ok=True)
    for p in sys.argv[1:]:
        add(p)
