"""A5 rendering. One FFmpeg encode per clip: reframe, captions, the disclosure label and the originality layer (title card,
progress bar, emphasis zooms), plus the cover image. Own code on FFmpeg (spec 10.2).

The layers that sit on top of the picture (captions, label, title card, progress bar) are one ASS subtitle file, so the
whole clip is a single filter graph and a single encode. User text never enters the filter graph: only the fixed ASS file
name does, and every piece of text that goes into the ASS file is escaped."""
from __future__ import annotations

import math
import re
from pathlib import Path

from .. import faces, ffmpeg, gaming_layout, hook_rules, music, polish
from ..caption_text import repair_stray_tokens
from ..core import AppError
from .base import Adapter, register

# ------------------------------------------------------------------ the template: what makes an account's clips look like its own

TEMPLATE_DEFAULTS: dict = {"layout": "auto", "font": "Arial", "primary": "#FFFFFF", "accent": "#FFC400", "caption_position": "lower",
                           "title_card": True, "progress_bar": True, "zooms": True, "font_size": 3.7, "cover": "band",
                           "tighten": True, "karaoke": True, "caption_style": "auto"}
CAPTION_POSITIONS = ("lower", "middle")
COVER_STYLES = ("band", "outline")
NICHE_OF_CROP = {"facecam_over_game": "gaming", "follow_speaker": "podcast"}   # the profile's crop style tells the niche


