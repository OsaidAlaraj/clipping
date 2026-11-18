"""Transcripts: versioned words with speakers and confidence, stored once per source file.

A transcript row is one provider's reading of one source. Each save (the first reading, then every correction)
adds a row to transcript_versions with its own word file; `load` always returns the newest version.
Word file shape (a plain JSON list, as before): [{text, start, end, confidence, speaker}]. Older files without
confidence or speaker still load.

The correction screen's logic lives here too: applying the owner's edits, the "fix what matters first" queue, the
word error rate behind the accuracy report, and the campaign's key terms. Owner edits are external input and are
validated before anything is saved."""
from __future__ import annotations

import difflib
import json
import math
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Callable

from psycopg.types.json import Jsonb

from . import config
from .adapters.transcription import utterances_from_words
from .alerts import registry
from .core import AppError, audit

MAX_SPEAKER_NAME = 60
MAX_EDITS = 500            # edits in one save
MAX_TEXT = 200             # characters in one typed word, phrase or replacement
BULK_WORDS = 100           # a replace touching more words than this needs the owner's confirmation
LOW_CONFIDENCE = 0.8       # words the provider was less sure of than this go to the fix queue
KEY_TERM_SIMILARITY = 0.75
MAX_QUEUE = 200
MAX_KEY_TERMS = 200
MAX_TERM_CHARS = 100


class NotFound(AppError):
    """Missing, or hidden by row-level security: the caller cannot tell which, so another owner's id is a 404."""
    status = 404


class StaleTranscript(AppError):
    """The transcript changed since the caller loaded it. `version` is the current one."""
    status = 409

    def __init__(self, version: int):
        super().__init__("This transcript was changed somewhere else. Reload it and apply your edit again.", "stale")
        self.version = version
        self.extra = {"version": version}


class BulkReplace(AppError):
    """A replace would touch more than BULK_WORDS words and the caller has not confirmed."""
    status = 409

    def __init__(self, words: int):
        super().__init__(f"This would change {words} words. Confirm to go ahead.", "confirm_bulk")
        self.extra = {"words": words}


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


def _read_words(conn, key: str) -> list[dict]:
    raw = json.loads(registry.get(conn, "storage").path(key).read_text(encoding="utf-8"))
    return [{"text": w["text"], "start": w["start"], "end": w["end"], "confidence": w.get("confidence"),
             "speaker": w.get("speaker")} for w in raw]


def _read_version(conn, transcript_id, version_no: int) -> tuple[list[dict], dict]:
    """The words and speaker names of one saved version."""
    row = conn.execute("select word_json_path, speakers from transcript_versions where transcript_id = %s and version_no = %s",
                       (transcript_id, version_no)).fetchone()
    if row is None:
        raise AppError("That version of the transcript does not exist.", "no_version")
    words = _read_words(conn, row["word_json_path"])
    return words, dict(row["speakers"] or speaker_map(words))


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
        raise NotFound("No transcript for this source.", "no_transcript")
    words = _read_words(conn, row["words_key"])
    return {"id": str(row["id"]), "version": row["version_no"], "provider": row["provider"],
            "dialect": row["language_code"] or row["dialect"] or row["language"], "words": words,
            "utterances": row["utterances"] or utterances_from_words(words),
            "speakers": row["speakers"] or speaker_map(words)}


def save_version(conn, actor, transcript_id, words: list[dict], speakers: dict | None, base_version: int) -> int:
    """Save an edited transcript as the next version. `base_version` is the version the edit started from:
    if someone saved in between, StaleTranscript tells the caller which version is current."""
    transcript = conn.execute("select * from transcripts where id = %s for update", (transcript_id,)).fetchone()
    if transcript is None:
        raise NotFound("Transcript not found.", "not_found")
    if base_version != transcript["current_version"]:
        raise StaleTranscript(transcript["current_version"])
    cleaned = clean_words(words)
    if not cleaned:
        raise AppError("A transcript needs at least one word.", "empty_transcript")
    version_no = transcript["current_version"] + 1
    _write_version(conn, transcript, version_no, cleaned, utterances_from_words(cleaned), speaker_map(cleaned, speakers),
                   actor.operator_id)
    original = _read_version(conn, transcript["id"], 1)[0]
    errors, total = wer_counts([w["text"] for w in cleaned], [w["text"] for w in original])
    conn.execute("update transcripts set current_version = %s, wer_errors = %s, wer_words = %s where id = %s",
                 (version_no, errors, total, transcript["id"]))
    audit(conn, "transcript.edit", "transcripts", transcript["id"], {"version": version_no, "words": len(cleaned)})
    return version_no


