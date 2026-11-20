"""Decision 31: a swipe right accepts the clip AND is the owner's confirm to post. Migration 019 lets the post_targets guard accept
a checklist without the disclosure-toggle tick only for that swipe flow; every other path keeps the full checklist."""
from __future__ import annotations

import json

import psycopg
import pytest
from fastapi.testclient import TestClient

from clipping import api, publish, review
from clipping.core import AppError, KillSwitchOn
from clipping.db import Actor
from test_18_post_targets import ALL, PLATFORMS, Posting, posting  # noqa: F401  (the fixture)

SWIPE = {"account_and_platform_correct": True, "caption_pasted": True, "hashtags_and_tags_checked": True}


def swipe_clip(p) -> str:
    """A clip still waiting in the feed (Posting already accepted the first one)."""
    with p.db.session(p.owner) as conn:
        return str(review.feed(conn, p.owner.operator_id)[0]["id"])


def swipe(p, clip=None, request_id="swipe-1", actor=None):
    actor = actor or p.owner
    with p.db.session(actor) as conn:
        return publish.swipe_confirm(conn, actor, clip or swipe_clip(p), request_id)


def record(confirm_via="swipe", toggle=False, server=True):
    ticks = {**SWIPE, **({"disclosure_toggle_on": True} if toggle else {})}
    out = {"ticks": ticks, "server": {"account_owned_and_allowed": server}}
    if confirm_via:
        out["confirm_via"] = confirm_via
    return out


def insert_target(p, clip, checklist, platform="tiktok"):
    """Create a post target the way confirm_post does, but with a checklist we choose, to test the database guard on its own."""
    with p.db.session(p.owner) as conn:
        conn.execute("update clips set status = 'ready_to_post' where id = %s and status = 'accepted'", (clip,))
        conn.execute(
            """insert into post_targets (clip_id, platform, account_id, manual, scheduled_at, idempotency_key, request_id, checklist_json, confirmed_by)
               values (%s, %s, %s, true, now(), %s, 'r', %s, %s)""",
            (clip, platform, p.accounts[platform], f"k-{platform}-{json.dumps(checklist, sort_keys=True)}", json.dumps(checklist), p.owner.operator_id))


def system(p, sql, *args):
    with p.db.session(Actor.system()) as conn:
        conn.execute(sql, args)


# ------------------------------------------------------------------ the database guard (migration 019)

def test_a_normal_confirm_without_the_disclosure_toggle_tick_is_still_rejected(posting):
    with pytest.raises(psycopg.errors.RaiseException, match="checklist is not complete"):
        insert_target(posting, posting.clip, record(confirm_via=None))          # no swipe marker: the full checklist is needed


def test_a_normal_confirm_with_the_full_checklist_still_passes(posting):
    insert_target(posting, posting.clip, record(confirm_via=None, toggle=True))


def test_a_swipe_confirm_where_disclosure_is_off_on_a_disclosure_required_campaign_is_rejected(posting):
    system(posting, "update campaign_versions set rules_json = jsonb_set(rules_json, '{disclosure,required_by_brief}', 'true')")
    system(posting, """update clips set compliance_report = jsonb_set(compliance_report, '{deterministic}',
                       '[{"check": "caption_starts_with_disclosure", "status": "fail"}, {"check": "overlay_present_full_duration", "status": "pass"}]') where id = %s""",
           posting.clip)
    with pytest.raises(psycopg.errors.RaiseException, match="checklist is not complete"):
        insert_target(posting, posting.clip, record())


def test_a_swipe_confirm_on_a_clip_that_failed_compliance_is_rejected(posting):
    with posting.db.session(posting.owner) as conn:
        conn.execute("update clips set status = 'ready_to_post' where id = %s", (posting.clip,))
    system(posting, "update clips set compliance_status = 'fail' where id = %s", posting.clip)
    with pytest.raises(psycopg.errors.RaiseException, match="checklist is not complete"):
        insert_target(posting, posting.clip, record())


def test_a_swipe_checklist_with_a_failed_server_check_is_rejected(posting):
    with pytest.raises(psycopg.errors.RaiseException, match="checklist is not complete"):
        insert_target(posting, posting.clip, record(server=False))


def test_a_practice_campaign_is_still_refused_for_a_swipe_checklist(posting):
    system(posting, "update campaigns set is_practice = true where id = %s", posting.produced.campaign_id)
    with pytest.raises(psycopg.errors.RaiseException, match="practice campaigns cannot be posted"):
        insert_target(posting, posting.clip, record())


