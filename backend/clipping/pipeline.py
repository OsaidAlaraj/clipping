"""Production pipeline (spec 12.1): transcribe, moments, hooks, render, captions and overlay,
compliance, package. Each stage is an idempotent job that records its cost and queues the next stage.
The worker can run every stage, but it cannot pass a human gate (database triggers)."""
from __future__ import annotations

import tempfile
import time
from decimal import Decimal
from pathlib import Path

from psycopg.types.json import Jsonb

from . import config, ffmpeg, transcripts
from .adapters.base import AdapterPaused
from .agents import compliance as agent_c
from .agents import hooks as agent_b
from .alerts import raise_alert, registry
from .core import AppError, KillSwitchOn, audit, claim_job, enqueue, fail_job, finish_job, get_setting, kill_switch_on
from .db import Actor, Database

SENTENCE_END = ".?!؟"
LOCAL_TRANSCRIBER = "faster_whisper"   # the declared fallback of every paid transcription provider


# ------------------------------------------------------------------ boundary snapping (FR-13)

def sentence_bounds(words: list[dict], gap_s: float = 0.6) -> tuple[list[float], list[float]]:
    starts, ends = [], []
    for i, word in enumerate(words):
        prev, nxt = (words[i - 1] if i else None), (words[i + 1] if i + 1 < len(words) else None)
        if prev is None or prev["text"][-1:] in SENTENCE_END or word["start"] - prev["end"] > gap_s:
            starts.append(word["start"])
        if nxt is None or word["text"][-1:] in SENTENCE_END or nxt["start"] - word["end"] > gap_s:
            ends.append(word["end"])
    return starts, ends


def snap(start_s: float, end_s: float, words: list[dict], silences: list[tuple[float, float]] | None = None,
         max_shift: float = 3.0) -> tuple[float, float]:
    """Move a cut to the nearest sentence boundary, or failing that the nearest silence, within max_shift."""
    starts, ends = sentence_bounds(words)
    silences = silences or []

    def nearest(value: float, options: list[float]) -> float | None:
        close = [o for o in options if abs(o - value) <= max_shift]
        return min(close, key=lambda o: (abs(o - value), o)) if close else None

    new_start = nearest(start_s, starts)
    if new_start is None:
        new_start = nearest(start_s, [e for _, e in silences])
    new_end = nearest(end_s, [e for e in ends if e > (new_start if new_start is not None else start_s) + 1.0])
    if new_end is None:
        new_end = nearest(end_s, [s for s, _ in silences if s > start_s + 1.0])
    # A snapped cut gets a little breathing room; an unsnapped one is left exactly as given.
    start = start_s if new_start is None else round(max(new_start - 0.05, 0), 3)
    end = end_s if new_end is None else round(new_end + 0.15, 3)
    if end <= start:
        return start_s, end_s
    return start, end


# ------------------------------------------------------------------ helpers

def disclosure_template(conn, category_id, language: str) -> dict | None:
    """Template for the category and language, else the language default (D11)."""
    return conn.execute(
        """select * from disclosure_templates where language = %s and (category_id = %s or category_id is null)
           order by (category_id is null) limit 1""",
        (language, category_id),
    ).fetchone()


def _source_ctx(conn, source_id) -> tuple[dict, dict, dict]:
    source = conn.execute("select * from source_assets where id = %s", (source_id,)).fetchone()
    campaign = conn.execute("select * from campaigns where id = %s", (source["campaign_id"],)).fetchone()
    version = conn.execute("select * from campaign_versions where id = %s", (campaign["approved_version_id"],)).fetchone()
    return source, campaign, version


def _words(conn, source: dict) -> list[dict]:
    return transcripts.load(conn, source["id"])["words"]


