"""Deterministic checks and Agent C (spec 12.1, 12.2).

Deterministic checks run first. The LLM never overrides a deterministic failure. Anything the agent
cannot confirm is `review`. The report never certifies legal compliance (hard rule 9)."""
from __future__ import annotations

import re

from .. import brand_checks

LEGAL_NOTICE = ("This report flags and records. It does not certify legal compliance, judge copyright "
                "or verify licences. The owner decides.")


def deterministic_checks(clip: dict, template: dict, limits: dict, banned_words: list[str], texts: list[str]) -> list[dict]:
    """duration, aspect 9:16, caption starts with disclosure, overlay present, banned words."""
    out = []
    duration = float(clip.get("duration_s") or 0)
    lo, hi = float(limits["min_s"]), float(limits["max_s"])
    out.append({"check": "duration", "status": "pass" if lo <= duration <= hi else "fail",
                "evidence": f"{duration:.1f} s; allowed {lo:g} to {hi:g} s"})
    w, h = clip.get("width") or 0, clip.get("height") or 0
    out.append({"check": "aspect_9_16", "status": "pass" if w and h and abs(w * 16 - h * 9) <= 16 else "fail",
                "evidence": f"{w}x{h}"})
    caption = (clip.get("caption_text") or "").lstrip()
    disclosure = (template.get("caption_text") or "").strip()
    overlay = clip.get("overlay_json") or {}
    if template.get("label_off"):   # the owner switched the ad label off for this campaign (ad_label.py)
        required = bool(template.get("required"))
        status = "fail" if required else "pass"
        note = "The brief requires the ad label, but it is off." if required else "Ad label off for this campaign."
        out.append({"check": "caption_starts_with_disclosure", "status": status, "evidence": note})
        out.append({"check": "overlay_present_full_duration", "status": status, "evidence": note})
    else:
        starts = bool(disclosure) and caption.startswith(disclosure) and (
            len(caption) == len(disclosure) or not re.match(r"[\w]", caption[len(disclosure)]))
        out.append({"check": "caption_starts_with_disclosure", "status": "pass" if starts else "fail",
                    "evidence": caption[:40] or "(empty caption)"})
        expected = (template.get("overlay_text") or "").strip()
        present = (bool(expected) and overlay.get("text") == expected and float(overlay.get("start_s", 1)) <= 0.05
                   and float(overlay.get("end_s", 0)) >= duration - 0.1 and duration > 0)
        out.append({"check": "overlay_present_full_duration", "status": "pass" if present else "fail",
                    "evidence": f"{overlay.get('text')!r} from {overlay.get('start_s')} to {overlay.get('end_s')} s"
                    if overlay else "no overlay recorded"})
    joined = " ".join(t for t in texts if t).lower()
    hits = sorted({word for word in banned_words if word and word.lower() in joined})
    out.append({"check": "banned_words", "status": "fail" if hits else "pass",
                "evidence": ", ".join(hits) if hits else "none found"})
    return out


def validate_llm(raw: dict, rules: list[dict], clip_text: str) -> list[dict]:
    """One result per rule. A missing, malformed or unevidenced answer becomes `review`."""
    by_index = {}
    for item in (raw or {}).get("results") or []:
        if isinstance(item, dict) and isinstance(item.get("rule_index"), int):
            by_index[item["rule_index"]] = item
    text_norm = " ".join(clip_text.lower().split())
    results = []
    for i, rule in enumerate(rules):
        item = by_index.get(i, {})
        status = item.get("status") if item.get("status") in ("pass", "review", "fail") else "review"
        evidence = " ".join(str(item.get("evidence") or "").split())
        if rule.get("requirement") == "unclear":
            status, evidence = "review", "The rule itself is unclear."
        elif rule.get("category") in ("music", "copyright"):
            status, evidence = "review", "Music and copyright are never judged by the system. The owner checks the licence on file."
        elif status == "pass" and (not evidence or evidence.lower() not in text_norm):
            status = "review"   # a pass needs evidence copied from the clip
        results.append({"rule": rule.get("text"), "category": rule.get("category"), "requirement": rule.get("requirement"),
                        "status": status, "evidence": evidence})
    return results


def overall(deterministic: list[dict], rule_results: list[dict]) -> str:
    if any(c["status"] == "fail" for c in deterministic):
        return "fail"          # no LLM result can change this
    if any(r["status"] == "fail" for r in rule_results):
        return "fail"
    if any(r["status"] == "review" for r in (*deterministic, *rule_results)):   # a brand check that wants a look
        return "review"
    return "pass"              # "note" rows (brand reminders) never change the verdict


def run(conn, registry, clip: dict, template: dict, limits: dict, banned_words: list[str], rules: list[dict],
        hook_text: str, owner_id=None, brand_rules: list[dict] | None = None) -> tuple[dict, object]:
    """`brand_rules` are the brand's checks learned from marketplace rejections (brand_checks); they run with the deterministic ones."""
    from ..core import AppError

    texts = [clip.get("caption_text") or "", hook_text or "", (clip.get("overlay_json") or {}).get("text") or ""]
    deterministic = deterministic_checks(clip, template, limits, banned_words, texts)
    deterministic += brand_checks.evaluate(brand_rules or [], texts)
    cost = 0
    rule_results: list[dict] = []
    if any(c["status"] == "fail" for c in deterministic):
        note = "Deterministic checks failed. Agent C was not asked."
    else:
        try:
            raw, cost = registry.call(conn, "llm", "complete_json", {"task": "compliance", "payload": {
                "caption": texts[0], "hook": texts[1], "overlay": texts[2],
                "rules": [{"text": r.get("text"), "category": r.get("category"), "requirement": r.get("requirement")} for r in rules]}},
                owner_id)
            note = "Agent C checked the campaign rules."
        except AppError:
            raw, note = {}, "Agent C was unavailable. Every campaign rule needs a human look."
        rule_results = validate_llm(raw, rules, " ".join(texts))
    report = {"status": overall(deterministic, rule_results), "deterministic": deterministic, "rules": rule_results,
              "note": note, "certifies_legal_compliance": False, "legal_notice": LEGAL_NOTICE}
    return report, cost
