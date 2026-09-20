"""P29-3B.2A - le scritture del motore delle journey. Fondazione: primitive, non tick.

TRE REGOLE, LE STESSE DI OGNI REPOSITORY DI QUESTO PROGETTO

L'agenzia e' `ctx.require_agency()` e nient'altro: ogni statement la nomina.
L'attore e' un parametro derivato dalla sessione dal service, mai dal client.
Ogni funzione prende il cursore del chiamante: la transazione e' sua.

LA MATRICE DI STATO E' NEL DATABASE. Qui si scrivono TUTTI i campi di stato a
ogni transizione - quelli che si azzerano compresi - cosi' che una transizione
scritta a meta' sia rifiutata dal CHECK e non arrivi mai a raccontare uno
stato che non esiste.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from psycopg2.extras import Json

from .exceptions import ConflictError, NotFoundError
from seller_intelligence import repository as si_repository

from .journey_enums import (
    ENR_ACTIVE, ENR_COMPLETED, ENR_PAUSED, ENR_STOPPED, KIND_AWAIT_OPERATOR,
    KIND_ENQUEUE, OPEN_STATUSES,
)

EVENT_SOURCE = "crm_automation"
EVENT_PAUSED = "automation_paused"
EVENT_RESUMED = "automation_resumed"
EVENT_ENROLLED = "journey_enrolled"
EVENT_STOPPED = "journey_stopped"

JOURNEY_COLUMNS = (
    "id", "journey_key", "version", "trigger_type", "status", "name", "send_timezone",
    "created_by_type", "activated_at", "activated_by_type", "retired_at", "created_at",
)
STEP_COLUMNS = (
    "id", "step_no", "step_key", "reason_code", "channel", "communication_type",
    "default_mode", "delay_from", "delay_seconds", "send_window", "template_key",
    "template_version", "stop_on", "active",
)
ENROLLMENT_COLUMNS = (
    "id", "journey_id", "contact_id", "lead_id", "stima_id", "trigger_message_id",
    "trigger_sent_at", "status", "next_step_no", "next_action_at", "next_action_kind",
    "awaiting_since", "run_no", "stop_reason", "enrolled_by_type", "paused_at",
    "paused_source", "stopped_at", "completed_at", "created_at", "updated_at",
)
CONTROL_COLUMNS = ("id", "contact_id", "paused", "paused_at", "pause_reason",
                   "resumed_at", "created_at", "updated_at")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _proietta(riga, colonne) -> dict[str, Any]:
    return {c: riga[c] for c in colonne}


def _timeline(cur, *, agency_id, event_type, contact_id, stima_id, payload,
              idempotency_key, created_by, occurred_at):
    """Una riga di storia, nello stesso cursore del fatto (come LMC-15)."""
    dati = {"contact_id": contact_id, "lead_id": None, "stima_id": stima_id,
            "property_id": None, "event_type": event_type, "event_source": EVENT_SOURCE,
            "occurred_at": occurred_at, "payload": payload,
            "idempotency_key": idempotency_key, "created_by": created_by}
    si_repository._assert_references_in_agency(cur, dati, agency_id)
    si_repository._insert_event_with_agency(cur, dati, agency_id)


# ---------------------------------------------------------------------------
# JOURNEYS
# ---------------------------------------------------------------------------

def insert_journey(cur, ctx, *, journey_key, version, trigger_type, name, send_timezone,
                   actor_type, actor_user_id, steps) -> dict[str, Any]:
    agency = ctx.require_agency()
    cur.execute(
        """
        INSERT INTO communication_journeys
               (agency_id, journey_key, version, trigger_type, status, name, send_timezone,
                created_by_type, created_by_operator_user_id)
        VALUES (%s, %s, %s, %s, 'draft', %s, %s, %s, %s)
        RETURNING *
        """,
        (agency, journey_key, version, trigger_type, name, send_timezone,
         actor_type, actor_user_id),
    )
    journey = _proietta(cur.fetchone(), JOURNEY_COLUMNS)
    for passo in steps:
        cur.execute(
            """
            INSERT INTO communication_journey_steps
                   (journey_id, step_no, step_key, reason_code, channel, communication_type,
                    default_mode, delay_from, delay_seconds, send_window,
                    template_key, template_version, stop_on)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (journey["id"], passo["step_no"], passo["step_key"], passo["reason_code"],
             passo["channel"], passo["communication_type"], passo["default_mode"],
             passo["delay_from"], passo["delay_seconds"], Json(passo.get("send_window") or {}),
             passo["template_key"], passo["template_version"], Json(list(passo.get("stop_on") or []))),
        )
    return journey


