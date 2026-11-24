"""Upload progress: where each recent source video is in the pipeline, for the Home progress cards.
Derived from jobs, candidates and clips; nothing is stored. Four cheap queries, owner-scoped."""
from __future__ import annotations

import datetime as dt
import re
from urllib.parse import unquote, urlparse

from .db import Actor

STAGES = ("uploaded", "transcribing", "moments", "clips", "ready")
ACTIVE_WINDOW_H = 24      # unfinished and failed videos stay on Home this long
READY_WINDOW_MIN = 60     # a finished video shows "Ready" this long
def _video_name(origin_url: str | None) -> str | None:
    """'upload:talk.mp4' -> 'talk.mp4'; a link -> its last path segment; otherwise nothing worth showing."""
    if not origin_url:
        return None
    if origin_url.startswith("upload:"):
        return origin_url[7:] or None
    segment = unquote(urlparse(origin_url).path.rsplit("/", 1)[-1])
    return segment or None


def _reason(error: str | None) -> str:
    """Job errors are stored as 'AppError: message'; the owner only needs the message."""
    text = re.sub(r"^[A-Za-z_][\w.]*: ", "", (error or "").strip())
    return text or "The job stopped without a reason. Check Alerts."


def _step(stage: str, running: dict | None, planned: int, done: int, clips_started, now) -> dict:
    """What is measured about the current step: `pct` (0 to 100) and `eta_s` are None when nothing real is known, and `detail` says
    what the numbers mean. Nothing here is a guess: the numbers come from the running job (seconds transcribed, seconds
    encoded) and from the speed this video's own clips have been made at."""
    live = (running or {}).get("progress") or {}
    share, eta = live.get("share"), live.get("eta_s")
    detail = None
    if stage == "transcribing" and share is not None:
        detail = f"{round(live['done'] / 60)} of {max(round(live['total'] / 60), 1)} min of the video heard"
    if stage == "clips" and planned:
        part = share if share is not None else 0.0
        pct = round(100 * min((done + part) / planned, 1))
        if done >= 1 and clips_started is not None:
            spent = (now - clips_started).total_seconds()
            eta = round(spent / (done + part) * (planned - done - part)) if spent > 0 else None
        detail = f"{done} of {planned} clips made"
        return {"pct": pct, "eta_s": eta, "detail": detail}
    return {"pct": None if share is None else round(100 * share), "eta_s": eta, "detail": detail}


def _stage(by_type: dict[str, set[str]], planned: int, done: int) -> str:
    """The earliest step that is not finished. `by_type` maps job type -> the statuses its jobs have."""
    if "transcribe" not in by_type:
        return "uploaded"
    if by_type["transcribe"] != {"done"}:
        return "transcribing"
    if by_type.get("moments") != {"done"}:
        return "moments"
    pending = any(by_type.get(t, set()) & {"queued", "running"} for t in ("hooks", "render", "captions", "compliance", "package"))
    return "clips" if pending or done < planned else "ready"


