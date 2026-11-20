"""Phase D step 6: the quality gate (chance of clearing the campaign minimum), brand checks from marketplace rejections, the
correction queue weighted by clip moments, the signals fixes (N2, N3) and the end-to-end demo. Fakes and the embedded database;
FFmpeg is real."""
from __future__ import annotations

import ast
import datetime as dt
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clipping import api, brand_checks, config, core, demo, pipeline, ranking, review, signals, transcripts
from clipping.agents import compliance
from clipping.core import AppError, GateError
from clipping.db import Actor
from conftest import approved_campaign

NOW = dt.datetime(2026, 11, 18, 12, 0, tzinfo=dt.timezone.utc)
ASSUMED = {"median": 2500.0, "sigma": 1.2, "basis": "assumed"}


def feed(produced, **kw):
    """These tests are about the chance-to-clear weakness only. The fixture clips are synthetic (no face, no disclosure in the
    caption), so the quality gate (test_quality_gate.py) would fail all of them and sink them together; its stored result is
    removed here so the two rules are tested apart."""
    with produced.db.session(produced.owner) as conn:
        conn.execute("update clips set render_meta = render_meta - 'gate'")
        return review.feed(conn, produced.owner.operator_id, **kw)


def set_threshold(produced, value):
    with produced.db.session(produced.owner) as conn:
        core.set_setting(conn, "quality_min_p_clear", value, produced.owner.operator_id)


def weakest(clips):
    return min(clips, key=lambda c: c["quality"]["p_clear"])


# ------------------------------------------------------------------ DW-6.1: the chance of clearing the minimum (pure)

def test_the_chance_rises_with_the_accounts_views_and_with_a_better_moment():
    low = review.chance_to_clear(5000, 0.5, {"median": 1000, "sigma": 1.0})
    high = review.chance_to_clear(5000, 0.5, {"median": 20000, "sigma": 1.0})
    assert 0 < low < 0.5 < high < 1
    worse = review.chance_to_clear(5000, 0.1, ASSUMED)
    better = review.chance_to_clear(5000, 0.9, ASSUMED)
    assert worse < review.chance_to_clear(5000, 0.5, ASSUMED) < better


def test_an_average_moment_on_an_account_whose_median_is_the_minimum_is_a_coin_flip():
    assert review.chance_to_clear(2500, 0.5, {"median": 2500, "sigma": 1.2}) == pytest.approx(0.5, abs=1e-9)


@pytest.mark.parametrize("minimum", [None, 0, -5, ""])
def test_no_view_minimum_means_no_chance_is_worked_out(minimum):
    assert review.chance_to_clear(minimum, 0.7, ASSUMED) is None


@pytest.mark.parametrize("percentile", [None, 0.0, 1.0, -3.0, 7.0])
def test_a_missing_or_out_of_range_percentile_is_clamped_and_stays_a_probability(percentile):
    p = review.chance_to_clear(5000, percentile, ASSUMED)
    assert 0.0 < p < 1.0 and math.isfinite(p)


def test_a_tiny_spread_and_a_tiny_median_do_not_break_the_maths():
    assert 0 <= review.chance_to_clear(5000, 0.5, {"median": 0, "sigma": 0}) <= 1
    assert review.chance_to_clear(5000, 0.5, {"median": 5000, "sigma": 0}) == pytest.approx(0.5, abs=0.01)


def test_the_moment_percentile_counts_ties_as_half_and_is_none_when_unknown():
    scores = [0.2, 0.5, 0.5, 0.9]
    assert review.moment_percentile(0.9, scores) == pytest.approx(3.5 / 4)
    assert review.moment_percentile(0.5, scores) == pytest.approx((1 + 1) / 4)
    assert review.moment_percentile(0.2, scores) == pytest.approx(0.5 / 4)
    assert review.moment_percentile(0.4, [0.4]) == 0.5
    assert review.moment_percentile(None, scores) is None and review.moment_percentile(0.4, []) is None


def test_the_label_says_the_chance_the_minimum_and_where_the_numbers_come_from():
    assert review.quality_label(0.62, 10000, "your posts", False) == "About 62% chance to reach 10,000 views (from your own posts)"
    weak = review.quality_label(0.12, 5000, "assumed", True)
    assert weak == "Weak: about 12% chance to reach 5,000 views (assumed: no view history yet). Shown last."
    assert "under 1%" in review.quality_label(0.004, 5000, "assumed", True) and "over 99%" in review.quality_label(0.999, 5000, "your posts", False)
    assert review.quality_label(None, None, "assumed", False) == "No view minimum on this campaign"


def _clip(name, pay, weak, hour):
    return {"id": name, "compliance_status": "pass", "est_net_per_hour": pay, "created_at": NOW + dt.timedelta(hours=hour),
            "quality": {"weak": weak}}


WEIGHTS = {"net_pay": 1.0, "deadline": 0.0, "hook_score": 0.0, "budget": 0.0, "account_cap": 0.0, "compliance": 0.0}


