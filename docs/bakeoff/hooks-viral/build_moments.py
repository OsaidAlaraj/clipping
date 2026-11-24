"""Real moments for the hook bake-off, no new downloads: (1) the candidates the production pipeline picked from two stored 8 to 10 minute
transcripts (.data/pilot-run: an English tech podcast and an Arabic interview), (2) the caption text of 7 business Shorts fetched by
fetch_subs.py (YouTube now asks anonymous clients to sign in, so the audio route was not used). Writes
.data/benchmark/hooks-viral/moments.json. Honest limit: there is no gaming moment in the stored transcripts."""
import glob
import json
from pathlib import Path

ROOT = Path(r"L:\Projects\Clipping")
OUT = ROOT / ".data/benchmark/hooks-viral/moments.json"
subs = [m for m in (json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else []) if m.get("niche") == "business"]
out = list(subs)
for res in glob.glob(str(ROOT / ".data/pilot-run/results-*.json")):
    r = json.loads(Path(res).read_text(encoding="utf-8"))
    lang = r["language"]
    tfile = next(f for f in glob.glob(str(ROOT / ".data/pilot-run/storage/transcripts/*.json")) if f"_{lang}_" in Path(f).name)
    words = json.loads(Path(tfile).read_text(encoding="utf-8"))
    for c in r["candidates"]:
        text = " ".join(w["text"] for w in words if w["start"] >= c["start_s"] - 0.05 and w["end"] <= c["end_s"] + 0.05)
        if len(text.split()) >= 25:
            out.append({"id": f"{Path(r['file']).stem[:14]}_{int(c['start_s'])}", "niche": "podcast", "lang": lang, "channel": r["label"], "text": text})
    # more real speech: consecutive 22 to 40 s windows that start at a sentence start and do not overlap a picked candidate
    ends = (".", "?", "!", "؟")
    punctuated = sum(w["text"].rstrip().endswith(ends) for w in words) / len(words) > 0.02   # an unpunctuated transcript: any word may start a window
    taken, t = [(c["start_s"], c["end_s"]) for c in r["candidates"]], 6.0
    while t < words[-1]["end"] - 22:
        i = next((k for k, w in enumerate(words) if w["start"] >= t and (k == 0 or not punctuated or words[k - 1]["text"].rstrip().endswith(ends))), None)
        if i is None:
            break
        j = next((k for k in range(i, len(words)) if words[k]["end"] - words[i]["start"] >= 22 and (not punctuated or words[k]["text"].rstrip().endswith(ends))
                  or words[k]["end"] - words[i]["start"] >= 40), len(words) - 1)
        a, b = words[i]["start"], words[j]["end"]
        t = b + 1
        if any(a < y and b > x for x, y in taken):
            continue
        text = " ".join(w["text"] for w in words[i:j + 1])
        if len(text.split()) >= 30:
            out.append({"id": f"{Path(r['file']).stem[:14]}_w{int(a)}", "niche": "podcast", "lang": lang, "channel": r["label"], "text": text})
OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
from collections import Counter
print(len(out), Counter((m["niche"], m["lang"]) for m in out))
