"""Passkey sign-in (decision 2026-11-17). The app is reached over Tailscale (private HTTPS) and every
request carries a session cookie created by a passkey sign-in (Face ID, fingerprint or Windows Hello).

- The first passkey needs a one-time setup code, printed in the laptop terminal at start.
- A signed-in device can create a code to add another device (for example the phone).
- Only public keys and hashes are stored. Nothing secret lives in the database.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
from urllib.parse import urlparse

import webauthn
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
                                      ResidentKeyRequirement, UserVerificationRequirement)

from . import config
from .core import AppError, audit
from .db import Actor

COOKIE = "clipping_session"
SESSION_DAYS = 30
SETUP_MINUTES = 30
CHALLENGE_MINUTES = 5


class AuthError(AppError):
    status = 401

    def __init__(self, message: str = "Sign in with your passkey."):
        super().__init__(message, "signin_required")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def mode() -> str:
    """'passkey' (default) or 'local' (single owner, no sign-in; tests and trusted local runs)."""
    return config.env("AUTH_MODE", "passkey")


def allowed_origins() -> list[str]:
    origins = ["http://localhost:3000", "http://localhost:8787"]
    public = config.env("CLIPPING_PUBLIC_ORIGIN")
    if public:
        origins.append(public.rstrip("/"))
    return origins


def origin_and_rp(origin: str | None) -> tuple[str, str]:
    """The relying party is the host the browser is on (localhost or the ts.net name)."""
    if not origin or origin.rstrip("/") not in allowed_origins():
        raise AppError("Open the app from its own address to use passkeys.", "bad_origin")
    host = urlparse(origin).hostname or ""
    return origin.rstrip("/"), host


def owner(conn) -> dict:
    row = conn.execute("select id, name, role from operators where role = 'admin' order by created_at limit 1").fetchone()
    if row is None:
        raise AppError("No owner account exists yet.", "no_owner")
    return row


# ---------------------------------------------------------------- setup codes

def has_passkeys(conn) -> bool:
    return conn.execute("select 1 from auth_credentials limit 1").fetchone() is not None


def new_setup_code(conn, operator_id) -> str:
    """A 10-character code, valid for 30 minutes and single use."""
    code = "-".join(secrets.token_hex(5).upper()[i:i + 5] for i in (0, 5))
    conn.execute(
        "insert into auth_challenges (kind, value_hash, operator_id, expires_at) values ('setup_code', %s, %s, %s)",
        (_hash(code), operator_id, _now() + dt.timedelta(minutes=SETUP_MINUTES)),
    )
    return code


def startup_code(db) -> str | None:
    """At start, when no passkey exists yet, print a setup code for the first device."""
    if mode() != "passkey":
        return None
    with db.session(Actor.system()) as conn:
        if has_passkeys(conn):
            return None
        conn.execute("update auth_challenges set used_at = now() where kind = 'setup_code' and used_at is null")
        return new_setup_code(conn, owner(conn)["id"])


def _take(conn, kind: str, value: str, consume: bool = True) -> dict:
    row = conn.execute(
        """select * from auth_challenges where kind = %s and value_hash = %s and used_at is null and expires_at > now()
           order by created_at desc limit 1""",
        (kind, _hash(value)),
    ).fetchone()
    if row is None:
        raise AppError("That code has expired or was already used." if kind == "setup_code" else "Sign-in timed out. Try again.",
                       "bad_code" if kind == "setup_code" else "expired")
    if consume:
        conn.execute("update auth_challenges set used_at = now() where id = %s", (row["id"],))
    return row


# ---------------------------------------------------------------- registration

def registration_options(conn, origin: str | None, setup_code: str | None, signed_in: Actor | None) -> dict:
    _, rp_id = origin_and_rp(origin)
    if signed_in is not None:
        operator_id = signed_in.operator_id
    elif setup_code:
        operator_id = str(_take(conn, "setup_code", setup_code.strip().upper(), consume=False)["operator_id"])
    else:
        raise AuthError("Enter the setup code shown on your laptop.")
    op = conn.execute("select id, name from operators where id = %s", (operator_id,)).fetchone()
    existing = conn.execute("select credential_id from auth_credentials where operator_id = %s and rp_id = %s", (operator_id, rp_id)).fetchall()
    options = webauthn.generate_registration_options(
        rp_id=rp_id, rp_name="Clipping", user_name=op["name"], user_id=str(op["id"]).encode(), user_display_name=op["name"],
        authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.PREFERRED,
                                                               user_verification=UserVerificationRequirement.PREFERRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=webauthn.base64url_to_bytes(r["credential_id"])) for r in existing],
    )
    challenge = bytes_to_base64url(options.challenge)
    conn.execute(
        "insert into auth_challenges (kind, value_hash, operator_id, rp_id, expires_at) values ('register', %s, %s, %s, %s)",
        (_hash(challenge), operator_id, rp_id, _now() + dt.timedelta(minutes=CHALLENGE_MINUTES)),
    )
    return json.loads(webauthn.options_to_json(options))


def verify_registration(conn, origin: str | None, credential: dict, setup_code: str | None, signed_in: Actor | None,
                        device_name: str | None) -> dict:
    origin_value, rp_id = origin_and_rp(origin)
    challenge = _client_challenge(credential)
    pending = _take(conn, "register", challenge)
    if signed_in is None:
        code_row = _take(conn, "setup_code", (setup_code or "").strip().upper())   # the code is used up only on success
        if str(code_row["operator_id"]) != str(pending["operator_id"]):
            raise AuthError()
    elif str(signed_in.operator_id) != str(pending["operator_id"]):
        raise AuthError()
    try:
        verified = webauthn.verify_registration_response(credential=credential, expected_challenge=webauthn.base64url_to_bytes(challenge),
                                                         expected_rp_id=rp_id, expected_origin=origin_value)
    except Exception as exc:
        raise AppError(f"The passkey could not be verified ({type(exc).__name__}).", "passkey_invalid") from None
    conn.execute(
        """insert into auth_credentials (operator_id, credential_id, public_key, sign_count, rp_id, device_name)
           values (%s, %s, %s, %s, %s, %s)""",
        (pending["operator_id"], bytes_to_base64url(verified.credential_id), bytes_to_base64url(verified.credential_public_key),
         verified.sign_count, rp_id, (device_name or "")[:80] or None),
    )
    audit(conn, "auth.passkey_added", "operators", pending["operator_id"], {"rp_id": rp_id})
    return {"operator_id": str(pending["operator_id"])}


# ---------------------------------------------------------------- sign-in

def login_options(conn, origin: str | None) -> dict:
    _, rp_id = origin_and_rp(origin)
    creds = conn.execute("select credential_id from auth_credentials where rp_id = %s", (rp_id,)).fetchall()
    if not creds:
        raise AppError("No passkey is set up for this address yet. Use the setup code.", "no_passkey")
    options = webauthn.generate_authentication_options(
        rp_id=rp_id, user_verification=UserVerificationRequirement.PREFERRED,
        allow_credentials=[PublicKeyCredentialDescriptor(id=webauthn.base64url_to_bytes(r["credential_id"])) for r in creds],
    )
    challenge = bytes_to_base64url(options.challenge)
    conn.execute(
        "insert into auth_challenges (kind, value_hash, rp_id, expires_at) values ('login', %s, %s, %s)",
        (_hash(challenge), rp_id, _now() + dt.timedelta(minutes=CHALLENGE_MINUTES)),
    )
    return json.loads(webauthn.options_to_json(options))


def verify_login(conn, origin: str | None, credential: dict) -> str:
    origin_value, rp_id = origin_and_rp(origin)
    challenge = _client_challenge(credential)
    _take(conn, "login", challenge)
    stored = conn.execute("select * from auth_credentials where credential_id = %s and rp_id = %s",
                          (credential.get("id") or credential.get("rawId"), rp_id)).fetchone()
    if stored is None:
        raise AuthError("This passkey is not registered here.")
    try:
        verified = webauthn.verify_authentication_response(
            credential=credential, expected_challenge=webauthn.base64url_to_bytes(challenge), expected_rp_id=rp_id,
            expected_origin=origin_value, credential_public_key=webauthn.base64url_to_bytes(stored["public_key"]),
            credential_current_sign_count=stored["sign_count"])
    except Exception as exc:
        raise AuthError(f"Sign-in failed ({type(exc).__name__}).") from None
    conn.execute("update auth_credentials set sign_count = %s, last_used_at = now() where id = %s", (verified.new_sign_count, stored["id"]))
    return create_session(conn, stored["operator_id"])


def _client_challenge(credential: dict) -> str:
    try:
        client_data = json.loads(webauthn.base64url_to_bytes(credential["response"]["clientDataJSON"]))
        return client_data["challenge"]
    except Exception:
        raise AppError("The passkey response is malformed.", "passkey_invalid") from None


# ---------------------------------------------------------------- sessions

def create_session(conn, operator_id, user_agent: str | None = None) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(
        "insert into auth_sessions (operator_id, token_hash, expires_at, user_agent) values (%s, %s, %s, %s)",
        (operator_id, _hash(token), _now() + dt.timedelta(days=SESSION_DAYS), (user_agent or "")[:200] or None),
    )
    return token


def actor_for_token(conn, token: str | None) -> Actor | None:
    if not token:
        return None
    row = conn.execute(
        """select o.id, o.role from auth_sessions s join operators o on o.id = s.operator_id
           where s.token_hash = %s and s.revoked_at is null and s.expires_at > now() and o.status = 'active'""",
        (_hash(token),),
    ).fetchone()
    return Actor(str(row["id"]), row["role"]) if row else None


def revoke(conn, token: str | None) -> None:
    if token:
        conn.execute("update auth_sessions set revoked_at = now() where token_hash = %s and revoked_at is null", (_hash(token),))
