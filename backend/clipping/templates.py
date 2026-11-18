"""The creative library: templates are versioned data, so a new look, an edit or a revert needs no release.

An account has any number of templates. Every change is a new immutable version (`template_versions`); a revert is a new
version that copies an older one. The render rotates through the account's templates, least recently used first, so its
clips never look identical, yet they share one family (font and main colour) and stay recognisable. A template that was
just added, edited or reverted goes first, so the next clip uses it. The persona is what the owner sees per account: its
look, how often to post and when (from its own results once they exist)."""
from __future__ import annotations

import datetime as dt
import math
import statistics

from psycopg.types.json import Jsonb

from .adapters import media
from .core import AppError, get_setting

# (name, font, main colour, accent, second accent): a starter family. An account without templates gets three variations of one.
LOOKS = [
    ("Sunrise", "Arial", "#FFFFFF", "#FFC400", "#FF8A3D"),
    ("Ocean", "Tahoma", "#FFFFFF", "#35C4FF", "#7C9BFF"),
    ("Mint", "Segoe UI", "#F4FFF8", "#2EE59D", "#B8F24A"),
    ("Berry", "Arial", "#FFF4FA", "#FF4FA3", "#C04BFF"),
    ("Slate", "Tahoma", "#F2F2F2", "#8EA2FF", "#53D6C8"),
    ("Ember", "Segoe UI", "#FFFFFF", "#FF6A3D", "#FFD23D"),
]
MAX_PER_ACCOUNT = 12
EMPHASIS_MAX = 8
PERSONA_MIN_POSTS = 6
POSTS_PER_WEEK = {"tiktok": 7, "instagram_reels": 5, "youtube_shorts": 5}
TYPICAL_HOURS = [12, 18, 21]


def presets() -> list[dict]:
    """The starter looks the owner can add with one tap (each is just a template spec)."""
    return [{"name": name, "spec": {**media.TEMPLATE_DEFAULTS, "font": font, "primary": primary, "accent": accent}}
            for name, font, primary, accent, _ in LOOKS]


def starter_set(account_id) -> list[tuple[str, dict]]:
    """Three variations of one look, chosen by the account (so accounts differ): same font and main colour, a different accent,
    caption position and progress bar for each, which keeps the family recognisable while the clips vary."""
    name, font, primary, accent, accent2 = LOOKS[account_id.int % len(LOOKS)]
    base = {"font": font, "primary": primary, "accent": accent}
    return [(f"{name} main", {**base}), (f"{name} bright", {**base, "accent": accent2, "progress_bar": False}),
            (f"{name} centre", {**base, "caption_position": "middle", "zooms": False})]


# ------------------------------------------------------------------ reading

_SELECT = """select t.id, t.account_id, t.owner_operator_id, t.name, t.active, t.current_version as version, t.last_used_at,
                    t.created_at, v.spec
             from templates t join template_versions v on v.template_id = t.id and v.version_no = t.current_version"""


def _public(row: dict) -> dict:
    return {"id": str(row["id"]), "account_id": str(row["account_id"]), "name": row["name"], "active": row["active"],
            "version": row["version"], "spec": media.normalize_template(row["spec"])}


def _get(conn, template_id) -> dict:
    row = conn.execute(_SELECT + " where t.id = %s", (template_id,)).fetchone() if _is_uuid(template_id) else None
    if row is None:
        raise AppError("Template not found.", "not_found")
    return row


def _is_uuid(value) -> bool:
    import uuid

    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True


def _account(conn, account_id) -> dict:
    row = conn.execute("select * from accounts where id = %s", (account_id,)).fetchone() if _is_uuid(account_id) else None
    if row is None:
        raise AppError("Account not found.", "not_found")
    return row


def list_for_account(conn, account_id) -> list[dict]:
    """The account's templates in creation order (active and switched off), with their current spec."""
    rows = conn.execute(_SELECT + " where t.account_id = %s order by t.created_at, t.id", (account_id,)).fetchall()
    return [_public(r) for r in rows]


def history(conn, template_id, limit: int = 10) -> list[dict]:
    """The newest versions of a template, newest first."""
    _get(conn, template_id)
    rows = conn.execute("select version_no, spec, note, created_at from template_versions where template_id = %s order by version_no desc limit %s",
                        (template_id, limit)).fetchall()
    return [{"version": r["version_no"], "spec": media.normalize_template(r["spec"]), "note": r["note"], "created_at": r["created_at"]} for r in rows]


# ------------------------------------------------------------------ writing

