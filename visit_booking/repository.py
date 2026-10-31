"""BLOCCO-5A - persistenza dei link di visita e delle prenotazioni (migrazione 120)."""
from __future__ import annotations


def installed(cur) -> bool:
    cur.execute("SELECT to_regclass('public.property_visit_booking_links') IS NOT NULL AS presente")
    return bool(cur.fetchone()["presente"])


LINK_COLUMNS = ("id, agency_id, property_id, assigned_user_id, status, duration_minutes, step_minutes, "
                "travel_minutes, min_notice_minutes, horizon_days, owner_authorization, owner_authorization_note, "
                "owner_authorization_recorded_by, owner_authorization_recorded_at, created_by_user_id, created_at, "
                "expires_at, disabled_at")


def insert_link(cur, data: dict) -> dict:
    cols = list(data)
    cur.execute(f"INSERT INTO property_visit_booking_links ({', '.join(cols)}) VALUES "
                f"({', '.join(['%s'] * len(cols))}) RETURNING {LINK_COLUMNS}", [data[c] for c in cols])
    return dict(cur.fetchone())


def links_for_property(cur, agency_id: int, property_id: int) -> list[dict]:
    cur.execute(f"SELECT {LINK_COLUMNS} FROM property_visit_booking_links WHERE agency_id = %s AND property_id = %s "
                "ORDER BY created_at DESC, id DESC", (agency_id, property_id))
    return [dict(r) for r in cur.fetchall()]


def link_by_id(cur, agency_id: int, link_id: int, *, lock: bool = False) -> dict | None:
    cur.execute(f"SELECT {LINK_COLUMNS} FROM property_visit_booking_links WHERE id = %s AND agency_id = %s"
                + (" FOR UPDATE" if lock else ""), (link_id, agency_id))
    row = cur.fetchone()
    return dict(row) if row else None


def link_by_token_hash(cur, token_hash: str) -> dict | None:
    cur.execute(f"SELECT {LINK_COLUMNS} FROM property_visit_booking_links WHERE token_hash = %s", (token_hash,))
    row = cur.fetchone()
    return dict(row) if row else None


def disable_link(cur, agency_id: int, link_id: int, user_id: int) -> dict | None:
    cur.execute(f"UPDATE property_visit_booking_links SET status = 'disabled', disabled_at = NOW(), "
                f"disabled_by_user_id = %s WHERE id = %s AND agency_id = %s AND status = 'active' "
                f"RETURNING {LINK_COLUMNS}", (user_id, link_id, agency_id))
    row = cur.fetchone()
    return dict(row) if row else None


def booking_by_submission(cur, link_id: int, submission_key: str) -> dict | None:
    cur.execute("SELECT * FROM property_visit_bookings WHERE link_id = %s AND submission_key = %s",
                (link_id, submission_key))
    row = cur.fetchone()
    return dict(row) if row else None


def booking_by_manage_hash(cur, manage_hash: str) -> dict | None:
    cur.execute("SELECT * FROM property_visit_bookings WHERE manage_token_hash = %s", (manage_hash,))
    row = cur.fetchone()
    return dict(row) if row else None


def insert_booking(cur, data: dict) -> dict | None:
    cur.execute("INSERT INTO property_visit_bookings (agency_id, link_id, appointment_id, submission_key, "
                "manage_token_hash) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING *",
                (data["agency_id"], data["link_id"], data["appointment_id"], data["submission_key"],
                 data["manage_token_hash"]))
    row = cur.fetchone()
    return dict(row) if row else None


def mark_replaced(cur, booking_id: int, replaced_by_id: int) -> None:
    cur.execute("UPDATE property_visit_bookings SET replaced_by_id = %s WHERE id = %s AND replaced_by_id IS NULL",
                (replaced_by_id, booking_id))


def appointment_brief(cur, agency_id: int, appointment_id: int) -> dict | None:
    cur.execute("SELECT id, status, start_at, end_at, property_id, assigned_user_id FROM appointments "
                "WHERE id = %s AND agency_id = %s", (appointment_id, agency_id))
    row = cur.fetchone()
    return dict(row) if row else None


def public_property(cur, agency_id: int, property_id: int) -> dict | None:
    """Solo cio' che la pagina pubblica puo' mostrare: tipologia e comune.
    Mai indirizzo, codice interno, prezzo minimo, note o proprietario."""
    cur.execute("SELECT p.property_type, p.city FROM properties p WHERE p.id = %s AND p.agency_id = %s",
                (property_id, agency_id))
    row = cur.fetchone()
    return dict(row) if row else None


def agency_name(cur, agency_id: int) -> str | None:
    cur.execute("SELECT name FROM agencies WHERE id = %s", (agency_id,))
    row = cur.fetchone()
    return row["name"] if row else None


def increment_and_check(cur, *, link_id, scope, window_start, limit, client_ip_hash="") -> bool:
    cur.execute("INSERT INTO property_visit_booking_rate_limits (link_id, scope, client_ip_hash, window_start, "
                "request_count) VALUES (%s, %s, %s, %s, 1) ON CONFLICT (link_id, scope, client_ip_hash, window_start) "
                "DO UPDATE SET request_count = property_visit_booking_rate_limits.request_count + 1 "
                "RETURNING request_count", (link_id, scope, client_ip_hash, window_start))
    return cur.fetchone()["request_count"] <= limit
