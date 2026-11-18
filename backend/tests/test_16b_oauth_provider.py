"""Phase E, phase 1: the real OAuth provider's requests and answer handling, over a mocked HTTP transport (no network)."""
from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

from clipping import connections
from clipping.adapters import oauth
from clipping.adapters.base import AdapterError
from clipping.core import AppError
from clipping.db import Actor

CREDS = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET", "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET")
REAL = oauth.PlatformOAuth({"config_json": {}})


def mock_platforms(monkeypatch, handler) -> list[httpx.Request]:
    for name in CREDS:
        monkeypatch.setenv(name, "id-" + name)
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(oauth, "http_client", lambda timeout=30: httpx.Client(transport=httpx.MockTransport(respond)))
    return seen


def form(request: httpx.Request) -> dict:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def test_youtube_exchange_sends_the_code_and_verifier_and_reads_the_channel(monkeypatch):
    def handler(request):
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "ya-1", "refresh_token": "rt-1", "expires_in": 3599, "scope": "a b"})
        assert request.headers["authorization"] == "Bearer ya-1"
        return httpx.Response(200, json={"items": [{"id": "UC1", "snippet": {"customUrl": "@chan"}}]})

    seen = mock_platforms(monkeypatch, handler)
    out = REAL.exchange_code("youtube_shorts", "the-code", "the-verifier", "https://x/cb")
    sent = form(seen[0])
    assert sent["code"] == "the-code" and sent["code_verifier"] == "the-verifier" and sent["grant_type"] == "authorization_code"
    assert out == {"access_token": "ya-1", "refresh_token": "rt-1", "expires_in": 3599, "refresh_expires_in": None, "scopes": ["a", "b"],
                   "external_account_id": "UC1", "handle": "@chan"}


def test_instagram_exchange_swaps_the_short_token_for_a_long_one_and_has_no_refresh_token(monkeypatch):
    def handler(request):
        if request.url.host == "api.instagram.com":
            return httpx.Response(200, json={"access_token": "short", "user_id": 77})
        if request.url.path == "/access_token":
            assert request.url.params["access_token"] == "short" and request.url.params["grant_type"] == "ig_exchange_token"
            return httpx.Response(200, json={"access_token": "long", "expires_in": 5184000})
        assert request.headers["authorization"] == "Bearer long"
        return httpx.Response(200, json={"user_id": "77", "username": "own_ig"})

    seen = mock_platforms(monkeypatch, handler)
    out = REAL.exchange_code("instagram_reels", "c", None, "https://x/cb")
    assert form(seen[0])["code"] == "c" and len(seen) == 3
    assert out["access_token"] == "long" and out["refresh_token"] is None and out["external_account_id"] == "77" and out["handle"] == "own_ig"
    assert out["expires_in"] == out["refresh_expires_in"] == 5184000


def test_tiktok_exchange_reads_open_id_and_keeps_going_when_the_name_lookup_fails(monkeypatch):
    def handler(request):
        if request.url.path.startswith("/v2/oauth/token"):
            return httpx.Response(200, json={"access_token": "tt-1", "refresh_token": "tr-1", "open_id": "oid-9", "expires_in": 86400,
                                             "refresh_expires_in": 31536000, "scope": "user.info.basic,video.upload"})
        return httpx.Response(500, json={})

    seen = mock_platforms(monkeypatch, handler)
    out = REAL.exchange_code("tiktok", "c", None, "https://x/cb")
    assert form(seen[0])["client_key"] == "id-TIKTOK_CLIENT_KEY" and form(seen[0])["code"] == "c"
    assert out["external_account_id"] == "oid-9" and out["handle"] is None and out["refresh_expires_in"] == 31536000
    assert out["scopes"] == ["user.info.basic", "video.upload"]


