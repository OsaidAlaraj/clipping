"""The moment engine: `find` turns one source into ranked, filtered, stored candidates.

Engines (router + moment_engines) only propose. Everything is judged here, in three layers:
  1. hard gates, deal-breakers only (avoid list, campaign length, mid-sentence start, already published, silence)
  2. soft score per profile: measured features, the LLM's ratings (corroborated against the signals), learned weights
  3. pairwise LLM comparison of the top few in BOTH orders, aggregated with Bradley-Terry (ties by signal score)
then a cut-off relative to the episode with a floor, the profile's minimum clip count, cut variants and hooks.
Nothing is dropped silently: a moment that is set aside is stored with its reason and layer and the owner can rescue it."""
from __future__ import annotations

import math

from psycopg.types.json import Jsonb

from . import hook_patterns, moment_rank as rank, profiles, router, signals, transcripts
from .adapters.vendors import ContentFiltered
from .agents import moments_llm
from .alerts import raise_alert, registry
from .core import AppError, GateError, KillSwitchOn, audit, enqueue, get_setting
from .moment_engines import Source
from .moment_learning import learn, weights_for  # noqa: F401  (learn is part of this module's interface)

RATE_TOP = 12            # candidates the LLM rates one by one
COMPARE_TOP = 8          # candidates compared head to head
MAX_PAIRS = 12           # pairs per order
FILTER_CALL_BUDGET = 6   # extra calls allowed to find which candidate a content filter refused
MAX_FILTERED = 25        # filtered moments stored per source
MIN_SECONDS = 3.0


# ------------------------------------------------------------------ the source

def _version(conn, campaign: dict, version) -> dict:
    if isinstance(version, dict):
        return version
    row = conn.execute("select * from campaign_versions where id = %s", (version or campaign["approved_version_id"],)).fetchone()
    if row is None:
        raise AppError("The campaign has no approved version.", "no_version")
    return row


def _source(conn, source_id, version, profile) -> Source:
    row = conn.execute("select * from source_assets where id = %s", (source_id,)).fetchone()
    if row is None:
        raise AppError("That source does not exist.", "no_source")
    campaign = conn.execute("select * from campaigns where id = %s", (row["campaign_id"],)).fetchone()
    version = _version(conn, campaign, version)
    words = transcripts.load(conn, source_id)["words"]
    duration = float(row["duration_s"] or 0) or words[-1]["end"]
    language = row["language"] if row["language"] in ("en", "ar") else "en"
    prof = profile if isinstance(profile, profiles.Profile) else profiles.get(conn, profile) if profile else profiles.for_source(conn, row, campaign)
    brief = rank.brief_terms(version.get("rules_json"), transcripts.key_terms_for(campaign, version))
    low, high = float(get_setting(conn, "clip_min_duration_s")), float(get_setting(conn, "clip_max_duration_s"))
    if brief["length"]:
        low, high = max(low, brief["length"][0] or low), min(high, brief["length"][1] or high)
    ep = rank.Episode(words, duration, language, signals.load(conn, source_id))
    src = Source(conn, row, campaign, version, ep, prof, prof.length_for(duration, (low, high)), brief, row["owner_operator_id"])
    src.campaign_length = (low, high)
    return src


# ------------------------------------------------------------------ gathering proposals

