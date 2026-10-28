"""BLOCCO-2A - SQL delle valutazioni. Ogni funzione riceve il cursore della
transazione del service e l'`agency_id` gia' ricavato dal contesto: ogni
lettura e scrittura e' filtrata per agenzia."""
from __future__ import annotations

from psycopg2.extras import Json

from .enums import METHOD, STATUS_APPROVED, STATUS_DRAFT, STATUS_SUPERSEDED


def installed(cur) -> bool:
    """La migrazione 116 e' applicata? Chiesto prima di toccare la tabella."""
    cur.execute("SELECT to_regclass('property_valuations') IS NOT NULL AS installed")
    row = cur.fetchone()
    return bool(row and row["installed"])


def get_property(cur, agency_id: int, property_id: int, *, lock: bool = False):
    """L'immobile dell'agenzia. Con `lock` la riga resta bloccata fino al commit:
    e' il primo lock di ogni scrittura sulle valutazioni dell'immobile, quindi
    creazioni e approvazioni concorrenti si mettono in fila nello stesso ordine
    (immobile -> valutazioni). La riga NON viene mai modificata da qui."""
    cur.execute("SELECT * FROM properties WHERE id = %s AND agency_id = %s" + (" FOR UPDATE" if lock else ""),
                (property_id, agency_id))
    return cur.fetchone()


def list_for_property(cur, agency_id: int, property_id: int):
    cur.execute("SELECT * FROM property_valuations WHERE agency_id = %s AND property_id = %s "
                "ORDER BY version DESC", (agency_id, property_id))
    return cur.fetchall()


def get(cur, agency_id: int, valuation_id: int, *, lock: bool = False):
    cur.execute("SELECT * FROM property_valuations WHERE id = %s AND agency_id = %s" + (" FOR UPDATE" if lock else ""),
                (valuation_id, agency_id))
    return cur.fetchone()


def has_draft(cur, agency_id: int, property_id: int) -> bool:
    cur.execute("SELECT 1 FROM property_valuations WHERE agency_id = %s AND property_id = %s AND status = %s",
                (agency_id, property_id, STATUS_DRAFT))
    return cur.fetchone() is not None


def next_version(cur, agency_id: int, property_id: int) -> int:
    cur.execute("SELECT COALESCE(MAX(version), 0) + 1 AS next FROM property_valuations "
                "WHERE agency_id = %s AND property_id = %s", (agency_id, property_id))
    return int(cur.fetchone()["next"])


def insert_draft(cur, *, agency_id: int, property_id: int, version: int, previous_valuation_id: int | None,
                 content: dict, snapshot: dict, amounts: dict, user_id: int):
    cur.execute(
        "INSERT INTO property_valuations (agency_id, property_id, version, previous_valuation_id, status, "
        "method, content, property_snapshot, sale_min, sale_max, sale_recommended, rent_min, rent_max, "
        "rent_recommended, created_by_user_id, updated_by_user_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (agency_id, property_id, version, previous_valuation_id, STATUS_DRAFT, METHOD, Json(content),
         Json(snapshot), amounts["sale_min"], amounts["sale_max"], amounts["sale_recommended"],
         amounts["rent_min"], amounts["rent_max"], amounts["rent_recommended"], user_id, user_id),
    )
    return cur.fetchone()


def update_draft(cur, *, agency_id: int, valuation_id: int, row_version: int, content: dict,
                 amounts: dict, user_id: int):
    """Solo una bozza, solo alla versione attesa: altrimenti nessuna riga."""
    cur.execute(
        "UPDATE property_valuations SET content = %s, sale_min = %s, sale_max = %s, sale_recommended = %s, "
        "rent_min = %s, rent_max = %s, rent_recommended = %s, updated_by_user_id = %s, updated_at = NOW(), "
        "row_version = row_version + 1 "
        "WHERE id = %s AND agency_id = %s AND status = %s AND row_version = %s RETURNING *",
        (Json(content), amounts["sale_min"], amounts["sale_max"], amounts["sale_recommended"],
         amounts["rent_min"], amounts["rent_max"], amounts["rent_recommended"], user_id,
         valuation_id, agency_id, STATUS_DRAFT, row_version),
    )
    return cur.fetchone()


def supersede_approved(cur, *, agency_id: int, property_id: int, by_valuation_id: int):
    """L'approvata corrente (se c'e') diventa superata. Nella stessa transazione
    dell'approvazione della nuova, mai prima."""
    cur.execute(
        "UPDATE property_valuations SET status = %s, superseded_at = NOW(), superseded_by_valuation_id = %s, "
        "row_version = row_version + 1 "
        "WHERE agency_id = %s AND property_id = %s AND status = %s AND id <> %s RETURNING id",
        (STATUS_SUPERSEDED, by_valuation_id, agency_id, property_id, STATUS_APPROVED, by_valuation_id),
    )
    return cur.fetchone()


def approve(cur, *, agency_id: int, valuation_id: int, user_id: int, snapshot: dict):
    cur.execute(
        "UPDATE property_valuations SET status = %s, approved_by_user_id = %s, approved_at = NOW(), "
        "property_snapshot = %s, row_version = row_version + 1 "
        "WHERE id = %s AND agency_id = %s AND status = %s RETURNING *",
        (STATUS_APPROVED, user_id, Json(snapshot), valuation_id, agency_id, STATUS_DRAFT),
    )
    return cur.fetchone()
