"""Database helpers dedicated to the additive P18 Follow-up module.

Mirrors core/database.py and seller_intelligence/database.py deliberately:
same context-manager shape, same underlying connection helper
(database.get_connection), so all three behave identically at the
transaction boundary without importing from one another for this.

The cursor this yields is a plain psycopg2 RealDictCursor - it is also what
gets passed into core.repository.create_task_with_cursor(cur, data) when a
rule creates a CORE task, so the followup_actions bookkeeping and the task
INSERT happen on the same connection/transaction (see
followup/repository.py for why: a local, P18-only transaction, never
shared with the stima INSERT, the CORE bridge, or seller_intelligence).
"""

from __future__ import annotations

from contextlib import contextmanager

from psycopg2.extras import RealDictCursor

from database import get_connection


@contextmanager
def followup_cursor(*, commit: bool = False):
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
