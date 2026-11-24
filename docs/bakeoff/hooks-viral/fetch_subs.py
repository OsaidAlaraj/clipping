"""Fetch auto-captions (text only, no video) for the Shorts listed in docs/market-style-study-2.md: anonymous, slow, internal use.
Writes .data/benchmark/hooks-viral/moments.json = [{id, niche, lang, text}] and appends to .data/benchmark/manifest.json.
Run: .venv\Scripts\python.exe docs\bakeoff\hooks-viral\fetch_subs.py"""
import json, re, subprocess, sys, time, datetime
from pathlib import Path
ROOT = Path(r"L:\Projects\Clipping")
DOC = Path(__file__).resolve().parents[2] / "market-style-study-2.md"
OUT = ROOT / ".data/benchmark/hooks-viral"; (OUT / "subs").mkdir(parents=True, exist_ok=True)
rows = re.findall(r"\[([\w-]{11})\]\(https://www.youtube.com/shorts/[\w-]{11}\) \| (\w+)/(\w+) \| ([^|]+) \|", DOC.read_text(encoding="utf-8"))
moments, manifest = [], json.loads((ROOT / ".data/benchmark/manifest.json").read_text(encoding="utf-8"))
def vtt_text(p):
    out, last = [], ""
    for ln in p.read_text(encoding="utf-8").splitlines():
        if "-->" in ln or ln.startswith(("WEBVTT", "Kind:", "Language:")) or not ln.strip(): continue
        t = re.sub(r"<[^>]+>", "", ln).strip()
        if t and t != last: out.append(t); last = t
    return " ".join(out)
for vid, niche, lang, chan in rows:
    if niche not in ("business", "gaming", "podcast"): continue
    f = OUT / "subs" / f"{vid}.{lang}.vtt"
    if not f.exists():
        subprocess.run([str(ROOT / ".venv/Scripts/yt-dlp.exe"), "--skip-download", "--write-auto-subs", "--write-subs", "--sub-langs", f"{lang}.*", "--sub-format", "vtt",
                        "--sleep-requests", "4", "-o", str(OUT / "subs" / f"{vid}.%(ext)s"), f"https://www.youtube.com/shorts/{vid}"], capture_output=True)
        time.sleep(4)
    got = next(iter((OUT / "subs").glob(f"{vid}.{lang}*.vtt")), None)
    status = "no-captions"
    if got:
        text = vtt_text(got)
        if len(text.split()) >= 25:
            moments.append({"id": vid, "niche": niche, "lang": lang, "channel": chan.strip(), "text": text}); status = "subs-only"
    manifest.append({"url": f"https://www.youtube.com/shorts/{vid}", "platform": "youtube", "niche": niche, "channel": chan.strip(), "date": datetime.date.today().isoformat(),
                     "size": got.stat().st_size if got else 0, "status": status, "note": "captions text only for hook benchmark, no video downloaded"})
    print(vid, niche, lang, status, flush=True)
(OUT / "moments.json").write_text(json.dumps(moments, ensure_ascii=False, indent=1), encoding="utf-8")
(ROOT / ".data/benchmark/manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
print("moments", len(moments))
