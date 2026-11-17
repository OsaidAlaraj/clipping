"""Tracking, payouts and time (FR-50 to FR-54, spec 13 and 14)."""
from __future__ import annotations

import csv
import datetime as dt
import io
import statistics
from decimal import Decimal

from .alerts import raise_alert
from .core import AppError, audit

TERMINAL = ("paid", "rejected", "capped", "budget_exhausted")


def compute_amounts(views: int, version: dict, withdrawal_fee_usd=None) -> dict:
    """Net = gross − creator fee − withdrawal fee. An unknown creator fee leaves fee and net empty."""
    rate = version.get("rate_per_1k")
    min_views = version.get("min_views_to_qualify") or 0
    below = views < min_views
    if rate is None:
        return {"gross_usd": None, "fee_usd": None, "net_usd": None, "below_min_views": below, "cap_reached": False}
    gross = Decimal("0") if below else (Decimal(views) / 1000 * Decimal(str(rate)))
    cap = version.get("max_payout_per_clip")
    cap_reached = cap is not None and gross >= Decimal(str(cap))
    if cap_reached:
        gross = Decimal(str(cap))
    gross = gross.quantize(Decimal("0.01"))
    fee_pct = version.get("fee_pct")
    fee = None if fee_pct is None else (gross * Decimal(str(fee_pct)) / 100).quantize(Decimal("0.01"))
    net = None if fee is None else gross - fee - Decimal(str(withdrawal_fee_usd or 0))
    return {"gross_usd": gross, "fee_usd": fee, "net_usd": net, "below_min_views": below, "cap_reached": cap_reached}


def record_views(conn, actor, post_id, window: str, views: int, watch_time_s=None, source: str = "manual") -> dict:
    """A11: a metrics snapshot (never edited). Also refreshes the clip's payout figures."""
    if window not in ("h48", "d7"):
        raise AppError("Window must be h48 or d7.", "bad_window")
    post = conn.execute("select * from posts where id = %s", (post_id,)).fetchone()
    if post is None:
        raise AppError("Post not found.", "not_found")
    metric = conn.execute(
        """insert into metrics (post_id, owner_operator_id, "window", views, watch_time_s, source)
           values (%s, %s, %s, %s, %s, %s) returning *""",
        (post_id, post["owner_operator_id"], window, int(views), watch_time_s, source),
    ).fetchone()
    payout = conn.execute("select * from payouts where clip_id = %s", (post["clip_id"],)).fetchone()
    if payout and payout["status"] not in TERMINAL:
        version = conn.execute("select * from campaign_versions where id = %s", (payout["campaign_version_id"],)).fetchone()
        best = conn.execute("select max(views) as v from metrics where post_id = %s", (post_id,)).fetchone()["v"]
        amounts = compute_amounts(int(best), version, payout["withdrawal_fee_usd"])
        conn.execute(
            """update payouts set gross_usd = %s, fee_usd = %s, net_usd = %s, below_min_views = %s,
                   notes = case when %s then 'Per-clip maximum reached' else notes end where id = %s""",
            (amounts["gross_usd"], amounts["fee_usd"], amounts["net_usd"], amounts["below_min_views"],
             amounts["cap_reached"], payout["id"]),
        )
        if payout["status"] == "submitted":
            conn.execute("update payouts set status = 'tracking' where id = %s", (payout["id"],))
    return metric


def set_payout_status(conn, actor, payout_id, status: str, withdrawal_fee_usd=None, note: str | None = None) -> dict:
    """A12: manual reconciliation. The allowed order is enforced by a database trigger."""
    try:
        with conn.transaction():
            row = conn.execute(
                """update payouts set status = %s, withdrawal_fee_usd = coalesce(%s, withdrawal_fee_usd),
                       net_usd = case when fee_usd is null then null
                                      else gross_usd - fee_usd - coalesce(%s, withdrawal_fee_usd, 0) end,
                       notes = coalesce(%s, notes) where id = %s returning *""",
                (status, withdrawal_fee_usd, withdrawal_fee_usd, note, payout_id),
            ).fetchone()
    except Exception as exc:
        message = str(getattr(getattr(exc, "diag", None), "message_primary", None) or exc)
        raise AppError(message, "bad_payout_status") from exc
    if row is None:
        raise AppError("Payout not found.", "not_found")
    return row


def mark_disputed(conn, actor, payout_id, note: str | None = None) -> dict:
    row = conn.execute("update payouts set disputed = true, notes = coalesce(%s, notes) where id = %s returning *",
                       (note, payout_id)).fetchone()
    if row is None:
        raise AppError("Payout not found.", "not_found")
    raise_alert(conn, row["owner_operator_id"], "payout_disputed", "warn", "payouts", row["id"],
                "A payout is marked disputed. Keep the 48-hour and 7-day screenshots.")
    return row