def build_caption(disclosure: str, hook_text: str, rules: dict) -> tuple[str, list[str], list[str]]:
    """Caption that starts with the disclosure, then the hook, required mentions and required hashtags."""
    import re

    hashtags, mentions = [], []
    for rule in rules.get("content_rules") or []:
        if rule.get("requirement") != "required":
            continue
        for token in re.findall(r"[#@][\w؀-ۿ]+", rule.get("text") or ""):
            target = hashtags if token.startswith("#") else mentions
            if token.lower() != disclosure.lower() and token not in target:
                target.append(token)
    parts = [disclosure, hook_text, " ".join(mentions), " ".join(hashtags)]
    return " ".join(p for p in parts if p).strip(), hashtags, mentions


# ------------------------------------------------------------------ stage handlers. Each returns its cost in USD.

def _transcribe(conn, source: dict, campaign: dict, version: dict, video: Path) -> tuple[dict, Decimal]:
    """Extract the audio and send it to the active transcription provider. When that provider is paused (cost cap,
    no price) the local Whisper takes over; three failed calls fall back inside the registry."""
    owner = source["owner_operator_id"]
    tmp_root = config.data_dir() / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    request = {"language": source["language"], "dialect_hint": source["dialect"],
               "duration_s": float(source["duration_s"] or 0), "keyterms": transcripts.key_terms_for(campaign, version)}
    with tempfile.TemporaryDirectory(dir=tmp_root) as folder:
        audio = ffmpeg.extract_audio(video, Path(folder) / "audio.wav")
        request["audio_path"] = str(audio)
        try:
            return registry.call(conn, "transcription", "transcribe", request, owner)
        except AdapterPaused as exc:
            raise_alert(conn, owner, "transcription_paused", "warn", "source_assets", source["id"],
                        f"{campaign['name']}: {exc} The local Whisper transcribes this video instead.")
            return registry.call(conn, "transcription", "transcribe", request, owner, provider=LOCAL_TRANSCRIBER)


def stage_transcribe(conn, job: dict) -> Decimal:
    source, campaign, version = _source_ctx(conn, job["payload"]["source_id"])
    path = registry.get(conn, "storage").path(source["stored_path"])
    if not path.exists() or ffmpeg.sha256_file(path) != source["file_hash"]:
        raise AppError("Source file hash mismatch. The job is stopped.", "hash_mismatch")   # S3
    cost = Decimal("0")
    if transcripts.find(conn, source) is None:
        result, cost = _transcribe(conn, source, campaign, version, path)
        transcripts.create(conn, source, result)
        if result.get("degraded"):
            raise_alert(conn, source["owner_operator_id"], "transcription_degraded", "warn", "source_assets", source["id"],
                        f"{campaign['name']}: {result['degraded']}. It works, but slowly.")
    enqueue(conn, "moments", {"source_id": str(source["id"])}, source["owner_operator_id"],
            source_id=source["id"], campaign_version_id=version["id"])
    return cost


def stage_moments(conn, job: dict) -> Decimal:
    source, campaign, version = _source_ctx(conn, job["payload"]["source_id"])
    owner = source["owner_operator_id"]
    storage = registry.get(conn, "storage")
    options = {"language": source["language"], "duration_s": float(source["duration_s"] or 0), "max_clips": 5}
    try:
        submitted, cost = registry.call(conn, "moments", "submit", {
            "source": {"key": source["stored_path"], "url": storage.get_signed_url(source["stored_path"], 3600)},
            "options": options}, owner)
        for _ in range(120):
            status, _ = registry.call(conn, "moments", "status", {"job_id": submitted["job_id"]}, owner)
            if status.get("done"):
                break
            time.sleep(5)
        else:
            raise AppError("Moment finding did not finish in time.", "moments_timeout")
        found, _ = registry.call(conn, "moments", "clips", {"job_id": submitted["job_id"]}, owner)
    except KillSwitchOn:
        raise
    except AppError as exc:
        # Declared fallback: manual selection in the review tool.
        raise_alert(conn, owner, "manual_selection", "warn", "source_assets", source["id"],
                    f"{campaign['name']}: moment finding is unavailable ({exc}). Pick the moments by hand.")
        return Decimal("0")
    provider = registry.row(conn, "moments")["provider"]
    words = _words(conn, source)
    quiet = ffmpeg.silences(storage.path(source["stored_path"]))
    candidates = []
    for item in found:
        start, end = snap(float(item["start_s"]), float(item["end_s"]), words, quiet)
        candidates.append({"start_s": start, "end_s": min(end, float(source["duration_s"] or end)),
                           "score": item.get("score"), "reason": item.get("reason")})
    return cost + _store_candidates(conn, source, campaign, version, candidates, provider)


