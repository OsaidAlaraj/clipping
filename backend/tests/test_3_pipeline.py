"""Build step 3: production pipeline on fixture footage with fake transcription and moment adapters and
real FFmpeg; deterministic checks; Agents B and C."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from clipping import alerts, campaigns, core, ffmpeg, pipeline
from clipping.adapters import fake_llm, media, vendors
from clipping.agents import compliance as agent_c
from clipping.agents import hooks as agent_b
from clipping.core import AppError
from clipping.db import Actor
from conftest import add_accounts, approved_campaign

TEMPLATE = {"caption_text": "#ad", "overlay_text": "#ad"}
LIMITS = {"min_s": 5, "max_s": 90}
GOOD = {"duration_s": 12.0, "width": 1080, "height": 1920, "caption_text": "#ad Great hook #Tag",
        "overlay_json": {"text": "#ad", "start_s": 0.0, "end_s": 12.0}}


def statuses(checks: list[dict]) -> dict:
    return {c["check"]: c["status"] for c in checks}


# ------------------------------------------------------------------ deterministic checks (spec 15.5)

def test_deterministic_checks_pass_on_a_good_clip():
    assert set(statuses(agent_c.deterministic_checks(GOOD, TEMPLATE, LIMITS, [], ["#ad Great hook"])).values()) == {"pass"}


@pytest.mark.parametrize("change,failed", [
    ({"duration_s": 3.0, "overlay_json": {"text": "#ad", "start_s": 0, "end_s": 3.0}}, "duration"),
    ({"duration_s": 120.0, "overlay_json": {"text": "#ad", "start_s": 0, "end_s": 120.0}}, "duration"),
    ({"width": 1920, "height": 1080}, "aspect_9_16"),
    ({"width": 1080, "height": 1350}, "aspect_9_16"),
    ({"caption_text": "Great hook #ad"}, "caption_starts_with_disclosure"),
    ({"caption_text": "#advice for founders"}, "caption_starts_with_disclosure"),
    ({"caption_text": ""}, "caption_starts_with_disclosure"),
    ({"overlay_json": None}, "overlay_present_full_duration"),
    ({"overlay_json": {"text": "#ad", "start_s": 0.0, "end_s": 6.0}}, "overlay_present_full_duration"),
    ({"overlay_json": {"text": "#ad", "start_s": 2.0, "end_s": 12.0}}, "overlay_present_full_duration"),
    ({"overlay_json": {"text": "promo", "start_s": 0.0, "end_s": 12.0}}, "overlay_present_full_duration"),
])
def test_each_deterministic_check_fails_when_it_should(change, failed):
    result = statuses(agent_c.deterministic_checks({**GOOD, **change}, TEMPLATE, LIMITS, [], []))
    assert result[failed] == "fail"
    assert [k for k, v in result.items() if v == "fail"] == [failed]


def test_banned_words_check():
    result = agent_c.deterministic_checks(GOOD, TEMPLATE, LIMITS, ["guaranteed", "miracle"], ["#ad a Guaranteed win"])
    assert statuses(result)["banned_words"] == "fail" and "guaranteed" in result[-1]["evidence"]


def test_arabic_disclosure_check():
    template = {"caption_text": "#إعلان", "overlay_text": "#إعلان"}
    clip = {**GOOD, "caption_text": "#إعلان ماذا لو", "overlay_json": {"text": "#إعلان", "start_s": 0, "end_s": 12.0}}
    assert set(statuses(agent_c.deterministic_checks(clip, template, LIMITS, [], [])).values()) == {"pass"}
    assert statuses(agent_c.deterministic_checks({**clip, "caption_text": "#ad ماذا لو"}, template, LIMITS, [], []))[
        "caption_starts_with_disclosure"] == "fail"


def test_the_llm_never_overrides_a_deterministic_failure(db, owner, monkeypatch):
    monkeypatch.setattr(fake_llm, "compliance", lambda payload: {"results": [
        {"rule_index": i, "status": "pass", "evidence": "#ad"} for i in range(len(payload["rules"]))], "status": "pass"})
    rules = [{"text": "You must include #Tag", "category": "hashtag", "requirement": "required"}]
    with db.session(owner) as conn:
        bad, _ = agent_c.run(conn, alerts.registry, {**GOOD, "width": 1920, "height": 1080}, TEMPLATE, LIMITS, [], rules, "hook")
        good, _ = agent_c.run(conn, alerts.registry, GOOD, TEMPLATE, LIMITS, [], rules, "hook")
    assert bad["status"] == "fail" and bad["rules"] == []
    assert good["status"] == "pass"
    assert agent_c.overall([{"check": "x", "status": "fail"}], [{"status": "pass"}]) == "fail"


def test_agent_c_unconfirmed_is_review_and_never_certifies(db, owner):
    rules = [{"text": "You must include #Tag", "category": "hashtag", "requirement": "required"},
             {"text": "Keep the tone friendly", "category": "other", "requirement": "required"},
             {"text": "You must use licensed music", "category": "music", "requirement": "required"},
             {"text": "You must include #Missing", "category": "hashtag", "requirement": "required"}]
    with db.session(owner) as conn:
        report, _ = agent_c.run(conn, alerts.registry, GOOD, TEMPLATE, LIMITS, [], rules, "hook")
    assert [r["status"] for r in report["rules"]] == ["pass", "review", "review", "fail"]
    assert report["rules"][0]["evidence"] == "#Tag" and report["status"] == "fail"
    assert report["certifies_legal_compliance"] is False and "does not certify" in report["legal_notice"]
    # A "pass" with no evidence from the clip, a missing answer and nonsense all become review.
    results = agent_c.validate_llm({"results": [{"rule_index": 0, "status": "pass", "evidence": "made up"},
                                                {"rule_index": 1, "status": "approved"}]}, rules[:3], "#ad Great hook #Tag")
    assert [r["status"] for r in results] == ["review", "review", "review"]


# ------------------------------------------------------------------ Agent B

def test_agent_b_returns_two_to_three_typed_hooks_and_never_chooses(db, owner):
    for language in ("en", "ar"):
        segment = " ".join(vendors.FAKE_SENTENCES[language][:3])
        with db.session(owner) as conn:
            hooks, _ = agent_b.run(conn, alerts.registry, segment, language, 0.8, owner.operator_id)
        assert 2 <= len(hooks) <= 3
        assert all(h["hook_type"] in agent_b.HOOK_TYPES and h["chosen"] is False for h in hooks)
        assert [h["rank"] for h in hooks] == list(range(1, len(hooks) + 1))


def test_agent_b_drops_invented_quotes_and_bad_types():
    raw = {"hooks": [{"text": 'He said "I made a million overnight"', "type": "story-open", "score": 0.9},
                     {"text": 'He said "that is completely wrong"', "type": "contrarian", "score": 0.8},
                     {"text": "Plain hook", "type": "shock", "score": 0.7},
                     {"text": "Why does this fail?", "type": "question", "score": 0.6, "chosen": True}]}
    hooks = agent_b.validate(raw, "Most people think talent wins. That is completely wrong.")
    assert [h["text"] for h in hooks] == ["He said that is completely wrong", "Why does this fail?"]
    assert not any(h["chosen"] for h in hooks)


def test_agent_b_ranks_candidates():
    ranked = agent_b.rank_candidates([{"start_s": 30, "score": 0.5}, {"start_s": 10, "score": 0.9}, {"start_s": 5, "score": 0.5}])
    assert [(c["start_s"], c["rank"]) for c in ranked] == [(10, 1), (5, 2), (30, 3)]


# ------------------------------------------------------------------ boundary snapping

def test_cuts_snap_to_sentence_boundaries():
    words = vendors.fake_words("en", 60)
    starts, ends = pipeline.sentence_bounds(words)
    start, end = pipeline.snap(3.3, 16.7, words)
    assert any(abs(start + 0.05 - s) < 0.01 for s in starts) and any(abs(end - 0.15 - e) < 0.01 for e in ends)
    assert abs(start - 4.95) < 0.01 and end > start + 5


def test_cuts_snap_to_silence_when_no_sentence_is_near():
    words = [{"text": "one", "start": 0.0, "end": 0.5}, {"text": "two.", "start": 40.0, "end": 40.5}]
    start, end = pipeline.snap(10.2, 20.4, words, silences=[(9.0, 10.0), (20.0, 21.0)])
    assert abs(start - 9.95) < 0.01 and abs(end - 20.15) < 0.01
    assert pipeline.snap(10.2, 20.4, words) == (10.2, 20.4)   # nothing near: unchanged


# ------------------------------------------------------------------ the full pipeline on fixture footage

def test_pipeline_produces_packaged_compliant_clips(produced):
    with produced.db.session(produced.owner) as conn:
        clips = conn.execute("select * from clips order by start_s").fetchall()
        jobs = conn.execute("select type, status, cost_usd, attempts from jobs").fetchall()
        hooks = conn.execute("select * from hooks").fetchall()
        candidates = conn.execute("select * from candidates order by rank").fetchall()
        storage = alerts.registry.get(conn, "storage")
        words = vendors.fake_words("en", 60)
    assert len(clips) == 3 and len(candidates) == 3
    assert sorted(j["type"] for j in jobs) == sorted(["transcribe", "signals", "moments"] + ["hooks", "render", "compliance", "package"] * 3)   # one render job: captions are burned in by its one encode
    assert all(j["status"] == "done" and j["cost_usd"] is not None for j in jobs)
    scores = [float(c["score"]) for c in candidates]   # the moment engine's own scores (phase 4), best first
    assert scores == sorted(scores, reverse=True) and all(0 < x <= 1 for x in scores)
    assert [c["rank"] for c in candidates] == [1, 2, 3] and all(c["origin"] and c["profile"] == "podcast" for c in candidates)
    starts, _ = pipeline.sentence_bounds(words)
    for clip in clips:
        assert clip["status"] == "ready_for_review" and clip["compliance_status"] == "pass"
        path = storage.path(clip["file_path"])
        info = ffmpeg.probe(path)
        assert info["width"] * 16 == info["height"] * 9 == 270 * 16 and info["has_audio"]
        assert abs(info["duration_s"] - float(clip["duration_s"])) < 0.01 and 5 <= info["duration_s"] <= 90
        assert clip["file_hash"] == ffmpeg.sha256_file(path)
        assert clip["caption_text"].startswith("#ad ") and "#FoundersTable" in clip["caption_text"] and "@founderstable" in clip["caption_text"]
        assert clip["overlay_json"]["text"] == "#ad" and clip["overlay_json"]["start_s"] == 0
        assert clip["overlay_json"]["end_s"] >= float(clip["duration_s"]) - 0.1
        assert clip["render_meta"]["loudness_normalised"] and clip["render_meta"]["reframe"] in ("centre_crop", "face")
        assert clip["render_meta"]["caption_language"] == "en"
        assert any(abs(float(clip["start_s"]) + 0.05 - s) < 0.02 for s in starts)   # cut at a sentence start
        assert clip["account_id"] and clip["platform"] in ("tiktok", "instagram_reels", "youtube_shorts")
        report = clip["compliance_report"]
        assert {c["status"] for c in report["deterministic"]} == {"pass"} and report["certifies_legal_compliance"] is False
        mine = [h for h in hooks if h["clip_id"] == clip["id"]]
        assert 2 <= len(mine) <= 3 and not any(h["chosen"] for h in mine)
    assert len({c["platform"] for c in clips}) == 3   # spread over the owner's own accounts


def test_disclosure_overlay_is_really_burned_in_for_the_full_duration(produced, tmp_path):
    """Pixel check on the finished clip: the label region differs from the same frame of the source reframed without any
    overlay at the start, middle and end."""
    with produced.db.session(produced.owner) as conn:
        clip = conn.execute("select * from clips order by start_s limit 1").fetchone()
        storage = alerts.registry.get(conn, "storage")
        source = storage.path(conn.execute("select stored_path from source_assets").fetchone()["stored_path"])
    final, duration = storage.path(clip["file_path"]), float(clip["duration_s"])

    def label_region(src: Path, at: float, name: str, crop: str = "") -> bytes:
        out = tmp_path / name
        ffmpeg.run(["-ss", f"{at:.2f}", "-i", str(src), "-frames:v", "1", "-vf", f"{crop}scale=270:480,crop=iw/2:ih/12:0:0,format=gray",
                    "-f", "rawvideo", str(out)])
        return out.read_bytes()

    plain = media.crop_filter(640, 360, 270, 480) + ","
    for i, at in enumerate((0.1, duration / 2, duration - 0.3)):
        with_label = label_region(final, at, f"a{i}.raw")
        without = label_region(source, float(clip["start_s"]) + at, f"b{i}.raw", plain)
        differing = sum(1 for x, y in zip(with_label, without) if abs(x - y) > 40)
        assert differing > 40, f"no overlay found at {at:.1f} s"
    ass = final.with_suffix(".ass").read_text(encoding="utf-8-sig")
    assert "Label,,0,0,0,,#ad" in ass and "Dialogue: 5,0:00:00.00" in ass


def test_arabic_source_gets_arabic_captions_and_disclosure(db, owner, footage):
    add_accounts(db, owner)
    campaign_id = approved_campaign(db, owner)
    with db.session(owner) as conn:
        core.set_setting(conn, "clips_per_campaign_per_week", 1, campaign_id)
        campaigns.register_source(conn, owner, campaign_id, footage, "ar", permission_ref="P-AR")
    pipeline.run_until_idle(db)
    with db.session(owner) as conn:
        clip = conn.execute("select * from clips").fetchone()
        transcript = conn.execute("select * from transcripts").fetchone()
        storage = alerts.registry.get(conn, "storage")
        cap_alert = conn.execute("select * from alerts where type = 'weekly_clip_cap'").fetchone()
        assert conn.execute("select count(*) as n from candidates").fetchone()["n"] == 3
    assert clip["language"] == "ar" and clip["dialect"] == "ar" and transcript["language"] == "ar"
    assert clip["caption_text"].startswith("#إعلان ") and clip["overlay_json"]["text"] == "#إعلان"
    assert clip["status"] == "ready_for_review" and clip["compliance_status"] == "pass"
    ass = storage.path(clip["file_path"]).with_suffix(".ass").read_text(encoding="utf-8-sig")
    assert "#إعلان" in ass and "Caption" in ass and any(0x600 <= ord(ch) <= 0x6FF for ch in ass.split("[Events]")[-1].replace("#إعلان", ""))
    assert cap_alert is not None   # weekly clip cap is soft: it warns


def test_transcription_is_cached_by_source_hash(produced, footage):
    with produced.db.session(produced.owner) as conn:
        second = approved_campaign(produced.db, produced.owner, "Second campaign")
    with produced.db.session(produced.owner) as conn:
        calls_before = conn.execute("select count(*) as n from audit_log where action = 'adapter.call' and detail->>'stage' = 'transcription'").fetchone()["n"]
        campaigns.register_source(conn, produced.owner, second, footage, "en", permission_ref="P-2")
    pipeline.run_until_idle(produced.db)
    with produced.db.session(produced.owner) as conn:
        calls_after = conn.execute("select count(*) as n from audit_log where action = 'adapter.call' and detail->>'stage' = 'transcription'").fetchone()["n"]
        assert conn.execute("select count(*) as n from transcripts").fetchone()["n"] == 1
        words = json.loads(alerts.registry.get(conn, "storage").path(conn.execute("select word_json_path from transcripts").fetchone()["word_json_path"]).read_text(encoding="utf-8"))
    assert calls_before == calls_after == 1 and {"text", "start", "end"} <= set(words[0])


def test_source_hash_mismatch_stops_the_job(db, owner, tmp_path):
    add_accounts(db, owner)
    campaign_id = approved_campaign(db, owner)
    video = ffmpeg.make_fixture(tmp_path / "small.mp4", seconds=6)
    with db.session(owner) as conn:
        source = campaigns.register_source(conn, owner, campaign_id, str(video), "en", permission_ref="P-3")
        stored = alerts.registry.get(conn, "storage").path(source["stored_path"])
    stored.write_bytes(stored.read_bytes() + b"tampered")
    try:
        pipeline.run_until_idle(db)
        with db.session(owner) as conn:
            job = conn.execute("select status, error, attempts from jobs").fetchall()
            assert len(job) == 1 and job[0]["status"] == "failed" and "hash mismatch" in job[0]["error"] and job[0]["attempts"] == 1
            assert conn.execute("select count(*) as n from alerts where type = 'job_failed'").fetchone()["n"] == 1
    finally:
        stored.unlink()


def test_missing_disclosure_text_blocks_the_clip(db, owner, footage):
    add_accounts(db, owner)
    campaign_id = approved_campaign(db, owner)
    with db.session(Actor.system()) as conn:
        conn.execute("delete from disclosure_templates where language = 'en'")
    with db.session(owner) as conn:
        core.set_setting(conn, "clips_per_campaign_per_week", 1, campaign_id)
        campaigns.register_source(conn, owner, campaign_id, footage, "en", permission_ref="P-4")
    pipeline.run_until_idle(db)
    with db.session(owner) as conn:
        clip = conn.execute("select status, file_path from clips").fetchone()
        assert clip["status"] == "rendering" and clip["file_path"] is None
        assert conn.execute("select status from jobs where type = 'render'").fetchone()["status"] == "failed"
        assert conn.execute("select severity from alerts where type = 'job_failed'").fetchone() is not None
    with pytest.raises(ValueError):
        media.FFmpegRender({"config_json": {}}).render_clip(src="x", clip={}, words=[], template={}, label=" ", out="y")


def test_a_clip_that_fails_compliance_never_reaches_the_feed(db, owner, footage):
    add_accounts(db, owner)
    campaign_id = approved_campaign(db, owner)
    with db.session(Actor.system()) as conn:
        conn.execute("update categories set banned_words = '{costs,tells,unpopular,started,wrong,mistake,what,the,you}' where name = 'podcast'")   # every hook uses one
        conn.execute("update settings set value = '13' where key = 'clip_max_duration_s'")
    with db.session(owner) as conn:
        campaigns.register_source(conn, owner, campaign_id, footage, "en", permission_ref="P-5")
    pipeline.run_until_idle(db)
    from clipping import review

    with db.session(owner) as conn:
        clips = conn.execute("select status, compliance_status, compliance_report from clips").fetchall()
        failed = [c for c in clips if c["compliance_status"] == "fail"]
        assert failed and all(c["status"] == "archived" for c in failed)
        assert all(c["compliance_status"] != "fail" for c in review.feed(conn, owner.operator_id))
        assert len(review.feed(conn, owner.operator_id)) == len(clips) - len(failed)
        assert conn.execute("select count(*) as n from alerts where type = 'clip_blocked' and severity = 'block'").fetchone()["n"] >= 1
        assert conn.execute("select count(*) as n from jobs where type = 'package'").fetchone()["n"] == len(clips) - len(failed)
    with pytest.raises(Exception):   # and the database refuses to move a failed clip forward
        with db.session(Actor.worker()) as conn:
            conn.execute("update clips set status = 'ready_for_review' where compliance_status = 'fail'")


def test_a_paused_moment_service_does_not_stop_the_engine(db, owner, footage):
    add_accounts(db, owner)
    campaign_id = approved_campaign(db, owner)
    with db.session(Actor.system()) as conn:
        conn.execute("update adapters set paused_reason = 'test outage' where stage = 'moments' and active")
    with db.session(owner) as conn:
        campaigns.register_source(conn, owner, campaign_id, footage, "en", permission_ref="P-6")
    pipeline.run_until_idle(db)
    with db.session(owner) as conn:
        origins = {r["origin"] for r in conn.execute("select origin from candidates").fetchall()}
        assert origins and "fake" not in origins            # our own engines carried on without the vendor
        assert conn.execute("select count(*) as n from alerts where type = 'manual_selection'").fetchone()["n"] == 0


def test_when_the_engine_itself_fails_the_owner_is_told_to_pick_by_hand(db, owner, footage, monkeypatch):
    from clipping import moments

    def broken(*args, **kwargs):
        raise AppError("The engine is down.", "engine_down")

    monkeypatch.setattr(moments, "find", broken)
    add_accounts(db, owner)
    campaign_id = approved_campaign(db, owner)
    with db.session(owner) as conn:
        source = campaigns.register_source(conn, owner, campaign_id, footage, "en", permission_ref="P-6")
    pipeline.run_until_idle(db)
    with db.session(owner) as conn:
        assert conn.execute("select count(*) as n from candidates").fetchone()["n"] == 0
        assert conn.execute("select count(*) as n from alerts where type = 'manual_selection'").fetchone()["n"] == 1
        assert pipeline.add_manual_candidates(conn, owner, source["id"], [{"start_s": 6.0, "end_s": 18.0}]) == 1
    pipeline.run_until_idle(db)
    with db.session(owner) as conn:
        clip = conn.execute("select status from clips").fetchone()
        assert clip["status"] == "ready_for_review"
        assert conn.execute("select provider from candidates").fetchone()["provider"] == "manual"


def test_worker_cannot_pass_a_human_gate(produced):
    import psycopg

    for sql in ("update clips set status = 'accepted'", "update clips set status = 'ready_to_post'",
                "update clips set status = 'posted'"):
        with pytest.raises(psycopg.errors.RaiseException):
            with produced.db.session(Actor.worker()) as conn:
                conn.execute(sql)


def test_crop_filter_is_nine_by_sixteen():
    assert media.crop_filter(1920, 1080, 1080, 1920) == "crop=608:1080:656:0,scale=1080:1920:flags=lanczos,setsar=1"
    assert media.crop_filter(1920, 1080, 1080, 1920, focus_x=0.0).startswith("crop=608:1080:0:0")
    assert media.crop_filter(1080, 1920, 1080, 1920).startswith("crop=1080:1920:0:0")
