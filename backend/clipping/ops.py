"""Operations: owner bootstrap, the Home dashboard, the keep-alive job and the worker loop."""
from __future__ import annotations

import datetime as dt
import threading
import time

from . import alerts, campaigns, config, ledger, pipeline
from .core import audit, get_setting, kill_switch
from .db import Actor, Database


def ensure_owner(db: Database, name: str = "Owner") -> Actor:
    """The sole operator at launch (D10). Created once, as admin."""
    with db.session(Actor.system()) as conn:
        row = conn.execute("select id, role from operators where role = 'admin' order by created_at limit 1").fetchone()
        if row is None:
            row = conn.execute("insert into operators (name, role, languages) values (%s, 'admin', '{en,ar}') returning id, role",
                               (name,)).fetchone()
    return Actor(str(row["id"]), row["role"])


def configure_adapters(db: Database) -> list[str]:
    """With CLIPPING_USE_FAKES=0, switch each stage to its real default provider when its keys are present."""
    changed: list[str] = []
    if config.use_fakes():
        return changed
    wanted = {"llm": "azure_openai", "transcription": "deepgram", "moments": "vizard", "alerts": "telegram"}
    if config.env("R2_BUCKET"):
        wanted["storage"] = "r2"
    with db.session(Actor.system()) as conn:
        for stage, provider in wanted.items():
            adapter = alerts.registry.get(conn, stage, provider)
            if adapter.health()["ok"]:
                alerts.base.activate(conn, stage, provider)
                changed.append(f"{stage}={provider}")
    return changed


