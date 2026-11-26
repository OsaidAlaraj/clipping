"""Clip layouts: one place that knows the four looks a 9:16 clip can have and how to pick between them.

  single    the existing crop that follows the speaker (or a blurred pad when the picture is not a talking head)
  gaming    the existing face-cam on top, gameplay below (gaming_layout.py)
  split     two panels, one above the other, a thin seam between them, the captions ON the seam. Two people from the same
            two-shot (a podcast or interview, one face per panel) or the main person on top and a second feed below (looped
            gameplay or a reaction file from the feeds folder)
  reaction  the face fills the frame, the source picture sits in a small card (picture-in-picture) where it covers nobody

Chosen like the top editors do it (OpusClip: Fill / Fit / Split / Gameplay, the tool picks per scene; OpenShorts and
opensource-clipping: stacked panels for two speakers, captions on the seam), with the safe areas worked out here, in output
pixels, so a face never sits under the hook title, the captions, the label or the progress bar (see docs/clip-layouts.md).

Everything here is geometry plus a few sampled frames (YuNet, MIT, already in the repo). The filtergraph stays one encode.
"""
from __future__ import annotations

import hashlib
import statistics
from pathlib import Path

from . import gaming_layout

KINDS = ("single", "gaming", "split", "reaction")
OVERRIDES = ("auto",) + KINDS

# ------------------------------------------------------------------ safe areas, as shares of the output height
HOOK_BAND = (0.085, 0.165)        # the title card at its default top position (build_ass: margin 10 %, one or two lines)
CAPTION_BAND = (0.70, 0.85)       # lower captions: margin 16 % up to about three lines
SEAM_BAND = 0.06                  # captions on the seam of a split: this share above and below the middle
CORNER_BAND = (0.0, 0.07)         # the label (top left, top right for Arabic); a future logo takes the same strip
BOTTOM_BAND = (0.86, 1.0)         # progress bar and the platform's own controls
FACE_MARGIN = 0.03                # a card or caption keeps this much of the height away from a face

SPLIT_FACE_Y = 0.52               # where a face sits in its panel (share of the panel height): below the hook, above the seam captions
SPLIT_FACE_W = 0.26               # a face is about this share of the panel width
REACTION_FACE_Y = 0.36
REACTION_FACE_H = 0.20            # the face is about this share of the frame height
PIP_W = 0.52                      # the content card, share of the frame width
PIP_MIN_CAM_PX = 480              # an inset webcam shorter than this is too soft to fill the frame (4x upscale at most)
MIN_CROP_W = 480                  # never zoom a panel in by more than about 2.3x
SAMPLES = 8


# ------------------------------------------------------------------ looking at the clip

def look(src: Path, start: float, end: float) -> dict:
    """What the sampled frames show: the faces per frame (boxes as fractions of the picture), the face-cam finding. {} on any failure."""
    try:
        times = [start + (i + 0.5) * (end - start) / SAMPLES for i in range(SAMPLES)]
        frames = gaming_layout._frames(src, times, gaming_layout.FRAME_W)
        if not frames:
            return {}
        h, w = frames[0].shape[:2]
        det = gaming_layout._detector(w, h)
        per = []
        for fr in frames:
            _, found = det.detect(fr)
            per.append([(f[0] / w, f[1] / h, f[2] / w, f[3] / h, float(f[-1])) for f in ([] if found is None else found) if f[2] * f[3] > 0])
        return {"faces": per, "aspect": w / h}
    except Exception:
        return {}


def main_face(seen: dict) -> tuple | None:
    """The median box (x, y, w, h as fractions) of the one person who is there most: faces are grouped by where they sit (a second
    person must not drag the median between two people), the group seen in the most frames wins, then the bigger one. Needs a face in
    at least half the frames."""
    frames = seen.get("faces", [])
    best = None
    for seed in (b for fs in frames for b in fs):
        cx = seed[0] + seed[2] / 2
        per = {}
        for i, fs in enumerate(frames):
            near = [b for b in fs if abs(b[0] + b[2] / 2 - cx) <= 0.08]
            if near:
                per[i] = max(near, key=lambda b: b[2] * b[3])
        group = list(per.values())
        score = (len(group), statistics.median(b[2] * b[3] for b in group))
        if best is None or score > best[0]:
            best = (score, group)
    if best is None or best[0][0] < max(2, len(frames) / 2):
        return None
    return tuple(statistics.median(b[i] for b in best[1]) for i in range(4))