def _store_candidates(conn, source: dict, campaign: dict, version: dict, candidates: list[dict], provider: str) -> Decimal:
    owner = source["owner_operator_id"]
    ranked = agent_b.rank_candidates(candidates)
    cap = int(get_setting(conn, "clips_per_campaign_per_week", campaign["id"]))
    made = conn.execute(
        "select count(*) as n from clips where campaign_id = %s and version_no = 1 and created_at > now() - interval '7 days'",
        (campaign["id"],),
    ).fetchone()["n"]
    room = max(cap - made, 0)
    room, share_note = _portfolio_room(conn, owner, campaign["id"], made, room, len(ranked))
    for candidate in ranked:
        row = conn.execute(
            """insert into candidates (source_id, owner_operator_id, provider, start_s, end_s, score, rank, reason)
               values (%s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (source["id"], owner, provider, candidate["start_s"], candidate["end_s"], candidate["score"], candidate["rank"],
             candidate["reason"]),
        ).fetchone()
        if candidate["rank"] <= room:
            enqueue(conn, "hooks", {"candidate_id": str(row["id"])}, owner, source_id=source["id"], campaign_version_id=version["id"])
    if len(ranked) > room:
        why = share_note or f"weekly clip cap of {cap} reached"
        raise_alert(conn, owner, "weekly_clip_cap", "warn", "campaigns", campaign["id"],
                    f"{campaign['name']}: {why}. {len(ranked) - room} moments were kept but not cut; they can be cut by hand.")
    return Decimal("0")


def _portfolio_room(conn, owner, campaign_id, made: int, room: int, wanted: int) -> tuple[int, str | None]:
    """Spread risk (practitioner lesson): while the owner has other approved campaigns, one campaign may take at
    most portfolio_max_share_pct of the week's clips. With a single approved campaign there is nothing to spread."""
    others = conn.execute(
        "select count(*) as n from campaigns where owner_operator_id = %s and status = 'approved' and id <> %s",
        (owner, campaign_id)).fetchone()["n"]
    if not others:
        return room, None
    share = float(get_setting(conn, "portfolio_max_share_pct", owner)) / 100
    week = conn.execute(
        "select count(*) as n from clips where owner_operator_id = %s and version_no = 1 and created_at > now() - interval '7 days'",
        (owner,)).fetchone()["n"]
    # Largest n with (made + n) <= share * (week + n), and always at least one clip so a new campaign can start.
    allowed = int((share * week - made) // (1 - share)) if share < 1 else wanted
    allowed = max(allowed, 1 if made == 0 else 0)
    if allowed < room:
        return max(allowed, 0), f"spreading risk: one campaign takes at most {round(share * 100)}% of the week's clips"
    return room, None


def add_manual_candidates(conn, actor, source_id, moments: list[dict]) -> int:
    """Manual selection (the A4 fallback): the owner gives start and end times."""
    source, campaign, version = _source_ctx(conn, source_id)
    if campaign["status"] != "approved":
        raise AppError("The campaign is not approved.", "bad_status")
    words = _words(conn, source)
    candidates = []
    for moment in moments:
        start, end = snap(float(moment["start_s"]), float(moment["end_s"]), words)
        candidates.append({"start_s": start, "end_s": end, "score": None, "reason": "Picked by hand"})
    _store_candidates(conn, source, campaign, version, candidates, "manual")
    return len(candidates)


def stage_hooks(conn, job: dict) -> Decimal:
    candidate = conn.execute("select * from candidates where id = %s", (job["payload"]["candidate_id"],)).fetchone()
    source, campaign, version = _source_ctx(conn, candidate["source_id"])
    owner = source["owner_operator_id"]
    words = [w for w in _words(conn, source) if w["start"] >= float(candidate["start_s"]) - 0.1 and w["end"] <= float(candidate["end_s"]) + 0.1]
    segment = " ".join(w["text"] for w in words)
    hooks, cost = agent_b.run(conn, registry, segment, source["language"], candidate["score"], owner)
    clip = conn.execute(
        """insert into clips (candidate_id, campaign_id, campaign_version_id, owner_operator_id, start_s, end_s, language, dialect, status)
           values (%s, %s, %s, %s, %s, %s, %s, %s, 'rendering') returning *""",
        (candidate["id"], campaign["id"], version["id"], owner, candidate["start_s"], candidate["end_s"],
         source["language"], source["dialect"]),
    ).fetchone()
    top = None
    for hook in hooks:
        row = conn.execute(
            """insert into hooks (candidate_id, clip_id, owner_operator_id, text, hook_type, chosen, rank, score)
               values (%s, %s, %s, %s, %s, false, %s, %s) returning id""",
            (candidate["id"], clip["id"], owner, hook["text"], hook["hook_type"], hook["rank"], hook["score"]),
        ).fetchone()
        if hook["rank"] == 1:
            top = {"hook_id": str(row["id"]), "hook_text": hook["text"], "hook_score": hook["score"]}
    conn.execute("update clips set render_meta = %s, hook_score = %s where id = %s",
                 (Jsonb({"hook_id": top["hook_id"], "hook_text": top["hook_text"]}), top["hook_score"], clip["id"]))
    conn.execute("update candidates set status = 'hooked' where id = %s", (candidate["id"],))
    enqueue(conn, "render", {"clip_id": str(clip["id"])}, owner, source_id=source["id"], campaign_version_id=version["id"])
    return cost


def _clip_ctx(conn, clip_id) -> tuple[dict, dict, dict, dict]:
    clip = conn.execute("select * from clips where id = %s", (clip_id,)).fetchone()
    candidate = conn.execute("select * from candidates where id = %s", (clip["candidate_id"],)).fetchone()
    source, campaign, _ = _source_ctx(conn, candidate["source_id"])
    version = conn.execute("select * from campaign_versions where id = %s", (clip["campaign_version_id"],)).fetchone()
    return clip, source, campaign, version


def stage_render(conn, job: dict) -> Decimal:
    clip, source, campaign, version = _clip_ctx(conn, job["payload"]["clip_id"])
    storage = registry.get(conn, "storage")
    key = f"clips/{clip['id']}/render.mp4"
    out = storage.path(key)
    result, cost = registry.call(conn, "render", "render", {
        "src": str(storage.path(source["stored_path"])), "start_s": float(clip["start_s"]), "end_s": float(clip["end_s"]),
        "out": str(out)}, clip["owner_operator_id"])
    storage.put(key, str(out))
    meta = dict(clip["render_meta"] or {}, render_key=key, reframe=result["reframe"],
                loudness_normalised=result["loudness_normalised"], render_hash=ffmpeg.sha256_file(out))
    conn.execute("update clips set render_meta = %s where id = %s", (Jsonb(meta), clip["id"]))
    conn.execute("update candidates set status = 'rendered' where id = %s", (clip["candidate_id"],))
    enqueue(conn, "captions", {"clip_id": str(clip["id"])}, clip["owner_operator_id"],
            source_id=source["id"], campaign_version_id=version["id"])
    return cost


def stage_captions(conn, job: dict) -> Decimal:
    clip, source, campaign, version = _clip_ctx(conn, job["payload"]["clip_id"])
    owner = clip["owner_operator_id"]
    template = disclosure_template(conn, campaign["category_id"], clip["language"])
    if template is None:
        raise_alert(conn, owner, "disclosure_missing", "block", "clips", clip["id"],
                    f"{campaign['name']}: no disclosure template for language {clip['language']}. The clip is blocked.")
        raise AppError("Disclosure text is missing. The clip is blocked.", "disclosure_missing")   # S8
    storage = registry.get(conn, "storage")
    meta = dict(clip["render_meta"] or {})
    key = f"clips/{clip['id']}/final.mp4"
    out = storage.path(key)
    result, cost = registry.call(conn, "captions", "burn_all", {
        "video": str(storage.path(meta["render_key"])), "words": _words(conn, source),
        "style": {"clip_start": float(clip["start_s"]), "clip_end": float(clip["end_s"]), "hook": meta.get("hook_text")},
        "label": template["overlay_text"], "out": str(out)}, owner)
    storage.put(key, str(out))
    info = ffmpeg.probe(out)
    caption = clip["caption_text"]
    hashtags, mentions = clip["hashtags"], clip["mentions"]
    if not (meta.get("caption_edited") and caption):
        caption, hashtags, mentions = build_caption(template["caption_text"], meta.get("hook_text") or "", version["rules_json"])
    meta.update(overlay_burned=result["overlay"] is not None, captions=result["captions"],
                caption_language=clip["language"], disclosure_template_id=str(template["id"]))
    conn.execute(
        """update clips set file_path = %s, file_hash = %s, duration_s = %s, width = %s, height = %s, overlay_json = %s,
               caption_text = %s, hashtags = %s, mentions = %s, render_meta = %s where id = %s""",
        (key, ffmpeg.sha256_file(out), info["duration_s"], info["width"], info["height"], Jsonb(result["overlay"]),
         caption, hashtags, mentions, Jsonb(meta), clip["id"]),
    )
    enqueue(conn, "compliance", {"clip_id": str(clip["id"])}, owner, source_id=source["id"], campaign_version_id=version["id"])
    return cost


def run_compliance(conn, clip: dict, campaign: dict, version: dict) -> tuple[dict, Decimal]:
    template = disclosure_template(conn, campaign["category_id"], clip["language"]) or {}
    category = conn.execute("select banned_words from categories where id = %s", (campaign["category_id"],)).fetchone()
    limits = {"min_s": get_setting(conn, "clip_min_duration_s"), "max_s": get_setting(conn, "clip_max_duration_s")}
    report, cost = agent_c.run(conn, registry, clip, template, limits, (category or {}).get("banned_words") or [],
                               version["rules_json"].get("content_rules") or [],
                               (clip["render_meta"] or {}).get("hook_text") or "", clip["owner_operator_id"])
    conn.execute("update clips set compliance_status = %s, compliance_report = %s where id = %s",
                 (report["status"], Jsonb(report), clip["id"]))
    return report, Decimal(str(cost))


def stage_compliance(conn, job: dict) -> Decimal:
    clip, source, campaign, version = _clip_ctx(conn, job["payload"]["clip_id"])
    report, cost = run_compliance(conn, clip, campaign, version)
    if report["status"] == "fail":
        # A hard fail blocks packaging and never reaches the feed. It appears in Alerts with the reason.
        reasons = [c["check"] for c in report["deterministic"] if c["status"] == "fail"] + \
                  [r["rule"] for r in report["rules"] if r["status"] == "fail"]
        conn.execute("update clips set status = 'archived' where id = %s", (clip["id"],))
        raise_alert(conn, clip["owner_operator_id"], "clip_blocked", "block", "clips", clip["id"],
                    f"{campaign['name']}: a clip failed compliance and was blocked ({'; '.join(str(r) for r in reasons)[:200]}).")
        return cost
    enqueue(conn, "package", {"clip_id": str(clip["id"])}, clip["owner_operator_id"],
            source_id=source["id"], campaign_version_id=version["id"])
    return cost


def pick_account(conn, owner_id, platforms: list[str], niche: str | None = None) -> dict | None:
    """The owner's own active account on an allowed platform, for this niche when one exists (one account per
    platform per niche), else an account with no niche; fewest clips assigned first."""
    if not platforms:
        return None
    return conn.execute(
        """select a.id, a.platform from accounts a
           where a.owner_operator_id = %s and a.status = 'active' and a.platform = any(%s)
             and (a.niche is null or a.niche = %s)
           order by (a.niche is not distinct from %s) desc,
                    (select count(*) from clips c where c.account_id = a.id and c.status <> 'archived'), a.created_at limit 1""",
        (owner_id, platforms, niche, niche),
    ).fetchone()


def stage_package(conn, job: dict) -> Decimal:
    clip, source, campaign, version = _clip_ctx(conn, job["payload"]["clip_id"])
    account_id, platform = clip["account_id"], clip["platform"]
    if account_id is None:
        niche_row = conn.execute("select name from categories where id = %s", (campaign["category_id"],)).fetchone()
        account = pick_account(conn, clip["owner_operator_id"], version["rules_json"].get("platforms_allowed") or [],
                               niche_row["name"] if niche_row else None)
        account_id, platform = (account["id"], account["platform"]) if account else (None, None)
    conn.execute("update clips set account_id = %s, platform = %s, due_at = %s, status = 'ready_for_review' where id = %s",
                 (account_id, platform, campaign["deadline"], clip["id"]))
    audit(conn, "clip.packaged", "clips", clip["id"], {"compliance_status": clip["compliance_status"]},
          rule_version_id=version["id"])
    return Decimal("0")


def stage_fetch_source(conn, job: dict) -> Decimal:
    from . import sources

    return sources.stage_fetch_source(conn, job)


def stage_discover(conn, job: dict) -> Decimal:
    from . import discovery

    discovery.refresh(conn, job["payload"].get("niches") or [])
    return Decimal("0")


HANDLERS = {"discover": stage_discover, "fetch_source": stage_fetch_source, "transcribe": stage_transcribe, "moments": stage_moments, "hooks": stage_hooks, "render": stage_render,
            "captions": stage_captions, "compliance": stage_compliance, "package": stage_package}


# ------------------------------------------------------------------ worker loop

def process_one(db: Database) -> bool:
    """Claim and run one job. Returns False when the queue is empty or the kill switch is on."""
    worker = Actor.worker()
    with db.session(worker) as conn:
        job = claim_job(conn)
    if job is None:
        return False
    try:
        with db.session(worker) as conn:
            if kill_switch_on(conn):
                raise KillSwitchOn()
            cost = HANDLERS[job["type"]](conn, job)
            finish_job(conn, job["id"], cost)
    except KillSwitchOn:
        with db.session(worker) as conn:
            conn.execute("update jobs set status = 'cancelled', error = 'kill switch' where id = %s", (job["id"],))
    except Exception as exc:
        retry = not isinstance(exc, AppError) and job["attempts"] < 3
        with db.session(worker) as conn:
            fail_job(conn, job["id"], f"{type(exc).__name__}: {exc}", retry)
            if not retry:
                raise_alert(conn, job["owner_operator_id"], "job_failed", "warn", "jobs", job["id"],
                            f"A {job['type']} job failed: {str(exc)[:160]}")
    return True


def run_until_idle(db: Database, max_jobs: int = 500) -> int:
    done = 0
    while done < max_jobs:
        with db.session(Actor.worker()) as conn:
            conn.execute("update jobs set run_after = now() where status = 'queued'")   # no waiting in tests and the demo
        if not process_one(db):
            break
        done += 1
    return done
