"""The scout: the LLM reads the episode as numbered sentences and names the moments, by sentence number, never by time.

The heuristic scan (`moment_engines.scan`) can only find windows its word features like; the scout lets a model with taste read
the whole story first. Code does all the arithmetic and the edge rules: times come from the sentences' own timings, every pick
is grown or trimmed to the length range on sentence ends, and the answer is validated like every other moment answer
(ids that were not sent are ignored, text is plain and short, nothing in the transcript can give orders)."""
from __future__ import annotations

import math
import re
from decimal import Decimal

from . import moment_rank as rank
from . import moments_pilot
from .adapters.base import AdapterPaused
from .alerts import registry
from .core import AppError, KillSwitchOn

PER_MINUTE = 0.8          # moments asked per minute of speech: a wide net, the ranking layers choose
MAX_PARSED = 40
MAX_REASON = 200
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def listing(ep: rank.Episode, lines: list[dict], ids: dict[int, int]) -> list[list]:
    """[id, start second, seconds, speaker, text, pause after in tenths of a second] for each sentence of a stretch.
    `ids` maps a line (by identity) to its number in the episode."""
    out = []
    for line in lines:
        k = ids[id(line)]
        gap = ep.lines[k + 1]["start"] - line["end"] if k + 1 < len(ep.lines) else 0.0
        speaker = ep.words[line["first"]].get("speaker")
        out.append([k, int(line["start"]), round(line["end"] - line["start"], 1), speaker, line["text"], max(round(gap * 10), 0)])
    return out


def _text(value, limit: int) -> str:
    return " ".join(CONTROL.sub(" ", value).split())[:limit] if isinstance(value, str) else ""


def _id(value) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value == int(value) else None


def parse(raw, allowed: set[int]) -> list[dict]:
    """Well-formed picks {first, last, score, reason, hook_unit}; a pick naming a sentence that was not sent is dropped."""
    items = raw.get("moments") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise ValueError("the answer has no moments list")
    out = []
    for item in items[:MAX_PARSED]:
        if not isinstance(item, dict):
            continue
        first, last, score = _id(item.get("first")), _id(item.get("last")), item.get("score")
        reason = _text(item.get("reason"), MAX_REASON)
        if first is None or last is None or first > last or first not in allowed or last not in allowed or not reason:
            continue
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
            continue
        out.append({"first": first, "last": last, "score": float(score), "reason": reason})
    return out


def propose(src, profile) -> tuple[list[dict], Decimal]:
    """Moments from the model, fitted to the length range. Raises AppError only when every stretch failed."""
    ep = src.ep
    lo, hi = src.length
    if not ep.lines:
        return [], Decimal("0")
    ids = {id(line): k for k, line in enumerate(ep.lines)}
    picks, cost, last_error, answered = [], Decimal("0"), "the model returned nothing usable", False
    sentence_ends = [line["end"] for line in ep.lines]
    for chunk in moments_pilot.chunks(ep.lines):
        minutes = (chunk[-1]["end"] - chunk[0]["start"]) / 60
        ask = max(3, min(MAX_PARSED // 2, math.ceil(minutes * PER_MINUTE)))
        request = {"task": "moment_scout", "payload": {
            "language": ep.language, "profile": {"name": profile.name, "moments": list(profile.moments), "info_ok": profile.info_ok},
            "avoid": src.brief["avoid"][:20], "include": src.brief["include"][:20], "min_s": round(lo), "max_s": round(hi),
            "max_moments": ask, "units": listing(ep, chunk, ids)}}
        try:
            raw, spent = registry.call(src.conn, "llm", "complete_json", request, src.owner_id)
            cost += spent
            parsed = parse(raw, {ids[id(line)] for line in chunk})
        except (KillSwitchOn, AdapterPaused):
            raise
        except (AppError, ValueError, TypeError, KeyError) as exc:
            last_error = str(exc)[:160]
            continue
        answered = True
        for item in parsed:
            first, last = ep.lines[item["first"]], ep.lines[item["last"]]
            span = rank._span(ep, first, last, hi)
            fitted = moments_pilot.fit({"start": span["start_s"], "end": span["end_s"]}, ep.words, sentence_ends, lo, hi, ep.duration)
            if fitted:
                picks.append({"start_s": fitted[0], "end_s": fitted[1], "score": round(item["score"], 4), "reasons": [item["reason"]],
                              "source": "scout"})
    if not answered:
        raise AppError(f"The scout found no usable moments ({last_error}).", "moments_scout_failed")
    return picks, cost
