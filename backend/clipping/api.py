"""HTTP API for the review app. Every request runs as an identified operator, so row-level security
and the gate triggers apply. Media is served only through short-lived signed links."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

import psycopg
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import alerts, auth, brand_checks, campaigns, config, discovery, ledger, moments, ops, pipeline, profiles, progress, publish, ranking, review, sources, templates, transcripts
from .adapters import vendors
from .core import AppError, get_settings, kill_switch, set_kill_switch, set_setting
from .db import Actor, get_db

app = FastAPI(title="Clipping Campaign System", docs_url="/api/docs", openapi_url="/api/openapi.json")

from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

# Only the laptop's own web app may call the API across ports (for large uploads that skip the dev proxy).
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"], allow_credentials=True,  # uploads and screenshots
                   allow_methods=["GET", "POST"], allow_headers=["*"])
_owner: Actor | None = None


def _supabase_actor(token: str) -> Actor:
    """Verify a Supabase JWT (HS256) and map its subject to an operator."""
    secret = config.env("SUPABASE_JWT_SECRET")
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        expected = hmac.new(secret.encode(), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256).digest()
        pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
        if not hmac.compare_digest(expected, base64.urlsafe_b64decode(pad(signature_b64))):
            raise ValueError
        claims = json.loads(base64.urlsafe_b64decode(pad(payload_b64)))
        if claims.get("exp", 0) < time.time():
            raise ValueError
    except Exception:
        raise HTTPException(401, "Sign in again.")
    with get_db().session(Actor.system()) as conn:
        row = conn.execute("select id, role from operators where auth_user_id = %s and status = 'active'", (claims.get("sub"),)).fetchone()
    if row is None:
        raise HTTPException(403, "This user is not an operator.")
    return Actor(str(row["id"]), row["role"])


def signed_in_actor(request: Request) -> Actor | None:
    with get_db().session(Actor.system()) as conn:
        return auth.actor_for_token(conn, request.cookies.get(auth.COOKIE))


def current_actor(request: Request) -> Actor:
    global _owner
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    mode = auth.mode()
    if mode == "supabase":
        return _supabase_actor(token)
    if mode == "passkey":
        actor = signed_in_actor(request)
        if actor is None:
            raise auth.AuthError()
        return actor
    required = config.env("CLIPPING_LOCAL_TOKEN")
    if required and not hmac.compare_digest(token, required):
        raise HTTPException(401, "Missing or wrong local token.")
    if _owner is None:
        _owner = ops.ensure_owner(get_db())
    return _owner


@app.exception_handler(AppError)
async def app_error(_: Request, exc: AppError):
    return JSONResponse({"error": str(exc), "code": exc.code, **getattr(exc, "extra", {})}, status_code=exc.status)


@app.exception_handler(psycopg.errors.RaiseException)
async def trigger_refusal(_: Request, exc: psycopg.errors.RaiseException):
    return JSONResponse({"error": exc.diag.message_primary, "code": f"db_{exc.sqlstate}"}, status_code=403)


@app.exception_handler(psycopg.errors.InsufficientPrivilege)
async def rls_refusal(_: Request, exc):
    return JSONResponse({"error": "You are not allowed to do that.", "code": "forbidden"}, status_code=403)


def run(actor: Actor, fn, *args, **kwargs) -> Any:
    with get_db().session(actor) as conn:
        return fn(conn, *args, **kwargs)


# ------------------------------------------------------------------ models

class IntakeIn(BaseModel):
    marketplace: str
    name: str
    brief_text: str
    url: str | None = None
    category: str = "podcast"
    deadline: str | None = None
    licence_ref: str | None = None


class PracticeIn(BaseModel):
    name: str
    niche: str = "podcast"
    language: str = "en"


class ReasonIn(BaseModel):
    reason: str | None = None


class SourceIn(BaseModel):
    file_path: str
    language: str
    dialect: str | None = None
    licence_ref: str | None = None
    permission_ref: str | None = None
    origin_url: str | None = None
    expected_hash: str | None = None


class DecisionIn(BaseModel):
    session_id: str | None = None
    client_action_id: str | None = None
    reason_code: str | None = None
    notes: str | None = None
    reason: str | None = None


class RejectionIn(BaseModel):
    reason: str | None = None


class SwitchIn(BaseModel):
    active: bool = True


class UndoIn(BaseModel):
    review_id: str
    client_action_id: str | None = None


class EditIn(BaseModel):
    trim: dict | None = None
    hook_id: str | None = None
    caption: str | None = None


class RecheckIn(BaseModel):
    current_text: str | None = None
    unchanged_confirmed: bool = False


class PublishIn(BaseModel):
    ticks: dict


class ScheduleIn(BaseModel):
    scheduled_at: str
    channel_id: str
    confirm: bool = False


class PostedIn(BaseModel):
    post_url: str
    confirm: bool = False
    disclosure_toggle_on: bool = False


class ViewsIn(BaseModel):
    window: str
    views: int
    watch_time_s: float | None = None
    source: str = "manual"


class PayoutIn(BaseModel):
    status: str | None = None
    withdrawal_fee_usd: float | None = None
    note: str | None = None
    disputed: bool = False


class SettingIn(BaseModel):
    value: Any
    scope_id: str | None = None


class KillIn(BaseModel):
    active: bool
    reason: str | None = None


class SessionStartIn(BaseModel):
    campaign_id: str | None = None


class SessionStopIn(BaseModel):
    position: dict | None = None


class TimerIn(BaseModel):
    task_type: str = "admin"
    campaign_id: str | None = None
    clip_id: str | None = None
    entry_id: str | None = None
    minutes: float | None = None


class AccountIn(BaseModel):
    platform: str
    handle: str
    niche: str | None = None
    language: str | None = None
    platform_account_id: str | None = None


class LinkIn(BaseModel):
    url: str
    language: str = "en"
    permission_ref: str | None = None
    youtube_permitted: bool = False


class AddListingIn(BaseModel):
    category: str = "podcast"
    licence_ref: str | None = None


class NichesIn(BaseModel):
    niches: list[str]


class AudienceIn(BaseModel):
    tier1_pct: float | None = None
    language: str | None = None
    platform_account_id: str | None = None


class PasskeyIn(BaseModel):
    credential: dict | None = None
    setup_code: str | None = None
    device_name: str | None = None


class MomentsIn(BaseModel):
    moments: list[dict]


class SnoozeIn(BaseModel):
    hours: float = 4


class TranscriptEditIn(BaseModel):
    base_version: int = Field(strict=True, ge=1)
    edits: list[dict]
    confirm_bulk: bool = False


class KeyTermIn(BaseModel):
    term: str


# ------------------------------------------------------------------ home, campaigns

@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/home")
def get_home(actor: Actor = Depends(current_actor)):
    return run(actor, ops.home, actor)


@app.get("/api/counts")
def get_counts(actor: Actor = Depends(current_actor)):
    """The two numbers the navigation badges show. Far smaller and cheaper than /api/home, so every screen can refresh them."""
    with get_db().session(actor) as conn:
        to_review = conn.execute("select count(*) as n from clips where status = 'ready_for_review'").fetchone()["n"]
        return {"to_review": to_review, "alerts": min(len(alerts.inbox(conn)), 5)}


@app.get("/api/progress")
def get_progress(actor: Actor = Depends(current_actor)):
    return run(actor, progress.overview, actor)


@app.get("/api/reference")
def reference(actor: Actor = Depends(current_actor)):
    with get_db().session(actor) as conn:
        return {
            "marketplaces": conn.execute("select slug, name, platforms, source_adapter, scraper_enabled, terms_version, terms_read_at, notes from marketplaces where slug <> %s order by name", (campaigns.PRACTICE_SLUG,)).fetchall(),
            "categories": conn.execute("select name from categories order by name").fetchall(),
            "reason_codes": review.REASON_CODES,
            "accounts": conn.execute(
                """select id, platform, handle, status, status_note, niche, language, platform_account_id, audience_tier1_pct,
                          audience_countries, audience_source, audience_updated_at
                   from accounts order by niche nulls last, platform""").fetchall(),
            "adapters": conn.execute("select stage, provider, plan, active, is_fake, quota_used, quota_limit, cost_month_usd, paused_reason, fallback_provider from adapters order by stage, provider").fetchall(),
        }


@app.post("/api/campaigns")
def post_campaign(body: IntakeIn, actor: Actor = Depends(current_actor)):
    return run(actor, campaigns.intake, actor, body.marketplace, body.name, body.brief_text, body.url, body.category,
               body.deadline, body.licence_ref)


@app.post("/api/campaigns/practice")
def post_practice_campaign(body: PracticeIn, actor: Actor = Depends(current_actor)):
    """One tap: a practice campaign for trying the pipeline on your own video. No marketplace, no money, nothing posted."""
    return run(actor, campaigns.create_practice, actor, body.name, body.niche, body.language)


@app.get("/api/campaigns/{campaign_id}")
def get_campaign(campaign_id: str, actor: Actor = Depends(current_actor)):
    with get_db().session(actor) as conn:
        campaign = campaigns.get_campaign(conn, campaign_id)
        version = conn.execute(
            """select id, version_no, read_at, rules_json, compliance_status, hard_blocks, unclear_fields, rules_confirmed_at,
                      rate_per_1k, fee_pct, budget_total, budget_remaining, min_views_to_qualify, max_payout_per_clip, payout_model
               from campaign_versions where id = %s""", (campaign["current_version_id"],)).fetchone()
        sources = conn.execute("select id, origin_url, duration_s, language, dialect, licence_ref, permission_ref, created_at from source_assets where campaign_id = %s", (campaign_id,)).fetchall()
        return {"campaign": campaign, "version": version, "sources": sources}


@app.post("/api/campaigns/{campaign_id}/confirm-rules")
def confirm_rules(campaign_id: str, actor: Actor = Depends(current_actor)):
    return run(actor, campaigns.confirm_rules, actor, campaign_id)


@app.post("/api/campaigns/{campaign_id}/approve")
def approve_campaign(campaign_id: str, actor: Actor = Depends(current_actor)):
    return run(actor, campaigns.approve, actor, campaign_id)


@app.post("/api/campaigns/{campaign_id}/park")
def park_campaign(campaign_id: str, actor: Actor = Depends(current_actor)):
    return run(actor, campaigns.park, actor, campaign_id)


@app.post("/api/campaigns/{campaign_id}/reject")
def reject_campaign(campaign_id: str, body: ReasonIn, actor: Actor = Depends(current_actor)):
    return run(actor, campaigns.reject, actor, campaign_id, body.reason or "")


@app.post("/api/campaigns/{campaign_id}/recheck")
def recheck(campaign_id: str, body: RecheckIn, actor: Actor = Depends(current_actor)):
    return run(actor, campaigns.recheck_brief, actor, campaign_id, body.current_text, body.unchanged_confirmed)


@app.post("/api/campaigns/{campaign_id}/sources")
def add_source(campaign_id: str, body: SourceIn, actor: Actor = Depends(current_actor)):
    return run(actor, campaigns.register_source, actor, campaign_id, body.file_path, body.language, body.dialect,
               body.licence_ref, body.permission_ref, body.origin_url, body.expected_hash)


@app.post("/api/campaigns/{campaign_id}/key-terms")
def add_key_term(campaign_id: str, body: KeyTermIn, actor: Actor = Depends(current_actor)):
    return {"key_terms": run(actor, transcripts.add_key_term, campaign_id, body.term)}


# ------------------------------------------------------------------ the correction screen (the id is the SOURCE id)

@app.get("/api/transcripts/accuracy")
def transcript_accuracy(actor: Actor = Depends(current_actor)):
    return run(actor, transcripts.accuracy)


@app.get("/api/transcripts/{source_id}")
def get_transcript(source_id: str, actor: Actor = Depends(current_actor)):
    return run(actor, transcripts.detail, source_id)


@app.put("/api/transcripts/{source_id}")
def put_transcript(source_id: str, body: TranscriptEditIn, actor: Actor = Depends(current_actor)):
    return {"version": run(actor, transcripts.update, actor, source_id, body.base_version, body.edits, body.confirm_bulk)}


@app.get("/api/transcripts/{source_id}/review-queue")
def transcript_review_queue(source_id: str, limit: int = transcripts.MAX_QUEUE, actor: Actor = Depends(current_actor)):
    return run(actor, transcripts.queue_for, source_id, max(1, min(limit, 1000)))


@app.post("/api/sources/{source_id}/moments")
def manual_moments(source_id: str, body: MomentsIn, actor: Actor = Depends(current_actor)):
    return {"added": run(actor, pipeline.add_manual_candidates, actor, source_id, body.moments)}


@app.post("/api/accounts")
def add_account(body: AccountIn, actor: Actor = Depends(current_actor)):
    with get_db().session(actor) as conn:
        if body.platform not in ("tiktok", "instagram_reels", "youtube_shorts", "x", "facebook"):
            raise AppError("Unknown platform.", "bad_platform")
        handle = body.handle.strip().lstrip("@")
        if not handle:
            raise AppError("Enter the account handle.", "no_handle")
        if conn.execute("select 1 from accounts where platform = %s and handle = %s", (body.platform, handle)).fetchone():
            raise AppError("That account is already added.", "duplicate")
        language = (body.language or "").strip().lower() or None
        if language and len(language) != 2:
            raise AppError("Language is a two-letter code, such as ar or en.", "bad_language")
        return conn.execute(
            """insert into accounts (platform, handle, niche, language, platform_account_id, owner_operator_id)
               values (%s, %s, %s, %s, %s, %s) returning *""",
            (body.platform, handle, body.niche or None, language, (body.platform_account_id or "").strip() or None,
             actor.operator_id)).fetchone()


# ------------------------------------------------------------------ discovery (Explore)

LISTING_SORTS = {"rank", "newest", "rate", "budget", "ending", "alpha"}


@app.get("/api/listings")
def get_listings(q: str | None = None, platform: str | None = None, niche: str | None = None, board: str | None = None,
                 type: str | None = "clipping", min_rate: float | None = None, min_days_left: float | None = None,
                 open_only: bool = False, fee_known: bool = False, min_grade: str | None = None, show_dismissed: bool = False,
                 pay_model: str | None = None, max_min_views: float | None = None, max_min_payout: float | None = None,
                 max_hold_days: float | None = None, min_budget_left: float | None = None, no_country_rule: bool = False,
                 show_out_of_reach: bool = False, sort: str = "rank", limit: int = 120, actor: Actor = Depends(current_actor)):
    sort = sort if sort in LISTING_SORTS else "rank"
    where, params = ["state = 'live'", "not age_restricted"], []
    if not show_dismissed:
        where.append("""not exists (select 1 from listing_choices lc where lc.listing_id = listings.id
                        and lc.owner_operator_id = app.operator_id() and lc.dismissed_at is not null)""")
    if pay_model:
        where.append("coalesce(pay_model, 'cpm') = %s")
        params.append(pay_model)
    for column, value in (("min_views", max_min_views), ("min_payout", max_min_payout), ("payout_window_days", max_hold_days)):
        if value is not None:   # unknown values stay in: unknown is not the same as too high
            where.append(f"({column} is null or {column} <= %s)")
            params.append(value)
    if min_budget_left is not None:
        where.append("budget_left >= %s")
        params.append(min_budget_left)
    if no_country_rule:
        where.append("coalesce(audience, '') = '' and coalesce(region, '') = '' and coalesce(array_length(audience_countries, 1), 0) = 0")
    if q:
        where.append("(title ilike %s or brand ilike %s or agency ilike %s)")
        params += [f"%{q}%"] * 3
    if platform:
        feed_name = {"instagram_reels": "instagram", "youtube_shorts": "youtube"}.get(platform, platform)
        where.append("%s = any(platforms)")
        params.append(feed_name)
    for column, value in (("niche", niche), ("board_slug", board), ("campaign_type", type)):
        if value:
            where.append(f"{column} = %s")
            params.append(value)
    if min_rate is not None:
        where.append("rate_per_1k >= %s")
        params.append(min_rate)
    if min_days_left is not None:
        where.append("(drain_p50 is null or drain_p50 >= now() + make_interval(days => %s))")
        params.append(int(min_days_left))
    if open_only:
        where.append("open_join is not false")
    if fee_known:
        where.append("board_fee_pct is not null")
    if min_grade:
        where.append("brief_grade is not null and brief_grade <= %s")
        params.append(min_grade.upper())
    order = {"newest": "new_at desc nulls last", "rate": "rate_per_1k desc nulls last", "budget": "budget_left desc nulls last",
             "ending": "drain_p50 asc nulls last", "alpha": "alpha_score desc nulls last", "rank": "alpha_score desc nulls last"}[sort]
    with get_db().session(actor) as conn:
        rows = conn.execute(f"select * from listings where {' and '.join(where)} order by {order} limit %s",
                            (*params, max(1, min(limit, 300)))).fetchall()
        ranked = ranking.rank(conn, rows, actor.operator_id)
        if sort != "rank":
            order_ids = [r["id"] for r in rows]
            ranked.sort(key=lambda r: order_ids.index(r["id"]))
        hidden = 0
        if not show_out_of_reach:
            hidden = sum(1 for r in ranked if r["rank"].get("out_of_reach"))
            ranked = [r for r in ranked if not r["rank"].get("out_of_reach")]
        mine = {r["listing_id"]: r["id"] for r in conn.execute(
            "select listing_id, id from campaigns where listing_id is not null and owner_operator_id = %s", (actor.operator_id,)).fetchall()}
        market = conn.execute("select data, as_of from market_snapshots order by as_of desc limit 1").fetchone()
        last = conn.execute("select max(last_seen_at) as at from listings").fetchone()["at"]
        for row in ranked:
            row.pop("raw", None)
            row.pop("dismissed_at", None)
            row["campaign_id"] = mine.get(row["id"])
        return {"listings": ranked, "market": market, "last_refresh": last, "hidden_out_of_reach": hidden}


@app.get("/api/listings/{listing_id}")
def get_listing(listing_id: str, actor: Actor = Depends(current_actor)):
    with get_db().session(actor) as conn:
        listing = discovery.get(conn, listing_id)
    feed_note = None
    if listing["source"] != "capture" and not (listing.get("raw") or {}).get("brief"):
        try:
            with get_db().session(Actor.worker()) as conn:
                discovery.enrich(conn, listing_id)
        except AppError as e:   # the page still opens with what the feed list gave us
            feed_note = str(e)
    with get_db().session(actor) as conn:
        listing = discovery.get(conn, listing_id)
        ranked = ranking.rank(conn, [listing], actor.operator_id)[0]
        ranked["campaign_id"] = discovery.own_campaign_id(conn, actor.operator_id, listing_id)
        ranked.pop("dismissed_at", None)
        ranked["feed_note"] = feed_note
        raw = ranked.pop("raw", {}) or {}
        brief = raw.get("brief") or {}
        analytics = raw.get("analytics") or {}
        ranked["rules"] = (brief.get("rules") or {}).get("groups") or []
        ranked["wants"] = brief.get("wants") or {}
        ranked["join"] = brief.get("join") or {}
        ranked["verdicts"] = (analytics.get("alpha") or {}).get("verdicts") or []
        ranked["about"] = raw.get("about")
        ranked["categories"] = [c["name"] for c in conn.execute("select name from categories order by name").fetchall()]   # saves the page a second call
        return ranked


@app.post("/api/listings/{listing_id}/add")
def add_listing(listing_id: str, body: AddListingIn, actor: Actor = Depends(current_actor)):
    return run(actor, discovery.add_listing, actor, listing_id, body.category, body.licence_ref)


@app.post("/api/listings/{listing_id}/dismiss")
def dismiss_listing(listing_id: str, undo: bool = False, actor: Actor = Depends(current_actor)):
    run(actor, discovery.dismiss, actor, listing_id, undo)
    return {"ok": True}


@app.post("/api/discovery/refresh")
def refresh_discovery(actor: Actor = Depends(current_actor)):
    """Queue a discovery pass now (the worker runs it)."""
    import time as _time

    with get_db().session(actor) as conn:
        recent = conn.execute(
            """select created_at from jobs where type = 'discover' and created_at > now() - interval '10 minutes'
               order by created_at desc limit 1""").fetchone()
        if recent:
            raise AppError("Marketplaces were checked in the last 10 minutes. Try again a little later.", "too_soon")
        niches = conn.execute("select niches from operators where id = %s", (actor.operator_id,)).fetchone()["niches"]
        job = pipeline.enqueue(conn, "discover", {"manual": int(_time.time() // 60), "niches": niches or []}, actor.operator_id)
    return {"queued": job is not None}


@app.put("/api/me/niches")
def set_niches(body: NichesIn, actor: Actor = Depends(current_actor)):
    from .adapters.discovery import NICHES

    bad = [n for n in body.niches if n not in NICHES]
    if bad:
        raise AppError(f"Unknown niche: {', '.join(bad)}", "bad_niche")
    with get_db().session(actor) as conn:
        conn.execute("update operators set niches = %s where id = %s", (body.niches, actor.operator_id))
    return {"niches": body.niches}


@app.get("/api/me")
def me(actor: Actor = Depends(current_actor)):
    from .adapters.discovery import NICHES

    with get_db().session(actor) as conn:
        row = conn.execute("select id, name, role, niches from operators where id = %s", (actor.operator_id,)).fetchone()
    return dict(row, all_niches=NICHES)


@app.put("/api/accounts/{account_id}/audience")
def set_audience(account_id: str, body: AudienceIn, actor: Actor = Depends(current_actor)):
    if body.tier1_pct is not None and not 0 <= body.tier1_pct <= 100:
        raise AppError("Enter a percentage from 0 to 100.", "bad_value")
    language = (body.language or "").strip().lower() or None
    if language and len(language) != 2:
        raise AppError("Language is a two-letter code, such as ar or en.", "bad_language")
    with get_db().session(actor) as conn:
        row = conn.execute(
            """update accounts set audience_tier1_pct = %s, audience_source = %s, audience_updated_at = now(),
                   language = coalesce(%s, language), platform_account_id = coalesce(%s, platform_account_id)
               where id = %s returning id, audience_tier1_pct, language""",
            (body.tier1_pct, "manual" if body.tier1_pct is not None else None, language,
             (body.platform_account_id or "").strip() or None, account_id),
        ).fetchone()
    if row is None:
        raise AppError("Account not found.", "not_found")
    return row


@app.post("/api/accounts/{account_id}/audience/screenshot")
def audience_screenshot(account_id: str, image: UploadFile = File(...), actor: Actor = Depends(current_actor)):
    """A screenshot of the platform's audience page (TikTok Studio > Viewers, Instagram Insights, YouTube Studio):
    the country percentages are read and the Tier-1 share is worked out."""
    import base64

    from . import audience

    data = image.file.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise AppError("The image is larger than 8 MB.", "too_large")
    mime = image.content_type or "image/png"
    if not mime.startswith("image/"):
        raise AppError("That is not an image.", "not_an_image")
    with get_db().session(actor) as conn:
        result, _ = alerts.registry.call(conn, "llm", "read_image", {"image_b64": base64.b64encode(data).decode(), "mime": mime}, actor.operator_id)
        countries = audience.parse_countries(str((result or {}).get("text") or ""))
        return audience.apply(conn, account_id, countries, "screenshot")


@app.post("/api/accounts/{account_id}/audience/refresh")
def audience_refresh(account_id: str, actor: Actor = Depends(current_actor)):
    from . import audience

    with get_db().session(actor) as conn:
        account = conn.execute("select * from accounts where id = %s", (account_id,)).fetchone()
        if account is None:
            raise AppError("Account not found.", "not_found")
    return run(actor, audience.from_api, account)


# ------------------------------------------------------------------ the creative library: templates and the account persona

def _template_view(conn, template: dict) -> dict:
    """A template as the Profile shows it: its spec fields at the top level, plus id, name, version and the newest versions."""
    return {**template["spec"], "template_id": template["id"], "name": template["name"], "active": template["active"],
            "version": template["version"], "versions": templates.history(conn, template["id"], 5)}


def _account_templates(conn, account_id: str) -> dict:
    return {"templates": [_template_view(conn, t) for t in templates.ensure_set(conn, account_id)], "presets": templates.presets()}


@app.get("/api/accounts/{account_id}/template")
def get_account_template(account_id: str, actor: Actor = Depends(current_actor)):
    """The account's main template (what the Profile edits)."""
    with get_db().session(actor) as conn:
        return _template_view(conn, templates.primary(conn, account_id))


