"""Production pipeline (spec 12.1): transcribe, signals, moments, hooks, render (one encode: reframe, captions,
disclosure, template), compliance, package. Each stage is an idempotent job that records its cost and queues the next stage.
The worker can run every stage, but it cannot pass a human gate (database triggers)."""
from __future__ import annotations

import math
import tempfile
from decimal import Decimal
from pathlib import Path

from psycopg.types.json import Jsonb

from . import ad_label, brand_checks, brief_wiring, config, ffmpeg, hook_patterns, hook_rules, job_progress, language_check, music, packaging, quality_gate, signals, templates, transcripts
from .adapters import music as _music_adapters  # noqa: F401  (registers the music stage providers)
from .adapters.base import AdapterPaused
from .adapters.vendors import ContentFiltered
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


def disclosure_for(conn, campaign: dict, version: dict, language: str) -> dict | None:
    """The label a clip carries: the campaign's template, or the off label when the owner switched it off (ad_label.py).
    None: the label is on but there is no template for this language."""
    if ad_label.is_off(campaign):
        return ad_label.off_label((version or {}).get("rules_json"))
    return disclosure_template(conn, campaign["category_id"], language)


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
            result = registry.call(conn, "transcription", "transcribe", request, owner)
            return _retry_if_thin(conn, source, campaign, video, request, result, owner)
        except AdapterPaused as exc:
            raise_alert(conn, owner, "transcription_paused", "warn", "source_assets", source["id"],
                        f"{campaign['name']}: {exc} The local Whisper transcribes this video instead.")
            detected = language_check.detect_language(video, request["duration_s"])   # never force Whisper into a language the audio is not in
            if detected in language_check.SUPPORTED and detected != request["language"]:
                request = {**request, "language": detected, "dialect_hint": None}
                _set_source_language(conn, source, detected)
            return registry.call(conn, "transcription", "transcribe", request, owner, provider=LOCAL_TRANSCRIBER)


def _set_source_language(conn, source: dict, language: str) -> None:
    conn.execute("update source_assets set language = %s, dialect = null where id = %s", (language, source["id"]))
    audit(conn, "source.language_corrected", "source_assets", source["id"], {"from": source["language"], "to": language})
    source["language"], source["dialect"] = language, None   # the caller stores the transcript under the corrected language


def _retry_if_thin(conn, source: dict, campaign: dict, video: Path, request: dict, result: tuple, owner) -> tuple[dict, Decimal]:
    """A long video with almost no words is in another language than the one chosen. Alert, detect the language, and transcribe ONCE
    more in it; keep whichever transcript has more words."""
    words, cost = result[0].get("words") or [], result[1]
    problem = language_check.empty_transcript_message(words, request["duration_s"], source["language"])
    if not problem:
        return result
    raise_alert(conn, owner, "transcript_too_thin", "warn", "source_assets", source["id"], f"{campaign['name']}: {problem}")
    detected = language_check.detect_language(video, request["duration_s"])
    if detected not in language_check.SUPPORTED or detected == source["language"]:
        return result
    retry_request = {**request, "language": detected, "dialect_hint": None}
    retry = registry.call(conn, "transcription", "transcribe", retry_request, owner)
    total = cost + retry[1]
    if len(retry[0].get("words") or []) <= len(words):
        return result[0], total
    _set_source_language(conn, source, detected)
    audit(conn, "transcription.retried_in_detected_language", "source_assets", source["id"], {"language": detected})
    return retry[0], total


def _drop_fake_transcript_if_real_provider(conn, source: dict) -> None:
    """A transcript made by the fake provider is test data. When a real provider is active it is never reused:
    it is removed so the video is transcribed for real."""
    if registry.row(conn, "transcription")["is_fake"]:
        return
    cached = transcripts.find(conn, source)
    if cached is not None and cached["provider"] == "fake":
        conn.execute("delete from transcripts where id = %s", (cached["id"],))
        audit(conn, "transcript.fake_discarded", "transcripts", cached["id"], {"source_id": str(source["id"])})


