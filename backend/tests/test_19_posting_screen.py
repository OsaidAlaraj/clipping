"""Phase E, phase 4 (backend half): the "finish in the app" reminder, the morning summary, the 30-minute countdown (`submit_by`)
and the phase 3 review clean-ups. Fakes only."""
from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from clipping import alerts, api, ops, pipeline, publish
from clipping.adapters import publishing
from clipping.db import Actor
from test_18_post_targets import URLS, posting  # noqa: F401  (posting is a fixture)


def alert_rows(p, type_=None):
    return p.rows("select * from alerts where (%s::text is null or type = %s) order by created_at", type_, type_)


def posted_all(p):
    p.confirm()
    p.run_worker()
    return p.targets()


# ------------------------------------------------------------------ DW-4.3: finish in the app

def test_DW_4_3_a_draft_and_a_private_upload_each_raise_a_finish_in_the_app_reminder(posting):
    by = posted_all(posting)
    reminders = alert_rows(posting, "finish_in_app")
    assert {str(r["entity_id"]) for r in reminders} == {str(by["tiktok"]["id"]), str(by["youtube_shorts"]["id"])}
    texts = {r["message"] for r in reminders}
    assert any("TikTok" in t and "draft" in t for t in texts) and any("YouTube" in t and "privately" in t for t in texts)
    assert all(r["severity"] == "warn" and "paste the public link" in r["message"] for r in reminders)


def test_DW_4_3_a_public_post_raises_no_reminder_and_pasting_the_link_does_not_either(posting):
    by = posted_all(posting)
    assert str(by["instagram_reels"]["id"]) not in {str(r["entity_id"]) for r in alert_rows(posting, "finish_in_app")}
    with posting.db.session(posting.owner) as conn:
        publish.paste_link(conn, posting.owner, by["tiktok"]["id"], URLS["tiktok"])
    assert len(alert_rows(posting, "finish_in_app")) == 2          # the earlier two; none new


def test_DW_4_3_evaluate_reminds_about_an_older_draft_once_and_respects_an_acknowledged_reminder(posting):
    posted_all(posting)
    with posting.db.session(Actor.system()) as conn:
        conn.execute("delete from alerts where type = 'finish_in_app'")          # a draft from before reminders existed
    with posting.db.session(posting.owner) as conn:
        raised = [r["type"] for r in alerts.evaluate(conn, posting.owner.operator_id)]
    assert raised.count("finish_in_app") == 2
    with posting.db.session(posting.owner) as conn:
        assert "finish_in_app" not in [r["type"] for r in alerts.evaluate(conn, posting.owner.operator_id)]   # still open: not repeated
        for r in alerts.inbox(conn):
            alerts.acknowledge(conn, r["id"])
        assert "finish_in_app" not in [r["type"] for r in alerts.evaluate(conn, posting.owner.operator_id)]   # acknowledged: left alone
    assert len(alert_rows(posting, "finish_in_app")) == 2


def test_DW_4_3_a_public_link_starts_a_thirty_minute_countdown_on_the_post_status(posting):
    by = posted_all(posting)
    client = TestClient(api.app)
    api._owner = posting.owner
    status = {t["platform"]: t for t in client.get(f"/api/clips/{posting.clip}/post-status").json()["targets"]}
    assert status["tiktok"]["submit_by"] is None and status["youtube_shorts"]["submit_by"] is None      # no public link yet
    posted_at = posting.posts()[0]["posted_at"]
    assert dt.datetime.fromisoformat(status["instagram_reels"]["submit_by"]) - posted_at == dt.timedelta(minutes=30)
    linked = client.post(f"/api/targets/{by['tiktok']['id']}/link", json={"url": URLS["tiktok"]}).json()
    assert linked["state"] == "published" and linked["submit_by"]
    tiktok_post = [p for p in posting.posts() if p["platform"] == "tiktok"][0]
    assert dt.datetime.fromisoformat(linked["submit_by"]) - tiktok_post["posted_at"] == dt.timedelta(minutes=30)


def test_the_countdown_is_empty_before_posting(posting):
    assert all(t["submit_by"] is None for t in posting.confirm()["targets"])


# ------------------------------------------------------------------ DW-4.3: the morning summary

def local_at(hour, days=0):
    return dt.datetime.now().astimezone().replace(hour=hour, minute=0, second=0, microsecond=0) + dt.timedelta(days=days)


def summary(p, now):
    with p.db.session(Actor.worker()) as conn:
        return alerts.send_morning_summary(conn, p.owner.operator_id, now)


def test_DW_4_3_the_morning_summary_has_a_finish_in_the_app_line(posting):
    posted_all(posting)
    with posting.db.session(posting.owner) as conn:
        lines = alerts.morning_summary(conn, posting.owner.operator_id)
    assert any("2 post(s) are drafts or private uploads" in line and "Finish them in the app" in line for line in lines)


