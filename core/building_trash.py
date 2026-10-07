"""CESTINO-EDIFICI-1 - l'edificio (palazzina contenitore) nel Cestino, visto
dal resto del CRM. Stesso impianto di `core/property_trash.py` e
`core/contact_trash.py`: un edificio e' nel Cestino quando
`buildings.deleted_at` e' valorizzato (migration 091).

  * `live(alias)`: il predicato SQL "edificio fuori dal Cestino", letto via
    `to_jsonb`, cosi' il codice resta valido su un database senza la 091;
  * `is_building_trash_db_error(exc)`: il rifiuto di una guardia della 091
    (`BUILDING_IN_TRASH: ...`), che `core.database.core_cursor` traduce nel
    409 BUILDING_IN_TRASH.
"""
from __future__ import annotations

from .exceptions import BuildingInTrash  # noqa: F401  (la classe vive in core.exceptions)

BUILDING_IN_TRASH = BuildingInTrash.code


def live(alias: str = "b") -> str:
    """Predicato SQL: l'edificio `alias` NON e' nel Cestino."""
    return f"(to_jsonb({alias})->>'deleted_at') IS NULL"


def is_building_trash_db_error(exc: BaseException) -> bool:
    import psycopg2
    if not isinstance(exc, psycopg2.Error):
        return False
    testo = getattr(exc, "pgerror", None) or str(exc)
    return BUILDING_IN_TRASH in (testo or "")
