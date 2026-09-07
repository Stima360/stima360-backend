"""Request and response models for the operator authentication API.

Pydantic v1 style, matching core/schemas.py.

Both models are closed. The request declares only what a login needs, so a
caller cannot smuggle an agency, a role or a platform-admin flag into the
authentication step: an undeclared field is rejected with 422 before any
handler runs. The response declares only what /me is allowed to disclose, so a
new column on operator_users or operator_sessions cannot leak by being carried
along in a dict.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class OperatorAuthModel(BaseModel):
    class Config:
        extra = "forbid"


class LoginRequest(OperatorAuthModel):
    """The only two values a login accepts.

    No agency_id, no role, no is_platform_admin, no session id. Scope is a
    server-side consequence of who you are, never a request parameter.
    """

    email: str
    password: str


class MeResponse(OperatorAuthModel):
    """The authenticated caller's own view of their session.

    Deliberately excludes email, password_hash, session_id and token_hash. The
    session id identifies a live credential and has no use in a UI; the others
    are secrets or personal data this endpoint has no reason to return.

    agency_id and agency_name are None for a platform admin holding no
    membership, which is a legitimate unbound scope rather than an error.
    """

    user_id: int | None
    agency_id: int | None
    agency_name: str | None
    role: str | None
    is_platform_admin: bool
    expires_at: datetime
