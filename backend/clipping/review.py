"""Review: the priority-ranked feed, accept, reject, undo, review later, edits and sessions
(FR-30 to FR-36, spec 6.3 to 6.7)."""
from __future__ import annotations

import datetime as dt
import math
import uuid
from decimal import Decimal
from statistics import NormalDist

from psycopg.types.json import Jsonb

from . import quality_gate, ranking
from .alerts import registry
from .core import AppError, GateError, audit, enqueue, get_setting, kill_switch_on

REASON_CODES = {
    "weak_hook": "Weak hook", "cut_mid_thought": "Cut mid-thought", "wrong_context": "Wrong context",
    "caption_error": "Caption error (name, term or language)", "audio_problem": "Audio problem",
    "framing_problem": "Framing problem", "copyright_or_music_risk": "Copyright or music risk",
    "disclosure_problem": "Disclosure problem", "outside_campaign_rules": "Outside campaign rules",
    "duplicate": "Duplicate", "other": "Other (note required)",
}
WEIGHT_KEYS = ["net_pay", "deadline", "hook_score", "budget", "account_cap", "compliance"]
UNDO_GRACE_S = 5   # network allowance on top of the Undo window
MOMENT_SKILL = 0.35   # share of a clip's view spread that the quality of its moment explains (the rest is the account and luck)
BASIS_TEXT = {"your posts": "from your own posts", "assumed": "assumed: no view history yet"}


# ------------------------------------------------------------------ priority (spec 6.6). Pure functions.

def expected_rate(clip: dict) -> float:
    """The `net_pay` basis: the campaign's net rate per 1,000 views times the clip's chance of clearing the campaign's view minimum
    (`quality.p_clear`, from `chance_to_clear`). A campaign with no minimum, or a clip without an estimate, counts as a sure 1.0, so
    a clip paying twice as much with a 30% chance ranks below a sure one that pays 1.5 times as much. Deterministic: same inputs, same order."""
    p = (clip.get("quality") or {}).get("p_clear")
    return float(clip.get("est_net_per_hour") or 0) * (1.0 if p is None else min(max(float(p), 0.0), 1.0))


def priority_inputs(clip: dict, now: dt.datetime, max_rate: float) -> dict:
    """Normalise each input to 0..1 (or 0..-1 for the two that push a clip down)."""
    rate = expected_rate(clip)
    deadline = clip.get("deadline")
    hours_left = (deadline - now).total_seconds() / 3600 if deadline else None
    total, remaining = clip.get("budget_total"), clip.get("budget_remaining")
    cap, posted = clip.get("account_cap") or 0, clip.get("account_posts_today") or 0
    return {
        "net_pay": rate / max_rate if max_rate > 0 else 0.0,
        "deadline": 0.0 if hours_left is None else min(max(1 - hours_left / (14 * 24), 0.0), 1.0),
        "hook_score": float(clip.get("hook_score") or 0),
        "budget": 0.0 if not total or remaining is None else min(max(1 - float(remaining) / float(total), 0.0), 1.0),
        "account_cap": -min(posted / cap, 1.0) if cap else 0.0,
        "compliance": -1.0 if clip.get("compliance_status") == "review" else 0.0,
        "_hours_left": hours_left,
    }


def top_reason(inputs: dict, weights: dict) -> str:
    contributions = {k: inputs[k] * float(weights.get(k, 0)) for k in WEIGHT_KEYS}
    negative = min(contributions, key=contributions.get)
    positive = max(contributions, key=contributions.get)
    if contributions[positive] <= 0 and contributions[negative] < 0:
        return {"account_cap": "Lower: account is near its daily cap", "compliance": "Lower: needs a compliance look"}[negative]
    hours = inputs.get("_hours_left")
    return {
        "net_pay": "Top-paying campaign",
        "deadline": f"Deadline in {int(hours)} h" if hours is not None and hours < 72 else "Deadline approaching",
        "hook_score": "Strong hook",
        "budget": "Campaign budget is running low",
        "account_cap": "Account has room today",
        "compliance": "Clean compliance report",
    }[positive] if contributions[positive] > 0 else "Oldest first"


