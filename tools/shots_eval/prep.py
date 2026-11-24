"""Ground-truth prep for one video: episode download (internal benchmark only, deleted after), heatmap, auto-caption word times,
and the channel's own Shorts that were cut from the same episode (found by caption n-gram overlap) with their view counts.
usage: prep.py <video_id> <label>   -> .data/benchmark/shots/<label>/{ep.mp4, truth.json}"""
import json, re, sys, unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import yt_dlp

sys.path.insert(0, "L:/Projects/Clipping/.claude/worktrees/owner-notes-7eaac2/backend")
from clipping import ffmpeg as _ff
FF = _ff.exe()
ROOT = Path(r"L:\Projects\Clipping\.data\benchmark\shots")
vid, label = sys.argv[1], sys.argv[2]
out = ROOT / label
out.mkdir(parents=True, exist_ok=True)
Q = {"js_runtimes": {"node": {}}, "ignore_no_formats_error": True, "quiet": True, "no_warnings": True, "retries": 3, "sleep_interval": 2, "max_sleep_interval": 5}


def norm(w):
    w = unicodedata.normalize("NFKD", w.lower())
    return re.sub(r"[^\w]", "", "".join(c for c in w if not unicodedata.combining(c)))


def caption_words(info, lang_hint):
    """Word timings from the auto captions (json3)."""
    subs = (info.get("automatic_captions") or {}) | (info.get("subtitles") or {})
    keys = [k for k in subs if k.split("-")[0] == lang_hint] or [k for k in subs if k.endswith("-orig")] or list(subs)[:1]
    if not keys:
        return []
    fmt = next((f for f in subs[keys[0]] if f.get("ext") == "json3"), None)
    if not fmt:
        return []
    import time, urllib.request
    for wait in (0, 30, 90, 240):
        time.sleep(wait)
        try:
            data = json.load(urllib.request.urlopen(fmt["url"], timeout=60)); break
        except Exception:
            data = {}
    words = []
    for ev in data.get("events", []):
        t0 = ev.get("tStartMs", 0)
        for s in ev.get("segs") or []:
            w = norm(s.get("utf8", ""))
            if w:
                words.append((t0 + s.get("tOffsetMs", 0)) / 1000), words.append(w)
    return list(zip(words[0::2], words[1::2]))


with yt_dlp.YoutubeDL({**Q, "skip_download": True}) as y:
    info = y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
lang = (info.get("language") or "en").split("-")[0]
ep_words = caption_words(info, lang)
meta = {"id": vid, "label": label, "title": info["title"], "channel": info["channel"], "channel_id": info["channel_id"], "duration": info["duration"],
        "views": info.get("view_count"), "lang": lang, "heatmap": info.get("heatmap"), "chapters": info.get("chapters"), "n_caption_words": len(ep_words)}
print(label, meta["title"][:50], "caption words", len(ep_words), "heat", len(meta["heatmap"] or []), flush=True)

# --- the channel's Shorts
import urllib.parse
shorts, seen = [], set()
for q in (sys.argv[3].split("|") if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else [""]):
    url = f"https://www.youtube.com/channel/{info['channel_id']}/" + (f"search?query={urllib.parse.quote(q)}" if q else "shorts")
    with yt_dlp.YoutubeDL({**Q, "extract_flat": True, "playlistend": 40}) as y:
        for e in (y.extract_info(url, download=False) or {}).get("entries") or []:
            if e and e["id"] not in seen and e["id"] != vid and (e.get("duration") or 0) <= 65:
                seen.add(e["id"]); shorts.append(e)
print("shorts listed", len(shorts), flush=True)
K = 4
index = {}
for i in range(len(ep_words) - K):
    index.setdefault(tuple(w for _, w in ep_words[i:i + K]), []).append(i)


def locate(sw):
    votes = {}
    for j in range(len(sw) - K):
        for i in index.get(tuple(w for _, w in sw[j:j + K]), []):
            votes.setdefault(i - j, []).append(j)       # same offset => same stretch of speech
    best = None
    for off, js in votes.items():
        near = [(o, j) for o, jj in votes.items() if abs(o - off) <= 12 for j in jj]
        if best is None or len(near) > best[0]:
            best = (len(near), off)
    if not best or best[0] < 0.3 * max(len(sw) - K, 1):
        return None
    first = ep_words[max(best[1], 0)][0]
    last = ep_words[min(best[1] + len(sw), len(ep_words) - 1)][0]
    return {"start_s": round(first, 2), "end_s": round(last, 2), "cover": round(best[0] / max(len(sw) - K, 1), 2)}


def one(e):
    try:
        with yt_dlp.YoutubeDL({**Q, "skip_download": True}) as y:
            si = y.extract_info(f"https://www.youtube.com/watch?v={e['id']}", download=False)
        sw = caption_words(si, lang)
        loc = locate(sw) if len(sw) > 12 else None
        if loc:
            return {"id": si["id"], "title": si["title"], "dur": si["duration"], "views": si.get("view_count"), "date": si.get("upload_date"), **loc}
    except Exception as exc:
        return {"id": e["id"], "error": str(exc)[:80]}
    return None


with ThreadPoolExecutor(2) as pool:
    found = [r for r in pool.map(one, shorts) if r and "error" not in r]
meta["official_shorts"] = sorted(found, key=lambda r: r["start_s"])
(out / "truth.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
(out / "ep_words.json").write_text(json.dumps(ep_words, ensure_ascii=False), encoding="utf-8")
print(label, "shorts matched", len(found), flush=True)

if "--nodl" not in sys.argv and not (out / "ep.mp4").exists():
    with yt_dlp.YoutubeDL({**Q, "format": "bv*[height<=480]+ba/b[height<=480]/worst", "merge_output_format": "mp4", "ffmpeg_location": __import__("sys").modules["__main__"].FF, "outtmpl": str(out / "ep.%(ext)s"), "ratelimit": 4_000_000}) as y:
        y.download([f"https://www.youtube.com/watch?v={vid}"])
print(label, "done", flush=True)