def home(conn, actor: Actor) -> dict:
    """Spec 6.2."""
    now = dt.datetime.now(dt.timezone.utc)
    show_pay = get_setting(conn, "pay_per_clip_display", actor.operator_id) == "card"
    cards = conn.execute(
        """select c.id, c.name, c.status, c.status_reason, c.deadline, m.name as marketplace, cat.name as niche, c.is_practice,
                  v.rules_json->'languages_allowed'->>0 as language,
                  v.rate_per_1k, v.fee_pct, v.budget_total, v.budget_remaining, v.max_payout_per_clip, v.payout_model,
                  v.compliance_status, v.hard_blocks, v.unclear_fields, v.version_no, v.min_views_to_qualify,
                  v.rules_json->'platforms_allowed' as platforms, v.rules_confirmed_at,
                  (select count(*) from clips cl where cl.campaign_id = c.id and cl.status = 'ready_for_review') as clips_ready
           from campaigns c join marketplaces m on m.id = c.marketplace_id left join categories cat on cat.id = c.category_id
           left join campaign_versions v on v.id = c.current_version_id
           where c.status in ('pending_approval', 'approved', 'paused')"""
    ).fetchall()
    for card in cards:
        rate = float(card["rate_per_1k"] or 0)
        card["est_net_rate_per_1k"] = round(rate * (1 - float(card["fee_pct"] or 0) / 100), 4)
        card["fee_known"] = card["fee_pct"] is not None
        card["budget_left_pct"] = (round(float(card["budget_remaining"]) / float(card["budget_total"]) * 100)
                                   if card["budget_total"] and card["budget_remaining"] is not None else None)
        card["hours_to_deadline"] = round((card["deadline"] - now).total_seconds() / 3600) if card["deadline"] else None
        if not show_pay:
            card.pop("max_payout_per_clip")
    cards.sort(key=lambda c: (-c["est_net_rate_per_1k"], c["deadline"] or dt.datetime.max.replace(tzinfo=dt.timezone.utc)))
    week_start = (now - dt.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    week, month = ledger.net_per_hour(conn, week_start), ledger.net_per_hour(conn, month_start)
    today = conn.execute(
        """select
             (select coalesce(sum(coalesce(minutes, extract(epoch from now() - started_at) / 60)), 0) from time_entries
               where operator_id = %(op)s and started_at::date = now()::date) as minutes,
             (select count(*) from reviews r where r.reviewer_id = %(op)s and r.decision = 'accept' and r.created_at::date = now()::date
               and not exists (select 1 from reviews u where u.undo_of = r.id)) as accepted,
             (select count(*) from posts where confirmed_by = %(op)s and posted_at::date = now()::date) as posts_done,
             (select count(*) from clips cl join campaigns c on c.id = cl.campaign_id
               where cl.status in ('accepted', 'ready_to_post', 'queued') and not c.is_practice) as posts_due,
             (select count(*) from clips where status = 'ready_for_review') as to_review,
             (select count(*) from clips where status = 'review_later') as review_later""",
        {"op": actor.operator_id},
    ).fetchone()
    return {
        "stories": [{"id": c["id"], "name": c["name"], "budget_left_pct": c["budget_left_pct"],
                     "hours_to_deadline": c["hours_to_deadline"], "clips_ready": c["clips_ready"]}
                    for c in cards if c["status"] == "approved"],
        "today": {"hours_logged": round(float(today["minutes"]) / 60, 2),
                  "hours_cap": float(get_setting(conn, "hours_cap_per_day", actor.operator_id)),
                  "clips_accepted": today["accepted"], "posts_done": today["posts_done"], "posts_due": today["posts_due"],
                  "to_review": today["to_review"], "review_later": today["review_later"]},
        "net_per_hour": {"this_week": week["overall"], "month_to_date": month["overall"], "confidence": month["confidence"]},
        "attention": alerts.inbox(conn)[:5],
        "campaigns": cards,
        "kill_switch": kill_switch(conn),
        "pay_per_clip_display": get_setting(conn, "pay_per_clip_display", actor.operator_id),
    }


def keepalive(db: Database) -> bool:
    """Open item O8: a small database write so a free Supabase project is not paused for inactivity.
    A failure sends an alert straight to the alert channel, because the database may be the thing that is down."""
    try:
        with db.session(Actor.system()) as conn:
            audit(conn, "keepalive.ping", "adapters", None, {"ok": True})
        return True
    except Exception as exc:
        message = f"Database keep-alive failed ({type(exc).__name__}). Check the Supabase project before it pauses."
        try:
            from .adapters import vendors

            provider = vendors.FakeAlerts if config.use_fakes() else vendors.TelegramAlerts
            provider({"config_json": {}}).send(alert={"message": message, "type": "keepalive_failed"}, severity="block")
        except Exception:
            pass
        return False


def schedule_discovery(conn) -> bool:
    """Queue a discovery pass once per interval (the job key is the interval bucket, so it runs once)."""
    from .core import enqueue

    hours = int(get_setting(conn, "discovery_interval_hours"))
    bucket = int(dt.datetime.now(dt.timezone.utc).timestamp() // (hours * 3600))
    owner = conn.execute("select id, niches from operators where role = 'admin' order by created_at limit 1").fetchone()
    if owner is None:
        return False
    try:
        return enqueue(conn, "discover", {"bucket": bucket, "niches": owner["niches"] or []}, owner["id"]) is not None
    except Exception:
        return False


def housekeeping(db: Database) -> None:
    """Alert rules for every operator, source retention, and scheduled discovery."""
    with db.session(Actor.worker()) as conn:
        schedule_discovery(conn)
    if time.time() - _audience_checked["at"] > 3600:   # audience countries: at most hourly, each account every few days
        _audience_checked["at"] = time.time()
        from . import audience

        with db.session(Actor.worker()) as conn:
            audience.refresh_all(conn)
        # Learning: turn each owner's new review decisions and 48 h views into labels, and refit the moment-engine,
        # engine-route and hook-pattern weights (hourly; a no-op until a profile has enough labels).
        from . import moment_learning

        with db.session(Actor.worker()) as conn:
            for row in conn.execute("select id from operators where status = 'active'").fetchall():
                try:
                    moment_learning.learn(conn, row["id"])
                except Exception:   # learning must never stop housekeeping
                    pass
    with db.session(Actor.worker()) as conn:
        for row in conn.execute("select id from operators where status = 'active'").fetchall():
            alerts.evaluate(conn, row["id"])
            alerts.send_morning_summary(conn, row["id"])
        campaigns.purge_expired_sources(conn)
    from . import publish

    with db.session(Actor.worker()) as conn:
        publish.sweep(conn)      # a send that never finished becomes needs_check; processing posts get their next status check


_awake = {"on": False}
_audience_checked = {"at": 0.0}


def keep_awake(on: bool) -> bool:
    """Stop the laptop from sleeping while there is work (Windows; a no-op elsewhere).
    Only the system sleep timer is held: the screen may still turn off. Returns True when the state changed."""
    if on == _awake["on"]:
        return False
    _awake["on"] = on
    import sys

    if sys.platform == "win32":
        try:
            import ctypes

            ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
        except Exception:
            pass
    return True


def has_work(db: Database) -> bool:
    with db.session(Actor.worker()) as conn:
        return conn.execute("select 1 from jobs where status in ('queued', 'running') limit 1").fetchone() is not None


def worker_loop(db: Database, stop: threading.Event, poll_s: float = 2.0) -> None:
    """Runs jobs, then housekeeping every minute and the keep-alive on its configured interval."""
    last_housekeeping = last_keepalive = 0.0
    while not stop.is_set():
        try:
            worked = pipeline.process_one(db)
        except Exception:
            worked = False
        now = time.time()
        if now - last_housekeeping > 60:
            last_housekeeping = now
            try:
                housekeeping(db)
            except Exception:
                pass
        if now - last_keepalive > 24 * 3600:
            last_keepalive = now
            keepalive(db)
        try:
            keep_awake(worked or has_work(db))
        except Exception:
            pass
        if not worked:
            stop.wait(poll_s)
    keep_awake(False)
