"""Phase B (round 2): footage from the phone and from links, accounts per niche, passkey sign-in, keep-awake."""
from __future__ import annotations

import base64
import http.server
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import sys

import pytest

from clipping import auth, campaigns, ffmpeg, ops, pipeline, sources
from clipping.core import AppError, GateError
from clipping.db import Actor
from conftest import add_accounts, approved_campaign, brief


# ------------------------------------------------------------------ links

@pytest.mark.parametrize("url,kind,direct", [
    ("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=sharing", "drive",
     "https://drive.usercontent.google.com/download?id=1AbCdEfGhIjKlMnOp&export=download&confirm=t"),
    ("https://drive.google.com/open?id=1AbCdEfGhIjKlMnOp", "drive",
     "https://drive.usercontent.google.com/download?id=1AbCdEfGhIjKlMnOp&export=download&confirm=t"),
    ("https://www.dropbox.com/s/abc123/episode.mp4?dl=0", "dropbox", "https://www.dropbox.com/s/abc123/episode.mp4?dl=1"),
    ("https://cdn.example.com/files/episode.mp4", "direct", "https://cdn.example.com/files/episode.mp4"),
])
def test_share_links_become_direct_downloads(url, kind, direct):
    assert sources.link_kind(url) == kind and sources.direct_download_url(url) == direct


def test_youtube_links_are_recognised():
    for url in ("https://www.youtube.com/watch?v=x", "https://youtu.be/x", "https://m.youtube.com/watch?v=x"):
        assert sources.link_kind(url) == "youtube"


def test_link_rules(db, owner, monkeypatch):
    monkeypatch.setitem(sys.modules, "yt_dlp", None)   # the case below assumes yt-dlp is not installed, whatever is in the venv
    with db.session(owner) as conn:
        draft = campaigns.intake(conn, owner, "content_rewards", "Draft", brief("01"))["campaign"]["id"]
    campaign_id = approved_campaign(db, owner)
    cases = [
        (draft, "https://cdn.example.com/a.mp4", "P-1", False, GateError),
        (campaign_id, "https://cdn.example.com/a.mp4", "", False, "no_permission"),
        (campaign_id, "ftp://example.com/a.mp4", "P-1", False, "bad_link"),
        (campaign_id, "https://drive.google.com/drive/folders/abc", "P-1", False, "bad_link"),
        (campaign_id, "https://youtu.be/abc", "P-1", False, "youtube_needs_ok"),
        (campaign_id, "https://youtu.be/abc", "P-1", True, "youtube_unavailable"),   # yt-dlp is optional and not installed
    ]
    for cid, url, perm, yt, expected in cases:
        with pytest.raises(AppError) as err:
            with db.session(owner) as conn:
                sources.queue_link(conn, owner, cid, url, "en", perm, yt)
        if isinstance(expected, str):
            assert err.value.code == expected, url
        else:
            assert isinstance(err.value, expected)
    with db.session(owner) as conn:
        job = sources.queue_link(conn, owner, campaign_id, "https://cdn.example.com/a.mp4", "en", "P-1")
        assert job["kind"] == "direct"
        with pytest.raises(AppError):   # the same link twice is one job
            sources.queue_link(conn, owner, campaign_id, "https://cdn.example.com/a.mp4", "en", "P-1")
        assert conn.execute("select type from jobs").fetchall() == [{"type": "fetch_source"}]


@pytest.mark.parametrize("url", [
    "http://cdn.example.com/a.mp4", "https://127.0.0.1:18787/api/health", "https://localhost/a.mp4", "https://[::1]/a.mp4",
    "https://169.254.169.254/latest/meta-data/", "https://10.0.0.5/a.mp4", "https://192.168.1.2/a.mp4", "https://100.64.0.1/a.mp4",
    "https://printer.local/a.mp4", "https://user:pw@cdn.example.com/a.mp4", "https://[::ffff:127.0.0.1]/a.mp4", "https://0.0.0.0/a.mp4",
])
def test_link_intake_refuses_non_https_and_private_hosts(url):
    with pytest.raises(AppError) as err:
        sources.check_public_https(url)
    assert err.value.code in ("bad_link", "private_address")


