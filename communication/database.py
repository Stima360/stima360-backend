"""Database helpers dedicated to the additive P29-2 COMMUNICATION module.

Rispecchia deliberatamente consent/database.py, core/database.py e
followup/database.py: stessa forma di context manager, stesso helper di
connessione (`database.get_connection`), cosi' che tutti si comportino
identicamente al confine di transazione senza importarsi a vicenda.

Questo file e' l'UNICO punto del modulo che apre una connessione, ed e' il
motivo per cui il modulo resta dentro il choke point censito in
docs/P26_DB_ENTRYPOINTS.md.
"""

from __future__ import annotations

from contextlib import contextmanager

from psycopg2.extras import RealDictCursor

from database import get_connection


@contextmanager
def communication_cursor(*, commit: bool = False):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield conn, cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
