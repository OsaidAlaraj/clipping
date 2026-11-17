"""Build step 2: intake, Agent A with the fake LLM, hard blocks, the approval gate, rule-change detection."""
from __future__ import annotations

import psycopg
import pytest

from clipping import campaigns
from clipping.adapters import fake_llm, vendors
from clipping.agents import rules as agent_a
from clipping.core import AppError, GateError, sha256_text
from clipping.db import Actor
from conftest import approved_campaign, brief

CTX = {"campaign_ref": "t", "marketplace": "content_rewards", "max_accounts": 1}


def take(db, owner, name: str, **kw):
    with db.session(owner) as conn:
        return campaigns.intake(conn, owner, kw.pop("marketplace", "content_rewards"), name, brief(name), **kw)


# ------------------------------------------------------------------ five sample briefs (plus one)

def test_clear_brief_extracts_rules_with_verbatim_quotes(db, owner):
    result = take(db, owner, "01")
    version, rules = result["version"], result["version"]["rules_json"]
    text = agent_a.norm(brief("01"))
    assert result["campaign"]["status"] == "pending_approval" and version["compliance_status"] == "pass"
    assert rules["payout"]["model"] == "cpm" and rules["payout"]["rate_per_1k"] == 1.5
    assert rules["payout"]["min_views_to_qualify"] == 5000 and rules["payout"]["max_payout_per_clip"] == 150
    assert rules["payout"]["budget_total"] == 2000
    assert rules["platforms_allowed"] == ["tiktok", "instagram_reels", "youtube_shorts"]
    assert rules["languages_allowed"] == ["en", "ar"] and rules["music_policy"] == "campaign_audio_only"
    assert rules["disclosure"]["required_by_brief"] and not rules["disclosure"]["forbidden_by_brief"]
    assert rules["snapshot_hash"] == sha256_text(brief("01")) == version["snapshot_hash"]
    for quote in rules["payout"]["quotes"].values():
        assert quote in text
    assert rules["content_rules"] and all(r["quote"] in text for r in rules["content_rules"])
    assert version["fee_pct"] == 10   # Content Rewards CPM fee, from the marketplace record
    assert str(result["campaign"]["deadline"]).startswith("2027-01-31")
    with db.session(owner) as conn:
        stored = conn.execute("select * from campaign_rules where version_id = %s", (version["id"],)).fetchall()
    assert len(stored) == len(rules["content_rules"]) and all(r["source_quote"] in text for r in stored)


@pytest.mark.parametrize("name,code", [("02", "gambling"), ("03", "finance_unlicensed"), ("04", "disclosure_forbidden"),
                                       ("06", "too_many_accounts")])
def test_blocked_categories_fail_and_can_never_be_approved(db, owner, name, code):
    result = take(db, owner, name)
    assert result["campaign"]["status"] == "rejected" and result["version"]["compliance_status"] == "blocked"
    assert code in [b["code"] for b in result["version"]["hard_blocks"]]
    with pytest.raises(AppError):
        with db.session(owner) as conn:
            campaigns.approve(conn, owner, result["campaign"]["id"])
    with pytest.raises(psycopg.errors.RaiseException):   # the database refuses too, whatever the code path
        with db.session(owner) as conn:
            conn.execute("update campaigns set status = 'approved' where id = %s", (result["campaign"]["id"],))


def test_vague_brief_is_review_never_pass(db, owner):
    result = take(db, owner, "05")
    assert result["version"]["compliance_status"] == "review"
    assert {"payout.model", "payout.rate_per_1k", "platforms_allowed", "music_policy"} <= set(result["version"]["unclear_fields"])
    assert result["version"]["rate_per_1k"] is None and result["version"]["fee_pct"] is None


def test_finance_brief_passes_the_block_only_with_a_licence_reference(db, owner):
    licensed = take(db, owner, "03", licence_ref="FCA-123456 approved copy v2")
    assert "finance_unlicensed" not in [b["code"] for b in licensed["version"]["hard_blocks"]]


def test_fee_is_unknown_when_the_marketplace_does_not_publish_it(db, owner):
    result = take(db, owner, "01", marketplace="vyro")
    assert result["version"]["fee_pct"] is None


def test_snapshot_keeps_raw_text_hash_and_read_time(db, owner):
    version = take(db, owner, "01")["version"]
    assert version["raw_text"] == brief("01") and version["snapshot_hash"] == sha256_text(brief("01")) and version["read_at"]


# ------------------------------------------------------------------ the agent's output is not trusted

