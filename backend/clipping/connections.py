"""Connected platform accounts: start and finish an OAuth sign-in, list states, disconnect.

Tokens never leave `vault`: this module stores them sealed and returns only {id, platform, state, scopes, expires_at, ...}.
The platform redirect carries no session, so the callback is identified by the one-time `state` created in `start`."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import secrets
import uuid

import psycopg
from psycopg.types.json import Jsonb

from . import vault
from .adapters.oauth import PKCE_PLATFORMS, PLATFORM_LABEL, PLATFORMS, provider_name, redirect_uri
from .alerts import registry
from .core import AppError, audit
from .db import Actor, Database

STATE_MINUTES = 10
EXPIRING_DAYS = 7
BAD_STATE = "This sign-in link is not valid or was already used. Start again from Profile."
VIEW = "id, account_id, platform, handle, state, scopes, expires_at, refresh_expires_at, key_id, last_error"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _label(platform: str) -> str:
    return PLATFORM_LABEL[platform]


def _uuid(value, message: str = "That account is not connected.", code: str = "not_connected") -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise AppError(message, code) from None


def _check_platform(platform: str) -> None:
    if platform not in PLATFORMS:
        raise AppError("Choose YouTube, Instagram or TikTok.", "bad_platform")


def _view(row: dict, key_id: str | None) -> dict:
    """What the web app may see. Never a token or ciphertext. `state` is connected, expiring or needs_reconnect."""
    now = dt.datetime.now(dt.timezone.utc)
    state = row["state"]
    if key_id is None or row["key_id"] != key_id:       # no vault key, or another one: the sign-in cannot be used
        state = "needs_reconnect"
    elif state == "connected" and row["refresh_expires_at"] is not None:
        left = row["refresh_expires_at"] - now
        state = "needs_reconnect" if left <= dt.timedelta(0) else "expiring" if left < dt.timedelta(days=EXPIRING_DAYS) else "connected"
    return {"id": str(row["id"]), "account_id": str(row["account_id"]), "platform": row["platform"], "handle": row["handle"], "state": state,
            "scopes": row["scopes"], "expires_at": row["expires_at"], "refresh_expires_at": row["refresh_expires_at"]}


def _current_key_id() -> str | None:
    return vault.key_id() if vault.configured() else None


def list_all(conn) -> dict:
    kid = _current_key_id()
    rows = conn.execute(f"select {VIEW} from platform_connections order by created_at").fetchall()
    return {"vault_ready": kid is not None, "platforms": [{"platform": p, "label": _label(p)} for p in PLATFORMS],
            "connections": [_view(r, kid) for r in rows]}


def for_account(conn, account_id) -> dict | None:
    """{id, platform, state, scopes, expires_at} for the account's connection, or None. Never tokens."""
    row = conn.execute(f"select {VIEW} from platform_connections where account_id = %s", (account_id,)).fetchone()
    if row is None:
        return None
    v = _view(row, _current_key_id())
    return {k: v[k] for k in ("id", "platform", "state", "scopes", "expires_at")}


def _human(actor: Actor) -> None:
    if actor.kind != "operator" or actor.role not in ("admin", "operator"):
        raise AppError("Only you can connect or disconnect an account.", "human_only")


# ------------------------------------------------------------------ start

