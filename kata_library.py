#!/usr/bin/env python3
"""
Build a kata MOVE TREE in the kimodo motion store.

Each node is one move, generated to CONTINUE from its parent's end pose
(POST /generate_continue, stitch=False) — so it's an individually-viewable clip
that also flows on from its parent. Roots are generated fresh (POST /generate).
The tree shows up in the /kata viewer; selecting a path regenerates it as one
continuous motion so the character walks the whole kata without resetting.

Spec format: (key, parent_key | None, prompt, seconds).
Parents must appear before their children. Grounded in Shotokan kata (Heian
Shodan deep spine + Taikyoku + a kicking and a hand-combo kata + a basics fan-out).
"""
import json, sys, time, urllib.request

URL = "http://127.0.0.1:7862"
LEAD = "a martial artist "  # keep generations on-style / on-subject

def post(path, body):
    req = urllib.request.Request(URL + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)

# (key, parent, prompt, seconds)
NODES = [
    # ===== Heian Shodan — deep complete kata (the showcase spine) =====
    ("hs01", None,   LEAD + "in a ready stance turns ninety degrees to the left into a front stance and performs a low sweeping down block", 3.0),
    ("hs02", "hs01", "steps forward into a front stance and throws a lunge punch to the stomach", 2.2),
    ("hs03", "hs02", "turns one hundred eighty degrees to the right into a front stance with a low down block", 2.5),
    ("hs04", "hs03", "steps forward and throws a lunge punch", 2.2),
    ("hs05", "hs04", "turns ninety degrees to the left into a front stance with a low down block", 2.5),
    ("hs06", "hs05", "steps forward into a front stance with a rising block", 2.2),
    ("hs07", "hs06", "steps forward with another rising block", 2.0),
    ("hs08", "hs07", "steps forward with a rising block and shouts", 2.2),
    ("hs09", "hs08", "turns to the left into a front stance with a low down block", 2.5),
    ("hs10", "hs09", "steps forward and throws a lunge punch", 2.2),
    ("hs11", "hs10", "turns one hundred eighty degrees right into a low down block", 2.5),
    ("hs12", "hs11", "steps forward and throws a lunge punch", 2.2),
    ("hs13", "hs12", "turns ninety degrees left into a low down block", 2.5),
    ("hs14", "hs13", "steps forward and throws a lunge punch", 2.0),
    ("hs15", "hs14", "steps forward and throws a second lunge punch", 2.0),
    ("hs16", "hs15", "steps forward and throws a third lunge punch and shouts", 2.2),
    ("hs17", "hs16", "turns to the left into a back stance with a knife-hand block", 2.5),
    ("hs18", "hs17", "steps diagonally forward with a knife-hand block", 2.2),
    ("hs19", "hs18", "turns into a back stance with a knife-hand block on the other side", 2.5),
    ("hs20", "hs19", "steps diagonally with a knife-hand block", 2.2),
    ("hs21", "hs20", "draws the back foot in and returns to a ready stance", 2.5),

    # ===== Branches off Heian Shodan (variations to view side-by-side) =====
    ("hs02b", "hs01", "steps forward and throws a reverse punch instead", 2.2),
    ("hs06b", "hs05", "steps forward and throws a front kick instead of a block", 2.2),
    ("hs06c", "hs05", "steps forward with an inside forearm block", 2.2),
    ("hs10b", "hs09", "steps forward with a roundhouse kick", 2.5),
    ("hs17b", "hs16", "spins into a back stance with a double knife-hand block", 2.6),

    # ===== Taikyoku Shodan — the basic kata (second deep spine) =====
    ("tk01", None,   LEAD + "from a ready stance turns left into a front stance with a low down block", 2.6),
    ("tk02", "tk01", "steps forward and throws a lunge punch", 2.0),
    ("tk03", "tk02", "turns one hundred eighty right into a low down block", 2.5),
    ("tk04", "tk03", "steps forward and throws a lunge punch", 2.0),
    ("tk05", "tk04", "turns ninety left into a low down block", 2.5),
    ("tk06", "tk05", "steps forward with a lunge punch", 2.0),
    ("tk07", "tk06", "steps forward with a lunge punch", 2.0),
    ("tk08", "tk07", "steps forward with a lunge punch and shouts", 2.2),
    ("tk09", "tk08", "turns left into a low down block", 2.5),
    ("tk10", "tk09", "steps forward with a lunge punch", 2.0),
    ("tk11", "tk10", "turns one hundred eighty right into a low down block", 2.5),
    ("tk12", "tk11", "steps forward with a lunge punch", 2.0),
    ("tk13", "tk12", "turns left into a low down block", 2.5),
    ("tk14", "tk13", "steps forward with three quick lunge punches and shouts", 2.6),
    ("tk15", "tk14", "draws back into a ready stance", 2.4),

    # ===== A kicking combination kata =====
    ("kk01", None,   LEAD + "stands in a fighting stance with fists up", 2.0),
    ("kk02", "kk01", "throws a front snap kick with the rear leg", 2.0),
    ("kk03", "kk02", "sets the foot down forward and throws a roundhouse kick", 2.2),
    ("kk04", "kk03", "lands and throws a side kick to the side", 2.2),
    ("kk05", "kk04", "spins and throws a back kick", 2.4),
    ("kk06", "kk05", "recovers into a fighting stance", 2.0),
    ("kk03b", "kk02", "lands and throws a spinning hook kick instead", 2.6),
    ("kk04b", "kk03", "jumps and throws a flying front kick", 2.4),
    ("kk02b", "kk01", "throws a low kick to the shin", 1.8),
    ("kk05b", "kk04", "drops low and sweeps with the leg", 2.2),

    # ===== A hand-combination kata =====
    ("hc01", None,   LEAD + "stands in a fighting stance and throws a quick jab with the lead hand", 1.8),
    ("hc02", "hc01", "follows with a reverse cross punch", 1.8),
    ("hc03", "hc02", "throws a lead hook punch", 1.8),
    ("hc04", "hc03", "finishes with a spinning back-fist strike", 2.2),
    ("hc05", "hc04", "recovers guard in a fighting stance", 1.8),
    ("hc03b", "hc02", "throws an uppercut instead", 1.8),
    ("hc04b", "hc03", "follows with an elbow strike", 1.8),
    ("hc02b", "hc01", "slips to the side and throws a body hook", 1.8),

    # ===== Basics fan-out: a stance that branches into technique families =====
    ("bx01", None,   LEAD + "settles into a deep horse-riding stance", 2.2),
    ("bx_p1", "bx01", "throws a straight punch to the front from horse stance", 1.8),
    ("bx_p2", "bx_p1", "pulls back and throws a punch with the other hand", 1.8),
    ("bx_b1", "bx01", "performs a rising block from horse stance", 1.8),
    ("bx_b2", "bx01", "performs an outside-to-inside forearm block", 1.8),
    ("bx_b3", "bx01", "performs a knife-hand block to the side", 1.8),
    ("bx_s1", "bx01", "delivers a back-fist strike to the side", 1.8),
    ("bx_s2", "bx01", "delivers a hammer-fist strike downward", 1.8),
    ("bx_s3", "bx01", "delivers an elbow strike to the side", 1.8),
    ("bx_e1", "bx_s3", "follows with an upward elbow strike", 1.8),
    ("bx_k1", "bx01", "rises and throws a front kick from horse stance", 2.0),
    ("bx_k2", "bx01", "rises and throws a side kick", 2.0),
    ("bx_k3", "bx_k1", "lands and follows with a roundhouse kick", 2.2),
    ("bx_p3", "bx_p1", "drops into a lunge punch forward", 2.0),
    ("bx_b4", "bx_b1", "follows the rising block with a counter punch", 1.8),

    # ===== Heian Nidan — a second deep kata =====
    ("hn01", None,   LEAD + "from a ready stance steps to the left into a back stance with a combined high-and-low block", 2.8),
    ("hn02", "hn01", "draws the hand back and strikes with a back-fist to the side", 2.0),
    ("hn03", "hn02", "turns to the right into a back stance with a combined block", 2.6),
    ("hn04", "hn03", "strikes with a back-fist to the side", 2.0),
    ("hn05", "hn04", "turns to the front and raises into a high block while looking to the side", 2.4),
    ("hn06", "hn05", "executes a side snap kick and a back-fist strike", 2.4),
    ("hn07", "hn06", "lands in a front stance with a knife-hand strike", 2.2),
    ("hn08", "hn07", "turns to the other side into a high block", 2.4),
    ("hn09", "hn08", "executes a side snap kick and back-fist", 2.4),
    ("hn10", "hn09", "lands with a knife-hand strike in front stance", 2.2),
    ("hn11", "hn10", "steps forward with a spear-hand thrust to the body", 2.2),
    ("hn12", "hn11", "turns into a back stance with a knife-hand block", 2.5),
    ("hn13", "hn12", "steps forward with a knife-hand block", 2.0),
    ("hn14", "hn13", "steps forward with a knife-hand block", 2.0),
    ("hn15", "hn14", "turns with a knife-hand block and shouts", 2.4),
    ("hn16", "hn15", "steps forward with a rising block and a reverse punch", 2.4),
    ("hn17", "hn16", "draws back into a ready stance", 2.4),
    # Heian Nidan branches
    ("hn06b", "hn05", "throws a front kick and a double punch instead", 2.4),
    ("hn11b", "hn10", "throws a low front kick before the spear-hand", 2.2),
    ("hn16b", "hn15", "finishes with a jumping front kick", 2.4),
]

