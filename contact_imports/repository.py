"""IMPORTAZIONE RUBRICA - SQL. Sempre nell'agenzia e per l'operatore del chiamante."""
from __future__ import annotations

from psycopg2.extras import Json

from core.contact_trash import live
from core.contact_privacy import contact_privacy_predicate
from inbound_messages.contact_match import _NORMALIZED


def installed(cur) -> bool:
    cur.execute("SELECT to_regclass('public.contact_imports') IS NOT NULL AND to_regclass('public.contact_phones') "
                "IS NOT NULL AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'contacts' "
                "AND column_name = 'private_user_id') AS presente")
    return bool(cur.fetchone()["presente"])


def visible_contacts(cur, ctx) -> tuple[list[dict], dict[str, set[int]]]:
    """I contatti vivi che l'importatore puo' vedere (agenzia + riservati
    propri) e l'indice numero normalizzato -> contatti, su TUTTI i campi
    telefonici (phone, secondary_phone, contact_phones)."""
    privacy, params = contact_privacy_predicate(ctx, "c")
    agency_id = ctx.require_agency()
    cur.execute(
        "SELECT c.id, c.display_name, c.first_name, c.last_name, c.company_name, c.contact_type, c.email, c.phone, "
        "c.secondary_phone, c.status, c.created_at, c.private_user_id, "
        "coalesce(c.phone_normalized, " + _NORMALIZED.format(col="c.phone") + ") AS n1, "
        + _NORMALIZED.format(col="c.secondary_phone") + " AS n2 "
        f"FROM contacts c WHERE c.agency_id = %s AND {live('c')} AND {privacy} ORDER BY c.created_at, c.id",
        [agency_id, *params])
    rows = [dict(r) for r in cur.fetchall()]
    index: dict[str, set[int]] = {}
    for r in rows:
        for n in (r["n1"], r["n2"]):
            if n:
                index.setdefault(n, set()).add(r["id"])
    ids = [r["id"] for r in rows]
    if ids:
        cur.execute("SELECT contact_id, phone_normalized FROM contact_phones WHERE agency_id = %s "
                    "AND contact_id = ANY(%s)", (agency_id, ids))
        for r in cur.fetchall():
            index.setdefault(r["phone_normalized"], set()).add(r["contact_id"])
    return rows, index


def contact_numbers(cur, contact_id: int) -> set[str]:
    cur.execute("SELECT coalesce(c.phone_normalized, " + _NORMALIZED.format(col="c.phone") + ") AS n1, "
                + _NORMALIZED.format(col="c.secondary_phone") + " AS n2, c.email, c.secondary_phone "
                "FROM contacts c WHERE c.id = %s", (contact_id,))
    row = cur.fetchone()
    numbers = {n for n in (row["n1"], row["n2"]) if n} if row else set()
    cur.execute("SELECT phone_normalized FROM contact_phones WHERE contact_id = %s", (contact_id,))
    numbers |= {r["phone_normalized"] for r in cur.fetchall()}
    return numbers


def insert_preview(cur, *, agency_id: int, user_id: int, source: str, file_name: str | None, entries: list,
                   summary: dict) -> dict:
    cur.execute("INSERT INTO contact_imports (agency_id, user_id, source, file_name, status, entries, summary) "
                "VALUES (%s, %s, %s, %s, 'preview', %s, %s) RETURNING id, status, created_at",
                (agency_id, user_id, source, file_name, Json(entries), Json(summary)))
    return dict(cur.fetchone())


def get_import(cur, agency_id: int, user_id: int, import_id: int, *, lock: bool = False) -> dict | None:
    cur.execute("SELECT id, agency_id, user_id, source, file_name, status, entries, summary, created_at, committed_at "
                "FROM contact_imports WHERE id = %s AND agency_id = %s AND user_id = %s" + (" FOR UPDATE" if lock else ""),
                (import_id, agency_id, user_id))
    row = cur.fetchone()
    return dict(row) if row else None


def save_progress(cur, import_id: int, summary: dict) -> None:
    """L'avanzamento di «Importa tutti», nella stessa transazione del blocco."""
    cur.execute("UPDATE contact_imports SET summary = %s WHERE id = %s AND status = 'preview'", (Json(summary), import_id))


def finish_import(cur, import_id: int, summary: dict) -> None:
    """Confermata: resta il riepilogo, le voci (dati personali) si tolgono."""
    cur.execute("UPDATE contact_imports SET status = 'done', committed_at = NOW(), entries = NULL, summary = %s "
                "WHERE id = %s", (Json(summary), import_id))


def insert_contact(cur, *, agency_id: int, user_id: int, assigned_agent_id: int | None, private_user_id: int | None,
                   contact_type: str, first_name, last_name, company_name, display_name, email, email_normalized,
                   phone, phone_normalized, secondary_phone, source: str) -> int:
    cur.execute(
        "INSERT INTO contacts (contact_type, first_name, last_name, company_name, display_name, email, email_normalized, "
        "phone, phone_normalized, secondary_phone, source, status, "
        "agency_id, created_by_user_id, assigned_agent_id, private_user_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "%s, %s, 'active', %s, %s, %s, %s) RETURNING id",
        (contact_type, first_name, last_name, company_name, display_name, email, email_normalized, phone,
         phone_normalized, secondary_phone, source, agency_id, user_id, assigned_agent_id, private_user_id))
    return cur.fetchone()["id"]


def add_phone(cur, *, agency_id: int, contact_id: int, phone: str, phone_normalized: str, label: str | None,
              position: int) -> bool:
    cur.execute("INSERT INTO contact_phones (agency_id, contact_id, phone, phone_normalized, label, position, source) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'import') ON CONFLICT (contact_id, phone_normalized) DO NOTHING "
                "RETURNING id", (agency_id, contact_id, phone, phone_normalized, label, position))
    return cur.fetchone() is not None


def fill_empty_fields(cur, contact_id: int, *, email: str | None, email_normalized: str | None,
                      secondary_phone: str | None) -> None:
    """Completa SOLO i campi vuoti: nessun dato esistente viene sovrascritto."""
    cur.execute("UPDATE contacts SET email = COALESCE(NULLIF(email, ''), %s), "
                "email_normalized = COALESCE(email_normalized, %s), "
                "secondary_phone = COALESCE(NULLIF(secondary_phone, ''), %s), updated_at = NOW() WHERE id = %s",
                (email, email_normalized, secondary_phone, contact_id))


def share_contact(cur, agency_id: int, user_id: int, contact_id: int) -> bool:
    cur.execute("UPDATE contacts SET private_user_id = NULL, updated_at = NOW() WHERE id = %s AND agency_id = %s "
                "AND private_user_id = %s RETURNING id", (contact_id, agency_id, user_id))
    return cur.fetchone() is not None
