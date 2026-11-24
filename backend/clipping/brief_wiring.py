"""What the confirmed campaign spec changes in the rest of the system (pure helpers; the callers own the database).

The spec lives in `campaign_versions.rules_json["spec"]`; the old keys (payout, platforms_allowed, content_rules, disclosure,
deadline) keep working: this module only fills them where the old reading found nothing, and records every decision it takes in
`rules_json["spec_decisions"]` so the owner sees it and can undo it. Other sessions read the hook and asset requirements through
`hook_constraints` and `asset_requirements`."""
from __future__ import annotations

import datetime as dt
import re
from urllib.parse import urlparse

from . import brief_spec

BROADCAST_HOSTS = ("espn.com", "nba.com", "nfl.com", "mlb.com", "nhl.com", "cbssports.com", "nbcsports.com", "foxsports.com", "skysports.com")
_PLATFORM_NAME = {"tiktok": "tiktok", "instagram": "instagram_reels", "youtube_shorts": "youtube_shorts"}


def spec_of(rules: dict | None) -> dict:
    return (rules or {}).get("spec") or {}


def apply(rules: dict, spec: dict) -> list[dict]:
    """Fill `rules` from the spec and return the decisions taken. Old keys the old reader already filled are kept."""
    decisions: list[dict] = []
    first = lambda key: brief_spec.first_value(spec, key)        # noqa: E731
    quote = lambda key: next((i["source_quote"] for i in spec["items"] if i["key"] == key and i["status"] in ("stated", "conflict")), None)  # noqa: E731

    payout = rules.setdefault("payout", {"currency": "USD", "quotes": {}})
    for field, key in (("model", "payout.model"), ("rate_per_1k", "payout.rate_per_1k"), ("min_views_to_qualify", "payout.min_qualifying_views"),
                       ("max_payout_per_clip", "payout.max_per_clip"), ("budget_total", "payout.budget_total")):
        value = first(key)
        if payout.get(field) is None and value is not None and (field != "model" or value in ("cpm", "retainer")):
            payout[field] = value
            payout.setdefault("quotes", {})[field] = quote(key)
    used, total = first("payout.budget_used_pct"), payout.get("budget_total")
    if payout.get("budget_remaining") is None and used is not None and total is not None:
        payout["budget_remaining"] = round(float(total) * (100 - float(used)) / 100, 2)
    if not rules.get("platforms_allowed"):
        rules["platforms_allowed"] = [_PLATFORM_NAME[p] for p in (first("platforms.allowed") or []) if p in _PLATFORM_NAME]
    if not rules.get("languages_allowed") and first("audience.language") in ("en", "ar"):
        rules["languages_allowed"] = [first("audience.language")]

    # Caption: required hashtags and mentions become required content rules (pipeline.build_caption reads those).
    have = {r.get("text") for r in rules.setdefault("content_rules", [])}
    for key in ("captions.required_hashtags", "captions.required_mentions"):
        for i in spec["items"]:
            if i["key"] == key and i["status"] in ("stated", "conflict") and i["value"] and i["value"] not in have:
                rules["content_rules"].append({"text": i["value"], "category": "caption", "requirement": "required", "quote": i["source_quote"]})
                have.add(i["value"])

    # Disclosure: forbidden turns the label off (the owner sees the decision); required keeps it on.
    policy = first("captions.disclosure_policy")
    disclosure = rules.setdefault("disclosure", {"required_by_brief": False, "forbidden_by_brief": False, "quote": None})
    if policy == "forbidden":
        disclosure.update(forbidden_by_brief=True, quote=quote("captions.disclosure_policy"))
        decisions.append({"id": "ad_label_off", "text": "The brief says never to use #ad, so the ad label is switched off for this campaign.",
                          "quote": quote("captions.disclosure_policy"), "undo": "ad_label"})
    elif policy == "required":
        disclosure.update(required_by_brief=True, quote=quote("captions.disclosure_policy"))
        decisions.append({"id": "ad_label_required", "text": "The brief requires the ad label, so it stays on.", "quote": quote("captions.disclosure_policy")})

    # Deadlines: a campaign end date becomes the campaign stop date.
    ends = sorted(v["at"][:10] for v in brief_spec.values(spec, "deadlines.deadline") if v.get("type") == "campaign_end" and v.get("at"))
    if ends and not rules.get("deadline"):
        rules["deadline"] = ends[0]
        decisions.append({"id": "stop_date", "text": f"The campaign stops on {ends[0]}.", "quote": quote("deadlines.deadline")})
    stops = [v for v in brief_spec.values(spec, "deadlines.deadline") if v.get("type") == "content_type_stop"]
    for v in stops:
        decisions.append({"id": "content_stop", "text": f"No more {v.get('content_type')} after {v.get('date_text')}"
                                                         + (" (the year was not in the brief: the next date is assumed)." if v.get("year_inferred") else "."),
                          "quote": quote("deadlines.deadline")})
    length = first("length.min_s")
    if length:
        decisions.append({"id": "min_length", "text": f"Clips are at least {length} seconds for this campaign.", "quote": quote("length.min_s")})
    if brief_spec.values(spec, "sources.whitelist"):
        decisions.append({"id": "source_whitelist", "text": "Footage must come from the listed sources. Anything else is refused unless you override.",
                          "quote": quote("sources.whitelist")})
    logo = first("assets.logo") or {}
    if logo.get("requirement") == "forbidden":
        decisions.append({"id": "logo_forbidden", "text": "No logo of any kind may appear on a clip.", "quote": quote("assets.logo")})
    elif logo.get("requirement") == "required":
        decisions.append({"id": "logo_required", "text": "A provided logo is required on every clip. Add the logo file from the link; the system does not download it.",
                          "quote": quote("assets.logo")})
    rules["spec_decisions"] = decisions
    rules["spec"] = spec
    return decisions


