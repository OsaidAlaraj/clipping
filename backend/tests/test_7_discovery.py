"""Phase C: discovery feed, listings, risk-adjusted ranking, brief grade, adding a listing, change detection."""
from __future__ import annotations

import copy
import datetime as dt
import json

import pytest

from clipping import campaigns, config, discovery, ops, ranking
from clipping.adapters import discovery as feed
from clipping.core import AppError, get_setting
from clipping.db import Actor
from conftest import add_accounts

SAMPLE = json.loads((config.FIXTURES / "clippingalpha" / "search_campaigns.json").read_text(encoding="utf-8"))["campaigns"]
NOW = dt.datetime.now(dt.timezone.utc)


def refresh(db, owner, niches=None):
    with db.session(Actor.worker()) as conn:
        return discovery.refresh(conn, niches)


def test_refresh_stores_live_campaigns_from_every_marketplace_query_without_duplicates(db, owner):
    result = refresh(db, owner, ["podcast", "not-a-niche"])
    with db.session(owner) as conn:
        rows = conn.execute("select * from listings").fetchall()
        snaps = conn.execute("select count(*) as n from listing_snapshots").fetchone()["n"]
        market = conn.execute("select data from market_snapshots").fetchone()
        assert conn.execute("select count(*) as n from audit_log where action = 'discovery.refresh'").fetchone()["n"] == 1
    assert result["seen"] == len(rows) == len({c["campaignId"] for c in SAMPLE})
    queries = [args for kind, args in feed.FAKE_DISCOVERY["calls"] if kind == "search"]
    assert len(queries) == 6 * 2 + 1   # every marketplace by score and by newest, plus the one known niche
    assert all(r["state"] == "live" and r["title"] and r["ref"] for r in rows) and snaps >= len(rows)
    first = next(r for r in rows if r["external_id"] == str(SAMPLE[0]["campaignId"]))
    assert float(first["rate_per_1k"]) == SAMPLE[0]["ratePer1kUsd"] and first["marketplace_id"] is not None
    assert market["data"]["cpm"]["medianUsd"] is not None


def test_campaigns_not_seen_for_two_days_end(db, owner):
    refresh(db, owner)
    with db.session(Actor.system()) as conn:
        conn.execute("update listings set last_seen_at = now() - interval '3 days' where external_id = %s", (str(SAMPLE[0]["campaignId"]),))
    feed.FAKE_DISCOVERY["campaigns"] = SAMPLE[1:]
    refresh(db, owner)
    with db.session(owner) as conn:
        states = {r["external_id"]: r["state"] for r in conn.execute("select external_id, state from listings").fetchall()}
    assert states[str(SAMPLE[0]["campaignId"])] == "ended" and set(states.values()) == {"live", "ended"}


def test_feed_failure_falls_back_to_manual_with_an_alert(db, owner):
    feed.FAKE_DISCOVERY["fail"] = True
    with db.session(Actor.worker()) as conn:
        result = discovery.refresh(conn)
        assert result["seen"] == 0   # the declared fallback (manual) returns nothing rather than breaking
        assert conn.execute("select count(*) as n from alerts where type = 'adapter_failed'").fetchone()["n"] == 1


def test_enrich_reads_fees_minimums_window_grade_and_runway(db, owner):
    refresh(db, owner)
    with db.session(Actor.worker()) as conn:
        listing = conn.execute("select id from listings order by alpha_score desc limit 1").fetchone()
        row = discovery.enrich(conn, listing["id"])
    assert row["brief_grade"] in tuple("ABCDF") and row["checklist"]
    assert row["payout_window_days"] is not None and row["board_fee_pct"] is not None
    assert row["drain_p50"] is not None and row["spent_per_day"] is not None


# ------------------------------------------------------------------ ranking

def listing(**kw) -> dict:
    base = {"id": "x", "pay_model": "cpm", "rate_per_1k": 2, "net_per_1k": 1.8, "min_views": None, "min_payout": None,
            "max_payout": None, "open_join": True, "drain_p50": NOW + dt.timedelta(days=30), "payout_window_days": 10,
            "audience_countries": [], "audience": None, "region": None, "stalled": False, "new_at": NOW - dt.timedelta(days=10),
            "platforms": ["tiktok"], "board_fee_pct": 0.1}
    return {**base, **kw}