def rank_feed(clips: list[dict], weights: dict, now: dt.datetime | None = None) -> list[dict]:
    """Order clips for the feed. Hard blocks are removed first and ranking never brings them back.
    Clips marked weak by `quality` go last. Ties break by creation time, oldest first. Each clip gets its score and one-line top reason."""
    now = now or dt.datetime.now(dt.timezone.utc)
    eligible = [c for c in clips if c.get("compliance_status") in ("pass", "review")]
    max_rate = max((expected_rate(c) for c in eligible), default=0.0)
    for clip in eligible:
        inputs = priority_inputs(clip, now, max_rate)
        clip["priority_score"] = round(sum(inputs[k] * float(weights.get(k, 0)) for k in WEIGHT_KEYS), 6)
        clip["priority_reason"] = top_reason(inputs, weights)
    # A clip under the quality threshold sinks below every other clip, whatever it pays; nothing is hidden.
    return sorted(eligible, key=lambda c: (bool((c.get("quality") or {}).get("weak")), -c["priority_score"], c["created_at"]))


def weights_for(conn, operator_id) -> dict:
    return {k: float(get_setting(conn, f"priority_weight_{k}", operator_id)) for k in WEIGHT_KEYS}


# ------------------------------------------------------------------ quality: the chance a clip clears the campaign minimum

def moment_percentile(score, scores: list[float]) -> float | None:
    """Where a moment's score sits among the scores of its source's moments, 0..1 (ties count half). None when unknown."""
    if score is None or not scores:
        return None
    score = float(score)
    return (sum(s < score for s in scores) + 0.5 * sum(s == score for s in scores)) / len(scores)


def chance_to_clear(min_views, percentile: float | None, stats: dict) -> float | None:
    """P(views >= min_views) for one clip. The account's views are log-normal around its median; a better moment than usual
    shifts the median up by MOMENT_SKILL of the account's spread per standard deviation of moment quality, a weaker one down.
    No minimum on the campaign -> None."""
    if not min_views or float(min_views) <= 0:
        return None
    q = min(max(0.5 if percentile is None else percentile, 0.02), 0.98)
    sigma = max(float(stats["sigma"]), 0.05)
    mu = math.log(max(float(stats["median"]), 1.0)) + MOMENT_SKILL * sigma * NormalDist().inv_cdf(q)
    return 1 - NormalDist(mu, sigma * math.sqrt(1 - MOMENT_SKILL ** 2)).cdf(math.log(float(min_views)))


def quality_label(p: float | None, min_views, basis: str, weak: bool) -> str:
    """One plain sentence saying what the chance is and where it comes from (the web builds the Arabic text from the numbers)."""
    if p is None:
        return "No view minimum on this campaign"
    pct = "under 1%" if p < 0.01 else "over 99%" if p > 0.99 else f"{round(p * 100)}%"
    text = f"About {pct} chance to reach {int(float(min_views)):,} views ({BASIS_TEXT.get(basis, basis)})"
    return f"Weak: {text[0].lower()}{text[1:]}. Shown last." if weak else text


