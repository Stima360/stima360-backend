"""WHATSAPP COEXISTENCE - SQL del registro dei collegamenti e delle prove di
ricezione. Ogni lettura e' filtrata per l'agenzia gia' ricavata dal contesto."""
from __future__ import annotations

from psycopg2.extras import Json

_COLUMNS = ("id, agency_id, requested_by_user_id, waba_id, phone_number_id, business_id, display_phone_number, "
            "verified_name, status, steps, meta_details, error_code, meta_confirmed_at, "
            "contacts_sync_requested_at, history_sync_requested_at, created_at, updated_at")


def installed(cur) -> bool:
    cur.execute("SELECT to_regclass('whatsapp_coexistence_onboardings') IS NOT NULL AS installed")
    return bool(cur.fetchone()["installed"])


def insert(cur, *, agency_id, user_id, waba_id, phone_number_id, business_id) -> dict:
    cur.execute(f"INSERT INTO whatsapp_coexistence_onboardings (agency_id, requested_by_user_id, waba_id, "
                f"phone_number_id, business_id) VALUES (%s, %s, %s, %s, %s) RETURNING {_COLUMNS}",
                (agency_id, user_id, waba_id, phone_number_id, business_id))
    return dict(cur.fetchone())


def update(cur, onboarding_id: int, agency_id: int, **fields) -> dict:
    allowed = {"status", "steps", "meta_details", "error_code", "meta_confirmed_at", "display_phone_number",
               "verified_name", "contacts_sync_requested_at", "history_sync_requested_at"}
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(key)
        sets.append(f"{key} = %s")
        params.append(Json(value) if key in ("steps", "meta_details") else value)
    cur.execute(f"UPDATE whatsapp_coexistence_onboardings SET {', '.join(sets)}, updated_at = NOW() "
                f"WHERE id = %s AND agency_id = %s RETURNING {_COLUMNS}", (*params, onboarding_id, agency_id))
    row = cur.fetchone()
    return dict(row) if row else None


def get(cur, onboarding_id: int, agency_id: int, *, lock: bool = False):
    cur.execute(f"SELECT {_COLUMNS} FROM whatsapp_coexistence_onboardings WHERE id = %s AND agency_id = %s"
                + (" FOR UPDATE" if lock else ""), (onboarding_id, agency_id))
    row = cur.fetchone()
    return dict(row) if row else None


def list_for_agency(cur, agency_id: int, limit: int = 20):
    cur.execute(f"SELECT {_COLUMNS} FROM whatsapp_coexistence_onboardings WHERE agency_id = %s "
                "ORDER BY created_at DESC, id DESC LIMIT %s", (agency_id, limit))
    return [dict(r) for r in cur.fetchall()]


def evidence(cur, agency_id: int, phone_number_id: str) -> dict:
    """Che cosa e' arrivato DA META per quel numero in questa agenzia: prove di
    ricezione, mai dichiarazioni del CRM. Conteggi e date, nessun contenuto."""
    cur.execute(
        "SELECT count(*) FILTER (WHERE COALESCE(to_jsonb(m)->>'origin', 'webhook') = 'webhook') AS live_messages, "
        "count(*) FILTER (WHERE to_jsonb(m)->>'origin' = 'app_echo') AS app_echoes, "
        "count(*) FILTER (WHERE to_jsonb(m)->>'origin' = 'history') AS history_messages, "
        "count(DISTINCT m.sender_phone) AS conversations, max(m.received_at) FILTER "
        "(WHERE COALESCE(to_jsonb(m)->>'origin', 'webhook') <> 'history') AS last_live_at "
        "FROM whatsapp_inbound_messages m WHERE m.agency_id = %s AND m.phone_number_id = %s",
        (agency_id, phone_number_id))
    out = dict(cur.fetchone())
    cur.execute("SELECT count(*) FILTER (WHERE state = 'present') AS present, "
                "count(*) FILTER (WHERE state = 'removed') AS removed, max(last_synced_at) AS last_synced_at "
                "FROM whatsapp_app_contacts WHERE agency_id = %s AND phone_number_id = %s",
                (agency_id, phone_number_id))
    out["app_contacts"] = dict(cur.fetchone())
    cur.execute("SELECT details, received_at FROM whatsapp_webhook_events WHERE agency_id = %s "
                "AND phone_number_id = %s AND event_kind = 'other' "
                "AND details->>'reason' IN ('history_progress', 'history_errors') "
                "ORDER BY received_at DESC, id DESC LIMIT 1", (agency_id, phone_number_id))
    row = cur.fetchone()
    out["history_last_event"] = dict(row) if row else None
    return out
