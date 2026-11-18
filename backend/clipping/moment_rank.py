"""The moment engine's judgement, as pure functions: the episode (words, sentences, signals), the features of a window,
the hard gates, the soft score with its corroboration rules, Bradley-Terry ranking, selection, and the cut variants.
Nothing here touches the database or an LLM, so every rule can be tested alone.

Research defaults (plan, "Research rules"): R1 arousal beats calm, anger is not boosted for brand profiles; R2 purely
informational moments score lower unless the brief or profile wants information; R3 the tight cut is the default;
R4 hook wording must be concrete "just right" and paid off; R5 cuts end after laughter."""
from __future__ import annotations

import math
import re
from bisect import bisect_left, bisect_right

from . import moments_pilot, signals, transcripts

MAX_OVERLAP = 0.3             # two moments sharing more than this of the shorter one are the same moment
TOPIC_JACCARD = 0.6           # ... or the same topic: this share of their content words in common
THRESHOLD_FLOOR = 0.35        # the soft-score cut-off never goes below this, and never above the episode's percentile
THRESHOLD_PERCENTILE = 55
SILENT_LUFS = -60.0
MIN_SPEECH_WORDS = 6
BREATH_BEFORE, BREATH_AFTER = 0.05, 0.15     # the same breathing room pipeline.snap leaves around a sentence
LAUGH_TAIL_S = 4.0
LLM_SHARE = 0.3               # how much of the final soft score comes from the LLM's overall rating when it answered
AROUSAL = {"awe": 1.0, "funny": 1.0, "hype": 1.0, "surprise": 1.0, "moved": 1.0, "anger": 0.7, "sad": 0.45,
           "calm": 0.3, "informational": 0.3}
EMOTIONS = tuple(AROUSAL)


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(t for t in (transcripts.normalise(w, fold_arabic=True) for w in text.split()) if t)


def _set(*texts: str) -> frozenset:
    return frozenset(t for text in texts for t in _tokens(text))


def _phrases(*texts: str) -> list[tuple[str, ...]]:
    return [_tokens(t) for t in texts]


WEAK_OPENERS = _set("and but so because then also which that this these those he she it they them him her its their",
                    "و لكن ثم يعني هذا هذه هو هي هم")
CONTEXT_PHRASES = _phrases("as i said", "like i said", "as we said", "as we discussed", "as mentioned", "as i mentioned",
                           "going back to", "back to what", "to continue", "as i was saying", "like we talked",
                           "كما قلت", "زي ما قلت", "كما ذكرت", "كما ذكرنا", "زي ما قلنا", "زى ما قلت")
CLAIMS = _set("never nobody everyone secret truth wrong mistake biggest worst best why how lost won only stop real actually crazy insane",
              "ابدا سر الحقيقه خطا اكبر اسوا لماذا ليه كيف ازاي خسرت مستحيل")
CONCLUSION = _set("so thats why lesson remember finally reason means truth", "عشان علشان لذلك النهايه الخلاصه")
VAGUE = _set("something things stuff thing some this that it", "اشياء حاجه حاجة شيء")
INFO_MARKERS = _set("how because reason tip step learn mean example rule lesson", "كيف ازاي لان سبب نصيحه خطوه")
NUMBER_WORDS = _set("one two three four five six seven eight nine ten hundred thousand million", "واحد اثنين ثلاثه اربعه خمسه الف مليون")
PRONOUNS = _set("it this that they he she them", "هذا هذه هو هي")
HYPE_WORDS = _set("wow insane unbelievable crazy huge amazing incredible", "والله مستحيل معقول")
WANTS_INFO = re.compile(r"\b(educational|inform\w*|explain\w*|teach\w*|tips?|how[- ]to|tutorial)\b", re.I)


def clip01(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    at = (len(ordered) - 1) * pct / 100
    low = math.floor(at)
    return ordered[low] + (ordered[min(low + 1, len(ordered) - 1)] - ordered[low]) * (at - low)


def overlap_share(a: tuple[float, float], b: tuple[float, float]) -> float:
    shorter = min(a[1] - a[0], b[1] - b[0])
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0])) / shorter if shorter > 0 else 1.0


# ------------------------------------------------------------------ the episode

