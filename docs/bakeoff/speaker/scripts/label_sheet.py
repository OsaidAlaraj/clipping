"""Labelling aid: python -I label_sheet.py CLIP_ID OUT_DIR. One tile per 2 s with a 0.1 ruler and the Deepgram speaker."""
import sys, cv2, numpy as np
from clipset import *
cid, out = sys.argv[1], Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
key, t0, grp = CLIPS[cid]; ws = words(key) if key in TR else []
cap = cv2.VideoCapture(str(SRC[key])); tiles = []
for s in range(0, 30, 2):
    cap.set(cv2.CAP_PROP_POS_MSEC, (t0 + s) * 1000); ok, f = cap.read()
    f = cv2.resize(f, (384, 216))
    for k in range(1, 10):
        x = int(k * 38.4); cv2.line(f, (x, 0), (x, 216), (0, 255, 0) if k != 5 else (0, 128, 255), 1)
        cv2.putText(f, str(k), (x + 2, 212), 0, 0.4, (255, 255, 0), 1)
    cv2.putText(f, f"{s}s spk={speaker_at(ws, t0 + s)}", (4, 18), 0, 0.6, (0, 0, 255), 2); tiles.append(f)
rows = [np.hstack(tiles[i:i + 5]) for i in range(0, 15, 5)]
cv2.imwrite(str(out / f"{cid}.jpg"), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 82])
