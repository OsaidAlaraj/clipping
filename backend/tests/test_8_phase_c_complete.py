"""Phase C, the remaining decisions: audience countries (API and screenshot), Arabic accounts, the brief's
language, fixed-pay campaigns for new accounts, payout record, spreading risk, fresh budgets first, short
transactions for the discovery pass."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from clipping import api, audience, config, discovery, pipeline, ranking
from clipping.adapters import audience as audience_feed
from clipping.adapters import discovery as feed
from clipping.adapters import fake_llm
from clipping.core import AppError, claim_job, enqueue
from clipping.db import Actor
from conftest import approved_campaign

SAMPLE = json.loads((config.FIXTURES / "clippingalpha" / "search_campaigns.json").read_text(encoding="utf-8"))["campaigns"]


def _account(db, owner, platform="tiktok", handle="me_tt", language=None, tier1=None):
    with db.session(owner) as conn:
        return conn.execute(
            """insert into accounts (platform, handle, language, audience_tier1_pct, audience_source, owner_operator_id)
               values (%s, %s, %s, %s, %s, %s) returning *""",
            (platform, handle, language, tier1, "manual" if tier1 is not None else None, owner.operator_id)).fetchone()


def _refresh(db, campaigns=None):
    if campaigns is not None:
        feed.FAKE_DISCOVERY["campaigns"] = campaigns
    with db.session(Actor.worker()) as conn:
        discovery.refresh(conn, [])


def _ranked(db, owner, **params):
    api._owner = owner
    query = "&".join(f"{k}={v}" for k, v in dict(show_out_of_reach="true", **params).items())
    return TestClient(api.app).get(f"/api/listings?{query}").json()["listings"]


# ------------------------------------------------------------------ audience countries

def test_screenshot_text_becomes_country_shares_in_english_and_arabic():
    english = "Top countries\nUnited States 41.5%\nUnited Kingdom 8%\nSaudi Arabia 22%\nEgypt · 12.5%\nOther 16%"
    assert audience.parse_countries(english) == {"US": 41.5, "GB": 8.0, "SA": 22.0, "EG": 12.5}
    arabic = "الدول\nالسعودية ٣٥٪\nمصر ٢٠٪\nالولايات المتحدة ٥٪"
    assert audience.parse_countries(arabic) == {"SA": 35.0, "EG": 20.0, "US": 5.0}
    assert audience.tier1_pct({"US": 41.5, "GB": 8, "SA": 22}) == 49.5


def test_audience_from_a_screenshot_through_the_api(db, owner):
    account = _account(db, owner)
    fake_llm.FAKE_IMAGE_TEXT = "Viewers by country\nUnited States 30%\nCanada 5%\nJordan 40%\nIraq 25%"
    try:
        api._owner = owner
        client = TestClient(api.app)
        row = client.post(f"/api/accounts/{account['id']}/audience/screenshot", files={"image": ("a.png", b"\x89PNG", "image/png")}).json()
        assert float(row["audience_tier1_pct"]) == 35.0 and row["audience_source"] == "screenshot"
        assert row["audience_countries"] == {"US": 30.0, "CA": 5.0, "JO": 40.0, "IQ": 25.0}
        fake_llm.FAKE_IMAGE_TEXT = "Followers 1,204\nLikes 9,800"
        bad = client.post(f"/api/accounts/{account['id']}/audience/screenshot", files={"image": ("a.png", b"\x89PNG", "image/png")})
        assert bad.status_code == 400 and bad.json()["code"] == "no_countries"
    finally:
        fake_llm.FAKE_IMAGE_TEXT = None


def test_audience_from_the_platform_api_and_the_weekly_refresh(db, owner):
    account = _account(db, owner, platform="instagram_reels", handle="me_ig")
    _account(db, owner, platform="youtube_shorts", handle="typed", tier1=12)   # typed in by hand: never overwritten
    with db.session(Actor.worker()) as conn:
        assert audience.refresh_all(conn) == 1
        row = conn.execute("select * from accounts where id = %s", (account["id"],)).fetchone()
        assert float(row["audience_tier1_pct"]) == 50.0 and row["audience_source"] == "api"
        assert audience.refresh_all(conn) == 0   # not again until the interval passes
        typed = conn.execute("select audience_tier1_pct, audience_source from accounts where handle = 'typed'").fetchone()
        assert float(typed["audience_tier1_pct"]) == 12 and typed["audience_source"] == "manual"
    assert audience_feed.FAKE_AUDIENCE["calls"] == ["instagram_reels"]


def test_the_live_audience_connector_needs_its_token_and_never_guesses(monkeypatch):
    monkeypatch.delenv("YOUTUBE_ANALYTICS_TOKEN", raising=False)
    adapter = audience_feed.PlatformAudience({"config_json": {}})
    with pytest.raises(AppError) as err:
        adapter.countries("youtube_shorts")
    assert err.value.code == "no_token"
    with pytest.raises(AppError) as err:
        adapter.countries("tiktok")
    assert err.value.code == "no_api"


# ------------------------------------------------------------------ Arabic and the brief's language

def test_arabic_accounts_assume_few_tier1_viewers_and_prefer_campaigns_without_a_country_rule(db, owner):
    _account(db, owner, language="ar")
    _refresh(db, [dict(SAMPLE[0], campaignId=1, title="Anywhere", platforms=["tiktok"]),
                  dict(SAMPLE[0], campaignId=2, title="US viewers", platforms=["tiktok"], audienceLabel="US audience")])
    with db.session(Actor.worker()) as conn:
        conn.execute("update listings set audience = null, audience_countries = '{}', region = null where external_id = '1'")
    rows = {r["title"]: r for r in _ranked(db, owner)}
    assert rows["US viewers"]["rank"]["geo_share"] == 0.05
    assert "no audience country rule: fits Arabic clips" in rows["Anywhere"]["rank"]["reasons"]
    assert rows["Anywhere"]["rank"]["score"] > rows["US viewers"]["rank"]["score"]


def test_a_brief_that_wants_another_language_does_not_count_your_account(db, owner):
    _account(db, owner, language="ar")
    _refresh(db, [dict(SAMPLE[0], campaignId=1, title="English only", platforms=["tiktok"])])
    with db.session(Actor.worker()) as conn:
        conn.execute("update listings set languages = '{English}'")
    row = _ranked(db, owner)[0]
    assert row["rank"]["language_mismatch"] is True and "none of your accounts posts in that language" in row["rank"]["reasons"][0]
    with db.session(owner) as conn:
        conn.execute("update accounts set language = 'en'")
    assert not _ranked(db, owner)[0]["rank"].get("language_mismatch")


# ------------------------------------------------------------------ fixed pay first while accounts are new

def test_fixed_pay_campaigns_come_first_while_accounts_are_new(db, owner):
    _account(db, owner)
    _refresh(db, [dict(SAMPLE[0], campaignId=1, title="Per view", platforms=["tiktok"]),
                  dict(SAMPLE[0], campaignId=2, title="Milestones", platforms=["tiktok"], payModel="milestone")])
    rows = _ranked(db, owner)
    assert rows[0]["title"] == "Milestones" and rows[0]["rank"]["fixed_first"]
    assert "pays without a viral clip" in rows[0]["rank"]["reasons"][0]
    with db.session(Actor.system()) as conn:   # once the account has a track record, per-view ranking takes over
        conn.execute("update settings set value = '0' where key = 'new_account_posts'")
    assert _ranked(db, owner)[0]["title"] == "Per view"


# ------------------------------------------------------------------ payout record

def test_own_payout_record_then_the_feeds_approval_figure_set_the_approval_odds():
    listing = {"marketplace_id": "m1", "raw": {"analytics": {"alpha": {"verdicts": [{"key": "approval", "value": 0.6}]}}}}
    assert ranking.approval_odds(listing, {"m1": 0.4}) == (0.4, "your payout record here: 40% of settled clips paid")
    assert ranking.approval_odds(listing, {})[0] == 0.6
    assert ranking.approval_odds({"marketplace_id": "m2"}, {}) == (ranking.CLIP_APPROVAL, "")


def test_payout_record_counts_only_settled_clips_and_needs_enough_of_them(produced, monkeypatch):
    db, owner = produced.db, produced.owner
    with db.session(owner) as conn:
        clips = conn.execute("select id, campaign_version_id from clips order by created_at").fetchall()
        assert len(clips) >= 3
        statuses = (["paid", "rejected", "tracking"] + ["paid"] * len(clips))[: len(clips)]
        path = {"paid": ["tracking", "approved", "held", "paid"], "rejected": ["rejected"], "tracking": ["tracking"]}
        for clip, status in zip(clips, statuses):
            payout = conn.execute("insert into payouts (clip_id, campaign_version_id) values (%s, %s) returning id",
                                  (clip["id"], clip["campaign_version_id"])).fetchone()["id"]
            for step in path[status]:   # the database only allows payouts to move forward in order
                conn.execute("update payouts set status = %s where id = %s", (step, payout))
        settled = [s for s in statuses if s != "tracking"]
        monkeypatch.setattr(ranking, "PAYOUT_RECORD_MIN", len(settled) + 1)
        assert ranking.payout_record(conn, owner.operator_id) == {}   # too few settled clips: the default stays
        monkeypatch.setattr(ranking, "PAYOUT_RECORD_MIN", len(settled))
        record = ranking.payout_record(conn, owner.operator_id)
    assert list(record.values()) == [pytest.approx(settled.count("paid") / len(settled))]


# ------------------------------------------------------------------ spreading risk and fresh budgets

def test_one_campaign_takes_at_most_the_set_share_of_the_weeks_clips(db, owner):
    with db.session(owner) as conn:
        assert pipeline._portfolio_room(conn, owner.operator_id, "00000000-0000-0000-0000-000000000000", 0, 5, 5) == (5, None)
    first = approved_campaign(db, owner, "First")
    approved_campaign(db, owner, "Second", "01")
    with db.session(owner) as conn:
        room, note = pipeline._portfolio_room(conn, owner.operator_id, first, 0, 5, 5)
        assert room == 1 and "spreading risk" in note   # nothing made this week yet: one clip to start
    with db.session(Actor.system()) as conn:
        conn.execute("update settings set value = '100' where key = 'portfolio_max_share_pct'")
    with db.session(owner) as conn:
        assert pipeline._portfolio_room(conn, owner.operator_id, first, 0, 5, 5) == (5, None)


def test_jobs_for_the_budget_running_out_soonest_go_first(db, owner):
    _refresh(db, [dict(SAMPLE[0], campaignId=1, title="Slow", drainP50="2099-01-01T00:00:00Z"),
                  dict(SAMPLE[0], campaignId=2, title="Fast", drainP50="2030-01-01T00:00:00Z")])
    late, soon = approved_campaign(db, owner, "Slow campaign"), approved_campaign(db, owner, "Fast campaign", "01")
    with db.session(Actor.system()) as conn:
        conn.execute("update listings set drain_p50 = case external_id when '1' then now() + interval '60 days' else now() + interval '2 days' end")
        for campaign_id, ext in ((late, "1"), (soon, "2")):
            conn.execute("update campaigns set listing_id = (select id from listings where external_id = %s) where id = %s", (ext, campaign_id))
        versions = {cid: conn.execute("select current_version_id from campaigns where id = %s", (cid,)).fetchone()["current_version_id"]
                    for cid in (late, soon)}
        conn.execute("delete from jobs")
    with db.session(Actor.worker()) as conn:
        enqueue(conn, "hooks", {"n": 1}, owner.operator_id, campaign_version_id=versions[late])
        enqueue(conn, "hooks", {"n": 2}, owner.operator_id, campaign_version_id=versions[soon])
        assert claim_job(conn)["campaign_version_id"] == versions[soon]


# ------------------------------------------------------------------ short transactions

def test_a_discovery_pass_commits_as_it_goes(db, owner):
    """Each marketplace query is committed, so a failure later keeps the campaigns already stored."""
    with pytest.raises(RuntimeError):
        with db.session(Actor.worker()) as conn:
            discovery.refresh(conn, [])
            raise RuntimeError("the pass failed later")
    with db.session(owner) as conn:
        assert conn.execute("select count(*) as n from listings").fetchone()["n"] > 0


def test_an_unread_brief_assumes_the_typical_minimum_instead_of_none():
    stats = {"median": 2500, "sigma": 1.2, "tier1": 1.0}
    unread = {"id": "u", "rate_per_1k": 2, "pay_model": "cpm"}
    optimistic = ranking.simulate(unread, stats, 30)
    careful = ranking.simulate(unread, stats, 30, typical_min_payout=6)
    assert careful["score"] < optimistic["score"] and careful["p_clear"] is None
    assert "assumed the typical $6 minimum payout" in careful["reasons"][-1]
    read = dict(unread, min_payout=1)
    assert ranking.simulate(read, stats, 30, typical_min_payout=6)["p_clear"] is not None   # a read brief keeps its own minimum
