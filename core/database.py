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
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
