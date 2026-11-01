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
from datetime import datetime, timezone
from types import SimpleNamespace

from core.database import core_cursor
from operator_auth.context import SystemAgencyContext
from operator_auth.permissions import sees_all_agency_records

from . import contact_match, repository, routing
from .enums import (
    CONTACT_NOT_FOUND_MESSAGE,
    LINK_FORBIDDEN_MESSAGE,
    MATCH_NONE,
    MESSAGE_NOT_FOUND_MESSAGE,
    MESSAGE_TYPE_LABELS,
    NOT_INSTALLED_MESSAGE,
    PROPERTY_NOT_FOUND_MESSAGE,
    READS_NOT_INSTALLED_MESSAGE,
    ROUTED,
    STOP_KEYWORDS,
)
from .parser import ParsedPayload

log = logging.getLogger(__name__)

INBOUND_ORIGIN = "whatsapp_inbound"

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
            decided = {"contact_id": None, "match": MATCH_NONE, "linked_by_user_id": None, "linked_at": None}
            if status == ROUTED:
                # Rubrica dell'agenzia su tutti i campi telefonici; l'associazione
                # manuale della conversazione, se c'e', vince (contact_match).
                decided = contact_match.resolve(cur, agency_id, m.sender_phone)
            else:
                summary["unrouted"] += 1
            contact_id = decided["contact_id"]
            new = repository.insert_message(cur, m, routing_status=status, agency_id=agency_id,
                                            routing_reason=reason, contact_id=contact_id,
                                            contact_match=decided["match"],
                                            linked_by_user_id=decided["linked_by_user_id"],
                                            linked_at=decided["linked_at"])
            summary["messages_new" if new else "messages_duplicate"] += 1
            if new and status == ROUTED:
                # La conversazione resta coerente: i messaggi precedenti senza contatto
                # prendono quello deciso adesso (mai quelli decisi a mano).
                summary["backfilled"] = summary.get("backfilled", 0) + contact_match.backfill_conversation(
                    cur, agency_id, m.sender_phone, decided)
            if new and is_stop_request(m) and status == ROUTED and contact_id is not None:
                _record_stop(cur, SystemAgencyContext(agency_id=agency_id, origin=INBOUND_ORIGIN),
                             contact_id=contact_id, provider_message_id=m.provider_message_id,
                             decided_at=m.provider_timestamp)
                summary["stop_revocations"] = summary.get("stop_revocations", 0) + 1
        for e in parsed.events:
            status, agency_id, _ = route(e.phone_number_id)
            new = repository.insert_event(cur, e, routing_status=status, agency_id=agency_id)
            summary["events_new" if new else "events_duplicate"] += 1
    log.info("whatsapp_inbound %s", json.dumps(summary))
    return summary


def is_stop_request(m) -> bool:
    """BLOCCO-4B: un messaggio di testo che dice solo STOP (maiuscole,
    minuscole e punteggiatura ignorate). Nessun'altra parola e' interpretata."""
    if m.message_type != "text" or not m.text_body:
        return False
    return "".join(ch for ch in m.text_body.upper() if ch.isalnum()) in STOP_KEYWORDS


def _record_stop(cur, ctx, *, contact_id: int, provider_message_id: str, decided_at=None) -> None:
    """Lo STOP revoca il consenso marketing WhatsApp di QUEL contatto, SOLO
    nell'agenzia del numero che l'ha ricevuto (`consent.service.record_revocation`,
    fonte `whatsapp_optout`, decisa dal soggetto), nella stessa transazione del
    messaggio e idempotente su messaggio e contatto.

    NON e' la rinuncia di piattaforma (`communication_optouts`): quel registro
    vale per indirizzo e per TUTTE le agenzie, e lo STOP scritto a un'agenzia
    non e' una dichiarazione verso le altre. Senza un contatto certo (numero
    non instradato, nessun contatto o piu' contatti con quel numero) non si
    revoca nulla d'ufficio: lo si fa quando un operatore associa il contatto.
    Una risposta del cliente non e' mai consenso marketing, e la revoca non
    autorizza ne' vieta le risposte di assistenza (vedi `message_replies`)."""
    from consent import service as consent_service

    consent_service.record_revocation(
        ctx, contact_id=contact_id, purpose="marketing", channel="whatsapp", source="whatsapp_optout",
        actor_type="subject", decided_at=decided_at or datetime.now(timezone.utc),
        note="STOP ricevuto su WhatsApp", idempotency_key=f"whatsapp_stop:{provider_message_id}:{contact_id}",
        cur=cur)


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


