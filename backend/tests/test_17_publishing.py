"""Phase E, phase 2: the publish adapters. The fake provider is the real adapter over fixture answers; the real provider is
driven over httpx.MockTransport against the documented request and response shapes. No network."""
from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

from clipping import vault
from clipping.adapters import base, oauth, publishing
from clipping.adapters.base import AdapterUnavailable
from clipping.alerts import registry
from clipping.core import AppError
from clipping.db import Actor
from test_16_connections import connect, second_owner

PLATFORMS = ("youtube_shorts", "instagram_reels", "tiktok")
STATES = {"scheduled", "sending", "processing", "published", "draft", "private", "needs_check", "failed", "cancelled"}
KEYS = {"state", "platform_post_id", "url", "reason"}
REAL = "platforms"


# ------------------------------------------------------------------ helpers

@pytest.fixture
def video(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"0123456789")
    return str(path)


def conn_id(db, owner, platform) -> str:
    view, _ = connect(db, owner, platform)
    return str(view["id"])


def req(connection_id, platform, video, caption="A caption #clip", options=None) -> dict:
    return {"connection_id": connection_id, "platform": platform, "file_path": video, "caption": caption,
            "options": {"paid_partnership": False, "privacy": "public", **(options or {})}}


def call(db, owner, method, request, provider="fake"):
    with db.session(owner) as conn:
        result, _ = registry.call(conn, "publishing", method, request, owner.operator_id, provider=provider)
    return result


def publish(db, owner, platform, video, provider="fake", **kw):
    connection = conn_id(db, owner, platform)
    return connection, call(db, owner, "publish_video", req(connection, platform, video, **kw), provider)


def status(db, owner, connection, platform, post_id, provider="fake"):
    return call(db, owner, "check_status", {"connection_id": connection, "platform": platform, "platform_post_id": post_id}, provider)


def set_config(db, provider, patch: dict) -> None:
    with db.session(Actor.system()) as conn:
        conn.execute("update adapters set config_json = config_json || %s::jsonb where stage = 'publishing' and provider = %s", (json.dumps(patch), provider))


def sql(db, query, *args):
    with db.session(Actor.system()) as conn:
        cur = conn.execute(query, args)
        return cur.fetchall() if cur.description else []


def alerts_of(db, type_):
    return sql(db, "select * from alerts where type = %s", type_)


def is_(method, needle):
    return lambda r: r.method == method and needle in str(r.url)


def real_http(monkeypatch, overrides=(), db=None):
    """The real provider over a mock transport: the fixture answers unless an override matches. Returns the request log."""
    seen: list[httpx.Request] = []
    happy = publishing.FakePublishing({"config_json": {}})._serve

    def handler(request):
        seen.append(request)
        for match, answer in overrides:
            if match(request):
                if isinstance(answer, Exception):
                    raise answer
                return answer(request) if callable(answer) else answer
        return happy(request)

    monkeypatch.setattr(publishing, "http_client", lambda timeout=0: httpx.Client(transport=httpx.MockTransport(handler)))
    return seen


def real_ready(db):
    set_config(db, REAL, {"poll_interval_s": 0, "poll_attempts": 3})


def body(request) -> dict:
    return json.loads(request.content)


def form(request) -> dict:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


# ------------------------------------------------------------------ DW-2.1

@pytest.mark.parametrize("platform, state, has_url", [("youtube_shorts", "private", True), ("instagram_reels", "published", True), ("tiktok", "draft", False)])
def test_DW_2_1_each_fake_platform_returns_its_documented_state_in_the_contract_shape(db, owner, video, platform, state, has_url):
    _, result = publish(db, owner, platform, video)
    assert set(result) == KEYS and result["state"] == state and result["state"] in STATES
    assert isinstance(result["platform_post_id"], str) and bool(result["url"]) is has_url
    assert (result["reason"] is None) or isinstance(result["reason"], str)


def test_DW_2_1_instagram_url_is_the_permalink_and_youtube_url_a_shorts_link(db, owner, video):
    assert publish(db, owner, "instagram_reels", video)[1]["url"] == "https://www.instagram.com/reel/FkReel123/"
    assert publish(db, owner, "youtube_shorts", video)[1]["url"] == "https://www.youtube.com/shorts/FkVid_abc12"


