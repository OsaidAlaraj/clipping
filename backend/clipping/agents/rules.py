"""Agent A: rules extraction (spec 12.2). It proposes a draft. It never approves, and it may not infer.

The LLM's output is not trusted. Every quote is checked word for word against the brief; anything
without a verbatim quote becomes `unclear`. Hard blocks are found by deterministic checks that the
LLM cannot remove."""
from __future__ import annotations

import re

from ..core import sha256_text

MODELS = {"cpm", "pool", "per_post", "retainer"}
PLATFORMS = {"tiktok", "instagram_reels", "youtube_shorts", "x", "facebook"}
MUSIC = {"campaign_audio_only", "licensed_only", "none", "unclear"}
PAYOUT_FIELDS = ["model", "rate_per_1k", "fee_pct", "min_views_to_qualify", "max_payout_per_clip", "budget_total", "budget_remaining"]

GAMBLING = r"casino|sportsbook|sports ?betting|\bbetting\b|gambl|\bpoker\b|roulette|wager|قمار|كازينو|مراهن"
FINANCE = r"crypto|bitcoin|ethereum|\bnft\b|\btoken sale\b|forex|\btrading\b|\binvest(?:ment|ing)?\b|\bbroker|\bloans?\b|تداول|عملات رقمية|استثمار"
NO_DISCLOSURE = (r"(?:do not|don't|never|no need to)[^.\n]{0,80}(?:#ad\b|disclos|sponsor|paid partnership|branded content)"
                 r"|without (?:any )?disclos|hide (?:that )?(?:it is|it's) (?:an ad|sponsored)")
PLATFORM_CONFLICT = (r"buy(?:ing)? views|view bots?|\bbots?\b to|engagement (?:pod|farm)|anti-?detect|fake engagement"
                     r"|remove (?:the )?(?:tiktok |platform )?watermark|same (?:video|clip) (?:on|across) (?:all|multiple) accounts")


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def is_verbatim(quote, brief_norm: str) -> bool:
    return isinstance(quote, str) and len(norm(quote)) >= 3 and norm(quote) in brief_norm


def _first_match(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text, re.I)
    if not m:
        return None
    start = max(text.rfind("\n", 0, m.start()), text.rfind(". ", 0, m.start())) + 1
    end_candidates = [i for i in (text.find("\n", m.end()), text.find(". ", m.end())) if i != -1]
    end = min(end_candidates) + 1 if end_candidates else len(text)
    return norm(text[start:end])


PROHIBITION = re.compile(r"^\s*(?:you must not\b|no\b|do not\b|don't\b|never\b|avoid\b|not\b|without\b|zero\b)", re.I)


def _sentences(text: str) -> list[str]:
    return [norm(s) for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if norm(s)]


def _first_asking(pattern: str, text: str) -> str | None:
    """The first sentence that matches and is not itself a prohibition ("No bought views" forbids, it does not ask)."""
    for sentence in _sentences(text):
        if re.search(pattern, sentence, re.I) and not PROHIBITION.search(sentence):
            return sentence
    return None


def deterministic_hard_blocks(brief_text: str, rules: dict, licence_ref: str | None, max_accounts: int) -> list[dict]:
    """FR-04 and spec 12.4. These never depend on the LLM."""
    blocks: list[dict] = []
    quote = _first_match(GAMBLING, brief_text)
    if quote:
        blocks.append({"code": "gambling", "reason": "Gambling-linked campaign (hard rule 5).", "quote": quote})
    quote = _first_match(FINANCE, brief_text)
    if quote and not (licence_ref and licence_ref.strip()):
        blocks.append({"code": "finance_unlicensed",
                       "reason": "Finance or crypto campaign without a licensed-client reference and approved copy (hard rule 6).",
                       "quote": quote})
    quote = _first_match(NO_DISCLOSURE, brief_text)
    if quote or (rules.get("disclosure") or {}).get("forbidden_by_brief"):
        blocks.append({"code": "disclosure_forbidden", "reason": "The brief forbids disclosure (spec 12.3).",
                       "quote": quote or (rules.get("disclosure") or {}).get("quote")})
    quote = _first_asking(PLATFORM_CONFLICT, brief_text)
    if quote:
        blocks.append({"code": "conflicts_with_platform_rules", "reason": "The brief asks for something platform rules forbid.",
                       "quote": quote})
    required = (rules.get("accounts") or {}).get("required_per_platform")
    if isinstance(required, (int, float)) and required > max_accounts:
        blocks.append({"code": "too_many_accounts",
                       "reason": f"The brief requires {int(required)} accounts per platform; the setting allows {max_accounts}.",
                       "quote": (rules.get("accounts") or {}).get("quote")})
    return blocks