def stage_transcribe(conn, job: dict) -> Decimal:
    source, campaign, version = _source_ctx(conn, job["payload"]["source_id"])
    path = registry.get(conn, "storage").path(source["stored_path"])
    if not path.exists() or ffmpeg.sha256_file(path) != source["file_hash"]:
        raise AppError("Source file hash mismatch. The job is stopped.", "hash_mismatch")   # S3
    cost = Decimal("0")
    _drop_fake_transcript_if_real_provider(conn, source)
    if transcripts.find(conn, source) is None:
        result, cost = _transcribe(conn, source, campaign, version, path)
        transcripts.create(conn, source, result)
        if result.get("degraded"):
            raise_alert(conn, source["owner_operator_id"], "transcription_degraded", "warn", "source_assets", source["id"],
                        f"{campaign['name']}: {result['degraded']}. It works, but slowly.")
    enqueue(conn, "signals", {"source_id": str(source["id"])}, source["owner_operator_id"],
            source_id=source["id"], campaign_version_id=version["id"])
    return cost


def stage_signals(conn, job: dict) -> Decimal:
    """Measure the per-second signals once, then queue moment finding. Signals only help the moment engine, so an
    unexpected failure raises an alert and the pipeline goes on without them; an unreadable video (AppError) stops the job."""
    source, campaign, version = _source_ctx(conn, job["payload"]["source_id"])
    path = registry.get(conn, "storage").path(source["stored_path"])
    if not path.exists() or ffmpeg.sha256_file(path) != source["file_hash"]:
        raise AppError("Source file hash mismatch. The job is stopped.", "hash_mismatch")
    try:
        signals.compute(conn, source["id"])
    except AppError:
        raise
    except Exception as exc:
        raise_alert(conn, source["owner_operator_id"], "signals_unavailable", "warn", "source_assets", source["id"],
                    f"{campaign['name']}: the video signals could not be measured ({type(exc).__name__}). Moments are found without them.")
    enqueue(conn, "moments", {"source_id": str(source["id"])}, source["owner_operator_id"],
            source_id=source["id"], campaign_version_id=version["id"])
    return Decimal("0")


def _manual_selection_alert(conn, owner, campaign: dict, source: dict, why: str) -> None:
    """The declared fallback of moment finding: the owner picks the moments by hand in the review tool."""
    raise_alert(conn, owner, "manual_selection", "warn", "source_assets", source["id"],
                f"{campaign['name']}: moment finding is unavailable ({why}). Pick the moments by hand.")


def stage_moments(conn, job: dict) -> Decimal:
    """The moment engine (moments.find) picks the moments for every campaign, practice or real: engines propose, one filter
    decides. Whatever goes wrong ends in the manual-selection alert; the job never crashes on bad model output."""
    from . import moments   # lazy: moments builds on this module's snapping

    source, campaign, version = _source_ctx(conn, job["payload"]["source_id"])
    owner = source["owner_operator_id"]
    try:
        result = moments.find(conn, source["id"], version)
    except KillSwitchOn:
        raise
    except Exception as exc:
        _manual_selection_alert(conn, owner, campaign, source, str(exc) if isinstance(exc, AppError) else type(exc).__name__)
        return Decimal("0")
    if not result["kept"]:
        if not result["filtered"]:
            _manual_selection_alert(conn, owner, campaign, source, "no moment qualified")
        return result["cost"]
    return result["cost"] + _schedule_candidates(conn, source, campaign, version, result["kept"])


def _schedule_candidates(conn, source: dict, campaign: dict, version: dict, rows: list[dict]) -> Decimal:
    """Queue the hooks job for each stored candidate (`id`, `rank`), within the weekly cap and the portfolio share."""
    owner = source["owner_operator_id"]
    cap = int(get_setting(conn, "clips_per_campaign_per_week", campaign["id"]))
    made = conn.execute(
        "select count(*) as n from clips where campaign_id = %s and version_no = 1 and created_at > now() - interval '7 days'",
        (campaign["id"],),
    ).fetchone()["n"]
    room, share_note = max(cap - made, 0), None
    if campaign["is_practice"]:
        room = len(rows)   # practice has no weekly cap or risk spreading: it exists to try the system on any video
    else:
        room, share_note = _portfolio_room(conn, owner, campaign["id"], made, room, len(rows))
    for row in rows:
        if row["rank"] <= room:
            enqueue(conn, "hooks", {"candidate_id": str(row["id"])}, owner, source_id=source["id"], campaign_version_id=version["id"])
    if len(rows) > room:
        why = share_note or f"weekly clip cap of {cap} reached"
        raise_alert(conn, owner, "weekly_clip_cap", "warn", "campaigns", campaign["id"],
                    f"{campaign['name']}: {why}. {len(rows) - room} moments were kept but not cut; they can be cut by hand.")
    return Decimal("0")


