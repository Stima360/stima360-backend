"""BLOCCO-4A - dal numero WhatsApp destinatario all'agenzia.

La sola fonte e' la configurazione del server (`WHATSAPP_INBOUND_ROUTES`),
verificata a ogni messaggio contro la tabella `agencies`. Non esiste un'agenzia
predefinita: un numero sconosciuto, configurato per due agenzie diverse o
legato a un'agenzia inesistente resta NON instradato.
"""
from __future__ import annotations

import os

from .enums import REASON_AGENCY_NOT_FOUND, REASON_AMBIGUOUS, REASON_UNKNOWN, ROUTED, ROUTES_ENV, UNROUTED


def configured_routes(raw: str | None = None) -> dict[str, set[int]]:
    """{phone_number_id: {agency_id, ...}}. Voci malformate ignorate."""
    raw = os.getenv(ROUTES_ENV, "") if raw is None else raw
    routes: dict[str, set[int]] = {}
    for item in raw.split(","):
        number, sep, agency = item.strip().partition(":")
        number, agency = number.strip(), agency.strip()
        if not sep or not number or not agency.isdigit():
            continue
        routes.setdefault(number, set()).add(int(agency))
    return routes


def resolve(cur, phone_number_id: str | None, routes: dict[str, set[int]]) -> tuple[str, int | None, str | None]:
    """(routing_status, agency_id, routing_reason)."""
    agencies = routes.get(phone_number_id or "")
    if not agencies:
        return UNROUTED, None, REASON_UNKNOWN
    if len(agencies) > 1:
        return UNROUTED, None, REASON_AMBIGUOUS
    agency_id = next(iter(agencies))
    cur.execute("SELECT id FROM agencies WHERE id = %s", (agency_id,))
    if cur.fetchone() is None:
        return UNROUTED, None, REASON_AGENCY_NOT_FOUND
    return ROUTED, agency_id, None
