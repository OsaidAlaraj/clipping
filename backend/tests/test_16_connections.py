"""Phase E, phase 1: connected accounts and the token vault. Everything runs on the fake OAuth provider."""
from __future__ import annotations

import datetime as dt
import logging
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import psycopg
import pytest
from fastapi.testclient import TestClient

from clipping import api, connections, vault
from clipping.adapters import oauth
from clipping.adapters.base import AdapterError
from clipping.core import AppError
from clipping.db import Actor

OTHER_KEY = "b29vb29vb29vb29vb29vb29vb29vb29vb29vb29vb28="   # 32 bytes, a different key


def connect(db, owner, platform="youtube_shorts", account_id=None, code="fake-code-1"):
    with db.session(owner) as conn:
        url = connections.start(conn, owner, platform, account_id)["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    return connections.complete(db, platform, code, state), state


def second_owner(db) -> Actor:
    with db.session(Actor.system()) as conn:
        row = conn.execute("insert into operators (name, role, languages) values ('Second', 'operator', '{en}') returning id").fetchone()
    return Actor(str(row["id"]), "operator")


def system_rows(db, sql, *args):
    with db.session(Actor.system()) as conn:
        return conn.execute(sql, args).fetchall()


def sweep_everything(db, owner, tokens, caplog, responses):
    """Every place a token could leak: the connection row's plain columns, audit rows, alerts, logs and API responses."""
    rows = system_rows(db, "select row_to_json(a)::text as t from audit_log a union all select row_to_json(x)::text from alerts x "
                           "union all select (row_to_json(c)::jsonb - 'secret_ct')::text from platform_connections c "
                           "union all select row_to_json(s)::text from oauth_states s")
    haystack = "\n".join([r["t"] for r in rows] + [rec.getMessage() for rec in caplog.records] + responses)
    for token in tokens:
        assert token not in haystack


# ------------------------------------------------------------------ DW-1.1

def test_DW_1_1_round_trip_stores_only_ciphertext_and_leaks_no_token(db, owner, caplog):
    caplog.set_level(logging.DEBUG)
    view, _ = connect(db, owner)
    assert view["state"] == "connected" and view["platform"] == "youtube_shorts"
    tokens = oauth.FAKE_OAUTH["issued"]
    assert len(tokens) == 2                                           # access and refresh
    row = system_rows(db, "select * from platform_connections")[0]
    assert isinstance(row["secret_ct"], (bytes, memoryview)) and row["key_id"] == vault.key_id()
    for token in tokens:
        assert token.encode() not in bytes(row["secret_ct"])
    with db.session(owner) as conn:
        assert vault.access_token(conn, row["id"]) == tokens[0]
    client = TestClient(api.app, follow_redirects=False)
    api._owner = owner
    responses = [client.get("/api/connections").text, client.post("/api/connections/tiktok/start").text]
    start = client.post("/api/connections/youtube_shorts/start")
    state = parse_qs(urlparse(start.json()["url"]).query)["state"][0]
    cb = client.get("/api/connections/youtube_shorts/callback", params={"code": "fake-code-2", "state": state})
    responses += [start.text, cb.text, cb.headers["location"], client.get("/api/connections").text]
    sweep_everything(db, owner, oauth.FAKE_OAUTH["issued"], caplog, responses)
    assert system_rows(db, "select count(*) as n from audit_log where action = 'connection.connected'")[0]["n"] == 2


def test_connections_list_and_for_account_never_carry_tokens_or_ciphertext(db, owner):
    view, _ = connect(db, owner)
    with db.session(owner) as conn:
        listing = connections.list_all(conn)
        one = connections.for_account(conn, view["account_id"])
        assert connections.for_account(conn, "00000000-0000-0000-0000-000000000000") is None
    assert set(one) == {"id", "platform", "state", "scopes", "expires_at"}
    assert listing["vault_ready"] is True and [c["id"] for c in listing["connections"]] == [view["id"]]
    assert "secret_ct" not in str(listing) and "access" not in str(listing)


# ------------------------------------------------------------------ DW-1.2

def test_DW_1_2_forged_state_is_refused_and_connects_nothing(db, owner):
    with pytest.raises(AppError) as exc:
        connections.complete(db, "youtube_shorts", "fake-code-1", "forged-state")
    assert exc.value.code == "bad_state" and "Start again" in str(exc.value)
    assert system_rows(db, "select 1 from platform_connections") == []


def test_DW_1_2_replayed_state_is_refused(db, owner):
    _, state = connect(db, owner)
    with pytest.raises(AppError) as exc:
        connections.complete(db, "youtube_shorts", "fake-code-1", state)
    assert exc.value.code == "bad_state"
    assert system_rows(db, "select count(*) as n from platform_connections")[0]["n"] == 1


def test_a_state_for_one_platform_cannot_finish_another_and_an_expired_state_is_refused(db, owner):
    with db.session(owner) as conn:
        url = connections.start(conn, owner, "tiktok")["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    with pytest.raises(AppError) as exc:
        connections.complete(db, "youtube_shorts", "fake-code-1", state)
    assert exc.value.code == "bad_state"
    with db.session(owner) as conn:
        state2 = parse_qs(urlparse(connections.start(conn, owner, "tiktok")["url"]).query)["state"][0]
    with db.session(Actor.system()) as conn:
        conn.execute("update oauth_states set expires_at = now() - interval '1 minute'")
    with pytest.raises(AppError):
        connections.complete(db, "tiktok", "fake-code-1", state2)


def test_DW_1_2_missing_vault_key_refuses_connect_and_existing_rows_report_needs_reconnect(db, owner, monkeypatch):
    view, _ = connect(db, owner)
    monkeypatch.delenv("TOKEN_VAULT_KEY")
    with db.session(owner) as conn:
        with pytest.raises(vault.VaultUnavailable) as exc:
            connections.start(conn, owner, "tiktok")
        assert "TOKEN_VAULT_KEY" in str(exc.value)
        listing = connections.list_all(conn)
        assert listing["vault_ready"] is False and listing["connections"][0]["state"] == "needs_reconnect"
        assert connections.for_account(conn, view["account_id"])["state"] == "needs_reconnect"
        with pytest.raises(vault.VaultUnavailable):
            vault.access_token(conn, view["id"])
        assert connections.disconnect(conn, owner, view["id"])["revoked"] is False        # still removable
    assert system_rows(db, "select 1 from platform_connections") == []


def test_a_malformed_vault_key_is_refused_with_a_plain_message(monkeypatch):
    monkeypatch.setenv("TOKEN_VAULT_KEY", "too-short")
    with pytest.raises(vault.VaultUnavailable) as exc:
        vault.key_id()
    assert "base64 of 32 bytes" in str(exc.value)


def test_DW_1_2_wrong_key_fails_closed_without_marking_the_row(db, owner, monkeypatch):
    view, _ = connect(db, owner)
    monkeypatch.setenv("TOKEN_VAULT_KEY", OTHER_KEY)
    with db.session(owner) as conn:
        with pytest.raises(vault.VaultError) as exc:
            vault.access_token(conn, view["id"])
        assert exc.value.code == "vault_key_changed"
        assert connections.list_all(conn)["connections"][0]["state"] == "needs_reconnect"
    assert system_rows(db, "select state from platform_connections")[0]["state"] == "connected"   # the old key may come back


def test_DW_1_2_tampered_ciphertext_fails_closed_and_needs_reconnect(db, owner):
    view, _ = connect(db, owner)
    with db.session(Actor.system()) as conn:
        conn.execute("update platform_connections set secret_ct = set_byte(secret_ct, 20, get_byte(secret_ct, 20) # 1)")
    with db.session(owner) as conn:
        with pytest.raises(vault.VaultError) as exc:
            vault.access_token(conn, view["id"])
        assert exc.value.code == "vault_tampered" and "Connect the account again" in str(exc.value)
    assert system_rows(db, "select state from platform_connections")[0]["state"] == "needs_reconnect"
    assert system_rows(db, "select 1 from alerts where type = 'connection_reconnect'")


def test_a_sealed_blob_copied_to_another_row_does_not_open(db, owner):
    a, _ = connect(db, owner, "youtube_shorts")
    b, _ = connect(db, owner, "tiktok")
    with db.session(Actor.system()) as conn:
        conn.execute("update platform_connections set secret_ct = (select secret_ct from platform_connections where id = %s) where id = %s",
                     (a["id"], b["id"]))
    with db.session(owner) as conn:
        with pytest.raises(vault.VaultError):
            vault.access_token(conn, b["id"])


def test_seal_and_open_round_trip_and_a_wrong_row_label_is_refused():
    blob, kid = vault.seal({"access": "x"}, "connection:1")
    assert vault.open_(blob, kid, "connection:1") == {"access": "x"}
    with pytest.raises(vault.VaultError):
        vault.open_(blob, kid, "connection:2")
    with pytest.raises(vault.VaultError):
        vault.open_(b"short", kid, "connection:1")


# ------------------------------------------------------------------ DW-1.3

def test_DW_1_3_an_expiring_token_refreshes_on_access_token_and_keeps_the_refresh_token(db, owner):
    oauth.FAKE_OAUTH["expires_in"] = 60                            # inside the two-minute skew
    view, _ = connect(db, owner)
    first = oauth.FAKE_OAUTH["issued"][0]
    oauth.FAKE_OAUTH["expires_in"] = 3600
    with db.session(owner) as conn:
        fresh = vault.access_token(conn, view["id"])
        assert fresh != first and fresh == oauth.FAKE_OAUTH["issued"][-1]
        row = conn.execute("select * from platform_connections").fetchone()
        assert row["expires_at"] - dt.datetime.now(dt.timezone.utc) > dt.timedelta(minutes=50) and row["last_refreshed_at"]
        assert vault.open_(row["secret_ct"], row["key_id"], vault.aad_for(row["id"]))["refresh"] == oauth.FAKE_OAUTH["issued"][1]
        calls = len(oauth.FAKE_OAUTH["calls"])
        assert vault.access_token(conn, view["id"]) == fresh          # still fresh: no second refresh
        assert len(oauth.FAKE_OAUTH["calls"]) == calls


def test_DW_1_3_a_revoked_refresh_token_marks_needs_reconnect_and_raises_an_alert(db, owner):
    oauth.FAKE_OAUTH["expires_in"] = 60
    view, _ = connect(db, owner)
    oauth.FAKE_OAUTH["revoked"] = True
    with db.session(owner) as conn:
        with pytest.raises(vault.NeedsReconnect):
            vault.access_token(conn, view["id"])
        assert connections.for_account(conn, view["account_id"])["state"] == "needs_reconnect"
        assert conn.execute("select 1 from alerts where type = 'connection_reconnect' and entity_id = %s", (view["id"],)).fetchone()
        before = len(oauth.FAKE_OAUTH["calls"])
        with pytest.raises(vault.NeedsReconnect):
            vault.access_token(conn, view["id"])                      # no further refresh attempts
        assert len(oauth.FAKE_OAUTH["calls"]) == before


def test_a_refresh_outage_keeps_a_still_valid_token_but_not_an_expired_one(db, owner):
    oauth.FAKE_OAUTH["expires_in"] = 60
    view, _ = connect(db, owner)
    oauth.FAKE_OAUTH["fail"] = "transient"
    with db.session(owner) as conn:
        assert vault.access_token(conn, view["id"]) == oauth.FAKE_OAUTH["issued"][0]
        conn.execute("update platform_connections set expires_at = now() - interval '1 minute'")
        with pytest.raises(AppError):
            vault.access_token(conn, view["id"])
        assert conn.execute("select state from platform_connections").fetchone()["state"] == "connected"   # an outage is not a revoke


def test_a_token_with_no_expiry_is_returned_without_a_refresh(db, owner):
    view, _ = connect(db, owner)
    with db.session(owner) as conn:
        conn.execute("update platform_connections set expires_at = null")
        calls = len(oauth.FAKE_OAUTH["calls"])
        assert vault.access_token(conn, view["id"]) == oauth.FAKE_OAUTH["issued"][0]
        assert len(oauth.FAKE_OAUTH["calls"]) == calls


def test_a_refresh_expiring_within_a_week_shows_expiring_and_a_lapsed_one_needs_reconnect(db, owner):
    view, _ = connect(db, owner)
    with db.session(owner) as conn:
        conn.execute("update platform_connections set refresh_expires_at = now() + interval '2 days'")
        assert connections.for_account(conn, view["account_id"])["state"] == "expiring"
        conn.execute("update platform_connections set refresh_expires_at = now() - interval '1 day'")
        assert connections.for_account(conn, view["account_id"])["state"] == "needs_reconnect"


def test_an_unreadable_platform_answer_is_refused(db, owner):
    with db.session(owner) as conn:
        for bad in ({}, {"access_token": "", "external_account_id": "x"}, {"access_token": "t", "external_account_id": ""}, None):
            with pytest.raises(AppError) as exc:
                connections._save(conn, owner, "tiktok", None, bad)
            assert exc.value.code == "oauth_bad_response"


# ------------------------------------------------------------------ DW-1.4

def test_DW_1_4_disconnect_revokes_and_deletes_the_ciphertext(db, owner):
    view, _ = connect(db, owner)
    with db.session(owner) as conn:
        result = connections.disconnect(conn, owner, view["id"])
    assert result["revoked"] is True and ("revoke", "youtube_shorts") in oauth.FAKE_OAUTH["calls"]
    assert system_rows(db, "select 1 from platform_connections") == []
    with db.session(owner) as conn:
        with pytest.raises(AppError) as exc:
            connections.disconnect(conn, owner, view["id"])
        assert exc.value.code == "not_connected"
        with pytest.raises(AppError):
            connections.disconnect(conn, owner, "not-a-uuid")


def test_instagram_cannot_be_revoked_by_us_but_is_still_deleted(db, owner):
    view, _ = connect(db, owner, "instagram_reels")
    with db.session(owner) as conn:
        assert connections.disconnect(conn, owner, view["id"])["revoked"] in (True, False)
    assert system_rows(db, "select 1 from platform_connections") == []


def test_DW_1_4_another_owner_can_never_read_or_use_the_connection(db, owner):
    view, _ = connect(db, owner)
    other = second_owner(db)
    with db.session(other) as conn:
        assert conn.execute("select count(*) as n from platform_connections").fetchone()["n"] == 0
        assert connections.list_all(conn)["connections"] == []
        assert connections.for_account(conn, view["account_id"]) is None
        with pytest.raises(AppError) as exc:
            vault.access_token(conn, view["id"])
        assert exc.value.code == "not_connected"
        with pytest.raises(AppError):
            connections.disconnect(conn, other, view["id"])
        assert conn.execute("delete from platform_connections where id = %s returning id", (view["id"],)).fetchone() is None
        assert conn.execute("update platform_connections set state = 'needs_reconnect' where id = %s returning id", (view["id"],)).fetchone() is None
    with db.session(other) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("insert into platform_connections (owner_operator_id, account_id, platform, external_account_id, secret_ct, key_id) "
                         "values (%s, %s, 'tiktok', 'x', %s, 'k')", (owner.operator_id, view["account_id"], b"x"))
    assert system_rows(db, "select state from platform_connections")[0]["state"] == "connected"


def test_an_account_connected_by_another_owner_is_refused(db, owner):
    connect(db, owner)
    other = second_owner(db)
    with db.session(other) as conn:
        url = connections.start(conn, other, "youtube_shorts")["url"]
    with pytest.raises(AppError) as exc:
        connections.complete(db, "youtube_shorts", "fake-code-1", parse_qs(urlparse(url).query)["state"][0])
    assert exc.value.code == "account_taken" and "someone else" in str(exc.value)
    assert system_rows(db, "select count(*) as n from platform_connections")[0]["n"] == 1


def test_the_worker_can_get_a_token_but_cannot_start_a_sign_in(db, owner):
    view, _ = connect(db, owner)
    with db.session(Actor.worker()) as conn:
        assert vault.access_token(conn, view["id"]) == oauth.FAKE_OAUTH["issued"][0]
        with pytest.raises(AppError) as exc:
            connections.start(conn, Actor.worker(), "tiktok")
        assert exc.value.code == "human_only"


# ------------------------------------------------------------------ linking accounts

def test_the_account_chosen_at_start_is_linked_and_reconnecting_reuses_the_row(db, owner):
    with db.session(owner) as conn:
        account = conn.execute("insert into accounts (platform, handle, owner_operator_id) values ('tiktok', 'own_tt', %s) returning id",
                               (owner.operator_id,)).fetchone()
    first, _ = connect(db, owner, "tiktok", str(account["id"]))
    again, _ = connect(db, owner, "tiktok", str(account["id"]))
    assert first["account_id"] == again["account_id"] == str(account["id"]) and first["id"] == again["id"]
    assert system_rows(db, "select count(*) as n from platform_connections")[0]["n"] == 1
    assert system_rows(db, "select platform_account_id from accounts where id = %s", account["id"])[0]["platform_account_id"] == "fake-tiktok-1"


def test_a_sign_in_for_a_different_platform_account_than_the_one_chosen_is_refused(db, owner):
    with db.session(owner) as conn:
        account = conn.execute("insert into accounts (platform, handle, platform_account_id, owner_operator_id) "
                               "values ('tiktok', 'own_tt', 'someone-else', %s) returning id", (owner.operator_id,)).fetchone()
    with pytest.raises(AppError) as exc:
        connect(db, owner, "tiktok", str(account["id"]))
    assert exc.value.code == "wrong_account"
    with db.session(owner) as conn:
        with pytest.raises(AppError) as exc2:
            connections.start(conn, owner, "youtube_shorts", str(account["id"]))          # an account of another platform
        assert exc2.value.code == "bad_account"
        with pytest.raises(AppError):
            connections.start(conn, owner, "youtube_shorts", "not-a-uuid")


def test_a_known_handle_is_matched_and_an_unknown_one_creates_the_account(db, owner):
    oauth.FAKE_OAUTH["accounts"]["instagram_reels"] = ("ig-77", "@own_ig")
    with db.session(owner) as conn:
        conn.execute("insert into accounts (platform, handle, owner_operator_id) values ('instagram_reels', 'own_ig', %s)", (owner.operator_id,))
    view, _ = connect(db, owner, "instagram_reels")
    assert system_rows(db, "select count(*) as n from accounts")[0]["n"] == 1 and view["handle"] == "own_ig"
    view2, _ = connect(db, owner, "tiktok")
    assert system_rows(db, "select handle, platform_account_id from accounts where platform = 'tiktok'") == [
        {"handle": "fake_tiktok", "platform_account_id": "fake-tiktok-1"}]
    assert view2["platform"] == "tiktok"


def test_a_handle_already_used_by_another_owners_account_is_refused(db, owner):
    other = second_owner(db)
    with db.session(Actor.system()) as conn:
        conn.execute("insert into accounts (platform, handle, owner_operator_id) values ('tiktok', 'fake_tiktok', %s)", (other.operator_id,))
    with pytest.raises(AppError) as exc:
        connect(db, owner, "tiktok")
    assert exc.value.code == "account_taken"


def test_unknown_platform_is_refused(db, owner):
    with db.session(owner) as conn:
        with pytest.raises(AppError) as exc:
            connections.start(conn, owner, "myspace")
    assert exc.value.code == "bad_platform"


def test_a_refused_code_connects_nothing_and_the_state_is_used_up(db, owner):
    oauth.FAKE_OAUTH["fail"] = "refused"
    with pytest.raises(AppError) as exc:
        connect(db, owner)
    assert exc.value.code == "oauth_failed"
    assert system_rows(db, "select 1 from platform_connections") == []
    assert system_rows(db, "select count(*) as n from oauth_states where used_at is not null")[0]["n"] == 1


def test_a_platform_outage_during_sign_in_is_reported_and_connects_nothing(db, owner):
    oauth.FAKE_OAUTH["fail"] = "transient"
    with pytest.raises(AppError):
        connect(db, owner)
    assert system_rows(db, "select 1 from platform_connections") == []


# ------------------------------------------------------------------ API

def test_the_api_connect_flow_redirects_to_profile_and_disconnects(db, owner):
    api._owner = owner
    client = TestClient(api.app, follow_redirects=False)
    start = client.post("/api/connections/tiktok/start")
    assert start.status_code == 200 and start.json()["url"].startswith("https://fake-oauth.test/tiktok/")
    state = parse_qs(urlparse(start.json()["url"]).query)["state"][0]
    cb = client.get("/api/connections/tiktok/callback", params={"code": "fake-code-9", "state": state})
    assert cb.status_code == 303 and cb.headers["location"] == "/profile?connected=tiktok"
    listing = client.get("/api/connections").json()
    assert [c["platform"] for c in listing["connections"]] == ["tiktok"] and listing["vault_ready"] is True
    done = client.post(f"/api/connections/{listing['connections'][0]['id']}/disconnect")
    assert done.status_code == 200 and client.get("/api/connections").json()["connections"] == []


def test_the_api_callback_reports_a_forged_state_a_cancel_and_a_bad_platform_in_plain_words(db, owner):
    api._owner = owner
    client = TestClient(api.app, follow_redirects=False)
    forged = client.get("/api/connections/tiktok/callback", params={"code": "fake-code-1", "state": "nope"})
    assert forged.status_code == 303 and "connect_error=" in forged.headers["location"] and "Start%20again" in forged.headers["location"]
    cancelled = client.get("/api/connections/tiktok/callback", params={"error": "access_denied"})
    assert "cancelled" in cancelled.headers["location"]
    assert "connect_error" in client.get("/api/connections/myspace/callback", params={"code": "c", "state": "s"}).headers["location"]
    assert client.get("/api/connections").json()["connections"] == []


def test_the_api_refuses_start_without_a_vault_key_with_a_plain_message(db, owner, monkeypatch):
    api._owner = owner
    monkeypatch.delenv("TOKEN_VAULT_KEY")
    r = TestClient(api.app).post("/api/connections/tiktok/start")
    assert r.status_code == 400 and r.json()["code"] == "vault_unavailable" and "TOKEN_VAULT_KEY" in r.json()["error"]


def test_the_api_disconnect_of_a_bad_id_is_a_plain_refusal(db, owner):
    api._owner = owner
    r = TestClient(api.app).post("/api/connections/not-a-uuid/disconnect")
    assert r.status_code == 400 and r.json()["code"] == "not_connected"


# ------------------------------------------------------------------ the real provider's own rules (no network)

def test_the_real_adapter_builds_authorize_urls_with_state_and_pkce_and_asks_for_missing_credentials(monkeypatch):
    adapter = oauth.PlatformOAuth({"config_json": {}})
    with pytest.raises(AppError) as exc:
        adapter.authorize_url("tiktok", "s", None, "https://x/cb")
    assert exc.value.code == "oauth_not_configured" and "TIKTOK_CLIENT_KEY" in str(exc.value)
    for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET", "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"):
        monkeypatch.setenv(name, "id-" + name)
    yt = parse_qs(urlparse(adapter.authorize_url("youtube_shorts", "STATE1", "CHAL", "https://x/cb")["url"]).query)
    assert yt["state"] == ["STATE1"] and yt["code_challenge"] == ["CHAL"] and yt["code_challenge_method"] == ["S256"] and yt["access_type"] == ["offline"]
    ig = parse_qs(urlparse(adapter.authorize_url("instagram_reels", "S2", None, "https://x/cb")["url"]).query)
    assert ig["state"] == ["S2"] and "instagram_business_content_publish" in ig["scope"][0]
    tt = parse_qs(urlparse(adapter.authorize_url("tiktok", "S3", None, "https://x/cb")["url"]).query)
    assert tt["state"] == ["S3"] and tt["client_key"] == ["id-TIKTOK_CLIENT_KEY"] and "video.upload" in tt["scope"][0]
    assert adapter.revoke("instagram_reels", "t") == {"ok": False}


def test_platform_answers_are_mapped_to_final_or_retryable_failures():
    reply = lambda status, body=None: SimpleNamespace(status_code=status, json=lambda: body)  # noqa: E731
    assert oauth._json(reply(200, {"a": 1})) == {"a": 1}
    with pytest.raises(AppError):
        oauth._json(reply(400, {}))
    with pytest.raises(oauth.TokenRevoked):
        oauth._json(reply(401, {}), oauth.TokenRevoked("tiktok"))
    with pytest.raises(AdapterError):
        oauth._json(reply(503, {}))
    with pytest.raises(AdapterError):
        oauth._json(reply(200, ["not", "a", "dict"]))

    def bad_json():
        raise ValueError

    with pytest.raises(AdapterError):
        oauth._json(SimpleNamespace(status_code=200, json=bad_json))
    assert oauth.PlatformOAuth._result({"access_token": "a", "scope": "x,y"}, 5, "h")["scopes"] == ["x", "y"]
    assert oauth.provider_name() == "fake"
