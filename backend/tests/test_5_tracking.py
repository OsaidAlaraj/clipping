"""Build step 5: metrics, ledger statuses, net pay per hour, CSV export, alerts, keep-alive, and the
MVP acceptance run (spec 15.6). Also an API pass over the same flow."""
from __future__ import annotations

import csv
import datetime as dt
import io
from decimal import Decimal

import psycopg
import pytest

from clipping import alerts, campaigns, core, demo, ledger, ops, pipeline, publish, review
from clipping.adapters import base, vendors
from clipping.core import AppError
from clipping.db import Actor, Database
from conftest import brief

TICKS = {name: True for name in publish.OWNER_TICKS}
VERSION = {"rate_per_1k": 1.5, "fee_pct": 10, "min_views_to_qualify": 5000, "max_payout_per_clip": 150}


def post_clip(produced, index: int = 0, url: str = "https://example.com/p/1") -> dict:
    with produced.db.session(produced.owner) as conn:
        target = review.feed(conn, produced.owner.operator_id)[index]["id"]
        review.accept(conn, produced.owner, target)
        campaigns.recheck_brief(conn, produced.owner, produced.campaign_id, current_text=brief("01"))
        publish.approve_publish(conn, produced.owner, target, TICKS)
        post = publish.mark_posted(conn, produced.owner, target, url, True, True)
        post["payout_id"] = conn.execute("select id from payouts where clip_id = %s", (target,)).fetchone()["id"]
        return post


# ------------------------------------------------------------------ amounts

def test_net_is_gross_minus_fee_minus_withdrawal_fee():
    amounts = ledger.compute_amounts(64000, VERSION, withdrawal_fee_usd=2)
    assert (amounts["gross_usd"], amounts["fee_usd"], amounts["net_usd"]) == (Decimal("96.00"), Decimal("9.60"), Decimal("84.40"))


def test_below_minimum_views_pays_nothing_and_cap_limits_gross():
    low = ledger.compute_amounts(4999, VERSION)
    assert low["below_min_views"] and low["gross_usd"] == 0
    capped = ledger.compute_amounts(500000, VERSION)
    assert capped["gross_usd"] == Decimal("150.00") and capped["cap_reached"]


def test_unknown_fee_is_never_invented():
    amounts = ledger.compute_amounts(64000, {**VERSION, "fee_pct": None})
    assert amounts["gross_usd"] == Decimal("96.00") and amounts["fee_usd"] is None and amounts["net_usd"] is None
    assert ledger.compute_amounts(64000, {**VERSION, "rate_per_1k": None})["gross_usd"] is None


def test_content_rewards_fee_rule():
    rules = {"creator_fee": [{"models": ["cpm", "pool"], "pct": 10}, {"models": ["per_post", "retainer"], "budget_lt": 5000, "pct": 10},
                             {"models": ["per_post", "retainer"], "budget_gte": 5000, "pct": 0}]}
    assert campaigns.fee_pct_from_marketplace(rules, "cpm", 100) == 10
    assert campaigns.fee_pct_from_marketplace(rules, "per_post", 4999) == 10
    assert campaigns.fee_pct_from_marketplace(rules, "retainer", 5000) == 0
    assert campaigns.fee_pct_from_marketplace(rules, "per_post", None) is None
    assert campaigns.fee_pct_from_marketplace({"creator_fee": None}, "cpm", 100) is None


# ------------------------------------------------------------------ metrics and ledger order

def test_views_are_snapshots_and_start_tracking(produced):
    post = post_clip(produced)
    with produced.db.session(produced.owner) as conn:
        ledger.record_views(conn, produced.owner, post["id"], "h48", 21000)
        ledger.record_views(conn, produced.owner, post["id"], "d7", 64000, source="export")
        with pytest.raises(AppError):
            ledger.record_views(conn, produced.owner, post["id"], "d30", 1)
        assert conn.execute("select count(*) as n from metrics").fetchone()["n"] == 2
        payout = conn.execute("select * from payouts where id = %s", (post["payout_id"],)).fetchone()
        assert payout["status"] == "tracking" and payout["gross_usd"] == Decimal("96.00") and payout["net_usd"] == Decimal("86.40")
        row = ledger.ledger(conn)[0]
        assert (row["views_h48"], row["views_d7"]) == (21000, 64000)