def test_DW_2_1_instagram_container_still_processing_is_processing_then_finish_publish_posts_it_once(db, owner, video):
    publishing.FAKE_PUBLISHING["mode"]["instagram_reels"] = "processing"
    connection, result = publish(db, owner, "instagram_reels", video)
    assert result["state"] == "processing" and result["platform_post_id"] == "17889455560051444"
    polls = [c for c in publishing.FAKE_PUBLISHING["calls"] if c[0] == "instagram_reels" and c[1].startswith("GET")]
    assert len(polls) == 3                                                        # poll_attempts, then it stops waiting
    assert not any(c[1].endswith("/media_publish") for c in publishing.FAKE_PUBLISHING["calls"])
    publishing.FAKE_PUBLISHING["mode"].clear()
    done = call(db, owner, "finish_publish", {"connection_id": connection, "platform": "instagram_reels", "platform_post_id": result["platform_post_id"]})
    assert done["state"] == "published" and done["url"] and done["platform_post_id"] == "17900000000000001"
    assert sum(c[1].endswith("/media_publish") for c in publishing.FAKE_PUBLISHING["calls"]) == 1


@pytest.mark.parametrize("platform, wanted, state", [
    ("youtube_shorts", "processing", "processing"), ("youtube_shorts", "failed", "failed"), ("youtube_shorts", None, "private"),
    ("instagram_reels", "processing", "processing"), ("instagram_reels", "failed", "failed"), ("instagram_reels", "complete", "published"),
    ("tiktok", "processing", "processing"), ("tiktok", "failed", "failed"), ("tiktok", None, "draft"), ("tiktok", "complete", "published")])
def test_DW_2_1_check_status_returns_the_same_shape_for_every_platform(db, owner, video, platform, wanted, state):
    connection, posted = publish(db, owner, platform, video)
    if wanted:
        publishing.FAKE_PUBLISHING["status"][platform] = wanted
    result = status(db, owner, connection, platform, posted["platform_post_id"])
    assert set(result) == KEYS and result["state"] == state
    if state == "failed":
        assert result["reason"]


def test_provider_name_follows_the_fakes_switch(monkeypatch):
    assert publishing.provider_name() == "fake"
    monkeypatch.setenv("CLIPPING_USE_FAKES", "0")
    assert publishing.provider_name() == "platforms"


def test_both_publishing_rows_are_seeded_inactive_without_secrets(db, owner):
    rows = sql(db, "select * from adapters where stage = 'publishing' order by provider")
    assert [r["provider"] for r in rows] == ["fake", "platforms"] and not any(r["active"] for r in rows)
    cfg = rows[1]["config_json"]
    assert cfg["unverified_project"] is True and cfg["paid_partnership_supported"]["instagram_reels"] is False
    assert cfg["limits"]["youtube_shorts"]["units_per_upload"] == 1600 and cfg["limits"]["instagram_reels"]["max_units_24h"] == 100
    assert not any(word in json.dumps(cfg).lower() for word in ("secret", "token", "key"))


# ------------------------------------------------------------------ DW-2.2

def test_DW_2_2_timeout_mid_upload_is_needs_check_with_exactly_one_upload_call_and_no_retry(db, owner, video, monkeypatch):
    seen = real_http(monkeypatch, [(is_("PUT", "upload_id"), httpx.ReadTimeout("slow"))])
    connection, result = publish(db, owner, "youtube_shorts", video, REAL)
    assert result["state"] == "needs_check" and result["reason"] and result["platform_post_id"] is None
    assert [r.method for r in seen] == ["POST", "PUT"]                            # one init, one upload attempt, nothing more


@pytest.mark.parametrize("platform", PLATFORMS)
def test_DW_2_2_every_fake_platform_timing_out_mid_upload_makes_one_attempt(db, owner, video, platform):
    publishing.FAKE_PUBLISHING["mode"][platform] = "timeout"
    _, result = publish(db, owner, platform, video)
    assert result["state"] == "needs_check"
    steps = [c[1] for c in publishing.FAKE_PUBLISHING["calls"] if c[0] == platform]
    assert len(steps) == 2 and len(set(steps)) == 2                               # create the upload, send it, stop
    assert len(sql(db, "select 1 from publish_quota_log")) == 1                   # the attempt counted against the allowance once


def test_DW_2_2_instagram_post_id_of_the_container_is_kept_when_the_upload_is_ambiguous(db, owner, video):
    publishing.FAKE_PUBLISHING["mode"]["instagram_reels"] = "timeout"
    assert publish(db, owner, "instagram_reels", video)[1]["platform_post_id"] == "17889455560051444"


@pytest.mark.parametrize("platform, needle", [("youtube_shorts", "exceeded your quota"), ("instagram_reels", "Invalid parameter"), ("tiktok", "Too many pending")])
def test_DW_2_2_a_4xx_before_any_bytes_is_a_safe_failure_with_the_platforms_reason(db, owner, video, platform, needle):
    publishing.FAKE_PUBLISHING["mode"][platform] = "refuse"
    _, result = publish(db, owner, platform, video)
    assert result["state"] == "failed" and needle in result["reason"] and result["platform_post_id"] is None
    assert len(publishing.FAKE_PUBLISHING["calls"]) == 1                          # only the refused request
    assert sql(db, "select 1 from publish_quota_log") == []                       # nothing was uploaded, nothing counted


