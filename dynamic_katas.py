#!/usr/bin/env python3
"""Build katas whose moves END mid-action (punch extended / foot up) via
end_on_peak, so each next move STARTS from a non-grounded pose and the kata
ends on a punch or high kick. Renders each kata's move-endings for eyeballing."""
import json, urllib.request
import numpy as np
from render_pose import fetch, panel
from PIL import Image, ImageDraw

URL = "http://127.0.0.1:7862"
def post(path, body):
    r = urllib.request.Request(URL + path, data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"}, method="POST")
    return json.load(urllib.request.urlopen(r, timeout=120))

# (prompt, seconds, end_on_peak)  — end_on_peak makes the move END mid-action.
KATAS = {
    "D1_highkick_finish": [
        ("a martial artist steps into a fighting stance with fists raised", 2.0, None),
        ("throws a straight punch with the rear hand", 2.0, "punch"),
        ("throws a high front kick to the head", 2.5, "kick"),          # ENDS on high kick
    ],
    "D2_punch_finish": [
        ("a martial artist stands in a fighting stance", 2.0, None),
        ("throws a front snap kick", 2.2, "kick"),                       # ends foot-up
        ("lands the foot forward and throws a lunging straight punch", 2.2, "punch"),  # starts foot-up, ENDS on punch
    ],
    "D3_sidekick_finish": [
        ("a martial artist stands in a fighting stance", 2.0, None),
        ("throws a quick lead jab", 1.8, "punch"),                       # ends arm-out
        ("throws a side kick to the side", 2.5, "kick"),                 # starts arm-out, ENDS on side kick
    ],
    "D4_double_kick_finish": [
        ("a martial artist stands in a fighting stance", 2.0, None),
        ("throws a front snap kick with the rear leg", 2.2, "kick"),     # ends foot-up
        ("throws another front kick with the other leg", 2.2, "kick"),   # starts foot-up, ENDS on kick
    ],
}

def montage(ids, name):
    panels = []
    for cid in ids:
        d = fetch(cid); P = np.array(d["posed_joints"])
        panels.append((panel(P, P.shape[0] - 1), d["prompt"]))   # each move's LAST frame
    W = sum(p.width for p, _ in panels); H = panels[0][0].height + 22
    img = Image.new("RGB", (W, H), (16, 16, 18)); dd = ImageDraw.Draw(img); x = 0
    for p, cap in panels:
        img.paste(p, (x, 20)); dd.text((x + 6, 5), cap[:30], fill=(210, 210, 210)); x += p.width
    dd.text((4, H - 15), name + "  (last frame of each move)", fill=(150, 255, 150))
    img.save(f"/tmp/{name}.png")

allids = {}
for name, moves in KATAS.items():
    prev, ids = None, []
    for i, (prompt, secs, peak) in enumerate(moves):
        body = {"prompt": prompt, "seconds": secs}
        if peak: body["end_on_peak"] = peak
        if prev is None:
            rec = post("/generate", body)
        else:
            body.update({"source_id": prev, "stitch": False})
            rec = post("/generate_continue", body)
        prev = rec["id"]; ids.append(rec["id"])
        print(f"{name} [{i}] {rec['id']} {rec['num_frames']}f  {prompt[:42]}")
    allids[name] = ids
    montage(ids, name)
    print("  saved /tmp/%s.png" % name)

json.dump(allids, open("/tmp/dynamic_katas.json", "w"), indent=2)
print("ids -> /tmp/dynamic_katas.json")