def _clean_fields(fields: dict) -> dict:
    unknown = sorted(set(fields) - set(media.TEMPLATE_DEFAULTS))
    if unknown:
        raise AppError(f"Unknown template field: {', '.join(unknown)}.", "bad_template")
    return fields


def _name(value) -> str:
    name = " ".join(str(value or "").split())
    if not 1 <= len(name) <= 60:
        raise AppError("Give the template a name of up to 60 characters.", "bad_template")
    return name


def _promote(conn, account_id, template_id) -> None:
    """Make this the template the next clip uses (and only this one)."""
    conn.execute("update templates set fresh = (id = %s) where account_id = %s", (template_id, account_id))


def add(conn, account_id, name: str, fields: dict | None = None, promote: bool = True) -> dict:
    """A new template for the account (version 1). It is used by the next clip."""
    account = _account(conn, account_id)
    if conn.execute("select count(*) as n from templates where account_id = %s", (account["id"],)).fetchone()["n"] >= MAX_PER_ACCOUNT:
        raise AppError(f"An account can have up to {MAX_PER_ACCOUNT} templates. Switch one off or edit it instead.", "too_many")
    spec = media.normalize_template({**media.TEMPLATE_DEFAULTS, **_clean_fields(fields or {})}, strict=True)
    name = _name(name)
    if conn.execute("select 1 from templates where account_id = %s and name = %s", (account["id"], name)).fetchone():
        raise AppError("This account already has a template with that name.", "duplicate")
    # clock_timestamp, not now(): templates made in one transaction (the starter set) keep their order
    row = conn.execute("insert into templates (owner_operator_id, account_id, name, created_at) values (%s, %s, %s, clock_timestamp()) returning id",
                       (account["owner_operator_id"], account["id"], name)).fetchone()
    conn.execute("insert into template_versions (owner_operator_id, template_id, version_no, spec, note) values (%s, %s, 1, %s, 'Created')",
                 (account["owner_operator_id"], row["id"], Jsonb(spec)))
    if promote:
        _promote(conn, account["id"], row["id"])
    return _public(_get(conn, row["id"]))


def _new_version(conn, row: dict, spec: dict, note: str, name: str | None = None, active: bool | None = None) -> dict:
    version = row["version"] + 1
    conn.execute("insert into template_versions (owner_operator_id, template_id, version_no, spec, note) values (%s, %s, %s, %s, %s)",
                 (row["owner_operator_id"], row["id"], version, Jsonb(spec), note))
    conn.execute("update templates set current_version = %s, name = coalesce(%s, name), active = coalesce(%s, active) where id = %s",
                 (version, name, active, row["id"]))
    _promote(conn, row["account_id"], row["id"])
    return _public(_get(conn, row["id"]))


def update(conn, template_id, fields: dict | None = None, name: str | None = None, active: bool | None = None) -> dict:
    """Change a template. Changed fields make a new version that goes first in the rotation; an unchanged spec saves nothing."""
    row = _get(conn, template_id)
    fields = _clean_fields(fields or {})
    spec = media.normalize_template({**media.normalize_template(row["spec"]), **fields}, strict=True)
    name = _name(name) if name is not None else None
    if name and name != row["name"] and conn.execute("select 1 from templates where account_id = %s and name = %s", (row["account_id"], name)).fetchone():
        raise AppError("This account already has a template with that name.", "duplicate")
    if spec != media.normalize_template(row["spec"]):
        return _new_version(conn, row, spec, "Edited", name, active)
    conn.execute("update templates set name = coalesce(%s, name), active = coalesce(%s, active) where id = %s", (name, active, row["id"]))
    return _public(_get(conn, row["id"]))


def revert(conn, template_id, to_version: int | None = None) -> dict:
    """Go back to an older look: a new version with that version's spec (the history is never rewritten). Default: the one before."""
    row = _get(conn, template_id)
    target = row["version"] - 1 if to_version is None else to_version
    old = conn.execute("select spec from template_versions where template_id = %s and version_no = %s", (row["id"], target)).fetchone()
    if old is None or target == row["version"]:
        raise AppError("There is no earlier version to go back to.", "no_version")
    return _new_version(conn, row, media.normalize_template(old["spec"]), f"Reverted to version {target}")


def ensure_set(conn, account_id) -> list[dict]:
    """The account's templates; an account that has none yet gets the starter set (three variations of one look)."""
    existing = list_for_account(conn, account_id)
    if existing:
        return existing
    account = _account(conn, account_id)
    made = [add(conn, account["id"], name, fields, promote=False) for name, fields in starter_set(account["id"])]
    _promote(conn, account["id"], made[0]["id"])
    return list_for_account(conn, account["id"])


