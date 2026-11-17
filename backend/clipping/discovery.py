"""Campaign discovery: pull live campaigns from the feed, keep a history for burn rates, enrich the ones that
matter with their brief and analytics, and add a chosen one to the owner's campaigns (intake + approval gate)."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from psycopg.types.json import Jsonb

from . import campaigns as campaigns_mod
from .adapters import discovery as feed  # noqa: F401  (registers providers)
from .alerts import registry
from .core import AppError, audit, get_setting
from .db import Database

PLATFORM_NAMES = {"tiktok": "TikTok", "instagram": "Instagram Reels", "youtube": "YouTube Shorts", "x": "X (Twitter)",
                  "facebook": "Facebook", "twitch": "Twitch", "kick": "Kick", "threads": "Threads", "snapchat": "Snapchat"}
PLATFORM_TO_OURS = {"tiktok": "tiktok", "instagram": "instagram_reels", "youtube": "youtube_shorts", "x": "x", "facebook": "facebook"}
ENDED_AFTER_HOURS = 48
ENRICH_PER_PASS = 25   # briefs read per discovery pass (two feed calls each)
REREAD_BRIEF_DAYS = 3  # a brief already read is read again after this many days


def _ts(value):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _num(value):
    return None if value is None else Decimal(str(value))


def _marketplace_ids(conn) -> dict:
    return {r["slug"]: r["id"] for r in conn.execute("select id, slug from marketplaces").fetchall()}


def upsert(conn, item: dict, source: str = "clippingalpha") -> dict:
    """Store one feed campaign and a snapshot of its budget."""
    markets = _marketplace_ids(conn)
    board = item.get("boardSlug")
    row = conn.execute(
        """insert into listings (source, external_id, ref, title, brand, marketplace_id, board_slug, agency, campaign_type, pay_model,
               platforms, niche, rate_per_1k, net_per_1k, budget_left, budget_total, drain_p50, alpha_score, alpha_tier, open_join,
               region, audience, age_restricted, first_seen_at, new_at, page_url, join_url, picture_url, state, last_seen_at, raw)
           values (%(source)s, %(external_id)s, %(ref)s, %(title)s, %(brand)s, %(marketplace_id)s, %(board)s, %(agency)s, %(type)s, %(pay)s,
               %(platforms)s, %(niche)s, %(rate)s, %(net)s, %(left)s, %(total)s, %(drain)s, %(alpha)s, %(tier)s, %(open)s,
               %(region)s, %(audience)s, %(age)s, %(first)s, %(new)s, %(page)s, %(join)s, %(picture)s, 'live', now(), %(raw)s)
           on conflict (source, external_id) do update set
               title = excluded.title, rate_per_1k = excluded.rate_per_1k, net_per_1k = excluded.net_per_1k,
               budget_left = excluded.budget_left, budget_total = excluded.budget_total, drain_p50 = excluded.drain_p50,
               alpha_score = excluded.alpha_score, alpha_tier = excluded.alpha_tier, open_join = excluded.open_join,
               region = excluded.region, audience = coalesce(excluded.audience, listings.audience), platforms = excluded.platforms,
               niche = excluded.niche, state = 'live', last_seen_at = now(), new_at = excluded.new_at,
               raw = excluded.raw || jsonb_strip_nulls(jsonb_build_object('brief', listings.raw->'brief', 'analytics', listings.raw->'analytics'))
           returning *""",
        {"source": source, "external_id": str(item["campaignId"]), "ref": f"{item.get('brandSlug')}/{item.get('seriesSlug')}",
         "title": item.get("title") or "Untitled", "brand": item.get("brand"), "marketplace_id": markets.get(feed.BOARD_TO_MARKETPLACE.get(board, "")),
         "board": board, "agency": item.get("agency"), "type": item.get("campaignType"), "pay": item.get("payModel"),
         "platforms": item.get("platforms") or [], "niche": item.get("niche"), "rate": _num(item.get("ratePer1kUsd")),
         "net": _num(item.get("netPer1kUsd")), "left": _num(item.get("budgetLeftUsd")), "total": _num(item.get("budgetTotalUsd")),
         "drain": _ts(item.get("drainP50")), "alpha": _num(item.get("alphaScore")), "tier": item.get("tier"),
         "open": item.get("openJoin"), "region": item.get("region"), "audience": item.get("audienceLabel") or item.get("audience"),
         "age": bool(item.get("ageRestricted")), "first": _ts(item.get("firstSeenAt")), "new": _ts(item.get("newAt") or item.get("launchedAt")),
         "page": item.get("pageUrl"), "join": item.get("joinUrl"), "picture": ((item.get("picture") or {}).get("square")),
         "raw": Jsonb(item)},
    ).fetchone()
    conn.execute("insert into listing_snapshots (listing_id, budget_left, rate_per_1k, alpha_score) values (%s, %s, %s, %s)",
                 (row["id"], row["budget_left"], row["rate_per_1k"], row["alpha_score"]))
    return row


def refresh(conn, niches: list[str] | None = None) -> dict:
    """One discovery pass: the best and newest campaigns on every marketplace, plus the owner's niches."""
    adapter = registry.get(conn, "discovery")
    owner = conn.execute("select id from operators where role = 'admin' order by created_at limit 1").fetchone()
    owner_id = owner["id"] if owner else None
    queries = [{"board": board, "sort": sort} for board in feed.BOARDS for sort in ("alpha", "newest")]
    queries += [{"niche": niche, "sort": "alpha"} for niche in (niches or []) if niche in feed.NICHES]
    seen, market, limited = set(), None, False
    for query in queries:
        try:
            result, _ = registry.call(conn, "discovery", "search", query, owner_id)
        except feed.FeedRateLimited:
            limited = True
            break
        market = market or result.get("market") or ({"cpm": result.get("cpm"), "total": result.get("total")} if result.get("cpm") else None)
        for item in result.get("campaigns", []):
            if item.get("campaignId") is None or str(item["campaignId"]) in seen:
                continue
            seen.add(str(item["campaignId"]))
            upsert(conn, item, "fake" if adapter.provider == "fake" else "clippingalpha")
        Database.checkpoint(conn)
    # Read the briefs of the most promising campaigns so their minimums, caps and fees are known.
    enriched = 0
    top = conn.execute(
        """select id from listings where state = 'live' and not age_restricted
             and ((raw->'brief') is null or enriched_at is null or enriched_at < now() - make_interval(days => %s))
             and (%s::text[] = '{}' or niche = any(%s))
           order by (raw->'brief') is null desc, alpha_score desc nulls last limit %s""",
        (REREAD_BRIEF_DAYS, niches or [], niches or [], ENRICH_PER_PASS),
    ).fetchall()
    for row in ([] if limited else top):
        try:
            enrich(conn, row["id"])
            enriched += 1
            Database.checkpoint(conn)
        except feed.FeedRateLimited:
            limited = True
            break
        except AppError:
            break
    if market:
        conn.execute("insert into market_snapshots (source, as_of, data) values (%s, now(), %s)", (adapter.provider, Jsonb(market)))
    if limited and not seen:
        audit(conn, "discovery.refresh", "listings", None, {"provider": adapter.provider, "rate_limited": True})
        return {"seen": 0, "ended": 0, "enriched": 0, "rate_limited": True}
    ended = conn.execute(
        "update listings set state = 'ended' where state = 'live' and last_seen_at < now() - make_interval(hours => %s) returning id",
        (ENDED_AFTER_HOURS,),
    ).fetchall()
    audit(conn, "discovery.refresh", "listings", None, {"provider": adapter.provider, "seen": len(seen), "ended": len(ended), "enriched": enriched})
    return {"seen": len(seen), "ended": len(ended), "enriched": enriched, "rate_limited": limited}


