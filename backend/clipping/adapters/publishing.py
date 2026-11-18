"""Publishing through the official platform APIs: YouTube Data v3 (resumable upload), Instagram Graph (Reels container,
resumable upload, publish) and TikTok Content Posting (upload to the owner's inbox as a draft).

Written from the official docs (see the phase 2 discovery file) and not yet run against the live services: that check
waits for the owner's developer apps. Everything is tested on `httpx.MockTransport`; the fake provider is this same class
with a fixture-driven transport, so the validation and ambiguity rules run in tests exactly as they will live.

Rules that keep it from double-posting:
- `publish_video` makes one attempt (`no_retry`). `_started` flips when the first byte is sent. Before that, any failure
  is a safe `failed` with a reason. After it, any non-success or unreadable answer is `needs_check`, never a guess.
- Platform answers are untrusted: only the fields read below are used, ids are shape-checked, a bad answer raises an alert.
- Tokens come from `vault.access_token` at call time, are held in a local variable, and are only sent to the platform's
  own hosts (the session and upload URLs a platform returns are checked before a token goes near them)."""
from __future__ import annotations

import copy
import functools
import json
import re
import time
from pathlib import Path

import httpx

from .. import config
from ..core import AppError
from .base import Adapter, AdapterError, http_client, register
from .oauth import PLATFORM_LABEL, PLATFORMS

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "publishing"
YT_UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
YT_API = "https://www.googleapis.com/youtube/v3/videos"
IG_GRAPH = "https://graph.instagram.com"
IG_UPLOAD = "https://rupload.facebook.com/ig-api-upload"
TT_API = "https://open.tiktokapis.com/v2/post/publish"
PRIVACY = ("private", "unlisted", "public")
ID_RE = re.compile(r"^[A-Za-z0-9_.~:-]{3,128}$")
BLOCK = 1 << 20


def provider_name() -> str:
    """The fake until the developer apps exist (CLIPPING_USE_FAKES stays on), the real endpoints after."""
    return "fake" if config.use_fakes() else "platforms"


def outcome(state: str, post_id: str | None = None, url: str | None = None, reason: str | None = None) -> dict:
    return {"state": state, "platform_post_id": post_id, "url": url, "reason": reason}


def _clean(text, limit: int = 160) -> str:
    """Platform text shown to the owner: printable, short, with any known secret removed."""
    return config.redact("".join(ch for ch in str(text) if ch.isprintable()))[:limit].strip()


def _why(response) -> str:
    try:
        error = response.json().get("error")
        message = error.get("message") if isinstance(error, dict) else None
    except (ValueError, AttributeError):
        message = None
    return _clean(message) if message else f"HTTP {response.status_code}"


def _blocks(path: Path):
    with path.open("rb") as handle:
        while block := handle.read(BLOCK):
            yield block


class _Done(Exception):
    """An outcome that is already decided: stops the flow and becomes the result."""

    def __init__(self, result: dict):
        self.result = result