def _valid(item: dict, ep: rank.Episode) -> dict | None:
    try:
        start, end, score = float(item["start_s"]), min(float(item["end_s"]), ep.duration), float(item.get("score") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    if not all(map(math.isfinite, (start, end, score))) or start < 0 or end - start < MIN_SECONDS:
        return None
    reasons = [" ".join(str(r).split())[:200] for r in item.get("reasons") or [] if r][:3]
    return {"start_s": round(start, 3), "end_s": round(end, 3), "score": rank.clip01(score), "reasons": reasons,
            "source": str(item.get("source") or "own")[:40], "hook": str(item.get("hook") or "")[:140]}


def _take(proposals: dict[str, list[dict]], slots: dict[str, int], size: int) -> list[dict]:
    """Each engine's best proposals up to its slots, then the best of what is left until the pool is full."""
    pool, spare = [], []
    for name, items in proposals.items():
        items = sorted(items, key=lambda c: -c["score"])
        pool += items[:slots[name]]
        spare += items[slots[name]:]
    spare.sort(key=lambda c: -c["score"])
    return pool + spare[:max(size - len(pool), 0)]


def _gather(src: Source) -> list[dict]:
    conn, prof = src.conn, src.profile
    names = router.route(conn, prof)
    src.engines = names
    pool_size = max(router.POOL_SLOTS, 4 * prof.target(src.ep.duration))          # a long episode needs a wider pool
    slots = router.allocate(router.shares(conn, prof, names, src.owner_id), pool_size)
    proposals: dict[str, list[dict]] = {}
    for name in names:
        try:
            found = router.ENGINES[name].propose(src, prof)
        except KillSwitchOn:
            raise
        except Exception as exc:                  # one engine's failure never loses the others' moments
            src.notes.append(f"The {name} engine failed ({type(exc).__name__}).")
            found = []
        proposals[name] = [{**c, "engine": name} for c in (_valid(i, src.ep) for i in found) if c]
        router.record(conn, prof, name, "try", src.owner_id)
    return _take(proposals, slots, pool_size)


# ------------------------------------------------------------------ layer 1

def _published(conn, source_id) -> list[tuple[float, float]]:
    return [(float(r["start_s"]), float(r["end_s"])) for r in conn.execute(
        """select ca.start_s, ca.end_s from clips cl join candidates ca on ca.id = cl.candidate_id
           where ca.source_id = %s and cl.status = 'posted'""", (source_id,)).fetchall()]


def _gate(src: Source, pool: list[dict]) -> tuple[list[dict], list[dict]]:
    published = _published(src.conn, src.source["id"])
    live, filtered = [], []
    for c in pool:
        reason = rank.hard_gate(src.ep, c["start_s"], c["end_s"], rank.term_hits(src.ep, c["start_s"], c["end_s"], src.brief["avoid"]),
                                src.campaign_length, published, src.profile)
        (filtered if reason else live).append({**c, "filtered": (reason, 1)} if reason else c)
    return live, filtered


# ------------------------------------------------------------------ layer 2

def _features(src: Source, live: list[dict], weights: dict) -> None:
    lo, hi = src.length
    for c in live:
        features, evidence = rank.window_features(src.ep, c["start_s"], c["end_s"], lo, hi)
        c["features"], c["evidence"] = features, evidence
        c["include"] = rank.term_hits(src.ep, c["start_s"], c["end_s"], src.brief["include"])
        c["signal"] = rank.adjust(rank.soft_score(features, weights), features, c["include"], src.profile, src.brief["wants_info"])
        c["score"] = c["signal"]
        c["notes"] = [f"Mentions \"{t}\" (brief)" for t in c["include"][:2]]


def _rate_batch(src: Source, items: list[dict], budget: list[int], refused: dict) -> dict:
    """Ratings for `items`. A content-filter refusal splits the batch to find the moment responsible."""
    try:
        ratings, cost = moments_llm.rate(src.conn, registry, src.owner_id, src.ep.language, src.profile, src.brief, items)
        src.cost += cost
        return ratings
    except ContentFiltered as exc:
        if len(items) == 1:
            refused[items[0]["id"]] = str(exc)
            return {}
        if budget[0] <= 0:
            return {}
        budget[0] -= 1
        half = len(items) // 2
        return {**_rate_batch(src, items[:half], budget, refused), **_rate_batch(src, items[half:], budget, refused)}


def _patterns_for(src: Source, count: int) -> list[dict]:
    chosen = hook_patterns.pick(src.conn, src.ep.language, src.profile.name, count, f"{src.source['id']}:{src.profile.name}", src.owner_id)
    return [{"name": p["name"], "description": p["description"], "template": p["template"]} for p in chosen]


def _rate(src: Source, live: list[dict], weights: dict) -> dict[str, str]:
    """The LLM's view of the top candidates, blended in. Returns {id: reason} for moments its safety filter refused."""
    top = sorted(live, key=lambda c: -c["signal"])[:RATE_TOP]
    if not top:
        return {}
    patterns = _patterns_for(src, len(top))
    items = [{"id": c["id"], "start_s": c["start_s"], "end_s": c["end_s"], "text": src.ep.text(c["start_s"], c["end_s"]),
              "pattern": patterns[i % len(patterns)] if patterns else None} for i, c in enumerate(top)]
    refused: dict[str, str] = {}
    try:
        ratings = _rate_batch(src, items, [FILTER_CALL_BUDGET], refused)
    except KillSwitchOn:
        raise
    except (AppError, moments_llm.LLMAnswerError) as exc:
        src.llm_state = "unavailable"
        src.notes.append(f"The AI was not available for rating ({type(exc).__name__}); signals alone ranked the moments.")
        return {}
    if not ratings:
        src.llm_state = "unavailable" if not refused else "partial"
        return refused
    src.llm_state = "ok" if len(ratings) + len(refused) >= len(top) else "partial"
    pattern_of = {i["id"]: (i["pattern"] or {}).get("name") for i in items}
    for c in top:
        r = ratings.get(c["id"])
        if not r:
            continue
        merged, overall, notes = rank.apply_llm(src.ep, c["features"], c["evidence"], r, src.profile, c["start_s"], c["end_s"])
        soft = (1 - rank.LLM_SHARE) * rank.soft_score(merged, weights) + rank.LLM_SHARE * overall
        c["features"], c["llm"] = merged, r
        c["score"] = rank.adjust(soft, merged, c["include"], src.profile, src.brief["wants_info"])
        c["notes"] += notes
        if r.get("reason"):
            c["notes"].insert(0, r["reason"])
        hook = r.get("hook_text")
        text = src.ep.text(c["start_s"], c["end_s"])
        if hook and rank.paid_off(hook, text):
            c["hook"], c["hook_pattern"], c["hook_rating"] = hook, pattern_of.get(c["id"]), rank.hook_rating(hook)
    return refused


# ------------------------------------------------------------------ layer 3

def _compare(src: Source, live: list[dict]) -> None:
    top = sorted(live, key=lambda c: (-c["score"], -c["signal"]))[:COMPARE_TOP]
    top = [c for c in top if "llm" in c]
    if len(top) < 2 or src.llm_state == "unavailable":
        return
    ids = [c["id"] for c in top]
    texts = {c["id"]: src.ep.text(c["start_s"], c["end_s"]) for c in top}
    pairs = rank.pair_plan(ids, MAX_PAIRS)
    wins: list[tuple[str, str]] = []
    for order in (pairs, [(b, a) for a, b in pairs]):          # every pair, asked in both orders
        try:
            found, cost = moments_llm.compare(src.conn, registry, src.owner_id, src.ep.language, src.profile, texts, order)
        except KillSwitchOn:
            raise
        except (AppError, moments_llm.LLMAnswerError):
            src.llm_state = "partial"
            src.notes.append("The head-to-head comparison was only partly answered; the scores so far were kept.")
            continue
        src.cost += cost
        wins += found
    if not wins:
        return
    strength = rank.bradley_terry(ids, wins)
    low, high = min(strength.values()), max(strength.values())
    for c in top:
        norm = (strength[c["id"]] - low) / (high - low) if high - low > 1e-6 else 0.5
        c["score"] = round(0.5 * c["score"] + 0.5 * norm, 4)
        c["bt"] = round(strength[c["id"]], 4)
        c["notes"].append("Won its head-to-head comparisons" if norm >= 0.75 else "Lost head-to-head comparisons" if norm <= 0.25 else "Mixed head-to-head results")


# ------------------------------------------------------------------ the result

def _candidate(src: Source, c: dict, variants: dict | None = None) -> dict:
    variants = variants or {}
    reasons = list(dict.fromkeys(c.get("notes", []) + c.get("reasons", []) + rank.reasons_for(
        c.get("features", {}), c.get("evidence", {}), src.profile.weights)))[:5]
    if src.llm_state in ("unavailable",):
        reasons.append("Ranked on audio and transcript signals only: the AI was not available")
    out = {"start_s": c["start_s"], "end_s": c["end_s"], "variant": c.get("variant", "tight"), "variants": variants,
           "score": round(c["score"], 4), "rank": 0, "reasons": reasons, "features": c.get("features", {}),
           "source": c["source"], "engine": c["engine"], "style": src.profile.style(), "profile": src.profile.name}
    if c.get("hook"):
        out["hook"] = {"text": c["hook"], "pattern": c.get("hook_pattern"), "rating": c.get("hook_rating")}
    return out


def _choose_cut(src: Source, c: dict) -> tuple[dict, str]:
    """The three variants of a winner, and the default: tight when it fits the campaign's length limits (R3)."""
    lo, hi = src.length
    variants = rank.make_variants(src.ep, c["start_s"], c["end_s"], lo, hi)
    low, high = src.campaign_length
    for name in ("tight", "hook_first", "with_setup"):
        v = variants[name]
        if low - 0.01 <= v["end_s"] - v["start_s"] <= high + 0.01:
            return variants, name
    whole = {"start_s": c["start_s"], "end_s": c["end_s"]}
    return {k: dict(whole) for k in variants}, "tight"


def find(conn, source_id, campaign_version, profile=None) -> dict:
    """Pick the moments of a source. Returns {"kept": [Candidate], "filtered": [{"candidate", "reason", "layer"}], ...} and
    stores both in `candidates` (kept: status new, filtered: status filtered)."""
    src = _source(conn, source_id, campaign_version, profile)
    pool = _gather(src)
    for i, c in enumerate(pool, start=1):
        c["id"] = f"m{i}"
    live, gated = _gate(src, pool)             # gates first: a moment that cannot be used must not hide a usable duplicate
    live, merged = rank.dedupe(src.ep, live)
    weights = weights_for(conn, src.profile, src.owner_id)
    _features(src, live, weights)
    refused = _rate(src, live, weights)
    asides = [{**c, "filtered": (f"{refused[c['id']]}", 2)} for c in live if c["id"] in refused]
    live = [c for c in live if c["id"] not in refused]
    _compare(src, live)
    live.sort(key=lambda c: (-round(c["score"], 6), -c["signal"], c["start_s"]))
    target = src.profile.target(src.ep.duration)
    kept, why, cutoff = rank.select(live, src.profile, target)
    for c in live:
        if c not in kept:
            asides.append({**c, "filtered": (why[c["id"]], 2 if c["score"] < cutoff else 3)})
    result = _finish(src, kept, gated + asides, target, cutoff, merged)
    _store(src, result)
    _alerts(src, result, len(gated), len(pool))
    return result


def _finish(src: Source, kept: list[dict], aside: list[dict], target: int, cutoff: float, merged: int) -> dict:
    out_kept = []
    for position, c in enumerate(kept, start=1):
        variants, name = _choose_cut(src, c)
        cut = variants[name]
        cand = _candidate(src, {**c, "start_s": cut["start_s"], "end_s": cut["end_s"], "variant": name}, variants)
        cand["rank"] = position
        out_kept.append(cand)
    aside.sort(key=lambda c: (c["filtered"][1], -c.get("score", 0)))
    out_filtered = [{"candidate": _candidate(src, c), "reason": c["filtered"][0], "layer": c["filtered"][1]} for c in aside[:MAX_FILTERED]]
    lo, hi = src.length
    return {"kept": out_kept, "filtered": out_filtered, "cost": src.cost, "notes": src.notes, "route": src.engines,
            "llm": src.llm_state, "cutoff": cutoff, "merged": merged,
            "profile": {"name": src.profile.name, "label": src.profile.label, **src.profile.style(), "length": [lo, hi], "target": target}}


def _store(src: Source, result: dict) -> None:
    conn, row = src.conn, src.source
    conn.execute(
        """delete from candidates ca where ca.source_id = %s and ca.engine is not null and ca.status in ('new', 'filtered')
           and not exists (select 1 from clips cl where cl.candidate_id = ca.id)""", (row["id"],))
    for cand, reason, layer, status in [(c, None, None, "new") for c in result["kept"]] + \
            [(f["candidate"], f["reason"], f["layer"], "filtered") for f in result["filtered"]]:
        hook = cand.get("hook") or {}
        text = cand["reasons"][0] if cand["reasons"] else None
        if hook.get("text") and status == "new":
            text = f"{text} (Hook: {hook['text']})"
        inserted = conn.execute(
            """insert into candidates (source_id, owner_operator_id, provider, start_s, end_s, score, rank, reason, status, variant, variants,
                   features, reasons, origin, engine, profile, style, hook_text, hook_pattern, filtered_reason, filtered_layer)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (row["id"], row["owner_operator_id"], cand["source"], cand["start_s"], cand["end_s"], cand["score"], cand["rank"] or None, text,
             status, cand["variant"], Jsonb(cand["variants"]), Jsonb(cand["features"]), Jsonb(cand["reasons"]), cand["source"], cand["engine"],
             cand["profile"], Jsonb(cand["style"]), hook.get("text"), hook.get("pattern"), reason, layer)).fetchone()
        cand["id"] = str(inserted["id"])
        if status == "new" and hook.get("pattern"):
            hook_patterns.record(conn, hook["pattern"], src.ep.language, src.profile.name, "use", src.owner_id)


def _alerts(src: Source, result: dict, gated: int, pooled: int) -> None:
    conn, owner, sid = src.conn, src.owner_id, src.source["id"]
    name = src.campaign["name"]
    if not result["kept"] and pooled and gated == pooled:
        raise_alert(conn, owner, "moments_all_avoided", "warn", "source_assets", sid,
                    f"{name}: every moment found in this video breaks a hard rule (see Filtered in Review), so none was cut.")
    if result["kept"] and src.llm_state == "unavailable":
        raise_alert(conn, owner, "moments_signals_only", "info", "source_assets", sid,
                    f"{name}: the AI was not available, so these moments were ranked on audio and transcript signals only.")


# ------------------------------------------------------------------ the owner's side: Filtered and rescue

def filtered_list(conn, campaign_id=None) -> list[dict]:
    return conn.execute(
        """select ca.id, ca.source_id, ca.start_s, ca.end_s, ca.score, ca.filtered_reason as reason, ca.filtered_layer as layer,
                  ca.profile, ca.engine, ca.origin, c.name as campaign_name, c.id as campaign_id
           from candidates ca join source_assets s on s.id = ca.source_id join campaigns c on c.id = s.campaign_id
           where ca.status = 'filtered' and c.status = 'approved' and s.deleted_at is null and (%s::uuid is null or c.id = %s)
           order by ca.created_at desc, ca.score desc nulls last limit 40""", (campaign_id, campaign_id)).fetchall()


def rescue(conn, actor, candidate_id) -> dict:
    """The owner overrides the filter: the moment is cut like any other, and the rescue is a label the engine learns from."""
    if actor.kind != "operator":
        raise GateError("Only the owner can rescue a moment.")
    cand = conn.execute("select * from candidates where id = %s", (candidate_id,)).fetchone()
    if cand is None:
        raise AppError("Moment not found.", "not_found")
    if cand["status"] != "filtered":
        raise AppError("That moment is not set aside.", "not_filtered")
    campaign = conn.execute("select c.* from campaigns c join source_assets s on s.campaign_id = c.id where s.id = %s", (cand["source_id"],)).fetchone()
    if campaign["status"] != "approved":
        raise AppError("The campaign is not approved.", "bad_status")
    conn.execute(
        """update candidates set status = 'new', rank = (select coalesce(max(rank), 0) + 1 from candidates where source_id = %s),
               filtered_reason = null, filtered_layer = null, reason = coalesce(reason, 'Rescued by you') where id = %s""",
        (cand["source_id"], candidate_id))
    conn.execute(
        """insert into moment_labels (owner_operator_id, candidate_id, profile, engine, language, hook_pattern, kind, features)
           values (%s, %s, %s, %s, (select language from source_assets where id = %s), %s, 'rescue', %s) on conflict do nothing""",
        (cand["owner_operator_id"], candidate_id, cand["profile"] or profiles.GENERAL, cand["engine"], cand["source_id"],
         cand["hook_pattern"], Jsonb(cand["features"] or {})))
    if cand["engine"]:
        router.record(conn, cand["profile"] or profiles.GENERAL, cand["engine"], "win", cand["owner_operator_id"])
    audit(conn, "moment.rescued", "candidates", candidate_id, {"layer": cand["filtered_layer"]})
    enqueue(conn, "hooks", {"candidate_id": str(candidate_id)}, cand["owner_operator_id"], source_id=cand["source_id"],
            campaign_version_id=campaign["approved_version_id"])
    return conn.execute("select * from candidates where id = %s", (candidate_id,)).fetchone()


def set_profile(conn, actor, source_id, name: str | None) -> dict:
    """The owner's per-video choice of content profile (None goes back to the campaign niche). Used by the next run."""
    if name is not None and profiles.get(conn, name).name == profiles.GENERAL and name != profiles.GENERAL:
        raise AppError("Unknown content profile.", "unknown_profile")
    row = conn.execute("update source_assets set profile = %s where id = %s returning id, profile", (name, source_id)).fetchone()
    if row is None:
        raise AppError("That source does not exist.", "no_source")
    return row


__all__ = ["find", "rescue", "learn", "filtered_list", "set_profile", "weights_for"]