def test_DW_2_2_a_5xx_or_network_error_before_the_upload_is_a_safe_failure(db, owner, video, monkeypatch):
    connection = conn_id(db, owner, "youtube_shorts")
    for failure in (httpx.Response(503), httpx.ConnectError("down")):
        seen = real_http(monkeypatch, [(is_("POST", "uploadType"), failure)])
        result = call(db, owner, "publish_video", req(connection, "youtube_shorts", video), REAL)
        assert result["state"] == "failed" and len(seen) == 1


def test_DW_2_2_a_4xx_during_the_upload_is_still_needs_check(db, owner, video, monkeypatch):
    real_http(monkeypatch, [(is_("PUT", "upload_id"), httpx.Response(400, json={"error": {"message": "bad bytes"}}))])
    result = publish(db, owner, "youtube_shorts", video, REAL)[1]
    assert result["state"] == "needs_check"                                       # bytes were sent: we cannot be sure nothing was created


def test_DW_2_2_instagram_publish_failing_is_needs_check_with_the_container_id(db, owner, video, monkeypatch):
    seen = real_http(monkeypatch, [(is_("POST", "media_publish"), httpx.ReadTimeout("slow"))])
    real_ready(db)
    result = publish(db, owner, "instagram_reels", video, REAL)[1]
    assert result["state"] == "needs_check" and result["platform_post_id"] == "17889455560051444"
    assert sum("media_publish" in str(r.url) for r in seen) == 1


def test_DW_2_2_the_registry_never_retries_an_unexpected_publish_failure(db, owner, video, monkeypatch):
    calls = []

    class Boom(publishing.PlatformPublishing):
        provider = "boom"

        def publish_video(self, **kw):
            calls.append(1)
            raise RuntimeError("worker died")

    monkeypatch.setitem(base.PROVIDERS, ("publishing", "boom"), Boom)
    with db.session(Actor.system()) as conn:
        conn.execute("insert into adapters (stage, provider, fallback_provider, is_fake) values ('publishing', 'boom', 'fake', true)")
    with db.session(owner) as conn:                                               # caught inside the session so the alert commits
        with pytest.raises(AdapterUnavailable) as info:
            registry.call(conn, "publishing", "publish_video", req("x", "youtube_shorts", video), owner.operator_id, provider="boom")
    assert len(calls) == 1 and "not retried" in str(info.value)                   # no 3 attempts, no fallback to another provider
    assert alerts_of(db, "adapter_failed")


def test_other_methods_still_retry_three_times(db, owner, monkeypatch):
    calls = []

    class Flaky(publishing.PlatformPublishing):
        provider = "flaky"

        def check_status(self, **kw):
            calls.append(1)
            raise RuntimeError("blip")

    monkeypatch.setitem(base.PROVIDERS, ("publishing", "flaky"), Flaky)
    with db.session(Actor.system()) as conn:
        conn.execute("insert into adapters (stage, provider, is_fake) values ('publishing', 'flaky', true)")
    with pytest.raises(AdapterUnavailable):
        call(db, owner, "check_status", {"connection_id": "x", "platform": "tiktok", "platform_post_id": "abc"}, "flaky")
    assert len(calls) == 3


# ------------------------------------------------------------------ DW-2.3

def test_DW_2_3_youtube_is_forced_private_while_the_project_is_unverified_and_sets_the_paid_flag(db, owner, video, monkeypatch):
    seen = real_http(monkeypatch)
    connection, result = publish(db, owner, "youtube_shorts", video, REAL, options={"paid_partnership": True, "privacy": "public"})
    init = seen[0]
    sent = body(init)
    assert sent["status"]["privacyStatus"] == "private"                           # public was asked for, the unverified project wins
    assert sent["paidProductPlacementDetails"] == {"hasPaidProductPlacement": True}
    assert "paidProductPlacementDetails" in init.url.params["part"] and init.url.params["uploadType"] == "resumable"
    assert init.headers["x-upload-content-length"] == "10" and init.headers["authorization"] == f"Bearer {oauth.FAKE_OAUTH['issued'][0]}"
    assert init.url.path == "/upload/youtube/v3/videos"
    upload = seen[1]
    assert upload.method == "PUT" and upload.headers["content-length"] == "10" and upload.content == b"0123456789"
    assert result["state"] == "private" and "audit" in result["reason"]


