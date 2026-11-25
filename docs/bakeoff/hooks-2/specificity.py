"""Objective check, no taste: share of hooks that carry a concrete anchor from the clip (a number, or a word of 4+ letters that the transcript contains,
ignoring stop words and the generic teaser words) and share of EMPTY TEASERS (a formula such as 'Wait for', 'Bet you', 'Hear me out', 'Why he says' plus nothing from the clip).
python specificity.py test.json  (also fed the transcripts from .data/benchmark/hooks-2/moments.json)"""
import json, re, sys
from pathlib import Path
rows = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
moments = {m["id"]: m for m in json.loads(Path(r"L:\Projects\Clipping\.data\benchmark\hooks-2\moments.json").read_text(encoding="utf-8"))}
STOP = set("wait bet that this what when with from have they them were your about into just like then than will would could should there their which while".split()) | \
       set("hear out why how who not the and for are you".split())
TEASER = re.compile(r"^(wait (for|till|until)|bet you|hear me out|why (he|she|they|did)|not what you think|you won.t believe|استنى|اسمعني شوي)", re.I)
def toks(s): return re.findall(r"\w{4,}", s.lower())
def anchored(h, text):
    t = set(toks(text)); body = h
    if re.search(r"\d", body): return True
    return any(w in t and w not in STOP for w in toks(body))
for arm in ("old", "spec", "angle", "quote"):
    hs = [(r, r["hooks"][arm]["hook"]) for r in rows if r["hooks"].get(arm, {}).get("hook")]
    if not hs: continue
    for lang in ("en", "ar", None):
        sel = [(r, h) for r, h in hs if lang is None or r["lang"] == lang]
        a = sum(anchored(h, moments[r["id"]]["text"]) for r, h in sel); e = sum(bool(TEASER.search(h)) for r, h in sel)
        print(f"{arm:6} {lang or 'all':3} n={len(sel):3} anchored {a/len(sel):.0%}  empty-teaser {e/len(sel):.0%}  avg words {sum(len(h.split()) for r,h in sel)/len(sel):.1f}")
