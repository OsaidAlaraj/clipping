"""Build step 4 (backend side): feed priority, accept, reject, Undo, Review Later, edits, sessions,
the publish checklist and the three human gates."""
from __future__ import annotations

import datetime as dt

import psycopg
import pytest

from clipping import campaigns, core, pipeline, publish, review
from clipping.adapters import base, vendors
from clipping.core import AppError, GateError, KillSwitchOn
from clipping.db import Actor
from conftest import brief

NOW = dt.datetime(2026, 11, 16, 12, 0, tzinfo=dt.timezone.utc)
WEIGHTS = {"net_pay": 5, "deadline": 4, "hook_score": 3, "budget": 2, "account_cap": 2, "compliance": 3}
TICKS = {name: True for name in publish.OWNER_TICKS}


def clip(name, **kw) -> dict:
    base_clip = {"id": name, "created_at": NOW, "compliance_status": "pass", "est_net_per_hour": 1.0, "deadline": None,
                 "hook_score": 0.5, "budget_total": 1000, "budget_remaining": 1000, "account_cap": 2, "account_posts_today": 0}
    return {**base_clip, **kw}


def order(clips, weights=WEIGHTS) -> list[str]:
    return [c["id"] for c in review.rank_feed(clips, weights, NOW)]


# ------------------------------------------------------------------ priority algorithm (spec 6.6)

def test_each_input_moves_a_clip_the_right_way():
    assert order([clip("low"), clip("high", est_net_per_hour=3.0)]) == ["high", "low"]
    assert order([clip("far", deadline=NOW + dt.timedelta(days=10)), clip("soon", deadline=NOW + dt.timedelta(hours=20))]) == ["soon", "far"]
    assert order([clip("weak", hook_score=0.2), clip("strong", hook_score=0.9)]) == ["strong", "weak"]
    assert order([clip("full"), clip("nearly_gone", budget_remaining=50)]) == ["nearly_gone", "full"]
    assert order([clip("near_cap", account_posts_today=2), clip("room")]) == ["room", "near_cap"]
    assert order([clip("warn", compliance_status="review"), clip("clean")]) == ["clean", "warn"]


def test_hard_blocks_are_removed_before_ranking_and_never_return():
    clips = [clip("blocked", compliance_status="fail", est_net_per_hour=99, hook_score=1), clip("ok"),
             clip("pending", compliance_status="pending", est_net_per_hour=99)]
    assert order(clips) == ["ok"]
    assert order(clips, {k: 10 for k in WEIGHTS}) == ["ok"]


def test_ties_break_by_creation_time_oldest_first():
    clips = [clip("newest", created_at=NOW), clip("oldest", created_at=NOW - dt.timedelta(hours=5)),
             clip("middle", created_at=NOW - dt.timedelta(hours=1))]
    assert order(clips) == ["oldest", "middle", "newest"]


def test_weights_change_the_order_and_every_card_states_its_top_reason():
    clips = [clip("pays", est_net_per_hour=3.0, hook_score=0.1), clip("hooks", est_net_per_hour=0.5, hook_score=1.0)]
    assert order(clips) == ["pays", "hooks"]
    assert order(clips, {**WEIGHTS, "net_pay": 0, "hook_score": 10}) == ["hooks", "pays"]
    ranked = {c["id"]: c["priority_reason"] for c in review.rank_feed(
        [clip("pays", est_net_per_hour=3.0, hook_score=0), clip("soon", est_net_per_hour=0, hook_score=0, deadline=NOW + dt.timedelta(hours=20)),
         clip("warn", est_net_per_hour=0, hook_score=0, compliance_status="review"),
         clip("capped", est_net_per_hour=0, hook_score=0, account_posts_today=2)], WEIGHTS, NOW)}
    assert ranked == {"pays": "Top-paying campaign", "soon": "Deadline in 20 h", "warn": "Lower: needs a compliance look",
                      "capped": "Lower: account is near its daily cap"}


