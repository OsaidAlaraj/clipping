"""Brand checks: what a marketplace told us about a rejected clip, kept per brand and applied to the brand's next clips.

The owner records the marketplace's own words for a rejected clip. They are read by plain rules (no AI, so this works with
no keys) into checks the compliance step runs on every later clip of the same brand:
  banned_phrase  "do not say X", "X is not allowed"         -> the clip must not contain X          (severity block)
  require_text   "missing #ad", "must tag @brand"           -> the clip must contain the tag or text (severity warn)
  reminder       anything too vague to check ("not on brand") -> shown to the owner, never changes the verdict
A block fails the clip like any other deterministic failure; a warn sends it to the owner as "review". A brand is the
listing's brand, else the campaign's name. The marketplace's words are stored as written (trimmed), never in the audit log."""
from __future__ import annotations

import re
import unicodedata

from .core import AppError, GateError, audit

MAX_REASON = 500
MAX_TERM = 80
MAX_RULES_PER_REASON = 6
SEVERITY_ORDER = {"block": 0, "warn": 1, "reminder": 2}

_QUOTED = re.compile(r"[\"“”«]([^\"“”«»]{2,%d})[\"“”»]|(?<!\w)[‘'`]([^‘'`’]{2,%d})[’'`](?!\w)" % (MAX_TERM, MAX_TERM))
_TAGS = re.compile(r"(?<![\w#@])([#@][\w.]{2,%d})" % MAX_TERM)
_CLAUSES = re.compile(r"[.;!?\n،؛]+|,\s")
# Words that say a thing is forbidden, and words that say a thing is missing. English and Arabic.
_FORBIDS = re.compile(
    r"\b(?:not allowed|isn't allowed|is not allowed|aren't allowed|banned|prohibited|forbidden|do not (?:say|use|mention|include|show|put)|"
    r"don't (?:say|use|mention|include|show|put)|must not|mustn't|cannot (?:use|say|mention)|can't (?:use|say|mention)|avoid|remove|no use of)\b"
    r"|ممنوع|محظور|لا يسمح|يمنع|لا تستخدم|لا تذكر|تجنب|احذف|شيل", re.IGNORECASE)
_REQUIRES = re.compile(
    r"\b(?:missing|must (?:include|have|contain|tag|mention|use|add)|needs?|required|requires?|should (?:include|have|tag|mention|use|add)|"
    r"add|forgot|forgotten|without|lacks?|not tagged|no tag)\b"
    r"|ناقص|مفقود|يجب|لازم|مطلوب|أضف|اضف|بدون|نسيت", re.IGNORECASE)


def _fold(text: str) -> str:
    """Case, accents, Arabic vowel marks and spacing removed, so “Acme ” and “acme” match."""
    text = unicodedata.normalize("NFKC", text or "")
    return " ".join("".join(c for c in text if unicodedata.category(c) != "Mn").casefold().split())


def _clean(value: str) -> str:
    return " ".join("".join(c if c.isprintable() or c in "\n\t" else " " for c in value).split())


def brand_of(conn, campaign_id) -> str:
    """The brand a campaign belongs to: the listing's brand when it came from one, else the campaign's name."""
    row = conn.execute(
        """select coalesce(nullif(trim(l.brand), ''), c.name) as brand from campaigns c
           left join listings l on l.id = c.listing_id where c.id = %s""", (campaign_id,)).fetchone()
    if row is None:
        raise AppError("Campaign not found.", "not_found")
    return _fold(row["brand"])[:120] or "unknown brand"


# ------------------------------------------------------------------ reading a reason (pure)

def interpret(reason: str) -> list[dict]:
    """The checks a rejection reason supports: [{kind, pattern, rule, severity}]. A reason with no specific tag or quoted
    phrase, or without a word saying whether it is forbidden or missing, becomes one reminder. Never raises."""
    text = _clean(str(reason or ""))
    found: dict[tuple[str, str], dict] = {}
    for clause in (c for c in _CLAUSES.split(text) if c and c.strip()):
        terms = [(a or b).strip() for a, b in _QUOTED.findall(clause)] + _TAGS.findall(clause)
        terms = [t for t in dict.fromkeys(terms) if 2 <= len(t) <= MAX_TERM]
        if not terms:
            continue
        forbids, requires = bool(_FORBIDS.search(clause)), bool(_REQUIRES.search(clause))
        if forbids and not requires:
            kind, severity, rule = "banned_phrase", "block", "Must not contain “{}”"
        elif requires and not forbids:
            kind, severity, rule = "require_text", "warn", "Must contain “{}”"
        else:
            continue        # both, or neither: too unclear to turn into a check
        for term in terms:
            found.setdefault((kind, _fold(term)), {"kind": kind, "pattern": term, "rule": rule.format(term), "severity": severity})
    out = list(found.values())[:MAX_RULES_PER_REASON]
    return out or [{"kind": "reminder", "pattern": text[:200], "rule": f"Marketplace note: {text[:120]}", "severity": "reminder"}]


