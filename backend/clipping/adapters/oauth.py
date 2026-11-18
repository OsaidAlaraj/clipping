"""OAuth sign-in for the posting platforms (YouTube, Instagram, TikTok): authorize URL, code exchange, refresh, revoke.

The real provider is written from the official docs (Google OAuth 2.0, Instagram Login, TikTok Login Kit v2) and has not
run against the live services yet: that check waits for the owner's developer apps. Client ids and secrets are read from
environment variables at call time. Responses are untrusted: only the fields below are used, and an error never echoes a
response body or URL (they can carry tokens)."""
from __future__ import annotations

import secrets
from urllib.parse import urlencode

from .. import config
from ..core import AppError
from .base import Adapter, AdapterError, http_client, register

PLATFORM_LABEL = {"youtube_shorts": "YouTube", "instagram_reels": "Instagram", "tiktok": "TikTok"}
PLATFORMS = tuple(PLATFORM_LABEL)
PKCE_PLATFORMS = {"youtube_shorts"}   # the TikTok PKCE encoding differs from RFC 7636: check it live before switching it on


class TokenRevoked(AppError):
    """The platform no longer accepts the refresh token (revoked, expired or password changed)."""

    def __init__(self, platform: str):
        super().__init__(f"{PLATFORM_LABEL.get(platform, platform)} no longer accepts this sign-in. Connect the account again.", "token_revoked")


def provider_name() -> str:
    """The fake until the developer apps exist (CLIPPING_USE_FAKES stays on), the real endpoints after."""
    return "fake" if config.use_fakes() else "platforms"


def redirect_uri(platform: str) -> str:
    base = (config.env("OAUTH_REDIRECT_BASE") or config.env("CLIPPING_PUBLIC_ORIGIN") or "http://localhost:3000").rstrip("/")
    return f"{base}/api/connections/{platform}/callback"


