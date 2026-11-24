"""The hook pattern library. Named patterns per language (and optionally per profile) live in `hook_patterns` as data, so a
new pattern is a new row and needs no release. Each pattern's results are counted per language and profile
(`hook_pattern_stats`): winners are chosen more often, weak ones less, but every pattern keeps at least PATTERN_FLOOR of
the choices, so new and weak patterns keep being tried.

R4: a hook is rated for concreteness ("just the right amount of information") and must be paid off inside the clip; both
checks live in moment_rank (`hook_rating`, `paid_off`)."""
from __future__ import annotations

import random

from . import hook_lexicon
from .router import floored, owner_of

PATTERN_FLOOR = 0.08
OUTCOMES = {"use": "uses", "approve": "approvals", "reject": "rejections", "views_high": "views_high", "views_low": "views_low"}


def library(conn, language: str, profile: str, niche: str | None = None) -> list[dict]:
    """The active patterns of a language that apply to this profile (a pattern with no profiles applies to all), plus the live
    formats of the hook lexicon for this niche (POV:, Nobody:, curiosity gap ...; a trend format drops out after its `expires`)."""
    rows = conn.execute("select name, description, template, hook_type, profiles from hook_patterns where active and language = %s order by name",
                        (language,)).fetchall()
    rows = [r for r in rows if not r["profiles"] or profile in r["profiles"]]
    known = {r["name"] for r in rows}
    fmts = [{"name": f["id"], "description": f["about"], "template": f["examples"][0], "hook_type": f["type"], "profiles": [], "base": f["weight"]}
            for f in hook_lexicon.formats(language, niche) if f["id"] not in known]
    return rows + fmts


def weights(conn, language: str, profile: str, owner_id=None, patterns: list[dict] | None = None, niche: str | None = None) -> dict[str, float]:
    """Each pattern's share of the choices: its Laplace-smoothed success rate (approvals and high views against
    rejections and low views), never below PATTERN_FLOOR."""
    patterns = library(conn, language, profile, niche) if patterns is None else patterns
    if not patterns:
        return {}
    stats = {r["pattern"]: r for r in conn.execute(
        "select pattern, approvals, rejections, views_high, views_low from hook_pattern_stats where owner_operator_id = %s and language = %s and profile = %s",
        (owner_of(conn, owner_id), language, profile)).fetchall()}
    raw = {}
    for p in patterns:
        s = stats.get(p["name"])
        good = (s["approvals"] + s["views_high"]) if s else 0
        bad = (s["rejections"] + s["views_low"]) if s else 0
        raw[p["name"]] = (good + 1) / (good + bad + 2) * float(p.get("base", 1.0))   # a lexicon format starts at its own weight
    return floored(raw, PATTERN_FLOOR)


def pick(conn, language: str, profile: str, count: int, seed, owner_id=None, niche: str | None = None) -> list[dict]:
    """`count` different patterns drawn by weight. The same seed gives the same draw, so a re-run of a video is stable."""
    patterns = library(conn, language, profile, niche)
    share = weights(conn, language, profile, owner_id, patterns)
    rng, chosen = random.Random(str(seed)), []
    pool = list(patterns)
    while pool and len(chosen) < count:
        total = sum(share[p["name"]] for p in pool)
        mark, running = rng.random() * total, 0.0
        for p in pool:
            running += share[p["name"]]
            if mark <= running:
                chosen.append(p)
                pool.remove(p)
                break
    return chosen


def record(conn, pattern: str, language: str, profile: str, outcome: str, owner_id=None) -> None:
    """Count one result for a pattern: use, approve, reject, views_high or views_low."""
    column = OUTCOMES[outcome]
    conn.execute(
        f"""insert into hook_pattern_stats (owner_operator_id, pattern, language, profile, {column}) values (%s, %s, %s, %s, 1)
            on conflict (owner_operator_id, pattern, language, profile) do update set {column} = hook_pattern_stats.{column} + 1""",
        (owner_of(conn, owner_id), pattern, language, profile))