def test_ledger_states_change_only_in_the_allowed_order(produced):
    post = post_clip(produced)
    pid = post["payout_id"]
    with produced.db.session(produced.owner) as conn:
        for bad in ("approved", "held", "paid"):                # submitted cannot skip ahead
            with pytest.raises(AppError):
                ledger.set_payout_status(conn, produced.owner, pid, bad)
        ledger.set_payout_status(conn, produced.owner, pid, "tracking")
        for bad in ("submitted", "paid", "held"):
            with pytest.raises(AppError):
                ledger.set_payout_status(conn, produced.owner, pid, bad)
        ledger.set_payout_status(conn, produced.owner, pid, "approved")
        with pytest.raises(AppError):
            ledger.set_payout_status(conn, produced.owner, pid, "paid")
        held = ledger.set_payout_status(conn, produced.owner, pid, "held")
        assert held["held_since"] is not None
        paid = ledger.set_payout_status(conn, produced.owner, pid, "paid", withdrawal_fee_usd=1.5)
        assert paid["paid_at"] is not None
        for bad in ("held", "tracking", "rejected", "submitted"):   # paid is terminal
            with pytest.raises(AppError):
                ledger.set_payout_status(conn, produced.owner, pid, bad)
    with pytest.raises(psycopg.errors.RaiseException):          # the database enforces the order itself
        with produced.db.session(Actor.system()) as conn:
            conn.execute("update payouts set status = 'tracking' where id = %s", (pid,))


@pytest.mark.parametrize("terminal", ["rejected", "capped", "budget_exhausted"])
def test_terminal_payout_states(produced, terminal):
    post = post_clip(produced)
    with produced.db.session(produced.owner) as conn:
        ledger.set_payout_status(conn, produced.owner, post["payout_id"], "tracking")
        ledger.set_payout_status(conn, produced.owner, post["payout_id"], terminal)
        with pytest.raises(AppError):
            ledger.set_payout_status(conn, produced.owner, post["payout_id"], "approved")
    with pytest.raises(psycopg.errors.RaiseException):
        with produced.db.session(produced.owner) as conn:
            conn.execute("insert into payouts (clip_id, campaign_version_id, status) select clip_id, campaign_version_id, 'paid' from payouts")


# ------------------------------------------------------------------ net pay per hour and export

def test_net_pay_per_hour_by_every_dimension(produced):
    first, second = post_clip(produced, 0, "https://example.com/p/1"), post_clip(produced, 0, "https://example.com/p/2")
    with produced.db.session(produced.owner) as conn:
        ledger.record_views(conn, produced.owner, first["id"], "d7", 64000)     # gross 96.00, net 86.40
        ledger.record_views(conn, produced.owner, second["id"], "d7", 20000)    # gross 30.00, net 27.00
        for status in ("approved", "held", "paid"):
            ledger.set_payout_status(conn, produced.owner, first["payout_id"], status)
        for minutes, task, campaign in ((60, "cutting", produced.campaign_id), (30, "review", produced.campaign_id), (30, "admin", None)):
            conn.execute("""insert into time_entries (operator_id, owner_operator_id, task_type, campaign_id, ended_at, minutes)
                            values (%s, %s, %s, %s, now(), %s)""", (produced.owner.operator_id, produced.owner.operator_id, task, campaign, minutes))
        stats = ledger.net_per_hour(conn)
        overall = stats["overall"]
        assert overall["hours"] == 2.0 and overall["paid_usd"] == Decimal("86.40") and overall["pending_usd"] == Decimal("27.00")
        assert overall["paid_per_hour"] == 43.2 and overall["with_pending_per_hour"] == 56.7     # paid and pending shown separately
        assert stats["confidence"] == "low" and stats["clips_posted"] == 2 and stats["clips_clearing_threshold"] == 2
        assert set(stats["by"]) == {"campaign", "niche", "language", "account", "operator"}
        campaign = stats["by"]["campaign"][0]
        assert campaign["hours"] == 1.5 and campaign["paid_per_hour"] == 57.6
        assert stats["by"]["niche"][0]["label"] == "podcast" and stats["by"]["language"][0]["label"] == "en"
        assert len(stats["by"]["account"]) == 2 and stats["by"]["operator"][0]["hours"] == 2.0
        weekly = ledger.weekly_median_net_per_hour(conn)
        assert weekly["median_net_per_hour"] == 56.7 and weekly["decision"] == "go"


