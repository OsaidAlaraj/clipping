"""The Story Director: for one chosen moment, an EDIT PLAN that applies what keeps people watching (docs/retention-playbook.md).

A plan is plain data (stored on the clip, shown in plain words on the review screen) that the one-encode render executes with the
trim and concat it already uses for tightening: a COLD OPEN (the moment's own strongest later line plays first, then the clip
rewinds to its start), an ENDING TRIM (no dead sign-off), and a RE-HOOK point (one punch-in around the middle). It only reorders
and trims the creator's own footage: nothing is invented, overlays (logo, disclosure) are drawn later on the final timeline, and
the plan never makes a clip longer than the campaign allows. Restraint is part of the design: a clip that already opens strongly
gets no cold open."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from . import moment_rank as rank

LIB = Path(__file__).resolve().parent / "assets" / "retention-techniques.json"
SENTENCE_END = ".?!؟…"
WORD = re.compile(r"[\w']+", re.U)


@lru_cache(maxsize=1)
def library() -> dict:
    return json.loads(LIB.read_text(encoding="utf-8"))


def intrigue(text: str, language: str = "en") -> float:
    """0..1: how much a line makes a stranger lean in: a number, a claim, a question, a contrast, a sensible length, no pointing back."""
    tokens = rank._tokens(text)
    if not tokens:
        return 0.0
    n = len(tokens)
    score = 0.0
    score += 0.22 if any(c.isdigit() for c in text) or any(t in rank.NUMBER_WORDS for t in tokens) else 0.0
    score += 0.22 * min(sum(t in rank.CLAIMS for t in tokens), 2) / 2
    score += 0.16 if "?" in text or "؟" in text else 0.0
    score += 0.10 if text.rstrip()[-1:] == "!" else 0.0
    score += 0.14 if 5 <= n <= 16 else 0.04 if 3 <= n < 5 else 0.0
    score += 0.10 if sum(t in rank.VAGUE for t in tokens) / n < 0.2 else 0.0
    score -= 0.25 if tokens[0] in rank.WEAK_OPENERS else 0.0
    score += 0.08 if sum(t in rank.HYPE_WORDS for t in tokens) else 0.0
    return round(max(0.0, min(score + 0.12, 1.0)), 3)


def _lines(words: list[dict], start: float, end: float) -> list[dict]:
    from .moments_pilot import sentences
    inside = [w for w in words if w["end"] > start and w["start"] < end]
    return [{**s, "start": max(s["start"] - start, 0.0), "end": min(s["end"] - start, end - start)} for s in sentences(inside)]


def plan(words: list[dict], start: float, end: float, language: str, niche: str, max_s: float, lib: dict | None = None) -> dict:
    """The edit plan of the clip [start, end] (source seconds). `max_s` is the longest the campaign allows. Always returns a dict;
    `techniques` lists what was applied (empty = leave the clip as cut)."""
    lib = lib or library()
    dur = end - start
    lines = _lines(words, start, end)
    out = {"version": 1, "techniques": [], "cold_open": None, "rehook": None, "ending_trim_s": 0.0, "why": [], "why_ar": [], "opening_intrigue": None}
    if len(lines) < 3 or dur <= 0:
        return out
    ar = language == "ar"
    opening = max((intrigue(l["text"], language) for l in lines if l["start"] < 4.0), default=0.0)
    out["opening_intrigue"] = opening

    # ---- cold open
    c = lib["cold_open"]
    if c["enabled"] and niche not in c["skip_niches"] and dur >= c["min_clip_s"] and opening < c["opening_strong"]:
        best = None
        for l in lines:
            length = l["end"] - l["start"]
            late = c["earliest_share"] * dur <= l["start"] and l["end"] <= c["latest_share"] * dur
            if not late or not c["min_teaser_s"] <= length <= c["max_teaser_s"] or l["text"].rstrip()[-1:] not in SENTENCE_END:
                continue
            value = intrigue(l["text"], language)
            if value >= c["teaser_min_intrigue"] and value - opening >= c["min_gain"] and (best is None or value > best[0]):
                best = (value, l)
        if best and dur + (best[1]["end"] - best[1]["start"]) <= max_s:
            value, l = best
            out["cold_open"] = {"clip": [round(l["start"], 3), round(l["end"], 3)], "text": l["text"], "intrigue": value}
            out["techniques"].append("cold_open")
            out["why"].append(f"Opens with the line \"{l['text']}\" from {int(l['start'])} s into the clip, then rewinds to the start: the real start is slower "
                              f"(opening strength {opening:.2f}, this line {value:.2f}).")
            out["why_ar"].append(f"يبدأ بجملة \"{l['text']}\" من الثانية {int(l['start'])} ثم يرجع للبداية، لأن البداية الأصلية أهدأ.")

    # ---- ending trim
    e = lib["ending_trim"]
    last = lines[-1]
    tokens = [t for t in WORD.findall(last["text"].lower())]
    signoffs = e["signoffs_ar"] if ar else e["signoffs_en"]
    soft = len(tokens) <= e["max_words"] and (any(s in last["text"].lower() for s in signoffs) or len(tokens) <= 2)
    gap = dur - last["start"]
    if e["enabled"] and soft and 0.3 <= gap <= e["max_trim_s"] and len(lines) >= 4 and dur - gap >= c["min_clip_s"] * 0.6:
        out["ending_trim_s"] = round(gap, 2)
        out["techniques"].append("ending_trim")
        out["why"].append(f"Ends {gap:.1f} s earlier, on the last real point, instead of a sign-off (\"{last['text']}\").")
        out["why_ar"].append(f"ينتهي قبل {gap:.1f} ثانية عند آخر فكرة حقيقية بدل جملة الختام.")

    # ---- re-hook
    r = lib["rehook"]
    if r["enabled"] and dur >= r["min_clip_s"]:
        mid = [l for l in lines if r["window"][0] * dur <= l["start"] <= r["window"][1] * dur]
        if mid:
            pick = max(mid, key=lambda l: intrigue(l["text"], language))
            out["rehook"] = {"at_s": round(pick["start"], 2), "depth": r["zoom_depth"]}
            out["techniques"].append("rehook")
            out["why"].append(f"A small punch-in at {int(pick['start'])} s re-hooks attention in the middle.")
            out["why_ar"].append(f"تقريب خفيف عند الثانية {int(pick['start'])} لإعادة شدّ الانتباه في المنتصف.")
    return out
