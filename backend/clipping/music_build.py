"""Builds assets/music-catalog.json from the downloaded files: measures each track (integrated LUFS, duration), hashes it and
writes the licence record. Run once after downloading: `python -m clipping.music_build`. Tags (niche, mood, energy) are the
curator's reading of Kevin MacLeod's own "feel" and tempo fields, checked by ear-proxies in docs/bakeoff/final/music.

Licence: every track here is by Kevin MacLeod (incompetech.com), offered free under Creative Commons Attribution 4.0
(https://creativecommons.org/licenses/by/4.0/). The incompetech licence page (https://incompetech.com/music/royalty-free/licenses/)
generates the credit "Title / Kevin MacLeod (incompetech.com) / Licensed under Creative Commons: By Attribution 4.0". CC BY 4.0
allows commercial use, adaptation (mixing, ducking, trimming) and distribution when the credit is given; the credit goes into the
caption (see music.attribution). Verified 2026-11-20 against the live licence page and pieces.json."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from . import ffmpeg, music

BASE = "https://incompetech.com/music/royalty-free/"
# title -> (file stem as downloaded, niches in priority order, moods in priority order, energy 0..1)
CURATION = {
    "Backbay Lounge": (["business", "podcasts"], ["calm", "upbeat"], 0.35),
    "Wallpaper": (["business", "podcasts"], ["calm", "upbeat"], 0.30),
    "Dream Culture": (["podcasts", "business"], ["calm"], 0.20),
    "Local Forecast - Elevator": (["business", "podcasts"], ["upbeat", "calm"], 0.40),
    "Impact Moderato": (["business", "podcasts"], ["calm"], 0.30),
    "Carefree": (["business"], ["upbeat", "calm"], 0.45),
    "Volatile Reaction": (["gaming"], ["tense", "driving"], 0.90),
    "Faster Does It": (["gaming", "business"], ["driving", "tense"], 0.80),
    "Heroic Age": (["gaming", "business"], ["driving", "upbeat"], 0.75),
    "Cipher": (["gaming", "business"], ["upbeat", "driving"], 0.70),
    "Night in Venice": (["podcasts"], ["calm"], 0.25),
    "Sunshine": (["podcasts", "business"], ["calm", "upbeat"], 0.25),
    "Investigations": (["podcasts"], ["mysterious", "playful"], 0.35),
    "Kool Kats": (["podcasts", "business"], ["mysterious", "calm"], 0.45),
    "Thinking Music": (["podcasts", "gaming"], ["mysterious", "tense"], 0.40),
    "Digital Lemonade": (["business", "gaming"], ["upbeat", "playful"], 0.50),
    "Funky Chunk": (["business", "gaming"], ["upbeat", "driving"], 0.60),
}


def _measure(path: Path) -> float:
    proc = subprocess.run([ffmpeg.exe(), "-hide_banner", "-nostdin", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    found = re.findall(r"I:\s+(-?[\d.]+) LUFS", proc.stderr)
    if not found:
        raise RuntimeError(f"cannot measure {path.name}")
    return float(found[-1])


def build(folder: Path | None = None) -> dict:
    folder = folder or music.music_dir()
    pieces = {p["title"]: p for p in json.loads((folder / "_raw" / "pieces.json").read_text(encoding="utf-8"))}
    out = []
    for title, (niches, moods, energy) in CURATION.items():
        piece = pieces[title]
        path = folder / piece["filename"]
        if not path.is_file():
            print("missing", path.name, file=sys.stderr)
            continue
        info = ffmpeg.probe(path)
        out.append({"id": "mm-" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"), "title": title, "artist": "Kevin MacLeod",
                    "file": piece["filename"], "sha256": ffmpeg.sha256_file(path), "source_url": BASE + "music.html", "licence": "CC-BY-4.0",
                    "licence_url": "https://creativecommons.org/licenses/by/4.0/", "isrc": piece.get("isrc"),
                    "bpm": int(piece["bpm"]), "energy": energy, "moods": moods, "niches": niches, "duration_s": info["duration_s"],
                    "lufs": _measure(path), "feel": piece.get("feel"), "instruments": piece.get("instruments")})
        print("ok", title, out[-1]["lufs"])
    catalog = {"version": 1, "licences_checked": "2026-11-20", "licence_page": BASE + "licenses/",
               "accepted_licences": {
                   "CC0-1.0": {"attribution_required": False, "burned_in_commercial": True, "url": "https://creativecommons.org/publicdomain/zero/1.0/"},
                   "CC-BY-4.0": {"attribution_required": True, "burned_in_commercial": True, "url": "https://creativecommons.org/licenses/by/4.0/"}},
               "rejected": [
                   {"source": "freepd.com", "reason": "site closed (checked 2026-11-20)"},
                   {"source": "YouTube Audio Library", "reason": "not cleared for burned-in use on TikTok or Instagram (research-verdicts: platform libraries stay in-app); not verified, so not used"},
                   {"source": "Pixabay / Mixkit style licences", "reason": "own licences, not a standard text; not reviewed for burned-in use on other platforms, so not adopted"},
                   {"source": "Kevin MacLeod - Bit Quest", "reason": "measured masking: speech-to-music ratio 15.1 dB under the winning mix, below the 18 dB intelligibility gate (docs/bakeoff/final/music/report.json)"},
                   {"source": "CC BY-NC / CC BY-SA tracks", "reason": "non-commercial or share-alike terms do not fit a monetised, burned-in clip"}],
               "tracks": out}
    target = music.CATALOG_FILE
    target.write_text(json.dumps(catalog, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return catalog


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
