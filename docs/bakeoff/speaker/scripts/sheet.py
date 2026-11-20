"""Contact sheet of a source: python -I sheet.py SRC OUT.jpg STEP_S [START_S END_S] [COLS]."""
import sys, cv2, numpy as np
src, out, step = sys.argv[1], sys.argv[2], float(sys.argv[3])
cap = cv2.VideoCapture(src); fps = cap.get(5); n = cap.get(7); dur = n / fps
a = float(sys.argv[4]) if len(sys.argv) > 4 else 0; b = float(sys.argv[5]) if len(sys.argv) > 5 else dur
cols = int(sys.argv[6]) if len(sys.argv) > 6 else 5
tiles, t = [], a
while t < b:
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000); ok, f = cap.read()
    if ok:
        f = cv2.resize(f, (384, 216)); cv2.putText(f, f"{int(t)}s", (4, 22), 0, 0.7, (0, 255, 255), 2); tiles.append(f)
    t += step
while len(tiles) % cols: tiles.append(np.zeros_like(tiles[0]))
rows = [np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
cv2.imwrite(out, np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 80]); print(dur, len(tiles))
