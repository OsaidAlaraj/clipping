"""Does a hook scorer separate market winners from losers? (owner direction: the judge is MARKET TASTE, validated before it is trusted)
Data: .data/market/shorts_all.jsonl (every recent Short, up to 60 per channel; outlier = views / channel median). Within each channel the top quarter
(winners) and bottom quarter (losers) are paired at random. A scorer 'passes a pair' when it prefers the winner's title (both A/B orders, so
position bias cancels). Chance = 50%.
Scorers: m_zero (LLM 'which got more views', no examples), j1_title (the bake-off 1 'stop scrolling' judge text without a transcript),
feat (logistic regression on simple title features, leave-one-channel-out), m_few (LLM with 16 winner/loser examples from OTHER channels of the
same niche and language). Caveat: these are video titles, not on-screen hook text (first-5-second text could not be collected).
Run from backend/: python ../docs/bakeoff/hooks-2/validate_scorer.py  -> scorer_validation.json / .md"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import arms  # noqa: E402

rows = [json.loads(l) for l in Path(r"L:\Projects\Clipping\.data\market\shorts_all.jsonl").read_text(encoding="utf-8").splitlines()]
by_ch = defaultdict(list)
for r in rows:
    by_ch[(r["niche"], r["lang"], r["channel"])].append(r)
rng = random.Random(7)
pairs, pool_ex = [], defaultdict(list)
for key, vs in by_ch.items():
    vs.sort(key=lambda r: -r["outlier"])
    q = len(vs) // 4
    win, lose = vs[:q], vs[-q:]
    for w, l in zip(rng.sample(win, min(12, q)), rng.sample(lose, min(12, q))):
        pairs.append({"ch": key, "win": w["title"], "lose": l["title"], "niche": key[0], "lang": key[1]})
    pool_ex[key[:2]] += [{"title": r["title"], "label": "winner", "ch": key[2]} for r in win[:8]] + [{"title": r["title"], "label": "loser", "ch": key[2]} for r in lose[:8]]
print(len(pairs), "pairs from", len(by_ch), "channels", flush=True)

ZERO = ("Two titles of short vertical videos from the same channel are given as A and B. One got far more views than the other. Using your knowledge of what "
        "stops people scrolling and gets watched and shared in this niche and language, guess which one got MORE views. Reply JSON only: {\"winner\":\"A\"|\"B\"}.")
J1T = ("You are a heavy TikTok and Reels user scrolling at night. Two on-screen hooks A and B of short clips are given. Decide which would make you STOP scrolling "
       "and keep watching. A hook wins by being specific, opening a curiosity gap, sounding like a real person and being instantly clear. Reply JSON only: {\"winner\":\"A\"|\"B\"}.")
FEW = ("Two titles of short vertical videos from the same niche are given as A and B; one got far more views than the other (relative to its channel). `examples` are real "
       "titles from OTHER channels of this niche and language, labelled winner (top views for their channel) or loser (bottom). Learn what the market rewards "
       "from them, then guess which of A and B got MORE views. Reply JSON only: {\"winner\":\"A\"|\"B\"}.")


def ask(sysmsg: str, task: str, p: dict, extra: dict | None = None) -> float:
    """1.0 if the scorer picks the winner in both orders, 0.5 in one, 0 in none."""
    score = 0.0
    for swap in (False, True):
        a, b = (p["lose"], p["win"]) if swap else (p["win"], p["lose"])
        out = arms.complete(task, sysmsg, {"language": p["lang"], "niche": p["niche"], "A": a, "B": b, **(extra or {})})
        pick = "A" if str(out.get("winner", "")).strip().upper().startswith("A") else "B"
        score += 0.5 if (pick == "B") == swap else 0.0
    return score


def safe(fn, p):
    try:
        return fn(p)
    except Exception as exc:
        print("err", repr(exc)[:80], flush=True)
        return None


def run(fn):
    with ThreadPoolExecutor(8) as ex:
        return list(ex.map(lambda p: safe(fn, p), pairs))


def feats(t: str) -> list[float]:
    w = t.split()
    return [len(w) / 10, "?" in t or "؟" in t, any(c.isdigit() for c in t), sum(c.isupper() for c in t) / max(len(t), 1) * 5, "#" in t, "!" in t,
            sum(not c.isalnum() and not c.isspace() for c in t) / max(len(t), 1) * 5, '"' in t or "«" in t,
            t.lower().startswith(("how", "why", "what", "can", "is")), any(x in t.lower() for x in ("pov", "nobody", "me when")), any(ord(c) > 0x1F300 for c in t)]


def feat_scores() -> list[float]:
    """Logistic regression (gradient descent) on the titles of the OTHER channels, tested on this channel's pairs."""
    res = {}
    for key in {p["ch"] for p in pairs}:
        X, y = [], []
        for k2, vs in by_ch.items():
            if k2 == key:
                continue
            q = len(vs) // 4
            X += [feats(r["title"]) for r in vs[:q]] + [feats(r["title"]) for r in vs[-q:]]
            y += [1] * q + [0] * q
        n = len(X[0]); w = [0.0] * n; b = 0.0
        for _ in range(300):
            gw, gb = [0.0] * n, 0.0
            for xi, yi in zip(X, y):
                z = max(min(b + sum(a * c for a, c in zip(w, xi)), 30), -30)
                e = 1 / (1 + math.exp(-z)) - yi
                gw = [g + e * c for g, c in zip(gw, xi)]; gb += e
            w = [a - 0.5 * g / len(X) for a, g in zip(w, gw)]; b -= 0.5 * gb / len(X)
        for p in pairs:
            if p["ch"] == key:
                sw = sum(a * c for a, c in zip(w, feats(p["win"]))); sl = sum(a * c for a, c in zip(w, feats(p["lose"])))
                res[id(p)] = 1.0 if sw > sl else 0.5 if sw == sl else 0.0
    return [res[id(p)] for p in pairs]