def test_DW_6_1_a_weak_clip_sorts_below_every_other_clip_whatever_it_pays():
    clips = [_clip("rich-but-weak", 9.0, True, 0), _clip("poor", 1.0, False, 1), _clip("middle", 4.0, False, 2), _clip("weak-too", 5.0, True, 3)]
    assert [c["id"] for c in review.rank_feed(clips, WEIGHTS, NOW)] == ["middle", "poor", "rich-but-weak", "weak-too"]


def test_a_clip_with_no_quality_block_is_not_weak_and_nothing_is_dropped():
    clips = [{"id": "a", "compliance_status": "pass", "est_net_per_hour": 1, "created_at": NOW},
             {"id": "b", "compliance_status": "review", "est_net_per_hour": 2, "created_at": NOW}]
    assert [c["id"] for c in review.rank_feed(clips, WEIGHTS, NOW)] == ["b", "a"]


# ------------------------------------------------------------------ DW-6.1 on the database

def test_DW_6_1_the_weakest_clip_goes_last_even_when_its_hook_would_put_it_first(produced):
    set_threshold(produced, 0)
    plain = feed(produced)
    weak_id = weakest(plain)["id"]
    with produced.db.session(produced.owner) as conn:
        conn.execute("update clips set hook_score = 5 where id = %s", (weak_id,))
    assert feed(produced)[0]["id"] == weak_id                      # without the gate its strong hook puts it on top
    set_threshold(produced, 0.25)
    gated = feed(produced)
    assert [c["id"] for c in gated][-1] == weak_id and len(gated) == 3
    quality = gated[-1]["quality"]
    assert quality["weak"] is True and quality["p_clear"] < 0.25 and quality["label"].startswith("Weak: ")
    assert "5,000 views" in quality["label"] and quality["label"].endswith("Shown last.")
    assert not any(c["quality"]["weak"] for c in gated[:-1])


def test_DW_6_1_the_threshold_is_a_setting_nobody_is_weak_at_zero_everybody_at_the_top(produced):
    set_threshold(produced, 0)
    assert not any(c["quality"]["weak"] for c in feed(produced))
    set_threshold(produced, 0.9)
    clips = feed(produced)
    assert all(c["quality"]["weak"] for c in clips) and len(clips) == 3
    assert [c["priority_score"] for c in clips] == sorted((c["priority_score"] for c in clips), reverse=True)


def test_no_view_history_uses_assumed_stats_and_the_label_says_so(produced):
    for clip in feed(produced):
        quality = clip["quality"]
        assert quality["basis"] == "assumed" and "assumed" in quality["label"]
        assert quality["min_views"] == 5000 and 0 < quality["p_clear"] < 1
        assert set(quality) >= {"p_clear", "label", "basis", "weak", "min_views", "moment_percentile", "threshold"}


def test_an_account_with_its_own_view_history_is_judged_on_it_and_says_your_posts(produced, monkeypatch):
    seen = []

    def stats(conn, account):
        seen.append(account["id"])
        return {"median": 400000.0, "sigma": 0.7, "basis": "your posts"}
    monkeypatch.setattr(ranking, "account_stats", stats)
    clips = feed(produced)
    assert len(set(seen)) == 3                                      # one lookup per account, not per clip
    for clip in clips:
        assert clip["quality"]["basis"] == "your posts" and "from your own posts" in clip["quality"]["label"]
        assert clip["quality"]["p_clear"] > 0.9 and not clip["quality"]["weak"]


def test_the_real_account_stats_move_with_the_accounts_assumed_median(produced):
    with produced.db.session(produced.owner) as conn:
        account = conn.execute("select id from accounts where handle = 'own_tt'").fetchone()["id"]
        core.set_setting(conn, "assumed_median_views", 900000, account)
    clips = {c["account_handle"]: c for c in feed(produced)}
    assert clips["own_tt"]["quality"]["p_clear"] > 0.95 and clips["own_ig"]["quality"]["p_clear"] < 0.5


def test_a_campaign_without_a_view_minimum_has_no_chance_and_is_never_weak(produced):
    with produced.db.session(produced.owner) as conn:
        conn.execute("update campaign_versions set min_views_to_qualify = null")
    set_threshold(produced, 0.9)
    for clip in feed(produced):
        q = clip["quality"]
        assert q["p_clear"] is None and q["weak"] is False and q["min_views"] is None and q["label"] == "No view minimum on this campaign"


def test_a_clip_without_an_account_or_a_scored_moment_is_still_judged_on_assumed_stats(produced):
    with produced.db.session(produced.owner) as conn:
        conn.execute("update clips set account_id = null")
        conn.execute("update candidates set score = null")
    clips = feed(produced)
    assert len(clips) == 3
    for clip in clips:
        assert clip["quality"]["basis"] == "assumed" and clip["quality"]["moment_percentile"] is None and clip["quality"]["p_clear"] is not None