def _contains(haystack: str, needle: str) -> bool:
    folded = _fold(needle)
    return bool(folded) and re.search(r"(?<![\w#@])" + r"\s+".join(map(re.escape, folded.split())) + r"(?!\w)", haystack) is not None


def evaluate(rules: list[dict], texts: list[str]) -> list[dict]:
    """One compliance row per brand check, in the shape of the deterministic checks plus the brand and the marketplace's words."""
    haystack = _fold("\n".join(t for t in texts if t))
    out = []
    for i, rule in enumerate(sorted(rules, key=lambda r: SEVERITY_ORDER.get(r["severity"], 9))):
        present = _contains(haystack, rule["pattern"]) if rule["kind"] != "reminder" else None
        broken = present if rule["kind"] == "banned_phrase" else (not present if rule["kind"] == "require_text" else False)
        if rule["kind"] == "reminder":
            status, evidence = "note", f"The marketplace said: “{rule['quote']}”"
        elif broken:
            status = "fail" if rule["severity"] == "block" else "review"
            evidence = (f"Found “{rule['pattern']}”" if rule["kind"] == "banned_phrase" else f"“{rule['pattern']}” is missing") + \
                       f". The marketplace said: “{rule['quote']}”"
        else:
            status, evidence = "pass", "Not found" if rule["kind"] == "banned_phrase" else "Present"
        out.append({"check": f"brand_{rule['kind']}_{i + 1}", "status": status, "evidence": evidence[:600], "brand": rule["brand"],
                    "rule": rule["rule"], "severity": rule["severity"]})
    return out


# ------------------------------------------------------------------ storage

def for_campaign(conn, campaign_id, owner_id) -> list[dict]:
    """The active checks of this campaign's brand for this owner (the worker sees every owner's rows, so the owner is explicit)."""
    return conn.execute(
        """select brand, kind, pattern, rule, quote, severity from brand_checks
           where owner_operator_id = %s and brand = %s and active order by created_at""",
        (owner_id, brand_of(conn, campaign_id))).fetchall()


def listing(conn, campaign_id=None) -> list[dict]:
    """The brand checks the owner can see (RLS), newest first, for one campaign's brand or all."""
    brand = brand_of(conn, campaign_id) if campaign_id else None
    return conn.execute(
        """select id, brand, kind, pattern, rule, quote, severity, active, clip_id, created_at from brand_checks
           where (%s::text is null or brand = %s) order by created_at desc limit 200""", (brand, brand)).fetchall()


def record_rejection(conn, actor, clip_id, reason) -> dict:
    """The owner tells us why a marketplace rejected a clip. Returns {brand, checks, added, kept_as}: `checks` are the rules now in
    force for the brand, `kept_as` is "reminder" when the reason was too vague to check (it is kept, but blocks nothing)."""
    if actor.kind != "operator":
        raise GateError("Only the owner can record a marketplace rejection.")
    if not isinstance(reason, str) or not _clean(reason):
        raise AppError("Write the reason the marketplace gave.", "reason_required")
    quote = _clean(reason)
    if len(quote) > MAX_REASON:
        raise AppError(f"Keep the reason under {MAX_REASON} characters.", "reason_too_long")
    clip = conn.execute("select id, campaign_id, owner_operator_id from clips where id = %s", (clip_id,)).fetchone()
    if clip is None:
        raise AppError("Clip not found.", "not_found")
    brand = brand_of(conn, clip["campaign_id"])
    rows, added = [], 0
    for rule in interpret(quote):
        row = conn.execute(
            """insert into brand_checks (owner_operator_id, brand, kind, pattern, rule, quote, severity, clip_id)
               values (%s, %s, %s, %s, %s, %s, %s, %s)
               on conflict (owner_operator_id, brand, kind, pattern) do update set active = true
               returning id, brand, kind, pattern, rule, quote, severity, (xmax = 0) as is_new""",
            (clip["owner_operator_id"], brand, rule["kind"], _fold(rule["pattern"]) if rule["kind"] != "reminder" else rule["pattern"],
             rule["rule"], quote, rule["severity"], clip["id"])).fetchone()
        added += bool(row.pop("is_new"))
        rows.append(row)
    audit(conn, "brand_check.recorded", "clips", clip["id"], {"count": len(rows), "added": added, "kinds": sorted({r["kind"] for r in rows})})
    return {"brand": brand, "checks": rows, "added": added,
            "kept_as": "reminder" if all(r["kind"] == "reminder" for r in rows) else "checks"}


def switch(conn, actor, check_id, active: bool) -> dict:
    """Turn one brand check off (or on again): the way to undo a mistaken or outdated rule."""
    if actor.kind != "operator":
        raise GateError("Only the owner can change a brand check.")
    row = conn.execute("update brand_checks set active = %s where id = %s returning id, active", (bool(active), check_id)).fetchone()
    if row is None:
        raise AppError("Brand check not found.", "not_found")
    return row


__all__ = ["brand_of", "interpret", "evaluate", "for_campaign", "listing", "record_rejection", "switch"]
