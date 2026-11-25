"""Clip polish inside the single encode: dead-air and filler tightening from word timestamps, and stress moments for the
punch-in zooms. Pure planning here (numbers in, numbers out); the filter graphs that use the plans live in adapters/media.py.

Times are seconds relative to the clip start unless a name says otherwise. Nothing here changes a word: tightening only drops
whole fillers and the middle of long silences, and every cut sits in quiet audio between words."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field

from . import ffmpeg

# ------------------------------------------------------------------ tuning (see docs/decisions.md, decision 34)

PAUSE_MIN_S = 0.4          # a gap between words longer than this is tightened
PAD_S = 0.12               # silence kept on each side of a cut (Whisper word times are good to about 0.1 s)
SENTENCE_PAD_S = 0.20      # after a sentence end the beat is kept a little longer
XFADE_S = 0.02             # audio crossfade at every cut
MAX_REMOVED_SHARE = 0.30   # never take more than this share of a clip out
MIN_KEEP_S = 0.30          # no kept stretch shorter than this
SNAP_S = 0.04              # a cut edge may slide this far to the quietest instant
RATE = 16000
HOP = 160                  # 10 ms envelope frames

SENTENCE_END = ".?!؟…"
FILLERS = {"en": {"um", "umm", "uh", "uhh", "uhm", "er", "erm", "hmm", "hm", "mm", "mmm", "mhm", "ah", "ahh"}}
# Arabic hesitation sounds: stretched alif + ha, "mm" runs. Never plain "اه" or "ايه", which are real words.
_AR_FILLER = re.compile(r"^(?:ا{2,}ه+|ا?م{2,}|إ?م{2,}|آ{1,}ه{2,}|ه{2,}م+)$")
_AR_SOFT = {"يعني"}        # a real word; cut only as a hesitation (next to a pause) or when said twice in a row
SOFT_PAUSE_S = 0.15
_STRIP = str.maketrans("", "", ".,;:!?،؛؟…\"'()-")


def _clean(text: str) -> str:
    return text.translate(_STRIP).strip().lower()


def is_filler(words: list[dict], i: int, language: str) -> bool:
    """Whether words[i] is a hesitation sound that can go. Conservative: a single standalone token, never part of a longer word."""
    token = _clean(words[i]["text"])
    if not token:
        return False
    if language == "ar":
        if _AR_FILLER.match(token):
            return True
        if token in _AR_SOFT:
            prev, nxt = (words[i - 1] if i else None), (words[i + 1] if i + 1 < len(words) else None)
            before = words[i]["start"] - prev["end"] if prev else 0.0
            after = nxt["start"] - words[i]["end"] if nxt else 0.0
            twice = (prev is not None and _clean(prev["text"]) in _AR_SOFT) or (nxt is not None and _clean(nxt["text"]) in _AR_SOFT)
            return twice or max(before, after) >= SOFT_PAUSE_S
        return False
    return token in FILLERS["en"]


# ------------------------------------------------------------------ the audio envelope

def envelope(src, start_s: float, duration_s: float):
    """Loudness (dB, one value per 10 ms) of the clip's audio, or None when it cannot be decoded."""
    import numpy as np

    try:
        proc = subprocess.run([ffmpeg.exe(), "-nostdin", "-loglevel", "error", "-ss", f"{start_s:.3f}", "-t", f"{duration_s:.3f}", "-i", str(src),
                               "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"], capture_output=True, timeout=300)
        pcm = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception:
        return None
    frames = len(pcm) // HOP
    if frames < 20:
        return None
    return 20 * np.log10(np.sqrt((pcm[:frames * HOP].reshape(frames, HOP) ** 2).mean(axis=1)) + 1e-6)


def _db_at(env, t: float) -> float:
    return float(env[min(max(int(t * 100), 0), len(env) - 1)])


def _quietest(env, t: float, lo: float, hi: float) -> float:
    """The instant within SNAP_S of `t` (and inside [lo, hi]) where the audio is quietest."""
    a, b = max(t - SNAP_S, lo), min(t + SNAP_S, hi)
    if env is None or b <= a:
        return t
    i0, i1 = int(a * 100), max(int(b * 100), int(a * 100) + 1)
    seg = env[i0:i1]
    return (i0 + int(seg.argmin()) + 0.5) / 100 if len(seg) else t


# ------------------------------------------------------------------ clip edges: start and end on a breath of silence

EDGE_SNAP = True           # switch for the before/after measurement (tools/cutq)
QUIET_DB = 18.0            # quieter than the speech level by this much counts as silence
LEAD_S, TAIL_S = 0.12, 0.15    # silence kept before the first sound and after the last one
EDGE_REACH_S = 0.6         # how far an edge may move to find its silence
GAP_FRAMES = 6             # a quiet stretch must last 60 ms to count as a gap


