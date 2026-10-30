"""BLOCCO-4A - SQL della ricezione WhatsApp. Ogni lettura di tenant e' filtrata
per l'agenzia gia' ricavata dal contesto; le righe non instradate (senza
agenzia) non sono mai restituite da queste funzioni di lettura."""
from __future__ import annotations

from psycopg2.extras import Json

_LIVE_CONTACT = "(to_jsonb(c)->>'deleted_at') IS NULL"


def installed(cur) -> bool:
    cur.execute("SELECT to_regclass('whatsapp_inbound_messages') IS NOT NULL AS installed")
    return bool(cur.fetchone()["installed"])


# --- scrittura dal webhook -----------------------------------------------------

def contacts_by_phone(cur, agency_id: int, phone: str) -> list[int]:
    cur.execute(f"SELECT c.id FROM contacts c WHERE c.agency_id = %s AND c.phone_normalized = %s AND {_LIVE_CONTACT} "
                "ORDER BY c.id LIMIT 3", (agency_id, phone))
    return [r["id"] for r in cur.fetchall()]


def insert_message(cur, m, *, routing_status, agency_id, routing_reason, contact_id, contact_match) -> bool:
    """True se la riga e' nuova; False se il provider l'aveva gia' consegnata."""
    cur.execute(
        "INSERT INTO whatsapp_inbound_messages (provider_message_id, phone_number_id, display_phone_number, "
        "agency_id, routing_status, routing_reason, sender_phone, sender_profile_name, message_type, text_body, "
        "metadata, provider_timestamp, contact_id, contact_match) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (provider_message_id) DO NOTHING RETURNING id",
        (m.provider_message_id, m.phone_number_id, m.display_phone_number, agency_id, routing_status,
         routing_reason, m.sender_phone, m.sender_profile_name, m.message_type, m.text_body, Json(m.metadata),
         m.provider_timestamp, contact_id, contact_match))
    return cur.fetchone() is not None


def insert_event(cur, e, *, routing_status, agency_id) -> bool:
    cur.execute(
        "INSERT INTO whatsapp_webhook_events (event_kind, dedupe_key, phone_number_id, agency_id, routing_status, "
        "provider_message_id, status, provider_timestamp, details) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (dedupe_key) DO NOTHING RETURNING id",
        (e.event_kind, e.dedupe_key, e.phone_number_id, agency_id, routing_status, e.provider_message_id,
         e.status, e.provider_timestamp, Json(e.details)))
    return cur.fetchone() is not None


# --- lettura per l'interfaccia ---------------------------------------------------

_MESSAGE_COLUMNS = (
    "m.id, m.provider_message_id, m.sender_phone, m.sender_profile_name, m.message_type, m.text_body, m.metadata, "
    "m.provider_timestamp, m.received_at, m.contact_id, m.contact_match, m.property_id, m.linked_by_user_id, "
    "m.linked_at, c.display_name AS contact_name, c.first_name AS contact_first_name, "
    "c.last_name AS contact_last_name, p.title AS property_title, p.code AS property_code")

_JOINS = ("FROM whatsapp_inbound_messages m "
          "LEFT JOIN contacts c ON c.id = m.contact_id AND c.agency_id = m.agency_id "
          "LEFT JOIN properties p ON p.id = m.property_id AND p.agency_id = m.agency_id ")


def _agent_filter(agent_user_id: int | None) -> tuple[str, tuple]:
    """L'agente vede solo i messaggi dei contatti a lui assegnati (core/scope.py)."""
    if agent_user_id is None:
        return "", ()
    return " AND c.assigned_agent_id = %s", (agent_user_id,)


def list_conversations(cur, agency_id: int, *, agent_user_id: int | None, only_unlinked: bool, limit: int):
    extra, params = _agent_filter(agent_user_id)
    having = " HAVING count(*) FILTER (WHERE m.contact_id IS NULL OR m.property_id IS NULL) > 0" if only_unlinked else ""
    cur.execute(
        "SELECT m.sender_phone, max(m.received_at) AS last_received_at, count(*) AS messages, "
        "count(*) FILTER (WHERE m.contact_id IS NULL) AS without_contact, "
        "count(*) FILTER (WHERE m.property_id IS NULL) AS without_property, "
        "(array_agg(m.id ORDER BY m.received_at DESC, m.id DESC))[1] AS last_message_id "
        f"{_JOINS} WHERE m.agency_id = %s{extra} GROUP BY m.sender_phone{having} "
        "ORDER BY max(m.received_at) DESC LIMIT %s",
        (agency_id, *params, limit))
    return [dict(r) for r in cur.fetchall()]


def messages_by_ids(cur, agency_id: int, ids: list[int]):
    if not ids:
        return []
    cur.execute(f"SELECT {_MESSAGE_COLUMNS} {_JOINS} WHERE m.agency_id = %s AND m.id = ANY(%s)", (agency_id, ids))
    return [dict(r) for r in cur.fetchall()]