class Episode:
    """Words, sentences and signals of one source, with the lookups the rules need."""

    def __init__(self, words: list[dict], duration: float, language: str = "en", signals_: dict | None = None):
        self.words, self.duration, self.language = words, float(duration), language
        self.norm = [transcripts.normalise(w["text"], fold_arabic=True) for w in words]
        self.starts = [w["start"] for w in words]
        self.lines = moments_pilot.sentences(words)
        self.line_starts = [line["start"] for line in self.lines]
        self.line_ends = [line["end"] for line in self.lines]
        self.series = (signals_ or {}).get("series") or None
        self.step = float((signals_ or {}).get("step_s") or 1.0)
        self._topics = (signals_ or {}).get("topics") or None
        self.stats = self._stats()

    @property
    def topics(self) -> list[dict]:
        """Topic segments: the stored ones, else TextTiling on the words (computed once, on first use)."""
        if self._topics is None:
            self._topics = signals.topic_segments(self.words, self.duration) if self.words else []
        return self._topics

    def _stats(self) -> dict:
        stats: dict = {}
        loud = [v for v in (self.series or {}).get("loudness") or [] if v > SILENT_LUFS]
        if loud:
            stats["loudness"] = (percentile(loud, 50), percentile(loud, 95))
        chat = [v for v in (self.series or {}).get("chat") or [] if v > 0]
        if chat:
            stats["chat"] = percentile(chat, 95) or 1.0
        return stats

    # --- lookups
    def has(self, name: str) -> bool:
        return bool(self.series and self.series.get(name))

    def values(self, name: str, start: float, end: float) -> list[float]:
        if not self.has(name):
            return []
        data = self.series[name]
        return list(data[max(int(start / self.step), 0):max(math.ceil(end / self.step), int(start / self.step) + 1)])

    def words_in(self, start: float, end: float) -> range:
        """Indexes of the words inside the window."""
        low = bisect_left(self.starts, start - 0.1)
        high = bisect_right(self.starts, end - 0.05)
        return range(low, max(low, high))

    def lines_in(self, start: float, end: float) -> list[dict]:
        low = bisect_left(self.line_starts, start - 0.1)
        return [line for line in self.lines[low:] if line["end"] <= end + 0.2 and line["start"] < end]

    def text(self, start: float, end: float) -> str:
        return " ".join(self.words[i]["text"] for i in self.words_in(start, end))

    def loud_norm(self, value: float) -> float:
        median, top = self.stats.get("loudness", (-30.0, -15.0))
        return clip01((value - median) / max(top - median, 1.0))

    # --- sentence edges
    def starts_inside_sentence(self, start: float) -> bool:
        at = bisect_right(self.line_starts, start + 0.35) - 1
        return at >= 0 and self.line_starts[at] + 0.35 < start and start < self.line_ends[at] - 0.35

    def next_word_start(self, time: float) -> float | None:
        i = bisect_left(self.starts, time - 0.05)
        return self.starts[i] if i < len(self.starts) else None


# ------------------------------------------------------------------ the brief: avoid list, must-include, length rules

_QUOTED = re.compile(r"[\"“«]([^\"”»]{2,40})[\"”»]")
_AVOID = re.compile(r"\b(?:do not|don't|dont|never|avoid|no|without)\s+(?:(?:mention|say|use|show|include|discuss|promote|reference|swear at)(?:ing)?\s+|talk(?:ing)? about\s+)?(.{2,80})", re.I)
_SKIP_RULE = re.compile(r"[#@]|music|audio|sound|seconds?\b|disclos|\bad\b|paid partnership|caption|post\b", re.I)
_GENERIC = frozenset({"any", "the", "anything", "else", "content", "videos", "clips", "video", "clip", "other", "a", "an", "of", "about"})
_RANGE = re.compile(r"(\d+)\s*(?:-|–|to|and)\s*(\d+)\s*(?:s\b|sec)", re.I)
_MIN = re.compile(r"(?:at least|minimum(?: of)?|min(?:imum)?)\s*(\d+)\s*(?:s\b|sec)", re.I)
_MAX = re.compile(r"(?:at most|maximum(?: of)?|max(?:imum)?|under|up to|no longer than|less than|within)\s*(\d+)\s*(?:s\b|sec)", re.I)


def _phrase_terms(text: str) -> list[str]:
    out = []
    for part in re.split(r",|;|\bor\b|\band\b", text.rstrip(". ")):
        words = [w for w in part.split() if w.lower() not in _GENERIC]
        if words and len(" ".join(words)) >= 3:
            out.append(" ".join(words[:4]))
    return out