def select_journey(cur, ctx, journey_id: int) -> dict[str, Any]:
    cur.execute("SELECT * FROM communication_journeys WHERE id = %s AND agency_id = %s",
                (journey_id, ctx.require_agency()))
    riga = cur.fetchone()
    if riga is None:
        raise NotFoundError("Risorsa non trovata")
    return _proietta(riga, JOURNEY_COLUMNS)


def select_active_journey(cur, ctx, journey_key: str) -> dict[str, Any] | None:
    cur.execute("SELECT * FROM communication_journeys WHERE agency_id = %s AND journey_key = %s "
                "AND status = 'active'", (ctx.require_agency(), journey_key))
    riga = cur.fetchone()
    return _proietta(riga, JOURNEY_COLUMNS) if riga else None


def list_steps(cur, ctx, journey_id: int) -> list[dict[str, Any]]:
    cur.execute(
        """SELECT s.* FROM communication_journey_steps s
             JOIN communication_journeys j ON j.id = s.journey_id
            WHERE s.journey_id = %s AND j.agency_id = %s
            ORDER BY s.step_no""", (journey_id, ctx.require_agency()))
    return [_proietta(r, STEP_COLUMNS) for r in cur.fetchall()]


def activate_journey(cur, ctx, journey_id: int, *, actor_type, actor_user_id) -> dict[str, Any]:
    """`draft -> active`. Ritira da sola la versione attiva precedente della
    stessa chiave: una sola attiva per chiave, e il passaggio e' un atto solo."""
    agency = ctx.require_agency()
    corrente = select_journey(cur, ctx, journey_id)
    if corrente["status"] != "draft":
        raise ConflictError(f"journey {journey_id} is {corrente['status']}, not draft")
    adesso = utcnow()
    cur.execute(
        """UPDATE communication_journeys SET status = 'retired', retired_at = %s, updated_at = %s
            WHERE agency_id = %s AND journey_key = %s AND status = 'active'""",
        (adesso, adesso, agency, corrente["journey_key"]))
    cur.execute(
        """UPDATE communication_journeys
              SET status = 'active', activated_at = %s, activated_by_type = %s,
                  activated_by_operator_user_id = %s, updated_at = %s
            WHERE id = %s AND agency_id = %s AND status = 'draft'
            RETURNING *""",
        (adesso, actor_type, actor_user_id, adesso, journey_id, agency))
    riga = cur.fetchone()
    if riga is None:
        raise ConflictError(f"journey {journey_id} could not be activated")
    return _proietta(riga, JOURNEY_COLUMNS)


def retire_journey(cur, ctx, journey_id: int) -> dict[str, Any]:
    adesso = utcnow()
    cur.execute(
        """UPDATE communication_journeys SET status = 'retired', retired_at = %s, updated_at = %s
            WHERE id = %s AND agency_id = %s AND status = 'active' RETURNING *""",
        (adesso, adesso, journey_id, ctx.require_agency()))
    riga = cur.fetchone()
    if riga is None:
        select_journey(cur, ctx, journey_id)
        raise ConflictError(f"journey {journey_id} is not active")
    return _proietta(riga, JOURNEY_COLUMNS)


# ---------------------------------------------------------------------------
# AUTOMATION CONTROLS (stato corrente per contatto)
# ---------------------------------------------------------------------------

def select_control(cur, ctx, contact_id: int) -> dict[str, Any] | None:
    cur.execute("SELECT * FROM communication_automation_controls "
                "WHERE agency_id = %s AND contact_id = %s FOR UPDATE",
                (ctx.require_agency(), contact_id))
    riga = cur.fetchone()
    return _proietta(riga, CONTROL_COLUMNS) if riga else None


def automations_paused(cur, ctx, contact_id: int) -> bool:
    cur.execute("SELECT paused FROM communication_automation_controls "
                "WHERE agency_id = %s AND contact_id = %s", (ctx.require_agency(), contact_id))
    riga = cur.fetchone()
    return bool(riga and riga["paused"])


def upsert_control_paused(cur, ctx, contact_id: int, *, actor_user_id: int, reason: str | None,
                          stima_id: int | None) -> dict[str, Any]:
    agency = ctx.require_agency()
    adesso = utcnow()
    cur.execute(
        """
        INSERT INTO communication_automation_controls
               (agency_id, contact_id, paused, paused_at, paused_by_operator_user_id, pause_reason,
                resumed_at, resumed_by_operator_user_id, updated_at)
        VALUES (%s, %s, TRUE, %s, %s, %s, NULL, NULL, %s)
        ON CONFLICT (agency_id, contact_id) DO UPDATE
           SET paused = TRUE, paused_at = EXCLUDED.paused_at,
               paused_by_operator_user_id = EXCLUDED.paused_by_operator_user_id,
               pause_reason = EXCLUDED.pause_reason,
               resumed_at = NULL, resumed_by_operator_user_id = NULL,
               updated_at = EXCLUDED.updated_at
         WHERE communication_automation_controls.paused = FALSE
        RETURNING *
        """,
        (agency, contact_id, adesso, actor_user_id, reason, adesso))
    riga = cur.fetchone()
    if riga is None:
        raise ConflictError("automations are already paused for this contact")
    _timeline(cur, agency_id=agency, event_type=EVENT_PAUSED, contact_id=contact_id,
              stima_id=stima_id, payload={"reason": reason},
              idempotency_key=f"p29_3:v1:{EVENT_PAUSED}:contact:{contact_id}:at:{adesso.isoformat()}",
              created_by=str(actor_user_id), occurred_at=adesso)
    return _proietta(riga, CONTROL_COLUMNS)