STATS = {"median": 5000, "sigma": 1.0, "basis": "your posts", "tier1": 0.3, "tier1_basis": "manual"}


def score(**kw):
    return ranking.simulate(listing(**kw), STATS, 30, NOW)


def test_tier1_rule_cuts_the_value_for_a_mostly_non_tier1_account():
    open_world, tier1_only = score(), score(audience_countries=["US", "GB"])
    assert tier1_only["mean_per_clip"] < open_world["mean_per_clip"] * 0.4
    assert any("Tier-1" in r for r in tier1_only["reasons"]) and tier1_only["geo_share"] == 0.3


def test_minimum_views_beyond_the_account_reach_scores_zero_on_the_cautious_outcome():
    reachable, unreachable = score(min_views=1000), score(min_views=100_000)
    assert reachable["p25_per_clip"] > 0 and unreachable["p25_per_clip"] == 0
    assert unreachable["p_clear"] < 0.1 and any("reach the minimum" in r for r in unreachable["reasons"])


def test_budget_running_out_before_payout_lowers_the_score():
    healthy, draining = score(), score(drain_p50=NOW + dt.timedelta(days=1))
    assert draining["p_budget"] == pytest.approx(0.1) and draining["score"] < healthy["score"]
    assert any("runs out" in r for r in draining["reasons"])
    assert score(stalled=True)["p_budget"] < healthy["p_budget"]


def test_cap_application_and_newness():
    assert score(max_payout=5)["mean_per_clip"] <= 5
    assert score(open_join=False)["p_approve"] < score()["p_approve"]
    fresh = score(new_at=NOW - dt.timedelta(hours=10))
    assert fresh["score"] > score()["score"] and "new: budget is fresh" in fresh["reasons"]


def test_non_cpm_pay_is_marked_not_modelled_and_results_are_stable():
    assert score(pay_model="pool")["modelled"] is False
    assert score() == score()   # seeded: the same inputs give the same ranking every load


def test_rank_uses_the_owners_own_account_history_and_audience(db, owner):
    add_accounts(db, owner)
    refresh(db, owner)
    with db.session(owner) as conn:
        rows = conn.execute("select * from listings").fetchall()
        before = ranking.rank(conn, rows, owner.operator_id)
        tt = conn.execute("select id from accounts where platform = 'tiktok'").fetchone()["id"]
        conn.execute("update accounts set audience_tier1_pct = 90, audience_source = 'manual' where id = %s", (tt,))
        stats = ranking.account_stats(conn, conn.execute("select * from accounts where id = %s", (tt,)).fetchone())
        assert stats["tier1"] == 0.9 and stats["basis"] == "assumed" and stats["median"] == float(get_setting(conn, "assumed_median_views"))
    assert all(r["rank"]["account"] is not None for r in before if {"tiktok", "instagram"} & set(r["platforms"]))
    scores = [r["rank"]["score"] for r in before if r["rank"].get("score") is not None]
    assert scores == sorted(scores, reverse=True)


# ------------------------------------------------------------------ adding a listing

def test_adding_a_listing_runs_intake_with_quoted_rules_and_keeps_the_approval_gate(db, owner):
    refresh(db, owner)
    with db.session(owner) as conn:
        target = conn.execute("select id from listings where external_id = %s", (str(SAMPLE[0]["campaignId"]),)).fetchone()["id"]
        result = discovery.add_listing(conn, owner, target, "gaming")
        campaign = result["campaign"]
        version = result["version"]
        assert campaign["status"] == "pending_approval"
        assert conn.execute("select listing_id from campaigns where id = %s", (campaign["id"],)).fetchone()["listing_id"] == target
        text = version["raw_text"]
        assert "source of truth" in text and "per 1,000 views" in text
        assert version["rules_json"]["payout"]["rate_per_1k"] == SAMPLE[0]["ratePer1kUsd"]
        assert all(rule["quote"] in " ".join(text.split()) for rule in version["rules_json"]["content_rules"] if rule["quote"])
        assert result["brief_grade"] in tuple("ABCDF")
        assert discovery.own_campaign_id(conn, owner.operator_id, target) == campaign["id"]
        with pytest.raises(AppError):
            discovery.add_listing(conn, owner, target)   # once only
        assert campaigns.get_campaign(conn, campaign["id"])["status"] != "approved"


