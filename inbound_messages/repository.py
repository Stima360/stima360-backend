"""BLOCCO-4A - SQL della ricezione WhatsApp. Ogni lettura di tenant e' filtrata
per l'agenzia gia' ricavata dal contesto; le righe non instradate (senza
agenzia) non sono mai restituite da queste funzioni di lettura."""
from __future__ import annotations

from psycopg2.extras import Json

_LIVE_CONTACT = "(to_jsonb(c)->>'deleted_at') IS NULL"


def installed(cur) -> bool:
    cur.execute("SELECT to_regclass('whatsapp_inbound_messages') IS NOT NULL AS installed")
    return bool(cur.fetchone()["installed"])


def coexistence_installed(cur) -> bool:
    """La 132: direzione e origine dei messaggi, rubrica dell'app, collegamenti Meta."""
    cur.execute("SELECT to_regclass('whatsapp_app_contacts') IS NOT NULL AND EXISTS (SELECT 1 FROM "
                "information_schema.columns WHERE table_name = 'whatsapp_inbound_messages' "
                "AND column_name = 'origin') AS installed")
    return bool(cur.fetchone()["installed"])


#: Un messaggio del CLIENTE ricevuto in diretta: il solo che conta per i non
#: letti e per le risposte automatiche. Funziona anche senza la 132 (colonne
#: assenti = tutte le righe sono ricezioni di sempre).
LIVE_CUSTOMER_SQL = ("COALESCE(to_jsonb(m)->>'direction', 'in') = 'in' "
                     "AND COALESCE(to_jsonb(m)->>'origin', 'webhook') = 'webhook'")
#: Un messaggio del cliente, anche dallo storico (finestra di 24 ore, STOP).
CUSTOMER_SQL = "COALESCE(to_jsonb(m)->>'direction', 'in') = 'in'"


# --- scrittura dal webhook -----------------------------------------------------

def contacts_by_phone(cur, agency_id: int, phone: str) -> list[int]:
    """Gli id dei contatti con quel numero (tutti i campi telefonici): vedi contact_match."""
    from . import contact_match

    return [c["id"] for c in contact_match.candidates(cur, agency_id, phone, limit=3)]


def insert_message(cur, m, *, routing_status, agency_id, routing_reason, contact_id, contact_match,
                   linked_by_user_id=None, linked_at=None, coexistence: bool = False) -> bool:
    """True se la riga e' nuova; False se il provider l'aveva gia' consegnata.
    `linked_by_user_id`/`linked_at`: solo quando il messaggio eredita
    l'associazione manuale della conversazione (contact_match.resolve).

    COEXISTENCE (132): direzione e origine. Un messaggio dello storico prende
    come ora di ricezione la sua ora originale, cosi' la conversazione resta in
    ordine e non sembra arrivato adesso."""
    if coexistence:
        received_at = m.provider_timestamp if (m.origin == "history" and m.provider_timestamp) else None
        cur.execute(
            "INSERT INTO whatsapp_inbound_messages (provider_message_id, phone_number_id, display_phone_number, "
            "agency_id, routing_status, routing_reason, sender_phone, sender_profile_name, message_type, text_body, "
            "metadata, provider_timestamp, contact_id, contact_match, linked_by_user_id, linked_at, direction, origin, "
            "received_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "COALESCE(%s, NOW())) ON CONFLICT (provider_message_id) DO NOTHING RETURNING id",
            (m.provider_message_id, m.phone_number_id, m.display_phone_number, agency_id, routing_status,
             routing_reason, m.sender_phone, m.sender_profile_name, m.message_type, m.text_body, Json(m.metadata),
             m.provider_timestamp, contact_id, contact_match, linked_by_user_id, linked_at, m.direction, m.origin,
             received_at))
        return cur.fetchone() is not None
    cur.execute(
        "INSERT INTO whatsapp_inbound_messages (provider_message_id, phone_number_id, display_phone_number, "
        "agency_id, routing_status, routing_reason, sender_phone, sender_profile_name, message_type, text_body, "
        "metadata, provider_timestamp, contact_id, contact_match, linked_by_user_id, linked_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (provider_message_id) DO NOTHING RETURNING id",
        (m.provider_message_id, m.phone_number_id, m.display_phone_number, agency_id, routing_status,
         routing_reason, m.sender_phone, m.sender_profile_name, m.message_type, m.text_body, Json(m.metadata),
         m.provider_timestamp, contact_id, contact_match, linked_by_user_id, linked_at))
    return cur.fetchone() is not None