def face_pair(seen: dict) -> tuple | None:
    """Two people side by side in a two-shot: (left box, right box), each the median of its side. Both need to be there in at least
    half the frames, about the same size, and clearly apart (a person and a poster behind them are not a pair)."""
    frames = seen.get("faces", [])
    lefts, rights = [], []
    for fs in frames:
        ranked = sorted([b for b in fs if b[2] * b[3] >= 0.0012], key=lambda b: -b[2] * b[3])
        big = ranked[:2]
        if len(big) == 2 and not (len(ranked) > 2 and ranked[2][2] * ranked[2][3] >= 0.4 * big[1][2] * big[1][3]):   # a panel of three is not a two-shot
            a, b = sorted(big, key=lambda f: f[0])
            if (b[0] + b[2] / 2) - (a[0] + a[2] / 2) >= 0.2 and 0.5 <= (a[2] * a[3]) / (b[2] * b[3]) <= 2.0:
                lefts.append(a); rights.append(b)
    if len(lefts) < max(2, len(frames) / 2):
        return None
    med = lambda boxes: tuple(statistics.median(x[i] for x in boxes) for i in range(4))
    return med(lefts), med(rights)


# ------------------------------------------------------------------ geometry

def window(face: tuple, src_w: int, src_h: int, aspect: float, face_w: float, face_y: float, max_w: float | None = None,
           min_w: float = MIN_CROP_W) -> tuple[int, int, int, int]:
    """The crop (x, y, w, h in source pixels, even numbers) of the given aspect that puts the face at `face_y` of its height and makes
    it `face_w` of its width, as far as the picture allows. `max_w` keeps a neighbour out."""
    fx, fy, fw, fh = face[0] * src_w, face[1] * src_h, face[2] * src_w, face[3] * src_h
    cap_w = min(src_w, src_h * aspect, max_w or src_w)
    cw = min(max(fw / face_w, min(min_w, cap_w)), cap_w)
    ch = cw / aspect
    x = min(max(fx + fw / 2 - cw / 2, 0), src_w - cw)
    y = min(max(fy + fh / 2 - face_y * ch, 0), src_h - ch)
    even = lambda v: int(v) // 2 * 2
    return even(x), even(y), even(cw), even(ch)


def face_in_output(face: tuple, crop: tuple, src_w: int, src_h: int, out_w: int, out_h: int, y0: int = 0, panel_h: int | None = None) -> tuple:
    """The face box (x, y, w, h) in output pixels after the crop is scaled to the panel at row y0."""
    cx, cy, cw, ch = crop
    ph = panel_h or out_h
    sx, sy = out_w / cw, ph / ch
    return ((face[0] * src_w - cx) * sx, y0 + (face[1] * src_h - cy) * sy, face[2] * src_w * sx, face[3] * src_h * sy)


def _overlap(a: tuple, b: tuple, margin: float = 0.0) -> bool:
    return not (a[0] + a[2] + margin <= b[0] or b[0] + b[2] + margin <= a[0] or a[1] + a[3] + margin <= b[1] or b[1] + b[3] + margin <= a[1])


def pip_slot(face_out: tuple, pip_w: int, pip_h: int, out_w: int, out_h: int) -> tuple[int, int] | None:
    """Where the content card goes: the first place from the list that covers no face, no caption, no hook title, no label and not the
    bottom controls. The card sits at the right (the left for none of the slots: the label and logo strip is clear in both)."""
    margin = out_w * 0.04
    x = int(out_w - pip_w - margin)
    top_slot = int(out_h * HOOK_BAND[1] + out_h * 0.01)
    low_slot = int(out_h * CAPTION_BAND[0] - pip_h - out_h * 0.01)
    for y in (top_slot, low_slot):
        box = (x, y, pip_w, pip_h)
        if y + pip_h <= out_h * CAPTION_BAND[0] and y >= out_h * HOOK_BAND[0] and not _overlap(box, face_out, out_h * FACE_MARGIN):
            return x, y
    return None