def _auto_reconcile(agency_id: int) -> None:
    """Riconoscimento predefinito, senza pulsanti: prima di mostrare la casella
    le conversazioni dell'agenzia senza contatto e non decise a mano prendono
    il contatto della rubrica (stessa regola della ricezione). Transazione
    propria, idempotente; un errore qui non impedisce la lettura."""
    try:
        with core_cursor(commit=True) as (_, cur):
            if repository.installed(cur):
                summary = contact_match.reconcile(cur, agency_id)
                if summary["linked_conversations"]:
                    log.info("whatsapp_inbound_auto_reconcile agency=%s %s", agency_id, json.dumps(summary))
    except Exception as exc:  # pragma: no cover - difesa: la casella si legge comunque
        log.warning("whatsapp_inbound_auto_reconcile_failed agency=%s error_type=%s", agency_id, type(exc).__name__)


def list_conversations(ctx, *, only_unlinked: bool = False) -> dict:
    agency_id = ctx.require_agency()
    agent = _agent_scope(ctx)
    _auto_reconcile(agency_id)
    with core_cursor() as (_, cur):
        _require_installed(cur)
        reads = repository.reads_installed(cur)
        rows = repository.list_conversations(cur, agency_id, agent_user_id=agent, only_unlinked=only_unlinked,
                                             limit=LIST_LIMIT, reader_user_id=ctx.user_id if reads else None)
        last = {m["id"]: m for m in repository.messages_by_ids(cur, agency_id, [r["last_message_id"] for r in rows])}
    items = []
    for r in rows:
        m = last.get(r["last_message_id"])
        items.append({
            "sender_phone": r["sender_phone"], "messages": int(r["messages"]),
            "without_contact": int(r["without_contact"]), "without_property": int(r["without_property"]),
            "unread": None if r["unread"] is None else int(r["unread"]),
            "last_received_at": _iso(r["last_received_at"]), "last_message": _message(m) if m else None,
        })
    return {"items": items, "can_link": agent is None, "limit": LIST_LIMIT, "unread_available": reads}


def conversation(ctx, sender_phone: str) -> dict:
    agency_id = ctx.require_agency()
    agent = _agent_scope(ctx)
    _auto_reconcile(agency_id)
    with core_cursor() as (_, cur):
        _require_installed(cur)
        rows = repository.conversation_messages(cur, agency_id, sender_phone, agent_user_id=agent, limit=LIST_LIMIT)
        # Tutti i contatti con questo numero: anche quando uno e' gia' collegato,
        # cosi' l'operatore puo' cambiare la scelta automatica.
        candidates = repository.candidate_contacts(cur, agency_id, sender_phone) if agent is None and rows else []
        contact_ids = sorted({r["contact_id"] for r in rows if r.get("contact_id")})
        stime = repository.related_stime(cur, agency_id, sender_phone, contact_ids) if rows else []
    if not rows:
        raise NotFound("MESSAGE_NOT_FOUND", MESSAGE_NOT_FOUND_MESSAGE)
    # Il contatto della conversazione: quello dell'ultimo messaggio che ne ha uno.
    current = next((r for r in rows if r.get("contact_id")), None)
    return {"sender_phone": sender_phone, "messages": [_message(r) for r in rows], "can_link": agent is None,
            "contact": ({"id": current["contact_id"], "name": _contact_name(current),
                         "match": current["contact_match"],
                         "others_with_number": max(0, len([c for c in candidates
                                                           if c["id"] != current["contact_id"]]))} if current else None),
            "sender_profile_name": next((r["sender_profile_name"] for r in rows if r.get("sender_profile_name")), None),
            "stime": [_stima(x) for x in stime],
            "contact_candidates": [{"id": c["id"], "name": _contact_name({"contact_name": c["display_name"],
                                                                           "contact_first_name": c["first_name"],
                                                                           "contact_last_name": c["last_name"]}),
                                    "on_phone": bool(c.get("on_phone")), "status": c.get("status"),
                                    "created_at": _iso(c.get("created_at"))}
                                   for c in candidates]}


