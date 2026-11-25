"""Agent B: hooks and ranking (D13). It may rank and tag. It may not choose the final hook or invent quotes.

A hook is written as a funny short-form editor would: 8 candidates, each in a DIFFERENT format from the hook lexicon (POV:,
Nobody:, curiosity gap, numbered ...), marked on a rubric, and the top 3 with different formats come back. The code, not the
model, computes the weighted score, checks the rules (hook_rules, including the AI-sounding phrases) and keeps the formats apart:
no two of the three share a format, and the best line never repeats the format a neighbouring clip of the same source opened with."""
from __future__ import annotations

import re

from .. import hook_lexicon, hook_rules

HOOK_TYPES = ("question", "contrarian", "stakes", "number", "story-open")
QUOTED = re.compile(r"[\"“«]([^\"”»]{3,})[\"”»]")
CANDIDATES = 8        # lines asked for in one call
KEEP = 3              # lines returned


def _format_of(item: dict, text: str, language: str) -> tuple[str | None, str | None]:
    """(format id, hook type). A leading tag decides the format; otherwise the model's `format` must be a lexicon id of this language."""
    tag = hook_lexicon.leading_tag(text)
    fmt = None
    if tag:
        fmt = next((f for f in hook_lexicon.load()["formats"] if f["language"] == language and f.get("tag") == tag), None)
    if fmt is None:
        fmt = hook_lexicon.by_id(str(item.get("format") or ""), language)
        if fmt is not None and fmt.get("tag") and not tag:
            return None, None   # the format is a tag format but the line has no tag: not that format
    hook_type = item.get("type") if item.get("type") in HOOK_TYPES else (fmt["type"] if fmt else None)
    return (fmt["id"] if fmt else None), hook_type


def validate(raw: dict, segment_text: str, avoid=(), rejected: list | None = None, language: str | None = None,
             avoid_formats=(), keep: int = KEEP) -> list[dict]:
    """Keep up to `keep` well-formed hooks, best first, no two of the same format. Drop any hook that quotes words the transcript
    does not contain, breaks the hook rules or sounds like an AI. Old-shape items (type and score, no format) still work."""
    segment_norm = " ".join(segment_text.lower().split())
    language = language or ("ar" if re.search("[؀-ۿ]", segment_text) else "en")
    pool: list[dict] = []
    for item in (raw or {}).get("hooks") or []:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        if not text:
            continue
        if any(" ".join(q.lower().split()) not in segment_norm for q in QUOTED.findall(text)):
            continue   # invented quote (checked before quote marks are cleaned away)
        text, bad = hook_rules.check(text, language=language)
        fmt, hook_type = _format_of(item, text, language)
        if hook_type is None:
            continue
        if bad:
            if rejected is not None:
                rejected.append((text, bad))
            continue
        if text.lower() in {a.lower() for a in avoid}:
            continue   # never the same text twice
        if isinstance(item.get("rubric"), dict):
            score = hook_lexicon.rubric_score(item["rubric"])
        else:
            try:
                score = min(max(float(item.get("score", 0.5)), 0.0), 1.0)
            except (TypeError, ValueError):
                score = 0.5
        if text.lower() not in {h["text"].lower() for h in pool}:
            pool.append({"text": text, "hook_type": hook_type, "score": score, "chosen": False, "pattern": fmt, "format": fmt})   # `format` for the story director: it can open as the title or as the cold-open text
    pool.sort(key=lambda h: -h["score"])
    return _spread(pool, set(avoid_formats), keep)


def _spread(pool: list[dict], avoid_formats: set, keep: int) -> list[dict]:
    """Best first, one line per format. The first line is never in `avoid_formats` when another format is available."""
    ordered = [h for h in pool if h["pattern"] not in avoid_formats] + [h for h in pool if h["pattern"] in avoid_formats]
    chosen: list[dict] = []
    for hook in ordered[:1] + pool:
        if hook in chosen or (hook["pattern"] and any(h["pattern"] == hook["pattern"] for h in chosen)):
            continue
        chosen.append(hook)
        if len(chosen) == keep:
            break
    head, rest = chosen[:1], sorted(chosen[1:], key=lambda h: -h["score"])
    chosen = head + rest
    for rank, hook in enumerate(chosen, start=1):
        hook["rank"] = rank
    return chosen


def _format_cards(names, language: str, niche: str | None) -> list[dict]:
    """What the model sees of each chosen format: id, tag, about and examples. Names that are not lexicon ids (old library rows) pass as ids."""
    cards = []
    for name in names:
        fmt = hook_lexicon.by_id(name, language)
        cards.append({"id": fmt["id"], "tag": fmt.get("tag"), "about": fmt["about"], "examples": fmt["examples"]} if fmt else {"id": name})
    if not cards:   # nothing picked by the caller: the live formats of this niche, heaviest first
        live = sorted(hook_lexicon.formats(language, niche), key=lambda f: -f["weight"])[:CANDIDATES]
        cards = [{"id": f["id"], "tag": f.get("tag"), "about": f["about"], "examples": f["examples"]} for f in live]
    return cards


