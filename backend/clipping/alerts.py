"""Alerts (FR-60, FR-61, spec 13.4). Telegram is the main channel; the in-app inbox mirrors it."""
from __future__ import annotations

import datetime as dt
import statistics
import uuid
from decimal import Decimal

from psycopg.types.json import Jsonb

from . import config
from .adapters import base
from .adapters import audience as _audience, media, oauth as _oauth, publishing as _publishing, transcription, vendors  # noqa: F401  (registers providers)
from .core import audit, get_setting


def raise_alert(conn, owner_id, type_: str, severity: str, entity_type: str | None, entity_id, message: str) -> dict | None:
    """Create an alert unless the same one is already open, then send it. Returns the new row or None."""
    existing = conn.execute(
        """select id from alerts where owner_operator_id = %s and type = %s and acknowledged_at is null
           and coalesce(entity_id, '00000000-0000-0000-0000-000000000000'::uuid) = coalesce(%s, '00000000-0000-0000-0000-000000000000'::uuid)""",
        (owner_id, type_, entity_id),
    ).fetchone()
    if existing:
        return None
    message = config.redact(message)
    row = conn.execute(
        """insert into alerts (owner_operator_id, type, severity, entity_type, entity_id, message)
           values (%s, %s, %s, %s, %s, %s) returning *""",
        (owner_id, type_, severity, entity_type, entity_id, message),
    ).fetchone()
    channels = ["inapp"]
    try:
        adapter = registry.get(conn, "alerts")
        if adapter.provider != "inapp":
            result = adapter.send(alert={"message": message, "type": type_}, severity=severity, channel="telegram")
            channels.append(f"{result['channel']}x{result['sent']}")
    except Exception as exc:  # delivery failure never loses the alert: it stays in the inbox
        audit(conn, "alert.delivery_failed", "alerts", row["id"], {"error": config.redact(str(exc))[:200]})
    conn.execute("update alerts set sent_channels = %s where id = %s", (Jsonb(channels), row["id"]))
    row["sent_channels"] = channels
    return row


registry = base.Registry(alert_fn=raise_alert)


def inbox(conn) -> list[dict]:
    """Open alerts. Block-level alerts stay at the top until acknowledged (FR-61)."""
    return conn.execute(
        """select * from alerts where acknowledged_at is null and (snoozed_until is null or snoozed_until <= now() or severity = 'block')
           order by (severity = 'block') desc, (severity = 'warn') desc, created_at desc"""
    ).fetchall()


def acknowledge(conn, alert_id) -> dict | None:
    return conn.execute("update alerts set acknowledged_at = now() where id = %s and acknowledged_at is null returning *",
                        (alert_id,)).fetchone()


def snooze(conn, alert_id, hours: float) -> dict | None:
    return conn.execute(
        "update alerts set snoozed_until = now() + make_interval(secs => %s) where id = %s returning *",
        (float(hours) * 3600, alert_id),
    ).fetchone()