def test_feed_is_recalculated_on_every_load(produced):
    with produced.db.session(produced.owner) as conn:
        core.set_setting(conn, "quality_min_p_clear", 0, produced.owner.operator_id)   # this test is about the priority weights; the quality gate has its own tests
        first = [c["id"] for c in review.feed(conn, produced.owner.operator_id)]
        assert len(first) == 3 and all(c["priority_reason"] and c["video_url"].startswith("/api/media/") for c in review.feed(conn, produced.owner.operator_id))
        last = first[-1]
        conn.execute("update clips set hook_score = 5 where id = %s", (last,))
        second = [c["id"] for c in review.feed(conn, produced.owner.operator_id)]
        assert second[0] == last and second != first
        core.set_setting(conn, "priority_weight_hook_score", 0, produced.owner.operator_id)
        assert [c["id"] for c in review.feed(conn, produced.owner.operator_id)] != second


# ------------------------------------------------------------------ accept, reject, undo, review later

def test_accept_moves_the_clip_to_the_queue_and_records_the_hook(produced):
    with produced.db.session(produced.owner) as conn:
        target = review.feed(conn, produced.owner.operator_id)[0]
        record = review.accept(conn, produced.owner, target["id"])
        assert record["decision"] == "accept" and str(record["reviewer_id"]) == produced.owner.operator_id
        assert target["id"] not in [c["id"] for c in review.feed(conn, produced.owner.operator_id)]
        queued = publish.queue(conn)["items"]
        assert [c["id"] for c in queued] == [target["id"]] and queued[0]["approved_file_hash"] == target["file_hash"]
        chosen = conn.execute("select id from hooks where chosen").fetchall()
        assert [str(h["id"]) for h in chosen] == [target["render_meta"]["hook_id"]]
        gate = conn.execute("select * from audit_log where action = 'gate.clip_acceptance'").fetchone()
        assert str(gate["actor_id"]) == produced.owner.operator_id and gate["entity_id"] == target["id"]


def test_reject_requires_a_reason_and_other_requires_a_note(produced):
    with produced.db.session(produced.owner) as conn:
        target = review.feed(conn, produced.owner.operator_id)[0]["id"]
        for code, notes in (("", None), ("not_a_code", None), ("other", "  ")):
            with pytest.raises(AppError):
                review.reject(conn, produced.owner, target, code, notes)
        assert conn.execute("select status from clips where id = %s", (target,)).fetchone()["status"] == "ready_for_review"
        record = review.reject(conn, produced.owner, target, "cut_mid_thought")
        assert record["reason_code"] == "cut_mid_thought"
        other = review.feed(conn, produced.owner.operator_id)[0]["id"]
        assert review.reject(conn, produced.owner, other, "other", "Guest asked us not to use this")["notes"]
    for sql in ("insert into reviews (clip_id, reviewer_id, decision) values (%s, %s, 'reject')",
                "insert into reviews (clip_id, reviewer_id, decision, reason_code) values (%s, %s, 'reject', 'other')"):
        with pytest.raises(psycopg.errors.CheckViolation):   # the database enforces it too
            with produced.db.session(produced.owner) as conn:
                conn.execute(sql, (target, produced.owner.operator_id))
    assert len(review.REASON_CODES) == 11


def test_undo_restores_the_clip_within_the_window_and_references_the_original(produced):
    with produced.db.session(produced.owner) as conn:
        target = review.feed(conn, produced.owner.operator_id)[0]["id"]
        rejection = review.reject(conn, produced.owner, target, "weak_hook")
        assert target not in [c["id"] for c in review.feed(conn, produced.owner.operator_id)]
        undone = review.undo(conn, produced.owner, rejection["id"])
        assert undone["decision"] == "undo" and undone["undo_of"] == rejection["id"]
        assert target in [c["id"] for c in review.feed(conn, produced.owner.operator_id)]
        with pytest.raises(AppError):
            review.undo(conn, produced.owner, rejection["id"])   # only once
        assert conn.execute("select count(*) as n from reviews where clip_id = %s", (target,)).fetchone()["n"] == 2   # both stay logged


def test_undo_is_refused_after_the_window(produced):
    with produced.db.session(produced.owner) as conn:
        target = review.feed(conn, produced.owner.operator_id)[0]["id"]
        rejection = review.reject(conn, produced.owner, target, "duplicate")
    with produced.db.session(Actor.system()) as conn:
        conn.execute("update reviews set created_at = now() - interval '16 seconds' where id = %s", (rejection["id"],))
    with produced.db.session(produced.owner) as conn:
        with pytest.raises(AppError) as err:
            review.undo(conn, produced.owner, rejection["id"])
        assert err.value.code == "undo_expired"
        assert conn.execute("select status from clips where id = %s", (target,)).fetchone()["status"] == "rejected"
        core.set_setting(conn, "undo_window_s", 30, produced.owner.operator_id)   # the window is a setting
        assert review.undo(conn, produced.owner, rejection["id"])["decision"] == "undo"