# ------------------------------------------------------------------ planning each layout

def _panel_h(out_h: int) -> int:
    return out_h // 2 // 2 * 2


def plan_split(seen: dict, src_w: int, src_h: int, out_w: int, out_h: int, feed: dict | None = None) -> dict:
    """Two panels. Two faces in the picture: one per panel, the left person on top. One face and a feed: the person on top, the feed
    below. Otherwise {"layout": "none", "reason": ...}."""
    ph = _panel_h(out_h)
    aspect = out_w / ph
    pair = face_pair(seen)
    if pair:
        left, right = pair
        sep = ((right[0] + right[2] / 2) - (left[0] + left[2] / 2)) * src_w
        crops = [window(b, src_w, src_h, aspect, SPLIT_FACE_W, SPLIT_FACE_Y, max_w=sep * 0.92) for b in (left, right)]
        faces_out = [face_in_output(b, c, src_w, src_h, out_w, out_h, y0=i * ph, panel_h=ph) for i, (b, c) in enumerate(zip((left, right), crops))]
        return {"layout": "split", "mode": "two_faces", "crops": crops, "faces_out": faces_out, "panel_h": ph}
    face = main_face(seen)
    if feed and face:
        crop = window(face, src_w, src_h, aspect, SPLIT_FACE_W, SPLIT_FACE_Y)
        return {"layout": "split", "mode": "feed", "crops": [crop], "faces_out": [face_in_output(face, crop, src_w, src_h, out_w, out_h, panel_h=ph)],
                "panel_h": ph, "feed": feed}
    return {"layout": "none", "reason": "split needs two people in the picture, or one person and a file in the feeds folder"}


def plan_reaction(seen: dict, src_w: int, src_h: int, out_w: int, out_h: int, cam: dict | None = None) -> dict:
    """The face large over the whole frame and the picture in a card. An inset face-cam (a reactor over content) fills the frame when it
    is sharp enough; otherwise the biggest face of the picture does."""
    if src_w <= src_h:
        return {"layout": "none", "reason": "reaction needs a wide picture"}
    aspect = out_w / out_h
    if cam and cam.get("kind") == "inset":
        rx, ry, rw, rh = cam["rect"]
        if rh * src_h >= PIP_MIN_CAM_PX:
            face = (rx + rw * 0.3, ry + rh * 0.15, rw * 0.4, rh * 0.7)
            return _reaction_plan(face, _tight_crop(face, src_w, src_h, aspect, max_h=rh * src_h), src_w, src_h, out_w, out_h, "cam")
    face = main_face(seen)
    if not face:
        return {"layout": "none", "reason": "no face found for the reaction"}
    return _reaction_plan(face, _tight_crop(face, src_w, src_h, aspect), src_w, src_h, out_w, out_h, "face")


def _tight_crop(face: tuple, src_w: int, src_h: int, aspect: float, max_h: float | None = None) -> tuple[int, int, int, int]:
    """A 9:16 crop whose height makes the face REACTION_FACE_H of the frame, with the face at REACTION_FACE_Y."""
    fx, fy, fw, fh = face[0] * src_w, face[1] * src_h, face[2] * src_w, face[3] * src_h
    ch = min(max(fh / REACTION_FACE_H, src_h * 0.45), src_h, max_h or src_h)
    cw = ch * aspect
    if cw > src_w:
        cw, ch = src_w, src_w / aspect
    x = min(max(fx + fw / 2 - cw / 2, 0), src_w - cw)
    y = min(max(fy + fh / 2 - REACTION_FACE_Y * ch, 0), src_h - ch)
    even = lambda v: int(v) // 2 * 2
    return even(x), even(y), even(cw), even(ch)


