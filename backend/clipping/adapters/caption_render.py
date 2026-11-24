"""Named caption styles (owner notes 3, 4 and 10): a small set of expert looks the owner picks like directing an editor.

Each style is data (`STYLES`): how many words a line holds, what is highlighted, how a line enters, how heavy the type is.
Every style draws into the same one-encode ASS script as before; nothing here adds an encode. Rules every style follows:
- a line is one subtitle event per spoken word with the whole line inside it, so the line never reflows and an Arabic line keeps
  its right-to-left order and letter joining (colour and scale changes sit only between words);
- the only motion is one pop-in or fade when a line appears (about 0.1 s) and a mild scale on the word being said; the rest of
  the line holds still, so nothing jitters;
- the text block stays inside the safe area of the platform's own interface (SAFE) at any size;
- Arabic is never upper-cased and never gets letter spacing.
Licence note: the animation ideas (pop-in with overshoot, active-word highlight) follow common ASS karaoke practice as in
pulsence/caption-animator (MIT); no code is copied from it."""
from __future__ import annotations

import re

from . import media

# Share of the frame the platform's own buttons and text cover: (top, bottom, left, right). Approximations of the published
# safe-zone guides, deliberately on the cautious side.
SAFE = {"tiktok": (0.07, 0.22, 0.06, 0.14), "instagram_reels": (0.13, 0.20, 0.06, 0.12), "youtube_shorts": (0.08, 0.20, 0.06, 0.14)}
SAFE_DEFAULT = (0.08, 0.22, 0.06, 0.14)

STYLES: dict[str, dict] = {
    "karaoke_clean": {"label": "Clean karaoke", "about": "Three words at a time, the spoken word turns to the accent colour. Calm and readable.",
                      "words": 3, "highlight": "word", "entrance": "fade", "bold": True, "size": 1.0, "outline": 1.0, "case": "asis", "lines": 1},
    "bold_pop": {"label": "Bold pop", "about": "Big, two words at a time, each line pops in. For fast, loud videos.",
                 "words": 2, "highlight": "word", "entrance": "pop", "bold": True, "size": 1.25, "outline": 1.35, "case": "upper", "lines": 1},
    "emphasis": {"label": "Key words", "about": "Whole phrases appear softly; only the number or the loudest word is in the accent colour.",
                 "words": 4, "highlight": "emphasis", "entrance": "fade", "bold": True, "size": 1.0, "outline": 1.0, "case": "asis", "lines": 1},
    "stacked": {"label": "Stacked", "about": "Two short lines, two words each, centred. Good for talking heads.",
                "words": 4, "highlight": "word", "entrance": "fade", "bold": True, "size": 1.1, "outline": 1.1, "case": "asis", "lines": 2},
    "minimal": {"label": "Minimal", "about": "Small, plain and steady, no colour changes. Lets the picture lead.",
                "words": 5, "highlight": "none", "entrance": "fade", "bold": False, "size": 0.85, "outline": 0.8, "case": "asis", "lines": 1},
    "speaker_label": {"label": "Speaker name", "about": "Clean karaoke plus the speaker's name above the line when someone new starts talking.",
                      "words": 3, "highlight": "word", "entrance": "fade", "bold": True, "size": 1.0, "outline": 1.0, "case": "asis", "lines": 1,
                      "speaker": True},
}
DEFAULT_STYLE = "karaoke_clean"
# What "auto" resolves to: the niche decides (podcasts and interviews name the speaker, gaming is loud, business stresses key words).
AUTO_BY_NICHE = {"gaming": "bold_pop", "podcast": "speaker_label", "business": "emphasis", "lifestyle": "stacked", "arabic": "karaoke_clean"}
SPEAKER_SHOWN_S = 2.2
_PLACEHOLDER = re.compile(r"^\s*(speaker|spk|host|guest|متحدث|المتحدث|ضيف|الضيف)?\s*[#_-]?\s*\d*\s*$", re.IGNORECASE)


def real_name(value) -> str | None:
    """The name to show, or None when it is empty or only a placeholder ("Speaker 1", "المتحدث 2", "0"). A made-up label on a public
    clip is worse than no label, so a name nobody gave is never drawn."""
    text = str(value or "").strip()
    return None if _PLACEHOLDER.match(text) else text


def resolve(style: str | None, niche: str | None = None, language: str | None = None) -> str:
    """The style name to draw: the owner's choice, or for "auto" the niche's own (Arabic keeps the clean style: no capitals, no pop
    that could pull letters apart)."""
    if style in STYLES:
        return style
    pick = AUTO_BY_NICHE.get(niche or "", DEFAULT_STYLE)
    return DEFAULT_STYLE if language == "ar" and STYLES[pick]["case"] == "upper" else pick


def safe_box(platform: str | None, width: int, height: int) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) pixels of the frame that is clear of the platform's interface."""
    top, bottom, left, right = SAFE.get(platform or "", SAFE_DEFAULT)
    return int(width * left), int(height * top), int(width * (1 - right)), int(height * (1 - bottom))


def style_params(name: str) -> dict:
    return STYLES.get(name, STYLES[DEFAULT_STYLE])


