"""Contact sheet of a rendered clip: frames at 1 fps-ish. Usage: python sheet.py clip.mp4 out.jpg [t0 t1 step]"""
import sys, cv2, numpy as np
p, o = sys.argv[1], sys.argv[2]; t0, t1, st = (float(x) for x in (sys.argv[3:6] or (0, 1e9, 2)))
cap = cv2.VideoCapture(p); fps = cap.get(5); n = int(cap.get(7)); ims = []
t = t0
while t < min(t1, n / fps):
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps)); ok, f = cap.read()
    if ok:
        f = cv2.resize(f, (180, 320)); cv2.putText(f, f"{t:.1f}", (4, 20), 0, .6, (0, 255, 0), 2); ims.append(f)
    t += st
while len(ims) % 8: ims.append(ims[0] * 0)
cv2.imwrite(o, np.vstack([np.hstack(ims[i:i + 8]) for i in range(0, len(ims), 8)]))
