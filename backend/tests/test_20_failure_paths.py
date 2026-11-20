"""Phase E failure-path coverage: OAuth, publishing, and human-gate refusals."""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from urllib.parse import unquote

from clipping import api, core, vault
from clipping.alerts import registry
from clipping.adapters import oauth
from clipping.core import AppError
from clipping.db import Actor
from test_16_connections import connect
from test_17_publishing import REAL, real_http, req, video  # noqa: F401
from test_18_post_targets import ALL, TICKS, http, posting  # noqa: F401


def test_oauth_callback_rejects_bad_and_replayed_state(db, owner):
    api._owner = owner
    client = TestClient(api.app, follow_redirects=False)
    bad = client.get("/api/connections/youtube_shorts/callback", params={"code": "fake-code-1", "state": "forged"})
    assert bad.status_code == 303 and "Start again" in unquote(bad.headers["location"])
    _, state = connect(db, owner)
    replay = client.get("/api/connections/youtube_shorts/callback", params={"code": "fake-code-1", "state": state})
    assert replay.status_code == 303 and "already used" in unquote(replay.headers["location"])


def test_expired_and_revoked_tokens_fail_closed(db, owner):
    oauth.FAKE_OAUTH["expires_in"] = 60
    view, _ = connect(db, owner)
    with db.session(Actor.system()) as conn:
        conn.execute("update platform_connections set expires_at = now() - interval '1 minute'")
    oauth.FAKE_OAUTH["revoked"] = True
    with db.session(owner) as conn:
        with pytest.raises(vault.NeedsReconnect):
            vault.access_token(conn, view["id"])


def test_refresh_failure_does_not_retry_or_mark_a_transient_outage_as_revoked(db, owner):
    oauth.FAKE_OAUTH["expires_in"] = 60
    view, _ = connect(db, owner)
    oauth.FAKE_OAUTH["fail"] = "transient"
    with db.session(owner) as conn:
        conn.execute("update platform_connections set expires_at = now() - interval '1 minute'")
        with pytest.raises(AppError):
            vault.access_token(conn, view["id"])
        state = conn.execute("select state from platform_connections").fetchone()["state"]
    assert state == "connected"


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("slow"), httpx.ConnectError("down")])
def test_publish_http_failure_is_explicit_and_never_retried(db, owner, video, monkeypatch, failure):
    connection, _ = connect(db, owner)
    real_http(monkeypatch, [(lambda r: r.method == "POST" and "uploadType" in str(r.url), failure)])
    result, _ = publish_call(db, owner, connection["id"], video)
    assert result["state"] == "failed"


def publish_call(db, owner, connection, video):
    with db.session(owner) as conn:
        return registry.call(conn, "publishing", "publish_video", req(connection, "youtube_shorts", video),
                             owner.operator_id, provider=REAL)


def test_practice_campaign_is_refused_without_creating_targets(posting):
    with posting.db.session(Actor.system()) as conn:
        conn.execute("update campaigns set is_practice = true where id = %s", (posting.produced.campaign_id,))
    result = http(posting).post(
        f"/api/clips/{posting.clip}/post",
        json={"platforms": ALL, "ticks": {p: TICKS for p in ALL}, "request_id": "practice"},
    )
    assert result.status_code == 400 and result.json()["code"] == "practice_no_posting"
    assert posting.rows("select 1 from post_targets") == []


def test_post_without_owner_confirmation_is_refused(posting):
    result = http(posting).post(
        f"/api/clips/{posting.clip}/post",
        json={"platforms": ALL, "ticks": {}, "request_id": "missing-confirm"},
    )
    assert result.status_code == 400 and result.json()["code"] == "checklist_incomplete"


def test_kill_switch_blocks_publishing(posting):
    with posting.db.session(Actor.system()) as conn:
        conn.execute("update kill_switch set active = true")
    with pytest.raises(core.KillSwitchOn):
        posting.confirm()
    assert posting.rows("select 1 from post_targets") == []


def test_duplicate_confirm_taps_are_idempotent(posting):
    client = http(posting)
    body = {"platforms": ALL, "ticks": {p: TICKS for p in ALL}, "request_id": "same-tap"}
    first = client.post(f"/api/clips/{posting.clip}/post", json=body)
    second = client.post(f"/api/clips/{posting.clip}/post", json=body)
    assert first.status_code == second.status_code == 200
    assert second.json()["targets"] == first.json()["targets"]
    assert len(posting.rows("select 1 from post_targets")) == len(ALL)
