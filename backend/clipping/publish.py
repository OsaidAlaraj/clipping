"""Publishing: the Queue, the publish checklist, gate 3, post targets and the post log (FR-40 to FR-43).
Nothing posts without the owner's tap. One confirm (`confirm_post`) turns an accepted clip into one target per platform, spaced
out on each account's own window (hard rule 4: no blasts). The worker sends each target exactly once (`stage_publish`); a target
whose outcome is unclear becomes needs_check and is never sent again. A `posts` row exists only once a public link does: an API
publish that came back public, or the link the owner pastes for a draft, a private upload or a post made by hand."""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from urllib.parse import urlsplit

import psycopg
from psycopg.types.json import Jsonb

from . import campaigns, config, connections, ffmpeg, review
from .adapters import publishing
from .adapters.oauth import PLATFORM_LABEL, PLATFORMS
from .alerts import raise_alert, registry
from .core import AppError, GateError, KillSwitchOn, audit, enqueue, get_setting, kill_switch_on, require_kill_switch_off
from .db import Database

OWNER_TICKS = ["account_and_platform_correct", "disclosure_toggle_on", "caption_pasted", "hashtags_and_tags_checked"]
SWIPE_TICKS = ["account_and_platform_correct", "caption_pasted", "hashtags_and_tags_checked"]   # what a swipe right stands in for (decision 31); never the disclosure toggle
ENDED = ("cancelled", "failed")                       # a target in one of these no longer holds its platform slot
LINKABLE = ("scheduled", "processing", "draft", "private", "needs_check")
HOSTS = {"youtube_shorts": ("youtube.com", "youtu.be"), "instagram_reels": ("instagram.com", "instagr.am"), "tiktok": ("tiktok.com",)}
STUCK_SENDING_S = 600            # longer than the slowest publish call (Instagram polling is capped near 270 s)
PROCESSING_GIVE_UP_S = 3 * 3600
SUBMIT_WITHIN_MIN = 30           # marketplaces want the public link submitted within 30 minutes of posting
STATUS_EVERY_S = 300


def queue(conn) -> dict:
    """Accepted clips ordered by due time. While the kill switch is on, ready-to-post packages are hidden."""
    killed = kill_switch_on(conn)
    statuses = ["accepted"] if killed else ["accepted", "ready_to_post", "queued"]
    rows = conn.execute(review.CLIP_VIEW + " where cl.status = any(%s) order by cl.due_at nulls last, cl.created_at",
                        (statuses,)).fetchall()
    return {"kill_switch": killed, "items": review._decorate(conn, rows)}


def _posting_window(conn, account: dict) -> dict:
    return account["posting_window"] or get_setting(conn, "posting_window", account["id"])


def cap_warnings(conn, actor, account: dict | None) -> list[str]:
    """Soft caps: warn, do not block (spec 4.3)."""
    warnings: list[str] = []
    if account is None:
        return warnings
    cap = account["daily_cap"] if account["daily_cap"] is not None else int(get_setting(conn, "posts_per_account_per_day", account["id"]))
    today = conn.execute("select count(*) as n from posts where account_id = %s and posted_at::date = now()::date",
                         (account["id"],)).fetchone()["n"]
    if today >= cap:
        warnings.append(f"@{account['handle']} has already posted {today} of {cap} today.")
    operator_cap = int(get_setting(conn, "posts_per_operator_per_day", actor.operator_id))
    mine = conn.execute("select count(*) as n from posts where confirmed_by = %s and posted_at::date = now()::date",
                        (actor.operator_id,)).fetchone()["n"]
    if mine >= operator_cap:
        warnings.append(f"You have already posted {mine} of {operator_cap} today.")
    window = _posting_window(conn, account)
    now = dt.datetime.now().strftime("%H:%M")
    if not (window["start"] <= now <= window["end"]):
        warnings.append(f"It is outside this account's posting window ({window['start']} to {window['end']}).")
    return warnings


def _account_for(conn, actor, clip: dict, campaign: dict, platform: str) -> dict | None:
    """The owner's account for one platform: the clip's own when it is on that platform, else their best active one."""
    if clip["account_id"] and clip["platform"] == platform:
        return conn.execute("select * from accounts where id = %s", (clip["account_id"],)).fetchone()
    niche = conn.execute("select name from categories where id = %s", (campaign["category_id"],)).fetchone()
    from .pipeline import pick_account   # lazy: the pipeline module imports the worker side of publishing

    picked = pick_account(conn, actor.operator_id, [platform], niche["name"] if niche else None)
    return conn.execute("select * from accounts where id = %s", (picked["id"],)).fetchone() if picked else None


