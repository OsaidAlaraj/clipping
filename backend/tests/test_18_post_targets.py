"""Phase E, phase 3: one confirm tap becomes staggered per-platform post targets; the worker sends each exactly once; every
outcome lands on its target, and a public link becomes a post record. Fakes only (fake OAuth, fake publishing)."""
from __future__ import annotations

import datetime as dt

import psycopg
import pytest
from fastapi.testclient import TestClient

from clipping import api, campaigns, core, pipeline, publish, review
from clipping.adapters import publishing
from clipping.alerts import registry
from clipping.core import AppError, GateError
from clipping.db import Actor
from conftest import brief
from test_16_connections import connect, second_owner
from test_4_review_publish import TICKS, accepted_clip

PLATFORMS = ("youtube_shorts", "instagram_reels", "tiktok")
ALL = list(PLATFORMS)
URLS = {"youtube_shorts": "https://www.youtube.com/shorts/OwnerPasted1", "instagram_reels": "https://www.instagram.com/reel/OwnerPasted1/",
        "tiktok": "https://www.tiktok.com/@own_tt/video/7300000000000000001"}


# ------------------------------------------------------------------ helpers

class Posting:
    """An accepted, brief-checked clip and the owner's three connected accounts."""

    def __init__(self, produced, monkeypatch, connected=PLATFORMS):
        self.db, self.owner, self.produced = produced.db, produced.owner, produced
        with self.db.session(self.owner) as conn:
            self.accounts = {r["platform"]: str(r["id"]) for r in conn.execute("select id, platform from accounts").fetchall()}
        for platform in connected:
            connect(self.db, self.owner, platform, self.accounts[platform])
        self.clip = accepted_clip(produced)
        with self.db.session(self.owner) as conn:
            campaigns.recheck_brief(conn, self.owner, produced.campaign_id, current_text=brief("01"))
        self.at(10, 0)
        self.monkeypatch = monkeypatch

    def unpin(self):
        """Use the real clock again (for tests that age rows in the database)."""
        self._pin = lambda: dt.datetime.now().astimezone()

    def force_state(self, target_id, state, **cols):
        """Put a target in a state the legal moves would not reach, to set up a test."""
        sets = ", ".join(f"{k} = %s" for k in ("state", *cols))
        with self.db.session(Actor.system()) as conn:
            conn.execute("alter table post_targets disable trigger post_targets_state")
            conn.execute(f"update post_targets set {sets} where id = %s", (state, *cols.values(), target_id))
            conn.execute("alter table post_targets enable trigger post_targets_state")

    def at(self, hour, minute=0, days=0):
        """Pin the local clock the stagger is planned from."""
        self.now = dt.datetime.now().astimezone().replace(hour=hour, minute=minute, second=0, microsecond=0) + dt.timedelta(days=days)
        self._pin = lambda: self.now

    def confirm(self, platforms=ALL, request_id="req-1", ticks=None, actor=None, clip=None):
        actor = actor or self.owner
        ticks = {p: TICKS for p in PLATFORMS} if ticks is None else ticks
        with self.db.session(actor) as conn:
            return publish.confirm_post(conn, actor, clip or self.clip, platforms, ticks, request_id)

    def rows(self, query, *args):
        with self.db.session(Actor.system()) as conn:
            return conn.execute(query, args).fetchall()

    def targets(self, state=None):
        return {r["platform"]: r for r in self.rows("select * from post_targets where (%s::text is null or state = %s) order by scheduled_at", state, state)}

    def posts(self):
        return self.rows("select * from posts order by created_at")

    def run_worker(self):
        return pipeline.run_until_idle(self.db)

    def run_stage(self, handler, target_id, attempts=1):
        with self.db.session(Actor.worker()) as conn:
            return handler(conn, {"payload": {"target_id": str(target_id)}, "attempts": attempts})

    def calls(self, platform=None):
        return [c for c in publishing.FAKE_PUBLISHING["calls"] if platform in (None, c[0])]


@pytest.fixture
def posting(produced, monkeypatch):
    p = Posting(produced, monkeypatch)
    monkeypatch.setattr(publish, "_now", lambda: p._pin())
    return p


def local(value: dt.datetime) -> dt.datetime:
    return value.astimezone()


def alert_types(p) -> list[str]:
    return [r["type"] for r in p.rows("select type from alerts")]


# ------------------------------------------------------------------ DW-3.1: one tap, staggered targets

def test_DW_3_1_one_confirm_for_three_platforms_makes_three_targets_staggered_inside_each_window(posting):
    result = posting.confirm()
    targets = result["targets"]
    assert [t["platform"] for t in targets] == ALL and {t["state"] for t in targets} == {"scheduled"} and not any(t["manual"] for t in targets)
    times = [t["scheduled_at"] for t in targets]
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(times, times[1:])]
    assert all(g >= 20 for g in gaps) and len(times) == 3
    for when in times:
        assert dt.time(9, 0) <= local(when).time() <= dt.time(22, 0)
    assert local(times[0]) == posting.now                                            # the first goes at the tap, the rest follow
    jobs = posting.rows("select run_after, payload from jobs where type = 'publish' order by run_after")
    assert [j["run_after"] for j in jobs] == times                                   # the worker will not touch one before its time
    assert posting.rows("select status from clips where id = %s", posting.clip)[0]["status"] == "ready_to_post"


def test_DW_3_1_a_replayed_request_id_creates_no_targets_and_no_jobs(posting):
    first = posting.confirm()
    again = posting.confirm()
    assert [t["id"] for t in again["targets"]] == [t["id"] for t in first["targets"]]
    assert len(posting.rows("select 1 from post_targets")) == 3 and len(posting.rows("select 1 from jobs where type = 'publish'")) == 3


def test_DW_3_1_the_stagger_is_the_setting_and_never_less_than_its_floor(posting):
    with posting.db.session(posting.owner) as conn:
        core.set_setting(conn, "post_stagger_min", 45, posting.owner.operator_id)
        with pytest.raises(AppError):
            core.set_setting(conn, "post_stagger_min", 0, posting.owner.operator_id)   # hard rule 4: no blast
    times = [t["scheduled_at"] for t in posting.confirm()["targets"]]
    assert all((b - a).total_seconds() >= 45 * 60 for a, b in zip(times, times[1:]))


def test_DW_3_1_a_window_that_closes_moves_the_rest_to_the_next_morning(posting):
    posting.at(21, 50)
    times = [local(t["scheduled_at"]) for t in posting.confirm()["targets"]]
    assert times[0] == posting.now and times[1] == posting.now.replace(hour=9, minute=0) + dt.timedelta(days=1)
    assert times[2] == times[1] + dt.timedelta(minutes=20)


