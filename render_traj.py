"""Top-down trajectory + body-facing render (ground truth from skeleton joints).

Draws the root XZ path and, at sampled frames, the direction the TOES point
(foot - ankle, projected to XZ) and the shoulder line. If the toe arrows point
opposite to the path of travel, the clip is a genuine moonwalk in the DATA
(not a viewer artifact). Convention-free: uses real joint positions.
"""
import math, requests, numpy as np
from PIL import Image, ImageDraw

API = "http://localhost:7862"
# joint indices
PEL, LANK, RANK, LFOOT, RFOOT, LSH, RSH, HEAD = 0, 7, 8, 10, 11, 16, 17, 15
W = H = 520
PAD = 50


def toe_forward(j):  # XZ unit vector the toes point along (where you face)
    v = (j[LFOOT] - j[LANK]) + (j[RFOOT] - j[RANK])
    v = np.array([v[0], v[2]])
    n = np.linalg.norm(v)
    return v / n if n > 1e-6 else np.array([0.0, 0.0])


def render(name, rec, path):
    rp = np.asarray(rec["root_positions"], float)[:, [0, 2]]   # [F,2] XZ
    pj = np.asarray(rec["posed_joints"], float)                # [F,J,3]
    F = len(rp)
    allx = rp[:, 0]; allz = rp[:, 1]
    xmin, xmax = allx.min(), allx.max(); zmin, zmax = allz.min(), allz.max()
    span = max(xmax - xmin, zmax - zmin, 1.0) * 1.15
    cx, cz = (xmin + xmax) / 2, (zmin + zmax) / 2

    def px(x, z):  # world XZ -> image px (X right, Z up)
        sx = (x - cx) / span + 0.5
        sz = (z - cz) / span + 0.5
        return PAD + sx * (W - 2 * PAD), (H - PAD) - sz * (H - 2 * PAD)

    img = Image.new("RGB", (W, H), (24, 26, 32))
    d = ImageDraw.Draw(img)
    # grid
    for g in range(-5, 6):
        x0, y0 = px(g, zmin - 5); x1, y1 = px(g, zmax + 5)
        d.line([x0, y0, x1, y1], fill=(40, 44, 52))
        x0, y0 = px(xmin - 5, g); x1, y1 = px(xmax + 5, g)
        d.line([x0, y0, x1, y1], fill=(40, 44, 52))
    # path
    pts = [px(x, z) for x, z in rp]
    d.line(pts, fill=(111, 185, 140), width=2)
    # facing arrows at sampled frames
    travel_dots = []
    for i in range(0, F, max(1, F // 12)):
        fwd = toe_forward(pj[i])
        bx, bz = rp[i]
        x0, y0 = px(bx, bz)
        x1, y1 = px(bx + fwd[0] * 0.45, bz + fwd[1] * 0.45)
        d.line([x0, y0, x1, y1], fill=(255, 150, 60), width=2)  # toe-forward (orange)
        d.ellipse([x1 - 3, y1 - 3, x1 + 3, y1 + 3], fill=(255, 150, 60))
        # shoulder line (cyan)
        sl = pj[i][LSH][[0, 2]]; sr = pj[i][RSH][[0, 2]]
        sx0, sy0 = px(sl[0], sl[1]); sx1, sy1 = px(sr[0], sr[1])
        d.line([sx0, sy0, sx1, sy1], fill=(90, 200, 230), width=2)
        if i + 1 < F:
            v = rp[min(i + 5, F - 1)] - rp[i]
            nv = np.linalg.norm(v)
            if nv > 1e-4:
                travel_dots.append(float(np.dot(v / nv, fwd)))
    # S / E
    sx, sy = px(rp[0, 0], rp[0, 1]); d.ellipse([sx - 6, sy - 6, sx + 6, sy + 6], fill=(80, 230, 120))
    ex, ey = px(rp[-1, 0], rp[-1, 1]); d.ellipse([ex - 6, ey - 6, ex + 6, ey + 6], fill=(230, 80, 80))
    md = float(np.mean(travel_dots)) if travel_dots else float("nan")
    d.text((10, 10), f"{name}  net=({rp[-1,0]-rp[0,0]:+.2f},{rp[-1,1]-rp[0,1]:+.2f})  toe-vs-travel={md:+.2f}", fill=(220, 220, 230))
    d.text((10, 26), "orange=toes point (facing)  cyan=shoulders  green=start red=end  +1 forward / -1 moonwalk", fill=(150, 150, 160))
    img.save(path)
    print(f"{name}: toe-vs-travel mean = {md:+.3f}  -> {path}")
    return md


if __name__ == "__main__":
    r1 = requests.post(f"{API}/generate", json={"prompt": "a person walks forward", "seconds": 2.5}, timeout=600).json()
    r2 = requests.post(f"{API}/generate_continue", json={
        "source_id": r1["id"], "prompt": "a person keeps walking forward",
        "seconds": 2.5, "source_frame": r1["num_frames"] - 1, "stitch": False,
    }, timeout=600).json()
    render("ROOT", r1, "/home/paul/projects/kimodo/_traj_root.png")
    render("CONT", r2, "/home/paul/projects/kimodo/_traj_cont.png")
