"""Edge metric: does a clip start and end on silence? Compares the pipeline's own padded window (0.05 s before, 0.15 s after the
sentence) with polish.snap_edges. Reported: dB of the first/last 20 ms vs the speech level; a cut above -10 dB is "in loud audio" (clicks, clipped phonemes)."""
import json, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, r"L:\Projects\Clipping\.claude\worktrees\cut-quality-3d8a7d\backend")
from clipping import polish
W = json.load(open(Path(__file__).with_name("windows.json"), encoding="utf8"))
def abrupt(src, s, e):
    env = polish.envelope(src, s - 2, e - s + 4); loud = env[env > np.percentile(env, 40)]; lvl = float(np.percentile(loud, 50))
    i, j = int(2 * 100), int((e - s + 2) * 100)
    return float(env[i:i + 2].mean()) - lvl, float(env[j - 2:j].mean()) - lvl     # loudness of the cut frame vs the speech level
tot = [0, 0, 0, 0]; n = 0
for w in W:
    words = json.load(open(w["transcript"], encoding="utf8")); s, e = w["start_s"] - 0.05, w["end_s"] + 0.15
    near = [x for x in words if x["end"] > s - 1 and x["start"] < e + 1]
    s2, e2 = polish.snap_edges(w["src"], s, e, near)
    b, a = abrupt(w["src"], s, e), abrupt(w["src"], s2, e2)
    print(w["id"], "before", [round(x,1) for x in b], "after", [round(x,1) for x in a], f"start {s2 - s:+.2f} end {e2 - e:+.2f}")
    for k, v in enumerate((*b, *a)): tot[k] += v > -10
    n += 1
print(f"cut in loud audio (> -10 dB): start {tot[0]}/{n} -> {tot[2]}/{n};  end {tot[1]}/{n} -> {tot[3]}/{n}")
