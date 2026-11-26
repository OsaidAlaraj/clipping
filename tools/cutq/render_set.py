"""Render the windows through the real FFmpegRender.render_clip. Usage: python render_set.py OUTDIR [ids...]"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, r"L:\Projects\Clipping\.claude\worktrees\cut-quality-3d8a7d\backend")
from clipping.adapters.media import FFmpegRender
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
wins = json.load(open(Path(__file__).with_name("windows.json"), encoding="utf8"))
want = set(sys.argv[2:])
ad = FFmpegRender({"config_json": {"encoder": "libx264", "preset": "veryfast", "crf": 20}})
for w in wins:
    if want and w["id"] not in want: continue
    words = json.load(open(w["transcript"], encoding="utf8"))
    clip = {"start_s": w["start_s"], "end_s": w["end_s"], "language": w["language"], "platform": "tiktok", "crop": "follow_speaker", "hook_text": None, "emphasis": [], "min_s": 20}
    t = time.time()
    try:
        r = ad.render_clip(w["src"], clip, words, {}, "#ad", str(out / f"{w['id']}.mp4"))
    except Exception as e:
        print(w["id"], "FAIL", e); continue
    json.dump(r, open(out / f"{w['id']}.json", "w"), indent=1, default=str)
    print(w["id"], f"{time.time()-t:.0f}s", r["duration_s"], r["reframe"], "cuts", len((r["tightened"] or {}).get("cuts", [])), flush=True)
