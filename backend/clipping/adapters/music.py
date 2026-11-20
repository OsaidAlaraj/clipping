"""Music stage adapters: `catalog` (the real, local, free catalog) and `fake` (synthetic tracks for tests).
Both answer `choose(...)` with a plain dict: {"use": False, "reason"} or {"use": True, "reason", "track", "offset_s", "score", "gain_db"}."""
from __future__ import annotations

from pathlib import Path

from .. import config, ffmpeg, music
from .base import Adapter, register

FAKE_MUSIC: dict = {"calls": [], "fail": False}

_FAKE_TRACKS = [
    {"id": "fake-a", "title": "Fake A", "artist": "Test", "file": "fake-a.mp3", "source_url": "https://example.invalid/a", "licence": "CC0-1.0",
     "licence_url": "https://creativecommons.org/publicdomain/zero/1.0/", "bpm": 90, "energy": 0.3, "moods": ["calm"], "niches": ["business", "podcasts", "gaming"],
     "duration_s": 40, "lufs": -16.0, "freq": 220},
    {"id": "fake-b", "title": "Fake B", "artist": "Test", "file": "fake-b.mp3", "source_url": "https://example.invalid/b", "licence": "CC0-1.0",
     "licence_url": "https://creativecommons.org/publicdomain/zero/1.0/", "bpm": 130, "energy": 0.8, "moods": ["driving"], "niches": ["business", "podcasts", "gaming"],
     "duration_s": 40, "lufs": -16.0, "freq": 440},
    {"id": "fake-c", "title": "Fake C", "artist": "Test", "file": "fake-c.mp3", "source_url": "https://example.invalid/c", "licence": "CC-BY-4.0",
     "licence_url": "https://creativecommons.org/licenses/by/4.0/", "bpm": 100, "energy": 0.5, "moods": ["upbeat"], "niches": ["business", "podcasts", "gaming"],
     "duration_s": 40, "lufs": -16.0, "freq": 330},
]


def fake_pool() -> list[dict]:
    """Three synthetic chord-ish tracks, written once under the data folder."""
    folder = config.data_dir() / "music-fake"
    folder.mkdir(parents=True, exist_ok=True)
    pool = []
    for track in _FAKE_TRACKS:
        path = folder / track["file"]
        if not path.exists():
            f = track["freq"]
            ffmpeg.run(["-f", "lavfi", "-i", f"sine=frequency={f}:sample_rate=44100", "-f", "lavfi", "-i", f"sine=frequency={f * 1.5}:sample_rate=44100",
                        "-filter_complex", "[0][1]amix=inputs=2:normalize=0,volume=0.3", "-t", str(track["duration_s"]), "-b:a", "128k", str(path)])
        pool.append({**track, "path": str(path)})
    return pool


class _Base(Adapter):
    def _decide(self, pool, niche, profile, duration_s, platform, recent, seed) -> dict:
        decision = music.choose(niche, profile, duration_s, platform, recent, seed, pool=pool)
        if decision["use"]:
            decision["gain_db"] = music.music_gain_db(decision["track"])
        return decision


@register
class CatalogMusic(_Base):
    stage, provider = "music", "catalog"

    def health(self) -> dict:
        return {"ok": True, "provider": self.provider, "usable_tracks": len(music.tracks(verify_hash=False))}

    def choose(self, niche, profile, duration_s, platform=None, recent=None, seed="") -> dict:
        return self._decide(music.tracks(), niche, profile, duration_s, platform, recent, seed)


@register
class FakeMusic(_Base):
    stage, provider = "music", "fake"

    def choose(self, niche, profile, duration_s, platform=None, recent=None, seed="") -> dict:
        FAKE_MUSIC["calls"].append({"niche": niche, "profile": profile, "recent": list(recent or [])})
        if FAKE_MUSIC["fail"]:
            raise RuntimeError("fake music failure")
        return self._decide(fake_pool(), niche, profile, duration_s, platform, recent, seed)