def _reaction_plan(face: tuple, crop: tuple, src_w: int, src_h: int, out_w: int, out_h: int, base: str) -> dict:
    face_out = face_in_output(face, crop, src_w, src_h, out_w, out_h)
    for share in (PIP_W, PIP_W * 0.85):          # a smaller card when the first size has no clear place
        pip_w = int(out_w * share) // 2 * 2
        pip_h = int(pip_w * src_h / src_w) // 2 * 2
        slot = pip_slot(face_out, pip_w, pip_h, out_w, out_h)
        if slot:
            return {"layout": "reaction", "base": base, "crop": crop, "faces_out": [face_out], "pip": {"x": slot[0], "y": slot[1], "w": pip_w, "h": pip_h}}
    return {"layout": "none", "reason": "the content card would cover the face"}


def plan(kind: str, src: Path, clip: dict, src_w: int, src_h: int, out_w: int, out_h: int, seen: dict | None = None, feed: dict | None = None) -> dict:
    """The plan for split or reaction: {"layout": "split"|"reaction", ...} or {"layout": "none", "reason"} when the footage cannot carry it."""
    if src_w <= src_h:
        return {"layout": "none", "reason": "the picture is already vertical"}
    seen = seen if seen is not None else look(src, float(clip["start_s"]), float(clip["end_s"]))
    if not seen:
        return {"layout": "none", "reason": "no face detector available"}
    if kind == "split":
        found = plan_split(seen, src_w, src_h, out_w, out_h, feed)
    else:
        cam = gaming_layout.find_facecam(src, float(clip["start_s"]), float(clip["end_s"]), out_w / out_h)
        found = plan_reaction(seen, src_w, src_h, out_w, out_h, cam)
    bad = violations(found, out_w, out_h)
    return {"layout": "none", "reason": f"{kind} would put {bad[0]} with this footage"} if bad else found


# ------------------------------------------------------------------ the filtergraph