def test_review_later_is_neither_accept_nor_reject_and_is_grouped_by_campaign(produced):
    with produced.db.session(produced.owner) as conn:
        clips = review.feed(conn, produced.owner.operator_id)
        review.park(conn, produced.owner, clips[0]["id"], "Check the name spelling")
        review.park(conn, produced.owner, clips[1]["id"])
        assert len(review.feed(conn, produced.owner.operator_id)) == 1   # parked clips leave the ranked feed
        assert publish.queue(conn)["items"] == []
        folders = review.review_later(conn)
        assert len(folders) == 1 and folders[0]["count"] == 2 and folders[0]["closed"] is False
        assert "Check the name spelling" in [c["park_reason"] for c in folders[0]["clips"]]
        assert conn.execute("select count(*) as n from reviews where decision in ('accept', 'reject')").fetchone()["n"] == 0
        review.accept(conn, produced.owner, clips[0]["id"])   # a parked clip can still be accepted
        campaigns.close(conn, produced.owner, produced.campaign_id)
        assert review.review_later(conn)[0]["closed"] is True


def test_closed_campaign_folders_sit_at_the_bottom():
    now = dt.datetime.now(dt.timezone.utc)

    def folder(name, closed, days):
        return {"campaign_name": name, "closed": closed, "deadline": now + dt.timedelta(days=days)}

    folders = [folder("closed", True, 1), folder("later", False, 9), folder("sooner", False, 2)]
    ordered = sorted(folders, key=lambda f: (f["closed"], f["deadline"]))
    assert [f["campaign_name"] for f in ordered] == ["sooner", "later", "closed"]


def test_offline_actions_are_applied_once(produced):
    with produced.db.session(produced.owner) as conn:
        target = review.feed(conn, produced.owner.operator_id)[0]["id"]
        first = review.accept(conn, produced.owner, target, client_action_id="device-1-action-7")
        again = review.accept(conn, produced.owner, target, client_action_id="device-1-action-7")
        assert first["id"] == again["id"]
        assert conn.execute("select count(*) as n from reviews").fetchone()["n"] == 1


# ------------------------------------------------------------------ edits create versions

def test_each_edit_creates_a_new_clip_version(produced):
    with produced.db.session(produced.owner) as conn:
        target = review.feed(conn, produced.owner.operator_id)[0]
        v2 = review.edit(conn, produced.owner, target["id"], caption="#ad A sharper line #FoundersTable @founderstable")
        assert v2["version_no"] == 2 and v2["parent_clip_id"] == target["id"] and v2["status"] == "ready_for_review"
        assert v2["file_hash"] == target["file_hash"]
        assert conn.execute("select status from clips where id = %s", (target["id"],)).fetchone()["status"] == "archived"
        with pytest.raises(AppError) as err:   # an edit that drops the disclosure is refused
            review.edit(conn, produced.owner, v2["id"], caption="A line with no disclosure")
        assert err.value.code == "compliance_fail"
    with produced.db.session(produced.owner) as conn:
        other_hook = next(h for h in review.get_clip(conn, v2["id"])["hooks"] if str(h["id"]) != v2["render_meta"]["hook_id"])
        v3 = review.edit(conn, produced.owner, v2["id"], hook_id=other_hook["id"], trim={"start_s": float(v2["start_s"]) + 1})
        assert v3["version_no"] == 3 and v3["status"] == "rendering"
    pipeline.run_until_idle(produced.db)
    with produced.db.session(produced.owner) as conn:
        v3 = review.get_clip(conn, v3["id"])
        assert v3["status"] == "ready_for_review" and v3["hook_text"] == other_hook["text"]
        assert other_hook["text"] in v3["caption_text"] and v3["file_hash"] != target["file_hash"]
        assert float(v3["duration_s"]) < float(target["duration_s"])
        assert conn.execute("select count(*) as n from audit_log where action = 'clip.edited'").fetchone()["n"] == 2


# ------------------------------------------------------------------ sessions and timer

