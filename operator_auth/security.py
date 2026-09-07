"""Stateless security primitives for operator authentication.

Standard library only. The approved design spec (D-7) forbids adding a
dependency: requirements.txt pins no passlib, bcrypt or argon2, and introducing
one for this would be scope expansion.

Nothing here touches a database, and nothing here logs. A password and a raw
session token exist only as arguments and return values; neither is ever
written to a log, and neither is ever persisted in its original form.

The cookie helpers are duck-typed over a response object, exactly as
owner/security.py is, so this module imports no web framework.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from .enums import COOKIE_NAME, PBKDF2_ITERATIONS, SESSION_MAX_HOURS

_ALGORITHM = "pbkdf2_sha256"
_SALT_BYTES = 16
_TOKEN_BYTES = 32


# ---------------------------------------------------------------------------
# Session tokens
# ---------------------------------------------------------------------------

def generate_session_token() -> str:
    """Return a fresh, opaque session token.

    32 bytes from the operating system's CSPRNG. There is no seeded or
    deterministic fallback: if the platform cannot provide secure randomness,
    ``secrets`` raises rather than degrading quietly.
    """
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_session_token(raw: str) -> str:
    """Return the lowercase 64-character SHA-256 hex digest of a token.

    Only this value is ever persisted. The raw token lives in the Set-Cookie
    header and in the viewer's browser, never in the database, so a read of
    ``operator_sessions`` does not yield a usable credential.

    A plain digest is correct here, unlike for passwords: the token already
    carries 32 bytes of entropy, so it is not brute-forceable and needs no key
    stretching.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

def hash_password(raw: str) -> str:
    """Return a PBKDF2-HMAC-SHA256 hash in the encoded storage format.

    Format, matching the CHECK constraint in migration 027::

        pbkdf2_sha256$<iterations>$<base64 salt>$<base64 derived key>

    A fresh 16-byte salt is drawn per call, so hashing the same password twice
    gives two different stored values and equal passwords are not detectable on
    disk. The iteration count travels with the hash, so the cost can be raised
    later without invalidating existing hashes.
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        "sha256", raw.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return "$".join(
        (
            _ALGORITHM,
            str(PBKDF2_ITERATIONS),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(derived).decode("ascii"),
        )
    )


def verify_password(raw: str, stored: str) -> bool:
    """Return True when ``raw`` matches the encoded ``stored`` hash.

    Never raises on a malformed, truncated, foreign-algorithm or non-string
    stored value: it returns False. A corrupt row must fail the login, not
    crash the request and disclose a stack trace.

    The digest comparison is constant-time, so a near-miss cannot be
    distinguished from a wild miss by timing.
    """
    if not isinstance(raw, str) or not isinstance(stored, str):
        return False

    parts = stored.split("$")
    if len(parts) != 4:
        return False

    algorithm, iterations_text, salt_b64, digest_b64 = parts
    if algorithm != _ALGORITHM:
        return False

    try:
        iterations = int(iterations_text)
    except ValueError:
        return False
    if iterations < 1:
        return False

    try:
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(digest_b64, validate=True)
    except (ValueError, TypeError):
        return False
    if not salt or not expected:
        return False

    candidate = hashlib.pbkdf2_hmac(
        "sha256", raw.encode("utf-8"), salt, iterations, dklen=len(expected)
    )
    return hmac.compare_digest(candidate, expected)


# ---------------------------------------------------------------------------
# Cookie
#
# Duck-typed over the response object, so this module imports no web framework
# and stays testable without one. Same shape as owner/security.py.
# ---------------------------------------------------------------------------

def set_cookie(response, token: str) -> None:
    """Attach the operator session cookie to ``response``.

    HttpOnly puts the token out of reach of page scripts. Secure is
    unconditional: TEST and PROD both serve over HTTPS, and a conditional flag
    is a configuration foot-gun. SameSite=Lax blocks cross-site POST while
    still allowing a top-level navigation back into the Shell, which is
    same-origin with the API.
    """
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
        max_age=SESSION_MAX_HOURS * 3600,
    )


def clear_cookie(response) -> None:
    """Remove the operator session cookie.

    Name, path and attributes must match those used when setting it, or the
    browser keeps the original cookie alongside the deletion.
    """
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        httponly=True,
        secure=True,
        samesite="lax",
    )
