"""ADM-2 - le letture dell'anteprima delle conseguenze. SOLO SELECT.

Ogni funzione riceve un cursore gia' aperto dal service, che apre la
transazione in `READ ONLY`: qui non c'e' una scrittura, e se ce ne fosse una
il database la rifiuterebbe.

Conta soltanto cio' che lo schema collega davvero all'agenzia, all'operatore o
al territorio (colonne `agency_id`, `assigned_agent_id`, `assigned_user_id`,
`requested_by_user_id`, ...). Le assegnazioni scritte in testo libero (buy,
match, attivita' e task legacy) non sono collegate a un operatore e NON vengono
contate: il service lo dichiara fra le dipendenze non verificate.

Una tabella assente dal database (migrazione non applicata) non e' un errore:
la sua voce torna `None` e diventa "non verificato" nell'anteprima.
"""
from __future__ import annotations

from typing import Any

from network_routing.repository import ALIAS_SOURCE_PUBLIC_STIMA, NORMALISED
from operator_auth.enums import DEFAULT_AGENCY_SLUG

#: Gli appuntamenti ancora da svolgere (072: `appointments_status_chk`).
UPCOMING_APPOINTMENT_STATUSES = ("requested", "scheduled", "confirmed")
#: Le acquisizioni ancora aperte (081: `acquisitions_status_chk`).
CLOSED_ACQUISITION_STATUSES = ("acquired", "lost")
#: Finestra dell'indicatore sul flusso delle stime pubbliche.
RECENT_DAYS = 90
#: Un elenco nell'anteprima e' un riepilogo, non un export.
LIST_LIMIT = 200

_NORM = NORMALISED.format


def present_tables(cur, names) -> set[str]:
    cur.execute(
        "SELECT t FROM unnest(%s::text[]) AS t WHERE to_regclass('public.' || t) IS NOT NULL",
        (list(names),),
    )
    return {row["t"] for row in cur.fetchall()}


def _one(cur, sql: str, params: dict) -> dict[str, Any]:
    cur.execute(sql, params)
    return dict(cur.fetchone())


def _counts(cur, present: set[str], queries: dict[str, str], params: dict) -> dict[str, dict | None]:
    return {name: (_one(cur, sql, params) if name in present else None) for name, sql in queries.items()}


# ---------------------------------------------------------------------------
# Agenzia
# ---------------------------------------------------------------------------

AGENCY_TABLES = (
    "properties", "buildings", "contacts", "leads", "buy_requests", "appointments", "acquisitions",
    "public_booking_links", "calendar_connections", "communication_journeys",
    "building_deletion_requests", "property_territorial_change_requests", "stime",
)

_AGENCY_COUNTS = {
    "properties": "SELECT count(*) FILTER (WHERE deleted_at IS NULL) AS live, "
                  "count(*) FILTER (WHERE deleted_at IS NOT NULL) AS trash FROM properties WHERE agency_id = %(a)s",
    "buildings": "SELECT count(*) FILTER (WHERE deleted_at IS NULL) AS live, "
                 "count(*) FILTER (WHERE deleted_at IS NOT NULL) AS trash FROM buildings WHERE agency_id = %(a)s",
    "contacts": "SELECT count(*) FILTER (WHERE deleted_at IS NULL) AS live, "
                "count(*) FILTER (WHERE deleted_at IS NOT NULL) AS trash FROM contacts WHERE agency_id = %(a)s",
    "leads": "SELECT count(*) AS live FROM leads WHERE agency_id = %(a)s",
    "buy_requests": "SELECT count(*) FILTER (WHERE deleted_at IS NULL) AS live, "
                    "count(*) FILTER (WHERE deleted_at IS NOT NULL) AS trash FROM buy_requests WHERE agency_id = %(a)s",
    "appointments": "SELECT count(*) AS total, count(*) FILTER (WHERE start_at >= NOW() AND status = ANY(%(upcoming)s)) "
                    "AS upcoming FROM appointments WHERE agency_id = %(a)s",
    "acquisitions": "SELECT count(*) AS total, count(*) FILTER (WHERE status <> ALL(%(closed)s)) AS open "
                    "FROM acquisitions WHERE agency_id = %(a)s",
    "public_booking_links": "SELECT count(*) AS active FROM public_booking_links WHERE agency_id = %(a)s "
                            "AND status = 'active' AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > NOW())",
    "calendar_connections": "SELECT count(*) AS connected FROM calendar_connections "
                            "WHERE agency_id = %(a)s AND status = 'connected'",
    "communication_journeys": "SELECT count(*) AS active FROM communication_journeys "
                              "WHERE agency_id = %(a)s AND status = 'active'",
    "building_deletion_requests": "SELECT count(*) AS pending FROM building_deletion_requests "
                                  "WHERE agency_id = %(a)s AND status = 'pending'",
    "property_territorial_change_requests":
        "SELECT count(*) FILTER (WHERE origin_agency_id = %(a)s) AS as_origin, "
        "count(*) FILTER (WHERE competent_agency_id = %(a)s) AS as_competent "
        "FROM property_territorial_change_requests WHERE status = 'pending' "
        "AND (origin_agency_id = %(a)s OR competent_agency_id = %(a)s)",
    "stime": f"SELECT count(*) AS recent FROM stime WHERE agency_id = %(a)s "
             f"AND data >= NOW() - INTERVAL '{RECENT_DAYS} days'",
}


