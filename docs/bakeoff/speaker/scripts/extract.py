"""Stage 1 (project venv): per clip, YuNet faces on every 1/25 s tick -> IoU tracks, TalkNet-style 224 gray crops, 16 kHz audio.
python extract.py CLIP_ID...   -> .data/benchmark/speaker/cache/<id>.npz"""
import sys, time, numpy as np, cv2, av
from clipset import *
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "backend"))
from clipping import faces as F
CACHE = DATA / "benchmark/speaker/cache"; CACHE.mkdir(parents=True, exist_ok=True)
FPS = 25; DUR = 30

def iou(a, b):
    ax2, ay2, bx2, by2 = a[0]+a[2], a[1]+a[3], b[0]+b[2], b[1]+b[3]
    iw = max(0, min(ax2, bx2) - max(a[0], b[0])); ih = max(0, min(ay2, by2) - max(a[1], b[1]))
    i = iw * ih; return i / (a[2]*a[3] + b[2]*b[3] - i + 1e-9)

def crop224(frame, box, cs=0.4):
    x, y, w, h = box; bs = max(w, h) / 2; cx, cy = x + w / 2, y + h / 2
    bsi = int(bs * (1 + 2 * cs)); pad = cv2.copyMakeBorder(frame, bsi, bsi, bsi, bsi, cv2.BORDER_CONSTANT, value=(110, 110, 110))
    my, mx = cy + bsi, cx + bsi
    face = pad[int(my - bs):int(my + bs * (1 + 2 * cs)), int(mx - bs * (1 + cs)):int(mx + bs * (1 + cs))]
    return cv2.resize(cv2.cvtColor(face, cv2.COLOR_BGR2GRAY), (224, 224))

def audio16k(src, t0):
    c = av.open(str(src)); s = c.streams.audio[0]; c.seek(int(t0 * 1e6)); rs = av.AudioResampler(format="s16", layout="mono", rate=16000); out = []
    for fr in c.decode(s):
        t = float(fr.pts * s.time_base)
        if t > t0 + DUR + 1: break
        for r in rs.resample(fr): out.append((float(r.pts / 16000) if r.pts is not None else t, r.to_ndarray().ravel()))
    # place by timestamp
    buf = np.zeros(int(DUR * 16000), np.int16); pos = None
    cat = np.concatenate([a for _, a in out]); t_first = out[0][0]; off = int(round((t0 - t_first) * 16000))
    seg = cat[max(off, 0):max(off, 0) + len(buf)]; buf[:len(seg)] = seg; return buf

def run(cid):
    key, t0, grp = CLIPS[cid]; src = SRC[key]; cap = cv2.VideoCapture(str(src)); cap.set(cv2.CAP_PROP_POS_MSEC, t0 * 1000)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25; W = int(cap.get(3)); H = int(cap.get(4))
    det = cv2.FaceDetectorYN.create(str(F.MODEL), "", (640, 360), 0.6, 0.3, 5000)
    tracks, nxt, frames_t = [], 0, 0.0; tick = 0; i = 0; t_det = 0
    while tick < FPS * DUR:
        ok, frame = cap.read()
        if not ok: break
        if i / fps < tick / FPS - 0.5 / fps: i += 1; continue
        i += 1
        t = time.time(); small = cv2.resize(frame, (640, 360), interpolation=cv2.INTER_AREA); _, found = det.detect(small); t_det += time.time() - t
        boxes = [] if found is None else [(f[0]*W/640, f[1]*H/360, f[2]*W/640, f[3]*H/360, float(f[-1])) for f in found]
        used = set()
        for tr in tracks:
            if tick - tr["fr"][-1] > 10: continue
            best, bi = 0.3, None
            for k, b in enumerate(boxes):
                if k in used: continue
                v = iou(tr["box"][-1], b)
                if v > best: best, bi = v, k
            if bi is not None:
                used.add(bi); tr["fr"].append(tick); tr["box"].append(boxes[bi][:4]); tr["crop"].append(crop224(frame, boxes[bi][:4]))
        for k, b in enumerate(boxes):
            if k not in used: tracks.append({"fr": [tick], "box": [b[:4]], "crop": [crop224(frame, b[:4])]})
        tick += 1
    cap.release()
    tracks = [t for t in tracks if len(t["fr"]) >= 12]
    aud = audio16k(src, t0)
    np.savez_compressed(CACHE / f"{cid}.npz", n_ticks=tick, W=W, H=H, audio=aud, det_s=t_det,
        **{f"fr{k}": np.array(t["fr"]) for k, t in enumerate(tracks)}, **{f"box{k}": np.array(t["box"], np.float32) for k, t in enumerate(tracks)},
        **{f"crop{k}": np.stack(t["crop"]) for k, t in enumerate(tracks)})
    print(cid, "ticks", tick, "tracks", len(tracks), [len(t["fr"]) for t in tracks], "det_s %.1f" % t_det, flush=True)

if __name__ == "__main__":
    for c in sys.argv[1:]: run(c)
