"""FastAPI dependencies that turn a credential into a server-side scope.

Whatever the channel, the scope is decided here on the server. Nothing reads an
agency, a role or a user from a query parameter, a header or a body, so an
`agency_id` arriving from client state is untrusted by construction rather than
by discipline.

Four functions, and the division between them is the point:

* `optional_session` - the only place the session cookie is read. Returns a
  live session or None, and never raises, so both callers below can share it
  through FastAPI's per-request dependency cache: one database resolution per
  request however many dependencies a handler declares.
* `current_session` - session or 401. Cookie only. For endpoints that need the
  session itself rather than a scope, such as /me.
* `require_operator` - the scope every CORE endpoint declares. Session first,
  then the legacy Basic compatibility channel, then 401.
* `legacy_basic_agency_context` - the C2 bridge that turns the legacy
  ADMIN_USER/ADMIN_PASS credential into an agency-*bound* context. Used
  directly by `require_operator`, and as a dependency by Next Best Action,
  which sits outside D-1's operator-session allowlist and whose OS Shell view
  still sends Basic.

Task 15 added the legacy branch. Before it, this module accepted the cookie
alone; the Basic channel is a bounded compatibility path, not a second identity
system, and `admin_security.require_admin` remains the single definition of
what that credential is.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasicCredentials

from admin_security import require_admin
from core.scope import resolve_default_agency_id

from . import service
from .context import OperatorContext
from .database import operator_cursor
from .enums import COOKIE_NAME

# The role the compatibility credential presents as. The legacy credential is
# the agency's own administrative login, so it maps to the agency's owner - and
# never to platform_admin, which would make it cross-agency (design spec D-2).
LEGACY_BASIC_ROLE = "agency_owner"

# One message for every rejection: absent cookie, unknown, revoked, expired,
# idle, disabled account, suspended membership, suspended agency. A caller
# learns only that they are not authenticated.
NOT_AUTHENTICATED_MESSAGE = "Non autorizzato."


@dataclass(frozen=True)
class AuthenticatedSession:
    """A resolved session: the caller's scope plus what /me may display.

    `agency_name` and `expires_at` are held here rather than on OperatorContext
    because a scope is an authorisation decision. Adding presentation fields to
    it would mean every scoped repository call carried data it must never use,
    and would invite exactly the kind of drift the frozen field list prevents.
    """

    context: OperatorContext
    agency_name: str | None
    expires_at: datetime


def optional_session(request: Request) -> AuthenticatedSession | None:
    """Resolve the session cookie if there is a live one, else None.

    The single point where a cookie becomes a scope, and the only place that
    reads the cookie at all. Both `current_session` and `require_operator`
    derive from it rather than resolving independently, so FastAPI's
    per-request dependency cache means one database resolution per request no
    matter how many dependencies a handler declares.

    Returns None - never a partial result - for an absent, unknown, revoked,
    expired or idle-timed-out session. Turning that None into a 401 is the
    caller's decision, because `require_operator` must be able to fall through
    to the legacy channel instead.
    """
    raw_token = request.cookies.get(COOKIE_NAME)
    if not raw_token:
        return None

    resolved = service.session_from_token(raw_token)
    if resolved is None:
        return None

    return AuthenticatedSession(
        context=resolved["context"],
        agency_name=resolved["agency_name"],
        expires_at=resolved["expires_at"],
    )


def current_session(
    session: AuthenticatedSession | None = Depends(optional_session),
) -> AuthenticatedSession:
    """The caller's session, or 401. Session cookie only - no legacy channel.

    Used by endpoints that need the session itself rather than a scope, such
    as /me. Basic is deliberately not accepted here: the legacy credential is
    a shared secret with no session behind it, so there is nothing to report.
    """
    if session is None:
        raise HTTPException(status_code=401, detail=NOT_AUTHENTICATED_MESSAGE)
    return session


def require_operator(
    request: Request,
    session: AuthenticatedSession | None = Depends(optional_session),
) -> OperatorContext:
    """The authenticated caller's scope, from either approved channel.

    Precedence, and the reasoning for it:

    1. **A valid operator session cookie.** A real person with a real agency
       and a real role. It is tried first so that an operator who also happens
       to send Basic is never silently downgraded to the shared credential's
       Default-Agency scope.
    2. **Valid legacy `ADMIN_USER`/`ADMIN_PASS`.** The OS Shell has not yet
       migrated to the cookie session (design spec section 18), so this channel
       must keep working on `/api/core`. It resolves to an agency-*bound*
       context: Default Agency by slug, `agency_owner`, never platform admin,
       with `user_id` and `session_id` left None because the credential is a
       shared secret rather than a person (D-2).
    3. **Otherwise 401**, with the same message for every cause.

    Fail-closed at each step. An invalid or revoked cookie does not lock out a
    caller who also sent valid Basic - it simply is not a session - but it can
    never itself become authority. Nothing here reads an agency, a role or a
    user from the request.

    It depends on `optional_session` rather than `current_session` so that an
    absent or dead cookie falls through to the Basic branch instead of raising
    401 inside a nested dependency - and, because that dependency is shared and
    cached, a handler declaring both still costs one session resolution.
    """
    if session is not None:
        return session.context

    credentials = _basic_credentials(request)
    if credentials is not None:
        return legacy_basic_agency_context(credentials)

    raise HTTPException(status_code=401, detail=NOT_AUTHENTICATED_MESSAGE)


def _basic_credentials(request: Request) -> str | None:
    """Return the verified legacy username, or None.

    The comparison itself is delegated to `admin_security.require_admin`, which
    P26-1 leaves untouched and which remains the single definition of what the
    legacy credential is. This function only adapts its shape: it parses the
    header FastAPI would have parsed, and turns "raise 401" into "return None"
    so a failed Basic attempt falls through to one 401 rather than producing a
    second, differently-worded one.

    A 503 is deliberately re-raised rather than swallowed. It means the server
    has no admin credentials configured at all, which is an operational fault -
    reporting it as "not authenticated" would send an operator hunting for the
    wrong problem, and it is the answer this path gave before P26-1.
    """
    header = request.headers.get("Authorization")
    if not header or not header.lower().startswith("basic "):
        return None

    try:
        raw = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None
    username, separator, password = raw.partition(":")
    if not separator:
        return None

    try:
        return require_admin(
            HTTPBasicCredentials(username=username, password=password)
        )
    except HTTPException as exc:
        if exc.status_code == 503:
            raise
        return None


def legacy_basic_agency_context(
    _credential: str = Depends(require_admin),
) -> OperatorContext:
    """An agency-bound scope for a route still authenticated by legacy Basic.

    P26-1's D-1 allowlist admits operator sessions on `/api/operator-auth/*`
    and `/api/core/*` only, so a route outside it - Next Best Action, whose
    OS Shell view still sends Basic - has no session to derive a scope from.
    Rather than let those routes keep reading CORE unscoped, they receive this
    context.

    Every field is decided here, on the server:

    * `agency_id` is resolved from the Default Agency slug. Not a parameter,
      not a header, not a numeric constant.
    * `role` is the agency owner, never platform admin, so the scope is
      agency-bound and can never reach the cross-agency branch.
    * `user_id` and `session_id` are None: the credential is a shared secret,
      not a person, and recording an invented operator would be a lie.

    `require_admin` is declared as a dependency rather than assumed: the
    context and the authentication that justifies it cannot be separated, even
    if a future router forgets the mount-level guard.

    This is a compatibility bridge with a known expiry. While it exists,
    GATE-MA1 blocks the activation of a second real agency: these routes remain
    bound to the Default Agency, so P26-1 does not certify platform-wide
    multi-agency isolation.
    """
    with operator_cursor() as (_, cur):
        agency_id = resolve_default_agency_id(cur)

    return OperatorContext(
        user_id=None,
        agency_id=agency_id,
        role=LEGACY_BASIC_ROLE,
        is_platform_admin=False,
        session_id=None,
        auth_channel="legacy_basic",
    )
