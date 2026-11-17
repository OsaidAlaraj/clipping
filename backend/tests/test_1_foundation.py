"""Build step 1: schema, row-level security, audit log, settings, kill switch, adapter interfaces and fakes."""
from __future__ import annotations

from decimal import Decimal

import psycopg
import pytest

from clipping import alerts, config, core
from clipping.adapters import base, vendors
from clipping.core import AppError
from clipping.db import Actor
from conftest import approved_campaign

TABLES = ["operators", "marketplaces", "categories", "campaigns", "campaign_versions", "campaign_rules", "source_assets",
          "transcripts", "jobs", "candidates", "clips", "hooks", "reviews", "accounts", "posts", "metrics", "payouts",
          "time_entries", "sessions", "alerts", "settings", "adapters", "kill_switch", "audit_log", "disclosure_templates"]


def test_every_spec_table_exists_with_standard_columns(db):
    with db.session(Actor.system()) as conn:
        rows = conn.execute("select table_name, column_name from information_schema.columns where table_schema = 'public'").fetchall()
    columns: dict = {}
    for row in rows:
        columns.setdefault(row["table_name"], set()).add(row["column_name"])
    for table in TABLES:
        assert {"id", "created_at", "updated_at", "created_by"} <= columns[table], table


def test_row_level_security_is_on_for_every_table(db):
    with db.session(Actor.system()) as conn:
        rows = conn.execute("""select c.relname, c.relrowsecurity, (select count(*) from pg_policies p where p.tablename = c.relname) as policies
                               from pg_class c join pg_namespace n on n.oid = c.relnamespace
                               where n.nspname = 'public' and c.relkind = 'r' and c.relname <> 'schema_migrations'""").fetchall()
    assert len(rows) >= len(TABLES)
    for row in rows:
        assert row["relrowsecurity"] and row["policies"] > 0, row["relname"]


def _operator(db, name: str, role: str = "operator") -> Actor:
    with db.session(Actor.system()) as conn:
        row = conn.execute("insert into operators (name, role) values (%s, %s) returning id", (name, role)).fetchone()
    return Actor(str(row["id"]), role)


def test_rls_separates_operators_and_admin_sees_all(db, owner):
    alice, bob = _operator(db, "Alice"), _operator(db, "Bob")
    campaign_id = approved_campaign(db, alice, "Alice campaign")
    with db.session(bob) as conn:
        assert conn.execute("select count(*) as n from campaigns").fetchone()["n"] == 0
        assert conn.execute("update campaigns set name = 'x' where id = %s returning id", (campaign_id,)).fetchone() is None
    with db.session(alice) as conn:
        assert conn.execute("select count(*) as n from campaigns").fetchone()["n"] == 1
    with db.session(owner) as conn:   # admin
        assert conn.execute("select count(*) as n from campaigns").fetchone()["n"] == 1
    with pytest.raises(psycopg.Error):   # Bob cannot create a row owned by Alice
        with db.session(bob) as conn:
            conn.execute("insert into accounts (platform, handle, owner_operator_id) values ('tiktok', 'h', %s)", (alice.operator_id,))


def test_reviewer_reads_clips_but_cannot_change_campaigns(db):
    alice, rev = _operator(db, "Alice"), _operator(db, "Rev", "reviewer")
    campaign_id = approved_campaign(db, alice)
    with db.session(rev) as conn:
        assert conn.execute("select count(*) as n from campaigns").fetchone()["n"] == 1
        assert conn.execute("update campaigns set name = 'x' where id = %s returning id", (campaign_id,)).fetchone() is None
        assert conn.execute("select count(*) as n from accounts").fetchone()["n"] == 0


def test_account_has_exactly_one_owner(db, owner):
    other = _operator(db, "Other")
    with db.session(owner) as conn:
        conn.execute("insert into accounts (platform, handle, owner_operator_id) values ('tiktok', 'same', %s)", (owner.operator_id,))
    with pytest.raises(psycopg.errors.UniqueViolation):   # the same handle cannot belong to a second operator
        with db.session(other) as conn:
            conn.execute("insert into accounts (platform, handle, owner_operator_id) values ('tiktok', 'same', %s)", (other.operator_id,))
    with pytest.raises(psycopg.errors.NotNullViolation):
        with db.session(Actor.system()) as conn:
            conn.execute("insert into accounts (platform, handle, owner_operator_id) values ('tiktok', 'orphan', null)")


