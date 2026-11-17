"""Vendor adapters and their fakes: source (A1), LLM (A2), moment finding (A4),
scheduler (A7), storage (A8), alerts (A10), metrics and payout records (A11, A12).

Live vendor adapters need keys (docs/open-items.md). None has been run against a live service in this
build: tests use the fakes only. Vizard, Klap and Buffer request shapes are best-effort and must be
checked against the vendor's current documentation before the adapter is switched on."""
from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import time
from decimal import Decimal
from pathlib import Path

from .. import config, ffmpeg
from .base import Adapter, AdapterError, check_response, http_client, register
from .transcription import FAKE_SENTENCES, fake_words  # noqa: F401  (registers the A3 providers; re-exported for older imports)


# ====================================================================== A1 campaign source

@register
class PasteSource(Adapter):
    """Manual paste. Always available (hard rule 3)."""
    stage, provider = "source", "paste"

    def list_campaigns(self, marketplace: dict) -> list[dict]:
        return []

    def read_brief(self, ref: dict) -> dict:
        return {"text": ref["text"], "url": ref.get("url")}

    def check_changes(self, ref: dict, last_hash: str) -> dict:
        """A pasted brief can only be re-checked by the owner pasting the current text."""
        text = ref.get("text")
        if text is None:
            return {"changed": None, "needs_owner": True}
        new_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return {"changed": new_hash != last_hash, "hash": new_hash, "text": text}


class ScraperDisabled(AdapterError):
    pass


@register
class ScraperSource(Adapter):
    """One scraper per marketplace, off by default. It refuses to run until the owner has read the
    marketplace's terms and recorded the version and date (hard rule 3, open item O9)."""
    stage, provider = "source", "scraper"

    @staticmethod
    def _require_terms(marketplace: dict) -> None:
        if not (marketplace.get("scraper_enabled") and marketplace.get("terms_version")
                and marketplace.get("terms_read_at") and marketplace.get("terms_confirmed_by")):
            raise ScraperDisabled(f"scraper for {marketplace.get('name')} is off until its terms are confirmed")

    def _fetch(self, url: str) -> str:
        time.sleep(float(self.cfg.get("min_seconds_between_requests", 10)) if self.cfg.get("throttle", True) else 0)
        with http_client() as client:
            response = client.get(url, headers={"User-Agent": "clipping-campaign-reader/1.0"}, follow_redirects=True)
        if response.status_code >= 400:
            raise AdapterError(f"HTTP {response.status_code}")
        return response.text

    def list_campaigns(self, marketplace: dict) -> list[dict]:
        self._require_terms(marketplace)
        listing = (marketplace.get("fee_rules") or {}).get("listing_url") or self.cfg.get("listing_urls", {}).get(marketplace["slug"])
        if not listing:
            raise AdapterError("no listing URL is configured for this marketplace")
        import re

        html = self._fetch(listing)
        pattern = self.cfg.get("link_patterns", {}).get(marketplace["slug"])
        links = sorted(set(re.findall(pattern, html))) if pattern else []
        return [{"external_ref": link, "url": link} for link in links]

    def read_brief(self, ref: dict) -> dict:
        self._require_terms(ref["marketplace"])
        import re

        html = self._fetch(ref["url"])
        text = re.sub(r"(?s)<(script|style)[^>]*>.*?</\1>", " ", html)
        text = re.sub(r"<[^>]+>", " ", text)
        return {"text": re.sub(r"\s+", " ", text).strip(), "url": ref["url"]}

    def check_changes(self, ref: dict, last_hash: str) -> dict:
        brief = self.read_brief(ref)
        new_hash = hashlib.sha256(brief["text"].encode("utf-8")).hexdigest()
        return {"changed": new_hash != last_hash, "hash": new_hash, "text": brief["text"]}


FAKE_SOURCE: dict = {"campaigns": [], "briefs": {}}


@register
class FakeSource(Adapter):
    stage, provider = "source", "fake"

    def list_campaigns(self, marketplace: dict) -> list[dict]:
        ScraperSource._require_terms(marketplace)
        return list(FAKE_SOURCE["campaigns"])

    def read_brief(self, ref: dict) -> dict:
        return {"text": FAKE_SOURCE["briefs"][ref["external_ref"]], "url": ref.get("url")}

    def check_changes(self, ref: dict, last_hash: str) -> dict:
        text = FAKE_SOURCE["briefs"][ref["external_ref"]]
        new_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return {"changed": new_hash != last_hash, "hash": new_hash, "text": text}