def test_every_clip_view_carries_quality_and_hides_the_working_columns(produced):
    with produced.db.session(produced.owner) as conn:
        one = review.get_clip(conn, feed(produced)[0]["id"])
        later = review.park(conn, produced.owner, one["id"], "later")
        folders = review.review_later(conn)
    assert "quality" in one and "moment_score" not in one and "min_views_to_qualify" not in one
    assert folders[0]["clips"][0]["quality"]["basis"] == "assumed" and later["decision"] == "review_later"


def test_the_feed_endpoint_returns_quality_on_every_clip(produced):
    api._owner = produced.owner
    body = TestClient(api.app).get("/api/feed").json()
    assert len(body["clips"]) == 3
    for clip in body["clips"]:
        assert set(clip["quality"]) >= {"p_clear", "label", "basis"} and clip["quality"]["basis"] in ("your posts", "assumed")
    assert [c["quality"]["weak"] for c in body["clips"]] == sorted(c["quality"]["weak"] for c in body["clips"])   # weak ones last


# ------------------------------------------------------------------ DW-6.2: brand checks (reading a reason)

@pytest.mark.parametrize("reason,kind,pattern,severity", [
    ("Rejected: missing #FoundersTable in the caption", "require_text", "#FoundersTable", "warn"),
    ("You must tag @acme in the post", "require_text", "@acme", "warn"),
    ('Do not use the word "guaranteed" in captions', "banned_phrase", "guaranteed", "block"),
    ("#giveaway is not allowed for this brand", "banned_phrase", "#giveaway", "block"),
    ("ممنوع استخدام «مضمون» في الوصف", "banned_phrase", "مضمون", "block"),
    ("الوصف ناقص #إعلان", "require_text", "#إعلان", "warn"),
])
def test_a_specific_reason_becomes_a_check(reason, kind, pattern, severity):
    rules = brand_checks.interpret(reason)
    assert [(r["kind"], r["pattern"], r["severity"]) for r in rules] == [(kind, pattern, severity)]


@pytest.mark.parametrize("reason", [
    "Not on brand", "Low quality, sorry", "Doesn't fit the campaign", "missing something important", "#ad", "ممنوع",
    'Do not use "free" but missing "offer"',   # forbids and requires in one clause: too unclear to check
    "", "   ", "\x00\x01",
])
def test_a_vague_reason_is_kept_as_a_reminder_not_a_check(reason):
    rules = brand_checks.interpret(reason)
    assert len(rules) == 1 and rules[0]["kind"] == "reminder" and rules[0]["severity"] == "reminder"


def test_two_clauses_give_two_checks_and_repeats_are_merged():
    rules = brand_checks.interpret('Missing #ad. Do not say "free money", no "FREE MONEY" please; also missing #ad')
    assert sorted((r["kind"], r["pattern"]) for r in rules) == [("banned_phrase", "free money"), ("require_text", "#ad")]


def test_a_reason_cannot_make_an_unbounded_number_of_checks():
    reason = ". ".join(f"missing #tag{i}" for i in range(40))
    assert len(brand_checks.interpret(reason)) == brand_checks.MAX_RULES_PER_REASON


def test_evaluating_checks_matches_whole_words_in_any_case_and_ignores_accents():
    rules = [{"brand": "b", "kind": "require_text", "pattern": "#ad", "rule": "r1", "quote": "q", "severity": "warn"},
             {"brand": "b", "kind": "banned_phrase", "pattern": "guaranteed", "rule": "r2", "quote": "q", "severity": "block"},
             {"brand": "b", "kind": "reminder", "pattern": "x", "rule": "r3", "quote": "Not on brand", "severity": "reminder"}]
    by_rule = lambda rows: {r["rule"]: r["status"] for r in rows}   # noqa: E731
    assert by_rule(brand_checks.evaluate(rules, ["#Ad Great clip", "", None])) == {"r1": "pass", "r2": "pass", "r3": "note"}
    assert by_rule(brand_checks.evaluate(rules, ["#ads only, Guaranteed wins"])) == {"r1": "review", "r2": "fail", "r3": "note"}
    assert by_rule(brand_checks.evaluate(rules, ["not#ad and noguaranteed"])) == {"r1": "review", "r2": "pass", "r3": "note"}
    arabic = [{"brand": "b", "kind": "banned_phrase", "pattern": "مضمون", "rule": "a", "quote": "q", "severity": "block"}]
    assert brand_checks.evaluate(arabic, ["ربح مَضْمُون اليوم"])[0]["status"] == "fail"
    assert brand_checks.evaluate([], ["anything"]) == []


def test_evaluated_rows_are_ordered_blocks_first_and_have_unique_names():
    rules = [{"brand": "b", "kind": "reminder", "pattern": "x", "rule": "z", "quote": "q", "severity": "reminder"},
             {"brand": "b", "kind": "banned_phrase", "pattern": "x", "rule": "y", "quote": "q", "severity": "block"},
             {"brand": "b", "kind": "require_text", "pattern": "#t", "rule": "w", "quote": "q", "severity": "warn"}]
    rows = brand_checks.evaluate(rules, ["text"])
    assert [r["severity"] for r in rows] == ["block", "warn", "reminder"] and len({r["check"] for r in rows}) == 3