def test_a_changed_feed_brief_blocks_posting_until_reapproved(db, owner):
    refresh(db, owner)
    with db.session(owner) as conn:
        target = conn.execute("select id, ref from listings where external_id = %s", (str(SAMPLE[0]["campaignId"]),)).fetchone()
        campaign_id = discovery.add_listing(conn, owner, target["id"])["campaign"]["id"]
        campaigns.approve(conn, owner, campaign_id)
        assert campaigns.recheck_brief(conn, owner, campaign_id)["changed"] is False
    changed = copy.deepcopy(json.loads((config.FIXTURES / "clippingalpha" / "get_brief.json").read_text(encoding="utf-8")))
    changed["join"]["maxPayoutUsd"] = 300
    feed.FAKE_DISCOVERY["briefs"] = {target["ref"]: changed}
    with db.session(owner) as conn:
        assert campaigns.recheck_brief(conn, owner, campaign_id)["changed"] is True
        assert campaigns.get_campaign(conn, campaign_id)["status"] == "pending_approval"


def test_grade_from_agent_a_output():
    assert discovery.grade_from_version({"compliance_status": "blocked"}) == "F"
    assert discovery.grade_from_version({"unclear_fields": [], "rules_json": {"content_rules": [1, 2, 3]}}) == "A"
    assert discovery.grade_from_version({"unclear_fields": ["music_policy"], "rules_json": {}}) == "C"
    assert discovery.grade_from_version({"unclear_fields": ["a", "b", "c"], "rules_json": {}}) == "F"


def test_discovery_is_scheduled_once_per_interval_and_respects_the_kill_switch(db, owner):
    from clipping import core

    with db.session(Actor.worker()) as conn:
        assert ops.schedule_discovery(conn) is True
        assert ops.schedule_discovery(conn) is False   # same interval: one job
    with db.session(owner) as conn:
        core.set_kill_switch(conn, True)
    with db.session(Actor.worker()) as conn:
        conn.execute("delete from jobs")
    with db.session(Actor.worker()) as conn:
        assert ops.schedule_discovery(conn) is False


def test_api_lists_ranked_campaigns_with_filters_and_adds_one(db, owner):
    from fastapi.testclient import TestClient

    from clipping import api

    api._owner = owner
    add_accounts(db, owner)
    refresh(db, owner)
    client = TestClient(api.app)
    data = client.get("/api/listings").json()
    assert data["listings"] and all("rank" in r and "raw" not in r for r in data["listings"])
    assert data["market"]["data"]["cpm"]
    tiktok = client.get("/api/listings?platform=tiktok").json()["listings"]
    assert all("tiktok" in r["platforms"] for r in tiktok)
    assert client.get("/api/listings?min_rate=999").json()["listings"] == []
    one = data["listings"][0]
    detail = client.get(f"/api/listings/{one['id']}").json()
    assert detail["rules"] and detail["join"] and "raw" not in detail
    assert client.post(f"/api/listings/{one['id']}/dismiss").json() == {"ok": True}
    assert one["id"] not in [r["id"] for r in client.get("/api/listings").json()["listings"]]
    added = client.post(f"/api/listings/{one['id']}/add", json={"category": "gaming"}).json()
    assert added["campaign"]["status"] in ("pending_approval", "rejected")
    assert client.put("/api/me/niches", json={"niches": ["podcast", "gaming"]}).json()["niches"] == ["podcast", "gaming"]
    assert client.put("/api/me/niches", json={"niches": ["cooking"]}).status_code == 400
    account = client.get("/api/reference").json()["accounts"][0]
    assert client.put(f"/api/accounts/{account['id']}/audience", json={"tier1_pct": 120}).status_code == 400
    assert client.put(f"/api/accounts/{account['id']}/audience", json={"tier1_pct": 25}).json()["audience_tier1_pct"] == 25


