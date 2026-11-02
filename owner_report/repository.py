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


def interested_contacts(cur, agency_id: int, property_id: int, start, end) -> list:
    """Date dei contatti interessati: la data del COLLEGAMENTO all'immobile
    (`property_leads.created_at`), mai quella di creazione del contatto."""
    cur.execute(
        "SELECT pl.created_at AS at FROM property_leads pl JOIN leads l ON l.id = pl.lead_id "
        "WHERE pl.property_id = %s AND l.agency_id = %s AND pl.relation_type = 'buyer_interest' "
        "AND pl.created_at >= %s AND pl.created_at < %s ORDER BY pl.created_at",
        (property_id, agency_id, start, end))
    return [r["at"] for r in cur.fetchall()]


def completed_visits(cur, agency_id: int, property_id: int, start, end) -> list:
    """Date delle visite svolte: gli appuntamenti `buyer_visit` completati
    (data di svolgimento) e le visite storiche registrate prima dell'agenda
    (`appointment_id` nullo, migrazione 078 senza backfill). Una visita storica
    che coincide con un appuntamento completato dello stesso immobile, nello
    stesso giorno (e con lo stesso contatto, se entrambi lo indicano) e' lo
    stesso evento registrato due volte: conta una volta sola."""
    cur.execute(
        "SELECT completed_at AS at FROM appointments WHERE agency_id = %s AND property_id = %s "
        "AND appointment_type = 'buyer_visit' AND status = 'completed' "
        "AND completed_at >= %s AND completed_at < %s "
        "UNION ALL "
        "SELECT pv.scheduled_at AS at FROM property_visits pv JOIN properties p ON p.id = pv.property_id "
        "WHERE pv.property_id = %s AND p.agency_id = %s AND pv.appointment_id IS NULL "
        "AND pv.status = 'completed' AND pv.scheduled_at >= %s AND pv.scheduled_at < %s "
        "AND NOT EXISTS (SELECT 1 FROM appointments a WHERE a.agency_id = p.agency_id "
        "AND a.property_id = pv.property_id AND a.appointment_type = 'buyer_visit' AND a.status = 'completed' "
        "AND (a.start_at AT TIME ZONE 'Europe/Rome')::date = (pv.scheduled_at AT TIME ZONE 'Europe/Rome')::date "
        "AND (pv.contact_id IS NULL OR a.contact_id IS NULL OR a.contact_id = pv.contact_id)) "
        "ORDER BY at",
        (agency_id, property_id, start, end, property_id, agency_id, start, end))
    return [r["at"] for r in cur.fetchall()]


def upcoming_visits(cur, agency_id: int, property_id: int, start, end) -> int:
    """Visite gia' fissate o confermate nell'intervallo futuro [start, end)."""
    cur.execute(
        "SELECT count(*) AS n FROM appointments WHERE agency_id = %s AND property_id = %s "
        "AND appointment_type = 'buyer_visit' AND status IN ('scheduled', 'confirmed') "
        "AND start_at >= %s AND start_at < %s", (agency_id, property_id, start, end))
    return int(cur.fetchone()["n"])


def proposals_by_status(cur, agency_id: int, property_id: int) -> dict:
    """Proposte di acquisto inviate, per stato attuale (mai le bozze). Senza
    periodo: la tabella non registra la data di invio."""
    cur.execute(
        "SELECT pp.status, count(*) AS n FROM property_proposals pp "
        "JOIN matches m ON m.id = pp.match_id JOIN buy_requests br ON br.id = m.buy_request_id "
        "WHERE m.property_id = %s AND br.agency_id = %s AND pp.status <> 'draft' GROUP BY pp.status",
        (property_id, agency_id))
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


# ---------------------------------------------------------------------------
# ROADMAP VISITE (fase D): le visite non svolte e i riscontri approvati
# ---------------------------------------------------------------------------