def is_crm_reply(cur, agency_id: int, provider_message_id: str) -> bool:
    """Un messaggio gia' registrato come risposta inviata dal CRM (stesso wamid):
    un'eco o lo storico non lo duplicano nella conversazione. La lettura e' del
    dominio delle comunicazioni (il registro ha un solo proprietario)."""
    from communication import repository as communication_repository
    from operator_auth.context import SystemAgencyContext

    ctx = SystemAgencyContext(agency_id=agency_id, origin="whatsapp_inbound")
    return communication_repository.has_provider_message(cur, ctx, provider_message_id)


def upsert_app_contact(cur, agency_id: int, change) -> str:
    """Una voce della rubrica dell'app: 'new', 'updated' o 'stale' (piu' vecchia
    di quanto gia' registrato: un ritardo di consegna non torna indietro)."""
    state = "present" if change.action == "add" else "removed"
    cur.execute(
        "INSERT INTO whatsapp_app_contacts AS a (agency_id, phone_number_id, contact_phone, full_name, first_name, "
        "state, meta_changed_at) VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (agency_id, phone_number_id, contact_phone) DO UPDATE SET "
        "full_name = COALESCE(EXCLUDED.full_name, a.full_name), first_name = COALESCE(EXCLUDED.first_name, a.first_name), "
        "state = EXCLUDED.state, meta_changed_at = EXCLUDED.meta_changed_at, last_synced_at = NOW() "
        "WHERE a.meta_changed_at IS NULL OR EXCLUDED.meta_changed_at IS NULL "
        "OR EXCLUDED.meta_changed_at >= a.meta_changed_at "
        "RETURNING (xmax = 0) AS inserted",
        (agency_id, change.phone_number_id, change.contact_phone, change.full_name, change.first_name, state,
         change.changed_at))
    row = cur.fetchone()
    if row is None:
        return "stale"
    return "new" if row["inserted"] else "updated"


def app_contact_names(cur, agency_id: int, pairs) -> dict:
    """{(phone_number_id, numero): nome} dalla rubrica dell'app, solo voci presenti."""
    pairs = list(pairs)
    if not pairs:
        return {}
    cur.execute("SELECT to_regclass('whatsapp_app_contacts') IS NOT NULL AS ok")
    if not cur.fetchone()["ok"]:
        return {}
    cur.execute("SELECT phone_number_id, contact_phone, COALESCE(full_name, first_name) AS name "
                "FROM whatsapp_app_contacts WHERE agency_id = %s AND state = 'present' "
                "AND (phone_number_id, contact_phone) IN (SELECT * FROM unnest(%s::text[], %s::text[]))",
                (agency_id, [p for p, _ in pairs], [n for _, n in pairs]))
    return {(r["phone_number_id"], r["contact_phone"]): r["name"] for r in cur.fetchall() if r["name"]}


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
    "m.id, m.provider_message_id, m.phone_number_id, m.display_phone_number, "
    "m.sender_phone, m.sender_profile_name, m.message_type, m.text_body, m.metadata, "
    "m.provider_timestamp, m.received_at, m.contact_id, m.contact_match, m.property_id, m.linked_by_user_id, "
    "m.linked_at, COALESCE(to_jsonb(m)->>'direction', 'in') AS direction, "
    "COALESCE(to_jsonb(m)->>'origin', 'webhook') AS origin, "
    "c.display_name AS contact_name, c.first_name AS contact_first_name, "
    "c.last_name AS contact_last_name, (to_jsonb(c)->>'private_user_id')::bigint AS contact_private_user_id, "
    "p.title AS property_title, p.code AS property_code")

_JOINS = ("FROM whatsapp_inbound_messages m "
          "LEFT JOIN contacts c ON c.id = m.contact_id AND c.agency_id = m.agency_id "
          "LEFT JOIN properties p ON p.id = m.property_id AND p.agency_id = m.agency_id ")


def _agent_filter(agent_user_id: int | None) -> tuple[str, tuple]:
    """L'agente vede solo i messaggi dei contatti a lui assegnati (core/scope.py)."""
    if agent_user_id is None:
        return "", ()
    return " AND c.assigned_agent_id = %s", (agent_user_id,)


def _inbox_filter(hidden_numbers, phone_number_id: str | None) -> tuple[str, tuple]:
    """DUE CASELLE: le caselle che l'operatore non vede (riservate senza
    autorizzazione, disattivate) e, se chiesta, UNA casella soltanto."""
    sql, params = "", ()
    if hidden_numbers:
        sql += " AND NOT (m.phone_number_id = ANY(%s))"
        params += (list(hidden_numbers),)
    if phone_number_id:
        sql += " AND m.phone_number_id = %s"
        params += (phone_number_id,)
    return sql, params


def _visibility(agent_user_id, hidden_numbers, phone_number_id) -> tuple[str, tuple]:
    a, pa = _agent_filter(agent_user_id)
    b, pb = _inbox_filter(hidden_numbers, phone_number_id)
    return a + b, pa + pb