@app.put("/api/accounts/{account_id}/template")
def put_account_template(account_id: str, body: dict[str, Any], actor: Actor = Depends(current_actor)):
    """Edit the main template: only the fields sent change. The next clip of the account uses the new version."""
    with get_db().session(actor) as conn:
        return _template_view(conn, templates.edit_primary(conn, account_id, body))


@app.get("/api/accounts/{account_id}/templates")
def list_account_templates(account_id: str, actor: Actor = Depends(current_actor)):
    with get_db().session(actor) as conn:
        return _account_templates(conn, account_id)


@app.post("/api/accounts/{account_id}/templates")
def add_account_template(account_id: str, body: dict[str, Any], actor: Actor = Depends(current_actor)):
    """Add a template to the account's rotation (`name` plus any spec fields). The next clip uses it."""
    fields = {k: v for k, v in body.items() if k != "name"}
    with get_db().session(actor) as conn:
        return _template_view(conn, templates.add(conn, account_id, body.get("name"), fields))


@app.put("/api/templates/{template_id}")
def put_template(template_id: str, body: dict[str, Any], actor: Actor = Depends(current_actor)):
    """Change one template: spec fields make a new version; `name` and `active` (switch off or on) are plain edits."""
    fields = {k: v for k, v in body.items() if k not in ("name", "active")}
    if "active" in body and not isinstance(body["active"], bool):
        raise AppError("Active must be on or off.", "bad_template")
    with get_db().session(actor) as conn:
        return _template_view(conn, templates.update(conn, template_id, fields, body.get("name"), body.get("active")))