def test_a_warn_makes_the_verdict_review_a_block_fails_and_a_note_changes_nothing():
    note, warn, block, ok = ({"check": "c", "status": s} for s in ("note", "review", "fail", "pass"))
    assert compliance.overall([ok, note], []) == "pass"
    assert compliance.overall([ok, warn], []) == "review"
    assert compliance.overall([warn, block], []) == "fail"
    assert compliance.overall([ok], [{"status": "review"}]) == "review"


# ------------------------------------------------------------------ DW-6.2 on the database

def _ctx(produced, clip_id):
    with produced.db.session(produced.owner) as conn:
        clip, _, campaign, version = pipeline._clip_ctx(conn, clip_id)
        return pipeline.run_compliance(conn, clip, campaign, version)[0]


def _brand_rows(report):
    return [c for c in report["deterministic"] if c["check"].startswith("brand_")]


def test_DW_6_2_a_rejection_recorded_for_a_brand_flags_the_same_issue_on_the_next_clip(produced):
    first, second = (c["id"] for c in feed(produced)[:2])
    before = _ctx(produced, second)
    assert before["status"] == "pass" and _brand_rows(before) == []
    with produced.db.session(produced.owner) as conn:
        done = brand_checks.record_rejection(conn, produced.owner, first, "Rejected: missing #SampleTag in the caption")
    assert done["brand"] == "founders table" and done["kept_as"] == "checks" and done["added"] == 1
    after = _ctx(produced, second)
    [row] = _brand_rows(after)
    assert row["status"] == "review" and row["severity"] == "warn" and row["brand"] == "founders table"
    assert "#sampletag" in row["evidence"].lower() and "missing #SampleTag" in row["evidence"]
    assert after["status"] == "review"
    with produced.db.session(produced.owner) as conn:   # the caption gets the tag: the same check now passes
        conn.execute("update clips set caption_text = caption_text || ' #sampletag' where id = %s", (second,))
    assert _brand_rows(_ctx(produced, second))[0]["status"] == "pass"


def test_DW_6_2_a_banned_phrase_blocks_the_clip_like_any_deterministic_failure(produced):
    first, second = (c["id"] for c in feed(produced)[:2])
    with produced.db.session(produced.owner) as conn:
        brand_checks.record_rejection(conn, produced.owner, first, 'Do not use the word "guaranteed" in captions')
        conn.execute("update clips set caption_text = caption_text || ' guaranteed results' where id = %s", (second,))
    report = _ctx(produced, second)
    assert report["status"] == "fail" and _brand_rows(report)[0]["status"] == "fail"
    assert "Agent C was not asked" in report["note"]


def test_DW_6_2_a_vague_reason_is_a_reminder_that_never_blocks(produced):
    first, second = (c["id"] for c in feed(produced)[:2])
    with produced.db.session(produced.owner) as conn:
        done = brand_checks.record_rejection(conn, produced.owner, first, "Not on brand, sorry")
    assert done["kept_as"] == "reminder" and done["checks"][0]["severity"] == "reminder"
    report = _ctx(produced, second)
    [row] = _brand_rows(report)
    assert row["status"] == "note" and "Not on brand, sorry" in row["evidence"] and report["status"] == "pass"


def test_a_rejection_for_one_brand_leaves_another_brand_alone(produced):
    other = approved_campaign(produced.db, produced.owner, name="Other Brand", brief_name="01")
    with produced.db.session(produced.owner) as conn:
        brand_checks.record_rejection(conn, produced.owner, feed(produced)[0]["id"], "missing #SampleTag")
        assert len(brand_checks.for_campaign(conn, produced.campaign_id, produced.owner.operator_id)) == 1
        assert brand_checks.for_campaign(conn, other, produced.owner.operator_id) == []


def test_a_listing_brand_is_the_brand_when_the_campaign_came_from_a_listing(produced):
    with produced.db.session(Actor.system()) as conn:
        listing = conn.execute("""insert into listings (source, external_id, title, brand) values ('fake', 'x1', 'T', '  ACME   Cola ')
                                  returning id""").fetchone()
        conn.execute("update campaigns set listing_id = %s", (listing["id"],))
    with produced.db.session(produced.owner) as conn:
        assert brand_checks.brand_of(conn, produced.campaign_id) == "acme cola"


def test_recording_the_same_reason_twice_adds_nothing_and_switching_a_check_off_removes_its_row(produced):
    first, second = (c["id"] for c in feed(produced)[:2])
    with produced.db.session(produced.owner) as conn:
        a = brand_checks.record_rejection(conn, produced.owner, first, "missing #SampleTag")
        b = brand_checks.record_rejection(conn, produced.owner, second, "Missing #sampletag!")
        assert (a["added"], b["added"]) == (1, 0) and a["checks"][0]["id"] == b["checks"][0]["id"]
        assert len(brand_checks.listing(conn)) == 1
        assert brand_checks.switch(conn, produced.owner, a["checks"][0]["id"], False)["active"] is False
    assert _brand_rows(_ctx(produced, second)) == []


