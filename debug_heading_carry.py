"""Decisive test of the 'branched move walks backward' bug.

(a) heading carry: when the parent ends FACING a non-trivial direction, does the
    child's START facing match the parent's END facing? (off by ~180 => the bug)
(b) stitched path: stitch parent+child and render the combined ground trail — if
    the child segment doubles back toward the parent, the bug is reproduced.

Facing is read convention-free from the toes (foot - ankle, XZ).
"""
import math, requests, numpy as np
from PIL import Image, ImageDraw

API = "http://localhost:7862"
LANK, RANK, LFOOT, RFOOT = 7, 8, 10, 11


def toe_forward(j):
    v = (j[LFOOT] - j[LANK]) + (j[RFOOT] - j[RANK])
    v = np.array([v[0], v[2]]); n = np.linalg.norm(v)
    return v / n if n > 1e-6 else np.array([0.0, 1.0])


def ang(v):
    return math.degrees(math.atan2(v[0], v[1]))  # 0=+Z, 90=+X


def render_path(name, recs_colors, path):
    allxz = np.vstack([np.asarray(r["root_positions"], float)[:, [0, 2]] for r, _ in recs_colors])
    xmin, xmax = allxz[:, 0].min(), allxz[:, 0].max()
    zmin, zmax = allxz[:, 1].min(), allxz[:, 1].max()
    W = HH = 560; PAD = 50
    span = max(xmax - xmin, zmax - zmin, 1.0) * 1.2
    cx, cz = (xmin + xmax) / 2, (zmin + zmax) / 2
    def px(x, z):
        return PAD + ((x - cx) / span + 0.5) * (W - 2 * PAD), (HH - PAD) - ((z - cz) / span + 0.5) * (HH - 2 * PAD)
    img = Image.new("RGB", (W, HH), (24, 26, 32)); d = ImageDraw.Draw(img)
    for g in range(-8, 9):
        d.line([*px(g, zmin - 8), *px(g, zmax + 8)], fill=(40, 44, 52))
        d.line([*px(xmin - 8, g), *px(xmax + 8, g)], fill=(40, 44, 52))
    for rec, col in recs_colors:
        rp = np.asarray(rec["root_positions"], float)[:, [0, 2]]
        pj = np.asarray(rec["posed_joints"], float)
        d.line([px(x, z) for x, z in rp], fill=col, width=2)
        for i in range(0, len(rp), max(1, len(rp) // 10)):
            f = toe_forward(pj[i]); bx, bz = rp[i]
            d.line([*px(bx, bz), *px(bx + f[0] * 0.4, bz + f[1] * 0.4)], fill=(255, 150, 60), width=2)
        d.ellipse([*[c - 6 for c in px(rp[0, 0], rp[0, 1])], *[c + 6 for c in px(rp[0, 0], rp[0, 1])]], fill=(80, 230, 120))
        d.ellipse([*[c - 6 for c in px(rp[-1, 0], rp[-1, 1])], *[c + 6 for c in px(rp[-1, 0], rp[-1, 1])]], fill=(230, 80, 80))
    d.text((10, 10), name, fill=(220, 220, 230))
    img.save(path); print("  ->", path)


if __name__ == "__main__":
    # Parent that ENDS turned (so heading carry is non-trivial).
    print("genRoot: walk forward then turn right 90 degrees")
    r1 = requests.post(f"{API}/generate", json={"prompt": "a person walks forward then turns 90 degrees to the right", "seconds": 3.0}, timeout=600).json()
    pj1 = np.asarray(r1["posed_joints"], float)
    end_face = toe_forward(pj1[-1])
    print(f"  parent END facing  = ({end_face[0]:+.2f},{end_face[1]:+.2f})  ang={ang(end_face):+.0f}deg")

    print("genContinue: keep walking forward (from last frame)")
    r2 = requests.post(f"{API}/generate_continue", json={
        "source_id": r1["id"], "prompt": "a person keeps walking forward",
        "seconds": 2.5, "source_frame": r1["num_frames"] - 1, "stitch": False,
    }, timeout=600).json()
    pj2 = np.asarray(r2["posed_joints"], float)
    start_face = toe_forward(pj2[0])
    rp2 = np.asarray(r2["root_positions"], float)[:, [0, 2]]
    net2 = rp2[-1] - rp2[0]; net2n = net2 / (np.linalg.norm(net2) + 1e-9)
    print(f"  child  START facing = ({start_face[0]:+.2f},{start_face[1]:+.2f})  ang={ang(start_face):+.0f}deg")
    print(f"  child  net travel   = ({net2[0]:+.2f},{net2[1]:+.2f})  ang={ang(net2n):+.0f}deg")
    carry = float(np.dot(end_face, start_face))
    travel_vs_face = float(np.dot(net2n, start_face))
    print(f"  CARRY  end_face . start_face = {carry:+.2f}   (+1 good, -1 = child faces BACKWARD vs parent)")
    print(f"  child  travel . start_face   = {travel_vs_face:+.2f}   (+1 child walks forward, -1 moonwalk)")

    # (b) stitched path
    print("stitch_path([parent, child]):")
    st = requests.post(f"{API}/stitch_path", json={"ids": [r1["id"], r2["id"]], "save": False}, timeout=600).json()
    render_path("STITCHED parent+child (orange=toes, green=start red=end)", [(st, (111, 185, 140))], "/home/paul/projects/kimodo/_traj_stitch.png")
    render_path("PARENT only", [(r1, (130, 160, 230))], "/home/paul/projects/kimodo/_traj_parent.png")
    render_path("CHILD only", [(r2, (230, 160, 130))], "/home/paul/projects/kimodo/_traj_child.png")