@app.post("/api/templates/{template_id}/revert")
def revert_template(template_id: str, body: dict[str, Any] | None = None, actor: Actor = Depends(current_actor)):
    """Go back to an earlier version (`version`, default the one before) as a new version."""
    version = (body or {}).get("version")
    if version is not None and (isinstance(version, bool) or not isinstance(version, int)):
        raise AppError("Version must be a whole number.", "bad_template")
    with get_db().session(actor) as conn:
        return _template_view(conn, templates.revert(conn, template_id, version))


@app.get("/api/accounts/{account_id}/persona")
def get_persona(account_id: str, actor: Actor = Depends(current_actor)):
    return run(actor, templates.persona, account_id)


# ------------------------------------------------------------------ footage from the phone or a link

@app.post("/api/campaigns/{campaign_id}/sources/upload")
def upload_source(campaign_id: str, file: UploadFile = File(...), language: str = Form("en"), permission_ref: str = Form(""),
                  actor: Actor = Depends(current_actor)):
    path = sources.save_upload(file.file, file.filename)
    return run(actor, sources.register_upload, actor, campaign_id, path, language, permission_ref or None, file.filename)


@app.post("/api/capture/screenshot")
def capture_screenshot(image: UploadFile = File(...), actor: Actor = Depends(current_actor)):
    """Read the text of a campaign screenshot into the add-campaign form. The owner checks it before intake."""
    import base64

    data = image.file.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise AppError("The image is larger than 8 MB.", "too_large")
    mime = image.content_type or "image/png"
    if not mime.startswith("image/"):
        raise AppError("That is not an image.", "not_an_image")
    with get_db().session(actor) as conn:
        result, _ = alerts.registry.call(conn, "llm", "read_image", {"image_b64": base64.b64encode(data).decode(), "mime": mime}, actor.operator_id)
    text = str((result or {}).get("text") or "").strip()
    if not text:
        raise AppError("No text was found in that screenshot.", "no_text")
    return {"text": text[:20000]}


