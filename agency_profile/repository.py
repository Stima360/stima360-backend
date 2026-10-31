"""BLOCCO-1B - SQL dei dati agenzia e del logo. Ogni funzione riceve il cursore
della transazione del service e l'`agency_id` gia' ricavato dal contesto."""
from __future__ import annotations

from psycopg2.extras import Json


def get_agency(cur, agency_id: int, *, lock: bool = False):
    """L'agenzia (id, nome, settings), bloccata fino al commit se `lock`: la
    stessa riga che la Platform aggiorna per timezone e locale."""
    cur.execute(
        "SELECT id, name, settings FROM agencies WHERE id = %s" + (" FOR UPDATE" if lock else ""),
        (agency_id,),
    )
    return cur.fetchone()


def write_settings(cur, agency_id: int, settings: dict) -> None:
    cur.execute(
        "UPDATE agencies SET settings = %s, updated_at = NOW() WHERE id = %s",
        (Json(settings), agency_id),
    )


def logos_installed(cur) -> bool:
    """La migrazione 115 e' applicata? Chiesto PRIMA di toccare `agency_logos`:
    una query su una tabella assente annullerebbe l'intera transazione,
    compreso un salvataggio dei dati agenzia gia' eseguito."""
    cur.execute("SELECT to_regclass('agency_logos') IS NOT NULL AS installed")
    row = cur.fetchone()
    return bool(row and row["installed"])


_LOGO_METADATA = ("content_type, width, height, octet_length(image_bytes) AS size_bytes, sha256, "
                  "uploaded_at")


def get_logo_metadata(cur, agency_id: int):
    cur.execute(f"SELECT {_LOGO_METADATA} FROM agency_logos WHERE agency_id = %s", (agency_id,))
    return cur.fetchone()


def get_logo(cur, agency_id: int):
    cur.execute(f"SELECT {_LOGO_METADATA}, image_bytes FROM agency_logos WHERE agency_id = %s",
                (agency_id,))
    return cur.fetchone()


def upsert_logo(cur, agency_id: int, *, content_type: str, width: int, height: int,
                data: bytes, sha256: str, uploaded_by: str | None):
    """Una sola statement: il logo precedente resta finche' questa non riesce."""
    cur.execute(
        "INSERT INTO agency_logos (agency_id, content_type, width, height, image_bytes, sha256, uploaded_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (agency_id) DO UPDATE SET content_type = EXCLUDED.content_type, "
        "width = EXCLUDED.width, height = EXCLUDED.height, image_bytes = EXCLUDED.image_bytes, "
        "sha256 = EXCLUDED.sha256, uploaded_by = EXCLUDED.uploaded_by, uploaded_at = NOW() "
        f"RETURNING {_LOGO_METADATA}",
        (agency_id, content_type, width, height, data, sha256, uploaded_by),
    )
    return cur.fetchone()


def delete_logo(cur, agency_id: int) -> bool:
    cur.execute("DELETE FROM agency_logos WHERE agency_id = %s", (agency_id,))
    return bool(cur.rowcount)