@register
class PlatformPublishing(Adapter):
    stage, provider = "publishing", "platforms"
    no_retry = ("publish_video", "finish_publish")

    # ------------------------------------------------------------------ public interface
    def publish_video(self, connection_id, platform: str, file_path: str, caption: str, options: dict | None = None) -> dict:
        """Upload one video. Returns {state, platform_post_id, url, reason}. State is published, draft, private, processing,
        needs_check or failed. Raises AppError only for a connection that is missing or needs the owner to sign in again."""
        self._begin_call()
        try:
            platform = self._platform(platform)
            opts = self._options(options)
            path, size = self._file(file_path)
            self._caption(platform, caption)
            row = self._connection(connection_id, platform)
            self._quota(platform, connection_id)
            token = self._token(connection_id)
            with self._client() as client:
                flow = {"youtube_shorts": self._yt_publish, "instagram_reels": self._ig_publish, "tiktok": self._tt_publish}[platform]
                return flow(client, token, row, connection_id, path, size, caption, opts)
        except _Done as done:
            return done.result
        except AppError:
            raise
        except Exception as exc:  # a bug or I/O error: if bytes were already sent, the post may exist
            if not self._started:
                raise
            return self._unsure(f"unexpected {type(exc).__name__}")

    def finish_publish(self, connection_id, platform: str, platform_post_id: str) -> dict:
        """Publish an Instagram container that was still processing when publish_video returned. At most once per call."""
        self._begin_call(started=True)
        try:
            platform = self._platform(platform)
            self._post_id = self._post_id_ok(platform_post_id)
            if platform != "instagram_reels":
                raise _Done(outcome("failed", platform_post_id, reason="Only Instagram needs a separate publish step."))
            row = self._connection(connection_id, platform)
            token = self._token(connection_id)
            with self._client() as client:
                return self._ig_finish(client, token, row, platform_post_id)
        except _Done as done:
            return done.result
        except AppError:
            raise
        except Exception as exc:
            return self._unsure(f"unexpected {type(exc).__name__}")

    def check_status(self, connection_id, platform: str, platform_post_id: str) -> dict:
        """Ask the platform where a post stands. Read-only: a network or 5xx failure raises AdapterError (the registry retries)."""
        self._begin_call(started=True)
        try:
            platform = self._platform(platform)
            self._post_id = self._post_id_ok(platform_post_id)
            row = self._connection(connection_id, platform)
            token = self._token(connection_id)
            with self._client() as client:
                check = {"youtube_shorts": self._yt_status, "instagram_reels": self._ig_status, "tiktok": self._tt_status}[platform]
                return check(client, token, row, platform_post_id)
        except _Done as done:
            return done.result

    # ------------------------------------------------------------------ seams (the fake overrides _client)
    def _client(self):
        return http_client(float(self.cfg.get("timeout_s", 120)))

    def _token(self, connection_id) -> str:
        from .. import vault   # lazy: vault imports this package, and an adapter may not depend on domain modules at import time

        return vault.access_token(self.conn, connection_id)

    # ------------------------------------------------------------------ shared steps
    def _begin_call(self, started: bool = False) -> None:
        self._started, self._post_id, self._notes = started, None, []

    def _stop(self, reason: str, post_id: str | None = None) -> None:
        if post_id:
            self._post_id = post_id
        raise _Done(self._unsure(reason) if self._started else outcome("failed", reason=reason))

    def _unsure(self, reason: str) -> dict:
        return outcome("needs_check", self._post_id, None,
                       f"We could not confirm whether this posted ({reason}). Check the platform before trying again.")

    def _finish(self, state: str, post_id=None, url=None, reason=None) -> dict:
        return outcome(state, post_id, url, " ".join([*([reason] if reason else []), *self._notes]) or None)

    def _bad(self, what: str) -> None:
        self.alert("publish_bad_response", "warn", f"A platform sent an unreadable {what} while posting. Check the post before trying again.")
        self._stop(f"the platform's {what} could not be read")

    def _platform(self, platform) -> str:
        if platform not in PLATFORMS:
            raise AppError("That platform is not supported for posting.", "unsupported_platform")
        return platform

    def _post_id_ok(self, post_id) -> str:
        if not isinstance(post_id, str) or not ID_RE.match(post_id):
            raise _Done(outcome("needs_check", None, None, "The saved post id looks wrong. Check the platform by hand."))
        return post_id

    def _options(self, options) -> dict:
        options = {} if options is None else options
        privacy = options.get("privacy") if isinstance(options, dict) else None
        paid = options.get("paid_partnership", False) if isinstance(options, dict) else None
        if not isinstance(options, dict) or privacy not in (None, *PRIVACY) or not isinstance(paid, bool):
            self._stop("the post options are not valid")
        return {"privacy": privacy or "private", "paid": paid}

    def _file(self, file_path) -> tuple[Path, int]:
        path = Path(str(file_path))
        if not path.is_file() or path.stat().st_size == 0:
            self._stop("the video file is missing or empty")
        return path, path.stat().st_size

    def _limit(self, platform: str) -> dict:
        limit = (self.cfg.get("limits") or {}).get(platform)
        if not isinstance(limit, dict) or not all(isinstance(limit.get(k), (int, float)) for k in ("units_per_upload", "max_units_24h", "caption_max")):
            self._stop("posting limits are not configured for this platform")
        return limit

    def _caption(self, platform: str, caption) -> None:
        if not isinstance(caption, str):
            self._stop("the caption is not text")
        longest = self._limit(platform)["caption_max"]
        if len(caption) > longest:
            self._stop(f"the caption has {len(caption)} characters and {PLATFORM_LABEL[platform]} allows {int(longest)}")

    def _connection(self, connection_id, platform: str) -> dict:
        row = self.conn.execute("select platform, external_account_id, handle, owner_operator_id from platform_connections where id = %s", (connection_id,)).fetchone()
        if row is None:
            raise AppError("That account is not connected.", "not_connected")
        if row["platform"] != platform:   # never send one platform's token to another platform's host
            self._stop(f"that account is not a {PLATFORM_LABEL[platform]} account")
        self._owner = row["owner_operator_id"]
        return row

    def _quota_where(self, platform: str, connection_id) -> tuple[str, tuple]:
        if self._limit(platform).get("scope") == "platform":
            return "platform = %s", (platform,)
        return "platform = %s and connection_id = %s", (platform, connection_id)

    def _quota(self, platform: str, connection_id) -> None:
        """Refuse before any call when the day's allowance would be exceeded."""
        limit = self._limit(platform)
        where, args = self._quota_where(platform, connection_id)
        self.conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"publish-quota:{platform}",))
        used = self.conn.execute(f"select coalesce(sum(units), 0) as used from publish_quota_log where {where} and at > now() - interval '24 hours'",
                                 args).fetchone()["used"]
        if used + limit["units_per_upload"] > limit["max_units_24h"]:
            label = PLATFORM_LABEL[platform]
            self.alert("publish_quota", "warn", f"{label} posting allowance for today is used up ({used} of {limit['max_units_24h']}). Post by hand or try tomorrow.")
            self._stop(f"today's {label} posting allowance is used up")

    def _upload_started(self, platform: str, connection_id) -> None:
        """Called right before the first byte leaves: from here on a failure is ambiguous. Counts against the quota."""
        self._started = True
        self.conn.execute("insert into publish_quota_log (owner_operator_id, connection_id, platform, units) values (%s, %s, %s, %s)",
                          (self._owner, connection_id, platform, self._limit(platform)["units_per_upload"]))

    def _call(self, client, method: str, url: str, *, safe: bool = False, **kw):
        """One request. Returns a 2xx response. Otherwise stops with failed (nothing sent yet) or needs_check (upload begun).
        safe=True (read-only calls) raises AdapterError for network and 5xx failures so the registry can retry."""
        try:
            response = client.request(method, url, **kw)
        except httpx.HTTPError as exc:
            if safe:
                raise AdapterError(type(exc).__name__) from None
            self._stop(f"the request did not finish ({type(exc).__name__})")
        code = response.status_code
        if code < 300:
            return response
        if safe and code >= 500:
            raise AdapterError(f"HTTP {code}")
        self._stop(f"the platform answered: {_why(response)}" if not self._started or safe else f"the platform answered HTTP {code}")

    def _json(self, response, what: str) -> dict:
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            self._bad(what)
        return data

    def _id(self, value, what: str) -> str:
        if not isinstance(value, str) or not ID_RE.match(value):
            self._bad(what)
        return value

    # ------------------------------------------------------------------ YouTube
    def _yt_publish(self, client, token, row, connection_id, path, size, caption, opts) -> dict:
        platform = "youtube_shorts"
        auth = {"Authorization": f"Bearer {token}"}
        asked = "private" if self.cfg.get("unverified_project", True) else opts["privacy"]
        paid_ok = bool((self.cfg.get("paid_partnership_supported") or {}).get(platform))
        body = {"snippet": {"title": (next((ln for ln in caption.splitlines() if ln.strip()), "") or "Clip")[:100], "description": caption, "categoryId": "22"},
                "status": {"privacyStatus": asked, "selfDeclaredMadeForKids": False}}
        if paid_ok:
            body["paidProductPlacementDetails"] = {"hasPaidProductPlacement": opts["paid"]}
        elif opts["paid"]:
            self._notes.append("Tick paid promotion in YouTube Studio: the API setting is switched off.")
        part = "snippet,status" + (",paidProductPlacementDetails" if paid_ok else "")
        started = self._call(client, "POST", YT_UPLOAD, params={"uploadType": "resumable", "part": part},
                             headers={**auth, "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"}, json=body)
        session = started.headers.get("location", "")
        if not session.startswith(YT_UPLOAD + "?"):      # the bearer token goes only to Google's upload host
            self._bad("upload session")
        self._upload_started(platform, connection_id)
        done = self._call(client, "PUT", session, headers={**auth, "Content-Type": "video/mp4", "Content-Length": str(size)}, content=_blocks(path))
        video = self._json(done, "upload answer")
        video_id = self._id(video.get("id"), "video id")
        self._post_id = video_id
        actual = (video.get("status") or {}).get("privacyStatus") if isinstance(video.get("status"), dict) else None
        actual = actual if actual in PRIVACY else asked
        reason = None
        if actual != opts["privacy"]:
            reason = "YouTube kept this upload private until the app passes Google's audit." if actual == "private" else f"YouTube set it to {actual}."
        return self._finish("published" if actual == "public" else "private", video_id, f"https://www.youtube.com/shorts/{video_id}", reason)

    def _yt_status(self, client, token, row, video_id) -> dict:
        data = self._json(self._call(client, "GET", YT_API, safe=True, params={"part": "status,processingDetails", "id": video_id},
                                     headers={"Authorization": f"Bearer {token}"}), "status answer")
        items = data.get("items")
        item = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else None
        if item is None:
            return outcome("needs_check", video_id, None, "YouTube does not list this video. Check it in YouTube Studio.")
        status = item.get("status") if isinstance(item.get("status"), dict) else {}
        details = item.get("processingDetails") if isinstance(item.get("processingDetails"), dict) else {}
        upload, privacy = status.get("uploadStatus"), status.get("privacyStatus")
        if upload in ("failed", "rejected", "deleted"):
            why = status.get("failureReason") or status.get("rejectionReason") or upload
            return outcome("failed", video_id, None, f"YouTube did not accept the video ({_clean(why, 60)}).")
        if details.get("processingStatus") == "processing" or upload == "uploaded" and not details:
            return outcome("processing", video_id, None, "YouTube is still processing the video.")
        if privacy not in PRIVACY:
            self._bad("status answer")
        return outcome("published" if privacy == "public" else "private", video_id, f"https://www.youtube.com/shorts/{video_id}")

    # ------------------------------------------------------------------ Instagram
    def _ig_url(self, tail: str) -> str:
        return f"{IG_GRAPH}/{self.cfg.get('instagram_api_version', 'v21.0')}/{tail}"

    def _ig_publish(self, client, token, row, connection_id, path, size, caption, opts) -> dict:
        platform = "instagram_reels"
        form = {"media_type": "REELS", "upload_type": "resumable", "caption": caption}
        if (self.cfg.get("paid_partnership_supported") or {}).get(platform):
            if opts["paid"]:
                form["is_paid_partnership"] = "true"
        elif opts["paid"]:
            self._notes.append("Tick the paid partnership label in Instagram: the API setting is switched off.")
        created = self._call(client, "POST", self._ig_url(f"{row['external_account_id']}/media"), headers={"Authorization": f"Bearer {token}"}, data=form)
        container = self._id(self._json(created, "container answer").get("id"), "container id")
        self._upload_started(platform, connection_id)
        self._post_id = container
        sent = self._call(client, "POST", f"{IG_UPLOAD}/{self.cfg.get('instagram_api_version', 'v21.0')}/{container}",
                          headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size), "Content-Length": str(size)}, content=_blocks(path))
        if self._json(sent, "upload answer").get("success") is not True:
            self._bad("upload answer")
        return self._ig_finish(client, token, row, container)

    def _ig_finish(self, client, token, row, container: str) -> dict:
        """Wait (bounded) for the container, then publish it once."""
        auth = {"Authorization": f"Bearer {token}"}
        attempts = max(1, int(self.cfg.get("poll_attempts", 10)))
        for attempt in range(attempts):
            status = self._json(self._call(client, "GET", self._ig_url(container), safe=False, params={"fields": "status_code"}, headers=auth),
                                "status answer").get("status_code")
            if status == "FINISHED":
                break
            if status in ("ERROR", "EXPIRED"):
                return outcome("failed", container, None, f"Instagram could not process the video ({status.lower()}). Nothing was posted.")
            if status == "PUBLISHED":
                return self._finish("published", container, None, "Instagram already published this video.")
            if status != "IN_PROGRESS":
                self._bad("status answer")
            if attempt < attempts - 1:
                time.sleep(float(self.cfg.get("poll_interval_s", 30)))
        else:
            return self._finish("processing", container, None, "Instagram is still processing the video.")
        published = self._call(client, "POST", self._ig_url(f"{row['external_account_id']}/media_publish"), headers=auth, data={"creation_id": container})
        media_id = self._id(self._json(published, "publish answer").get("id"), "media id")
        self._post_id = media_id
        return self._finish("published", media_id, self._ig_permalink(client, auth, media_id))

    def _ig_permalink(self, client, auth, media_id: str) -> str | None:
        """Best effort and outside the ambiguity rules: the post is already live, a missing link must not undo that."""
        try:
            data = client.get(self._ig_url(media_id), params={"fields": "permalink"}, headers=auth).json()
            link = data.get("permalink") if isinstance(data, dict) else None
        except (httpx.HTTPError, ValueError):
            return None
        return link if isinstance(link, str) and link.startswith("https://") else None

    def _ig_status(self, client, token, row, media_id) -> dict:
        data = self._json(self._call(client, "GET", self._ig_url(media_id), safe=True, params={"fields": "status_code,permalink"},
                                     headers={"Authorization": f"Bearer {token}"}), "status answer")
        link, status = data.get("permalink"), data.get("status_code")
        if isinstance(link, str) and link.startswith("https://"):
            return outcome("published", media_id, link)
        if status in ("ERROR", "EXPIRED"):
            return outcome("failed", media_id, None, f"Instagram could not process the video ({status.lower()}).")
        if status == "PUBLISHED":
            return outcome("published", media_id)
        if status == "IN_PROGRESS":
            return outcome("processing", media_id, None, "Instagram is still processing the video.")
        if status == "FINISHED":
            return outcome("processing", media_id, None, "Instagram finished processing. It is ready to publish.")
        self._bad("status answer")

    # ------------------------------------------------------------------ TikTok
    def _tt_publish(self, client, token, row, connection_id, path, size, caption, opts) -> dict:
        if opts["paid"]:
            self._notes.append("Switch on the commercial content label in TikTok when you finish the draft.")
        self._notes.append("Open TikTok's inbox notification to add the caption and post it.")
        chunk = max(1, int(self.cfg.get("chunk_bytes", 10 << 20)))
        count = max(1, size // chunk)
        source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk if count > 1 else size, "total_chunk_count": count}
        init = self._call(client, "POST", f"{TT_API}/inbox/video/init/", headers={"Authorization": f"Bearer {token}"}, json={"source_info": source})
        data = self._json(init, "init answer")
        error = data.get("error") if isinstance(data.get("error"), dict) else {}
        if error.get("code") != "ok":
            self._stop(f"the platform answered: {_clean(error.get('message') or error.get('code') or 'no reason given')}")
        body = data.get("data") if isinstance(data.get("data"), dict) else {}
        publish_id, url = self._id(body.get("publish_id"), "publish id"), body.get("upload_url")
        if not (isinstance(url, str) and url.startswith("https://") and url.split("/")[2].endswith(".tiktokapis.com")):
            self._bad("upload address")
        self._post_id = publish_id
        self._upload_started("tiktok", connection_id)
        with path.open("rb") as handle:       # no Authorization header: the upload address carries its own token
            for index in range(count):
                first = index * chunk
                last = size - 1 if index == count - 1 else first + chunk - 1
                handle.seek(first)
                self._call(client, "PUT", url, headers={"Content-Type": "video/mp4", "Content-Length": str(last - first + 1),
                                                        "Content-Range": f"bytes {first}-{last}/{size}"}, content=handle.read(last - first + 1))
        return self._finish("draft", publish_id)

    def _tt_status(self, client, token, row, publish_id) -> dict:
        data = self._json(self._call(client, "POST", f"{TT_API}/status/fetch/", safe=True, headers={"Authorization": f"Bearer {token}"},
                                     json={"publish_id": publish_id}), "status answer")
        error = data.get("error") if isinstance(data.get("error"), dict) else {}
        body = data.get("data") if isinstance(data.get("data"), dict) else None
        if error.get("code") != "ok" or body is None:
            self._bad("status answer")
        status = body.get("status")
        if status in ("PROCESSING_UPLOAD", "PROCESSING_DOWNLOAD"):
            return outcome("processing", publish_id, None, "TikTok is still receiving the video.")
        if status == "SEND_TO_USER_INBOX":
            return outcome("draft", publish_id, None, "The draft is waiting in your TikTok inbox.")
        if status == "FAILED":
            return outcome("failed", publish_id, None, f"TikTok did not accept the video ({_clean(body.get('fail_reason') or 'no reason given', 60)}).")
        if status == "PUBLISH_COMPLETE":
            ids = body.get("publicaly_available_post_id")      # sic: TikTok's spelling
            post = ids[0] if isinstance(ids, list) and ids and isinstance(ids[0], int) and not isinstance(ids[0], bool) else None
            handle = (row["handle"] or "").lstrip("@")
            url = f"https://www.tiktok.com/@{handle}/video/{post}" if post and re.fullmatch(r"[\w.]{1,64}", handle) else None
            return outcome("published", publish_id, url)
        self._bad("status answer")


