"""Audience countries per account, from the official platform APIs (decision 2026-11-17, practitioner lesson:
views outside the US, UK, Canada and Australia often pay less or nothing).

Tokens come from environment variables only and are never stored or logged. TikTok has no audience API for
us until its app review passes, so TikTok (and any platform without a token) uses a screenshot instead."""
from __future__ import annotations

from .. import config
from ..core import AppError
from .base import Adapter, AdapterError, register

GRAPH = "https://graph.facebook.com/v21.0"
YT_ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"


def _shares(counts: dict[str, float]) -> dict[str, float]:
    total = sum(v for v in counts.values() if v and v > 0)
    if total <= 0:
        raise AdapterError("no audience data returned")
    return {k.upper(): round(v / total * 100, 2) for k, v in counts.items() if v and v > 0}


@register
class PlatformAudience(Adapter):
    stage, provider = "audience", "platform_apis"

    def countries(self, platform: str, external_id: str | None = None) -> dict:
        import httpx

        if platform == "youtube_shorts":
            token = config.env("YOUTUBE_ANALYTICS_TOKEN")
            if not token:
                raise AppError("Connect YouTube (YOUTUBE_ANALYTICS_TOKEN) or add a screenshot of YouTube Studio > Audience.", "no_token")
            with httpx.Client(timeout=30) as client:
                r = client.get(YT_ANALYTICS, headers={"Authorization": f"Bearer {token}"},
                               params={"ids": "channel==MINE", "startDate": "2020-01-01", "endDate": "2100-01-01",
                                       "dimensions": "country", "metrics": "views", "sort": "-views", "maxResults": 50})
            if r.status_code >= 400:
                raise AdapterError(f"HTTP {r.status_code}")
            return {"countries": _shares({row[0]: float(row[1]) for row in r.json().get("rows") or []})}
        if platform == "instagram_reels":
            token = config.env("INSTAGRAM_GRAPH_TOKEN")
            if not token or not external_id:
                raise AppError("Connect Instagram (INSTAGRAM_GRAPH_TOKEN and the account's ID) or add a screenshot of the Insights audience.", "no_token")
            with httpx.Client(timeout=30) as client:
                r = client.get(f"{GRAPH}/{external_id}/insights", params={
                    "metric": "follower_demographics", "period": "lifetime", "metric_type": "total_value",
                    "breakdown": "country", "access_token": token})
            if r.status_code >= 400:
                raise AdapterError(f"HTTP {r.status_code}")
            counts: dict[str, float] = {}
            for metric in r.json().get("data") or []:
                for breakdown in ((metric.get("total_value") or {}).get("breakdowns") or []):
                    for item in breakdown.get("results") or []:
                        counts[(item.get("dimension_values") or ["?"])[0]] = float(item.get("value") or 0)
            return {"countries": _shares(counts)}
        raise AppError("This platform has no audience API we can use yet. Add a screenshot of its audience page.", "no_api")


FAKE_AUDIENCE: dict = {"countries": {"US": 40, "GB": 10, "SA": 30, "EG": 20}, "fail": False, "calls": []}


@register
class FakeAudience(Adapter):
    stage, provider = "audience", "fake"

    def countries(self, platform: str, external_id: str | None = None) -> dict:
        FAKE_AUDIENCE["calls"].append(platform)
        if FAKE_AUDIENCE["fail"]:
            raise AdapterError("fake audience failure")
        return {"countries": dict(FAKE_AUDIENCE["countries"])}


@register
class ManualAudience(Adapter):
    """Declared fallback: the owner types the share in or adds a screenshot."""
    stage, provider = "audience", "manual"

    def countries(self, platform: str, external_id: str | None = None) -> dict:
        raise AppError("Audience countries are not connected. Type the share in or add a screenshot.", "audience_manual")