# ------------------------------------------------------------------ readers for other parts of the system

def min_clip_s(rules: dict | None, default: float) -> float:
    """The campaign's minimum clip length when the brief states one, never below the system minimum."""
    value = brief_spec.first_value(spec_of(rules), "length.min_s")
    return max(float(default), float(value)) if value else float(default)


def hook_constraints(rules: dict | None) -> list[str]:
    """Rules for the opening hook (for the hook agent): stated items only."""
    return [str(v) for v in brief_spec.values(spec_of(rules), "hook.constraints")]


def asset_requirements(rules: dict | None) -> dict:
    """Logo requirement for the render stage: {requirement, link, placement, type, guidelines_link} or {} when the brief says nothing."""
    spec = spec_of(rules)
    logo = brief_spec.first_value(spec, "assets.logo")
    if not logo:
        return {}
    return {"requirement": logo.get("requirement"), "link": logo.get("link"),
            "placement": brief_spec.first_value(spec, "assets.logo_placement"), "type": brief_spec.first_value(spec, "assets.logo_type"),
            "guidelines_link": (brief_spec.first_value(spec, "assets.logo_guidelines") or {}).get("link")}


def edit_style(rules: dict | None) -> list[str]:
    return [str(v) for v in brief_spec.values(spec_of(rules), "edit_style.guidance")]


# ------------------------------------------------------------------ source whitelist

def _norm_url(url: str) -> str:
    u = urlparse(url or "")
    host = (u.hostname or "").lower().removeprefix("www.")
    query = u.query if host in ("youtube.com", "m.youtube.com") else ""
    video = re.search(r"v=([\w-]{6,})", query)
    if host == "youtu.be":
        return f"youtube:{u.path.strip('/')}"
    if video:
        return f"youtube:{video.group(1)}"
    return f"{host}{u.path.rstrip('/')}".lower()


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2}


def source_refusal(rules: dict | None, url: str | None = None, title: str | None = None) -> str | None:
    """None when the source is allowed. Otherwise a plain sentence. A brief without a whitelist allows everything.
    A link matches by address; a file or link also matches when its title shares most words with an allowed title."""
    spec = spec_of(rules)
    forbidden = brief_spec.values(spec, "sources.forbidden_class")
    host = (urlparse(url or "").hostname or "").lower().removeprefix("www.")
    if "broadcast" in forbidden and host.endswith(BROADCAST_HOSTS):
        return "The brief forbids broadcast footage, and this link is from a broadcaster."
    allowed = brief_spec.values(spec, "sources.whitelist")
    if not allowed:
        return None
    mine = _norm_url(url) if url else None
    words = _words(title)
    for entry in allowed:
        if mine and entry.get("url") and _norm_url(entry["url"]) == mine:
            return None
        theirs = _words(entry.get("title"))
        if words and theirs and len(words & theirs) / len(theirs) >= 0.6:
            return None
    return ("This is not on the brief's list of allowed footage, so it is not added. "
            "If you are sure it is allowed, add it again with the override switched on.")


# ------------------------------------------------------------------ accounts and deadlines

def account_warnings(rules: dict | None, account: dict | None) -> list[str]:
    """Plain warnings when the account chosen for a clip may not meet the brief's account requirements."""
    spec = spec_of(rules)
    if not spec:
        return []
    niche = ((account or {}).get("niche") or "").lower()
    out: list[str] = []
    for req in brief_spec.values(spec, "account.requirements"):
        kind, detail = req.get("type"), str(req.get("detail") or "")
        if kind == "branded_page" and detail.lower() not in niche:
            out.append(f"The brief wants a {detail} branded page. This account is not marked as one.")
        elif kind == "niche_page" and not (_words(detail) & _words(niche)):
            out.append(f"The brief accepts only dedicated {detail} pages. This account's niche does not match.")
        elif kind == "bio_link":
            out.append("Check the account bio carries the website link the brief asks for before posting.")
    share = brief_spec.first_value(spec, "audience.min_share")
    if share:
        out.append(f"The brief needs {share['pct']}% of the audience from {', '.join(share['countries'])}. Check the account's audience before posting.")
    return out


def stopped_content(rules: dict | None, now: dt.datetime | None = None) -> list[str]:
    """Content types whose stop time has passed (for example skull edits), as plain names."""
    now = now or dt.datetime.now(dt.timezone.utc)
    out = []
    for v in brief_spec.values(spec_of(rules), "deadlines.deadline"):
        if v.get("type") == "content_type_stop" and v.get("at"):
            try:
                if dt.datetime.fromisoformat(v["at"]) <= now:
                    out.append(str(v.get("content_type")))
            except ValueError:
                continue
    return out
