"""BLOCCO-4A - ricezione WhatsApp: persistenza dal webhook, consultazione e
associazione manuale.

Webhook: tutto il payload in UNA transazione. Se una scrittura fallisce la
transazione si annulla e l'errore risale (il webhook risponde 500 e Meta
ritenta); i reinvii non duplicano nulla grazie ai vincoli unici del database.
Nessuna attivita', nessun contatto, nessuna richiesta viene creata
automaticamente; il contatto si collega da solo solo se il telefono
corrisponde a UN contatto dell'agenzia, altrimenti serve l'associazione
manuale. L'immobile non viene mai dedotto dal testo.

Consultazione: solo i messaggi instradati della propria agenzia. Titolare e
amministratori vedono tutto, compresi i messaggi da associare; un agente vede
solo i messaggi dei contatti a lui assegnati (stessa regola di core/scope.py).
Associazione: titolare e amministratori (o Super Admin che opera
nell'agenzia), con autore e data scritti dal server e registro append-only.
"""
from __future__ import annotations

import json
import logging

from core.database import core_cursor
from operator_auth.permissions import sees_all_agency_records

from . import repository, routing
from .enums import (
    CONTACT_NOT_FOUND_MESSAGE,
    LINK_FORBIDDEN_MESSAGE,
    MATCH_AMBIGUOUS,
    MATCH_MATCHED,
    MATCH_NONE,
    MESSAGE_NOT_FOUND_MESSAGE,
    MESSAGE_TYPE_LABELS,
    NOT_INSTALLED_MESSAGE,
    PROPERTY_NOT_FOUND_MESSAGE,
    ROUTED,
)
from .parser import ParsedPayload

log = logging.getLogger(__name__)

LIST_LIMIT = 200


class InboundError(Exception):
    status = 400

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class NotInstalled(InboundError):
    status = 503


class NotFound(InboundError):
    status = 404


class Forbidden(InboundError):
    status = 403


# ---------------------------------------------------------------------------
# webhook
# ---------------------------------------------------------------------------

def ingest(parsed: ParsedPayload, routes: dict[str, set[int]] | None = None) -> dict:
    """Salva messaggi ed eventi del payload. Ritorna i conteggi (nessun dato
    personale). Qualsiasi errore di scrittura annulla TUTTO e risale."""
    routes = routing.configured_routes() if routes is None else routes
    summary = {"messages_new": 0, "messages_duplicate": 0, "events_new": 0, "events_duplicate": 0, "unrouted": 0}
    with core_cursor(commit=True) as (_, cur):
        if not repository.installed(cur):
            raise NotInstalled("NOT_INSTALLED", NOT_INSTALLED_MESSAGE)
        resolved: dict[str | None, tuple] = {}

        def route(number):
            if number not in resolved:
                resolved[number] = routing.resolve(cur, number, routes)
            return resolved[number]

        for m in parsed.messages:
            status, agency_id, reason = route(m.phone_number_id)
            contact_id, match = None, MATCH_NONE
            if status == ROUTED:
                found = repository.contacts_by_phone(cur, agency_id, m.sender_phone)
                if len(found) == 1:
                    contact_id, match = found[0], MATCH_MATCHED
                elif len(found) > 1:
                    match = MATCH_AMBIGUOUS
            else:
                summary["unrouted"] += 1
            new = repository.insert_message(cur, m, routing_status=status, agency_id=agency_id,
                                            routing_reason=reason, contact_id=contact_id, contact_match=match)
            summary["messages_new" if new else "messages_duplicate"] += 1
        for e in parsed.events:
            status, agency_id, _ = route(e.phone_number_id)
            new = repository.insert_event(cur, e, routing_status=status, agency_id=agency_id)
            summary["events_new" if new else "events_duplicate"] += 1
    log.info("whatsapp_inbound %s", json.dumps(summary))
    return summary


# ---------------------------------------------------------------------------
# consultazione
# ---------------------------------------------------------------------------

def _agent_scope(ctx) -> int | None:
    return None if sees_all_agency_records(ctx.role, ctx.is_platform_admin) else ctx.user_id


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _contact_name(row) -> str | None:
    name = row.get("contact_name") or " ".join(x for x in (row.get("contact_first_name"), row.get("contact_last_name")) if x)
    return name or None


def _message(row) -> dict:
    return {
        "id": row["id"], "sender_phone": row["sender_phone"], "sender_profile_name": row.get("sender_profile_name"),
        "message_type": row["message_type"],
        "message_type_label": MESSAGE_TYPE_LABELS.get(row["message_type"], f"Tipo «{row['message_type']}»"),
        "is_text": row["message_type"] == "text", "text": row.get("text_body"), "metadata": row.get("metadata") or {},
        "provider_timestamp": _iso(row.get("provider_timestamp")), "received_at": _iso(row["received_at"]),
        "contact": {"id": row["contact_id"], "name": _contact_name(row)} if row.get("contact_id") else None,
        "contact_match": row["contact_match"],
        "property": ({"id": row["property_id"], "title": row.get("property_title"), "code": row.get("property_code")}
                     if row.get("property_id") else None),
        "linked_by_user_id": row.get("linked_by_user_id"), "linked_at": _iso(row.get("linked_at")),
    }


