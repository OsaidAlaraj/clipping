"""Where the captions land. Renders the real caption lines of each segment onto a black 1080x1920 frame and measures the box of the
text (share of width and height) for the busiest line, with the TikTok and Reels interface zones as the yardstick.
Usage: measure_captions.py <backend root> <label>   (root = the worktree's backend folder for new, the HEAD copy for old)"""
import json
import os
import subprocess
import sys
import tempfile

import numpy as np

ROOT, LABEL = sys.argv[1], sys.argv[2]
D = os.environ.get("POLISH_DATA", r"L:\Projects\Clipping\.data\polish-work")
sys.path.insert(0, ROOT)
os.environ["CLIPPING_DATA_DIR"] = r"L:\Projects\Clipping\.data"
from clipping import ffmpeg  # noqa: E402
from clipping.adapters import media  # noqa: E402

OFFSET = {"en": 600.0, "ar": 900.0}
SEG = json.load(open(rf"{D}\segments.json"))
W, H = 1080, 1920
NEW = hasattr(media, "karaoke_events")


def lines(lang: str):
    words = [{**w, "start": w["start"] + OFFSET[lang], "end": w["end"] + OFFSET[lang]} for w in json.load(open(rf"{D}\{lang}_words.json", encoding="utf-8"))["words"]]
    start, end = (OFFSET[lang] + SEG[lang][0], OFFSET[lang] + SEG[lang][1])
    tpl = media.normalize_template({"progress_bar": False, "title_card": False})
    if NEW:
        ev = media.karaoke_events(words, start, end, True, tpl["accent"], W, H, int(H * tpl["font_size"] / 100), lang == "ar", "lower")
    else:
        ev = media.caption_events(words, start, end, True, [], tpl["accent"])
    return ev, tpl


def box(ass: str, at: float) -> dict:
    with tempfile.TemporaryDirectory(dir=r"L:\Projects\Clipping\.data\polish-work") as tmp:
        open(os.path.join(tmp, "c.ass"), "w", encoding="utf-8-sig").write(ass)
        raw = subprocess.run([ffmpeg.exe(), "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r=10:d=40", "-vf", "ass=c.ass,format=gray",
                              "-ss", f"{at:.2f}", "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True, cwd=tmp).stdout
    img = np.frombuffer(raw, dtype=np.uint8).reshape(H, W)
    ys, xs = np.where(img > 128)      # the text face and outline are bright; the black outline is not counted
    if not len(xs):
        return {}
    return {"x": [round(xs.min() / W, 3), round(xs.max() / W, 3)], "y": [round(ys.min() / H, 3), round(ys.max() / H, 3)],
            "width_share": round((xs.max() - xs.min()) / W, 3), "height_px": int(ys.max() - ys.min())}


out = {"label": LABEL}
for lang in ("en", "ar"):
    ev, tpl = lines(lang)
    widest = max(ev, key=lambda e: len(e["text"]))
    t = media.build_ass(W, H, 40, [{**widest, "start": 0.0, "end": 2.0}], None, lang == "ar", tpl, None)
    out[lang] = {"events": len(ev), "font_px": int(H * tpl["font_size"] / 100), "widest_line": box(t, 1.0)}
print(json.dumps(out, ensure_ascii=False))