def _annotate_quality(conn, rows: list[dict], operator_id) -> None:
    """Adds `quality` {p_clear, label, basis, weak, min_views, moment_percentile, threshold} to each clip. Account stats are worked
    out once per account, the source's moment scores once per source."""
    threshold = float(get_setting(conn, "quality_min_p_clear", operator_id))
    sources = list({r["source_id"] for r in rows if r.get("source_id")})
    scores: dict = {}
    if sources:
        for r in conn.execute("select source_id, score from candidates where source_id = any(%s) and status <> 'discarded' and score is not null",
                              (sources,)).fetchall():
            scores.setdefault(r["source_id"], []).append(float(r["score"]))
    ids = list({r["account_id"] for r in rows if r.get("account_id")})
    accounts = {a["id"]: a for a in conn.execute("select * from accounts where id = any(%s)", (ids,)).fetchall()} if ids else {}
    stats: dict = {}
    for row in rows:
        key = row.get("account_id")
        if key not in stats:
            stats[key] = ranking.account_stats(conn, accounts.get(key) or {"id": key})
        q = moment_percentile(row.pop("moment_score", None), scores.get(row.get("source_id"), []))
        min_views = row.get("min_views_to_qualify")
        p = chance_to_clear(min_views, q, stats[key])
        gate = (row.get("render_meta") or {}).get("gate")
        weak = (p is not None and p < threshold) or bool(gate and gate.get("status") == "fail")   # a failed quality gate sinks the clip too
        row["quality"] = {"p_clear": None if p is None else round(p, 3), "label": quality_label(p, min_views, stats[key]["basis"], weak),
                          "basis": stats[key]["basis"], "weak": weak, "gate": gate,
                          "swipe_blockers": [b["code"] for b in quality_gate.swipe_blockers(gate)], "min_views": int(float(min_views)) if min_views else None,
                          "moment_percentile": None if q is None else round(q, 3), "threshold": threshold}


CLIP_VIEW = """
    select cl.id, cl.status, cl.created_at, cl.version_no, cl.duration_s, cl.caption_text, cl.hashtags, cl.mentions,
           cl.language, cl.dialect, cl.compliance_status, cl.compliance_report, cl.file_path, cl.file_hash, cl.platform,
           cl.hook_score, cl.park_reason, cl.due_at, cl.render_meta, cl.campaign_id, cl.account_id, cl.start_s, cl.end_s,
           cl.candidate_id, cl.approved_file_hash, cl.campaign_version_id, cl.skip_count, cl.skip_anchor, cl.skipped_at,
           (select source_id from candidates where id = cl.candidate_id) as source_id,
           c.name as campaign_name, c.deadline, c.status as campaign_status, c.is_practice,
           (select reason from candidates where id = cl.candidate_id) as moment_reason,
           (select score from candidates where id = cl.candidate_id) as moment_score, v.min_views_to_qualify,
           v.budget_total, v.budget_remaining, v.rate_per_1k, v.fee_pct, v.max_payout_per_clip,
           a.handle as account_handle, a.daily_cap as account_daily_cap,
           (select count(*) from posts p where p.account_id = cl.account_id and p.posted_at::date = now()::date) as account_posts_today
    from clips cl
    join campaigns c on c.id = cl.campaign_id
    join campaign_versions v on v.id = cl.campaign_version_id
    left join accounts a on a.id = cl.account_id
"""


def _decorate(conn, rows: list[dict], operator_id=None) -> list[dict]:
    _annotate_quality(conn, rows, operator_id)
    storage = registry.get(conn, "storage")
    default_cap = int(get_setting(conn, "posts_per_account_per_day"))
    for row in rows:
        cap = row.pop("account_daily_cap")
        row["account_cap"] = default_cap if cap is None else cap
        # Ranking proxy until the owner's own data exists: the campaign's net rate per 1,000 views (`rank_feed` multiplies it by `quality.p_clear`).
        rate = Decimal(str(row["rate_per_1k"] or 0))
        row["est_net_per_hour"] = float(rate * (1 - Decimal(str(row["fee_pct"] or 0)) / 100))
        row["video_url"] = storage.get_signed_url(row["file_path"], 900) if row["file_path"] else None
        cover = (row.get("render_meta") or {}).get("cover_key")
        row["cover_url"] = storage.get_signed_url(cover, 900) if cover else None
        row["hook_text"] = (row.get("render_meta") or {}).get("hook_text")
        row.pop("min_views_to_qualify", None)
        row["hooks"] = conn.execute(
            "select id, text, hook_type, chosen, rank, score, rating from hooks where candidate_id = %s order by rank", (row["candidate_id"],)
        ).fetchall()
    return rows


SKIP_GAP = 5          # a skipped clip comes back after about this many other clips
MAX_RETURNS = 2       # it comes back at most twice; the third skip moves it to Review Later


