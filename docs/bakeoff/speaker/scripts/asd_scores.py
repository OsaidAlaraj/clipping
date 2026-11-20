"""Stage 2 (bake-off venv, GPU): per-track, per-frame speaking scores. python asd_scores.py METHOD CLIP_ID...
METHOD = lr | fabu | asdnb.  Writes .data/benchmark/speaker/scores/<method>/<id>.npz (score_k per track at the track's frames) + timing/VRAM json line."""
import sys, os, time, json, math, numpy as np, torch, python_speech_features
from pathlib import Path
DATA = Path("L:/Projects/Clipping/.data"); CACHE = DATA / "benchmark/speaker/cache"; REPOS = DATA / "benchmark/repos"
method, ids = sys.argv[1], sys.argv[2:]
OUT = DATA / "benchmark/speaker/scores" / method; OUT.mkdir(parents=True, exist_ok=True)

def dense(d, k):
    """Track k as a gap-free run of crops over [first, last] tick (hold the last crop through missing ticks)."""
    fr, crops = d[f"fr{k}"], d[f"crop{k}"]; a, b = int(fr[0]), int(fr[-1]); out = np.zeros((b - a + 1,) + crops.shape[1:], np.uint8); j = 0
    for t in range(a, b + 1):
        while j + 1 < len(fr) and fr[j + 1] <= t: j += 1
        out[t - a] = crops[j]
    return a, out

def mfcc(audio, nfft=512):
    return python_speech_features.mfcc(audio, 16000, numcep=13, winlen=0.025, winstep=0.010, **({"nfft": nfft} if nfft != 512 else {}))

if method == "lr":
    os.chdir(REPOS / "LR-ASD"); sys.path.insert(0, str(REPOS / "LR-ASD")); from ASD import ASD
    s = ASD(); s.loadParameters("weight/pretrain_AVA.model"); s.eval(); NFFT = 512; SIZE = None
    def infer(a_feat, v):          # v: (T,224,224) -> centre 112
        v = v[:, 56:168, 56:168]; return run_lr(a_feat, v)
    def run_lr(af, vf):
        length = min((af.shape[0] - af.shape[0] % 4) / 100, vf.shape[0]); af = af[:int(round(length * 100))]; vf = vf[:int(round(length * 25))]
        allS = []
        for dur in (1, 1, 1, 2, 2, 2, 3, 3, 4, 5, 6):
            sc = []
            with torch.no_grad():
                for i in range(int(math.ceil(length / dur))):
                    a = torch.FloatTensor(af[i*dur*100:(i+1)*dur*100]).unsqueeze(0).cuda(); v = torch.FloatTensor(vf[i*dur*25:(i+1)*dur*25]).unsqueeze(0).cuda()
                    if v.shape[1] == 0: continue
                    ea = s.model.forward_audio_frontend(a); ev = s.model.forward_visual_frontend(v); out = s.model.forward_audio_visual_backend(ea, ev)
                    sc.extend(s.lossAV.forward(out, labels=None))
            allS.append(sc)
        n = min(len(x) for x in allS); return np.mean(np.array([x[:n] for x in allS]), axis=0).astype(np.float32)
elif method == "fabu":
    os.chdir(REPOS / "FabuLight-ASD"); sys.path.insert(0, str(REPOS / "FabuLight-ASD")); from ASD import ASD
    import torch.nn.functional as F
    s = ASD(bodyPose=False, upperBody=False); w = [p for p in (REPOS / "FabuLight-ASD/exps").glob("*_F_fabulight_config/model/best_*.model")][0]
    s.loadParameters(str(w)); s.eval(); NFFT = 1024
    def infer(a_feat, v):
        import cv2
        v = np.stack([cv2.resize(f[40:184, 40:184], (112, 112)) for f in v])     # tighter than the TalkNet context crop: detector box ~ central 144 px of 224
        n = min(a_feat.shape[0] // 4, v.shape[0]); sc = []
        with torch.no_grad():
            for dur in (1, 2, 3):
                part = []
                for i in range(0, n, dur * 25):
                    vv = torch.FloatTensor(v[i:i + dur*25]).unsqueeze(0).cuda(); aa = torch.FloatTensor(a_feat[i*4:(i + dur*25)*4][:vv.shape[1]*4]).unsqueeze(0).cuda()
                    if vv.shape[1] == 0: continue
                    o, _, _ = s.model(vv, aa, None); p = F.softmax(s.lossFA.module.FC(o.squeeze(1)), dim=-1)[:, 1]; part.extend(p.cpu().numpy())
                sc.append(part)
        m = min(len(x) for x in sc); return np.mean(np.array([x[:m] for x in sc]), axis=0).astype(np.float32)
else:
    raise SystemExit("method not implemented here")

torch.cuda.reset_peak_memory_stats()
for cid in ids:
    d = np.load(CACHE / f"{cid}.npz"); audio = d["audio"].astype(np.float64); af = mfcc(audio, NFFT); res = {}; t0 = time.time(); k = 0
    while f"fr{k}" in d:
        a, v = dense(d, k); sc = infer(af[a * 4:], v); full = np.zeros(len(v), np.float32); full[:len(sc)] = sc
        res[f"score{k}"] = full[d[f"fr{k}"] - a]; k += 1
    torch.cuda.synchronize(); el = time.time() - t0
    np.savez_compressed(OUT / f"{cid}.npz", **res)
    print(json.dumps({"method": method, "clip": cid, "tracks": k, "infer_s": round(el, 2), "peak_vram_mb": round(torch.cuda.max_memory_allocated() / 2**20)}), flush=True)
