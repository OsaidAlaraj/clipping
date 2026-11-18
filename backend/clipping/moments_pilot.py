"""Interim moment picker for the pilot: the active LLM reads the transcript (with timestamps) and proposes the moments
worth clipping. The full engine of phase 4 replaces it behind the same function.

Safety rules: the transcript is data, never instructions (the system prompt is fixed per language and the transcript
travels only as JSON in the user message); the answer is validated strictly (numbers for times, short plain text for the
reason and hook) and every time must fall inside the video; a bad answer for one stretch is skipped, and when nothing
valid is left the caller gets an AppError so the pipeline falls back to manual selection. It never raises anything else
for bad model output."""
from __future__ import annotations

import math
import re
from decimal import Decimal

from . import transcripts
from .adapters.base import AdapterPaused
from .agents import hooks as agent_b
from .alerts import registry
from .core import AppError, KillSwitchOn
from .pipeline import sentence_bounds, snap   # pipeline imports this module only inside its moments stage

# Clip length range in seconds per niche (the owner's workflows: podcast 30-60, streams 20-45, promo 15-30).
LENGTH_RANGES = {"podcast": (30, 60), "stream": (20, 45), "streams": (20, 45), "gaming": (20, 45), "promo": (15, 30)}
DEFAULT_RANGE = (20, 60)
CHUNK_S = 600              # about ten minutes of speech per LLM call
OVERLAP = 0.2              # the next stretch starts 20 % of a chunk before the previous one ended
PAUSE_WINDOW_S = 60        # a stretch ends at the longest pause within a minute of its target end
MIN_MOMENTS, MAX_MOMENTS = 3, 8
PER_CHUNK = 3              # moments asked from each stretch of a long transcript
MAX_PARSED = 20            # items read from one answer; the rest is ignored
MAX_REASON, MAX_HOOK = 200, 140
MAX_OVERLAP_SHARE = 0.3    # two picks sharing more than this of the shorter one are duplicates
SENTENCE_END = ".?!؟"
BREATH = 0.15              # the same breathing room pipeline.snap leaves after a sentence

CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def length_range(profile_hint: str | None) -> tuple[int, int]:
    return LENGTH_RANGES.get((profile_hint or "").strip().lower(), DEFAULT_RANGE)


# ------------------------------------------------------------------ the transcript as lines

def _sentences(words: list[dict]) -> list[dict]:
    """Sentence-like lines {start, end, text, first, last}: a sentence mark, a pause over 0.6 s, or 40 words ends one."""
    out: list[dict] = []
    first = 0
    for i, word in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else None
        if nxt is None or word["text"][-1:] in SENTENCE_END or nxt["start"] - word["end"] > 0.6 or i - first >= 39:
            out.append({"start": words[first]["start"], "end": word["end"], "first": first, "last": i,
                        "text": " ".join(w["text"] for w in words[first:i + 1])})
            first = i + 1
    return out


def _chunks(lines: list[dict]) -> list[list[dict]]:
    """Stretches of about CHUNK_S, each cut at the longest pause near its target end, overlapping by OVERLAP."""
    if lines[-1]["end"] - lines[0]["start"] <= CHUNK_S * 1.2:
        return [lines]
    chunks: list[list[dict]] = []
    i, n = 0, len(lines)
    while i < n:
        target = lines[i]["start"] + CHUNK_S
        near = [k for k in range(i, n - 1) if abs(lines[k]["end"] - target) <= PAUSE_WINDOW_S]
        if near:
            k = max(near, key=lambda x: lines[x + 1]["start"] - lines[x]["end"])
        else:
            k = max([x for x in range(i, n) if lines[x]["end"] <= target] or [i])
        if lines[-1]["end"] - lines[k]["end"] < CHUNK_S * 0.3:   # do not leave a thin tail for its own call
            k = n - 1
        chunks.append(lines[i:k + 1])
        if k == n - 1:
            break
        back = lines[k]["end"] - CHUNK_S * OVERLAP
        i = max(next((x for x in range(i + 1, k + 1) if lines[x]["start"] >= back), k + 1), i + 1)
    return chunks


# ------------------------------------------------------------------ the answer, validated

def _text(value, limit: int) -> str:
    return " ".join(CONTROL.sub(" ", value).split())[:limit] if isinstance(value, str) else ""


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _parse(raw, window: tuple[float, float], duration: float) -> list[dict]:
    """Well-formed moments from one answer. Anything else raises ValueError (the whole answer is unusable) or is dropped."""
    items = raw.get("moments") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise ValueError("the answer has no moments list")
    found = []
    for item in items[:MAX_PARSED]:
        if not isinstance(item, dict):
            continue
        start, end, score = _number(item.get("start_s")), _number(item.get("end_s")), _number(item.get("score"))
        reason = _text(item.get("reason"), MAX_REASON)
        if None in (start, end, score) or not reason or not 0 <= score <= 1:
            continue
        # Inside the video, inside the stretch that was sent (a time outside it was invented), and a real span.
        if not (0 <= start < end <= duration + 0.5 and window[0] - 2 <= start and end <= window[1] + 2):
            continue
        found.append({"start": start, "end": end, "score": score, "reason": reason, "hook": _text(item.get("hook"), MAX_HOOK)})
    return found


# ------------------------------------------------------------------ fitting a pick to sentences and the length range

