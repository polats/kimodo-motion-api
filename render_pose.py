#!/usr/bin/env python3
"""Render a clip's SMPL-X skeleton (22 joints) to a PNG montage so poses can be
eyeballed: feet = blue, hands = orange, ground line drawn. Auto-orients to the
side that shows the most action (kick/punch reads clearly)."""
import sys, json, urllib.request
import numpy as np
from PIL import Image, ImageDraw

URL = "http://127.0.0.1:7862"
PARENT = [-1, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9, 12, 13, 14, 16, 17, 18, 19]
FEET = {7, 8, 10, 11}      # ankles + feet
HANDS = {20, 21}           # wrists

def fetch(cid):
    return json.load(urllib.request.urlopen(f"{URL}/animations/{cid}", timeout=30))

def panel(P, frame, W=240, H=360):
    J = P[frame]
    x, y, z = J[:, 0], J[:, 1], J[:, 2]
    h = x if x.std() >= z.std() else z      # horizontal = the action axis
    img = Image.new("RGB", (W, H), (26, 26, 30)); d = ImageDraw.Draw(img)
    pad = 36
    ymin, ymax = min(0.0, float(y.min())), max(float(y.max()), 1.0)
    hmin, hmax = float(h.min()), float(h.max())
    s = min((W - 2*pad) / max(0.6, hmax - hmin), (H - 2*pad) / max(0.6, ymax - ymin))
    cx = W/2 - (hmin + hmax)/2 * s
    def pt(i): return (cx + h[i]*s, H - pad - (y[i]-ymin)*s)
    gy = H - pad - (0 - ymin)*s
    d.line([(0, gy), (W, gy)], fill=(70, 70, 84), width=1)   # ground (y=0)
    for i, p in enumerate(PARENT):
        if p < 0: continue
        d.line([pt(i), pt(p)], fill=(170, 175, 190), width=3)
    for i in range(len(J)):
        a = pt(i); r = 4 if (i in FEET or i in HANDS) else 3
        c = (110, 200, 255) if i in FEET else (255, 170, 70) if i in HANDS else (205, 205, 215)
        d.ellipse([a[0]-r, a[1]-r, a[0]+r, a[1]+r], fill=c)
    return img

def render(cid, out, frames=None, label=""):
    rec = fetch(cid)
    P = np.array(rec["posed_joints"]); T = P.shape[0]
    if frames is None:
        frames = [0, T//3, 2*T//3, T-1]
    panels = [panel(P, f) for f in frames]
    W = sum(p.width for p in panels); H = panels[0].height + 22
    img = Image.new("RGB", (W, H), (16, 16, 18)); dd = ImageDraw.Draw(img); x = 0
    for f, p in zip(frames, panels):
        img.paste(p, (x, 20)); dd.text((x+6, 5), f"frame {f}", fill=(210, 210, 210)); x += p.width
    dd.text((4, H-15), (label or rec.get("prompt", ""))[:110], fill=(150, 255, 150))
    img.save(out); print("saved", out, "frames", frames, "joints", P.shape)

if __name__ == "__main__":
    # args: clip_id out.png [label]
    cid, out = sys.argv[1], sys.argv[2]
    render(cid, out, label=sys.argv[3] if len(sys.argv) > 3 else "")
