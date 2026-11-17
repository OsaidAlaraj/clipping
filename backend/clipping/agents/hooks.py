"""Agent B: hooks and ranking (D13). It may rank and tag. It may not choose the final hook or invent quotes."""
from __future__ import annotations

import re

HOOK_TYPES = ("question", "contrarian", "stakes", "number", "story-open")
QUOTED = re.compile(r"[\"“«]([^\"”»]{3,})[\"”»]")


def validate(raw: dict, segment_text: str) -> list[dict]:
    """Keep 2 to 3 well-formed hooks. Drop any hook that quotes words the transcript does not contain."""
    segment_norm = " ".join(segment_text.lower().split())
    hooks: list[dict] = []
    for item in (raw or {}).get("hooks") or []:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        if not text or len(text) > 140 or item.get("type") not in HOOK_TYPES:
            continue
        if any(" ".join(q.lower().split()) not in segment_norm for q in QUOTED.findall(text)):
            continue   # invented quote
        try:
            score = min(max(float(item.get("score", 0.5)), 0.0), 1.0)
        except (TypeError, ValueError):
            score = 0.5
        if text.lower() not in {h["text"].lower() for h in hooks}:
            hooks.append({"text": text, "hook_type": item["type"], "score": score, "chosen": False})
    hooks.sort(key=lambda h: -h["score"])
    hooks = hooks[:3]
    for rank, hook in enumerate(hooks, start=1):
        hook["rank"] = rank
    return hooks


def run(conn, registry, segment_text: str, language: str, candidate_score, owner_id=None) -> tuple[list[dict], object]:
    """An empty or too-small set is re-prompted once (spec 12.1, S6)."""
    from ..core import AppError

    total_cost = 0
    for _ in range(2):
        raw, cost = registry.call(conn, "llm", "complete_json", {"task": "hooks", "payload": {
            "segment_text": segment_text, "language": language, "candidate_score": float(candidate_score or 0.6)}}, owner_id)
        total_cost += cost
        hooks = validate(raw, segment_text)
        if len(hooks) >= 2:
            return hooks, total_cost
    raise AppError("Agent B did not return 2 to 3 valid hooks.", "hooks_failed")


def rank_candidates(candidates: list[dict]) -> list[dict]:
    """Order adapter candidates, best first. Ties keep the earlier moment first."""
    ordered = sorted(candidates, key=lambda c: (-(float(c["score"]) if c.get("score") is not None else 0.0), float(c["start_s"])))
    for rank, candidate in enumerate(ordered, start=1):
        candidate["rank"] = rank
    return ordered