def enrich(conn, listing_id) -> dict:
    """Fetch the brief and the analytics for one listing (fees, minimums, payout window, run-out, brief grade)."""
    listing = get(conn, listing_id)
    if listing["source"] == "capture":
        return listing
    brief, _ = registry.call(conn, "discovery", "brief", {"ref": listing["ref"]})
    try:
        analytics, _ = registry.call(conn, "discovery", "analytics", {"ref": listing["ref"]})
    except AppError:   # keep what was read before; the brief alone is still worth saving
        analytics = (listing.get("raw") or {}).get("analytics") or {}
    join = brief.get("join") or {}
    wants = brief.get("wants") or {}
    quality = brief.get("quality") or {}
    runway = analytics.get("runway") or {}
    forecast = runway.get("forecast") or {}
    qualify = ((analytics.get("alpha") or {}).get("qualify") or {}).get("share")
    raw = dict(listing["raw"] or {}, brief=brief, analytics=analytics)
    audience = listing.get("audience")
    if not audience:   # a Tier rule written only in the brief still limits which views pay
        texts = " ".join(item.get("text") or "" for group in (brief.get("rules") or {}).get("groups") or [] for item in group.get("items") or [])
        import re as _re

        match = _re.search(r"(mostly\s+)?tier[\s-]*1(?:[\s-]*2)?[^.]*audience|\d+\s*%\s*(?:us|tier[\s-]*1)[^.]*", texts, _re.I)
        audience = match.group(0)[:120] if match else None
    return conn.execute(
        """update listings set min_views = %s, min_payout = %s, max_payout = %s, board_fee_pct = %s, payout_window_days = %s,
               qualify_share = %s, brief_grade = %s, brief_flags = %s, checklist = %s, audience_countries = %s, languages = %s,
               brief_url = %s, spent_per_day = coalesce(%s, spent_per_day), drain_p10 = coalesce(%s, drain_p10),
               drain_p50 = coalesce(%s, drain_p50), stalled = %s, raw = %s, audience = %s, enriched_at = now()
           where id = %s returning *""",
        (_num(join.get("minViews")), _num(join.get("minPayoutUsd")), _num(join.get("maxPayoutUsd")),
         _num(join.get("boardFeePct")), _num(join.get("payoutWindowDays")), _num(qualify), quality.get("grade"),
         Jsonb(quality.get("flags") or []), Jsonb(brief.get("checklist") or []), wants.get("audienceCountries") or [],
         wants.get("languages") or [], brief.get("briefUrl"), _num(runway.get("spentPerDayUsd")), _ts(forecast.get("p10")),
         _ts(forecast.get("p50")), runway.get("stalled"), Jsonb(raw), audience, listing_id),
    ).fetchone()