def checklist(conn, actor, clip_id, platform: str | None = None) -> dict:
    """FR-40. Server-checked items cannot be ticked by hand. With `platform`, the checklist for that platform's account."""
    clip = review.get_clip(conn, clip_id)
    campaign = campaigns.get_campaign(conn, clip["campaign_id"])
    version = conn.execute("select * from campaign_versions where id = %s", (clip["campaign_version_id"],)).fetchone()
    if platform is None:
        platform = clip["platform"]
        account = conn.execute("select * from accounts where id = %s", (clip["account_id"],)).fetchone() if clip["account_id"] else None
    else:
        account = _account_for(conn, actor, clip, campaign, platform)
    allowed = version["rules_json"].get("platforms_allowed") or []
    account_ok = bool(account and str(account["owner_operator_id"]) == str(actor.operator_id) and account["status"] == "active"
                      and account["platform"] == platform and platform in allowed)
    path = registry.get(conn, "storage").path(clip["file_path"]) if clip["file_path"] else None
    hash_ok = bool(path and path.exists() and clip["approved_file_hash"] and ffmpeg.sha256_file(path) == clip["approved_file_hash"])
    version_ok = campaign["status"] == "approved" and str(campaign["approved_version_id"]) == str(clip["campaign_version_id"])
    brief_ok = version_ok and campaigns.brief_recheck_is_fresh(conn, campaign)
    return {
        "clip": clip,
        "platform": platform,
        "account": account,
        "platform_allowed": platform in allowed,
        "server": {
            "account_owned_and_allowed": {"ok": account_ok, "label": "Your own active account, on a platform the campaign allows"},
            "file_hash_matches_approved": {"ok": hash_ok, "label": "File matches the version you accepted"},
            "brief_rechecked": {"ok": brief_ok, "label": "Brief re-checked and unchanged for this batch"},
        },
        "owner_ticks": OWNER_TICKS,
        "warnings": cap_warnings(conn, actor, account),
        "scheduler_has_disclosure_toggle": bool(registry.get(conn, "scheduler").cfg.get("paid_partnership_toggle")),
        "campaign_needs_reapproval": not version_ok,
        "practice": bool(campaign["is_practice"]),
    }


