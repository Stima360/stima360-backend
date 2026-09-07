"""Raw SQL for operator identity and sessions.

Every function takes an already-open cursor and opens no connection of its own.
The service layer owns the transaction, so a login's session insert and its
last_login_at update commit together or not at all.

This layer never sees a raw session token or a plaintext password. It receives
the SHA-256 hash of a token and the encoded PBKDF2 hash of a password; the
originals never leave the service.
"""
from __future__ import annotations

from typing import Any


def find_operator_by_email(cur, email_normalized: str) -> dict[str, Any] | None:
    """Return the operator, with their active membership and its agency.

    One query rather than three: login must judge the account, the membership
    and the agency together, and a single statement keeps that judgement on one
    consistent snapshot.

    LEFT JOIN because a platform admin legitimately holds no membership. The
    membership join is restricted to status='active', so a suspended or revoked
    row yields NULLs rather than a usable scope.
    """
    cur.execute(
        """
        SELECT u.id,
               u.email,
               u.email_normalized,
               u.password_hash,
               u.status,
               u.is_platform_admin,
               m.agency_id,
               m.role,
               m.status AS membership_status,
               a.status AS agency_status
          FROM operator_users u
          LEFT JOIN agency_memberships m
                 ON m.operator_user_id = u.id AND m.status = 'active'
          LEFT JOIN agencies a ON a.id = m.agency_id
         WHERE u.email_normalized = %s
        """,
        (email_normalized,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def mark_login(cur, operator_user_id: int) -> None:
    """Stamp a successful authentication."""
    cur.execute(
        "UPDATE operator_users SET last_login_at = NOW(), updated_at = NOW() "
        "WHERE id = %s",
        (operator_user_id,),
    )


def create_session(cur, operator_user_id: int, token_hash: str, expires_at) -> dict:
    """Persist one session. Only the token's hash is stored.

    No agency_id column is written, deliberately: the session pins no agency,
    so a suspended membership takes effect on the next request rather than at
    the next login (design spec D-3).
    """
    cur.execute(
        """
        INSERT INTO operator_sessions (operator_user_id, token_hash, expires_at)
        VALUES (%s, %s, %s)
        RETURNING id, operator_user_id, created_at, last_seen_at, expires_at
        """,
        (operator_user_id, token_hash, expires_at),
    )
    return dict(cur.fetchone())


def resolve_session(cur, token_hash: str, idle_minutes: int) -> dict[str, Any] | None:
    """Return the live session with the identity and scope it currently implies.

    Everything except the session's own validity is re-read here on every
    request: user status, membership, membership status and agency status. That
    is what makes a revocation effective on the next call instead of at the
    next login.

    The membership join is a LEFT JOIN restricted to status='active'. A
    platform admin with no membership therefore resolves with NULL agency
    columns, which is a legitimate unbound scope rather than a failure. The
    partial unique index uq_agency_memberships_single_active guarantees at most
    one such row, so the join cannot multiply.
    """
    cur.execute(
        """
        SELECT s.id            AS session_id,
               s.expires_at,
               s.last_seen_at,
               u.id            AS user_id,
               u.status        AS user_status,
               u.is_platform_admin,
               m.agency_id,
               m.role,
               m.status        AS membership_status,
               a.name          AS agency_name,
               a.status        AS agency_status
          FROM operator_sessions s
          JOIN operator_users u ON u.id = s.operator_user_id
          LEFT JOIN agency_memberships m
                 ON m.operator_user_id = u.id AND m.status = 'active'
          LEFT JOIN agencies a ON a.id = m.agency_id
         WHERE s.token_hash = %s
           AND s.revoked_at IS NULL
           AND s.expires_at > NOW()
           AND s.last_seen_at > NOW() - (%s || ' minutes')::interval
        """,
        (token_hash, str(idle_minutes)),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def touch_session(cur, session_id: int) -> None:
    """Advance the idle window. Called only after a session has validated."""
    cur.execute(
        "UPDATE operator_sessions SET last_seen_at = NOW() WHERE id = %s",
        (session_id,),
    )


def revoke_session(cur, token_hash: str) -> int:
    """Revoke a session by token hash; return how many rows were affected.

    Already-revoked and unknown tokens both yield 0. The caller must not turn
    that count into a response difference: logout reports the same outcome
    either way, so it cannot be used to probe which tokens exist.
    """
    cur.execute(
        "UPDATE operator_sessions SET revoked_at = NOW() "
        "WHERE token_hash = %s AND revoked_at IS NULL",
        (token_hash,),
    )
    return cur.rowcount


def membership_exists(cur, agency_id: int, operator_user_id: int) -> bool:
    """True when the operator holds an active membership in that agency.

    Used to validate an assignment target against the agency derived from the
    record being assigned, never against a client-supplied agency.
    """
    cur.execute(
        """
        SELECT 1 FROM agency_memberships
         WHERE agency_id = %s AND operator_user_id = %s AND status = 'active'
         LIMIT 1
        """,
        (agency_id, operator_user_id),
    )
    return cur.fetchone() is not None