def _store_candidates(conn, source: dict, campaign: dict, version: dict, candidates: list[dict], provider: str) -> Decimal:
    """Manual selection: store the owner's own picks and queue them."""
    owner = source["owner_operator_id"]
    stored = []
    for candidate in agent_b.rank_candidates(candidates):
        row = conn.execute(
            """insert into candidates (source_id, owner_operator_id, provider, start_s, end_s, score, rank, reason)
               values (%s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (source["id"], owner, provider, candidate["start_s"], candidate["end_s"], candidate["score"], candidate["rank"],
             candidate["reason"]),
        ).fetchone()
        stored.append({"id": row["id"], "rank": candidate["rank"]})
    return _schedule_candidates(conn, source, campaign, version, stored)


def _portfolio_room(conn, owner, campaign_id, made: int, room: int, wanted: int) -> tuple[int, str | None]:
    """Spread risk (practitioner lesson): while the owner has other approved campaigns, one campaign may take at
    most portfolio_max_share_pct of the week's clips. With a single approved campaign there is nothing to spread."""
    others = conn.execute(
        "select count(*) as n from campaigns where owner_operator_id = %s and status = 'approved' and id <> %s and not is_practice",
        (owner, campaign_id)).fetchone()["n"]
    if not others:
        return room, None
    share = float(get_setting(conn, "portfolio_max_share_pct", owner)) / 100
    week = conn.execute(
        """select count(*) as n from clips cl join campaigns c on c.id = cl.campaign_id
           where cl.owner_operator_id = %s and cl.version_no = 1 and cl.created_at > now() - interval '7 days' and not c.is_practice""",
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


def _with_pattern_hook(conn, candidate: dict, source: dict, hooks: list[dict]) -> list[dict]:
    """The engine's own hook (written in a library pattern, rated for concreteness and checked to be paid off in the clip)
    joins the agent's hooks and competes on its rating; it carries its pattern so the owner's choice can teach the library."""
    from . import moment_rank

    text = candidate.get("hook_text")
    if text:
        text, bad = hook_rules.check(text)   # the engine's line follows the same rules as the agent's
        if bad:
            text = None
    if not text:
        return hooks
    row = conn.execute("select hook_type from hook_patterns where name = %s and language = %s",
                       (candidate.get("hook_pattern"), source["language"])).fetchone()
    ours = {"text": text, "hook_type": row["hook_type"] if row else "question", "score": moment_rank.hook_rating(text),
            "chosen": False, "pattern": candidate.get("hook_pattern")}
    merged = sorted([h for h in hooks if h["text"].lower() != text.lower()] + [ours], key=lambda h: -h["score"])[:3]
    for rank_, hook in enumerate(merged, start=1):
        hook["rank"] = rank_
    return merged


def regenerate_hooks(conn, clip_id) -> list[dict]:
    """New hook variants for a clip's moment (the hook editor's Regenerate button): never a text the moment already has, each
    one passing hook_rules. They are added after the existing hooks; nothing is re-rendered until the owner picks one."""
    import time

    clip = conn.execute("select * from clips where id = %s", (clip_id,)).fetchone()
    if clip is None:
        raise AppError("Clip not found.", "not_found")
    candidate = conn.execute("select * from candidates where id = %s", (clip["candidate_id"],)).fetchone()
    source, _campaign, _version = _source_ctx(conn, candidate["source_id"])
    owner = source["owner_operator_id"]
    words = [w for w in _words(conn, source) if w["start"] >= float(candidate["start_s"]) - 0.1 and w["end"] <= float(candidate["end_s"]) + 0.1]
    existing = conn.execute("select text, rank from hooks where candidate_id = %s", (candidate["id"],)).fetchall()
    picked = hook_patterns.pick(conn, source["language"], "default", 3, f"{clip_id}:{time.time()}", owner)
    try:
        fresh, _cost = agent_b.run(conn, registry, " ".join(w["text"] for w in words), source["language"], candidate["score"], owner,
                                   avoid=[h["text"] for h in existing], patterns=[p["name"] for p in picked])
    except (ContentFiltered, AdapterPaused, KillSwitchOn):
        raise
    except AppError:
        raise
    except Exception as exc:   # a vendor or network failure: plain words for the owner, the cause goes to the server log
        import logging

        logging.getLogger("clipping").warning("hook regenerate failed: %r", exc)
        raise AppError("Could not write new hooks right now. Try again in a moment.", "hooks_failed") from exc
    top = max([h["rank"] for h in existing], default=0)
    for offset, hook in enumerate(fresh, start=1):
        conn.execute(
            """insert into hooks (candidate_id, clip_id, owner_operator_id, text, hook_type, chosen, rank, score)
               values (%s, %s, %s, %s, %s, false, %s, %s)""",
            (candidate["id"], clip["id"], owner, hook["text"], hook["hook_type"], top + offset, hook["score"]))
    return [dict(r) | {"id": str(r["id"])} for r in conn.execute(
        "select id, text, hook_type, chosen, rank, score from hooks where candidate_id = %s order by rank", (candidate["id"],)).fetchall()]


def stage_hooks(conn, job: dict) -> Decimal:
    candidate = conn.execute("select * from candidates where id = %s", (job["payload"]["candidate_id"],)).fetchone()
    source, campaign, version = _source_ctx(conn, candidate["source_id"])
    owner = source["owner_operator_id"]
    words = [w for w in _words(conn, source) if w["start"] >= float(candidate["start_s"]) - 0.1 and w["end"] <= float(candidate["end_s"]) + 0.1]
    segment = " ".join(w["text"] for w in words)
    try:
        hooks, cost = agent_b.run(conn, registry, segment, source["language"], candidate["score"], owner)
    except ContentFiltered as exc:
        # The AI safety filter refused this moment (e.g. graphic violence): not brand-safe either, so the moment is
        # set aside with its reason and the other moments carry on. Never retried, never a failed job.
        conn.execute("update candidates set status = 'discarded', reason = %s where id = %s",
                     (f"Filtered: {exc}", candidate["id"]))
        raise_alert(conn, owner, "moment_filtered", "info", "candidates", candidate["id"],
                    f"{campaign['name']}: one moment was set aside because {str(exc)[0].lower()}{str(exc)[1:]}")
        return Decimal("0")
    hooks = _with_pattern_hook(conn, candidate, source, hooks)
    clip = conn.execute(
        """insert into clips (candidate_id, campaign_id, campaign_version_id, owner_operator_id, start_s, end_s, language, dialect, status)
           values (%s, %s, %s, %s, %s, %s, %s, %s, 'rendering') returning *""",
        (candidate["id"], campaign["id"], version["id"], owner, candidate["start_s"], candidate["end_s"],
         source["language"], source["dialect"]),
    ).fetchone()
    top = None
    for hook in hooks:
        row = conn.execute(
            """insert into hooks (candidate_id, clip_id, owner_operator_id, text, hook_type, chosen, rank, score, pattern)
               values (%s, %s, %s, %s, %s, false, %s, %s, %s) returning id""",
            (candidate["id"], clip["id"], owner, hook["text"], hook["hook_type"], hook["rank"], hook["score"], hook.get("pattern")),
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


def _assign_account(conn, clip: dict, campaign: dict, version: dict) -> tuple:
    """The account this clip is for, chosen now (not at packaging) because its template and its platform's caption style
    shape the render. A clip that already has one (an edited version) keeps it."""
    if clip["account_id"] is not None:
        return clip["account_id"], clip["platform"]
    niche = conn.execute("select name from categories where id = %s", (campaign["category_id"],)).fetchone()
    account = pick_account(conn, clip["owner_operator_id"], version["rules_json"].get("platforms_allowed") or [], niche["name"] if niche else None)
    if account is None:
        return None, None
    conn.execute("update clips set account_id = %s, platform = %s where id = %s", (account["id"], account["platform"], clip["id"]))
    return account["id"], account["platform"]


def _render_request(conn, clip: dict, source: dict, platform: str | None, hook_text: str | None) -> dict:
    """What the render needs to know about the clip: its window, language, platform, the profile's crop and audio style, and
    from the signals the loud moments (zooms, highlighted words) and the face, laughter, scene and loudness around it."""
    candidate = conn.execute("select style from candidates where id = %s", (clip["candidate_id"],)).fetchone()
    style = (candidate or {}).get("style") or {}
    start, end = float(clip["start_s"]), float(clip["end_s"])
    request = {"start_s": start, "end_s": end, "language": clip["language"], "platform": platform, "crop": style.get("crop"),
               "audio": style.get("audio"), "hook_text": hook_text, "emphasis": [],
               "min_s": float(get_setting(conn, "clip_min_duration_s"))}      # tightening never takes the clip below the campaign minimum
    try:
        request["speaker_names"] = {str(k): str(v) for k, v in (transcripts.load(conn, source["id"]).get("speakers") or {}).items()}
    except Exception:   # no transcript names: the label falls back to "Speaker N"
        request["speaker_names"] = {}
    measured = signals.load(conn, source["id"])
    if measured:
        first, last = int(start), math.ceil(end)
        series = measured["series"]
        request["emphasis"] = templates.emphasis_times(series.get("loudness") or [], start, end)
        request["signals"] = {"first_s": first, **{k: (series.get(k) or [])[first:last] for k in ("face", "laughter", "scene", "loudness")}}
    return request


MUSIC_NICHE = {"podcast": "podcasts", "gaming": "gaming"}   # every other category (lifestyle, reddit stories) takes the business beds
RECENT_TRACKS = 5


def _pick_music(conn, clip: dict, campaign: dict, render_clip: dict, words: list[dict], platform: str | None) -> dict | None:
    """The bed for this clip: the music stage's choice (niche, the clip's mood and energy, the owner's most recent tracks avoided),
    as {"path", "offset_s", "gain_db", "record"} for the render. A bed is a nicety: a failure or no usable track gives None and
    the clip renders without one."""
    try:
        category = conn.execute("select name from categories where id = %s", (campaign["category_id"],)).fetchone()
        niche = MUSIC_NICHE.get(category["name"] if category else None, "business")
        start, end = float(clip["start_s"]), float(clip["end_s"])
        profile = music.clip_profile(words, start, end, render_clip.get("signals"))
        recent = [r["track"] for r in conn.execute(
            """select render_meta->'music'->>'track_id' as track from clips where owner_operator_id = %s and id <> %s
               and render_meta->'music'->>'track_id' is not null
               order by updated_at desc limit %s""", (clip["owner_operator_id"], clip["id"], RECENT_TRACKS)).fetchall()]
        decision, _ = registry.call(conn, "music", "choose", {"niche": niche, "profile": profile, "duration_s": end - start, "platform": platform,
                                                              "recent": recent, "seed": str(clip["id"])}, clip["owner_operator_id"])
    except Exception as exc:
        return {"record": None, "skipped": f"music stage failed: {config.redact(f'{type(exc).__name__}: {exc}')[:200]}"}
    if not decision.get("use"):
        return {"record": None, "skipped": decision.get("reason"), "profile": profile}
    track = decision["track"]
    record = music.licence_record(track, decision["offset_s"], platform)
    record.update(mood=profile["mood"], energy=profile["energy"], score=decision["score"], reason=decision["reason"], gain_db=decision["gain_db"])
    return {"path": track["path"], "offset_s": decision["offset_s"], "gain_db": decision["gain_db"], "record": record, "profile": profile}


def stage_render(conn, job: dict) -> Decimal:
    """The clip in one encode: reframe, captions, the disclosure label and the account's template (title card, progress bar,
    emphasis zooms), then its cover. The disclosure text is checked first: without it the clip is blocked."""
    clip, source, campaign, version = _clip_ctx(conn, job["payload"]["clip_id"])
    owner = clip["owner_operator_id"]
    disclosure = disclosure_for(conn, campaign, version, clip["language"])
    if disclosure is None:
        raise_alert(conn, owner, "disclosure_missing", "block", "clips", clip["id"],
                    f"{campaign['name']}: no disclosure template for language {clip['language']}. The clip is blocked.")
        raise AppError("Disclosure text is missing. The clip is blocked.", "disclosure_missing")   # S8
    if disclosure.get("required"):
        raise AppError("The brief requires the ad label, so it stays on for this campaign. The clip is not rendered.", "ad_label_required")
    account_id, platform = _assign_account(conn, clip, campaign, version)
    meta = dict(clip["render_meta"] or {})
    template = templates.pick(conn, account_id, meta.get("template_id"))
    storage = registry.get(conn, "storage")
    key = f"clips/{clip['id']}/final.mp4"
    out = storage.path(key)
    words = _words(conn, source)
    render_clip = _render_request(conn, clip, source, platform, meta.get("hook_text"))
    render_clip["min_s"] = brief_wiring.min_clip_s(version["rules_json"], render_clip["min_s"])   # the brief's own minimum length (brief_spec)
    account_row = conn.execute("select niche from accounts where id = %s", (account_id,)).fetchone() if account_id else None
    meta["account_warnings"] = brief_wiring.account_warnings(version["rules_json"], account_row)   # shown to the owner before posting
    bed = _pick_music(conn, clip, campaign, render_clip, words, platform)
    if bed and bed.get("path"):
        render_clip["music"] = bed
    result, cost = registry.call(conn, "render", "render_clip", {
        "src": str(storage.path(source["stored_path"])), "clip": render_clip,
        "words": words, "template": template["spec"], "label": None if disclosure.get("label_off") else disclosure["overlay_text"], "out": str(out)}, owner)
    storage.put(key, str(out))
    cover_key = None
    if result.get("cover_path"):
        cover_key = f"clips/{clip['id']}/cover.png"
        storage.put(cover_key, result["cover_path"])
    info = ffmpeg.probe(out)
    caption, hashtags, mentions = clip["caption_text"], clip["hashtags"], clip["mentions"]
    if not (meta.get("caption_edited") and caption):
        caption, hashtags, mentions = build_caption(disclosure["caption_text"], meta.get("hook_text") or "", version["rules_json"])
    try:   # the gate only reports: a failure here must never stop the render
        gate = quality_gate.run(Path(str(out)), words, float(clip["start_s"]), float(clip["end_s"]), language=clip["language"], caption=caption,
                                disclosure=None if disclosure.get("label_off") else disclosure.get("caption_text"), niche=media.NICHE_OF_CROP.get(render_clip.get("crop")))
    except Exception:
        gate = None
    meta["gate"] = gate
    credit = music.credit_line(result.get("music"))
    if credit and credit not in caption and not meta.get("caption_edited"):
        caption = caption + "\n" + credit
    meta["music"] = result.get("music") or ({"track_id": None, "skipped": bed.get("skipped")} if bed else None)
    meta.update(loudness_target_lufs=result.get("loudness_target"), music_credit_in_caption=bool(credit and credit in caption))
    meta.update(template_id=template["id"], template_version=template["version"], encoder=result["encoder"], encodes=result["encodes"],
                reframe=result["reframe"], loudness_normalised=result["loudness_normalised"], overlay_burned=True,
                captions=result["captions"], zooms=result.get("zooms", 0), caption_language=clip["language"],
                disclosure_template_id=str(disclosure["id"]) if disclosure.get("id") else None, cover_key=cover_key)
    conn.execute(
        """update clips set file_path = %s, file_hash = %s, duration_s = %s, width = %s, height = %s, overlay_json = %s,
               caption_text = %s, hashtags = %s, mentions = %s, render_meta = %s, cover_path = %s where id = %s""",
        (key, ffmpeg.sha256_file(out), info["duration_s"], info["width"], info["height"], Jsonb(result["overlay"]),
         caption, hashtags, mentions, Jsonb(meta), cover_key, clip["id"]))
    conn.execute("update candidates set status = 'rendered' where id = %s", (clip["candidate_id"],))
    enqueue(conn, "compliance", {"clip_id": str(clip["id"])}, owner, source_id=source["id"], campaign_version_id=version["id"])
    return cost


def stage_captions(conn, job: dict) -> Decimal:
    """Retired: captions are burned in by the render's one encode. The stage stays registered for jobs queued before that. A clip
    that already has its file needs nothing; one that was cut but not captioned is rendered again in one pass."""
    clip, source, _, version = _clip_ctx(conn, job["payload"]["clip_id"])
    if not clip["file_path"]:
        enqueue(conn, "render", {"clip_id": str(clip["id"]), "retry": "one_encode"}, clip["owner_operator_id"],
                source_id=source["id"], campaign_version_id=version["id"])
    return Decimal("0")


def run_compliance(conn, clip: dict, campaign: dict, version: dict) -> tuple[dict, Decimal]:
    template = disclosure_for(conn, campaign, version, clip["language"]) or {}
    category = conn.execute("select banned_words from categories where id = %s", (campaign["category_id"],)).fetchone()
    limits = {"min_s": brief_wiring.min_clip_s(version["rules_json"], get_setting(conn, "clip_min_duration_s")),
              "max_s": get_setting(conn, "clip_max_duration_s")}
    report, cost = agent_c.run(conn, registry, clip, template, limits, (category or {}).get("banned_words") or [],
                               version["rules_json"].get("content_rules") or [],
                               (clip["render_meta"] or {}).get("hook_text") or "", clip["owner_operator_id"],
                               brand_checks.for_campaign(conn, campaign["id"], clip["owner_operator_id"]))
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
    conn.execute("update clips set account_id = %s, platform = %s, due_at = %s where id = %s",
                 (account_id, platform, campaign["deadline"], clip["id"]))
    cost = _package_text(conn, clip["id"], source, campaign, version)
    conn.execute("update clips set status = 'ready_for_review' where id = %s", (clip["id"],))
    audit(conn, "clip.packaged", "clips", clip["id"], {"compliance_status": clip["compliance_status"]},
          rule_version_id=version["id"])
    return cost


def _package_text(conn, clip_id, source: dict, campaign: dict, version: dict) -> Decimal:
    """Caption lab, per-platform packaging and the originality record (packaging.py). The text is checked against the campaign's rules
    again: if the new caption fails compliance, the earlier one is put back. A failure here never stops the clip."""
    clip = conn.execute("select * from clips where id = %s", (clip_id,)).fetchone()
    disclosure = disclosure_for(conn, campaign, version, clip["language"])
    if disclosure is None:
        return Decimal("0")
    before = {k: clip[k] for k in ("caption_text", "hashtags", "mentions", "render_meta")}
    try:
        with conn.transaction():
            cost = Decimal(str(packaging.package_clip(conn, registry, clip, source, campaign, version, disclosure)))
            fresh = conn.execute("select * from clips where id = %s", (clip_id,)).fetchone()
            report, extra = run_compliance(conn, fresh, campaign, version)
            cost += extra
            if report["status"] == "fail":
                raise AppError("The new caption failed compliance.", "caption_lab_compliance")
        return cost
    except AppError as exc:
        reason = str(exc)
    except Exception as exc:   # a model or database problem: keep the caption the render built
        reason = f"{type(exc).__name__}: {exc}"
    meta = dict(before["render_meta"] or {}, caption_lab_error=reason[:300])
    conn.execute("update clips set caption_text = %s, hashtags = %s, mentions = %s, render_meta = %s where id = %s",
                 (before["caption_text"], before["hashtags"], before["mentions"], Jsonb(meta), clip_id))
    run_compliance(conn, conn.execute("select * from clips where id = %s", (clip_id,)).fetchone(), campaign, version)
    return Decimal("0")


def stage_fetch_source(conn, job: dict) -> Decimal:
    from . import sources

    return sources.stage_fetch_source(conn, job)


def stage_discover(conn, job: dict) -> Decimal:
    from . import discovery

    discovery.refresh(conn, job["payload"].get("niches") or [])
    return Decimal("0")


def stage_publish(conn, job: dict) -> Decimal:
    from . import publish   # lazy: publish builds on the review and campaign modules

    return publish.stage_publish(conn, job)


def stage_post_status(conn, job: dict) -> Decimal:
    from . import publish

    return publish.stage_post_status(conn, job)


HANDLERS = {"discover": stage_discover, "fetch_source": stage_fetch_source, "transcribe": stage_transcribe, "signals": stage_signals, "moments": stage_moments, "hooks": stage_hooks, "render": stage_render,
            "captions": stage_captions, "compliance": stage_compliance, "package": stage_package,
            "publish": stage_publish, "post_status": stage_post_status}


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
            with job_progress.bind(db, job["id"]):
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