LEDGER_SQL = """
    select p.id as payout_id, p.status, p.gross_usd, p.fee_usd, p.withdrawal_fee_usd, p.net_usd, p.below_min_views, p.disputed,
           p.held_since, p.paid_at, p.notes, cl.id as clip_id, cl.language, c.id as campaign_id, c.name as campaign_name,
           cat.name as niche, m.name as marketplace, po.id as post_id, po.platform, po.post_url, po.posted_at,
           a.id as account_id, a.handle as account_handle, po.confirmed_by as operator_id, v.fee_pct,
           (select views from metrics where post_id = po.id and "window" = 'h48' order by checked_at desc limit 1) as views_h48,
           (select views from metrics where post_id = po.id and "window" = 'd7' order by checked_at desc limit 1) as views_d7,
           v.min_views_to_qualify
    from payouts p
    join clips cl on cl.id = p.clip_id
    join campaigns c on c.id = cl.campaign_id
    join campaign_versions v on v.id = p.campaign_version_id
    join marketplaces m on m.id = c.marketplace_id
    left join categories cat on cat.id = c.category_id
    join posts po on po.clip_id = cl.id
    join accounts a on a.id = po.account_id
"""


def ledger(conn, month: str | None = None) -> list[dict]:
    if month:
        return conn.execute(LEDGER_SQL + " where to_char(po.posted_at, 'YYYY-MM') = %s order by po.posted_at", (month,)).fetchall()
    return conn.execute(LEDGER_SQL + " order by po.posted_at desc").fetchall()


def export_csv(conn, month: str) -> str:
    """FR-54: one CSV per month."""
    rows = ledger(conn, month)
    columns = ["posted_at", "campaign_name", "marketplace", "niche", "language", "platform", "account_handle", "post_url",
               "views_h48", "views_d7", "status", "gross_usd", "fee_usd", "withdrawal_fee_usd", "net_usd", "below_min_views",
               "disputed", "held_since", "paid_at", "notes"]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if row[c] is None else row[c] for c in columns])
    audit(conn, "ledger.export", "payouts", None, {"month": month, "rows": len(rows)})
    return buffer.getvalue()


# ------------------------------------------------------------------ analytics (spec 14)

def _money(row: dict) -> Decimal:
    """Net when the fee is known; otherwise gross, flagged as fee-unknown by the caller."""
    return Decimal(str(row["net_usd"] if row["net_usd"] is not None else row["gross_usd"] or 0))


def confidence(first_post: dt.datetime | None, now: dt.datetime | None = None) -> str:
    """Low until 30 days of data, medium until 90, high after (spec 14.4)."""
    if first_post is None:
        return "low"
    days = ((now or dt.datetime.now(dt.timezone.utc)) - first_post).days
    return "low" if days < 30 else "medium" if days < 90 else "high"


def net_per_hour(conn, since: dt.datetime | None = None, until: dt.datetime | None = None) -> dict:
    """Net pay per hour = net earned ÷ logged hours. Paid and pending are shown separately.
    Hours logged against a campaign are split evenly across its posted clips for the per-language and
    per-account views; hours with no campaign count only in the overall and per-operator figures."""
    since = since or dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)
    until = until or dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
    rows = [r for r in ledger(conn) if since <= r["posted_at"] < until]
    entries = conn.execute(
        "select operator_id, campaign_id, coalesce(minutes, 0) as minutes from time_entries where started_at >= %s and started_at < %s",
        (since, until),
    ).fetchall()
    total_hours = sum(float(e["minutes"]) for e in entries) / 60
    campaign_hours: dict = {}
    operator_hours: dict = {}
    for e in entries:
        operator_hours[e["operator_id"]] = operator_hours.get(e["operator_id"], 0) + float(e["minutes"]) / 60
        if e["campaign_id"]:
            campaign_hours[e["campaign_id"]] = campaign_hours.get(e["campaign_id"], 0) + float(e["minutes"]) / 60
    clips_per_campaign: dict = {}
    for r in rows:
        clips_per_campaign[r["campaign_id"]] = clips_per_campaign.get(r["campaign_id"], 0) + 1

    def bucket() -> dict:
        return {"paid_usd": Decimal("0"), "pending_usd": Decimal("0"), "hours": 0.0, "clips": 0, "fee_unknown": False}

    def add(group: dict, key, label, row: dict, hours: float) -> None:
        b = group.setdefault(str(key), dict(bucket(), label=label))
        if row["status"] == "paid":
            b["paid_usd"] += _money(row)
        elif row["status"] not in ("rejected", "budget_exhausted"):
            b["pending_usd"] += _money(row)
        b["clips"] += 1
        b["hours"] += hours
        b["fee_unknown"] = b["fee_unknown"] or (row["fee_usd"] is None and row["gross_usd"] is not None)

    groups: dict = {name: {} for name in ("campaign", "niche", "language", "account", "operator")}
    overall = dict(bucket(), label="Overall")
    for r in rows:
        share = campaign_hours.get(r["campaign_id"], 0) / clips_per_campaign[r["campaign_id"]]
        add(groups["campaign"], r["campaign_id"], r["campaign_name"], r, share)
        add(groups["niche"], r["niche"], r["niche"], r, share)
        add(groups["language"], r["language"], r["language"], r, share)
        add(groups["account"], r["account_id"], f"@{r['account_handle']} ({r['platform']})", r, share)
        add(groups["operator"], r["operator_id"], str(r["operator_id"]), r, 0)
        add({"x": overall}, "x", "Overall", r, 0)
    overall["hours"] = total_hours
    for key, b in groups["operator"].items():
        b["hours"] = next((h for op, h in operator_hours.items() if str(op) == key), 0.0)

    def finish(b: dict) -> dict:
        hours = b["hours"]
        b["hours"] = round(hours, 2)
        b["paid_per_hour"] = round(float(b["paid_usd"]) / hours, 2) if hours > 0 else None
        b["with_pending_per_hour"] = round(float(b["paid_usd"] + b["pending_usd"]) / hours, 2) if hours > 0 else None
        return b

    first = min((r["posted_at"] for r in ledger(conn)), default=None)
    posted = len(rows)
    cleared = sum(1 for r in rows if max(r["views_d7"] or 0, r["views_h48"] or 0) >= (r["min_views_to_qualify"] or 0)
                  and (r["views_d7"] is not None or r["views_h48"] is not None))
    return {
        "overall": finish(overall), "confidence": confidence(first),
        "by": {name: [finish(b) for b in group.values()] for name, group in groups.items()},
        "clips_posted": posted, "clips_clearing_threshold": cleared,
        "clearing_rate": round(cleared / posted, 3) if posted else None,
    }