def test_DW_3_1_a_window_already_closed_today_means_the_next_window(posting):
    posting.at(23, 30)
    times = [local(t["scheduled_at"]) for t in posting.confirm()["targets"]]
    assert times[0] == posting.now.replace(hour=9, minute=0) + dt.timedelta(days=1) and times == sorted(times)


def test_DW_3_1_each_target_uses_its_own_accounts_window(posting):
    with posting.db.session(posting.owner) as conn:
        core.set_setting(conn, "posting_window", {"start": "11:00", "end": "12:00"}, posting.accounts["instagram_reels"])
    times = {t["platform"]: local(t["scheduled_at"]) for t in posting.confirm()["targets"]}
    assert times["youtube_shorts"] == posting.now and times["instagram_reels"] == posting.now.replace(hour=11)
    assert times["tiktok"] == times["instagram_reels"] + dt.timedelta(minutes=20)    # still after the stagger from the one before


@pytest.mark.parametrize("window, hour, minute, expect", [
    ({"start": "09:00", "end": "22:00"}, 10, 15, (10, 15)),        # inside: unchanged
    ({"start": "09:00", "end": "22:00"}, 7, 0, (9, 0)),            # before it opens: today's start
    ({"start": "09:00", "end": "22:00"}, 22, 1, (9, 0)),           # after it closes: tomorrow's start
    ({"start": "22:00", "end": "06:00"}, 23, 0, (23, 0)),          # overnight window, late evening
    ({"start": "22:00", "end": "06:00"}, 3, 0, (3, 0)),            # overnight window, small hours
    ({"start": "22:00", "end": "06:00"}, 12, 0, (22, 0)),          # overnight window, midday waits for the evening
    ({"start": "00:00", "end": "00:00"}, 10, 0, (0, 0))])          # a zero-width window opens once a day
def test_next_slot_finds_the_first_moment_inside_the_window(window, hour, minute, expect):
    earliest = dt.datetime(2026, 11, 18, hour, minute, tzinfo=dt.timezone.utc)
    slot = publish.next_slot(window, earliest)
    assert (slot.hour, slot.minute) == expect and slot >= earliest and slot - earliest < dt.timedelta(days=1, minutes=1)


def test_a_malformed_posting_window_is_refused_in_plain_words():
    with pytest.raises(AppError) as err:
        publish.next_slot({"start": "nine", "end": "22:00"}, dt.datetime.now().astimezone())
    assert err.value.code == "bad_window"


# ------------------------------------------------------------------ DW-3.2: refusals, at the API and in the database

def refuses(p, code, **kw):
    with pytest.raises(AppError) as err:
        p.confirm(**kw)
    assert err.value.code == code
    assert p.rows("select 1 from post_targets") == []          # all or nothing


def insert_target(p, actor=None, clip=None, platform="youtube_shorts", account=None, checklist=None, state="scheduled", request="direct-1"):
    actor = actor or p.owner
    record = checklist if checklist is not None else {"ticks": {t: True for t in publish.OWNER_TICKS}, "server": {"a": True}}
    with p.db.session(actor) as conn:
        return conn.execute(
            """insert into post_targets (clip_id, platform, account_id, scheduled_at, idempotency_key, request_id, checklist_json, confirmed_by, state)
               values (%s, %s, %s, now(), %s, %s, %s, %s, %s) returning *""",
            (clip or p.clip, platform, account or p.accounts[platform], f"k-{request}-{platform}", request, psycopg.types.json.Jsonb(record),
             actor.operator_id or p.owner.operator_id, state)).fetchone()


def approve(p):
    """The clip as gate 3 leaves it, for tests that insert a target directly."""
    with p.db.session(p.owner) as conn:
        publish.approve_publish(conn, p.owner, p.clip, TICKS)


def make_practice(p):
    with p.db.session(Actor.system()) as conn:
        conn.execute("update campaigns set is_practice = true where id = %s", (p.produced.campaign_id,))


def test_DW_3_2_a_practice_campaign_is_refused_by_the_api_and_the_database(posting):
    # the database first: the clip is already approved, then its campaign becomes a practice one
    posting.confirm(["tiktok"], request_id="before")
    make_practice(posting)
    with pytest.raises(psycopg.errors.RaiseException, match="practice"):
        insert_target(posting, platform="youtube_shorts")
    with pytest.raises(AppError) as err:
        posting.confirm(["youtube_shorts"], request_id="after")
    assert err.value.code == "practice_no_posting"
    assert len(posting.rows("select 1 from post_targets")) == 1


@pytest.mark.parametrize("actor", [Actor.worker(), Actor.system()])
def test_DW_3_2_a_worker_or_system_actor_cannot_confirm_or_insert_a_target(posting, actor):
    with pytest.raises(GateError):
        posting.confirm(actor=actor)
    with pytest.raises(psycopg.errors.Error):                                       # the database says no as well (not a human)
        insert_target(posting, actor=actor)
    assert posting.rows("select 1 from post_targets") == []


def test_DW_3_2_an_incomplete_checklist_is_refused_by_the_api_and_the_database(posting):
    missing = {p: {**TICKS, "caption_pasted": False} for p in PLATFORMS}
    refuses(posting, "checklist_incomplete", ticks=missing)
    refuses(posting, "checklist_incomplete", ticks={})
    refuses(posting, "checklist_incomplete", ticks={"tiktok": TICKS}, platforms=["tiktok", "youtube_shorts"])   # per platform
    approve(posting)
    with pytest.raises(psycopg.errors.RaiseException, match="checklist is not complete"):
        insert_target(posting, checklist={"ticks": {**{t: True for t in publish.OWNER_TICKS}, "caption_pasted": False}, "server": {"a": True}})
    with pytest.raises(psycopg.errors.RaiseException, match="checklist is not complete"):
        insert_target(posting, checklist={"ticks": {t: True for t in publish.OWNER_TICKS}, "server": {"a": False}})
    with pytest.raises(psycopg.errors.RaiseException, match="checklist is not complete"):
        insert_target(posting, checklist={})


def test_DW_3_2_the_server_checks_must_pass_for_each_platform(posting):
    approve(posting)
    with posting.db.session(Actor.system()) as conn:
        conn.execute("update accounts set status = 'warned' where platform = 'tiktok'")
    with pytest.raises(AppError) as err:
        posting.confirm(["youtube_shorts", "tiktok"])
    assert err.value.code == "checklist_incomplete" and "TikTok" in str(err.value)
    with pytest.raises(psycopg.errors.RaiseException, match="excluded from posting"):
        insert_target(posting, platform="tiktok")


