"""Per-second signals of a source, measured once and stored (table source_signals). Scoring is not here: this module
only measures, and the moment engine reads the numbers through `load`.

Series (one value per second, `step_s` = 1.0, every list the same length):
  loudness     EBU R128 momentary loudness in LUFS (FFmpeg ebur128), -70 for silence
  speech_rate  words spoken per second, from the transcript; 0 in pauses and in silent stretches. Fast talk is a high
               value of this series (compare it with the source's own median)
  turns        speaker changes that start in the second (needs speaker labels, else 0)
  scene        0..1, how much the picture changed since the previous second (a cut is near 1)
  face         0..1, how much of the frame is skin-toned: a cheap stand-in for "a person is on screen"
  laughter     0..1, laughter or applause (sherpa-onnx model when installed, else a loudness-burst heuristic)
  reaction     reaction phrases ("no way", "والله") said in the second, English and Arabic; 0 in silent seconds
  chat         live chat messages per second, or None when the stream had no chat replay
  replay       YouTube "most replayed" 0..1, or None when the video has no heatmap
`topics` are TextTiling-style topic segments of the transcript: [{start, end, title}].

Media is decoded once (one audio pass, one tiny-frame video pass, run side by side). The text signals depend on the
transcript, so when the owner corrects it they are rebuilt from the stored loudness without touching FFmpeg again."""
from __future__ import annotations

import math
import statistics
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from psycopg.types.json import Jsonb

from . import ffmpeg, transcripts
from .adapters import audiotag, heatmap
from .alerts import registry
from .core import AppError

STEP_S = 1.0
MAX_SECONDS = 24 * 3600
SERIES = ("loudness", "speech_rate", "turns", "scene", "face", "laughter", "reaction", "chat", "replay")
SILENT_LUFS = -60.0          # speech signals are zero where the audio is this quiet or quieter
MAX_WORD_S = 10.0            # a word's span is capped so one bad timestamp cannot smear across the file
SCENE_FULL = 50.0            # mean colour change (0..255) that counts as a full cut
FACE_FULL = 0.10             # share of skin-toned pixels that counts as a face being clearly on screen

# Reaction phrases, compared after normalisation (case, punctuation and Arabic vowel marks removed; alef/ya folded).
REACTIONS = {
    "en": ["no way", "oh my god", "oh my gosh", "are you serious", "i can't believe", "shut up", "holy cow", "holy crap",
           "what the hell", "wait what", "wow", "unbelievable", "insane", "oh my"],
    "ar": ["والله", "مستحيل", "يا الله", "يا الهي", "سبحان الله", "ما شاء الله", "غير معقول", "لا يمكن", "يا سلام", "ياه",
           "لا والله", "معقول"],
}
STOPWORDS = frozenset("""the and for are but not you all can had her was one our out has have that with this from they
will would there their what about which when your said them these been than then into just like more some very also
because even over such only does were here know yeah okay going really thing think right""".split()) | frozenset(
    "في من على الى إلى عن مع هذا هذه ذلك التي الذي كان كانت هو هي ان أن لا ما لم لن قد كل او أو ثم لكن هناك عند".split())
BLOCK_TOKENS = 12            # topic detection: content words per block
BLOCKS_PER_SIDE = 6          # ... blocks compared on each side of a gap
MIN_TOPIC_S = 60.0


def _round(values, places=3) -> list[float]:
    return [round(float(v), places) for v in values]


def _fit(values, size: int, fill=0.0) -> list:
    """Exactly `size` entries: cut a longer list, pad a shorter one."""
    return list(values[:size]) + [fill] * max(size - len(values), 0)


# ------------------------------------------------------------------ media (FFmpeg)

def _measure_video(path, duration_s: float) -> tuple[list[float], list[float]]:
    """(scene, face) per second from one-per-second 64x36 frames."""
    import numpy as np

    scene, face, previous = [], [], None
    for raw in ffmpeg.video_thumbs(path, duration_s):
        if len(raw) < ffmpeg.THUMB_BYTES:
            break
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(ffmpeg.THUMB_H, ffmpeg.THUMB_W, 3).astype(np.int16)
        scene.append(0.0 if previous is None else min(float(np.abs(frame - previous).mean()) / SCENE_FULL, 1.0))
        previous = frame
        face.append(min(float(ffmpeg.skin_mask(frame).mean()) / FACE_FULL, 1.0))
    return scene, face