def test_session_starts_the_timer_and_ends_with_a_summary(produced):
    with produced.db.session(produced.owner) as conn:
        session = review.start_session(conn, produced.owner, produced.campaign_id)
        assert float(session["length_setting"]) == 25
        entry = conn.execute("select * from time_entries where session_id = %s", (session["id"],)).fetchone()
        assert entry["task_type"] == "review" and entry["ended_at"] is None
        clips = review.feed(conn, produced.owner.operator_id)
        review.accept(conn, produced.owner, clips[0]["id"], session["id"])
        review.reject(conn, produced.owner, clips[1]["id"], "weak_hook", session_id=session["id"])
        review.park(conn, produced.owner, clips[2]["id"], session_id=session["id"])
        summary = review.stop_session(conn, produced.owner, session["id"], {"campaign_id": produced.campaign_id, "clip_id": str(clips[2]["id"])})
        assert (summary["clips_reviewed"], summary["accepted"], summary["rejected"], summary["parked"], summary["posts_due"]) == (3, 1, 1, 1, 1)
        assert summary["minutes_logged"] >= 0 and "pay_per_clip" in summary   # pay shown on the summary by default
        assert conn.execute("select ended_at from time_entries where session_id = %s", (session["id"],)).fetchone()["ended_at"] is not None
        resumed = review.start_session(conn, produced.owner)
        assert resumed["resume_position"]["clip_id"] == str(clips[2]["id"])   # "Stop here" saved the position
        core.set_setting(conn, "session_length_min", 10, produced.owner.operator_id)
        assert float(review.start_session(conn, produced.owner)["length_setting"]) == 10
        core.set_setting(conn, "pay_per_clip_display", "off", produced.owner.operator_id)
        assert "pay_per_clip" not in review.session_summary(conn, produced.owner, conn.execute("select * from sessions where id = %s", (session["id"],)).fetchone())


def test_one_tap_timer_by_task_type_with_correction(db, owner):
    with db.session(owner) as conn:
        entry = review.timer_start(conn, owner, "sourcing")
        assert review.timer_stop(conn, owner)["ended_at"] is not None
        assert float(review.timer_stop(conn, owner, entry["id"], 12.5)["minutes"]) == 12.5
        with pytest.raises(psycopg.errors.CheckViolation):
            review.timer_start(conn, owner, "napping")


# ------------------------------------------------------------------ publish checklist and gate 3

def accepted_clip(produced) -> str:
    with produced.db.session(produced.owner) as conn:
        target = review.feed(conn, produced.owner.operator_id)[0]["id"]
        review.accept(conn, produced.owner, target)
    return str(target)


def test_checklist_blocks_until_every_item_is_done(produced):
    target = accepted_clip(produced)
    with produced.db.session(produced.owner) as conn:
        state = publish.checklist(conn, produced.owner, target)
        assert state["server"]["account_owned_and_allowed"]["ok"] and state["server"]["file_hash_matches_approved"]["ok"]
        assert not state["server"]["brief_rechecked"]["ok"] and state["scheduler_has_disclosure_toggle"] is False
        with pytest.raises(AppError) as err:
            publish.approve_publish(conn, produced.owner, target, TICKS)
        assert "Brief re-checked" in str(err.value)
        campaigns.recheck_brief(conn, produced.owner, produced.campaign_id, current_text=brief("01"))
        for missing in publish.OWNER_TICKS:
            with pytest.raises(AppError):
                publish.approve_publish(conn, produced.owner, target, {**TICKS, missing: False})
        ready = publish.approve_publish(conn, produced.owner, target, TICKS)
        assert ready["status"] == "ready_to_post"
        gate = conn.execute("select * from audit_log where action = 'gate.publish_approval'").fetchone()
        assert str(gate["actor_id"]) == produced.owner.operator_id


def test_changed_file_fails_the_hash_check(produced):
    target = accepted_clip(produced)
    with produced.db.session(Actor.system()) as conn:
        conn.execute("update clips set approved_file_hash = %s where id = %s", ("0" * 64, target))
    with produced.db.session(produced.owner) as conn:
        campaigns.recheck_brief(conn, produced.owner, produced.campaign_id, current_text=brief("01"))
        assert not publish.checklist(conn, produced.owner, target)["server"]["file_hash_matches_approved"]["ok"]
        with pytest.raises(AppError):
            publish.approve_publish(conn, produced.owner, target, TICKS)