def _require_installed(cur):
    if not repository.installed(cur):
        raise NotInstalled("NOT_INSTALLED", NOT_INSTALLED_MESSAGE)


def list_conversations(ctx, *, only_unlinked: bool = False) -> dict:
    agency_id = ctx.require_agency()
    agent = _agent_scope(ctx)
    with core_cursor() as (_, cur):
        _require_installed(cur)
        rows = repository.list_conversations(cur, agency_id, agent_user_id=agent, only_unlinked=only_unlinked,
                                             limit=LIST_LIMIT)
        last = {m["id"]: m for m in repository.messages_by_ids(cur, agency_id, [r["last_message_id"] for r in rows])}
    items = []
    for r in rows:
        m = last.get(r["last_message_id"])
        items.append({
            "sender_phone": r["sender_phone"], "messages": int(r["messages"]),
            "without_contact": int(r["without_contact"]), "without_property": int(r["without_property"]),
            "last_received_at": _iso(r["last_received_at"]), "last_message": _message(m) if m else None,
        })
    return {"items": items, "can_link": agent is None, "limit": LIST_LIMIT}


def conversation(ctx, sender_phone: str) -> dict:
    agency_id = ctx.require_agency()
    agent = _agent_scope(ctx)
    with core_cursor() as (_, cur):
        _require_installed(cur)
        rows = repository.conversation_messages(cur, agency_id, sender_phone, agent_user_id=agent, limit=LIST_LIMIT)
        candidates = repository.candidate_contacts(cur, agency_id, sender_phone) if agent is None and rows else []
    if not rows:
        raise NotFound("MESSAGE_NOT_FOUND", MESSAGE_NOT_FOUND_MESSAGE)
    return {"sender_phone": sender_phone, "messages": [_message(r) for r in rows], "can_link": agent is None,
            "contact_candidates": [{"id": c["id"], "name": _contact_name({"contact_name": c["display_name"],
                                                                           "contact_first_name": c["first_name"],
                                                                           "contact_last_name": c["last_name"]})}
                                   for c in candidates]}


def get_message(ctx, message_id: int) -> dict:
    agency_id = ctx.require_agency()
    agent = _agent_scope(ctx)
    with core_cursor() as (_, cur):
        _require_installed(cur)
        row = repository.get_message(cur, agency_id, message_id, agent_user_id=agent)
        history = repository.link_history(cur, agency_id, message_id) if row and agent is None else []
    if row is None:
        raise NotFound("MESSAGE_NOT_FOUND", MESSAGE_NOT_FOUND_MESSAGE)
    out = _message(row)
    out["link_history"] = [{k: _iso(v) for k, v in h.items()} for h in history]
    out["can_link"] = agent is None
    return out


# ---------------------------------------------------------------------------
# associazione manuale
# ---------------------------------------------------------------------------

def link(ctx, message_id: int, contact_id: int | None, property_id: int | None) -> dict:
    """Collega (o scollega, con None) contatto e immobile di un messaggio.
    Non crea richieste, attivita' o contatti: e' solo un collegamento."""
    agency_id = ctx.require_agency()
    if _agent_scope(ctx) is not None:
        raise Forbidden("LINK_FORBIDDEN", LINK_FORBIDDEN_MESSAGE)
    with core_cursor(commit=True) as (_, cur):
        _require_installed(cur)
        row = repository.get_message(cur, agency_id, message_id, lock=True)
        if row is None:
            raise NotFound("MESSAGE_NOT_FOUND", MESSAGE_NOT_FOUND_MESSAGE)
        if contact_id is not None and repository.live_contact(cur, agency_id, contact_id) is None:
            raise NotFound("CONTACT_NOT_FOUND", CONTACT_NOT_FOUND_MESSAGE)
        if property_id is not None and repository.live_property(cur, agency_id, property_id) is None:
            raise NotFound("PROPERTY_NOT_FOUND", PROPERTY_NOT_FOUND_MESSAGE)
        changed = (row["contact_id"], row["property_id"]) != (contact_id, property_id)
        if changed:
            repository.link(cur, agency_id=agency_id, message_id=message_id, contact_id=contact_id,
                            property_id=property_id, previous_contact_id=row["contact_id"],
                            previous_property_id=row["property_id"], user_id=ctx.user_id)
    result = get_message(ctx, message_id)
    result["changed"] = changed
    return result
