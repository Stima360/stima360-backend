"""FIRMA OTP - riepiloghi in SOLA LETTURA per il riquadro firma, la sezione «Fogli visita» e gli avvisi in «Oggi».

Un solo modo di calcolare lo stato (`service._with_signers` -> `otp_service.decorate` -> `presentation.ui_state`),
cosi' Agenda, Fogli visita e Oggi dicono sempre la stessa cosa. Nessuna scrittura: una richiesta scaduta per tempo ma
non ancora chiusa si MOSTRA «Scaduto» (la chiusura vera la fa la prima lettura del riquadro, come sempre).

Visibilita': quella dei fogli visita (`visit_sheets.service._visible`): titolare, amministratori e Supreme in acting
vedono l'agenzia; un agente solo le visite a lui assegnate. Sempre e solo l'agenzia della sessione.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import presentation, providers
from . import service as _s

LIST_LABELS = {**presentation.LABELS, presentation.TO_SEND: "Da firmare"}
ATTENTION = (presentation.COMPLETED, presentation.DECLINED, presentation.EXPIRED, presentation.SEND_FAILED)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _installed(cur) -> bool:
    cur.execute("SELECT to_regclass('public.signature_requests') IS NOT NULL AS ok")
    return bool(cur.fetchone()["ok"])


def _per_sola_lettura(row: dict) -> dict:
    if row["status"] == "sent" and row["expires_at"] <= _now():
        return {**row, "status": "expired"}
    return row


def views_by_id(cur, request_ids: list[int]) -> dict[int, dict]:
    if not request_ids:
        return {}
    cur.execute(f"SELECT {_s.COLUMNS} FROM signature_requests WHERE id = ANY(%s)", (request_ids,))
    rows = [_per_sola_lettura(dict(r)) for r in cur.fetchall()]
    out = {}
    for v in _s._with_signers(cur, rows):
        v.setdefault("ui", presentation.ui_state(v))
        out[v["id"]] = v
    return out


def latest_by_sheet(cur, agency_id: int, sheet_ids: list[int]) -> dict[int, dict]:
    """La richiesta piu' recente di ogni foglio (con stato per l'interfaccia), o niente."""
    if not sheet_ids or not _installed(cur):
        return {}
    cur.execute("SELECT DISTINCT ON (visit_sheet_id) id, visit_sheet_id FROM signature_requests "
                "WHERE agency_id = %s AND visit_sheet_id = ANY(%s) ORDER BY visit_sheet_id, created_at DESC, id DESC",
                (agency_id, sheet_ids))
    pairs = [(r["visit_sheet_id"], r["id"]) for r in cur.fetchall()]
    views = views_by_id(cur, [rid for _, rid in pairs])
    return {sid: views[rid] for sid, rid in pairs if rid in views}


def brief(view: dict | None) -> dict:
    """Quello che serve all'elenco e al riquadro, senza identificativi tecnici del fornitore."""
    ui = (view or {}).get("ui") or presentation.ui_state(view)
    visitor = next((s for s in (view or {}).get("signers") or [] if s.get("role") == "visitor"), None)
    return {
        "request_id": view["id"] if view else None,
        "state": ui["state"], "state_label": ui["label"], "list_label": LIST_LABELS[ui["state"]], "note": ui["note"],
        "signer_name": visitor["name"] if visitor else (view or {}).get("signer_name"),
        "signed_at": visitor.get("signed_at") if visitor else None,
        "completed_at": (view or {}).get("closed_at") if ui["state"] == presentation.COMPLETED else None,
        "has_document": bool(view and view.get("has_document")),
        "has_audit_trail": bool(view and view.get("has_audit_trail")),
        "simulation": bool(view and view.get("simulation")),
    }


def _visibilita(ctx) -> int | None:
    from visit_sheets import service as sheets_service
    return None if sheets_service._manager(ctx) else (ctx.user_id if ctx.user_id is not None else -1)