def test_confidence_label_and_go_decision(db, owner):
    now = dt.datetime.now(dt.timezone.utc)
    assert ledger.confidence(None) == "low" and ledger.confidence(now - dt.timedelta(days=29)) == "low"
    assert ledger.confidence(now - dt.timedelta(days=30)) == "medium" and ledger.confidence(now - dt.timedelta(days=90)) == "high"
    with db.session(owner) as conn:
        assert [ledger.go_decision(conn, v) for v in (None, 1.99, 2.0, 4.59, 4.6)] == ["not enough data", "stop", "iterate", "iterate", "go"]


def test_unknown_fee_is_flagged_in_the_report(produced):
    post = post_clip(produced)
    with produced.db.session(Actor.system()) as conn:
        conn.execute("update campaign_versions set fee_pct = null")
    with produced.db.session(produced.owner) as conn:
        ledger.record_views(conn, produced.owner, post["id"], "d7", 64000)
        payout = conn.execute("select * from payouts").fetchone()
        assert payout["fee_usd"] is None and payout["net_usd"] is None and payout["gross_usd"] == Decimal("96.00")
        assert ledger.net_per_hour(conn)["overall"]["fee_unknown"] is True


def test_csv_export_per_month(produced):
    post = post_clip(produced)
    month = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
    with produced.db.session(produced.owner) as conn:
        ledger.record_views(conn, produced.owner, post["id"], "d7", 64000)
        rows = list(csv.DictReader(io.StringIO(ledger.export_csv(conn, month))))
        assert len(rows) == 1 and rows[0]["net_usd"] == "86.40" and rows[0]["post_url"] == "https://example.com/p/1"
        assert rows[0]["status"] == "tracking" and rows[0]["views_d7"] == "64000"
        assert list(csv.DictReader(io.StringIO(ledger.export_csv(conn, "1999-01")))) == []


def test_rejection_stats_ignore_undone_decisions(produced):
    with produced.db.session(produced.owner) as conn:
        clips = review.feed(conn, produced.owner.operator_id)
        review.accept(conn, produced.owner, clips[0]["id"])
        review.reject(conn, produced.owner, clips[1]["id"], "weak_hook")
        review.undo(conn, produced.owner, review.reject(conn, produced.owner, clips[2]["id"], "duplicate")["id"])
        stats = ledger.rejection_stats(conn)
        assert (stats["accepted"], stats["rejected"], stats["rejection_rate"], stats["reasons"]) == (1, 1, 0.5, {"weak_hook": 1})


# ------------------------------------------------------------------ alerts

def test_deadline_and_budget_alerts(produced):
    with produced.db.session(Actor.system()) as conn:
        conn.execute("update campaigns set deadline = now() + interval '20 hours'")
        conn.execute("update campaign_versions set budget_remaining = 300")   # 15% of 2,000
    with produced.db.session(produced.owner) as conn:
        raised = {a["type"]: a["severity"] for a in alerts.evaluate(conn, produced.owner.operator_id)}
        assert raised == {"deadline": "warn", "budget_low": "warn"}
        assert alerts.evaluate(conn, produced.owner.operator_id) == []          # open alerts are not repeated
        core.set_setting(conn, "budget_alert_threshold_pct", 10)
    with produced.db.session(Actor.system()) as conn:
        conn.execute("update campaign_versions set budget_remaining = 0")
    with produced.db.session(produced.owner) as conn:
        assert {a["type"]: a["severity"] for a in alerts.evaluate(conn, produced.owner.operator_id)} == {"budget_gone": "block"}
    assert [a["severity"] for a in vendors.FAKE_ALERTS["sent"]] == ["warn", "warn", "block", "block"]   # block is sent twice


def test_inbox_keeps_block_alerts_on_top_and_supports_ack_and_snooze(db, owner):
    with db.session(owner) as conn:
        warn = alerts.raise_alert(conn, owner.operator_id, "deadline", "warn", None, None, "Deadline soon")
        block = alerts.raise_alert(conn, owner.operator_id, "rule_change", "block", None, None, "Brief changed")
        info = alerts.raise_alert(conn, owner.operator_id, "kill_switch", "info", None, None, "Kill switch on")
        assert [a["severity"] for a in alerts.inbox(conn)] == ["block", "warn", "info"]
        alerts.snooze(conn, warn["id"], 4)
        alerts.snooze(conn, block["id"], 4)
        assert [a["type"] for a in alerts.inbox(conn)] == ["rule_change", "kill_switch"]   # a block alert cannot be snoozed away
        alerts.acknowledge(conn, block["id"])
        assert [a["id"] for a in alerts.inbox(conn)] == [info["id"]]
        assert block["sent_channels"] == ["inapp", "fakex2"]


