"""CESTINO-CONTATTI-1 - il contatto nel Cestino, visto dal resto del CRM.

Stesso impianto di `core/property_trash.py` (immobili, DELETE-ARCH 2B2). Un
contatto e' nel Cestino quando `contacts.deleted_at` e' valorizzato
(migration 090). Da li' sparisce dalle superfici operative (elenco Contatti,
ricerche, selettori, worklist) e non riceve nuovi collegamenti; lo storico
resta consultabile secondo i permessi.

  * `live(alias)`: il predicato SQL "contatto fuori dal Cestino", letto via
    `to_jsonb`, cosi' il codice resta valido su un database senza la 090;
  * `refuse_if_in_trash(cur, contact_id)`: il rifiuto applicativo (409
    CONTACT_IN_TRASH) prima di creare un collegamento o modificare il
    contatto. Le guardie della 090 sollevano lo stesso codice nel messaggio;
    `core.database.core_cursor` lo traduce in `ContactInTrash`.
"""
from __future__ import annotations

from .exceptions import ContactInTrash  # noqa: F401  (la classe vive in core.exceptions)

CONTACT_IN_TRASH = ContactInTrash.code


def live(alias: str = "c") -> str:
    """Predicato SQL: il contatto `alias` NON e' nel Cestino."""
    return f"(to_jsonb({alias})->>'deleted_at') IS NULL"


def deleted_at_sql(alias: str = "c") -> str:
    """Espressione SQL che legge `deleted_at` anche senza la 090 (NULL)."""
    return f"(to_jsonb({alias})->>'deleted_at')"


def live_contact_id(colonna: str) -> str:
    """Predicato SQL: la colonna `colonna` (un contact_id) e' NULL o punta a un
    contatto fuori dal Cestino. Per le liste che mostrano righe con un
    contatto (richieste d'acquisto, worklist, «Oggi»)."""
    return (f"({colonna} IS NULL OR NOT EXISTS (SELECT 1 FROM contacts ct_trash WHERE ct_trash.id = {colonna} "
            f"AND {deleted_at_sql('ct_trash')} IS NOT NULL))")


def is_contact_trash_db_error(exc: BaseException) -> bool:
    import psycopg2
    if not isinstance(exc, psycopg2.Error):
        return False
    testo = getattr(exc, "pgerror", None) or str(exc)
    return CONTACT_IN_TRASH in (testo or "")


def in_trash(cur, contact_id) -> bool:
    if contact_id is None:
        return False
    cur.execute(f"SELECT {deleted_at_sql('contacts')} AS deleted_at FROM contacts WHERE id = %s", (contact_id,))
    riga = cur.fetchone()
    if riga is None:
        return False
    valore = riga.get("deleted_at") if hasattr(riga, "get") else riga[0]
    return valore is not None


def refuse_if_in_trash(cur, contact_id, *, lock: bool = False) -> None:
    """Rifiuto applicativo prima di un collegamento nuovo o di una modifica.
    `lock=True` (FOR KEY SHARE, come le guardie della 090) tiene ferma la
    riga fino al commit: uno spostamento nel Cestino concorrente attende, e un
    collegamento che arriva durante lo spostamento attende e poi lo vede."""
    if contact_id is None:
        return
    cur.execute(f"SELECT {deleted_at_sql('contacts')} AS deleted_at FROM contacts WHERE id = %s"
                + (" FOR KEY SHARE" if lock else ""), (contact_id,))
    riga = cur.fetchone()
    if riga is None:
        return
    valore = riga.get("deleted_at") if hasattr(riga, "get") else riga[0]
    if valore is not None:
        raise ContactInTrash()