# ---------------------------------------------------------------------- the fake
FAKE_PUBLISHING: dict = {"calls": [], "mode": {}, "status": {}}


def reset_fake() -> None:
    """mode[platform]: 'refuse' (clear 4xx before bytes), 'timeout' (mid-upload), 'malformed' (unreadable upload answer),
    'processing' (Instagram container never finishes), 'error' (Instagram container fails).
    status[platform]: 'processing' | 'failed' | 'complete' for check_status. `calls` holds (platform, step) pairs, never tokens."""
    FAKE_PUBLISHING.update(calls=[], mode={}, status={})


@functools.cache
def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@register
class FakePublishing(PlatformPublishing):
    """The real adapter over a mock transport that answers with the documented response shapes from fixtures/publishing."""
    provider = "fake"

    def _client(self):
        return httpx.Client(transport=httpx.MockTransport(self._serve))

    def _serve(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host.endswith("googleapis.com"):
            platform, serve = "youtube_shorts", lambda: self._serve_youtube(request, path)
        elif host.endswith(("instagram.com", "facebook.com")):
            platform, serve = "instagram_reels", lambda: self._serve_instagram(request, host, path)
        else:
            platform, serve = "tiktok", lambda: self._serve_tiktok(request, host, path)
        FAKE_PUBLISHING["calls"].append((platform, f"{request.method} {path}"))   # logged first: a timeout still counts as a call
        return serve()

    @staticmethod
    def _mode(platform: str) -> str | None:
        return FAKE_PUBLISHING["mode"].get(platform)

    def _serve_youtube(self, request, path) -> httpx.Response:
        mode, fx = self._mode("youtube_shorts"), _fixture("youtube")
        if request.method == "POST":
            if mode == "refuse":
                return httpx.Response(403, json=fx["error_forbidden"])
            self._privacy = json.loads(request.content)["status"]["privacyStatus"]
            return httpx.Response(200, headers={"Location": fx["session_location"]})
        if request.method == "PUT":
            if mode == "timeout":
                raise httpx.ReadTimeout("fake timeout", request=request)
            if mode == "malformed":
                return httpx.Response(201, text="<html>not json</html>")
            video = copy.deepcopy(fx["insert_created"])
            video["status"]["privacyStatus"] = getattr(self, "_privacy", "private")
            return httpx.Response(201, json=video)
        wanted = FAKE_PUBLISHING["status"].get("youtube_shorts")
        return httpx.Response(200, json=fx[{"processing": "status_processing", "failed": "status_failed"}.get(wanted, "status_processed")])

    def _serve_instagram(self, request, host, path) -> httpx.Response:
        mode, fx = self._mode("instagram_reels"), _fixture("instagram")
        if host.startswith("rupload"):
            if mode == "timeout":
                raise httpx.ReadTimeout("fake timeout", request=request)
            return httpx.Response(200, text="oops" if mode == "malformed" else json.dumps(fx["upload_ok"]))
        if path.endswith("/media"):
            return httpx.Response(400, json=fx["error_param"]) if mode == "refuse" else httpx.Response(200, json=fx["container_created"])
        if path.endswith("/media_publish"):
            return httpx.Response(200, json=fx["publish_ok"])
        fields = request.url.params.get("fields", "")
        if "status_code" in fields:
            wanted = FAKE_PUBLISHING["status"].get("instagram_reels")
            if wanted == "complete":
                return httpx.Response(200, json={**fx["status_finished"], **fx["permalink"]})
            state = "status_in_progress" if mode == "processing" or wanted == "processing" else "status_error" if mode == "error" or wanted == "failed" else "status_finished"
            return httpx.Response(200, json=fx[state])
        return httpx.Response(200, json=fx["permalink"])

    def _serve_tiktok(self, request, host, path) -> httpx.Response:
        mode, fx = self._mode("tiktok"), _fixture("tiktok")
        if path.endswith("/inbox/video/init/"):
            return httpx.Response(403, json=fx["error_spam"]) if mode == "refuse" else httpx.Response(200, json=fx["init_ok"])
        if path.endswith("/status/fetch/"):
            wanted = FAKE_PUBLISHING["status"].get("tiktok", "inbox")
            return httpx.Response(200, json=fx[{"processing": "status_processing", "failed": "status_failed", "complete": "status_complete"}.get(wanted, "status_inbox")])
        if mode == "timeout":
            raise httpx.ReadTimeout("fake timeout", request=request)
        return httpx.Response(500 if mode == "malformed" else 201, text="")
