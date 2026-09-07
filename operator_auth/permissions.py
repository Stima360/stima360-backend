"""The P26-1 permission matrix, as pure functions.

No I/O, no state, no context type: every function takes a role string and the
platform-admin flag and returns a bool. Keeping the matrix here rather than
inside OperatorContext means it can be asserted directly, and means the future
membership-management endpoints (P26-2) have predicates to import rather than
rules to re-derive.

The matrix, from the approved design spec section 13:

    Capability              platform_admin  agency_owner  agency_admin  agent
    Create agency                YES            NO            NO         NO
    Manage agency users          YES            YES         LIMITED      NO
    Change agency owner          YES            YES           NO         NO
    See all agency records       YES            YES           YES        NO
    See assigned records         YES            YES           YES        YES
    Assign records               YES            YES           YES        NO
    Cross-agency                 YES            NO            NO         NO

"LIMITED" is defined exactly and minimally: an agency_admin may manage
memberships whose role is `agent`, and no other.

Every function fails closed. An unrecognised role - including the string
"platform_admin", which is a flag on operator_users and never a role value -
grants nothing.
"""
from __future__ import annotations

from .enums import AGENCY_ROLES

_OWNER, _ADMIN, _AGENT = AGENCY_ROLES

# Roles that see every record in their own agency rather than only their own.
_FULL_AGENCY_VISIBILITY = frozenset({_OWNER, _ADMIN})

# Roles that may (re)assign a record to an operator.
_MAY_ASSIGN = frozenset({_OWNER, _ADMIN})

# Roles that may transfer agency ownership.
_MAY_CHANGE_OWNER = frozenset({_OWNER})

# Which membership roles each agency role may manage. An agency_admin is
# limited to `agent`; that single entry is the whole of "admin LIMITED".
_MANAGEABLE_TARGETS = {
    _OWNER: frozenset(AGENCY_ROLES),
    _ADMIN: frozenset({_AGENT}),
    _AGENT: frozenset(),
}


def sees_all_agency_records(role: str | None, is_platform_admin: bool) -> bool:
    """True when the caller sees every record in scope, not only assigned ones."""
    return bool(is_platform_admin) or role in _FULL_AGENCY_VISIBILITY


def may_assign_records(role: str | None, is_platform_admin: bool) -> bool:
    """True when the caller may set a record's assigned agent."""
    return bool(is_platform_admin) or role in _MAY_ASSIGN


def may_create_agency(role: str | None, is_platform_admin: bool) -> bool:
    """True only for a platform admin.

    Creating an agency is platform administration, not agency operation. No
    agency role can create a peer.
    """
    return bool(is_platform_admin)


def may_change_agency_owner(role: str | None, is_platform_admin: bool) -> bool:
    """True for a platform admin or the agency's own owner."""
    return bool(is_platform_admin) or role in _MAY_CHANGE_OWNER


def may_manage_membership(
    role: str | None, is_platform_admin: bool, target_role: str
) -> bool:
    """True when the caller may create or alter a membership of ``target_role``.

    Fails closed on an unrecognised target: a role outside AGENCY_ROLES is
    refused even for a platform admin, so a typo or a future role cannot be
    granted by accident.
    """
    if target_role not in AGENCY_ROLES:
        return False
    if is_platform_admin:
        return True
    return target_role in _MANAGEABLE_TARGETS.get(role, frozenset())