def test_DW_2_3_youtube_uploads_public_once_the_project_is_verified_and_the_owner_asked_for_it(db, owner, video, monkeypatch):
    seen = real_http(monkeypatch)
    set_config(db, REAL, {"unverified_project": False})
    result = publish(db, owner, "youtube_shorts", video, REAL, options={"privacy": "public"})[1]
    assert body(seen[0])["status"]["privacyStatus"] == "public"
    seen.clear()
    unlisted = publish(db, owner, "youtube_shorts", video, REAL, options={"privacy": "unlisted"})[1]
    assert body(seen[0])["status"]["privacyStatus"] == "unlisted" and unlisted["state"] == "private"
    assert result["state"] == "published" and result["reason"] is None


def test_DW_2_3_youtube_paid_flag_is_left_out_when_the_config_says_it_is_unsupported(db, owner, video, monkeypatch):
    seen = real_http(monkeypatch)
    set_config(db, REAL, {"paid_partnership_supported": {"youtube_shorts": False}})
    result = publish(db, owner, "youtube_shorts", video, REAL, options={"paid_partnership": True})[1]
    assert "paidProductPlacementDetails" not in body(seen[0]) and "paidProductPlacementDetails" not in seen[0].url.params["part"]
    assert "YouTube Studio" in result["reason"]


def test_DW_2_3_youtube_title_comes_from_the_first_line_and_is_capped(db, owner, video, monkeypatch):
    seen = real_http(monkeypatch)
    publish(db, owner, "youtube_shorts", video, REAL, caption="\n" + "T" * 150 + "\nsecond line")
    snippet = body(seen[0])["snippet"]
    assert snippet["title"] == "T" * 100 and snippet["description"].endswith("second line")


def test_DW_2_3_tiktok_uses_the_inbox_endpoint_and_ends_as_a_draft(db, owner, video, monkeypatch):
    seen = real_http(monkeypatch)
    connection, result = publish(db, owner, "tiktok", video, REAL, options={"paid_partnership": True})
    init, upload = seen
    assert init.url.path == "/v2/post/publish/inbox/video/init/" and "direct" not in str(init.url)
    assert body(init) == {"source_info": {"source": "FILE_UPLOAD", "video_size": 10, "chunk_size": 10, "total_chunk_count": 1}}
    assert upload.method == "PUT" and upload.headers["content-range"] == "bytes 0-9/10" and "authorization" not in upload.headers
    assert result["state"] == "draft" and result["platform_post_id"] == "v_inbox_file~v2.123456789" and result["url"] is None
    assert "inbox" in result["reason"] and "commercial content" in result["reason"]


def test_DW_2_3_tiktok_splits_a_larger_file_into_ranges_that_cover_every_byte(db, owner, video, monkeypatch):
    seen = real_http(monkeypatch)
    set_config(db, REAL, {"chunk_bytes": 4})
    publish(db, owner, "tiktok", video, REAL)
    assert body(seen[0])["source_info"] == {"source": "FILE_UPLOAD", "video_size": 10, "chunk_size": 4, "total_chunk_count": 2}
    ranges = [(r.headers["content-range"], r.content) for r in seen[1:]]
    assert ranges == [("bytes 0-3/10", b"0123"), ("bytes 4-9/10", b"456789")]


def test_DW_2_3_instagram_publishes_publicly_with_a_url_and_follows_the_container_steps(db, owner, video, monkeypatch):
    seen = real_http(monkeypatch)
    real_ready(db)
    connection, result = publish(db, owner, "instagram_reels", video, REAL, options={"paid_partnership": True})
    create, upload, poll, publish_call, link = seen
    assert create.url.path == "/v27.0/fake-instagram_reels-1/media" and form(create) == {
        "media_type": "REELS", "upload_type": "resumable", "caption": "A caption #clip", "share_to_feed": "true"}
    assert upload.url.path == "/ig-api-upload/v27.0/17889455560051444"
    assert upload.url.host == "rupload.facebook.com" and upload.headers["authorization"].startswith("OAuth ")
    assert upload.headers["offset"] == "0" and upload.headers["file_size"] == "10" and upload.content == b"0123456789"
    assert poll.url.params["fields"] == "status_code"
    assert form(publish_call) == {"creation_id": "17889455560051444"} and publish_call.url.path.endswith("/media_publish")
    assert result == {"state": "published", "platform_post_id": "17900000000000001", "url": "https://www.instagram.com/reel/FkReel123/",
                      "reason": "Tick the paid partnership label in Instagram: the API setting is switched off."}