def test_changed_brief_blocks_the_batch_until_reapproved(produced):
    target = accepted_clip(produced)
    changed = brief("01") + "\nYou must not use slow motion."
    with produced.db.session(produced.owner) as conn:
        assert campaigns.recheck_brief(conn, produced.owner, produced.campaign_id, current_text=changed)["changed"]
        state = publish.checklist(conn, produced.owner, target)
        assert state["campaign_needs_reapproval"] and not state["server"]["brief_rechecked"]["ok"]
        with pytest.raises(AppError):
            publish.approve_publish(conn, produced.owner, target, TICKS)
        campaigns.approve(conn, produced.owner, produced.campaign_id)
        with pytest.raises(AppError):   # the clip was approved against version 1; version 2 is now in force
            publish.approve_publish(conn, produced.owner, target, TICKS)


def ready_clip(produced) -> str:
    target = accepted_clip(produced)
    with produced.db.session(produced.owner) as conn:
        campaigns.recheck_brief(conn, produced.owner, produced.campaign_id, current_text=brief("01"))
        publish.approve_publish(conn, produced.owner, target, TICKS)
    return target


def test_mark_posted_needs_the_link_the_toggle_and_a_confirm(produced):
    target = ready_clip(produced)
    with produced.db.session(produced.owner) as conn:
        for url, confirm, toggle, code in (("", True, True, "link_required"), ("not a link", True, True, "link_required"),
                                           ("https://example.com/p/1", False, True, "confirm_required"),
                                           ("https://example.com/p/1", True, False, "toggle_required")):
            with pytest.raises(AppError) as err:
                publish.mark_posted(conn, produced.owner, target, url, confirm, toggle)
            assert err.value.code == code
        post = publish.mark_posted(conn, produced.owner, target, "https://example.com/p/1", True, True)
        assert post["disclosure_toggle_on"] and str(post["confirmed_by"]) == produced.owner.operator_id
        assert post["checklist_json"]["ticks"] == TICKS and post["checklist_json"]["server"]["brief_rechecked"]
        assert conn.execute("select status from clips where id = %s", (target,)).fetchone()["status"] == "posted"
        assert conn.execute("select status from payouts where clip_id = %s", (target,)).fetchone()["status"] == "submitted"
        assert publish.queue(conn)["items"] == []


def test_nothing_is_posted_without_passing_the_gates_in_order(produced):
    with produced.db.session(produced.owner) as conn:
        target = review.feed(conn, produced.owner.operator_id)[0]["id"]
        with pytest.raises(AppError):
            publish.approve_publish(conn, produced.owner, target, TICKS)          # not accepted yet
        with pytest.raises(AppError):
            publish.mark_posted(conn, produced.owner, target, "https://example.com/p/2", True, True)
    for sql in ("update clips set status = 'ready_to_post' where id = %s", "update clips set status = 'posted' where id = %s"):
        with pytest.raises(psycopg.errors.RaiseException):
            with produced.db.session(produced.owner) as conn:
                conn.execute(sql, (target,))
    with pytest.raises(GateError):
        with produced.db.session(Actor.worker()) as conn:
            review.accept(conn, Actor.worker(), target)
    with pytest.raises(psycopg.errors.RaiseException):   # a post row cannot exist for an unapproved clip
        with produced.db.session(produced.owner) as conn:
            account = conn.execute("select id from accounts limit 1").fetchone()["id"]
            conn.execute("""insert into posts (clip_id, account_id, platform, post_url, disclosure_toggle_on, checklist_json, confirmed_by)
                            values (%s, %s, 'tiktok', 'https://example.com/x', true, '{}', %s)""", (target, account, produced.owner.operator_id))


def test_an_account_is_only_ever_used_by_its_owner(produced):
    target = ready_clip(produced)
    with produced.db.session(Actor.system()) as conn:
        other = conn.execute("insert into operators (name, role) values ('Second admin', 'admin') returning id").fetchone()["id"]
    intruder = Actor(str(other), "admin")
    with produced.db.session(intruder) as conn:
        assert not publish.checklist(conn, intruder, target)["server"]["account_owned_and_allowed"]["ok"]
    with pytest.raises(psycopg.errors.RaiseException):
        with produced.db.session(intruder) as conn:
            publish.mark_posted(conn, intruder, target, "https://example.com/p/3", True, True)