def get(conn, listing_id) -> dict:
    row = conn.execute("select * from listings where id = %s", (listing_id,)).fetchone()
    if row is None:
        raise AppError("Campaign not found.", "not_found")
    return row


def brief_text(listing: dict) -> str:
    """The brief as plain sentences for Agent A. Every sentence restates the feed's own data, so every quote
    Agent A takes is verbatim from this text. The marketplace page stays the source of truth."""
    brief = (listing.get("raw") or {}).get("brief") or {}
    camp = brief.get("campaign") or {}
    wants = brief.get("wants") or {}
    join = brief.get("join") or {}
    lines = [f"{listing['title']} ({camp.get('board') or listing.get('board_slug')}).",
             f"Brief as listed by ClippingAlpha; the marketplace page is the source of truth: {brief.get('briefUrl') or listing.get('page_url')}"]
    about = (listing.get("raw") or {}).get("about") or camp.get("about")
    if about:
        lines.append(about)
    pay = listing.get("pay_model")
    if listing.get("rate_per_1k") is not None and pay in (None, "cpm"):
        lines.append(f"We pay ${Decimal(str(listing['rate_per_1k'])):.2f} per 1,000 views.")
    if pay and pay != "cpm":
        lines.append(f"Pay model: {pay}.")
    if listing.get("budget_total") is not None:
        lines.append(f"Total budget: ${Decimal(str(listing['budget_total'])):,.0f}.")
    if listing.get("budget_left") is not None:
        lines.append(f"${Decimal(str(listing['budget_left'])):,.0f} remaining.")
    if join.get("boardFeePct") is not None:
        lines.append(f"{Decimal(str(join['boardFeePct'])) * 100:.0f}% platform fee.")
    if join.get("minViews"):
        lines.append(f"Clips need a minimum of {int(join['minViews']):,} views.")
    if join.get("maxPayoutUsd"):
        lines.append(f"Maximum payout of ${Decimal(str(join['maxPayoutUsd'])):,.0f} per clip.")
    platforms = [PLATFORM_NAMES.get(p, p) for p in (listing.get("platforms") or [])]
    if platforms:
        lines.append("Post on " + ", ".join(platforms) + ".")
    if wants.get("languages"):
        lines.append("Language: " + ", ".join(wants["languages"]) + ".")
    if wants.get("audienceCountries"):
        lines.append("Audience must be in: " + ", ".join(wants["audienceCountries"]) + ".")
    if wants.get("deadline"):
        lines.append(f"Deadline: {str(wants['deadline'])[:10]}")
    for group in (brief.get("rules") or {}).get("groups") or []:
        for item in group.get("items") or []:
            text = (item.get("text") or "").strip().rstrip(".")
            if not text or item.get("chips"):
                continue
            prefix = ""
            if group.get("key") in ("do", "make") and not text.lower().startswith(("must", "you must", "keep", "no ", "do not")):
                prefix = "You must: "
            lines.append(f"{prefix}{text}.")
    return "\n".join(lines)


