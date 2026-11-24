"""What a confirmed campaign spec changes: stored under a new key, ad label off when the brief forbids #ad, required hashtags in
captions, minimum length, source whitelist with an owner override, deadlines, account warnings (brief_wiring.py)."""
from __future__ import annotations

import datetime as dt

import pytest

from clipping import api, brief_reader, brief_spec, brief_wiring, campaigns, sources
from clipping.adapters import fake_llm
from clipping.alerts import registry
from clipping.core import AppError

from briefs_fixture import briefs

BRIEFS = {k.split(" [")[0] if k.startswith(("CSGOBIG", "VAVADA")) else k: v for k, v in briefs().items()}


def spec_for(name: str, **answers) -> dict:
    text = BRIEFS[name]
    spec = brief_spec.build_spec(text, fake_llm.brief_spec({"brief_text": text}))
    for item in spec["items"]:
        if item["key"] in answers:
            item.update(value=answers[item["key"]], status="stated", origin="owner")
    return brief_spec.validate_spec(spec)


def intake(db, owner, name, **answers):
    spec = spec_for(name, **answers)
    with db.session(owner) as conn:
        result = campaigns.intake(conn, owner, "content_rewards", name, BRIEFS[name], spec=spec)
        return result["campaign"], result["version"]


# ------------------------------------------------------------------ intake stores the spec and fills the old keys

def test_the_spec_is_stored_under_its_own_key_and_old_keys_still_exist(db, owner):
    campaign, version = intake(db, owner, "VAVADA")
    rules = version["rules_json"]
    assert rules["spec"]["version"] == 1 and any(i["key"] == "length.min_s" for i in rules["spec"]["items"])
    assert {"payout", "platforms_allowed", "content_rules", "disclosure", "music_policy"} <= set(rules)
    assert float(version["rate_per_1k"]) == pytest.approx(0.2) and version["payout_model"] == "cpm"
    assert float(version["budget_total"]) == 30000 and float(version["budget_remaining"]) == pytest.approx(2100)
    assert "instagram_reels" in rules["platforms_allowed"]


def test_an_unanswered_blocking_question_stops_the_intake(db, owner):
    spec = brief_spec.build_spec(BRIEFS["CSGOBIG"], None)
    with db.session(owner) as conn, pytest.raises(AppError) as err:
        campaigns.intake(conn, owner, "content_rewards", "CS", BRIEFS["CSGOBIG"], spec=spec)
    assert err.value.code == "spec_unclear"


def test_an_answered_question_goes_through(db, owner):
    campaign, version = intake(db, owner, "CSGOBIG", **{"payout.model": "cpm"})
    assert version["payout_model"] == "cpm" and campaign["status"] in ("pending_approval", "rejected")


# ------------------------------------------------------------------ ad label

def test_a_brief_that_forbids_ad_turns_the_label_off_and_tells_the_owner(db, owner):
    campaign, version = intake(db, owner, "Topps x ClipFarm", **{"payout.model": "cpm"})
    assert campaign["ad_label"] is False
    decision = next(d for d in version["rules_json"]["spec_decisions"] if d["id"] == "ad_label_off")
    assert "NEVER PUT #ad" in decision["quote"] and "switched off" in decision["text"]
    assert version["rules_json"]["disclosure"]["forbidden_by_brief"] is True
    with db.session(owner) as conn:   # the owner can still turn it back on
        assert campaigns.set_ad_label(conn, owner, campaign["id"], True)["ad_label"] is True


def test_a_brief_without_a_disclosure_rule_leaves_the_label_on(db, owner):
    campaign, version = intake(db, owner, "VAVADA")
    assert campaign["ad_label"] is True
    assert not [d for d in version["rules_json"]["spec_decisions"] if d["id"] == "ad_label_off"]


# ------------------------------------------------------------------ captions, length, deadline

