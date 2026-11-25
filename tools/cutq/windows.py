"""Pick repeatable clip windows from the real sources: sentence start to sentence end, 35-50 s, spread across each video.
Usage: python windows.py > windows.json"""
import glob, json, os, sys
STORE = r"L:\Projects\Clipping\.data\storage"
END = ".?!؟…"
def pick(words, n=4, lo=35, hi=50):
    total = words[-1]["end"]; out = []
    for k in range(n):
        t0 = total * (k + 0.5) / n * 0.9 + total * 0.05
        i = next((i for i, w in enumerate(words) if w["start"] >= t0 and i and words[i-1]["text"].rstrip()[-1:] in END), None)
        if i is None: continue
        j = next((j for j in range(i, len(words)) if words[j]["end"] - words[i]["start"] >= lo and words[j]["text"].rstrip()[-1:] in END), None)
        if j is None or words[j]["end"] - words[i]["start"] > hi: continue
        out.append({"start_s": words[i]["start"], "end_s": words[j]["end"]})
    return out
res = []
for tf in sorted(glob.glob(STORE + r"\transcripts\*.json")):
    h = os.path.basename(tf)[:8]; lang = "ar" if "_ar_" in tf else "en"
    words = json.load(open(tf, encoding="utf8"))
    src = glob.glob(STORE + rf"\sources\{h}*.mp4")[0]
    for k, w in enumerate(pick(words)):
        res.append({"id": f"{h}_{k}", "src": src, "transcript": tf, "language": lang, **w})
json.dump(res, sys.stdout, ensure_ascii=False, indent=1)
