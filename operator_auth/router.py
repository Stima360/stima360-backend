"""HTTP adapter for operator authentication.

No router-level dependency: /login and /logout must stay reachable without a
session, and attaching one here would make logging in impossible. /me declares
its own.

This module is not mounted in main.py by Task 6. Wiring it into the application
is Task 15, where it lands together with the CORE route allowlist and the
frozen legacy-Basic surface test.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from . import service
from .context import OperatorContext
from .dependencies import AuthenticatedSession, current_session, require_operator
from .enums import COOKIE_NAME
from .exceptions import AUTHENTICATION_FAILED_MESSAGE, AuthenticationFailed
from .schemas import LoginRequest, MeResponse
from .security import clear_cookie, set_cookie

router = APIRouter(prefix="/api/operator-auth", tags=["operator-auth"])


@router.post("/login", status_code=204)
def login(payload: LoginRequest) -> Response:
    """Authenticate and issue a session cookie.

    Returns 204 with an empty body. The raw token appears in the Set-Cookie
    header and nowhere else - not in the body, not in a header of our own, not
    in a log. A body carrying the token would put it in reach of page scripts,
    which is precisely what HttpOnly exists to prevent.
    """
    try:
        raw_token = service.login(payload.email, payload.password)
    except AuthenticationFailed as exc:
        raise HTTPException(
            status_code=401, detail=AUTHENTICATION_FAILED_MESSAGE
        ) from exc

    response = Response(status_code=204)
    set_cookie(response, raw_token)
    return response


@router.post("/logout", status_code=204)
def logout(request: Request) -> Response:
    """Revoke the session and clear the cookie.

    Always 204: with no cookie, with an unknown token, with an already-revoked
    one. Logout must not become an oracle for which tokens exist.
    """
    service.logout(request.cookies.get(COOKIE_NAME))
    response = Response(status_code=204)
    clear_cookie(response)
    return response


@router.get("/me", response_model=MeResponse)
def me(
    operator: OperatorContext = Depends(require_operator),
    session: AuthenticatedSession = Depends(current_session),
) -> MeResponse:
    """The caller's own session, as an explicit projection.

    Both dependencies resolve from the same cached `current_session`, so this
    costs one database resolution, not two.

    The response is constructed field by field rather than spread from a row:
    a new column on operator_users or operator_sessions must never be able to
    reach a client by simply existing.
    """
    return MeResponse(
        user_id=operator.user_id,
        agency_id=operator.agency_id,
        agency_name=session.agency_name,
        role=operator.role,
        is_platform_admin=operator.is_platform_admin,
        expires_at=session.expires_at,
    )