def add_listing(conn, actor, listing_id, category: str = "podcast", licence_ref: str | None = None) -> dict:
    """The owner chose a discovered campaign: read its brief, run Agent A and the hard blocks, and put it in
    'pending approval'. Approval is still a separate human step."""
    listing = get(conn, listing_id)
    if own_campaign_id(conn, actor.operator_id, listing_id):
        raise AppError("This campaign is already in your campaigns.", "duplicate")
    if listing["source"] != "capture" and not (listing.get("raw") or {}).get("brief"):
        listing = enrich(conn, listing_id)
    market = conn.execute("select slug from marketplaces where id = %s", (listing["marketplace_id"],)).fetchone()
    deadline = ((listing.get("raw") or {}).get("brief") or {}).get("wants", {}).get("deadline")
    result = campaigns_mod.intake(conn, actor, market["slug"] if market else "direct", listing["title"], brief_text(listing),
                                  url=listing.get("brief_url") or listing.get("page_url"), category=category,
                                  deadline=str(deadline)[:10] if deadline else None, licence_ref=licence_ref,
                                  external_ref=listing["external_id"])
    campaign = result["campaign"]
    conn.execute("update campaigns set listing_id = %s where id = %s", (listing_id, campaign["id"]))
    grade = listing.get("brief_grade") or grade_from_version(result["version"])
    conn.execute("update campaign_versions set brief_grade = %s where id = %s", (grade, result["version"]["id"]))
    audit(conn, "listing.added", "listings", listing_id, {"campaign_id": str(campaign["id"]), "brief_grade": grade})
    return {"campaign": campaign, "version": result["version"], "brief_grade": grade}


def grade_from_version(version: dict) -> str:
    """A to F from how much of the brief Agent A could pin to a quoted sentence."""
    if version.get("compliance_status") == "blocked":
        return "F"
    unclear = len(version.get("unclear_fields") or [])
    rules = len((version.get("rules_json") or {}).get("content_rules") or [])
    if unclear == 0 and rules >= 3:
        return "A"
    return {0: "B", 1: "C", 2: "D"}.get(unclear, "F")


def own_campaign_id(conn, operator_id, listing_id):
    row = conn.execute("select id from campaigns where listing_id = %s and owner_operator_id = %s order by created_at desc limit 1",
                       (listing_id, operator_id)).fetchone()
    return row["id"] if row else None


def dismiss(conn, actor, listing_id, undo: bool = False) -> None:
    """'Not for me' hides a campaign from this owner only."""
    get(conn, listing_id)
    conn.execute(
        """insert into listing_choices (owner_operator_id, listing_id, dismissed_at) values (%s, %s, %s)
           on conflict (owner_operator_id, listing_id) do update set dismissed_at = excluded.dismissed_at""",
        (actor.operator_id, listing_id, None if undo else dt.datetime.now(dt.timezone.utc)))


def current_brief_text(conn, campaign: dict) -> str | None:
    """For change detection on feed campaigns: fetch the brief again and rebuild the text."""
    if not campaign.get("listing_id"):
        return None
    listing = get(conn, campaign["listing_id"])
    if listing["source"] == "capture":
        return None
    refreshed = enrich(conn, listing["id"])
    return brief_text(refreshed)