def test_DW_3_2_the_kill_switch_is_refused_by_the_api_and_the_database(posting):
    with posting.db.session(Actor.system()) as conn:
        conn.execute("update kill_switch set active = true")
    with pytest.raises(core.KillSwitchOn):
        posting.confirm()
    with pytest.raises(psycopg.errors.RaiseException, match="kill switch"):
        insert_target(posting)
    assert posting.rows("select 1 from post_targets") == []


def test_DW_3_2_a_platform_the_campaign_does_not_allow_is_refused_by_the_api_and_the_database(posting):
    approve(posting)
    with posting.db.session(Actor.system()) as conn:
        conn.execute("update campaign_versions set rules_json = jsonb_set(rules_json, '{platforms_allowed}', '[\"youtube_shorts\"]')")
    refuses(posting, "platform_not_allowed", platforms=["youtube_shorts", "tiktok"])
    with pytest.raises(psycopg.errors.RaiseException, match="does not allow tiktok"):
        insert_target(posting, platform="tiktok")
    allowed = {o["platform"]: o for o in options(posting)}
    assert allowed["youtube_shorts"]["allowed"] and not allowed["tiktok"]["allowed"] and "does not allow" in allowed["tiktok"]["blocked_reason"]


def test_DW_3_2_a_second_account_on_the_same_platform_is_refused_by_the_api_and_the_database(posting):
    posting.confirm(["youtube_shorts"], request_id="one")
    with posting.db.session(posting.owner) as conn:
        other = conn.execute("insert into accounts (platform, handle, owner_operator_id, verified) values ('youtube_shorts', 'second_yt', %s, true) returning id",
                             (posting.owner.operator_id,)).fetchone()["id"]
    with pytest.raises(AppError) as err:                                         # the same clip, a new tap, the same platform
        posting.confirm(["youtube_shorts"], request_id="two")
    assert err.value.code == "already_targeted"
    with pytest.raises(psycopg.errors.UniqueViolation):                          # a direct insert for the other account
        insert_target(posting, account=str(other), request="three")
    with pytest.raises(AppError) as dup:
        posting.confirm(["tiktok", "tiktok"], request_id="four")
    assert dup.value.code == "bad_platforms"
    assert len(posting.rows("select 1 from post_targets")) == 1


def test_DW_3_2_a_cancelled_or_failed_target_frees_its_platform_slot(posting):
    first = posting.confirm(["youtube_shorts"], request_id="one")["targets"][0]
    with posting.db.session(posting.owner) as conn:
        publish.cancel_target(conn, posting.owner, first["id"])
    assert posting.confirm(["youtube_shorts"], request_id="two")["targets"][0]["state"] == "scheduled"


def test_DW_3_2_another_owners_account_and_an_account_of_the_wrong_platform_are_refused_in_the_database(posting):
    intruder = second_owner(posting.db)
    with posting.db.session(Actor.system()) as conn:
        theirs = conn.execute("insert into accounts (platform, handle, owner_operator_id, verified) values ('tiktok', 'theirs', %s, true) returning id",
                              (intruder.operator_id,)).fetchone()["id"]
    posting.confirm(["youtube_shorts"], request_id="ready")                      # the clip is now approved
    with pytest.raises(psycopg.errors.RaiseException, match="only be used by its owner"):
        insert_target(posting, platform="tiktok", account=str(theirs))
    with pytest.raises(psycopg.errors.RaiseException, match="is not a tiktok account"):
        insert_target(posting, platform="tiktok", account=posting.accounts["youtube_shorts"])
    with pytest.raises(AppError):                                                # another owner cannot confirm someone else's clip
        posting.confirm(["tiktok"], request_id="x", actor=intruder)


def test_DW_3_2_a_clip_that_is_not_accepted_cannot_be_posted_and_a_target_needs_an_approved_clip(posting):
    other = next(c["id"] for c in _feed(posting) if str(c["id"]) != posting.clip)
    with pytest.raises(AppError):
        posting.confirm(["tiktok"], clip=str(other), request_id="early")
    with pytest.raises(psycopg.errors.RaiseException, match="not approved"):
        insert_target(posting, clip=str(other), platform="tiktok")


def test_the_request_and_ids_are_validated(posting):
    for bad in ("", "  ", None, "x" * 101):
        refuses(posting, "request_id_required", request_id=bad)
    refuses(posting, "bad_platforms", platforms=[])
    refuses(posting, "bad_platforms", platforms=["myspace"])
    refuses(posting, "bad_platforms", platforms="tiktok")
    with pytest.raises(AppError) as err:
        posting.confirm(ticks=["not", "a", "dict"])
    assert err.value.code == "checklist_incomplete"
    with pytest.raises(AppError) as err:
        posting.confirm(clip="not-a-uuid")
    assert err.value.code == "not_found"


def _feed(p):
    with p.db.session(p.owner) as conn:
        return review.feed(conn, p.owner.operator_id)


# ------------------------------------------------------------------ the API surface

def options(p) -> list[dict]:
    with p.db.session(p.owner) as conn:
        return publish.post_options(conn, p.owner, p.clip)["platforms"]


def http(p, actor=None) -> TestClient:
    api._owner = actor or p.owner
    return TestClient(api.app)


def test_post_options_lists_each_platform_with_account_connection_checklist_and_warnings(posting):
    body = http(posting).get(f"/api/clips/{posting.clip}/post-options").json()
    assert [o["platform"] for o in body["platforms"]] == ALL
    for option in body["platforms"]:
        assert option["allowed"] and not option["manual_only"] and option["connection_state"] == "connected"
        assert option["account"]["handle"] and option["existing_target"] is None
        assert set(option["checklist"]) == {"server", "owner_ticks"} and option["checklist"]["owner_ticks"] == publish.OWNER_TICKS
        assert all(item["ok"] for item in option["checklist"]["server"].values()) and isinstance(option["warnings"], list)


def test_post_options_marks_an_unconnected_or_expired_account_as_manual_and_a_held_platform_as_taken(posting):
    posting.confirm(["tiktok"])
    with posting.db.session(Actor.system()) as conn:
        conn.execute("update platform_connections set state = 'needs_reconnect' where platform = 'instagram_reels'")
        conn.execute("delete from platform_connections where platform = 'youtube_shorts'")
    by = {o["platform"]: o for o in options(posting)}
    assert by["instagram_reels"]["manual_only"] and by["instagram_reels"]["connection_state"] == "needs_reconnect"
    assert by["youtube_shorts"]["manual_only"] and by["youtube_shorts"]["connection_state"] == "not_connected" and by["youtube_shorts"]["allowed"]
    assert not by["tiktok"]["allowed"] and by["tiktok"]["existing_target"] == "scheduled"


