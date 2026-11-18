"""Learning from the owner's decisions. Labels are read from `reviews` (accept, reject, not undone) and `metrics` (48 h
views against the owner's median), plus the owner's rescues; none of those tables is changed. Each new label also updates
the engine bandit and the hook-pattern stats once.

Per profile, once it has MIN_LABELS labels, the feature weights move from the profile's defaults towards what the labelled
moments had in common; before that the defaults apply and nothing is stored."""
from __future__ import annotations

import statistics

from psycopg.types.json import Jsonb

from . import hook_patterns, profiles, router
from .router import owner_of

MIN_LABELS = 30
STEP = 0.5               # how far the weights follow the difference between liked and disliked moments
WEIGHT_FLOOR = 0.02
POSITIVE = ("accept", "rescue", "views_high")
PATTERN_OUTCOME = {"accept": "approve", "reject": "reject", "views_high": "views_high", "views_low": "views_low"}


def weights_for(conn, profile, owner_id=None) -> dict:
    """The weights in force: learned ones once they exist, else the profile's defaults."""
    name = getattr(profile, "name", profile)
    row = conn.execute("select weights from profile_weights where owner_operator_id = %s and profile = %s", (owner_of(conn, owner_id), name)).fetchone()
    return dict(row["weights"]) if row else dict(getattr(profile, "weights", None) or profiles.Profile.build(name, None).weights)


def _insert(conn, owner, row: dict, kind: str) -> bool:
    inserted = conn.execute(
        """insert into moment_labels (owner_operator_id, candidate_id, profile, engine, language, hook_pattern, kind, features)
           values (%s, %s, %s, %s, %s, %s, %s, %s) on conflict (candidate_id, kind) do nothing returning id""",
        (owner, row["candidate_id"], row["profile"], row["engine"], row["language"], row["pattern"], kind, Jsonb(row["features"] or {}))).fetchone()
    if inserted is None:
        return False
    win = kind in POSITIVE
    if row["engine"]:
        router.record(conn, row["profile"], row["engine"], "win" if win else "loss", owner)
    if row["pattern"] and kind in PATTERN_OUTCOME:
        hook_patterns.record(conn, row["pattern"], row["language"] or "en", row["profile"], PATTERN_OUTCOME[kind], owner)
    return True


_BASE = """select ca.id as candidate_id, ca.profile, ca.engine, ca.features, s.language,
              (select h.pattern from hooks h where h.id::text = cl.render_meta->>'hook_id') as pattern"""


def sync_labels(conn, owner) -> int:
    """Turn the owner's reviews and 48 h views into labels (once each). Returns how many are new."""
    added = 0
    for row in conn.execute(
            _BASE + """, r.decision as kind from reviews r join clips cl on cl.id = r.clip_id join candidates ca on ca.id = cl.candidate_id
               join source_assets s on s.id = ca.source_id
               where r.owner_operator_id = %s and r.decision in ('accept', 'reject') and ca.profile is not null
                 and not exists (select 1 from reviews u where u.undo_of = r.id)""", (owner,)).fetchall():
        added += _insert(conn, owner, row, row["kind"])
    views = conn.execute(
        _BASE + """, m.views from metrics m join posts p on p.id = m.post_id join clips cl on cl.id = p.clip_id
           join candidates ca on ca.id = cl.candidate_id join source_assets s on s.id = ca.source_id
           where m.owner_operator_id = %s and m."window" = 'h48' and ca.profile is not null""", (owner,)).fetchall()
    if len(views) >= 2:
        median = statistics.median(float(v["views"]) for v in views)
        for row in views:
            if float(row["views"]) != median:
                added += _insert(conn, owner, row, "views_high" if float(row["views"]) > median else "views_low")
    return added


def _means(vectors: list[dict]) -> dict[str, float]:
    names = {k for v in vectors for k in v}
    return {k: sum(v.get(k, 0.0) for v in vectors) / len(vectors) for k in names}


def _fit(conn, owner, profile: str, labels: list[dict]) -> dict:
    default = profiles.Profile.build(profile, (conn.execute("select config from content_profiles where name = %s", (profile,)).fetchone() or {}).get("config")).weights
    liked = [l["features"] for l in labels if l["kind"] in POSITIVE and l["features"]]
    disliked = [l["features"] for l in labels if l["kind"] not in POSITIVE and l["features"]]
    everyone = [r["features"] for r in conn.execute(
        "select features from candidates where owner_operator_id = %s and profile = %s and features is not null", (owner, profile)).fetchall()]
    if not liked and not disliked:
        return dict(default)
    if liked and disliked:
        up, down = _means(liked), _means(disliked)
        delta = {f: up.get(f, 0) - down.get(f, 0) for f in default}
    else:                                              # one-sided: against what the engine usually produces
        base = _means(everyone or [{}])
        mean = _means(liked or disliked)
        sign = 1 if liked else -1
        delta = {f: sign * (mean.get(f, 0) - base.get(f, 0)) for f in default}
    raw = {f: max(default[f] + STEP * delta[f], WEIGHT_FLOOR) if default[f] > 0 else default[f] for f in default}
    total = sum(raw.values())
    fitted = {f: v / total for f, v in raw.items()}
    blend = len(labels) / (len(labels) + MIN_LABELS)
    return {f: round((1 - blend) * default[f] + blend * fitted[f], 5) for f in default}


def learn(conn, owner_id=None) -> dict:
    """Read the owner's new decisions, then refit every profile that has enough labels. Returns the fitted weights by
    profile (empty before MIN_LABELS labels) and how many moments are labelled in all."""
    owner = owner_of(conn, owner_id)
    sync_labels(conn, owner)
    rows = conn.execute("select candidate_id, profile, kind, features from moment_labels where owner_operator_id = %s", (owner,)).fetchall()
    by_profile: dict[str, list[dict]] = {}
    for r in rows:
        by_profile.setdefault(r["profile"], []).append(r)
    fitted = {}
    for name, labels in by_profile.items():
        if len(labels) < MIN_LABELS:
            continue
        fitted[name] = _fit(conn, owner, name, labels)
        conn.execute(
            """insert into profile_weights (owner_operator_id, profile, weights, labelled, fitted_at) values (%s, %s, %s, %s, now())
               on conflict (owner_operator_id, profile) do update set weights = excluded.weights, labelled = excluded.labelled, fitted_at = now()""",
            (owner, name, Jsonb(fitted[name]), len(labels)))
    return {"weights": fitted, "labelled": len(rows)}
