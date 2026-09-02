"""Domain exceptions for the isolated Property Watch module."""


class PropertyWatchError(Exception):
    """Base exception for Property Watch."""


class ValidationError(PropertyWatchError):
    """Raised when a Property Watch service input is invalid."""


class StimaNotFoundError(PropertyWatchError):
    """Raised when a watch is requested for a nonexistent valuation."""


class WatchNotFoundError(PropertyWatchError):
    """Raised when no watch has been initialized for a valuation."""
