"""Moments for the hook bake-off 2: real Deepgram transcripts stored in .data (12 long English and Arabic videos, none of them the two
used by bake-off 1), cut into 25 to 45 s windows that start at a sentence start, plus the 28 clip spans the production pipeline picked
(table candidates). A fixed seed splits them into `dev` (the prompts may be tuned on these) and `test` (read only once, at the end).
Writes .data/benchmark/hooks-2/moments.json. Run: python build_moments.py"""
import glob, json, os, random, sys
from pathlib import Path

DATA = Path(r"L:\Projects\Clipping\.data")
OUT = DATA / "benchmark/hooks-2/moments.json"
USED_BEFORE = ("00d4e87415cc", "d327f3083a24")   # bake-off 1 sources
ENDS = (".", "?", "!", "؟")
rng = random.Random(20261125)


def load(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return d if isinstance(d, list) and d and "start" in d[0] else None


def windows(words, n):
    punct = sum(w["text"].rstrip().endswith(ENDS) for w in words) / len(words) > 0.02
    starts = [k for k in range(1, len(words) - 80) if (not punct) or words[k - 1]["text"].rstrip().endswith(ENDS)]
    rng.shuffle(starts)
    out, taken = [], []
    for i in starts:
        a = words[i]["start"]
        if any(abs(a - t) < 90 for t in taken):
            continue
        j = next((k for k in range(i, len(words)) if words[k]["end"] - a >= 25 and (not punct or words[k]["text"].rstrip().endswith(ENDS))
                  or words[k]["end"] - a >= 45), None)
        if j is None:
            continue
        text = " ".join(w["text"] for w in words[i:j + 1])
        if len(text.split()) >= 45:
            after = " ".join(w["text"] for w in words[j + 1:] if w["start"] < words[j]["end"] + 40)
            out.append((a, words[j]["end"], text, after))
            taken.append(a)
        if len(out) == n:
            break
    return out


def main():
    seen, moments = set(), []
    for f in sorted(glob.glob(str(DATA / "**/*deepgram.json"), recursive=True)):
        b = os.path.basename(f)
        if b[:12] in USED_BEFORE or "clip_test" in f or b in seen:
            continue
        w = load(f)
        if not w or w[-1]["end"] < 600:
            continue
        seen.add(b)
        lang = "ar" if "_ar_" in b else "en"
        for a, e, text, after in windows(w, 9 if lang == "en" else 14):
            moments.append({"after": after, "id": f"{b[:6]}_{int(a)}", "lang": lang, "niche": "podcast", "kind": "window", "text": text})
    rng.shuffle(moments)
    for lang, n_dev in (("en", 12), ("ar", 10)):
        pool = [m for m in moments if m["lang"] == lang]
        for k, m in enumerate(pool):
            m["split"] = "dev" if k < n_dev else "test"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(moments, ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    print(Counter((m["lang"], m["split"]) for m in moments), "sources", len(seen))


main()
