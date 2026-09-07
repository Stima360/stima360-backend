"""The two server-side agency scopes, and the protocol they share.

P26-1 has exactly two context types and no third:

* ``OperatorContext``     - an authenticated human, produced only by the
                            request dependency (Task 6).
* ``SystemAgencyContext`` - a scope with no principal, for the public
                            estimation flow, produced only by the factory in
                            core/scope.py (Task 10).

Neither is constructible from client input. ``OperatorContext.agency_id`` comes
from a server-side membership join; ``SystemAgencyContext.agency_id`` comes from
a server-side slug lookup. In neither case is ``agency_id`` an argument a caller
supplies, which is what satisfies the approved rule that an agency_id arriving
from a query parameter, a path, a body or frontend state must never be trusted.

Both are frozen. No service or repository can widen a scope mid-request: there
is no setter, no ``with_agency()``, and no copy-with-override helper.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from . import permissions
from .exceptions import PlatformAdminAgencyRequired

# How an OperatorContext was authenticated. 'legacy_basic' is the temporary
# ADMIN_USER/ADMIN_PASS compatibility channel, confined to the Default Agency
# on /api/core and never granting platform-admin rights.
AUTH_CHANNELS = ("operator_session", "legacy_basic")

# Flows entitled to a SystemAgencyContext. Closed set: adding a second value is
# a deliberate edit, not an accident.
SYSTEM_CONTEXT_ORIGINS = ("public_stima",)


@runtime_checkable
class AgencyScope(Protocol):
    """The minimum a query builder needs to scope a statement.

    Deliberately smaller than either concrete context: core/scope.py should be
    able to read the agency, the role and the operator id, and nothing else.
    """

    agency_id: int | None
    role: str | None
    user_id: int | None
    is_platform_admin: bool

    def require_agency(self) -> int:
        ...


@dataclass(frozen=True)
class OperatorContext:
    """The authenticated caller's effective scope.

    ``agency_id`` is None only for a platform admin holding no membership.
    ``user_id`` is None only on the legacy Basic channel, where the credential
    is a shared secret rather than a person - recorded honestly as absent
    rather than attributed to an invented operator.
    """

    user_id: int | None
    agency_id: int | None
    role: str | None
    is_platform_admin: bool
    session_id: int | None
    auth_channel: str

    @property
    def is_agency_bound(self) -> bool:
        return self.agency_id is not None

    @property
    def sees_all_agency_records(self) -> bool:
        return permissions.sees_all_agency_records(self.role, self.is_platform_admin)

    @property
    def may_assign_records(self) -> bool:
        return permissions.may_assign_records(self.role, self.is_platform_admin)

    def require_agency(self) -> int:
        """Return the bound agency, or refuse.

        Compared against None rather than tested for truthiness: agency id 0
        is a legitimate identifier and must not be mistaken for "unbound".
        """
        if self.agency_id is None:
            raise PlatformAdminAgencyRequired(
                "this operation requires an agency-bound context"
            )
        return self.agency_id


@dataclass(frozen=True)
class SystemAgencyContext:
    """A server-generated scope for a flow that has no operator.

    Carries two fields and nothing else. ``user_id``, ``role``,
    ``is_platform_admin``, ``session_id`` and ``auth_channel`` are deliberately
    *not* dataclass fields, so no constructor argument can set them: this type
    can express "this agency" and nothing more. The three neutral class
    attributes below exist only to satisfy AgencyScope.

    Because ``is_platform_admin`` is False and ``agency_id`` is always a real
    integer, a scope builder can never widen this to a cross-agency query.
    """

    agency_id: int
    origin: str

    # Class attributes, not fields: no annotation, so dataclass ignores them.
    user_id = None
    role = None
    is_platform_admin = False

    def __post_init__(self) -> None:
        """Refuse any origin P26-1 has not entitled to a system-owned scope.

        Fail closed. This type bypasses operator authentication entirely, so
        the set of flows allowed to hold one is closed at construction rather
        than checked by convention at each call site.
        """
        if self.origin not in SYSTEM_CONTEXT_ORIGINS:
            raise ValueError(
                f"unsupported SystemAgencyContext origin {self.origin!r}; "
                f"P26-1 permits only {', '.join(SYSTEM_CONTEXT_ORIGINS)}"
            )

    def require_agency(self) -> int:
        return self.agency_id