def graph(p: dict, out_w: int, out_h: int, label: str = "lay", feed_input: int = 1) -> str:
    """The filtergraph of a split or reaction plan, ending at `[label]`."""
    if p["layout"] == "split":
        ph = p["panel_h"]
        a = p["crops"][0]
        top = f"[0:v]split=2[sa][sb];[sa]crop={a[2]}:{a[3]}:{a[0]}:{a[1]},scale={out_w}:{ph}:flags=lanczos,setsar=1[top]"
        if p["mode"] == "two_faces":
            b = p["crops"][1]
            bottom = f"[sb]crop={b[2]}:{b[3]}:{b[0]}:{b[1]},scale={out_w}:{ph}:flags=lanczos,setsar=1[bot]"
        else:
            top = f"[0:v]crop={a[2]}:{a[3]}:{a[0]}:{a[1]},scale={out_w}:{ph}:flags=lanczos,setsar=1[top]"
            bottom = (f"[{feed_input}:v]setpts=PTS-STARTPTS,scale={out_w}:{ph}:force_original_aspect_ratio=increase,crop={out_w}:{ph},setsar=1[bot]")
        seam = max(out_h // 160, 4)
        return f"{top};{bottom};[top][bot]vstack=inputs=2,drawbox=x=0:y=ih/2-{seam // 2}:w=iw:h={seam}:color=black@0.9:t=fill,setsar=1[{label}]"
    x, y, w, h = p["crop"]
    pip = p["pip"]
    return (f"[0:v]split=2[ra][rb];[ra]crop={w}:{h}:{x}:{y},scale={out_w}:{out_h}:flags=lanczos,setsar=1[big];"
            f"[rb]scale={pip['w'] - 8}:{pip['h'] - 8}:flags=lanczos,pad={pip['w']}:{pip['h']}:4:4:color=white,setsar=1[card];"
            f"[big][card]overlay={pip['x']}:{pip['y']},setsar=1[{label}]")


# ------------------------------------------------------------------ the suggestion and the feeds

HINTS = {"split": ("split screen", "split-screen", "splitscreen", "شاشة منقسمة"), "reaction": ("reaction", "react ", "ردة فعل", "ردود الفعل")}


def hint_of(text: str | None) -> str | None:
    """The layout a campaign's edit-style guidance asks for, if it names one."""
    low = (text or "").lower()
    for kind, words in HINTS.items():
        if any(w in low for w in words):
            return kind
    return None


def suggest(niche: str | None, src_w: int, src_h: int, seen: dict, hint_text: str | None = None, has_feed: bool = False,
            out_w: int = 1080, out_h: int = 1920) -> dict:
    """The layout this clip should get when the owner has not chosen: {"layout", "why"}. Conservative: anything but a clear signal stays
    the single crop (or the gaming stack for a gaming source), because a wrong split looks worse than a plain crop."""
    if src_w <= src_h:
        return {"layout": "single", "why": "The picture is already vertical."}
    hint = hint_of(hint_text)
    if niche == "gaming":
        return {"layout": "gaming", "why": "Gaming footage: the face-cam over the gameplay."}
    split = plan_split(seen, src_w, src_h, out_w, out_h, {"path": ""} if has_feed else None)
    if split["layout"] == "split" and not violations(split, out_w, out_h):
        if split["mode"] == "two_faces":
            return {"layout": "split", "why": "Two people in the same shot: one panel each."}
        if hint == "split":
            return {"layout": "split", "why": "The campaign asks for a split screen; one person on top, a second feed below."}
    react = plan_reaction(seen, src_w, src_h, out_w, out_h, None) if hint == "reaction" else {}
    if react.get("layout") == "reaction" and not violations(react, out_w, out_h):
        return {"layout": "reaction", "why": "The campaign asks for a reaction: the face large, the content in a card."}
    return {"layout": "single", "why": "One speaker: the crop that follows them."}


def pick_feed(folder: Path | None, seed: str) -> dict | None:
    """A second-feed file from the feeds folder, the same one for the same clip: {"path", "offset_s"}. None when there is none."""
    if not folder or not Path(folder).is_dir():
        return None
    files = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm") and p.is_file())
    if not files:
        return None
    digest = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    return {"path": str(files[digest % len(files)]), "offset_s": float((digest >> 16) % 30)}


# ------------------------------------------------------------------ the safe-area check (used by the measurement and the tests)

def violations(p: dict, out_w: int, out_h: int, caption_position: str = "lower") -> list[str]:
    """What a plan puts under something that must stay clear: the faces against the hook title, the captions (on the seam for a split,
    lower otherwise), the label and logo strip and the bottom controls; the content card against the same. Empty when it is clean."""
    if p.get("layout") not in ("split", "reaction"):
        return []
    bands = {"hook title": HOOK_BAND, "label and logo strip": CORNER_BAND, "bottom controls": BOTTOM_BAND}
    bands["captions"] = (0.5 - SEAM_BAND, 0.5 + SEAM_BAND) if p["layout"] == "split" else CAPTION_BAND
    problems = []
    for i, f in enumerate(p.get("faces_out", [])):
        for name, (lo, hi) in bands.items():
            if f[1] < hi * out_h and f[1] + f[3] > lo * out_h:
                problems.append(f"face {i + 1} is under the {name}")
        if f[0] < 0 or f[0] + f[2] > out_w or f[1] < 0 or f[1] + f[3] > out_h:
            problems.append(f"face {i + 1} is cut off")
    if p["layout"] == "reaction":
        pip = p["pip"]
        box = (pip["x"], pip["y"], pip["w"], pip["h"])
        for name, (lo, hi) in {k: v for k, v in bands.items() if k != "label and logo strip"}.items():
            if box[1] < hi * out_h and box[1] + box[3] > lo * out_h:
                problems.append(f"the content card is under the {name}")
        for f in p.get("faces_out", []):
            if _overlap(box, f):
                problems.append("the content card covers a face")
    return problems
