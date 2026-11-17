"""Adapter contract and registry (spec section 10).

Every external service sits behind an adapter with health(), quota(), cost(request), config() and a declared
fallback. The business layer never calls a vendor directly: it calls registry.call(stage, method, ...), which
enforces the monthly cost cap, retries three times, runs the fallback, raises alerts and writes an audit row.
Plans, prices and limits are rows in the `adapters` table; keys are environment variables."""
from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal
from typing import Any, Callable

from .. import config
from ..core import AppError, audit, get_setting


class AdapterError(Exception):
    """A vendor call failed."""


# httpx logs request URLs at INFO. The Telegram URL contains the bot token, so keep it quiet.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def http_client(timeout: float = 60):
    import httpx

    return httpx.Client(timeout=timeout)


def check_response(response) -> dict:
    if response.status_code >= 400:
        # Status only: a response body or URL could echo a token.
        raise AdapterError(f"HTTP {response.status_code}")
    return response.json()


class AdapterPaused(AppError):
    def __init__(self, stage: str, reason: str):
        super().__init__(f"The {stage} adapter is paused: {reason}", "adapter_paused")


class AdapterUnavailable(AppError):
    def __init__(self, stage: str, reason: str):
        super().__init__(f"The {stage} stage needs manual work: {reason}", "adapter_unavailable")


class Adapter:
    stage: str = ""
    provider: str = ""
    requires_env: tuple[str, ...] = ()

    def __init__(self, row: dict):
        self.row = row
        self.cfg: dict = dict(row.get("config_json") or {})

    # --- common interface (spec 10.3)
    def health(self) -> dict:
        missing = [name for name in self.requires_env if not config.env(name)]
        return {"ok": not missing, "provider": self.provider, "missing_env": missing}

    def quota(self) -> dict:
        used, limit = self.row.get("quota_used") or 0, self.row.get("quota_limit")
        return {"used": used, "limit": limit, "left": None if limit is None else limit - used}

    def cost(self, request: dict) -> Decimal | None:
        """Cost of one call in USD. None means the price is unknown and must not be guessed."""
        return Decimal("0")

    def units(self, request: dict) -> Decimal:
        """Quota units one call uses (minutes, clips, channels...)."""
        return Decimal("0")

    def config(self) -> dict:
        return {"stage": self.stage, "provider": self.provider, "plan": self.row.get("plan"),
                "fallback": self.row.get("fallback_provider"), **self.cfg}


PROVIDERS: dict[tuple[str, str], type[Adapter]] = {}


def register(cls: type[Adapter]) -> type[Adapter]:
    PROVIDERS[(cls.stage, cls.provider)] = cls
    return cls


CAP_SETTING = {"llm": "adapter_cap_usd_llm", "transcription": "adapter_cap_usd_transcription",
               "moments": "adapter_cap_usd_moments", "database": "adapter_cap_usd_database"}


