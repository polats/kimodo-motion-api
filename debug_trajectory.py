"""Debug: does a generated continuation travel forward (in its facing dir) or backward?

Mirrors the kata-builder flow: genRoot(walk) -> genContinue(keep walking) from a frame.
Prints the ground trail (root XZ path) + facing alignment for each clip.
"""
import sys, math, requests, numpy as np

API = "http://localhost:7862"
LHIP, RHIP, ROOT = 1, 2, 0  # SMPL-X joint indices


def heading_xz(joints):  # joints: [J,3] -> world-forward unit vec (x,z), kimodo convention
    d = joints[RHIP] - joints[LHIP]          # right - left hip
    theta = math.atan2(d[2], -d[0])          # compute_heading_angle
    return np.array([math.sin(theta), math.cos(theta)])


def analyze(name, rec):
    rp = np.asarray(rec["root_positions"], dtype=float)        # [F,3]
    pj = np.asarray(rec["posed_joints"], dtype=float)          # [F,J,3]
    F = len(rp)
    xz = rp[:, [0, 2]]
    net = xz[-1] - xz[0]
    fwd0 = heading_xz(pj[0])
    fwdL = heading_xz(pj[-1])
    netn = net / (np.linalg.norm(net) + 1e-9)
    # per-step velocity vs facing
    vel = np.diff(xz, axis=0)
    dots = []
    for i in range(len(vel)):
        v = vel[i]
        if np.linalg.norm(v) < 1e-5:
            continue
        f = heading_xz(pj[i])
        dots.append(float(np.dot(v / np.linalg.norm(v), f)))
    mean_dot = float(np.mean(dots)) if dots else float("nan")
    print(f"\n=== {name}  ({F} frames, id={rec.get('id')}) ===")
    print(f"  prompt        : {rec.get('prompt')!r}")
    print(f"  start XZ      : ({xz[0,0]:+.3f}, {xz[0,1]:+.3f})")
    print(f"  end   XZ      : ({xz[-1,0]:+.3f}, {xz[-1,1]:+.3f})")
    print(f"  net disp      : ({net[0]:+.3f}, {net[1]:+.3f})  |{np.linalg.norm(net):.3f}|")
    print(f"  facing @start : ({fwd0[0]:+.3f}, {fwd0[1]:+.3f})")
    print(f"  facing @end   : ({fwdL[0]:+.3f}, {fwdL[1]:+.3f})")
    print(f"  net·facing0   : {float(np.dot(netn, fwd0)):+.3f}   (+1 forward, -1 backward)")
    print(f"  mean step·fac : {mean_dot:+.3f}   (per-frame velocity vs instantaneous facing)")
    # ascii ground trail (top-down, X right, Z up)
    return xz, fwd0


def ascii_trail(label, paths):
    allp = np.vstack([p for p, _ in paths])
    xmin, xmax = allp[:, 0].min(), allp[:, 0].max()
    zmin, zmax = allp[:, 1].min(), allp[:, 1].max()
    W, H = 60, 22
    def to_cell(x, z):
        cx = int((x - xmin) / (xmax - xmin + 1e-9) * (W - 1))
        cz = int((z - zmin) / (zmax - zmin + 1e-9) * (H - 1))
        return cx, H - 1 - cz
    grid = [[" "] * W for _ in range(H)]
    marks = "12"
    for k, (p, f) in enumerate(paths):
        for i, (x, z) in enumerate(p):
            cx, cz = to_cell(x, z)
            grid[cz][cx] = marks[k] if 0 <= k < len(marks) else "*"
        # S=start, E=end
        sx, sz = to_cell(p[0, 0], p[0, 1]); grid[sz][sx] = "S"
        ex, ez = to_cell(p[-1, 0], p[-1, 1]); grid[ez][ex] = "E"
    print(f"\n--- {label} ground trail (top-down: X→right, Z→up; S=start E=end, clip1='1' clip2='2') ---")
    print(f"    X:[{xmin:+.2f},{xmax:+.2f}] Z:[{zmin:+.2f},{zmax:+.2f}]")
    for row in grid:
        print("    " + "".join(row))


if __name__ == "__main__":
    root_prompt = "a person walks forward"
    cont_prompt = "a person keeps walking forward"
    secs = 2.5

    print("genRoot:", root_prompt)
    r1 = requests.post(f"{API}/generate", json={"prompt": root_prompt, "seconds": secs}, timeout=600).json()
    src_id = r1["id"]
    frame = r1["num_frames"] - 1

    print(f"genContinue from f{frame}:", cont_prompt)
    r2 = requests.post(f"{API}/generate_continue", json={
        "source_id": src_id, "prompt": cont_prompt, "seconds": secs,
        "source_frame": frame, "stitch": False,
    }, timeout=600).json()

    xz1, f1 = analyze("ROOT (clip1)", r1)
    xz2, f2 = analyze("CONTINUATION (clip2)", r2)
    ascii_trail("ROOT", [(xz1, f1)])
    ascii_trail("CONTINUATION", [(xz2, f2)])