def test_the_api_confirms_replays_reports_status_cancels_and_takes_a_link(posting):
    client = http(posting)
    body = {"platforms": ALL, "ticks": {p: TICKS for p in PLATFORMS}, "request_id": "web-1"}
    first = client.post(f"/api/clips/{posting.clip}/post", json=body)
    assert first.status_code == 200 and [t["platform"] for t in first.json()["targets"]] == ALL
    assert client.post(f"/api/clips/{posting.clip}/post", json=body).json()["targets"] == first.json()["targets"]
    status = client.get(f"/api/clips/{posting.clip}/post-status").json()["targets"]
    assert [(t["platform"], t["state"]) for t in status] == [(p, "scheduled") for p in ALL] and all(t["scheduled_at"] for t in status)
    assert set(status[0]) >= {"platform", "state", "scheduled_at", "url", "reason"}
    cancelled = client.post(f"/api/targets/{status[2]['id']}/cancel").json()
    assert cancelled["state"] == "cancelled"
    refused = client.post(f"/api/clips/{posting.clip}/post", json={**body, "request_id": "web-2", "platforms": ["youtube_shorts"]})
    assert refused.status_code == 400 and refused.json()["code"] == "already_targeted"
    posting.run_worker()
    yt = posting.targets()["youtube_shorts"]
    linked = client.post(f"/api/targets/{yt['id']}/link", json={"url": URLS["youtube_shorts"]})
    assert linked.status_code == 200 and linked.json()["state"] == "published"
    assert client.post(f"/api/targets/{yt['id']}/link", json={"url": "https://elsewhere.example/x"}).status_code == 400


def test_the_api_refuses_a_worker_with_a_403_and_a_bad_id_with_a_plain_message(posting):
    body = {"platforms": ALL, "ticks": {p: TICKS for p in PLATFORMS}, "request_id": "w-1"}
    assert http(posting, Actor.worker()).post(f"/api/clips/{posting.clip}/post", json=body).status_code == 403
    assert http(posting).post("/api/targets/not-a-uuid/cancel").status_code == 400
    assert http(posting).get("/api/clips/not-a-uuid/post-status").status_code == 400
    assert posting.rows("select 1 from post_targets") == []


# ------------------------------------------------------------------ DW-3.3: at most once

def test_DW_3_3_running_the_job_twice_sends_once(posting):
    target = posting.confirm(["instagram_reels"])["targets"][0]
    posting.run_stage(pipeline.stage_publish, target["id"])
    uploads = len(posting.calls("instagram_reels"))
    assert uploads and posting.targets()["instagram_reels"]["state"] == "published"
    posting.run_stage(pipeline.stage_publish, target["id"])                          # the same job again
    posting.run_stage(pipeline.stage_publish, target["id"], attempts=2)              # and as a retry
    assert len(posting.calls("instagram_reels")) == uploads
    assert sum(c[1].endswith("/media_publish") for c in posting.calls("instagram_reels")) == 1
    assert posting.targets()["instagram_reels"]["attempts"] == 1


def test_DW_3_3_the_worker_sends_every_target_once_through_the_job_queue(posting):
    posting.confirm()
    assert posting.run_worker() == 3 and posting.run_worker() == 0
    assert {p: t["attempts"] for p, t in posting.targets().items()} == {p: 1 for p in PLATFORMS}
    assert len({c[0] for c in posting.calls()}) == 3


def test_DW_3_3_a_worker_that_dies_mid_send_leaves_sending_then_needs_check_and_never_resends(posting, monkeypatch):
    target = posting.confirm(["tiktok"])["targets"][0]

    def die(*args, **kwargs):
        raise SystemExit("the worker was killed")

    monkeypatch.setattr(registry, "call", die)
    with pytest.raises(SystemExit):
        posting.run_stage(pipeline.stage_publish, target["id"])
    row = posting.targets()["tiktok"]
    assert row["state"] == "sending" and row["attempts"] == 1 and row["sent_at"]     # committed before the call
    monkeypatch.undo()
    posting.run_stage(pipeline.stage_publish, target["id"])                          # a duplicate job: leaves it alone
    assert posting.targets()["tiktok"]["state"] == "sending"
    with posting.db.session(Actor.worker()) as conn:
        assert publish.sweep(conn, stuck_after_s=3600) == 0                          # not stuck yet
        assert publish.sweep(conn, stuck_after_s=0) == 1
    row = posting.targets()["tiktok"]
    assert row["state"] == "needs_check" and "stopped while sending" in row["reason"]
    assert posting.calls() == [] and "post_needs_check" in alert_types(posting)
    posting.run_stage(pipeline.stage_publish, target["id"])
    posting.run_stage(pipeline.stage_publish, target["id"], attempts=2)
    assert posting.calls() == []                                                     # still never sent


def test_DW_3_3_a_retried_job_that_finds_the_target_sending_turns_it_into_needs_check(posting, monkeypatch):
    target = posting.confirm(["tiktok"])["targets"][0]
    monkeypatch.setattr(registry, "call", lambda *a, **k: (_ for _ in ()).throw(SystemExit()))
    with pytest.raises(SystemExit):
        posting.run_stage(pipeline.stage_publish, target["id"])
    monkeypatch.undo()
    posting.run_stage(pipeline.stage_publish, target["id"], attempts=2)
    assert posting.targets()["tiktok"]["state"] == "needs_check" and posting.calls() == []


def test_DW_3_3_an_unexpected_error_during_the_call_is_needs_check_not_a_retry(posting, monkeypatch):
    target = posting.confirm(["tiktok"])["targets"][0]
    monkeypatch.setattr(registry, "call", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("socket reset secret-value")))
    posting.run_stage(pipeline.stage_publish, target["id"])
    row = posting.targets()["tiktok"]
    assert row["state"] == "needs_check" and "RuntimeError" in row["reason"] and "secret-value" not in row["reason"]


def test_DW_3_3_the_kill_switch_between_confirm_and_send_cancels_every_unsent_target(posting):
    posting.confirm()
    with posting.db.session(posting.owner) as conn:
        core.set_kill_switch(conn, True, "stop")
    assert {t["state"] for t in posting.targets().values()} == {"cancelled"}
    assert all("kill switch" in t["reason"] for t in posting.targets().values())
    assert pipeline.process_one(posting.db) is False and posting.calls() == []
    assert posting.posts() == []