def reads_installed(cur) -> bool:
    """La 126 (segni di lettura) E la 130 (segno per casella)."""
    cur.execute("SELECT to_regclass('whatsapp_inbound_reads') IS NOT NULL AND EXISTS (SELECT 1 FROM "
                "information_schema.columns WHERE table_name = 'whatsapp_inbound_reads' "
                "AND column_name = 'phone_number_id') AS installed")
    return bool(cur.fetchone()["installed"])


def list_conversations(cur, agency_id: int, *, agent_user_id: int | None, only_unlinked: bool, limit: int,
                       reader_user_id: int | None = None, hidden_numbers=(), phone_number_id: str | None = None):
    """Una riga per CONVERSAZIONE = (numero ricevente, numero del cliente):
    lo stesso cliente su due caselle da' due righe. `reader_user_id`: con la
    migrazione 126 (e la 130), conta i messaggi non ancora letti da
    QUELL'operatore (`unread`); senza, `unread` e' NULL."""
    extra, params = _visibility(agent_user_id, hidden_numbers, phone_number_id)
    having = " HAVING count(*) FILTER (WHERE m.contact_id IS NULL OR m.property_id IS NULL) > 0" if only_unlinked else ""
    if reader_user_id is None:
        unread, reads, read_params = "NULL::bigint AS unread", "", ()
    else:
        # Solo i messaggi del cliente ricevuti in diretta: non le eco dall'app ne' lo storico.
        unread = f"count(*) FILTER (WHERE m.id > COALESCE(r.last_read_message_id, 0) AND {LIVE_CUSTOMER_SQL}) AS unread"
        reads = ("LEFT JOIN whatsapp_inbound_reads r ON r.agency_id = m.agency_id AND r.user_id = %s "
                 "AND r.phone_number_id = m.phone_number_id AND r.sender_phone = m.sender_phone ")
        read_params = (reader_user_id,)
    cur.execute(
        "SELECT m.phone_number_id, m.sender_phone, max(m.received_at) AS last_received_at, count(*) AS messages, "
        "count(*) FILTER (WHERE m.contact_id IS NULL) AS without_contact, "
        "count(*) FILTER (WHERE m.property_id IS NULL) AS without_property, "
        f"{unread}, "
        "(array_agg(m.id ORDER BY m.received_at DESC, m.id DESC))[1] AS last_message_id "
        f"{_JOINS}{reads}WHERE m.agency_id = %s{extra} GROUP BY m.phone_number_id, m.sender_phone{having} "
        "ORDER BY max(m.received_at) DESC LIMIT %s",
        (*read_params, agency_id, *params, limit))
    return [dict(r) for r in cur.fetchall()]


def sender_inboxes(cur, agency_id: int, sender_phone: str, *, agent_user_id: int | None, hidden_numbers=()) -> list[str]:
    """I numeri riceventi su cui QUESTO cliente ha conversazioni visibili
    all'operatore, dal piu' recente."""
    extra, params = _visibility(agent_user_id, hidden_numbers, None)
    cur.execute(f"SELECT m.phone_number_id, max(m.received_at) AS last_at {_JOINS} WHERE m.agency_id = %s "
                f"AND m.sender_phone = %s{extra} GROUP BY m.phone_number_id ORDER BY max(m.received_at) DESC",
                (agency_id, sender_phone, *params))
    return [r["phone_number_id"] for r in cur.fetchall()]


def mark_read(cur, agency_id: int, user_id: int, phone_number_id: str, sender_phone: str, message_id: int) -> None:
    """Il segno avanza soltanto: una lettura piu' vecchia non lo riporta indietro."""
    cur.execute(
        "INSERT INTO whatsapp_inbound_reads (agency_id, user_id, phone_number_id, sender_phone, last_read_message_id) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (agency_id, user_id, phone_number_id, sender_phone) DO UPDATE "
        "SET last_read_message_id = GREATEST(whatsapp_inbound_reads.last_read_message_id, "
        "EXCLUDED.last_read_message_id), read_at = NOW()",
        (agency_id, user_id, phone_number_id, sender_phone, message_id))


# Stesso numero in formato internazionale: le cifre, senza `00` iniziale; un
# numero nazionale (fino a 10 cifre) prende il prefisso 39.
_STIMA_DIGITS = "regexp_replace(regexp_replace(coalesce(s.telefono, ''), '\\D', '', 'g'), '^00', '')"
_STIMA_PHONE = f"(CASE WHEN length({_STIMA_DIGITS}) <= 10 THEN '39' || {_STIMA_DIGITS} ELSE {_STIMA_DIGITS} END)"


