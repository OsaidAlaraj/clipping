"""Music bed (decision 32): a free, licence-checked catalog, a selector that matches a track to the clip, and the audio graph that
mixes it into the clip's single encode (sidechain ducking under speech, loudness per platform, no clipping).

Nothing here calls a network: tracks are files under `<data>/music` (or CLIPPING_MUSIC_DIR) whose names and SHA-256 are in
`assets/music-catalog.json`. A track whose file is missing or changed is skipped, never played. Every chosen track leaves a
licence record for render_meta (track, source, licence, attribution text the caption must carry)."""
from __future__ import annotations

import hashlib
import json
import math
import zlib
from functools import lru_cache
from pathlib import Path

from . import config

CATALOG_FILE = Path(__file__).resolve().parent / "assets" / "music-catalog.json"
NICHES = ("business", "gaming", "podcasts")
MOODS = ("calm", "upbeat", "playful", "tense", "driving", "mysterious")

# Loudness of the finished clip per platform (integrated LUFS, true peak dBTP). Both are the commonly used short-video target;
# kept as a table so a measured platform difference changes one line.
PLATFORM_LOUDNESS = {"tiktok": (-14.0, -1.5), "instagram": (-14.0, -1.5), "youtube": (-14.0, -1.5), "facebook": (-14.0, -1.5)}
DEFAULT_LOUDNESS = (-14.0, -1.5)

# Mix settings. Chosen by the measured bake-off in docs/bakeoff/final/music (speech-to-music ratio while someone talks, music lift in
# the gaps, final loudness error): the music sits at `music_lufs` and the compressor pulls it further down whenever speech is present.
MIX = {"music_lufs": -22.0, "threshold": 0.06, "ratio": 14.0, "attack_ms": 15, "release_ms": 350, "makeup": 1.0,
       "fade_in_s": 0.6, "fade_out_s": 1.2}

# Podcasts are speech: the bed stays off unless there is room for it (speech fills less than this share of the clip).
PODCAST_MAX_SPEECH_SHARE = 0.62


# ------------------------------------------------------------------ catalog

def music_dir() -> Path:
    explicit = config.env("CLIPPING_MUSIC_DIR")
    return Path(explicit) if explicit else config.data_dir() / "music"


@lru_cache(maxsize=1)
def _raw_catalog() -> dict:
    return json.loads(CATALOG_FILE.read_text(encoding="utf-8"))


def catalog() -> dict:
    return _raw_catalog()


def validate_track(track: dict) -> list[str]:
    """What is wrong with one catalog entry (empty when it is acceptable). A track without a clear licence is never usable."""
    problems = []
    for key in ("id", "title", "artist", "file", "source_url", "licence", "licence_url", "bpm", "energy", "moods", "niches", "duration_s", "fit_why"):
        if track.get(key) in (None, "", []):
            problems.append(f"missing {key}")
    if track.get("licence") not in _raw_catalog().get("accepted_licences", {}):
        problems.append("licence not accepted")
    if not set(track.get("niches") or []) <= set(NICHES):
        problems.append("unknown niche")
    if not set(track.get("moods") or []) <= set(MOODS):
        problems.append("unknown mood")
    if track.get("vocals") != "none":
        problems.append("track has vocals or the tag is missing")   # a bed under speech must not carry a competing voice
    if track.get("intro_s") is None:
        problems.append("missing intro_s")
    if not 0 <= float(track.get("energy") or 0) <= 1:
        problems.append("energy outside 0..1")
    return problems


def tracks(directory: Path | None = None, verify_hash: bool = True) -> list[dict]:
    """Usable tracks: valid entries whose file is present (and unchanged). Each gets `path`."""
    folder = directory or music_dir()
    out = []
    for track in _raw_catalog()["tracks"]:
        if validate_track(track):
            continue
        path = folder / track["file"]
        if not path.is_file():
            continue
        if verify_hash and track.get("sha256") and _sha256(path) != track["sha256"]:
            continue
        out.append({**track, "path": str(path)})
    return out


@lru_cache(maxsize=64)
def _sha256_cached(path: str, size: int, mtime: float) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    stat = path.stat()
    return _sha256_cached(str(path), stat.st_size, stat.st_mtime)


# ------------------------------------------------------------------ the clip's mood and energy

_UPBEAT = {"win", "best", "amazing", "love", "great", "free", "growth", "money", "success", "easy", "fun", "crazy", "insane", "huge"}
_TENSE = {"secret", "danger", "mistake", "never", "lost", "fail", "wrong", "problem", "warning", "dead", "scary", "worst", "risk"}
_PLAYFUL = {"lol", "haha", "funny", "joke", "laugh", "wild", "bro", "dude"}
_UPBEAT_AR = {"رائع", "ممتاز", "ربح", "نجاح", "مجاني", "احب", "أحب"}
_TENSE_AR = {"خطر", "سر", "خطأ", "مشكلة", "ابدا", "أبدا", "خسارة"}