def notifications(ctx, *, days: int = 14, limit: int = 20) -> dict:
    """Gli avvisi per «Oggi»: firme completate, rifiutate, scadute e invii non riusciti degli ultimi giorni.
    UNO per visita (la sua richiesta piu' recente): nessun doppione fra versioni o reinvii."""
    from core.database import core_cursor

    agency_id = ctx.require_agency()
    solo = _visibilita(ctx)
    days = max(1, min(int(days), 60))
    with core_cursor() as (_, cur):
        if not _installed(cur):
            return {"items": []}
        cur.execute("SELECT DISTINCT ON (s.appointment_id) r.id, s.appointment_id, s.id AS sheet_id, s.data_snapshot "
                    "FROM signature_requests r JOIN visit_sheets s ON s.id = r.visit_sheet_id AND s.agency_id = r.agency_id "
                    "JOIN appointments a ON a.id = s.appointment_id AND a.agency_id = r.agency_id "
                    "WHERE r.agency_id = %s AND r.provider = %s AND r.created_at > NOW() - make_interval(days => %s + 30) "
                    "AND (%s::bigint IS NULL OR a.assigned_user_id = %s::bigint) "
                    "ORDER BY s.appointment_id, r.created_at DESC, r.id DESC",
                    (agency_id, providers.OTP_PROVIDER, days, solo, solo))
        candidati = [dict(r) for r in cur.fetchall()]
        views = views_by_id(cur, [c["id"] for c in candidati])
    soglia = _now() - timedelta(days=days)
    items = []
    for c in candidati:
        v = views.get(c["id"])
        if v is None or v["ui"]["state"] not in ATTENTION:
            continue
        if v["ui"]["state"] == presentation.EXPIRED:
            quando = v.get("expires_at")
        elif v["ui"]["state"] == presentation.SEND_FAILED:
            quando = ((v.get("link") or {}).get("issued_at")) or v.get("created_at")
        else:
            quando = v.get("closed_at") or v.get("created_at")
        if not quando or datetime.fromisoformat(quando) < soglia:
            continue
        snap = c["data_snapshot"] or {}
        prop, visit = snap.get("property") or {}, snap.get("visit") or {}
        items.append({"appointment_id": c["appointment_id"], "state": v["ui"]["state"], "label": v["ui"]["label"],
                      "client": v.get("signer_name") or visit.get("visitor_name"),
                      "property": prop.get("address") or prop.get("code"), "visit_text": visit.get("date_text"),
                      "at": quando})
    items.sort(key=lambda x: x["at"], reverse=True)
    return {"items": items[:max(1, min(int(limit), 50))], "days": days}