def test_a_valid_swipe_checklist_passes_the_guard(posting):
    insert_target(posting, posting.clip, record())


# ------------------------------------------------------------------ the swipe flow

def test_a_swipe_accepts_the_clip_and_queues_a_staggered_post_for_every_platform_with_an_audit_row(posting):
    clip = swipe_clip(posting)
    result = swipe(posting, clip)
    assert result["review"]["decision"] == "accept" and result["practice"] is False
    assert [t["platform"] for t in result["targets"]] == ALL and {t["state"] for t in result["targets"]} == {"scheduled"}
    assert all(t["handle"] and t["label"] for t in result["targets"])
    gaps = [(b["scheduled_at"] - a["scheduled_at"]).total_seconds() / 60 for a, b in zip(result["targets"], result["targets"][1:])]
    assert all(g >= 20 for g in gaps)                                                  # the Round E stagger is unchanged
    assert posting.rows("select status from clips where id = %s", clip)[0]["status"] == "ready_to_post"
    stored = posting.rows("select checklist_json from post_targets where clip_id = %s", clip)
    assert all(r["checklist_json"]["confirm_via"] == "swipe" and "disclosure_toggle_on" not in r["checklist_json"]["ticks"] for r in stored)
    audit = posting.rows("select detail as details from audit_log where action = 'post.swipe_confirmed'")
    assert len(audit) == 1
    details = audit[0]["details"]
    assert details["via"] == "swipe" and len(details["caption_hash"]) >= 16 and {t["platform"] for t in details["targets"]} == set(ALL)
    caption = posting.rows("select caption_text from clips where id = %s", clip)[0]["caption_text"]
    assert caption not in json.dumps(details)                                          # a hash, never the caption text


def test_a_disconnected_account_becomes_a_manual_target_and_connected_ones_are_queued(produced, monkeypatch):
    p = Posting(produced, monkeypatch, connected=("tiktok",))
    monkeypatch.setattr(publish, "_now", lambda: p._pin())
    result = swipe(p)
    by_platform = {t["platform"]: t for t in result["targets"]}
    assert by_platform["tiktok"]["manual"] is False and by_platform["instagram_reels"]["manual"] is True and by_platform["youtube_shorts"]["manual"] is True
    manual_jobs = p.rows("select count(*) as n from jobs where type = 'publish'")[0]["n"]
    assert manual_jobs == 1                                                            # only the connected account is sent by the worker


def test_both_tiktok_and_instagram_go_through_the_same_path(posting):
    result = swipe(posting)
    assert {"tiktok", "instagram_reels"} <= {t["platform"] for t in result["targets"]}


def test_undo_inside_the_window_cancels_the_posts_and_puts_the_clip_back(posting):
    clip = swipe_clip(posting)
    done = swipe(posting, clip)
    with posting.db.session(posting.owner) as conn:
        undone = publish.swipe_undo(conn, posting.owner, clip, str(done["review"]["id"]))
        assert undone["cancelled"] == 3
        assert conn.execute("select status from clips where id = %s", (clip,)).fetchone()["status"] == "ready_for_review"
        assert {r["state"] for r in conn.execute("select state from post_targets where clip_id = %s", (clip,)).fetchall()} == {"cancelled"}
        assert clip in [str(c["id"]) for c in review.feed(conn, posting.owner.operator_id)]
    # the same clip can be swiped again afterwards
    again = swipe(posting, clip, request_id="swipe-2")
    assert len(again["targets"]) == 3


def test_undo_after_the_window_is_refused_and_nothing_is_cancelled(posting):
    clip = swipe_clip(posting)
    done = swipe(posting, clip)
    system(posting, "update reviews set created_at = now() - interval '16 seconds' where id = %s", done["review"]["id"])
    with posting.db.session(posting.owner) as conn:
        with pytest.raises(AppError) as err:
            publish.swipe_undo(conn, posting.owner, clip, str(done["review"]["id"]))
        assert err.value.code == "undo_expired"
    assert {r["state"] for r in posting.rows("select state from post_targets where clip_id = %s", clip)} == {"scheduled"}
    assert posting.rows("select status from clips where id = %s", clip)[0]["status"] == "ready_to_post"


