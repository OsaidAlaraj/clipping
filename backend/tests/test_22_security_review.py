"""Security review fixes (docs/security-review.md): secret redaction, cross-site writes, upload and link host checks."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from clipping import api, config, publish
from clipping.adapters import publishing


def test_oauth_client_secrets_and_the_vault_key_are_scrubbed_from_error_text(monkeypatch):
    for name in ("TOKEN_VAULT_KEY", "GOOGLE_CLIENT_SECRET", "INSTAGRAM_APP_SECRET", "TIKTOK_CLIENT_SECRET"):
        monkeypatch.setenv(name, f"value-of-{name.lower()}-123456")
        text = f"ConnectError for https://x.test/?client_secret=value-of-{name.lower()}-123456"
        assert f"value-of-{name.lower()}" not in config.redact(text) and f"<{name}>" in config.redact(text)


def test_a_write_from_another_site_is_refused_before_any_handler_runs(db, owner):
    api._owner = owner
    client = TestClient(api.app)
    evil = client.post("/api/targets/00000000-0000-0000-0000-000000000000/cancel", headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403 and evil.json()["code"] == "bad_origin"
    null = client.post("/api/connections/00000000-0000-0000-0000-000000000000/disconnect", headers={"Origin": "null"})
    assert null.status_code == 403
    own = client.post("/api/connections/00000000-0000-0000-0000-000000000000/disconnect", headers={"Origin": "http://localhost:3000"})
    assert own.status_code == 400 and own.json()["code"] == "not_connected"      # reached the handler, refused for its own reason
    assert client.post("/api/connections/00000000-0000-0000-0000-000000000000/disconnect").status_code == 400   # no Origin: not a browser
    assert client.get("/api/connections", headers={"Origin": "https://evil.example"}).status_code == 200      # reads are not writes


@pytest.mark.parametrize("url", ["https://open-upload.tiktokapis.com/video/?upload_id=1", "https://a.b.tiktokapis.com/x"])
def test_tiktok_upload_addresses_on_tiktoks_own_domain_are_accepted(url):
    assert publishing._tiktok_upload_ok(url)


@pytest.mark.parametrize("url", [
    "https://evil.example/?x.tiktokapis.com", "https://evil.example#.tiktokapis.com", r"https://evil.example\.tiktokapis.com",
    "http://open-upload.tiktokapis.com/video", "https://tiktokapis.com/video", "https://x.tiktokapis.com.evil.example/",
    "https://user@evil.example/.tiktokapis.com", "https://open-upload.tiktokapis.com:bad/x", "", None, 5])
def test_tiktok_upload_addresses_elsewhere_are_refused(url):
    assert not publishing._tiktok_upload_ok(url)


def test_a_post_link_from_the_platform_answer_must_be_on_that_platforms_own_site():
    good = {"state": "published", "url": "https://www.youtube.com/shorts/abc", "platform_post_id": "abc"}
    assert publish._clean_outcome(good, "youtube_shorts")["state"] == "published"
    off = publish._clean_outcome({**good, "url": "https://evil.example/shorts/abc"}, "youtube_shorts")
    assert off["state"] == "needs_check" and off["url"] is None
    assert publish._clean_outcome({**good, "url": "https://youtube.com.evil.example/x"}, "youtube_shorts")["url"] is None


def test_a_malformed_clip_id_is_a_plain_refusal_on_every_posting_endpoint(db, owner):
    api._owner = owner
    client = TestClient(api.app, raise_server_exceptions=False)
    calls = [client.get("/api/clips/not-a-uuid/checklist"), client.get("/api/clips/not-a-uuid/post-options"),
             client.post("/api/clips/not-a-uuid/publish-approve", json={"ticks": {}}),
             client.post("/api/clips/not-a-uuid/schedule", json={"scheduled_at": "2026-01-01T10:00", "channel_id": "c", "confirm": True}),
             client.post("/api/clips/not-a-uuid/posted", json={"post_url": "https://youtube.com/x", "confirm": True, "disclosure_toggle_on": True})]
    assert [r.status_code for r in calls] == [400] * 5 and all(r.json()["code"] == "not_found" for r in calls)


def test_a_malformed_id_in_the_address_is_a_400_not_a_server_error(db, owner):
    api._owner = owner
    client = TestClient(api.app)
    for path in ("/api/clips/not-an-id", "/api/campaigns/zzz"):
        reply = client.get(path)
        assert reply.status_code == 400 and reply.json()["code"] == "bad_value"