def _checklist_record(state: dict, ticks: dict, via: str | None = None) -> dict:
    """The record kept with a gate-3 approval. Refuses unless every server check passed and every owner tick is ticked.
    A swipe (`via='swipe'`) stands in for the account, caption and hashtag ticks only; it never claims the disclosure toggle."""
    ticks = ticks if isinstance(ticks, dict) else {}
    needed = SWIPE_TICKS if via == "swipe" else OWNER_TICKS
    failed = [item["label"] for item in state["server"].values() if not item["ok"]]
    missing = [name for name in needed if ticks.get(name) is not True]
    if failed or missing:
        raise AppError("The checklist is not complete: " + "; ".join(failed + [m.replace("_", " ") for m in missing]) + ".",
                       "checklist_incomplete")
    record = {"ticks": {name: True for name in needed}, "server": {k: v["ok"] for k, v in state["server"].items()},
              "warnings": state["warnings"], "file_hash": state["clip"]["file_hash"],
              "approved_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    if via == "swipe":
        record["confirm_via"] = "swipe"
    return record


def approve_publish(conn, actor, clip_id, ticks: dict) -> dict:
    """Gate 3: publish approval. One clip at a time; there is no batch call (hard rule 4)."""
    if actor.kind != "operator":
        raise GateError("Publish approval needs a human.")
    require_kill_switch_off(conn)
    state = checklist(conn, actor, clip_id)
    campaigns.refuse_practice(conn, state["clip"]["campaign_id"], "publishing")
    if state["clip"]["status"] != "accepted":
        raise AppError("Only an accepted clip can be approved for publishing.", "bad_status")
    record = _checklist_record(state, ticks)
    meta = dict(state["clip"]["render_meta"] or {}, checklist=record)
    conn.execute("update clips set status = 'ready_to_post', render_meta = %s where id = %s", (Jsonb(meta), clip_id))
    audit(conn, "gate.publish_approval", "clips", clip_id, {"warnings": len(state["warnings"])},
          rule_version_id=state["clip"]["campaign_version_id"])
    return review.get_clip(conn, clip_id)


def schedule(conn, actor, clip_id, scheduled_at: str, channel_id: str, confirm: bool) -> dict:
    """Hand one publish-approved clip to the scheduler adapter (from week 4). Needs an explicit confirm.
    The paid-partnership toggle is still set by hand unless the scheduler exposes it (O3)."""
    if not confirm:
        raise AppError("Scheduling needs an explicit confirm.", "confirm_required")
    require_kill_switch_off(conn)
    clip = review.get_clip(conn, clip_id)
    campaigns.refuse_practice(conn, clip["campaign_id"], "scheduling")
    if clip["status"] != "ready_to_post":
        raise AppError("Approve the publish checklist first.", "bad_status")
    storage = registry.get(conn, "storage")
    result, _ = registry.call(conn, "scheduler", "schedule", {"post": {
        "channel_id": channel_id, "caption": clip["caption_text"], "scheduled_at": scheduled_at,
        "video_url": storage.get_signed_url(clip["file_path"], 24 * 3600)}}, actor.operator_id)
    if result.get("manual"):
        raise AppError("No scheduler is switched on. Post natively, then mark the clip posted.", "manual_posting")
    meta = dict(clip["render_meta"] or {}, scheduler_ref=result["ref"], scheduled_at=scheduled_at, channel_id=channel_id)
    conn.execute("update clips set status = 'queued', render_meta = %s where id = %s", (Jsonb(meta), clip_id))
    report = registry.get(conn, "scheduler").report()
    limit = report.get("channels_limit")
    if limit and report["channels_used"] >= limit - 1:
        from .alerts import raise_alert

        raise_alert(conn, actor.operator_id, "scheduler_channels", "warn", None, None,
                    f"The scheduler is using {report['channels_used']} of {limit} channels.")
    return review.get_clip(conn, clip_id)


def mark_posted(conn, actor, clip_id, post_url: str, confirm: bool, disclosure_toggle_on: bool) -> dict:
    """The owner records the post link. Needs the link and an explicit confirm. Metric tracking starts."""
    if actor.kind != "operator":
        raise GateError("Posts are recorded by the owner.")
    if not confirm:
        raise AppError("Marking a clip posted needs an explicit confirm.", "confirm_required")
    if not post_url or not post_url.strip().lower().startswith(("http://", "https://")):
        raise AppError("Paste the link to the post.", "link_required")
    if not disclosure_toggle_on:
        raise AppError("Switch the paid-partnership toggle on in the platform app, then confirm it here.", "toggle_required")
    require_kill_switch_off(conn)
    clip = review.get_clip(conn, clip_id)
    campaigns.refuse_practice(conn, clip["campaign_id"], "marking a clip posted")
    if clip["status"] not in ("ready_to_post", "queued"):
        raise AppError("Approve the publish checklist first.", "bad_status")
    if conn.execute("select 1 from post_targets where clip_id = %s and platform = %s and state <> all(%s)",
                    (clip_id, clip["platform"], list(ENDED))).fetchone():
        raise AppError("This clip is already being posted to that platform. Paste the link on its post card instead.", "target_exists")
    meta = clip["render_meta"] or {}
    post = conn.execute(
        """insert into posts (clip_id, owner_operator_id, account_id, platform, post_url, disclosure_toggle_on, checklist_json,
               scheduler_ref, confirmed_by, state, privacy, submitted_at)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'published', 'public', now()) returning *""",
        (clip_id, actor.operator_id, clip["account_id"], clip["platform"], post_url.strip(), True,
         Jsonb(meta.get("checklist") or {}), meta.get("scheduler_ref"), actor.operator_id),
    ).fetchone()
    conn.execute("update clips set status = 'posted' where id = %s", (clip_id,))
    conn.execute(
        "insert into payouts (clip_id, owner_operator_id, campaign_version_id) values (%s, %s, %s)",
        (clip_id, actor.operator_id, clip["campaign_version_id"]),
    )
    audit(conn, "post.recorded", "posts", post["id"], {"platform": clip["platform"]}, rule_version_id=clip["campaign_version_id"])
    return post


def back_to_review_later(conn, actor, clip_id, reason: str | None = None) -> dict:
    return review.park(conn, actor, clip_id, reason or "Moved back from the Queue")


# ====================================================================== post targets (one tap, several platforms)

def _now() -> dt.datetime:
    """Local, timezone-aware 'now': posting windows are local clock times. A seam for tests."""
    return dt.datetime.now().astimezone()


def _id(value, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise AppError(f"That {what} was not found.", "not_found") from None


def _clock(text) -> dt.time:
    try:
        return dt.datetime.strptime(str(text), "%H:%M").time()
    except ValueError:
        raise AppError("The posting window is not valid. Use hours and minutes like 09:00.", "bad_window") from None


def next_slot(window: dict, earliest: dt.datetime) -> dt.datetime:
    """The first moment at or after `earliest` that falls inside the window (local clock times; start > end wraps past midnight)."""
    start, end = _clock((window or {}).get("start")), _clock((window or {}).get("end"))
    now = earliest.timetz().replace(tzinfo=None)
    if (start <= now <= end) if start <= end else (now >= start or now <= end):
        return earliest
    slot = earliest.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    return slot if slot > earliest else slot + dt.timedelta(days=1)


def plan_times(conn, actor, accounts: list[dict], now: dt.datetime) -> list[dt.datetime]:
    """One time per account, in order: each inside its own account's window and at least `post_stagger_min` after the one before."""
    gap = dt.timedelta(minutes=int(get_setting(conn, "post_stagger_min", actor.operator_id)))
    times: list[dt.datetime] = []
    for account in accounts:
        earliest = now if not times else times[-1] + gap
        times.append(next_slot(_posting_window(conn, account), earliest))
    return times


def _view(row: dict) -> dict:
    return {"id": str(row["id"]), "clip_id": str(row["clip_id"]), "platform": row["platform"], "state": row["state"],
            "scheduled_at": row["scheduled_at"], "url": row["url"], "reason": row["reason"], "manual": row["manual"],
            "account_id": str(row["account_id"]), "privacy": row["privacy"], "submit_by": row.get("submit_by")}


def post_options(conn, actor, clip_id) -> dict:
    """What the post screen shows: per platform, the account, its connection, the checklist and any warnings."""
    clip_id = _id(clip_id, "clip")
    held = {r["platform"]: r["state"] for r in conn.execute(
        "select platform, state from post_targets where clip_id = %s and state <> all(%s)", (clip_id, list(ENDED))).fetchall()}
    platforms = []
    for platform in PLATFORMS:
        state = checklist(conn, actor, clip_id, platform)
        account = state["account"]
        connection = connections.for_account(conn, account["id"]) if account else None
        usable = connection is not None and connection["state"] != "needs_reconnect"
        if not state["platform_allowed"]:
            blocked = "The campaign does not allow this platform."
        elif account is None:
            blocked = f"You have no active {PLATFORM_LABEL[platform]} account."
        elif platform in held:
            blocked = "This clip is already going to this platform."
        else:
            blocked = None
        platforms.append({
            "platform": platform, "label": PLATFORM_LABEL[platform],
            "account": {"id": str(account["id"]), "handle": account["handle"]} if account else None,
            "connection_state": connection["state"] if connection else "not_connected",
            "allowed": blocked is None, "blocked_reason": blocked, "manual_only": not usable, "existing_target": held.get(platform),
            "checklist": {"server": state["server"], "owner_ticks": state["owner_ticks"]}, "warnings": state["warnings"]})
    return {"platforms": platforms}


_TARGET_WITH_DEADLINE = """select t.*, p.posted_at + make_interval(mins => %s) as submit_by
                           from post_targets t left join posts p on p.target_id = t.id"""


def _targets_of(conn, clip_id, request_id: str | None = None) -> list[dict]:
    """A clip's targets, oldest first. `submit_by` is set once the public link exists: the end of the marketplace's 30-minute window."""
    rows = conn.execute(_TARGET_WITH_DEADLINE + " where t.clip_id = %s and (%s::text is null or t.request_id = %s) order by t.scheduled_at, t.created_at",
                        (SUBMIT_WITHIN_MIN, clip_id, request_id, request_id)).fetchall()
    return [_view(r) for r in rows]


def post_status(conn, clip_id) -> dict:
    return {"targets": _targets_of(conn, _id(clip_id, "clip"))}


def _idempotency_key(clip_id, platform: str, request_id: str) -> str:
    from .core import stable_hash

    return stable_hash([str(clip_id), platform, request_id])


def confirm_post(conn, actor, clip_id, platforms, ticks, request_id, via: str | None = None) -> dict:
    """Gate 3 for several platforms at once: ONE owner tap queues one post per platform, spaced out on each account's own window.
    All or nothing. A replayed `request_id` returns the targets it already made and creates none."""
    if actor.kind != "operator" or actor.role not in ("admin", "operator"):
        raise GateError("Posting needs the owner's own confirm.")
    clip_id = _id(clip_id, "clip")
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 100:
        raise AppError("The request needs an id so a double tap cannot post twice.", "request_id_required")
    request_id = request_id.strip()
    if (replayed := _targets_of(conn, clip_id, request_id)):
        return {"targets": replayed}
    if (not isinstance(platforms, list) or not platforms or any(p not in PLATFORMS for p in platforms)
            or len(set(platforms)) != len(platforms)):
        raise AppError("Choose each platform once: YouTube, Instagram or TikTok.", "bad_platforms")
    if not isinstance(ticks, dict):
        raise AppError("The checklist ticks are missing.", "checklist_incomplete")
    require_kill_switch_off(conn)
    clip = review.get_clip(conn, clip_id)
    campaigns.refuse_practice(conn, clip["campaign_id"], "posting")
    if clip["status"] not in ("accepted", "ready_to_post", "queued", "posted"):
        raise AppError("Accept the clip before posting it.", "bad_status")
    taken = [r["platform"] for r in conn.execute("select platform from post_targets where clip_id = %s and platform = any(%s) and state <> all(%s)",
                                                 (clip_id, platforms, list(ENDED))).fetchall()]
    if taken:
        raise AppError(f"This clip is already going to {', '.join(PLATFORM_LABEL[p] for p in taken)}. One account per platform.", "already_targeted")
    states, records = {}, {}
    for platform in platforms:
        states[platform] = checklist(conn, actor, clip_id, platform)
        if not states[platform]["platform_allowed"]:
            raise AppError(f"This campaign does not allow {PLATFORM_LABEL[platform]} posts.", "platform_not_allowed")
        try:
            records[platform] = _checklist_record(states[platform], ticks.get(platform), via)
        except AppError as exc:
            raise AppError(f"{PLATFORM_LABEL[platform]}: {exc}", exc.code) from None
    times = plan_times(conn, actor, [states[p]["account"] for p in platforms], _now())
    if clip["status"] == "accepted":
        meta = dict(clip["render_meta"] or {}, checklist=records[platforms[0]])
        conn.execute("update clips set status = 'ready_to_post', render_meta = %s where id = %s", (Jsonb(meta), clip_id))
        audit(conn, "gate.publish_approval", "clips", clip_id, {"platforms": platforms}, rule_version_id=clip["campaign_version_id"])
    made = []
    for platform, when in zip(platforms, times):
        account = states[platform]["account"]
        connection = connections.for_account(conn, account["id"])
        manual = connection is None or connection["state"] == "needs_reconnect"
        row = conn.execute(
            """insert into post_targets (clip_id, platform, account_id, connection_id, manual, scheduled_at, idempotency_key, request_id,
                   checklist_json, confirmed_by)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict do nothing returning *""",
            (clip_id, platform, account["id"], None if manual else connection["id"], manual, when,
             _idempotency_key(clip_id, platform, request_id), request_id, Jsonb(records[platform]), actor.operator_id)).fetchone()
        if row is None:     # a concurrent tap won (same id, or the same platform): never add a second
            raise AppError("This clip is already being posted. Check its post status.", "already_targeted")
        made.append(row)
        if not manual:
            enqueue(conn, "publish", {"target_id": str(row["id"])}, actor.operator_id, campaign_version_id=clip["campaign_version_id"], run_after=when)
        audit(conn, "post.confirmed", "post_targets", row["id"], {"platform": platform, "manual": manual, **({"via": via} if via else {})},
              rule_version_id=clip["campaign_version_id"])
    return {"targets": [_view(r) for r in made]}


def _compliance_warning(clip: dict) -> str | None:
    """One plain sentence when compliance says "review" (a warning, not a block); None when it passes."""
    if clip["compliance_status"] != "review":
        return None
    report = clip.get("compliance_report") or {}
    names = [str(c.get("check", "")).replace("_", " ") for c in report.get("deterministic", []) if c.get("status") != "pass"]
    names += [str(r.get("rule", ""))[:60] for r in report.get("rules", []) if r.get("status") != "pass"]
    return "Compliance needs a look" + (": " + ", ".join(n for n in names if n) if any(names) else "") + "."


def swipe_confirm(conn, actor, clip_id, request_id, session_id=None, client_action_id=None) -> dict:
    """Decision 31: a swipe right in the review feed accepts the clip AND is the owner's confirm to post, one tap for every platform
    the clip can go to. It stands in for the account, caption and hashtag ticks (the card showed them); the disclosure toggle is never
    part of it. A clip that failed compliance does not post (the screen opens the report). A practice campaign accepts the clip and
    queues nothing. Everything is one transaction: a refusal anywhere leaves the clip in the feed, not accepted."""
    if actor.kind != "operator" or actor.role not in ("admin", "operator"):
        raise GateError("Posting needs the owner's own confirm.")
    clip_id = _id(clip_id, "clip")
    with conn.transaction():
        if request_id and (replayed := _targets_of(conn, clip_id, request_id)):          # a repeated tap: the same answer, nothing new
            first = conn.execute("select * from reviews where clip_id = %s and decision = 'accept' order by created_at desc limit 1", (clip_id,)).fetchone()
            handles = {str(a["id"]): a["handle"] for a in conn.execute("select id, handle from accounts where id = any(%s)", ([t["account_id"] for t in replayed],)).fetchall()}
            return {"review": first, "clip_id": clip_id, "practice": False, "warning": None, "skipped": [],
                    "targets": [{**t, "label": PLATFORM_LABEL[t["platform"]], "handle": handles.get(t["account_id"])} for t in replayed]}
        clip = review.get_clip(conn, clip_id)
        if clip["compliance_status"] not in ("pass", "review"):
            raise AppError("This clip did not pass the compliance check. Open the report before posting.", "compliance_blocked")
        if clip["status"] not in ("ready_for_review", "accepted"):
            raise AppError(f"A clip that is {clip['status']} cannot be posted from a swipe.", "bad_status")
        decision = review.accept(conn, actor, clip_id, session_id, client_action_id) if clip["status"] == "ready_for_review" else None
        base = {"review": decision, "clip_id": clip_id, "practice": bool(clip["is_practice"]), "warning": _compliance_warning(clip),
                "targets": [], "skipped": []}
        if clip["is_practice"]:
            return base                                  # accepted, never queued: the existing refusal stays in force
        require_kill_switch_off(conn)
        options = post_options(conn, actor, clip_id)["platforms"]
        chosen = [o["platform"] for o in options if o["allowed"]]
        base["skipped"] = [{"platform": o["platform"], "label": o["label"], "reason": o["blocked_reason"]} for o in options if not o["allowed"]]
        if not chosen:
            return base
        made = confirm_post(conn, actor, clip_id, chosen, {p: {t: True for t in SWIPE_TICKS} for p in chosen},
                            request_id or client_action_id, via="swipe")["targets"]
        handles = {str(a["id"]): a["handle"] for a in conn.execute("select id, handle from accounts where id = any(%s)",
                                                                 ([t["account_id"] for t in made],)).fetchall()}
        base["targets"] = [{**t, "label": PLATFORM_LABEL[t["platform"]], "handle": handles.get(t["account_id"])} for t in made]
        from .core import stable_hash

        audit(conn, "post.swipe_confirmed", "clips", clip_id,
              {"via": "swipe", "caption_hash": stable_hash(clip["caption_text"] or ""), "at": dt.datetime.now(dt.timezone.utc).isoformat(),
               "targets": [{"platform": t["platform"], "manual": t["manual"]} for t in made]},
              rule_version_id=clip["campaign_version_id"])
        return base


def swipe_undo(conn, actor, clip_id, review_id) -> dict:
    """Undo a swipe inside the Undo window: its queued posts are cancelled and the clip goes back to the feed as it was.
    Refused once the window has passed or any post has started sending."""
    if actor.kind != "operator" or actor.role not in ("admin", "operator"):
        raise GateError("Only the owner can do this.")
    clip_id = _id(clip_id, "clip")
    with conn.transaction():
        targets = conn.execute("select * from post_targets where clip_id = %s and state <> all(%s) for update", (clip_id, list(ENDED))).fetchall()
        busy = [t for t in targets if t["state"] != "scheduled"]
        if busy:
            raise AppError("A post has already started sending, so this cannot be undone. Check the platform.", "not_cancellable")
        for target in targets:
            conn.execute("update post_targets set state = 'cancelled', reason = 'You undid the swipe.' where id = %s", (target["id"],))
            audit(conn, "post.cancelled", "post_targets", target["id"], {"platform": target["platform"], "was": "scheduled", "via": "swipe_undo"})
        conn.execute("update clips set status = 'accepted' where id = %s and status in ('ready_to_post', 'queued')", (clip_id,))
        undone = review.undo(conn, actor, review_id)
    return {"review": undone, "cancelled": len(targets)}


def _owned_target(conn, actor, target_id) -> dict:
    if actor.kind != "operator" or actor.role not in ("admin", "operator"):
        raise GateError("Only the owner can do this.")
    row = conn.execute("select * from post_targets where id = %s for update", (_id(target_id, "post"),)).fetchone()
    if row is None or str(row["owner_operator_id"]) != str(actor.operator_id):
        raise AppError("That post was not found.", "not_found")
    return row


def cancel_target(conn, actor, target_id) -> dict:
    """Stop a post that has not started sending (or clear one the owner found never went out)."""
    target = _owned_target(conn, actor, target_id)
    if target["state"] not in ("scheduled", "needs_check"):
        raise AppError("It has already been sent, so it cannot be cancelled." if target["state"] in ("sending", "processing", "published")
                       else "That post is already finished.", "not_cancellable")
    row = conn.execute("update post_targets set state = 'cancelled', reason = %s where id = %s returning *",
                       ("You cancelled it." if target["state"] == "scheduled" else "You checked and it did not post.", target["id"])).fetchone()
    audit(conn, "post.cancelled", "post_targets", row["id"], {"platform": row["platform"], "was": target["state"]})
    return _view(row)


def _link_ok(platform: str, url) -> str:
    parts = urlsplit(url.strip()) if isinstance(url, str) else None
    host = (parts.hostname or "").lower() if parts else ""
    if not parts or parts.scheme not in ("http", "https") or not any(host == h or host.endswith("." + h) for h in HOSTS[platform]):
        raise AppError(f"Paste the link to the {PLATFORM_LABEL[platform]} post.", "link_required")
    return url.strip()


def paste_link(conn, actor, target_id, url) -> dict:
    """The owner pastes the public link for a draft, a private upload, a manual post or one that needed a check. Records the post."""
    target = _owned_target(conn, actor, target_id)
    url = _link_ok(target["platform"], url)
    if target["state"] == "published":
        if target["url"] == url:
            return _view(target)                  # a replayed paste
        raise AppError("That post already has its link.", "already_published")
    if target["state"] not in LINKABLE:
        raise AppError("That post has no link to add yet.", "not_linkable")
    require_kill_switch_off(conn)
    _publish(conn, target, url, target["platform_post_id"])
    return _view(conn.execute(_TARGET_WITH_DEADLINE + " where t.id = %s", (SUBMIT_WITHIN_MIN, target["id"])).fetchone())


# ------------------------------------------------------------------ recording a public post

def _publish(conn, target: dict, url: str, post_id: str | None) -> dict:
    """target -> published, then the post row, the clip and the payout row. One step: a link exists, so a post exists."""
    row = conn.execute("update post_targets set state = 'published', url = %s, platform_post_id = coalesce(%s, platform_post_id), "
                       "privacy = 'public', reason = null where id = %s returning *", (url, post_id, target["id"])).fetchone()
    clip = conn.execute("select campaign_version_id from clips where id = %s", (row["clip_id"],)).fetchone()
    post = conn.execute(
        """insert into posts (clip_id, owner_operator_id, account_id, platform, post_url, disclosure_toggle_on, checklist_json, confirmed_by,
               platform_post_id, state, privacy, submitted_at, target_id)
           values (%s, %s, %s, %s, %s, true, %s, %s, %s, 'published', 'public', coalesce(%s, now()), %s) returning id""",
        (row["clip_id"], row["owner_operator_id"], row["account_id"], row["platform"], url, Jsonb(row["checklist_json"]),
         row["confirmed_by"], row["platform_post_id"], row["sent_at"], row["id"])).fetchone()
    conn.execute("update clips set status = 'posted' where id = %s and status in ('ready_to_post', 'queued')", (row["clip_id"],))
    conn.execute("insert into payouts (clip_id, owner_operator_id, campaign_version_id) values (%s, %s, %s) on conflict (clip_id) do nothing",
                 (row["clip_id"], row["owner_operator_id"], clip["campaign_version_id"]))
    audit(conn, "post.recorded", "posts", post["id"], {"platform": row["platform"], "target": str(row["id"])}, rule_version_id=clip["campaign_version_id"])
    return row


# ------------------------------------------------------------------ the worker side

ZERO = Decimal("0")
STATES = {"published", "draft", "private", "processing", "needs_check", "failed"}


FINISH_ALERT = "finish_in_app"


def finish_message(platform: str, state: str) -> str:
    """The reminder for a draft or private upload: it earns nothing until the owner makes it public and pastes the link."""
    what = "is saved as a draft" if state == "draft" else "was uploaded privately"
    return f"Your {PLATFORM_LABEL[platform]} post {what}. Finish it in the app, then paste the public link here."[:300]


def _end(conn, target: dict, state: str, reason: str, alert: bool = True) -> None:
    """Close a target that never went out (cancelled) with a reason the owner can read."""
    conn.execute("update post_targets set state = %s, reason = %s where id = %s", (state, reason[:300], target["id"]))
    if alert:
        raise_alert(conn, target["owner_operator_id"], f"post_{state}", "warn", "post_targets", target["id"],
                    f"{PLATFORM_LABEL[target['platform']]} post {state}: {reason}"[:300])


def _clean_outcome(result) -> dict:
    """The adapter's answer is untrusted: keep only well-formed fields, and turn anything odd into needs_check (never a guess)."""
    good = isinstance(result, dict) and result.get("state") in STATES
    out = {"state": result["state"] if good else "needs_check", "post_id": None, "url": None,
           "reason": None if good else "We could not read the platform's answer. Check the platform before trying again."}
    if isinstance(result, dict):
        pid, url, why = result.get("platform_post_id"), result.get("url"), result.get("reason")
        out["post_id"] = pid if isinstance(pid, str) and 0 < len(pid) <= 128 else None
        out["url"] = url if isinstance(url, str) and url.lower().startswith(("http://", "https://")) and len(url) <= 500 else None
        out["reason"] = config.redact(why)[:300] if isinstance(why, str) and why else out["reason"]
    if out["state"] == "published" and out["url"] is None:
        out.update(state="needs_check", reason="It posted, but the platform gave no link. Paste the post link to record it.")
    return out


def _apply(conn, target: dict, result) -> None:
    """Record one outcome on the target. A public link also writes the post (see _publish); nothing else does."""
    out = _clean_outcome(result)
    state = out["state"]
    if state == "published":
        try:
            with conn.transaction():
                _publish(conn, target, out["url"], out["post_id"])
            return
        except psycopg.Error:
            state, out["reason"] = "needs_check", "It posted, but we could not record it. Paste the post link to record it."
    conn.execute("update post_targets set state = %s, platform_post_id = coalesce(%s, platform_post_id), reason = %s, privacy = %s where id = %s",
                 (state, out["post_id"], out["reason"], "private" if state == "private" else None, target["id"]))
    if state in ("needs_check", "failed"):
        raise_alert(conn, target["owner_operator_id"], f"post_{state}", "warn", "post_targets", target["id"],
                    f"{PLATFORM_LABEL[target['platform']]} post: {out['reason'] or 'see the post card'}"[:300])
    if state in ("draft", "private"):
        raise_alert(conn, target["owner_operator_id"], FINISH_ALERT, "warn", "post_targets", target["id"],
                    finish_message(target["platform"], state))
    if state == "processing":
        try:
            enqueue(conn, "post_status", {"target_id": str(target["id"]), "round": 0}, target["owner_operator_id"],
                    run_after=_now() + dt.timedelta(seconds=60))
        except KillSwitchOn:
            pass      # the sweep queues the next check once the switch is off


def _send_blocker(conn, target: dict) -> str | None:
    """Why a scheduled target must not go out now (nothing is sent), or None."""
    row = conn.execute(
        """select cl.file_path, cl.approved_file_hash, cl.campaign_version_id, c.status as campaign_status, c.approved_version_id, c.is_practice,
                  a.status as account_status
           from clips cl join campaigns c on c.id = cl.campaign_id join accounts a on a.id = %s where cl.id = %s""",
        (target["account_id"], target["clip_id"])).fetchone()
    if kill_switch_on(conn):
        return KILLED
    if row["is_practice"]:
        return "Practice campaigns are never posted."
    if target["connection_id"] is None:
        return "The account was disconnected. Post by hand and paste the link."
    if row["account_status"] != "active":
        return f"The account is {row['account_status']}."
    if not (row["campaign_status"] == "approved" and str(row["approved_version_id"]) == str(row["campaign_version_id"])):
        return "The brief changed since you approved this clip. Approve it again before posting."
    path = registry.get(conn, "storage").path(row["file_path"]) if row["file_path"] else None
    if not (path and path.exists() and row["approved_file_hash"] and ffmpeg.sha256_file(path) == row["approved_file_hash"]):
        return "The video file is not the version you accepted."
    return None


FINISH_CLAIMED = 2        # attempts: 1 for the upload, 2 while one finish_publish is in flight. A third means the second never came back.
KILLED = "The kill switch was on."
WORKER_STOPPED = "The worker stopped while sending. Check the platform before trying again."


def stage_publish(conn, job: dict) -> Decimal:
    """Send one confirmed target, at most once. The target is marked `sending` and committed BEFORE any byte leaves, so a worker
    that dies mid-send leaves it `sending` (the sweep turns that into needs_check) and nothing ever sends it a second time."""
    target = conn.execute("select * from post_targets where id = %s", (job["payload"]["target_id"],)).fetchone()
    if target is None or target["manual"]:
        return ZERO
    if target["state"] == "sending":
        if job["attempts"] > 1:     # this job already ran and did not finish: the platform may have the video
            _apply(conn, target, {"state": "needs_check", "reason": WORKER_STOPPED})
        return ZERO
    if target["state"] != "scheduled":
        return ZERO                 # already sent, cancelled or finished: running the job again does nothing
    if (blocker := _send_blocker(conn, target)):
        _end(conn, target, "cancelled", blocker, alert=blocker != KILLED)
        return ZERO
    claimed = conn.execute("update post_targets set state = 'sending', attempts = attempts + 1, sent_at = now(), reason = null "
                           "where id = %s and state = 'scheduled' returning *", (target["id"],)).fetchone()
    if claimed is None:
        return ZERO                 # cancelled (or taken) a moment ago
    Database.checkpoint(conn)       # `sending` is now durable
    clip = conn.execute("select file_path, caption_text from clips where id = %s", (target["clip_id"],)).fetchone()
    request = {"connection_id": target["connection_id"], "platform": target["platform"],
               "file_path": str(registry.get(conn, "storage").path(clip["file_path"])), "caption": clip["caption_text"] or "",
               "options": {"privacy": "public", "paid_partnership": True}}
    cost = ZERO
    try:
        result, cost = registry.call(conn, "publishing", "publish_video", request, target["owner_operator_id"], provider=publishing.provider_name())
    except AppError as exc:        # refused before anything was sent (not connected, sign-in expired, adapter paused)
        result = {"state": "failed", "reason": str(exc)}
    except Exception as exc:       # unknown: the upload may have started, so never guess
        result = {"state": "needs_check", "reason": f"An unexpected error ({type(exc).__name__}) stopped the post. Check the platform before trying again."}
    _apply(conn, claimed, result)
    return cost


def stage_post_status(conn, job: dict) -> Decimal:
    """Ask the platform about a target still `processing`. Instagram is also published once its video is ready (finish_publish).
    Single-flight: a claim stamp stops two checks from running the Instagram publish step together."""
    target = conn.execute("update post_targets set checked_at = now(), attempts = attempts + 1 where id = %s and state = 'processing' "
                          "and (checked_at is null or checked_at < now() - make_interval(secs => %s)) returning *",
                          (job["payload"]["target_id"], STUCK_SENDING_S)).fetchone()
    if target is None or not target["platform_post_id"] or target["connection_id"] is None:
        return ZERO
    if target["platform"] == "instagram_reels" and target["attempts"] > FINISH_CLAIMED:
        # An earlier finish_publish started and never reported back (the worker died): Instagram may have published it already.
        _apply(conn, target, {"state": "needs_check", "reason": WORKER_STOPPED})
        return ZERO
    Database.checkpoint(conn)       # the claim is durable before the platform is called
    method = "finish_publish" if target["platform"] == "instagram_reels" else "check_status"
    request = {"connection_id": target["connection_id"], "platform": target["platform"], "platform_post_id": target["platform_post_id"]}
    cost = ZERO
    try:
        result, cost = registry.call(conn, "publishing", method, request, target["owner_operator_id"], provider=publishing.provider_name())
    except AppError as exc:
        result = {"state": "needs_check", "reason": f"Could not check this post: {exc}"[:300]}
    except Exception as exc:
        result = {"state": "needs_check", "reason": f"Could not check this post ({type(exc).__name__}). Check the platform."}
    if isinstance(result, dict) and result.get("state") == "processing":
        conn.execute("update post_targets set checked_at = null, attempts = %s where id = %s", (FINISH_CLAIMED - 1, target["id"]))   # the sweep asks again later
        return cost
    _apply(conn, target, result)
    return cost


def sweep(conn, stuck_after_s: int = STUCK_SENDING_S) -> int:
    """Housekeeping. A target `sending` for longer than any publish call takes means its worker died: needs_check, never resent.
    A target `processing` too long gives up; the others get their next status check queued. Returns the targets it changed."""
    changed = 0
    for row in conn.execute("select * from post_targets where state = 'sending' and sent_at < now() - make_interval(secs => %s) for update skip locked",
                            (stuck_after_s,)).fetchall():
        _apply(conn, row, {"state": "needs_check", "reason": WORKER_STOPPED})
        changed += 1
    for row in conn.execute("select * from post_targets where state = 'processing' for update skip locked").fetchall():
        if (_now() - row["sent_at"]).total_seconds() > PROCESSING_GIVE_UP_S:
            _apply(conn, row, {"state": "needs_check", "reason": "The platform is still processing this after hours. Check it there."})
            changed += 1
            continue
        try:
            enqueue(conn, "post_status", {"target_id": str(row["id"]), "round": int(_now().timestamp() // STATUS_EVERY_S)},
                    row["owner_operator_id"])
        except KillSwitchOn:
            break
    return changed
