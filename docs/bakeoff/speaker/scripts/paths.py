"""Crop paths per method for every clip + the rubric metrics. python paths.py  -> results.json (project venv)."""
import sys, json, time, numpy as np
from clipset import *
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "backend"))
from clipping import faces as F
CACHE = DATA / "benchmark/speaker/cache"; SC = DATA / "benchmark/speaker/scores"
FPS = 25; STEP = F.SAMPLE_S
LABELS = json.loads((Path(__file__).parent.parent / "labels.json").read_text())["labels"]

def load(cid):
    d = np.load(CACHE / f"{cid}.npz"); W, H = int(d["W"]), int(d["H"]); tr = []; k = 0
    while f"fr{k}" in d:
        tr.append({"fr": d[f"fr{k}"], "box": d[f"box{k}"], "crop": d[f"crop{k}"]}); k += 1
    return d, W, H, tr

def centre_at(tr, tick, W):
    """x centre (0..1) of the track at a tick, nearest known frame within 5 ticks, else None."""
    i = int(np.searchsorted(tr["fr"], tick)); c = [j for j in (i - 1, i) if 0 <= j < len(tr["fr"]) and abs(tr["fr"][j] - tick) <= 5]
    if not c: return None
    j = min(c, key=lambda j: abs(tr["fr"][j] - tick)); b = tr["box"][j]; return float((b[0] + b[2] / 2) / W)

def path_from_choice(centres, W, H):
    share = H * 9 / 16 / W; return F.keyframes(F.smooth(centres), STEP, share), share

def ticks_3hz(n_ticks): return [int(round(i * STEP * FPS)) for i in range(int(n_ticks / FPS / STEP))]

def choose(tr, scores, W, n_ticks, margin=0.12, win=12, floor=-1e9):
    """Per 1/3 s sample: the track with the best windowed score; switch only when a rival beats the current one by `margin`."""
    out, cur = [], None
    for t in ticks_3hz(n_ticks):
        best, bs = {}, {}
        for k, (x, s) in enumerate(zip(tr, scores)):
            c = centre_at(x, t, W)
            if c is None: continue
            m = (x["fr"] >= t - win) & (x["fr"] <= t + win); bs[k] = float(np.mean(s[m])) if m.any() else 0.0; best[k] = c
        if not best: out.append(None); continue
        top = max(bs, key=bs.get)
        if bs[top] < floor:            # nobody is clearly speaking: keep the current face, else the biggest one
            if cur not in best: cur = max(best, key=lambda k: float(np.median(tr[k]['box'][:, 2] * tr[k]['box'][:, 3])))
            out.append(best[cur]); continue
        if cur not in best or bs[top] > bs[cur] + margin: cur = top
        out.append(best[cur])
    return out

def speaker_at_t(ws, t):
    best, bd = None, 0.5
    for w in ws:
        d = 0 if w["start"] <= t <= w["end"] else min(abs(t - w["start"]), abs(t - w["end"]))
        if d < bd: bd, best = d, w.get("speaker")
    return best

def mouth_energy(crop):
    """Motion energy of the lower-face region between consecutive crops (cheap lip-movement proxy)."""
    m = crop[:, 120:200, 64:160].astype(np.float32); e = np.abs(np.diff(m, axis=0)).mean(axis=(1, 2)); return np.concatenate([[0], e])

def diar_choice(cid, tr, W, n_ticks, bind_scores=None):
    """Deepgram speaker label per tick -> face track. Binding label->track by correlation of 'label speaks' with the track's mouth-motion
    (or with ASD scores when bind_scores is given), computed once over the clip."""
    key, t0, _ = CLIPS[cid]; ws = [w for w in words(key) if t0 - 1 <= w["start"] <= t0 + 31]
    lab = [speaker_at_t(ws, t0 + t / FPS) for t in range(n_ticks)]; labels = sorted({l for l in lab if l is not None})
    if not labels or not tr: return [None] * len(ticks_3hz(n_ticks)), {}
    sig = []
    for k, x in enumerate(tr):
        e = mouth_energy(x["crop"]) if bind_scores is None else bind_scores[k]
        full = np.full(n_ticks, np.nan); full[x["fr"]] = e; sig.append(full)
    bind = {}
    cost = np.zeros((len(labels), len(tr)))
    for li, l in enumerate(labels):
        ind = np.array([1.0 if x == l else 0.0 for x in lab])
        for k in range(len(tr)):
            ok = ~np.isnan(sig[k])
            if ok.sum() < 25 or ind[ok].std() == 0: cost[li, k] = -9; continue
            s = sig[k][ok]; s = (s - s.mean()) / (s.std() + 1e-9); i2 = (ind[ok] - ind[ok].mean()) / ind[ok].std(); cost[li, k] = float((s * i2).mean())
    used = set()
    for li in np.argsort(-cost.max(axis=1)):
        k = int(np.argmax([cost[li, j] if j not in used else -99 for j in range(len(tr))]))
        if cost[li, k] > -9: bind[labels[li]] = k; used.add(k)
    out = []
    for t in ticks_3hz(n_ticks):
        l = lab[min(t, n_ticks - 1)]
        if l is None: out.append(None); continue          # no one speaking: hold (smooth() holds on None)
        k = bind.get(l); out.append(None if k is None else centre_at(tr[k], t, W))
    return out, bind