def primary(conn, account_id) -> dict:
    """The account's main template: the first active one (it is the one the Profile edits)."""
    active = [t for t in ensure_set(conn, account_id) if t["active"]]
    if not active:
        raise AppError("All of this account's templates are switched off. Switch one on.", "no_template")
    return active[0]


def edit_primary(conn, account_id, fields: dict) -> dict:
    return update(conn, primary(conn, account_id)["id"], fields)


# ------------------------------------------------------------------ the render's choice

def pick(conn, account_id, keep_id=None) -> dict:
    """The template for the next clip of the account: `keep_id` while it is still active (an edited clip keeps its look, a
    retried job does not advance the rotation), else the fresh one (just added, edited or reverted), else the least recently
    used active template. Marks the chosen one as used. No account, or none of its templates active: the defaults, id None."""
    chosen = None
    if account_id is not None:
        active = {t["id"]: t for t in ensure_set(conn, account_id) if t["active"]}
        chosen = active.get(str(keep_id)) if keep_id else None
        if chosen is None and active:
            row = conn.execute("""select id from templates where account_id = %s and active
                                  order by fresh desc, last_used_at nulls first, created_at, id limit 1""", (account_id,)).fetchone()
            chosen = active[str(row["id"])]
            conn.execute("update templates set last_used_at = clock_timestamp(), fresh = false where id = %s", (chosen["id"],))
    if chosen is None:
        return {"id": None, "name": "Default", "version": 0, "spec": dict(media.TEMPLATE_DEFAULTS)}
    return chosen


# ------------------------------------------------------------------ what the render needs from the signals

def emphasis_times(loudness: list[float], start_s: float, end_s: float, limit: int = EMPHASIS_MAX) -> list[float]:
    """Source seconds of the loudest moments inside the clip: local peaks of the loudness series that are in the top fifth of
    the clip and clearly above its typical level. They drive the emphasis zooms and the highlighted caption words."""
    first = int(start_s)
    window = [float(v) for v in loudness[first:math.ceil(end_s)]]
    if len(window) < 5:
        return []
    ordered = sorted(window)
    floor = max(ordered[int(0.8 * (len(ordered) - 1))], statistics.median(ordered) + 3)
    peaks = []
    for i, level in enumerate(window):
        at = first + i + 0.5
        if (level >= floor and level > -60 and (i == 0 or level >= window[i - 1]) and (i == len(window) - 1 or level >= window[i + 1])
                and start_s + 0.5 <= at <= end_s - 0.5):
            peaks.append((level, at))
    return sorted(at for _, at in sorted(peaks, reverse=True)[:limit])


# ------------------------------------------------------------------ the persona

def best_hours(posts: list[dict]) -> dict | None:
    """The three best UTC hours of the account's own posts (by average views), in clock order; None until there are enough
    posts to learn from. `posts`: dicts with `posted_at` (datetime) and `views`."""
    if len(posts) < PERSONA_MIN_POSTS:
        return None
    by_hour: dict[int, list[int]] = {}
    for post in posts:
        by_hour.setdefault(post["posted_at"].astimezone(dt.timezone.utc).hour, []).append(int(post["views"]))
    best = sorted(by_hour, key=lambda h: (-sum(by_hour[h]) / len(by_hour[h]), h))[:3]
    return {"hours": sorted(best), "basis": "results", "tz": "utc", "posts_counted": len(posts)}


def persona(conn, account_id) -> dict:
    """What an account is: its look, how many posts a week suit it, and the best hours to post. The hours come from the
    account's own posts once there are enough of them (UTC hours, the screen shows them in the owner's time); before that,
    typical evening hours, marked as such."""
    account = _account(conn, account_id)
    looks = ensure_set(conn, account["id"])
    active = [t for t in looks if t["active"]] or looks
    cap = account["daily_cap"] if account["daily_cap"] is not None else int(get_setting(conn, "posts_per_account_per_day"))
    posts = conn.execute(
        """select p.posted_at, coalesce(max(m.views), 0) as views from posts p left join metrics m on m.post_id = p.id
           where p.account_id = %s group by p.id""", (account["id"],)).fetchall()
    hours = best_hours(posts) or {"hours": TYPICAL_HOURS, "basis": "typical", "tz": "local", "posts_counted": len(posts)}
    main = active[0]["spec"]
    return {"account_id": str(account["id"]), "look": {"names": [t["name"] for t in active], "font": main["font"], "primary": main["primary"],
                                                      "accent": main["accent"], "templates": len(active)},
            "posts_per_week": min(POSTS_PER_WEEK.get(account["platform"], 5), max(int(float(cap)) * 7, 1)), "best_hours": hours}
