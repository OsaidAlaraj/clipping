"""Publishing: the Queue, the publish checklist, gate 3 and the post log (FR-40 to FR-43).
Nothing here posts on its own. The owner posts natively (or confirms one scheduled post), then records the link."""
from __future__ import annotations

import datetime as dt

from psycopg.types.json import Jsonb

from . import campaigns, ffmpeg, review
from .alerts import registry
from .core import AppError, GateError, audit, get_setting, kill_switch_on, require_kill_switch_off

OWNER_TICKS = ["account_and_platform_correct", "disclosure_toggle_on", "caption_pasted", "hashtags_and_tags_checked"]


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


def checklist(conn, actor, clip_id) -> dict:
    """FR-40. Server-checked items cannot be ticked by hand."""
    clip = review.get_clip(conn, clip_id)
    campaign = campaigns.get_campaign(conn, clip["campaign_id"])
    version = conn.execute("select * from campaign_versions where id = %s", (clip["campaign_version_id"],)).fetchone()
    account = conn.execute("select * from accounts where id = %s", (clip["account_id"],)).fetchone() if clip["account_id"] else None
    allowed = version["rules_json"].get("platforms_allowed") or []
    account_ok = bool(account and str(account["owner_operator_id"]) == str(actor.operator_id) and account["status"] == "active"
                      and account["platform"] == clip["platform"] and clip["platform"] in allowed)
    path = registry.get(conn, "storage").path(clip["file_path"]) if clip["file_path"] else None
    hash_ok = bool(path and path.exists() and clip["approved_file_hash"] and ffmpeg.sha256_file(path) == clip["approved_file_hash"])
    version_ok = campaign["status"] == "approved" and str(campaign["approved_version_id"]) == str(clip["campaign_version_id"])
    brief_ok = version_ok and campaigns.brief_recheck_is_fresh(conn, campaign)
    return {
        "clip": clip,
        "server": {
            "account_owned_and_allowed": {"ok": account_ok, "label": "Your own active account, on a platform the campaign allows"},
            "file_hash_matches_approved": {"ok": hash_ok, "label": "File matches the version you accepted"},
            "brief_rechecked": {"ok": brief_ok, "label": "Brief re-checked and unchanged for this batch"},
        },
        "owner_ticks": OWNER_TICKS,
        "warnings": cap_warnings(conn, actor, account),
        "scheduler_has_disclosure_toggle": bool(registry.get(conn, "scheduler").cfg.get("paid_partnership_toggle")),
        "campaign_needs_reapproval": not version_ok,
    }


def approve_publish(conn, actor, clip_id, ticks: dict) -> dict:
    """Gate 3: publish approval. One clip at a time; there is no batch call (hard rule 4)."""
    if actor.kind != "operator":
        raise GateError("Publish approval needs a human.")
    require_kill_switch_off(conn)
    state = checklist(conn, actor, clip_id)
    if state["clip"]["status"] != "accepted":
        raise AppError("Only an accepted clip can be approved for publishing.", "bad_status")
    failed = [item["label"] for item in state["server"].values() if not item["ok"]]
    missing = [name for name in OWNER_TICKS if not ticks.get(name)]
    if failed or missing:
        raise AppError("The checklist is not complete: " + "; ".join(failed + [m.replace("_", " ") for m in missing]) + ".",
                       "checklist_incomplete")
    record = {"ticks": {name: True for name in OWNER_TICKS}, "server": {k: v["ok"] for k, v in state["server"].items()},
              "warnings": state["warnings"], "file_hash": state["clip"]["file_hash"],
              "approved_at": dt.datetime.now(dt.timezone.utc).isoformat()}
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
    if clip["status"] not in ("ready_to_post", "queued"):
        raise AppError("Approve the publish checklist first.", "bad_status")
    meta = clip["render_meta"] or {}
    post = conn.execute(
        """insert into posts (clip_id, owner_operator_id, account_id, platform, post_url, disclosure_toggle_on, checklist_json,
               scheduler_ref, confirmed_by)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning *""",
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
