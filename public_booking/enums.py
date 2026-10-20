"""A30-12 - i pochi vocabolari chiusi propri di questo pacchetto.

`APPOINTMENT_TYPES` non si ridichiara qui: un tipo pubblico deve restare
uno di quelli che l'Agenda gia' riconosce (`appointments.enums`), non un
vocabolario parallelo che puo' divergere.
"""
from __future__ import annotations

from appointments.enums import APPOINTMENT_TYPES, MANDATE_REQUIRED_TYPES

#: SEMPLIFICAZIONE-CRM-1: la visita immobile (`buyer_visit`) e l'appuntamento
#: di vendita (106) richiedono un incarico e un cliente scelti dall'agenzia,
#: che una prenotazione pubblica non puo' fornire. Un link gia' esistente di
#: questi tipi non crea piu' appuntamenti (`appointments/service.py`).
NOT_BOOKABLE_TYPES = tuple(MANDATE_REQUIRED_TYPES)

LINK_STATUSES = ("active", "disabled")

SUBMISSION_STATUSES = ("pending", "succeeded", "failed")

#: D9: i quattro budget - GET/POST separati, per-link e per-(link+IP).
RATE_LIMIT_SCOPES = ("get_link", "get_ip", "post_link", "post_ip")

__all__ = ["APPOINTMENT_TYPES", "LINK_STATUSES", "NOT_BOOKABLE_TYPES",
           "SUBMISSION_STATUSES", "RATE_LIMIT_SCOPES"]