def few(p):
    ex = [e for e in pool_ex[(p["niche"], p["lang"])] if e["ch"] != p["ch"][2]]
    random.Random(p["win"]).shuffle(ex)
    return ask(FEW, "mkt_few", p, {"examples": [{"title": e["title"], "label": e["label"]} for e in ex[:16]]})


def rate(v):
    v = [x for x in v if x is not None]
    if not v:
        return None
    m = sum(v) / len(v)
    return {"acc": m, "n": len(v), "ci": 1.96 * math.sqrt(max(m * (1 - m), 0.01) / len(v))}


def main():
    res = {"feat": feat_scores(), "m_zero": run(lambda p: ask(ZERO, "mkt_zero", p)), "j1_title": run(lambda p: ask(J1T, "mkt_j1", p)), "m_few": run(few)}
    lines = ["# Market-taste scorer validation (held-out pairs: winner vs loser of the same channel; chance 50%)", "", f"{len(pairs)} pairs, {len(by_ch)} channels.", "",
             "| Scorer | all | en | ar | podcast | gaming | comedy | movies | business |", "|---|---|---|---|---|---|---|---|---|"]
    for name, v in res.items():
        def sub(f):
            r = rate([x for x, p in zip(v, pairs) if f(p)])
            return f"{r['acc'] * 100:.0f}% ±{r['ci'] * 100:.0f} (n={r['n']})" if r else "-"
        lines.append(f"| {name} | {sub(lambda p: True)} | {sub(lambda p: p['lang'] == 'en')} | {sub(lambda p: p['lang'] == 'ar')} | " +
                     " | ".join(sub(lambda p, n=n: p["niche"] == n) for n in ("podcast", "gaming", "comedy", "movies", "business")) + " |")
    (HERE / "scorer_validation.md").write_text("\n".join(lines), encoding="utf-8")
    (HERE / "scorer_validation.json").write_text(json.dumps({"pairs": pairs, **res}, ensure_ascii=False), encoding="utf-8")
    print("\n".join(lines))


main()
