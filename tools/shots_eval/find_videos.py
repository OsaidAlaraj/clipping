"""List candidate long videos (30-100 min) with a heatmap, for the ground-truth set. Metadata only, no download."""
import sys, json
import yt_dlp
queries = sys.argv[1:]
opts = {"quiet": True, "skip_download": True, "no_warnings": True, "extract_flat": False}
for q in queries:
    with yt_dlp.YoutubeDL({**opts, "extract_flat": True}) as y:
        res = y.extract_info(f"ytsearch8:{q}", download=False)
    for e in res["entries"]:
        d = e.get("duration") or 0
        if not 1800 <= d <= 6000:
            continue
        with yt_dlp.YoutubeDL(opts) as y:
            try:
                info = y.extract_info(e["url"], download=False)
            except Exception as exc:
                print("ERR", e["id"], str(exc)[:80]); continue
        print(q, "|", info["id"], info.get("channel"), "|", info["title"][:60], "|", int(d/60), "min", "| heat", len(info.get("heatmap") or []), "| views", info.get("view_count"), "| lang", info.get("language"), flush=True)