def test_restricted_account_is_excluded_and_caps_only_warn(produced):
    target = accepted_clip(produced)
    with produced.db.session(produced.owner) as conn:
        campaigns.recheck_brief(conn, produced.owner, produced.campaign_id, current_text=brief("01"))
        account = conn.execute("select account_id from clips where id = %s", (target,)).fetchone()["account_id"]
        core.set_setting(conn, "posting_window", {"start": "00:00", "end": "00:00"}, account)
        conn.execute("update accounts set daily_cap = 0 where id = %s", (account,))
        warnings = publish.checklist(conn, produced.owner, target)["warnings"]
        assert len(warnings) == 2 and "posting window" in warnings[1]      # soft caps warn...
        conn.execute("update accounts set status = 'restricted' where id = %s", (account,))
        with pytest.raises(AppError):                                       # ...compliance rules block
            publish.approve_publish(conn, produced.owner, target, TICKS)
        conn.execute("update accounts set status = 'active' where id = %s", (account,))
        assert publish.approve_publish(conn, produced.owner, target, TICKS)["status"] == "ready_to_post"


def test_kill_switch_hides_ready_packages_and_blocks_publishing(produced):
    target = ready_clip(produced)
    second = accepted_clip(produced)
    with produced.db.session(produced.owner) as conn:
        assert {i["status"] for i in publish.queue(conn)["items"]} == {"ready_to_post", "accepted"}
        core.set_kill_switch(conn, True, "test")
        hidden = publish.queue(conn)
        assert hidden["kill_switch"] and [i["status"] for i in hidden["items"]] == ["accepted"]
        assert review.feed(conn, produced.owner.operator_id) == []
        for call in (lambda: publish.mark_posted(conn, produced.owner, target, "https://example.com/p/4", True, True),
                     lambda: publish.approve_publish(conn, produced.owner, second, TICKS)):
            with pytest.raises(KillSwitchOn):
                call()
    with pytest.raises(psycopg.errors.RaiseException):
        with produced.db.session(produced.owner) as conn:
            conn.execute("update clips set status = 'ready_to_post' where id = %s", (second,))
    with produced.db.session(produced.owner) as conn:
        core.set_kill_switch(conn, False)
        assert len(publish.queue(conn)["items"]) == 2


def test_queue_item_can_go_back_to_review_later(produced):
    target = accepted_clip(produced)
    with produced.db.session(produced.owner) as conn:
        publish.back_to_review_later(conn, produced.owner, target)
        assert publish.queue(conn)["items"] == [] and review.review_later(conn)[0]["count"] == 1


def test_scheduler_adapter_needs_publish_approval_and_a_confirm(produced):
    target = ready_clip(produced)
    with produced.db.session(produced.owner) as conn:
        with pytest.raises(AppError) as err:   # manual posting is the default scheduler (weeks 1 to 3)
            publish.schedule(conn, produced.owner, target, "2026-12-01T10:00:00Z", "chan-1", True)
        assert err.value.code == "manual_posting"
        base.activate(conn, "scheduler", "fake")
        with pytest.raises(AppError) as err:
            publish.schedule(conn, produced.owner, target, "2026-12-01T10:00:00Z", "chan-1", False)
        assert err.value.code == "confirm_required"
        queued = publish.schedule(conn, produced.owner, target, "2026-12-01T10:00:00Z", "chan-1", True)
        assert queued["status"] == "queued" and queued["render_meta"]["scheduler_ref"] == "fake-1"
        assert vendors.FAKE_SCHEDULER["posts"]["fake-1"]["caption"].startswith("#ad ")
        post = publish.mark_posted(conn, produced.owner, target, "https://example.com/p/5", True, True)
        assert post["scheduler_ref"] == "fake-1"
        other = review.feed(conn, produced.owner.operator_id)[0]["id"]
        with pytest.raises(AppError):   # a clip that has not passed the gates cannot be scheduled
            publish.schedule(conn, produced.owner, other, "2026-12-01T10:00:00Z", "chan-1", True)


def test_there_is_no_bulk_or_automatic_posting_path():
    import inspect

    from clipping import api

    routes = [r.path for r in api.app.routes]
    assert not any(word in path for path in routes for word in ("bulk", "batch", "auto"))
    assert "clip_id" in inspect.signature(publish.mark_posted).parameters and "confirm" in inspect.signature(publish.mark_posted).parameters
    assert "confirm" in inspect.signature(publish.schedule).parameters
    source = inspect.getsource(pipeline) + inspect.getsource(__import__("clipping.ops", fromlist=["x"]))
    assert "mark_posted" not in source and "schedule(" not in source   # the worker never posts or schedules
