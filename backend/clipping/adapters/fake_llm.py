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
    "en": [("question", "What if {x}?"), ("contrarian", "You are doing {x} wrong"),
           ("stakes", "Skip this and {x} costs you"), ("number", "{n} things nobody says about this"),
           ("story-open", "It all started with {x}")],
    "ar": [("question", "شو لو {x}؟"), ("contrarian", "كلنا غلطانين في {x}"),
           ("stakes", "تجاهل هاد وبتدفع ثمنه"), ("number", "{n} أشياء ما حدا بقولها"),
           ("story-open", "كل شي بلش مع {x}")],
}


def hooks(payload: dict) -> dict:
    language = payload.get("language", "en")
    text = payload.get("segment_text", "").strip()
    first = sentences(text)[0] if sentences(text) else text
    spoken = first.rstrip(".?!؟").split()
    shift = len(payload.get("avoid") or [])
    start = shift % max(len(spoken) - 2, 1)             # a regenerate looks at other words of the clip, so it is never the same text
    gist = " ".join(spoken[start:start + 3])
    gist = gist.lower() if language == "en" else gist
    seed = (sum(ord(c) for c in text) if text else 0) + shift
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


# The moment engine's two LLM tasks. Tests set "rate" / "compare" to a raw reply (or a function of the payload), "bias" to
# "first" to simulate a model that always prefers the first of a pair, and read "calls" for every payload received.
FAKE_MOMENT_LLM: dict = {"rate": None, "compare": None, "scout": None, "bias": None, "calls": []}
FUNNY = re.compile(r"\b(haha|lol|joke|funny|laugh\w*)\b|ههه|ضحك", re.I)
HYPE = re.compile(r"\b(wow|insane|unbelievable|no way|crazy)\b|!|والله|مستحيل", re.I)


def quality(text: str) -> float:
    """A stable stand-in for "how good is this clip": questions, numbers and strong words help, plus fixed variety."""
    import zlib

    value = 0.4 + (0.15 if "?" in text or "؟" in text else 0.0) + (0.1 if any(c.isdigit() for c in text) else 0.0)
    value += 0.15 if re.search(r"never|nobody|wrong|secret|lost|mistake|why|how|لماذا|كيف|خسرت", text, re.I) else 0.0
    return min(value + (zlib.crc32(text.encode("utf-8")) % 100) / 1000, 0.95)


def _hook_from(pattern: dict | None, text: str) -> str:
    """The pattern's template filled with the clip's closing words, so the hook is paid off inside the clip."""
    if not pattern:
        return ""
    last = (sentences(text) or [text])[-1].rstrip(".?!؟")
    gist = " ".join(last.split()[:3])
    return pattern["template"].replace("{x}", gist).replace("{n}", "3")[:140]


def moment_score(payload: dict) -> dict:
    FAKE_MOMENT_LLM["calls"].append(("rate", payload))
    answer = FAKE_MOMENT_LLM["rate"]
    if answer is not None:
        return answer(payload) if callable(answer) else answer
    ratings = []
    for item in payload["candidates"]:
        text = item["text"]
        emotion = "funny" if FUNNY.search(text) else "hype" if HYPE.search(text) else "informational" if re.search(r"\d", text) else "calm"
        q = quality(text)
        ratings.append({"id": item["id"], "hook": round(q, 2), "standalone": round(min(q + 0.1, 1), 2), "payoff": round(q, 2),
                        "value": round(q - 0.1, 2), "emotion": emotion, "emotion_strength": 0.85 if emotion in ("funny", "hype") else 0.5,
                        "overall": round(q, 2), "reason": f"A clear, self-contained stretch ({emotion}).",
                        "hook_text": _hook_from(item.get("pattern"), text)})
    return {"ratings": ratings}


def moment_scout(payload: dict) -> dict:
    """The scout's stand-in: groups of whole units that reach min_s, the best-looking ones first, never overlapping."""
    FAKE_MOMENT_LLM["calls"].append(("scout", payload))
    answer = FAKE_MOMENT_LLM["scout"]
    if answer is not None:
        return answer(payload) if callable(answer) else answer
    units, groups, i = payload["units"], [], 0
    while i < len(units):
        j, seconds = i, 0.0
        while j < len(units) and seconds < payload["min_s"]:
            seconds += units[j][2] + units[j][5] / 10
            j += 1
        if seconds >= payload["min_s"] and seconds <= payload["max_s"]:
            text = " ".join(u[4] for u in units[i:j])
            groups.append((quality(text), units[i][0], units[j - 1][0], text))
        i = max(j, i + 1)
    best = sorted(groups, reverse=True)[:payload["max_moments"]]
    return {"moments": [{"first": a, "last": b, "score": round(q, 2), "reason": "A self-contained stretch that builds to its point."}
                        for q, a, b, _ in best]}


