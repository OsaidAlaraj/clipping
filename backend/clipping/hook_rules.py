"""The hook rules: what a short-form editor would accept as an on-screen hook, enforced in code (owner notes 1 and 10).

A hook is 3 to 9 words, one idea, spoken style, no dashes, semicolons, colons or quotation marks, at most two lines at the
title size inside the safe area, and on screen long enough to read (at least words / 3 seconds, never under 1.2 s).
`clean` rewrites what can be fixed safely (punctuation only, never the words); `problems` lists what is still wrong;
`check` does both. A hook that still fails is rejected and the generator is asked again with the problems named."""
from __future__ import annotations

import re

MIN_WORDS, MAX_WORDS = 3, 9
MIN_SHOW_S, WORDS_PER_S, MAX_SHOW_S = 1.2, 3.0, 4.0
MAX_LINES = 2
TITLE_FLOOR = 0.55                      # the renderer never shrinks the title below this share of its base size
TITLE_MAX_W = 0.8                       # share of the frame width the title may use (the renderer's safe width)
DASHES = "-–—‒―−"
QUOTE_MARKS = "\"“”„‟«»‹›`"
APOSTROPHES = "'’‘"
_SPACE = re.compile(r"\s+")
_DASH_RE = re.compile(f"[{re.escape(DASHES)}]+")
_QUOTE_RE = re.compile(f"[{re.escape(QUOTE_MARKS)}]")
_EDGE_APOS = re.compile(f"(?<!\\w)[{re.escape(APOSTROPHES)}]+|[{re.escape(APOSTROPHES)}]+(?!\\w)")


def words(text: str) -> list[str]:
    return [w for w in _SPACE.split(str(text).strip()) if w]


def clean(text: str) -> str:
    """Punctuation only: dashes and colons become a space, semicolons a comma, quotation marks go, a repeated !!! or ???
    becomes one, spaces collapse. The words are never touched."""
    text = _QUOTE_RE.sub("", str(text or ""))
    text = _EDGE_APOS.sub("", text)
    text = _DASH_RE.sub(" ", text)
    text = text.replace(":", " ").replace("؛", ",").replace(";", ",").replace("…", ".")
    text = re.sub(r"([!?؟.،,])\1+", r"\1", text)
    return _SPACE.sub(" ", text).strip(" ,")


def show_seconds(text: str) -> float:
    """How long the hook must stay on screen to be read: words / 3 s, at least 1.2 s, at most 4 s."""
    return round(min(max(MIN_SHOW_S, len(words(text)) / WORDS_PER_S), MAX_SHOW_S), 2)


def lines_needed(text: str, frame_w: int, size: int) -> int:
    """Lines the hook takes at this font size inside the title's safe width (a character is about 0.55 em wide)."""
    per_line = max(int(frame_w * TITLE_MAX_W / (0.55 * size)), 1)
    count, current = 1, 0
    for w in words(text):
        need = len(w) + (1 if current else 0)
        if current and current + need > per_line:
            count, current = count + 1, len(w)
        else:
            current += need
    return count


def problems(text: str, available_s: float | None = None, frame_w: int = 1080, title_size: int = 69) -> list[str]:
    """Why a hook is not acceptable, in plain words; [] when it is fine."""
    out = []
    n = len(words(text))
    if n < MIN_WORDS:
        out.append(f"too short ({n} words, need at least {MIN_WORDS})")
    if n > MAX_WORDS:
        out.append(f"too long ({n} words, at most {MAX_WORDS})")
    if _DASH_RE.search(text) or ";" in text or ":" in text or "؛" in text:
        out.append("contains a dash, colon or semicolon")
    if _QUOTE_RE.search(text):
        out.append("contains quotation marks")
    if lines_needed(text, frame_w, int(title_size * TITLE_FLOOR)) > MAX_LINES:   # the renderer shrinks the title down to this
        out.append(f"needs more than {MAX_LINES} lines on screen")
    if available_s is not None and available_s + 1e-9 < show_seconds(text):
        out.append(f"needs {show_seconds(text)} s on screen to be read but only {available_s:.1f} s are free")
    return out


def check(text: str, available_s: float | None = None, frame_w: int = 1080, title_size: int = 69) -> tuple[str, list[str]]:
    """(cleaned text, remaining problems)."""
    fixed = clean(text)
    return fixed, problems(fixed, available_s, frame_w, title_size)


def feedback(rejected: list[tuple[str, list[str]]]) -> str:
    """One plain line per rejected hook, for the next ask."""
    return " | ".join(f"{t}: {', '.join(p)}" for t, p in rejected[:4])
