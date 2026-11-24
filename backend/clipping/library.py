"""The accepted-clips library (owner notes 13): the clips the owner accepted, as a scroll of reels, each with the numbers collected
for it (views at 48 hours and 7 days, watch time, what it earned). Grouped and filtered by campaign name. Read only.

Filters and sorting run on the server over the owner's own rows (a few hundred at most), so the page never sends the whole list
again on each keystroke-free change."""
from __future__ import annotations

from .alerts import registry

# A clip is in the library once the owner accepted it: accepted, then ready, queued or posted.
LIBRARY_STATUSES = ("accepted", "ready_to_post", "queued", "posted")
SORTS = ("newest", "views", "earned", "campaign")

LIBRARY_SQL = """
    select cl.id, cl.status, cl.updated_at, cl.duration_s, cl.platform, cl.file_path, cl.render_meta,
           cl.campaign_id, c.name as campaign_name, c.is_practice,
           (cl.render_meta->>'hook_text') as hook_text,
           po.post_url, po.posted_at,
           (select views from metrics m where m.post_id = po.id and m."window" = 'h48' order by checked_at desc limit 1) as views_h48,
           (select views from metrics m where m.post_id = po.id and m."window" = 'd7' order by checked_at desc limit 1) as views_d7,
           (select watch_time_s from metrics m where m.post_id = po.id order by checked_at desc limit 1) as watch_time_s,
           p.net_usd, p.status as payout_status
    from clips cl
    join campaigns c on c.id = cl.campaign_id
    left join lateral (select * from posts where clip_id = cl.id order by posted_at desc nulls last limit 1) po on true
    left join payouts p on p.clip_id = cl.id
    where cl.status = any(%s) and not c.is_practice
"""


def select_clips(rows: list[dict], campaign_ids: set[str] | None = None, q: str = "", sort: str = "newest") -> list[dict]:
    """Pure: filter by campaign ids and by text (campaign name or hook), then sort. Unknown sorts fall back to newest."""
    needle = (q or "").strip().lower()
    out = []
    for row in rows:
        if campaign_ids and str(row["campaign_id"]) not in campaign_ids:
            continue
        if needle and needle not in f"{row.get('campaign_name') or ''} {row.get('hook_text') or ''}".lower():
            continue
        out.append(row)
    if sort == "views":
        out.sort(key=lambda r: (r.get("views_h48") is None, -(r.get("views_h48") or 0)))
    elif sort == "earned":
        out.sort(key=lambda r: (r.get("net_usd") is None, -float(r.get("net_usd") or 0)))
    elif sort == "campaign":
        out.sort(key=lambda r: (str(r.get("campaign_name") or "").lower(), -(r["updated_at"].timestamp() if r.get("updated_at") else 0)))
    else:
        out.sort(key=lambda r: -(r["updated_at"].timestamp() if r.get("updated_at") else 0))
    return out


def campaign_list(rows: list[dict]) -> list[dict]:
    """Every campaign that has library clips, with how many: the picker's choices (not filtered, so the picker never empties)."""
    counts: dict[str, dict] = {}
    for row in rows:
        entry = counts.setdefault(str(row["campaign_id"]), {"id": str(row["campaign_id"]), "name": row["campaign_name"], "count": 0})
        entry["count"] += 1
    return sorted(counts.values(), key=lambda c: c["name"].lower())


def listing(conn, campaign_ids: list[str] | None = None, q: str = "", sort: str = "newest") -> dict:
    rows = conn.execute(LIBRARY_SQL, (list(LIBRARY_STATUSES),)).fetchall()
    storage = registry.get(conn, "storage") if rows else None
    for row in rows:
        row["video_url"] = storage.get_signed_url(row["file_path"], 900) if row["file_path"] else None
        cover = (row.get("render_meta") or {}).get("cover_key")
        row["cover_url"] = storage.get_signed_url(cover, 900) if cover else None
        row.pop("render_meta", None)
        row.pop("file_path", None)
    wanted = set(campaign_ids) if campaign_ids else None
    chosen = select_clips(rows, wanted, q, sort if sort in SORTS else "newest")
    return {"clips": chosen, "campaigns": campaign_list(rows), "total": len(rows), "sort": sort if sort in SORTS else "newest"}