def _measure_audio(path) -> tuple[list[float], list[float]]:
    """(loudness, laughter) per second from one audio decode."""
    import numpy as np

    tagger = audiotag.pick_tagger()
    loudness = ffmpeg.audio_pass(path, lambda raw: tagger.observe(np.frombuffer(raw[:len(raw) // 2 * 2], dtype=np.int16)))
    return loudness, tagger.finish(loudness)


def measure_media(path, seconds: int, duration_s: float, has_audio: bool, has_video: bool) -> dict[str, list[float]]:
    """The FFmpeg-derived series. Missing audio or video gives silence or a still picture, not an error."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        audio = pool.submit(_measure_audio, path) if has_audio else None
        scene, face = _measure_video(path, duration_s) if has_video else ([], [])
        loudness, laughter = audio.result() if audio else ([], [])
    return {"loudness": _round(_fit(loudness, seconds, ffmpeg.SILENCE_LUFS), 1), "laughter": _round(_fit(laughter, seconds)),
            "scene": _round(_fit(scene, seconds)), "face": _round(_fit(face, seconds))}


# ------------------------------------------------------------------ transcript signals

def _norm(text: str) -> str:
    return transcripts.normalise(text, fold_arabic=True)


def _reaction_phrases() -> dict[tuple[str, ...], int]:
    phrases = {}
    for items in REACTIONS.values():
        for phrase in items:
            tokens = tuple(t for t in (_norm(p) for p in phrase.split()) if t)
            if tokens:
                phrases[tokens] = len(tokens)
    return phrases


def _reactions(words: list[dict], seconds: int) -> list[float]:
    phrases = _reaction_phrases()
    longest = max(phrases.values())
    tokens = [_norm(w["text"]) for w in words]
    series = [0.0] * seconds
    for i, word in enumerate(words):
        for size in range(1, longest + 1):
            if tuple(tokens[i:i + size]) in phrases and 0 <= word["start"] < seconds:
                series[int(word["start"])] += 1
                break
    return series


def _speech_rate(words: list[dict], seconds: int) -> list[float]:
    """Each word spreads one unit over the seconds it spans, so 0 means nobody spoke in that second."""
    series = [0.0] * seconds
    for word in words:
        start = max(word["start"], 0.0)
        end = min(max(word["end"], start + 0.05), start + MAX_WORD_S)
        for second in range(int(start), min(math.ceil(end), seconds)):
            series[second] += (min(end, second + 1) - max(start, second)) / (end - start)
    return series


def _turns(utterances: list[dict], seconds: int) -> list[int]:
    series = [0] * seconds
    previous = None
    for utt in sorted(utterances, key=lambda u: u["start"]):
        speaker = utt.get("speaker")
        if previous is not None and speaker is not None and speaker != previous and 0 <= utt["start"] < seconds:
            series[int(utt["start"])] += 1
        previous = speaker if speaker is not None else previous
    return series


def _cosine(a: Counter, b: Counter) -> float:
    dot = sum(n * b.get(k, 0) for k, n in a.items())
    norm = math.sqrt(sum(n * n for n in a.values())) * math.sqrt(sum(n * n for n in b.values()))
    return dot / norm if norm else 0.0


def topic_segments(words: list[dict], duration_s: float) -> list[dict]:
    """TextTiling: cut the transcript into blocks of content words, score how alike the blocks before and after each gap
    are, and cut where the similarity dips deeper than average (and at least MIN_TOPIC_S from the last cut)."""
    content = [(_norm(w["text"]), w["text"].strip(" .,;:!?\"'()[]،؟"), w["start"]) for w in words]
    content = [c for c in content if len(c[0]) > 2 and c[0] not in STOPWORDS]
    blocks = [content[i:i + BLOCK_TOKENS] for i in range(0, len(content), BLOCK_TOKENS)]
    end = float(duration_s)
    cuts: list[float] = []
    k = BLOCKS_PER_SIDE
    if len(blocks) >= 2 * k:
        bags = [Counter(t[0] for t in block) for block in blocks]
        gaps = list(range(k, len(blocks) - k + 1))
        sims = [_cosine(sum(bags[g - k:g], Counter()), sum(bags[g:g + k], Counter())) for g in gaps]
        depth = []
        for i, sim in enumerate(sims):
            left = max(sims[:i + 1])
            right = max(sims[i:])
            depth.append((left - sim) + (right - sim))
        cutoff = statistics.fmean(depth) + 0.5 * statistics.pstdev(depth)
        for i in sorted(range(len(gaps)), key=lambda j: -depth[j]):   # deepest valleys first
            at = blocks[gaps[i]][0][2]
            if depth[i] > cutoff and all(abs(at - c) >= MIN_TOPIC_S for c in cuts) and at >= MIN_TOPIC_S and end - at >= MIN_TOPIC_S:
                cuts.append(at)
    edges = [0.0, *sorted(cuts), end]
    topics = []
    for start, stop in zip(edges, edges[1:]):
        counts = Counter(c[1].lower() for c in content if start <= c[2] < stop)
        title = " ".join(w for w, _ in counts.most_common(3))
        topics.append({"start": round(start, 2), "end": round(stop, 2), "title": title})
    return topics


def text_signals(transcript: dict, loudness: list[float], duration_s: float) -> dict:
    """The series built from the transcript. Seconds quieter than SILENT_LUFS carry no speech whatever the words say."""
    seconds = len(loudness)
    words = transcript["words"]
    quiet = [level <= SILENT_LUFS for level in loudness]
    speech = [0.0 if q else v for v, q in zip(_speech_rate(words, seconds), quiet)]
    turns = [0 if q else v for v, q in zip(_turns(transcript["utterances"], seconds), quiet)]
    reaction = [0.0 if q else v for v, q in zip(_reactions(words, seconds), quiet)]
    return {"speech_rate": _round(speech), "turns": turns, "reaction": _round(reaction),
            "topics": topic_segments(words, duration_s)}


# ------------------------------------------------------------------ storage

def _row(conn, source_id) -> dict | None:
    return conn.execute("select * from source_signals where source_id = %s", (source_id,)).fetchone()


def record_inputs(conn, source: dict, heatmap_segments, chat_counts) -> None:
    """Keep what a YouTube fetch found (already validated by adapters.heatmap): None means the video had none."""
    conn.execute(
        """insert into source_signals (source_id, owner_operator_id, heatmap, chat_counts) values (%s, %s, %s, %s)
           on conflict (source_id) do update set heatmap = excluded.heatmap, chat_counts = excluded.chat_counts""",
        (source["id"], source["owner_operator_id"], Jsonb(heatmap_segments) if heatmap_segments else None,
         Jsonb(chat_counts) if chat_counts else None))


def _shape(row: dict) -> dict:
    return {"step_s": float(row["step_s"]), "series": row["series"], "topics": row["topics"]}


def load(conn, source_id) -> dict | None:
    """The stored signals of a source, or None when they have not been computed (the moment engine then runs without).
    When the owner has corrected the transcript since, the text series and topics are rebuilt first (the media numbers are
    reused, FFmpeg does not run); a changed file is not re-measured here."""
    row = _row(conn, source_id)
    if not (row and row["series"]):
        return None
    current = transcripts.current_version(conn, source_id)
    if current is not None and row["transcript_version"] != current:
        file_hash = conn.execute("select file_hash from source_assets where id = %s", (source_id,)).fetchone()
        if file_hash and file_hash["file_hash"] == row["file_hash"]:
            return compute(conn, source_id)
    return _shape(row)


def _inputs_series(row: dict | None, seconds: int) -> dict:
    return {"chat": heatmap.chat_series(row["chat_counts"], seconds) if row else None,
            "replay": heatmap.replay_series(row["heatmap"], seconds) if row else None}


def compute(conn, source_id) -> dict:
    """The signals of a source, measured now if they are not stored yet. A second call does no FFmpeg work; after a
    transcript correction only the text signals are rebuilt. A video FFmpeg cannot read raises AppError("bad_media")."""
    source = conn.execute("select * from source_assets where id = %s", (source_id,)).fetchone()
    if source is None:
        raise AppError("That source does not exist.", "no_source")
    row = _row(conn, source_id)
    transcript = transcripts.load(conn, source_id)
    cached = row is not None and row["series"] and row["file_hash"] == source["file_hash"]
    if cached and row["transcript_version"] == transcript["version"]:
        return _shape(row)
    if cached:
        media = {k: row["series"][k] for k in ("loudness", "laughter", "scene", "face")}
        duration_s = float(source["duration_s"] or len(media["loudness"]))
    else:
        path = registry.get(conn, "storage").path(source["stored_path"])
        try:
            info = ffmpeg.probe(path)
            duration_s = info["duration_s"]
            seconds = max(math.ceil(duration_s), 1)
            if seconds > MAX_SECONDS:
                raise AppError("The video is longer than 24 hours.", "too_long")
            media = measure_media(path, seconds, duration_s, info["has_audio"], info["width"] is not None)
        except ffmpeg.FFmpegError:
            raise AppError("The video could not be read, so its signals could not be measured.", "bad_media") from None
    seconds = len(media["loudness"])
    text = text_signals(transcript, media["loudness"], duration_s)
    topics = text.pop("topics")
    series = {"loudness": media["loudness"], "speech_rate": text["speech_rate"], "turns": text["turns"],
              "scene": media["scene"], "face": media["face"], "laughter": media["laughter"], "reaction": text["reaction"],
              **_inputs_series(row, seconds)}
    row = conn.execute(
        """insert into source_signals (source_id, owner_operator_id, file_hash, transcript_version, step_s, series, topics, computed_at)
           values (%s, %s, %s, %s, %s, %s, %s, now())
           on conflict (source_id) do update set file_hash = excluded.file_hash, transcript_version = excluded.transcript_version,
               step_s = excluded.step_s, series = excluded.series, topics = excluded.topics, computed_at = now()
           returning *""",
        (source_id, source["owner_operator_id"], source["file_hash"], transcript["version"], Decimal(str(STEP_S)),
         Jsonb(series), Jsonb(topics))).fetchone()
    return _shape(row)