def test_a_rule_without_a_verbatim_quote_becomes_unclear():
    raw = fake_llm.extract_rules({"brief_text": brief("01")})
    raw["payout"]["rate_per_1k"] = 9.99
    raw["payout"]["quotes"]["rate_per_1k"] = "We pay $9.99 per 1,000 views."           # invented quote
    raw["content_rules"].append({"text": "Clips must be under 30 seconds", "category": "duration",
                                 "requirement": "required", "quote": "Clips must be under 30 seconds"})   # inferred rule
    raw["content_rules"].append({"text": "No quote at all", "category": "other", "requirement": "required"})
    result = agent_a.validate(raw, brief("01"), CTX)
    assert result["rules_json"]["payout"]["rate_per_1k"] is None
    assert "payout.rate_per_1k" in result["unclear_fields"] and result["compliance_status"] == "review"
    invented = [r for r in result["rules_json"]["content_rules"] if "30 seconds" in r["text"] or "No quote" in r["text"]]
    assert len(invented) == 2 and all(r["requirement"] == "unclear" and r["quote"] is None for r in invented)


def test_unclear_never_passes_for_any_required_field():
    for field, mutate in {
        "payout.model": lambda r: r["payout"].update(model=None),
        "platforms_allowed": lambda r: r.update(platforms_allowed=[]),
        "music_policy": lambda r: r.update(music_policy="unclear"),
        "payout.rate_per_1k": lambda r: r["payout"]["quotes"].pop("rate_per_1k"),
    }.items():
        raw = fake_llm.extract_rules({"brief_text": brief("01")})
        mutate(raw)
        result = agent_a.validate(raw, brief("01"), CTX)
        assert field in result["unclear_fields"] and result["compliance_status"] == "review", field


def test_the_llm_cannot_remove_a_hard_block_or_approve():
    for garbage in ({}, {"hard_blocks": [], "risk_flags": [], "approved": True, "compliance_status": "pass"}, None, "text"):
        result = agent_a.validate(garbage, brief("02"), CTX)
        assert result["compliance_status"] == "blocked" and result["hard_blocks"][0]["code"] == "gambling"
    assert "approved" not in agent_a.validate({"approved": True}, brief("01"), CTX)["rules_json"]


def test_agent_a_falls_back_to_a_human_draft_when_the_llm_is_unavailable(db, owner):
    with db.session(Actor.system()) as conn:
        conn.execute("update adapters set paused_reason = 'test' where stage = 'llm' and active")
    result = take(db, owner, "01")
    assert result["version"]["compliance_status"] == "review" and result["campaign"]["status"] == "pending_approval"
    blocked = take(db, owner, "02")
    assert blocked["campaign"]["status"] == "rejected"   # deterministic blocks still apply


def test_arabic_gambling_terms_are_blocked():
    result = agent_a.validate({}, "حملة لموقع كازينو على الإنترنت. ندفع لكل ألف مشاهدة.", CTX)
    assert result["hard_blocks"][0]["code"] == "gambling"


# ------------------------------------------------------------------ approval gate

def test_approval_is_a_recorded_human_action(db, owner):
    result = take(db, owner, "01")
    campaign_id = result["campaign"]["id"]
    with pytest.raises(GateError):
        with db.session(Actor.worker()) as conn:
            campaigns.approve(conn, Actor.worker(), campaign_id)
    for actor in (Actor.worker(), Actor.system()):   # even direct SQL cannot pass the gate without a human
        with pytest.raises(psycopg.errors.RaiseException):
            with db.session(actor) as conn:
                conn.execute("update campaigns set status = 'approved' where id = %s", (campaign_id,))
    with db.session(owner) as conn:
        approved = campaigns.approve(conn, owner, campaign_id)
        assert approved["status"] == "approved" and approved["approved_version_id"] == result["version"]["id"]
        version = conn.execute("select rules_confirmed_by from campaign_versions where id = %s", (result["version"]["id"],)).fetchone()
        assert str(version["rules_confirmed_by"]) == owner.operator_id
        gate = conn.execute("select * from audit_log where action = 'gate.campaign_approval' and entity_id = %s", (campaign_id,)).fetchone()
        assert str(gate["actor_id"]) == owner.operator_id and gate["rule_version_id"] == result["version"]["id"]


def test_unconfirmed_rules_cannot_be_approved_by_direct_sql(db, owner):
    campaign_id = take(db, owner, "01")["campaign"]["id"]
    with pytest.raises(psycopg.errors.RaiseException):
        with db.session(owner) as conn:
            conn.execute("update campaigns set status = 'approved' where id = %s", (campaign_id,))