def test_DW_2_3_instagram_sets_the_paid_partnership_option_only_when_the_config_supports_it(db, owner, video, monkeypatch):
    seen = real_http(monkeypatch)
    real_ready(db)
    set_config(db, REAL, {"paid_partnership_supported": {"instagram_reels": True}})
    result = publish(db, owner, "instagram_reels", video, REAL, options={"paid_partnership": True})[1]
    assert form(seen[0])["is_paid_partnership"] == "true" and result["reason"] is None
    seen.clear()
    publish(db, owner, "instagram_reels", video, REAL, options={"paid_partnership": False})
    assert "is_paid_partnership" not in form(seen[0])


def test_DW_2_3_the_token_only_goes_to_the_platforms_own_hosts(db, owner, video, monkeypatch):
    seen = real_http(monkeypatch)
    real_ready(db)
    for platform in PLATFORMS:
        publish(db, owner, platform, video, REAL)
    hosts = {r.url.host for r in seen}
    assert hosts <= {"www.googleapis.com", "graph.instagram.com", "rupload.facebook.com", "open.tiktokapis.com", "open-upload.tiktokapis.com"}


# ------------------------------------------------------------------ DW-2.4

def test_DW_2_4_youtube_quota_is_refused_before_any_call_with_an_alert(db, owner, video):
    connection = conn_id(db, owner, "youtube_shorts")
    for _ in range(6):                                                            # 6 x 1600 = 9600 of 10000
        assert call(db, owner, "publish_video", req(connection, "youtube_shorts", video))["state"] == "private"
    calls_before = len(publishing.FAKE_PUBLISHING["calls"])
    result = call(db, owner, "publish_video", req(connection, "youtube_shorts", video))
    assert result["state"] == "failed" and "allowance" in result["reason"] and result["platform_post_id"] is None
    assert len(publishing.FAKE_PUBLISHING["calls"]) == calls_before               # refused without touching the platform
    assert len(alerts_of(db, "publish_quota")) == 1


def test_DW_2_4_instagram_quota_is_per_account_and_the_window_is_24_hours(db, owner, video):
    connection = conn_id(db, owner, "instagram_reels")
    set_config(db, "fake", {"limits": {"instagram_reels": {"units_per_upload": 1, "max_units_24h": 2, "scope": "connection", "caption_max": 2200}}})
    sql(db, "insert into publish_quota_log (owner_operator_id, connection_id, platform, units, at) values (%s, %s, 'instagram_reels', 1, now() - interval '25 hours')",
        owner.operator_id, connection)                                           # too old to count
    assert [call(db, owner, "publish_video", req(connection, "instagram_reels", video))["state"] for _ in range(3)] == ["published", "published", "failed"]
    assert len(alerts_of(db, "publish_quota")) == 1


def test_DW_2_4_a_malformed_upload_answer_after_bytes_is_needs_check_with_an_alert(db, owner, video, monkeypatch):
    real_http(monkeypatch, [(is_("PUT", "upload_id"), httpx.Response(201, text="<html>oops</html>"))])
    result = publish(db, owner, "youtube_shorts", video, REAL)[1]
    assert result["state"] == "needs_check" and result["platform_post_id"] is None and "Check the platform" in result["reason"]
    assert len(alerts_of(db, "publish_bad_response")) == 1


@pytest.mark.parametrize("bad", [{"id": "../../etc"}, {"id": 5}, {"nope": 1}, [1, 2]])
def test_DW_2_4_a_video_id_that_is_not_id_shaped_is_never_trusted(db, owner, video, monkeypatch, bad):
    real_http(monkeypatch, [(is_("PUT", "upload_id"), httpx.Response(201, json=bad))])
    result = publish(db, owner, "youtube_shorts", video, REAL)[1]
    assert result["state"] == "needs_check" and result["platform_post_id"] is None and result["url"] is None
    assert alerts_of(db, "publish_bad_response")


def test_DW_2_4_an_upload_session_on_another_host_is_refused_and_the_token_never_sent_there(db, owner, video, monkeypatch):
    seen = real_http(monkeypatch, [(is_("POST", "uploadType"), httpx.Response(200, headers={"Location": "https://evil.test/upload?id=1"}))])
    result = publish(db, owner, "youtube_shorts", video, REAL)[1]
    assert result["state"] == "failed" and [r.url.host for r in seen] == ["www.googleapis.com"]
    assert alerts_of(db, "publish_bad_response") and sql(db, "select 1 from publish_quota_log") == []


def test_DW_2_4_instagram_container_answer_without_an_id_fails_before_any_upload(db, owner, video, monkeypatch):
    seen = real_http(monkeypatch, [(is_("POST", "/media"), httpx.Response(200, json={"oops": 1}))])
    result = publish(db, owner, "instagram_reels", video, REAL)[1]
    assert result["state"] == "failed" and len(seen) == 1 and alerts_of(db, "publish_bad_response")