# ------------------------------------------------------------------ audit log

def test_audit_log_is_append_only_for_every_role(db, owner):
    approved_campaign(db, owner)
    for actor in (owner, Actor.worker(), Actor.system()):
        for sql in ("update audit_log set action = 'x'", "delete from audit_log", "truncate audit_log"):
            with pytest.raises(psycopg.Error):
                with db.session(actor) as conn:
                    conn.execute(sql)
    with db.session(Actor.system()) as conn:
        grants = conn.execute("""select privilege_type from information_schema.role_table_grants
                                 where table_name = 'audit_log' and grantee in ('app_user', 'app_worker')""").fetchall()
        assert {g["privilege_type"] for g in grants} == {"SELECT", "INSERT"}
        assert conn.execute("select count(*) as n from audit_log").fetchone()["n"] > 0


def test_every_state_change_writes_an_audit_row_with_actor_and_hashes(db, owner):
    campaign_id = approved_campaign(db, owner)
    with db.session(owner) as conn:
        rows = conn.execute("select * from audit_log where entity_type = 'campaigns' and entity_id = %s order by at, action", (campaign_id,)).fetchall()
    actions = [r["action"] for r in rows]
    assert "campaigns.insert" in actions
    assert "campaigns.status:reading>pending_approval" in actions
    assert "campaigns.status:pending_approval>approved" in actions
    assert "gate.campaign_approval" in actions
    change = next(r for r in rows if r["action"] == "campaigns.status:pending_approval>approved")
    assert str(change["actor_id"]) == owner.operator_id and change["actor_kind"] == "operator"
    assert change["before_hash"] and change["after_hash"] and change["before_hash"] != change["after_hash"]
    assert change["rule_version_id"] is not None


def test_audit_log_never_stores_brief_text(db, owner):
    approved_campaign(db, owner)
    with db.session(owner) as conn:
        dump = " ".join(str(r) for r in conn.execute("select action, detail from audit_log").fetchall())
    assert "FoundersTable" not in dump and "per 1,000 views" not in dump


# ------------------------------------------------------------------ settings

def test_settings_defaults_match_the_spec(db, owner):
    expected = {"hours_cap_per_day": 5, "session_length_min": 25, "undo_window_s": 10, "pay_per_clip_display": "summary",
                "posts_per_operator_per_day": 6, "posts_per_account_per_day": 2, "clips_per_campaign_per_week": 10,
                "budget_alert_threshold_pct": 20, "health_alert_drop_pct": 60, "source_retention_days": 30,
                "clip_retention_days_after_close": 90, "adapter_cap_usd_moments": 20, "adapter_cap_usd_transcription": 10,
                "adapter_cap_usd_llm": 10, "adapter_cap_usd_database": 25, "adapter_alert_pct": 80}
    with db.session(owner) as conn:
        for key, value in expected.items():
            assert core.get_setting(conn, key) == value, key
        assert core.get_setting(conn, "posting_window") == {"start": "09:00", "end": "22:00"}


@pytest.mark.parametrize("key,bad", [("hours_cap_per_day", 9), ("hours_cap_per_day", 0), ("session_length_min", 61),
                                     ("undo_window_s", 4), ("posts_per_account_per_day", 4), ("clips_per_campaign_per_week", 31),
                                     ("source_retention_days", 6), ("adapter_cap_usd_moments", 101), ("pay_per_clip_display", "always"),
                                     ("session_length_min", "long")])
def test_settings_stay_within_bounds(db, owner, key, bad):
    with pytest.raises(AppError):
        with db.session(owner) as conn:
            core.set_setting(conn, key, bad)
    with db.session(owner) as conn:
        assert core.get_setting(conn, key) != bad


