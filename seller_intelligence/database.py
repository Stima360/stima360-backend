"""Database helpers dedicated to the additive Seller Intelligence module.

Mirrors core/database.py deliberately: same context-manager shape, same
underlying connection helper, so the two modules behave identically at the
transaction boundary without either importing from the other.
"""

from __future__ import annotations

from contextlib import contextmanager

from psycopg2.extras import RealDictCursor

from database import get_connection


@contextmanager
def si_cursor(*, commit: bool = False):
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
