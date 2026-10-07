"""Database helpers dedicated to the additive CORE module."""

from __future__ import annotations

from contextlib import contextmanager

from psycopg2.extras import RealDictCursor

from database import get_connection


@contextmanager
def core_cursor(*, commit: bool = False):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield conn, cur
        if commit:
            conn.commit()
    except Exception as exc:
        conn.rollback()
        # DELETE-ARCH Fase 2B2: il rifiuto di una guardia della 086 (un nuovo
        # collegamento verso un immobile nel Cestino) arriva qui come errore
        # del database; diventa il 409 PROPERTY_IN_TRASH del dominio.
        from .property_trash import PropertyInTrash, is_trash_db_error
        if not isinstance(exc, PropertyInTrash) and is_trash_db_error(exc):
            raise PropertyInTrash() from exc
        # CESTINO-CONTATTI-1: lo stesso per un contatto nel Cestino (090).
        from .contact_trash import ContactInTrash, is_contact_trash_db_error
        if not isinstance(exc, ContactInTrash) and is_contact_trash_db_error(exc):
            raise ContactInTrash() from exc
        # CESTINO-EDIFICI-1: lo stesso per un edificio nel Cestino (091).
        from .building_trash import BuildingInTrash, is_building_trash_db_error
        if not isinstance(exc, BuildingInTrash) and is_building_trash_db_error(exc):
            raise BuildingInTrash() from exc
        raise
    finally:
        cur.close()
        conn.close()
