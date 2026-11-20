"""Measured facts about a track, used to curate the catalog and to check the curator's tags: tempo, onset rate, how long the intro
takes to get going and how loud the first two seconds are. Pure numpy on a mono 11 kHz decode; no models."""
from __future__ import annotations

import subprocess

import numpy as np

from . import ffmpeg

RATE = 11025
HOP = 256


def decode(path, seconds: float | None = None) -> np.ndarray:
    args = [ffmpeg.exe(), "-hide_banner", "-nostdin", "-v", "error", "-i", str(path), *(["-t", str(seconds)] if seconds else []),
            "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"]
    raw = subprocess.run(args, capture_output=True, timeout=300).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768


def onset_envelope(x: np.ndarray) -> np.ndarray:
    """Positive spectral flux per hop (the usual beat-tracking input)."""
    frames = (len(x) - 1024) // HOP
    win = np.hanning(1024)
    prev, out = None, np.zeros(frames)
    for i in range(frames):
        mag = np.abs(np.fft.rfft(x[i * HOP:i * HOP + 1024] * win))
        if prev is not None:
            out[i] = np.maximum(mag - prev, 0).sum()
        prev = mag
    return out


def tempo(env: np.ndarray, lo: float = 70.0, hi: float = 170.0) -> float:
    """BPM from the autocorrelation of the onset envelope, folded into [lo, hi]; mild preference for 90-140 where octaves tie."""
    env = env - env.mean()
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    fps = RATE / HOP
    best, best_bpm = -1.0, 100.0
    for bpm in np.arange(lo, hi, 0.5):
        lag = fps * 60.0 / bpm
        i = int(round(lag))
        if i + 1 >= len(ac):
            continue
        score = ac[i] + 0.5 * ac[min(int(round(lag * 2)), len(ac) - 1)] + 0.25 * ac[min(int(round(lag * 4)), len(ac) - 1)]
        score *= 1.0 + (0.08 if 90 <= bpm <= 140 else 0.0)
        if score > best:
            best, best_bpm = score, float(bpm)
    return round(best_bpm, 1)


def analyze(path) -> dict:
    x = decode(path)
    env = onset_envelope(x[: RATE * 150])           # the first 2.5 minutes are plenty
    fps = RATE / HOP
    win = RATE                                      # 1 s rms windows every 0.25 s
    rms = np.array([np.sqrt(np.mean(x[i:i + win] ** 2)) for i in range(0, len(x) - win, win // 4)])
    ref = float(np.median(rms)) or 1e-6
    reached = np.nonzero(rms >= 0.7 * ref)[0]
    first2 = np.sqrt(np.mean(x[: 2 * RATE] ** 2))
    return {"bpm": tempo(env), "onsets_per_s": round(float((env > env.mean() + env.std()).sum() / (len(env) / fps)), 2),
            "intro_s": round(float(reached[0] * 0.25), 2) if len(reached) else 0.0,
            "first2s_db": round(20 * np.log10(first2 / ref + 1e-9), 1), "dynamic_db": round(float(20 * np.log10((np.percentile(rms, 95) + 1e-9) / (np.percentile(rms, 10) + 1e-9))), 1)}