@app.post("/api/campaigns/{campaign_id}/sources/link")
def link_source(campaign_id: str, body: LinkIn, actor: Actor = Depends(current_actor)):
    return run(actor, sources.queue_link, actor, campaign_id, body.url, body.language, body.permission_ref, body.youtube_permitted)


# ------------------------------------------------------------------ passkey sign-in

def _set_session(response: Response, request: Request, token: str) -> None:
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https" or \
        (request.headers.get("origin") or "").startswith("https://")
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=secure, path="/")


@app.get("/api/auth/status")
def auth_status(request: Request):
    with get_db().session(Actor.system()) as conn:
        has = auth.has_passkeys(conn)
    return {"mode": auth.mode(), "signed_in": auth.mode() != "passkey" or signed_in_actor(request) is not None,
            "has_passkeys": has}


@app.post("/api/auth/register/options")
def register_options(body: PasskeyIn, request: Request):
    signed_in = signed_in_actor(request)
    with get_db().session(Actor.system()) as conn:
        return auth.registration_options(conn, request.headers.get("origin"), body.setup_code, signed_in)


@app.post("/api/auth/register/verify")
def register_verify(body: PasskeyIn, request: Request, response: Response):
    signed_in = signed_in_actor(request)
    with get_db().session(Actor.system()) as conn:
        result = auth.verify_registration(conn, request.headers.get("origin"), body.credential or {}, body.setup_code,
                                          signed_in, body.device_name)
        if signed_in is None:
            _set_session(response, request, auth.create_session(conn, result["operator_id"], request.headers.get("user-agent")))
    return {"ok": True}