def speech_share(words: list[dict], start_s: float, end_s: float) -> float:
    """Share of the clip's duration in which someone is speaking (0..1)."""
    span = max(end_s - start_s, 0.001)
    covered = sum(max(min(w["end"], end_s) - max(w["start"], start_s), 0.0) for w in words)
    return min(covered / span, 1.0)


def clip_profile(words: list[dict], start_s: float, end_s: float, signals: dict | None = None) -> dict:
    """The clip's `mood` (one of MOODS) and `energy` (0..1) from its words and, when measured, its loudness and laughter."""
    inside = [w for w in words if w["start"] >= start_s - 0.1 and w["end"] <= end_s + 0.1]
    span = max(end_s - start_s, 0.001)
    rate = len(inside) / span                                   # words per second; conversation is about 2.5
    text = " ".join(w["text"].lower().strip(".,!?;:\"'") for w in inside)
    tokens = set(text.split())
    bangs = sum(w["text"].count("!") for w in inside)
    loud = [v for v in ((signals or {}).get("loudness") or []) if v > -60]
    spread = (max(loud) - min(loud)) / 30 if len(loud) > 2 else 0.0
    laughter = max(((signals or {}).get("laughter") or [0]), default=0)
    cuts = sum(1 for v in ((signals or {}).get("scene") or []) if v and v > 0.5) / span * 10      # picture cuts per 10 s: the edit's pace
    energy = min(max(0.15 + 0.22 * (rate - 1.5) + 0.25 * min(spread, 1.0) + 0.05 * min(bangs, 4) + 0.2 * laughter + 0.04 * min(cuts, 6), 0.0), 1.0)
    up, tense, play = len(tokens & (_UPBEAT | _UPBEAT_AR)), len(tokens & (_TENSE | _TENSE_AR)), len(tokens & _PLAYFUL)
    if laughter > 0.5 or play:
        mood = "playful"
    elif tense > up:
        mood = "tense" if energy > 0.45 else "mysterious"
    elif up > 0 or energy > 0.6:
        mood = "upbeat" if energy < 0.75 else "driving"
    else:
        mood = "calm"
    return {"mood": mood, "energy": round(energy, 2), "speech_share": round(speech_share(inside, start_s, end_s), 2), "words_per_s": round(rate, 2), "cuts_per_10s": round(cuts, 1)}


# ------------------------------------------------------------------ the selector

def wants_music(niche: str | None, profile: dict, platform: str | None = None) -> tuple[bool, str]:
    """Whether the clip gets a bed, and why. Speech-heavy podcasts stay without one unless there is room for it."""
    if niche == "podcasts" and profile["speech_share"] > PODCAST_MAX_SPEECH_SHARE:
        return False, f"podcast clip is speech-heavy ({profile['speech_share']:.0%} of the clip)"
    return True, "bed fits the clip" if niche != "podcasts" else f"podcast clip has room ({profile['speech_share']:.0%} speech)"


def tempo_gap(bpm: float, target: float) -> float:
    """Distance in BPM between a track and the wanted tempo, where half and double time count as the same feel (a 75 BPM beat
    reads as 150 on a fast edit)."""
    return min(abs(bpm - target), abs(bpm * 2 - target), abs(bpm / 2 - target))


def score(track: dict, niche: str, profile: dict) -> float:
    """How well a track fits the clip (higher is better): energy and tempo closeness (tempo is matched to the energy of the speech
    and the pace of the cuts), mood match, niche priority, and a small cost for a slow intro (it only matters when the clip cannot
    start past it). Vocal tracks never reach this point."""
    target_bpm = 70 + 80 * profile["energy"]
    s = 3.0 * (1 - abs(track["energy"] - profile["energy"]))
    s += 1.5 * (1 - min(tempo_gap(track["bpm"], target_bpm) / 60, 1.0))
    s += 2.0 if profile["mood"] == track["moods"][0] else (1.0 if profile["mood"] in track["moods"] else 0.0)
    s += 1.0 if track["niches"][0] == niche else 0.0
    s -= 0.4 if float(track.get("intro_s", 0)) > 8 else 0.0
    return s


