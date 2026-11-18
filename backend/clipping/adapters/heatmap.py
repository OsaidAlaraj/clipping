"""YouTube audience signals read from yt-dlp's own output: the "most replayed" heatmap (an `info` field) and the live
chat replay (a `.live_chat.json` file, one JSON object per line). Both are untrusted input: every parser returns
None, never raises, and keeps only well-formed, bounded entries. Saved samples live in backend/fixtures/ytdlp."""
from __future__ import annotations

import json
import math
from pathlib import Path

MAX_SECONDS = 24 * 3600      # nothing past a day is a video offset
MAX_HEATMAP = 1000           # YouTube sends about 100 segments
MAX_CHAT_BYTES = 100 * 1024 ** 2
MAX_CHAT_LINES = 3_000_000


def _finite(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def heatmap_from_info(info) -> list[dict] | None:
    """The valid {start_time, end_time, value} segments of yt-dlp's `heatmap` field, or None when there are none."""
    raw = info.get("heatmap") if isinstance(info, dict) else None
    if not isinstance(raw, list):
        return None
    segments = []
    for item in raw[:MAX_HEATMAP]:
        if not isinstance(item, dict):
            continue
        start, end, value = (_finite(item.get(k)) for k in ("start_time", "end_time", "value"))
        if None in (start, end, value) or not (0 <= start < end <= MAX_SECONDS):
            continue
        segments.append({"start_time": start, "end_time": end, "value": min(max(value, 0.0), 1.0)})
    return segments or None


def replay_series(heatmap, seconds: int) -> list[float] | None:
    """One value per second: the segment covering that second. None when there is no heatmap (absent, not zero)."""
    if not heatmap:
        return None
    series = [0.0] * seconds
    for seg in heatmap:
        for second in range(max(int(seg["start_time"]), 0), min(math.ceil(seg["end_time"]), seconds)):
            series[second] = max(series[second], float(seg["value"]))
    return series


def chat_counts_from_live_chat(text: str) -> list[list[int]] | None:
    """[[second, messages]] counted from the lines of a live chat replay file; None when no message has a usable offset."""
    counts: dict[int, int] = {}
    for line in text.splitlines()[:MAX_CHAT_LINES]:
        try:
            action = json.loads(line).get("replayChatItemAction")
            offset = int(action["videoOffsetTimeMsec"]) / 1000
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
        if 0 <= offset < MAX_SECONDS:
            counts[int(offset)] = counts.get(int(offset), 0) + 1
    return [[second, n] for second, n in sorted(counts.items())] or None


def read_live_chat(path: Path) -> list[list[int]] | None:
    """Parse a live chat replay file. A missing, oversized or unreadable file is no chat."""
    try:
        if path.stat().st_size > MAX_CHAT_BYTES:
            return None
        return chat_counts_from_live_chat(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None


def chat_series(counts, seconds: int) -> list[float] | None:
    """Chat messages per second from stored [[second, n]] pairs. None when there is no chat."""
    if not counts:
        return None
    series = [0.0] * seconds
    for item in counts:
        try:
            second, n = int(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 0 <= second < seconds and math.isfinite(n):
            series[second] += n
    return series