def test_payout_held_over_14_days_and_dispute_alerts(produced):
    post = post_clip(produced)
    with produced.db.session(produced.owner) as conn:
        for status in ("tracking", "approved", "held"):
            ledger.set_payout_status(conn, produced.owner, post["payout_id"], status)
        assert alerts.evaluate(conn, produced.owner.operator_id) == []
    with produced.db.session(Actor.system()) as conn:
        conn.execute("update payouts set held_since = now() - interval '15 days'")
    with produced.db.session(produced.owner) as conn:
        assert [a["type"] for a in alerts.evaluate(conn, produced.owner.operator_id)] == ["payout_held"]
        ledger.mark_disputed(conn, produced.owner, post["payout_id"], "Views do not match")
        assert conn.execute("select severity from alerts where type = 'payout_disputed'").fetchone()["severity"] == "warn"


def test_account_health_alert_pauses_the_account(produced):
    post = post_clip(produced)
    with produced.db.session(Actor.system()) as conn:
        clip = conn.execute("select * from clips where id = %s", (post["clip_id"],)).fetchone()
        for days_ago, views in [(20, 10000), (15, 12000), (10, 9000), (7, 11000), (1, 2000), (0, 3000)]:
            new_clip = conn.execute(
                """insert into clips (candidate_id, campaign_id, campaign_version_id, owner_operator_id, start_s, end_s, language, status)
                   values (%s, %s, %s, %s, 0, 10, 'en', 'archived') returning id""",
                (clip["candidate_id"], clip["campaign_id"], clip["campaign_version_id"], clip["owner_operator_id"])).fetchone()["id"]
            conn.execute("alter table posts disable trigger post_gate")
            new_post = conn.execute(
                """insert into posts (clip_id, owner_operator_id, account_id, platform, post_url, disclosure_toggle_on, checklist_json, confirmed_by, posted_at)
                   values (%s, %s, %s, %s, 'https://example.com/h', true, '{}', %s, now() - make_interval(days => %s)) returning id""",
                (new_clip, clip["owner_operator_id"], post["account_id"], post["platform"], clip["owner_operator_id"], days_ago)).fetchone()["id"]
            conn.execute("alter table posts enable trigger post_gate")
            conn.execute("""insert into metrics (post_id, owner_operator_id, "window", views) values (%s, %s, 'h48', %s)""",
                         (new_post, clip["owner_operator_id"], views))
    with produced.db.session(Actor.worker()) as conn:
        raised = alerts.evaluate(conn, produced.owner.operator_id)
    assert [(a["type"], a["severity"]) for a in raised] == [("account_health", "block")]
    with produced.db.session(produced.owner) as conn:
        assert conn.execute("select status from accounts where id = %s", (post["account_id"],)).fetchone()["status"] == "warned"
        assert pipeline.pick_account(conn, produced.owner.operator_id, [post["platform"]]) is None   # excluded from production


def test_adapter_quota_alert_and_scheduler_channel_alert(db, owner):
    with db.session(owner) as conn:
        base.activate(conn, "scheduler", "fake")
        conn.execute("update adapters set quota_used = 2.5 where stage = 'scheduler' and provider = 'fake'")
        assert [a["type"] for a in alerts.evaluate(conn, owner.operator_id)] == ["adapter_quota"]


def test_alert_delivery_failure_keeps_the_alert_in_the_inbox(db, owner):
    vendors.FAKE_ALERTS["fail"] = True
    with db.session(owner) as conn:
        row = alerts.raise_alert(conn, owner.operator_id, "deadline", "warn", None, None, "Deadline soon")
        assert row["sent_channels"] == ["inapp"] and len(alerts.inbox(conn)) == 1
        assert conn.execute("select count(*) as n from audit_log where action = 'alert.delivery_failed'").fetchone()["n"] == 1


def test_telegram_adapter_never_leaks_the_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:SECRET-TOKEN-VALUE")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    class Boom:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, **_): raise RuntimeError(f"cannot reach {url}")

    monkeypatch.setattr(vendors, "_http", lambda: Boom())
    with pytest.raises(vendors.AdapterError) as err:
        vendors.TelegramAlerts({"config_json": {}}).send(alert={"message": "x"}, severity="block")
    assert "SECRET-TOKEN-VALUE" not in str(err.value) and err.value.__cause__ is None
    from clipping import config

    assert config.redact("failed for 123456:SECRET-TOKEN-VALUE") == "failed for <TELEGRAM_BOT_TOKEN>"