def test_undo_is_refused_once_a_post_has_started_sending(posting):
    clip = swipe_clip(posting)
    done = swipe(posting, clip)
    target = posting.rows("select id from post_targets where clip_id = %s order by scheduled_at limit 1", clip)[0]["id"]
    posting.force_state(target, "sending")
    with posting.db.session(posting.owner) as conn:
        with pytest.raises(AppError) as err:
            publish.swipe_undo(conn, posting.owner, clip, str(done["review"]["id"]))
        assert err.value.code == "not_cancellable"


def test_a_practice_campaign_accepts_the_clip_but_never_queues_a_post(posting):
    system(posting, "update campaigns set is_practice = true where id = %s", posting.produced.campaign_id)
    clip = swipe_clip(posting)
    result = swipe(posting, clip)
    assert result["practice"] is True and result["targets"] == [] and result["review"]["decision"] == "accept"
    assert posting.rows("select count(*) as n from post_targets")[0]["n"] == 0


def test_a_clip_that_failed_compliance_does_not_post_and_is_not_accepted(posting):
    clip = swipe_clip(posting)
    system(posting, "update clips set compliance_status = 'fail' where id = %s", clip)
    with pytest.raises(AppError) as err:
        swipe(posting, clip)
    assert err.value.code == "compliance_blocked"
    assert posting.rows("select status from clips where id = %s", clip)[0]["status"] == "ready_for_review"


def test_a_compliance_warning_posts_and_comes_back_as_text_for_the_bar(posting):
    clip = swipe_clip(posting)
    system(posting, "update clips set compliance_status = 'review' where id = %s", clip)
    result = swipe(posting, clip)
    assert result["warning"].startswith("Compliance needs a look") and len(result["targets"]) == 3


def test_the_kill_switch_stops_the_swipe_and_the_clip_stays_in_the_feed(posting):
    clip = swipe_clip(posting)
    system(posting, "update kill_switch set active = true")
    with pytest.raises(KillSwitchOn):
        swipe(posting, clip)
    assert posting.rows("select status from clips where id = %s", clip)[0]["status"] == "ready_for_review"
    assert posting.rows("select count(*) as n from post_targets where clip_id = %s", clip)[0]["n"] == 0


def test_a_replayed_swipe_request_makes_no_second_set_of_targets(posting):
    clip = swipe_clip(posting)
    swipe(posting, clip)
    again = swipe(posting, clip)                                                       # the same request id
    assert len(again["targets"]) == 3
    assert posting.rows("select count(*) as n from post_targets where clip_id = %s", clip)[0]["n"] == 3


def test_the_swipe_endpoint_and_its_undo_work_over_the_api(posting):
    clip = swipe_clip(posting)
    api._owner = posting.owner
    client = TestClient(api.app)
    reply = client.post(f"/api/clips/{clip}/swipe", json={"request_id": "api-1"})
    assert reply.status_code == 200 and len(reply.json()["targets"]) == 3
    undo = client.post(f"/api/clips/{clip}/swipe/undo", json={"review_id": str(reply.json()["review"]["id"])})
    assert undo.status_code == 200 and undo.json()["cancelled"] == 3


def test_the_posting_screen_confirm_cannot_set_the_swipe_marker(posting):
    """The marker comes only from the swipe endpoint: the normal confirm body has no `via`, and without all four ticks it is refused."""
    api._owner = posting.owner
    client = TestClient(api.app)
    other = swipe_clip(posting)
    with posting.db.session(posting.owner) as conn:
        review.accept(conn, posting.owner, other)
    reply = client.post(f"/api/clips/{other}/post", json={"platforms": ["tiktok"], "request_id": "x1", "via": "swipe",
                                                         "ticks": {"tiktok": SWIPE}})
    assert reply.status_code == 400


def test_the_feed_shows_where_a_swipe_would_post_before_the_swipe(posting):
    api._owner = posting.owner
    clips = TestClient(api.app).get("/api/feed").json()["clips"]
    targets = clips[0]["post_targets"]
    assert [t["platform"] for t in targets] == ALL and all(t["handle"] and t["manual"] is False for t in targets)


def test_the_feed_marks_a_platform_without_a_connection_as_manual(produced, monkeypatch):
    p = Posting(produced, monkeypatch, connected=("tiktok",))
    api._owner = p.owner
    targets = {t["platform"]: t for t in TestClient(api.app).get("/api/feed").json()["clips"][0]["post_targets"]}
    assert targets["tiktok"]["manual"] is False and targets["instagram_reels"]["manual"] is True
