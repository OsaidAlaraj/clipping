"""Hook generators compared in bake-off 2. Every arm returns ONE hook text that passes hook_rules (3 to 9 words, no dashes or quotes),
so any winner can ship without a renderer change. Arms:
  old    the production generator (8 candidates in lexicon formats, the model's own rubric picks the best)
  quote  the clip's own strongest words, trimmed (verbatim, checked in code against the transcript)
  spec   producer brief -> writer (6 candidates, each tied to a concrete anchor) -> editor picks and sharpens
The shared LLM is the project's own adapter; prompts are registered at run time so the product code is untouched until a winner is adopted."""
from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
from clipping import config, hook_lexicon, hook_rules  # noqa: E402
from clipping.adapters import vendors  # noqa: E402
from clipping.agents import hooks as agent  # noqa: E402

config.load_dotenv(Path(r"L:\Projects\Clipping\.env"))
LLM = vendors.AzureOpenAILLM({})
sys.path.insert(0, str(Path(__file__).parent))
from prompts import PROMPTS  # noqa: E402


def complete(task: str, system: str, payload: dict, tries: int = 4) -> dict:
    """One LLM call with the long timeout (these tasks are registered as reasoning tasks) and retries on a timeout or a dropped connection."""
    import time
    vendors.LLM_SYSTEM[task] = system
    vendors.REASONING_TASKS = frozenset(set(vendors.REASONING_TASKS) | {task})
    for k in range(tries):
        try:
            return LLM.complete_json(task, payload)
        except vendors.ContentFiltered:
            raise
        except Exception as exc:  # timeout, reset
            if k == tries - 1:
                raise
            time.sleep(3 * (k + 1))


def call(task: str, payload: dict) -> dict:
    return complete(task, PROMPTS[task], payload)


def norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", s.lower()).split())


def ok(text: str, lang: str) -> str | None:
    text, bad = hook_rules.check(text, language=lang)
    return None if bad or not text else text


# ---- old: what production does today
def _weighted_formats(lang, niche, seed, n=agent.CANDIDATES):
    rng, pool, out = random.Random(seed), list(hook_lexicon.formats(lang, niche)), []
    while pool and len(out) < n:
        mark, run = rng.random() * sum(f["weight"] for f in pool), 0.0
        for f in pool:
            run += f["weight"]
            if mark <= run:
                out.append(f["id"]); pool.remove(f); break
    return out


def old(m: dict) -> dict:
    text, lang, niche = m["text"], m["lang"], m["niche"]
    vendors.LLM_SYSTEM["hooks"] = vendors.LLM_SYSTEM["hooks"]   # production prompt, untouched
    seen, rejected = [], []
    cards = agent._format_cards(_weighted_formats(lang, niche, m["id"]), lang, niche)
    for _ in range(3):
        payload = {"segment_text": text, "language": lang, "niche": niche, "candidate_score": 0.7, "avoid": seen, "avoid_formats": [], "formats": cards,
                   "patterns": [c["id"] for c in cards], "good_bad": hook_lexicon.fewshot(lang, niche), "constraints": [], "fix": hook_rules.feedback(rejected)}
        raw = complete("hooks", vendors.LLM_SYSTEM["hooks"], payload)
        rejected = []
        hooks = agent.validate(raw, text, seen, rejected, lang)
        seen += [str(i.get("text")) for i in raw.get("hooks") or [] if isinstance(i, dict) and i.get("text")]
        if len(hooks) >= 2:
            return {"hook": hooks[0]["text"], "all": [h["text"] for h in hooks]}
    return {"hook": None, "why": "no valid hook"}


# ---- quote: the speaker's own words
def quote(m: dict) -> dict:
    raw = call("hook_quote", {"transcript": m["text"], "language": m["lang"]})
    t = norm(m["text"])
    for cand in [raw.get("hook")] + list(raw.get("backups") or []):
        if not isinstance(cand, str):
            continue
        fixed = ok(cand, m["lang"])
        if fixed and norm(fixed) in t:
            return {"hook": fixed}
    return {"hook": None, "why": "no verbatim line passed the rules"}


# ---- spec: producer -> writer -> editor
def spec(m: dict) -> dict:
    brief = call("hook_brief", {"transcript": m["text"], "language": m["lang"]})
    rejected = ""
    for _ in range(2):
        written = call("hook_write", {"transcript": m["text"], "language": m["lang"], "brief": brief, "fix": rejected})
        cands, bad = [], []
        for c in written.get("hooks") or []:
            if not isinstance(c, dict) or not isinstance(c.get("text"), str):
                continue
            fixed, why = hook_rules.check(c["text"], language=m["lang"])
            if why:
                bad.append((fixed, why)); continue
            cands.append({"text": fixed, "mode": c.get("mode"), "anchor": c.get("anchor")})
        if len(cands) >= 2:
            break
        rejected = hook_rules.feedback(bad)
    if len(cands) < 1:
        return {"hook": None, "why": "writer gave nothing valid"}
    final = call("hook_edit", {"transcript": m["text"], "language": m["lang"], "brief": brief, "candidates": [c["text"] for c in cands]})
    best = ok(str(final.get("hook") or ""), m["lang"]) or cands[0]["text"]
    return {"hook": best, "all": [c["text"] for c in cands], "brief": brief}


def angle(m: dict) -> dict:
    """Owner direction: an angle finder first (transcript plus what comes after), then a writer per angle, then an editor picks."""
    plan = call("hook_angles", {"transcript": m["text"], "after": m.get("after", ""), "language": m["lang"]})
    cands = []
    for ang in (plan.get("angles") or [])[:5]:
        if not isinstance(ang, dict):
            continue
        raw = call("hook_angle_write", {"transcript": m["text"], "language": m["lang"], "angle": ang})
        for t in raw.get("hooks") or []:
            if isinstance(t, str):
                fixed = ok(t, m["lang"])
                if fixed:
                    cands.append({"type": ang.get("type"), "text": fixed})
    if not cands:
        return {"hook": None, "why": "no valid angle hook"}
    final = call("hook_angle_pick", {"transcript": m["text"], "language": m["lang"], "candidates": cands})
    best = ok(str(final.get("hook") or ""), m["lang"]) or cands[0]["text"]
    return {"hook": best, "all": [c["type"] + " | " + c["text"] for c in cands], "mood": plan.get("mood")}


ARMS = {"old": old, "quote": quote, "spec": spec, "angle": angle}
