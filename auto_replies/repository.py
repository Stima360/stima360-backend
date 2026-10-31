"""BLOCCO-4D - persistenza del motore (migrazione 119). Sempre nell'agenzia.

Solo le tre tabelle della 119; i messaggi ricevuti si leggono con
`inbound_messages.repository`. Nessuna scrittura altrove.
"""
from __future__ import annotations

from psycopg2.extras import Json


def installed(cur) -> bool:
    cur.execute("SELECT to_regclass('public.whatsapp_auto_reply_decisions') IS NOT NULL AS presente")
    return bool(cur.fetchone()["presente"])


def settings(cur, agency_id: int) -> dict | None:
    cur.execute("SELECT agency_id, mode, enabled_at, updated_by_user_id, updated_at "
                "FROM whatsapp_auto_reply_settings WHERE agency_id = %s", (agency_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def set_mode(cur, agency_id: int, mode: str, user_id: int | None) -> dict:
    """Il modo dell'agenzia. `enabled_at` riparte a ogni riattivazione: i
    messaggi arrivati mentre era spento non si elaborano a posteriori."""
    cur.execute(
        "INSERT INTO whatsapp_auto_reply_settings (agency_id, mode, enabled_at, updated_by_user_id, updated_at) "
        "VALUES (%s, %s, CASE WHEN %s = 'off' THEN NULL ELSE NOW() END, %s, NOW()) "
        "ON CONFLICT (agency_id) DO UPDATE SET mode = EXCLUDED.mode, "
        "  enabled_at = CASE WHEN EXCLUDED.mode = 'off' THEN NULL "
        "                    WHEN whatsapp_auto_reply_settings.mode = EXCLUDED.mode "
        "                    THEN whatsapp_auto_reply_settings.enabled_at ELSE NOW() END, "
        "  updated_by_user_id = EXCLUDED.updated_by_user_id, updated_at = NOW() "
        "RETURNING agency_id, mode, enabled_at, updated_by_user_id, updated_at",
        (agency_id, mode, mode, user_id))
    return dict(cur.fetchone())


def open_pause(cur, agency_id: int, sender_phone: str) -> dict | None:
    cur.execute("SELECT id, paused_by_user_id, paused_at, reason FROM whatsapp_auto_reply_pauses "
                "WHERE agency_id = %s AND sender_phone = %s AND resumed_at IS NULL", (agency_id, sender_phone))
    row = cur.fetchone()
    return dict(row) if row else None


def pause(cur, agency_id: int, sender_phone: str, user_id: int) -> bool:
    """Apre la pausa; False se era gia' in pausa (indice unico parziale)."""
    cur.execute("INSERT INTO whatsapp_auto_reply_pauses (agency_id, sender_phone, paused_by_user_id) "
                "VALUES (%s, %s, %s) ON CONFLICT (agency_id, sender_phone) WHERE resumed_at IS NULL DO NOTHING "
                "RETURNING id", (agency_id, sender_phone, user_id))
    return cur.fetchone() is not None


def resume(cur, agency_id: int, sender_phone: str, user_id: int) -> bool:
    cur.execute("UPDATE whatsapp_auto_reply_pauses SET resumed_at = NOW(), resumed_by_user_id = %s "
                "WHERE agency_id = %s AND sender_phone = %s AND resumed_at IS NULL RETURNING id",
                (user_id, agency_id, sender_phone))
    return cur.fetchone() is not None


def pending_message_ids(cur, agency_id: int, since, rules_version: str, limit: int) -> list[int]:
    """I messaggi instradati all'agenzia, ricevuti dall'attivazione, ancora
    senza decisione per questa versione delle regole. Dal piu' vecchio."""
    cur.execute("SELECT m.id FROM whatsapp_inbound_messages m WHERE m.agency_id = %s AND m.routing_status = 'routed' "
                "AND m.received_at >= %s AND NOT EXISTS (SELECT 1 FROM whatsapp_auto_reply_decisions d "
                "  WHERE d.inbound_message_id = m.id AND d.rules_version = %s) "
                "ORDER BY m.received_at, m.id LIMIT %s", (agency_id, since, rules_version, limit))
    return [r["id"] for r in cur.fetchall()]


def record_decision(cur, data: dict) -> dict | None:
    """Inserisce la decisione; None se esiste gia' per (messaggio, versione):
    un reinvio o un giro concorrente non la duplica."""
    cur.execute(
        "INSERT INTO whatsapp_auto_reply_decisions (agency_id, inbound_message_id, rules_version, mode, decision, "
        "reason_code, proposed_text, data_used, handoff) VALUES (%s, %s, %s, 'simulate', %s, %s, %s, %s, %s) "
        "ON CONFLICT (inbound_message_id, rules_version) DO NOTHING RETURNING *",
        (data["agency_id"], data["inbound_message_id"], data["rules_version"], data["decision"],
         data.get("reason_code"), data.get("proposed_text"), Json(data.get("data_used") or {}),
         Json(data["handoff"]) if data.get("handoff") is not None else None))
    row = cur.fetchone()
    return dict(row) if row else None


def decisions_for_messages(cur, agency_id: int, message_ids: list[int]) -> list[dict]:
    if not message_ids:
        return []
    cur.execute("SELECT * FROM whatsapp_auto_reply_decisions WHERE agency_id = %s AND inbound_message_id = ANY(%s) "
                "ORDER BY created_at, id", (agency_id, message_ids))
    return [dict(r) for r in cur.fetchall()]


def stop_in_conversation(cur, agency_id: int, sender_phone: str) -> bool:
    """Uno STOP del mittente in questa agenzia (testo «STOP», come in 4A)."""
    cur.execute("SELECT 1 FROM whatsapp_inbound_messages WHERE agency_id = %s AND sender_phone = %s "
                "AND message_type = 'text' AND upper(regexp_replace(coalesce(text_body, ''), '[^A-Za-z0-9]', '', 'g')) "
                "= 'STOP' LIMIT 1", (agency_id, sender_phone))
    return cur.fetchone() is not None


def contact_row(cur, agency_id: int, contact_id: int) -> dict | None:
    cur.execute("SELECT id, assigned_agent_id, phone_normalized FROM contacts WHERE id = %s AND agency_id = %s",
                (contact_id, agency_id))
    row = cur.fetchone()
    return dict(row) if row else None