def test_a_rule_that_forbids_a_bad_practice_is_not_a_hard_block():
    from clipping.agents import rules

    allowed = "We pay $2.00 per 1,000 views.\nNo spam posting the same video on multiple accounts.\nNo bought views, likes or bots."
    assert rules.deterministic_hard_blocks(allowed, {}, None, 1) == []
    asking = "We pay $2.00 per 1,000 views.\nPost the same video on multiple accounts to boost reach."
    assert rules.deterministic_hard_blocks(asking, {}, None, 1)[0]["code"] == "conflicts_with_platform_rules"


def test_a_tier_rule_written_only_in_the_brief_is_picked_up(db, owner):
    refresh(db, owner)
    with db.session(Actor.worker()) as conn:
        target = conn.execute("select id from listings where external_id = %s", (str(SAMPLE[0]["campaignId"]),)).fetchone()["id"]
        row = discovery.enrich(conn, target)
    assert row["audience"] and "tier" in row["audience"].lower()
    stats = {"median": 5000, "sigma": 1.0, "basis": "x", "tier1": 0.2, "tier1_basis": "manual"}
    assert ranking.geo_share(row, stats)[0] == 0.2


def test_screenshot_reading_needs_a_vision_model_and_returns_text_for_review(db, owner, monkeypatch):
    from fastapi.testclient import TestClient

    from clipping import api
    from clipping.adapters import fake_llm

    api._owner = owner
    client = TestClient(api.app)
    png = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000")
    monkeypatch.setattr(fake_llm, "FAKE_IMAGE_TEXT", None)
    refused = client.post("/api/capture/screenshot", files={"image": ("shot.png", png, "image/png")})
    assert refused.status_code == 400 and refused.json()["code"] == "needs_vision"
    monkeypatch.setattr(fake_llm, "FAKE_IMAGE_TEXT", "We pay $1.50 per 1,000 views.")
    read = client.post("/api/capture/screenshot", files={"image": ("shot.png", png, "image/png")})
    assert read.json() == {"text": "We pay $1.50 per 1,000 views."}
    not_image = client.post("/api/capture/screenshot", files={"image": ("a.txt", b"hello", "text/plain")})
    assert not_image.status_code == 400


def test_refresh_reads_the_top_briefs_and_unread_briefs_have_unknown_reach(db, owner):
    result = refresh(db, owner)
    with db.session(owner) as conn:
        rows = conn.execute("select * from listings").fetchall()
    read = [r for r in rows if (r["raw"] or {}).get("brief")]
    assert result["enriched"] == len(read) == min(discovery.ENRICH_PER_PASS, len(rows))
    unread = ranking.simulate(listing(payout_window_days=None), STATS, 30, NOW)
    assert unread["p_clear"] is None and any("not read yet" in r for r in unread["reasons"])


def test_rate_limit_stops_the_pass_without_retries_or_fallback(db, owner):
    feed.FAKE_DISCOVERY["fail"] = "rate"
    result = refresh(db, owner)
    assert result["rate_limited"] is True and result["seen"] == 0
    assert len([c for c in feed.FAKE_DISCOVERY["calls"] if c[0] == "search"]) == 1   # stopped at the first refusal
    with db.session(owner) as conn:
        assert conn.execute("select count(*) as n from alerts where type = 'adapter_failed'").fetchone()["n"] == 0


def test_check_now_waits_ten_minutes_between_passes(db, owner):
    from fastapi.testclient import TestClient

    from clipping import api

    api._owner = owner
    client = TestClient(api.app)
    assert client.post("/api/discovery/refresh").json() == {"queued": True}
    again = client.post("/api/discovery/refresh")
    assert again.status_code == 400 and again.json()["code"] == "too_soon"