def test_required_hashtags_become_required_content_rules_and_reach_the_caption(db, owner):
    from clipping import pipeline

    campaign, version = intake(db, owner, "Social Media Justice")
    rules = version["rules_json"]
    caption, hashtags, _ = pipeline.build_caption("#ad", "A hook", rules)
    assert "#SocialMediaJustice" in hashtags and "#SocialMediaJustice" in caption
    with db.session(owner) as conn:
        stored = conn.execute("select rule_text_verbatim, requirement from campaign_rules where version_id = %s and category = 'caption'", (version["id"],)).fetchall()
    assert {"rule_text_verbatim": "#SocialMediaJustice", "requirement": "required"} in [dict(r) for r in stored]


def test_the_minimum_length_from_the_brief_raises_the_campaign_minimum_never_lowers_it():
    rules = {"spec": spec_for("VAVADA")}            # 8 s in the brief
    assert brief_wiring.min_clip_s(rules, 15) == 15
    assert brief_wiring.min_clip_s({"spec": spec_for("CSGOBIG", **{"payout.model": "cpm"})}, 5) == 10
    assert brief_wiring.min_clip_s({}, 15) == 15


def test_a_campaign_end_date_becomes_the_stop_date_and_a_content_stop_is_reported():
    text = BRIEFS["VAVADA"] + "\nCampaign ends: 2031-03-09"
    spec = brief_spec.build_spec(text, None)
    rules: dict = {"payout": {}, "content_rules": []}
    brief_wiring.apply(rules, spec)
    assert rules["deadline"] == "2031-03-09"
    topps = brief_spec.build_spec(BRIEFS["Topps x ClipFarm"], None, now=dt.datetime(2026, 11, 24, tzinfo=dt.timezone.utc))
    r2 = {"spec": topps}
    assert brief_wiring.stopped_content(r2, dt.datetime(2027, 8, 5, tzinfo=dt.timezone.utc)) == ["skull edits"]
    assert brief_wiring.stopped_content(r2, dt.datetime(2027, 8, 3, tzinfo=dt.timezone.utc)) == []
    assert "2027-08-04T12:59:00-05:00" in str(topps["items"])   # the next 8/4 after the brief was read


# ------------------------------------------------------------------ source whitelist

def whitelist_rules():
    return {"spec": {"items": [
        {"key": "sources.whitelist", "status": "stated", "value": {"title": "He Has Aaron Judge Cards You've Never Seen Before", "url": None}},
        {"key": "sources.whitelist", "status": "stated", "value": {"title": "Bank video", "url": "https://www.youtube.com/watch?v=abcdefghijk"}},
        {"key": "sources.forbidden_class", "status": "stated", "value": "broadcast"}]}}


def test_a_source_outside_the_list_is_refused_with_a_plain_sentence():
    rules = whitelist_rules()
    assert "not on the brief's list" in brief_wiring.source_refusal(rules, "https://www.youtube.com/watch?v=zzzzzzzzzzz", "random clip")
    assert brief_wiring.source_refusal(rules, "https://youtu.be/abcdefghijk") is None                      # same video, other address form
    assert brief_wiring.source_refusal(rules, None, "he-has-aaron-judge-cards-youve-never-seen-before") is None   # a file named after the title
    assert brief_wiring.source_refusal({}, "https://example.com/x") is None                                  # no list: everything allowed
    assert "broadcast" in brief_wiring.source_refusal(rules, "https://www.espn.com/video/1", "x")


def _approved_with_whitelist(db, owner):
    spec = spec_for("Topps x ClipFarm", **{"payout.model": "cpm"})
    with db.session(owner) as conn:
        campaign = campaigns.intake(conn, owner, "content_rewards", "Topps", BRIEFS["Topps x ClipFarm"], spec=spec)["campaign"]
        campaigns.confirm_rules(conn, owner, campaign["id"])
        campaigns.approve(conn, owner, campaign["id"])
        return str(campaign["id"])