@pytest.mark.parametrize("platform", ["youtube_shorts", "instagram_reels", "tiktok"])
def test_a_refused_exchange_is_final_and_an_outage_is_retryable(monkeypatch, platform):
    mock_platforms(monkeypatch, lambda request: httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(AppError) as exc:
        REAL.exchange_code(platform, "c", "v", "https://x/cb")
    assert exc.value.code == "oauth_failed" and "invalid_grant" not in str(exc.value)
    mock_platforms(monkeypatch, lambda request: httpx.Response(503))
    with pytest.raises(AdapterError):
        REAL.exchange_code(platform, "c", "v", "https://x/cb")


def test_youtube_and_tiktok_refresh_send_the_refresh_token_and_read_the_answer(monkeypatch):
    seen = mock_platforms(monkeypatch, lambda request: httpx.Response(
        200, json={"access_token": "new", "refresh_token": "rot", "expires_in": 3600, "refresh_expires_in": 100}))
    yt = REAL.refresh("youtube_shorts", "rt-1", "old")
    assert form(seen[0])["grant_type"] == "refresh_token" and form(seen[0])["refresh_token"] == "rt-1"
    assert yt == {"access_token": "new", "refresh_token": "rot", "expires_in": 3600, "refresh_expires_in": 100}
    tt = REAL.refresh("tiktok", "tr-1", "old")
    assert form(seen[1])["refresh_token"] == "tr-1" and form(seen[1])["client_key"] == "id-TIKTOK_CLIENT_KEY" and tt["access_token"] == "new"


def test_instagram_refreshes_the_long_lived_token_with_itself(monkeypatch):
    seen = mock_platforms(monkeypatch, lambda request: httpx.Response(200, json={"access_token": "new", "expires_in": 5184000}))
    out = REAL.refresh("instagram_reels", None, "long-old")
    assert seen[0].url.params["grant_type"] == "ig_refresh_token" and seen[0].url.params["access_token"] == "long-old"
    assert out["access_token"] == "new" and out["refresh_token"] is None and out["refresh_expires_in"] == 5184000


@pytest.mark.parametrize("platform", ["youtube_shorts", "instagram_reels", "tiktok"])
@pytest.mark.parametrize("status", [400, 401])
def test_a_refresh_the_platform_refuses_raises_token_revoked(monkeypatch, platform, status):
    mock_platforms(monkeypatch, lambda request: httpx.Response(status, json={"error": "invalid_grant"}))
    with pytest.raises(oauth.TokenRevoked) as exc:
        REAL.refresh(platform, "rt", "at")
    assert exc.value.code == "token_revoked"


def test_a_refresh_outage_is_retryable_not_a_revoke(monkeypatch):
    mock_platforms(monkeypatch, lambda request: httpx.Response(502))
    with pytest.raises(AdapterError):
        REAL.refresh("tiktok", "rt", "at")


def test_revoke_posts_the_token_to_google_and_tiktok_and_skips_instagram(monkeypatch):
    seen = mock_platforms(monkeypatch, lambda request: httpx.Response(200, json={}))
    assert REAL.revoke("youtube_shorts", "tok-y") == {"ok": True}
    assert seen[0].url.host == "oauth2.googleapis.com" and seen[0].url.path == "/revoke" and form(seen[0]) == {"token": "tok-y"}
    assert REAL.revoke("tiktok", "tok-t") == {"ok": True}
    assert seen[1].url.path == "/v2/oauth/revoke/" and form(seen[1])["token"] == "tok-t" and form(seen[1])["client_key"] == "id-TIKTOK_CLIENT_KEY"
    assert REAL.revoke("instagram_reels", "tok-i") == {"ok": False} and len(seen) == 2


def test_a_unique_violation_race_names_the_other_owner_case_and_the_own_duplicate_case(db, owner):
    """identity_taken misses the other owner's row at first (as in a race); the unique index then fires and the message is still right."""
    from clipping import api  # noqa: F401  (imports the adapters)
    from test_16_connections import connect, second_owner

    connect(db, owner)
    other = second_owner(db)
    result = {"access_token": "t", "external_account_id": "fake-youtube_shorts-1", "handle": "someone_else"}
    with db.session(other) as conn:
        real = conn.execute
        seen = {"n": 0}

        def execute(sql, *args, **kw):
            if "app.identity_taken" in str(sql):
                seen["n"] += 1
                if seen["n"] == 1:                        # the first look misses it
                    return SimpleNamespace(fetchone=lambda: {"t": False})
            return real(sql, *args, **kw)

        proxy = SimpleNamespace(execute=execute, transaction=conn.transaction)
        with pytest.raises(AppError) as exc:
            connections._save(proxy, Actor(other.operator_id, "operator"), "youtube_shorts", None, result)
    assert exc.value.code == "account_taken" and "someone else" in str(exc.value)