def conversation_messages(cur, agency_id: int, sender_phone: str, *, agent_user_id: int | None, limit: int):
    extra, params = _agent_filter(agent_user_id)
    cur.execute(f"SELECT {_MESSAGE_COLUMNS} {_JOINS} WHERE m.agency_id = %s AND m.sender_phone = %s{extra} "
                "ORDER BY m.received_at DESC, m.id DESC LIMIT %s", (agency_id, sender_phone, *params, limit))
    return [dict(r) for r in cur.fetchall()]


def get_message(cur, agency_id: int, message_id: int, *, agent_user_id: int | None = None, lock: bool = False):
    extra, params = _agent_filter(agent_user_id)
    cur.execute(f"SELECT {_MESSAGE_COLUMNS}, m.agency_id {_JOINS} WHERE m.id = %s AND m.agency_id = %s{extra}"
                + (" FOR UPDATE OF m" if lock else ""), (message_id, agency_id, *params))
    row = cur.fetchone()
    return dict(row) if row else None


def live_contact(cur, agency_id: int, contact_id: int):
    cur.execute(f"SELECT c.id FROM contacts c WHERE c.id = %s AND c.agency_id = %s AND {_LIVE_CONTACT}",
                (contact_id, agency_id))
    return cur.fetchone()


def live_property(cur, agency_id: int, property_id: int):
    cur.execute("SELECT id FROM properties WHERE id = %s AND agency_id = %s "
                "AND (to_jsonb(properties)->>'deleted_at') IS NULL", (property_id, agency_id))
    return cur.fetchone()


def link(cur, *, agency_id, message_id, contact_id, property_id, previous_contact_id, previous_property_id, user_id):
    cur.execute(
        "UPDATE whatsapp_inbound_messages SET contact_id = %s, property_id = %s, contact_match = 'manual', "
        "linked_by_user_id = %s, linked_at = NOW() WHERE id = %s AND agency_id = %s RETURNING id",
        (contact_id, property_id, user_id, message_id, agency_id))
    if cur.fetchone() is None:
        return False
    cur.execute(
        "INSERT INTO whatsapp_inbound_message_links (message_id, agency_id, previous_contact_id, previous_property_id, "
        "contact_id, property_id, linked_by_user_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (message_id, agency_id, previous_contact_id, previous_property_id, contact_id, property_id, user_id))
    return True


def link_history(cur, agency_id: int, message_id: int):
    cur.execute("SELECT previous_contact_id, previous_property_id, contact_id, property_id, linked_by_user_id, "
                "linked_at FROM whatsapp_inbound_message_links WHERE message_id = %s AND agency_id = %s "
                "ORDER BY linked_at DESC, id DESC", (message_id, agency_id))
    return [dict(r) for r in cur.fetchall()]


def candidate_contacts(cur, agency_id: int, phone: str):
    cur.execute(f"SELECT c.id, c.display_name, c.first_name, c.last_name FROM contacts c "
                f"WHERE c.agency_id = %s AND c.phone_normalized = %s AND {_LIVE_CONTACT} ORDER BY c.id LIMIT 20",
                (agency_id, phone))
    return [dict(r) for r in cur.fetchall()]


# --- BLOCCO-4B: letture per le risposte manuali ---------------------------------

def last_inbound(cur, agency_id: int, sender_phone: str, *, agent_user_id: int | None):
    """L'ultimo messaggio ricevuto della conversazione nell'agenzia (visibile a chi chiede)."""
    extra, params = _agent_filter(agent_user_id)
    # L'ora del cliente e' quella dichiarata da Meta, MA mai dopo la ricezione:
    # un orario futuro non e' attendibile e non puo' allungare la finestra.
    cur.execute("SELECT m.id, m.phone_number_id, m.contact_id, m.property_id, m.provider_timestamp, m.received_at, "
                "LEAST(COALESCE(m.provider_timestamp, m.received_at), m.received_at) AS customer_at "
                f"{_JOINS} WHERE m.agency_id = %s AND m.sender_phone = %s{extra} "
                "ORDER BY LEAST(COALESCE(m.provider_timestamp, m.received_at), m.received_at) DESC, m.id DESC LIMIT 1",
                (agency_id, sender_phone, *params))
    row = cur.fetchone()
    return dict(row) if row else None


def delivery_statuses(cur, provider_message_ids: list[str]) -> dict[str, dict]:
    """L'ultimo stato di consegna ricevuto da Meta per ciascun messaggio inviato."""
    if not provider_message_ids:
        return {}
    cur.execute("SELECT DISTINCT ON (provider_message_id) provider_message_id, status, provider_timestamp, details "
                "FROM whatsapp_webhook_events WHERE event_kind = 'status' AND provider_message_id = ANY(%s) "
                "ORDER BY provider_message_id, provider_timestamp DESC NULLS LAST, id DESC",
                (provider_message_ids,))
    return {r["provider_message_id"]: dict(r) for r in cur.fetchall()}


def operator_names(cur, user_ids: list[int]) -> dict[int, str]:
    if not user_ids:
        return {}
    cur.execute("SELECT id, first_name, last_name FROM operator_users WHERE id = ANY(%s)", (user_ids,))
    return {r["id"]: " ".join(x for x in (r["first_name"], r["last_name"]) if x) or f"Operatore n. {r['id']}"
            for r in cur.fetchall()}
