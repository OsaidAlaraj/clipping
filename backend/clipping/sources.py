"""Footage intake (decision 2026-11-17): upload from the phone or computer, or fetch from a Google Drive,
Dropbox or direct link. YouTube only when the owner confirms the brief permits clipping that video, because
YouTube's terms forbid downloads; it also needs the optional yt-dlp package."""
from __future__ import annotations

import re
import shutil
import uuid
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import campaigns, config, ffmpeg, language_check, signals
from .adapters import heatmap
from .core import AppError, GateError, audit, enqueue
from .db import Actor

MAX_SOURCE_BYTES = 10 * 1024 ** 3   # 10 GB
YOUTUBE_HOSTS = ("youtube.com", "youtu.be")


def link_kind(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if any(host == h or host.endswith("." + h) for h in YOUTUBE_HOSTS):
        return "youtube"
    if host.endswith("drive.google.com") or host.endswith("docs.google.com"):
        return "drive"
    if host.endswith("dropbox.com") or host.endswith("dropboxusercontent.com"):
        return "dropbox"
    return "direct"


def direct_download_url(url: str) -> str:
    """Turn a share link into a link that returns the file itself."""
    kind = link_kind(url)
    if kind == "drive":
        match = re.search(r"/d/([A-Za-z0-9_-]{10,})", url) or re.search(r"[?&]id=([A-Za-z0-9_-]{10,})", url)
        if not match:
            raise AppError("That Google Drive link has no file id. Share a single file, not a folder.", "bad_link")
        return f"https://drive.usercontent.google.com/download?id={match.group(1)}&export=download&confirm=t"
    if kind == "dropbox":
        parsed = urlparse(url)
        kept = [f"{k}={v[0]}" for k, v in parse_qs(parsed.query).items() if k != "dl"]
        return parsed._replace(query="&".join(kept + ["dl=1"])).geturl()
    return url


def save_upload(stream, filename: str | None) -> Path:
    """Write an uploaded file to disk in 1 MB chunks."""
    suffix = Path(filename or "upload.mp4").suffix.lower()
    if suffix not in (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"):
        suffix = ".mp4"
    dest = config.data_dir() / "incoming" / f"{uuid.uuid4()}{suffix}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with open(dest, "wb") as fh:
        while True:
            chunk = stream.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_SOURCE_BYTES:
                fh.close()
                dest.unlink(missing_ok=True)
                raise AppError("The file is larger than 10 GB.", "too_large")
            fh.write(chunk)
    return dest


def register_upload(conn, actor, campaign_id, path: Path, language: str, permission_ref: str | None, filename: str | None) -> dict:
    try:
        try:
            ffmpeg.probe(path)
        except Exception:
            raise AppError("That file is not a video the system can read.", "not_a_video") from None
        detected = language_check.detect_language(path)
        result = campaigns.register_source(conn, actor, campaign_id, str(path), language, permission_ref=permission_ref,
                                           origin_url=f"upload:{filename or path.name}")
        if isinstance(result, dict):   # the owner's choice stands; the answer carries the detection so the form can pre-select and warn
            result = {**result, "detected_language": detected, "language_warning": language_check.mismatch_message(language, detected)}
        return result
    finally:
        path.unlink(missing_ok=True)


def queue_link(conn, actor, campaign_id, url: str, language: str, permission_ref: str | None, youtube_permitted: bool = False) -> dict:
    campaign = campaigns.get_campaign(conn, campaign_id)
    if campaign["status"] != "approved":
        raise GateError("Only an approved campaign can take source footage.")
    if language not in ("en", "ar"):
        raise AppError("Language must be en or ar.", "bad_language")
    if not (permission_ref and permission_ref.strip()):
        raise AppError("Permitted footage only: give a licence or written-permission reference.", "no_permission")
    if not re.match(r"^https?://", url or "", re.I):
        raise AppError("Paste a full link that starts with https://", "bad_link")
    kind = link_kind(url)
    if kind == "youtube":
        if not youtube_permitted:
            raise AppError("YouTube's terms forbid downloading. Confirm the brief explicitly permits clipping this video.", "youtube_needs_ok")
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            raise AppError("YouTube downloads need the optional yt-dlp package, which is not installed. "
                           "Ask the campaign for a file link instead.", "youtube_unavailable") from None
        audit(conn, "source.youtube_risk_accepted", "campaigns", campaign_id, {"kind": "youtube"})
    elif kind == "drive":
        direct_download_url(url)   # validates the file id now, not later in the worker
    job = enqueue(conn, "fetch_source", {"campaign_id": str(campaign_id), "url": url, "language": language,
                                         "permission_ref": permission_ref.strip(), "kind": kind},
                  campaign["owner_operator_id"], campaign_version_id=campaign["approved_version_id"])
    if job is None:
        raise AppError("This link is already being fetched for this campaign.", "duplicate")
    return {"job_id": str(job["id"]), "kind": kind}


def download_to(url: str, dest: Path) -> Path:
    """Stream a file to disk. A web page instead of a file (for example a private Drive file) is refused."""
    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with httpx.Client(timeout=httpx.Timeout(60, read=300), follow_redirects=True) as client:
        with client.stream("GET", direct_download_url(url), headers={"User-Agent": "clipping-footage-fetch/1.0"}) as response:
            if response.status_code >= 400:
                raise AppError(f"The link returned HTTP {response.status_code}.", "fetch_failed")
            if response.headers.get("content-type", "").startswith("text/html"):
                raise AppError("The link opened a web page, not a video. Make the file viewable by anyone with the link.", "not_a_file")
            with open(dest, "wb") as fh:
                for chunk in response.iter_bytes(1 << 20):
                    size += len(chunk)
                    if size > MAX_SOURCE_BYTES:
                        raise AppError("The file is larger than 10 GB.", "too_large")
                    fh.write(chunk)
    return dest


def download_youtube(url: str, folder: Path, info_out: dict | None = None) -> Path:
    """Download one YouTube video. yt-dlp's metadata (the "most replayed" heatmap) is copied into `info_out` and the
    stream's live chat replay is saved beside the video when there is one; neither is needed for the download to work."""
    import yt_dlp

    folder.mkdir(parents=True, exist_ok=True)
    options = {"outtmpl": str(folder / "%(id)s.%(ext)s"), "format": "mp4/best", "quiet": True,
               "writesubtitles": True, "subtitleslangs": ["live_chat"]}
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        if info_out is not None and isinstance(info, dict):
            info_out.update({"heatmap": info.get("heatmap")})
        return Path(ydl.prepare_filename(info))


def youtube_signal_inputs(info: dict, folder: Path) -> tuple[list | None, list | None]:
    """(heatmap, chat counts) from a fetch: each None when absent or malformed."""
    chat_files = sorted(folder.glob("*.live_chat.json"))[:1]
    return heatmap.heatmap_from_info(info), (heatmap.read_live_chat(chat_files[0]) if chat_files else None)


def stage_fetch_source(conn, job: dict) -> Decimal:
    payload = job["payload"]
    folder = config.data_dir() / "incoming" / str(uuid.uuid4())
    try:
        info: dict = {}
        path = (download_youtube(payload["url"], folder, info) if payload["kind"] == "youtube"
                else download_to(payload["url"], folder / "source.mp4"))
        try:
            ffmpeg.probe(path)
        except Exception:
            raise AppError("The downloaded file is not a video the system can read.", "not_a_video") from None
        source = campaigns.register_source(conn, Actor.worker(), payload["campaign_id"], str(path), payload["language"],
                                           permission_ref=payload["permission_ref"], origin_url=payload["url"])
        if payload["kind"] == "youtube":   # reached only after the owner confirmed the brief permits this video
            signals.record_inputs(conn, source, *youtube_signal_inputs(info, folder))
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return Decimal("0")
