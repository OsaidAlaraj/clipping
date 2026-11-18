"""The moment engines. Each one only proposes windows {start_s, end_s, score, reasons, source[, hook]}; the filter in
`moments.find` decides. They register with the router, so a new engine is a new class here and a name in a profile's route.

  own     map -> pool: every chunk of the transcript is scanned for sentence-snapped windows scored by the shared features
  topic   one opener window and one best window per topic segment (TextTiling, from signals.topics)
  hype    loudness, laughter, reaction, chat and replay peaks, for streams, sports and music
  pilot   the interim LLM picker of phase 2P
  vendor  the active `moments` adapter (Vizard, Klap), when its keys exist (or a fake in demo mode)

Licence note: TextTiling is re-implemented from the paper's idea in signals.py; no third-party clipper code is copied."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal

from . import config, ffmpeg, moment_rank as rank, moments_pilot
from .alerts import registry
from .core import AppError, KillSwitchOn
from .router import register

PER_CHUNK = 8              # windows the own engine keeps from each chunk of the transcript
CHUNK_OVERLAP = 0.5        # inside the engine, windows may overlap this much (the final filter is stricter)
END_OPTIONS = 6            # window ends tried per start sentence
HYPE_SMOOTH_S = 5
HYPE_PEAKS = 12
HYPE_WEIGHTS = {"loud": 0.4, "laughter": 0.2, "reaction": 0.15, "chat": 0.15, "replay": 0.1}
VENDOR_POLLS = 120


@dataclass
class Source:
    """Everything an engine may look at: the episode, the profile's length range for this video, the brief."""
    conn: object
    source: dict
    campaign: dict
    version: dict
    ep: rank.Episode
    profile: object
    length: tuple[float, float]
    brief: dict
    owner_id: object
    cost: Decimal = Decimal("0")
    notes: list = field(default_factory=list)
    llm_state: str = "unused"       # unused -> ok | partial | unavailable, set by moments.find


def _window(ep: rank.Episode, first: dict, last: dict, hi: float) -> tuple[float, float]:
    span = rank._span(ep, first, last, hi)
    return span["start_s"], span["end_s"]