def test_a_name_that_resolves_to_a_private_address_is_refused(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.1.2.3", 443))])
    with pytest.raises(AppError) as err:
        sources.check_public_https("https://rebind.example.com/a.mp4")
    assert err.value.code == "private_address"
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    sources.check_public_https("https://cdn.example.com/a.mp4")


def test_a_redirect_to_an_internal_address_is_refused_per_hop(monkeypatch, tmp_path):
    import httpx
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://169.254.169.254/latest/meta-data/"})

    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(AppError) as err:
        sources.download_to("https://cdn.example.com/a.mp4", tmp_path / "x.mp4")
    assert err.value.code == "private_address" and seen == ["https://cdn.example.com/a.mp4"]


class _Files(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture
def file_server(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPPING_ALLOW_LOCAL_FETCH", "1")   # the test server is http on loopback, which the SSRF guard refuses
    video = ffmpeg.make_fixture(tmp_path / "clip.mp4", seconds=6)
    (tmp_path / "page.html").write_text("<html>sign in</html>", encoding="utf-8")
    handler = lambda *a, **k: _Files(*a, directory=str(tmp_path), **k)  # noqa: E731
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", video
    server.shutdown()


def test_fetch_job_downloads_registers_and_starts_the_pipeline(db, owner, file_server):
    base, video = file_server
    campaign_id = approved_campaign(db, owner)
    with db.session(owner) as conn:
        sources.queue_link(conn, owner, campaign_id, f"{base}/clip.mp4", "en", "P-LINK")
    assert pipeline.process_one(db)
    with db.session(owner) as conn:
        source = conn.execute("select * from source_assets").fetchone()
        jobs = {r["type"]: r["status"] for r in conn.execute("select type, status from jobs").fetchall()}
    assert source["file_hash"] == ffmpeg.sha256_file(video) and source["origin_url"] == f"{base}/clip.mp4"
    assert source["permission_ref"] == "P-LINK" and jobs == {"fetch_source": "done", "transcribe": "queued"}


def test_a_web_page_instead_of_a_file_is_refused(db, owner, file_server):
    base, _ = file_server
    campaign_id = approved_campaign(db, owner)
    with db.session(owner) as conn:
        sources.queue_link(conn, owner, campaign_id, f"{base}/page.html", "en", "P-1")
    pipeline.process_one(db)
    with db.session(owner) as conn:
        job = conn.execute("select status, error from jobs").fetchone()
        assert job["status"] == "failed" and "web page" in job["error"]
        assert conn.execute("select count(*) as n from source_assets").fetchone()["n"] == 0
        assert conn.execute("select count(*) as n from alerts where type = 'job_failed'").fetchone()["n"] == 1


# ------------------------------------------------------------------ uploads

def test_upload_from_the_phone_through_the_api(db, owner, tmp_path):
    from fastapi.testclient import TestClient

    from clipping import api

    api._owner = owner
    campaign_id = approved_campaign(db, owner)
    video = ffmpeg.make_fixture(tmp_path / "phone.mov", seconds=6)
    client = TestClient(api.app)
    with open(video, "rb") as fh:
        response = client.post(f"/api/campaigns/{campaign_id}/sources/upload",
                               files={"file": ("IMG_0042.mov", fh, "video/quicktime")}, data={"language": "en", "permission_ref": "P-UP"})
    assert response.status_code == 200, response.text
    assert response.json()["origin_url"] == "upload:IMG_0042.mov"
    not_video = client.post(f"/api/campaigns/{campaign_id}/sources/upload",
                            files={"file": ("notes.mp4", b"not a video", "video/mp4")}, data={"language": "en", "permission_ref": "P-UP"})
    assert not_video.status_code == 400 and not_video.json()["code"] == "not_a_video"
    no_permission = client.post(f"/api/campaigns/{campaign_id}/sources/upload",
                                files={"file": ("a.mp4", open(video, "rb"), "video/mp4")}, data={"language": "en"})
    assert no_permission.status_code == 400
    assert not list((Path(ops.config.data_dir()) / "incoming").glob("*"))   # temporary files are cleaned up


# ------------------------------------------------------------------ accounts per niche

def test_clips_go_to_the_account_for_the_campaign_niche(db, owner):
    add_accounts(db, owner)   # no niche: usable for any niche
    with db.session(owner) as conn:
        gaming = conn.execute("insert into accounts (platform, handle, niche, owner_operator_id) values ('tiktok', 'game_tt', 'gaming', %s) returning id",
                              (owner.operator_id,)).fetchone()["id"]
        podcast = conn.execute("insert into accounts (platform, handle, niche, owner_operator_id) values ('tiktok', 'pod_tt', 'podcast', %s) returning id",
                               (owner.operator_id,)).fetchone()["id"]
        assert pipeline.pick_account(conn, owner.operator_id, ["tiktok"], "podcast")["id"] == podcast
        assert pipeline.pick_account(conn, owner.operator_id, ["tiktok"], "gaming")["id"] == gaming
        other = pipeline.pick_account(conn, owner.operator_id, ["tiktok"], "lifestyle")
        assert other["id"] not in (podcast, gaming)   # falls back to the account with no niche


def test_api_adds_accounts_with_a_niche_and_refuses_duplicates(db, owner):
    from fastapi.testclient import TestClient

    from clipping import api

    api._owner = owner
    client = TestClient(api.app)
    assert client.post("/api/accounts", json={"platform": "tiktok", "handle": "@pod_one", "niche": "podcast"}).json()["niche"] == "podcast"
    assert client.post("/api/accounts", json={"platform": "tiktok", "handle": "pod_one"}).status_code == 400
    assert client.post("/api/accounts", json={"platform": "myspace", "handle": "x"}).status_code == 400
    assert client.get("/api/reference").json()["accounts"][0]["niche"] == "podcast"


# ------------------------------------------------------------------ passkey sign-in

ORIGIN = "http://localhost:3000"


def _credential(challenge: str, cred_id: str = "Y3JlZC0x") -> dict:
    client_data = base64.urlsafe_b64encode(json.dumps({"challenge": challenge}).encode()).decode().rstrip("=")
    return {"id": cred_id, "rawId": cred_id, "type": "public-key", "response": {"clientDataJSON": client_data}}


@pytest.fixture
def passkey_mode(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "passkey")
    monkeypatch.setattr(auth.webauthn, "verify_registration_response",
                        lambda **kw: SimpleNamespace(credential_id=b"cred-1", credential_public_key=b"public-key", sign_count=0))
    monkeypatch.setattr(auth.webauthn, "verify_authentication_response", lambda **kw: SimpleNamespace(new_sign_count=1))


def test_signin_flow_with_setup_code(db, owner, passkey_mode):
    from fastapi.testclient import TestClient

    from clipping import api

    client = TestClient(api.app, headers={"origin": ORIGIN})
    assert client.get("/api/home").status_code == 401                       # nothing without a session
    assert client.get("/api/auth/status").json() == {"mode": "passkey", "signed_in": False, "has_passkeys": False}
    code = auth.startup_code(db)
    assert code and len(code) == 11
    assert client.post("/api/auth/register/options", json={}).status_code == 401
    assert client.post("/api/auth/register/options", json={"setup_code": "WRONG-CODE0"}).status_code == 400
    bad_origin = TestClient(api.app, headers={"origin": "https://evil.example"})
    assert bad_origin.post("/api/auth/register/options", json={"setup_code": code}).json()["code"] == "bad_origin"

    options = client.post("/api/auth/register/options", json={"setup_code": code}).json()
    assert options["rp"]["id"] == "localhost" and options["challenge"]
    done = client.post("/api/auth/register/verify", json={"credential": _credential(options["challenge"]), "setup_code": code})
    assert done.status_code == 200 and auth.COOKIE in done.cookies
    assert client.get("/api/home").status_code == 200                       # signed in by the cookie
    reuse = client.post("/api/auth/register/options", json={"setup_code": code})
    assert reuse.status_code == 200   # a signed-in device may add passkeys; the code itself is spent:
    anonymous = TestClient(api.app, headers={"origin": ORIGIN})
    assert anonymous.post("/api/auth/register/options", json={"setup_code": code}).status_code == 400

    phone_code = client.post("/api/auth/device-code", json={}).json()["code"]
    assert phone_code != code and anonymous.post("/api/auth/device-code", json={}).status_code == 401

    client.post("/api/auth/logout", json={})
    client.cookies.clear()
    assert client.get("/api/home").status_code == 401
    login = client.post("/api/auth/login/options", json={}).json()
    signed = client.post("/api/auth/login/verify", json={"credential": _credential(login["challenge"], "Y3JlZC0x")})
    assert signed.status_code == 200 and client.get("/api/home").status_code == 200
    replay = client.post("/api/auth/login/verify", json={"credential": _credential(login["challenge"], "Y3JlZC0x")})
    assert replay.status_code == 400   # a challenge works once
    with db.session(Actor.system()) as conn:
        stored = conn.execute("select * from auth_credentials").fetchone()
        sessions = conn.execute("select token_hash from auth_sessions").fetchall()
        assert stored["public_key"] and stored["rp_id"] == "localhost"
        assert all(len(s["token_hash"]) == 64 for s in sessions)              # only hashes of session tokens are stored
        assert conn.execute("select count(*) as n from audit_log where action = 'auth.passkey_added'").fetchone()["n"] == 1


def test_no_startup_code_once_a_passkey_exists_or_in_local_mode(db, owner, passkey_mode, monkeypatch):
    with db.session(Actor.system()) as conn:
        conn.execute("insert into auth_credentials (operator_id, credential_id, public_key, rp_id) values (%s, 'c', 'k', 'localhost')",
                     (owner.operator_id,))
    assert auth.startup_code(db) is None
    monkeypatch.setenv("AUTH_MODE", "local")
    assert auth.startup_code(db) is None


# ------------------------------------------------------------------ keep-awake

def test_keep_awake_follows_the_work(db, owner, monkeypatch):
    ops._awake["on"] = False
    assert ops.has_work(db) is False
    campaign_id = approved_campaign(db, owner)
    with db.session(owner) as conn:
        sources.queue_link(conn, owner, campaign_id, "https://cdn.example.com/a.mp4", "en", "P-1")
    assert ops.has_work(db) is True
    assert ops.keep_awake(True) is True and ops.keep_awake(True) is False
    assert ops.keep_awake(False) is True