def ass_style_fields(name: str, cap: int) -> dict:
    """Bold flag, outline width and shadow of the Caption style for this look."""
    p = style_params(name)
    return {"bold": 1 if p["bold"] else 0, "outline": max(int(cap * p["size"] / 12 * p["outline"]), 2), "shadow": 0 if p["bold"] else max(cap // 24, 1)}


def _case(text: str, mode: str, rtl: bool) -> str:
    return text.upper() if mode == "upper" and not rtl else text


def style_events(name: str, words: list[dict], clip_start: float, clip_end: float, social: bool, accent: str, width: int, height: int,
                 cap: int, rtl: bool, platform: str | None = None, position: str = "lower", y_share: float | None = None,
                 emphasis: list[float] | None = None, names: dict | None = None) -> list[dict]:
    """The caption events of one named style, plus its speaker chips. `emphasis` are loud moments (seconds in the clip's own
    time); `names` maps a speaker id to the name shown."""
    p = style_params(name)
    chunks = media.caption_chunks(words, clip_start, clip_end, p["words"], media.KARAOKE_MAX_S if p["highlight"] == "word" else 2.2)
    white, hot = media._ass_colour("#FFFFFF"), media._ass_colour(accent)
    size_cap = int(cap * p["size"])
    left, top, right, bottom = safe_box(platform, width, height)
    avail_w = min(right - left, width * media.CAPTION_MAX_W)
    base_y = height * (min(y_share or media.CAPTION_Y["ar" if rtl else "en"], media.CAPTION_Y_MAX) if position == "lower" else 0.5)
    events: list[dict] = []
    emph = emphasis or []
    for ci, chunk in enumerate(chunks):
        shown = [(w, media._esc(w["text"].translate(media._LIGHT) if social else w["text"])) for w in chunk["words"]]
        shown = [(w, _case(t, p["case"], rtl)) for w, t in shown if t]
        if not shown:
            continue
        per_line = (len(shown) + 1) // 2 if p["lines"] == 2 and len(shown) > 2 else len(shown)
        text_of = [t for _, t in shown]
        size = media.fit_size(" ".join(text_of[:per_line]), avail_w, size_cap, max_lines=1 if p["lines"] == 2 else 2)
        rows = 1 if per_line == len(shown) else 2
        y = int(min(max(base_y, top + size * rows * 0.65), bottom - size * rows * 0.65))   # the whole block stays inside the safe area
        head = f"{{\\an5\\pos({width // 2},{y})\\fs{size}"
        stop = chunk["end"] + media.KARAOKE_HOLD_S
        if ci + 1 < len(chunks):
            stop = min(stop, chunks[ci + 1]["start"])
        enter = "\\fscx82\\fscy82\\t(0,110,\\fscx106\\fscy106)\\t(110,190,\\fscx100\\fscy100)" if p["entrance"] == "pop" else "\\fad(90,0)"
        loud = None
        if p["highlight"] == "emphasis":
            mids = [(w["start"] + w["end"]) / 2 for w, _ in shown]
            near = [(min((abs(m - e) for e in emph), default=99.0), i) for i, m in enumerate(mids)]
            loud = min(near)[1] if near and min(near)[0] <= media._EMPH_WINDOW_S else None
            if loud is None:
                loud = next((i for i, (_, t) in enumerate(shown) if any(c.isdigit() for c in t)), None)

        def line(active: int | None) -> str:
            parts = []
            for j, (_, t) in enumerate(shown):
                if p["highlight"] == "word" and j == active:
                    t = f"{{\\1c{hot}&}}{t}{{\\1c{white}&}}"
                elif p["highlight"] == "emphasis" and j == loud:
                    t = f"{{\\1c{hot}&}}{t}{{\\1c{white}&}}"
                parts.append(t)
                if p["lines"] == 2 and j + 1 == per_line and per_line < len(shown):
                    parts.append("\\N")
            return " ".join(parts).replace(" \\N ", "\\N")

        if p["highlight"] == "word":
            for k, (word, _) in enumerate(shown):
                start = chunk["start"] if k == 0 else max(word["start"] - clip_start, chunk["start"])
                end = stop if k == len(shown) - 1 else max(shown[k + 1][0]["start"] - clip_start, start + 0.04)
                if end > start:
                    events.append({"start": start, "end": end, "text": head + (enter if k == 0 else "") + "}" + line(k)})
        else:   # one event for the whole line: it appears once and holds still
            events.append({"start": chunk["start"], "end": stop, "text": head + enter + "}" + line(None)})
    if p.get("speaker"):
        events += speaker_events(chunks, clip_start, width, height, size_cap, base_y, names or {}, rtl, left, right)
    return events


def speaker_events(chunks: list[dict], clip_start: float, width: int, height: int, cap: int, base_y: float, names: dict, rtl: bool,
                   left: int, right: int) -> list[dict]:
    """A name chip above the captions whenever the speaker changes (and at the start when the first speaker is known). Only when
    the transcript has speakers; nothing is shown for a one-speaker clip."""
    ids = {w.get("speaker") for c in chunks for w in c["words"] if w.get("speaker") is not None}
    if len(ids) < 2:
        return []
    events, last = [], object()
    y = int(max(base_y - cap * 1.9, height * 0.1))
    for chunk in chunks:
        speaker = chunk["words"][0].get("speaker")
        if speaker is None or speaker == last:
            continue
        last = speaker
        name = real_name(names.get(speaker))
        if name is None:
            continue   # unknown speaker: show nothing rather than "Speaker 1"
        label = media._esc(name)
        start = chunk["start"]
        events.append({"start": start, "end": start + SPEAKER_SHOWN_S, "layer": 2, "style": "Speaker",
                       "text": f"{{\\an5\\pos({width // 2},{y})\\fad(120,200)}}{label}"})
    return events