def test_bounds_hold_even_for_direct_sql(db):
    with pytest.raises(psycopg.errors.RaiseException):
        with db.session(Actor.system()) as conn:
            conn.execute("update settings set value = '99' where key = 'hours_cap_per_day'")


def test_each_setting_change_writes_an_audit_row(db, owner):
    with db.session(owner) as conn:
        row = core.set_setting(conn, "session_length_min", 40)
        assert core.get_setting(conn, "session_length_min") == 40
        audits = conn.execute("select * from audit_log where entity_type = 'settings' and entity_id = %s and action = 'settings.update'",
                              (row["id"],)).fetchall()
    assert len(audits) == 1 and str(audits[0]["actor_id"]) == owner.operator_id


def test_scoped_setting_overrides_the_base_value(db, owner):
    with db.session(owner) as conn:
        core.set_setting(conn, "undo_window_s", 20, owner.operator_id)
        assert core.get_setting(conn, "undo_window_s", owner.operator_id) == 20
        assert core.get_setting(conn, "undo_window_s") == 10


def test_settings_cannot_add_or_override_a_hard_rule(db, owner):
    for key in ("allow_gambling", "auto_post", "skip_disclosure", "allow_shared_accounts"):
        with pytest.raises(AppError):
            with db.session(owner) as conn:
                core.set_setting(conn, key, True)
    with db.session(owner) as conn:
        keys = {r["key"] for r in conn.execute("select key from settings").fetchall()}
    assert not any(word in key for key in keys for word in ("gambl", "disclos", "auto_post", "bot"))


# ------------------------------------------------------------------ kill switch

def test_kill_switch_blocks_new_jobs_in_code_and_in_the_database(db, owner):
    campaign_id = approved_campaign(db, owner)
    with db.session(owner) as conn:
        version = conn.execute("select approved_version_id from campaigns where id = %s", (campaign_id,)).fetchone()["approved_version_id"]
        assert core.enqueue(conn, "transcribe", {"x": 1}, owner.operator_id, campaign_version_id=version) is not None
        core.set_kill_switch(conn, True, "test")
        assert conn.execute("select status from jobs").fetchone()["status"] == "cancelled"
    with pytest.raises(core.KillSwitchOn):
        with db.session(owner) as conn:
            core.enqueue(conn, "transcribe", {"x": 2}, owner.operator_id, campaign_version_id=version)
    with pytest.raises(psycopg.errors.RaiseException):   # direct insert, bypassing the code path
        with db.session(Actor.worker()) as conn:
            conn.execute("insert into jobs (type, input_hash, idempotency_key, owner_operator_id) values ('x', 'h', 'x:h', %s)", (owner.operator_id,))
    with db.session(Actor.worker()) as conn:
        assert core.claim_job(conn) is None
    with db.session(owner) as conn:
        core.set_kill_switch(conn, False)
        assert core.enqueue(conn, "transcribe", {"x": 3}, owner.operator_id, campaign_version_id=version) is not None


def test_only_an_admin_can_flip_the_kill_switch(db):
    operator = _operator(db, "Op")
    with pytest.raises(AppError):
        with db.session(operator) as conn:
            core.set_kill_switch(conn, True)


def test_jobs_are_idempotent_and_need_an_approved_campaign(db, owner):
    campaign_id = approved_campaign(db, owner)
    with db.session(owner) as conn:
        version = conn.execute("select approved_version_id from campaigns where id = %s", (campaign_id,)).fetchone()["approved_version_id"]
        first = core.enqueue(conn, "render", {"clip": "a"}, owner.operator_id, campaign_version_id=version)
        again = core.enqueue(conn, "render", {"clip": "a"}, owner.operator_id, campaign_version_id=version)
        assert first is not None and again is None
        assert first["idempotency_key"] == "render:" + first["input_hash"] and first["cost_usd"] == 0
        conn.execute("update campaigns set status = 'paused' where id = %s", (campaign_id,))
    with pytest.raises(psycopg.errors.RaiseException):
        with db.session(owner) as conn:
            core.enqueue(conn, "render", {"clip": "b"}, owner.operator_id, campaign_version_id=version)


# ------------------------------------------------------------------ adapters

