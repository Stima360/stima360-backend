"""Enumerations for the sale (property_sales) bounded context."""

SALE_STATUSES = {"pending", "completed", "cancelled"}

TERMINAL_SALE_STATUSES = {"completed", "cancelled"}

SALE_TRANSITIONS = {
    "pending": {"completed", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}

SALE_SELLER_ROLES = {"owner", "seller"}
