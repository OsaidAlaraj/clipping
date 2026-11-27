"""Framing metric on the SOURCE: how often the biggest face is fully inside the 9:16 crop the tracker chose.
Usage: python framing.py [tracker_module:function]  (default: clipping.faces.track).  Source detections are cached per window."""
import json, sys, importlib
from pathlib import Path
import numpy as np, cv2
sys.path.insert(0, r"L:\Projects\Clipping\.claude\worktrees\cut-quality-3d8a7d\backend")
from clipping import faces
import os
if os.environ.get("CUTQ_OLD"):      # the tracker as it was before the per-shot path
    faces.SHOT_AWARE = False; faces.FILL_SHORT_TRACKS = False
only = set(os.environ.get("CUTQ_ONLY", "").split(",")) - {""}
HERE = Path(__file__).parent; CACHE = Path(r"L:\Projects\Clipping\.data\tmp\cutq\facecache"); CACHE.mkdir(parents=True, exist_ok=True)
STEP = 0.25

def source_faces(w):
    f = CACHE / f"{w['id']}.json"
    if f.exists(): return json.load(open(f))
    det = faces._load(); cap = cv2.VideoCapture(w["src"]); out = []
    n = int((w["end_s"] - w["start_s"]) / STEP)
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_MSEC, (w["start_s"] + i * STEP) * 1000); ok, fr = cap.read()
        if not ok: out.append([]); continue
        _, found = det.detect(cv2.resize(fr, (faces.FRAME_W, faces.FRAME_H)))
        out.append([] if found is None else [[float(q[0]) / faces.FRAME_W, float(q[2]) / faces.FRAME_W, float(q[2] * q[3]) / (faces.FRAME_W * faces.FRAME_H), float(q[-1])] for q in found])
    json.dump(out, open(f, "w")); return out

def centre_at(path, t):
    if not path: return 0.5
    if t <= path[0][0]: return path[0][1]
    for (t0, c0), (t1, c1) in zip(path, path[1:]):
        if t < t1: return c0 + (c1 - c0) * (t - t0) / max(t1 - t0, 1e-6)
    return path[-1][1]

def evaluate(w, path, src_w=1920, src_h=1080):
    share = src_h * 9 / 16 / src_w; fc = source_faces(w); ok = tot = cut = 0; lost = []
    for i, fl in enumerate(fc):
        big = [f for f in fl if f[2] > 0.004 and f[3] > 0.7]
        if not big: continue
        f = max(big, key=lambda q: q[2]); c = centre_at(path, i * STEP) if path else 0.5
        lo, hi = c - share / 2, c + share / 2; tot += 1
        inside = f[0] >= lo - 0.005 and f[0] + f[1] <= hi + 0.005
        ok += inside; cut += (not inside) and (f[0] + f[1] / 2 > lo and f[0] + f[1] / 2 < hi)
    return {"samples": tot, "face_in_crop_pct": round(100 * ok / max(tot, 1), 1), "face_half_cut_pct": round(100 * cut / max(tot, 1), 1)}

if __name__ == "__main__":
    spec = sys.argv[1] if len(sys.argv) > 1 else "clipping.faces:track"
    mod, fn = spec.split(":"); track = getattr(importlib.import_module(mod), fn)
    wins = [w for w in json.load(open(HERE / "windows.json", encoding="utf8")) if not only or w["id"].split("_")[0] in only or w["id"] in only]; rows = []
    for w in wins:
        got = track(Path(w["src"]), {"start_s": w["start_s"], "end_s": w["end_s"]})
        path = got[0] if got else None
        r = {"id": w["id"], **evaluate(w, path), "keyframes": len(path or [])}; rows.append(r); print(json.dumps(r), flush=True)
    s = sum(r["samples"] for r in rows)
    print("TOTAL face_in_crop_pct", round(sum(r["face_in_crop_pct"] * r["samples"] for r in rows) / s, 1), "half_cut", round(sum(r["face_half_cut_pct"] * r["samples"] for r in rows) / s, 1))
