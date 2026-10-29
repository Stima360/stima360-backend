"""REPORT-PROPRIETARIO-1 - letture SQL del report. Sola lettura: nessuna
funzione scrive. Ogni query e' filtrata per l'agenzia del contesto, anche
quando passa da tabelle figlie (visite storiche, proposte) che non portano
`agency_id`: l'agenzia si ricava sempre dall'immobile o dalla richiesta."""
from __future__ import annotations

from core.property_mandate import signed_link_mandate


def get_property(cur, agency_id: int, property_id: int):
    cur.execute("SELECT *, (to_jsonb(properties)->>'deleted_at') AS trash_marker FROM properties "
                "WHERE id = %s AND agency_id = %s", (property_id, agency_id))
    row = cur.fetchone()
    if row is None:
        return None
    row = dict(row)
    row["signed_mandate"] = signed_link_mandate(cur, property_id)
    return row


def inquiries(cur, agency_id: int, property_id: int, start, end) -> list:
    """Date delle richieste: contatti collegati come interessati all'acquisto."""
    cur.execute(
        "SELECT pl.created_at AS at FROM property_leads pl JOIN leads l ON l.id = pl.lead_id "
        "WHERE pl.property_id = %s AND l.agency_id = %s AND pl.relation_type = 'buyer_interest' "
        "AND pl.created_at >= %s AND pl.created_at < %s ORDER BY pl.created_at",
        (property_id, agency_id, start, end))
    return [r["at"] for r in cur.fetchall()]


def completed_visits(cur, agency_id: int, property_id: int, start, end) -> list:
    """Date delle visite svolte: gli appuntamenti `buyer_visit` completati e le
    visite storiche registrate prima del calendario (senza appuntamento)."""
    cur.execute(
        "SELECT completed_at AS at FROM appointments WHERE agency_id = %s AND property_id = %s "
        "AND appointment_type = 'buyer_visit' AND status = 'completed' "
        "AND completed_at >= %s AND completed_at < %s "
        "UNION ALL "
        "SELECT pv.scheduled_at AS at FROM property_visits pv JOIN properties p ON p.id = pv.property_id "
        "WHERE pv.property_id = %s AND p.agency_id = %s AND pv.appointment_id IS NULL "
        "AND pv.status = 'completed' AND pv.scheduled_at >= %s AND pv.scheduled_at < %s "
        "ORDER BY at",
        (agency_id, property_id, start, end, property_id, agency_id, start, end))
    return [r["at"] for r in cur.fetchall()]


def upcoming_visits(cur, agency_id: int, property_id: int, now) -> int:
    cur.execute(
        "SELECT count(*) AS n FROM appointments WHERE agency_id = %s AND property_id = %s "
        "AND appointment_type = 'buyer_visit' AND status IN ('requested', 'scheduled', 'confirmed') "
        "AND start_at >= %s", (agency_id, property_id, now))
    return int(cur.fetchone()["n"])


def proposals_by_status(cur, agency_id: int, property_id: int, start, end) -> dict:
    """Proposte di acquisto inviate nel periodo, per stato (mai le bozze)."""
    cur.execute(
        "SELECT pp.status, count(*) AS n FROM property_proposals pp "
        "JOIN matches m ON m.id = pp.match_id JOIN buy_requests br ON br.id = m.buy_request_id "
        "WHERE m.property_id = %s AND br.agency_id = %s AND pp.status <> 'draft' "
        "AND pp.created_at >= %s AND pp.created_at < %s GROUP BY pp.status",
        (property_id, agency_id, start, end))
    return {r["status"]: int(r["n"]) for r in cur.fetchall()}


def price_history(cur, property_id: int, start, end) -> list:
    cur.execute("SELECT old_price, new_price, created_at FROM property_price_history "
                "WHERE property_id = %s AND created_at >= %s AND created_at < %s ORDER BY created_at, id",
                (property_id, start, end))
    return [dict(r) for r in cur.fetchall()]


def status_history(cur, property_id: int, start, end) -> list:
    cur.execute("SELECT old_value, new_value, created_at FROM property_status_history "
                "WHERE property_id = %s AND field_name = 'commercial_status' "
                "AND created_at >= %s AND created_at < %s ORDER BY created_at, id",
                (property_id, start, end))
    return [dict(r) for r in cur.fetchall()]