def test_a_bad_rejection_is_refused_with_a_sentence_the_owner_can_act_on(produced):
    clip = feed(produced)[0]["id"]
    with produced.db.session(produced.owner) as conn:
        for reason, code in [("", "reason_required"), ("   ", "reason_required"), (None, "reason_required"), (42, "reason_required"),
                             ("x" * (brand_checks.MAX_REASON + 1), "reason_too_long")]:
            with pytest.raises(AppError) as err:
                brand_checks.record_rejection(conn, produced.owner, clip, reason)
            assert err.value.code == code
        with pytest.raises(AppError) as err:
            brand_checks.record_rejection(conn, produced.owner, "00000000-0000-0000-0000-000000000000", "missing #x")
        assert err.value.code == "not_found"
        with pytest.raises(GateError):
            brand_checks.record_rejection(conn, Actor.worker(), clip, "missing #x")
        with pytest.raises(GateError):
            brand_checks.switch(conn, Actor.worker(), clip, False)
        with pytest.raises(AppError):
            brand_checks.switch(conn, produced.owner, "00000000-0000-0000-0000-000000000000", False)
        assert brand_checks.listing(conn) == []
    with produced.db.session(produced.owner) as conn:   # the boundary: exactly the limit is fine
        assert brand_checks.record_rejection(conn, produced.owner, clip, "n" * brand_checks.MAX_REASON)["kept_as"] == "reminder"


def test_another_owner_cannot_see_or_inherit_a_brand_check(produced):
    with produced.db.session(produced.owner) as conn:
        brand_checks.record_rejection(conn, produced.owner, feed(produced)[0]["id"], "missing #SampleTag")
    with produced.db.session(Actor.system()) as conn:
        row = conn.execute("insert into operators (name, role) values ('Second owner', 'operator') returning id").fetchone()
    stranger = Actor(str(row["id"]), "operator")
    with produced.db.session(stranger) as conn:
        assert conn.execute("select count(*) as n from brand_checks").fetchone()["n"] == 0
        with pytest.raises(AppError):                                   # the stranger cannot even see the campaign
            brand_checks.for_campaign(conn, produced.campaign_id, stranger.operator_id)
    with produced.db.session(Actor.worker()) as conn:     # the worker sees every row, so the owner is always explicit
        assert brand_checks.for_campaign(conn, produced.campaign_id, stranger.operator_id) == []
        assert len(brand_checks.for_campaign(conn, produced.campaign_id, produced.owner.operator_id)) == 1


def test_the_audit_log_names_the_clip_and_the_count_but_never_the_marketplaces_words(produced):
    with produced.db.session(produced.owner) as conn:
        brand_checks.record_rejection(conn, produced.owner, feed(produced)[0]["id"], "Rejected: missing #SecretSentenceTag today")
        rows = conn.execute("select detail from audit_log where action = 'brand_check.recorded'").fetchall()
        everything = " ".join(str(r["detail"]) for r in conn.execute("select detail from audit_log where detail is not null").fetchall())
    assert len(rows) == 1 and rows[0]["detail"]["count"] == 1 and rows[0]["detail"]["kinds"] == ["require_text"]
    assert "SecretSentenceTag".lower() not in everything.lower().replace("brand_checks", "")


def test_the_rejection_endpoints_record_list_and_switch_off(produced):
    api._owner = produced.owner
    client = TestClient(api.app)
    clip = feed(produced)[0]["id"]
    done = client.post(f"/api/clips/{clip}/rejection", json={"reason": "Do not use \"guaranteed\" here"})
    assert done.status_code == 200 and done.json()["checks"][0]["kind"] == "banned_phrase" and done.json()["brand"] == "founders table"
    bad = client.post(f"/api/clips/{clip}/rejection", json={"reason": ""})
    assert bad.status_code == 400 and bad.json()["code"] == "reason_required"
    assert client.post(f"/api/clips/{clip}/rejection", json={}).json()["code"] == "reason_required"
    assert client.post("/api/clips/00000000-0000-0000-0000-000000000000/rejection", json={"reason": "x"}).json()["code"] == "not_found"
    listed = client.get("/api/brand-checks", params={"campaign_id": produced.campaign_id}).json()["checks"]
    assert len(listed) == 1 and listed[0]["quote"].startswith("Do not use")
    off = client.post(f"/api/brand-checks/{listed[0]['id']}/active", json={"active": False})
    assert off.status_code == 200 and off.json()["active"] is False
    assert client.get("/api/brand-checks").json()["checks"][0]["active"] is False


# ------------------------------------------------------------------ DW-6.5: the correction queue weighted by clip moments

def _words_with_two_unsure(produced):
    """Words 2 s apart; word 0 (at 0 s) and word 21 (at 42 s) are equally unsure. The stored moments cover 4.95 to 54.3 s."""
    with produced.db.session(produced.owner) as conn:
        current = transcripts.load(conn, produced.source_id)
        words = [dict(w, confidence=0.99) for w in current["words"]]
        words[0]["confidence"] = 0.5
        words[40]["confidence"] = 0.5
        transcripts.save_version(conn, produced.owner, current["id"], words, None, current["version"])
        return words, current["id"]


