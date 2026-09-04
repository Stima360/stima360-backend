"""Database helpers dedicated to the additive P24 Database Revival module.

Mirrors core/database.py, followup/database.py, seller_intent/database.py
and next_best_action/database.py deliberately: same context-manager shape,
same underlying connection helper (database.get_connection), so this
module behaves identically at the transaction boundary without importing
from any of them for this. Its own local transaction is never shared with
any other module's cursor.
"""

from __future__ import annotations

from contextlib import contextmanager

from psycopg2.extras import RealDictCursor

from database import get_connection


@contextmanager
def database_revival_cursor(*, commit: bool = False):
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