def test_kill_switch_alert_is_info_level(db, owner):
    from fastapi.testclient import TestClient

    from clipping import api

    api._owner = owner
    client = TestClient(api.app)
    assert client.post("/api/kill-switch", json={"active": True, "reason": "test"}).json()["active"] is True
    alert = client.get("/api/alerts").json()["alerts"][0]
    assert (alert["type"], alert["severity"]) == ("kill_switch", "info")
    client.post("/api/kill-switch", json={"active": False})
    assert client.get("/api/alerts").json()["alerts"] == []


# ------------------------------------------------------------------ keep-alive (O8)

def test_keepalive_writes_to_the_database(db):
    assert ops.keepalive(db) is True
    with db.session(Actor.system()) as conn:
        assert conn.execute("select count(*) as n from audit_log where action = 'keepalive.ping'").fetchone()["n"] == 1


def test_keepalive_failure_sends_a_block_alert():
    dead = Database("postgresql://postgres:@127.0.0.1:9/postgres?connect_timeout=2")
    vendors.FAKE_ALERTS.update(sent=[], fail=False)
    assert ops.keepalive(dead) is False
    assert [(a["type"], a["severity"]) for a in vendors.FAKE_ALERTS["sent"]] == [("keepalive_failed", "block")] * 2


def test_source_footage_is_deleted_after_retention(produced):
    with produced.db.session(Actor.system()) as conn:
        source = conn.execute("select * from source_assets").fetchone()
        assert (source["delete_after"] - source["created_at"]).days == 30
        storage = alerts.registry.get(conn, "storage")
        copy_key = "sources/retention-test.mp4"
        storage.put(copy_key, str(storage.path(source["stored_path"])))
        conn.execute("update source_assets set stored_path = %s, delete_after = now() - interval '1 day'", (copy_key,))
        assert campaigns.purge_expired_sources(conn) == 1 and not storage.path(copy_key).exists()
        assert conn.execute("select deleted_at from source_assets").fetchone()["deleted_at"] is not None


# ------------------------------------------------------------------ MVP acceptance (spec 15.6)

def test_mvp_acceptance_on_the_seeded_sample_campaign(db):
    lines: list[str] = []
    started = dt.datetime.now()
    result = demo.run(db, log=lines.append, render_size=(270, 480))
    owner = ops.ensure_owner(db)
    with db.session(owner) as conn:
        campaign = conn.execute("select * from campaigns where name = %s", (demo.DEMO_NAME,)).fetchone()
        # intake -> approval -> packaged clip -> accepted in the feed -> posted through the checklist -> views and payout logged
        assert campaign["status"] == "approved" and campaign["approved_version_id"] is not None
        posted = conn.execute("select * from clips where status = 'posted'").fetchall()
        assert len(posted) == 1 and posted[0]["compliance_status"] == "pass"
        post = conn.execute("select * from posts").fetchone()
        assert post["checklist_json"]["ticks"] and post["disclosure_toggle_on"]
        assert {m["window"] for m in conn.execute('select "window" from metrics').fetchall()} == {"h48", "d7"}
        payout = conn.execute("select * from payouts").fetchone()
        assert payout["status"] == "paid" and payout["net_usd"] == Decimal("86.40")
        # the audit log records each step
        actions = [r["action"] for r in conn.execute("select action from audit_log order by at").fetchall()]
        steps = ["campaigns.insert", "rules.confirmed", "gate.campaign_approval", "source_assets.insert", "jobs.insert",
                 "clip.packaged", "gate.clip_acceptance", "brief.recheck", "gate.publish_approval", "post.recorded",
                 "metrics.insert", "payouts.status:held>paid", "kill_switch.update"]
        positions = [actions.index(step) for step in steps]
        assert positions == sorted(positions), "audit steps are missing or out of order"
        for gate in ("gate.campaign_approval", "gate.clip_acceptance", "gate.publish_approval"):
            row = conn.execute("select actor_id, actor_kind from audit_log where action = %s", (gate,)).fetchone()
            assert str(row["actor_id"]) == owner.operator_id and row["actor_kind"] == "operator"
        # median net pay per hour is calculated from the ledger
        weekly = ledger.weekly_median_net_per_hour(conn)
        assert weekly["median_net_per_hour"] == result["median"] == 144.0 and result["net_per_hour"] == 144.0
        # the kill switch stops all new jobs within one minute
        version = campaign["approved_version_id"]
        core.enqueue(conn, "render", {"probe": 1}, owner.operator_id, campaign_version_id=version)
        flipped = dt.datetime.now()
        core.set_kill_switch(conn, True, "acceptance")
    assert pipeline.run_until_idle(db) == 0
    with db.session(owner) as conn:
        assert conn.execute("select count(*) as n from jobs where status in ('queued', 'running')").fetchone()["n"] == 0
        with pytest.raises(core.KillSwitchOn):
            core.enqueue(conn, "render", {"probe": 2}, owner.operator_id, campaign_version_id=version)
        assert all(i["status"] == "accepted" for i in publish.queue(conn)["items"])
    assert (dt.datetime.now() - flipped).total_seconds() < 60
    assert any("hard-blocked" in line for line in lines) and (dt.datetime.now() - started).total_seconds() < 300


