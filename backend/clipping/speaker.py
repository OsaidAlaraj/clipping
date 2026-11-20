"""Active-speaker choice for the 9:16 crop: which visible face is talking, from the picture and the sound.

Winner of the speaker bake-off (docs/bakeoff/speaker): FabuLight-ASD, face-only weights (MIT code, 1.0 M parameters), exported to
ONNX (4 MB, assets/fabulight_asd_face.onnx) so only onnxruntime is needed. On 11 labelled people clips it kept the speaker inside
the crop in 87 % of seconds against 65 % for the YuNet-only crop, and it fixed the listener-from-behind case.

This module is pure: it turns face tracks plus a scorer into per-sample face centres. `faces.track` feeds it. A scorer is any object
with `score(faces, mfcc) -> per-frame probability`; `ScriptedScorer` is the fake used by tests.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

import numpy as np

MODEL = Path(__file__).with_name("assets") / "fabulight_asd_face.onnx"
FPS = 25
CHUNKS_S = (1, 2, 3)          # the model sees 1, 2 and 3 s windows; the scores are averaged (as in the bake-off)
FLOOR = 0.5                   # probability: below it nobody is clearly speaking, keep the current face (or the biggest)
MARGIN = 0.12                 # a rival face must beat the current one by this much to take the crop
WINDOW = 12                   # frames each side used to smooth a track's score at a sample
MAX_TRACKS = 4
MIN_TRACK_FRAMES = 12


class Scorer(Protocol):
    def score(self, faces: np.ndarray, mfcc: np.ndarray) -> np.ndarray: ...


# ---- audio features: the same MFCC as python_speech_features (13 cepstra, 25 ms / 10 ms, nfft 1024), in numpy ----

def _mel(hz): return 2595 * np.log10(1 + hz / 700.0)
def _hz(mel): return 700 * (10 ** (mel / 2595.0) - 1)


def _filterbank(nfilt: int, nfft: int, rate: int) -> np.ndarray:
    pts = np.linspace(_mel(0), _mel(rate / 2), nfilt + 2); bins = np.floor((nfft + 1) * _hz(pts) / rate)
    fb = np.zeros((nfilt, nfft // 2 + 1))
    for j in range(nfilt):
        for i in range(int(bins[j]), int(bins[j + 1])): fb[j, i] = (i - bins[j]) / (bins[j + 1] - bins[j])
        for i in range(int(bins[j + 1]), int(bins[j + 2])): fb[j, i] = (bins[j + 2] - i) / (bins[j + 2] - bins[j + 1])
    return fb


def _dct_matrix(n_in: int, n_out: int) -> np.ndarray:
    k, n = np.arange(n_out)[:, None], np.arange(n_in)[None, :]
    m = np.cos(np.pi * k * (2 * n + 1) / (2 * n_in)) * np.sqrt(2.0 / n_in); m[0] /= np.sqrt(2.0); return m


def mfcc(samples: np.ndarray, rate: int = 16000, numcep: int = 13, nfilt: int = 26, nfft: int = 1024, winlen: float = 0.025,
         winstep: float = 0.010, preemph: float = 0.97, ceplifter: int = 22) -> np.ndarray:
    """(frames, 13) features from int16-scaled samples; 100 frames a second."""
    x = np.asarray(samples, dtype=np.float64); x = np.append(x[0], x[1:] - preemph * x[:-1])
    flen, step = int(round(winlen * rate)), int(round(winstep * rate)); n = 1 if len(x) <= flen else 1 + int(np.ceil((len(x) - flen) / step))
    padded = np.concatenate([x, np.zeros(max((n - 1) * step + flen - len(x), 0))])
    idx = np.arange(flen)[None, :] + step * np.arange(n)[:, None]; frames = padded[idx]
    pspec = (1.0 / nfft) * np.square(np.abs(np.fft.rfft(frames, nfft)))
    energy = np.where(pspec.sum(1) == 0, np.finfo(float).eps, pspec.sum(1))
    feat = pspec @ _filterbank(nfilt, nfft, rate).T; feat = np.log(np.where(feat == 0, np.finfo(float).eps, feat))
    ceps = feat @ _dct_matrix(nfilt, numcep).T
    ceps *= 1 + (ceplifter / 2.0) * np.sin(np.pi * np.arange(numcep) / ceplifter); ceps[:, 0] = np.log(energy)
    return ceps


# ---- scorers ----

class OnnxScorer:
    """FabuLight-ASD (face-only) through onnxruntime on the CPU. Faces are (T,112,112) uint8 grey crops, mfcc is (>=4T,13)."""

    def __init__(self, path: Path = MODEL):
        import onnxruntime as ort
        self.sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])

    def score(self, faces: np.ndarray, mfcc: np.ndarray) -> np.ndarray:
        n = min(len(faces), len(mfcc) // 4); v = faces[:n].astype(np.float32); a = mfcc.astype(np.float32); runs = []
        for dur in CHUNKS_S:
            part: list[float] = []
            for i in range(0, n, dur * FPS):
                vv = v[i:i + dur * FPS][None]; t = vv.shape[1]
                if t:
                    part.extend(self.sess.run(None, {"face": vv, "audio": a[i * 4:(i + t) * 4][None]})[0])
            runs.append(part)
        m = min(len(r) for r in runs); out = np.zeros(len(faces), np.float32); out[:m] = np.mean([r[:m] for r in runs], axis=0); return out


class ScriptedScorer:
    """Test fake: `fn(faces, mfcc)` returns the per-frame scores (default: bright faces speak)."""

    def __init__(self, fn=None): self.fn = fn or (lambda faces, mfcc: faces.reshape(len(faces), -1).mean(axis=1) / 255.0)
    def score(self, faces, mfcc): return np.asarray(self.fn(faces, mfcc), np.float32)


def default_scorer() -> Scorer | None:
    """The ONNX scorer when onnxruntime and the model are there, else None (the caller keeps the YuNet-only crop)."""
    try:
        return OnnxScorer() if MODEL.exists() else None
    except Exception:
        return None


# ---- choosing the face ----

def centre_at(track: dict, tick: int, tol: int = 5) -> float | None:
    fr = track["frames"]; i = int(np.searchsorted(fr, tick)); near = [j for j in (i - 1, i) if 0 <= j < len(fr) and abs(int(fr[j]) - tick) <= tol]
    if not near: return None
    j = min(near, key=lambda j: abs(int(fr[j]) - tick)); return float(track["centre"][j])


def pick(tracks: list[dict], scores: list[np.ndarray], ticks: list[int], floor: float = FLOOR, margin: float = MARGIN) -> list[float | None]:
    """Per sample tick, the centre of the face that is talking. A rival must win by `margin` to take over, so the crop does not flip on noise;
    when nobody scores above `floor` (slides, game characters, silence) the current face stays, else the biggest one."""
    out: list[float | None] = []; cur: int | None = None
    for t in ticks:
        here, avg = {}, {}
        for k, (tr, sc) in enumerate(zip(tracks, scores)):
            c = centre_at(tr, t)
            if c is None: continue
            sel = (tr["frames"] >= t - WINDOW) & (tr["frames"] <= t + WINDOW); here[k] = c; avg[k] = float(np.mean(sc[sel])) if sel.any() else 0.0
        if not here: out.append(None); continue
        top = max(avg, key=avg.get)
        if avg[top] < floor:
            if cur not in here: cur = max(here, key=lambda k: tracks[k]["area"])
        elif cur not in here or avg[top] > avg[cur] + margin:
            cur = top
        out.append(here[cur])
    return out


def keep_main_tracks(tracks: list[dict]) -> list[dict]:
    """Long, big tracks only: stray detections on posters or game characters are dropped and the cost stays bounded."""
    good = [t for t in tracks if len(t["frames"]) >= MIN_TRACK_FRAMES]
    return sorted(good, key=lambda t: len(t["frames"]) * t["area"], reverse=True)[:MAX_TRACKS]


def score_tracks(tracks: list[dict], scorer: Scorer, mfcc_all: np.ndarray) -> list[np.ndarray]:
    """Per-frame speaking probability for every track (a track's frames are held through short gaps, as in the bake-off)."""
    out = []
    for tr in tracks:
        a, b = int(tr["frames"][0]), int(tr["frames"][-1]); dense = np.zeros((b - a + 1,) + tr["crops"].shape[1:], np.uint8); j = 0
        for t in range(a, b + 1):
            while j + 1 < len(tr["frames"]) and tr["frames"][j + 1] <= t: j += 1
            dense[t - a] = tr["crops"][j]
        s = scorer.score(dense, mfcc_all[a * 4:]); out.append(np.asarray(s)[tr["frames"] - a])
    return out