def test_the_live_connector_spaces_calls_and_honours_429(monkeypatch):
    import httpx

    from clipping.adapters import discovery as d

    d._pace.update(last=0.0, until=0.0)
    sent = []

    class Resp:
        def __init__(self, code):
            self.status_code, self.headers, self.content, self.text = code, {"retry-after": "120"}, b"{}", "{}"

        def json(self):
            return {"result": {"structuredContent": {"campaigns": []}}}

    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            sent.append(k["json"]["method"])
            return Resp(429 if len(sent) == 3 else 200)

    monkeypatch.setattr(httpx, "Client", Client)
    client = d.McpClient("https://example.invalid/mcp", min_interval=0.01)
    assert client.call("search_campaigns", {}) == {"campaigns": []}
    with pytest.raises(d.FeedRateLimited):
        client.call("search_campaigns", {})
    with pytest.raises(d.FeedRateLimited):   # still waiting: no request is sent at all
        client.call("search_campaigns", {})
    assert len(sent) == 3
    d._pace.update(last=0.0, until=0.0)


def _second_owner(db) -> Actor:
    with db.session(Actor.system()) as conn:
        row = conn.execute("insert into operators (name, role) values ('Second owner', 'operator') returning id").fetchone()
    return Actor(str(row["id"]), "operator")


def test_a_second_pass_keeps_briefs_already_read_and_does_not_read_them_again(db, owner):
    refresh(db, owner)
    first_reads = len([c for c in feed.FAKE_DISCOVERY["calls"] if c[0] == "brief"])
    assert first_reads > 0
    with db.session(owner) as conn:
        before = conn.execute("select count(*) as n from listings where raw ? 'brief' and enriched_at is not null").fetchone()["n"]
    refresh(db, owner)
    with db.session(owner) as conn:
        after = conn.execute("select count(*) as n from listings where raw ? 'brief'").fetchone()["n"]
    assert after == before == first_reads
    assert len([c for c in feed.FAKE_DISCOVERY["calls"] if c[0] == "brief"]) == first_reads   # nothing read twice
    with db.session(Actor.worker()) as conn:   # after a few days a brief is read again
        conn.execute("update listings set enriched_at = now() - interval '4 days'")
    refresh(db, owner)
    assert len([c for c in feed.FAKE_DISCOVERY["calls"] if c[0] == "brief"]) == first_reads * 2


def test_not_for_me_and_added_are_each_owners_own(db, owner):
    from fastapi.testclient import TestClient

    from clipping import api

    refresh(db, owner)
    other = _second_owner(db)
    client = TestClient(api.app)
    api._owner = owner
    target = client.get("/api/listings").json()["listings"][0]["id"]
    client.post(f"/api/listings/{target}/dismiss")
    assert target not in [r["id"] for r in client.get("/api/listings").json()["listings"]]
    api._owner = other
    assert target in [r["id"] for r in client.get("/api/listings").json()["listings"]]   # the other owner still sees it
    added = client.post(f"/api/listings/{target}/add", json={"category": "gaming"}).json()
    mine = next(r for r in client.get("/api/listings?show_out_of_reach=true").json()["listings"] if r["id"] == target)
    assert mine["campaign_id"] == added["campaign"]["id"]
    api._owner = owner
    assert client.post(f"/api/listings/{target}/dismiss?undo=true").json() == {"ok": True}
    theirs = next(r for r in client.get("/api/listings?show_out_of_reach=true").json()["listings"] if r["id"] == target)
    assert theirs["campaign_id"] is None   # the first owner can still add it for themselves


def test_listing_page_opens_when_the_feed_is_rate_limited(db, owner):
    from fastapi.testclient import TestClient

    from clipping import api

    feed.FAKE_DISCOVERY["campaigns"] = [dict(SAMPLE[0])]
    refresh(db, owner)
    with db.session(Actor.worker()) as conn:
        target = conn.execute("update listings set raw = raw - 'brief', enriched_at = null returning id").fetchone()["id"]

    def limited(self, ref):
        raise feed.FeedRateLimited(300)

    original = feed.FakeDiscovery.brief
    feed.FakeDiscovery.brief = limited
    try:
        api._owner = owner
        page = TestClient(api.app).get(f"/api/listings/{target}")
    finally:
        feed.FakeDiscovery.brief = original
    assert page.status_code == 200 and "slow down" in page.json()["feed_note"]