def test_DW_2_4_instagram_upload_that_does_not_say_success_is_needs_check_with_the_container(db, owner, video, monkeypatch):
    real_http(monkeypatch, [(is_("POST", "ig-api-upload"), httpx.Response(200, json={"success": False}))])
    result = publish(db, owner, "instagram_reels", video, REAL)[1]
    assert result["state"] == "needs_check" and result["platform_post_id"] == "17889455560051444"


def test_DW_2_4_tiktok_upload_address_on_another_host_is_refused_and_nothing_is_uploaded(db, owner, video, monkeypatch):
    evil = {"data": {"publish_id": "pid123", "upload_url": "https://evil.test/up"}, "error": {"code": "ok"}}
    seen = real_http(monkeypatch, [(is_("POST", "inbox/video/init"), httpx.Response(200, json=evil))])
    result = publish(db, owner, "tiktok", video, REAL)[1]
    assert result["state"] == "failed" and len(seen) == 1 and alerts_of(db, "publish_bad_response")


def test_DW_2_4_tiktok_init_error_code_with_a_200_is_a_refusal(db, owner, video, monkeypatch):
    answer = httpx.Response(200, json={"error": {"code": "invalid_param", "message": "video_size is wrong"}})
    seen = real_http(monkeypatch, [(is_("POST", "inbox/video/init"), answer)])
    result = publish(db, owner, "tiktok", video, REAL)[1]
    assert result["state"] == "failed" and "video_size is wrong" in result["reason"] and len(seen) == 1


def test_DW_2_4_a_malformed_status_answer_is_needs_check_with_an_alert(db, owner, video, monkeypatch):
    connection, posted = publish(db, owner, "tiktok", video)
    real_http(monkeypatch, [(is_("POST", "status/fetch"), httpx.Response(200, json={"data": {"status": "WHO_KNOWS"}, "error": {"code": "ok"}}))])
    result = status(db, owner, connection, "tiktok", posted["platform_post_id"], REAL)
    assert result["state"] == "needs_check" and alerts_of(db, "publish_bad_response")


# ------------------------------------------------------------------ edge cases and other methods

def test_a_caption_over_the_platform_limit_is_refused_before_any_call(db, owner, video):
    connection = conn_id(db, owner, "instagram_reels")
    result = call(db, owner, "publish_video", req(connection, "instagram_reels", video, caption="x" * 2201))
    assert result["state"] == "failed" and "2201" in result["reason"] and "2200" in result["reason"]
    assert publishing.FAKE_PUBLISHING["calls"] == [] and sql(db, "select 1 from publish_quota_log") == []
    assert call(db, owner, "publish_video", req(connection, "instagram_reels", video, caption="x" * 2200))["state"] == "published"


def test_a_missing_file_bad_options_and_a_non_text_caption_are_refused_before_any_call(db, owner, video, tmp_path):
    connection = conn_id(db, owner, "tiktok")
    (tmp_path / "empty.mp4").write_bytes(b"")
    for bad in (req(connection, "tiktok", str(tmp_path / "nope.mp4")), req(connection, "tiktok", str(tmp_path / "empty.mp4")),
                req(connection, "tiktok", video, options={"privacy": "everyone"}), req(connection, "tiktok", video, options={"paid_partnership": "yes"}),
                {**req(connection, "tiktok", video), "caption": None}, {**req(connection, "tiktok", video), "options": "x"}):
        result = call(db, owner, "publish_video", bad)
        assert result["state"] == "failed" and result["reason"], bad
    assert publishing.FAKE_PUBLISHING["calls"] == []


def test_a_connection_for_another_platform_is_refused_so_a_token_never_reaches_the_wrong_host(db, owner, video):
    youtube = conn_id(db, owner, "youtube_shorts")
    result = call(db, owner, "publish_video", req(youtube, "tiktok", video))
    assert result["state"] == "failed" and "not a TikTok account" in result["reason"] and publishing.FAKE_PUBLISHING["calls"] == []


def test_an_unknown_platform_and_missing_limits_are_refused(db, owner, video):
    connection = conn_id(db, owner, "tiktok")
    with pytest.raises(AppError) as info:
        call(db, owner, "publish_video", req(connection, "myspace", video))
    assert info.value.code == "unsupported_platform"
    set_config(db, "fake", {"limits": {}})
    assert call(db, owner, "publish_video", req(connection, "tiktok", video))["state"] == "failed"