def test_only_approved_campaigns_take_sources_and_sources_need_permission(db, owner, footage):
    campaign_id = take(db, owner, "01")["campaign"]["id"]
    with pytest.raises(GateError):
        with db.session(owner) as conn:
            campaigns.register_source(conn, owner, campaign_id, footage, "en", permission_ref="P-1")
    with db.session(owner) as conn:
        campaigns.approve(conn, owner, campaign_id)
    for kwargs, code in (({}, "no_permission"), ({"permission_ref": "P-1", "expected_hash": "0" * 64}, "hash_mismatch")):
        with pytest.raises(AppError) as err:
            with db.session(owner) as conn:
                campaigns.register_source(conn, owner, campaign_id, footage, "en", **kwargs)
        assert err.value.code == code
    with db.session(owner) as conn:
        source = campaigns.register_source(conn, owner, campaign_id, footage, "ar", licence_ref="L-1")
        assert source["dialect"] == "ar"   # Modern Standard Arabic until the bake-off (O2)
        assert conn.execute("select type, status from jobs").fetchall() == [{"type": "transcribe", "status": "queued"}]


def test_park_and_reject_with_reason(db, owner):
    campaign_id = take(db, owner, "01")["campaign"]["id"]
    with db.session(owner) as conn:
        assert campaigns.park(conn, owner, campaign_id)["status"] == "paused"
        assert campaigns.approve(conn, owner, campaign_id)["status"] == "approved"
        with pytest.raises(AppError):
            campaigns.reject(conn, owner, campaign_id, " ")
        assert campaigns.reject(conn, owner, campaign_id, "Rate too low")["status_reason"] == "Rate too low"


# ------------------------------------------------------------------ rule-change detection

def test_changed_brief_writes_a_new_version_and_blocks_until_reapproved(db, owner):
    campaign_id = approved_campaign(db, owner)
    changed = brief("01").replace("$1.50 per 1,000 views", "$0.80 per 1,000 views")
    with db.session(owner) as conn:
        assert campaigns.recheck_brief(conn, owner, campaign_id, current_text=brief("01"))["changed"] is False
        assert campaigns.brief_recheck_is_fresh(conn, campaigns.get_campaign(conn, campaign_id))
        assert campaigns.recheck_brief(conn, owner, campaign_id, current_text=changed)["changed"] is True
        campaign = campaigns.get_campaign(conn, campaign_id)
        versions = conn.execute("select version_no, rate_per_1k, raw_text from campaign_versions where campaign_id = %s order by version_no",
                                (campaign_id,)).fetchall()
        assert campaign["status"] == "pending_approval" and not campaigns.brief_recheck_is_fresh(conn, campaign)
        assert [v["version_no"] for v in versions] == [1, 2] and versions[0]["raw_text"] == brief("01")   # never overwritten
        assert float(versions[0]["rate_per_1k"]) == 1.5 and float(versions[1]["rate_per_1k"]) == 0.8
        alert = conn.execute("select severity from alerts where type = 'rule_change'").fetchone()
        assert alert["severity"] == "block"
    assert len([a for a in vendors.FAKE_ALERTS["sent"] if a["type"] == "rule_change"]) == 2


def test_pasted_brief_recheck_needs_text_or_an_owner_confirmation(db, owner):
    campaign_id = approved_campaign(db, owner)
    with db.session(owner) as conn:
        with pytest.raises(AppError):
            campaigns.recheck_brief(conn, owner, campaign_id)
        assert campaigns.recheck_brief(conn, owner, campaign_id, unchanged_confirmed=True)["changed"] is False


def test_scraped_campaign_is_rechecked_through_its_source_adapter(db, owner):
    with db.session(owner) as conn:
        conn.execute("""update marketplaces set scraper_enabled = true, terms_version = 'v1', terms_read_at = now(),
                        terms_confirmed_by = %s where slug = 'content_rewards'""", (owner.operator_id,))
        from clipping.adapters.base import activate

        activate(conn, "source", "fake")
        vendors.FAKE_SOURCE["briefs"]["cr-1"] = brief("01")
        result = campaigns.intake(conn, owner, "content_rewards", "Scraped", brief("01"), external_ref="cr-1")
        campaigns.approve(conn, owner, result["campaign"]["id"])
        assert campaigns.recheck_brief(conn, owner, result["campaign"]["id"])["changed"] is False
        vendors.FAKE_SOURCE["briefs"]["cr-1"] = brief("01") + "\nYou must not show competitor logos."
        assert campaigns.recheck_brief(conn, owner, result["campaign"]["id"])["changed"] is True
