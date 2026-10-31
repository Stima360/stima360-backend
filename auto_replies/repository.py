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


def record_decision(cur, data: dict, *, mode: str = "simulate") -> dict | None:
    """Inserisce la decisione; None se esiste gia' per (messaggio, versione):
    un reinvio o un giro concorrente non la duplica. `mode` e' quello in cui
    e' stata presa ('simulate', o 'send' dalla migrazione 123)."""
    cur.execute(
        "INSERT INTO whatsapp_auto_reply_decisions (agency_id, inbound_message_id, rules_version, mode, decision, "
        "reason_code, proposed_text, data_used, handoff) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (inbound_message_id, rules_version) DO NOTHING RETURNING *",
        (data["agency_id"], data["inbound_message_id"], data["rules_version"], mode, data["decision"],
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


# ---------------------------------------------------------------------------
# modo d'invio (migrazione 123)
# ---------------------------------------------------------------------------

def send_installed(cur) -> bool:
    cur.execute("SELECT to_regclass('public.whatsapp_auto_reply_dispatches') IS NOT NULL AS presente")
    return bool(cur.fetchone()["presente"])


def decision_exists(cur, inbound_message_id: int, rules_version: str) -> bool:
    cur.execute("SELECT 1 FROM whatsapp_auto_reply_decisions WHERE inbound_message_id = %s AND rules_version = %s",
                (inbound_message_id, rules_version))
    return cur.fetchone() is not None


def create_handoff_task(cur, *, agency_id: int, contact_id: int, title: str, description: str,
                        assigned_name: str | None, assigned_user_id: int | None, inbound_message_id: int) -> dict:
    """L'attivita' REALE per l'operatore, una per messaggio ricevuto (il
    chiamante tiene il lock del messaggio). `assigned_to` e' il nome
    dell'agente (lo stesso campo libero delle attivita' create a mano), l'id
    resta in `metadata`. Senza agente attivo l'attivita' nasce non assegnata e
    nessun testo promette un ricontatto (regola del 4C)."""
    cur.execute(
        "INSERT INTO tasks (agency_id, contact_id, title, description, task_type, priority, status, due_at, "
        "assigned_to, created_by, metadata) VALUES (%s, %s, %s, %s, 'whatsapp_auto_reply_handoff', 'high', 'open', "
        "NOW() + interval '4 hours', %s, 'system', jsonb_build_object('source', 'whatsapp_auto_reply', "
        "'inbound_message_id', %s::bigint, 'assigned_user_id', %s::bigint)) RETURNING id, assigned_to",
        (agency_id, contact_id, title, description, assigned_name, inbound_message_id, assigned_user_id))
    return dict(cur.fetchone())


def insert_dispatch(cur, *, agency_id: int, decision_id: int, communication_message_id: int | None,
                    task_id: int | None) -> dict:
    cur.execute("INSERT INTO whatsapp_auto_reply_dispatches (agency_id, decision_id, communication_message_id, "
                "task_id) VALUES (%s, %s, %s, %s) RETURNING *",
                (agency_id, decision_id, communication_message_id, task_id))
    return dict(cur.fetchone())


def dispatches_for_decisions(cur, agency_id: int, decision_ids: list[int]) -> dict[int, dict]:
    if not decision_ids or not send_installed(cur):
        return {}
    cur.execute("SELECT d.decision_id, d.task_id, d.communication_message_id, m.status, m.sent_at, m.suppressed_reason, "
                "m.error_code FROM whatsapp_auto_reply_dispatches d "
                "LEFT JOIN communication_messages m ON m.id = d.communication_message_id "
                "WHERE d.agency_id = %s AND d.decision_id = ANY(%s)", (agency_id, decision_ids))
    return {r["decision_id"]: dict(r) for r in cur.fetchall()}


def resumable_message_ids(cur, agency_id: int, limit: int) -> list[int]:
    """Risposte automatiche accodate e MAI prese in carico (un giro interrotto
    fra l'accodamento e il claim). `sending` e `indeterminate` non ripartono."""
    cur.execute("SELECT id FROM communication_messages WHERE agency_id = %s AND reason_code = 'auto_reply' "
                "AND channel = 'whatsapp' AND status = 'queued' AND attempt_count = 0 ORDER BY id LIMIT %s",
                (agency_id, limit))
    return [r["id"] for r in cur.fetchall()]


def inbound_phone_number_id(cur, agency_id: int, message_id: int) -> str | None:
    """Il numero aziendale (phone_number_id di Meta) che ha ricevuto il messaggio."""
    cur.execute("SELECT phone_number_id FROM whatsapp_inbound_messages WHERE id = %s AND agency_id = %s",
                (message_id, agency_id))
    row = cur.fetchone()
    return row["phone_number_id"] if row else None
