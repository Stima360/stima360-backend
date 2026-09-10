"""FastAPI dependencies that turn a credential into a server-side scope.

Whatever the channel, the scope is decided here on the server. Nothing reads an
agency, a role or a user from a query parameter, a header or a body, so an
`agency_id` arriving from client state is untrusted by construction rather than
by discipline.

Six dependencies, and the division between them is the point:

* `optional_session` - the only place the session cookie is read. Returns a
  live session or None, and never raises, so every caller below shares it
  through FastAPI's per-request dependency cache: one database resolution per
  request however many dependencies a handler declares.
* `current_session` - session or 401. Cookie only. For endpoints that need the
  session itself rather than a scope, such as /me.
* `require_operator` - the scope every CORE endpoint declares. Session first,
  then the legacy Basic compatibility channel, then 401.
* `require_authenticated_operator` - P26-3. Mount-level admission for the
  routers the OS Shell calls. Same two channels, no scope, no database. This
  is the widening of D-1: before P26-3 the operator session reached only
  `/api/operator-auth` and `/api/core`, and the Shell's other eleven routers
  were Basic-only. The Shell now holds a cookie, so they admit it too - and
  the boundary is still explicit, still a list of mounts in `main.py`, and
  still asserted route by route rather than assumed.
* `legacy_basic_agency_context` - the scope for every router outside CORE.
  P26-3 made it session-first; the name is retired in P26-5 with the channel.
* `basic_only_agency_context` - P26-3. What the one above was before P26-3,
  for OWNER Admin: a surface whose mount accepts nothing but Basic and whose
  scope therefore must not come from a cookie.

Task 15 added the legacy branch. Before it, this module accepted the cookie
alone; the Basic channel is a bounded compatibility path, not a second identity
system, and `admin_security.require_admin` remains the single definition of
what that credential is.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

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
#
# The wording and the challenge header below are byte-identical to what
# admin_security.require_admin returned before P26-1. /api/core kept that
# contract through the transition, and the OS Shell relies on the challenge to
# prompt for the legacy credential - a 401 without it simply fails silently in
# a browser.
NOT_AUTHENTICATED_MESSAGE = "Non autorizzato"
BASIC_CHALLENGE = {"WWW-Authenticate": 'Basic realm="STIMA360 Admin"'}

# Declared once, at module level, so FastAPI treats it as the route's security
# scheme. auto_error=False because an absent credential is not yet a failure -
# the cookie branch may still succeed.
_basic_scheme = HTTPBasic(auto_error=False)


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
    credentials: HTTPBasicCredentials | None = Depends(_basic_scheme),
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

    The Basic credential arrives through FastAPI's own `HTTPBasic` scheme
    rather than by parsing the header here. That is what keeps the OpenAPI
    document honest: the scheme is a `SecurityBase`, so every route declaring
    this dependency continues to advertise `security`, exactly as it did when
    `require_admin` guarded it. A hand-rolled header parse authenticates just
    as well and silently drops that declaration - which is how CORE briefly
    came to look unauthenticated to anything reading the schema.
    """
    context = _scope_from_session_or_basic(
        session, credentials, session_was_presented(request)
    )
    if context is None:
        raise HTTPException(
            status_code=401,
            detail=NOT_AUTHENTICATED_MESSAGE,
            headers=BASIC_CHALLENGE,
        )
    return context


def require_authenticated_operator(
    request: Request,
    session: AuthenticatedSession | None = Depends(optional_session),
    credentials: HTTPBasicCredentials | None = Depends(_basic_scheme),
) -> None:
    """Mount-level admission for the routers the OS Shell calls. No scope.

    P26-3 needed every one of those routers to accept the session cookie, and
    the obvious way - mounting them on `require_operator` - would have made
    each request resolve the Default Agency twice on the legacy channel: once
    for the mount, once for the route's own `legacy_basic_agency_context`.
    FastAPI caches per callable, and those are two different callables.

    So this one answers the only question a mount has to answer - may this
    caller in at all - and answers it without touching the database: a session
    is resolved once, by the cached `optional_session`, and the legacy branch is
    a string comparison. The scope still comes from the route's own dependency,
    which is where it belongs and where tests already override it.

    Returns None on purpose. A mount-level dependency whose value nothing reads
    should not look like it produces one.
    """
    if session is not None:
        return None

    # A refused cookie does not fall through to the shared credential.
    if not session_was_presented(request) and credentials is not None:
        if _verify_legacy_credentials(credentials) is not None:
            return None

    # Same refusal `require_admin` gave before P26-3, including its 503 when the
    # server has no admin credentials configured at all.
    require_admin(credentials)
    raise HTTPException(          # pragma: no cover - require_admin always raises
        status_code=401,
        detail=NOT_AUTHENTICATED_MESSAGE,
        headers=BASIC_CHALLENGE,
    )


