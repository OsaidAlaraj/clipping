"""Risk-adjusted campaign ranking (decision 2026-11-17, practitioner lessons).

For each discovered campaign and each of the owner's suitable accounts, simulate one clip many times:
views from the account's own history (lognormal around its median), only the views that count (audience
geography), nothing below the minimum, the per-clip cap, approval odds, and whether the budget lasts until
payout. Campaigns rank on the cautious outcome (25th percentile), per hour of work."""
from __future__ import annotations

import datetime as dt
import hashlib
import math
import random
import re
import statistics

from .core import get_setting

TIER1 = {"US", "GB", "UK", "CA", "AU"}
SIMULATIONS = 1000
OUT_OF_REACH = 0.10       # below this chance of clearing the minimum, Explore hides the campaign by default
CLIP_APPROVAL = 0.85      # until a marketplace publishes its own counts (ClippingAlpha's default)
APPLICATION_JOIN = 0.5    # campaigns that need an application
OUR_PLATFORM = {"tiktok": "tiktok", "instagram": "instagram_reels", "youtube": "youtube_shorts", "x": "x", "facebook": "facebook"}
FIXED_PAY = {"milestone", "hybrid", "flat", "retainer", "fixed"}   # pays without one viral clip
PAYOUT_RECORD_MIN = 5     # own resolved payouts on a marketplace before they replace the default approval odds
LANGUAGE_CODES = {"english": "en", "arabic": "ar", "german": "de", "french": "fr", "spanish": "es", "portuguese": "pt",
                  "italian": "it", "turkish": "tr", "hindi": "hi", "indonesian": "id", "japanese": "ja", "korean": "ko",
                  "dutch": "nl", "polish": "pl", "russian": "ru", "chinese": "zh"}


def language_codes(listing: dict) -> set[str]:
    """The languages a brief asks for, as two-letter codes (empty = any language)."""
    out = set()
    for name in listing.get("languages") or []:
        name = str(name).strip().lower()
        out.add(LANGUAGE_CODES.get(name, name[:2]))
    return out


def payout_record(conn, operator_id) -> dict:
    """Per marketplace, the share of this owner's settled clips that were paid (approved, paid or capped)."""
    rows = conn.execute(
        """select c.marketplace_id,
                  count(*) filter (where p.status in ('approved', 'paid', 'capped')) as good,
                  count(*) filter (where p.status in ('approved', 'paid', 'capped', 'rejected', 'budget_exhausted')) as settled
           from payouts p join campaign_versions v on v.id = p.campaign_version_id join campaigns c on c.id = v.campaign_id
           where p.owner_operator_id = %s group by c.marketplace_id""", (operator_id,)).fetchall()
    return {r["marketplace_id"]: r["good"] / r["settled"] for r in rows if r["settled"] >= PAYOUT_RECORD_MIN}


def approval_odds(listing: dict, record: dict) -> tuple[float, str]:
    """Own payout record on this marketplace first, then the feed's approval figure, then the default."""
    own = record.get(listing.get("marketplace_id"))
    if own is not None:
        return own, f"your payout record here: {round(own * 100)}% of settled clips paid"
    for verdict in (((listing.get("raw") or {}).get("analytics") or {}).get("alpha") or {}).get("verdicts") or []:
        if verdict.get("key") == "approval" and isinstance(verdict.get("value"), (int, float)) and 0 < verdict["value"] <= 1:
            return float(verdict["value"]), f"approval on this campaign: {round(verdict['value'] * 100)}%"
    return CLIP_APPROVAL, ""


def account_stats(conn, account: dict) -> dict:
    """Median 48-hour views and spread from the account's own posts, else the configured assumption."""
    rows = conn.execute(
        """select m.views from metrics m join posts p on p.id = m.post_id
           where p.account_id = %s and m."window" = 'h48' order by m.checked_at desc limit 60""",
        (account["id"],),
    ).fetchall()
    views = [r["views"] for r in rows if r["views"] and r["views"] > 0]
    if len(views) >= 5:
        logs = [math.log(v) for v in views]
        median, sigma, basis = statistics.median(views), max(statistics.pstdev(logs), 0.5), "your posts"
    else:
        median, sigma, basis = float(get_setting(conn, "assumed_median_views", account["id"])), 1.2, "assumed"
    tier1 = account.get("audience_tier1_pct")
    tier1_basis = account.get("audience_source") or "manual"
    if tier1 is None:
        key = "assumed_tier1_pct_arabic" if account.get("language") == "ar" else "assumed_tier1_pct"
        tier1, tier1_basis = float(get_setting(conn, key, account["id"])), "assumed"
    posts = conn.execute("select count(*) as n from posts where account_id = %s", (account["id"],)).fetchone()["n"]
    is_new = posts < int(get_setting(conn, "new_account_posts", account["id"]))
    return {"median": float(median), "sigma": float(sigma), "basis": basis, "tier1": float(tier1) / 100, "tier1_basis": tier1_basis,
            "language": account.get("language"), "new": is_new}


