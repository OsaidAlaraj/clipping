"""Face tracking for the 9:16 crop: OpenCV YuNet (MIT) at 3 samples a second, a smoothed path with jumps at shot cuts.

When the clip has speech and the ONNX speaker model is available, the crop follows the face that is TALKING (speaker.py: FabuLight-ASD
on 25 fps face tracks plus the audio) instead of the biggest face; otherwise, and on any failure, the YuNet-only path below runs.

Chosen by the reframe bake-off (docs/bakeoff/reframe): on the interview video the speaker stayed inside the crop in 89 % of sampled
frames, against 10 % for the skin-colour stand-in. Returns None when OpenCV or the model is missing, or no face is seen, so the
caller falls back to what it did before.
"""
from __future__ import annotations

import tempfile
import wave
from pathlib import Path

MODEL = Path(__file__).with_name("assets") / "face_detection_yunet_2023mar.onnx"
FRAME_W, FRAME_H = 640, 360
SAMPLE_S = 1 / 3
MAX_CLIP_S = 120            # longer clips are sampled at a coarser step so the cost stays near 5 s
CUT_JUMP = 0.22             # a face-centre jump bigger than this share of the width is a shot cut: jump, do not glide
DEAD_ZONE, ALPHA, HOLD_SAMPLES = 0.04, 0.12, 9
MIN_SCORE = 0.6
MAX_ASD_CLIP_S = 90          # longer clips use the YuNet-only path (the speaker model costs about 0.4 s per clip second on the CPU)
TRACK_GAP, TRACK_IOU = 10, 0.3

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


def track(src: Path, clip: dict, scorer=None, tracks_fn=None) -> tuple[list[tuple[float, float]], float] | None:
    """(keyframes, median centre) of the person in the clip, or None when nobody is found or the detector is unavailable.
    Follows the speaking face when the speaker model works (`scorer` and `tracks_fn` are the test seams), else the YuNet-only path."""
    duration = float(clip["end_s"]) - float(clip["start_s"])
    if 0 < duration <= MAX_ASD_CLIP_S:
        try:
            got = _track_speaker(src, clip, scorer, tracks_fn)
        except Exception:
            got = None
        if got:
            return got
    return _track_yunet(src, clip)


def _iou(a, b) -> float:
    iw = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])); ih = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])); i = iw * ih
    return i / (a[2] * a[3] + b[2] * b[3] - i + 1e-9)


def _face_crop(frame, box):
    """112x112 grey crop of a face with a little context (the crop the speaker model was scored on in the bake-off)."""
    import cv2
    x, y, w, h = box; bs = max(w, h) / 2; cs = 0.4; bsi = int(bs * (1 + 2 * cs))
    pad = cv2.copyMakeBorder(frame, bsi, bsi, bsi, bsi, cv2.BORDER_CONSTANT, value=(110, 110, 110)); my, mx = y + h / 2 + bsi, x + w / 2 + bsi
    face = pad[int(my - bs):int(my + bs * (1 + 2 * cs)), int(mx - bs * (1 + cs)):int(mx + bs * (1 + cs))]
    g = cv2.resize(cv2.cvtColor(face, cv2.COLOR_BGR2GRAY), (224, 224))
    return cv2.resize(g[40:184, 40:184], (112, 112))


def face_tracks(src: Path, start: float, end: float) -> tuple[list[dict], int, int]:
    """YuNet on every 1/25 s tick, linked into tracks by overlap: [{frames, centre (0..1 of the width), area, crops}], the tick count, the width."""
    import cv2
    import numpy as np
    det = _load(); cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        return [], 0, 0
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000); fps = cap.get(cv2.CAP_PROP_FPS) or 25; w, h = int(cap.get(3)), int(cap.get(4))
        total, tick, i, live = int((end - start) * 25), 0, 0, []
        while tick < total:
            ok, frame = cap.read()
            if not ok:
                break
            i += 1
            if (i - 1) / fps < tick / 25 - 0.5 / fps:
                continue
            _, found = det.detect(cv2.resize(frame, (FRAME_W, FRAME_H), interpolation=cv2.INTER_AREA))
            boxes = [] if found is None else [(f[0] * w / FRAME_W, f[1] * h / FRAME_H, f[2] * w / FRAME_W, f[3] * h / FRAME_H) for f in found]
            used = set()
            for tr in live:
                if tick - tr["fr"][-1] > TRACK_GAP:
                    continue
                best, bi = TRACK_IOU, None
                for k, b in enumerate(boxes):
                    if k not in used and _iou(tr["box"][-1], b) > best:
                        best, bi = _iou(tr["box"][-1], b), k
                if bi is not None:
                    used.add(bi); tr["fr"].append(tick); tr["box"].append(boxes[bi]); tr["crops"].append(_face_crop(frame, boxes[bi]))
            for k, b in enumerate(boxes):
                if k not in used:
                    live.append({"fr": [tick], "box": [b], "crops": [_face_crop(frame, b)]})
            tick += 1
    finally:
        cap.release()
    out = [{"frames": np.array(t["fr"]), "centre": np.array([(b[0] + b[2] / 2) / w for b in t["box"]]), "area": float(np.median([b[2] * b[3] for b in t["box"]])) / (w * h),
            "crops": np.stack(t["crops"])} for t in live]
    return out, tick, w


def _audio16k(src: Path, start: float, duration: float):
    import numpy as np
    from . import ffmpeg
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "a.wav"
        ffmpeg.run(["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)])
        with wave.open(str(wav)) as f:
            return np.frombuffer(f.readframes(f.getnframes()), np.int16)


def _track_speaker(src: Path, clip: dict, scorer, tracks_fn):
    import numpy as np
    from . import speaker
    scorer = scorer or speaker.default_scorer()
    if scorer is None:
        return None
    start, end = float(clip["start_s"]), float(clip["end_s"])
    tracks, ticks, _ = (tracks_fn or face_tracks)(src, start, end)
    tracks = speaker.keep_main_tracks(tracks)
    if not tracks or ticks < 25:
        return None
    audio = _audio16k(src, start, end - start)
    if len(audio) < 16000:
        return None
    scores = speaker.score_tracks(tracks, scorer, speaker.mfcc(audio))
    sample_ticks = [int(round(i * SAMPLE_S * 25)) for i in range(int(ticks / 25 / SAMPLE_S))]
    centres = speaker.pick(tracks, scores, sample_ticks)
    seen = [c for c in centres if c is not None]
    if not seen:
        return None
    crop_share = FRAME_H * 9 / 16 / FRAME_W
    return keyframes(smooth(centres), SAMPLE_S, crop_share), float(np.median(seen))


def _track_yunet(src: Path, clip: dict) -> tuple[list[tuple[float, float]], float] | None:
    """The YuNet-only path: the most confident biggest face at 3 samples a second."""
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
