"""The moment router: engines that only propose moments, a route per content profile, and a simple bandit that shifts
how many slots of the candidate pool each engine gets as the owner's decisions come in.

An engine is any object with `name`, `available(conn)` and `propose(source, profile) -> list[dict]` registered with
`register`. Engines never decide: every candidate they return goes through the same filter in `moments.find`.
Every engine on a route keeps at least MIN_SHARE of the pool, so a weak engine is still tried now and then and no engine
becomes the fixed answer."""
from __future__ import annotations

from typing import Protocol

MIN_SHARE = 0.10         # the least share of the pool any engine on a route gets
POOL_SLOTS = 24          # how many proposals the route may contribute in total
FALLBACK_ENGINE = "own"
OUTCOMES = ("win", "loss", "try")


class Engine(Protocol):
    name: str

    def available(self, conn) -> bool: ...
    def propose(self, source, profile) -> list[dict]: ...


ENGINES: dict[str, Engine] = {}


def register(cls):
    """Class decorator: one instance of the engine class is registered under its name (like adapters)."""
    ENGINES[cls.name] = cls()
    return cls


def route(conn, profile) -> list[str]:
    """The engines this profile's route names that are registered and available right now (no key, no engine).
    An empty result falls back to our own engine, which needs nothing."""
    names = [n for n in profile.route if n in ENGINES and ENGINES[n].available(conn)]
    return names or [FALLBACK_ENGINE]


# ------------------------------------------------------------------ the bandit

def owner_of(conn, owner_id):
    if owner_id:
        return owner_id
    row = conn.execute(
        "select coalesce(app.operator_id(), (select id from operators where role = 'admin' order by created_at limit 1)) as id").fetchone()
    return row["id"]


def record(conn, profile, engine: str, outcome: str, owner_id=None) -> None:
    """Count one result for an engine under a profile: 'win' (the owner kept its pick or it earned views), 'loss'
    (rejected or few views) or 'try' (it was asked)."""
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}")
    name = getattr(profile, "name", profile)
    wins, losses = int(outcome == "win"), int(outcome == "loss")
    conn.execute(
        """insert into engine_stats (owner_operator_id, profile, engine, tries, wins, losses) values (%s, %s, %s, %s, %s, %s)
           on conflict (owner_operator_id, profile, engine) do update set tries = engine_stats.tries + excluded.tries,
               wins = engine_stats.wins + excluded.wins, losses = engine_stats.losses + excluded.losses""",
        (owner_of(conn, owner_id), name, engine, wins + losses + int(outcome == "try"), wins, losses))


def floored(raw: dict[str, float], floor: float) -> dict[str, float]:
    """Shares that sum to 1 with none below `floor`: the low ones are lifted and the rest scaled down to pay for it."""
    total = sum(raw.values())
    shares = {k: v / total for k, v in raw.items()}
    floor = min(floor, 1 / len(shares))
    lifted: set[str] = set()
    for _ in range(len(shares)):
        low = {k for k, v in shares.items() if v < floor and k not in lifted}
        if not low:
            break
        lifted |= low
        rest = [k for k in shares if k not in lifted]
        room = 1 - floor * len(lifted)
        base = sum(shares[k] for k in rest) or 1.0
        shares = {k: (floor if k in lifted else shares[k] / base * room) for k in shares}
    return shares


def shares(conn, profile, engines: list[str], owner_id=None) -> dict[str, float]:
    """Each engine's share of the pool for this profile: its win rate (Laplace-smoothed, so a new engine starts level),
    never below MIN_SHARE."""
    name = getattr(profile, "name", profile)
    rows = {r["engine"]: r for r in conn.execute(
        "select engine, wins, losses from engine_stats where owner_operator_id = %s and profile = %s",
        (owner_of(conn, owner_id), name)).fetchall()}
    raw = {e: (rows[e]["wins"] + 1) / (rows[e]["wins"] + rows[e]["losses"] + 2) if e in rows else 0.5 for e in engines}
    return floored(raw, MIN_SHARE)


def allocate(shares_: dict[str, float], slots: int = POOL_SLOTS) -> dict[str, int]:
    """Whole slots per engine from its share; every engine gets at least one."""
    return {e: max(1, round(s * slots)) for e, s in shares_.items()}