def brief_terms(rules: dict | None, key_terms: list[str] | None = None) -> dict:
    """What the brief asks of a moment: words to avoid, words that earn a boost, a length rule, and whether information
    is welcome. Only the structured lists and the plain "do not mention X" / "must say X" rules are read; the brief text
    itself is never sent anywhere."""
    rules = rules if isinstance(rules, dict) else {}
    avoid = [str(t) for t in rules.get("avoid_terms") or [] if isinstance(t, str)]
    include = [str(t) for t in rules.get("must_include_terms") or [] if isinstance(t, str)] + [str(t) for t in key_terms or []]
    low, high, info = None, None, False
    for rule in rules.get("content_rules") or []:
        if not isinstance(rule, dict):
            continue
        text = rule.get("text") or ""
        info = info or bool(WANTS_INFO.search(text))
        if re.search(r"seconds?|\bsec\b|\d\s*s\b", text, re.I):
            for a, b in _RANGE.findall(text):
                low, high = max(low or 0, float(a)), min(high or 1e9, float(b))
            for a in _MIN.findall(text):
                low = max(low or 0, float(a))
            for a in _MAX.findall(text):
                high = min(high or 1e9, float(a))
        if _SKIP_RULE.search(text):
            continue
        quoted = _QUOTED.findall(text)
        if rule.get("requirement") == "forbidden":
            avoid += quoted or [t for m in _AVOID.findall(text) for t in _phrase_terms(m)]
        elif rule.get("requirement") == "required":
            include += quoted
    avoid = list(dict.fromkeys(a.strip() for a in avoid if a.strip()))[:30]
    include = list(dict.fromkeys(i.strip() for i in include if i and len(i.strip()) >= 3))[:30]
    return {"avoid": avoid, "include": include, "length": (low, high) if low or high else None, "wants_info": info}


def term_hits(ep: Episode, start: float, end: float, terms: list[str]) -> list[str]:
    """The terms (one or more words, in order) that are spoken inside the window."""
    tokens = [ep.norm[i] for i in ep.words_in(start, end)]
    hits = []
    for term in terms:
        want = _tokens(term)
        if want and any(tuple(tokens[i:i + len(want)]) == want for i in range(len(tokens) - len(want) + 1)):
            hits.append(term)
    return hits


# ------------------------------------------------------------------ features of a window

def concreteness(text: str, tokens: tuple[str, ...]) -> float:
    """0 (vague teaser) .. 1 (full detail). R4: a hook rated about 0.55 is "just right"."""
    value = 0.25
    if any(ch.isdigit() for ch in text) or NUMBER_WORDS & set(tokens):
        value += 0.25
    value += min(0.2, 0.1 * sum(1 for w in text.split()[1:] if w[:1].isupper()))
    value += 0.1 if len(tokens) >= 6 else 0.0
    value -= min(0.3, 1.5 * sum(t in VAGUE for t in tokens) / max(len(tokens), 1))
    return clip01(value)


def just_right(concrete: float) -> float:
    return clip01(1 - abs(concrete - 0.55) / 0.55)


def hook_rating(text: str) -> float:
    """How good a hook line is on its own: concreteness "just right", short, no shouting."""
    tokens = _tokens(text)
    if not tokens or len(text) > 140:
        return 0.0
    return round(clip01(0.2 + 0.5 * just_right(concreteness(text, tokens)) + (0.2 if 3 <= len(tokens) <= 12 else 0.0)
                        + (0.1 if text.rstrip().endswith(("?", "؟")) else 0.0)), 3)


