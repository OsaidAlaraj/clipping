"""Core services: errors, audit rows, settings, kill switch, and the idempotent job queue."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from psycopg.types.json import Jsonb


class AppError(Exception):
    """An expected refusal (a rule, a gate, a cap). The API returns it as a 4xx with this message."""
    status = 400

    def __init__(self, message: str, code: str = "refused"):
        super().__init__(message)
        self.code = code


class GateError(AppError):
    status = 403


class KillSwitchOn(AppError):
    status = 423

    def __init__(self, message: str = "The kill switch is on."):
        super().__init__(message, "kill_switch")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_hash(obj: Any) -> str:
    return sha256_text(json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False))


# ------------------------------------------------------------------ audit

def audit(conn, action: str, entity_type: str, entity_id=None, detail: dict | None = None, rule_version_id=None) -> None:
    """Explicit audit row for an action that is not a plain row change (gates, adapter calls, rechecks).
    Row changes are audited automatically by a database trigger. Detail holds small facts only."""
    kind = conn.execute(
        "select case when current_user = 'app_worker' then 'worker' when app.operator_id() is not null then 'operator' else 'system' end as k"
    ).fetchone()["k"]
    conn.execute(
        """insert into audit_log (actor_id, actor_kind, action, entity_type, entity_id, rule_version_id, detail)
           values (app.operator_id(), %s, %s, %s, %s, %s, %s)""",
        (kind, action, entity_type, entity_id, rule_version_id, Jsonb(detail) if detail is not None else None),
    )


# ------------------------------------------------------------------ settings

def get_setting(conn, key: str, scope_id=None) -> Any:
    """Scoped value if one exists for scope_id, otherwise the base value."""
    row = conn.execute(
        """select value from settings where key = %s and (scope_id = %s or scope_id is null)
           order by (scope_id is null) limit 1""",
        (key, scope_id),
    ).fetchone()
    if row is None:
        raise AppError(f"Unknown setting: {key}", "unknown_setting")
    return row["value"]


def get_settings(conn, scope_id=None) -> list[dict]:
    return conn.execute(
        """select distinct on (key) key, value, "default", min, max, allowed, scope, scope_id
           from settings where scope_id is null or scope_id = %s
           order by key, (scope_id is null)""",
        (scope_id,),
    ).fetchall()


def set_setting(conn, key: str, value: Any, scope_id=None) -> dict:
    """Change a setting. Unknown keys are refused, so no setting can be invented to override a hard rule.
    Bounds are enforced by a database trigger; the change is audit-logged by the audit trigger."""
    base = conn.execute("select * from settings where key = %s and scope_id is null", (key,)).fetchone()
    if base is None:
        raise AppError(f"Unknown setting: {key}. Settings cannot add or override hard rules.", "unknown_setting")
    if scope_id is not None and base["scope"] == "system":
        raise AppError(f"{key} is a system setting and has no per-item value.", "bad_scope")
    try:
        with conn.transaction():
            if scope_id is None:
                row = conn.execute(
                    "update settings set value = %s where id = %s returning *", (Jsonb(value), base["id"])
                ).fetchone()
            else:
                row = conn.execute(
                    """insert into settings (key, value, "default", min, max, allowed, scope, scope_id)
                       values (%s, %s, %s, %s, %s, %s, %s, %s)
                       on conflict (key, coalesce(scope_id, '00000000-0000-0000-0000-000000000000'::uuid))
                       do update set value = excluded.value returning *""",
                    (key, Jsonb(value), Jsonb(base["default"]), base["min"], base["max"],
                     Jsonb(base["allowed"]) if base["allowed"] is not None else None, base["scope"], scope_id),
                ).fetchone()
    except Exception as exc:  # trigger refusal (out of bounds) or RLS refusal
        message = str(getattr(getattr(exc, "diag", None), "message_primary", None) or exc)
        raise AppError(message, "setting_refused") from exc
    if row is None:
        raise AppError("You are not allowed to change this setting.", "setting_refused")
    return row


# ------------------------------------------------------------------ kill switch

def kill_switch(conn) -> dict:
    return conn.execute("select active, set_by, set_at, reason from kill_switch limit 1").fetchone()


def kill_switch_on(conn) -> bool:
    return bool(conn.execute("select app.kill_switch_on() as on").fetchone()["on"])


def set_kill_switch(conn, active: bool, reason: str | None = None) -> dict:
    row = conn.execute(
        "update kill_switch set active = %s, set_by = app.operator_id(), set_at = now(), reason = %s returning *",
        (active, reason),
    ).fetchone()
    if row is None:
        raise GateError("Only an admin can change the kill switch.")
    if active:
        # Stop work already queued. Running jobs check the switch before each stage.
        conn.execute("update jobs set status = 'cancelled', error = 'kill switch' where status = 'queued'")
    return row


def require_kill_switch_off(conn) -> None:
    if kill_switch_on(conn):
        raise KillSwitchOn()


# ------------------------------------------------------------------ jobs

def enqueue(conn, job_type: str, payload: dict, owner_operator_id, source_id=None, campaign_version_id=None) -> dict | None:
    """Add a job. Idempotent on type + input hash: the same input never creates a second job.
    Returns the new row, or None if the job already exists. Refused while the kill switch is on."""
    require_kill_switch_off(conn)
    input_hash = stable_hash(payload)
    return conn.execute(
        """insert into jobs (type, source_id, campaign_version_id, input_hash, idempotency_key, payload, owner_operator_id)
           values (%s, %s, %s, %s, %s, %s, %s)
           on conflict (idempotency_key) do nothing returning *""",
        (job_type, source_id, campaign_version_id, input_hash, f"{job_type}:{input_hash}", Jsonb(payload), owner_operator_id),
    ).fetchone()


def claim_job(conn) -> dict | None:
    if kill_switch_on(conn):
        return None
    return conn.execute(
        """update jobs set status = 'running', attempts = attempts + 1
           where id = (select j.id from jobs j
                         left join campaign_versions v on v.id = j.campaign_version_id
                         left join campaigns c on c.id = v.campaign_id
                         left join listings l on l.id = c.listing_id
                       where j.status = 'queued' and j.run_after <= now()
                       -- Work approved campaigns while their budget is fresh: the one running out soonest first.
                       order by l.drain_p50 asc nulls last, j.created_at
                       for update of j skip locked limit 1)
           returning *"""
    ).fetchone()


def finish_job(conn, job_id, cost_usd=0) -> None:
    conn.execute("update jobs set status = 'done', cost_usd = cost_usd + %s, error = null where id = %s", (cost_usd, job_id))


def fail_job(conn, job_id, error: str, retry: bool, cost_usd=0) -> None:
    from .config import redact

    conn.execute(
        """update jobs set status = %s, error = %s, cost_usd = cost_usd + %s,
                  run_after = now() + interval '5 seconds' where id = %s""",
        ("queued" if retry else "failed", redact(error)[:500], cost_usd, job_id),
    )
