"""BLOCCO-4C - letture per il suggerimento. Solo SELECT, solo nell'agenzia.

Le colonne dell'immobile sono SOLO quelle di `fields.PROPERTY_COLUMNS`:
nessun `SELECT *`, cosi' un dato riservato non arriva nemmeno in memoria.
"""
from __future__ import annotations

from core.property_trash import live

from .fields import PROPERTY_COLUMNS

_LIVE_CRM = (f"{live('p')} AND p.archived_at IS NULL "
             "AND COALESCE(to_jsonb(p)->>'record_kind', 'crm') = 'crm'")


def communicable_property(cur, agency_id: int, property_id: int) -> dict | None:
    columns = ", ".join(f"p.{c}" for c in PROPERTY_COLUMNS)
    cur.execute(f"SELECT p.id, p.code, p.title, p.updated_at, {columns} FROM properties p "
                f"WHERE p.id = %s AND p.agency_id = %s AND {_LIVE_CRM}", (property_id, agency_id))
    row = cur.fetchone()
    return dict(row) if row else None


def accessories(cur, property_id: int) -> list[dict]:
    cur.execute("SELECT kind, quantity FROM property_accessories WHERE property_id = %s ORDER BY kind, id",
                (property_id,))
    return [dict(r) for r in cur.fetchall()]


def properties_by_codes(cur, agency_id: int, codes: list[str]) -> list[dict]:
    """Gli immobili dell'agenzia il cui codice e' citato nel testo (stessa
    scrittura, a meno di maiuscole e separatori)."""
    if not codes:
        return []
    cur.execute("SELECT p.id, p.code FROM properties p WHERE p.agency_id = %s AND p.code IS NOT NULL "
                f"AND upper(regexp_replace(p.code, '[_ ]', '-', 'g')) = ANY(%s) AND {_LIVE_CRM} ORDER BY p.id",
                (agency_id, codes))
    return [dict(r) for r in cur.fetchall()]