def test_another_owners_connection_cannot_be_used_and_needs_reconnect_stops_the_call(db, owner, video):
    connection = conn_id(db, owner, "tiktok")
    other = second_owner(db)
    with pytest.raises(AppError) as info:
        call(db, other, "publish_video", req(connection, "tiktok", video))
    assert info.value.code == "not_connected"
    sql(db, "update platform_connections set state = 'needs_reconnect'")
    with pytest.raises(vault.NeedsReconnect):
        call(db, owner, "publish_video", req(connection, "tiktok", video))
    assert publishing.FAKE_PUBLISHING["calls"] == []


def test_quota_rows_belong_to_their_owner(db, owner, video):
    publish(db, owner, "tiktok", video)
    other = second_owner(db)
    with db.session(other) as conn:
        assert conn.execute("select count(*) as n from publish_quota_log").fetchone()["n"] == 0
    with db.session(owner) as conn:
        assert conn.execute("select count(*) as n from publish_quota_log").fetchone()["n"] == 1


def test_instagram_container_that_errors_is_a_safe_failure_and_an_unknown_status_needs_a_check(db, owner, video, monkeypatch):
    publishing.FAKE_PUBLISHING["mode"]["instagram_reels"] = "error"
    result = publish(db, owner, "instagram_reels", video)[1]
    assert result["state"] == "failed" and "error" in result["reason"] and not any(c[1].endswith("/media_publish") for c in publishing.FAKE_PUBLISHING["calls"])
    real_http(monkeypatch, [(is_("GET", "status_code"), httpx.Response(200, json={"status_code": "WEIRD"}))])
    odd = publish(db, owner, "instagram_reels", video, REAL)[1]
    assert odd["state"] == "needs_check" and alerts_of(db, "publish_bad_response")


def test_instagram_missing_permalink_does_not_undo_a_live_post(db, owner, video, monkeypatch):
    real_http(monkeypatch, [(lambda r: r.method == "GET" and r.url.params.get("fields") == "permalink", httpx.ConnectError("down"))])
    real_ready(db)
    result = publish(db, owner, "instagram_reels", video, REAL)[1]
    assert result["state"] == "published" and result["url"] is None and result["platform_post_id"] == "17900000000000001"
    real_http(monkeypatch, [(lambda r: r.method == "GET" and r.url.params.get("fields") == "permalink", httpx.Response(200, json={"permalink": "javascript:1"}))])
    assert publish(db, owner, "instagram_reels", video, REAL)[1]["url"] is None


def test_finish_publish_refuses_other_platforms_and_is_needs_check_when_the_call_fails(db, owner, video, monkeypatch):
    connection, posted = publish(db, owner, "tiktok", video)
    refused = call(db, owner, "finish_publish", {"connection_id": connection, "platform": "tiktok", "platform_post_id": posted["platform_post_id"]})
    assert refused["state"] == "failed"
    ig = conn_id(db, owner, "instagram_reels")
    real_http(monkeypatch, [(is_("POST", "media_publish"), httpx.ReadTimeout("slow"))])
    result = call(db, owner, "finish_publish", {"connection_id": ig, "platform": "instagram_reels", "platform_post_id": "17889455560051444"}, REAL)
    assert result["state"] == "needs_check" and result["platform_post_id"] == "17889455560051444"
    bad = call(db, owner, "finish_publish", {"connection_id": ig, "platform": "instagram_reels", "platform_post_id": "../x"}, REAL)
    assert bad["state"] == "needs_check"


def test_check_status_failures_retry_through_the_registry_but_a_4xx_is_a_needs_check(db, owner, video, monkeypatch):
    connection, posted = publish(db, owner, "youtube_shorts", video)
    seen = real_http(monkeypatch, [(is_("GET", "youtube/v3/videos"), httpx.Response(503))])
    with pytest.raises(AdapterUnavailable):
        status(db, owner, connection, "youtube_shorts", posted["platform_post_id"], REAL)
    assert len(seen) == 3                                                         # a read is safe to retry
    real_http(monkeypatch, [(is_("GET", "youtube/v3/videos"), httpx.Response(404, json={"error": {"message": "nope"}}))])
    assert status(db, owner, connection, "youtube_shorts", posted["platform_post_id"], REAL)["state"] == "needs_check"
    real_http(monkeypatch, [(is_("GET", "youtube/v3/videos"), httpx.Response(200, json={"items": []}))])
    assert "not list" in status(db, owner, connection, "youtube_shorts", posted["platform_post_id"], REAL)["reason"]


