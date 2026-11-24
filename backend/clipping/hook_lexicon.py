"""The hook lexicon: the formats top short-form editors open with (POV:, Nobody:, Me when ...), the phrases that sound like an AI,
and the few-shot good/bad pairs, all DATA in `assets/hook-lexicon.json` (research and sources: docs/viral-templates-research.md).
A trend format carries an `expires` date and drops out of use after it; the owner's results re-weight formats through
`hook_pattern_stats` (hook_patterns.weights), so nothing here is a code change."""
from __future__ import annotations

import datetime
import functools
import json
import re
from pathlib import Path

_PATH = Path(__file__).parent / "assets" / "hook-lexicon.json"


@functools.cache
def load() -> dict:
    return json.loads(_PATH.read_text(encoding="utf-8"))


def _today(today) -> datetime.date:
    if isinstance(today, datetime.date):
        return today
    return datetime.date.fromisoformat(today) if today else datetime.date.today()


def active(fmt: dict, today=None) -> bool:
    """A format is live until its `expires` date (no date: always)."""
    return not fmt.get("expires") or _today(today) <= datetime.date.fromisoformat(fmt["expires"])


def formats(language: str, niche: str | None = None, today=None) -> list[dict]:
    """Live formats of a language that suit this niche (a format with no niches suits all)."""
    return [f for f in load()["formats"] if f["language"] == language and active(f, today) and (not f["niches"] or not niche or niche in f["niches"])]


def by_id(format_id: str | None, language: str) -> dict | None:
    return next((f for f in load()["formats"] if f["id"] == format_id and f["language"] == language), None)


def tags(today=None) -> list[str]:
    """Every live leading tag, any language ('POV:', 'Nobody:', 'Me when:', 'ما حدا:' ...)."""
    return sorted({f["tag"] for f in load()["formats"] if f.get("tag") and active(f, today)}, key=len, reverse=True)


def leading_tag(text: str, today=None) -> str | None:
    """The allowed tag a hook starts with, or None. Matching ignores case and a space before the colon."""
    head = re.sub(r"\s+", " ", str(text or "").lstrip())
    for tag in tags(today):
        if head.lower().startswith(tag.lower()):
            return tag
    return None


def banned_phrase(text: str, language: str) -> str | None:
    """The first phrase that makes a hook sound like an AI wrote it, or None."""
    lowered = " " + re.sub(r"[^\w\s']", " ", str(text or "").lower().replace("’", "'")) + " "
    lowered = re.sub(r"\s+", " ", lowered)
    for phrase in load()["banned"].get(language, []) + (load()["banned"]["en"] if language != "en" else []):
        needle = re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", phrase.lower().replace("’", "'"))).strip()
        if needle and (f" {needle} " in lowered or (language == "ar" and needle in lowered)):
            return phrase
    return None


def fewshot(language: str, niche: str | None) -> list[dict]:
    pool = load()["fewshot"].get(language, {})
    return pool.get(niche or "") or pool.get("lifestyle", [])


def rubric_score(scores: dict) -> float:
    """Weighted 0 to 1 score of a candidate's rubric marks (each 0 to 10). Missing marks count as 0."""
    weights = load()["rubric_weights"]
    total = 0.0
    for key, weight in weights.items():
        try:
            total += weight * min(max(float(scores.get(key, 0)), 0.0), 10.0) / 10.0
        except (TypeError, ValueError):
            continue
    return round(total, 4)