def not_held_visits(cur, agency_id: int, property_id: int, start, end) -> dict:
    """Visite ANNULLATE o NON SVOLTE (cliente assente) nell'intervallo, con la data
    dell'annullamento o dell'assenza. Non contano: le visite «create per errore» (mai
    esistite) e quelle che il cliente ha solo riprogrammato (hanno una visita
    successiva: non sono perse), ne' una riga storica che coincide con un
    appuntamento. Solo conteggi: nessun nome, nessun motivo, nessuna nota."""
    cur.execute(
        "SELECT a.status, count(*) AS n FROM appointments a WHERE a.agency_id = %s AND a.property_id = %s "
        "AND a.appointment_type = 'buyer_visit' AND a.status IN ('cancelled', 'no_show') "
        "AND COALESCE(a.cancelled_at, a.no_show_at) >= %s AND COALESCE(a.cancelled_at, a.no_show_at) < %s "
        "AND COALESCE(to_jsonb(a)->>'cancelled_kind', '') <> 'mistake' "
        "AND NOT EXISTS (SELECT 1 FROM appointments s WHERE s.rescheduled_from_id = a.id) "
        "GROUP BY a.status "
        "UNION ALL "
        "SELECT pv.status, count(*) AS n FROM property_visits pv JOIN properties p ON p.id = pv.property_id "
        "WHERE pv.property_id = %s AND p.agency_id = %s AND pv.appointment_id IS NULL "
        "AND pv.status IN ('cancelled', 'no_show') AND pv.scheduled_at >= %s AND pv.scheduled_at < %s "
        "GROUP BY pv.status",
        (agency_id, property_id, start, end, property_id, agency_id, start, end))
    conti = {"cancelled": 0, "no_show": 0}
    for r in cur.fetchall():
        conti[r["status"]] += int(r["n"])
    return conti


def _feedback_installed(cur) -> bool:
    cur.execute("SELECT to_regclass('public.owner_visit_feedback_publications') IS NOT NULL AS presente")
    return bool(cur.fetchone()["presente"])


def published_visit_feedback(cur, agency_id: int, property_id: int, start, end,
                             owner_account_id: int | None = None, all_owners: bool = False) -> list:
    """I riscontri PUBBLICATI (cioe' approvati dall'agente) delle visite di un immobile, nella
    versione corrente, con la data di pubblicazione nell'intervallo. Per il proprietario:
    quelli per tutti i proprietari dell'immobile e quelli indirizzati a lui. Mai le bozze,
    mai le versioni sostituite o ritirate. Solo il testo approvato: nessuna nota interna."""
    if not _feedback_installed(cur):
        return []
    owner_filter = "" if all_owners else " AND (vf.owner_account_id IS NULL OR vf.owner_account_id = %s)"
    params = [property_id, agency_id, start, end] + ([] if all_owners else [owner_account_id])
    cur.execute(
        "SELECT vf.id, vf.category, vf.public_summary, vf.sentiment, vf.published_at "
        "FROM owner_visit_feedback_publications vf JOIN property_visits pv ON pv.id = vf.property_visit_id "
        "JOIN properties p ON p.id = pv.property_id "
        "WHERE pv.property_id = %s AND p.agency_id = %s AND vf.status = 'published' "
        "AND vf.superseded_by_feedback_publication_id IS NULL "
        "AND vf.published_at >= %s AND vf.published_at < %s" + owner_filter + " ORDER BY vf.published_at DESC, vf.id DESC",
        params)
    return [dict(r) for r in cur.fetchall()]


def pending_visit_feedback(cur, agency_id: int, property_id: int) -> int:
    """Bozze di riscontro in attesa di approvazione: le vede solo il consulente."""
    if not _feedback_installed(cur):
        return 0
    cur.execute(
        "SELECT count(*) AS n FROM owner_visit_feedback_publications vf "
        "JOIN property_visits pv ON pv.id = vf.property_visit_id JOIN properties p ON p.id = pv.property_id "
        "WHERE pv.property_id = %s AND p.agency_id = %s AND vf.status = 'draft'", (property_id, agency_id))
    return int(cur.fetchone()["n"])


def owners_with_access(cur, agency_id: int, property_id: int) -> int:
    """Quanti proprietari hanno ADESSO un'autorizzazione attiva sull'immobile (il portale legge la
    stessa regola): dice all'agente a chi arrivera' un riscontro approvato."""
    cur.execute(
        "SELECT count(*) AS n FROM owner_property_access x JOIN properties p ON p.id = x.property_id "
        "WHERE x.property_id = %s AND p.agency_id = %s AND x.access_status = 'active' AND x.revoked_at IS NULL "
        "AND (x.valid_until IS NULL OR x.valid_until > NOW())", (property_id, agency_id))
    return int(cur.fetchone()["n"])
