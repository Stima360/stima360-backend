"""Domain exceptions for operator authentication.

Pure domain types. Nothing here imports a web framework: the mapping from an
exception to an HTTP status belongs to the router layer, not to the domain.
These are shared between the service, which raises them, and the HTTP adapter,
which translates them.
"""
from __future__ import annotations

# The single message returned for every authentication failure. It lives beside
# the exception because the service and the router must not be able to drift
# into two different wordings - a difference in phrasing is itself an oracle.
AUTHENTICATION_FAILED_MESSAGE = "Credenziali non valide."


class AuthenticationFailed(Exception):
    """Raised for every login failure, with no indication of the cause.

    Unknown email, wrong password, disabled account, missing, suspended or
    revoked membership and a suspended agency are all indistinguishable to the
    caller. A differentiated error would let anyone enumerate registered
    addresses and probe account state without ever authenticating.

    The router maps this to HTTP 401 with the message above, unchanged.
    """

    def __init__(self, message: str = AUTHENTICATION_FAILED_MESSAGE) -> None:
        super().__init__(message)


class PlatformAdminAgencyRequired(Exception):
    """Raised when an operation needs an agency but the context has none.

    In P26-1 the only context that can reach this state is a platform admin
    holding no agency membership: `agency_id` is None by design, because
    platform administration sits outside the agency hierarchy rather than
    inside every agency.

    Reads are still permitted for such a context - it sees across agencies.
    What it cannot do is *write* through a generic, agency-bound CORE endpoint,
    because there is no agency to attribute the new record to and inferring a
    default would silently assign data to an agency the caller never named.

    The router maps this to HTTP 403: the endpoint exists and the caller knows
    it, so nothing is being hidden; the action is refused. 404 is reserved for
    concealing another agency's records.
    """