def update(conn, actor, source_id, base_version: int, edits, confirm_bulk: bool = False) -> int:
    """Apply the owner's edits to the version they were looking at and save the result as the next version.
    A different current version is refused as stale; a big replace is refused until confirmed."""
    current = load(conn, source_id)
    if base_version != current["version"]:
        raise StaleTranscript(current["version"])
    words, speakers = apply_edits(current["words"], current["speakers"], edits, confirm_bulk,
                                  lambda number: _read_version(conn, current["id"], number))
    return save_version(conn, actor, current["id"], words, speakers, base_version)


# ------------------------------------------------------------------ text matching and word error rate

_ARABIC_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي"})   # alef and ya variants
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _is_mark_or_punct(char: str) -> bool:
    category = unicodedata.category(char)
    return category[0] in "PS" or category in ("Mn", "Cf", "Cc") or char == "ـ"   # ـ: tatweel


def normalise(text: str, fold_arabic: bool = False, keep_case: bool = False) -> str:
    """The comparable form of a word: no case, punctuation or Arabic vowel marks. With fold_arabic the alef and ya
    variants match each other too (for finding words, not for counting errors)."""
    text = unicodedata.normalize("NFKC", str(text))
    text = text if keep_case else text.casefold()
    if fold_arabic:
        text = text.translate(_ARABIC_FOLD)
    return "".join(c for c in text if not c.isspace() and not _is_mark_or_punct(c))


def _tokens(texts) -> list[str]:
    return [token for text in texts for piece in str(text).split() if (token := normalise(piece))]


def _levenshtein(a: list, b: list) -> int:
    if not a or not b:
        return max(len(a), len(b))
    if len(a) * len(b) > 250_000:   # a huge unanchored stretch: count it as all wrong rather than stall the save
        return max(len(a), len(b))
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, start=1):
        current = [i]
        for j, y in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def wer_counts(corrected: list[str], heard: list[str]) -> tuple[int, int]:
    """(errors, words): the edits that turn what the provider heard into the owner's corrected text, and the number of
    words in the corrected text. Case, punctuation and Arabic vowel marks do not count. Matching runs of words anchor
    the comparison, so a long transcript with a few fixes stays fast."""
    truth, got = _tokens(corrected), _tokens(heard)
    errors, i, j = 0, 0, 0
    for a, b, size in difflib.SequenceMatcher(None, got, truth).get_matching_blocks():
        errors += _levenshtein(got[i:a], truth[j:b])
        i, j = a + size, b + size
    return errors, len(truth)


def accuracy(conn) -> list[dict]:
    """Word error rate per provider and dialect, from the transcripts the owner has corrected (or confirmed)."""
    rows = conn.execute(
        """select provider, coalesce(nullif(language_code, ''), nullif(dialect, ''), language) as dialect,
                  sum(wer_errors)::int as errors, sum(wer_words)::int as words
           from transcripts where current_version > 1 and wer_words > 0 group by 1, 2 order by 2, 1""").fetchall()
    return [{"provider": r["provider"], "dialect": r["dialect"], "wer": round(r["errors"] / r["words"], 4), "words": r["words"]}
            for r in rows]


# ------------------------------------------------------------------ edits (the owner's input)

def _bad_edit(message: str) -> AppError:
    return AppError(message, "bad_edit")


def _typed(value, what: str, limit: int = MAX_TEXT) -> str:
    """Free text from the owner: control characters become spaces, spaces collapse, and it must fit."""
    if not isinstance(value, str):
        raise _bad_edit(f"{what} must be text.")
    text = " ".join(_CONTROL.sub(" ", value).split())
    if len(text) > limit:
        raise _bad_edit(f"{what} can be at most {limit} characters.")
    return text