def snap_edges(src, start: float, end: float, words: list[dict] | None = None) -> tuple[float, float]:
    """Move a clip's start to just before the sentence's first sound and its end to just after the last, inside silence. The word
    times a transcript gives are good to about 0.1 s, so a cut placed on them clips the onset or the last syllable; the audio is
    the truth. An edge with no silence within EDGE_REACH_S stays where it is."""
    import numpy as np

    lo = max(start - EDGE_REACH_S - 0.3, 0.0)
    env = envelope(src, lo, end + EDGE_REACH_S + 0.3 - lo)
    if env is None or len(env) < 100:
        return start, end
    loud = env[env > np.percentile(env, 40)]
    if len(loud) < 50:
        return start, end
    quiet = env < float(np.percentile(loud, 50)) - QUIET_DB

    def gap_end_before(i: int, reach: int):      # the nearest quiet gap at or before frame i: (gap start, first loud frame after it)
        k = min(i, len(quiet) - 1)
        while k >= max(i - reach, 0):
            if quiet[k]:
                j = k
                while j > 0 and quiet[j - 1] and k - j < 400:
                    j -= 1
                if k - j + 1 >= GAP_FRAMES or j == 0:
                    e = k + 1
                    while e < len(quiet) and quiet[e]:
                        e += 1
                    return j, e
                k = j - 1
            else:
                k -= 1
        return None

    def gap_start_after(i: int, reach: int):     # the nearest quiet gap at or after frame i: (last loud frame before it, gap end)
        k = max(i, 0)
        while k < min(i + reach, len(quiet)):
            if quiet[k]:
                j = k
                while j < len(quiet) and quiet[j]:
                    j += 1
                if j - k >= GAP_FRAMES or j >= len(quiet):
                    return k, j
                k = j
            else:
                k += 1
        return None

    s_i, e_i = int(round((start - lo) * 100)), int(round((end - lo) * 100))
    new_start, new_end = start, end
    g = gap_end_before(s_i + 15, int(EDGE_REACH_S * 100) + 15)
    if g:
        onset = g[1]                              # first loud frame after the gap
        if onset - g[0] > 0 and onset >= s_i - 5:
            cand = max(lo + g[0] / 100, lo + onset / 100 - LEAD_S)
            if not (words and any(w["start"] < cand - 0.02 and w["end"] > cand + 0.02 for w in words)):
                new_start = cand
    h = gap_start_after(e_i - 15, int(EDGE_REACH_S * 100) + 15)
    if h:
        cand = min(lo + h[0] / 100 + TAIL_S, lo + h[1] / 100)
        last = max((w["end"] for w in words or [] if w["start"] < end), default=0.0)
        if cand >= last - 0.02:
            new_end = cand
    # running speech has no silence at a sentence edge: take the quietest instant near it (the dip between two words)
    sm = np.convolve(env, np.ones(3) / 3, mode="same")

    def dip(t: float, a: float, b: float) -> float:
        i0, i1 = max(int(round((t + a - lo) * 100)), 0), min(int(round((t + b - lo) * 100)), len(sm))
        return lo + (i0 + int(sm[i0:i1].argmin()) + 0.5) / 100 if i1 > i0 else t

    loud_now = lambda t: float(sm[min(max(int(round((t - lo) * 100)), 0), len(sm) - 1)]) > float(np.percentile(loud, 50)) - 10
    if new_start == start and loud_now(start):
        cand = dip(start, -0.25, 0.05)
        if not (words and any(w["start"] < cand - 0.02 and w["end"] > cand + 0.02 for w in words)) or not loud_now(cand):
            new_start = cand if not loud_now(cand) else start
    if new_end == end and loud_now(end):
        cand = dip(end, -0.05, 0.5)
        last = max((w["end"] for w in words or [] if w["start"] < end), default=0.0)
        if cand >= last - 0.02 and not loud_now(cand):
            new_end = cand
    return (new_start, new_end) if new_end - new_start > 5 else (start, end)


# ------------------------------------------------------------------ the plan