def test_DW_4_3_the_summary_is_sent_once_a_day_after_the_morning_hour_and_replaced_the_next_day(posting):
    posted_all(posting)
    assert summary(posting, local_at(7)) is None                                         # too early
    first = summary(posting, local_at(9))
    assert first and first["severity"] == "info" and "drafts or private uploads" in first["message"]
    assert summary(posting, local_at(10)) is None                                        # once per day
    second = summary(posting, local_at(9, days=1))
    assert second and second["id"] != first["id"]
    open_ones = [r for r in alert_rows(posting, "morning_summary") if r["acknowledged_at"] is None]
    assert [r["id"] for r in open_ones] == [second["id"]]                                # yesterday's was acknowledged


def test_the_summary_is_quiet_when_nothing_waits(produced):
    with produced.db.session(Actor.worker()) as conn:         # nothing accepted, nothing posted
        assert alerts.morning_summary(conn, produced.owner.operator_id) == []
        assert alerts.send_morning_summary(conn, produced.owner.operator_id, local_at(9)) is None
        assert conn.execute("select 1 from alerts where type = 'morning_summary'").fetchall() == []


def test_housekeeping_sweeps_and_sends_the_summary(posting, monkeypatch):
    posted_all(posting)
    swept = []
    monkeypatch.setattr(publish, "sweep", lambda conn, *a, **k: swept.append(1) or 0)
    monkeypatch.setattr(alerts, "MORNING_HOUR", 0)           # any local time counts as morning
    ops.housekeeping(posting.db)
    assert swept == [1]                                      # clean-up item 3: housekeeping calls publish.sweep
    assert len(alert_rows(posting, "morning_summary")) == 1


# ------------------------------------------------------------------ phase 3 review clean-ups

def test_the_target_link_body_is_its_own_model_not_the_source_link_model():
    assert api.TargetLinkIn is not api.LinkIn and set(api.TargetLinkIn.model_fields) == {"url"}
    assert "language" in api.LinkIn.model_fields           # the source-link model is no longer shadowed


def test_the_unused_in_flight_constant_is_gone():
    assert not hasattr(publish, "IN_FLIGHT")


class WorkerDied(BaseException):
    """Stands in for the process dying: not an Exception, so no handler in the stage can turn it into a result."""


def finish_calls(p):
    return [c for c in p.calls("instagram_reels") if c[1].endswith("/media_publish")]


def test_a_finish_that_never_reported_back_is_needs_check_not_a_second_publish(posting, monkeypatch):
    publishing.FAKE_PUBLISHING["mode"]["instagram_reels"] = "processing"
    target = posting.confirm(["instagram_reels"])["targets"][0]
    posting.run_stage(pipeline.stage_publish, target["id"])
    publishing.FAKE_PUBLISHING["mode"].clear()
    real = publish.registry.call

    def dying(conn, stage, method, *args, **kw):
        if method == "finish_publish":
            raise WorkerDied()
        return real(conn, stage, method, *args, **kw)

    with monkeypatch.context() as m:
        m.setattr(publish.registry, "call", dying)
        with pytest.raises(WorkerDied):
            posting.run_stage(pipeline.stage_post_status, target["id"])
    with posting.db.session(Actor.system()) as conn:        # eleven minutes later another status job comes
        conn.execute("update post_targets set checked_at = now() - interval '11 minutes'")
    posting.run_stage(pipeline.stage_post_status, target["id"])
    row = posting.targets()["instagram_reels"]
    assert row["state"] == "needs_check" and "Check the platform" in row["reason"]
    assert finish_calls(posting) == [] and posting.posts() == []                  # Instagram was never asked to publish twice


def test_a_finish_that_is_still_processing_can_be_asked_again_and_published_once(posting):
    publishing.FAKE_PUBLISHING["mode"]["instagram_reels"] = "processing"
    target = posting.confirm(["instagram_reels"])["targets"][0]
    posting.run_stage(pipeline.stage_publish, target["id"])
    posting.run_stage(pipeline.stage_post_status, target["id"])                  # still processing: the claim is handed back
    row = posting.targets()["instagram_reels"]
    assert row["state"] == "processing" and row["attempts"] == 1 and row["checked_at"] is None
    publishing.FAKE_PUBLISHING["mode"].clear()
    posting.run_stage(pipeline.stage_post_status, target["id"])
    assert posting.targets()["instagram_reels"]["state"] == "published" and len(finish_calls(posting)) == 1
    posting.run_stage(pipeline.stage_post_status, target["id"])
    assert len(finish_calls(posting)) == 1