def test_DW_3_3_a_kill_switch_flipped_without_the_trigger_still_stops_the_send_and_the_database_refuses_sending(posting):
    target = posting.confirm(["youtube_shorts"])["targets"][0]
    with posting.db.session(Actor.system()) as conn:
        conn.execute("alter table kill_switch disable trigger kill_switch_stops_targets")
        conn.execute("update kill_switch set active = true")
    with pytest.raises(psycopg.errors.RaiseException, match="kill switch is on: nothing can be sent"):
        with posting.db.session(Actor.worker()) as conn:
            conn.execute("update post_targets set state = 'sending' where id = %s", (target["id"],))
    posting.run_stage(pipeline.stage_publish, target["id"])
    row = posting.targets()["youtube_shorts"]
    assert row["state"] == "cancelled" and "kill switch" in row["reason"] and posting.calls() == []
    assert "post_cancelled" not in alert_types(posting)


def test_DW_3_3_cancel_stops_an_unsent_target_and_the_job_then_does_nothing(posting):
    target = posting.confirm(["youtube_shorts"])["targets"][0]
    with posting.db.session(posting.owner) as conn:
        assert publish.cancel_target(conn, posting.owner, target["id"])["state"] == "cancelled"
    assert posting.run_worker() == 1 and posting.calls() == []
    with posting.db.session(posting.owner) as conn:
        with pytest.raises(AppError) as err:
            publish.cancel_target(conn, posting.owner, target["id"])
    assert err.value.code == "not_cancellable"


def test_DW_3_3_a_target_already_sent_cannot_be_cancelled(posting):
    target = posting.confirm(["instagram_reels"])["targets"][0]
    posting.run_worker()
    with posting.db.session(posting.owner) as conn:
        with pytest.raises(AppError) as err:
            publish.cancel_target(conn, posting.owner, target["id"])
    assert err.value.code == "not_cancellable" and "already been sent" in str(err.value)


def test_DW_3_3_a_brief_changed_since_approval_blocks_the_send(posting):
    target = posting.confirm(["youtube_shorts"])["targets"][0]
    with posting.db.session(Actor.system()) as conn:
        conn.execute("update campaigns set approved_version_id = current_version_id, status = 'paused' where id = %s", (posting.produced.campaign_id,))
    posting.run_stage(pipeline.stage_publish, target["id"])
    row = posting.targets()["youtube_shorts"]
    assert row["state"] == "cancelled" and "brief changed" in row["reason"] and posting.calls() == []
    assert "post_cancelled" in alert_types(posting)


def test_DW_3_3_a_changed_file_a_practice_flip_a_lost_connection_and_a_restricted_account_each_block_the_send(posting):
    targets = {t["platform"]: t["id"] for t in posting.confirm()["targets"]}
    with posting.db.session(Actor.system()) as conn:
        conn.execute("update clips set approved_file_hash = 'other' where id = %s", (posting.clip,))
    posting.run_stage(pipeline.stage_publish, targets["youtube_shorts"])
    assert "not the version" in posting.targets()["youtube_shorts"]["reason"]
    with posting.db.session(Actor.system()) as conn:
        conn.execute("delete from platform_connections where platform = 'instagram_reels'")   # the target keeps no connection (set null)
    posting.run_stage(pipeline.stage_publish, targets["instagram_reels"])
    assert "disconnected" in posting.targets()["instagram_reels"]["reason"]
    with posting.db.session(Actor.system()) as conn:
        conn.execute("update accounts set status = 'restricted' where platform = 'tiktok'")
    posting.run_stage(pipeline.stage_publish, targets["tiktok"])
    assert "restricted" in posting.targets()["tiktok"]["reason"] and posting.calls() == []


def test_DW_3_3_a_practice_flip_after_confirm_blocks_the_send(posting):
    target = posting.confirm(["youtube_shorts"])["targets"][0]
    make_practice(posting)
    posting.run_stage(pipeline.stage_publish, target["id"])
    assert "Practice" in posting.targets()["youtube_shorts"]["reason"] and posting.calls() == []


def test_DW_3_3_a_sign_in_that_expired_fails_the_target_with_a_reason_and_marks_the_connection(posting):
    target = posting.confirm(["youtube_shorts"])["targets"][0]
    from clipping.adapters import oauth

    oauth.FAKE_OAUTH["revoked"] = True                                              # the platform no longer accepts the refresh token
    with posting.db.session(Actor.system()) as conn:
        conn.execute("update platform_connections set expires_at = now() - interval '1 hour' where platform = 'youtube_shorts'")
    posting.run_stage(pipeline.stage_publish, target["id"])
    row = posting.targets()["youtube_shorts"]
    assert row["state"] == "failed" and row["reason"] and posting.posts() == []
    assert posting.rows("select state from platform_connections where platform = 'youtube_shorts'")[0]["state"] == "needs_reconnect"


# ------------------------------------------------------------------ DW-3.4: outcomes and the post record

def test_DW_3_4_each_outcome_lands_on_its_target_and_only_a_public_link_writes_a_post(posting):
    posting.confirm()
    posting.run_worker()
    by = posting.targets()
    assert by["youtube_shorts"]["state"] == "private" and by["youtube_shorts"]["platform_post_id"] and by["youtube_shorts"]["privacy"] == "private"
    assert by["tiktok"]["state"] == "draft" and by["tiktok"]["platform_post_id"] and by["tiktok"]["url"] is None
    assert by["instagram_reels"]["state"] == "published" and by["instagram_reels"]["url"].startswith("https://www.instagram.com/")
    posts = posting.posts()
    assert [p["platform"] for p in posts] == ["instagram_reels"]                      # a draft and a private upload write nothing
    post = posts[0]
    assert (post["platform_post_id"], post["state"], post["privacy"], post["target_id"]) == (
        by["instagram_reels"]["platform_post_id"], "published", "public", by["instagram_reels"]["id"])
    assert post["posted_at"] and post["submitted_at"] == by["instagram_reels"]["sent_at"] and post["post_url"] == by["instagram_reels"]["url"]
    assert post["disclosure_toggle_on"] and str(post["confirmed_by"]) == posting.owner.operator_id
    assert post["checklist_json"]["ticks"] == TICKS
    assert [p["status"] for p in posting.rows("select status from payouts where clip_id = %s", posting.clip)] == ["submitted"]
    assert posting.rows("select status from clips where id = %s", posting.clip)[0]["status"] == "posted"