@app.post("/api/auth/login/options")
def login_options(request: Request):
    with get_db().session(Actor.system()) as conn:
        return auth.login_options(conn, request.headers.get("origin"))


@app.post("/api/auth/login/verify")
def login_verify(body: PasskeyIn, request: Request, response: Response):
    with get_db().session(Actor.system()) as conn:
        token = auth.verify_login(conn, request.headers.get("origin"), body.credential or {})
    _set_session(response, request, token)
    return {"ok": True}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    with get_db().session(Actor.system()) as conn:
        auth.revoke(conn, request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@app.post("/api/auth/device-code")
def device_code(actor: Actor = Depends(current_actor)):
    """A one-time code for adding a passkey on another device (for example the phone)."""
    with get_db().session(Actor.system()) as conn:
        return {"code": auth.new_setup_code(conn, actor.operator_id), "valid_minutes": auth.SETUP_MINUTES}


# ------------------------------------------------------------------ feed and review

@app.get("/api/feed")
def get_feed(campaign_id: str | None = None, actor: Actor = Depends(current_actor)):
    with get_db().session(actor) as conn:
        return {"clips": review.feed(conn, actor.operator_id, campaign_id), "kill_switch": kill_switch(conn)["active"],
                "filtered": moments.filtered_list(conn, campaign_id),
                "undo_window_s": review.get_setting(conn, "undo_window_s", actor.operator_id),
                "session_length_min": review.get_setting(conn, "session_length_min", actor.operator_id),
                "pay_per_clip_display": review.get_setting(conn, "pay_per_clip_display", actor.operator_id)}


@app.post("/api/candidates/{candidate_id}/rescue")
def rescue_moment(candidate_id: str, actor: Actor = Depends(current_actor)):
    """The owner overrides the moment filter: the moment is cut like any other and the rescue teaches the engine."""
    return run(actor, moments.rescue, actor, candidate_id)


class ProfileIn(BaseModel):
    profile: str | None = None


@app.get("/api/profiles")
def list_profiles(actor: Actor = Depends(current_actor)):
    return {"profiles": run(actor, profiles.listing)}


@app.put("/api/sources/{source_id}/profile")
def set_source_profile(source_id: str, body: ProfileIn, actor: Actor = Depends(current_actor)):
    return run(actor, moments.set_profile, actor, source_id, body.profile)


@app.get("/api/clips/{clip_id}")
def get_clip(clip_id: str, actor: Actor = Depends(current_actor)):
    return run(actor, review.get_clip, clip_id)


@app.post("/api/clips/{clip_id}/accept")
def accept(clip_id: str, body: DecisionIn, actor: Actor = Depends(current_actor)):
    return run(actor, review.accept, actor, clip_id, body.session_id, body.client_action_id)


@app.post("/api/clips/{clip_id}/reject")
def reject(clip_id: str, body: DecisionIn, actor: Actor = Depends(current_actor)):
    return run(actor, review.reject, actor, clip_id, body.reason_code or "", body.notes, body.session_id, body.client_action_id)


@app.post("/api/clips/{clip_id}/rejection")
def marketplace_rejection(clip_id: str, body: RejectionIn, actor: Actor = Depends(current_actor)):
    """The marketplace rejected this clip: its reason becomes a check on the brand's next clips (a vague one a reminder)."""
    return run(actor, brand_checks.record_rejection, actor, clip_id, body.reason)


@app.get("/api/brand-checks")
def get_brand_checks(campaign_id: str | None = None, actor: Actor = Depends(current_actor)):
    return {"checks": run(actor, brand_checks.listing, campaign_id)}


@app.post("/api/brand-checks/{check_id}/active")
def switch_brand_check(check_id: str, body: SwitchIn, actor: Actor = Depends(current_actor)):
    return run(actor, brand_checks.switch, actor, check_id, body.active)


@app.post("/api/clips/{clip_id}/later")
def later(clip_id: str, body: DecisionIn, actor: Actor = Depends(current_actor)):
    return run(actor, review.park, actor, clip_id, body.reason, body.session_id, body.client_action_id)


@app.post("/api/reviews/undo")
def undo(body: UndoIn, actor: Actor = Depends(current_actor)):
    return run(actor, review.undo, actor, body.review_id, body.client_action_id)


@app.post("/api/clips/{clip_id}/edit")
def edit(clip_id: str, body: EditIn, actor: Actor = Depends(current_actor)):
    return run(actor, review.edit, actor, clip_id, body.trim, body.hook_id, body.caption)


@app.get("/api/review-later")
def get_review_later(actor: Actor = Depends(current_actor)):
    return {"folders": run(actor, review.review_later)}


@app.post("/api/sessions")
def start_session(body: SessionStartIn, actor: Actor = Depends(current_actor)):
    return run(actor, review.start_session, actor, body.campaign_id)


@app.post("/api/sessions/{session_id}/stop")
def stop_session(session_id: str, body: SessionStopIn, actor: Actor = Depends(current_actor)):
    return run(actor, review.stop_session, actor, session_id, body.position)


@app.post("/api/timer/start")
def timer_start(body: TimerIn, actor: Actor = Depends(current_actor)):
    return run(actor, review.timer_start, actor, body.task_type, body.campaign_id, body.clip_id)


@app.post("/api/timer/stop")
def timer_stop(body: TimerIn, actor: Actor = Depends(current_actor)):
    return run(actor, review.timer_stop, actor, body.entry_id, body.minutes) or {}


# ------------------------------------------------------------------ queue and publishing

@app.get("/api/queue")
def get_queue(actor: Actor = Depends(current_actor)):
    return run(actor, publish.queue)


@app.get("/api/clips/{clip_id}/checklist")
def get_checklist(clip_id: str, actor: Actor = Depends(current_actor)):
    return run(actor, publish.checklist, actor, clip_id)


@app.post("/api/clips/{clip_id}/publish-approve")
def publish_approve(clip_id: str, body: PublishIn, actor: Actor = Depends(current_actor)):
    return run(actor, publish.approve_publish, actor, clip_id, body.ticks)


@app.post("/api/clips/{clip_id}/schedule")
def schedule(clip_id: str, body: ScheduleIn, actor: Actor = Depends(current_actor)):
    return run(actor, publish.schedule, actor, clip_id, body.scheduled_at, body.channel_id, body.confirm)


@app.post("/api/clips/{clip_id}/posted")
def posted(clip_id: str, body: PostedIn, actor: Actor = Depends(current_actor)):
    return run(actor, publish.mark_posted, actor, clip_id, body.post_url, body.confirm, body.disclosure_toggle_on)


@app.post("/api/clips/{clip_id}/back-to-later")
def back_to_later(clip_id: str, body: ReasonIn, actor: Actor = Depends(current_actor)):
    return run(actor, publish.back_to_review_later, actor, clip_id, body.reason)


# ------------------------------------------------------------------ tracking and money

@app.post("/api/posts/{post_id}/views")
def views(post_id: str, body: ViewsIn, actor: Actor = Depends(current_actor)):
    return run(actor, ledger.record_views, actor, post_id, body.window, body.views, body.watch_time_s, body.source)


@app.get("/api/ledger")
def get_ledger(month: str | None = None, actor: Actor = Depends(current_actor)):
    with get_db().session(actor) as conn:
        return {"rows": ledger.ledger(conn, month), "net_per_hour": ledger.net_per_hour(conn),
                "weekly": ledger.weekly_median_net_per_hour(conn), "rejections": ledger.rejection_stats(conn),
                "hooks": ledger.hook_win_rates(conn)}


@app.post("/api/payouts/{payout_id}")
def payout(payout_id: str, body: PayoutIn, actor: Actor = Depends(current_actor)):
    with get_db().session(actor) as conn:
        row = None
        if body.disputed:
            row = ledger.mark_disputed(conn, actor, payout_id, body.note)
        if body.status:
            row = ledger.set_payout_status(conn, actor, payout_id, body.status, body.withdrawal_fee_usd, body.note)
        return row or {}


@app.get("/api/export.csv")
def export(month: str, actor: Actor = Depends(current_actor)):
    return PlainTextResponse(run(actor, ledger.export_csv, month), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="ledger-{month}.csv"'})


# ------------------------------------------------------------------ alerts, settings, kill switch

@app.get("/api/alerts")
def get_alerts(actor: Actor = Depends(current_actor)):
    return {"alerts": run(actor, alerts.inbox)}


@app.post("/api/alerts/{alert_id}/ack")
def ack(alert_id: str, actor: Actor = Depends(current_actor)):
    return run(actor, alerts.acknowledge, alert_id) or {}


@app.post("/api/alerts/{alert_id}/snooze")
def snooze(alert_id: str, body: SnoozeIn, actor: Actor = Depends(current_actor)):
    return run(actor, alerts.snooze, alert_id, body.hours) or {}


@app.get("/api/settings")
def settings(actor: Actor = Depends(current_actor)):
    with get_db().session(actor) as conn:
        return {"settings": get_settings(conn, actor.operator_id), "kill_switch": kill_switch(conn)}


@app.put("/api/settings/{key}")
def put_setting(key: str, body: SettingIn, actor: Actor = Depends(current_actor)):
    return run(actor, set_setting, key, body.value, body.scope_id)


@app.post("/api/kill-switch")
def post_kill_switch(body: KillIn, actor: Actor = Depends(current_actor)):
    with get_db().session(actor) as conn:
        row = set_kill_switch(conn, body.active, body.reason)
        if body.active:
            alerts.raise_alert(conn, actor.operator_id, "kill_switch", "info", None, None,
                               "Kill switch is on. No new jobs; ready-to-post packages are hidden.")
        else:
            conn.execute("update alerts set acknowledged_at = now() where type = 'kill_switch' and acknowledged_at is null")
        return row


# ------------------------------------------------------------------ media (signed, short-lived)

@app.get("/api/media/{key:path}")
def media(key: str, exp: int, sig: str):
    if not vendors.verify_media(key, exp, sig):
        raise HTTPException(403, "This media link has expired.")
    path = vendors.LocalStorage({"config_json": {}}).path(key)
    if not path.is_file():
        raise HTTPException(404, "Not found.")
    return FileResponse(path, media_type="video/mp4" if path.suffix == ".mp4" else None)
