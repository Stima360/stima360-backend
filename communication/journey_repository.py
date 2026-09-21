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
    # `stima_id_snapshot` sta accanto a `stima_id` da P29-3C: il fence e la
    # lettura fresca degli stop cercano i fatti per SNAPSHOT, che sopravvive
    # alla cancellazione della stima, e il service deve poterglielo passare.
    "id", "journey_id", "contact_id", "lead_id", "stima_id", "stima_id_snapshot",
    "trigger_message_id",
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
                      actor_type, actor_user_id, idempotency_key,
                      stop_event_id=None) -> tuple[dict[str, Any], bool]:
    """Nasce `active` (con il primo passo dovuto) oppure `stopped` (audit).

    P29-3C: `stop_event_id` accompagna una nascita gia' ferma quando esiste
    davvero l'evento di timeline che racconta quel fatto. La colonna non e'
    vincolata da nessun CHECK della 071 - la matrice di stato non la nomina -
    quindi scriverla all'INSERT e' esattamente cio' che l'UPDATE di
    `stop_enrollment` fa dopo: stessa colonna, stesso significato, un momento
    prima. Nessuna migration cambia.

    Idempotente sulla chiave; il trigger e lo snapshot li scrive il database.
    """
    agency = ctx.require_agency()
    adesso = utcnow()
    stopped = status == ENR_STOPPED
    cur.execute(
        """
        INSERT INTO communication_enrollments
               (agency_id, journey_id, contact_id, lead_id, stima_id, stima_id_snapshot,
                trigger_message_id, trigger_sent_at, status,
                next_step_no, next_action_at, next_action_kind, awaiting_since, run_no,
                stop_reason, stopped_at, stop_event_id, enrolled_by_type,
                enrolled_by_operator_user_id, idempotency_key)
        VALUES (%s, %s, %s, %s, %s, 0, %s, NOW(), %s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (agency_id, idempotency_key) DO NOTHING
        RETURNING *
        """,
        (agency, journey_id, contact_id, lead_id, stima_id, trigger_message_id, status,
         None if stopped else next_step_no, None if stopped else next_action_at,
         None if stopped else next_action_kind,
         adesso if (not stopped and next_action_kind == KIND_AWAIT_OPERATOR) else None,
         stop_reason if stopped else None, adesso if stopped else None,
         stop_event_id if stopped else None,
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


# ===========================================================================
# P29-3C - IL MOTORE
#
# Le query del tick vivono qui come tutte le altre: il repository e' il posto
# in cui questo modulo scrive SQL, e un motore che se lo scrivesse per conto
# suo avrebbe una seconda opinione su cosa sia una iscrizione aperta.
#
# Tutte prendono le righe con `FOR UPDATE SKIP LOCKED`: due tick simultanei si
# dividono il lavoro invece di aspettarsi. Il lock evita lo SPRECO; a evitare
# il DOPPIONE sono i vincoli della 071, che valgono anche se il lock non c'e'.
# ===========================================================================

#: Le colonne che il motore legge per decidere. Non `*`: una iscrizione ha
#: ventotto colonne e il tick ne usa otto, e nominarle dice quali.
MOTORE_COLUMNS = (
    "id", "journey_id", "contact_id", "lead_id", "stima_id", "stima_id_snapshot",
    "trigger_message_id", "trigger_sent_at", "status", "next_step_no",
    "next_action_at", "next_action_kind", "awaiting_since", "run_no",
)


def schema_ready(cur) -> bool:
    """La 071 e' applicata su QUESTO database?

    UNA query, per giro o per richiesta, e mai una per iscrizione. Il codice
    puo' arrivare in TEST prima della migration - il push fa partire il
    deploy, applicare la 071 e' un gesto separato - e in quella finestra le
    rotte delle journey devono rispondere "non ancora", non rompersi con un
    `UndefinedTable`.

    Non si memorizza il risultato: un esito negativo messo in cache
    sopravviverebbe alla migration e terrebbe la funzione spenta fino al
    riavvio successivo, che e' il modo peggiore di scoprire di aver migrato.
    """
    cur.execute("SELECT to_regclass('public.communication_enrollments') IS NOT NULL AS pronto")
    return bool(cur.fetchone()["pronto"])


def lock_open_enrollments(cur, ctx, *, limit: int) -> list[dict[str, Any]]:
    """Le iscrizioni aperte (active/paused) dell'agenzia, bloccate per il giro."""
    cur.execute(
        f"""SELECT {', '.join(MOTORE_COLUMNS)} FROM communication_enrollments
             WHERE agency_id = %s AND status IN ('active', 'paused')
             ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED""",
        (ctx.require_agency(), limit))
    return [dict(r) for r in cur.fetchall()]


def lock_advanceable_enrollments(cur, ctx, *, limit: int) -> list[dict[str, Any]]:
    """Le iscrizioni attive che stanno aspettando un passo."""
    cur.execute(
        f"""SELECT {', '.join(MOTORE_COLUMNS)} FROM communication_enrollments
             WHERE agency_id = %s AND status = 'active' AND next_step_no IS NOT NULL
             ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED""",
        (ctx.require_agency(), limit))
    return [dict(r) for r in cur.fetchall()]


def lock_due_enrollments(cur, ctx, *, kind: str, now, limit: int) -> list[dict[str, Any]]:
    """Le iscrizioni attive la cui azione e' DOVUTA, per tipo di azione."""
    cur.execute(
        f"""SELECT {', '.join(MOTORE_COLUMNS)} FROM communication_enrollments
             WHERE agency_id = %s AND status = 'active'
               AND next_action_kind = %s AND next_action_at <= %s
             ORDER BY next_action_at, id LIMIT %s FOR UPDATE SKIP LOCKED""",
        (ctx.require_agency(), kind, now, limit))
    return [dict(r) for r in cur.fetchall()]


def advance_enrollment(cur, ctx, enrollment_id: int, *, da_step: int, next_step_no: int,
                       next_action_at, next_action_kind: str) -> dict[str, Any] | None:
    """Il passo successivo, SE l'iscrizione e' ancora a quello di prima.

    `None` quando la condizione non regge: un altro tick l'ha gia' avanzata,
    o un altro atto l'ha chiusa. E' qui che l'avanzamento diventa idempotente
    - non nel lock, che protegge solo dentro una transazione, ma nella
    condizione della UPDATE, che vale sempre.
    """
    cur.execute(
        """UPDATE communication_enrollments
              SET next_step_no = %s, next_action_at = %s, next_action_kind = %s,
                  awaiting_since = %s, updated_at = NOW()
            WHERE id = %s AND agency_id = %s AND status = 'active' AND next_step_no = %s
        RETURNING *""",
        (next_step_no, next_action_at, next_action_kind,
         next_action_at if next_action_kind == KIND_AWAIT_OPERATOR else None,
         enrollment_id, ctx.require_agency(), da_step))
    riga = cur.fetchone()
    return _proietta(riga, ENROLLMENT_COLUMNS) if riga else None


def hand_to_enqueue(cur, ctx, enrollment_id: int, *, step_no: int) -> dict[str, Any] | None:
    """Dopo l'invio assistito: l'iscrizione torna in attesa del ledger.

    Condizionata a `await_operator` sullo STESSO passo, cosi' che il secondo
    di due click non riscriva niente.
    """
    cur.execute(
        """UPDATE communication_enrollments
              SET next_action_kind = 'enqueue', awaiting_since = NULL, updated_at = NOW()
            WHERE id = %s AND agency_id = %s AND status = 'active'
              AND next_step_no = %s AND next_action_kind = 'await_operator'
        RETURNING *""",
        (enrollment_id, ctx.require_agency(), step_no))
    riga = cur.fetchone()
    return _proietta(riga, ENROLLMENT_COLUMNS) if riga else None


def select_journey_message(cur, ctx, enrollment_id: int, *, step_no: int, run_no: int):
    """Il messaggio di QUEL tentativo di passo, se esiste ed e' ancora vivo."""
    cur.execute(
        """SELECT * FROM communication_messages
            WHERE agency_id = %s AND enrollment_id = %s AND step_no = %s AND run_no = %s
              AND status <> 'cancelled'
            ORDER BY id LIMIT 1""",
        (ctx.require_agency(), enrollment_id, step_no, run_no))
    riga = cur.fetchone()
    return dict(riga) if riga else None


def sent_steps(cur, ctx, enrollment_ids: list[int]) -> dict[tuple[int, int, int], Any]:
    """`(enrollment, step, run) -> sent_at` per i passi gia' SPEDITI.

    Una query per tutte le iscrizioni del giro: e' cio' che rende ADVANCE una
    fase e non un ciclo di interrogazioni.
    """
    if not enrollment_ids:
        return {}
    cur.execute(
        """SELECT enrollment_id, step_no, run_no, sent_at FROM communication_messages
            WHERE agency_id = %s AND enrollment_id = ANY(%s) AND status = 'sent'""",
        (ctx.require_agency(), enrollment_ids))
    return {(r["enrollment_id"], r["step_no"], r["run_no"]): r["sent_at"]
            for r in cur.fetchall()}


def active_journeys(cur, ctx, *, trigger_type: str) -> list[dict[str, Any]]:
    cur.execute(
        "SELECT * FROM communication_journeys WHERE agency_id = %s AND status = 'active' "
        "AND trigger_type = %s ORDER BY id", (ctx.require_agency(), trigger_type))
    return [_proietta(r, JOURNEY_COLUMNS) for r in cur.fetchall()]


def candidate_triggers(cur, ctx, *, journey_id: int, activated_at, limit: int) -> list[dict[str, Any]]:
    """Le mail di stima SPEDITE che non hanno ancora una iscrizione a questa journey.

    Il filtro sta NELLA QUERY e non in un ciclo Python, perche' sono gli
    stessi vincoli che la 071 impone e che, letti qui, evitano di provare un
    INSERT destinato a fallire:

      * `sent_at >= activated_at` - nessuna iscrizione storica (P29-3A.1 §C);
      * un contatto e una stima, altrimenti non c'e' niente da iscrivere;
      * il trigger non gia' usato DA QUESTA journey;
      * la stima non gia' usata da questa journey;
      * il contatto senza iscrizioni aperte (la regola "una sola");
      * le automazioni del contatto non in pausa.

    Restano comunque possibili le corse fra due tick: le vince il database,
    non questa SELECT.
    """
    cur.execute(
        """
        SELECT m.id AS trigger_message_id, m.contact_id, m.stima_id, m.lead_id, m.sent_at
          FROM communication_messages m
         WHERE m.agency_id = %(agency)s
           AND m.reason_code = 'stima_pdf' AND m.status = 'sent'
           AND m.contact_id IS NOT NULL AND m.stima_id IS NOT NULL
           AND m.sent_at >= %(cutoff)s
           AND NOT EXISTS (SELECT 1 FROM communication_enrollments e
                            WHERE e.journey_id = %(journey)s
                              AND (e.trigger_message_id = m.id OR e.stima_id = m.stima_id))
           AND NOT EXISTS (SELECT 1 FROM communication_enrollments o
                            WHERE o.agency_id = m.agency_id AND o.contact_id = m.contact_id
                              AND o.status IN ('active', 'paused'))
           AND NOT EXISTS (SELECT 1 FROM communication_automation_controls c
                            WHERE c.agency_id = m.agency_id AND c.contact_id = m.contact_id
                              AND c.paused = TRUE)
         ORDER BY m.sent_at, m.id
         LIMIT %(limite)s
        """,
        {"agency": ctx.require_agency(), "cutoff": activated_at, "journey": journey_id,
         "limite": limit})
    return [dict(r) for r in cur.fetchall()]


def rendering_context(cur, ctx, contact_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Il minimo che serve a comporre un messaggio: chi e', dove, per quale agenzia."""
    if not contact_ids:
        return {}
    cur.execute(
        """SELECT c.id, c.first_name, c.display_name, c.email, a.name AS agency_name
             FROM contacts c JOIN agencies a ON a.id = c.agency_id
            WHERE c.agency_id = %s AND c.id = ANY(%s)""",
        (ctx.require_agency(), contact_ids))
    return {r["id"]: dict(r) for r in cur.fetchall()}


def timeline_event(cur, ctx, *, event_type: str, contact_id: int, stima_id: int | None,
                   payload: dict[str, Any], idempotency_key: str, created_by: str,
                   occurred_at) -> None:
    """Una riga di storia scritta dal motore, con lo stesso helper delle altre."""
    _timeline(cur, agency_id=ctx.require_agency(), event_type=event_type,
              contact_id=contact_id, stima_id=stima_id, payload=payload,
              idempotency_key=idempotency_key, created_by=created_by, occurred_at=occurred_at)


# ---------------------------------------------------------------------------
# P29-3C - I FATTI DI STOP, una query per FAMIGLIA
#
# Nessuna di queste funzioni interroga una iscrizione per volta: ricevono gli
# identificativi del giro e tornano cio' che e' vero per tutti. E' il vincolo
# esplicito del mandato ("niente N+1"), ed e' anche l'unico modo perche' il
# risultato NON dipenda dall'ordine in cui le iscrizioni vengono lette.
#
# TENANCY DI `stima_acquisitions` E `stima_inspections`. Le due tabelle della
# 070 non hanno `agency_id`: la loro appartenenza e' DERIVATA dalla stima e
# dall'immobile, e il trigger della 070 la impone in scrittura. Qui si parte
# dagli snapshot delle iscrizioni di QUESTA agenzia, e `stime.id` e' globale:
# un numero di stima non puo' nominare la stima di un'altra agenzia. Si usa lo
# SNAPSHOT e non il riferimento vivo perche' cancellare una stima azzera
# `stima_id` da entrambe le parti ma lascia intatti i due snapshot - e un
# incarico firmato resta un incarico firmato anche se la stima non c'e' piu'.
# ---------------------------------------------------------------------------

def facts_acquisitions(cur, snapshots: list[int]) -> dict[int, set[str]]:
    """Per snapshot di stima: `acquisition_linked`, e `mandate_signed` se firmato.

    Solo i link ATTIVI: un link revocato non e' un fatto commerciale in corso,
    e fermare una journey per un incarico annullato sarebbe fermarla per un
    fatto che non c'e' piu'.
    """
    if not snapshots:
        return {}
    cur.execute(
        """SELECT stima_id_snapshot AS s,
                  bool_or(mandate_signed_at IS NOT NULL) AS mandato
             FROM stima_acquisitions
            WHERE link_status = 'active' AND stima_id_snapshot = ANY(%s)
            GROUP BY 1""", (snapshots,))
    fatti: dict[int, set[str]] = {}
    for r in cur.fetchall():
        ragioni = {"acquisition_linked"}
        if r["mandato"]:
            ragioni.add("mandate_signed")
        fatti[r["s"]] = ragioni
    return fatti


def facts_inspections(cur, snapshots: list[int]) -> set[int]:
    """Gli snapshot con un sopralluogo in piedi o avvenuto.

    `cancelled` non conta: un sopralluogo annullato e' esattamente il caso in
    cui la journey deve continuare a parlare.
    """
    if not snapshots:
        return set()
    cur.execute(
        """SELECT DISTINCT stima_id_snapshot AS s FROM stima_inspections
            WHERE status IN ('scheduled', 'completed') AND stima_id_snapshot = ANY(%s)""",
        (snapshots,))
    return {r["s"] for r in cur.fetchall()}


def facts_leads_closed(cur, ctx, lead_ids: list[int]) -> set[int]:
    if not lead_ids:
        return set()
    cur.execute("SELECT id FROM leads WHERE agency_id = %s AND id = ANY(%s) AND status = 'closed'",
                (ctx.require_agency(), lead_ids))
    return {r["id"] for r in cur.fetchall()}


def facts_contacts_not_active(cur, ctx, contact_ids: list[int]) -> set[int]:
    """`inactive` e `archived`: un contatto che non e' piu' attivo non riceve."""
    if not contact_ids:
        return set()
    cur.execute("SELECT id FROM contacts WHERE agency_id = %s AND id = ANY(%s) "
                "AND status <> 'active'", (ctx.require_agency(), contact_ids))
    return {r["id"] for r in cur.fetchall()}


def stop_events(cur, ctx, stima_ids: list[int], event_types: tuple[str, ...]):
    """`(stima_id, event_type) -> id dell'evento piu' recente`.

    Serve a due cose diverse: per `consultation_requested` l'evento E' il
    fatto (LMC-9 non ha una tabella di consulenze); per le altre ragioni e'
    solo la riga di storia da mettere in `stop_event_id`, quando esiste.

    Sul riferimento VIVO e non sullo snapshot: `seller_timeline_events.stima_id`
    va a NULL con la stima, e un evento che ha perso il suo soggetto non e'
    piu' attribuibile a nessuno.
    """
    if not stima_ids or not event_types:
        return {}
    cur.execute(
        """SELECT DISTINCT ON (stima_id, event_type) stima_id, event_type, id
             FROM seller_timeline_events
            WHERE agency_id = %s AND stima_id = ANY(%s) AND event_type = ANY(%s)
            ORDER BY stima_id, event_type, occurred_at DESC, id DESC""",
        (ctx.require_agency(), stima_ids, list(event_types)))
    return {(r["stima_id"], r["event_type"]): r["id"] for r in cur.fetchall()}


def message_provenance(cur, *, agency_id: int, enrollment_id: int, step_no: int):
    """Chi era il passo che ha prodotto QUESTO messaggio: `(chiave, versione, passo)`.

    Prende `agency_id` e non un `ctx` perche' il chiamante e' la
    finalizzazione del dispatcher, che possiede la riga e la sua agenzia -
    non una sessione. Il vincolo di tenancy resta scritto nella WHERE.
    """
    cur.execute(
        """SELECT j.journey_key, j.version AS journey_version, s.step_key
             FROM communication_enrollments e
             JOIN communication_journeys j ON j.id = e.journey_id
             LEFT JOIN communication_journey_steps s
                    ON s.journey_id = e.journey_id AND s.step_no = %s
            WHERE e.id = %s AND e.agency_id = %s""",
        (step_no, enrollment_id, agency_id))
    riga = cur.fetchone()
    return dict(riga) if riga else None


# ===========================================================================
# P29-3C - IL FENCE, e la lettura FRESCA degli stop
#
# IL CONTRATTO, in una riga: uno stop che ha COMMITTATO prima che noi
# prendessimo il fence deve vincere; se il fence lo prendiamo prima noi, noi
# finiamo di scrivere e lo scrittore dello stop aspetta.
#
# Il bulk che precede resta ed e' utile: scarta in fretta il 99% dei casi.
# Non basta pero' a decidere, perche' fra la lettura in blocco e la scrittura
# c'e' un intervallo in cui un incarico puo' essere firmato. La decisione si
# prende con le righe BLOCCATE in mano, e la scrittura avviene nella stessa
# transazione, prima di rilasciarle.
#
# ORDINE DEI LOCK, SEMPRE QUESTO: contatto, poi lead, poi stima. Un ordine
# fisso e' cio' che rende impossibile il deadlock fra due percorsi che
# prendono gli stessi oggetti. Gli scrittori degli stop prendono un
# SOTTOINSIEME di questi, nello stesso ordine relativo - il consenso il solo
# contatto (`consent.repository.lock_contact`), acquisizioni, sopralluoghi e
# consulenze la sola stima - quindi nessun ciclo puo' formarsi.
# ===========================================================================

def fence(cur, ctx, *, contact_id: int, lead_id: int | None, stima_id: int | None) -> None:
    """Blocca contatto, lead e stima - in QUEST'ORDINE - fino al commit.

    `FOR UPDATE` e non `SHARE`: gli scrittori degli stop aggiornano proprio
    queste righe (o le bloccano esplicitamente), quindi un lock condiviso li
    lascerebbe passare. Nessuna riga mancante e' un errore: una stima
    cancellata o un lead assente semplicemente non hanno niente da bloccare.
    """
    agency = ctx.require_agency()
    cur.execute("SELECT id FROM contacts WHERE id = %s AND agency_id = %s FOR UPDATE",
                (contact_id, agency))
    if lead_id is not None:
        cur.execute("SELECT id FROM leads WHERE id = %s AND agency_id = %s FOR UPDATE",
                    (lead_id, agency))
    if stima_id is not None:
        cur.execute("SELECT id FROM stime WHERE id = %s AND agency_id = %s FOR UPDATE",
                    (stima_id, agency))


def fresh_stop(cur, ctx, *, contact_id: int, lead_id: int | None, stima_id: int | None,
               stima_snapshot: int | None, priorita: tuple[str, ...],
               tipi_evento: dict[str, tuple[str, ...]], nomi: dict[str, str]):
    """La ragione di stop VINCENTE adesso, e il suo evento. UNA statement.

    Una sola query, e non sei: sotto READ COMMITTED ogni statement vede uno
    snapshot suo, quindi sei SELECT consecutive potrebbero raccontare sei
    istanti diversi. Con una sola, cio' che si legge e' coerente per
    costruzione - e con il fence gia' preso, e' anche cio' che vale fino al
    commit.

    NESSUN LETTERALE DI DOMINIO NEL SQL: i nomi delle ragioni arrivano in
    `nomi`, l'ordine in `priorita` (che e' `STOP_PRIORITY`, l'unica fonte) e
    la corrispondenza ragione -> tipo di evento in `tipi_evento`. Cambiare
    l'ordine di priorita' si fa in un posto solo, e questa query lo segue.

    Il consenso NON e' qui: la sua decisione e' `consent.guard`, e
    riscriverla in SQL sarebbe la seconda implementazione che il mandato
    vieta. La si interroga subito dopo, con il fence gia' preso - il
    contatto e' bloccato, quindi nessuna revoca puo' atterrare nel mezzo.

    Restituisce `(ragione, stop_event_id)` oppure `(None, None)`.
    """
    agency = ctx.require_agency()
    coppie = [(r, t) for r in priorita for t in tipi_evento.get(r, ())]
    parametri = {
        "agency": agency, "contact": contact_id, "lead": lead_id,
        "stima": stima_id, "snapshot": stima_snapshot,
        "ordine": list(priorita),
        "ev_ragione": [r for r, _ in coppie],
        "ev_tipo": [t for _, t in coppie],
        "r_mandato": nomi["mandate_signed"],
        "r_acquisizione": nomi["acquisition_linked"],
        "r_sopralluogo": nomi["inspection"],
        "r_consulenza": nomi["consultation_requested"],
        "r_lead": nomi["lead_closed"],
        "r_contatto": nomi["contact_inactive"],
        "tipo_consulenza": tipi_evento[nomi["consultation_requested"]][0],
    }
    cur.execute(
        """
        WITH prio(reason, rank) AS (
            SELECT valore, n FROM unnest(%(ordine)s::text[]) WITH ORDINALITY AS t(valore, n)
        ),
        tipi(reason, event_type, ord) AS (
            SELECT r, e, n FROM unnest(%(ev_ragione)s::text[], %(ev_tipo)s::text[])
                 WITH ORDINALITY AS t(r, e, n)
        ),
        fatti(reason) AS (
            SELECT %(r_mandato)s::text WHERE EXISTS (
                SELECT 1 FROM stima_acquisitions a
                 WHERE a.link_status = 'active' AND a.stima_id_snapshot = %(snapshot)s
                   AND a.mandate_signed_at IS NOT NULL)
            UNION ALL
            SELECT %(r_acquisizione)s::text WHERE EXISTS (
                SELECT 1 FROM stima_acquisitions a
                 WHERE a.link_status = 'active' AND a.stima_id_snapshot = %(snapshot)s)
            UNION ALL
            SELECT %(r_sopralluogo)s::text WHERE EXISTS (
                SELECT 1 FROM stima_inspections i
                 WHERE i.status IN ('scheduled', 'completed')
                   AND i.stima_id_snapshot = %(snapshot)s)
            UNION ALL
            SELECT %(r_consulenza)s::text WHERE EXISTS (
                SELECT 1 FROM seller_timeline_events e
                 WHERE e.agency_id = %(agency)s AND e.stima_id = %(stima)s
                   AND e.event_type = %(tipo_consulenza)s)
            UNION ALL
            SELECT %(r_lead)s::text WHERE EXISTS (
                SELECT 1 FROM leads l
                 WHERE l.agency_id = %(agency)s AND l.id = %(lead)s AND l.status = 'closed')
            UNION ALL
            SELECT %(r_contatto)s::text WHERE EXISTS (
                SELECT 1 FROM contacts c
                 WHERE c.agency_id = %(agency)s AND c.id = %(contact)s AND c.status <> 'active')
        ),
        vincente AS (
            SELECT f.reason FROM fatti f JOIN prio p ON p.reason = f.reason
             ORDER BY p.rank LIMIT 1
        )
        SELECT v.reason,
               (SELECT e.id FROM seller_timeline_events e
                  JOIN tipi t ON t.event_type = e.event_type AND t.reason = v.reason
                 WHERE e.agency_id = %(agency)s AND e.stima_id = %(stima)s
                 ORDER BY t.ord, e.occurred_at DESC, e.id DESC LIMIT 1) AS stop_event_id
          FROM vincente v
        """,
        parametri)
    riga = cur.fetchone()
    return (riga["reason"], riga["stop_event_id"]) if riga else (None, None)