def test_DW_3_4_pasting_the_link_for_a_draft_and_a_private_upload_writes_their_posts_and_one_payout(posting):
    posting.confirm()
    posting.run_worker()
    by = posting.targets()
    with posting.db.session(posting.owner) as conn:
        for platform in ("youtube_shorts", "tiktok"):
            done = publish.paste_link(conn, posting.owner, by[platform]["id"], URLS[platform])
            assert done["state"] == "published" and done["url"] == URLS[platform]
    posts = {p["platform"]: p for p in posting.posts()}
    assert set(posts) == set(PLATFORMS) and posts["tiktok"]["post_url"] == URLS["tiktok"] and posts["tiktok"]["submitted_at"]
    assert posts["youtube_shorts"]["platform_post_id"] == by["youtube_shorts"]["platform_post_id"]
    assert len(posting.rows("select 1 from payouts where clip_id = %s", posting.clip)) == 1


def test_DW_3_4_a_refused_upload_is_failed_with_the_platforms_reason_and_writes_no_post(posting):
    publishing.FAKE_PUBLISHING["mode"]["tiktok"] = "refuse"
    target = posting.confirm(["tiktok"])["targets"][0]
    posting.run_stage(pipeline.stage_publish, target["id"])
    row = posting.targets()["tiktok"]
    assert row["state"] == "failed" and "Too many pending" in row["reason"] and posting.posts() == []
    assert "post_failed" in alert_types(posting)


def test_DW_3_4_an_ambiguous_upload_is_needs_check_keeps_the_post_id_and_a_pasted_link_resolves_it(posting):
    publishing.FAKE_PUBLISHING["mode"]["instagram_reels"] = "timeout"
    target = posting.confirm(["instagram_reels"])["targets"][0]
    posting.run_stage(pipeline.stage_publish, target["id"])
    row = posting.targets()["instagram_reels"]
    assert row["state"] == "needs_check" and row["platform_post_id"] and "Check the platform" in row["reason"] and posting.posts() == []
    assert "post_needs_check" in alert_types(posting)
    with posting.db.session(posting.owner) as conn:
        publish.paste_link(conn, posting.owner, target["id"], URLS["instagram_reels"])
    assert [p["post_url"] for p in posting.posts()] == [URLS["instagram_reels"]]


def test_DW_3_4_the_owner_can_clear_a_needs_check_that_never_posted(posting):
    publishing.FAKE_PUBLISHING["mode"]["tiktok"] = "timeout"
    target = posting.confirm(["tiktok"])["targets"][0]
    posting.run_stage(pipeline.stage_publish, target["id"])
    with posting.db.session(posting.owner) as conn:
        cleared = publish.cancel_target(conn, posting.owner, target["id"])
    assert cleared["state"] == "cancelled" and "did not post" in cleared["reason"] and posting.posts() == []


def test_DW_3_4_a_processing_instagram_post_is_published_by_the_status_job_exactly_once(posting):
    publishing.FAKE_PUBLISHING["mode"]["instagram_reels"] = "processing"
    target = posting.confirm(["instagram_reels"])["targets"][0]
    posting.run_stage(pipeline.stage_publish, target["id"])
    assert posting.targets()["instagram_reels"]["state"] == "processing" and posting.posts() == []
    assert len(posting.rows("select 1 from jobs where type = 'post_status'")) == 1       # the first check is already queued
    publishing.FAKE_PUBLISHING["mode"].clear()
    posting.run_stage(pipeline.stage_post_status, target["id"])
    row = posting.targets()["instagram_reels"]
    assert row["state"] == "published" and row["url"] and len(posting.posts()) == 1
    assert sum(c[1].endswith("/media_publish") for c in posting.calls("instagram_reels")) == 1
    posting.run_stage(pipeline.stage_post_status, target["id"])                           # nothing left to check
    assert sum(c[1].endswith("/media_publish") for c in posting.calls("instagram_reels")) == 1


def test_DW_3_4_a_still_processing_post_is_asked_again_later_and_a_failed_check_is_recorded(posting):
    publishing.FAKE_PUBLISHING["mode"]["instagram_reels"] = "processing"
    target = posting.confirm(["instagram_reels"])["targets"][0]
    posting.run_stage(pipeline.stage_publish, target["id"])
    posting.unpin()                                           # rows are aged by the real clock; a pinned 10:00 is hours later when run early in the day
    posting.run_stage(pipeline.stage_post_status, target["id"])                           # still processing
    row = posting.targets()["instagram_reels"]
    assert row["state"] == "processing" and row["checked_at"] is None
    with posting.db.session(Actor.worker()) as conn:
        publish.sweep(conn)
    assert len(posting.rows("select 1 from jobs where type = 'post_status'")) == 2        # the sweep queued the next round
    publishing.FAKE_PUBLISHING["mode"]["instagram_reels"] = "error"
    posting.run_stage(pipeline.stage_post_status, target["id"])
    row = posting.targets()["instagram_reels"]
    assert row["state"] == "failed" and "Nothing was posted" in row["reason"] and posting.posts() == []


def test_DW_3_4_a_tiktok_status_check_that_says_failed_updates_the_target(posting):
    target = posting.confirm(["tiktok"])["targets"][0]
    posting.run_stage(pipeline.stage_publish, target["id"])
    posting.force_state(target["id"], "processing")           # pretend the platform had still been receiving the upload
    publishing.FAKE_PUBLISHING["status"]["tiktok"] = "failed"
    posting.run_stage(pipeline.stage_post_status, target["id"])
    row = posting.targets()["tiktok"]
    assert row["state"] == "failed" and row["reason"]


