"""Run ClipMind's moment engine on the benchmark episodes with the real providers, in a throwaway database (drive L).
  run_engine.py full   <label>...   transcribe + signals + moments (first time)
  run_engine.py refind <label>...   moments only, on the stored transcript and signals (after a code change)
Output: .data/shots-run/out/<VARIANT>/<label>.json  (kept and filtered candidates with times, scores, reasons)."""
import json, os, sys, time
from pathlib import Path

ROOT = Path(r"L:\Projects\Clipping")
WT = Path(__file__).resolve().parents[2]
RUN = ROOT / ".data" / "shots-run"
RUN.mkdir(parents=True, exist_ok=True)
os.environ.update(CLIPPING_DATA_DIR=str(RUN), TMP=str(ROOT / ".data/tmp"), TEMP=str(ROOT / ".data/tmp"), PYTHONIOENCODING="utf-8")
sys.path.insert(0, str(WT / "backend"))
from clipping import config  # noqa: E402
from pathlib import Path as _P; config.load_dotenv(_P("L:/Projects/Clipping/.env"))
os.environ.pop("DATABASE_URL", None)
assert not config.use_fakes()
from clipping import campaigns, moments, ops, pipeline  # noqa: E402
from clipping.db import Actor, get_db  # noqa: E402

mode, labels = sys.argv[1], sys.argv[2:]
POOL = []
_orig_finish = moments._finish
def _spy(src, kept, aside, *a, **k):          # every candidate that reached the layers, with its features, for the ablation
    POOL[:] = [{"start_s": c["start_s"], "end_s": c["end_s"], "engine": c.get("engine"), "signal": c.get("signal"), "score": c.get("score"),
                "proposed": c.get("proposed"), "features": c.get("features"), "llm": (c.get("llm") or {}).get("overall"), "kept": c in kept}
               for c in kept + aside if c.get("features")]
    return _orig_finish(src, kept, aside, *a, **k)
moments._finish = _spy
VARIANT = os.environ.get("SHOTS_VARIANT", "base")
LOCAL = {"local_ar": "L:/Projects/Clipping/.data/samples/ar_seif_el_islam_interview.mp4", "local_en": "L:/Projects/Clipping/.data/tmp/en_cordkillers_first10min.mp4"}
META = {"local_ar": ("ar", "podcast"), "local_en": ("en", "podcast"), "en_podcast": ("en", "podcast"), "en_business": ("en", "podcast"), "ar_podcast": ("ar", "podcast"),
        "en_gaming": ("en", "gaming"), "en_stream": ("en", "gaming")}
db = get_db(); db.migrate()
owner = ops.ensure_owner(db)
print("adapters:", ops.configure_adapters(db), "variant", VARIANT, flush=True)
reg = RUN / "sources.json"
sources = json.loads(reg.read_text()) if reg.exists() else {}
if os.environ.get("SHOTS_ROUTE"):
    import json as _j
    from psycopg.types.json import Jsonb
    with db.session(Actor.system()) as conn:
        for name in ("podcast", "stream"):
            conn.execute("update content_profiles set config = config || %s where name = %s", (Jsonb({"route": os.environ["SHOTS_ROUTE"].split(",")}), name))
out = RUN / "out" / VARIANT; out.mkdir(parents=True, exist_ok=True)
for label in labels:
    lang, niche = META[label]
    t0 = time.time()
    if mode == "full" and label not in sources:
        with db.session(owner) as conn:
            camp = campaigns.create_practice(conn, owner, label, niche, lang)
            src = campaigns.register_source(conn, owner, camp["id"], LOCAL.get(label) or str(ROOT / f".data/benchmark/shots/{label}/ep.mp4"), lang)
        sources[label] = str(src["id"]); reg.write_text(json.dumps(sources))
        while True:
            with db.session(Actor.worker()) as conn:
                nxt = conn.execute("select type from jobs where status = 'queued' and source_id = %s order by created_at limit 1", (src["id"],)).fetchone()
            if nxt is None or nxt["type"] in ("hooks", "render"):
                break
            pipeline.run_until_idle(db, max_jobs=1); print(label, nxt["type"], round(time.time() - t0), flush=True)
    sid = sources[label]
    if mode == "refind":
        with db.session(Actor.worker()) as conn:
            result = moments.find(conn, sid, None)
    else:
        with db.session(owner) as conn:
            rows = conn.execute("select * from candidates where source_id = %s", (sid,)).fetchall()
        result = None
    if result is None:
        with db.session(Actor.worker()) as conn:
            result = moments.find(conn, sid, None)
    (RUN / "words").mkdir(exist_ok=True)
    if not (RUN / "words" / f"{label}.json").exists():
        from clipping import transcripts
        with db.session(Actor.worker()) as conn:
            json.dump([[w["start"], w["end"], w["text"]] for w in transcripts.load(conn, sid)["words"]], open(RUN / "words" / f"{label}.json", "w", encoding="utf-8"), ensure_ascii=False)
    def pack(c): return {k: c.get(k) for k in ("start_s", "end_s", "score", "rank", "reasons", "engine", "variant", "hook", "features")}
    json.dump({"label": label, "variant": VARIANT, "cost": float(result["cost"]), "llm": result["llm"], "route": result["route"], "notes": result["notes"],
               "profile": result["profile"], "pool": list(POOL), "wall_s": round(time.time() - t0, 1),
               "kept": [pack(c) for c in result["kept"]], "filtered": [{**pack(f["candidate"]), "why": f["reason"], "layer": f["layer"]} for f in result["filtered"]]},
              open(out / f"{label}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(label, "kept", len(result["kept"]), "filtered", len(result["filtered"]), "cost", result["cost"], flush=True)
