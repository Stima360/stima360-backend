"""Domain exceptions for the Seller Intelligence module.

Kept separate from core/exceptions.py on purpose: this module must stay
importable and usable even if the CORE package is absent or broken.
"""


class SellerIntelligenceError(Exception):
    """Base error for the Seller Intelligence module."""


class ValidationError(SellerIntelligenceError):
    """Raised by the application layer, never by the database.

    In particular: "at least one of contact_id/lead_id/stima_id/property_id"
    is an application-level rule enforced here, deliberately NOT a SQL CHECK
    constraint (see migrations/017_seller_intelligence_01.sql for why).
    """
