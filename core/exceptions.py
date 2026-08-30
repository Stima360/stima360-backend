"""Domain exceptions translated to HTTP responses by the router."""


class CoreError(Exception):
    """Base error for the CORE module."""


class NotFoundError(CoreError):
    pass


class ConflictError(CoreError):
    pass


class ValidationError(CoreError):
    pass