def geo_share(listing: dict, stats: dict) -> tuple[float, str | None]:
    countries = {c.upper() for c in (listing.get("audience_countries") or [])}
    label = " ".join(filter(None, [listing.get("audience"), listing.get("region")])).lower()
    if countries & TIER1 or re.search(r"\btier\b|\btier[\s-]*1|\bus\b|\bu\.s\.|united states|\buk\b|canada|australia", label):
        return stats["tier1"], f"needs a Tier-1 audience: about {round(stats['tier1'] * 100)}% of your views would count"
    if countries or label:
        return 0.5, "has an audience or region rule: assume half your views count"
    return 1.0, None


def _seed(*parts) -> int:
    return int(hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12], 16)


def simulate(listing: dict, stats: dict, minutes_per_clip: float, now: dt.datetime | None = None,
             approval: tuple[float, str] | None = None, typical_min_payout: float | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    reasons: list[str] = []
    rate = listing.get("net_per_1k") if listing.get("net_per_1k") is not None else listing.get("rate_per_1k")
    if rate is None or (listing.get("pay_model") not in (None, "cpm")):
        reasons = ["Pay model is not per 1,000 views: not modelled"]
        if (listing.get("pay_model") or "").lower() in FIXED_PAY:
            reasons.insert(0, "pays without a viral clip: good while your accounts are new")
        return {"score": None, "reasons": reasons, "modelled": False, "fixed_pay": (listing.get("pay_model") or "").lower() in FIXED_PAY}
    rate = float(rate)
    if listing.get("net_per_1k") is None and listing.get("board_fee_pct") is not None:
        rate *= 1 - float(listing["board_fee_pct"])
    share, geo_reason = geo_share(listing, stats)
    if geo_reason:
        reasons.append(geo_reason)
    elif stats.get("language") == "ar":
        reasons.append("no audience country rule: fits Arabic clips")
    brief_read = any(listing.get(k) is not None for k in ("payout_window_days", "min_payout", "min_views", "max_payout"))
    min_views = float(listing.get("min_views") or 0)
    if listing.get("min_payout") and rate > 0:
        min_views = max(min_views, float(listing["min_payout"]) / rate * 1000)
    if not brief_read and typical_min_payout and rate > 0:
        # Unknown is not zero: until the brief is read, assume the typical minimum of the briefs we have read.
        min_views = float(typical_min_payout) / rate * 1000
    if min_views:
        reasons.append(f"pays from {int(round(min_views, -2)):,} counted views")
    cap = float(listing["max_payout"]) if listing.get("max_payout") else None
    clip_approval, approval_reason = approval or (CLIP_APPROVAL, "")
    if approval_reason:
        reasons.append(approval_reason)
    p_approve = (1.0 if listing.get("open_join") is not False else APPLICATION_JOIN) * clip_approval
    # Budget: views verify over the payout window; a budget that runs out first pays nothing for late clips.
    window = float(listing.get("payout_window_days") or 10)
    drain = listing.get("drain_p50")
    if drain is not None:
        days_left = max((drain - now).total_seconds() / 86400, 0)
        p_budget = max(min(days_left / window, 1.0), 0.0)
        if days_left < 3:
            reasons.append(f"budget runs out in about {days_left:.1f} days")
    else:
        total, left = listing.get("budget_total"), listing.get("budget_left")
        p_budget = 1.0 if not total or left is None else max(min(float(left) / float(total) * 2, 1.0), 0.0)
    total, left = listing.get("budget_total"), listing.get("budget_left")
    if total and left is not None and float(left) < float(total) * 0.1:
        p_budget *= 0.5
        reasons.append("over 90% of the budget is spent")
    if listing.get("stalled"):
        p_budget *= 0.7
        reasons.append("budget has stalled")
    rng = random.Random(_seed(listing.get("id"), stats["median"], stats["tier1"]))
    mu = math.log(max(stats["median"], 1))
    pays, cleared = [], 0
    for _ in range(SIMULATIONS):
        views = rng.lognormvariate(mu, stats["sigma"]) * share
        if views < min_views:
            pays.append(0.0)
            continue
        cleared += 1
        pay = views / 1000 * rate
        if cap is not None:
            pay = min(pay, cap)
        if rng.random() > p_approve or rng.random() > p_budget:
            pay = 0.0
        pays.append(pay)
    pays.sort()
    p25, mean = pays[int(SIMULATIONS * 0.25)], sum(pays) / SIMULATIONS
    hours = max(minutes_per_clip, 1) / 60
    new_at = listing.get("new_at")
    boost = 1.1 if new_at and (now - new_at).total_seconds() < 72 * 3600 else 1.0
    if boost > 1:
        reasons.append("new: budget is fresh")
    p_clear = cleared / SIMULATIONS
    if not brief_read:
        reasons.append(f"brief not read yet: assumed the typical ${typical_min_payout:g} minimum payout" if typical_min_payout
                       else "brief not read yet: the minimum and cap are unknown")
    elif p_clear < 0.25:
        reasons.insert(0, f"only {round(p_clear * 100)}% of your clips would reach the minimum")
    return {
        "modelled": True,
        "score": round((p25 / hours) * boost + (mean / hours) * 0.01, 4),   # cautious outcome first, mean breaks ties
        "p25_per_clip": round(p25, 2), "mean_per_clip": round(mean, 2),
        "p25_per_hour": round(p25 / hours, 2), "mean_per_hour": round(mean / hours, 2),
        "p_clear": round(p_clear, 3) if brief_read else None, "p_budget": round(p_budget, 3), "p_approve": round(p_approve, 3),
        "geo_share": share, "reasons": reasons, "out_of_reach": brief_read and p_clear < OUT_OF_REACH,
    }


def rank(conn, listings: list[dict], operator_id) -> list[dict]:
    """Score each listing with the owner's best suitable account. Listings with no suitable account still get a
    score with assumed numbers, marked as such."""
    accounts = conn.execute("select * from accounts where status = 'active' and owner_operator_id = %s", (operator_id,)).fetchall()
    stats_cache = {a["id"]: account_stats(conn, a) for a in accounts}
    minutes = float(get_setting(conn, "minutes_per_clip", operator_id))
    assumed = {"median": float(get_setting(conn, "assumed_median_views")), "sigma": 1.2, "basis": "assumed",
               "tier1": float(get_setting(conn, "assumed_tier1_pct")) / 100, "tier1_basis": "assumed", "new": True}
    record = payout_record(conn, operator_id)
    typical = conn.execute("select percentile_cont(0.5) within group (order by min_payout) as p from listings where min_payout > 0").fetchone()["p"]
    typical = float(typical) if typical is not None else None
    all_new = all(s["new"] for s in stats_cache.values())   # no accounts yet counts as new
    out = []
    for listing in listings:
        platforms = {OUR_PLATFORM.get(p, p) for p in (listing.get("platforms") or [])}
        on_platform = [a for a in accounts if a["platform"] in platforms]
        wanted = language_codes(listing)
        suitable = [a for a in on_platform if not wanted or not a.get("language") or a["language"] in wanted]
        approval = approval_odds(listing, record)
        best, best_account = None, None
        for account in suitable or [None]:
            stats = stats_cache[account["id"]] if account else assumed
            result = simulate(listing, stats, minutes, approval=approval, typical_min_payout=typical)
            if best is None or (result.get("score") or -1) > (best.get("score") or -1):
                best, best_account = result, account
        best["account"] = ({"id": str(best_account["id"]), "handle": best_account["handle"], "platform": best_account["platform"]}
                           if best_account else None)
        if on_platform and not suitable:
            best["language_mismatch"] = True
            best["reasons"] = [f"wants clips in {', '.join(sorted(listing.get('languages') or []))}: none of your accounts posts in that language"] + best.get("reasons", [])
        elif best_account is None:
            best["reasons"] = best.get("reasons", []) + ["no account of yours posts here yet: numbers are assumed"]
        best["fixed_first"] = bool(all_new and best.get("fixed_pay"))
        out.append(dict(listing, rank=best))
    out.sort(key=lambda listing: (not listing["rank"]["fixed_first"], bool(listing["rank"].get("language_mismatch")),
                                  -(listing["rank"].get("score") if listing["rank"].get("score") is not None else -1),
                                  -(float(listing.get("alpha_score") or 0))))
    return out
