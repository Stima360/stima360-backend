"""DELETE-ARCH Fase 2B2 - l'immobile nel Cestino, visto dal resto del CRM.

Un immobile e' nel Cestino quando `properties.deleted_at` e' valorizzato
(migration 085). Da 2B2 sparisce da OGNI superficie operativa e non riceve
nuovi collegamenti. Questo modulo e' il punto unico per entrambe le cose:

  * `live(alias)`: il predicato SQL "immobile fuori dal Cestino" da mettere
    in ogni lettura operativa di `properties`. Letto via `to_jsonb`, cosi'
    il codice resta valido su un database senza la 085 (stesso idioma di
    2B1); quando la 085 sara' ovunque si potra' passare a
    `{alias}.deleted_at IS NULL` cambiando solo questa funzione.
    La sentinella `tests/test_delete_arch_2b2.py` rifiuta ogni nuova lettura
    di `properties` che non lo usi (o non sia dichiarata in un elenco breve
    e motivato).
  * `PropertyInTrash`: il rifiuto applicativo (409 `PROPERTY_IN_TRASH`) per
    chi prova a collegare qualcosa a un immobile nel Cestino. Le guardie del
    database (migration 086) sollevano lo stesso codice nel messaggio;
    `core.database.core_cursor` lo traduce in questa eccezione.
"""
from __future__ import annotations

from .exceptions import PropertyInTrash  # noqa: F401  (la classe vive in core.exceptions)

PROPERTY_IN_TRASH = PropertyInTrash.code


def live(alias: str = "p") -> str:
    """Predicato SQL: l'immobile `alias` NON e' nel Cestino."""
    return f"(to_jsonb({alias})->>'deleted_at') IS NULL"


def deleted_at_sql(alias: str = "p") -> str:
    """Espressione SQL che legge `deleted_at` anche senza la 085 (NULL)."""
    return f"(to_jsonb({alias})->>'deleted_at')"


def trash_409(exc: PropertyInTrash):
    """La risposta HTTP del rifiuto, nella forma standard del CRM
    `{"detail", "code"}` (Agenda, Acquisizioni, CRM, scheda Immobile). Per i
    router che traducono `ConflictError` con il solo `detail`: SOLO questo
    errore nuovo della 2B2 cambia forma, gli altri restano come sono."""
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=409, content={**(getattr(exc, "extra", None) or {}),
                                                  "detail": str(exc), "code": exc.code})


def is_trash_db_error(exc: BaseException) -> bool:
    """Il rifiuto di una guardia della 086 (errore del database con il codice
    nel messaggio)."""
    import psycopg2
    if not isinstance(exc, psycopg2.Error):
        return False
    testo = getattr(exc, "pgerror", None) or str(exc)
    return PROPERTY_IN_TRASH in (testo or "")


def refuse_if_in_trash(cur, property_id, *, lock: bool = False) -> None:
    """Rifiuto applicativo prima di creare un collegamento: legge la sola
    riga dell'immobile (gia' nello scope di chi chiama).

    `lock=True` (FOR SHARE) per chi scrive su una tabella SENZA guardia nel
    database: tiene ferma la riga fino al commit, cosi' uno spostamento nel
    Cestino concorrente attende e la verifica non diventa vecchia prima
    dell'INSERT."""
    if property_id is None:
        return
    cur.execute(f"SELECT {deleted_at_sql('properties')} AS deleted_at FROM properties WHERE id = %s"
                + (" FOR SHARE" if lock else ""), (property_id,))
    riga = cur.fetchone()
    if riga is None:
        return
    valore = riga.get("deleted_at") if hasattr(riga, "get") else riga[0]
    if valore is not None:
        raise PropertyInTrash()