def _cred(platform: str) -> tuple[str, str]:
    names = {"youtube_shorts": ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"), "instagram_reels": ("INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET"),
             "tiktok": ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET")}[platform]
    values = [config.env(n) for n in names]
    if not all(values):
        raise AppError(f"{PLATFORM_LABEL[platform]} sign-in is not set up yet. Add {names[0]} and {names[1]} to .env first.", "oauth_not_configured")
    return values[0], values[1]  # type: ignore[return-value]


def _json(response, on_permanent: Exception | None = None) -> dict:
    """A 4xx means the platform refused (final); anything else bad is transient and the registry retries it."""
    if response.status_code in (400, 401, 403):
        raise (on_permanent or AppError("The platform did not accept the sign-in. Start again.", "oauth_failed"))
    if response.status_code >= 400:
        raise AdapterError(f"HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError:
        raise AdapterError("unreadable response")
    if not isinstance(data, dict):
        raise AdapterError("unexpected response")
    return data


def _seconds(value) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


@register
class PlatformOAuth(Adapter):
    stage, provider = "oauth", "platforms"

    def authorize_url(self, platform: str, state: str, code_challenge: str | None, redirect: str) -> dict:
        client_id, _ = _cred(platform)
        if platform == "youtube_shorts":
            params = {"client_id": client_id, "redirect_uri": redirect, "response_type": "code", "access_type": "offline", "prompt": "consent",
                      "scope": "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly",
                      "state": state, "code_challenge": code_challenge, "code_challenge_method": "S256"}
            return {"url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)}
        if platform == "instagram_reels":
            params = {"client_id": client_id, "redirect_uri": redirect, "response_type": "code", "state": state,
                      "scope": "instagram_business_basic,instagram_business_content_publish"}
            return {"url": "https://www.instagram.com/oauth/authorize?" + urlencode(params)}
        params = {"client_key": client_id, "redirect_uri": redirect, "response_type": "code", "state": state, "scope": "user.info.basic,video.upload"}
        return {"url": "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(params)}

    def exchange_code(self, platform: str, code: str, code_verifier: str | None, redirect: str) -> dict:
        client_id, secret = _cred(platform)
        with http_client(30) as client:
            if platform == "youtube_shorts":
                data = _json(client.post("https://oauth2.googleapis.com/token", data={
                    "code": code, "client_id": client_id, "client_secret": secret, "redirect_uri": redirect,
                    "grant_type": "authorization_code", "code_verifier": code_verifier or ""}))
                me = _json(client.get("https://www.googleapis.com/youtube/v3/channels", params={"part": "snippet", "mine": "true"},
                                      headers={"Authorization": f"Bearer {data.get('access_token')}"}))
                item = (me.get("items") or [{}])[0]
                snippet = item.get("snippet") or {}
                return self._result(data, item.get("id"), snippet.get("customUrl") or snippet.get("title"))
            if platform == "instagram_reels":
                short = _json(client.post("https://api.instagram.com/oauth/access_token", data={
                    "client_id": client_id, "client_secret": secret, "grant_type": "authorization_code", "redirect_uri": redirect, "code": code}))
                long = _json(client.get("https://graph.instagram.com/access_token", params={
                    "grant_type": "ig_exchange_token", "client_secret": secret, "access_token": short.get("access_token")}))
                me = _json(client.get("https://graph.instagram.com/v21.0/me", params={"fields": "user_id,username"},
                                      headers={"Authorization": f"Bearer {long.get('access_token')}"}))
                out = self._result(long, me.get("user_id") or short.get("user_id"), me.get("username"))
                out["refresh_token"] = None            # Instagram refreshes the long-lived token itself
                out["refresh_expires_in"] = out["expires_in"]
                return out
            data = _json(client.post("https://open.tiktokapis.com/v2/oauth/token/", data={
                "client_key": client_id, "client_secret": secret, "code": code, "grant_type": "authorization_code", "redirect_uri": redirect}))
            handle = None
            try:
                info = _json(client.get("https://open.tiktokapis.com/v2/user/info/", params={"fields": "open_id,display_name"},
                                        headers={"Authorization": f"Bearer {data.get('access_token')}"}))
                handle = ((info.get("data") or {}).get("user") or {}).get("display_name")
            except (AppError, AdapterError):
                pass                                    # the name is a nicety; open_id identifies the account
            return self._result(data, data.get("open_id"), handle)

    @staticmethod
    def _result(data: dict, external_id, handle) -> dict:
        scope = data.get("scope") or data.get("permissions") or ""
        scopes = scope if isinstance(scope, list) else [s for s in str(scope).replace(",", " ").split() if s]
        return {"access_token": data.get("access_token"), "refresh_token": data.get("refresh_token"), "expires_in": _seconds(data.get("expires_in")),
                "refresh_expires_in": _seconds(data.get("refresh_expires_in")), "scopes": scopes,
                "external_account_id": str(external_id or ""), "handle": handle}

    def refresh(self, platform: str, refresh_token: str | None, access_token: str) -> dict:
        client_id, secret = _cred(platform)
        revoked = TokenRevoked(platform)
        with http_client(30) as client:
            if platform == "youtube_shorts":
                data = _json(client.post("https://oauth2.googleapis.com/token", data={
                    "client_id": client_id, "client_secret": secret, "refresh_token": refresh_token, "grant_type": "refresh_token"}), revoked)
            elif platform == "instagram_reels":
                data = _json(client.get("https://graph.instagram.com/refresh_access_token", params={
                    "grant_type": "ig_refresh_token", "access_token": access_token}), revoked)
                data["refresh_expires_in"] = data.get("expires_in")
            else:
                data = _json(client.post("https://open.tiktokapis.com/v2/oauth/token/", data={
                    "client_key": client_id, "client_secret": secret, "grant_type": "refresh_token", "refresh_token": refresh_token}), revoked)
        return {"access_token": data.get("access_token"), "refresh_token": data.get("refresh_token"),
                "expires_in": _seconds(data.get("expires_in")), "refresh_expires_in": _seconds(data.get("refresh_expires_in"))}

    def revoke(self, platform: str, token: str) -> dict:
        """Best effort. Instagram has no revoke endpoint: the owner removes the app in Instagram's settings."""
        if platform == "instagram_reels":
            return {"ok": False}
        client_id, secret = _cred(platform)
        with http_client(30) as client:
            if platform == "youtube_shorts":
                client.post("https://oauth2.googleapis.com/revoke", data={"token": token})
            elif platform == "tiktok":
                client.post("https://open.tiktokapis.com/v2/oauth/revoke/", data={"client_key": client_id, "client_secret": secret, "token": token})
        return {"ok": True}


FAKE_OAUTH: dict = {"accounts": {}, "fail": None, "revoked": False, "issued": [], "calls": [], "expires_in": 3600, "refresh_expires_in": 86400 * 30}


def reset_fake() -> None:
    FAKE_OAUTH.update(accounts={}, fail=None, revoked=False, issued=[], calls=[], expires_in=3600, refresh_expires_in=86400 * 30)


@register
class FakeOAuth(Adapter):
    """Control: FAKE_OAUTH['accounts'][platform] = (external id, handle); 'fail' = 'transient' | 'refused'; 'revoked' makes refresh fail.
    `calls` records method and platform only, never a token."""
    stage, provider = "oauth", "fake"

    def _token(self) -> str:
        token = f"fake-token-{secrets.token_hex(12)}"
        FAKE_OAUTH["issued"].append(token)
        return token

    def authorize_url(self, platform: str, state: str, code_challenge: str | None, redirect: str) -> dict:
        FAKE_OAUTH["calls"].append(("authorize_url", platform))
        query = urlencode({"state": state, "code_challenge": code_challenge or "", "redirect_uri": redirect})
        return {"url": f"https://fake-oauth.test/{platform}/authorize?{query}"}

    def exchange_code(self, platform: str, code: str, code_verifier: str | None, redirect: str) -> dict:
        FAKE_OAUTH["calls"].append(("exchange_code", platform))
        if FAKE_OAUTH["fail"] == "transient":
            raise AdapterError("fake outage")
        refused = AppError("The platform did not accept the sign-in. Start again.", "oauth_failed")
        if FAKE_OAUTH["fail"] == "refused" or not code.startswith("fake-code"):
            raise refused
        if platform in PKCE_PLATFORMS and not code_verifier:
            raise refused
        external, handle = FAKE_OAUTH["accounts"].get(platform, (f"fake-{platform}-1", f"fake_{platform}"))
        return {"access_token": self._token(), "refresh_token": self._token(), "expires_in": FAKE_OAUTH["expires_in"],
                "refresh_expires_in": FAKE_OAUTH["refresh_expires_in"], "scopes": ["upload"], "external_account_id": external, "handle": handle}

    def refresh(self, platform: str, refresh_token: str | None, access_token: str) -> dict:
        FAKE_OAUTH["calls"].append(("refresh", platform))
        if FAKE_OAUTH["revoked"]:
            raise TokenRevoked(platform)
        if FAKE_OAUTH["fail"] == "transient":
            raise AdapterError("fake outage")
        return {"access_token": self._token(), "refresh_token": None, "expires_in": FAKE_OAUTH["expires_in"], "refresh_expires_in": None}

    def revoke(self, platform: str, token: str) -> dict:
        FAKE_OAUTH["calls"].append(("revoke", platform))
        return {"ok": True}
