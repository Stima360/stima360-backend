from __future__ import annotations

from contextlib import contextmanager

from psycopg2.extras import RealDictCursor

from database import get_connection


@contextmanager
def seller_intent_cursor():
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield conn, cur
    finally:
        cur.close()
        conn.close()