def interp(keys, t):
    ts = [a for a, _ in keys]; cs = [c for _, c in keys]; return float(np.interp(t, ts, cs))

def score_clip(cid, keys, share, W, tr, n_ticks):
    lab = LABELS[cid]; hits, tot, listener_fail, listener_tot = 0, 0, 0, 0
    for s in range(30):
        x = lab[s // 2]
        if x is None: continue
        c = interp(keys, s + 0.5); ok = abs(c - x) <= 0.45 * share; hits += ok; tot += 1
        if CLIPS[cid][2] == "listener" and not ok:
            others = [centre_at(t, int((s + .5) * FPS), W) for t in tr]; others = [o for o in others if o is not None and abs(o - x) > 0.12]
            listener_tot += 1
            if others and min(abs(o - c) for o in others) < abs(x - c): listener_fail += 1
    path = np.array([interp(keys, i * 0.25) for i in range(120)]) * 1920
    jit = float(np.mean(np.abs(np.diff(path, 2)))) if len(path) > 2 else 0.0
    d = np.diff(path); d = d[np.abs(d) > 2]; rev = int(np.sum(np.sign(d[1:]) != np.sign(d[:-1]))) * 2 if len(d) > 1 else 0
    moves = sum(1 for a, b in zip(keys, keys[1:]) if abs(a[1] - b[1]) > 0.05)
    xs = [x for x in lab if x is not None]; true_changes = sum(1 for a, b in zip(xs, xs[1:]) if abs(a - b) > 0.15)
    return {"hit": hits, "n": tot, "jitter_px": round(jit, 2), "reversals_per_min": rev, "moves": moves, "true_changes": true_changes, "listener_fail": listener_fail, "listener_miss": tot - hits if CLIPS[cid][2] == "listener" else 0}

def main():
    methods = sys.argv[1:] or ["baseline", "diar", "lr", "fabu"]; res = {}
    for cid, (key, t0, grp) in CLIPS.items():
        if grp == "noface": continue
        d, W, H, tr = load(cid); n = int(d["n_ticks"]); res[cid] = {"group": grp}
        for m in methods:
            t = time.time()
            if m == "baseline":
                r = F.track(SRC[key], {"start_s": t0, "end_s": t0 + 30}); keys = r[0] if r else [(0.0, 0.5)]; share = H * 9 / 16 / W; sec = time.time() - t
            elif m == "diar":
                ch, _ = diar_choice(cid, tr, W, n); keys, share = path_from_choice(ch, W, H); sec = time.time() - t
            elif m in ("lr", "fabu", "asdnb", "fabu_onnx"):
                p = SC / m / f"{cid}.npz"
                if not p.exists(): continue
                z = np.load(p); sc = [z[f"score{k}"] for k in range(len(tr))]; keys, share = path_from_choice(choose(tr, sc, W, n), W, H); sec = time.time() - t
            elif m in ("lr_f", "fabu_f", "asdnb_f"):
                src_m = {"lr_f": "lr", "fabu_f": "fabu_onnx", "asdnb_f": "asdnb"}[m]; z = np.load(SC / src_m / f"{cid}.npz"); sc = [z[f"score{k}"] for k in range(len(tr))]
                keys, share = path_from_choice(choose(tr, sc, W, n, floor=0.0 if m == "lr_f" else 0.5), W, H); sec = 0   # natural threshold: logit 0 / probability 0.5
            elif m in ("diar+lr", "diar+fabu"):
                p = SC / m.split("+")[1] / f"{cid}.npz"
                if not p.exists(): continue
                z = np.load(p); sc = [z[f"score{k}"] for k in range(len(tr))]; ch, _ = diar_choice(cid, tr, W, n, bind_scores=sc); keys, share = path_from_choice(ch, W, H); sec = 0
            res[cid][m] = score_clip(cid, keys, share, W, tr, n); res[cid][m]["keys"] = keys
    json.dump(res, open(Path(__file__).parent.parent / "results.json", "w"), indent=0)
    print("done", len(res))
if __name__ == "__main__": main()
