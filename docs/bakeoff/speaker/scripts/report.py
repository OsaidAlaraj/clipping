"""Scores table + paired bootstrap over clips -> prints markdown. python report.py"""
import json, numpy as np
from pathlib import Path
r = json.load(open(Path(__file__).parent.parent / "results.json")); M = ["baseline", "diar", "lr", "fabu_onnx", "asdnb", "lr_f", "fabu_f", "asdnb_f", "diar+lr"]
G = {"Multi-person + listener (6 clips)": lambda g: g in ("multi", "listener"), "Listener-from-behind (4)": lambda g: g == "listener", "Multi-person (2)": lambda g: g == "multi",
     "Two-up / single-cam interview (5)": lambda g: g == "two", "All labelled people clips (11)": lambda g: g != "single", "Gaming face-cam (5)": lambda g: g == "single"}
print("| Group | " + " | ".join(M) + " |\n|---|" + "---|" * len(M))
for name, f in G.items():
    row = []
    for m in M:
        h = sum(v[m]["hit"] for v in r.values() if f(v["group"])); n = sum(v[m]["n"] for v in r.values() if f(v["group"])); row.append("%.1f %%" % (100 * h / n))
    print(f"| {name} | " + " | ".join(row) + " |")
rng = np.random.default_rng(1); ids = [c for c, v in r.items() if v["group"] != "single"]
for m in ("diar", "lr", "fabu_onnx", "asdnb", "lr_f", "fabu_f", "asdnb_f"):
    d = []
    for _ in range(5000):
        s = rng.choice(len(ids), len(ids)); h1 = sum(r[ids[i]][m]["hit"] for i in s); h0 = sum(r[ids[i]]["baseline"]["hit"] for i in s); n = sum(r[ids[i]][m]["n"] for i in s); d.append(100 * (h1 - h0) / n)
    print(f"bootstrap {m} - baseline over 11 people clips: mean {np.mean(d):+.1f} pts, 95% CI [{np.percentile(d, 2.5):+.1f}, {np.percentile(d, 97.5):+.1f}]")