def niche_defaults(niche: str | None, language: str | None = None) -> dict:
    """Per-niche render defaults (layout, caption position, caption centre and size, caption words), from the measured market
    studies; a template's own fields win. A cell for the niche and language (`podcast_ar`) refines the niche's entry (`podcast`)."""
    import json
    try:
        data = json.loads((Path(__file__).resolve().parent.parent / "assets" / "render-profile-defaults.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {**data.get(niche or "", {}), **data.get(f"{niche}_{language}", {})} if niche else {}


LAYOUTS = ("auto", "blur_pad", "gaming_stack")   # auto: the profile crop decides; blur_pad: the whole picture centred on a blurred, darkened 9:16 copy
FLAGS = ("title_card", "progress_bar", "zooms", "tighten", "karaoke")
FONT_SIZE_RANGE = (2.5, 6.5)          # caption size as a percentage of the frame height
_FONT = re.compile(r"^\w[\w .\-]{0,59}$")
_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _bad(message: str) -> AppError:
    return AppError(message, "bad_template")


def _checked(key: str, value):
    """The value when it is valid for this template field, else raises the refusal that names the field."""
    if key == "font" and not (isinstance(value, str) and _FONT.match(value.strip())):
        raise _bad("The font name can use letters, numbers, spaces, dots and dashes, up to 60 characters.")
    if key in ("primary", "accent") and not (isinstance(value, str) and _COLOUR.match(value)):
        raise _bad(f"The {key} colour must look like #FFC400.")
    if key == "caption_position" and value not in CAPTION_POSITIONS:
        raise _bad("The caption position must be lower or middle.")
    if key == "caption_style":
        from . import caption_render
        if value != "auto" and value not in caption_render.STYLES:
            raise _bad("The caption style must be auto or one of: " + ", ".join(caption_render.STYLES) + ".")
    if key == "layout" and value not in LAYOUTS:
        raise _bad("The layout must be auto, blur_pad or gaming_stack.")
    if key == "cover" and value not in COVER_STYLES:
        raise _bad("The cover style must be band or outline.")
    if key in FLAGS and not isinstance(value, bool):
        raise _bad(f"{key.replace('_', ' ').capitalize()} must be on or off.")
    if key == "font_size":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not FONT_SIZE_RANGE[0] <= value <= FONT_SIZE_RANGE[1]:
            raise _bad(f"The caption size must be between {FONT_SIZE_RANGE[0]} and {FONT_SIZE_RANGE[1]} percent of the frame height.")
        return float(value)
    return value.strip() if isinstance(value, str) else value


def normalize_template(spec, strict: bool = False) -> dict:
    """A complete template. A missing or invalid field takes its default (a render must never fail on a bad template);
    with `strict` an invalid field is refused instead (an owner edit). Unknown fields are dropped."""
    spec = spec if isinstance(spec, dict) else {}
    out = dict(TEMPLATE_DEFAULTS)
    for key in TEMPLATE_DEFAULTS:
        if key not in spec:
            continue
        try:
            out[key] = _checked(key, spec[key])
        except AppError:
            if strict:
                raise
    return out


# ------------------------------------------------------------------ framing

def crop_filter(src_w: int, src_h: int, out_w: int, out_h: int, focus_x: float | None = None, path: list[tuple[float, float]] | None = None) -> str:
    """Crop to 9:16. focus_x (0..1) is the speaker's horizontal position when a face was found;
    None means centre crop, which is also the fallback."""
    target = out_w / out_h
    if src_w / src_h > target:
        crop_w = int(round(src_h * target / 2) * 2)
        centre = (focus_x if focus_x is not None else 0.5) * src_w
        x = int(min(max(centre - crop_w / 2, 0), src_w - crop_w))
        crop = f"crop={crop_w}:{src_h}:{x}:0"
        if path and len(path) > 1:
            crop = f"crop={crop_w}:{src_h}:'{path_expr(path, src_w, crop_w)}':0"
    else:
        crop_h = int(round(src_w / target / 2) * 2)
        crop = f"crop={src_w}:{min(crop_h, src_h)}:0:{max((src_h - crop_h) // 2, 0)}"
    return f"{crop},scale={out_w}:{out_h}:flags=lanczos,setsar=1"


def path_expr(path: list[tuple[float, float]], src_w: int, crop_w: int) -> str:
    """ffmpeg expression for the crop's left edge over time (t = seconds into the clip): straight lines between keyframes (time, centre 0..1)."""
    xs = [(t, int(min(max(c * src_w - crop_w / 2, 0), src_w - crop_w))) for t, c in path]
    expr = str(xs[-1][1])
    for (t0, x0), (t1, x1) in reversed(list(zip(xs, xs[1:]))):
        seg = str(x0) if x1 == x0 or t1 <= t0 else f"{x0}+({x1 - x0})*(t-{t0})/{t1 - t0:.3f}"
        expr = f"if(lt(t,{t1}),{seg},{expr})"
    return f"if(lt(t,{xs[0][0]}),{xs[0][1]},{expr})"


FOLLOW_STYLES = ("follow_speaker", "follow_action")
FACE_MIN_SHARE = 0.02          # skin-toned share of the frame below which nobody is taken to be there


def layout_graph(style: str, src_w: int, src_h: int, out_w: int, out_h: int, focus_x: float | None, label: str = "lay", path: list[tuple[float, float]] | None = None, stack: dict | None = None) -> str:
    """The reframing part of the filter graph, ending at the pad `[label]`. `facecam_over_game` keeps the whole picture (the
    facecam stays in it) fitted to the width over a blurred fill; every other style is a 9:16 crop."""
    if style == "gaming_stack" and stack and stack.get("layout") == "stack":
        from .. import gaming_layout
        return gaming_layout.stack_graph(stack, src_w, src_h, out_w, out_h, label)
    if style == "blur_pad":
        return (f"[0:v]split=2[bga][fga];[bga]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h},"
                f"boxblur=luma_radius=30:luma_power=2,eq=brightness=-0.18[bg];[fga]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
                f"[bg][fg]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2,setsar=1[{label}]")
    if style == "facecam_over_game" and src_w / src_h > out_w / out_h:
        return (f"[0:v]split=2[bga][fga];[bga]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h},"
                f"boxblur=luma_radius=24:luma_power=2[bg];[fga]scale={out_w}:-2:flags=lanczos[fg];"
                f"[bg][fg]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2,setsar=1[{label}]")
    return f"[0:v]{crop_filter(src_w, src_h, out_w, out_h, focus_x if style in FOLLOW_STYLES else None, path if style in FOLLOW_STYLES else None)}[{label}]"


def _samples(clip: dict, count: int = 3) -> list[float]:
    """Absolute times worth looking at for a face: the seconds of the clip where the face signal is strongest, else the middle."""
    start, end = float(clip["start_s"]), float(clip["end_s"])
    sig = clip.get("signals") or {}
    face = sig.get("face") or []
    first = int(sig.get("first_s", start))
    ranked = sorted((i for i, v in enumerate(face) if first + i + 0.5 < end and first + i >= start - 1 and v >= 0.3), key=lambda i: -face[i])
    return [first + i + 0.5 for i in ranked[:count]] or [(start + end) / 2]


def face_focus(src: Path, clip: dict) -> float | None:
    """Horizontal position (0..1) of the person in the clip: the median centre of the skin-toned pixels over a few frames.
    None when no frame shows anybody, which gives the centre crop. A stand-in until a face detector is installed."""
    import numpy as np

    width, height, found = 96, 54, []
    for at in _samples(clip):
        raw = ffmpeg.frame_raw(src, at, width, height)
        if raw is None:
            continue
        mask = ffmpeg.skin_mask(np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3).astype(np.int16))
        if mask.mean() >= FACE_MIN_SHARE:
            found.append(float((mask.sum(axis=0) * np.arange(width)).sum() / mask.sum() + 0.5) / width)
    return float(np.median(found)) if found else None


# ------------------------------------------------------------------ emphasis zooms

ZOOM_DEPTH, ZOOM_HALF_S, ZOOM_GAP_S, ZOOM_MAX = 0.07, 0.35, 4.0, 6


def zoom_times(emphasis: list[float], clip_start: float, duration: float) -> list[float]:
    """Where in the clip (seconds from its start) the picture pushes in: loud moments, at least ZOOM_GAP_S apart, none in the
    first or last second, at most ZOOM_MAX."""
    picked: list[float] = []
    for at in sorted(float(e) - clip_start for e in emphasis):
        if 1.0 <= at <= duration - 1.0 and (not picked or at - picked[-1] >= ZOOM_GAP_S):
            picked.append(round(at, 2))
    return picked[:ZOOM_MAX]


def zoom_graph(times: list[float], out_w: int, out_h: int, source: str = "lay", label: str = "zm") -> str:
    """Scale up by a few percent around each time and crop back to the frame (before the subtitles, so text never zooms)."""
    bumps = "+".join(f"max(0,1-abs(t-{t:.2f})/{ZOOM_HALF_S})" for t in times)
    z = f"(1+{ZOOM_DEPTH}*({bumps}))"
    return (f"[{source}]scale=w='trunc({out_w}*{z}/2)*2':h='trunc({out_h}*{z}/2)*2':eval=frame,crop={out_w}:{out_h}[{label}]")


ZOOM_IN_S, ZOOM_HOLD_S, ZOOM_OUT_S = 0.60, 0.90, 1.00     # punch-in: ease in, hold, ease back out
ZOOM_FLAT_STYLES = ("facecam_over_game", "stacked_facecam_over_gameplay", "stacked")   # the gameplay half never zooms


def _smoother(x: str) -> str:
    """ffmpeg expression of the smootherstep curve (zero slope and zero curvature at both ends) of a 0..1 expression."""
    return f"(st(0,clip({x},0,1))*ld(0)*ld(0)*ld(0)*(ld(0)*(ld(0)*6-15)+10))"


def punch_factor(t: float, moments: list[tuple[float, float]]) -> float:
    """The zoom factor at clip time t (python twin of `punch_graph`, used by the measurements and the tests)."""
    def ss(x: float) -> float:
        x = min(max(x, 0.0), 1.0)
        return x * x * x * (x * (x * 6 - 15) + 10)
    return 1 + sum((d - 1) * (ss((t - t0) / ZOOM_IN_S) - ss((t - t0 - ZOOM_IN_S - ZOOM_HOLD_S) / ZOOM_OUT_S)) for t0, d in moments)


def punch_graph(moments: list[tuple[float, float]], out_w: int, out_h: int, source: str = "lay", label: str = "zm") -> str:
    """Punch-in zooms: at each (time, factor) the picture eases in to `factor` (1.05 to 1.12) over ZOOM_IN_S, holds, and eases back
    out, on a smootherstep curve so speed and acceleration start and end at zero (no jerk). The frame is scaled up and cropped
    back around its centre (sizes stay even, so the crop offset is a whole pixel); it runs before the subtitles, so text never zooms."""
    terms = []
    for t0, d in moments:
        up = _smoother(f"(t-{t0:.2f})/{ZOOM_IN_S}")
        down = _smoother(f"(t-{t0 + ZOOM_IN_S + ZOOM_HOLD_S:.2f})/{ZOOM_OUT_S}")
        terms.append(f"{d - 1:.3f}*({up}-{down})")
    z = "(1+" + "+".join(terms) + ")"
    return f"[{source}]scale=w='trunc({out_w}*{z}/2)*2':h='trunc({out_h}*{z}/2)*2':eval=frame:flags=lanczos,crop={out_w}:{out_h}[{label}]"


def tighten_graphs(plan, source: str, has_audio: bool, fps: float) -> str:
    """Video and audio graph that keep only the planned stretches and join them: video by trim and concat, audio by trim and a
    20 ms crossfade at every join (each audio piece reaches half a fade into the cut, so the audio stays exactly as long as the
    video). Ends at [cutv] and, with audio, [cuta]."""
    keeps = plan.keeps
    n = len(keeps)
    half = polish.XFADE_S / 2
    half_frame = 0.5 / fps
    parts = [f"[{source}]split={n}" + "".join(f"[tv{i}]" for i in range(n))]
    parts += [f"[tv{i}]trim=start={max(a - half_frame, 0) if i or plan.prefix else 0:.4f}:end={b - half_frame:.4f},"
              f"setpts=PTS-STARTPTS[cv{i}]" for i, (a, b) in enumerate(keeps)]
    parts.append("".join(f"[cv{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[cutv]")
    if has_audio:
        parts.append(f"[0:a:0]asplit={n}" + "".join(f"[ta{i}]" for i in range(n)))
        parts += [f"[ta{i}]atrim=start={max(a - (half if i else 0), 0):.4f}:end={b + (half if i < n - 1 else 0):.4f},asetpts=PTS-STARTPTS[ca{i}]"
                  for i, (a, b) in enumerate(keeps)]
        chain, prev = [], "ca0"
        for i in range(1, n):
            out = "cuta" if i == n - 1 else f"ax{i}"
            chain.append(f"[{prev}][ca{i}]acrossfade=d={polish.XFADE_S}:c1=tri:c2=tri[{out}]")
            prev = out
        parts += chain if n > 1 else ["[ca0]anull[cuta]"]
    return ";".join(parts)


# ------------------------------------------------------------------ captions and overlay (ASS subtitles)

def _ts(seconds: float) -> str:
    seconds = max(seconds, 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


_CONTROL = re.compile(r"[\x00-\x1f\x7f  ]+")


def _esc(text: str) -> str:
    """Text that is safe inside an ASS dialogue line: no override blocks, no escapes, no line breaks."""
    return _CONTROL.sub(" ", str(text)).replace("\\", "").replace("{", "(").replace("}", ")").strip()


def _ass_colour(colour: str, alpha: int = 0) -> str:
    r, g, b = colour[1:3], colour[3:5], colour[5:7]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _contrast(colour: str) -> str:
    """Black or white, whichever reads better on this colour."""
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return "#101010" if 0.299 * r + 0.587 * g + 0.114 * b > 140 else "#FFFFFF"


def fit_size(text: str, avail_w: float, base: int, max_lines: int = 3, floor: float = 0.55) -> int:
    """The largest font size up to `base` at which the text fits in `max_lines` lines of `avail_w` pixels (a character is about
    0.55 em wide), never below `floor` of the base size. A long Arabic hook is wrapped by the renderer and shrunk by this."""
    size = base
    while size > base * floor and math.ceil(max(len(text), 1) * 0.55 * size / avail_w) > max_lines:
        size -= 1
    return int(max(size, base * floor))


# libass reads a style's Encoding as the paragraph direction: 0 and 1 mean left to right, -1 means detect it from the first strong
# letter. With 1 every colour change inside an Arabic line cut it into segments laid out left to right (measured: a three-word
# line came out as words 2-3-1 when the first was highlighted), so each caption style carries -1.
AUTO_DIRECTION = -1
SOCIAL_PLATFORMS = ("tiktok", "instagram_reels")   # R6: social-style captions; the words themselves are never changed
_LIGHT = str.maketrans("", "", ".,;:،؛")
_EMPH_WINDOW_S = 0.5


def caption_chunks(words: list[dict], clip_start: float, clip_end: float, max_words: int = 4, max_s: float = 1.8) -> list[dict]:
    """Group words into short caption lines, timed relative to the clip. Each chunk keeps its words."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    for word in repair_stray_tokens(words):     # older transcripts still hold lone punctuation and cut-off articles
        if word["end"] <= clip_start or word["start"] >= clip_end:
            continue
        if current and (len(current) >= max_words or word["end"] - current[0]["start"] > max_s
                        or current[-1]["text"].rstrip()[-1:] in ".?!؟"):
            groups.append(current)
            current = []
        current.append(word)
    if current:
        groups.append(current)
    duration = clip_end - clip_start
    return [{"start": max(g[0]["start"] - clip_start, 0), "end": min(g[-1]["end"] - clip_start, duration),
             "text": " ".join(w["text"] for w in g), "words": g} for g in groups]


def caption_events(words: list[dict], clip_start: float, clip_end: float, social: bool, emphasis: list[float],
                   accent: str = TEMPLATE_DEFAULTS["accent"]) -> list[dict]:
    """The caption lines as ASS-ready events. Social style (TikTok, Reels) drops light punctuation and puts the one loudest or
    numeric word of a line in the accent colour; the standard style keeps the text as spoken. Words are never changed."""
    events = []
    for chunk in caption_chunks(words, clip_start, clip_end):
        mids = [(w["start"] + w["end"]) / 2 for w in chunk["words"]]
        near = [(min((abs(m - e) for e in emphasis), default=99.0), i) for i, m in enumerate(mids)]
        loud = min(near)[1] if near and min(near)[0] <= _EMPH_WINDOW_S else None
        parts = []
        for i, word in enumerate(chunk["words"]):
            text = _esc(word["text"].translate(_LIGHT) if social else word["text"])
            if not text:
                continue
            if social and (i == loud or (loud is None and any(c.isdigit() for c in text))):
                text = f"{{\\1c{_ass_colour(accent)}&}}{text}{{\\r}}"
            parts.append(text)
        if parts:
            events.append({"start": chunk["start"], "end": chunk["end"], "text": " ".join(parts)})
    return events


CAPTION_Y_MAX = 0.72                        # never lower: the TikTok and Reels bottom interface zone starts near 0.78
CAPTION_Y = {"en": 0.58, "ar": 0.60}       # centre of the caption block as a share of the frame height (market-style defaults: 0.70, Arabic 0.67)
CAPTION_MAX_W = 0.78                        # share of the width a caption line may use: clear of TikTok's right-hand buttons and the frame edge
KARAOKE_WORDS, KARAOKE_MAX_S, KARAOKE_HOLD_S = 3, 1.6, 0.15


def karaoke_events(words: list[dict], clip_start: float, clip_end: float, social: bool, accent: str, width: int, height: int,
                   cap: int, rtl: bool, position: str = "lower", y_share: float | None = None, max_words: int = KARAOKE_WORDS) -> list[dict]:
    """Word-by-word captions: a short line (up to 3 words) is on screen while it is spoken, and the word being said turns to the
    accent colour (a 70 ms blend from white). Each word has its own subtitle event with the whole line in it, so the line never
    reflows and an Arabic line keeps its right-to-left order and letter joining (the colour changes sit only between words).
    The block is centred at CAPTION_Y of the height and at most CAPTION_MAX_W wide: inside the TikTok and Reels safe zone."""
    events = []
    chunks = caption_chunks(words, clip_start, clip_end, max_words, KARAOKE_MAX_S)
    white, hot = _ass_colour("#FFFFFF"), _ass_colour(accent)
    y = int(height * (min(y_share or CAPTION_Y["ar" if rtl else "en"], CAPTION_Y_MAX) if position == "lower" else 0.5))
    for ci, chunk in enumerate(chunks):
        shown = [(w, _esc(w["text"].translate(_LIGHT) if social else w["text"])) for w in chunk["words"]]
        shown = [(w, text) for w, text in shown if text]
        if not shown:
            continue
        size = fit_size(" ".join(t for _, t in shown), width * CAPTION_MAX_W, cap, max_lines=2)
        head = f"{{\\an5\\pos({width // 2},{y})\\fs{size}"
        stop = chunk["end"] + KARAOKE_HOLD_S
        if ci + 1 < len(chunks):
            stop = min(stop, chunks[ci + 1]["start"])
        for k, (word, _) in enumerate(shown):
            start = chunk["start"] if k == 0 else max(word["start"] - clip_start, chunk["start"])
            end = stop if k == len(shown) - 1 else max(shown[k + 1][0]["start"] - clip_start, start + 0.04)
            if end <= start:
                continue
            body = " ".join((f"{{\\1c{white}&\\t(0,70,\\1c{hot}&)}}{t}{{\\1c{white}&}}" if j == k else t) for j, (_, t) in enumerate(shown))
            events.append({"start": start, "end": end, "text": head + ("\\fad(80,0)" if k == 0 else "") + "}" + body})
    return events


def _box(size: int) -> int:
    """Padding of an opaque text box for a font size."""
    return max(size // 5, 2)


def build_ass(width: int, height: int, duration: float, events: list[dict], label: str | None, rtl: bool, template: dict,
              hook: str | None = None, caption_style: str | None = None, hook_show_s: float | None = None) -> str:
    """One subtitle script for the whole clip: captions, the disclosure label (top right for Arabic, top left otherwise, the
    whole clip), the title card with the hook and the progress bar."""
    t = normalize_template(template)
    cap = int(height * t["font_size"] / 100)
    label_size, bar = int(height * 0.026), max(int(height * 0.006), 3)
    title_size = fit_size(_esc(hook or ""), width * 0.8, int(height * 0.036), max_lines=2)
    primary, accent = _ass_colour(t["primary"]), _ass_colour(t["accent"])
    lower = t["caption_position"] == "lower"
    font = t["font"]
    from . import caption_render
    look = caption_render.ass_style_fields(caption_style, cap) if caption_style else {"bold": 1, "outline": max(cap // 12, 2), "shadow": 0}
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Caption,{font},{cap},{primary},{primary},&H00000000,&H80000000,{look['bold']},0,0,0,100,100,0,0,1,{look['outline']},{look['shadow']},"
        f"{2 if lower else 5},{width // 12},{width // 12},{int(height * 0.16) if lower else 0},{AUTO_DIRECTION}",
        f"Style: Label,{font},{label_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&HC0000000,1,0,0,0,100,100,0,0,3,{_box(label_size)},0,"
        f"{9 if rtl else 7},{width // 24},{width // 24},{int(height * 0.035)},{AUTO_DIRECTION}",
        f"Style: Title,{font},{title_size},{_ass_colour(_contrast(t['accent']))},&H00FFFFFF,{accent},{accent},1,0,0,0,100,100,0,0,3,"
        f"{_box(title_size)},0,8,{width // 10},{width // 10},{int(height * 0.1)},{AUTO_DIRECTION}",
        f"Style: Speaker,{font},{max(int(cap * 0.62), 14)},{_ass_colour(_contrast(t['accent']))},&H00FFFFFF,{accent},{accent},1,0,0,0,100,100,0,0,3,"
        f"{_box(cap // 3)},0,5,0,0,0,{AUTO_DIRECTION}",
        f"Style: Bar,{font},20,{accent},{accent},{accent},{accent},0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    if label:
        lines.append(f"Dialogue: 5,{_ts(0)},{_ts(duration + 1.0)},Label,,0,0,0,,{_esc(label)}")   # runs past the last frame: the full clip
    if hook and t["title_card"]:
        shown = (hook_show_s if hook_show_s and hook_show_s > 0 else hook_rules.show_seconds(hook)) + 0.6   # the owner's own time, or 3 to 5 s from the word count, plus the fades
        lines.append(f"Dialogue: 3,{_ts(0)},{_ts(min(shown, duration))},Title,,0,0,0,,{{\\fad(150,200)}}{_esc(hook)}")
    if t["progress_bar"]:
        lines.append(f"Dialogue: 4,{_ts(0)},{_ts(duration + 1.0)},Bar,,0,0,0,,{{\\an7\\pos(0,{height - bar})\\fscx0.1"
                     f"\\t(0,{int(duration * 1000)},\\fscx100)\\p1}}m 0 0 l {width} 0 {width} {bar} 0 {bar}")
    for event in events:
        lines.append(f"Dialogue: {event.get('layer', 0)},{_ts(event['start'])},{_ts(event['end'])},{event.get('style', 'Caption')},,0,0,0,,{event['text']}")
    return "\n".join(lines) + "\n"


COVER_HOOK_MAX = 60


def short_hook(text: str, limit: int = COVER_HOOK_MAX) -> str:
    """The cover carries a short line: the hook cut at a word before `limit` characters (R7)."""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return (text[:limit].rsplit(" ", 1)[0] or text[:limit]).rstrip(" ,;:-") + "…"


def build_cover_ass(width: int, height: int, hook: str | None, template: dict) -> str:
    """The cover's text: the short hook line low in the frame, in the template's cover style (accent band or outlined text)."""
    t = normalize_template(template)
    text = _esc(short_hook(hook)) if hook else ""
    size = fit_size(text, width * 0.84, int(height * 0.062))
    band = t["cover"] == "band"
    colour = _ass_colour(_contrast(t["accent"]) if band else t["primary"])
    fill = _ass_colour(t["accent"] if band else "#000000")
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cover,{t['font']},{size},{colour},{colour},{fill},{fill},1,0,0,0,100,100,0,0,{3 if band else 1},"
        f"{max(size // 5, 2) if band else max(size // 10, 3)},0,2,{width // 12},{width // 12},{int(height * 0.18)},{AUTO_DIRECTION}",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    if text:
        lines.append(f"Dialogue: 0,{_ts(0)},{_ts(30)},Cover,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ the cover frame

COVER_SIZE = (160, 284)


def cover_times(clip: dict, count: int = 3) -> list[float]:
    """Absolute times worth a cover: seconds in the middle of the clip with a face on screen, laughter or a loud voice and
    no scene change (a cut is blurred or half of another shot). Without signals, a few spread-out times."""
    start, end = float(clip["start_s"]), float(clip["end_s"])
    sig = clip.get("signals") or {}
    first, length = int(sig.get("first_s", start)), end - start
    face, laugh, scene, loud = (sig.get(k) or [] for k in ("face", "laughter", "scene", "loudness"))
    scored = []
    for i in range(len(face)):
        at = first + i + 0.5
        if not start + 0.1 * length <= at <= end - 0.1 * length:
            continue
        loudness = ((loud[i] + 50) / 40 if i < len(loud) else 0.0)
        score = (0.6 * face[i] + 0.25 * (laugh[i] if i < len(laugh) else 0.0) + 0.15 * min(max(loudness, 0), 1)
                 - 0.4 * (scene[i] if i < len(scene) else 0.0))
        scored.append((score, at))
    if not scored:
        return [start + length * f for f in (0.35, 0.5, 0.65)][:count]
    return [at for _, at in sorted(scored, reverse=True)[:count]]


def sharpness(gray: bytes, width: int, height: int) -> float:
    """Variance of the Laplacian of a grey frame: higher is sharper."""
    import numpy as np

    g = np.frombuffer(gray, dtype=np.uint8).reshape(height, width).astype(np.float32)
    lap = 4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(lap.var())


def sharpest_time(src: Path, times: list[float]) -> float:
    """The candidate time whose frame is the sharpest (the first one when none can be read)."""
    width, height = COVER_SIZE
    best, best_score = times[0], -1.0
    for at in times:
        gray = ffmpeg.frame_raw(src, at, width, height, "gray")
        score = sharpness(gray, width, height) if gray else -1.0
        if score > best_score:
            best, best_score = at, score
    return best


# ------------------------------------------------------------------ the render adapter

@register
class FFmpegRender(Adapter):
    stage, provider = "render", "ffmpeg"
    force_crop: str | None = None    # the declared fallback reframes by a plain centre crop

    def health(self) -> dict:
        try:
            return {"ok": True, "provider": self.provider, "binary": Path(ffmpeg.exe()).name}
        except Exception as exc:
            return {"ok": False, "provider": self.provider, "error": str(exc)}

    def _encode(self, args_for, folder: Path, total_s: float | None = None) -> str:
        """Run the one encode with the configured encoder; when NVENC cannot take it (busy, missing) the same encode runs with x264."""
        encoder = ffmpeg.video_encoder(str(self.cfg.get("encoder", "auto")))
        try:
            ffmpeg.run(args_for(encoder), cwd=folder, progress_total_s=total_s)
        except ffmpeg.FFmpegError:
            if encoder == "libx264":
                raise
            encoder = "libx264"
            ffmpeg.run(args_for(encoder), cwd=folder, progress_total_s=total_s)
        return "nvenc" if encoder == "h264_nvenc" else "x264"

    def render_clip(self, src: str, clip: dict, words: list[dict], template: dict, label: str, out: str) -> dict:
        """Cut [start_s, end_s] of `src` and make the finished clip in one encode, then its cover.
        `clip`: start_s, end_s, language, platform, crop, audio, hook_text, emphasis (loud moments, source seconds) and
        signals (per-second series from `first_s`). Returns {"encodes", "encoder", "cover_path", ...}."""
        if label is not None and not label.strip():   # None: the owner switched the ad label off for this campaign
            raise ValueError("disclosure label is missing")
        tpl = normalize_template(template)
        src_path, out_path = Path(src).resolve(), Path(out).resolve()
        info = ffmpeg.probe(src_path)
        width, height = int(self.cfg.get("width", 1080)), int(self.cfg.get("height", 1920))
        start, end = float(clip["start_s"]), float(clip["end_s"])
        duration, style = end - start, self.force_crop or clip.get("crop") or "follow_speaker"
        niche = NICHE_OF_CROP.get(style)
        wanted = tpl["layout"] if (isinstance(template, dict) and "layout" in template) else niche_defaults(niche).get("layout", "auto")
        if self.force_crop is None and wanted in ("blur_pad", "gaming_stack"):
            style = wanted
        stack = None
        if style in ("gaming_stack", "facecam_over_game"):
            style = "gaming_stack"
            stack = gaming_layout.plan(src_path, clip, info["width"], info["height"], width, height)
            style = {"stack": "gaming_stack", "fullbleed": "follow_speaker"}.get(stack["layout"], "blur_pad")   # no face-cam: the whole picture on a blurred pad
        cell = niche_defaults(niche, clip.get("language"))
        if not (isinstance(template, dict) and "caption_position" in template) and cell.get("caption_position") in CAPTION_POSITIONS:
            tpl["caption_position"] = cell["caption_position"]
        size = cell.get("caption_font_pct")
        if not (isinstance(template, dict) and "font_size" in template) and isinstance(size, (int, float)) and FONT_SIZE_RANGE[0] <= size <= FONT_SIZE_RANGE[1]:
            tpl["font_size"] = float(size)
        tracked = faces.track(src_path, clip) if style in FOLLOW_STYLES else None   # YuNet path; None falls back to the old single-point focus
        path, focus = (tracked if tracked else (None, face_focus(src_path, clip) if style in FOLLOW_STYLES else None))
        language, has_audio = clip.get("language") or "en", info["has_audio"]
        env = polish.envelope(src_path, start, duration) if has_audio and self.force_crop is None and (tpl["tighten"] or tpl["zooms"]) else None
        plan = None
        if tpl["tighten"] and self.force_crop is None and words:
            plan = polish.plan_tighten(words, start, end, language, env, info.get("fps") or 30.0, min_s=float(clip.get("min_s") or 0.0))
            plan = plan if plan.cuts else None
        story = clip.get("story") or {}
        if story and self.force_crop is None:   # the Story Director's cold open and ending trim ride on the same trim and concat
            cold = story.get("cold_open")
            plan = polish.with_story(plan, duration, tuple(cold["clip"]) if cold else None, float(story.get("ending_trim_s") or 0.0))
        shown_words, c0, c1 = (plan.words(words, start, end), 0.0, plan.new_duration) if plan else (words, start, end)
        shown_emphasis = [plan.map(float(e) - start) for e in clip.get("emphasis") or []] if plan else clip.get("emphasis") or []
        social, rtl = clip.get("platform") in SOCIAL_PLATFORMS, language == "ar"
        from . import caption_render
        chosen = caption_render.resolve(tpl.get("caption_style"), niche, language) if tpl["karaoke"] else None
        if chosen and chosen != caption_render.DEFAULT_STYLE:
            events = caption_render.style_events(
                chosen, shown_words, c0, c1, social, tpl["accent"], width, height, int(height * tpl["font_size"] / 100), rtl, clip.get("platform"),
                tpl["caption_position"], cell.get("caption_y") if isinstance(cell.get("caption_y"), (int, float)) else None, shown_emphasis,
                clip.get("speaker_names"))
        elif tpl["karaoke"]:
            events = karaoke_events(shown_words, c0, c1, social, tpl["accent"], width, height, int(height * tpl["font_size"] / 100), rtl, tpl["caption_position"],
                                    cell.get("caption_y") if isinstance(cell.get("caption_y"), (int, float)) else None, max(1, math.ceil(float(cell.get("caption_words") or KARAOKE_WORDS))))
        else:
            events = caption_events(shown_words, c0, c1, social, shown_emphasis, tpl["accent"])
        ass = build_ass(width, height, c1 - c0, events, label, rtl, tpl, clip.get("hook_text"), chosen if chosen != caption_render.DEFAULT_STYLE else None,
                        clip.get("hook_show_s"))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        ass_path = out_path.with_suffix(".ass")
        ass_path.write_text(ass, encoding="utf-8-sig")
        moments: list[tuple[float, float]] = []
        if tpl["zooms"] and self.force_crop is None and style not in ZOOM_FLAT_STYLES and niche != "gaming":
            moments = polish.stress_moments(words, start, end, env, plan) if env is not None and words else []
            if not moments:    # no words or no audio: the loud moments the pipeline found, at a middle depth
                moments = [(max(t - 0.25, 0.0), 1.08) for t in zoom_times(clip.get("emphasis") or [], start, duration)]
            hook_zoom = (story.get("rehook") or {}) if story else {}
            if hook_zoom and all(abs(hook_zoom["at_s"] - 0.3 - t) >= 4.0 for t, _ in moments):
                moments = sorted(moments + [(round(hook_zoom["at_s"] - 0.3, 2), float(hook_zoom["depth"]))])
        graph = layout_graph(style, info["width"], info["height"], width, height, focus, path=path, stack=stack)
        graph += (";" + punch_graph(moments, width, height)) if moments else ""
        last = "zm" if moments else "lay"
        if plan:
            graph += ";" + tighten_graphs(plan, last, has_audio, info.get("fps") or 30.0)
            last = "cutv"
        graph += f";[{last}]ass={ass_path.name},format=yuv420p[v]"   # the file name only: no user text in the graph
        bed = clip.get("music") if has_audio else None      # {"path", "offset_s", "gain_db"}: the chosen, licence-checked bed
        if bed and not Path(bed["path"]).is_file():
            bed = None
        audio = music.audio_graph(bed, clip.get("platform"), clip.get("audio") == "natural", speech_in="cuta" if plan else "0:a:0", duration_s=c1 - c0) if has_audio else None
        if audio:
            graph += ";" + audio

        def args_for(encoder: str) -> list[str]:
            args = ["-ss", f"{start:.3f}", "-i", str(src_path)]
            if bed:   # looped from the chosen bar line, so a track shorter than the clip still fills it
                args += ["-stream_loop", "-1", "-ss", f"{float(bed['offset_s']):.3f}", "-i", str(Path(bed["path"]).resolve())]
            args += ["-t", f"{c1 - c0 + 0.05:.3f}", "-filter_complex", graph, "-map", "[v]"]
            if has_audio:
                args += ["-map", "[a]", "-c:a", "aac", "-ar", "44100", "-b:a", "160k"]
            return [*args, *ffmpeg.encoder_args(encoder, str(self.cfg.get("preset", "medium")), int(self.cfg.get("crf", 23))),
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path)]

        encoder = self._encode(args_for, out_path.parent, c1 - c0)
        finished = ffmpeg.probe(out_path)
        cover = self._cover(src_path, clip, tpl, style, info, (width, height), focus, out_path.with_name("cover.png"), stack)
        return {"path": str(out_path), "ass_path": str(ass_path), "encodes": 1, "encoder": encoder, "cover_path": cover,
                "duration_s": finished["duration_s"], "captions": len(events), "zooms": len(moments),
                "tightened": {"removed_s": round(plan.removed, 2), "cuts": plan.cuts} if plan else None,
                "reframe": "face" if focus is not None else "centre_crop", "loudness_normalised": bool(info["has_audio"]),
                "music": bed["record"] if bed and bed.get("record") else None, "loudness_target": music.target_loudness(clip.get("platform"))[0],
                "overlay": {"text": label, "start_s": 0.0, "end_s": finished["duration_s"]}, "width": width, "height": height}

    def _cover(self, src: Path, clip: dict, tpl: dict, style: str, info: dict, size: tuple[int, int], focus, out: Path, stack: dict | None = None) -> str | None:
        """The cover PNG: the sharpest of the best candidate frames, reframed like the clip, with the short hook line. A cover is a
        nicety, so any failure leaves the clip without one and never blocks it."""
        width, height = size
        try:
            at = sharpest_time(src, cover_times(clip))
            ass_path = out.with_suffix(".ass")
            ass_path.write_text(build_cover_ass(width, height, clip.get("hook_text"), tpl), encoding="utf-8-sig")
            graph = layout_graph(style, info["width"], info["height"], width, height, focus, stack=stack) + f";[lay]ass={ass_path.name}[v]"
            ffmpeg.run(["-ss", f"{at:.3f}", "-i", str(src), "-filter_complex", graph, "-map", "[v]", "-frames:v", "1", str(out)], cwd=out.parent)
        except Exception:
            return None
        return str(out)


@register
class CentreCropRender(FFmpegRender):
    """Declared fallback for A5: a plain centre crop, no emphasis zooms."""
    stage, provider = "render", "centre_crop"
    force_crop = "centre"


@register
class FFmpegCaptions(Adapter):
    """Still registered because the adapters table lists the stage and queued `captions` jobs name it. Captions are burned in by
    the render adapter in the clip's one encode, so this adapter does no work of its own."""
    stage, provider = "captions", "ffmpeg"


@register
class PlainTextCaptions(FFmpegCaptions):
    """Declared fallback of the retired captions stage."""
    stage, provider = "captions", "plain_text"