STAGES = ["source", "discovery", "audience", "llm", "transcription", "moments", "render", "captions", "scheduler", "storage", "database", "alerts",
          "metrics", "payouts"]


def test_every_adapter_has_the_common_interface_and_every_external_service_has_a_fake(db, owner):
    with db.session(owner) as conn:
        rows = conn.execute("select * from adapters").fetchall()
    assert {r["stage"] for r in rows} == set(STAGES)
    for row in rows:
        cls = base.PROVIDERS[(row["stage"], row["provider"])]
        adapter = cls(row)
        assert isinstance(adapter.health(), dict) and "ok" in adapter.health()
        assert set(adapter.quota()) == {"used", "limit", "left"}
        assert adapter.config()["stage"] == row["stage"] and "fallback" in adapter.config()
        cost = adapter.cost({})
        assert cost is None or isinstance(cost, Decimal)
    for stage in ("source", "discovery", "audience", "llm", "transcription", "moments", "scheduler", "alerts"):
        assert any(r["stage"] == stage and r["is_fake"] for r in rows), stage
    external = [r for r in rows if r["provider"] in ("scraper", "azure_openai", "deepgram", "vizard", "klap", "opusclip", "buffer", "r2", "telegram", "clippingalpha", "platform_apis")]
    assert all(r["fallback_provider"] for r in external) and not any(r["active"] for r in external)


def test_plans_prices_and_keys_are_configuration_and_no_key_is_stored(db, owner):
    with db.session(owner) as conn:
        rows = conn.execute("select provider, config_json from adapters").fetchall()
        deepgram = base.PROVIDERS[("transcription", "deepgram")](conn.execute("select * from adapters where provider = 'deepgram'").fetchone())
        assert deepgram.cost({"duration_s": 600}) == Decimal("0.0520")
        conn.execute("""update adapters set config_json = config_json || '{"price_per_min_usd":0.01}' where provider = 'deepgram'""")
        repriced = base.PROVIDERS[("transcription", "deepgram")](conn.execute("select * from adapters where provider = 'deepgram'").fetchone())
        assert repriced.cost({"duration_s": 600}) == Decimal("0.1000")
    text = str(rows).lower()
    assert "api_key" not in text and "token\"" not in text and "secret" not in text
    assert (config.ROOT / ".env.example").exists()


def test_switching_vendor_is_a_config_change_and_opusclip_needs_a_quote(db, owner):
    with db.session(owner) as conn:
        base.activate(conn, "moments", "klap")
        assert alerts.registry.row(conn, "moments")["provider"] == "klap"
        assert conn.execute("select count(*) as n from audit_log where entity_type = 'adapters' and action = 'adapters.update'").fetchone()["n"] >= 2
    with pytest.raises(AppError):
        with db.session(owner) as conn:
            base.activate(conn, "moments", "opusclip")


class _Flaky(base.Adapter):
    stage, provider = "transcription", "flaky"
    calls = 0

    def transcribe(self, **_):
        type(self).calls += 1
        raise RuntimeError("boom token=SECRETVALUE123")


def test_three_failures_run_the_fallback_and_raise_an_alert(db, owner, monkeypatch):
    base.register(_Flaky)
    _Flaky.calls = 0
    monkeypatch.setenv("DEEPGRAM_API_KEY", "SECRETVALUE123")
    with db.session(Actor.system()) as conn:
        conn.execute("insert into adapters (stage, provider, fallback_provider, is_fake) values ('transcription', 'flaky', 'fake', true)")
        base.activate(conn, "transcription", "flaky")
    with db.session(owner) as conn:
        result, _ = alerts.registry.call(conn, "transcription", "transcribe",
                                         {"audio_path": "x", "language": "en", "duration_s": 10}, owner.operator_id)
        assert result["provider"] == "fake" and _Flaky.calls == 3
        alert = conn.execute("select * from alerts where type = 'adapter_failed'").fetchone()
        assert alert is not None
        failed = conn.execute("select detail from audit_log where action = 'adapter.call' and (detail->>'ok') = 'false'").fetchone()
        assert "SECRETVALUE123" not in str(failed["detail"])   # secrets are redacted before anything is stored
    del base.PROVIDERS[("transcription", "flaky")]