def paid_off(hook: str, window_text: str) -> bool:
    """R4: the hook's own content words come back later in the clip (so it is not a teaser the clip never answers)."""
    wanted = {t for t in _tokens(hook) if len(t) > 3 and t not in signals.STOPWORDS}
    if not wanted:
        return True
    later = window_text[len(window_text) // 3:]
    return len(wanted & set(_tokens(later))) >= max(1, len(wanted) // 4)


def _hook(ep: Episode, line: dict, start: float) -> tuple[float, list[str]]:
    text, tokens = line["text"], tuple(ep.norm[line["first"]:line["last"] + 1])
    value, tags = 0.15, []
    if text.rstrip().endswith(("?", "؟")):
        value, tags = value + 0.30, tags + ["opens with a question"]
    if any(ch.isdigit() for ch in text) or NUMBER_WORDS & set(tokens):
        value, tags = value + 0.12, tags + ["opens with a number"]
    if 4 <= len(tokens) <= 14:
        value += 0.13
    if CLAIMS & set(tokens):
        value, tags = value + 0.15, tags + ["opens with a strong claim"]
    value += 0.15 * just_right(concreteness(text, tokens))
    loud = ep.values("loudness", start, start + 3)
    if loud and sum(loud) / len(loud) >= ep.stats.get("loudness", (0, 0))[0] > SILENT_LUFS:
        value, tags = value + 0.10, tags + ["loud opening"]
    return clip01(value), tags


def _standalone(ep: Episode, line: dict, last: dict) -> tuple[float, list[str]]:
    tokens = tuple(ep.norm[line["first"]:line["last"] + 1])
    value, tags = 1.0, []
    if tokens and tokens[0] in WEAK_OPENERS:
        value, tags = value - 0.30, tags + ["opens leaning on earlier talk"]
    if any(tokens[:len(p)] == p for p in CONTEXT_PHRASES):
        value, tags = value - 0.60, tags + ["refers back to earlier context"]
    if tokens and sum(t in PRONOUNS for t in tokens[:8]) / min(len(tokens), 8) >= 0.25:
        value -= 0.15
    if last["text"].rstrip()[-1:] not in ".?!؟":
        value -= 0.20
    return clip01(value), tags or ["makes sense on its own"]


def _payoff(ep: Episode, lines: list[dict], start: float, end: float) -> tuple[float, list[str]]:
    last, tail = lines[-1], tuple(ep.norm[lines[-1]["first"]:lines[-1]["last"] + 1])
    value, tags = 0.2, []
    if last["text"].rstrip()[-1:] in ".?!؟" and len(tail) >= 4:
        value, tags = value + 0.25, tags + ["ends on a finished thought"]
    peak = max([*ep.values("laughter", end - (end - start) * 0.3, end + 1), *(ep.loud_norm(v) for v in ep.values("loudness", end - (end - start) * 0.3, end))] or [0])
    if peak > 0.5:
        value, tags = value + 0.25 * peak, tags + ["a peak near the end"]
    after = ep.next_word_start(end)
    if after is None or after - end >= 0.4:
        value += 0.15
    if CONCLUSION & set(tail):
        value, tags = value + 0.15, tags + ["lands on a conclusion"]
    if len(lines) >= 3 and lines[0]["text"].rstrip().endswith(("?", "؟")):
        value, tags = value + 0.10, tags + ["the opening question is answered"]
    return clip01(value), tags


def _arousal_proxy(ep: Episode, start: float, end: float, words: range) -> tuple[float, list[str]]:
    """Emotion without the LLM: loudness peaks, reaction words, laughter, exclamation marks."""
    tags, parts = [], []
    if ep.has("loudness"):
        peak = max((ep.loud_norm(v) for v in ep.values("loudness", start, end)), default=0.0)
        parts.append((0.45, peak))
        if peak > 0.7:
            tags.append("loud stretch")
    reacted = sum(ep.norm[i] in HYPE_WORDS for i in words) + (sum(ep.values("reaction", start, end)) if ep.has("reaction") else 0)
    parts.append((0.3, clip01(reacted / 2)))
    if reacted:
        tags.append("reaction words")
    if ep.has("laughter"):
        parts.append((0.25, max(ep.values("laughter", start, end + LAUGH_TAIL_S), default=0.0)))
    marks = sum(ep.words[i]["text"].endswith("!") for i in words)
    parts.append((0.15, clip01(marks / 2)))
    total = sum(w for w, _ in parts)
    return clip01(0.1 + 0.9 * sum(w * v for w, v in parts) / total), tags


def _value(ep: Episode, words: range, lines: list[dict]) -> tuple[float, list[str]]:
    tokens = [ep.norm[i] for i in words]
    numbers = sum(any(ch.isdigit() for ch in ep.words[i]["text"]) or ep.norm[i] in NUMBER_WORDS for i in words)
    value = 0.15 + 0.12 * min(numbers, 3) + (0.15 if INFO_MARKERS & set(tokens) else 0.0) + (0.1 if len(lines) >= 3 else 0.0)
    return clip01(value), (["facts and numbers"] if numbers else [])


def _peaks(ep: Episode, start: float, end: float) -> tuple[float | None, list[str]]:
    parts, tags = [], []
    if ep.has("loudness"):
        parts.append(max((ep.loud_norm(v) for v in ep.values("loudness", start, end)), default=0.0))
    if ep.has("chat"):
        top = max(ep.values("chat", start, end), default=0.0)
        parts.append(clip01(top / ep.stats.get("chat", 1.0)))
        tags += ["a chat burst"] if parts[-1] > 0.7 else []
    if ep.has("replay"):
        values = ep.values("replay", start, end)
        parts.append(sum(values) / len(values) if values else 0.0)
        tags += ["a most-replayed stretch"] if parts[-1] > 0.7 else []
    if ep.has("reaction"):
        parts.append(clip01(sum(ep.values("reaction", start, end)) / 2))
    if not parts:
        return None, []
    return clip01(0.7 * max(parts) + 0.3 * sum(parts) / len(parts)), tags


def window_features(ep: Episode, start: float, end: float, lo: float, hi: float) -> tuple[dict, dict]:
    """Subscores (0..1) of one window and the evidence behind each. A feature that needs a signal the source does not
    have is simply absent: the weights renormalise over the features that exist."""
    words, lines = ep.words_in(start, end), ep.lines_in(start, end)
    features: dict[str, float] = {}
    evidence: dict[str, list[str]] = {}
    if lines:
        features["hook"], evidence["hook"] = _hook(ep, lines[0], start)
        features["standalone"], evidence["standalone"] = _standalone(ep, lines[0], lines[-1])
        features["payoff"], evidence["payoff"] = _payoff(ep, lines, start, end)
        features["value"], evidence["value"] = _value(ep, words, lines)
    features["emotion"], evidence["emotion"] = _arousal_proxy(ep, start, end, words)
    if ep.has("scene") or ep.has("face"):
        scenes = ep.values("scene", start, end)
        cuts = sum(v > 0.3 for v in scenes) / len(scenes) if scenes else 0.0
        faces = ep.values("face", start, end)
        features["energy"] = clip01(0.6 * min(1.0, cuts * 4) + 0.4 * (sum(faces) / len(faces) if faces else 0.0))
    peaks, evidence["peaks"] = _peaks(ep, start, end)
    if peaks is not None:
        features["peaks"] = peaks
    if ep.has("laughter"):
        features["laughter"] = max(ep.values("laughter", start, end + LAUGH_TAIL_S), default=0.0)
        evidence["laughter"] = ["laughter in the clip"] if features["laughter"] > 0.5 else []
    mid, span = lo + 0.55 * (hi - lo), (hi - lo) / 2
    features["length_fit"] = clip01(1 - abs((end - start) - mid) / max(span + 5, 1))
    return {k: round(v, 4) for k, v in features.items()}, evidence


def soft_score(features: dict, weights: dict) -> float:
    """The weighted mean of the features that exist (weights renormalised over them)."""
    total = sum(weights.get(f, 0.0) for f in features)
    return clip01(sum(weights.get(f, 0.0) * v for f, v in features.items()) / total) if total > 0 else 0.0


def reasons_for(features: dict, evidence: dict, weights: dict, limit: int = 3) -> list[str]:
    """The strongest evidence behind a score, in plain words."""
    ranked = sorted(features, key=lambda f: -weights.get(f, 0.0) * features[f])
    out = []
    for name in ranked:
        for tag in evidence.get(name, [])[:1]:
            out.append(tag[0].upper() + tag[1:])
        if len(out) >= limit:
            break
    return out or ["Picked on its overall signals"]


# ------------------------------------------------------------------ the LLM's view, with corroboration (cross-modal)

def corroboration(ep: Episode, emotion: str, start: float, end: float) -> tuple[float, str | None]:
    """How far to trust the LLM's emotion label against what was measured. "Funny" needs laughter in the audio, "hype"
    needs loudness, a chat burst or reaction words. Without the signals the claim cannot be checked: a mild discount."""
    if emotion == "funny":
        seen = max(ep.values("laughter", start, end + LAUGH_TAIL_S), default=None) if ep.has("laughter") else None
        label = "funny, but no laughter was heard"
    elif emotion == "hype":
        parts = [max((ep.loud_norm(v) for v in ep.values("loudness", start, end)), default=0.0)] if ep.has("loudness") else []
        parts += [clip01(max(ep.values("chat", start, end), default=0.0) / ep.stats.get("chat", 1.0))] if ep.has("chat") else []
        parts += [clip01(sum(ep.values("reaction", start, end)) / 2)] if ep.has("reaction") else []
        seen, label = (max(parts) if parts else None), "hype, but no loud peak, chat burst or reaction was measured"
    else:
        return 1.0, None
    if seen is None:
        return 0.85, None
    return (1.0, None) if seen >= 0.3 else (0.5, f"The AI called it {label}")


def apply_llm(ep: Episode, features: dict, evidence: dict, rating: dict, prof, start: float, end: float) -> tuple[dict, float, list[str]]:
    """Blend the LLM's subscores into the measured ones. Returns the new features, the LLM's overall rating after the
    corroboration discount, and notes for the reasons list."""
    notes: list[str] = []
    merged = dict(features)
    for name in ("hook", "standalone", "payoff", "value"):
        if name in merged and isinstance(rating.get(name), (int, float)):
            merged[name] = round(0.5 * merged[name] + 0.5 * clip01(rating[name]), 4)
    emotion = rating.get("emotion") if rating.get("emotion") in AROUSAL else "calm"
    trust, note = corroboration(ep, emotion, start, end)
    if note:
        notes.append(note)
    arousal = AROUSAL[emotion]
    if emotion == "anger" and prof.name == "promo":
        arousal = 0.2                              # R1: outrage is not boosted for brand campaigns
    strength = clip01(float(rating.get("emotion_strength", 0.5))) * trust
    merged["emotion"] = round(0.5 * merged.get("emotion", 0.3) + 0.5 * clip01(arousal * strength + 0.3 * (1 - strength)), 4)
    overall = clip01(float(rating.get("overall", 0.5)))
    overall *= 1.0 if trust >= 0.85 else trust + 0.25   # the discount reaches the overall rating too, more gently
    if emotion in ("awe", "funny", "hype", "surprise", "moved"):
        evidence.setdefault("emotion", []).append(f"{emotion} moment, as the AI sees it")
    return merged, round(overall, 4), notes


def adjust(score: float, features: dict, include_hits: list[str], prof, wants_info: bool) -> float:
    """After weighting: a must-include hit raises the score; a purely informational moment is lowered (R2)."""
    score += min(0.08 * len(include_hits), 0.16)
    if features.get("value", 0) >= 0.55 and features.get("emotion", 1) <= 0.3 and not (prof.info_ok or wants_info):
        score *= 0.85
    return round(clip01(score), 4)


# ------------------------------------------------------------------ layer 1: hard gates

def hard_gate(ep: Episode, start: float, end: float, avoid_hits: list[str], campaign: tuple[float, float],
              published: list[tuple[float, float]], prof) -> str | None:
    """A reason this moment cannot be used at all, or None. Only deal-breakers live here."""
    if avoid_hits:
        return f"It mentions \"{avoid_hits[0]}\", which the brief says to avoid."
    length = end - start
    if not campaign[0] - 0.01 <= length <= campaign[1] + 0.01:
        return f"It is {length:.0f} s long; the campaign allows {campaign[0]:.0f} to {campaign[1]:.0f} s."
    lines = ep.lines_in(start, end)
    if ep.starts_inside_sentence(start):
        return "It starts in the middle of a sentence."
    if lines and any(tuple(ep.norm[lines[0]["first"]:lines[0]["last"] + 1][:len(p)]) == p for p in CONTEXT_PHRASES):
        return "It starts by pointing back to something said earlier, so it cannot stand alone."
    if any(overlap_share((start, end), p) >= 0.5 for p in published):
        return "A clip of this moment was already published."
    loud = ep.values("loudness", start, end)
    if loud and (sum(loud) / len(loud) <= SILENT_LUFS or sum(v > SILENT_LUFS for v in loud) / len(loud) < 0.3):
        return "The audio is silent or broken here."
    if prof.needs_speech and len(ep.words_in(start, end)) < MIN_SPEECH_WORDS:
        return "There is almost no speech in it."
    return None


# ------------------------------------------------------------------ duplicates

def _topic_set(ep: Episode, start: float, end: float) -> frozenset:
    return frozenset(ep.norm[i] for i in ep.words_in(start, end) if len(ep.norm[i]) > 3 and ep.norm[i] not in signals.STOPWORDS)


def dedupe(ep: Episode, items: list[dict]) -> tuple[list[dict], int]:
    """Best first, drop a moment that overlaps a better one or covers the same topic words. Returns (kept, dropped)."""
    kept: list[dict] = []
    topics: list[frozenset] = []
    for item in sorted(items, key=lambda c: (-c["score"], c["start_s"])):
        span, words = (item["start_s"], item["end_s"]), _topic_set(ep, item["start_s"], item["end_s"])
        same = any(overlap_share(span, (k["start_s"], k["end_s"])) > MAX_OVERLAP
                   or (len(words) >= 6 and len(t) >= 6 and len(words & t) / len(words | t) >= TOPIC_JACCARD)
                   for k, t in zip(kept, topics))
        if not same:
            kept.append(item)
            topics.append(words)
    return kept, len(items) - len(kept)


# ------------------------------------------------------------------ layer 3: pairwise ranking

def pair_plan(ids: list[str], max_pairs: int) -> list[tuple[str, str]]:
    """Which pairs to compare. All of them when they fit; otherwise the closest ranks first (neighbours, then one apart...)."""
    pairs = [(ids[i], ids[j]) for gap in range(1, len(ids)) for i in range(len(ids) - gap) for j in (i + gap,)]
    return pairs[:max_pairs]


def bradley_terry(ids: list[str], wins: list[tuple[str, str]], iterations: int = 200) -> dict[str, float]:
    """Strengths from (winner, loser) results. Each pair that was compared gets half a win each way, so a clean sweep
    stays finite and contradictory answers (A beats B once, B beats A once) come out equal. Strengths average 1."""
    count = {i: 0.0 for i in ids}
    games: dict[tuple[str, str], float] = {}
    for winner, loser in wins:
        count[winner] += 1.0
        games[tuple(sorted((winner, loser)))] = games.get(tuple(sorted((winner, loser))), 0.0) + 1.0
    for pair in games:
        for who in pair:
            count[who] += 0.5
        games[pair] += 1.0
    strength = {i: 1.0 for i in ids}
    for _ in range(iterations):
        new = {}
        for i in ids:
            denominator = sum(n / (strength[i] + strength[j if i == a else a]) for (a, j), n in games.items() if i in (a, j))
            new[i] = count[i] / denominator if denominator else strength[i]
        mean = sum(new.values()) / len(new)
        strength = {i: v / mean for i, v in new.items()}
    return strength


# ------------------------------------------------------------------ selection

def pack(items: list[dict], count: int) -> list[dict]:
    """The best-scoring set of up to `count` windows that do not overlap at all (weighted interval scheduling)."""
    ordered = sorted(items, key=lambda c: c["end_s"])
    n, ends = len(ordered), [c["end_s"] for c in ordered]
    before = [bisect_right(ends, c["start_s"]) for c in ordered]
    none = float("-inf")
    best = [[0.0] * (n + 1)] + [[none] * (n + 1) for _ in range(count)]
    for k in range(1, count + 1):
        for i in range(1, n + 1):
            best[k][i] = max(best[k][i - 1], best[k - 1][before[i - 1]] + ordered[i - 1]["score"])
    k = max((j for j in range(count + 1) if best[j][n] > none), default=0)
    chosen, i = [], n
    while k > 0 and i > 0:
        if best[k][i] == best[k][i - 1]:
            i -= 1
        else:
            chosen.append(ordered[i - 1])
            i, k = before[i - 1], k - 1
    return chosen


def select(ranked: list[dict], prof, target: int) -> tuple[list[dict], dict[str, str], float]:
    """Keep the best moments: those at or above the episode's cut-off, up to the target, then top up to the profile's
    minimum. Returns (kept, why-not for every other moment keyed by id, the cut-off)."""
    scores = [c["score"] for c in ranked]
    cut = max(THRESHOLD_FLOOR, percentile(scores, THRESHOLD_PERCENTILE)) if scores else THRESHOLD_FLOOR
    kept: list[dict] = []

    def fits(c):
        return all(overlap_share((c["start_s"], c["end_s"]), (k["start_s"], k["end_s"])) <= MAX_OVERLAP for k in kept)

    for c in ranked:
        if len(kept) < target and c["score"] >= cut and fits(c):
            kept.append(c)
    topped = False
    for c in ranked:
        if len(kept) >= min(prof.min_clips, target) or c in kept:
            continue
        if fits(c):
            kept.append(c)
            topped = True
    if len(kept) < prof.min_clips:                      # greedy picks may block each other: pack the video instead
        packed = pack(ranked, prof.min_clips)
        if len(packed) > len(kept):
            kept, topped = packed, True
    kept.sort(key=lambda c: (-c["score"], c["start_s"]))
    why: dict[str, str] = {}
    for position, c in enumerate(ranked, start=1):
        if c in kept:
            if topped and c["score"] < cut:
                c.setdefault("notes", []).append(f"Kept to reach the minimum of {prof.min_clips} clips for this type")
        elif c["score"] < cut:
            why[c["id"]] = f"Scored {c['score']:.2f}, below this episode's cut-off of {cut:.2f}."
        else:
            why[c["id"]] = f"Ranked #{position}; this type keeps the top {target}."
    return kept, why, round(cut, 4)


# ------------------------------------------------------------------ cut variants

def laugh_tail(ep: Episode, end: float, cap: float) -> float:
    """R5: end after the laugh, not on the punchline."""
    if not ep.has("laughter"):
        return end
    data, second, tail = ep.series["laughter"], int(end / ep.step), end
    if second + 1 < len(data) and data[second] < 0.4 <= data[second + 1]:
        second += 1                                  # the laugh starts in the next second, right after the punchline
    while second < len(data) and second - int(end / ep.step) < LAUGH_TAIL_S and data[second] >= 0.4:
        tail, second = (second + 1) * ep.step, second + 1
    if tail <= end:
        return end
    following = ep.next_word_start(end + 0.2)
    limit = min(cap, ep.duration, (following - 0.1) if following else cap)
    return round(min(max(tail + 0.3, end), max(limit, end)), 3)


def _span(ep: Episode, first: dict, last: dict, hi: float) -> dict:
    start = max(first["start"] - BREATH_BEFORE, 0.0)
    end = min(last["end"] + BREATH_AFTER, ep.duration)
    return {"start_s": round(start, 3), "end_s": round(min(laugh_tail(ep, end, start + hi), ep.duration), 3)}


def make_variants(ep: Episode, start: float, end: float, lo: float, hi: float) -> dict[str, dict]:
    """hook_first (starts on the strongest opening line), with_setup (a line or two of lead-in) and tight (the payoff with
    the filler trimmed, the default). All are sentence cuts inside the length limit `hi`."""
    lines = ep.lines_in(start, end)
    if not lines:
        window = {"start_s": round(start, 3), "end_s": round(end, 3)}
        return {"hook_first": dict(window), "with_setup": dict(window), "tight": dict(window)}
    floor = lo
    i, j = 0, len(lines) - 1
    while i < min(2, j) and (len(lines[i]["text"].split()) < 4 or ep.norm[lines[i]["first"]] in WEAK_OPENERS)             and lines[j]["end"] - lines[i + 1]["start"] >= floor:
        i += 1
    laughs_after = ep.has("laughter") and max(ep.values("laughter", lines[j]["end"], lines[j]["end"] + 2), default=0) >= 0.4
    if j > i and len(lines[j]["text"].split()) < 4 and lines[j]["end"] - lines[i]["start"] > floor and not laughs_after:
        j -= 1
    best = max(range(min(3, len(lines))), key=lambda k: (_hook(ep, lines[k], lines[k]["start"])[0], -k))
    hooked = lines[best] if lines[-1]["end"] - lines[best]["start"] >= lo else lines[0]
    before, first = bisect_left(ep.line_starts, lines[0]["start"] - 0.01), lines[0]
    for back in (1, 2):
        k = before - back
        if k >= 0 and first["start"] - ep.line_ends[k] <= 1.5 and lines[-1]["end"] - ep.lines[k]["start"] <= hi                 and lines[0]["start"] - ep.lines[k]["start"] <= 8:
            first = ep.lines[k]
    return {"hook_first": _span(ep, hooked, lines[-1], hi), "with_setup": _span(ep, first, lines[-1], hi),
            "tight": _span(ep, lines[i], lines[j], hi)}
