"""The campaign's ad-label switch (owner notes 2, note 2): off per campaign, but a brief that requires the label keeps it on."""
from __future__ import annotations

import pytest
from psycopg.types.json import Jsonb

from clipping import ad_label, campaigns, pipeline
from clipping.agents import compliance
from clipping.core import AppError

from conftest import approved_campaign

TEMPLATE = {"caption_text": "#ad", "overlay_text": "Paid partnership"}


def _clip(caption: str, overlay_text: str | None, duration: float = 30.0) -> dict:
    overlay = {"text": overlay_text, "start_s": 0.0, "end_s": duration} if overlay_text else {}
    return {"caption_text": caption, "overlay_json": overlay, "duration_s": duration, "width": 1080, "height": 1920}


# ------------------------------------------------------------------ the rule (pure)

def test_a_brief_that_requires_the_label_is_read_from_its_rules():
    assert ad_label.required_by_brief({"disclosure": {"required_by_brief": True}}) is True
    assert ad_label.required_by_brief({"disclosure": {"required_by_brief": False}}) is False
    assert ad_label.required_by_brief({}) is False
    assert ad_label.required_by_brief(None) is False


def test_only_an_explicit_off_switch_turns_the_label_off():
    assert ad_label.is_off({"ad_label": False}) is True
    assert ad_label.is_off({"ad_label": True}) is False
    assert ad_label.is_off({}) is False        # campaigns made before the column existed keep the label


def test_an_off_label_carries_no_text_and_says_whether_the_brief_requires_it():
    assert ad_label.off_label({"disclosure": {"required_by_brief": True}}) == {
        "id": None, "caption_text": "", "overlay_text": None, "label_off": True, "required": True}
    assert ad_label.off_label({})["required"] is False


# ------------------------------------------------------------------ compliance follows the switch

def test_compliance_passes_an_off_label_the_brief_does_not_require():
    checks = {c["check"]: c for c in compliance.deterministic_checks(
        _clip("Plain caption", None), ad_label.off_label({}), {"min_s": 15, "max_s": 60}, [], [])}
    assert checks["caption_starts_with_disclosure"]["status"] == "pass"
    assert checks["overlay_present_full_duration"]["status"] == "pass"


def test_compliance_fails_an_off_label_the_brief_requires():
    checks = {c["check"]: c for c in compliance.deterministic_checks(
        _clip("Plain caption", None), ad_label.off_label({"disclosure": {"required_by_brief": True}}),
        {"min_s": 15, "max_s": 60}, [], [])}
    assert checks["caption_starts_with_disclosure"]["status"] == "fail"
    assert "requires the ad label" in checks["caption_starts_with_disclosure"]["evidence"]


def test_compliance_still_checks_the_label_when_it_is_on():
    on = {c["check"]: c for c in compliance.deterministic_checks(
        _clip("Plain caption", None), TEMPLATE, {"min_s": 15, "max_s": 60}, [], [])}
    assert on["caption_starts_with_disclosure"]["status"] == "fail"
    good = {c["check"]: c for c in compliance.deterministic_checks(
        _clip("#ad Plain caption", "Paid partnership"), TEMPLATE, {"min_s": 15, "max_s": 60}, [], [])}
    assert good["caption_starts_with_disclosure"]["status"] == "pass"
    assert good["overlay_present_full_duration"]["status"] == "pass"


def test_a_caption_without_the_label_has_no_leading_space():
    caption, _, _ = pipeline.build_caption("", "A hook", {})
    assert caption == "A hook"


# ------------------------------------------------------------------ the switch itself (database)

def _set_required(db, owner, campaign_id: str, required: bool) -> None:
    with db.session(owner) as conn:
        version_id = conn.execute("select current_version_id from campaigns where id = %s", (campaign_id,)).fetchone()["current_version_id"]
        rules = dict(conn.execute("select rules_json from campaign_versions where id = %s", (version_id,)).fetchone()["rules_json"])
        rules["disclosure"] = {**(rules.get("disclosure") or {}), "required_by_brief": required}
        conn.execute("update campaign_versions set rules_json = %s where id = %s", (Jsonb(rules), version_id))


def test_the_owner_can_switch_the_label_off_and_on_for_a_campaign(db, owner):
    campaign_id = approved_campaign(db, owner)
    _set_required(db, owner, campaign_id, False)
    with db.session(owner) as conn:
        assert campaigns.set_ad_label(conn, owner, campaign_id, False)["ad_label"] is False
    with db.session(owner) as conn:
        assert campaigns.set_ad_label(conn, owner, campaign_id, True)["ad_label"] is True


def test_the_switch_is_refused_when_the_brief_requires_the_label(db, owner):
    campaign_id = approved_campaign(db, owner)
    _set_required(db, owner, campaign_id, True)
    with db.session(owner) as conn:
        with pytest.raises(AppError) as refused:
            campaigns.set_ad_label(conn, owner, campaign_id, False)
    assert refused.value.code == "ad_label_required"
    with db.session(owner) as conn:
        assert conn.execute("select ad_label from campaigns where id = %s", (campaign_id,)).fetchone()["ad_label"] is True


def test_a_clip_is_not_rendered_when_the_brief_requires_the_label_and_the_switch_is_off(db, owner):
    campaign_id = approved_campaign(db, owner)
    _set_required(db, owner, campaign_id, True)
    with db.session(owner) as conn:
        conn.execute("update campaigns set ad_label = false where id = %s", (campaign_id,))   # forced past the switch
        campaign = conn.execute("select * from campaigns where id = %s", (campaign_id,)).fetchone()
        version = conn.execute("select * from campaign_versions where id = %s", (campaign["current_version_id"],)).fetchone()
        disclosure = pipeline.disclosure_for(conn, campaign, version, "en")
    assert disclosure["required"] is True and disclosure["label_off"] is True