def test_check_status_reads_public_youtube_and_tiktok_post_urls(db, owner, video, monkeypatch):
    connection, posted = publish(db, owner, "youtube_shorts", video)
    real_http(monkeypatch, [(is_("GET", "youtube/v3/videos"), httpx.Response(200, json={"items": [{"id": "x", "status": {"uploadStatus": "processed", "privacyStatus": "public"}, "processingDetails": {"processingStatus": "succeeded"}}]}))])
    done = status(db, owner, connection, "youtube_shorts", posted["platform_post_id"], REAL)
    assert done["state"] == "published" and done["url"] == "https://www.youtube.com/shorts/FkVid_abc12"
    tiktok, draft = publish(db, owner, "tiktok", video)
    sql(db, "update platform_connections set handle = '@own_tt' where id = %s", tiktok)
    publishing.FAKE_PUBLISHING["status"]["tiktok"] = "complete"
    assert status(db, owner, tiktok, "tiktok", draft["platform_post_id"])["url"] == "https://www.tiktok.com/@own_tt/video/7300000000000000001"
    sql(db, "update platform_connections set handle = 'bad handle/../' where id = %s", tiktok)
    assert status(db, owner, tiktok, "tiktok", draft["platform_post_id"])["url"] is None


def test_check_status_rejects_a_post_id_that_is_not_id_shaped(db, owner, video):
    connection = conn_id(db, owner, "tiktok")
    assert status(db, owner, connection, "tiktok", "../../x")["state"] == "needs_check" and publishing.FAKE_PUBLISHING["calls"] == []


def test_platform_text_in_reasons_is_cleaned_and_capped(db, owner, video, monkeypatch):
    nasty = {"error": {"message": "bad\x00\x1b[31m " + "A" * 400}}
    real_http(monkeypatch, [(is_("POST", "uploadType"), httpx.Response(400, json=nasty))])
    reason = publish(db, owner, "youtube_shorts", video, REAL)[1]["reason"]
    assert "\x00" not in reason and "\x1b" not in reason and len(reason) < 260


def test_no_token_appears_in_results_audit_rows_or_alerts(db, owner, video, monkeypatch):
    real_http(monkeypatch, [(is_("PUT", "upload_id"), httpx.Response(201, text="junk"))])
    results = [publish(db, owner, "youtube_shorts", video, REAL)[1], publish(db, owner, "tiktok", video)[1]]
    rows = sql(db, "select row_to_json(a)::text as t from audit_log a union all select row_to_json(x)::text from alerts x "
                   "union all select row_to_json(q)::text from publish_quota_log q")
    haystack = "\n".join([r["t"] for r in rows] + [json.dumps(r) for r in results])
    assert oauth.FAKE_OAUTH["issued"] and not any(token in haystack for token in oauth.FAKE_OAUTH["issued"])


def test_the_base_adapter_alert_hook_is_a_no_op_until_the_registry_binds_it():
    adapter = publishing.PlatformPublishing({"config_json": {}})
    assert adapter.alert("x", "warn", "hello") is None and adapter.conn is None and adapter.no_retry == ("publish_video", "finish_publish")


# ------------------------------------------------------------------ Instagram dry run

def test_instagram_dry_run_creates_uploads_and_waits_but_never_calls_media_publish_and_uses_no_allowance(db, owner, video, monkeypatch):
    monkeypatch.setenv("CLIPPING_PUBLISH_DRY_RUN", "1")
    seen = real_http(monkeypatch)
    real_ready(db)
    connection, result = publish(db, owner, "instagram_reels", video, REAL)
    assert [r.url.path.rsplit("/", 1)[-1] for r in seen][-1] != "media_publish" and not any(r.url.path.endswith("/media_publish") for r in seen)
    assert any(r.url.host == "rupload.facebook.com" for r in seen)
    assert result["state"] == "failed" and "Dry run" in result["reason"] and result["url"] is None
    assert sql(db, "select count(*) as n from publish_quota_log where connection_id = %s", connection)[0]["n"] == 0


def test_dry_run_refuses_the_other_platforms_before_any_call(db, owner, video, monkeypatch):
    monkeypatch.setenv("CLIPPING_PUBLISH_DRY_RUN", "1")
    seen = real_http(monkeypatch)
    real_ready(db)
    result = publish(db, owner, "tiktok", video, REAL)[1]
    assert result["state"] == "failed" and seen == []


def test_instagram_upload_goes_only_to_metas_upload_host_even_if_the_container_answer_names_another(db, owner, video, monkeypatch):
    seen = real_http(monkeypatch, overrides=[(lambda r: r.url.path.endswith("/media"),
                                              httpx.Response(200, json={"id": "17889455560051444", "uri": "https://evil.example/steal"}))])
    real_ready(db)
    publish(db, owner, "instagram_reels", video, REAL)
    assert all(r.url.host != "evil.example" for r in seen) and seen[1].url.host == "rupload.facebook.com"
