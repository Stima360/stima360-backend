"""Login, session resolution and logout.

This layer owns the transaction and is the only place a raw session token or a
plaintext password exists. Neither is ever logged, and neither is ever passed
to the repository: the repository receives a SHA-256 token hash and an encoded
PBKDF2 password hash.

There is deliberately no logging in this module at all. An authentication
failure is the one event most likely to be logged with its cause attached, and
the cause is exactly what must not be recorded or disclosed.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from core.normalization import normalize_email

from . import repository, security
from .context import OperatorContext
from .database import operator_cursor
from .enums import SESSION_IDLE_MINUTES, SESSION_MAX_HOURS
from .exceptions import AuthenticationFailed


# A real PBKDF2 hash of an unguessable value, computed once when this module is
# imported. When an email is unknown the password is verified against this
# instead of skipping the check, so a request for a non-existent account costs
# the same 600k iterations as a request for a real one and login timing does
# not disclose which addresses are registered.
#
# Computed once per process, never per request: regenerating it on each failed
# login would reintroduce the timing difference it exists to remove, and would
# hand an attacker a cheap way to make the server do unbounded work.
_DUMMY_PASSWORD_HASH = security.hash_password(secrets.token_urlsafe(32))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _scope_is_usable(row: dict) -> bool:
    """True when the account, its membership and its agency all permit access.

    A platform admin needs no membership: platform administration sits outside
    the agency hierarchy. Everyone else needs an active membership in an active
    agency.
    """
    if row.get("status") != "active" and row.get("user_status") != "active":
        return False
    if row.get("is_platform_admin"):
        return True
    return (
        row.get("membership_status") == "active"
        and row.get("agency_status") == "active"
    )


def login(email: str, password: str) -> str:
    """Authenticate and return the raw session token.

    The raw token is returned to the caller (the router, which puts it in a
    Set-Cookie header) and nowhere else. Only its hash is persisted.

    Raises AuthenticationFailed, identically, for every failure.
    """
    normalized = normalize_email(email)

    with operator_cursor(commit=True) as (_, cur):
        operator = (
            repository.find_operator_by_email(cur, normalized) if normalized else None
        )

        # Always verify, even with no account: the hashing cost must not depend
        # on whether the address exists.
        stored = operator["password_hash"] if operator else _DUMMY_PASSWORD_HASH
        password_ok = security.verify_password(password, stored)

        if operator is None or not password_ok or not _scope_is_usable(operator):
            raise AuthenticationFailed()

        raw_token = security.generate_session_token()
        repository.create_session(
            cur,
            operator["id"],
            security.hash_session_token(raw_token),
            _utcnow() + timedelta(hours=SESSION_MAX_HOURS),
        )
        repository.mark_login(cur, operator["id"])
        return raw_token


def logout(raw_token: str | None) -> None:
    """Revoke a session. Silent whether or not the token existed.

    Returns None in every case: a caller cannot learn from logout whether a
    token was real.
    """
    if not raw_token:
        return

    with operator_cursor(commit=True) as (_, cur):
        repository.revoke_session(cur, security.hash_session_token(raw_token))


def session_from_token(raw_token: str | None) -> dict | None:
    """Resolve a cookie value into the caller's scope plus session display data.

    Returns None - never a partial result - for an absent, unknown, revoked,
    expired or idle-timed-out session, for a disabled account, and for an
    agency operator whose membership or agency is no longer active.

    The idle window is advanced only after the session has fully validated, so
    a rejected request cannot keep a dead session alive.

    The returned mapping carries the scope under "context" plus the two values
    /me needs that are not part of the scope itself: the agency's display name
    and the session's expiry. They are returned separately, rather than added to
    OperatorContext, because a scope is an authorisation decision and must not
    accumulate presentation fields. A plain dict keeps this module free of any
    dependency on the HTTP layer that consumes it.
    """
    if not raw_token:
        return None

    with operator_cursor(commit=True) as (_, cur):
        row = repository.resolve_session(
            cur, security.hash_session_token(raw_token), SESSION_IDLE_MINUTES
        )
        if row is None or not _scope_is_usable(row):
            return None

        repository.touch_session(cur, row["session_id"])
        return {
            "context": OperatorContext(
                user_id=row["user_id"],
                agency_id=row["agency_id"],
                role=row["role"],
                is_platform_admin=bool(row["is_platform_admin"]),
                session_id=row["session_id"],
                auth_channel="operator_session",
            ),
            "agency_name": row.get("agency_name"),
            "expires_at": row["expires_at"],
        }


def context_from_token(raw_token: str | None) -> OperatorContext | None:
    """Resolve a cookie value into the caller's effective scope, or None."""
    resolved = session_from_token(raw_token)
    return resolved["context"] if resolved else None
