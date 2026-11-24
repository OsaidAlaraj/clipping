"""Summarise .data/market/youtube_curated.jsonl: length, outlier score and title-opener share per niche and language -> prints markdown."""
import collections, json, re, statistics
from pathlib import Path
rows = [json.loads(l) for l in Path(r"L:\Projects\Clipping\.data\market\youtube_curated.jsonl").read_text(encoding="utf-8").splitlines()]
cells = collections.defaultdict(list)
for r in rows: cells[(r["niche"], r["lang"])].append(r)
OPEN = {"question": r"\?|^(why|how|what|who|ليش|كيف|شو|مين|هل)", "number": r"\d", "pov/nobody/me": r"^(pov|nobody|me when|ما حدا|أنا لما)", "caps_shout": r"[A-Z]{4,}"}
print("| Niche | Lang | n | Median length s | Length IQR | Median views | Top outlier (x channel median) | Title has ? | has number | POV/Nobody/Me | ALL CAPS word |\n|---|---|---|---|---|---|---|---|---|---|---|")
for (n, l), v in sorted(cells.items()):
    d = [x["duration_s"] for x in v if x["duration_s"]]; o = [x["outlier"] for x in v if x["outlier"]]
    q = statistics.quantiles(d, n=4) if len(d) >= 4 else [0, 0, 0]
    med = f"{statistics.median(d):.0f}" if d else "n/a"
    sh = lambda pat: f"{sum(bool(re.search(pat, x['title'] or '', re.I)) for x in v) / len(v) * 100:.0f}%"
    print(f"| {n} | {l} | {len(v)} | {med} | {q[0]:.0f} to {q[2]:.0f} | {statistics.median(x['views'] for x in v):,.0f} | {max(o) if o else 0:.1f} | " + " | ".join(sh(p) for p in OPEN.values()) + " |")
print("\nTop outliers per cell (title, outlier, views, length):")
for (n, l), v in sorted(cells.items()):
    for x in sorted(v, key=lambda x: -(x["outlier"] or 0))[:2]: print(f"- {n}/{l}: {x['title'][:70]} | x{x['outlier']} | {x['views']:,} | {x['duration_s']}s")