def session_was_presented(request: Request) -> bool:
    """Did this request carry a session cookie at all?

    P26-3 review. `optional_session` returns None for absent, unknown, revoked,
    expired, idle-timed-out, disabled and de-membered alike - deliberately, so
    that nothing downstream can tell them apart and use the difference as an
    oracle. That is right for the caller and wrong for us: it made "no cookie"
    and "a cookie the server rejected" the same input, and both fell through to
    the legacy credential.

    Falling through is correct for the first and not for the second. A revoked
    session must mean revoked: an operator whose account was disabled must not
    keep working because their browser also happens to hold ADMIN_USER and
    ADMIN_PASS. This is the one bit that distinguishes the two, and it is read
    from the request rather than from the resolution, so no rejection reason
    leaks with it.

    P26-1 chose the other way round - test_g2_an_invalid_cookie_falls_through
    _to_valid_basic asserted the fall-through - on the reasoning that a stale
    cookie should not lock out a legitimate Basic client. It does not: a Basic
    client sends no cookie. The only caller affected is one presenting both,
    and the right answer for it is to clear the dead cookie and log in again.
    """
    return bool(request.cookies.get(COOKIE_NAME))


def _scope_from_session_or_basic(
    session: AuthenticatedSession | None,
    credentials: HTTPBasicCredentials | None,
    cookie_presented: bool = False,
) -> OperatorContext | None:
    """The caller's scope from either approved channel, or None.

    P26-3 made this the one place the precedence lives, because two
    dependencies now need it: `require_operator`, which authenticates, and
    `legacy_basic_agency_context`, which supplies a scope to every other
    router. They differ only in how they refuse, and that difference is
    deliberate - see the note on the latter.

    Never raises for "not authenticated": returning None leaves the refusal,
    and therefore its wording and its status code, to the caller. It does let a
    503 through from `_verify_legacy_credentials`, because a server with no
    admin credentials configured is an operational fault rather than a failed
    authentication.
    """
    if session is not None:
        return session.context

    # A cookie was presented and the server refused it. Do not quietly serve
    # the same request through the shared credential instead.
    if cookie_presented:
        return None

    if credentials is not None:
        username = _verify_legacy_credentials(credentials)
        if username is not None:
            return _default_agency_context()

    return None


