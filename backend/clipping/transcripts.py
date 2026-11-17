"""Transcripts: versioned words with speakers and confidence, stored once per source file.

A transcript row is one provider's reading of one source. Each save (the first reading, then every correction)
adds a row to transcript_versions with its own word file; `load` always returns the newest version.
Word file shape (a plain JSON list, as before): [{text, start, end, confidence, speaker}]. Older files without
confidence or speaker still load."""
from __future__ import annotations

import json
import math
import re
import tempfile
from pathlib import Path

from psycopg.types.json import Jsonb

from . import config
from .adapters.transcription import utterances_from_words
from .alerts import registry
from .core import AppError, audit

MAX_SPEAKER_NAME = 60


class StaleTranscript(AppError):
    """The transcript changed since the caller loaded it. `version` is the current one."""

    def __init__(self, version: int):
        super().__init__("This transcript was changed somewhere else. Reload it and apply your edit again.", "stale")
        self.version = version


# ------------------------------------------------------------------ validation (vendor output and owner edits are external input)

def _number(value, default=None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def clean_words(raw) -> list[dict]:
    """Keep well-formed words only: non-blank text, finite times (end never before start), confidence in 0..1."""
    if not isinstance(raw, list):
        raise AppError("The words must be a list.", "bad_words")
    words = []
    for item in raw:
        if not isinstance(item, dict):
            raise AppError("Each word must be an object with text, start and end.", "bad_words")
        text, start, end = str(item.get("text") or "").strip(), _number(item.get("start")), _number(item.get("end"))
        if not text or start is None or end is None or start < 0:
            continue
        confidence = _number(item.get("confidence"))
        speaker = item.get("speaker")
        words.append({"text": text, "start": round(start, 3), "end": round(max(end, start), 3),
                      "confidence": None if confidence is None else round(min(max(confidence, 0.0), 1.0), 4),
                      "speaker": None if speaker is None or not str(speaker).strip() else str(speaker).strip()})
    return words


def _clean_utterances(raw) -> list[dict]:
    out = []
    for item in raw if isinstance(raw, list) else []:
        start, end, text = _number(item.get("start")), _number(item.get("end")), str(item.get("text") or "").strip()
        if text and start is not None and end is not None:
            speaker = item.get("speaker")
            out.append({"start": round(start, 3), "end": round(end, 3), "text": text,
                        "speaker": None if speaker is None else str(speaker)})
    return out


def speaker_map(words: list[dict], given=None) -> dict[str, str]:
    """{speaker id: name} for every speaker in the words, in order of first appearance. Missing names get "Speaker N"."""
    if given is not None and not isinstance(given, dict):
        raise AppError("Speakers must be a map of id to name.", "bad_speakers")
    given = given or {}
    ids = list(dict.fromkeys(w["speaker"] for w in words if w.get("speaker") is not None))
    return {i: (str(given.get(i) or "").strip()[:MAX_SPEAKER_NAME] or f"Speaker {n}") for n, i in enumerate(ids, start=1)}


# ------------------------------------------------------------------ key terms

def key_terms_for(campaign: dict, version: dict) -> list[str]:
    """The campaign's words, most important first: the owner's key terms, the campaign name, then the required
    hashtags and mentions. The adapter cuts the list to its own limit."""
    terms = list(campaign.get("key_terms") or []) + [campaign["name"]]
    for rule in (version.get("rules_json") or {}).get("content_rules") or []:
        if rule.get("requirement") == "required":
            terms += [t.lstrip("#@") for t in re.findall(r"[#@][\w؀-ۿ]+", rule.get("text") or "") if len(t) > 3]
    return terms


# ------------------------------------------------------------------ files

def to_srt(words: list[dict]) -> str:
    def ts(t: float) -> str:
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    lines = []
    for n, start in enumerate(range(0, len(words), 7), start=1):
        chunk = words[start:start + 7]
        lines += [str(n), f"{ts(chunk[0]['start'])} --> {ts(chunk[-1]['end'])}", " ".join(w["text"] for w in chunk), ""]
    return "\n".join(lines)


def _put_text(storage, key: str, text: str) -> None:
    tmp_root = config.data_dir() / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=tmp_root) as folder:
        file = Path(folder) / "transcript.tmp"
        file.write_text(text, encoding="utf-8")
        storage.put(key, str(file))