def related_stime(cur, agency_id: int, sender_phone: str, contact_ids: list[int], limit: int = 10):
    """Le stime dell'agenzia legate alla conversazione: quelle dei contatti
    collegati (lead del contatto) e quelle con lo stesso numero di telefono.
    `match` dice come: mai un collegamento salvato, solo una lettura."""
    cur.execute(
        "SELECT s.id, s.data, s.comune, s.via, s.civico, s.tipologia, s.nome, s.cognome, "
        "bool_or(l.contact_id = ANY(%s)) AS by_contact, "
        f"bool_or(coalesce(s.telefono, '') <> '' AND {_STIMA_PHONE} = %s) AS by_phone "
        "FROM stime s LEFT JOIN lead_stime ls ON ls.stima_id = s.id "
        "LEFT JOIN leads l ON l.id = ls.lead_id AND l.agency_id = s.agency_id "
        "WHERE s.agency_id = %s AND (l.contact_id = ANY(%s) "
        f"OR (coalesce(s.telefono, '') <> '' AND {_STIMA_PHONE} = %s)) "
        "GROUP BY s.id ORDER BY s.data DESC NULLS LAST, s.id DESC LIMIT %s",
        (contact_ids, sender_phone, agency_id, contact_ids, sender_phone, limit))
    return [dict(r) for r in cur.fetchall()]


def messages_by_ids(cur, agency_id: int, ids: list[int]):
    if not ids:
        return []
    cur.execute(f"SELECT {_MESSAGE_COLUMNS} {_JOINS} WHERE m.agency_id = %s AND m.id = ANY(%s)", (agency_id, ids))
    return [dict(r) for r in cur.fetchall()]


def conversation_messages(cur, agency_id: int, sender_phone: str, *, agent_user_id: int | None, limit: int,
                          phone_number_id: str | None = None, hidden_numbers=()):
    """I messaggi della conversazione (con `phone_number_id`: di UNA casella)."""
    extra, params = _visibility(agent_user_id, hidden_numbers, phone_number_id)
    cur.execute(f"SELECT {_MESSAGE_COLUMNS} {_JOINS} WHERE m.agency_id = %s AND m.sender_phone = %s{extra} "
                "ORDER BY m.received_at DESC, m.id DESC LIMIT %s", (agency_id, sender_phone, *params, limit))
    return [dict(r) for r in cur.fetchall()]


def get_message(cur, agency_id: int, message_id: int, *, agent_user_id: int | None = None, lock: bool = False,
                hidden_numbers=()):
    extra, params = _visibility(agent_user_id, hidden_numbers, None)
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


def candidate_contacts(cur, agency_id: int, phone: str, *, viewer_user_id: int | None = None):
    from . import contact_match

    return contact_match.candidates(cur, agency_id, phone, limit=20, viewer_user_id=viewer_user_id)


def conversation_rows_for_link(cur, agency_id: int, sender_phone: str, hidden_numbers=()):
    """Tutti i messaggi instradati della conversazione (su ogni casella
    visibile), bloccati per l'associazione."""
    extra, params = _inbox_filter(hidden_numbers, None)
    cur.execute("SELECT m.id, m.contact_id, m.property_id, m.message_type, m.text_body, m.provider_message_id, "
                "m.provider_timestamp, COALESCE(to_jsonb(m)->>'direction', 'in') AS direction "
                "FROM whatsapp_inbound_messages m WHERE m.agency_id = %s AND m.sender_phone = %s"
                f"{extra} ORDER BY m.id FOR UPDATE", (agency_id, sender_phone, *params))
    return [dict(r) for r in cur.fetchall()]


# --- BLOCCO-4B: letture per le risposte manuali ---------------------------------

def last_inbound(cur, agency_id: int, sender_phone: str, *, agent_user_id: int | None,
                 phone_number_id: str | None = None, hidden_numbers=()):
    """L'ultimo messaggio ricevuto della conversazione nell'agenzia (visibile a
    chi chiede). Con `phone_number_id`: su QUELLA casella, cosi' la risposta
    parte dal numero che ha ricevuto."""
    extra, params = _visibility(agent_user_id, hidden_numbers, phone_number_id)
    # L'ora del cliente e' quella dichiarata da Meta, MA mai dopo la ricezione:
    # un orario futuro non e' attendibile e non puo' allungare la finestra.
    cur.execute("SELECT m.id, m.phone_number_id, m.contact_id, m.property_id, m.provider_timestamp, m.received_at, "
                "LEAST(COALESCE(m.provider_timestamp, m.received_at), m.received_at) AS customer_at "
                f"{_JOINS} WHERE m.agency_id = %s AND m.sender_phone = %s{extra} AND {CUSTOMER_SQL} "
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
