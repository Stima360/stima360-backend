"""WHATSAPP DUE CASELLE - SQL delle caselle. Sempre nell'agenzia del chiamante."""
from __future__ import annotations

from .enums import ACCESS_RESTRICTED, STATUS_ACTIVE


def installed(cur) -> bool:
    cur.execute("SELECT to_regclass('public.whatsapp_inboxes') IS NOT NULL AS presente")
    return bool(cur.fetchone()["presente"])


_COLUMNS = ("i.id, i.agency_id, i.phone_number_id, i.display_phone_number, i.label, i.kind, i.automations_enabled, "
            "i.access_mode, i.status, i.created_by_user_id, i.created_at, i.updated_at")


def list_for_agency(cur, agency_id: int) -> list[dict]:
    """Le caselle configurate dell'agenzia, con gli operatori autorizzati."""
    cur.execute(f"SELECT {_COLUMNS}, COALESCE((SELECT array_agg(a.user_id ORDER BY a.user_id) "
                "FROM whatsapp_inbox_access a WHERE a.inbox_id = i.id), ARRAY[]::bigint[]) AS access_user_ids "
                "FROM whatsapp_inboxes i WHERE i.agency_id = %s ORDER BY i.id", (agency_id,))
    return [dict(r) for r in cur.fetchall()]


def get(cur, agency_id: int, phone_number_id: str, *, lock: bool = False) -> dict | None:
    cur.execute(f"SELECT {_COLUMNS} FROM whatsapp_inboxes i WHERE i.agency_id = %s AND i.phone_number_id = %s"
                + (" FOR UPDATE" if lock else ""), (agency_id, phone_number_id))
    row = cur.fetchone()
    return dict(row) if row else None


def by_numbers(cur, phone_number_ids: list[str]) -> dict[str, dict]:
    """Le caselle configurate per quei numeri, qualunque agenzia (il chiamante
    ha gia' filtrato i messaggi per agenzia; `phone_number_id` e' unico)."""
    if not phone_number_ids:
        return {}
    cur.execute(f"SELECT {_COLUMNS} FROM whatsapp_inboxes i WHERE i.phone_number_id = ANY(%s)", (phone_number_ids,))
    return {r["phone_number_id"]: dict(r) for r in cur.fetchall()}


def upsert(cur, *, agency_id: int, phone_number_id: str, label: str, kind: str, automations_enabled: bool,
           access_mode: str, status: str, display_phone_number: str | None, user_id: int | None) -> dict:
    cur.execute(
        "INSERT INTO whatsapp_inboxes (agency_id, phone_number_id, display_phone_number, label, kind, "
        "automations_enabled, access_mode, status, created_by_user_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (phone_number_id) DO UPDATE SET display_phone_number = EXCLUDED.display_phone_number, "
        "label = EXCLUDED.label, kind = EXCLUDED.kind, automations_enabled = EXCLUDED.automations_enabled, "
        "access_mode = EXCLUDED.access_mode, status = EXCLUDED.status, updated_at = NOW() "
        "WHERE whatsapp_inboxes.agency_id = EXCLUDED.agency_id "
        f"RETURNING {_COLUMNS.replace('i.', '')}",
        (agency_id, phone_number_id, display_phone_number, label, kind, automations_enabled, access_mode, status,
         user_id))
    row = cur.fetchone()
    return dict(row) if row else None


def set_access(cur, inbox_id: int, user_ids: list[int], granted_by: int | None) -> list[int]:
    """Sostituisce l'elenco degli operatori autorizzati (il titolare vede sempre)."""
    cur.execute("DELETE FROM whatsapp_inbox_access WHERE inbox_id = %s AND NOT (user_id = ANY(%s))",
                (inbox_id, user_ids))
    for user_id in sorted(set(user_ids)):
        cur.execute("INSERT INTO whatsapp_inbox_access (inbox_id, user_id, granted_by_user_id) VALUES (%s, %s, %s) "
                    "ON CONFLICT (inbox_id, user_id) DO NOTHING", (inbox_id, user_id, granted_by))
    cur.execute("SELECT user_id FROM whatsapp_inbox_access WHERE inbox_id = %s ORDER BY user_id", (inbox_id,))
    return [r["user_id"] for r in cur.fetchall()]


def active_members(cur, agency_id: int, user_ids: list[int]) -> set[int]:
    if not user_ids:
        return set()
    cur.execute("SELECT m.operator_user_id AS id FROM agency_memberships m JOIN operator_users u "
                "ON u.id = m.operator_user_id WHERE m.agency_id = %s AND m.status = 'active' AND u.status = 'active' "
                "AND m.operator_user_id = ANY(%s)", (agency_id, user_ids))
    return {r["id"] for r in cur.fetchall()}


def hidden_numbers(cur, agency_id: int, user_id: int | None, *, sees_every_inbox: bool) -> list[str]:
    """I numeri riceventi (phone_number_id) dell'agenzia che QUESTO operatore
    non puo' vedere: caselle riservate senza autorizzazione, e caselle
    disattivate. Il titolare (e il Super Admin) vede ogni casella attiva."""
    if not installed(cur):
        return []
    if sees_every_inbox:
        cur.execute("SELECT phone_number_id FROM whatsapp_inboxes WHERE agency_id = %s AND status <> %s",
                    (agency_id, STATUS_ACTIVE))
    else:
        cur.execute("SELECT i.phone_number_id FROM whatsapp_inboxes i WHERE i.agency_id = %s AND (i.status <> %s "
                    "OR (i.access_mode = %s AND NOT EXISTS (SELECT 1 FROM whatsapp_inbox_access a "
                    "WHERE a.inbox_id = i.id AND a.user_id = %s)))",
                    (agency_id, STATUS_ACTIVE, ACCESS_RESTRICTED, user_id if user_id is not None else -1))
    return [r["phone_number_id"] for r in cur.fetchall()]


def automations_enabled(cur, phone_number_id: str | None) -> bool:
    """Le automazioni (risposte automatiche, prenotazione visita) valgono su
    questa casella? Senza riga (il numero 1 di oggi): si'."""
    if not phone_number_id or not installed(cur):
        return True
    cur.execute("SELECT automations_enabled, status FROM whatsapp_inboxes WHERE phone_number_id = %s",
                (phone_number_id,))
    row = cur.fetchone()
    return True if row is None else bool(row["automations_enabled"]) and row["status"] == STATUS_ACTIVE


def latest_display_numbers(cur, agency_id: int, phone_number_ids: list[str]) -> dict[str, str | None]:
    """Il numero visibile (display_phone_number) come lo dichiara Meta
    nell'ultimo messaggio ricevuto su ciascun numero."""
    if not phone_number_ids:
        return {}
    cur.execute("SELECT DISTINCT ON (phone_number_id) phone_number_id, display_phone_number "
                "FROM whatsapp_inbound_messages WHERE agency_id = %s AND phone_number_id = ANY(%s) "
                "ORDER BY phone_number_id, received_at DESC, id DESC", (agency_id, phone_number_ids))
    return {r["phone_number_id"]: r["display_phone_number"] for r in cur.fetchall()}