def offset_for(track: dict, duration_s: float, seed: str) -> float:
    """Where in the track to start: on a bar line (four beats), after the intro so the bed opens at full strength, varied by the clip
    so two clips on one track do not start alike. A track too short for that starts at 0 and loops."""
    bar = 4 * 60.0 / track["bpm"]
    first = math.ceil(float(track.get("intro_s", 0)) / bar) * bar
    room = float(track["duration_s"]) - duration_s - 1.0 - first
    if room <= bar:
        return 0.0
    bars = int(room // bar)
    return round(first + (zlib.crc32(seed.encode()) % (bars + 1)) * bar, 3)


def choose(niche: str | None, profile: dict, duration_s: float, platform: str | None = None, recent: list[str] | None = None,
           seed: str = "", pool: list[dict] | None = None) -> dict:
    """The decision: {"use": bool, "reason", and when used "track", "offset_s", "score"}. The most recent tracks (`recent`, newest
    first) are avoided: the last one is excluded outright while any other track fits, and older ones lose points."""
    niche = niche if niche in NICHES else "business"
    ok, reason = wants_music(niche, profile, platform)
    if not ok:
        return {"use": False, "reason": reason}
    pool = [t for t in (tracks() if pool is None else pool) if t.get("vocals", "none") == "none"]
    fitting = [t for t in pool if niche in t["niches"]] or pool
    if not fitting:
        return {"use": False, "reason": "no licensed track is available"}
    recent = recent or []
    if recent and len(fitting) > 1:
        fitting = [t for t in fitting if t["id"] != recent[0]] or fitting
    def final(t):
        age = recent.index(t["id"]) if t["id"] in recent else None
        return score(t, niche, profile) - (0.0 if age is None else 1.5 / (age + 1))
    best = max(fitting, key=lambda t: (final(t), t["id"]))
    return {"use": True, "reason": reason, "track": best, "offset_s": offset_for(best, duration_s, seed or best["id"]), "score": round(final(best), 2)}


# ------------------------------------------------------------------ licence record

def licence_record(track: dict, offset_s: float, platform: str | None) -> dict:
    """What render_meta keeps about the bed: enough to prove the right to use it and to build the caption credit."""
    lic = _raw_catalog()["accepted_licences"][track["licence"]]
    return {"track_id": track["id"], "title": track["title"], "artist": track["artist"], "source_url": track["source_url"],
            "licence": track["licence"], "licence_url": track["licence_url"], "licence_checked": _raw_catalog().get("licences_checked"),
            "attribution_required": bool(lic["attribution_required"]), "attribution_text": attribution(track) if lic["attribution_required"] else None,
            "offset_s": offset_s, "platform": platform}


def attribution(track: dict) -> str:
    return f"Music: \"{track['title']}\" by {track['artist']} ({track['source_url']}), {track['licence']}"


def credit_line(record: dict | None) -> str | None:
    """Short credit for the caption (None when the licence needs none)."""
    if not record or not record.get("attribution_required"):
        return None
    return record["attribution_text"]


# ------------------------------------------------------------------ the mix (inside the clip's one encode)

def target_loudness(platform: str | None) -> tuple[float, float]:
    return PLATFORM_LOUDNESS.get(platform or "", DEFAULT_LOUDNESS)


def music_gain_db(track: dict, mix: dict | None = None) -> float:
    """Gain that brings the track to the bed's level; each track is measured (integrated LUFS) so beds are even."""
    return round((mix or MIX)["music_lufs"] - float(track.get("lufs", -16.0)), 2)


def duck_graph(speech: str, music: str, out: str, mix: dict | None = None, gain_db: float = 0.0, duration_s: float | None = None) -> str:
    """Filter chain that turns `[music]` into the ducked bed `[out]`, keyed by `[speech]` (a copy of the voice)."""
    m = mix or MIX
    fade_out = f",afade=t=out:st={max(duration_s - m['fade_out_s'], 0):.3f}:d={m['fade_out_s']}" if duration_s else ""
    return (f"{music}volume={gain_db}dB,afade=t=in:st=0:d={m['fade_in_s']}{fade_out}[mus];"
            f"[mus]{speech}sidechaincompress=threshold={m['threshold']}:ratio={m['ratio']}:attack={m['attack_ms']}:release={m['release_ms']}"
            f":makeup={m['makeup']}:link=average{out}")


def audio_graph(plan: dict, platform: str | None, natural: bool, speech_in: str = "0:a:0", music_in: str = "1:a:0", out: str = "[a]",
                duration_s: float | None = None) -> str:
    """Complete audio graph for the encode: voice and (when `plan` is set) the ducked bed mixed, then loudness and a peak limiter.
    `plan`: {"gain_db": float}. Without a plan only the loudness step runs, as before."""
    lufs, tp = target_loudness(platform)
    norm = f"loudnorm=I={lufs}:TP={tp}:LRA={18 if natural else 11},alimiter=limit=0.89:level=disabled"
    if not plan:
        return f"[{speech_in}]{norm}{out}"
    duck = duck_graph("[sc]", f"[{music_in}]", "[md]", gain_db=plan["gain_db"], duration_s=duration_s)
    voice = f"loudnorm=I={lufs}:TP={tp}:LRA={18 if natural else 11}"   # the voice first, so the bed's level is relative to it
    return f"[{speech_in}]{voice},asplit=2[sp][sc];{duck};[sp][md]amix=inputs=2:duration=first:normalize=0,{norm}{out}"
