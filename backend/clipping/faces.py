"""Face tracking for the 9:16 crop: OpenCV YuNet (MIT) at 3 samples a second, a smoothed path with jumps at shot cuts.

Chosen by the reframe bake-off (docs/bakeoff/reframe): on the interview video the speaker stayed inside the crop in 89 % of sampled
frames, against 10 % for the skin-colour stand-in. Returns None when OpenCV or the model is missing, or no face is seen, so the
caller falls back to what it did before.
"""
from __future__ import annotations

from pathlib import Path

MODEL = Path(__file__).with_name("assets") / "face_detection_yunet_2023mar.onnx"
FRAME_W, FRAME_H = 640, 360
SAMPLE_S = 1 / 3
MAX_CLIP_S = 120            # longer clips are sampled at a coarser step so the cost stays near 5 s
CUT_JUMP = 0.22             # a face-centre jump bigger than this share of the width is a shot cut: jump, do not glide
DEAD_ZONE, ALPHA, HOLD_SAMPLES = 0.04, 0.12, 9
MIN_SCORE = 0.6

_detector = None


def _load():
    global _detector
    if _detector is None:
        import cv2
        _detector = cv2.FaceDetectorYN.create(str(MODEL), "", (FRAME_W, FRAME_H), MIN_SCORE, 0.3, 5000)
    return _detector


def smooth(centres: list[float | None]) -> list[float]:
    """Per-sample face centre (0..1 of the width, None = no face) to a crop-centre path: exponential smoothing with a dead zone so the
    crop does not shake, a jump on a cut, hold for 3 s when the face is lost, then back to the middle."""
    cur, out, miss = 0.5, [], 0
    for c in centres:
        if c is None:
            miss += 1
            if miss <= HOLD_SAMPLES:
                out.append(cur)
                continue
            c = 0.5
        else:
            miss = 0
        if abs(c - cur) > CUT_JUMP:
            cur = c
        elif abs(c - cur) > DEAD_ZONE:
            cur += ALPHA * (c - cur)
        out.append(cur)
    return out


def keyframes(path: list[float], step: float, crop_share: float, min_move: float = 0.01) -> list[tuple[float, float]]:
    """Thin the path to (time, crop centre) points worth keeping; the crop is clamped so its box stays inside the picture."""
    lo, hi = crop_share / 2, 1 - crop_share / 2
    pts: list[tuple[float, float]] = []
    for i, c in enumerate(path):
        c = min(max(c, lo), hi)
        if not pts or abs(c - pts[-1][1]) >= min_move or i == len(path) - 1:
            pts.append((round(i * step, 3), round(c, 4)))
    return pts


def track(src: Path, clip: dict) -> tuple[list[tuple[float, float]], float] | None:
    """(keyframes, median centre) of the person in the clip, or None when nobody is found or the detector is unavailable."""
    try:
        import cv2
        import numpy as np
        if not MODEL.exists():
            return None
        det = _load()
    except Exception:
        return None
    start, end = float(clip["start_s"]), float(clip["end_s"])
    duration = max(end - start, 0.1)
    step = max(SAMPLE_S, duration / (MAX_CLIP_S / SAMPLE_S))
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        return None
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        every, centres, i = max(1, round(fps * step)), [], 0
        while len(centres) * step < duration:
            if not cap.grab():
                break
            if i % every == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                small = cv2.resize(frame, (FRAME_W, FRAME_H), interpolation=cv2.INTER_AREA)
                _, found = det.detect(small)
                best = None if found is None else max(found, key=lambda f: float(f[2]) * float(f[3]) * float(f[-1]))
                centres.append(None if best is None else (float(best[0]) + float(best[2]) / 2) / FRAME_W)
            i += 1
    finally:
        cap.release()
    seen = [c for c in centres if c is not None]
    if not seen:
        return None
    crop_share = FRAME_H * 9 / 16 / FRAME_W
    return keyframes(smooth(centres), step, crop_share), float(np.median(seen))