class Registry:
    """Looks up the active adapter per stage and wraps every call."""

    max_attempts = 3

    def __init__(self, alert_fn: Callable | None = None):
        self._alert_fn = alert_fn

    def row(self, conn, stage: str, provider: str | None = None) -> dict:
        if provider:
            row = conn.execute("select * from adapters where stage = %s and provider = %s", (stage, provider)).fetchone()
        else:
            row = conn.execute("select * from adapters where stage = %s and active", (stage,)).fetchone()
        if row is None:
            raise AdapterUnavailable(stage, "no adapter is switched on")
        return row

    def get(self, conn, stage: str, provider: str | None = None) -> Adapter:
        row = self.row(conn, stage, provider)
        cls = PROVIDERS.get((stage, row["provider"]))
        if cls is None:
            raise AdapterUnavailable(stage, f"provider {row['provider']} is not implemented")
        return cls(row)

    def _alert(self, conn, owner_id, type_: str, severity: str, entity_id, message: str) -> None:
        if self._alert_fn and owner_id:
            self._alert_fn(conn, owner_id, type_, severity, "adapters", entity_id, message)

    def _roll_month(self, conn, row: dict) -> dict:
        month = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
        if row["cost_month"] != month:
            row = conn.execute("select * from app.adapter_roll_month(%s, %s)", (row["id"], month)).fetchone()
        return row

    def call(self, conn, stage: str, method: str, request: dict, owner_id=None, provider: str | None = None) -> Any:
        """Call adapter.method(**request) with cap, retry, fallback, alert and audit handling.
        Returns (result, cost_usd)."""
        adapter = self.get(conn, stage, provider)
        row = self._roll_month(conn, adapter.row)
        adapter.row = row
        if row["paused_reason"]:
            raise AdapterPaused(stage, row["paused_reason"])

        cost = adapter.cost(request)
        cap_key = CAP_SETTING.get(stage)
        if cap_key and not row["is_fake"]:
            cap = Decimal(str(get_setting(conn, cap_key)))
            if cost is None:
                # Unknown price: never guess. The owner must set the price before a paid adapter runs.
                raise AdapterPaused(stage, "price is not configured, so the monthly cap cannot be enforced")
            if row["cost_month_usd"] + cost > cap:
                reason = f"monthly cap of ${cap} reached"
                conn.execute("select app.adapter_pause(%s, %s)", (row["id"], reason))
                self._alert(conn, owner_id, "adapter_cap", "block", row["id"],
                            f"{stage} adapter ({row['provider']}) paused: {reason}.")
                audit(conn, "adapter.paused", "adapters", row["id"], {"stage": stage, "reason": reason})
                raise AdapterPaused(stage, reason)
        cost = cost or Decimal("0")

        last_error = ""
        for attempt in range(1, self.max_attempts + 1):
            try:
                result = getattr(adapter, method)(**request)
            except AppError:
                raise
            except Exception as exc:  # vendor failure
                last_error = config.redact(f"{type(exc).__name__}: {exc}")[:300]
                continue
            units = adapter.units(request)
            row = conn.execute("select * from app.adapter_usage(%s, %s, %s)", (row["id"], units, cost)).fetchone()
            audit(conn, "adapter.call", "adapters", row["id"],
                  {"stage": stage, "provider": row["provider"], "method": method, "ok": True,
                   "attempt": attempt, "cost_usd": str(cost)})
            self._after_call_alerts(conn, owner_id, stage, row)
            return result, cost

        audit(conn, "adapter.call", "adapters", row["id"],
              {"stage": stage, "provider": row["provider"], "method": method, "ok": False, "error": last_error})
        fallback = row["fallback_provider"]
        self._alert(conn, owner_id, "adapter_failed", "warn", row["id"],
                    f"{stage} adapter ({row['provider']}) failed {self.max_attempts} times. Fallback: {fallback or 'none'}.")
        if fallback and (stage, fallback) in PROVIDERS and provider is None:
            return self.call(conn, stage, method, request, owner_id, provider=fallback)
        raise AdapterUnavailable(stage, f"{row['provider']} failed ({last_error}); fallback is {fallback or 'manual'}")

    def _after_call_alerts(self, conn, owner_id, stage: str, row: dict) -> None:
        alert_pct = Decimal(str(get_setting(conn, "adapter_alert_pct")))
        cap_key = CAP_SETTING.get(stage)
        if cap_key and not row["is_fake"]:
            cap = Decimal(str(get_setting(conn, cap_key)))
            if cap > 0 and row["cost_month_usd"] >= cap * alert_pct / 100:
                self._alert(conn, owner_id, "adapter_cost", "warn", row["id"],
                            f"{stage} adapter has used ${row['cost_month_usd']} of its ${cap} monthly cap.")
        if row["quota_limit"]:
            # Spec 13.4: adapter quota within 20% of its limit.
            if row["quota_used"] >= row["quota_limit"] * Decimal("0.8"):
                self._alert(conn, owner_id, "adapter_quota", "warn", row["id"],
                            f"{stage} adapter ({row['provider']}) has used {row['quota_used']} of {row['quota_limit']}.")


def activate(conn, stage: str, provider: str) -> None:
    """Switch the active provider for a stage. A configuration change, audit-logged by trigger."""
    target = conn.execute("select * from adapters where stage = %s and provider = %s", (stage, provider)).fetchone()
    if target is None:
        raise AppError(f"No adapter {provider} for stage {stage}.", "unknown_adapter")
    if provider == "opusclip" and not (target["config_json"] or {}).get("quoted"):
        raise AppError("OpusClip stays off until a Business quote is recorded (open item O10).", "needs_quote")
    conn.execute("update adapters set active = false where stage = %s and active", (stage,))
    conn.execute("update adapters set active = true, paused_reason = null where id = %s", (target["id"],))