def test_DW_3_4_a_status_check_that_cannot_run_is_needs_check_never_a_guess(posting, monkeypatch):
    publishing.FAKE_PUBLISHING["mode"]["instagram_reels"] = "processing"
    target = posting.confirm(["instagram_reels"])["targets"][0]
    posting.run_stage(pipeline.stage_publish, target["id"])
    monkeypatch.setattr(registry, "call", lambda *a, **k: (_ for _ in ()).throw(AppError("That account is not connected.", "not_connected")))
    posting.run_stage(pipeline.stage_post_status, target["id"])
    assert posting.targets()["instagram_reels"]["state"] == "needs_check"
    posting.force_state(target["id"], "processing", checked_at=None)
    monkeypatch.setattr(registry, "call", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    posting.run_stage(pipeline.stage_post_status, target["id"])
    assert posting.targets()["instagram_reels"]["state"] == "needs_check"


def test_DW_3_4_the_sweep_gives_up_on_a_post_still_processing_after_hours(posting):
    publishing.FAKE_PUBLISHING["mode"]["instagram_reels"] = "processing"
    target = posting.confirm(["instagram_reels"])["targets"][0]
    posting.run_stage(pipeline.stage_publish, target["id"])
    posting.unpin()
    with posting.db.session(Actor.system()) as conn:
        conn.execute("update post_targets set sent_at = now() - interval '4 hours' where id = %s", (target["id"],))
    with posting.db.session(Actor.worker()) as conn:
        assert publish.sweep(conn) == 1
    assert posting.targets()["instagram_reels"]["state"] == "needs_check"


def test_DW_3_4_the_sweep_stays_quiet_while_the_kill_switch_is_on(posting):
    publishing.FAKE_PUBLISHING["mode"]["instagram_reels"] = "processing"
    target = posting.confirm(["instagram_reels"])["targets"][0]
    posting.run_stage(pipeline.stage_publish, target["id"])
    posting.unpin()
    with posting.db.session(Actor.system()) as conn:
        conn.execute("update kill_switch set active = true")
    with posting.db.session(Actor.worker()) as conn:
        assert publish.sweep(conn) == 0
    assert len(posting.rows("select 1 from jobs where type = 'post_status'")) == 1


@pytest.mark.parametrize("answer, state", [
    ({"state": "published", "url": None, "platform_post_id": "abc123"}, "needs_check"),                 # public but no link
    ({"state": "published", "url": "ftp://x/y"}, "needs_check"),
    ({"state": "weird", "url": "https://x.example/1"}, "needs_check"),
    (None, "needs_check"),
    ({"state": "draft", "url": "https://www.tiktok.com/x", "platform_post_id": "p" * 200}, "draft")])
def test_an_odd_platform_answer_is_never_trusted(answer, state):
    clean = publish._clean_outcome(answer)
    assert clean["state"] == state
    if state == "needs_check":
        assert clean["reason"]
    assert clean["post_id"] is None or len(clean["post_id"]) <= 128


def test_a_public_link_that_cannot_be_recorded_falls_back_to_needs_check_with_a_prompt_to_paste(posting, monkeypatch):
    def refuse(*args, **kwargs):
        raise psycopg.errors.UniqueViolation("duplicate")

    monkeypatch.setattr(publish, "_publish", refuse)
    target = posting.confirm(["instagram_reels"])["targets"][0]
    posting.run_stage(pipeline.stage_publish, target["id"])
    row = posting.targets()["instagram_reels"]
    assert row["state"] == "needs_check" and "could not record" in row["reason"] and posting.posts() == []


# ------------------------------------------------------------------ manual targets and pasted links

def test_a_manual_target_is_never_sent_and_is_completed_by_pasting_the_link(produced, monkeypatch):
    p = Posting(produced, monkeypatch, connected=("youtube_shorts",))
    monkeypatch.setattr(publish, "_now", lambda: p._pin())
    targets = {t["platform"]: t for t in p.confirm()["targets"]}
    assert not targets["youtube_shorts"]["manual"] and targets["instagram_reels"]["manual"] and targets["tiktok"]["manual"]
    assert len(p.rows("select 1 from jobs where type = 'publish'")) == 1                  # only the connected one is queued
    p.run_worker()
    assert p.targets()["instagram_reels"]["state"] == "scheduled" and p.calls("instagram_reels") == []
    p.run_stage(pipeline.stage_publish, targets["instagram_reels"]["id"])                 # even a stray job does not send it
    assert p.calls("instagram_reels") == []
    with p.db.session(p.owner) as conn:
        done = publish.paste_link(conn, p.owner, targets["instagram_reels"]["id"], URLS["instagram_reels"])
        assert done["state"] == "published"
        assert publish.paste_link(conn, p.owner, targets["instagram_reels"]["id"], URLS["instagram_reels"])["state"] == "published"   # replay
        with pytest.raises(AppError) as err:
            publish.paste_link(conn, p.owner, targets["instagram_reels"]["id"], "https://www.instagram.com/reel/Other/")
        assert err.value.code == "already_published"
    assert [x["platform"] for x in p.posts()] == ["instagram_reels"] and p.posts()[0]["platform_post_id"] is None


def test_pasting_a_link_needs_the_right_platform_the_owner_and_an_open_target(posting):
    target = posting.confirm(["youtube_shorts"])["targets"][0]
    intruder = second_owner(posting.db)
    with posting.db.session(posting.owner) as conn:
        for bad in ("", "not a link", None, "https://evil.example/youtube.com", "https://www.instagram.com/reel/x/", "javascript:alert(1)"):
            with pytest.raises(AppError) as err:
                publish.paste_link(conn, posting.owner, target["id"], bad)
            assert err.value.code == "link_required"
    for who, error in ((intruder, AppError), (Actor.worker(), GateError)):
        with posting.db.session(who) as conn:
            with pytest.raises(error):
                publish.paste_link(conn, who, target["id"], URLS["youtube_shorts"])
    assert posting.posts() == []
    posting.run_worker()
    cancelled = posting.confirm(["tiktok"], request_id="t")["targets"][0]
    with posting.db.session(posting.owner) as conn:
        publish.cancel_target(conn, posting.owner, cancelled["id"])
        with pytest.raises(AppError) as err:
            publish.paste_link(conn, posting.owner, cancelled["id"], URLS["tiktok"])
        assert err.value.code == "not_linkable"
        with pytest.raises(AppError) as gone:
            publish.cancel_target(conn, posting.owner, "00000000-0000-0000-0000-000000000000")
        assert gone.value.code == "not_found"


def test_pasting_a_link_while_the_kill_switch_is_on_is_refused(posting):
    target = posting.confirm(["tiktok"])["targets"][0]
    posting.run_worker()
    with posting.db.session(Actor.system()) as conn:
        conn.execute("update kill_switch set active = true")
    with posting.db.session(posting.owner) as conn:
        with pytest.raises(core.KillSwitchOn):
            publish.paste_link(conn, posting.owner, target["id"], URLS["tiktok"])


# ------------------------------------------------------------------ database rules for targets and posts

def test_the_database_only_allows_legal_target_moves_by_the_right_actor(posting):
    target = posting.confirm(["youtube_shorts"])["targets"][0]["id"]

    def move(actor, state, where=None):
        with posting.db.session(actor) as conn:
            conn.execute("update post_targets set state = %s where id = %s", (state, target))

    with pytest.raises(psycopg.errors.RaiseException, match="cannot move from scheduled to sending"):
        move(posting.owner, "sending")                                                   # a human never sends
    with pytest.raises(psycopg.errors.RaiseException, match="scheduled to failed"):
        move(Actor.worker(), "failed")
    move(Actor.worker(), "sending")
    with pytest.raises(psycopg.errors.RaiseException, match="sending to cancelled"):
        move(posting.owner, "cancelled")                                                 # too late to cancel
    with pytest.raises(psycopg.errors.RaiseException, match="sending to published"):
        move(posting.owner, "published")
    move(Actor.worker(), "failed")
    for state in ("scheduled", "published", "sending"):
        with pytest.raises(psycopg.errors.RaiseException, match="failed to"):
            move(Actor.worker(), state)                                                  # final


def test_a_target_cannot_be_pointed_somewhere_else_or_rescheduled_after_sending(posting):
    target = posting.confirm(["youtube_shorts"])["targets"][0]["id"]
    with pytest.raises(psycopg.errors.RaiseException, match="pointed somewhere else"):
        with posting.db.session(posting.owner) as conn:
            conn.execute("update post_targets set platform = 'tiktok' where id = %s", (target,))
    with posting.db.session(Actor.worker()) as conn:
        conn.execute("update post_targets set scheduled_at = now() where id = %s", (target,))      # allowed while unsent
        conn.execute("update post_targets set state = 'sending' where id = %s", (target,))
    with pytest.raises(psycopg.errors.RaiseException, match="only an unsent"):
        with posting.db.session(Actor.worker()) as conn:
            conn.execute("update post_targets set scheduled_at = now() + interval '1 day' where id = %s", (target,))


def test_a_published_target_needs_a_link(posting):
    target = posting.confirm(["youtube_shorts"])["targets"][0]["id"]
    with pytest.raises(psycopg.errors.CheckViolation):
        with posting.db.session(posting.owner) as conn:
            conn.execute("update post_targets set state = 'published' where id = %s", (target,))


def test_the_worker_cannot_write_a_post_without_a_confirmed_published_target(posting):
    target = posting.confirm(["youtube_shorts"])["targets"][0]
    values = (posting.clip, posting.owner.operator_id, posting.accounts["youtube_shorts"])
    sql = """insert into posts (clip_id, owner_operator_id, account_id, platform, post_url, disclosure_toggle_on, checklist_json, confirmed_by, target_id)
             values (%s, %s, %s, 'youtube_shorts', 'https://www.youtube.com/shorts/x1', true, '{}', %s, %s)"""
    with pytest.raises(psycopg.errors.RaiseException, match="posts are written by the owner only"):
        with posting.db.session(Actor.worker()) as conn:
            conn.execute(sql, (*values, posting.owner.operator_id, None))                 # no target: the old rule stands
    with pytest.raises(psycopg.errors.RaiseException, match="confirmed, published post target"):
        with posting.db.session(Actor.worker()) as conn:
            conn.execute(sql, (*values, posting.owner.operator_id, target["id"]))        # the target is not published
    with pytest.raises(psycopg.errors.RaiseException, match="confirmed, published post target"):
        with posting.db.session(Actor.worker()) as conn:
            conn.execute(sql, (*values, posting.owner.operator_id, "00000000-0000-0000-0000-000000000000"))


def test_the_worker_cannot_mark_a_clip_posted_without_a_published_target(posting):
    posting.confirm(["youtube_shorts"])
    with pytest.raises(psycopg.errors.RaiseException, match="needs a recorded human action"):
        with posting.db.session(Actor.worker()) as conn:
            conn.execute("update clips set status = 'posted' where id = %s", (posting.clip,))


def test_the_worker_cannot_create_targets_or_change_the_gates_any_other_way(posting):
    import inspect

    source = inspect.getsource(pipeline) + inspect.getsource(publish.stage_publish) + inspect.getsource(publish.stage_post_status)
    assert "insert into post_targets" not in source and "confirm_post" not in source


# ------------------------------------------------------------------ DW-3.5: the manual path is unchanged

def test_DW_3_5_mark_posted_still_works_and_writes_the_same_record_shape(posting):
    with posting.db.session(posting.owner) as conn:
        publish.approve_publish(conn, posting.owner, posting.clip, TICKS)
        post = publish.mark_posted(conn, posting.owner, posting.clip, "https://example.com/p/1", True, True)
    assert post["post_url"] == "https://example.com/p/1" and post["disclosure_toggle_on"] and post["state"] == "published"
    assert (post["platform_post_id"], post["target_id"], post["privacy"]) == (None, None, "public") and post["posted_at"] and post["submitted_at"]
    assert post["checklist_json"]["ticks"] == TICKS and str(post["confirmed_by"]) == posting.owner.operator_id
    assert posting.rows("select status from clips where id = %s", posting.clip)[0]["status"] == "posted"
    assert [p["status"] for p in posting.rows("select status from payouts where clip_id = %s", posting.clip)] == ["submitted"]


def test_DW_3_5_the_old_one_platform_approval_then_a_target_for_another_platform_both_work(posting):
    with posting.db.session(posting.owner) as conn:
        publish.approve_publish(conn, posting.owner, posting.clip, TICKS)
    clip_platform = posting.rows("select platform from clips where id = %s", posting.clip)[0]["platform"]
    other = next(p for p in PLATFORMS if p != clip_platform)
    assert posting.confirm([other])["targets"][0]["state"] == "scheduled"               # an already ready clip can add a platform
    posting.run_worker()
    assert posting.targets()[other]["state"] in ("private", "published", "draft")


def test_mark_posted_refuses_a_platform_that_already_has_a_live_target(posting):
    clip_platform = posting.rows("select platform from clips where id = %s", posting.clip)[0]["platform"]
    posting.confirm([clip_platform])
    with posting.db.session(posting.owner) as conn:
        with pytest.raises(AppError) as err:
            publish.mark_posted(conn, posting.owner, posting.clip, "https://example.com/p/9", True, True)
    assert err.value.code == "target_exists"


def test_a_clip_posted_once_can_still_take_another_platform(posting):
    posting.confirm(["instagram_reels"], request_id="a")
    posting.run_worker()
    assert posting.rows("select status from clips where id = %s", posting.clip)[0]["status"] == "posted"
    assert posting.confirm(["tiktok"], request_id="b")["targets"][0]["state"] == "scheduled"
    posting.run_worker()
    assert posting.targets()["tiktok"]["state"] == "draft"
    assert len(posting.rows("select 1 from payouts where clip_id = %s", posting.clip)) == 1


def test_the_new_job_kinds_are_registered_and_the_migration_checks_match_the_ticks():
    assert {"publish", "post_status"} <= set(pipeline.HANDLERS)
    assert publish.OWNER_TICKS == ["account_and_platform_correct", "disclosure_toggle_on", "caption_pasted", "hashtags_and_tags_checked"]