@dataclass
class Plan:
    """What tightening does to one clip: the stretches kept (in order) and the cuts between them."""
    duration: float
    keeps: list[tuple[float, float]]
    cuts: list[dict] = field(default_factory=list)    # {start, end, kind: pause|filler}
    dropped: set[int] = field(default_factory=set)     # indexes of the words removed with their filler cut
    prefix: tuple[float, float] | None = None          # a cold open: this stretch of the clip plays first, then the clip from its start

    @property
    def removed(self) -> float:
        return sum(c["end"] - c["start"] for c in self.cuts)

    @property
    def prefix_s(self) -> float:
        return self.prefix[1] - self.prefix[0] if self.prefix else 0.0

    @property
    def new_duration(self) -> float:
        return self.duration - self.removed + self.prefix_s

    def map(self, t: float) -> float:
        """A time of the original clip on the tightened timeline (a time inside a cut lands where the cut was)."""
        gone = 0.0
        for c in self.cuts:
            if t >= c["end"]:
                gone += c["end"] - c["start"]
            elif t > c["start"]:
                return c["start"] - gone + self.prefix_s
            else:
                break
        return t - gone + self.prefix_s

    def words(self, words: list[dict], clip_start: float, clip_end: float) -> list[dict]:
        """The clip's words on the tightened timeline, relative to 0, without the fillers that were cut."""
        out = []
        if self.prefix:   # the cold open's own words, on the first seconds of the timeline
            a, b = self.prefix
            out += [{**w, "start": w["start"] - clip_start - a, "end": w["end"] - clip_start - a} for w in words
                    if w["start"] - clip_start >= a - 0.05 and w["end"] - clip_start <= b + 0.05]
        for i, w in enumerate(words):
            if w["end"] <= clip_start or w["start"] >= clip_end or i in self.dropped:
                continue
            out.append({**w, "start": self.map(w["start"] - clip_start), "end": self.map(w["end"] - clip_start)})
        return out


def with_story(plan: Plan | None, duration: float, cold: tuple[float, float] | None = None, trim: float = 0.0) -> Plan | None:
    """Add the Story Director's edits to the tightening plan (or to none): an ending trim (a last cut of the dead outro) and a cold
    open (a stretch of the clip that plays first). Times are clip-relative. Returns `plan` unchanged when there is nothing to add."""
    base = plan or Plan(duration, [(0.0, duration)], [])
    keeps, cuts = list(base.keeps), list(base.cuts)
    edge = duration - trim
    if trim >= 0.3 and edge >= MIN_KEEP_S and all(c["end"] <= edge + 0.01 for c in cuts):
        clipped = [(a, min(b, edge)) for a, b in keeps if a < edge - 0.05]
        if clipped:
            keeps = clipped
            cuts.append({"start": round(edge, 3), "end": round(duration, 3), "kind": "outro"})
    if cold:
        keeps = [cold] + keeps
    if keeps == base.keeps and not cold:
        return plan
    return Plan(duration, keeps, cuts, base.dropped, prefix=cold)


def _grid(t: float, fps: float) -> float:
    return round(t * fps) / fps