def set_control_resumed(cur, ctx, contact_id: int, *, actor_user_id: int) -> dict[str, Any]:
    agency = ctx.require_agency()
    adesso = utcnow()
    cur.execute(
        """
        UPDATE communication_automation_controls
           SET paused = FALSE, paused_at = NULL, paused_by_operator_user_id = NULL,
               pause_reason = NULL, resumed_at = %s, resumed_by_operator_user_id = %s,
               updated_at = %s
         WHERE agency_id = %s AND contact_id = %s AND paused = TRUE
        RETURNING *
        """,
        (adesso, actor_user_id, adesso, agency, contact_id))
    riga = cur.fetchone()
    if riga is None:
        raise ConflictError("automations are not paused for this contact")
    _timeline(cur, agency_id=agency, event_type=EVENT_RESUMED, contact_id=contact_id,
              stima_id=None, payload={},
              idempotency_key=f"p29_3:v1:{EVENT_RESUMED}:contact:{contact_id}:at:{adesso.isoformat()}",
              created_by=str(actor_user_id), occurred_at=adesso)
    return _proietta(riga, CONTROL_COLUMNS)


# ---------------------------------------------------------------------------
# ENROLLMENTS
# ---------------------------------------------------------------------------

def select_enrollment(cur, ctx, enrollment_id: int, *, for_update: bool = False) -> dict[str, Any]:
    cur.execute(
        "SELECT * FROM communication_enrollments WHERE id = %s AND agency_id = %s"
        + (" FOR UPDATE" if for_update else ""),
        (enrollment_id, ctx.require_agency()))
    riga = cur.fetchone()
    if riga is None:
        raise NotFoundError("Risorsa non trovata")
    return _proietta(riga, ENROLLMENT_COLUMNS)


def select_open_enrollment(cur, ctx, contact_id: int) -> dict[str, Any] | None:
    cur.execute(
        "SELECT * FROM communication_enrollments WHERE agency_id = %s AND contact_id = %s "
        "AND status IN ('active', 'paused')", (ctx.require_agency(), contact_id))
    riga = cur.fetchone()
    return _proietta(riga, ENROLLMENT_COLUMNS) if riga else None


def insert_enrollment(cur, ctx, *, journey_id, contact_id, lead_id, stima_id, trigger_message_id,
                      status, next_step_no, next_action_at, next_action_kind, stop_reason,
                      actor_type, actor_user_id, idempotency_key) -> tuple[dict[str, Any], bool]:
    """Nasce `active` (con il primo passo dovuto) oppure `stopped` (consenso
    negato, audit). Idempotente sulla chiave; il trigger e lo snapshot li
    scrive il database."""
    agency = ctx.require_agency()
    adesso = utcnow()
    stopped = status == ENR_STOPPED
    cur.execute(
        """
        INSERT INTO communication_enrollments
               (agency_id, journey_id, contact_id, lead_id, stima_id, stima_id_snapshot,
                trigger_message_id, trigger_sent_at, status,
                next_step_no, next_action_at, next_action_kind, awaiting_since, run_no,
                stop_reason, stopped_at, enrolled_by_type, enrolled_by_operator_user_id,
                idempotency_key)
        VALUES (%s, %s, %s, %s, %s, 0, %s, NOW(), %s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s)
        ON CONFLICT (agency_id, idempotency_key) DO NOTHING
        RETURNING *
        """,
        (agency, journey_id, contact_id, lead_id, stima_id, trigger_message_id, status,
         None if stopped else next_step_no, None if stopped else next_action_at,
         None if stopped else next_action_kind,
         adesso if (not stopped and next_action_kind == KIND_AWAIT_OPERATOR) else None,
         stop_reason if stopped else None, adesso if stopped else None,
         actor_type, actor_user_id, idempotency_key))
    riga = cur.fetchone()
    if riga is not None:
        enrollment = _proietta(riga, ENROLLMENT_COLUMNS)
        _timeline(cur, agency_id=agency, event_type=EVENT_STOPPED if stopped else EVENT_ENROLLED,
                  contact_id=contact_id, stima_id=stima_id,
                  payload={"enrollment_id": enrollment["id"], "journey_id": journey_id,
                           **({"stop_reason": stop_reason} if stopped else {})},
                  idempotency_key=f"p29_3:v1:{'stopped' if stopped else 'enrolled'}:enr:{enrollment['id']}",
                  created_by=str(actor_user_id) if actor_user_id else "system",
                  occurred_at=adesso)
        return enrollment, True
    cur.execute("SELECT * FROM communication_enrollments WHERE agency_id = %s AND idempotency_key = %s",
                (agency, idempotency_key))
    return _proietta(cur.fetchone(), ENROLLMENT_COLUMNS), False


