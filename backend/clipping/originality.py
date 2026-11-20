"""Originality layer (minimal, honest). A clip that only repeats its source is the kind of post YouTube and Meta now give less
reach (docs/growth-tricks.md). So each clip can carry an original element the owner adds, and the record says exactly what was added.

What it does:
- a per-account template (`accounts.commentary_template`): an intro line per language, with {topic}. It keeps the account's
  family look but, being a template, it does NOT count as added value (the platforms say template-based bulk changes do not).
- a per-clip commentary line written from the transcript (a take or a framing question; not a description, not a repeated
  sentence, no new facts). It goes into the post text. This is what counts as added value.
- the record in render_meta["originality"] lists each element, where it went, that the owner has not yet confirmed it, and that
  it is not burned into the video (the render stage does not take it yet).
What it does not do: change the picture or audio to hide the source, or any trick against duplicate detection. A brief that asks
for raw, unedited clips switches the layer off for that clip and says so."""
from __future__ import annotations

import re

from psycopg.types.json import Jsonb

from . import caption_match
from .caption_lab import arabic_dialect_hint
from .caption_text import content_stems, sentences
from .core import AppError, GateError, audit

COMMENTARY_MAX = 120
RAW_ONLY = re.compile(r"\b(raw|unedited|un-edited|as[- ]is|unmodified)\b|(?:original|source) (?:clip|footage|video)s? only|"
                      r"\b(?:no|do not (?:add|use|include)|don't (?:add|use|include)) (?:added |extra |overlay |on-screen )?(?:text|commentary|intro|captions?|voice[- ]?over)\b|"
                      r"\bdo not (?:edit|modify|alter|change)\b|\bwithout (?:any )?(?:edits|changes)\b", re.I)


def brief_blocker(rules: dict) -> str | None:
    """The brief's own words when it asks for raw clips or bans added text or commentary, else None."""
    for rule in (rules or {}).get("content_rules") or []:
        text = str(rule.get("text") or "")
        if RAW_ONLY.search(text):
            return text[:200]
    return None


def validate_template(template: dict | None) -> dict | None:
    if template is None:
        return None
    if not isinstance(template, dict):
        raise AppError("The commentary template must be an object.", "bad_template")
    intro = template.get("intro") or {}
    if not isinstance(intro, dict) or any(k not in ("en", "ar") or not isinstance(v, str) or len(v) > COMMENTARY_MAX for k, v in intro.items()):
        raise AppError(f"Each intro line must be text of at most {COMMENTARY_MAX} characters, for en or ar.", "bad_template")
    style = str(template.get("style") or "")[:200]
    return {"intro": {k: " ".join(v.split()) for k, v in intro.items() if v.strip()}, "style": style, "enabled": template.get("enabled", True) is not False}


def set_template(conn, actor, account_id, template: dict | None) -> dict:
    """The owner sets (or clears) the commentary template of one of their accounts."""
    if actor.kind != "operator":
        raise GateError("Only the owner sets an account's template.")
    clean = validate_template(template)
    row = conn.execute("update accounts set commentary_template = %s where id = %s returning id, commentary_template",
                       (Jsonb(clean) if clean is not None else None, account_id)).fetchone()
    if row is None:
        raise AppError("Account not found.", "not_found")
    audit(conn, "account.commentary_template", "accounts", account_id, {"set": clean is not None})
    return row


def template_line(template: dict | None, language: str, transcript: str) -> str | None:
    """The account's intro line for this language, with {topic} filled from the clip."""
    if not template or template.get("enabled") is False:
        return None
    line = (template.get("intro") or {}).get(language)
    if not line:
        return None
    topic = " ".join(dict.fromkeys(content_stems(transcript, language)[:2])) or ""
    return " ".join(line.replace("{topic}", topic).split())[:COMMENTARY_MAX] or None


def _is_copy(text: str, transcript: str, language: str) -> bool:
    """A commentary line that is mostly one of the clip's own sentences adds nothing."""
    mine = set(content_stems(text, language))
    if not mine:
        return False
    return any(len(mine & set(content_stems(s, language))) / len(mine) >= 0.8 for s in sentences(transcript))


def clip_commentary(conn, registry, owner_id, transcript: str, language: str, dialect: str | None, template: dict | None) -> tuple[str | None, object]:
    """One original line for this clip, or None. It must be a take (not a copy of the source), short, and free of invented numbers,
    quotes and clickbait."""
    raw, cost = registry.call(conn, "llm", "complete_json", {"task": "commentary", "payload": {
        "language": language, "dialect": arabic_dialect_hint(dialect) if language == "ar" else None, "transcript": transcript,
        "account_style": (template or {}).get("style") or ""}}, owner_id)
    text = " ".join(str((raw or {}).get("commentary") or "").split()) if isinstance(raw, dict) else ""
    if not text or len(text) > COMMENTARY_MAX or _is_copy(text, transcript, language):
        return None, cost
    if not caption_match.check(text, transcript, language, "commentary")["ok"]:
        return None, cost
    return text, cost


def build(conn, registry, owner_id, *, transcript: str, language: str, dialect: str | None, rules: dict, template: dict | None) -> tuple[dict, object]:
    """The originality record for one clip: {"commentary_text", "template_text", "elements", ...}."""
    block = brief_blocker(rules)
    elements: list[dict] = []
    cost, commentary = 0, None
    if block:
        return ({"status": "off", "brief_block": block, "commentary_text": None, "template_text": None, "elements": [],
                 "added_value": False, "note": "The brief asks for raw clips or forbids added text, so nothing is added."}, cost)
    commentary, cost = clip_commentary(conn, registry, owner_id, transcript, language, dialect, template)
    intro = template_line(template, language, transcript)
    if commentary:
        elements.append({"kind": "commentary_line", "text": commentary, "placement": "post_text", "counts_as_added_value": True,
                         "owner_confirmed": False, "burned_into_video": False})
    if intro:
        elements.append({"kind": "account_template_intro", "text": intro, "placement": "post_text", "counts_as_added_value": False,
                         "owner_confirmed": False, "burned_into_video": False})
    elements.append({"kind": "selection_and_cut", "text": None, "placement": "video", "counts_as_added_value": False,
                     "owner_confirmed": True, "burned_into_video": True})
    return ({"status": "on", "brief_block": None, "commentary_text": commentary, "template_text": intro, "elements": elements,
             "added_value": bool(commentary),
             "note": "Commentary and template lines are in the post text for the owner to confirm; they are not yet shown on the video. "
                     "Only the commentary line counts as added value; a template and the cut alone do not."}, cost)
