"""The frozen test set: id -> (source key, t0 seconds, group). 30 s each. Sources live under .data (drive L)."""
import json
from pathlib import Path
DATA = Path("L:/Projects/Clipping/.data")
SRC = {"cord": DATA / "storage/sources/5a0f953d7860f80a787d5bc60403265815c75e3929778b25a3c145ccd81954e5.mp4",
       "gam": DATA / "storage/sources/75dfbc7be9734add87afe65d8dc78baab3a9b10c4e2cc2b1646c5137d5c1b7b3.mp4",
       "int": DATA / "storage/sources/9fa39e3ca9998aa5db760336cb0de0b692f0ce300bcd40f4d5b45273e2a9d9a9.mp4",
       "lec": DATA / "samples/ar_kau_physiology_l38.mp4"}
TR = {"cord": "5a0f953d7860f80a787d5bc60403265815c75e3929778b25a3c145ccd81954e5_en_default_deepgram.json",
      "gam": "75dfbc7be9734add87afe65d8dc78baab3a9b10c4e2cc2b1646c5137d5c1b7b3_en_default_deepgram.json",
      "int": "9fa39e3ca9998aa5db760336cb0de0b692f0ce300bcd40f4d5b45273e2a9d9a9_en_default_deepgram.json"}
CLIPS = {}
for i, t in enumerate([286.6, 740.3, 1194.1, 1647.8, 2101.6]): CLIPS[f"gam{i}"] = ("gam", t, "single")
for i, t in enumerate([782.2, 2020.6, 3259.0, 4544.4, 5735.9]): CLIPS[f"int{i}"] = ("int", t, "two")
for i, t in enumerate([355.7, 918.8, 1482.0, 2045.1, 2655.3]): CLIPS[f"lec{i}"] = ("lec", t, "noface")
for i, t in enumerate([1500, 3000, 3900, 4800]): CLIPS[f"back{i}"] = ("int", float(t), "listener")
for i, t in enumerate([1800, 2700]): CLIPS[f"multi{i}"] = ("cord", float(t), "multi")
def words(key):
    return json.loads((DATA / "storage/transcripts" / TR[key]).read_text(encoding="utf-8"))
def speaker_at(ws, t):
    for w in ws:
        if w["start"] <= t <= w["end"] + 0.3: return w.get("speaker")
    return None