def plan_tighten(words: list[dict], clip_start: float, clip_end: float, language: str = "en", env=None, fps: float = 30.0,
                 pauses: bool = True, fillers: bool = True, min_s: float = 0.0) -> Plan:
    """Cuts that make a clip tighter without touching speech. `words` are source-time words (`start`, `end`, `text`); `env` is
    the clip's `envelope` (cut edges slide to the quietest instant, and a cut whose edge is still loud speech is dropped)."""
    duration = clip_end - clip_start
    inside = [(i, w) for i, w in enumerate(words) if w["end"] > clip_start and w["start"] < clip_end]
    rel = [{**w, "start": max(w["start"] - clip_start, 0.0), "end": min(w["end"] - clip_start, duration)} for _, w in inside]
    index = [i for i, _ in inside]
    voiced = None
    if env is not None:
        import numpy as np
        loud = env[env > np.percentile(env, 40)]
        voiced = float(np.percentile(loud, 50)) if len(loud) else None
    raw: list[tuple[float, float, str, int | None]] = []
    if pauses:
        for k in range(len(rel) - 1):
            a, b = rel[k]["end"], rel[k + 1]["start"]
            if b - a > PAUSE_MIN_S:
                pad = SENTENCE_PAD_S if rel[k]["text"].rstrip()[-1:] in SENTENCE_END else PAD_S
                if b - a - 2 * pad >= 0.1:
                    raw.append((a + pad, b - pad, "pause", None))
        if env is not None:    # silence that Whisper folded into a word's own span
            import numpy as np
            floor, top = float(np.percentile(env, 5)), float(np.percentile(env, 90))
            if top - floor > 25:
                quiet, k = env < top - 30, 0
                while k < len(quiet):
                    if quiet[k]:
                        j = k
                        while j < len(quiet) and quiet[j]:
                            j += 1
                        if (j - k) / 100 > PAUSE_MIN_S and not any(a <= k / 100 + 0.05 and j / 100 - 0.05 <= b for a, b, *_ in raw):
                            raw.append((k / 100 + PAD_S, j / 100 - PAD_S, "pause", None))
                        k = j
                    else:
                        k += 1
    if fillers:
        for k in range(len(rel)):
            if is_filler(rel, k, language) and rel[k]["end"] - rel[k]["start"] < 1.2:
                raw.append((rel[k]["start"] - 0.01, rel[k]["end"] + 0.01, "filler", index[k]))
    cuts: list[dict] = []
    for a, b, kind, w in sorted(raw):
        lo = cuts[-1]["end"] if cuts else 0.0
        a, b = _quietest(env, a, lo, b), _quietest(env, b, a, duration)
        a, b = _grid(max(a, lo), fps), _grid(min(b, duration), fps)
        if b - a < 2 / fps or a < MIN_KEEP_S or b > duration - MIN_KEEP_S:
            continue                                   # the clip's first and last instants are never touched
        if voiced is not None and kind == "pause" and (_db_at(env, a) > voiced - 3 or _db_at(env, b) > voiced - 3):
            continue                                   # an edge in loud speech: the word times were off, so skip
        if cuts and a - cuts[-1]["end"] < MIN_KEEP_S:
            cuts[-1]["end"] = max(cuts[-1]["end"], b)
            cuts[-1]["words"] = cuts[-1].get("words", []) + ([w] if w is not None else [])
            if kind == "filler":
                cuts[-1]["kind"] = "filler"
            continue
        cuts.append({"start": a, "end": b, "kind": kind, "words": [w] if w is not None else []})
    budget = min(MAX_REMOVED_SHARE * duration, max(duration - min_s - 0.2, 0.0))     # a clip never drops under the campaign's minimum length
    if sum(c["end"] - c["start"] for c in cuts) > budget:    # keep the longest cuts that fit
        kept, used = [], 0.0
        for c in sorted(cuts, key=lambda c: c["start"] - c["end"]):
            if used + c["end"] - c["start"] <= budget:
                kept.append(c)
                used += c["end"] - c["start"]
        cuts = sorted(kept, key=lambda c: c["start"])
    dropped = {w for c in cuts for w in c.pop("words", [])}
    keeps, at = [], 0.0
    for c in cuts:
        keeps.append((at, c["start"]))
        at = c["end"]
    keeps.append((at, duration))
    return Plan(duration=duration, keeps=keeps, cuts=[{"start": round(c["start"], 3), "end": round(c["end"], 3), "kind": c["kind"]} for c in cuts],
                dropped=dropped)


# ------------------------------------------------------------------ stress moments for the punch-in zooms

ZOOM_DEPTH_MIN, ZOOM_DEPTH_MAX = 1.03, 1.06   # subtle: a push you feel, not one you notice
STRESS_GAP_S = 12.0   # at least 12 s between pushes: a zoom every few seconds reads as the picture breathing
STRESS_MAX = 2


def stress_moments(words: list[dict], clip_start: float, clip_end: float, env, plan: Plan | None = None) -> list[tuple[float, float]]:
    """(time, zoom factor) pairs: the words said with the most stress, at least STRESS_GAP_S apart, none in the first or last
    second. Stress is the word's peak loudness over the clip's speech level, plus a bonus for the start of a sentence, a question
    or an exclamation. `time` is when the zoom starts to ease in (a little before the word, so it peaks on it), in clip time."""
    import numpy as np

    if env is None:
        return []
    duration = clip_end - clip_start
    speech = env[env > np.percentile(env, 40)]
    if len(speech) < 50:
        return []
    mean, spread = float(speech.mean()), max(float(speech.std()), 1.0)
    scored = []
    for k, w in enumerate(words):
        if w["end"] <= clip_start or w["start"] >= clip_end or (plan is not None and k in plan.dropped):
            continue
        a, b = w["start"] - clip_start, w["end"] - clip_start
        seg = env[int(a * 100):max(int(b * 100), int(a * 100) + 1)]
        if len(seg) == 0:
            continue
        z = (float(seg.max()) - mean) / spread
        prev = words[k - 1]["text"].rstrip()[-1:] if k else "."
        if prev in SENTENCE_END:
            z += 0.4
        if w["text"].rstrip()[-1:] in "?!؟":
            z += 0.3
        scored.append((z, a))
    picked: list[tuple[float, float]] = []
    for z, a in sorted(scored, reverse=True):
        if z < 1.0 or len(picked) >= STRESS_MAX:
            break
        t0 = a - 0.30
        if 1.0 <= a <= duration - 1.5 and all(abs(t0 - p) >= STRESS_GAP_S for p, _ in picked):
            depth = ZOOM_DEPTH_MIN + (ZOOM_DEPTH_MAX - ZOOM_DEPTH_MIN) * min(max((z - 1.0) / 1.5, 0.0), 1.0)
            picked.append((round(t0, 2), round(depth, 3)))
    return sorted(picked)
