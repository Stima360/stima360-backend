"""Domain exceptions translated to HTTP responses by the router."""


class CoreError(Exception):
    """Base error for the CORE module."""


class NotFoundError(CoreError):
    pass


class ConflictError(CoreError):
    pass


class ValidationError(CoreError):
    pass


class PermissionDenied(CoreError):
    """The caller's role does not permit this operation.

    Distinct from NotFoundError: nothing is being hidden. Under design spec
    D-6, a caller who legitimately knows an endpoint exists and is refused on
    role grounds gets 403, while a record belonging to another agency gets 404
    - so this is never raised for a record the caller cannot see.

    The role decision itself is not made here. It belongs to
    operator_auth.permissions, which is the single home of the agency role
    matrix; this type only carries the outcome to the router.
    """