def _index(value, size: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < size:
        raise _bad_edit("That word is no longer in the transcript.")
    return value


def _spread(tokens: list[str], start: float, end: float, speaker) -> list[dict]:
    """New words from typed text, sharing the time of the words they replace. The owner vouches for them: confidence 1."""
    step = (end - start) / len(tokens)
    return [{"text": t, "start": round(start + n * step, 3), "end": round(start + (n + 1) * step, 3),
             "confidence": 1.0, "speaker": speaker} for n, t in enumerate(tokens)]


def _edges(text: str) -> tuple[str, str]:
    """The punctuation before and after a word ("“Acme," gives ("“", ","))."""
    start = 0
    while start < len(text) and unicodedata.category(text[start])[0] in "PS":
        start += 1
    end = len(text)
    while end > start and unicodedata.category(text[end - 1])[0] in "PS":
        end -= 1
    return text[:start], text[end:]


def _edit_word(words: list[dict], edit: dict) -> None:
    i = _index(edit.get("index"), len(words))
    tokens = _typed(edit.get("text"), "The word").split()
    word = words[i]
    if tokens == [word["text"]]:   # the same text: the owner confirms the word is right
        words[i] = {**word, "confidence": 1.0}
    else:                          # blank deletes the word; several words share its time
        words[i:i + 1] = _spread(tokens, word["start"], word["end"], word["speaker"]) if tokens else []


def _edit_replace(words: list[dict], edit: dict, confirm_bulk: bool) -> None:
    find = _typed(edit.get("find"), "The text to find")
    new = _typed(edit.get("replace", ""), "The replacement")
    keep_case = edit.get("match_case") is True
    needle = [normalise(t, True, keep_case) for t in find.split()]
    if not needle or not all(needle):
        raise _bad_edit("Enter the words to find.")
    keys = [normalise(w["text"], True, keep_case) for w in words]
    spans, i = [], 0
    while i <= len(words) - len(needle):
        if keys[i:i + len(needle)] == needle:
            spans.append((i, i + len(needle)))
            i += len(needle)
        else:
            i += 1
    touched = sum(b - a for a, b in spans)
    if touched > BULK_WORDS and not confirm_bulk:
        raise BulkReplace(touched)
    for a, b in reversed(spans):                       # from the end, so earlier positions stay valid
        first, last = words[a], words[b - 1]
        tokens = new.split()
        if tokens:                                     # keep the punctuation that stood around the old words
            tokens[0] = _edges(first["text"])[0] + tokens[0]
            tokens[-1] += _edges(last["text"])[1]
        if len(tokens) == b - a:                       # same count: every word keeps its own time and speaker
            words[a:b] = [{**old, "text": t, "confidence": 1.0} for old, t in zip(words[a:b], tokens)]
        else:
            words[a:b] = _spread(tokens, first["start"], last["end"], first["speaker"]) if tokens else []


def _edit_speaker(words: list[dict], speakers: dict, edit: dict) -> None:
    speaker = edit.get("speaker")
    if not isinstance(speaker, str) or speaker not in {w["speaker"] for w in words if w.get("speaker") is not None}:
        raise _bad_edit("That speaker is not in this transcript.")
    speakers[speaker] = _typed(edit.get("name", ""), "The name", MAX_SPEAKER_NAME)   # blank goes back to "Speaker N"


def apply_edits(words: list[dict], speakers: dict, edits, confirm_bulk: bool = False,
                read_version: Callable[[int], tuple[list[dict], dict]] | None = None) -> tuple[list[dict], dict]:
    """Apply edits in order to copies of the words and speaker names; the arguments are never changed.
    Ops: word {index, text}, replace {find, replace, match_case}, speaker {speaker, name}, revert {to_version}.
    An index means a position in the list as the previous edit left it."""
    if not isinstance(edits, list) or not edits:
        raise _bad_edit("There is nothing to save.")
    if len(edits) > MAX_EDITS:
        raise _bad_edit(f"Save at most {MAX_EDITS} edits at once.")
    words, speakers = [dict(w) for w in words], dict(speakers)
    for edit in edits:
        op = edit.get("op") if isinstance(edit, dict) else None
        if op == "word":
            _edit_word(words, edit)
        elif op == "replace":
            _edit_replace(words, edit, confirm_bulk)
        elif op == "speaker":
            _edit_speaker(words, speakers, edit)
        elif op == "revert" and read_version is not None:
            number = edit.get("to_version")
            if isinstance(number, bool) or not isinstance(number, int) or number < 1:
                raise _bad_edit("Pick a version to go back to.")
            words, speakers = read_version(number)
        else:
            raise _bad_edit("That kind of edit is not supported.")
    return words, speakers


# ------------------------------------------------------------------ the fix queue

def _near_key_terms(keys: list[str], key_terms) -> list[tuple[float, int, int, str]]:
    """(similarity, first word, word count, term) for stretches of words that nearly spell a key term, best first,
    none overlapping. A stretch of the same words as the term (any letter case) is right and is never listed; the same
    letters split differently ("acmecola" for "Acme Cola") are listed."""
    terms, seen = [], set()
    for term in key_terms:
        parts = [k for k in (normalise(p, True) for p in str(term).split()) if k]
        joined = "".join(parts)
        if len(joined) >= 4 and joined not in seen:   # shorter terms would flag ordinary words
            seen.add(joined)
            terms.append((str(term), parts, joined))
    ratios: dict[tuple[str, str], float] = {}
    found = []
    for term, parts, joined in terms:
        count = len(parts)
        exact = {i + n for i in range(len(keys) - count + 1) if keys[i:i + count] == parts for n in range(count)}
        for size in sorted({max(1, count - 1), count, count + 1}):
            for i in range(len(keys) - size + 1):
                candidate = "".join(keys[i:i + size])
                if (not candidate or abs(len(candidate) - len(joined)) > max(2, len(joined) // 4)
                        or any(n in exact for n in range(i, i + size))):
                    continue
                ratio = ratios.get((candidate, joined))
                if ratio is None:
                    ratio = ratios[(candidate, joined)] = difflib.SequenceMatcher(None, candidate, joined).ratio()
                if ratio >= KEY_TERM_SIMILARITY:
                    found.append((ratio, i, size, term))
    taken, chosen = set(), []
    for ratio, i, size, term in sorted(found, key=lambda f: (-f[0], f[1])):
        if taken.isdisjoint(range(i, i + size)):
            taken.update(range(i, i + size))
            chosen.append((ratio, i, size, term))
    return chosen


def rank_words(words: list[dict], key_terms=(), ranges=None, threshold: float = LOW_CONFIDENCE,
               limit: int = MAX_QUEUE) -> list[dict]:
    """The words worth the owner's time, most important first: [{word_index, text, confidence, reason}].
    reason is "key_term" (nearly spells one of the campaign's words; the row also has `term` and `span`) or
    "low_confidence". `ranges` are the likely clip moments: (start_s, end_s) pairs or (start_s, end_s, weight) triples
    (a moment's score; a pair weighs 1). Words inside a moment come before the rest, the ones inside the strongest moment
    first; then key terms before low confidence; then the least sure first."""
    spans = []   # (word_index, word_count, reason, term)
    for _, i, size, term in _near_key_terms([normalise(w["text"], True) for w in words], key_terms):
        spans.append((i, size, "key_term", term))
    covered = {n for i, size, _, _ in spans for n in range(i, i + size)}
    spans += [(i, 1, "low_confidence", None) for i, w in enumerate(words)
              if i not in covered and w.get("confidence") is not None and w["confidence"] < threshold]
    moments = [(m[0], m[1], 1.0 if len(m) < 3 or m[2] is None else max(float(m[2]), 0.0)) for m in ranges or ()]

    def weight(i: int, size: int) -> float:
        """The best moment weight over the span's words; 0 when no moment covers it. A moment of weight 0 still counts as inside."""
        hits = [w for start, end, w in moments if any(words[n]["start"] < end and words[n]["end"] > start for n in range(i, i + size))]
        return max(hits) + 1.0 if hits else 0.0

    rows = []
    for i, size, reason, term in spans:
        known = [w["confidence"] for w in words[i:i + size] if w.get("confidence") is not None]
        row = {"word_index": i, "text": " ".join(w["text"] for w in words[i:i + size]), "confidence": min(known, default=None),
               "reason": reason}
        if term is not None:
            row.update(term=term, span=size)
        rows.append((-weight(i, size), reason != "key_term", 1.0 if row["confidence"] is None else row["confidence"], i, row))
    return [row for *_, row in sorted(rows, key=lambda r: r[:4])[:max(0, limit)]]


def review_queue(source, *args, **kwargs) -> list[dict]:
    """The fix queue, in two call shapes with one meaning:
      review_queue(words, key_terms=(), ranges=None, threshold=..., limit=...)       rank words already in hand (`rank_words`)
      review_queue(conn, transcript_id, moments=None, limit=...)                     the stored transcript, judged against its
          campaign's key terms, with `moments` (candidate dicts with start_s, end_s and optionally score) weighting the order.
          moments=None reads the kept moments of the transcript's source from the candidates table; [] means no weighting."""
    if isinstance(source, list):
        return rank_words(source, *args, **kwargs)
    return _queue_for_transcript(source, *args, **kwargs)


def moment_ranges(moments) -> list[tuple[float, float, float]]:
    """(start_s, end_s, score) triples from candidate dicts. Anything without usable times is skipped; a missing score weighs 0.5."""
    out = []
    for m in moments or ():
        try:
            start, end = float(m["start_s"]), float(m["end_s"])
            score = m.get("score")
            score = 0.5 if score is None else float(score)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if math.isfinite(start) and math.isfinite(end) and math.isfinite(score) and end > start:
            out.append((start, end, score))
    return out


# ------------------------------------------------------------------ key terms and the screen's data

def add_key_term(conn, campaign_id, term) -> list[str]:
    """Add a word or name the campaign cares about (sent to the provider next time, used by the fix queue now).
    A repeat in any letter case changes nothing."""
    text = _typed(term, "The key term", MAX_TERM_CHARS) if isinstance(term, str) else ""
    if not text:
        raise AppError("Enter the word or name to add.", "bad_term")
    row = conn.execute("select key_terms from campaigns where id = %s for update", (campaign_id,)).fetchone()
    if row is None:
        raise NotFound("Campaign not found.", "not_found")
    terms = list(row["key_terms"] or [])
    if any(t.casefold() == text.casefold() for t in terms):
        return terms
    if len(terms) >= MAX_KEY_TERMS:
        raise AppError(f"A campaign can have at most {MAX_KEY_TERMS} key terms.", "too_many_terms")
    terms.append(text)
    conn.execute("update campaigns set key_terms = %s where id = %s", (terms, campaign_id))
    return terms


def _source_context(conn, source_id) -> tuple[dict, dict, dict]:
    """(source row with its campaign, the campaign as key_terms_for wants it, the campaign's current rules version)."""
    row = conn.execute(
        """select s.stored_path, s.language, s.campaign_id, c.name, c.key_terms, c.current_version_id
           from source_assets s join campaigns c on c.id = s.campaign_id where s.id = %s""", (source_id,)).fetchone()
    if row is None:
        raise NotFound("No transcript for this source.", "no_transcript")
    version = conn.execute("select rules_json from campaign_versions where id = %s", (row["current_version_id"],)).fetchone()
    return row, {"name": row["name"], "key_terms": row["key_terms"]}, version or {}


def detail(conn, source_id) -> dict:
    """What the correction screen opens with: the transcript plus the source's media, campaign and key terms."""
    data = load(conn, source_id)
    row, _, _ = _source_context(conn, source_id)
    return {**data, "source_id": str(source_id), "campaign_id": str(row["campaign_id"]), "campaign_name": row["name"],
            "language": row["language"], "media_url": registry.get(conn, "storage").get_signed_url(row["stored_path"], 3600),
            "key_terms": list(row["key_terms"] or [])}


def source_moments(conn, source_id) -> list[dict]:
    """The moments the engine kept for this source's file (stored candidates that are not set aside or discarded)."""
    return conn.execute(
        """select ca.start_s, ca.end_s, ca.score from candidates ca join source_assets s on s.id = ca.source_id
           join source_assets me on me.file_hash = s.file_hash and me.language = s.language
           where me.id = %s and ca.status in ('new', 'hooked', 'rendered') order by ca.score desc nulls last""", (source_id,)).fetchall()


def queue_for(conn, source_id, limit: int = MAX_QUEUE, ranges=None, moments=None) -> list[dict]:
    """The fix queue for one source, judged against the campaign's key terms (its own, its name and its required tags).
    Words inside the source's likely clip moments come first: `moments` (candidate dicts), else `ranges` as given, else the
    kept candidates stored for the source."""
    words = load(conn, source_id)["words"]
    _, campaign, version = _source_context(conn, source_id)
    if moments is None and ranges is None:
        moments = source_moments(conn, source_id)
    weighted = moment_ranges(moments) if moments is not None else ranges
    return rank_words(words, key_terms_for(campaign, version), weighted, limit=limit)


def _queue_for_transcript(conn, transcript_id, moments=None, limit: int = MAX_QUEUE) -> list[dict]:
    row = conn.execute("select source_id from transcripts where id = %s", (transcript_id,)).fetchone()
    if row is None:
        raise NotFound("Transcript not found.", "not_found")
    return queue_for(conn, row["source_id"], limit, moments=moments)


def current_version(conn, source_id) -> int | None:
    """The newest saved version number of the transcript `load` would return for this source, without reading its words;
    None when the source has no transcript."""
    row = conn.execute(
        """select v.version_no from source_assets s
           join transcripts t on t.source_hash = s.file_hash and t.language = s.language
           join transcript_versions v on v.transcript_id = t.id
                and v.version_no = (select max(version_no) from transcript_versions where transcript_id = t.id)
           where s.id = %s order by (t.source_id = s.id) desc, t.created_at desc limit 1""", (source_id,)).fetchone()
    return row["version_no"] if row else None
