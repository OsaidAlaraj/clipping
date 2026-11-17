"""Campaign discovery adapters (decision 2026-11-17).

ClippingAlpha's public MCP server lists live campaigns from Content Rewards, Promote.fun, Clipster, Clipstake,
Vyro and Evangelist, with briefs, fees, minimums, run-out forecasts and brief grades. No account or key.
Data: ClippingAlpha (clippingalpha.com), CC BY 4.0. The platform's own page is the source of truth.
We never read Vyro, Elev8or or Shoot pages ourselves (their terms forbid scraping)."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .. import config
from ..core import AppError
from .base import Adapter, AdapterError, register


class FeedRateLimited(AppError):
    """The feed asked us to slow down. Not retried, no fallback: the pass stops and keeps what it has."""

    def __init__(self, wait_s: float):
        super().__init__(f"The campaign feed asked us to slow down. Try again in about {max(int(wait_s // 60), 1)} minutes.", "rate_limited")
        self.wait_s = wait_s


_pace = {"last": 0.0, "until": 0.0}
_pace_lock = threading.Lock()
_clients: dict = {}

BOARDS = ["content-rewards", "promote-fun", "clipster", "clipstake", "vyro", "evangelist"]
BOARD_TO_MARKETPLACE = {"content-rewards": "content_rewards", "promote-fun": "promote_fun", "clipster": "clipster",
                        "clipstake": "clipstake", "vyro": "vyro", "evangelist": "evangelist"}
NICHES = ["podcast", "streams", "gaming", "sports", "film-and-tv", "music", "brand-ads", "ugc-and-apps", "behind-the-scenes"]


class McpClient:
    """Minimal MCP Streamable-HTTP client (JSON responses; event-stream tolerated)."""

    def __init__(self, endpoint: str, timeout: float = 60, min_interval: float = 1.5):
        self.endpoint = endpoint
        self.timeout = timeout
        self.min_interval = min_interval
        self._ready = False
        self._session: str | None = None

    def _post(self, payload: dict) -> dict:
        import httpx

        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
                   "User-Agent": "clipping-campaign-reader/1.0"}
        if self._session:
            headers["mcp-session-id"] = self._session
        with _pace_lock:   # one call at a time, spaced out, and none while the feed has asked us to wait
            now = time.monotonic()
            if now < _pace["until"]:
                raise FeedRateLimited(_pace["until"] - now)
            wait = _pace["last"] + self.min_interval - now
            if wait > 0:
                time.sleep(wait)
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(self.endpoint, headers=headers, json=payload)
            _pace["last"] = time.monotonic()
            if response.status_code == 429:
                try:
                    retry_after = float(response.headers.get("retry-after", "600"))
                except ValueError:
                    retry_after = 600.0
                _pace["until"] = time.monotonic() + max(retry_after, 60)
                raise FeedRateLimited(retry_after)
        if response.status_code >= 400:
            raise AdapterError(f"HTTP {response.status_code}")
        self._session = response.headers.get("mcp-session-id") or self._session
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            for line in response.text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[5:])
            raise AdapterError("empty event stream")
        return response.json() if response.content else {}

    def _initialize(self) -> None:
        self._session = None
        self._post({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "clipping", "version": "1.0"}}})
        self._ready = True

    def call(self, tool: str, arguments: dict) -> dict:
        if not self._ready:
            self._initialize()
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
        try:
            result = self._post(request)
        except AdapterError as exc:   # the server forgot our session: start a new one once
            if not self._session or not str(exc).startswith(("HTTP 400", "HTTP 404")):
                raise
            self._initialize()
            result = self._post(request)
        if "error" in result:
            raise AdapterError(f"{tool}: {result['error'].get('message', 'error')}")
        body = result.get("result", {})
        if body.get("isError"):
            raise AdapterError(f"{tool} returned an error")
        return body.get("structuredContent") or {}


@register
class ClippingAlphaDiscovery(Adapter):
    stage, provider = "discovery", "clippingalpha"

    def _client(self) -> McpClient:
        """One MCP session per endpoint for the whole process, so each feed call is one request, not two."""
        endpoint = self.cfg.get("endpoint", "https://clippingalpha.com/mcp/general")
        client = _clients.get(endpoint)
        if client is None:
            client = _clients[endpoint] = McpClient(endpoint, timeout=30)
        client.min_interval = float(self.cfg.get("min_seconds_between_calls", 1.5))
        return client

    def search(self, **filters) -> dict:
        args = {k: v for k, v in filters.items() if v not in (None, "", [])}
        args.setdefault("limit", int(self.cfg.get("per_call", 25)))
        return self._client().call("search_campaigns", args)

    def brief(self, ref: str) -> dict:
        return self._client().call("get_brief", {"campaign": ref})

    def analytics(self, ref: str) -> dict:
        return self._client().call("get_campaign_analytics", {"campaign": ref})

    def assets(self, ref: str) -> dict:
        return self._client().call("get_assets", {"campaign": ref})


FIXTURES = config.FIXTURES / "clippingalpha"
FAKE_DISCOVERY: dict = {"campaigns": None, "fail": False, "calls": []}


@register
class FakeDiscovery(Adapter):
    """Serves the saved real sample in backend/fixtures/clippingalpha (or a test's own campaigns)."""
    stage, provider = "discovery", "fake"

    @staticmethod
    def _load(name: str) -> dict:
        return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))

    def search(self, **filters) -> dict:
        FAKE_DISCOVERY["calls"].append(("search", filters))
        if FAKE_DISCOVERY["fail"] == "rate":
            raise FeedRateLimited(600)
        if FAKE_DISCOVERY["fail"]:
            raise AdapterError("fake discovery failure")
        data = self._load("search_campaigns")
        if FAKE_DISCOVERY["campaigns"] is not None:
            data = dict(data, campaigns=FAKE_DISCOVERY["campaigns"])
        board = filters.get("board")
        if board:
            data = dict(data, campaigns=[c for c in data["campaigns"] if c.get("boardSlug") == board])
        return data

    def brief(self, ref: str) -> dict:
        FAKE_DISCOVERY["calls"].append(("brief", ref))
        data = self._load("get_brief")
        overrides = (FAKE_DISCOVERY.get("briefs") or {}).get(ref)
        return overrides if overrides is not None else data

    def analytics(self, ref: str) -> dict:
        FAKE_DISCOVERY["calls"].append(("analytics", ref))
        return self._load("get_campaign_analytics")

    def assets(self, ref: str) -> dict:
        return self._load("get_assets")


@register
class ManualDiscovery(Adapter):
    """Declared fallback: no feed; campaigns come in by paste, share or screenshot."""
    stage, provider = "discovery", "manual"

    def search(self, **filters) -> dict:
        return {"campaigns": []}

    def brief(self, ref: str) -> dict:
        raise AppError("No campaign feed is available right now. Open the brief on the marketplace.", "feed_unavailable")

    analytics = assets = brief
