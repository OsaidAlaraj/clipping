"""Upload progress (GET /api/progress): the stage of each recent source video, worked out from jobs, candidates and
clips, scoped to the signed-in owner. Rows are written directly so each stage is exact; one test runs the real pipeline."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from clipping import api, campaigns, progress
from clipping.db import Actor, Jsonb


def _campaign(db, owner, name="Pilot") -> dict:
    with db.session(owner) as conn:
        return campaigns.create_practice(conn, owner, name, "gaming", "en")


def _source(db, owner, campaign, origin_url="upload:talk.mp4", duration=600) -> str:
    with db.session(owner) as conn:
        return str(conn.execute(
            """insert into source_assets (campaign_id, owner_operator_id, origin_url, stored_path, file_hash, language, duration_s, permission_ref)
               values (%s, %s, %s, 'sources/x.mp4', %s, 'en', %s, 'P') returning id""",
            (campaign["id"], owner.operator_id, origin_url, "a" * 64, duration)).fetchone()["id"])


def _job(db, owner, source_id, job_type, status, error=None, payload=None, age_s=0) -> None:
    payload = dict(payload or {}, n=f"{job_type}-{status}-{source_id}-{json.dumps(payload or {}, sort_keys=True)}")
    with db.session(Actor.worker()) as conn:
        conn.execute(
            """insert into jobs (type, source_id, owner_operator_id, input_hash, idempotency_key, payload, status, error, updated_at)
               values (%s, %s, %s, %s, %s, %s, %s, %s, now() - make_interval(secs => %s))""",
            (job_type, source_id, owner.operator_id, "h", f"{job_type}:{payload['n']}", Jsonb(payload), status, error, age_s))


def _candidate(db, owner, source_id, status="new") -> str:
    with db.session(owner) as conn:
        return str(conn.execute(
            "insert into candidates (source_id, owner_operator_id, provider, start_s, end_s, status) values (%s, %s, 'x', 1, 20, %s) returning id",
            (source_id, owner.operator_id, status)).fetchone()["id"])


def _clip(db, owner, candidate_id, campaign, status) -> None:
    with db.session(Actor.worker()) as conn:
        version = conn.execute("select id from campaign_versions where campaign_id = %s order by version_no desc limit 1",
                               (campaign["id"],)).fetchone()
        conn.execute(
            """insert into clips (candidate_id, campaign_id, campaign_version_id, owner_operator_id, start_s, end_s, language, status)
               values (%s, %s, %s, %s, 1, 20, 'en', %s)""", (candidate_id, campaign["id"], version["id"], owner.operator_id, status))


def _overview(db, actor) -> dict:
    with db.session(actor) as conn:
        return progress.overview(conn, actor)


def _only(db, actor) -> dict:
    items = _overview(db, actor)["items"]
    assert len(items) == 1
    return items[0]


def _second_admin(db) -> Actor:
    with db.session(Actor.system()) as conn:
        row = conn.execute("insert into operators (name, role) values ('Second owner', 'admin') returning id").fetchone()
    return Actor(str(row["id"]), "admin")


def _with_hooks(db, owner, source_id, campaign, statuses, age_s=0) -> None:
    """A source whose moments are found: one candidate and hooks job per clip, with each clip in the given status."""
    _job(db, owner, source_id, "transcribe", "done", age_s=age_s)
    _job(db, owner, source_id, "moments", "done", age_s=age_s)
    for status in statuses:
        candidate = _candidate(db, owner, source_id, "hooked")
        _job(db, owner, source_id, "hooks", "done", payload={"candidate_id": candidate}, age_s=age_s)
        _clip(db, owner, candidate, campaign, status)


def test_a_video_being_transcribed_shows_the_transcribing_stage_with_a_time_estimate(db, owner):
    campaign = _campaign(db, owner, "Pilot")
    source_id = _source(db, owner, campaign, duration=600)
    _job(db, owner, source_id, "transcribe", "running")
    item = _only(db, owner)
    assert (item["stage"], item["state"], item["name"], item["video"]) == ("transcribing", "active", "Practice · Pilot", "talk.mp4")
    assert item["clips_planned"] == 0 and item["error"] is None
    assert item["eta_s"] == 60 + 60 + 3 * 30   # 10% of 10 minutes, one minute for moments, three clips assumed


def test_transcription_time_is_capped_for_a_very_long_video(db, owner):
    source_id = _source(db, owner, _campaign(db, owner), duration=36000)
    _job(db, owner, source_id, "transcribe", "queued")
    assert _only(db, owner)["eta_s"] == 600 + 60 + 90


def test_a_source_with_no_jobs_yet_shows_the_uploaded_stage(db, owner):
    _source(db, owner, _campaign(db, owner))
    assert _only(db, owner)["stage"] == "uploaded"


def test_a_video_whose_moments_are_being_chosen_shows_the_moments_stage(db, owner):
    source_id = _source(db, owner, _campaign(db, owner))
    _job(db, owner, source_id, "transcribe", "done")
    _job(db, owner, source_id, "moments", "running")
    item = _only(db, owner)
    assert item["stage"] == "moments" and item["state"] == "active"
    assert item["eta_s"] == 60 + 3 * 30


def test_making_clips_counts_finished_clips_of_the_planned_ones(db, owner):
    campaign = _campaign(db, owner)
    source_id = _source(db, owner, campaign)
    _with_hooks(db, owner, source_id, campaign, ["ready_for_review", "rendering", "rendering"])
    _job(db, owner, source_id, "render", "running")
    item = _only(db, owner)
    assert (item["stage"], item["state"], item["clips_done"], item["clips_planned"]) == ("clips", "active", 1, 3)
    assert item["eta_s"] == 2 * 30


def test_a_set_aside_moment_is_not_a_planned_clip(db, owner):
    campaign = _campaign(db, owner)
    source_id = _source(db, owner, campaign)
    _with_hooks(db, owner, source_id, campaign, ["rendering"])
    _job(db, owner, source_id, "hooks", "done", payload={"candidate_id": _candidate(db, owner, source_id, "discarded")})
    _job(db, owner, source_id, "render", "queued")
    assert _only(db, owner)["clips_planned"] == 1


def test_a_finished_video_is_ready_with_its_clip_count_and_no_time_left(db, owner):
    campaign = _campaign(db, owner, "Pilot")
    source_id = _source(db, owner, campaign)
    _with_hooks(db, owner, source_id, campaign, ["ready_for_review", "accepted"])
    item = _only(db, owner)
    assert (item["stage"], item["state"], item["clips_done"], item["clips_planned"]) == ("ready", "ready", 2, 2)
    assert item["eta_s"] is None and item["error"] is None and item["campaign_id"] == str(campaign["id"])


def test_a_ready_video_leaves_the_list_after_an_hour_but_an_unfinished_one_stays(db, owner):
    campaign = _campaign(db, owner)
    finished, stuck = _source(db, owner, campaign), _source(db, owner, campaign, origin_url="upload:b.mp4")
    _with_hooks(db, owner, finished, campaign, ["ready_for_review"], age_s=2 * 3600)
    _job(db, owner, stuck, "transcribe", "running", age_s=5 * 3600)
    assert [i["video"] for i in _overview(db, owner)["items"]] == ["b.mp4"]


def test_a_source_older_than_a_day_is_not_listed(db, owner):
    source_id = _source(db, owner, _campaign(db, owner))
    _job(db, owner, source_id, "transcribe", "running")
    with db.session(Actor.worker()) as conn:
        conn.execute("update source_assets set created_at = now() - interval '25 hours'")
    assert _overview(db, owner)["items"] == []


def test_a_failed_job_shows_the_error_state_with_its_reason(db, owner):
    source_id = _source(db, owner, _campaign(db, owner))
    _job(db, owner, source_id, "transcribe", "failed", error="AppError: Source file hash mismatch. The job is stopped.")
    item = _only(db, owner)
    assert (item["state"], item["stage"]) == ("failed", "transcribing")
    assert item["error"] == "Source file hash mismatch. The job is stopped."
    assert item["eta_s"] is None


def test_a_failed_job_without_a_message_still_gives_a_reason(db, owner):
    campaign = _campaign(db, owner)
    source_id = _source(db, owner, campaign)
    _with_hooks(db, owner, source_id, campaign, ["rendering"])
    _job(db, owner, source_id, "render", "failed", error=None)
    item = _only(db, owner)
    assert item["state"] == "failed" and item["stage"] == "clips" and "Alerts" in item["error"]


def test_a_cancelled_job_is_ignored(db, owner):
    source_id = _source(db, owner, _campaign(db, owner))
    _job(db, owner, source_id, "transcribe", "cancelled", error="kill switch")
    assert _only(db, owner)["state"] == "active"


def test_no_moments_found_is_ready_with_a_note_to_pick_by_hand(db, owner):
    source_id = _source(db, owner, _campaign(db, owner))
    _job(db, owner, source_id, "transcribe", "done")
    _job(db, owner, source_id, "moments", "done")
    item = _only(db, owner)
    assert item["state"] == "ready" and item["clips_planned"] == 0 and "by hand" in item["note"]


def test_a_link_still_being_fetched_has_a_card_before_it_has_a_source(db, owner):
    campaign = _campaign(db, owner, "Linked")
    with db.session(Actor.worker()) as conn:
        conn.execute(
            """insert into jobs (type, owner_operator_id, input_hash, idempotency_key, payload, status)
               values ('fetch_source', %s, 'h', 'fetch_source:h', %s, 'running')""",
            (owner.operator_id, Jsonb({"campaign_id": str(campaign["id"])})))
    item = _only(db, owner)
    assert (item["name"], item["stage"], item["state"], item["source_id"]) == ("Practice · Linked", "uploaded", "active", None)


def test_unfinished_cards_come_first_and_ready_ones_last(db, owner):
    campaign = _campaign(db, owner)
    ready = _source(db, owner, campaign, origin_url="upload:ready.mp4")
    _with_hooks(db, owner, ready, campaign, ["ready_for_review"])
    busy = _source(db, owner, campaign, origin_url="upload:busy.mp4")
    _job(db, owner, busy, "transcribe", "running")
    result = _overview(db, owner)
    assert [i["video"] for i in result["items"]] == ["busy.mp4", "ready.mp4"] and result["active"] is True


def test_only_ready_cards_means_nothing_is_active(db, owner):
    campaign = _campaign(db, owner)
    source_id = _source(db, owner, campaign)
    _with_hooks(db, owner, source_id, campaign, ["ready_for_review"])
    assert _overview(db, owner)["active"] is False


def test_another_owners_sources_are_not_returned_even_for_an_admin(db, owner):
    other = _second_admin(db)
    mine = _source(db, owner, _campaign(db, owner, "Mine"))
    theirs = _source(db, other, _campaign(db, other, "Theirs"))
    _job(db, owner, mine, "transcribe", "running")
    _job(db, other, theirs, "transcribe", "failed", error="AppError: private reason")
    assert [i["name"] for i in _overview(db, owner)["items"]] == ["Practice · Mine"]
    seen = _overview(db, other)["items"]
    assert [i["name"] for i in seen] == ["Practice · Theirs"] and seen[0]["error"] == "private reason"


def test_the_endpoint_returns_the_progress_for_the_signed_in_owner(db, owner):
    source_id = _source(db, owner, _campaign(db, owner, "Pilot"))
    _job(db, owner, source_id, "transcribe", "running")
    api._owner = owner
    body = TestClient(api.app).get("/api/progress").json()
    assert body["active"] is True and body["stages"][0] == "uploaded"
    assert body["items"][0]["stage"] == "transcribing" and body["items"][0]["name"] == "Practice · Pilot"


def test_a_real_pipeline_run_ends_ready_with_every_clip_counted(produced):
    with produced.db.session(produced.owner) as conn:
        conn.execute("update jobs set updated_at = now()")
        item = progress.overview(conn, produced.owner)["items"][0]
    assert (item["state"], item["stage"]) == ("ready", "ready")
    assert item["clips_done"] == item["clips_planned"] > 0
