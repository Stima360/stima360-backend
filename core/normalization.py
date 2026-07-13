"""Conservative normalization helpers.

Original values are always preserved in the database. Normalized values are used
for search only and never trigger automatic record merging.
"""

from __future__ import annotations

import re


def normalize_email(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def normalize_phone(value: str | None) -> str | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None

    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None

    # Keep international numbers stable. For Italian local numbers, align with
    # the existing STIMA360 convention used by WhatsApp matching.
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("39"):
        return digits
    return "39" + digits.lstrip("0")
