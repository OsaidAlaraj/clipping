"""Deterministic stand-in for the LLM (A2), used in tests and local runs without keys.
It reads the text with simple patterns and always quotes the sentence it used. The agents validate
its output exactly as they validate a real model's output."""
from __future__ import annotations

import re

NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?؟])\s+|\n+", text)
    return [p.strip(" -•\t") for p in parts if p.strip(" -•\t")]


def _num(raw: str) -> float:
    return float(raw.replace(",", ""))


def _find(sents: list[str], pattern: str):
    for s in sents:
        m = re.search(pattern, s, re.I)
        if m:
            return m, s
    return None, None


def extract_rules(payload: dict) -> dict:
    text = payload["brief_text"]
    sents = sentences(text)
    quotes: dict = {}
    payout: dict = {"model": None, "rate_per_1k": None, "currency": "USD", "fee_pct": None, "min_views_to_qualify": None,
                    "max_payout_per_clip": None, "budget_total": None, "budget_remaining": None}

    m, s = _find(sents, r"\$\s*([\d.,]+)\s*(?:per|/|for every)\s*(?:1,?000|1k|thousand)\s*(?:eligible |verified )?views")
    if m:
        payout["rate_per_1k"], quotes["rate_per_1k"] = _num(m.group(1)), s
        payout["model"], quotes["model"] = "cpm", s
    for model, pattern in (("per_post", r"\bper[- ]post\b|\bper approved (?:post|video)\b"), ("retainer", r"\bretainer\b"),
                           ("pool", r"\bprize pool\b|\bpool of\b"), ("cpm", r"\bCPM\b")):
        if payout["model"] is None:
            m, s = _find(sents, pattern)
            if m:
                payout["model"], quotes["model"] = model, s
    m, s = _find(sents, r"minimum (?:of )?([\d,]+) views|at least ([\d,]+) views")
    if m:
        payout["min_views_to_qualify"], quotes["min_views_to_qualify"] = _num(m.group(1) or m.group(2)), s
    m, s = _find(sents, r"max(?:imum)?(?: payout)?(?: of)? \$\s*([\d,]+(?:\.\d+)?) per (?:clip|video|post)")
    if m:
        payout["max_payout_per_clip"], quotes["max_payout_per_clip"] = _num(m.group(1)), s
    m, s = _find(sents, r"(?:total )?budget(?: is| of|:)?\s*\$\s*([\d,]+)")
    if m:
        payout["budget_total"], quotes["budget_total"] = _num(m.group(1)), s
    m, s = _find(sents, r"\$\s*([\d,]+)\s*(?:remaining|left)")
    if m:
        payout["budget_remaining"], quotes["budget_remaining"] = _num(m.group(1)), s
    m, s = _find(sents, r"(\d+(?:\.\d+)?)\s*%\s*(?:platform |creator |marketplace )?fee")
    if m:
        payout["fee_pct"], quotes["fee_pct"] = _num(m.group(1)), s
    payout["quotes"] = quotes

    platforms, platform_quote = [], None
    for name, pattern in (("tiktok", r"tiktok"), ("instagram_reels", r"instagram|reels"),
                          ("youtube_shorts", r"youtube|shorts"), ("x", r"\bX \(Twitter\)|\btwitter\b|\bon X\b"),
                          ("facebook", r"facebook")):
        m, s = _find(sents, pattern)
        if m:
            platforms.append(name)
            platform_quote = platform_quote or s
    languages, language_quote = [], None
    for code, pattern in (("en", r"\benglish\b"), ("ar", r"\barabic\b|العربية")):
        m, s = _find(sents, pattern)
        if m:
            languages.append(code)
            language_quote = language_quote or s

    accounts = {"max_per_platform": 1, "second_account_allowed": False, "required_per_platform": None, "quote": None}
    m, s = _find(sents, r"(?:must|need to|required to) (?:post|use|run)[^.]*?(\d+|one|two|three|four|five) (?:different |separate )?accounts")
    if m:
        n = NUM_WORDS.get(m.group(1).lower()) or int(m.group(1))
        accounts.update(required_per_platform=n, max_per_platform=n, second_account_allowed=n > 1, quote=s)
    else:
        m, s = _find(sents, r"(?:a )?second account (?:is )?(?:allowed|permitted)|up to (two|2) accounts")
        if m:
            accounts.update(max_per_platform=2, second_account_allowed=True, quote=s)

    disclosure = {"required_by_brief": False, "forbidden_by_brief": False, "quote": None}
    m, s = _find(sents, r"(?:do not|don't|never|no need to)[^.]*(?:#ad\b|disclos|sponsor|paid partnership)|without (?:any )?disclos")
    if m:
        disclosure.update(forbidden_by_brief=True, quote=s)
    else:
        m, s = _find(sents, r"(?:must|required|always)[^.]*(?:#ad\b|disclos|paid partnership)")
        if m:
            disclosure.update(required_by_brief=True, quote=s)

    content_rules = []
    for s in sents:
        low = s.lower()
        forbidden = bool(re.search(r"\b(do not|don't|never|no )\b", low))
        required = bool(re.search(r"\b(must|required|always|need to)\b", low))
        if not (forbidden or required):
            continue
        category = ("hashtag" if "#" in s else "mention" if "@" in s else "duration" if re.search(r"\bseconds?\b", low)
                    else "music" if re.search(r"music|audio|sound", low) else "other")
        content_rules.append({"text": s, "category": category,
                              "requirement": "forbidden" if forbidden else "required", "quote": s})

    music, music_quote = "unclear", None
    for policy, pattern in (("campaign_audio_only", r"only (?:use )?the (?:original|campaign|podcast|provided) (?:podcast )?audio|original audio only"),
                            ("licensed_only", r"licensed music"), ("none", r"no (?:background )?music")):
        m, s = _find(sents, pattern)
        if m:
            music, music_quote = policy, s
            break

    risk_flags, hard_blocks = [], []
    if re.search(r"casino|betting|sportsbook|gambl", text, re.I):
        hard_blocks.append({"code": "gambling", "quote": _find(sents, r"casino|betting|sportsbook|gambl")[1]})
    if re.search(r"crypto|bitcoin|forex|trading", text, re.I):
        risk_flags.append("finance_or_crypto")
    m, s = _find(sents, r"deadline:?\s*(\d{4}-\d{2}-\d{2})")
    return {
        "campaign_ref": payload.get("campaign_ref"), "marketplace": payload.get("marketplace"),
        "payout": payout, "platforms_allowed": platforms, "platforms_quote": platform_quote,
        "languages_allowed": languages, "languages_quote": language_quote,
        "accounts": accounts, "disclosure": disclosure, "content_rules": content_rules,
        "music_policy": music, "music_quote": music_quote, "risk_flags": risk_flags, "hard_blocks": hard_blocks,
        "deadline": m.group(1) if m else None,
    }