def start(conn, actor: Actor, platform: str, account_id: str | None = None) -> dict:
    """Begin a sign-in. Returns {"url"} to send the browser to. Refused with a plain message when the vault key is missing."""
    _human(actor)
    _check_platform(platform)
    kid = vault.key_id()                                  # raises VaultUnavailable: connecting without a vault is refused
    if account_id:
        account_id = _uuid(account_id, f"Choose one of your {_label(platform)} accounts.", "bad_account")
        account = conn.execute("select platform from accounts where id = %s and owner_operator_id = %s",
                               (account_id, actor.operator_id)).fetchone()
        if account is None or account["platform"] != platform:
            raise AppError(f"Choose one of your {_label(platform)} accounts.", "bad_account")
    conn.execute("delete from oauth_states where expires_at < now() - interval '1 day'")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48) if platform in PKCE_PLATFORMS else None
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode() if verifier else None
    state_id = uuid.uuid4()
    blob, _ = vault.seal({"verifier": verifier}, f"state:{state_id}")
    conn.execute(
        """insert into oauth_states (id, owner_operator_id, state_hash, platform, account_id, verifier_ct, key_id, expires_at)
           values (%s, %s, %s, %s, %s, %s, %s, now() + make_interval(mins => %s))""",
        (state_id, actor.operator_id, _hash(state), platform, account_id or None, blob, kid, STATE_MINUTES))
    result, _ = registry.call(conn, "oauth", "authorize_url",
                              {"platform": platform, "state": state, "code_challenge": challenge, "redirect": redirect_uri(platform)},
                              actor.operator_id, provider=provider_name())
    audit(conn, "connection.start", "oauth_states", state_id, {"platform": platform})
    return {"url": result["url"]}


# ------------------------------------------------------------------ callback

def complete(db: Database, platform: str, code: str, state: str) -> dict:
    """Finish a sign-in from the platform's redirect. The state is looked up and used up first, in its own transaction, so a
    failed attempt cannot be replayed. Returns the new connection's public view."""
    _check_platform(platform)
    if not code or not state:
        raise AppError(BAD_STATE, "bad_state")
    with db.session(Actor.system()) as conn:
        pending = conn.execute(
            """update oauth_states set used_at = now() where state_hash = %s and used_at is null and expires_at > now()
               returning *""", (_hash(state),)).fetchone()
        owner = conn.execute("select id, role from operators where id = %s and status = 'active'",
                             (pending["owner_operator_id"],)).fetchone() if pending else None
    if pending is None or owner is None or pending["platform"] != platform:
        raise AppError(BAD_STATE, "bad_state")
    actor = Actor(str(owner["id"]), owner["role"])
    with db.session(actor) as conn:
        verifier = vault.open_(pending["verifier_ct"], pending["key_id"], f"state:{pending['id']}").get("verifier")
        result, _ = registry.call(conn, "oauth", "exchange_code",
                                  {"platform": platform, "code": code, "code_verifier": verifier, "redirect": redirect_uri(platform)},
                                  actor.operator_id, provider=provider_name())
        return _save(conn, actor, platform, pending["account_id"], result)


def _save(conn, actor: Actor, platform: str, account_id, result) -> dict:
    """Validate the platform's answer (untrusted), find or create the account, and store the sealed tokens."""
    if not isinstance(result, dict) or not isinstance(result.get("access_token"), str) or not result["access_token"] \
            or not isinstance(result.get("external_account_id"), str) or not result["external_account_id"]:
        raise AppError(f"{_label(platform)} sent an answer we could not read. Try again.", "oauth_bad_response")
    external = result["external_account_id"][:200]
    taken = AppError(f"That {_label(platform)} account is already connected to someone else.", "account_taken")
    if conn.execute("select app.identity_taken(%s, %s) as t", (platform, external)).fetchone()["t"]:
        raise taken
    handle = (str(result.get("handle") or "").strip().lstrip("@") or f"{platform}-{external[:8]}")[:100]
    account = _account_for(conn, actor, platform, account_id, external, handle, taken)
    existing = conn.execute("select id from platform_connections where account_id = %s for update", (account["id"],)).fetchone()
    conn_id = existing["id"] if existing else uuid.uuid4()
    refresh = result.get("refresh_token") if isinstance(result.get("refresh_token"), str) else None
    blob, kid = vault.seal({"access": result["access_token"], "refresh": refresh}, vault.aad_for(conn_id))
    scopes = [s for s in (result.get("scopes") or []) if isinstance(s, str)][:20]
    now = dt.datetime.now(dt.timezone.utc)
    expires, refresh_in = (now + dt.timedelta(seconds=n) if n else None
                           for n in (vault.clean_seconds(result.get("expires_in")), vault.clean_seconds(result.get("refresh_expires_in"))))
    cols = {"platform": platform, "external_account_id": external, "handle": handle, "state": "connected", "scopes": Jsonb(scopes),
            "secret_ct": blob, "key_id": kid, "expires_at": expires, "refresh_expires_at": refresh_in, "last_error": None}
    try:
        with conn.transaction():
            if existing:
                conn.execute(f"update platform_connections set {', '.join(f'{c} = %s' for c in cols)} where id = %s", (*cols.values(), conn_id))
            else:
                names = ", ".join(("id", "account_id", "owner_operator_id", *cols))
                conn.execute(f"insert into platform_connections ({names}) values ({', '.join(['%s'] * (3 + len(cols)))})",
                             (conn_id, account["id"], actor.operator_id, *cols.values()))
    except psycopg.errors.UniqueViolation:                # a race: tell another owner's claim apart from our own duplicate
        if conn.execute("select app.identity_taken(%s, %s) as t", (platform, external)).fetchone()["t"]:
            raise taken from None
        raise AppError(f"That {_label(platform)} account is already connected here. Disconnect the other one first.", "already_connected") from None
    audit(conn, "connection.connected", "platform_connections", conn_id, {"platform": platform})
    row = conn.execute(f"select {VIEW} from platform_connections where id = %s", (conn_id,)).fetchone()
    return _view(row, kid)