def overview(ctx, *, q: str | None = None, agent_id: int | None = None, date_from=None, date_to=None,
             state: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    """La sezione «Fogli visita»: ogni visita immobile con il suo foglio (l'ultima versione) o che ne puo' avere uno,
    con cliente, immobile, agente, stato della firma e ultima attivita'. Ricerca su cliente, codice, indirizzo e comune;
    filtri per agente, periodo e stato."""
    from core.database import core_cursor

    agency_id = ctx.require_agency()
    solo = _visibilita(ctx)
    testo = f"%{q.strip()}%" if q and q.strip() else None
    if state is not None and state not in LIST_LABELS:
        state = None
    with core_cursor() as (_, cur):
        cur.execute("SELECT to_regclass('public.visit_sheets') IS NOT NULL AS ok")
        if not cur.fetchone()["ok"]:
            return {"items": [], "total": 0, "agents": []}
        # Ogni visita immobile che ha un foglio, o che puo' averne uno (fissata, confermata o svolta, con immobile):
        # anche quelle ancora senza foglio, «Da firmare», si fanno firmare da qui.
        cur.execute(
            """
            SELECT * FROM (
              SELECT s.id AS sheet_id, a.id AS appointment_id, s.sheet_version, s.generated_at, s.data_snapshot,
                     a.start_at, a.status AS visit_status, a.assigned_user_id,
                     NULLIF(btrim(concat_ws(' ', u.first_name, u.last_name)), '') AS agent_name,
                     COALESCE(NULLIF(btrim(c.display_name), ''), NULLIF(btrim(concat_ws(' ', c.first_name, c.last_name)), ''),
                              c.company_name) AS contact_name,
                     p.code AS property_code, p.address AS property_address, p.city AS property_city
                FROM appointments a
                LEFT JOIN LATERAL (SELECT vs.id, vs.sheet_version, vs.generated_at, vs.data_snapshot FROM visit_sheets vs
                                    WHERE vs.appointment_id = a.id AND vs.agency_id = a.agency_id
                                    ORDER BY vs.sheet_version DESC LIMIT 1) s ON TRUE
                LEFT JOIN operator_users u ON u.id = a.assigned_user_id
                LEFT JOIN contacts c ON c.id = a.contact_id AND c.agency_id = a.agency_id
                LEFT JOIN properties p ON p.id = a.property_id AND p.agency_id = a.agency_id
               WHERE a.agency_id = %(agency)s AND a.appointment_type = 'buyer_visit'
                 AND (s.id IS NOT NULL OR (a.property_id IS NOT NULL AND a.status IN ('scheduled', 'confirmed', 'completed')
                                           AND (to_jsonb(p)->>'deleted_at') IS NULL))
                 AND (%(solo)s::bigint IS NULL OR a.assigned_user_id = %(solo)s::bigint)
                 AND (%(agent)s::bigint IS NULL OR a.assigned_user_id = %(agent)s::bigint)
                 AND (%(from)s::timestamptz IS NULL OR a.start_at >= %(from)s::timestamptz)
                 AND (%(to)s::timestamptz IS NULL OR a.start_at < %(to)s::timestamptz)
            ) visite
            WHERE %(q)s::text IS NULL
               OR contact_name ILIKE %(q)s OR property_code ILIKE %(q)s OR property_address ILIKE %(q)s
               OR property_city ILIKE %(q)s OR (data_snapshot->'visit'->>'visitor_name') ILIKE %(q)s
            ORDER BY start_at DESC, appointment_id DESC
            LIMIT 500
            """,
            {"agency": agency_id, "solo": solo, "agent": agent_id, "from": date_from, "to": date_to, "q": testo})
        righe = [dict(r) for r in cur.fetchall()]
        ultime = latest_by_sheet(cur, agency_id, [r["sheet_id"] for r in righe if r["sheet_id"] is not None])
        ids = [v["id"] for v in ultime.values()]
        attivita = {}
        if ids:
            cur.execute("SELECT request_id, max(occurred_at) AS at FROM signature_evidence WHERE request_id = ANY(%s) "
                        "GROUP BY request_id", (ids,))
            attivita = {r["request_id"]: r["at"] for r in cur.fetchall()}
        agenti = []
        if solo is None:
            cur.execute("SELECT u.id, NULLIF(btrim(concat_ws(' ', u.first_name, u.last_name)), '') AS name, u.email "
                        "FROM agency_memberships m JOIN operator_users u ON u.id = m.operator_user_id "
                        "WHERE m.agency_id = %s AND m.status = 'active' ORDER BY 2 NULLS LAST, u.id", (agency_id,))
            agenti = [{"id": r["id"], "name": r["name"] or (r["email"] or "").split("@")[0]} for r in cur.fetchall()]
    items = []
    for r in righe:
        v = ultime.get(r["sheet_id"])
        b = brief(v)
        if state is not None and b["state"] != state:
            continue
        snap = r["data_snapshot"] or {}
        visit = snap.get("visit") or {}
        indirizzo = ", ".join(x for x in (r["property_address"], r["property_city"]) if x) or (snap.get("property") or {}).get("address")
        tempi = [t for t in (r["generated_at"],) if t is not None]
        if v is not None:
            tempi += [datetime.fromisoformat(x) for x in (v.get("created_at"), v.get("closed_at")) if x]
            if attivita.get(v["id"]):
                tempi.append(attivita[v["id"]])
        items.append({
            "appointment_id": r["appointment_id"], "sheet_id": r["sheet_id"], "version": r["sheet_version"],
            "visit_start_at": r["start_at"].isoformat(), "visit_status": r["visit_status"],
            "client_name": r["contact_name"] or visit.get("visitor_name"),
            "property_code": r["property_code"], "property_address": indirizzo,
            "agent_id": r["assigned_user_id"], "agent_name": r["agent_name"] or visit.get("agent_name"),
            "has_sheet": r["sheet_id"] is not None,
            "signature": b, "last_activity_at": max(tempi).isoformat() if tempi else None,
        })
    totale = len(items)
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 200))
    return {"items": items[offset:offset + limit], "total": totale, "agents": agenti,
            "states": [{"value": k, "label": v} for k, v in LIST_LABELS.items()]}