HOOK_TEMPLATES = {
    "en": [("question", "What if {x}?"), ("contrarian", "Everyone gets this wrong: {x}"),
           ("stakes", "Ignore this and it costs you: {x}"), ("number", "{n} things nobody says about this"),
           ("story-open", "It started like this: {x}")],
    "ar": [("question", "ماذا لو {x}؟"), ("contrarian", "الجميع يخطئ في هذا: {x}"),
           ("stakes", "تجاهل هذا وسوف يكلفك: {x}"), ("number", "{n} أشياء لا يقولها أحد عن هذا"),
           ("story-open", "بدأ الأمر هكذا: {x}")],
}


def hooks(payload: dict) -> dict:
    language = payload.get("language", "en")
    text = payload.get("segment_text", "").strip()
    first = sentences(text)[0] if sentences(text) else text
    gist = " ".join(first.rstrip(".?!؟").split()[:7]).lower() if language == "en" else " ".join(first.rstrip(".?!؟").split()[:7])
    seed = sum(ord(c) for c in text) if text else 0
    templates = HOOK_TEMPLATES.get(language, HOOK_TEMPLATES["en"])
    picks = [templates[(seed + i * 2) % len(templates)] for i in range(3)]
    out, seen = [], set()
    for i, (hook_type, template) in enumerate(picks):
        if hook_type in seen:
            continue
        seen.add(hook_type)
        base = float(payload.get("candidate_score") or 0.6)
        out.append({"text": template.format(x=gist, n=3 + seed % 5), "type": hook_type,
                    "score": round(max(min(base - 0.07 * i, 1), 0), 2)})
    return {"hooks": out}


def compliance(payload: dict) -> dict:
    caption = payload.get("caption", "")
    all_text = " ".join([caption, payload.get("hook", ""), payload.get("overlay", "")])
    results = []
    for i, rule in enumerate(payload.get("rules", [])):
        text, requirement, category = rule.get("text", ""), rule.get("requirement"), rule.get("category")
        tokens = re.findall(r"[#@][\w؀-ۿ]+", text)
        if category in ("hashtag", "mention") and tokens:
            present = [t for t in tokens if t.lower() in all_text.lower()]
            if requirement == "required":
                ok = len(present) == len(tokens)
                results.append({"rule_index": i, "status": "pass" if ok else "fail",
                                "evidence": present[0] if ok else ""})
            else:
                results.append({"rule_index": i, "status": "fail" if present else "pass", "evidence": " ".join(present)})
        else:
            results.append({"rule_index": i, "status": "review", "evidence": ""})
    return {"results": results}


FAKE_IMAGE_TEXT: str | None = None   # tests set this to simulate a vision model
FAKE_MOMENTS: dict = {"answer": None, "calls": []}   # tests set "answer" to a raw reply (or a function of the payload) to simulate a model


def moments(payload: dict) -> dict:
    """The moment picker's stand-in: from each stretch it takes sentences spread evenly, builds windows that start at a
    segment start and run to the first segment end inside the length range, and quotes the first sentence as the hook."""
    FAKE_MOMENTS["calls"].append(payload)
    answer = FAKE_MOMENTS["answer"]
    if answer is not None:
        return answer(payload) if callable(answer) else answer
    segments, lo, hi, limit = payload["segments"], payload["min_s"], payload["max_s"], payload["max_moments"]
    found, used_until = [], -1.0
    for start, _, text in segments:
        if len(found) >= limit:
            break
        if start < used_until:
            continue
        end = next((e for _, e, _ in segments if lo + 1 <= e - start <= hi - 1), None)
        if end is None:
            continue
        found.append({"start_s": start, "end_s": end, "score": round(max(0.95 - 0.05 * len(found), 0.1), 2),
                      "reason": f"A self-contained stretch that opens on: {text[:60]}", "hook": " ".join(text.split()[:8])})
        used_until = end + 1
    return {"moments": found}


def complete(task: str, payload: dict) -> dict:
    return {"rules": extract_rules, "hooks": hooks, "compliance": compliance, "moments": moments}[task](payload)