def _pause_end(words: list[dict], start: float, lo: int, hi: int, duration: float) -> float | None:
    """When no sentence end fits the range, end at the longest pause inside it."""
    best, best_gap = None, -1.0
    for i, word in enumerate(words[:-1]):
        if start + lo <= word["end"] + BREATH <= min(start + hi, duration):
            gap = words[i + 1]["start"] - word["end"]
            if gap > best_gap:
                best, best_gap = word["end"] + BREATH, gap
    return best


def _fit(pick: dict, words: list[dict], sentence_ends: list[float], lo: int, hi: int, duration: float) -> tuple[float, float] | None:
    start, end = snap(pick["start"], pick["end"], words)
    end = min(end, duration)
    if not lo <= end - start <= hi:
        fits = [x + BREATH for x in sentence_ends if start + lo <= x + BREATH <= min(start + hi, duration)]
        # Too long: the last sentence end that fits. Too short: the first one.
        end = (max(fits) if end - start > hi else min(fits)) if fits else _pause_end(words, start, lo, hi, duration)
        if end is None:
            return None
    return (round(start, 3), round(end, 3)) if lo <= end - start <= hi else None


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0])) / min(a[1] - a[0], b[1] - b[0])


def _without_duplicates(picks: list[dict]) -> list[dict]:
    kept: list[dict] = []
    for pick in sorted(picks, key=lambda p: (-p["score"], p["start_s"])):
        if all(_overlap((pick["start_s"], pick["end_s"]), (k["start_s"], k["end_s"])) <= MAX_OVERLAP_SHARE for k in kept):
            kept.append(pick)
    return kept


def _hook_line(hook: str, words: list[dict], start: float, end: float) -> str:
    """A hook may quote the clip but never invent a quote (the same rule as Agent B)."""
    said = " ".join(" ".join(w["text"] for w in words if start - 0.1 <= w["start"] and w["end"] <= end + 0.1).lower().split())
    return "" if any(" ".join(q.lower().split()) not in said for q in agent_b.QUOTED.findall(hook)) else hook


# ------------------------------------------------------------------ the public interface

def propose_with_cost(conn, source: dict, version: dict, profile_hint: str | None = None,
                      length: tuple[float, float] | None = None) -> tuple[list[dict], Decimal]:
    """Candidates for one source, best first, and what the LLM calls cost. `version` is the campaign version the
    source belongs to (kept for the phase-4 signature: a practice campaign has no brief to read). `length` is the clip
    length range to use (the moment engine passes its profile's); without it the niche hint decides.
    Raises AppError when no valid moment can be had."""
    lo, hi = length or length_range(profile_hint)
    words = transcripts.load(conn, source["id"])["words"]
    duration = float(source["duration_s"] or 0) or words[-1]["end"]
    language = source["language"] if source["language"] in ("en", "ar") else "en"
    wanted = min(MAX_MOMENTS, max(MIN_MOMENTS, 2 + int(duration // 420)))
    lines = _sentences(words)
    chunks = _chunks(lines)
    ask = max(PER_CHUNK, min(MAX_MOMENTS, wanted + 1)) if len(chunks) == 1 else PER_CHUNK
    sentence_ends = sentence_bounds(words)[1]
    picks: list[dict] = []
    cost, last_error = Decimal("0"), "the model returned nothing usable"
    for chunk in chunks:
        window = (chunk[0]["start"], chunk[-1]["end"])
        request = {"task": "moments", "payload": {
            "language": language, "niche": (profile_hint or "").strip().lower()[:40], "min_s": lo, "max_s": hi, "max_moments": ask,
            "video_seconds": round(duration, 1), "window": [round(window[0], 1), round(window[1], 1)],
            "segments": [[round(s["start"], 1), round(s["end"], 1), s["text"]] for s in chunk]}}
        try:
            raw, spent = registry.call(conn, "llm", "complete_json", request, source["owner_operator_id"])
            cost += spent
            parsed = _parse(raw, window, duration)
        except (KillSwitchOn, AdapterPaused):
            raise
        except (AppError, ValueError, TypeError, KeyError) as exc:   # one bad stretch must not lose the others
            last_error = str(exc)[:160]
            continue
        for item in parsed:
            fitted = _fit(item, words, sentence_ends, lo, hi, duration)
            if fitted:
                picks.append({"start_s": fitted[0], "end_s": fitted[1], "variant": "tight", "score": round(item["score"], 3),
                              "rank": 0, "reasons": [item["reason"]], "features": {"llm_score": round(item["score"], 3),
                              "duration_s": round(fitted[1] - fitted[0], 3)}, "source": "pilot_llm",
                              "hook": _hook_line(item["hook"], words, *fitted)})
    kept = _without_duplicates(picks)[:wanted]
    if not kept:
        raise AppError(f"The moment picker found no usable moments ({last_error}).", "moments_pilot_failed")
    for rank, pick in enumerate(kept, start=1):
        pick["rank"] = rank
    return kept, cost


def propose(conn, source: dict, version: dict, profile_hint: str | None = None) -> list[dict]:
    """The pinned contract: Candidate dicts {start_s, end_s, variant, score, rank, reasons, features, source}
    (plus an additive `hook` line), best first."""
    return propose_with_cost(conn, source, version, profile_hint)[0]


# The moment engine (moments.py, moment_engines.py) builds on the same text helpers under public names.
sentences, chunks, fit, overlap_share, hook_line = _sentences, _chunks, _fit, _overlap, _hook_line