def scan(source: Source, lines: list[dict]) -> list[dict]:
    """The best window from each start sentence of `lines`: up to END_OPTIONS ends in the length range, scored by the shared
    features. Sentence edges only, so every window is a clean cut."""
    ep, (lo, hi) = source.ep, source.length
    weights = source.profile.weights
    found = []
    for i, first in enumerate(lines):
        opening = first["start"] - rank.BREATH_BEFORE
        ends = []
        for j in range(i, len(lines)):
            length = lines[j]["end"] + rank.BREATH_AFTER - opening
            if length > hi:
                break
            if length >= lo:
                ends.append(j)
        if not ends:
            continue
        step = max(len(ends) // END_OPTIONS, 1)
        best = None
        for j in ends[::step]:
            start, end = _window(ep, first, lines[j], hi)
            if end - start > hi or end - start < lo - 1e-6:
                continue
            features, evidence = rank.window_features(ep, start, end, lo, hi)
            score = rank.soft_score(features, weights)
            if best is None or score > best["score"]:
                best = {"start_s": start, "end_s": end, "score": round(score, 4), "features": features,
                        "reasons": rank.reasons_for(features, evidence, weights)}
        if best:
            found.append(best)
    return found


def top_windows(found: list[dict], count: int, overlap: float = CHUNK_OVERLAP) -> list[dict]:
    kept: list[dict] = []
    for item in sorted(found, key=lambda c: (-c["score"], c["start_s"])):
        if len(kept) >= count:
            break
        if all(rank.overlap_share((item["start_s"], item["end_s"]), (k["start_s"], k["end_s"])) <= overlap for k in kept):
            kept.append(item)
    return kept


@register
class OwnEngine:
    name = "own"

    def available(self, conn) -> bool:
        return True

    def propose(self, source: Source, profile) -> list[dict]:
        if not source.ep.lines:
            return []
        picks = []
        for chunk in moments_pilot.chunks(source.ep.lines):
            picks += top_windows(scan(source, chunk), PER_CHUNK)
        return [{**p, "source": self.name} for p in sorted(picks, key=lambda c: -c["score"])]


@register
class TopicEngine:
    name = "topic"

    def available(self, conn) -> bool:
        return True

    def propose(self, source: Source, profile) -> list[dict]:
        ep, out = source.ep, []
        for topic in ep.topics:
            lines = [line for line in ep.lines if topic["start"] - 0.1 <= line["start"] and line["end"] <= topic["end"] + 0.5]
            if not lines:
                continue
            opener = top_windows([w for w in scan(source, lines[:3])], 1)
            best = top_windows(scan(source, lines), 1)
            for pick, tag in [(p, "Opens a new topic") for p in opener] + [(p, "The strongest stretch of a topic") for p in best]:
                title = f": {topic['title']}" if topic.get("title") else ""
                out.append({**pick, "reasons": [f"{tag}{title}", *pick["reasons"]][:3], "source": self.name})
        return top_windows(out, len(out), overlap=0.3)


def hype_map(ep: rank.Episode) -> list[float]:
    """One 0..1 value per second: how much is happening, from the signals that exist. Smoothed over a few seconds."""
    parts: list[tuple[float, list[float]]] = []
    if ep.has("loudness"):
        parts.append((HYPE_WEIGHTS["loud"], [ep.loud_norm(v) for v in ep.series["loudness"]]))
    for name, key in (("laughter", "laughter"), ("reaction", "reaction"), ("chat", "chat"), ("replay", "replay")):
        if ep.has(name):
            top = ep.stats.get("chat", 1.0) if name == "chat" else (2.0 if name == "reaction" else 1.0)
            parts.append((HYPE_WEIGHTS[key], [rank.clip01((v or 0) / top) for v in ep.series[name]]))
    if not parts:
        return []
    size = min(len(values) for _, values in parts)
    total = sum(w for w, _ in parts)
    raw = [sum(w * values[i] for w, values in parts) / total for i in range(size)]
    half = HYPE_SMOOTH_S // 2
    return [sum(raw[max(i - half, 0):i + half + 1]) / len(raw[max(i - half, 0):i + half + 1]) for i in range(size)]


@register
class HypeEngine:
    name = "hype"

    def available(self, conn) -> bool:
        return True

    def propose(self, source: Source, profile) -> list[dict]:
        ep = source.ep
        hype = hype_map(ep)
        if not hype:
            source.notes.append("The hype map needs the video signals, which are missing, so that engine proposed nothing.")
            return []
        lo, hi = source.length
        want = (lo + hi) / 2
        floor = rank.percentile(hype, 85)
        peaks = [i for i in range(len(hype)) if hype[i] >= floor and hype[i] == max(hype[max(i - 3, 0):i + 4])]
        picks: list[dict] = []
        for t in sorted(peaks, key=lambda i: -hype[i]):
            if len(picks) >= HYPE_PEAKS or any(abs(t - p["peak"]) < lo for p in picks):
                continue
            window = self._window(ep, t + 0.5, want, lo, hi)
            if window is None:
                continue
            features, evidence = rank.window_features(ep, *window, lo, hi)
            score = 0.5 * hype[t] + 0.5 * rank.soft_score(features, source.profile.weights)
            tags = [tag for name in ("peaks", "laughter", "emotion") for tag in evidence.get(name, [])[:1]]
            picks.append({"start_s": window[0], "end_s": window[1], "score": round(score, 4), "peak": t, "features": features,
                          "reasons": [f"A peak {t // 60}:{t % 60:02d}: " + (", ".join(tags) or "the loudest stretch around it")][:1],
                          "source": self.name})
        return picks

    @staticmethod
    def _window(ep: rank.Episode, peak: float, want: float, lo: float, hi: float) -> tuple[float, float] | None:
        """Setup before the peak and the laugh after it, on sentence edges when there is speech near them."""
        start = max(peak - 0.4 * want, 0.0)
        end = min(start + want, ep.duration)
        starts = [s for s in ep.line_starts if abs(s - start) <= 4]
        if starts:
            start = max(min(starts, key=lambda s: abs(s - start)) - rank.BREATH_BEFORE, 0.0)
        fits = [e + rank.BREATH_AFTER for e in ep.line_ends if lo <= e + rank.BREATH_AFTER - start <= hi and e + rank.BREATH_AFTER <= ep.duration]
        end = min(fits, key=lambda e: abs(e - end)) if fits else min(max(end, start + lo), start + hi, ep.duration)
        end = min(rank.laugh_tail(ep, end, start + hi), ep.duration)
        return (round(start, 3), round(end, 3)) if lo <= end - start <= hi else None


@register
class PilotEngine:
    """The phase 2P picker: the LLM reads the transcript in chunks and names moments. Costs one call per ~10 minutes."""
    name = "pilot"

    def available(self, conn) -> bool:
        return True

    def propose(self, source: Source, profile) -> list[dict]:
        try:
            found, cost = moments_pilot.propose_with_cost(source.conn, source.source, source.version, profile.name, source.length)
        except KillSwitchOn:
            raise
        except AppError as exc:
            source.notes.append(f"The LLM picker proposed nothing ({exc}).")
            return []
        source.cost += cost
        return [{"start_s": c["start_s"], "end_s": c["end_s"], "score": c["score"], "reasons": c["reasons"],
                 "source": c["source"], "hook": c.get("hook") or ""} for c in found]


@register
class VendorEngine:
    """The configured `moments` adapter. Its picks are snapped to sentences and marked with the vendor's own name."""
    name = "vendor"

    def available(self, conn) -> bool:
        try:
            row = registry.row(conn, "moments")
        except AppError:
            return False
        if row["paused_reason"]:
            return False
        return config.use_fakes() if row["is_fake"] else registry.get(conn, "moments").health()["ok"]

    def propose(self, source: Source, profile) -> list[dict]:
        conn, row, owner = source.conn, source.source, source.owner_id
        storage = registry.get(conn, "storage")
        options = {"language": row["language"], "duration_s": float(row["duration_s"] or 0), "max_clips": 5}
        try:
            submitted, cost = registry.call(conn, "moments", "submit", {
                "source": {"key": row["stored_path"], "url": storage.get_signed_url(row["stored_path"], 3600)},
                "options": options}, owner)
            source.cost += cost
            for _ in range(VENDOR_POLLS):
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
            source.notes.append(f"The moment service proposed nothing ({exc}).")
            return []
        provider = registry.row(conn, "moments")["provider"]
        try:
            quiet = ffmpeg.silences(storage.path(row["stored_path"]))
        except Exception:                                    # a snap without silences is still a sentence snap
            quiet = []
        from .pipeline import sentence_bounds, snap   # lazy: pipeline imports this package's engines only inside its moments stage

        sentence_ends = sentence_bounds(source.ep.words)[1]
        out = []
        for item in found:
            if item.get("start_s") is None or item.get("end_s") is None:
                continue
            start, end = snap(float(item["start_s"]), float(item["end_s"]), source.ep.words, quiet)
            fitted = moments_pilot.fit({"start": start, "end": min(end, source.ep.duration)}, source.ep.words, sentence_ends,
                                       *source.length, source.ep.duration)    # the vendor's pick, grown or trimmed to our length range
            if fitted:
                out.append({"start_s": fitted[0], "end_s": fitted[1], "score": float(item.get("score") or 0.5),
                            "reasons": [str(item["reason"])[:200]] if item.get("reason") else [], "source": provider})
        return out