def validate(raw: dict, brief_text: str, ctx: dict) -> dict:
    """Turn raw LLM output into a rules JSON (spec 11.2) that only contains what the brief says."""
    brief_norm = norm(brief_text)
    unclear: list[str] = []
    raw = raw if isinstance(raw, dict) else {}

    raw_payout = raw.get("payout") if isinstance(raw.get("payout"), dict) else {}
    quotes = raw_payout.get("quotes") if isinstance(raw_payout.get("quotes"), dict) else {}
    payout: dict = {"currency": "USD", "quotes": {}}
    for field in PAYOUT_FIELDS:
        value = raw_payout.get(field)
        ok = value is not None and is_verbatim(quotes.get(field), brief_norm)
        if field == "model":
            ok = ok and value in MODELS
        else:
            ok = ok and isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
        payout[field] = value if ok else None
        if ok:
            payout["quotes"][field] = norm(quotes[field])
    if payout["model"] is None:
        unclear.append("payout.model")
    if payout["model"] in (None, "cpm") and payout["rate_per_1k"] is None:
        unclear.append("payout.rate_per_1k")

    platforms = [p for p in raw.get("platforms_allowed") or [] if p in PLATFORMS]
    if not platforms or not is_verbatim(raw.get("platforms_quote"), brief_norm):
        platforms = []
        unclear.append("platforms_allowed")
    languages = [code for code in raw.get("languages_allowed") or [] if code in ("en", "ar")]
    if not is_verbatim(raw.get("languages_quote"), brief_norm):
        languages = []   # not stated: not a required field, the owner picks per source

    raw_accounts = raw.get("accounts") if isinstance(raw.get("accounts"), dict) else {}
    accounts = {"max_per_platform": 1, "second_account_allowed": False, "required_per_platform": None, "quote": None}
    if is_verbatim(raw_accounts.get("quote"), brief_norm):
        accounts.update({k: raw_accounts.get(k) for k in ("max_per_platform", "second_account_allowed", "required_per_platform")},
                        quote=norm(raw_accounts["quote"]))

    raw_disc = raw.get("disclosure") if isinstance(raw.get("disclosure"), dict) else {}
    disclosure = {"required_by_brief": False, "forbidden_by_brief": False, "quote": None}
    if is_verbatim(raw_disc.get("quote"), brief_norm):
        disclosure = {"required_by_brief": bool(raw_disc.get("required_by_brief")),
                      "forbidden_by_brief": bool(raw_disc.get("forbidden_by_brief")), "quote": norm(raw_disc["quote"])}

    content_rules = []
    for rule in raw.get("content_rules") or []:
        if not isinstance(rule, dict) or not rule.get("text"):
            continue
        quoted = is_verbatim(rule.get("quote"), brief_norm)
        requirement = rule.get("requirement") if rule.get("requirement") in ("required", "forbidden", "allowed") else "unclear"
        content_rules.append({"text": norm(str(rule["text"])), "category": str(rule.get("category") or "other"),
                              "requirement": requirement if quoted else "unclear",
                              "quote": norm(rule["quote"]) if quoted else None})

    music = raw.get("music_policy") if raw.get("music_policy") in MUSIC else "unclear"
    if music != "unclear" and not is_verbatim(raw.get("music_quote"), brief_norm):
        music = "unclear"
    if music == "unclear":
        unclear.append("music_policy")

    rules = {
        "campaign_ref": ctx.get("campaign_ref"), "marketplace": ctx.get("marketplace"), "version": ctx.get("version", 1),
        "read_at": ctx.get("read_at"), "snapshot_hash": sha256_text(brief_text),
        "payout": payout, "platforms_allowed": platforms, "languages_allowed": languages, "accounts": accounts,
        "disclosure": disclosure, "content_rules": content_rules, "music_policy": music,
        "music_quote": norm(raw["music_quote"]) if music != "unclear" else None,
        "risk_flags": [str(f) for f in raw.get("risk_flags") or []][:20],
        "deadline": raw.get("deadline") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(raw.get("deadline") or "")) else None,
    }
    blocks = deterministic_hard_blocks(brief_text, rules, ctx.get("licence_ref"), int(ctx.get("max_accounts", 1)))
    seen = {b["code"] for b in blocks}
    for proposed in raw.get("hard_blocks") or []:   # the agent may add a block, with a verbatim quote
        if isinstance(proposed, dict) and proposed.get("code") not in seen and is_verbatim(proposed.get("quote"), brief_norm):
            blocks.append({"code": str(proposed["code"]), "reason": "Flagged by Agent A.", "quote": norm(proposed["quote"])})
            seen.add(proposed["code"])
    rules["hard_blocks"] = blocks
    status = "blocked" if blocks else "review" if unclear else "pass"
    return {"rules_json": rules, "unclear_fields": unclear, "hard_blocks": blocks, "compliance_status": status}


def run(conn, registry, brief_text: str, ctx: dict, owner_id=None) -> dict:
    """Extract and validate. If the LLM adapter is unavailable the result is an all-unclear draft for
    human entry (the declared fallback), still with deterministic hard blocks applied."""
    from ..core import AppError

    cost = 0
    try:
        raw, cost = registry.call(conn, "llm", "complete_json", {"task": "rules", "payload": {
            "brief_text": brief_text, "category_schema": ctx.get("category_schema"),
            "campaign_ref": ctx.get("campaign_ref"), "marketplace": ctx.get("marketplace")}}, owner_id)
    except AppError:
        raw = {}
    result = validate(raw, brief_text, ctx)
    result["cost_usd"] = cost
    return result
