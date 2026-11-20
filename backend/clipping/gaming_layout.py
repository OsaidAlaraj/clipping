"""Stacked gaming layout: face-cam on top, gameplay below, in one filtergraph.

The face-cam finder ports the idea of `studio/face_layout.py` in StreamCut (MIT licence): eligible faces are small and peripheral
(a big or central face is a game character), and the webcam is the cluster of faces that stays put across sampled frames. The detector
is OpenCV YuNet (MIT, already in the repo, see faces.py). The gameplay crop is ours: centre-weighted, nudged toward where the picture
moves with a velocity limit, and never overlapping the face-cam.
"""
from __future__ import annotations

import math
import statistics
from pathlib import Path

from . import faces

SAMPLES = 8                  # frames looked at to find the face-cam
CAM_SHARE = 0.375            # the face-cam is 720 of 1920 rows
FRAME_W = 960                # frames are looked at this wide: a small face-cam face needs the pixels
MOTION_FPS = 2
MAX_PAN_PX_S = 120.0         # gameplay crop velocity limit, in 1280-wide source pixels per second
CENTRE_WEIGHT = 0.5          # 0.5 centre of the allowed range, 0.5 where the motion is


def _detector(w: int, h: int):
    import cv2
    return cv2.FaceDetectorYN.create(str(faces.MODEL), "", (w, h), 0.5, 0.3, 5000)


def _frames(src: Path, times: list[float], width: int) -> list:
    import cv2
    cap = cv2.VideoCapture(str(src))
    out = []
    try:
        for t in times:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, fr = cap.read()
            if ok:
                h, w = fr.shape[:2]
                out.append(cv2.resize(fr, (width, round(h * width / w)), interpolation=cv2.INTER_AREA))
    finally:
        cap.release()
    return out


def eligible(face: tuple, w: int, h: int) -> bool:
    """A webcam face: small, and off the middle or near an edge. A big or central face is a character in the game."""
    x, y, fw, fh = face[:4]
    if fh > h * 0.28 or fw > w * 0.24:
        return False
    cx, cy = (x + fw / 2) / w, (y + fh / 2) / h
    off_centre = abs(cx - 0.5) >= 0.16
    near_edge = cx <= 0.25 or cx >= 0.75 or cy <= 0.26 or cy >= 0.70
    centre_tb = cy <= 0.26 or cy >= 0.70
    small = off_centre or (fh <= h * (0.24 if centre_tb else 0.16) and fw <= w * (0.20 if centre_tb else 0.12))
    return (off_centre or near_edge) and small


def stationary_cluster(cands: list[dict], n: int, w: int, h: int) -> list[dict]:
    """The group of candidates (one per frame) that stays put: the most frames, then the most corner-like and steadiest."""
    tol_x, tol_y, need = w * 0.065, h * 0.085, max(2, math.ceil(n * 0.4))
    best, best_group = None, []
    for seed in cands:
        per = {}
        for c in cands:
            if abs(c["cx"] - seed["cx"]) <= tol_x and abs(c["cy"] - seed["cy"]) <= tol_y and (c["fi"] not in per or c["score"] > per[c["fi"]]["score"]):
                per[c["fi"]] = c
        group = list(per.values())
        if len(group) < need:
            continue
        mx, my = statistics.median(c["cx"] for c in group) / w, statistics.median(c["cy"] for c in group) / h
        edge = min(mx, 1 - mx, my, 1 - my)
        jitter = statistics.pstdev(c["cx"] / w for c in group) + statistics.pstdev(c["cy"] / h for c in group) if len(group) > 1 else 0.0
        score = (len(group), round(max(0.0, 0.35 - edge) - jitter * 4, 4))
        if best is None or score > best:
            best, best_group = score, group
    return best_group


def cam_rect(face: tuple, w: int, h: int, aspect: float) -> tuple[int, int, int, int]:
    """The face-cam rectangle (x, y, w, h) around a face: about three face widths, snapped to the nearest edge when the face sits near
    it (a webcam is flush with a corner), at the aspect the top band needs."""
    x, y, fw, fh = face[:4]
    rw = min(max(fw * 3.0, w * 0.12), w * 0.42)
    rh = rw / aspect
    if rh > h * 0.5:
        rh = h * 0.5; rw = rh * aspect
    cx, cy = x + fw / 2, y + fh / 2
    rx = 0.0 if cx - rw / 2 < w * 0.06 else w - rw if cx + rw / 2 > w * 0.94 else cx - rw / 2
    ry = 0.0 if cy - rh / 2 < h * 0.06 else h - rh if cy + rh / 2 > h * 0.94 else cy - rh / 2
    return int(rx), int(ry), int(rw), int(rh)


def find_facecam(src: Path, start: float, end: float, aspect: float) -> dict:
    """{"kind": "inset", "rect": (x, y, w, h) in frame fractions} | {"kind": "fullbleed"} | {"kind": "none"}."""
    try:
        times = [start + (i + 0.5) * (end - start) / SAMPLES for i in range(SAMPLES)]
        frames = _frames(src, times, FRAME_W)
        if not frames:
            return {"kind": "none"}
        h, w = frames[0].shape[:2]
        det = _detector(w, h)
        cands, big = [], 0
        for fi, fr in enumerate(frames):
            _, found = det.detect(fr)
            for f in [] if found is None else found:
                box = (float(f[0]), float(f[1]), float(f[2]), float(f[3]))
                if eligible(box, w, h):
                    cands.append({"fi": fi, "cx": box[0] + box[2] / 2, "cy": box[1] + box[3] / 2, "box": box, "score": float(f[-1])})
            if found is not None and any(float(f[3]) > h * 0.28 and abs((float(f[0]) + float(f[2]) / 2) / w - 0.5) < 0.2 for f in found):
                big += 1
        group = stationary_cluster(cands, len(frames), w, h)
        if group:
            med = tuple(statistics.median(c["box"][i] for c in group) for i in range(4))
            x, y, rw, rh = cam_rect(med, w, h, aspect)
            return {"kind": "inset", "rect": (x / w, y / h, rw / w, rh / h)}
        if big >= len(frames) * 0.7:
            return {"kind": "fullbleed"}                 # an IRL or full-bleed cam: the face is the picture
    except Exception:
        pass
    return {"kind": "none"}