def test_api_runs_the_review_flow(produced):
    from fastapi.testclient import TestClient

    from clipping import api

    api._owner = produced.owner
    client = TestClient(api.app)
    home = client.get("/api/home").json()
    assert home["stories"][0]["clips_ready"] == 3 and home["today"]["to_review"] == 3 and "max_payout_per_clip" not in home["campaigns"][0]
    feed = client.get("/api/feed").json()
    assert len(feed["clips"]) == 3 and feed["undo_window_s"] == 10 and feed["session_length_min"] == 25
    clip = feed["clips"][0]
    video = client.get(clip["video_url"], headers={"Range": "bytes=0-99"})
    assert video.status_code in (200, 206) and client.get(clip["video_url"].split("&sig=")[0] + "&sig=bad").status_code == 403
    session = client.post("/api/sessions", json={}).json()
    assert client.post(f"/api/clips/{clip['id']}/reject", json={"session_id": session["id"]}).status_code == 400   # reason required
    rejected = client.post(f"/api/clips/{clip['id']}/reject", json={"reason_code": "weak_hook", "session_id": session["id"]}).json()
    assert client.post("/api/reviews/undo", json={"review_id": rejected["id"]}).json()["decision"] == "undo"
    assert client.post(f"/api/clips/{clip['id']}/accept", json={"session_id": session["id"]}).status_code == 200
    summary = client.post(f"/api/sessions/{session['id']}/stop", json={"position": {"clip_id": clip["id"]}}).json()
    assert (summary["clips_reviewed"], summary["accepted"], summary["rejected"]) == (1, 1, 0)
    assert client.post(f"/api/clips/{clip['id']}/publish-approve", json={"ticks": TICKS}).status_code == 400   # brief not re-checked
    assert client.post(f"/api/campaigns/{produced.campaign_id}/recheck", json={"unchanged_confirmed": True}).json()["changed"] is False
    assert client.post(f"/api/clips/{clip['id']}/publish-approve", json={"ticks": TICKS}).json()["status"] == "ready_to_post"
    assert client.post(f"/api/clips/{clip['id']}/posted", json={"post_url": "https://example.com/p/9"}).status_code == 400   # no confirm
    post = client.post(f"/api/clips/{clip['id']}/posted", json={"post_url": "https://example.com/p/9", "confirm": True, "disclosure_toggle_on": True}).json()
    assert client.post(f"/api/posts/{post['id']}/views", json={"window": "h48", "views": 9000}).status_code == 200
    ledger_view = client.get("/api/ledger").json()
    assert ledger_view["rows"][0]["status"] == "tracking" and ledger_view["rows"][0]["views_h48"] == 9000
    assert client.put("/api/settings/session_length_min", json={"value": 99}).status_code == 400
    assert client.put("/api/settings/session_length_min", json={"value": 30}).status_code == 200
    assert client.put("/api/settings/allow_auto_post", json={"value": True}).status_code == 400
    month = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
    assert "example.com/p/9" in client.get(f"/api/export.csv?month={month}").text
    blocked = client.post("/api/campaigns", json={"marketplace": "vyro", "name": "Casino", "brief_text": brief("02")}).json()
    assert blocked["campaign"]["status"] == "rejected"
    assert client.post(f"/api/campaigns/{blocked['campaign']['id']}/approve").status_code == 400   # refused: a rejected campaign cannot be approved