def _stima(row) -> dict:
    """Una stima legata alla conversazione, con il motivo del legame."""
    return {"id": row["id"], "data": _iso(row.get("data")), "comune": row.get("comune"), "via": row.get("via"),
            "civico": row.get("civico"), "tipologia": row.get("tipologia"),
            "name": " ".join(x for x in (row.get("nome"), row.get("cognome")) if x) or None,
            "match": "contact" if row.get("by_contact") else "phone"}


def mark_read(ctx, sender_phone: str) -> dict:
    """Segna come letti, per l'operatore della sessione, i messaggi della
    conversazione che vede adesso. Non cambia lo stato degli altri operatori."""
    agency_id = ctx.require_agency()
    agent = _agent_scope(ctx)
    with core_cursor(commit=True) as (_, cur):
        _require_installed(cur)
        if not repository.reads_installed(cur):
            raise NotInstalled("READS_NOT_INSTALLED", READS_NOT_INSTALLED_MESSAGE)
        rows = repository.conversation_messages(cur, agency_id, sender_phone, agent_user_id=agent, limit=LIST_LIMIT)
        if not rows:
            raise NotFound("MESSAGE_NOT_FOUND", MESSAGE_NOT_FOUND_MESSAGE)
        last = max(r["id"] for r in rows)
        repository.mark_read(cur, agency_id, ctx.user_id, sender_phone, last)
    return {"sender_phone": sender_phone, "last_read_message_id": last, "unread": 0}


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
            if (contact_id is not None and contact_id != row["contact_id"]
                    and is_stop_request(SimpleNamespace(message_type=row["message_type"], text_body=row["text_body"]))):
                # Lo STOP del soggetto, ora attribuito a un contatto di questa agenzia.
                _record_stop(cur, ctx, contact_id=contact_id, provider_message_id=row["provider_message_id"],
                             decided_at=row["provider_timestamp"])
    result = get_message(ctx, message_id)
    result["changed"] = changed
    return result


def link_conversation(ctx, sender_phone: str, contact_id: int | None) -> dict:
    """Collega (o scollega) TUTTI i messaggi della conversazione a un contatto,
    con lo stesso registro dell'associazione per messaggio. Gli immobili dei
    singoli messaggi non cambiano. Non crea richieste, attivita' o contatti."""
    agency_id = ctx.require_agency()
    if _agent_scope(ctx) is not None:
        raise Forbidden("LINK_FORBIDDEN", LINK_FORBIDDEN_MESSAGE)
    with core_cursor(commit=True) as (_, cur):
        _require_installed(cur)
        rows = repository.conversation_rows_for_link(cur, agency_id, sender_phone)
        if not rows:
            raise NotFound("MESSAGE_NOT_FOUND", MESSAGE_NOT_FOUND_MESSAGE)
        if contact_id is not None and repository.live_contact(cur, agency_id, contact_id) is None:
            raise NotFound("CONTACT_NOT_FOUND", CONTACT_NOT_FOUND_MESSAGE)
        changed = 0
        for row in rows:
            if row["contact_id"] == contact_id:
                continue
            repository.link(cur, agency_id=agency_id, message_id=row["id"], contact_id=contact_id,
                            property_id=row["property_id"], previous_contact_id=row["contact_id"],
                            previous_property_id=row["property_id"], user_id=ctx.user_id)
            changed += 1
            if (contact_id is not None
                    and is_stop_request(SimpleNamespace(message_type=row["message_type"], text_body=row["text_body"]))):
                _record_stop(cur, ctx, contact_id=contact_id, provider_message_id=row["provider_message_id"],
                             decided_at=row["provider_timestamp"])
    result = conversation(ctx, sender_phone)
    result["changed"] = changed
    return result


def reconcile_contacts(ctx) -> dict:
    """Riconciliazione retroattiva per l'agenzia della sessione (titolare o
    amministratore): vedi contact_match.reconcile. Una transazione."""
    agency_id = ctx.require_agency()
    if _agent_scope(ctx) is not None:
        raise Forbidden("LINK_FORBIDDEN", LINK_FORBIDDEN_MESSAGE)
    with core_cursor(commit=True) as (_, cur):
        _require_installed(cur)
        summary = contact_match.reconcile(cur, agency_id)
    log.info("whatsapp_inbound_reconcile agency=%s %s", agency_id, json.dumps(summary))
    return summary
