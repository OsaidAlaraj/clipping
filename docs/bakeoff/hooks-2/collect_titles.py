"""Market taste corpus, part 1: EVERY recent Short (up to 60 per channel) of known channels, anonymous yt-dlp flat listing, no download, slow on purpose.
Each row: title, views, outlier = views / that channel's median. Unlike the first corpus (top 12 only) this keeps the losers too, so a hook scorer
can be tested on 'does it separate winners from non-winners of the same channel'. Writes .data/market/shorts_all.jsonl"""
import json, statistics, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, r"L:\Projects\Clipping\docs\market")
import build_corpus as bc

OUT = Path(r"L:\Projects\Clipping\.data\market\shorts_all.jsonl")
EXTRA = {("podcast", "en"): ["JREClips", "LexClips", "ModernWisdom", "TheDiaryOfACEO", "hubermanlab", "flagrantpodcast", "PowerfulJRE"],
         ("comedy", "en"): ["netflixisajoke", "jimmyfallon", "kevinhart4real", "SNL", "ComedyCentral"],
         ("gaming", "en"): ["IShowSpeed", "Jynxzi", "KaiCenatLive", "xQcOW", "Ludwig", "Markiplier"],
         ("movies", "en"): ["Movieclips", "netflix", "HBO", "A24"],
         ("business", "en"): ["AlexHormozi", "CodieSanchez", "Valuetainment", "garyvee", "ycombinator"],
         ("podcast", "ar"): ["thmanyah", "Annajah"], ("gaming", "ar"): ["aboflah", "SA7RAWI"]}
done = set()
if OUT.exists():
    done = {(json.loads(l)["niche"], json.loads(l)["lang"], json.loads(l)["channel"]) for l in OUT.read_text(encoding="utf-8").splitlines()}
for (niche, lang), handles in EXTRA.items():
    for h in handles:
        if (niche, lang, h) in done:
            continue
        vids = [v for v in bc.flat(f"https://www.youtube.com/@{h}/shorts", 60) if v.get("view_count") and v.get("id") and v.get("title")]
        time.sleep(2)
        if len(vids) < 12:
            print("skip", h, len(vids), flush=True); continue
        med = statistics.median(v["view_count"] for v in vids)
        with OUT.open("a", encoding="utf-8") as fh:
            for v in vids:
                fh.write(json.dumps({"niche": niche, "lang": lang, "channel": h, "id": v["id"], "title": v["title"], "views": v["view_count"],
                                     "outlier": round(v["view_count"] / med, 2)}, ensure_ascii=False) + "\n")
        print(niche, lang, h, len(vids), flush=True)
