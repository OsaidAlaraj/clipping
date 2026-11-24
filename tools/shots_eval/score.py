"""Offline metrics (no LLM) for engine variants against the ground truth.
  score.py <variant>... [--k 3,5,8]      reads .data/benchmark/shots/<label>/truth.json and .data/shots-run/out/<variant>/<label>.json
Per label and variant: heat precision@k (window's mean replay percentile >= 0.80), official-short precision@k and recall, with the
two baselines that matter (Text Barely Beats Timing: position is strong): `position` = the first k windows of the same lengths from
the start, and `random` = mean of 300 random draws. Writes docs/bakeoff/shots/metrics.json and prints a markdown table."""
import json, random, sys
from pathlib import Path

ROOT = Path(r"L:\Projects\Clipping\.data")
variants = [a for a in sys.argv[1:] if not a.startswith("--")]
KS = [3, 5, 8]
HEAT_PCT = 0.80
MATCH = 0.5


def heat_pct(heat, a, b):
    """The window's mean replay value as a percentile among the video's bins (1.0 = hottest)."""
    vals = [h["value"] for h in heat]
    cover = [(h["value"], max(0.0, min(b, h["end_time"]) - max(a, h["start_time"]))) for h in heat]
    tot = sum(w for _, w in cover)
    if not tot:
        return 0.0
    mean = sum(v * w for v, w in cover) / tot
    return sum(v <= mean for v in vals) / len(vals)


def share(a, b):
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    return inter / max(min(a[1] - a[0], b[1] - b[0]), 1e-6)


def metrics(windows, truth):
    heat, shorts, dur = truth["heatmap"] or [], truth["official_shorts"], truth["duration"]
    out = {}
    for k in KS:
        top = windows[:k]
        if heat:
            out[f"heat@{k}"] = sum(heat_pct(heat, *w) >= HEAT_PCT for w in top) / max(len(top), 1)
        if shorts:
            out[f"short@{k}"] = sum(any(share(w, (s["start_s"], s["end_s"])) >= MATCH for s in shorts) for w in top) / max(len(top), 1)
            out[f"recall@{k}"] = sum(any(share(w, (s["start_s"], s["end_s"])) >= MATCH for w in top) for s in shorts) / len(shorts)
    return out


def baselines(lengths, truth, rng):
    dur = truth["duration"]
    pos, t = [], 0.0
    for L in lengths:
        pos.append((t, t + L)); t += L
    acc = {}
    for _ in range(300):
        rnd = [(x, x + L) for L in lengths for x in [rng.uniform(0, max(dur - L, 1))]]
        for key, val in metrics(rnd, truth).items():
            acc[key] = acc.get(key, 0) + val / 300
    return metrics(pos, truth), acc


rows, rng = {}, random.Random(7)
for tf in sorted((ROOT / "benchmark" / "shots").glob("*/truth.json")):
    label, truth = tf.parent.name, json.loads(tf.read_text(encoding="utf-8"))
    for v in variants:
        f = ROOT / "shots-run" / "out" / v / f"{label}.json"
        if not f.exists():
            continue
        kept = sorted(json.loads(f.read_text(encoding="utf-8"))["kept"], key=lambda c: c["rank"])
        wins = [(c["start_s"], c["end_s"]) for c in kept]
        rows[(label, v)] = metrics(wins, truth) | {"n": len(wins)}
    if variants and (label, variants[0]) in rows:
        lengths = [b - a for a, b in wins] or [45.0]
        rows[(label, "position")], rows[(label, "random")] = baselines(lengths, truth, rng)
keys = [f"{m}@{k}" for m in ("heat", "short") for k in KS] + [f"recall@{k}" for k in KS]
print("| video | variant | n | " + " | ".join(keys) + " |\n|---|---|---|" + "---|" * len(keys))
for (label, v), m in rows.items():
    print(f"| {label} | {v} | {m.get('n', '')} | " + " | ".join(f"{m[k]:.2f}" if k in m else "-" for k in keys) + " |")
Path("docs/bakeoff/shots").mkdir(parents=True, exist_ok=True)
json.dump({f"{a}|{b}": m for (a, b), m in rows.items()}, open("docs/bakeoff/shots/metrics.json", "w"), indent=1)
