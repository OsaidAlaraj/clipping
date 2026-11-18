"""Token vault. OAuth tokens are stored only as AES-GCM ciphertext; the key is TOKEN_VAULT_KEY in .env (base64 of 32 bytes).

- A sealed blob is `nonce || ciphertext+tag` and is bound (as associated data) to the row it belongs to, so a blob copied to
  another row, or altered, fails to open. Every failure closes: no partial or guessed value is ever returned.
- Tokens are decrypted only inside `access_token` (and `open_`) and handed straight to the caller. They are never logged,
  audited or put in an error message.
- Barricade: everything read from the database or a platform is validated here before it is used."""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import json
import os

from . import config
from .alerts import raise_alert, registry
from .adapters.oauth import PLATFORM_LABEL, TokenRevoked, provider_name
from .core import AppError, audit

NONCE_BYTES = 12
REFRESH_SKEW = dt.timedelta(seconds=120)


class VaultUnavailable(AppError):
    def __init__(self, message: str = "The token vault key is missing. Add TOKEN_VAULT_KEY to .env (see .env.example), then restart."):
        super().__init__(message, "vault_unavailable")


class VaultError(AppError):
    """A sealed value could not be opened (wrong key or altered data). Nothing is returned."""

    def __init__(self, message: str = "This saved sign-in could not be read. Connect the account again.", code: str = "vault_unreadable"):
        super().__init__(message, code)


class NeedsReconnect(AppError):
    """The connection cannot give a token until the owner connects it again. A caller inside a transaction must catch this
    there (not let it roll the transaction back) so the needs_reconnect mark and the alert are saved."""

    def __init__(self, platform: str):
        super().__init__(f"Connect your {PLATFORM_LABEL.get(platform, platform)} account again in Profile.", "needs_reconnect")


def _key() -> bytes:
    raw = config.env("TOKEN_VAULT_KEY")
    if not raw:
        raise VaultUnavailable()
    try:
        key = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        key = b""
    if len(key) != 32:
        raise VaultUnavailable("TOKEN_VAULT_KEY is not a valid key: it must be base64 of 32 bytes. Make a new one (see .env.example) and restart.")
    return key


def configured() -> bool:
    try:
        _key()
        return True
    except VaultUnavailable:
        return False


def key_id() -> str:
    """A short fingerprint of the key (not the key): tells a row which key sealed it."""
    return hashlib.sha256(b"vault-key-id:" + _key()).hexdigest()[:8]


def _aead():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM(_key())


def seal(data: dict, aad: str) -> tuple[bytes, str]:
    """Encrypt a JSON-able dict. Returns (blob, key id). Raises VaultUnavailable when there is no key."""
    nonce = os.urandom(NONCE_BYTES)
    blob = nonce + _aead().encrypt(nonce, json.dumps(data).encode(), aad.encode())
    return blob, key_id()


def open_(blob, stored_key_id: str, aad: str) -> dict:
    """Decrypt a sealed dict, or fail closed. Raises VaultUnavailable (no key) or VaultError (other key, wrong row, altered)."""
    from cryptography.exceptions import InvalidTag

    aead = _aead()                                    # raises VaultUnavailable first
    if stored_key_id != key_id():
        raise VaultError("The vault key changed since this account was connected. Connect it again.", "vault_key_changed")
    blob = bytes(blob or b"")
    if len(blob) <= NONCE_BYTES:
        raise VaultError(code="vault_tampered")
    try:
        data = json.loads(aead.decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], aad.encode()))
    except (InvalidTag, ValueError):
        raise VaultError(code="vault_tampered")
    if not isinstance(data, dict):
        raise VaultError(code="vault_tampered")
    return data


def aad_for(connection_id) -> str:
    return f"connection:{connection_id}"


def _mark_needs_reconnect(conn, row: dict, reason: str) -> None:
    conn.execute("update platform_connections set state = 'needs_reconnect', last_error = %s where id = %s", (reason[:200], row["id"]))
    label = PLATFORM_LABEL.get(row["platform"], row["platform"])
    raise_alert(conn, row["owner_operator_id"], "connection_reconnect", "warn", "platform_connections", row["id"],
                f"{label} (@{row['handle'] or 'account'}) needs to be connected again. Open Profile and tap Connect.")
    audit(conn, "connection.needs_reconnect", "platform_connections", row["id"], {"platform": row["platform"], "reason": reason[:80]})


def clean_seconds(value) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < 10 ** 9 else None


def access_token(conn, connection_id) -> str:
    """A usable access token for this connection, refreshed first when it is about to expire. In memory only.
    Raises AppError('not_connected') for an unknown or someone else's connection (row-level security hides it),
    NeedsReconnect when the owner must sign in again, VaultUnavailable/VaultError when the vault cannot open the row."""
    row = conn.execute("select * from platform_connections where id = %s for update", (connection_id,)).fetchone()
    if row is None:
        raise AppError("That account is not connected.", "not_connected")
    if row["state"] == "needs_reconnect":
        raise NeedsReconnect(row["platform"])
    aad = aad_for(row["id"])
    try:
        secret = open_(row["secret_ct"], row["key_id"], aad)
    except VaultError as exc:
        if exc.code == "vault_tampered":              # a changed key may come back; altered data never will
            _mark_needs_reconnect(conn, row, "stored sign-in unreadable")
        raise
    access = secret.get("access")
    if not isinstance(access, str) or not access:
        _mark_needs_reconnect(conn, row, "stored sign-in unreadable")
        raise VaultError(code="vault_tampered")

    now = dt.datetime.now(dt.timezone.utc)
    if row["expires_at"] is None or row["expires_at"] - now > REFRESH_SKEW:
        return access
    try:
        result, _ = registry.call(conn, "oauth", "refresh",
                                  {"platform": row["platform"], "refresh_token": secret.get("refresh"), "access_token": access},
                                  row["owner_operator_id"], provider=provider_name())
        new_access = result.get("access_token") if isinstance(result, dict) else None
        if not isinstance(new_access, str) or not new_access:
            raise AppError("The platform sent an answer we could not read.", "oauth_bad_response")
    except TokenRevoked as exc:
        _mark_needs_reconnect(conn, row, str(exc))
        raise NeedsReconnect(row["platform"]) from None
    except AppError:
        if row["expires_at"] > now:                   # the refresh service is down but this token still works
            return access
        raise
    refresh = result.get("refresh_token")
    secret = {"access": new_access, "refresh": refresh if isinstance(refresh, str) and refresh else secret.get("refresh")}
    blob, kid = seal(secret, aad)
    expires_in, refresh_in = clean_seconds(result.get("expires_in")), clean_seconds(result.get("refresh_expires_in"))
    conn.execute(
        """update platform_connections set secret_ct = %s, key_id = %s, last_refreshed_at = now(), last_error = null,
               expires_at = case when %s::int is null then null else now() + make_interval(secs => %s::int) end,
               refresh_expires_at = case when %s::int is null then refresh_expires_at else now() + make_interval(secs => %s::int) end
           where id = %s""",
        (blob, kid, expires_in, expires_in, refresh_in, refresh_in, row["id"]))
    audit(conn, "connection.refreshed", "platform_connections", row["id"], {"platform": row["platform"]})
    return new_access