def _default_agency_context() -> OperatorContext:
    """The legacy Basic scope: Default Agency, agency owner, never platform admin.

    Every field is decided here, on the server:

    * `agency_id` is resolved from the Default Agency slug. Not a parameter,
      not a header, not a numeric constant.
    * `role` is the agency owner, never platform admin, so the scope is
      agency-bound and can never reach the cross-agency branch.
    * `user_id` and `session_id` are None: the credential is a shared secret,
      not a person, and recording an invented operator would be a lie.
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


def basic_only_agency_context(
    _credential: str = Depends(require_admin),
) -> OperatorContext:
    """The Default-Agency scope, for a surface that is HTTP Basic and only that.

    P26-3 review, and the reason it exists: OWNER Admin is mounted on
    `require_owner_admin`, which accepts nothing but Basic, while its routes
    took the shared scope dependency - which after P26-3 prefers a session. A
    browser holding both would then have been ADMITTED by Basic and SCOPED by
    the cookie: two different credentials deciding two different halves of one
    request. That hybrid is not a widening or a narrowing, it is an incoherence,
    and this removes it.

    OWNER Admin stays Basic-only deliberately. The OS Shell does not call it,
    so P26-3 has no reason to touch it - and admitting any operator session
    would be a real escalation, because `require_authenticated_operator` checks
    that a caller is authenticated and not what they are allowed to do. Managing
    owner accounts, minting their login tokens and reading their documents
    would become reachable by any agent-role session. That decision belongs to a
    phase that also brings roles with it, not to this one.

    This is what `legacy_basic_agency_context` was before P26-3, kept under a
    name that says so. Both are retired in P26-5 with the channel itself.
    """
    return _default_agency_context()


def _verify_legacy_credentials(credentials: HTTPBasicCredentials) -> str | None:
    """Return the verified legacy username, or None.

    The comparison is delegated to `admin_security.require_admin`, which P26-1
    leaves untouched and which remains the single definition of what the legacy
    credential is. This only turns "raise 401" into "return None", so a failed
    Basic attempt falls through to one 401 rather than producing a second,
    differently-worded one.

    A 503 is deliberately re-raised rather than swallowed. It means the server
    has no admin credentials configured at all, which is an operational fault -
    reporting it as "not authenticated" would send an operator hunting for the
    wrong problem, and it is the answer this path gave before P26-1.
    """
    try:
        return require_admin(credentials)
    except HTTPException as exc:
        if exc.status_code == 503:
            raise
        return None


def legacy_basic_agency_context(
    request: Request,
    session: AuthenticatedSession | None = Depends(optional_session),
    credentials: HTTPBasicCredentials | None = Depends(_basic_scheme),
) -> OperatorContext:
    """The scope for every router outside CORE. Session first, Basic second.

    P26-3 CHANGED WHAT THIS DOES, AND DELIBERATELY NOT ITS NAME.

    Until P26-3 this resolved the Default Agency and nothing else: the OS Shell
    authenticated with legacy Basic, so there was no session to prefer. The
    Shell now logs in through `/api/operator-auth/login` and carries an HttpOnly
    cookie, and this dependency is declared by roughly a hundred and fifty
    routes across PROPERTY, BUY, MATCH, PROPOSAL, SALE, CRM, FLOW, OWNER Admin,
    the intelligence routers and six routes in main.py. Renaming it in the same
    change that altered its behaviour would have put a mechanical edit of every
    one of those - and of the twenty test files that override it - in the same
    diff as a security-relevant decision. The name is retired in P26-5, with the
    legacy channel it describes.

    So, in order:

    1. **A live operator session cookie.** The operator's REAL agency, role and
       user id - not the Default Agency. This is what makes the OS Shell a
       multi-agency client.
    2. **Valid legacy `ADMIN_USER`/`ADMIN_PASS`.** The six legacy admin pages
       and any script still on that channel keep working, unchanged, bound to
       the Default Agency exactly as before.
    3. **Otherwise refuse**, and refuse the way `require_admin` used to: the
       same status, the same message, and the same 503 when the server has no
       admin credentials configured at all. That is why the refusal is
       delegated rather than raised here - a route that answered "Non
       autorizzato" before P26-3 must not start answering something else.

    Nothing here reads an agency, a role or a user from the request. A client
    that sends both a cookie and Basic gets the session, never the shared
    credential's wider-looking but Default-Agency-bound scope.

    GATE-MA1 is still OPEN. Branch 2 is a compatibility bridge with a known
    expiry: while a shared secret can still authenticate a tenant route, the
    platform is not certified for a second real agency. P26-5 confines or
    removes it; P26-6 certifies what is left.
    """
    context = _scope_from_session_or_basic(
        session, credentials, session_was_presented(request)
    )
    if context is not None:
        return context

    # No usable session and no usable Basic. `require_admin` owns this refusal and
    # always has: it raises 401 with its own message, or 503 when the server is
    # unconfigured. Calling it keeps both answers byte-identical to P26-1.
    require_admin(credentials)
    raise HTTPException(          # pragma: no cover - require_admin always raises
        status_code=401,
        detail=NOT_AUTHENTICATED_MESSAGE,
        headers=BASIC_CHALLENGE,
    )