def test_DW_6_5_words_inside_likely_clip_moments_come_first(produced):
    words, transcript_id = _words_with_two_unsure(produced)
    early, late = 0, 40
    assert words[early]["start"] < 4.95 <= words[late]["start"] < 54.3
    with produced.db.session(produced.owner) as conn:
        plain = [r["word_index"] for r in transcripts.review_queue(conn, transcript_id, [])]       # moments=[]: no weighting
        weighted = [r["word_index"] for r in transcripts.review_queue(conn, transcript_id)]        # None: the stored candidates
        by_source = [r["word_index"] for r in transcripts.queue_for(conn, produced.source_id)]
    assert plain == [early, late] and weighted == [late, early] and by_source == weighted


def test_DW_6_5_a_strong_moment_beats_a_weak_one_and_a_moment_is_a_plain_dict(produced):
    words, transcript_id = _words_with_two_unsure(produced)
    t_early, t_late = words[0]["start"] + 0.1, words[40]["start"] + 0.1
    moments = [{"start_s": t_early - 1, "end_s": t_early + 1, "score": 0.2}, {"start_s": t_late - 1, "end_s": t_late + 1, "score": 0.9}]
    with produced.db.session(produced.owner) as conn:
        assert [r["word_index"] for r in transcripts.review_queue(conn, transcript_id, moments)] == [40, 0]
        flipped = [dict(moments[0], score=0.95), moments[1]]
        assert [r["word_index"] for r in transcripts.review_queue(conn, transcript_id, flipped)] == [0, 40]


def test_the_words_call_of_review_queue_still_works_and_a_moment_beats_key_terms_and_confidence():
    def w(text, start, conf):
        return {"text": text, "start": start, "end": start + 0.5, "confidence": conf, "speaker": None}
    words = [w("a", 0, 0.1), w("acne", 2, 0.9), w("cola", 2.6, 0.9), w("c", 4, 0.7), w("d", 6, 0.79)]
    plain = transcripts.review_queue(words, ["Acme Cola"])
    assert transcripts.rank_words(words, ["Acme Cola"]) == plain
    assert [r["word_index"] for r in plain] == [1, 0, 3, 4]               # the key term, then the least sure
    inside_d = transcripts.review_queue(words, ["Acme Cola"], ranges=[(5.9, 6.6)])
    assert [r["word_index"] for r in inside_d] == [4, 1, 0, 3]            # a moment outranks the key term
    weighted = transcripts.review_queue(words, ["Acme Cola"], ranges=[(0, 0.4, 0.3), (5.9, 6.6, 0.8)])
    assert [r["word_index"] for r in weighted] == [4, 0, 1, 3]


@pytest.mark.parametrize("moments", [[{"start_s": "x", "end_s": 3}], [{"end_s": 3}], [None], [{"start_s": 5, "end_s": 5}],
                                     [{"start_s": float("nan"), "end_s": 9}], "nonsense", 7])
def test_moments_that_cannot_be_read_are_skipped_and_never_break_the_queue(moments):
    assert transcripts.moment_ranges(moments if not isinstance(moments, (str, int)) else None) == []
    if isinstance(moments, list):
        words = [{"text": "x", "start": 0, "end": 1, "confidence": 0.1, "speaker": None}]
        assert len(transcripts.rank_words(words, [], transcripts.moment_ranges(moments))) == 1


def test_a_moment_with_no_score_weighs_a_half_and_decimal_times_work():
    from decimal import Decimal
    assert transcripts.moment_ranges([{"start_s": Decimal("1.5"), "end_s": Decimal("4"), "score": None}]) == [(1.5, 4.0, 0.5)]


def test_the_queue_for_an_unknown_transcript_is_not_found(produced):
    with produced.db.session(produced.owner) as conn:
        with pytest.raises(transcripts.NotFound):
            transcripts.review_queue(conn, "00000000-0000-0000-0000-000000000000")


def test_the_filtered_and_discarded_moments_do_not_weight_the_queue(produced):
    with produced.db.session(produced.owner) as conn:
        assert len(transcripts.source_moments(conn, produced.source_id)) == 3
        conn.execute("update candidates set status = 'filtered' where start_s < 30")
        conn.execute("update candidates set status = 'discarded' where start_s > 30")
        assert transcripts.source_moments(conn, produced.source_id) == []


def test_DW_6_5_the_review_queue_endpoint_uses_the_moments(produced):
    words, _ = _words_with_two_unsure(produced)
    api._owner = produced.owner
    rows = TestClient(api.app).get(f"/api/transcripts/{produced.source_id}/review-queue").json()
    assert [r["word_index"] for r in rows] == [40, 0]


# ------------------------------------------------------------------ N2 and N3: signals follow the transcript

