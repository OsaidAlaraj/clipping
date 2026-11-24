"""The moment engine's LLM steps: rate the top candidates (subscores, emotion, a hook in a requested pattern) and compare
pairs of them. Both go through the adapter registry (task names `moment_score` and `moment_compare`).

Safety rules (security-sensitive):
- The system instructions are fixed per language in `vendors.LLM_SYSTEM`; the transcript, the brief's terms and the pattern
  library travel only as JSON data in the user message.
- The model never supplies times. It rates candidates by the ids we sent; an id we did not send is ignored.
- An answer must be a JSON object with exactly the expected key. Numbers must be finite and in range, text is stripped of
  control characters and capped, and ANY string that looks like an attempt to change instructions rejects the whole answer
  (raises LLMAnswerError), which the caller treats as "no answer"."""
from __future__ import annotations

import math
import re
from decimal import Decimal

from .. import hook_rules
from .. import moment_rank as rank

MAX_TEXT_CHARS = 700            # a candidate's text sent to the model: head and tail of long windows
MAX_REASON, MAX_HOOK = 200, 140
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
INJECTION = re.compile(
    r"ignore\s+(?:all\s+|the\s+|any\s+|your\s+)?(?:previous|prior|above|earlier)|disregard\s+(?:the\s+|all\s+|any\s+)?(?:previous|above|instructions)"
    r"|system\s+prompt|you\s+are\s+now|new\s+instructions|override\s+(?:the\s+)?(?:instructions|rules)|تجاهل\s+(?:كل\s+)?التعليمات",
    re.I)


class LLMAnswerError(ValueError):
    """The model's answer cannot be used (not JSON of the expected shape, or it tried to give orders)."""


def excerpt(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else f"{text[:int(limit * 0.6)]} … {text[-int(limit * 0.35):]}"


def _clean(value, limit: int) -> str:
    """Plain short text, or "" for anything else. A string that tries to give orders rejects the whole answer."""
    if not isinstance(value, str):
        return ""
    if INJECTION.search(value):
        raise LLMAnswerError("the answer tried to change the instructions")
    return " ".join(CONTROL.sub(" ", value).split())[:limit]


def _unit(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        return None
    return float(value)


def _expect(raw, key: str) -> list:
    if not isinstance(raw, dict) or set(raw) - {key}:
        raise LLMAnswerError(f"the answer is not an object with only {key!r}")
    items = raw.get(key)
    if not isinstance(items, list):
        raise LLMAnswerError(f"the answer has no {key} list")
    return items


# ------------------------------------------------------------------ rating

def parse_ratings(raw, texts: dict[str, str]) -> dict[str, dict]:
    """Valid ratings by candidate id. `texts` are the windows that were sent (a hook may quote only those words)."""
    ratings: dict[str, dict] = {}
    for item in _expect(raw, "ratings")[:len(texts) + 2]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] not in texts or item["id"] in ratings:
            continue
        overall = _unit(item.get("overall"))
        reason, hook = _clean(item.get("reason"), MAX_REASON), _clean(item.get("hook_text"), MAX_HOOK)
        if overall is None:
            continue
        said = " ".join(texts[item["id"]].lower().split())
        if any(" ".join(q.lower().split()) not in said for q in _QUOTED.findall(hook)):
            hook = ""                                   # a hook may quote the clip, never invent a quote
        if hook:
            hook, bad = hook_rules.check(hook)          # the same rules as every hook; a line that breaks them is dropped
            if bad:
                hook = ""
        rating = {"overall": overall, "reason": reason, "hook_text": hook,
                  "emotion": item.get("emotion") if item.get("emotion") in rank.EMOTIONS else "calm"}
        for name in ("hook", "standalone", "payoff", "value", "emotion_strength"):
            value = _unit(item.get(name))
            if value is not None:
                rating[name] = value
        ratings[item["id"]] = rating
    if not ratings:
        raise LLMAnswerError("the answer rated none of the candidates")
    return ratings


_QUOTED = re.compile(r"[\"“«]([^\"”»]{3,})[\"”»]")


def rate(conn, registry, owner, language: str, profile, brief: dict, items: list[dict]) -> tuple[dict[str, dict], Decimal]:
    """One call: rate `items` ([{id, start_s, end_s, text, pattern}]). Raises LLMAnswerError for a bad answer; registry
    errors (paused, failed, ContentFiltered, kill switch) pass through for the caller to decide."""
    payload = {"language": language, "profile": {"name": profile.name, "moments": list(profile.moments), "info_ok": profile.info_ok},
               "avoid": brief["avoid"][:20], "include": brief["include"][:20],
               "candidates": [{"id": i["id"], "seconds": round(i["end_s"] - i["start_s"], 1), "text": excerpt(i["text"]),
                               "pattern": i.get("pattern")} for i in items]}
    raw, cost = registry.call(conn, "llm", "complete_json", {"task": "moment_score", "payload": payload}, owner)
    return parse_ratings(raw, {i["id"]: i["text"] for i in items}), cost


# ------------------------------------------------------------------ comparing

def parse_comparisons(raw, pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """(winner, loser) for every answered pair; a tie or an unknown pair is left out."""
    asked = set(pairs)
    results = []
    for item in _expect(raw, "results")[:len(pairs) + 2]:
        if not isinstance(item, dict):
            continue
        first, second, winner = item.get("first"), item.get("second"), item.get("winner")
        if not isinstance(first, str) or not isinstance(second, str) or (first, second) not in asked:
            continue
        asked.discard((first, second))
        if winner == first:
            results.append((first, second))
        elif winner == second:
            results.append((second, first))
        elif winner != "tie":
            raise LLMAnswerError("a winner is neither of the pair")
    if not results and asked == set(pairs):
        raise LLMAnswerError("the answer compared none of the pairs")
    return results


def compare(conn, registry, owner, language: str, profile, texts: dict[str, str], pairs: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], Decimal]:
    """One call: which of each pair (shown in the given order) is the better clip. Returns (winner, loser) pairs."""
    payload = {"language": language, "profile": {"name": profile.name, "moments": list(profile.moments)},
               "candidates": {i: excerpt(t, MAX_TEXT_CHARS - 200) for i, t in texts.items()},
               "pairs": [[a, b] for a, b in pairs]}
    raw, cost = registry.call(conn, "llm", "complete_json", {"task": "moment_compare", "payload": payload}, owner)
    return parse_comparisons(raw, pairs), cost

