"""BLOCCO-6A - persistenza dei fogli visita (migrazione 121)."""
from __future__ import annotations

from psycopg2.extras import Json

SHEET_COLUMNS = ("id, agency_id, appointment_id, property_id, sheet_version, template_version, visit_state, "
                 "data_snapshot, sha256, generated_by_user_id, generated_at")


def installed(cur) -> bool:
    cur.execute("SELECT to_regclass('public.visit_sheets') IS NOT NULL AS presente")
    return bool(cur.fetchone()["presente"])


def visit(cur, agency_id: int, appointment_id: int, *, lock: bool = False) -> dict | None:
    cur.execute("SELECT a.id, a.agency_id, a.appointment_type, a.status, a.start_at, a.end_at, a.completed_at, "
                "a.assigned_user_id, a.contact_id, a.property_id FROM appointments a "
                "WHERE a.id = %s AND a.agency_id = %s" + (" FOR UPDATE" if lock else ""), (appointment_id, agency_id))
    row = cur.fetchone()
    return dict(row) if row else None


def visits_for_property(cur, agency_id: int, property_id: int, agent_user_id: int | None) -> list[dict]:
    extra, params = ("", []) if agent_user_id is None else (" AND a.assigned_user_id = %s", [agent_user_id])
    cur.execute("SELECT a.id, a.status, a.start_at, a.end_at, a.assigned_user_id FROM appointments a "
                "WHERE a.agency_id = %s AND a.property_id = %s AND a.appointment_type = 'buyer_visit'"
                + extra + " ORDER BY a.start_at DESC, a.id DESC LIMIT 100", [agency_id, property_id, *params])
    return [dict(r) for r in cur.fetchall()]


def property_row(cur, agency_id: int, property_id: int) -> dict | None:
    cur.execute("SELECT p.*, (to_jsonb(p)->>'deleted_at') AS trash_marker FROM properties p "
                "WHERE p.id = %s AND p.agency_id = %s", (property_id, agency_id))
    row = cur.fetchone()
    return dict(row) if row else None


def contact_name(cur, agency_id: int, contact_id: int | None) -> str | None:
    if contact_id is None:
        return None
    cur.execute("SELECT COALESCE(NULLIF(btrim(display_name), ''), btrim(concat_ws(' ', first_name, last_name))) "
                "AS name FROM contacts WHERE id = %s AND agency_id = %s", (contact_id, agency_id))
    row = cur.fetchone()
    return row["name"] if row else None


def latest(cur, appointment_id: int) -> dict | None:
    cur.execute(f"SELECT {SHEET_COLUMNS} FROM visit_sheets WHERE appointment_id = %s "
                "ORDER BY sheet_version DESC LIMIT 1", (appointment_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def sheets_for_appointments(cur, agency_id: int, appointment_ids: list[int]) -> list[dict]:
    if not appointment_ids:
        return []
    cur.execute(f"SELECT {SHEET_COLUMNS} FROM visit_sheets WHERE agency_id = %s AND appointment_id = ANY(%s) "
                "ORDER BY appointment_id, sheet_version DESC", (agency_id, appointment_ids))
    return [dict(r) for r in cur.fetchall()]


def insert(cur, data: dict) -> dict | None:
    cur.execute("INSERT INTO visit_sheets (agency_id, appointment_id, property_id, sheet_version, template_version, "
                "visit_state, data_snapshot, pdf_bytes, sha256, generated_by_user_id) VALUES "
                "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (appointment_id, sheet_version) DO NOTHING "
                f"RETURNING {SHEET_COLUMNS}",
                (data["agency_id"], data["appointment_id"], data["property_id"], data["sheet_version"],
                 data["template_version"], data["visit_state"], Json(data["data_snapshot"]), data["pdf_bytes"],
                 data["sha256"], data["generated_by_user_id"]))
    row = cur.fetchone()
    return dict(row) if row else None


def pdf(cur, agency_id: int, sheet_id: int) -> dict | None:
    cur.execute("SELECT id, appointment_id, property_id, sheet_version, sha256, pdf_bytes FROM visit_sheets "
                "WHERE id = %s AND agency_id = %s", (sheet_id, agency_id))
    row = cur.fetchone()
    return dict(row) if row else None