def _transizione(cur, ctx, enrollment_id: int, *, da: tuple[str, ...], assegnazioni: str,
                 valori: tuple) -> dict[str, Any]:
    """UPDATE condizionato allo stato di partenza; 409 se non c'e'."""
    agency = ctx.require_agency()
    cur.execute(
        f"""UPDATE communication_enrollments SET {assegnazioni}, updated_at = NOW()
             WHERE id = %s AND agency_id = %s AND status = ANY(%s)
         RETURNING *""",
        (*valori, enrollment_id, agency, list(da)))
    riga = cur.fetchone()
    if riga is None:
        corrente = select_enrollment(cur, ctx, enrollment_id)
        raise ConflictError(
            f"enrollment {enrollment_id} is {corrente['status']}: transition not allowed")
    return _proietta(riga, ENROLLMENT_COLUMNS)


def pause_enrollment(cur, ctx, enrollment_id: int, *, actor_user_id: int, source: str):
    return _transizione(
        cur, ctx, enrollment_id, da=(ENR_ACTIVE,),
        assegnazioni="status = 'paused', paused_at = NOW(), paused_source = %s, "
                     "paused_by_operator_user_id = %s",
        valori=(source, actor_user_id))


def resume_enrollment(cur, ctx, enrollment_id: int, *, next_action_at) -> dict[str, Any]:
    """`paused -> active`, run successivo, azione ricalcolata dal service."""
    return _transizione(
        cur, ctx, enrollment_id, da=(ENR_PAUSED,),
        assegnazioni="status = 'active', paused_at = NULL, paused_source = NULL, "
                     "paused_by_operator_user_id = NULL, run_no = run_no + 1, next_action_at = %s",
        valori=(next_action_at,))


def stop_enrollment(cur, ctx, enrollment_id: int, *, reason: str, actor_user_id: int | None,
                    stop_event_id: int | None = None) -> dict[str, Any]:
    return _transizione(
        cur, ctx, enrollment_id, da=(ENR_ACTIVE, ENR_PAUSED),
        assegnazioni="status = 'stopped', stopped_at = NOW(), stop_reason = %s, "
                     "stopped_by_operator_user_id = %s, stop_event_id = %s, "
                     "next_step_no = NULL, next_action_at = NULL, next_action_kind = NULL, "
                     "awaiting_since = NULL, paused_at = NULL, paused_source = NULL, "
                     "paused_by_operator_user_id = NULL",
        valori=(reason, actor_user_id, stop_event_id))


def complete_enrollment(cur, ctx, enrollment_id: int) -> dict[str, Any]:
    return _transizione(
        cur, ctx, enrollment_id, da=(ENR_ACTIVE,),
        assegnazioni="status = 'completed', completed_at = NOW(), next_step_no = NULL, "
                     "next_action_at = NULL, next_action_kind = NULL, awaiting_since = NULL",
        valori=())


def list_open_enrollments_for_contact(cur, ctx, contact_id: int) -> list[dict[str, Any]]:
    cur.execute("SELECT * FROM communication_enrollments WHERE agency_id = %s AND contact_id = %s "
                "AND status IN ('active','paused') FOR UPDATE",
                (ctx.require_agency(), contact_id))
    return [_proietta(r, ENROLLMENT_COLUMNS) for r in cur.fetchall()]


def cancel_queued_journey_messages(cur, ctx, enrollment_id: int, *, reason: str,
                                   actor_user_id: int | None) -> int:
    """I `queued` di QUESTA iscrizione -> `cancelled`, con la ragione nei
    metadata. Non tocca i messaggi manuali (che non hanno enrollment_id)."""
    cur.execute(
        """
        UPDATE communication_messages
           SET status = 'cancelled', updated_at = NOW(),
               metadata = metadata || %s::jsonb
         WHERE agency_id = %s AND enrollment_id = %s AND status = 'queued'
        """,
        (json.dumps({"cancel_reason": reason,
                     "cancelled_by": str(actor_user_id) if actor_user_id else "system"}),
         ctx.require_agency(), enrollment_id))
    return cur.rowcount
