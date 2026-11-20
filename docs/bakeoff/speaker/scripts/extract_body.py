"""ASDnB needs a body crop per face. No person detector: body box = face box x3 wide, from 0.3 face-heights above the face to 5 heights below (clipped).
python extract_body.py CLIP... -> cache/<id>_body.npz (body{k}: n,112,112 gray, same frames as track k)"""
import sys, numpy as np, cv2
from clipset import *
CACHE = DATA / "benchmark/speaker/cache"
for cid in sys.argv[1:]:
    key, t0, _ = CLIPS[cid]; d = np.load(CACHE / f"{cid}.npz"); W, H = int(d["W"]), int(d["H"]); tracks = []; k = 0
    while f"fr{k}" in d: tracks.append((d[f"fr{k}"], d[f"box{k}"])); k += 1
    need = {}
    for ki, (fr, bx) in enumerate(tracks):
        for j, t in enumerate(fr): need.setdefault(int(t), []).append((ki, j))
    out = [np.zeros((len(fr), 112, 112), np.uint8) for fr, _ in tracks]
    cap = cv2.VideoCapture(str(SRC[key])); cap.set(cv2.CAP_PROP_POS_MSEC, t0 * 1000); fps = cap.get(cv2.CAP_PROP_FPS) or 25; i = 0; tick = 0
    while tick < int(d["n_ticks"]):
        ok, f = cap.read()
        if not ok: break
        if i / fps < tick / 25 - 0.5 / fps: i += 1; continue
        i += 1
        for ki, j in need.get(tick, []):
            x, y, w, h = tracks[ki][1][j]; cx = x + w / 2
            x0, x1 = int(max(0, cx - 1.5 * w)), int(min(W, cx + 1.5 * w)); y0, y1 = int(max(0, y - 0.3 * h)), int(min(H, y + 5 * h))
            b = f[y0:y1, x0:x1]
            if b.size: out[ki][j] = cv2.resize(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), (112, 112))
        tick += 1
    np.savez_compressed(CACHE / f"{cid}_body.npz", **{f"body{k}": o for k, o in enumerate(out)}); print(cid, flush=True)