def evaluate(conn, owner_id) -> list[dict]:
    """Run the time- and data-driven alert rules for one owner. Safe to run often: open alerts are not repeated."""
    raised: list[dict] = []

    def fire(*args):
        row = raise_alert(conn, owner_id, *args)
        if row:
            raised.append(row)

    threshold = Decimal(str(get_setting(conn, "budget_alert_threshold_pct")))
    campaigns = conn.execute(
        """select c.id, c.name, c.deadline, v.budget_total, v.budget_remaining
           from campaigns c join campaign_versions v on v.id = c.approved_version_id
           where c.owner_operator_id = %s and c.status = 'approved'""",
        (owner_id,),
    ).fetchall()
    now = dt.datetime.now(dt.timezone.utc)
    for c in campaigns:
        if c["deadline"] and now <= c["deadline"] <= now + dt.timedelta(hours=48):
            hours = int((c["deadline"] - now).total_seconds() // 3600)
            fire("deadline", "warn", "campaigns", c["id"], f"{c['name']}: deadline in {hours} hours.")
        if c["budget_remaining"] is not None:
            if c["budget_remaining"] <= 0:
                fire("budget_gone", "block", "campaigns", c["id"], f"{c['name']}: budget is gone. Stop posting for it.")
            elif c["budget_total"] and c["budget_remaining"] / c["budget_total"] * 100 < threshold:
                pct = round(c["budget_remaining"] / c["budget_total"] * 100)
                fire("budget_low", "warn", "campaigns", c["id"], f"{c['name']}: {pct}% of the budget is left.")

    for p in conn.execute(
        """select p.id, c.name from payouts p join clips cl on cl.id = p.clip_id join campaigns c on c.id = cl.campaign_id
           where p.owner_operator_id = %s and p.status = 'held' and p.held_since < now() - interval '14 days'""",
        (owner_id,),
    ).fetchall():
        fire("payout_held", "warn", "payouts", p["id"], f"{p['name']}: a payout has been held for over 14 days.")

    # Account health: rolling 48-hour median of 48-hour views against the 4-week baseline (spec 13.4).
    drop = float(get_setting(conn, "health_alert_drop_pct"))
    for account in conn.execute("select id, handle, platform from accounts where owner_operator_id = %s and status = 'active'",
                                (owner_id,)).fetchall():
        rows = conn.execute(
            """select m.views, p.posted_at from metrics m join posts p on p.id = m.post_id
               where p.account_id = %s and m."window" = 'h48' and p.posted_at > now() - interval '28 days'""",
            (account["id"],),
        ).fetchall()
        recent = [r["views"] for r in rows if r["posted_at"] > now - dt.timedelta(hours=96)]
        baseline = [r["views"] for r in rows if r["posted_at"] <= now - dt.timedelta(hours=96)]
        if len(recent) >= 2 and len(baseline) >= 4:
            base_median, recent_median = statistics.median(baseline), statistics.median(recent)
            if base_median > 0 and recent_median < base_median * (1 - drop / 100):
                conn.execute("update accounts set status = 'warned', status_note = %s where id = %s",
                             ("Paused by the health alert. Check the account's analytics, then set it active again.", account["id"]))
                fire("account_health", "block", "accounts", account["id"],
                     f"@{account['handle']} ({account['platform']}): median views fell from {int(base_median)} to "
                     f"{int(recent_median)}. The account is paused.")

    from . import publish   # lazy: publish imports this module

    # A draft or private upload earns nothing until the owner finishes it in the app. publish raises the reminder when the post
    # lands; this catches any older one that never got one (a reminder the owner acknowledged is not raised again).
    for t in conn.execute(
        """select t.id, t.platform, t.state from post_targets t where t.owner_operator_id = %s and t.state in ('draft', 'private')
           and not exists (select 1 from alerts a where a.type = %s and a.entity_id = t.id)""",
        (owner_id, publish.FINISH_ALERT),
    ).fetchall():
        fire(publish.FINISH_ALERT, "warn", "post_targets", t["id"], publish.finish_message(t["platform"], t["state"]))

    for a in conn.execute("select id, stage, provider, quota_used, quota_limit from adapters where quota_limit is not null and active").fetchall():
        if a["quota_used"] >= a["quota_limit"] * Decimal("0.8"):
            fire("adapter_quota", "warn", "adapters", a["id"],
                 f"{a['stage']} adapter ({a['provider']}) has used {a['quota_used']} of {a['quota_limit']}.")
    return raised


MORNING_HOUR = 8     # local hour from which the day's summary may be sent


def morning_summary(conn, owner_id) -> list[str]:
    """The lines of the morning summary: what is waiting on the owner today. Empty when nothing is."""
    counts = conn.execute(
        """select count(*) filter (where t.state in ('draft', 'private')) as unfinished,
                  count(*) filter (where t.state = 'needs_check') as to_check,
                  (select count(*) from clips cl join campaigns c on c.id = cl.campaign_id
                    where cl.status in ('accepted', 'ready_to_post', 'queued') and not c.is_practice and cl.owner_operator_id = %(o)s) as ready
           from post_targets t where t.owner_operator_id = %(o)s""",
        {"o": owner_id},
    ).fetchone()
    lines = []
    if counts["unfinished"]:
        lines.append(f"{counts['unfinished']} post(s) are drafts or private uploads. Finish them in the app, then paste the public link.")
    if counts["to_check"]:
        lines.append(f"{counts['to_check']} post(s) need a check on the platform before you try again.")
    if counts["ready"]:
        lines.append(f"{counts['ready']} clip(s) are ready to post.")
    return lines


def send_morning_summary(conn, owner_id, now: dt.datetime | None = None) -> dict | None:
    """Once per local day, from MORNING_HOUR on, send the summary as an info alert (in-app and the alert channel).
    Yesterday's summary is acknowledged so the inbox holds one. Returns the alert, or None when it is early, empty or already sent today."""
    now = now or dt.datetime.now().astimezone()
    if now.hour < MORNING_HOUR:
        return None
    lines = morning_summary(conn, owner_id)
    day_key = uuid.uuid5(uuid.NAMESPACE_URL, f"morning-summary/{owner_id}/{now.date().isoformat()}")
    if not lines or conn.execute("select 1 from alerts where type = 'morning_summary' and entity_id = %s", (day_key,)).fetchone():
        return None
    conn.execute("update alerts set acknowledged_at = now() where owner_operator_id = %s and type = 'morning_summary' and acknowledged_at is null",
                 (owner_id,))
    return raise_alert(conn, owner_id, "morning_summary", "info", "morning_summary", day_key, "Good morning. " + " ".join(lines))