def main():
    # connectivity check
    try:
        urllib.request.urlopen(URL + "/animations", timeout=10)
    except Exception as e:
        print(f"motion API not reachable at {URL}: {e}"); sys.exit(1)

    ids = {}        # key -> generated clip id
    ok = fail = 0
    t0 = time.time()
    for i, (key, parent, prompt, secs) in enumerate(NODES, 1):
        try:
            if parent is None:
                rec = post("/generate", {"prompt": prompt, "seconds": secs})
            else:
                if parent not in ids:
                    print(f"[{i}/{len(NODES)}] SKIP {key}: parent {parent} not generated"); fail += 1; continue
                rec = post("/generate_continue", {"source_id": ids[parent], "prompt": prompt,
                                                  "seconds": secs, "stitch": False})
            ids[key] = rec["id"]
            ok += 1
            print(f"[{i}/{len(NODES)}] {key:6} <- {parent or 'ROOT':6}  id={rec['id']}  ({rec.get('num_frames')}f)  {int(time.time()-t0)}s")
        except Exception as e:
            fail += 1
            print(f"[{i}/{len(NODES)}] FAIL {key}: {type(e).__name__}: {e}")
    print(f"\ndone: ok={ok} fail={fail} in {int(time.time()-t0)}s")
    # write the key->id map for reference
    with open("/tmp/kata_library_ids.json", "w") as f:
        json.dump(ids, f, indent=2)
    print("wrote /tmp/kata_library_ids.json")

if __name__ == "__main__":
    main()