def test_cost_cap_pauses_a_paid_adapter_with_a_block_alert_and_unknown_price_never_runs(db, owner):
    with db.session(Actor.system()) as conn:
        conn.execute("update adapters set cost_month_usd = 9.99 where provider = 'deepgram'")
        base.activate(conn, "transcription", "deepgram")
    with db.session(owner) as conn:
        with pytest.raises(base.AdapterPaused):
            alerts.registry.call(conn, "transcription", "transcribe", {"audio_path": "x", "language": "en", "duration_s": 600}, owner.operator_id)
        assert conn.execute("select paused_reason from adapters where provider = 'deepgram'").fetchone()["paused_reason"]
        assert conn.execute("select severity from alerts where type = 'adapter_cap'").fetchone()["severity"] == "block"
    assert [a["severity"] for a in vendors.FAKE_ALERTS["sent"]] == ["block", "block"]   # block alerts are sent twice
    with db.session(Actor.system()) as conn:
        base.activate(conn, "llm", "azure_openai")
        conn.execute("update adapters set config_json = config_json - 'price_per_1k_tokens_usd' where provider = 'azure_openai'")   # price not configured
    with db.session(owner) as conn:
        with pytest.raises(base.AdapterPaused):
            alerts.registry.call(conn, "llm", "complete_json", {"task": "rules", "payload": {}}, owner.operator_id)


def test_every_adapter_call_writes_an_audit_row(db, owner):
    with db.session(owner) as conn:
        alerts.registry.call(conn, "llm", "complete_json", {"task": "hooks", "payload": {"segment_text": "Hello there."}}, owner.operator_id)
        row = conn.execute("select detail from audit_log where action = 'adapter.call'").fetchone()
    assert row["detail"]["stage"] == "llm" and row["detail"]["ok"] is True


def test_scraper_stays_off_until_terms_are_recorded(db, owner):
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.session(owner) as conn:
            conn.execute("update marketplaces set scraper_enabled = true where slug = 'vyro'")
    with db.session(owner) as conn:
        assert conn.execute("select count(*) as n from marketplaces where scraper_enabled").fetchone()["n"] == 0
        market = conn.execute("select * from marketplaces where slug = 'vyro'").fetchone()
        scraper = alerts.registry.get(conn, "source", "scraper")
        with pytest.raises(vendors.ScraperDisabled):
            scraper.list_campaigns(marketplace=market)
        with pytest.raises(vendors.ScraperDisabled):
            alerts.registry.get(conn, "source", "fake").list_campaigns(marketplace=market)
        conn.execute("""update marketplaces set scraper_enabled = true, terms_version = '2026-11', terms_read_at = now(),
                        terms_confirmed_by = %s where slug = 'vyro'""", (owner.operator_id,))
        market = conn.execute("select * from marketplaces where slug = 'vyro'").fetchone()
        assert alerts.registry.get(conn, "source", "fake").list_campaigns(marketplace=market) == []


def test_unknown_fees_are_stored_as_unknown(db, owner):
    with db.session(owner) as conn:
        rules = {r["slug"]: r["fee_rules"] for r in conn.execute("select slug, fee_rules from marketplaces").fetchall()}
    for slug in ("vyro", "clipping_net", "elev8or", "clipflip"):
        assert rules[slug]["creator_fee"] is None
    assert rules["content_rewards"]["creator_fee"][0]["pct"] == 10


def test_media_links_are_signed_and_expire(db, owner):
    with db.session(owner) as conn:
        url = alerts.registry.get(conn, "storage").get_signed_url("clips/a/final.mp4", 60)
    key, query = url.removeprefix("/api/media/").split("?")
    params = dict(p.split("=") for p in query.split("&"))
    assert vendors.verify_media(key, int(params["exp"]), params["sig"])
    assert not vendors.verify_media(key, int(params["exp"]), "0" * 64)
    assert not vendors.verify_media("clips/b/final.mp4", int(params["exp"]), params["sig"])
    assert not vendors.verify_media(key, 1, vendors.sign_media(key, 1))
