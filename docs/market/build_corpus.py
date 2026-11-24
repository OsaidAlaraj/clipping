"""Market corpus builder (cheap, resumable, no API key, no video download). For each niche x language: YouTube search (this month, by view
count), keep Shorts-length results (<= 90 s), look up each channel's recent median views for an outlier score, and try the first words
from the auto-captions (VTT only). Writes L:\\Projects\\Clipping\\.data\\market\\youtube_curated.jsonl (one row per video, dated, with `expires`).
Run:  .venv\\Scripts\\python.exe docs\\market\\build_corpus.py [cells_limit]   (anonymous, slow on purpose; see docs/market-corpus.md)"""
import datetime
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"L:\Projects\Clipping")
OUT = ROOT / ".data/market"; (OUT / "subs").mkdir(parents=True, exist_ok=True)
YT = str(ROOT / ".venv/Scripts/yt-dlp.exe")
SP = "CAMSBAgEEAE%253D"   # sort by view count, uploaded this month (requested; not independently verified per row)
TODAY = datetime.date.today()
CELLS = {
    ("gaming", "en"): ["gaming clip shorts funny", "streamer reaction shorts"], ("gaming", "ar"): ["مقاطع قيمنق شورتس", "ستريمر ردة فعل شورتس"],
    ("podcast", "en"): ["podcast clip shorts", "interview moment shorts"], ("podcast", "ar"): ["بودكاست مقطع شورتس", "مقابلة شورتس"],
    ("business", "en"): ["business advice shorts", "entrepreneur money shorts"], ("business", "ar"): ["نصائح بيزنس شورتس", "ريادة اعمال شورتس"],
    ("sports", "en"): ["sports highlights shorts", "football moment shorts"], ("sports", "ar"): ["اهداف كرة قدم شورتس", "رياضة لقطة شورتس"],
    ("comedy", "en"): ["stand up comedy shorts", "funny sketch shorts"], ("comedy", "ar"): ["كوميدي مقطع مضحك شورتس", "ستاند اب كوميدي شورتس"],
    ("movies", "en"): ["movie scene shorts", "tv series scene shorts"], ("movies", "ar"): ["مشهد فيلم شورتس", "مشهد مسلسل شورتس"],
    ("general", "en"): ["viral youtube shorts", "most viewed shorts"], ("general", "ar"): ["شورتس منتشر", "اكثر مشاهدة شورتس"],
}


# v2: search discovery was noisy (kids, DIY, music channels), so the real run uses known channels per cell (handles that do not resolve are skipped).
CURATED = {
    ("gaming", "en"): ["IShowSpeed", "Jynxzi", "KaiCenatLive", "xQcOW", "Ludwig", "Markiplier"], ("gaming", "ar"): ["aboflah", "SA7RAWI"],
    ("podcast", "en"): ["lexfridman", "PowerfulJRE", "TheDiaryOfACEO", "hubermanlab", "flagrantpodcast"], ("podcast", "ar"): ["thmanyah"],
    ("business", "en"): ["AlexHormozi", "CodieSanchez", "Valuetainment", "garyvee", "ycombinator"], ("business", "ar"): ["thmanyah", "Annajah"],
    ("sports", "en"): ["ESPN", "brfootball", "DAZNFootball", "NBA", "FabrizioRomano"],
    ("comedy", "en"): ["netflixisajoke", "jimmyfallon", "kevinhart4real", "ComedyCentral", "SNL"],
    ("movies", "en"): ["Movieclips", "netflix", "HBO", "A24", "WarnerBrosPictures"],
    ("general", "en"): ["MrBeast", "MarkRober", "DudePerfect"],
}


def ytdlp(args, timeout=120):
    r = subprocess.run([YT, *args], capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    return r.stdout


def flat(url, n):
    rows = []
    for ln in ytdlp(["--flat-playlist", "--dump-json", "--playlist-end", str(n), url]).splitlines():
        try:
            rows.append(json.loads(ln))
        except ValueError:
            pass
    return rows


def first_words(vid, lang):
    for old in (OUT / "subs").glob(f"{vid}.*"):
        old.unlink()
    ytdlp(["--skip-download", "--write-auto-subs", "--write-subs", "--sub-langs", f"{lang}.*", "--sub-format", "vtt", "--sleep-requests", "3",
           "-o", str(OUT / "subs" / f"{vid}.%(ext)s"), f"https://www.youtube.com/shorts/{vid}"])
    f = next(iter((OUT / "subs").glob(f"{vid}.*.vtt")), None)
    if not f:
        return None
    out, t0 = [], None
    for ln in f.read_text(encoding="utf-8").splitlines():
        m = re.match(r"(\d+):(\d+):([\d.]+) -->", ln)
        if m:
            t0 = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
        elif ln.strip() and t0 is not None and t0 < 5 and "-->" not in ln and not ln.startswith(("WEBVTT", "Kind", "Language")):
            t = re.sub(r"<[^>]+>", "", ln).strip()
            if t and (not out or out[-1] != t):
                out.append(t)
    f.unlink(missing_ok=True)
    return " ".join(out) or None


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 99
    path = OUT / "youtube_curated.jsonl"
    done = {(json.loads(l)["niche"], json.loads(l)["lang"]) for l in path.read_text(encoding="utf-8").splitlines()} if path.exists() else set()
    medians: dict[str, float | None] = {}
    for (niche, lang), queries in list(CELLS.items())[:limit]:
        if (niche, lang) not in CURATED:
            continue
        if (niche, lang) in done:
            continue
        # 1) discover active channels of the niche from search results (short-video filter); 2) read each channel's newest 30 Shorts
        # (newest first, so roughly the last weeks; the flat listing has no dates) and score every one against that channel's own median
        channels: dict[str, str] = {f"https://www.youtube.com/@{h}": h for h in CURATED.get((niche, lang), [])}
        for q in ([] if channels else queries):
            for r in flat(f"https://www.youtube.com/results?search_query={q.replace(' ', '+')}&sp=EgIYAQ%253D%253D", 40):
                if r.get("channel_url") and r["channel_url"] not in channels:
                    channels[r["channel_url"]] = r.get("channel")
        cand = []
        for ch, name in list(channels.items())[:9]:
            vids = [v for v in flat(ch.rstrip("/") + "/shorts", 30) if v.get("view_count") and v.get("id")]
            time.sleep(2)
            if len(vids) < 8:
                continue
            med = statistics.median(v["view_count"] for v in vids)
            for v in vids:
                cand.append({"niche": niche, "lang": lang, "id": v["id"], "title": v.get("title"), "channel": name, "views": v["view_count"],
                             "duration_s": v.get("duration"), "channel_median_views": med, "outlier": round(v["view_count"] / med, 1) if med else None})
        top = sorted(cand, key=lambda r: -(r["outlier"] or 0))[:12]
        rows = []
        for i, r in enumerate(top):
            rows.append(r | {"first5s": first_words(r["id"], lang) if i < 6 else None, "collected": TODAY.isoformat(),
                             "expires": (TODAY + datetime.timedelta(days=30)).isoformat()})
            time.sleep(2)
        with path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(niche, lang, len(rows), sum(1 for x in rows if x["first5s"]), flush=True)


if __name__ == "__main__":
    main()