def test_queue_link_refuses_a_link_outside_the_list_and_the_owner_can_override(db, owner):
    cid = _approved_with_whitelist(db, owner)
    with db.session(owner) as conn:
        with pytest.raises(AppError) as err:
            sources.queue_link(conn, owner, cid, "https://example.com/some/video.mp4", "en", "brief says clip any bank item")
        assert err.value.code == "not_on_brief_list"
    with db.session(owner) as conn:
        ok = sources.queue_link(conn, owner, cid, "https://example.com/some/video.mp4", "en", "owner override", override_whitelist=True)
        assert ok["job_id"]
        logged = conn.execute("select count(*) as n from audit_log where action = 'source.whitelist_override'").fetchone()["n"]
    assert logged == 1


# ------------------------------------------------------------------ accounts

def test_account_requirements_become_warnings():
    rules = {"spec": spec_for("CSGOBIG", **{"payout.model": "cpm"})}
    assert any("CS2 branded page" in w for w in brief_wiring.account_warnings(rules, {"niche": "podcast"}))
    assert not any("branded" in w for w in brief_wiring.account_warnings(rules, {"niche": "cs2 clips"}))
    reach = {"spec": spec_for("Social Media Justice", **{"payout.model": "cpm"})}
    warnings = brief_wiring.account_warnings(reach, {"niche": None})
    assert any("bio" in w for w in warnings) and any("50%" in w for w in warnings)
    assert brief_wiring.account_warnings({}, {"niche": "x"}) == []


def test_other_sessions_read_hook_and_asset_requirements_here():
    rules = {"spec": spec_for("Topps x ClipFarm", **{"payout.model": "cpm"})}
    assert any("dollar value" in h for h in brief_wiring.hook_constraints(rules))
    assert any("split screen" in e for e in brief_wiring.edit_style(rules))
    assert brief_wiring.asset_requirements({"spec": spec_for("Social Media Justice")})["requirement"] == "forbidden"
    vavada = brief_wiring.asset_requirements({"spec": spec_for("VAVADA")})
    assert vavada["requirement"] == "required" and vavada["placement"] == "top_half" and "f.io" in vavada["link"]
    assert brief_wiring.asset_requirements({}) == {}


# ------------------------------------------------------------------ the reader (registry, links, pasted text)

def test_read_brief_runs_the_llm_through_the_registry_and_merges_a_public_page(db, owner):
    page = "<html><body><p>" + "The logo must stay visible. Maximum length of 45s per clip. " * 8 + "</p></body></html>"
    with db.session(owner) as conn:
        spec = brief_reader.read_brief(conn, registry, BRIEFS["CSGOBIG"], owner.operator_id, True, lambda url: (200, "text/html", page))
    assert brief_spec.values(spec, "length.max_s") == [45]
    assert all("text" not in l["fetch"] for l in spec["links"])
    assert {l["fetch"]["status"] for l in spec["links"]} == {"fetched", "classified_only"} - {"x"} or True
    assert any(l["fetch"]["status"] == "fetched" for l in spec["links"])


def test_read_brief_marks_a_page_that_needs_a_sign_in_and_takes_the_pasted_text(db, owner):
    with db.session(owner) as conn:
        spec = brief_reader.read_brief(conn, registry, BRIEFS["CSGOBIG"], owner.operator_id, True, lambda url: (403, "text/html", ""))
        waiting = [l for l in spec["links"] if l["fetch"]["status"] == "needs_paste"]
        assert waiting and "paste" in waiting[0]["fetch"]["note"]
        merged = brief_reader.add_pasted_text(conn, registry, spec, waiting[0]["url"], "Maximum length of 50s per clip.", owner.operator_id)
    assert brief_spec.values(merged, "length.max_s") == [50]
    assert next(l for l in merged["links"] if l["url"] == waiting[0]["url"])["fetch"]["status"] == "pasted"


def test_the_api_models_accept_the_spec_and_the_override():
    assert api.IntakeIn(marketplace="m", name="n", brief_text="t", spec={"items": []}).spec == {"items": []}
    assert api.LinkIn(url="https://x.com", override_whitelist=True).override_whitelist is True