def place_skipped(ranked: list[dict]) -> list[dict]:
    """Skipped clips leave their ranked place and sit after the clip they were skipped behind (else after SKIP_GAP clips).
    Clips skipped earlier are placed first, so a later skip can land behind an earlier one."""
    out = [c for c in ranked if not c.get("skip_count")]
    movers = sorted((c for c in ranked if c.get("skip_count")), key=lambda c: c.get("skipped_at") or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
    for clip in movers:
        ids = [str(c["id"]) for c in out]
        anchor = str(clip["skip_anchor"]) if clip.get("skip_anchor") else None
        out.insert(ids.index(anchor) + 1 if anchor in ids else min(SKIP_GAP, len(out)), clip)
    return out


def feed(conn, operator_id, campaign_id=None) -> list[dict]:
    """Recalculated on every load. Review Later clips are not here; hard-blocked clips never are."""
    if kill_switch_on(conn):
        return []
    rows = conn.execute(
        CLIP_VIEW + """ where cl.status = 'ready_for_review' and c.status = 'approved'
            and cl.compliance_status in ('pass', 'review') and (%s::uuid is null or cl.campaign_id = %s)""",
        (campaign_id, campaign_id),
    ).fetchall()
    return place_skipped(rank_feed(_decorate(conn, rows, operator_id), weights_for(conn, operator_id)))


def review_later(conn) -> list[dict]:
    """Grouped by campaign; folders for closed campaigns sit at the bottom."""
    rows = _decorate(conn, conn.execute(CLIP_VIEW + " where cl.status = 'review_later' order by cl.updated_at desc").fetchall())
    folders: dict = {}
    for row in rows:
        folder = folders.setdefault(row["campaign_id"], {
            "campaign_id": row["campaign_id"], "campaign_name": row["campaign_name"], "deadline": row["deadline"],
            "closed": row["campaign_status"] in ("closed", "rejected"), "clips": []})
        folder["clips"].append(row)
    out = list(folders.values())
    for folder in out:
        folder["count"] = len(folder["clips"])
    return sorted(out, key=lambda f: (f["closed"], f["deadline"] or dt.datetime.max.replace(tzinfo=dt.timezone.utc)))


def get_clip(conn, clip_id) -> dict:
    rows = conn.execute(CLIP_VIEW + " where cl.id = %s", (clip_id,)).fetchall()
    if not rows:
        raise AppError("Clip not found.", "not_found")
    return _decorate(conn, rows)[0]


# ------------------------------------------------------------------ decisions

def _existing(conn, client_action_id):
    if not client_action_id:
        return None
    return conn.execute("select * from reviews where client_action_id = %s", (client_action_id,)).fetchone()


def _log(conn, actor, clip_id, decision, reason_code=None, notes=None, undo_of=None, session_id=None, client_action_id=None) -> dict:
    return conn.execute(
        """insert into reviews (clip_id, owner_operator_id, reviewer_id, decision, reason_code, notes, undo_of, session_id, client_action_id)
           values (%s, (select owner_operator_id from clips where id = %s), %s, %s, %s, %s, %s, %s, %s) returning *""",
        (clip_id, clip_id, actor.operator_id, decision, reason_code, notes, undo_of, session_id, client_action_id),
    ).fetchone()


def _bump(conn, session_id, column: str) -> None:
    if session_id:
        conn.execute(f"update sessions set clips_reviewed = clips_reviewed + 1, {column} = {column} + 1 where id = %s and ended_at is null",
                     (session_id,))


def accept(conn, actor, clip_id, session_id=None, client_action_id=None) -> dict:
    """Gate 2: clip acceptance. Records the chosen hook and moves the clip to the Queue."""
    if actor.kind != "operator":
        raise GateError("Clip acceptance needs a human.")
    if (done := _existing(conn, client_action_id)):
        return done
    clip = get_clip(conn, clip_id)
    if clip["status"] not in ("ready_for_review", "review_later"):
        raise AppError(f"A clip that is {clip['status']} cannot be accepted.", "bad_status")
    if clip["compliance_status"] == "fail":
        raise GateError("This clip failed compliance.")
    updated = conn.execute("update clips set status = 'accepted', approved_file_hash = file_hash, park_reason = null where id = %s returning id",
                           (clip_id,)).fetchone()
    if updated is None:
        raise GateError("You cannot accept this clip.")
    hook_id = (clip.get("render_meta") or {}).get("hook_id")
    conn.execute("update hooks set chosen = coalesce(id::text = %s, false) where candidate_id = %s", (hook_id, clip["candidate_id"]))
    review = _log(conn, actor, clip_id, "accept", session_id=session_id, client_action_id=client_action_id)
    _bump(conn, session_id, "accepted")
    audit(conn, "gate.clip_acceptance", "clips", clip_id, {"hook_id": hook_id, "review_id": str(review["id"])},
          rule_version_id=clip["campaign_version_id"])
    return review


def rate_hook(conn, hook_id, value: str) -> dict:
    """The owner's thumb on one hook ("up", "down" or "none"). The first thumb teaches the hook library: an up counts as an approval
    and a down as a rejection of the hook's format for this language (a changed mind only changes the thumb, never counts twice)."""
    from . import hook_patterns

    marks = {"up": 1, "down": -1, "none": None}
    if value not in marks:
        raise AppError("The thumb must be up, down or none.", "bad_rating")
    hook = conn.execute("select h.id, h.rating, h.pattern, h.hook_type, h.owner_operator_id, coalesce(c.language, 'en') as language "
                        "from hooks h left join clips c on c.id = h.clip_id where h.id = %s", (hook_id,)).fetchone()
    if hook is None:
        raise AppError("Hook not found.", "not_found")
    conn.execute("update hooks set rating = %s where id = %s", (marks[value], hook_id))
    if hook["rating"] is None and marks[value] is not None:
        hook_patterns.record(conn, hook["pattern"] or hook["hook_type"], hook["language"], "default", "approve" if value == "up" else "reject",
                             hook["owner_operator_id"])
    return {"id": str(hook["id"]), "rating": marks[value]}


def reject(conn, actor, clip_id, reason_code: str, notes: str | None = None, session_id=None, client_action_id=None) -> dict:
    """A rejection needs a reason code; "other" needs a note (FR-31)."""
    if (done := _existing(conn, client_action_id)):
        return done
    if reason_code not in REASON_CODES:
        raise AppError("Pick a reason for the rejection.", "reason_required")
    if reason_code == "other" and not (notes or "").strip():
        raise AppError("“Other” needs a note.", "note_required")
    clip = get_clip(conn, clip_id)
    if clip["status"] not in ("ready_for_review", "review_later"):
        raise AppError(f"A clip that is {clip['status']} cannot be rejected.", "bad_status")
    if conn.execute("update clips set status = 'rejected' where id = %s returning id", (clip_id,)).fetchone() is None:
        raise GateError("You cannot reject this clip.")
    review = _log(conn, actor, clip_id, "reject", reason_code, notes, session_id=session_id, client_action_id=client_action_id)
    _bump(conn, session_id, "rejected")
    return review


def park(conn, actor, clip_id, reason: str | None = None, session_id=None, client_action_id=None) -> dict:
    """Review later: neither accepted nor rejected."""
    if (done := _existing(conn, client_action_id)):
        return done
    clip = get_clip(conn, clip_id)
    if clip["status"] not in ("ready_for_review", "accepted", "ready_to_post"):
        raise AppError(f"A clip that is {clip['status']} cannot be moved to Review Later.", "bad_status")
    if conn.execute("update clips set status = 'review_later', park_reason = %s where id = %s returning id",
                    (reason or "Parked for later", clip_id)).fetchone() is None:
        raise GateError("You cannot park this clip.")
    review = _log(conn, actor, clip_id, "review_later", notes=reason, session_id=session_id, client_action_id=client_action_id)
    if clip["status"] == "ready_for_review":
        _bump(conn, session_id, "parked")
    return review


def skip(conn, actor, clip_id, anchor_id: str | None = None, session_id=None) -> dict:
    """The owner swiped past a clip without deciding. It comes back after about SKIP_GAP other clips, at most MAX_RETURNS times;
    the next skip moves it to Review Later. A clip that was decided in the meantime is left alone."""
    clip = get_clip(conn, clip_id)
    if clip["status"] != "ready_for_review":
        return {"clip_id": str(clip["id"]), "skipped": False, "parked": False, "status": clip["status"]}
    count = int(clip["skip_count"]) + 1
    if count > MAX_RETURNS:
        park(conn, actor, clip_id, "Skipped three times", session_id)
        return {"clip_id": str(clip["id"]), "skipped": True, "parked": True, "skip_count": count}
    try:
        anchor = str(uuid.UUID(str(anchor_id))) if anchor_id and str(anchor_id) != str(clip["id"]) else None
    except ValueError:
        anchor = None
    conn.execute("update clips set skip_count = %s, skip_anchor = %s, skipped_at = now() where id = %s", (count, anchor, clip_id))
    return {"clip_id": str(clip["id"]), "skipped": True, "parked": False, "skip_count": count}


def undo(conn, actor, review_id, client_action_id=None) -> dict:
    """Undo a reject (or an accept or park) inside the Undo window. The undo row references the original."""
    if (done := _existing(conn, client_action_id)):
        return done
    original = conn.execute("select * from reviews where id = %s", (review_id,)).fetchone()
    if original is None or original["decision"] == "undo":
        raise AppError("Nothing to undo.", "not_found")
    if conn.execute("select 1 from reviews where undo_of = %s", (review_id,)).fetchone():
        raise AppError("This decision was already undone.", "already_undone")
    window = float(get_setting(conn, "undo_window_s", actor.operator_id))
    age = (dt.datetime.now(dt.timezone.utc) - original["created_at"]).total_seconds()
    if age > window + UNDO_GRACE_S:
        raise AppError(f"The {int(window)}-second Undo window has passed.", "undo_expired")
    clip = conn.execute("select status from clips where id = %s", (original["clip_id"],)).fetchone()
    expected = {"reject": "rejected", "accept": "accepted", "review_later": "review_later"}[original["decision"]]
    if clip["status"] != expected:
        raise AppError("The clip has moved on and cannot be restored.", "bad_status")
    conn.execute("update clips set status = 'ready_for_review', park_reason = null, approved_file_hash = null where id = %s",
                 (original["clip_id"],))
    if original["decision"] == "accept":
        conn.execute("update hooks set chosen = false where clip_id = %s or candidate_id = (select candidate_id from clips where id = %s)",
                     (original["clip_id"], original["clip_id"]))
    review = _log(conn, actor, original["clip_id"], "undo", undo_of=review_id, session_id=original["session_id"],
                  client_action_id=client_action_id)
    if original["session_id"]:
        column = {"reject": "rejected", "accept": "accepted", "review_later": "parked"}[original["decision"]]
        conn.execute(f"update sessions set clips_reviewed = greatest(clips_reviewed - 1, 0), {column} = greatest({column} - 1, 0) where id = %s",
                     (original["session_id"],))
    return review


# ------------------------------------------------------------------ edits (FR-34): every edit is a new clip version

def edit(conn, actor, clip_id, trim: dict | None = None, hook_id=None, caption: str | None = None) -> dict:
    """A refused edit (for example one that drops the disclosure) is rolled back: the original clip stays as it was."""
    with conn.transaction():
        return _edit(conn, actor, clip_id, trim, hook_id, caption)


def _edit(conn, actor, clip_id, trim: dict | None = None, hook_id=None, caption: str | None = None) -> dict:
    clip = conn.execute("select * from clips where id = %s", (clip_id,)).fetchone()
    if clip is None:
        raise AppError("Clip not found.", "not_found")
    if clip["status"] not in ("ready_for_review", "review_later"):
        raise AppError(f"A clip that is {clip['status']} cannot be edited.", "bad_status")
    if not (trim or hook_id or caption is not None):
        raise AppError("Nothing to change.", "no_change")
    meta = dict(clip["render_meta"] or {})
    start, end = float(clip["start_s"]), float(clip["end_s"])
    hook_score = clip["hook_score"]
    if trim:
        start, end = float(trim.get("start_s", start)), float(trim.get("end_s", end))
        if end - start < 1:
            raise AppError("The trimmed clip is too short.", "bad_trim")
    if hook_id:
        hook = conn.execute("select * from hooks where id = %s and candidate_id = %s", (hook_id, clip["candidate_id"])).fetchone()
        if hook is None:
            raise AppError("That hook does not belong to this clip.", "bad_hook")
        meta.update(hook_id=str(hook["id"]), hook_text=hook["text"])
        hook_score = hook["score"]
    needs_render = bool(trim or hook_id)
    if caption is not None:
        meta["caption_edited"] = True
    elif hook_id:
        meta.pop("caption_edited", None)   # the caption is rebuilt around the new hook
    new = conn.execute(
        """insert into clips (candidate_id, campaign_id, campaign_version_id, owner_operator_id, parent_clip_id, version_no,
               start_s, end_s, file_path, file_hash, duration_s, width, height, caption_text, hashtags, mentions, language, dialect,
               overlay_json, render_meta, account_id, platform, hook_score, due_at, status)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'rendering')
           returning *""",
        (clip["candidate_id"], clip["campaign_id"], clip["campaign_version_id"], clip["owner_operator_id"], clip["id"],
         clip["version_no"] + 1, start, end,
         None if needs_render else clip["file_path"], None if needs_render else clip["file_hash"],
         None if needs_render else clip["duration_s"], clip["width"], clip["height"],
         caption if caption is not None else clip["caption_text"], clip["hashtags"], clip["mentions"], clip["language"],
         clip["dialect"], None if needs_render else Jsonb(clip["overlay_json"]), Jsonb(meta), clip["account_id"], clip["platform"],
         hook_score, clip["due_at"]),
    ).fetchone()
    conn.execute("update clips set status = 'archived' where id = %s", (clip["id"],))
    audit(conn, "clip.edited", "clips", new["id"],
          {"from_version": clip["version_no"], "trim": bool(trim), "hook": bool(hook_id), "caption": caption is not None},
          rule_version_id=clip["campaign_version_id"])
    if needs_render:
        source_id = conn.execute("select source_id from candidates where id = %s", (clip["candidate_id"],)).fetchone()["source_id"]
        enqueue(conn, "render", {"clip_id": str(new["id"])}, clip["owner_operator_id"], source_id=source_id,
                campaign_version_id=clip["campaign_version_id"])
    else:
        from . import pipeline

        campaign = conn.execute("select * from campaigns where id = %s", (clip["campaign_id"],)).fetchone()
        version = conn.execute("select * from campaign_versions where id = %s", (clip["campaign_version_id"],)).fetchone()
        report, _ = pipeline.run_compliance(conn, new, campaign, version)
        conn.execute("update clips set status = %s where id = %s",
                     ("archived" if report["status"] == "fail" else "ready_for_review", new["id"]))
        if report["status"] == "fail":
            failed = [c["check"] for c in report["deterministic"] if c["status"] == "fail"]
            raise AppError(f"The edit fails compliance ({', '.join(failed) or 'campaign rule'}). It was not saved to the feed.",
                           "compliance_fail")
    return conn.execute("select * from clips where id = %s", (new["id"],)).fetchone()


# ------------------------------------------------------------------ sessions and timer (D17, FR-52)

def start_session(conn, actor, campaign_id=None) -> dict:
    conn.execute("update sessions set ended_at = now() where operator_id = %s and ended_at is null", (actor.operator_id,))
    conn.execute("update time_entries set ended_at = now(), minutes = extract(epoch from now() - started_at) / 60 "
                 "where operator_id = %s and ended_at is null and task_type = 'review'", (actor.operator_id,))
    length = get_setting(conn, "session_length_min", actor.operator_id)
    session = conn.execute(
        "insert into sessions (operator_id, owner_operator_id, length_setting) values (%s, %s, %s) returning *",
        (actor.operator_id, actor.operator_id, length),
    ).fetchone()
    conn.execute(
        "insert into time_entries (operator_id, owner_operator_id, task_type, campaign_id, session_id) values (%s, %s, 'review', %s, %s)",
        (actor.operator_id, actor.operator_id, campaign_id, session["id"]),
    )
    last = conn.execute(
        "select position_json from sessions where operator_id = %s and position_json is not null order by ended_at desc nulls last limit 1",
        (actor.operator_id,),
    ).fetchone()
    session["resume_position"] = last["position_json"] if last else None
    return session


def stop_session(conn, actor, session_id, position: dict | None = None) -> dict:
    """End the session and return its summary: clips reviewed, accepted, rejected, posts due, hours logged."""
    session = conn.execute(
        "update sessions set ended_at = coalesce(ended_at, now()), position_json = coalesce(%s, position_json) where id = %s returning *",
        (Jsonb(position) if position is not None else None, session_id),
    ).fetchone()
    if session is None:
        raise AppError("Session not found.", "not_found")
    conn.execute(
        """update time_entries set ended_at = %s, minutes = round((extract(epoch from %s - started_at) / 60)::numeric, 2)
           where session_id = %s and ended_at is null""",
        (session["ended_at"], session["ended_at"], session_id),
    )
    return session_summary(conn, actor, session)


def session_summary(conn, actor, session: dict) -> dict:
    posts_due = conn.execute("select count(*) as n from clips where status in ('accepted', 'ready_to_post', 'queued')").fetchone()["n"]
    minutes = conn.execute("select coalesce(sum(minutes), 0) as m from time_entries where session_id = %s", (session["id"],)).fetchone()["m"]
    today = conn.execute(
        "select coalesce(sum(minutes), 0) as m from time_entries where operator_id = %s and started_at::date = now()::date",
        (actor.operator_id,),
    ).fetchone()["m"]
    summary = {"session_id": session["id"], "clips_reviewed": session["clips_reviewed"], "accepted": session["accepted"],
               "rejected": session["rejected"], "parked": session["parked"], "posts_due": posts_due,
               "minutes_logged": float(minutes), "hours_today": round(float(today) / 60, 2),
               "length_setting": float(session["length_setting"])}
    if get_setting(conn, "pay_per_clip_display", actor.operator_id) in ("summary", "card"):
        summary["pay_per_clip"] = conn.execute(
            """select c.name, v.rate_per_1k, v.max_payout_per_clip from reviews r join clips cl on cl.id = r.clip_id
               join campaigns c on c.id = cl.campaign_id join campaign_versions v on v.id = cl.campaign_version_id
               where r.session_id = %s and r.decision = 'accept' group by c.name, v.rate_per_1k, v.max_payout_per_clip""",
            (session["id"],),
        ).fetchall()
    return summary


def timer_start(conn, actor, task_type: str, campaign_id=None, clip_id=None) -> dict:
    conn.execute(
        "update time_entries set ended_at = now(), minutes = round((extract(epoch from now() - started_at) / 60)::numeric, 2) "
        "where operator_id = %s and ended_at is null and session_id is null", (actor.operator_id,))
    return conn.execute(
        """insert into time_entries (operator_id, owner_operator_id, task_type, campaign_id, clip_id)
           values (%s, %s, %s, %s, %s) returning *""",
        (actor.operator_id, actor.operator_id, task_type, campaign_id, clip_id),
    ).fetchone()


def timer_stop(conn, actor, entry_id=None, minutes: float | None = None) -> dict | None:
    """Stop the running timer, or correct an entry's minutes in one tap."""
    if entry_id and minutes is not None:
        return conn.execute("update time_entries set minutes = %s, ended_at = coalesce(ended_at, now()) where id = %s returning *",
                            (minutes, entry_id)).fetchone()
    return conn.execute(
        """update time_entries set ended_at = now(), minutes = round((extract(epoch from now() - started_at) / 60)::numeric, 2)
           where operator_id = %s and ended_at is null and (%s::uuid is null or id = %s) returning *""",
        (actor.operator_id, entry_id, entry_id),
    ).fetchone()
