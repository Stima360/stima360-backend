"""Database helpers dedicated to the additive P29 CONSENT module.

Rispecchia deliberatamente core/database.py, followup/database.py e
seller_intelligence/database.py: stessa forma di context manager, stesso
helper di connessione (database.get_connection), cosi' che tutti si comportino
identicamente al confine di transazione senza importarsi a vicenda.

Il cursore che questo yield-a e' un normale RealDictCursor di psycopg2. E'
anche quello su cui viaggiano INSERT dell'evento e UPDATE della proiezione:
una sola connessione, una sola transazione, un solo commit. E' l'intero
requisito 1 di P29-1.2, e vive qui perche' e' una proprieta' del confine di
transazione, non della logica.
"""

from __future__ import annotations

from contextlib import contextmanager

from psycopg2.extras import RealDictCursor

from database import get_connection


@contextmanager
def consent_cursor(*, commit: bool = False):
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
