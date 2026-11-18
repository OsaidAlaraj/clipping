"""Content profiles: what a good moment is for each type of video. One row per type in `content_profiles` (data, not code,
so a new type needs no release). A profile sets the clip count and length, the signal weights, what counts as a moment,
the crop, audio and edit style and the engine route.

Every field has a default here, so a row with missing or broken fields still works, and an unknown niche uses the
`general` profile. The owner's per-video choice (`source_assets.profile`) wins over the campaign niche."""
from __future__ import annotations

from dataclasses import dataclass

FEATURES = ("hook", "standalone", "payoff", "emotion", "value", "energy", "peaks", "laughter", "length_fit")
GENERAL = "general"
MIN_LENGTH_S, MAX_LENGTH_S = 5.0, 180.0
SHORT_FLOOR_S = 8.0          # a short source never shrinks a window below this

DEFAULTS: dict = {
    "label": "General", "clips": 5, "min_clips": 3, "max_clips": 10, "full_episode_min": 30,
    "length_min_s": 20, "length_max_s": 60,
    "weights": {"hook": 0.18, "standalone": 0.17, "payoff": 0.15, "emotion": 0.15, "value": 0.10, "energy": 0.05,
                "peaks": 0.08, "laughter": 0.05, "length_fit": 0.07},
    "moments": ["a standalone moment with a payoff"],
    "crop": "follow_speaker", "audio": "speech", "edit": "dead_air_out", "route": ["own", "topic"],
    "needs_speech": True, "info_ok": False,
}


def _num(value, default, low, high) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    number = number if number == number else float(default)                      # NaN falls back too
    return min(max(number, low), high)


def _text(value, default: str) -> str:
    return " ".join(value.split())[:40] if isinstance(value, str) and value.strip() else default


def _names(value, default) -> tuple[str, ...]:
    items = [str(v).strip().lower() for v in value if str(v).strip()] if isinstance(value, (list, tuple)) else []
    return tuple(dict.fromkeys(items)) or tuple(default)


@dataclass(frozen=True)
class Profile:
    name: str
    label: str
    clips: int
    min_clips: int
    max_clips: int
    full_episode_min: float
    length_min_s: float
    length_max_s: float
    weights: dict
    moments: tuple
    crop: str
    audio: str
    edit: str
    route: tuple
    needs_speech: bool
    info_ok: bool

    @classmethod
    def build(cls, name: str, config: dict | None) -> "Profile":
        """A profile from a (possibly partial or broken) config: every field that is missing or invalid takes its default."""
        config = config if isinstance(config, dict) else {}
        d = DEFAULTS
        min_clips = int(_num(config.get("min_clips"), d["min_clips"], 1, 20))
        max_clips = int(_num(config.get("max_clips"), d["max_clips"], min_clips, 40))
        clips = int(_num(config.get("clips"), d["clips"], min_clips, max_clips))
        low = _num(config.get("length_min_s"), d["length_min_s"], MIN_LENGTH_S, MAX_LENGTH_S)
        high = _num(config.get("length_max_s"), d["length_max_s"], MIN_LENGTH_S, MAX_LENGTH_S)
        if high <= low:                                     # a broken range falls back to the default range
            low, high = float(d["length_min_s"]), float(d["length_max_s"])
        given = config.get("weights") if isinstance(config.get("weights"), dict) else {}
        weights = {f: _num(given.get(f), d["weights"][f], 0.0, 1.0) for f in FEATURES}
        if sum(weights.values()) <= 0:
            weights = dict(d["weights"])
        moments = [str(m)[:120] for m in config.get("moments", []) if isinstance(m, str)] if isinstance(config.get("moments"), list) else []
        return cls(
            name=name, label=_text(config.get("label"), d["label"]), clips=clips, min_clips=min_clips, max_clips=max_clips,
            full_episode_min=_num(config.get("full_episode_min"), d["full_episode_min"], 1, 600), length_min_s=low, length_max_s=high,
            weights=weights, moments=tuple(moments or d["moments"]), crop=_text(config.get("crop"), d["crop"]),
            audio=_text(config.get("audio"), d["audio"]), edit=_text(config.get("edit"), d["edit"]),
            route=_names(config.get("route"), d["route"]),
            needs_speech=bool(config.get("needs_speech", d["needs_speech"])), info_ok=bool(config.get("info_ok", d["info_ok"])))

    def style(self) -> dict:
        return {"crop": self.crop, "audio": self.audio, "edit": self.edit}

    def target(self, duration_s: float) -> int:
        """How many clips this video should give: the profile's count for a full episode, scaled by length."""
        scaled = round(self.clips * float(duration_s) / (self.full_episode_min * 60))
        return min(max(scaled, self.min_clips), self.max_clips)

    def length_for(self, duration_s: float, campaign: tuple[float, float] | None = None) -> tuple[float, float]:
        """The clip length range for this video. A source too short to hold the minimum clip count shrinks the range in
        proportion; the campaign's own length rules always win."""
        low, high = self.length_min_s, self.length_max_s
        need = self.min_clips * low * 1.25
        if 0 < duration_s < need:
            factor = duration_s / need
            low = max(round(low * factor), SHORT_FLOOR_S)
            high = min(high, max(round(high * factor), low * 2))
        if campaign:
            lo, hi = max(low, campaign[0]), min(high, campaign[1])
            low, high = (lo, hi) if lo < hi else campaign
        return float(low), float(high)


# ------------------------------------------------------------------ lookup

def _key(value) -> str:
    return " ".join(str(value or "").lower().replace("&", " ").replace("-", "_").replace("/", " ").split()).replace(" ", "_")


def get(conn, niche_or_name) -> Profile:
    """The profile named like this, else the one that lists this niche, else the general profile."""
    key = _key(niche_or_name)
    rows = conn.execute("select name, niches, config from content_profiles where active").fetchall()
    for row in rows:
        if row["name"] == key:
            return Profile.build(row["name"], row["config"])
    for row in rows:
        if key and key in [_key(n) for n in row["niches"]]:
            return Profile.build(row["name"], row["config"])
    general = next((r for r in rows if r["name"] == GENERAL), None)
    return Profile.build(GENERAL, general["config"] if general else None)


def for_source(conn, source: dict, campaign: dict) -> Profile:
    """The owner's choice for this video if there is one, else the campaign niche."""
    if source.get("profile"):
        return get(conn, source["profile"])
    niche = conn.execute("select name from categories where id = %s", (campaign.get("category_id"),)).fetchone()
    return get(conn, niche["name"] if niche else None)


def listing(conn) -> list[dict]:
    return [{"name": r["name"], "label": Profile.build(r["name"], r["config"]).label}
            for r in conn.execute("select name, config from content_profiles where active order by name").fetchall()]