def _account_for(conn, actor: Actor, platform: str, account_id, external: str, handle: str, taken: AppError) -> dict:
    """The owner's account this sign-in belongs to: the one chosen at start, one already known by id or handle, or a new one."""
    if account_id:
        account = conn.execute("select id, platform_account_id from accounts where id = %s and owner_operator_id = %s",
                               (account_id, actor.operator_id)).fetchone()
        if account is None:
            raise AppError("That account no longer exists. Start again.", "bad_account")
        if account["platform_account_id"] and account["platform_account_id"] != external:
            raise AppError(f"That is a different {_label(platform)} account than the one you chose. Sign in with the right one.", "wrong_account")
    else:
        account = conn.execute(
            """select id, platform_account_id from accounts where platform = %s and owner_operator_id = %s
               and (platform_account_id = %s or lower(handle) = lower(%s)) order by (platform_account_id = %s) desc limit 1""",
            (platform, actor.operator_id, external, handle, external)).fetchone()
    if account is None:
        try:
            with conn.transaction():
                account = conn.execute(
                    """insert into accounts (platform, handle, platform_account_id, owner_operator_id) values (%s, %s, %s, %s)
                       returning id, platform_account_id""", (platform, handle, external, actor.operator_id)).fetchone()
        except psycopg.errors.UniqueViolation:
            raise taken from None
    elif account["platform_account_id"] != external:
        conn.execute("update accounts set platform_account_id = %s where id = %s", (external, account["id"]))
    return account


# ------------------------------------------------------------------ disconnect

def disconnect(conn, actor: Actor, connection_id) -> dict:
    """Ask the platform to revoke the token where it can, then delete the sealed tokens whatever the answer."""
    _human(actor)
    row = conn.execute("select * from platform_connections where id = %s for update", (_uuid(connection_id),)).fetchone()
    if row is None:
        raise AppError("That account is not connected.", "not_connected")
    revoked = False
    if row["state"] == "connected" and vault.configured():
        try:
            secret = vault.open_(row["secret_ct"], row["key_id"], vault.aad_for(row["id"]))
            result, _ = registry.call(conn, "oauth", "revoke",
                                      {"platform": row["platform"], "token": secret.get("refresh") or secret.get("access") or ""},
                                      actor.operator_id, provider=provider_name())
            revoked = bool(result.get("ok"))
        except AppError:                                  # the platform or vault could not help: still remove our copy
            revoked = False
    conn.execute("delete from platform_connections where id = %s", (row["id"],))
    audit(conn, "connection.disconnected", "platform_connections", row["id"], {"platform": row["platform"], "revoked": revoked})
    return {"disconnected": True, "revoked": revoked, "platform": row["platform"]}