def test_N2_load_rebuilds_the_text_signals_when_the_transcript_has_a_newer_version(produced, monkeypatch):
    from clipping import ffmpeg
    calls = []
    for name in ("probe", "audio_pass", "video_thumbs"):
        real = getattr(ffmpeg, name)
        monkeypatch.setattr(ffmpeg, name, lambda *a, _r=real, _n=name, **k: (calls.append(_n), _r(*a, **k))[1])
    with produced.db.session(produced.owner) as conn:
        before = signals.load(conn, produced.source_id)
        current = transcripts.load(conn, produced.source_id)
        words = [dict(w) for w in current["words"]]
        words[0].update(text="Wow", start=1.0, end=1.4)
        transcripts.save_version(conn, produced.owner, current["id"], words, None, current["version"])
        after = signals.load(conn, produced.source_id)              # load, not compute: the moment engine and render call load
        again = signals.load(conn, produced.source_id)
        stored = conn.execute("select transcript_version from source_signals").fetchone()["transcript_version"]
    assert calls == [] and stored == 2 and again == after
    assert before["series"]["reaction"][1] == 0 and after["series"]["reaction"][1] == 1
    assert after["series"]["loudness"] == before["series"]["loudness"] and after["series"]["scene"] == before["series"]["scene"]


def test_N2_load_leaves_signals_alone_when_the_transcript_is_unchanged_or_missing(produced):
    with produced.db.session(produced.owner) as conn:
        first = signals.load(conn, produced.source_id)
        assert signals.load(conn, produced.source_id) == first
        assert transcripts.current_version(conn, produced.source_id) == 1
        assert transcripts.current_version(conn, "00000000-0000-0000-0000-000000000000") is None
        conn.execute("update source_signals set transcript_version = 0")
        conn.execute("update source_assets set file_hash = %s", ("f" * 64,))   # a changed file is not re-measured by load
        assert signals.load(conn, produced.source_id)["series"]["loudness"] == first["series"]["loudness"]


def test_N3_reaction_words_in_silent_seconds_count_for_nothing():
    def w(text, start):
        return {"text": text, "start": start, "end": start + 0.4, "confidence": 0.95, "speaker": None}
    words = [w("wow", 1.0), w("no", 5.0), w("way", 5.4), w("wow", 8.0)]
    loud = [-20.0] * 4 + [-70.0] * 4 + [-20.0] * 2          # seconds 4 to 7 are silent: the words said there are not on the audio
    out = signals.text_signals({"words": words, "utterances": []}, loud, 10)
    assert out["reaction"][1] == 1 and out["reaction"][5] == 0 and out["reaction"][8] == 1 and sum(out["reaction"]) == 2
    assert signals.text_signals({"words": words, "utterances": []}, [-20.0] * 10, 10)["reaction"][5] == 1


# ------------------------------------------------------------------ DW-6.3: the demo runs every stage

@pytest.fixture
def sample_footage(monkeypatch, footage):
    monkeypatch.setattr(demo, "fixture_video", lambda: footage)


def test_DW_6_3_the_demo_runs_transcription_signals_moments_render_and_review(db, owner, sample_footage):
    lines = []
    out = demo.run(db, log=lines.append, render_size=None)
    text = "\n".join(out["steps"])
    for stage in ("Transcription: ", "Signals: 60 seconds measured", "Moments: 3 kept", "Render: 3 clip(s), one encode each: yes"):
        assert stage in text, stage
    assert text.index("Transcription: ") < text.index("Signals: ") < text.index("Moments: ") < text.index("Render: ") < text.index("Accepted the top clip")
    assert "chance to reach" in text and "A marketplace rejection reason was recorded" in text and "stopped" not in out
    assert any("Session summary" in s for s in out["steps"]) and any("Audit log holds" in s for s in out["steps"])
    with db.session(owner) as conn:
        assert conn.execute("select count(*) as n from brand_checks").fetchone()["n"] == 1
        assert not core.kill_switch_on(conn)


def test_a_second_demo_run_does_nothing(db, owner, sample_footage):
    demo.run(db, log=lambda *_: None, render_size=None, finish_all=False)
    with db.session(owner) as conn:
        before = conn.execute("select count(*) as n from clips").fetchone()["n"]
    again = demo.run(db, log=lambda *_: None, render_size=None)
    with db.session(owner) as conn:
        assert again["steps"] == [] and conn.execute("select count(*) as n from clips").fetchone()["n"] == before


def test_DW_6_3_the_demo_does_not_start_when_the_kill_switch_is_already_on(db, owner, sample_footage):
    with db.session(owner) as conn:
        core.set_kill_switch(conn, True, "test")
    out = demo.run(db, log=lambda *_: None, render_size=None)
    assert out["stopped"] == "kill_switch" and "did not start" in out["steps"][0]
    with db.session(owner) as conn:
        assert conn.execute("select count(*) as n from campaigns").fetchone()["n"] == 0
        assert core.kill_switch_on(conn)            # the demo never switches off a switch it did not switch on


