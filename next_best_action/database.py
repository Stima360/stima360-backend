"""Database helpers dedicated to the additive P23 Next Best Action module.

Mirrors core/database.py, followup/database.py and seller_intent/database.py
deliberately: same context-manager shape, same underlying connection helper
(database.get_connection), so this module behaves identically at the
transaction boundary without importing from any of them for this. Its own
local transaction is never shared with any P17-P22 module's cursor.
"""

from __future__ import annotations

from contextlib import contextmanager

from psycopg2.extras import RealDictCursor

from database import get_connection


@contextmanager
def next_best_action_cursor(*, commit: bool = False):
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