def overview(conn, actor: Actor) -> dict:
    """Spec: GET /api/progress. `items` are newest first, unfinished and failed ahead of ready ones."""
    now = dt.datetime.now(dt.timezone.utc)
    owner = actor.operator_id   # an admin's RLS reads everything, so scope to the signed-in owner explicitly
    sources = conn.execute(
        """select s.id, s.campaign_id, s.origin_url, s.duration_s, s.created_at, c.name, c.is_practice
           from source_assets s join campaigns c on c.id = s.campaign_id
           where s.owner_operator_id = %s and s.deleted_at is null and s.created_at > now() - make_interval(hours => %s)""",
        (owner, ACTIVE_WINDOW_H)).fetchall()
    ids = [s["id"] for s in sources]
    jobs = conn.execute(
        """select source_id, type, status, count(*) as n, max(updated_at) as last,
                  (array_agg(error order by updated_at desc) filter (where status = 'failed'))[1] as error,
                  (array_agg(progress order by updated_at desc) filter (where status = 'running' and progress is not null))[1] as progress,
                  min(created_at) as first_at
           from jobs where owner_operator_id = %s and source_id = any(%s) and status <> 'cancelled'
           group by source_id, type, status""", (owner, ids)).fetchall() if ids else []
    counts = conn.execute(
        """select s.id as source_id,
             (select count(*) from candidates ca where ca.source_id = s.id and ca.status <> 'discarded'
                and exists (select 1 from jobs j where j.type = 'hooks' and j.source_id = s.id
                            and j.payload->>'candidate_id' = ca.id::text and j.status <> 'cancelled')) as planned,
             (select count(*) from clips cl join candidates ca on ca.id = cl.candidate_id
               where ca.source_id = s.id and cl.version_no = 1 and cl.status <> 'rendering') as done,
             (select count(*) from candidates ca where ca.source_id = s.id and ca.status <> 'filtered') as moments
           from source_assets s where s.id = any(%s)""", (ids,)).fetchall() if ids else []
    fetches = conn.execute(
        """select j.id, j.status, j.error, j.created_at, j.updated_at, c.id as campaign_id, c.name, c.is_practice
           from jobs j left join campaigns c on c.id = (j.payload->>'campaign_id')::uuid
           where j.owner_operator_id = %s and j.type = 'fetch_source' and j.source_id is null
             and j.status in ('queued', 'running', 'failed') and j.created_at > now() - make_interval(hours => %s)""",
        (owner, ACTIVE_WINDOW_H)).fetchall()

    per_source: dict = {}
    for row in jobs:
        entry = per_source.setdefault(row["source_id"], {"types": {}, "last": None, "error": None, "error_at": None, "running": {}, "clips_at": None})
        entry["types"].setdefault(row["type"], set()).add(row["status"])
        if row["progress"] is not None:
            entry["running"][row["type"]] = {"progress": row["progress"]}
        if row["type"] in ("hooks", "render"):
            entry["clips_at"] = min(filter(None, (entry["clips_at"], row["first_at"])), default=None)
        entry["last"] = max(filter(None, (entry["last"], row["last"])), default=None)
        if row["status"] == "failed" and (entry["error_at"] is None or row["last"] > entry["error_at"]):
            entry["error"], entry["error_at"] = row["error"], row["last"]
    by_id = {row["source_id"]: row for row in counts}

    items = []
    for s in sources:
        info = per_source.get(s["id"], {"types": {}, "last": None, "error": None, "running": {}, "clips_at": None})
        n = by_id.get(s["id"], {"planned": 0, "done": 0, "moments": 0})
        planned, done = int(n["planned"]), int(n["done"])
        failed = any("failed" in statuses for statuses in info["types"].values())
        stage = _stage(info["types"], planned, done)
        finished_at = info["last"] or s["created_at"]
        running = next(iter(info["running"].values()), None) if stage in ("transcribing", "moments", "clips") else None
        step = _step(stage, running, planned, done, info["clips_at"], now)
        if not failed and stage == "ready" and now - finished_at > dt.timedelta(minutes=READY_WINDOW_MIN):
            continue
        items.append({
            "id": str(s["id"]), "source_id": str(s["id"]), "campaign_id": str(s["campaign_id"]), "name": s["name"],
            "video": _video_name(s["origin_url"]), "is_practice": bool(s["is_practice"]),
            "state": "failed" if failed else "ready" if stage == "ready" else "active", "stage": stage,
            "clips_done": done, "clips_planned": planned,
            "eta_s": None if failed or stage == "ready" else step["eta_s"], "step_pct": None if failed or stage == "ready" else step["pct"],
            "detail": None if failed or stage == "ready" else step["detail"],
            "error": _reason(info["error"]) if failed else None,
            "note": "No moments were found in this video. Pick them by hand in Review." if stage == "ready" and not n["moments"] else None,
            "started_at": s["created_at"], "updated_at": finished_at,
        })
    for f in fetches:   # a link still being fetched has no source yet
        failed = f["status"] == "failed"
        items.append({
            "id": f"fetch-{f['id']}", "source_id": None, "campaign_id": str(f["campaign_id"]) if f["campaign_id"] else None,
            "name": f["name"] or "Video link", "video": None, "is_practice": bool(f["is_practice"]),
            "state": "failed" if failed else "active", "stage": "uploaded", "clips_done": 0, "clips_planned": 0,
            "eta_s": None, "step_pct": None, "detail": None, "error": _reason(f["error"]) if failed else None,
            "note": None, "started_at": f["created_at"], "updated_at": f["updated_at"],
        })
    items.sort(key=lambda i: (i["state"] == "ready", -i["started_at"].timestamp()))
    return {"items": items, "active": any(i["state"] == "active" for i in items), "stages": list(STAGES)}