def test_DW_6_3_the_demo_stops_cleanly_when_the_kill_switch_goes_on_during_production(db, owner, sample_footage, monkeypatch):
    def pipeline_with_the_switch_thrown(database, max_jobs=500):
        with database.session(owner) as conn:
            core.set_kill_switch(conn, True, "thrown during the demo")
        return 0
    monkeypatch.setattr(pipeline, "run_until_idle", pipeline_with_the_switch_thrown)
    out = demo.run(db, log=lambda *_: None, render_size=None)
    assert out["stopped"] == "kill_switch" and "stopped here" in out["steps"][-1]
    assert not any("Accepted" in s for s in out["steps"])


def test_a_failed_production_names_the_failed_job_instead_of_saying_nothing_happened(db, owner, sample_footage, monkeypatch):
    real = pipeline.run_until_idle

    def broken(database, max_jobs=500):
        with database.session(Actor.system()) as conn:
            conn.execute("update adapters set active = false where stage = 'transcription'")
        return real(database, max_jobs)
    monkeypatch.setattr(pipeline, "run_until_idle", broken)
    with pytest.raises(RuntimeError) as err:
        demo.run(db, log=lambda *_: None, render_size=None)
    assert "produced no clips" in str(err.value) and "transcribe" in str(err.value)


def test_stage_lines_say_what_is_missing_when_nothing_ran(db, owner):
    with db.session(owner) as conn:
        lines = demo.stage_lines(conn, "00000000-0000-0000-0000-000000000000")
    assert lines[0] == "Transcription: no transcript was stored" and lines[1].startswith("Signals: not measured")
    assert "0 kept" in lines[2] and "one encode each: no" in lines[3]


# ------------------------------------------------------------------ DW-6.3: no keys

RUN_PY = Path(__file__).resolve().parents[2] / "run.py"


def _run_py_names(name):
    tree = ast.parse(RUN_PY.read_text(encoding="utf-8"))
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None) == name)


def test_the_no_keys_demo_blanks_every_secret_that_can_reach_a_live_service():
    blanked = set(_run_py_names("KEY_NAMES"))
    own = {"CLIPPING_MEDIA_SECRET", "CLIPPING_LOCAL_TOKEN"}               # the app's own tokens, not a service's
    assert set(config.SECRET_NAMES) - own <= blanked
    assert {"AZURE_OPENAI_API_KEY", "DEEPGRAM_API_KEY", "DATABASE_URL", "R2_BUCKET"} <= blanked


def test_DW_6_3_with_fakes_off_and_no_keys_the_demo_still_completes(tmp_path, monkeypatch):
    """A real subprocess in a throwaway data dir: fakes off, every key blank in this process's environment only (.env is never
    read for a value that is set, even to an empty string)."""
    backend = Path(__file__).resolve().parents[1]
    env = {**os.environ, "CLIPPING_USE_FAKES": "0", "CLIPPING_DATA_DIR": str(tmp_path / "data"), "PYTHONIOENCODING": "utf-8",
           "AUTH_MODE": "local", **{name: "" for name in _run_py_names("KEY_NAMES")}}
    done = subprocess.run([sys.executable, "-m", "clipping", "demo", "--feed-only"], cwd=backend, env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=900)
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    assert "Render: " in done.stdout and "Accepted the top clip" in done.stdout and "Adapter switched on" not in done.stdout


def test_a_clip_that_failed_the_quality_gate_goes_last_in_the_feed_even_with_the_best_score(produced):
    set_threshold(produced, 0)
    top = feed(produced)[0]["id"]
    with produced.db.session(produced.owner) as conn:
        conn.execute("update clips set render_meta = jsonb_set(render_meta, '{gate}', '{\"status\": \"fail\", \"reasons\": []}') where id = %s", (top,))
        ids = [c["id"] for c in review.feed(conn, produced.owner.operator_id)]
    assert ids[-1] == top and len(ids) == 3


def test_priority_is_the_campaign_rate_times_the_chance_to_clear_the_minimum():
    def card(name, rate, p):
        return {"id": name, "compliance_status": "pass", "est_net_per_hour": rate, "created_at": NOW,
                "quality": {"p_clear": p, "weak": False}}
    sure, risky, no_min = card("sure", 3.0, 0.9), card("risky", 4.0, 0.3), card("no_min", 2.0, None)
    assert review.expected_rate(sure) == pytest.approx(2.7) and review.expected_rate(risky) == pytest.approx(1.2)
    assert review.expected_rate(no_min) == 2.0                                      # no minimum: a sure thing
    assert review.expected_rate({"est_net_per_hour": 5.0}) == 5.0                   # no estimate: rate unchanged
    ranked = review.rank_feed([risky, no_min, sure], WEIGHTS, NOW)
    assert [c["id"] for c in ranked] == ["sure", "no_min", "risky"]                  # 2.7 > 2.0 > 1.2, though risky pays most per 1,000 views
    assert [c["id"] for c in review.rank_feed([sure, no_min, risky], WEIGHTS, NOW)] == ["sure", "no_min", "risky"]   # same answer in any input order