MODE_TYPE = {"quote": "story-open", "claim": "contrarian", "result": "stakes", "gap": "question"}


def _raw_from_spec(brief_pick: str | None, written: list[dict]) -> dict:
    """The editor's pick first, then the writer's other lines; scores only keep that order (the model's own marks are not trusted: bake-off 2)."""
    items, seen = [], set()
    lines = ([{"text": brief_pick, "mode": None}] if brief_pick else []) + [w for w in written if isinstance(w, dict)]
    for line in lines:
        text = " ".join(str(line.get("text") or "").split())
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        kind = "number" if re.search(r"\d", text) else MODE_TYPE.get(str(line.get("mode") or ""), "contrarian")
        items.append({"text": text, "type": kind, "score": round(max(0.95 - 0.1 * len(items), 0.3), 2)})
    return {"hooks": items}


def _spec_call(conn, registry, segment_text, language, owner_id, avoid, constraints, fix) -> tuple[dict, object]:
    """Producer -> writer -> editor (bake-off 2): the producer names what is worth watching and the concrete anchors, the writer writes six lines each built
    on one anchor, the editor picks and sharpens one. No format formulas, so no empty teasers ('Wait for ...', 'Bet you ...')."""
    brief, cost1 = registry.call(conn, "llm", "complete_json", {"task": "hook_brief", "payload": {"transcript": segment_text, "language": language}}, owner_id)
    written, cost2 = registry.call(conn, "llm", "complete_json", {"task": "hook_write", "payload": {
        "transcript": segment_text, "language": language, "brief": brief, "avoid": list(avoid), "constraints": constraints, "fix": fix}}, owner_id)
    lines = [w for w in (written or {}).get("hooks") or [] if isinstance(w, dict) and w.get("text")]
    if not lines:
        return {"hooks": []}, cost1 + cost2
    final, cost3 = registry.call(conn, "llm", "complete_json", {"task": "hook_edit", "payload": {
        "transcript": segment_text, "language": language, "brief": brief, "candidates": [w["text"] for w in lines], "avoid": list(avoid)}}, owner_id)
    return _raw_from_spec((final or {}).get("hook"), lines), cost1 + cost2 + cost3


def run(conn, registry, segment_text: str, language: str, candidate_score, owner_id=None, avoid=(), patterns=(), niche: str | None = None,
        avoid_formats=(), hook_constraints=()) -> tuple[list[dict], object]:
    """Hooks that pass the rules (hook_rules). The first two tries use the producer, writer, editor chain (`_spec_call`); the last falls back to the lexicon
    formats. A set that is too small is re-asked, each time naming what was wrong with the rejected lines and the lines to avoid, so a regenerate never
    repeats itself. `hook_constraints` are the campaign's own hook rules (strings): the model is told to obey every one."""
    from ..core import AppError

    total_cost, rejected, seen = 0, [], list(avoid)
    cards = _format_cards(patterns, language, niche)
    constraints = [str(c) for c in hook_constraints or ()]
    for attempt in range(3):
        if attempt < 2:
            raw, cost = _spec_call(conn, registry, segment_text, language, owner_id, seen, constraints, hook_rules.feedback(rejected))
        else:
            raw, cost = registry.call(conn, "llm", "complete_json", {"task": "hooks", "payload": {
                "segment_text": segment_text, "language": language, "niche": niche or "general", "candidate_score": float(candidate_score or 0.6),
                "avoid": seen, "avoid_formats": list(avoid_formats), "formats": cards, "patterns": [c["id"] for c in cards],
                "good_bad": hook_lexicon.fewshot(language, niche), "constraints": constraints, "fix": hook_rules.feedback(rejected)}}, owner_id)
        total_cost += cost
        rejected = []
        hooks = validate(raw, segment_text, seen, rejected, language, avoid_formats)
        seen += [str(i.get("text")) for i in (raw or {}).get("hooks") or [] if isinstance(i, dict) and i.get("text")]   # a retry must not repeat
        if len(hooks) >= 2:
            return hooks, total_cost
    raise AppError("The AI could not write good hooks for this clip. Try again in a moment.", "hooks_failed")


def rank_candidates(candidates: list[dict]) -> list[dict]:
    """Order adapter candidates, best first. Ties keep the earlier moment first."""
    ordered = sorted(candidates, key=lambda c: (-(float(c["score"]) if c.get("score") is not None else 0.0), float(c["start_s"])))
    for rank, candidate in enumerate(ordered, start=1):
        candidate["rank"] = rank
    return ordered