def _write_version(conn, transcript: dict, version_no: int, words: list[dict], utterances: list[dict], speakers: dict,
                   actor_id) -> tuple[str, str]:
    """Write the word and subtitle files for a version and record it. Returns the two storage keys."""
    storage = registry.get(conn, "storage")
    stem = transcript["word_json_path"].removesuffix(".json")
    words_key = transcript["word_json_path"] if version_no == 1 else f"{stem}_v{version_no}.json"
    srt_key = words_key.removesuffix(".json") + ".srt"
    _put_text(storage, words_key, json.dumps(words, ensure_ascii=False))
    _put_text(storage, srt_key, to_srt(words))
    conn.execute(
        """insert into transcript_versions (transcript_id, owner_operator_id, version_no, word_json_path, srt_path, speakers,
               utterances, word_count, edited_by) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (transcript["id"], transcript["owner_operator_id"], version_no, words_key, srt_key, Jsonb(speakers),
         Jsonb(utterances), len(words), actor_id),
    )
    return words_key, srt_key


# ------------------------------------------------------------------ the public interface

def find(conn, source: dict) -> dict | None:
    """The cached transcript for this file, language and dialect, if one exists."""
    return conn.execute(
        "select * from transcripts where source_hash = %s and language = %s and dialect is not distinct from %s limit 1",
        (source["file_hash"], source["language"], source["dialect"]),
    ).fetchone()


def create(conn, source: dict, result: dict) -> dict:
    """Store a provider's result as version 1. An empty result is refused: nothing downstream can use it."""
    words = clean_words(result["words"])
    if not words:
        raise AppError("The transcript is empty. Check that the footage has speech.", "empty_transcript")
    utterances = _clean_utterances(result.get("utterances")) or utterances_from_words(words)
    stem = f"transcripts/{source['file_hash']}_{source['language']}_{source['dialect'] or 'default'}_{result['provider']}"
    transcript = conn.execute(
        """insert into transcripts (source_id, owner_operator_id, source_hash, language, dialect, provider, model,
               word_json_path, srt_path, language_code) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning *""",
        (source["id"], source["owner_operator_id"], source["file_hash"], source["language"], source["dialect"],
         result["provider"], result.get("model"), stem + ".json", stem + ".srt", result.get("language_code")),
    ).fetchone()
    _write_version(conn, transcript, 1, words, utterances, speaker_map(words), None)
    return transcript


def load(conn, source_id) -> dict:
    """The newest version of the transcript for a source (found by this source, else by the same file and language)."""
    row = conn.execute(
        """select t.id, t.provider, t.dialect, t.language, t.language_code, v.version_no, v.word_json_path as words_key,
                  v.speakers, v.utterances
           from source_assets s
           join transcripts t on t.source_hash = s.file_hash and t.language = s.language
           join transcript_versions v on v.transcript_id = t.id
                and v.version_no = (select max(version_no) from transcript_versions where transcript_id = t.id)
           where s.id = %s order by (t.source_id = s.id) desc, t.created_at desc limit 1""",
        (source_id,),
    ).fetchone()
    if row is None:
        raise AppError("No transcript for this source.", "no_transcript")
    raw = json.loads(registry.get(conn, "storage").path(row["words_key"]).read_text(encoding="utf-8"))
    words = [{"text": w["text"], "start": w["start"], "end": w["end"], "confidence": w.get("confidence"),
              "speaker": w.get("speaker")} for w in raw]
    return {"id": str(row["id"]), "version": row["version_no"], "provider": row["provider"],
            "dialect": row["language_code"] or row["dialect"] or row["language"], "words": words,
            "utterances": row["utterances"] or utterances_from_words(words),
            "speakers": row["speakers"] or speaker_map(words)}


def save_version(conn, actor, transcript_id, words: list[dict], speakers: dict | None, base_version: int) -> int:
    """Save an edited transcript as the next version. `base_version` is the version the edit started from:
    if someone saved in between, StaleTranscript tells the caller which version is current."""
    transcript = conn.execute("select * from transcripts where id = %s for update", (transcript_id,)).fetchone()
    if transcript is None:
        raise AppError("Transcript not found.", "not_found")
    if base_version != transcript["current_version"]:
        raise StaleTranscript(transcript["current_version"])
    cleaned = clean_words(words)
    if not cleaned:
        raise AppError("A transcript needs at least one word.", "empty_transcript")
    version_no = transcript["current_version"] + 1
    _write_version(conn, transcript, version_no, cleaned, utterances_from_words(cleaned), speaker_map(cleaned, speakers),
                   actor.operator_id)
    conn.execute("update transcripts set current_version = %s where id = %s", (version_no, transcript["id"]))
    audit(conn, "transcript.edit", "transcripts", transcript["id"], {"version": version_no, "words": len(cleaned)})
    return version_no
