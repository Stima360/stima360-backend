"""A30-11 - l'accesso al database per orari di lavoro, eccezioni ed
chiusure agenzia. Nessuna scrittura su `appointments`/`appointment_events`
(D2): queste tabelle non partecipano al motore di sovrapposizione.

Stesso stile di `appointments/repository.py`: funzioni SQL pure, il cursore
lo passa il chiamante (nessuna connessione propria, nessun commit qui).
"""
from __future__ import annotations


def has_weekly_configuration(cur, agency_id: int, user_id: int) -> bool:
    """D1: l'agente ha almeno una riga di orario settimanale?"""
    cur.execute(
        "SELECT 1 FROM agent_working_hours WHERE agency_id = %s AND user_id = %s LIMIT 1",
        (agency_id, user_id),
    )
    return cur.fetchone() is not None


def weekly_hours(cur, agency_id: int, user_id: int) -> list[dict]:
    """TUTTE le righe settimanali dell'agente (sono poche per costruzione:
    al massimo qualche fascia per ognuno dei 7 giorni)."""
    cur.execute(
        "SELECT id, day_of_week, start_minute, end_minute"
        "  FROM agent_working_hours"
        " WHERE agency_id = %s AND user_id = %s"
        " ORDER BY day_of_week, start_minute",
        (agency_id, user_id),
    )
    return [dict(r) for r in cur.fetchall()]


def exceptions_in_range(cur, agency_id: int, user_id: int, date_from, date_to) -> list[dict]:
    cur.execute(
        "SELECT id, exception_date, start_minute, end_minute, is_available, reason_code"
        "  FROM agent_availability_exceptions"
        " WHERE agency_id = %s AND user_id = %s"
        "   AND exception_date BETWEEN %s AND %s"
        " ORDER BY exception_date, start_minute",
        (agency_id, user_id, date_from, date_to),
    )
    return [dict(r) for r in cur.fetchall()]


def closures_in_range(cur, agency_id: int, date_from, date_to) -> list[dict]:
    cur.execute(
        "SELECT id, closure_date, start_minute, end_minute, reason_code"
        "  FROM agency_closures"
        " WHERE agency_id = %s"
        "   AND closure_date BETWEEN %s AND %s"
        " ORDER BY closure_date, start_minute",
        (agency_id, date_from, date_to),
    )
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# SCRITTURE - orario settimanale: sostituzione dell'intero set per l'agente
# (PUT, idempotente per costruzione: il risultato finale non dipende da
# quante volte si applica).
# ---------------------------------------------------------------------------

def replace_weekly_hours(cur, agency_id: int, user_id: int, rows: list[dict]) -> list[dict]:
    """Sostituisce TUTTE le righe settimanali dell'agente con `rows`
    (ognuna: day_of_week, start_minute, end_minute). L'EXCLUDE della 076
    resta l'unica verita' contro le sovrapposizioni: una `rows` con fasce
    sovrapposte fallisce qui con l'eccezione del database, mai in Python."""
    cur.execute(
        "DELETE FROM agent_working_hours WHERE agency_id = %s AND user_id = %s",
        (agency_id, user_id),
    )
    for riga in rows:
        cur.execute(
            "INSERT INTO agent_working_hours (agency_id, user_id, day_of_week, "
            "       start_minute, end_minute)"
            " VALUES (%s, %s, %s, %s, %s)",
            (agency_id, user_id, riga["day_of_week"], riga["start_minute"], riga["end_minute"]),
        )
    return weekly_hours(cur, agency_id, user_id)


def create_exception(cur, agency_id: int, user_id: int, *, exception_date, start_minute,
                     end_minute, is_available, reason_code=None) -> dict:
    cur.execute(
        "INSERT INTO agent_availability_exceptions"
        "       (agency_id, user_id, exception_date, start_minute, end_minute,"
        "        is_available, reason_code)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)"
        " RETURNING id, exception_date, start_minute, end_minute, is_available, reason_code",
        (agency_id, user_id, exception_date, start_minute, end_minute, is_available, reason_code),
    )
    return dict(cur.fetchone())


def delete_exception(cur, agency_id: int, user_id: int, exception_id: int) -> bool:
    cur.execute(
        "DELETE FROM agent_availability_exceptions"
        " WHERE id = %s AND agency_id = %s AND user_id = %s",
        (exception_id, agency_id, user_id),
    )
    return cur.rowcount > 0


def create_closure(cur, agency_id: int, *, closure_date, start_minute, end_minute,
                   reason_code=None) -> dict:
    cur.execute(
        "INSERT INTO agency_closures (agency_id, closure_date, start_minute, end_minute, reason_code)"
        " VALUES (%s, %s, %s, %s, %s)"
        " RETURNING id, closure_date, start_minute, end_minute, reason_code",
        (agency_id, closure_date, start_minute, end_minute, reason_code),
    )
    return dict(cur.fetchone())


def delete_closure(cur, agency_id: int, closure_id: int) -> bool:
    cur.execute(
        "DELETE FROM agency_closures WHERE id = %s AND agency_id = %s",
        (closure_id, agency_id),
    )
    return cur.rowcount > 0


def effective_inputs(cur, agency_id: int, user_id: int, date_from, date_to) -> dict:
    """Tutto cio' che serve al domain puro per calcolare l'availability
    effettiva in `[date_from, date_to)`: un'unica chiamata comoda per il
    service, MAI usata dal domain (che resta ignaro del database)."""
    return {
        "has_weekly_config": has_weekly_configuration(cur, agency_id, user_id),
        "weekly_rows": weekly_hours(cur, agency_id, user_id),
        "exception_rows": exceptions_in_range(cur, agency_id, user_id, date_from, date_to),
        "closure_rows": closures_in_range(cur, agency_id, date_from, date_to),
    }