# ====================================================================== A2 LLM

LLM_SYSTEM = {
    "rules": (
        "You extract campaign rules from a clipping-campaign brief into JSON matching the given schema. "
        "Every rule and every payout figure must carry a quote copied word for word from the brief. "
        "If the brief does not state something, use null or 'unclear'. Never infer a rule. "
        "You may flag risks and hard blocks. You never approve anything."),
    "hooks": (
        "You write 2 to 3 short hook lines for a short video clip, as JSON {\"hooks\":[{\"text\",\"type\",\"score\"}]}. "
        "type is one of question, contrarian, stakes, number, story-open. score is 0 to 1. "
        "Write in the language given. Do not invent quotes: anything in quotation marks must appear in the transcript."),
    "compliance": (
        "You check a clip's caption, hook and overlay text against campaign rules. Return JSON "
        "{\"results\":[{\"rule_index\",\"status\",\"evidence\"}]} with status pass, review or fail. "
        "Evidence must be copied from the clip text. If you cannot confirm a rule, answer review. "
        "You never certify legal compliance, judge copyright or verify licences."),
}


@register
class AzureOpenAILLM(Adapter):
    stage, provider = "llm", "azure_openai"
    requires_env = ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT")

    def cost(self, request: dict) -> Decimal | None:
        price = self.cfg.get("price_per_1k_tokens_usd")
        if price is None:
            return None
        tokens = len(json.dumps(request.get("payload", {}), ensure_ascii=False)) / 3 + 800
        return (Decimal(str(price)) * Decimal(tokens) / 1000).quantize(Decimal("0.0001"))

    def complete_json(self, task: str, payload: dict) -> dict:
        endpoint = config.env("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
        deployment = config.env("AZURE_OPENAI_DEPLOYMENT")
        version = self.cfg.get("api_version", "2024-10-21")
        body = {"messages": [{"role": "system", "content": LLM_SYSTEM[task]},
                             {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                "response_format": {"type": "json_object"}}
        if not _reasoning_model(deployment):
            body["temperature"] = 0   # reasoning models (gpt-5 family, o-series) accept only their default
        with http_client() as client:
            response = client.post(
                f"{endpoint}/openai/deployments/{deployment}/chat/completions", params={"api-version": version},
                headers={"api-key": config.env("AZURE_OPENAI_API_KEY", "")}, json=body,
            )
        data = check_response(response)
        return json.loads(data["choices"][0]["message"]["content"])


def _reasoning_model(deployment: str | None) -> bool:
    name = (deployment or "").lower()
    return name.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4"))


VISION_PROMPT = ("Copy all the text in this screenshot of a clipping-campaign page exactly as written, line by line. "
                 "Do not summarise, translate or add anything. Return JSON {\"text\": \"...\"}.")


def _azure_read_image(image_b64: str, mime: str) -> dict:
    endpoint = config.env("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    deployment = config.env("AZURE_OPENAI_VISION_DEPLOYMENT") or config.env("AZURE_OPENAI_DEPLOYMENT")
    body = {"messages": [{"role": "user", "content": [{"type": "text", "text": VISION_PROMPT},
                                                      {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}}]}],
            "response_format": {"type": "json_object"}}
    if not _reasoning_model(deployment):
        body["temperature"] = 0
    with http_client() as client:
        response = client.post(
            f"{endpoint}/openai/deployments/{deployment}/chat/completions", params={"api-version": "2024-10-21"},
            headers={"api-key": config.env("AZURE_OPENAI_API_KEY", "")}, json=body,
        )
    return json.loads(check_response(response)["choices"][0]["message"]["content"])


AzureOpenAILLM.read_image = lambda self, image_b64, mime: _azure_read_image(image_b64, mime)  # type: ignore[attr-defined]


@register
class FakeLLM(Adapter):
    """Deterministic stand-in for the LLM. See fake_llm.py."""
    stage, provider = "llm", "fake"

    def complete_json(self, task: str, payload: dict) -> dict:
        from . import fake_llm

        return fake_llm.complete(task, payload)

    def read_image(self, image_b64: str, mime: str) -> dict:
        from . import fake_llm
        from ..core import AppError

        if fake_llm.FAKE_IMAGE_TEXT is None:
            raise AppError("Reading screenshots needs a vision-capable model (Azure). Paste the text instead for now.", "needs_vision")
        return {"text": fake_llm.FAKE_IMAGE_TEXT}


# A3 transcription lives in transcription.py (Deepgram, local Whisper, fake); the names below keep older imports working.


# ====================================================================== A4 moment finding

@register
class VizardMoments(Adapter):
    """Vizard. Unverified request shape: check docs.vizard.ai before switching on. Needs a public source URL."""
    stage, provider = "moments", "vizard"
    requires_env = ("VIZARD_API_KEY",)
    BASE = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1"

    def cost(self, request: dict) -> Decimal | None:
        return Decimal("0")  # flat plan; usage is counted in minutes against the plan's pool

    def units(self, request: dict) -> Decimal:
        return (Decimal(str(request.get("options", {}).get("duration_s", 0))) / 60).quantize(Decimal("0.01"))

    def _headers(self) -> dict:
        return {"VIZARDAI_API_KEY": config.env("VIZARD_API_KEY", ""), "Content-Type": "application/json"}

    def submit(self, source: dict, options: dict) -> dict:
        if not str(source.get("url", "")).startswith("https://"):
            raise AdapterError("Vizard needs a public https source URL (use R2 storage)")
        with http_client() as client:
            data = check_response(client.post(f"{self.cfg.get('base_url', self.BASE)}/project/create", headers=self._headers(),
                                      json={"lang": options.get("language", "en"), "videoUrl": source["url"],
                                            "videoType": 1, "ext": "mp4", "preferLength": [0]}))
        if "projectId" not in data:
            raise AdapterError("Vizard did not return a project id")
        return {"job_id": str(data["projectId"])}

    def status(self, job_id: str) -> dict:
        with http_client() as client:
            data = check_response(client.get(f"{self.cfg.get('base_url', self.BASE)}/project/query/{job_id}", headers=self._headers()))
        return {"done": bool(data.get("videos")), "raw": data}

    def clips(self, job_id: str) -> list[dict]:
        raw = self.status(job_id)["raw"]
        out = []
        for video in raw.get("videos") or []:
            start, end = video.get("startTime"), video.get("endTime")
            if start is None or end is None:
                raise AdapterError("Vizard response has no clip start and end; use manual selection")
            out.append({"start_s": float(start) / 1000, "end_s": float(end) / 1000,
                        "score": float(video.get("viralScore", 0)) / 10 if video.get("viralScore") is not None else None,
                        "reason": video.get("viralReason"), "reframed_url": video.get("videoUrl")})
        return out


@register
class KlapMoments(Adapter):
    """Klap. Unverified request shape: check Klap's API docs before switching on."""
    stage, provider = "moments", "klap"
    requires_env = ("KLAP_API_KEY",)

    def cost(self, request: dict) -> Decimal | None:
        if "source" not in request:
            return Decimal("0")   # only submit is charged; status and clips are free
        per_input, per_short = self.cfg.get("usd_per_input_video"), self.cfg.get("usd_per_short")
        if per_input is None or per_short is None:
            return None
        return Decimal(str(per_input)) + Decimal(str(per_short)) * int(request.get("options", {}).get("max_clips", 3))

    def units(self, request: dict) -> Decimal:
        return Decimal(1 if "source" in request else 0)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {config.env('KLAP_API_KEY', '')}"}

    def submit(self, source: dict, options: dict) -> dict:
        with http_client() as client:
            data = check_response(client.post(f"{self.cfg.get('base_url', 'https://api.klap.app/v2')}/tasks/video-to-shorts",
                                      headers=self._headers(),
                                      json={"source_video_url": source.get("url"), "language": options.get("language", "en"),
                                            "max_clip_count": options.get("max_clips", 3)}))
        return {"job_id": str(data.get("id"))}

    def status(self, job_id: str) -> dict:
        with http_client() as client:
            data = check_response(client.get(f"{self.cfg.get('base_url', 'https://api.klap.app/v2')}/tasks/{job_id}", headers=self._headers()))
        return {"done": data.get("status") == "ready", "raw": data}

    def clips(self, job_id: str) -> list[dict]:
        raw = self.status(job_id)["raw"]
        with http_client() as client:
            items = check_response(client.get(f"{self.cfg.get('base_url', 'https://api.klap.app/v2')}/projects/{raw.get('output_id')}",
                                      headers=self._headers()))
        out = []
        for item in items if isinstance(items, list) else []:
            if item.get("start") is None or item.get("end") is None:
                raise AdapterError("Klap response has no clip start and end; use manual selection")
            out.append({"start_s": float(item["start"]), "end_s": float(item["end"]),
                        "score": (item.get("virality_score") or 0) / 100, "reason": item.get("virality_score_explanation")})
        return out


@register
class OpusClipMoments(Adapter):
    """OpusClip. API access is on the quote-only Business plan (D2, O10). The adapter slot exists and
    stays off; the request code is written once a quote and API documentation are in hand."""
    stage, provider = "moments", "opusclip"
    requires_env = ("OPUSCLIP_API_KEY",)

    def cost(self, request: dict) -> Decimal | None:
        return None

    def submit(self, source: dict, options: dict) -> dict:
        raise AdapterError("OpusClip is not available until a Business quote is recorded (O10)")

    status = clips = submit  # type: ignore[assignment]


@register
class FakeMoments(Adapter):
    """Returns three candidate windows with deliberately rough edges, so boundary snapping is exercised."""
    stage, provider = "moments", "fake"

    def submit(self, source: dict, options: dict) -> dict:
        return {"job_id": f"fake:{float(options.get('duration_s', 60)):.2f}"}

    def status(self, job_id: str) -> dict:
        return {"done": True}

    def clips(self, job_id: str) -> list[dict]:
        duration = float(job_id.split(":", 1)[1])
        windows = [(3.3, 16.7, 0.91, "Strong opening claim"), (21.4, 33.1, 0.78, "Personal story"),
                   (37.2, 51.6, 0.66, "Clear takeaway")]
        return [{"start_s": s, "end_s": e, "score": score, "reason": why}
                for s, e, score, why in windows if e <= duration]


# ====================================================================== A7 scheduler

@register
class ManualScheduler(Adapter):
    """Manual native posting (weeks 1 to 3, and always the fallback)."""
    stage, provider = "scheduler", "manual"

    def schedule(self, post: dict) -> dict:
        return {"ref": None, "manual": True}

    def cancel(self, id: str) -> dict:
        return {"cancelled": True}

    def status(self, id: str) -> dict:
        return {"status": "manual"}

    def report(self) -> dict:
        return {"channels_used": 0, "channels_limit": None, "queued_per_channel": {}, "posts_per_day": {}}


@register
class BufferScheduler(Adapter):
    """Buffer (from week 4). Unverified request shape: check Buffer's current API before switching on.
    It schedules a post the owner has already approved; it never decides to post. The paid-partnership
    toggle is assumed missing (O3), so it stays a manual checklist step."""
    stage, provider = "scheduler", "buffer"
    requires_env = ("BUFFER_ACCESS_TOKEN",)

    def units(self, request: dict) -> Decimal:
        return Decimal(0)

    def schedule(self, post: dict) -> dict:
        with http_client() as client:
            data = check_response(client.post(f"{self.cfg.get('base_url', 'https://api.bufferapp.com/1')}/updates/create.json",
                                      headers={"Authorization": f"Bearer {config.env('BUFFER_ACCESS_TOKEN', '')}"},
                                      data={"profile_ids[]": post["channel_id"], "text": post["caption"],
                                            "media[video]": post["video_url"], "scheduled_at": post["scheduled_at"]}))
        updates = data.get("updates") or [{}]
        return {"ref": str(updates[0].get("id")), "manual": False}

    def cancel(self, id: str) -> dict:
        with http_client() as client:
            check_response(client.post(f"{self.cfg.get('base_url', 'https://api.bufferapp.com/1')}/updates/{id}/destroy.json",
                               headers={"Authorization": f"Bearer {config.env('BUFFER_ACCESS_TOKEN', '')}"}))
        return {"cancelled": True}

    def status(self, id: str) -> dict:
        with http_client() as client:
            data = check_response(client.get(f"{self.cfg.get('base_url', 'https://api.bufferapp.com/1')}/updates/{id}.json",
                                     headers={"Authorization": f"Bearer {config.env('BUFFER_ACCESS_TOKEN', '')}"}))
        return {"status": data.get("status")}

    def report(self) -> dict:
        return {"channels_used": int(self.row.get("quota_used") or 0), "channels_limit": self.cfg.get("channels_limit"),
                "queued_per_channel": {}, "posts_per_day": {}}


FAKE_SCHEDULER: dict = {"posts": {}, "channels": set(), "fail": False}


@register
class FakeScheduler(Adapter):
    stage, provider = "scheduler", "fake"

    def schedule(self, post: dict) -> dict:
        if FAKE_SCHEDULER["fail"]:
            raise AdapterError("fake scheduler failure")
        limit = int(self.cfg.get("queued_per_channel_limit", 10))
        queued = [p for p in FAKE_SCHEDULER["posts"].values() if p["channel_id"] == post["channel_id"]]
        if len(queued) >= limit:
            raise AdapterError("queue limit reached for this channel")
        ref = f"fake-{len(FAKE_SCHEDULER['posts']) + 1}"
        FAKE_SCHEDULER["posts"][ref] = dict(post)
        FAKE_SCHEDULER["channels"].add(post["channel_id"])
        return {"ref": ref, "manual": False}

    def cancel(self, id: str) -> dict:
        return {"cancelled": FAKE_SCHEDULER["posts"].pop(id, None) is not None}

    def status(self, id: str) -> dict:
        return {"status": "scheduled" if id in FAKE_SCHEDULER["posts"] else "unknown"}

    def report(self) -> dict:
        per_channel: dict = {}
        for post in FAKE_SCHEDULER["posts"].values():
            per_channel[post["channel_id"]] = per_channel.get(post["channel_id"], 0) + 1
        return {"channels_used": len(FAKE_SCHEDULER["channels"]), "channels_limit": self.cfg.get("channels_limit"),
                "queued_per_channel": per_channel, "posts_per_day": {}}


# ====================================================================== A8 storage

def _media_secret() -> bytes:
    secret = config.env("CLIPPING_MEDIA_SECRET")
    if not secret:
        path = config.data_dir() / "media.secret"
        if not path.exists():
            import secrets

            path.write_text(secrets.token_hex(32), encoding="utf-8")
        secret = path.read_text(encoding="utf-8").strip()
    return secret.encode("utf-8")


def sign_media(key: str, expires_at: int) -> str:
    return hmac.new(_media_secret(), f"{key}:{expires_at}".encode("utf-8"), hashlib.sha256).hexdigest()


def verify_media(key: str, expires_at: int, signature: str) -> bool:
    return expires_at >= int(time.time()) and hmac.compare_digest(sign_media(key, expires_at), signature)


@register
class LocalStorage(Adapter):
    """Local disk. Media is still served only through short-lived signed links."""
    stage, provider = "storage", "local"

    @staticmethod
    def root() -> Path:
        root = config.data_dir() / "storage"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def path(self, key: str) -> Path:
        path = (self.root() / key).resolve()
        if self.root().resolve() not in path.parents:
            raise ValueError("bad storage key")
        return path

    def put(self, key: str, src: str) -> dict:
        dest = self.path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if Path(src).resolve() != dest:
            shutil.copyfile(src, dest)
        return {"key": key, "bytes": dest.stat().st_size}

    def get_signed_url(self, key: str, expiry: int = 600) -> str:
        expires_at = int(time.time()) + int(expiry)
        return f"/api/media/{key}?exp={expires_at}&sig={sign_media(key, expires_at)}"

    def delete(self, key: str) -> dict:
        path = self.path(key)
        existed = path.exists()
        path.unlink(missing_ok=True)
        return {"deleted": existed}

    def usage(self) -> dict:
        total = sum(p.stat().st_size for p in self.root().rglob("*") if p.is_file())
        return {"bytes": total}


@register
class R2Storage(Adapter):
    """Cloudflare R2 through its S3-compatible API. Needs boto3 (pip install boto3) and R2 keys."""
    stage, provider = "storage", "r2"
    requires_env = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")

    def _client(self):
        try:
            import boto3  # type: ignore
        except ImportError as exc:
            raise AdapterError("boto3 is not installed") from exc
        return boto3.client("s3", endpoint_url=f"https://{config.env('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com",
                            aws_access_key_id=config.env("R2_ACCESS_KEY_ID"),
                            aws_secret_access_key=config.env("R2_SECRET_ACCESS_KEY"), region_name="auto")

    def path(self, key: str) -> Path:
        """Local working copy, downloaded on demand."""
        local = LocalStorage.root() / key
        if not local.exists():
            local.parent.mkdir(parents=True, exist_ok=True)
            self._client().download_file(config.env("R2_BUCKET"), key, str(local))
        return local

    def put(self, key: str, src: str) -> dict:
        self._client().upload_file(src, config.env("R2_BUCKET"), key)
        return {"key": key, "bytes": Path(src).stat().st_size}

    def get_signed_url(self, key: str, expiry: int = 600) -> str:
        return self._client().generate_presigned_url("get_object", Params={"Bucket": config.env("R2_BUCKET"), "Key": key},
                                                     ExpiresIn=int(expiry))

    def delete(self, key: str) -> dict:
        self._client().delete_object(Bucket=config.env("R2_BUCKET"), Key=key)
        (LocalStorage.root() / key).unlink(missing_ok=True)
        return {"deleted": True}

    def usage(self) -> dict:
        total = 0
        for page in self._client().get_paginator("list_objects_v2").paginate(Bucket=config.env("R2_BUCKET")):
            total += sum(obj["Size"] for obj in page.get("Contents", []))
        return {"bytes": total}


# ====================================================================== A10 alerts

@register
class InAppAlerts(Adapter):
    """In-app inbox only. The alert row itself is the delivery."""
    stage, provider = "alerts", "inapp"

    def send(self, alert: dict, severity: str, channel: str = "inapp") -> dict:
        return {"channel": "inapp", "sent": 1}


@register
class TelegramAlerts(Adapter):
    stage, provider = "alerts", "telegram"
    requires_env = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")

    def send(self, alert: dict, severity: str, channel: str = "telegram") -> dict:
        token, chat = config.env("TELEGRAM_BOT_TOKEN"), config.env("TELEGRAM_CHAT_ID")
        if not token or not chat:
            raise AdapterError("Telegram token or chat id is not set")
        text = f"[{severity.upper()}] {alert['message']}"
        times = 2 if severity == "block" else 1   # block alerts are sent twice (spec 10.3)
        try:
            with http_client() as client:
                for _ in range(times):
                    response = client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                           json={"chat_id": chat, "text": text})
                    if response.status_code >= 400:
                        raise AdapterError(f"Telegram HTTP {response.status_code}")
        except AdapterError:
            raise
        except Exception as exc:  # never let the URL (which holds the token) into an error message
            raise AdapterError(f"Telegram request failed: {type(exc).__name__}") from None
        return {"channel": "telegram", "sent": times}


FAKE_ALERTS: dict = {"sent": [], "fail": False}


@register
class FakeAlerts(Adapter):
    stage, provider = "alerts", "fake"

    def send(self, alert: dict, severity: str, channel: str = "telegram") -> dict:
        if FAKE_ALERTS["fail"]:
            raise AdapterError("fake alert failure")
        times = 2 if severity == "block" else 1
        for _ in range(times):
            FAKE_ALERTS["sent"].append({"severity": severity, "message": alert["message"], "type": alert.get("type")})
        return {"channel": "fake", "sent": times}


# ====================================================================== A9, A11, A12

@register
class PostgresDatabase(Adapter):
    stage, provider = "database", "postgres"


@register
class ManualMetrics(Adapter):
    """Entries, not API calls (spec 10.3)."""
    stage, provider = "metrics", "manual"


@register
class ManualPayouts(Adapter):
    stage, provider = "payouts", "manual"


def storage_for(conn, registry) -> Adapter:
    return registry.get(conn, "storage")


__all__ = ["ffmpeg"]