def moment_compare(payload: dict) -> dict:
    FAKE_MOMENT_LLM["calls"].append(("compare", payload))
    answer = FAKE_MOMENT_LLM["compare"]
    if answer is not None:
        return answer(payload) if callable(answer) else answer
    texts, results = payload["candidates"], []
    for first, second in payload["pairs"]:
        a, b = quality(texts[first]), quality(texts[second])
        winner = first if FAKE_MOMENT_LLM["bias"] == "first" else first if a > b else second if b > a else "tie"
        results.append({"first": first, "second": second, "winner": winner})
    return {"results": results}


# The caption lab's three tasks (caption_lab, caption_check, commentary). Tests set "lab" / "check" / "commentary" to a raw reply (or a
# function of the payload) to simulate a model, and read "calls" for every payload received.
FAKE_CAPTION_LAB: dict = {"lab": None, "check": None, "commentary": None, "calls": []}


def _topic(text: str, language: str, n: int = 2) -> str:
    """The first meaningful words of the text, as they were said."""
    from ..caption_text import WORD, content_stems, stem

    words = WORD.findall(text)
    longest = sorted(dict.fromkeys(content_stems(text, language)), key=len, reverse=True)[:n]   # the longest words tend to be the topic
    order = {w: i for i, w in enumerate(dict.fromkeys(content_stems(text, language)))}
    picked = [next((x for x in words if stem(x) == w), w) for w in sorted(longest, key=order.get)]
    return (" و" if language == "ar" else " and ").join(picked)


def _best_sentence(text: str) -> str:
    sents = [s for s in sentences(text) if len(s.split()) >= 4] or sentences(text) or [text]
    return max(sents[:3], key=len)


def caption_lab(payload: dict) -> dict:
    from ..caption_text import clip_to

    FAKE_CAPTION_LAB["calls"].append(("lab", payload))
    answer = FAKE_CAPTION_LAB["lab"]
    if answer is not None:
        return answer(payload) if callable(answer) else answer
    language, text, dialect = payload["language"], payload["transcript"], (payload.get("dialect") or "").lower()
    best = _best_sentence(text).strip().rstrip(".!؟?")
    topic = _topic(text, language) or "this"
    if language == "ar":
        frame = ("شو بصير مع {t}؟ الجواب بالفيديو" if "jo" in dialect
                 else "وش صار مع {t}؟ شوفوا الفيديو للآخر" if any(k in dialect for k in ("sa", "ae", "kw", "qa", "gulf", "bh", "om"))
                 else "ليش {t}؟ الجواب بالفيديو")
    else:
        frame = "What actually happens with {t}? Watch to the end."
    tags = ["#" + w for w in _topic(text, language, 3).replace(" and ", " ").replace(" و", " ").split()]
    return {"variants": [
        {"kind": "matched", "caption": clip_to(best, 110), "title": clip_to(best, 60), "cover_text": " ".join(best.split()[:5]), "hashtags": tags},
        {"kind": "curiosity", "caption": frame.format(t=topic), "title": clip_to(frame.format(t=topic), 60),
         "cover_text": " ".join(best.split()[:4]), "hashtags": []}]}


def caption_check(payload: dict) -> dict:
    """For each sentence of the caption, quotes the transcript sentence that shares the most meaningful words with it."""
    from ..caption_match import FRAME_STEMS
    from ..caption_text import content_stems, strip_tags

    FAKE_CAPTION_LAB["calls"].append(("check", payload))
    answer = FAKE_CAPTION_LAB["check"]
    if answer is not None:
        return answer(payload) if callable(answer) else answer
    language, spoken = payload["language"], sentences(payload["transcript"])
    claims = []
    for claim in sentences(strip_tags(payload["caption"])):
        mine = set(content_stems(claim, language)) - FRAME_STEMS.get(language, set())
        if not mine:
            continue   # a call to action ("watch to the end") states nothing about the clip
        best = max(spoken, key=lambda s: len(mine & set(content_stems(s, language))), default="")
        shared = len(mine & set(content_stems(best, language)))
        claims.append({"claim": claim, "supported": bool(mine) and shared / len(mine) >= 0.4, "quote": best})
    return {"claims": claims}


def commentary(payload: dict) -> dict:
    FAKE_CAPTION_LAB["calls"].append(("commentary", payload))
    answer = FAKE_CAPTION_LAB["commentary"]
    if answer is not None:
        return answer(payload) if callable(answer) else answer
    language, dialect = payload["language"], (payload.get("dialect") or "").lower()
    topic = _topic(payload["transcript"], language) or "this"
    if language == "ar":
        text = f"برأيي {topic} هي الأهم بهالمقطع" if "jo" in dialect else f"رأيي: {topic} أهم نقطة في هذا المقطع"
    else:
        text = f"My take: {topic} is the part worth thinking about here."
    return {"commentary": text}


def complete(task: str, payload: dict) -> dict:
    return {"rules": extract_rules, "hooks": hooks, "compliance": compliance, "moments": moments,
            "moment_score": moment_score, "moment_compare": moment_compare, "moment_scout": moment_scout, "caption_lab": caption_lab,
            "caption_check": caption_check, "commentary": commentary}[task](payload)
