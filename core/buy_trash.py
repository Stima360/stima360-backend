"""CESTINO-RICHIESTE-1 - la richiesta acquirente nel Cestino, vista dal resto
del CRM. Stesso impianto di `core/property_trash.py`, `core/contact_trash.py`
e `core/building_trash.py`: una richiesta e' nel Cestino quando
`buy_requests.deleted_at` e' valorizzato (migration 092). Da li' esce dalle
superfici operative (Acquirenti, ricerca, selettori, abbinamenti, FLOW, NBA,
pressione acquirenti, vendite invisibili) e non riceve operazioni nuove; la
scheda e lo storico restano consultabili.

  * `live(alias)`: il predicato SQL "richiesta fuori dal Cestino", letto via
    `to_jsonb`, cosi' il codice resta valido su un database senza la 092;
  * `live_buy_id(colonna)`: lo stesso per una colonna `buy_request_id`
    (abbinamenti, candidati, NBA...);
  * `is_buy_trash_db_error(exc)`: il rifiuto di una guardia della 092
    (`BUY_REQUEST_IN_TRASH: ...`), che `core.database.core_cursor` traduce nel
    409 BUY_REQUEST_IN_TRASH;
  * `refuse_if_request_in_trash(cur, buy_request_id, lock=...)`: il rifiuto
    applicativo prima di un'operazione nuova.
"""
from __future__ import annotations

from .exceptions import BuyRequestInTrash  # noqa: F401  (la classe vive in core.exceptions)

BUY_REQUEST_IN_TRASH = BuyRequestInTrash.code


def live(alias: str = "b") -> str:
    """Predicato SQL: la richiesta `alias` NON e' nel Cestino."""
    return f"(to_jsonb({alias})->>'deleted_at') IS NULL"


def deleted_at_sql(alias: str = "b") -> str:
    """Espressione SQL che legge `deleted_at` anche senza la 092 (NULL)."""
    return f"(to_jsonb({alias})->>'deleted_at')"


def live_buy_id(colonna: str) -> str:
    """Predicato SQL: la colonna `colonna` (un buy_request_id) e' NULL o punta a
    una richiesta fuori dal Cestino."""
    return (f"({colonna} IS NULL OR NOT EXISTS (SELECT 1 FROM buy_requests br_trash WHERE br_trash.id = {colonna} "
            f"AND {deleted_at_sql('br_trash')} IS NOT NULL))")


def is_buy_trash_db_error(exc: BaseException) -> bool:
    import psycopg2
    if not isinstance(exc, psycopg2.Error):
        return False
    testo = getattr(exc, "pgerror", None) or str(exc)
    return BUY_REQUEST_IN_TRASH in (testo or "")


def refuse_if_request_in_trash(cur, buy_request_id, *, lock: bool = False) -> None:
    """Rifiuto applicativo prima di un'operazione o di un collegamento nuovo.
    `lock=True` (FOR SHARE, come le guardie della 092) tiene ferma la riga
    fino al commit: uno spostamento nel Cestino concorrente attende."""
    if buy_request_id is None:
        return
    cur.execute(f"SELECT {deleted_at_sql('buy_requests')} AS deleted_at FROM buy_requests WHERE id = %s"
                + (" FOR SHARE" if lock else ""), (buy_request_id,))
    riga = cur.fetchone()
    if riga is None:
        return
    valore = riga.get("deleted_at") if hasattr(riga, "get") else riga[0]
    if valore is not None:
        raise BuyRequestInTrash()
