"""Builds assets/music-catalog.json from tracks downloaded off Free Music Archive (artist HoliznaCC0, CC0 1.0).
Run after downloading + measuring: `python -m clipping.music_build <candidates.json> <download folder>`.

Admission (owner decision 2026-11-20: CC0 / no-attribution only, and music that suits short-form feeds). A track is admitted only if
  1. its Free Music Archive page links the CC0 1.0 licence (checked live, record kept),
  2. that page lists the genre "Instrumental" (no vocals to compete with speech),
  3. measured: a clear pulse (>= 3.4 onsets/s), tempo 70-135 BPM, a steady level (<= 17 dB swing), 90 s or longer,
  4. later, in the bake-off: the voice stays 18 dB or more above the bed under the chosen mix.
The intro length is measured and the bed always starts on a bar line after it, so the 1-2 s a viewer hears is the track at full
strength. Niche and mood are the curator's reading of the album; energy is computed from tempo and pulse."""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path

from . import ffmpeg, music

CC0_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
# album -> (niches in priority order, moods in priority order, style note)
ALBUMS = {
    "power-pop": (["business", "gaming"], ["upbeat", "playful"], "bright synth-pop with a steady kick"),
    "bassic": (["business", "gaming"], ["driving", "upbeat"], "bass-led groove, fast opening"),
    "city-slacker": (["business", "podcasts"], ["upbeat", "calm"], "laid-back hip-hop drums, relaxed but rhythmic"),
    "left-overs": (["business", "podcasts"], ["upbeat", "calm"], "mid-tempo beat, positive"),
    "kick-it-laid-back-hiphop": (["podcasts", "business"], ["calm"], "laid-back hip-hop"),
    "summer-air-lo-fi": (["podcasts", "business"], ["calm", "upbeat"], "warm lo-fi beat"),
    "public-domain-lofi": (["podcasts", "business"], ["calm", "upbeat"], "lo-fi beat"),
    "lo-fi-and-chill": (["podcasts", "business"], ["calm"], "lo-fi chill beat"),
    "lazy-summer-lofi-1": (["podcasts", "business"], ["calm", "upbeat"], "lo-fi chill beat"),
    "gamer-beats": (["gaming"], ["driving", "tense"], "gaming beats"),
    "phonk-aura-farming": (["gaming"], ["tense", "driving"], "phonk, a current short-form gaming sound"),
    "chiptunes": (["gaming"], ["playful", "driving"], "chiptune, the classic gaming bed"),
    "retro-gamer-soundtrack": (["gaming", "business"], ["driving", "mysterious"], "retro game soundtrack"),
    "we-drove-all-night-game-soundtrack": (["gaming", "podcasts"], ["calm", "mysterious"], "game soundtrack"),
}


THIN_SPOTS = ["One artist for all 37 tracks (HoliznaCC0): across many accounts the beds will start to sound alike. Free CC0 sources with several artists are rare.", "Styles are lo-fi, synth-pop, hip-hop beats, phonk, chiptune and retro-game. Missing: current trending pop/EDM-style beds, bright corporate/explainer beds, and anything regional or Arabic in feel.", "Business has 4 high-energy tracks and podcasts have none above energy 0.65; podcasts and business have no tense or mysterious bed (only gaming does).", "Fit is measured (tempo, pulse, intro, level swing, speech clarity) but 'sounds current on TikTok or Instagram' is the curator's judgement: no free tool measures it. A paid library with trend tags would cover this."]


def _page(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")


def verify_page(url: str) -> dict:
    """Licence and vocal facts read from the track's own Free Music Archive page."""
    html = _page(url)
    genres = re.findall(r"genre/([A-Za-z0-9_\-]+)/", html)
    return {"cc0": "creativecommons.org/publicdomain/zero/1.0" in html, "genres": sorted(set(genres)), "instrumental": "Instrumental" in genres}


def energy_of(f: dict) -> float:
    pulse = min(max((f["onsets_per_s"] - 3.0) / 3.5, 0.0), 1.0)
    tempo = min(max((f["bpm"] - 70.0) / 60.0, 0.0), 1.0)
    return round(0.5 * pulse + 0.5 * tempo, 2)


def admit(f: dict) -> list[str]:
    """Reasons a measured candidate is refused (empty = admitted on measurement)."""
    why = []
    if f["onsets_per_s"] < 3.4:
        why.append("pulse too weak")
    if not 70 <= f["bpm"] <= 135:
        why.append("tempo outside 70-135")
    if f["dynamic_db"] > 17:
        why.append("level swings too much under speech")
    if f["duration_s"] < 90:
        why.append("shorter than 90 s")
    if f["album"] not in ALBUMS:
        why.append("album not curated")
    return why


def build(candidates: Path, folder: Path, target_dir: Path | None = None) -> dict:
    target_dir = target_dir or music.music_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    out, refused = [], []
    for f in json.loads(candidates.read_text(encoding="utf-8")):
        problems = admit(f)
        page = None
        if not problems:
            page = verify_page(f["url"])
            if not page["cc0"]:
                problems.append("CC0 licence not found on the track page")
            if not page["instrumental"]:
                problems.append("page does not list the Instrumental genre")
        if problems:
            refused.append({"title": f["title"], "reason": "; ".join(problems)})
            continue
        niches, moods, style = ALBUMS[f["album"]]
        name = f"{f['id']}.mp3"
        shutil.copy2(folder / name, target_dir / name)
        title = re.sub(r"\s*\(.*?\)|\.mp3$", "", f["title"]).strip()
        out.append({"id": f"hz-{f['id']}", "title": title, "artist": "HoliznaCC0", "file": name, "sha256": ffmpeg.sha256_file(target_dir / name),
                    "source_url": f["url"], "licence": "CC0-1.0", "licence_url": CC0_URL, "vocals": "none", "genres": page["genres"],
                    "bpm": round(f["bpm"]), "energy": energy_of(f), "moods": moods, "niches": niches, "duration_s": f["duration_s"],
                    "intro_s": f["intro_s"], "onsets_per_s": f["onsets_per_s"], "dynamic_db": f["dynamic_db"], "lufs": f["lufs"],
                    "fit_why": (f"{style}; {round(f['bpm'])} BPM with a {f['onsets_per_s']}/s pulse; instrumental (page genre tag); level swing "
                                f"{f['dynamic_db']} dB; full strength after {f['intro_s']} s, so the bed starts on a bar line past the intro")})
    catalog = {"version": 2, "licences_checked": dt.date.today().isoformat(), "policy": "CC0 only; no attribution lines (owner, 2026-11-20)",
               "accepted_licences": {"CC0-1.0": {"attribution_required": False, "burned_in_commercial": True, "url": CC0_URL}},
               "fit_criteria": ["instrumental (no vocals under speech)", "clear pulse and tempo 70-135 BPM, matched to the clip's energy and cut pace (half/double time counts)",
                                "steady level, so ducking can follow speech", "full strength within the first 1-2 s of where the bed starts (start is on a bar line after the measured intro)",
                                "tagged per niche and mood; no repeats on consecutive clips"],
               "thin_spots": THIN_SPOTS, "rejected": refused, "tracks": out}
    music.CATALOG_FILE.write_text(json.dumps(catalog, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return catalog


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]))
