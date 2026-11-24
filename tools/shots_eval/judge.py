"""Blind pairwise judge: variant B against variant A, rank i against rank i, every pair asked in BOTH orders, ties count half.
  judge.py <A> <B> <label>...      -> prints win-rate of B and writes docs/bakeoff/shots/judge_<A>_vs_<B>.json
Clips are shown as their transcript text only (the judge never sees engine scores, ranks or reasons). Pairs whose windows overlap
by 70 % or more are the same clip and are not asked (counted as ties). Caveat: the judge is gpt-5-mini, the engine's own rater."""
import json, os, sys
from pathlib import Path
ROOT = Path(r"L:\Projects\Clipping")
WT = Path(__file__).resolve().parents[2]
RUN = ROOT / ".data" / "shots-run"
os.environ.update(CLIPPING_DATA_DIR=str(RUN), TMP=str(ROOT / ".data/tmp"), TEMP=str(ROOT / ".data/tmp"), PYTHONIOENCODING="utf-8")
sys.path.insert(0, str(WT / "backend"))
from clipping import config
config.load_dotenv(Path("L:/Projects/Clipping/.env")); os.environ.pop("DATABASE_URL", None)
from clipping import ops, profiles  # noqa
from clipping.alerts import registry
from clipping.agents import moments_llm
from clipping.db import Actor, get_db

A, B, labels = sys.argv[1], sys.argv[2], sys.argv[3:]
K = int(os.environ.get("JUDGE_K", "6"))
db = get_db(); db.migrate(); owner = ops.ensure_owner(db); ops.configure_adapters(db)
def share(a, b):
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0])) / max(min(a[1] - a[0], b[1] - b[0]), 1e-6)
res, detail = {"B": 0.0, "A": 0.0, "tie": 0.0}, []
for label in labels:
    words = json.load(open(RUN / "words" / f"{label}.json", encoding="utf-8"))
    lang = "ar" if label.startswith(("ar_", "local_ar")) else "en"
    def text(w):
        return " ".join(t for s, e, t in words if w[0] - 0.1 <= s and e <= w[1] + 0.1)
    ka = sorted(json.load(open(RUN / "out" / A / f"{label}.json", encoding="utf-8"))["kept"], key=lambda c: c["rank"])[:K]
    kb = sorted(json.load(open(RUN / "out" / B / f"{label}.json", encoding="utf-8"))["kept"], key=lambda c: c["rank"])[:K]
    for i, (a, b) in enumerate(zip(ka, kb)):
        wa, wb = (a["start_s"], a["end_s"]), (b["start_s"], b["end_s"])
        if share(wa, wb) >= 0.7:
            res["tie"] += 1; detail.append({"label": label, "rank": i + 1, "result": "same clip"}); continue
        texts = {"x": text(wa), "y": text(wb)}
        score = 0.0
        with db.session(Actor.worker()) as conn:
            prof = profiles.get(conn, "podcast")
            for order in ([("x", "y")], [("y", "x")]):
                found, _ = moments_llm.compare(conn, registry, owner.operator_id, lang, prof, texts, order)
                for win, lose in found:
                    score += 0.5 if win == "y" else 0.0
                if not found:
                    score += 0.25
        res["B"] += score; res["A"] += 1 - score
        detail.append({"label": label, "rank": i + 1, "A": wa, "B": wb, "B_score": score, "A_text": texts["x"][:400], "B_text": texts["y"][:400]})
asked = sum(1 for d in detail if d["result"] != "same clip") if False else sum(1 for d in detail if "B_score" in d)
rate = res["B"] / asked if asked else float("nan")
print(f"{B} vs {A}: win-rate of {B} = {rate:.3f} over {asked} judged pairs ({res['tie']:.0f} identical)")
Path(WT / "docs/bakeoff/shots").mkdir(parents=True, exist_ok=True)
json.dump({"A": A, "B": B, "win_rate_B": rate, "asked": asked, "identical": res["tie"], "pairs": detail}, open(WT / f"docs/bakeoff/shots/judge_{A}_vs_{B}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
