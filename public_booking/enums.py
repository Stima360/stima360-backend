"""A30-12 - i pochi vocabolari chiusi propri di questo pacchetto.

`APPOINTMENT_TYPES` non si ridichiara qui: un tipo pubblico deve restare
uno di quelli che l'Agenda gia' riconosce (`appointments.enums`), non un
vocabolario parallelo che puo' divergere.
"""
from __future__ import annotations

from appointments.enums import APPOINTMENT_TYPES, SALE_APPOINTMENT_TYPE

#: APPUNTAMENTI DI VENDITA (106): un appuntamento di vendita richiede un
#: incarico e un cliente scelti dall'agenzia, che una prenotazione pubblica
#: non puo' fornire. Il CHECK di `public_booking_links` (077) non lo ammette.
NOT_BOOKABLE_TYPES = (SALE_APPOINTMENT_TYPE,)

LINK_STATUSES = ("active", "disabled")

SUBMISSION_STATUSES = ("pending", "succeeded", "failed")

#: D9: i quattro budget - GET/POST separati, per-link e per-(link+IP).
RATE_LIMIT_SCOPES = ("get_link", "get_ip", "post_link", "post_ip")

__all__ = ["APPOINTMENT_TYPES", "LINK_STATUSES", "NOT_BOOKABLE_TYPES",
           "SUBMISSION_STATUSES", "RATE_LIMIT_SCOPES"]
