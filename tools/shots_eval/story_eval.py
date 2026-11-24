"""Story Director on stored moments: how often each technique fires, and a blind pairwise judge of the opening order.
  story_eval.py <variant> <label>...
For each kept clip: plan = story_director.plan(...). Where a cold open is planned, A = the clip as cut, B = the teaser line, then the clip
(what the viewer hears first changes, nothing else); judged in BOTH orders on 'which would you stop scrolling for'. Writes
docs/bakeoff/shots/story-director.json. The judge is gpt-5-mini (same family as the engine's rater): read the examples too."""
import json, os, sys
from pathlib import Path
ROOT = Path(r"L:\Projects\Clipping"); WT = Path(__file__).resolve().parents[2]; RUN = ROOT / ".data" / "shots-run"
os.environ.update(CLIPPING_DATA_DIR=str(RUN), TMP=str(ROOT / ".data/tmp"), TEMP=str(ROOT / ".data/tmp"), PYTHONIOENCODING="utf-8")
sys.path.insert(0, str(WT / "backend"))
from clipping import config
config.load_dotenv(Path("L:/Projects/Clipping/.env")); os.environ.pop("DATABASE_URL", None)
from clipping import ops, profiles, story_director
from clipping.alerts import registry
from clipping.agents import moments_llm
from clipping.db import Actor, get_db

variant, labels = sys.argv[1], sys.argv[2:]
db = get_db(); db.migrate(); owner = ops.ensure_owner(db); ops.configure_adapters(db)
counts = {"clips": 0, "cold_open": 0, "ending_trim": 0, "rehook": 0}; wins = asked = 0.0; detail = []
for label in labels:
    raw = json.load(open(RUN / "words" / f"{label}.json", encoding="utf-8"))
    words = [{"start": s, "end": e, "text": t} for s, e, t in raw]
    lang = "ar" if "ar" in label else "en"
    kept = json.load(open(RUN / "out" / variant / f"{label}.json", encoding="utf-8"))["kept"]
    for c in kept:
        p = story_director.plan(words, c["start_s"], c["end_s"], lang, "podcast", 60.0)
        counts["clips"] += 1
        for t in p["techniques"]:
            counts[t] += 1
        if not p["cold_open"]:
            continue
        clip_text = " ".join(w["text"] for w in words if c["start_s"] - 0.1 <= w["start"] and w["end"] <= c["end_s"] + 0.1)
        texts = {"x": clip_text, "y": p["cold_open"]["text"] + " … " + clip_text}
        score = 0.0
        with db.session(Actor.worker()) as conn:
            prof = profiles.get(conn, "podcast")
            for order in ([("x", "y")], [("y", "x")]):
                found, _ = moments_llm.compare(conn, registry, owner.operator_id, lang, prof, texts, order)
                score += sum(0.5 for win, _ in found if win == "y") + (0.25 if not found else 0)
        asked += 1; wins += score
        detail.append({"label": label, "start_s": c["start_s"], "teaser": p["cold_open"]["text"], "opening_was": clip_text[:140], "cold_open_score": score, "why": p["why"]})
rate = wins / asked if asked else float("nan")
print(counts, f"cold-open win-rate {rate:.3f} over {int(asked)} judged clips")
json.dump({"variant": variant, "counts": counts, "cold_open_win_rate": rate, "judged": int(asked), "examples": detail}, open(WT / "docs/bakeoff/shots/story-director.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