def weekly_median_net_per_hour(conn, weeks: int = 8) -> dict:
    """Median of weekly net pay per hour, calculated from the ledger (spec 14.2, 15.6)."""
    now = dt.datetime.now(dt.timezone.utc)
    start_of_week = (now - dt.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    values, detail = [], []
    for i in range(weeks):
        since = start_of_week - dt.timedelta(weeks=i)
        week = net_per_hour(conn, since, since + dt.timedelta(weeks=1))["overall"]
        if week["hours"] > 0:
            values.append(week["with_pending_per_hour"])
            detail.append({"week_start": since.date().isoformat(), "net_per_hour": week["with_pending_per_hour"],
                           "paid_per_hour": week["paid_per_hour"], "hours": week["hours"]})
    median = round(statistics.median(values), 2) if values else None
    return {"median_net_per_hour": median, "weeks": detail, "decision": go_decision(conn, median)}


def rejection_stats(conn) -> dict:
    rows = conn.execute(
        """select r.decision, r.reason_code, count(*) as n from reviews r
           where r.decision in ('accept', 'reject') and not exists (select 1 from reviews u where u.undo_of = r.id)
           group by r.decision, r.reason_code"""
    ).fetchall()
    accepted = sum(r["n"] for r in rows if r["decision"] == "accept")
    rejected = sum(r["n"] for r in rows if r["decision"] == "reject")
    total = accepted + rejected
    return {"accepted": accepted, "rejected": rejected, "rejection_rate": round(rejected / total, 3) if total else None,
            "reasons": {r["reason_code"]: r["n"] for r in rows if r["decision"] == "reject"}}


def go_decision(conn, median: float | None) -> str:
    """Go, iterate or stop (spec 14.2). A framework from the owner's own target, not market data."""
    if median is None:
        return "not enough data"
    rejection = rejection_stats(conn)["rejection_rate"] or 0
    if median < 2 or rejection > 0.30:
        return "stop"
    return "go" if median >= 4.60 else "iterate"


def hook_win_rates(conn) -> list[dict]:
    """Median 48-hour views by hook type, relative to the niche median (spec 14.1)."""
    rows = conn.execute(
        """select h.hook_type, cat.name as niche, m.views
           from hooks h join clips cl on cl.candidate_id = h.candidate_id and h.chosen
           join posts p on p.clip_id = cl.id join metrics m on m.post_id = p.id and m."window" = 'h48'
           join campaigns c on c.id = cl.campaign_id left join categories cat on cat.id = c.category_id"""
    ).fetchall()
    niche_views: dict = {}
    for r in rows:
        niche_views.setdefault(r["niche"], []).append(r["views"])
    by_type: dict = {}
    for r in rows:
        niche_median = statistics.median(niche_views[r["niche"]])
        by_type.setdefault(r["hook_type"], []).append(r["views"] / niche_median if niche_median else 0)
    return [{"hook_type": k, "relative_median": round(statistics.median(v), 2), "clips": len(v)} for k, v in sorted(by_type.items())]