def gameplay_range(src_w: int, src_h: int, gw: int, cam: tuple | None) -> tuple[int, int, bool]:
    """Left edge range (x_min, x_max) for the gameplay crop, and whether it can avoid the face-cam completely."""
    lo, hi = 0, max(src_w - gw, 0)
    if cam is None:
        return lo, hi, True
    margin = 0.04 * src_w            # the detected box can be a little narrower than the real webcam frame
    cx0, cx1 = cam[0] * src_w - margin, (cam[0] + cam[2]) * src_w + margin
    if (cam[0] + cam[2] / 2) < 0.5:
        lo = min(max(lo, math.ceil(cx1)), hi)
        return lo, hi, lo >= cx1 - 1
    hi = max(min(hi, math.floor(cx0) - gw), 0)
    return lo, hi, hi + gw <= cx0 + 1


def motion_path(src: Path, start: float, end: float, src_w: int, gw: int, lo: int, hi: int) -> list[tuple[float, float]]:
    """Keyframes (seconds into the clip, crop centre 0..1 of the width) for the gameplay crop: halfway between the middle of the allowed
    range and the centre of the motion, smoothed and limited to MAX_PAN_PX_S."""
    import numpy as np
    dur = end - start
    mid = (lo + hi) / 2
    if hi <= lo:
        return [(0.0, (lo + gw / 2) / src_w), (round(dur, 2), (lo + gw / 2) / src_w)]
    n = max(2, int(dur * MOTION_FPS))
    times = [start + i / MOTION_FPS for i in range(n)]
    frs = _frames(src, times, 320)
    targets = []
    for a, b in zip(frs, frs[1:]):
        d = np.abs(a.astype(np.int16) - b.astype(np.int16)).sum(2).sum(0).astype(np.float64)
        c = (d * np.arange(len(d))).sum() / d.sum() / len(d) * src_w if d.sum() > 0 else mid + gw / 2
        targets.append(min(max(CENTRE_WEIGHT * (mid + gw / 2) + (1 - CENTRE_WEIGHT) * c, lo + gw / 2), hi + gw / 2))
    if not targets:
        targets = [mid + gw / 2]
    limit = MAX_PAN_PX_S * src_w / 1280 / MOTION_FPS
    cur, pts = targets[0], []
    for i, t in enumerate(targets):
        cur += min(max((t - cur) * 0.35, -limit), limit)        # smoothing, then the velocity limit
        pts.append((round(i / MOTION_FPS, 2), round(cur / src_w, 4)))
    pts.append((round(dur, 2), pts[-1][1]))
    return pts


def plan(src: Path, clip: dict, src_w: int, src_h: int, out_w: int, out_h: int) -> dict:
    """How to lay out this gaming clip: {"layout": "stack", cam, game...} or {"layout": "fullbleed"} or {"layout": "none"}."""
    if src_w <= src_h:
        return {"layout": "none"}
    cam_h = round(out_h * CAM_SHARE / 2) * 2
    game_h = out_h - cam_h
    found = find_facecam(src, float(clip["start_s"]), float(clip["end_s"]), out_w / cam_h)
    if found["kind"] == "fullbleed":
        return {"layout": "fullbleed"}
    if found["kind"] != "inset":
        return {"layout": "none"}
    gw = min(src_w, round(src_h * out_w / game_h / 2) * 2)
    lo, hi, clean = gameplay_range(src_w, src_h, gw, found["rect"])
    if not clean:
        return {"layout": "none", "reason": "the face-cam cannot be kept out of the gameplay crop"}
    path = motion_path(src, float(clip["start_s"]), float(clip["end_s"]), src_w, gw, lo, hi)
    return {"layout": "stack", "cam": found["rect"], "gw": gw, "path": path, "cam_h": cam_h, "game_h": game_h}


def stack_graph(p: dict, src_w: int, src_h: int, out_w: int, out_h: int, label: str = "lay") -> str:
    """The filtergraph: the face-cam rectangle scaled into the top band, the gameplay crop (moving along its path) below it."""
    from .adapters import media
    cx, cy, cw, ch = p["cam"]
    rw, rh = max(2, int(cw * src_w) // 2 * 2), max(2, int(ch * src_h) // 2 * 2)
    rx, ry = min(int(cx * src_w), src_w - rw), min(int(cy * src_h), src_h - rh)
    gw = p["gw"]
    expr = media.path_expr(p["path"], src_w, gw)
    return (f"[0:v]split=2[cama][gamea];[cama]crop={rw}:{rh}:{rx}:{ry},scale={out_w}:{p['cam_h']},setsar=1[cam];"
            f"[gamea]crop={gw}:{src_h}:'{expr}':0,scale={out_w}:{p['game_h']},setsar=1[game];[cam][game]vstack=inputs=2,setsar=1[{label}]")