def test_out_of_reach_campaigns_are_hidden_by_default(db, owner):
    from fastapi.testclient import TestClient

    from clipping import api

    feed.FAKE_DISCOVERY["campaigns"] = [dict(SAMPLE[0], campaignId=1), dict(SAMPLE[0], campaignId=2, title="Huge minimum")]
    refresh(db, owner)
    with db.session(Actor.worker()) as conn:
        conn.execute("update listings set min_views = 5000000, enriched_at = now() where external_id = '2'")
        conn.execute("update listings set min_views = 100, min_payout = null, audience = null where external_id = '1'")   # clearly in reach
    api._owner = owner
    client = TestClient(api.app)
    data = client.get("/api/listings").json()
    assert [r["title"] for r in data["listings"]] == [SAMPLE[0]["title"]] and data["hidden_out_of_reach"] == 1
    assert len(client.get("/api/listings?show_out_of_reach=true").json()["listings"]) == 2


def test_new_filters(db, owner):
    from fastapi.testclient import TestClient

    from clipping import api

    feed.FAKE_DISCOVERY["campaigns"] = [dict(SAMPLE[0], campaignId=1, title="Any country"),
                                        dict(SAMPLE[0], campaignId=2, title="US only", audienceLabel="US audience")]
    refresh(db, owner)
    with db.session(Actor.worker()) as conn:
        conn.execute("update listings set payout_window_days = 30, min_views = 1000, min_payout = null where external_id = '2'")
        conn.execute("update listings set payout_window_days = 7, min_views = null, region = null, audience = null, audience_countries = '{}' where external_id = '1'")
    api._owner = owner
    client = TestClient(api.app)
    titles = lambda q: sorted(r["title"] for r in client.get(f"/api/listings?show_out_of_reach=true&{q}").json()["listings"])  # noqa: E731
    assert titles("no_country_rule=true") == ["Any country"]
    assert titles("max_hold_days=14") == ["Any country"]
    assert titles("max_min_views=500") == ["Any country"]   # unknown minimum stays in
    assert titles("pay_model=cpm") == ["Any country", "US only"]
    assert titles("min_budget_left=999999999") == []


def test_geography_words_are_matched_as_words():
    stats = {"tier1": 0.3}
    assert ranking.geo_share({"audience": "Famous creators, campus life"}, stats)[0] == 0.5
    assert ranking.geo_share({"audience": "US audience"}, stats)[0] == 0.3
    assert ranking.geo_share({"audience": "Tier-1 only"}, stats)[0] == 0.3
    assert ranking.geo_share({}, stats)[0] == 1.0


def test_nearly_spent_budget_is_penalised():
    stats = {"median": 5000, "sigma": 1.0, "tier1": 1.0}
    base = {"id": "x", "rate_per_1k": 2, "pay_model": "cpm", "budget_total": 1000, "min_views": 1000}
    full = ranking.simulate(dict(base, budget_left=900), stats, 30)
    nearly = ranking.simulate(dict(base, budget_left=50), stats, 30)
    assert nearly["p_budget"] < full["p_budget"] and "over 90% of the budget is spent" in nearly["reasons"]


def test_the_connector_reuses_one_session_and_renews_it_when_forgotten(monkeypatch):
    import httpx

    from clipping.adapters import discovery as d

    d._pace.update(last=0.0, until=0.0)
    sent = []

    class Resp:
        def __init__(self, code):
            self.status_code, self.content, self.text = code, b"{}", "{}"
            self.headers = {"mcp-session-id": f"s{len(sent)}"}

        def json(self):
            return {"result": {"structuredContent": {"campaigns": []}}}

    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            sent.append(k["json"]["method"])
            return Resp(404 if len(sent) == 4 else 200)   # the 2nd tool call finds its session gone

    monkeypatch.setattr(httpx, "Client", Client)
    client = d.McpClient("https://example.invalid/mcp", min_interval=0.0)
    for _ in range(3):
        client.call("search_campaigns", {})
    assert sent == ["initialize", "tools/call", "tools/call", "tools/call", "initialize", "tools/call", "tools/call"][:len(sent)]
    assert sent.count("initialize") == 2 and sent.count("tools/call") == 4
    d._pace.update(last=0.0, until=0.0)