def agency_data_counts(cur, agency_id: int, present: set[str]) -> dict[str, dict | None]:
    return _counts(cur, present, _AGENCY_COUNTS, {
        "a": agency_id, "upcoming": list(UPCOMING_APPOINTMENT_STATUSES),
        "closed": list(CLOSED_ACQUISITION_STATUSES)})


def agency_members(cur, agency_id: int) -> list[dict[str, Any]]:
    """Le membership per ruolo e stato; `usable` = persone che oggi accedono
    con questa membership (account attivo, non platform admin)."""
    cur.execute(
        """
        SELECT m.role, m.status, count(*) AS total,
               count(*) FILTER (WHERE u.status = 'active' AND NOT u.is_platform_admin) AS usable
          FROM agency_memberships m JOIN operator_users u ON u.id = m.operator_user_id
         WHERE m.agency_id = %s
         GROUP BY m.role, m.status ORDER BY m.role, m.status
        """,
        (agency_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def agency_live_sessions(cur, agency_id: int) -> dict[str, int]:
    """Le sessioni vive che ADM-1 revocherebbe (stesso predicato di
    `operator_auth.repository.revoke_agency_member_sessions`) e gli acting."""
    return _one(cur, """
        SELECT
          (SELECT count(*) FROM operator_sessions s JOIN operator_users u ON u.id = s.operator_user_id
            WHERE s.revoked_at IS NULL AND s.expires_at > NOW() AND u.is_platform_admin = FALSE
              AND EXISTS (SELECT 1 FROM agency_memberships m WHERE m.operator_user_id = u.id
                           AND m.agency_id = %(a)s AND m.status = 'active')) AS member_sessions,
          (SELECT count(*) FROM operator_sessions s WHERE s.acting_agency_id = %(a)s
              AND s.revoked_at IS NULL AND s.expires_at > NOW()) AS acting_sessions
        """, {"a": agency_id})


def agency_assignments(cur, agency_id: int) -> list[dict[str, Any]]:
    """Le assegnazioni attive e sospese, con il territorio e i suoi alias attivi."""
    cur.execute(
        """
        SELECT a.id, a.status, t.id AS territory_id, t.kind, t.label, t.canonical_key,
               (SELECT count(*) FROM network_territory_aliases al
                 WHERE al.territory_id = t.id AND al.status = 'active') AS active_aliases
          FROM agency_territory_assignments a JOIN network_territories t ON t.id = a.territory_id
         WHERE a.agency_id = %s AND a.status IN ('active', 'suspended')
         ORDER BY t.label, a.id LIMIT %s
        """,
        (agency_id, LIST_LIMIT),
    )
    return [dict(r) for r in cur.fetchall()]


def _competent_chain(reader: str) -> str:
    """Comune della riga `p` competente per `reader` (SQL) secondo la catena C1."""
    return (
        "EXISTS (SELECT 1 FROM network_territory_aliases nta "
        "JOIN network_territories nt ON nt.id = nta.territory_id "
        "JOIN agency_territory_assignments ata ON ata.territory_id = nt.id AND ata.status = 'active' "
        "JOIN agencies ag ON ag.id = ata.agency_id AND ag.status = 'active' "
        f"WHERE nta.source = '{ALIAS_SOURCE_PUBLIC_STIMA}' AND nta.status = 'active' "
        f"AND nt.kind = 'municipality' AND {reader} "
        "AND p.city IS NOT NULL AND btrim(p.city) <> '' "
        f"AND {_NORM('nta.match_value')} = {_NORM('p.city')})"
    )


def agency_territorial_sharing(cur, agency_id: int) -> dict[str, int]:
    """C1, nei due versi. `visible_to_agency`: immobili di ALTRE agenzie che oggi
    questa agenzia legge per competenza. `shared_with_others`: immobili di questa
    agenzia letti oggi da un'altra agenzia competente."""
    return _one(cur, f"""
        SELECT
          (SELECT count(*) FROM properties p WHERE p.agency_id <> %(a)s AND p.deleted_at IS NULL
              AND {_competent_chain('ata.agency_id = %(a)s')}) AS visible_to_agency,
          (SELECT count(*) FROM properties p WHERE p.agency_id = %(a)s AND p.deleted_at IS NULL
              AND {_competent_chain('ata.agency_id <> p.agency_id')}) AS shared_with_others
        """, {"a": agency_id})


def system_agency(cur) -> dict[str, Any] | None:
    cur.execute("SELECT id, name, slug, status FROM agencies WHERE slug = %s", (DEFAULT_AGENCY_SLUG,))
    row = cur.fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Operatore
# ---------------------------------------------------------------------------

OPERATOR_TABLES = (
    "contacts", "leads", "properties", "acquisitions", "appointments", "public_booking_links",
    "calendar_connections", "property_territorial_change_requests", "building_deletion_requests",
)

_OPERATOR_COUNTS = {
    "contacts": "SELECT count(*) AS assigned FROM contacts WHERE assigned_agent_id = %(u)s AND deleted_at IS NULL",
    "leads": "SELECT count(*) AS assigned FROM leads WHERE assigned_agent_id = %(u)s",
    "properties": "SELECT count(*) AS assigned FROM properties WHERE assigned_agent_id = %(u)s AND deleted_at IS NULL",
    "acquisitions": "SELECT count(*) AS assigned, count(*) FILTER (WHERE status <> ALL(%(closed)s)) AS open "
                    "FROM acquisitions WHERE assigned_agent_id = %(u)s",
    "appointments": "SELECT count(*) FILTER (WHERE start_at >= NOW() AND status = ANY(%(upcoming)s)) AS upcoming "
                    "FROM appointments WHERE assigned_user_id = %(u)s",
    "public_booking_links": "SELECT count(*) AS active FROM public_booking_links WHERE assigned_user_id = %(u)s "
                            "AND status = 'active' AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > NOW())",
    "calendar_connections": "SELECT count(*) AS connected FROM calendar_connections "
                            "WHERE user_id = %(u)s AND status = 'connected'",
    "property_territorial_change_requests": "SELECT count(*) AS pending FROM property_territorial_change_requests "
                                            "WHERE requested_by_user_id = %(u)s AND status = 'pending'",
    "building_deletion_requests": "SELECT count(*) AS pending FROM building_deletion_requests "
                                  "WHERE requested_by_user_id = %(u)s AND status = 'pending'",
}


def operator_data_counts(cur, operator_user_id: int, present: set[str]) -> dict[str, dict | None]:
    return _counts(cur, present, _OPERATOR_COUNTS, {
        "u": operator_user_id, "upcoming": list(UPCOMING_APPOINTMENT_STATUSES),
        "closed": list(CLOSED_ACQUISITION_STATUSES)})


def operator_memberships(cur, operator_user_id: int) -> list[dict[str, Any]]:
    """Tutte le membership, con l'agenzia e, per un titolare attivo, i Comuni
    attivi che quell'agenzia presidia."""
    cur.execute(
        """
        SELECT m.id, m.agency_id, ag.name AS agency_name, ag.status AS agency_status, m.role, m.status,
               (SELECT count(*) FROM agency_territory_assignments ata
                  JOIN network_territories nt ON nt.id = ata.territory_id
                 WHERE ata.agency_id = m.agency_id AND ata.status = 'active'
                   AND nt.kind = 'municipality') AS agency_active_municipalities,
               (SELECT count(*) FROM agency_memberships o
                 WHERE o.agency_id = m.agency_id AND o.role = 'agency_owner' AND o.status = 'active'
                   AND o.operator_user_id <> m.operator_user_id) AS other_active_owners
          FROM agency_memberships m JOIN agencies ag ON ag.id = m.agency_id
         WHERE m.operator_user_id = %s ORDER BY m.id
        """,
        (operator_user_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def operator_live_sessions(cur, operator_user_id: int) -> int:
    return _one(cur, "SELECT count(*) AS n FROM operator_sessions WHERE operator_user_id = %(u)s "
                     "AND revoked_at IS NULL AND expires_at > NOW()", {"u": operator_user_id})["n"]


# ---------------------------------------------------------------------------
# Territorio: assegnazione e alias
# ---------------------------------------------------------------------------

def territory_aliases(cur, territory_id: int) -> list[dict[str, Any]]:
    cur.execute(
        "SELECT id, match_value FROM network_territory_aliases "
        "WHERE territory_id = %s AND status = 'active' AND source = %s ORDER BY id LIMIT %s",
        (territory_id, ALIAS_SOURCE_PUBLIC_STIMA, LIST_LIMIT),
    )
    return [dict(r) for r in cur.fetchall()]


def other_assignments(cur, territory_id: int, excluding_id: int | None) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT a.id, a.agency_id, ag.name AS agency_name, ag.status AS agency_status, a.status
          FROM agency_territory_assignments a JOIN agencies ag ON ag.id = a.agency_id
         WHERE a.territory_id = %s AND a.status IN ('active', 'suspended')
           AND (%s::bigint IS NULL OR a.id <> %s::bigint)
         ORDER BY a.id
        """,
        (territory_id, excluding_id, excluding_id),
    )
    return [dict(r) for r in cur.fetchall()]


def comune_counts(cur, values: list[str], agency_id: int | None, present: set[str]) -> dict[str, Any]:
    """Per i valori di Comune indicati (confronto normalizzato, come il routing):
    stime recenti, immobili di `agency_id` e immobili di altre agenzie."""
    out: dict[str, Any] = {"recent_stime": None, "own_properties": None, "other_properties": None}
    if not values:
        return {k: 0 for k in out}
    norm = f"(SELECT {_NORM('x')} FROM unnest(%(v)s::text[]) AS x)"
    if "stime" in present:
        out["recent_stime"] = _one(cur, f"SELECT count(*) AS n FROM stime WHERE {_NORM('comune')} IN {norm} "
                                        f"AND data >= NOW() - INTERVAL '{RECENT_DAYS} days'", {"v": list(values)})["n"]
    if "properties" in present:
        riga = _one(cur, f"""
            SELECT count(*) FILTER (WHERE agency_id = %(a)s) AS own,
                   count(*) FILTER (WHERE %(a)s::bigint IS NULL OR agency_id <> %(a)s) AS other
              FROM properties WHERE deleted_at IS NULL AND {_NORM('city')} IN {norm}
            """, {"v": list(values), "a": agency_id})
        out["own_properties"], out["other_properties"] = riga["own"], riga["other"]
    return out


def pending_c2_for_territory(cur, territory_id: int, agency_id: int | None, present: set[str]) -> int | None:
    if "property_territorial_change_requests" not in present:
        return None
    return _one(cur, "SELECT count(*) AS n FROM property_territorial_change_requests WHERE status = 'pending' "
                     "AND territory_id = %(t)s AND (%(a)s::bigint IS NULL OR competent_agency_id = %(a)s)",
                {"t": territory_id, "a": agency_id})["n"]
