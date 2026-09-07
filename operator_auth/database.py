"""Database helpers dedicated to operator authentication.

Mirrors core/database.py and the module-local cursor convention already used by
followup/, seller_intent/, property_watch/ and next_best_action/. Keeping a
local contextmanager rather than importing core's means this package owns its
own transaction boundary and adds no cross-module dependency.

This is the only place in the package that opens a connection. Repository
functions take an already-open cursor and never open one themselves, so the
service layer decides where a transaction begins and ends.
"""
from __future__ import annotations

from contextlib import contextmanager

from psycopg2.extras import RealDictCursor

from database import get_connection


@contextmanager
def operator_cursor(*, commit: bool = False):
    """Yield ``(connection, cursor)`` for one operator-auth transaction.

    Commits only when asked, rolls back on any exception, and always closes.
    """
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
