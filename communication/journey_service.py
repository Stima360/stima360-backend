"""P29-3B.2A - il servizio delle journey: primitive core, nessun tick.

COSA C'E'. Provisioning e attivazione di una journey; iscrizione da un
trigger ESPLICITO (la mail della stima, spedita); iscrizione gia' `stopped`
quando il consenso nega; pausa/ripresa/stop di una iscrizione; pausa/ripresa
delle automazioni di un CONTATTO; lettura dell'iscrizione aperta e del
prossimo passo.

COSA NON C'E', DI PROPOSITO. Nessuno scanner che trovi le stime da iscrivere,
nessun avanzamento automatico, nessun `enqueue` di M1: sono il tick (3B.3), e
un tick senza il suo gate e' un cron che manda email.

L'ATTORE VIENE DALLA SESSIONE. Ogni primitiva "operatore" legge `ctx.user_id`;
se non c'e' una persona (canale Basic legacy, contesto di sistema) l'atto
viene rifiutato. Le primitive "sistema" prendono un `SystemAgencyContext` e
firmano `system`. Nessuna funzione accetta un `agency_id`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from consent.guard import can_send_marketing
from core.exceptions import PermissionDenied

from .exceptions import ConflictError, NotFoundError, ValidationError

from . import journey_repository as repo
from . import send_window
from . import templates
from .database import communication_cursor
from .journey_enums import (
    ACTOR_OPERATOR, ACTOR_SYSTEM, CONSENT_STOP_BY_GUARD_REASON, DELAY_FROM, ENR_ACTIVE,
    ENR_PAUSED, ENR_STOPPED, KIND_AWAIT_OPERATOR, KIND_ENQUEUE, PAUSED_BY_CONTACT_CONTROL,
    PAUSED_BY_ENROLLMENT, STEP_MODES, STEP_REASON_CODES, STOP_REASONS, TRIGGER_TYPES,
    choose_stop_reason,
)
from .repository import contact_in_scope

#: Una iscrizione in pausa da piu' di tanto non riprende: si ferma con
#: `expired_on_resume` (P29-3A.1 §E). Misurato dal `next_action_at`
#: originale, non dalla pausa.
MAX_RESUME_AGE = timedelta(days=30)


# ---------------------------------------------------------------------------
# attore
# ---------------------------------------------------------------------------

def operatore(ctx) -> int:
    """L'operatore della sessione, o un rifiuto. Pubblica da P29-3C: il
    motore delle journey fa gli stessi atti da operatore (invio assistito,
    salto di un passo) e deve porre la stessa domanda, non una simile."""
    user_id = getattr(ctx, "user_id", None)
    if user_id is None:
        raise PermissionDenied("this action requires an authenticated operator session")
    return int(user_id)


def _attore(ctx) -> tuple[str, int | None]:
    """`(actor_type, actor_user_id)`: una persona se c'e', altrimenti il sistema."""
    user_id = getattr(ctx, "user_id", None)
    return (ACTOR_OPERATOR, int(user_id)) if user_id is not None else (ACTOR_SYSTEM, None)


def cursore(cur):
    """La transazione: quella del chiamante se la presta, altrimenti la
    propria. Pubblica da P29-3C per la stessa ragione di `operatore`."""
    if cur is not None:
        class _Prestato:
            def __enter__(self):
                return None, cur
            def __exit__(self, *a):
                return False
        return _Prestato()
    return communication_cursor(commit=True)


# ---------------------------------------------------------------------------
# JOURNEY
# ---------------------------------------------------------------------------

def _valida_passi(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not steps:
        raise ValidationError("a journey needs at least one step")
    numeri = [s.get("step_no") for s in steps]
    if numeri != list(range(1, len(steps) + 1)):
        raise ValidationError("step_no must be 1..n without gaps")
    for s in steps:
        if s.get("reason_code") not in STEP_REASON_CODES:
            raise ValidationError(f"step {s.get('step_no')}: reason_code must be one of {STEP_REASON_CODES}")
        if s.get("default_mode") not in STEP_MODES:
            raise ValidationError(f"step {s.get('step_no')}: default_mode must be one of {sorted(STEP_MODES)}")
        if s.get("delay_from") not in DELAY_FROM:
            raise ValidationError(f"step {s.get('step_no')}: delay_from must be one of {sorted(DELAY_FROM)}")
        if not isinstance(s.get("delay_seconds"), int) or s["delay_seconds"] < 0:
            raise ValidationError(f"step {s.get('step_no')}: delay_seconds must be a non-negative integer")
        sconosciute = set(s.get("stop_on") or []) - STOP_REASONS
        if sconosciute:
            raise ValidationError(f"step {s.get('step_no')}: unknown stop reasons {sorted(sconosciute)}")
        # P29-3C: la finestra di invio si valida QUI. Una finestra malformata
        # scoperta dal tick sarebbe un passo che non parte in un orario in cui
        # nessuno guarda; scoperta al provisioning e' una journey che non
        # nasce.
        try:
            s["send_window"] = send_window.validate(s.get("send_window"))
        except ValidationError as exc:
            raise ValidationError(f"step {s.get('step_no')}: {exc}") from None
        # Il template deve ESISTERE nel registro, con quel canale e quel tipo:
        # una journey che nomina un template che nessuno ha approvato non si
        # crea nemmeno come bozza.
        t = templates.get(s.get("template_key"), s.get("template_version"))
        if t.channel != s.get("channel") or t.communication_type != s.get("communication_type"):
            raise ValidationError(
                f"step {s.get('step_no')}: template {t.key!r} v{t.version} is "
                f"{t.channel}/{t.communication_type}, the step says "
                f"{s.get('channel')}/{s.get('communication_type')}")
    return steps


def provision_journey(ctx, *, journey_key: str, version: int, trigger_type: str, name: str,
                      steps: list[dict[str, Any]], send_timezone: str = "Europe/Rome",
                      cur=None) -> dict[str, Any]:
    """Crea una journey in `draft`. Dal sistema o da un operatore: lo dice `ctx`."""
    if trigger_type not in TRIGGER_TYPES:
        raise ValidationError(f"trigger_type must be one of {sorted(TRIGGER_TYPES)}")
    try:
        ZoneInfo(send_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValidationError(f"send_timezone {send_timezone!r} is not a known IANA zone") from None
    passi = _valida_passi(steps)
    actor_type, actor_user_id = _attore(ctx)
    with cursore(cur) as (_, c):
        return repo.insert_journey(
            c, ctx, journey_key=journey_key, version=version, trigger_type=trigger_type,
            name=name, send_timezone=send_timezone, actor_type=actor_type,
            actor_user_id=actor_user_id, steps=passi)


def activate_journey(ctx, journey_id: int, *, cur=None) -> dict[str, Any]:
    actor_type, actor_user_id = _attore(ctx)
    with cursore(cur) as (_, c):
        return repo.activate_journey(c, ctx, journey_id, actor_type=actor_type,
                                     actor_user_id=actor_user_id)


def retire_journey(ctx, journey_id: int, *, cur=None) -> dict[str, Any]:
    with cursore(cur) as (_, c):
        return repo.retire_journey(c, ctx, journey_id)


def get_journey(ctx, journey_id: int, *, cur=None) -> dict[str, Any]:
    with cursore(cur) as (_, c):
        j = repo.select_journey(c, ctx, journey_id)
        j["steps"] = repo.list_steps(c, ctx, journey_id)
        return j


# ---------------------------------------------------------------------------
# ENROLLMENT
# ---------------------------------------------------------------------------

def _prima_azione(journey: dict[str, Any], steps: list[dict[str, Any]],
                  trigger_sent_at: datetime) -> tuple[int, datetime, str]:
    primo = steps[0]
    quando = trigger_sent_at + timedelta(seconds=primo["delay_seconds"])
    kind = KIND_AWAIT_OPERATOR if primo["default_mode"] == "assisted" else KIND_ENQUEUE
    return primo["step_no"], quando, kind


def enroll_from_trigger(ctx, *, journey_id: int, contact_id: int, trigger_message_id: int,
                        lead_id: int | None = None, cur=None) -> dict[str, Any]:
    """Iscrive un contatto a partire da un messaggio `stima_pdf` `sent`.

    Rifiuta se le automazioni del contatto sono in pausa, se esiste gia' una
    iscrizione aperta, se la journey non e' attiva o se il fatto e' anteriore
    alla sua attivazione. Se il consenso marketing nega, l'iscrizione nasce
    GIA' `stopped` con la ragione del consenso: e' l'opzione B di P29-3A.1,
    una riga che spiega perche' non e' partito niente.

    `trigger_sent_at` e lo snapshot della stima li scrive il database.
    Restituisce ``{'enrollment': riga, 'created': bool}``.
    """
    actor_type, actor_user_id = _attore(ctx)
    with cursore(cur) as (_, c):
        agency = ctx.require_agency()
        contact_in_scope(c, ctx, contact_id)
        journey = repo.select_journey(c, ctx, journey_id)
        if journey["status"] != "active":
            raise ConflictError(f"journey {journey_id} is not active")
        steps = [s for s in repo.list_steps(c, ctx, journey_id) if s["active"]]
        if not steps:
            raise ConflictError(f"journey {journey_id} has no active steps")
        c.execute(
            """SELECT id, contact_id, stima_id, lead_id, sent_at, reason_code, status
                 FROM communication_messages WHERE id = %s AND agency_id = %s""",
            (trigger_message_id, agency))
        trigger = c.fetchone()
        if trigger is None:
            raise NotFoundError("Risorsa non trovata")
        if trigger["reason_code"] != "stima_pdf" or trigger["status"] != "sent":
            raise ConflictError("the trigger must be a sent stima_pdf message")
        if trigger["contact_id"] != contact_id or trigger["stima_id"] is None:
            raise ConflictError("the trigger message does not belong to this contact, or has no stima")
        if trigger["sent_at"] < journey["activated_at"]:
            raise ConflictError("the trigger predates the journey activation: no historical enrollment")
        if repo.automations_paused(c, ctx, contact_id):
            raise ConflictError("automations are paused for this contact")
        if repo.select_open_enrollment(c, ctx, contact_id) is not None:
            raise ConflictError("this contact already has an open journey")

        decisione = can_send_marketing(ctx, contact_id)
        if decisione.allowed:
            status, stop_reason = ENR_ACTIVE, None
            step_no, quando, kind = _prima_azione(journey, steps, trigger["sent_at"])
        else:
            status = ENR_STOPPED
            stop_reason = CONSENT_STOP_BY_GUARD_REASON.get(decisione.reason, "consent_not_granted")
            step_no = quando = kind = None

        enrollment, created = repo.insert_enrollment(
            c, ctx, journey_id=journey_id, contact_id=contact_id,
            lead_id=lead_id if lead_id is not None else trigger["lead_id"],
            stima_id=trigger["stima_id"], trigger_message_id=trigger_message_id,
            status=status, next_step_no=step_no, next_action_at=quando, next_action_kind=kind,
            stop_reason=stop_reason, actor_type=actor_type, actor_user_id=actor_user_id,
            idempotency_key=f"journey:{journey['journey_key']}:v{journey['version']}:stima:{trigger['stima_id']}")
        return {"enrollment": enrollment, "created": created}


def get_open_enrollment(ctx, contact_id: int, *, cur=None) -> dict[str, Any] | None:
    with cursore(cur) as (_, c):
        contact_in_scope(c, ctx, contact_id)
        return repo.select_open_enrollment(c, ctx, contact_id)


def inspect_next_step(ctx, enrollment_id: int, *, cur=None) -> dict[str, Any] | None:
    """Il passo che l'iscrizione sta aspettando, con il suo template. `None`
    se l'iscrizione e' chiusa."""
    with cursore(cur) as (_, c):
        e = repo.select_enrollment(c, ctx, enrollment_id)
        if e["next_step_no"] is None:
            return None
        passo = next(s for s in repo.list_steps(c, ctx, e["journey_id"]) if s["step_no"] == e["next_step_no"])
        return {"enrollment_id": e["id"], "status": e["status"], "step": passo,
                "due_at": e["next_action_at"], "kind": e["next_action_kind"],
                "awaiting_since": e["awaiting_since"], "run_no": e["run_no"]}


def pause_enrollment(ctx, enrollment_id: int, *, cur=None) -> dict[str, Any]:
    """Operatore. `active -> paused`; il `queued` del passo corrente viene cancellato."""
    utente = operatore(ctx)
    with cursore(cur) as (_, c):
        e = repo.pause_enrollment(c, ctx, enrollment_id, actor_user_id=utente, source=PAUSED_BY_ENROLLMENT)
        repo.cancel_queued_journey_messages(c, ctx, enrollment_id, reason="paused", actor_user_id=utente)
        return e


def resume_enrollment(ctx, enrollment_id: int, *, now: datetime | None = None, cur=None) -> dict[str, Any]:
    """Operatore. `paused -> active` con `run_no + 1`; il passo riprende dal
    tempo residuo. Una pausa troppo vecchia non riprende: `expired_on_resume`."""
    utente = operatore(ctx)
    adesso = now or datetime.now(timezone.utc)
    with cursore(cur) as (_, c):
        e = repo.select_enrollment(c, ctx, enrollment_id, for_update=True)
        if e["status"] != ENR_PAUSED:
            raise ConflictError(f"enrollment {enrollment_id} is {e['status']}, not paused")
        if adesso - e["next_action_at"] > MAX_RESUME_AGE:
            return repo.stop_enrollment(c, ctx, enrollment_id, reason="expired_on_resume",
                                        actor_user_id=None)
        residuo = e["next_action_at"] - e["paused_at"]
        nuovo = adesso + residuo if residuo > timedelta(0) else adesso
        return repo.resume_enrollment(c, ctx, enrollment_id, next_action_at=nuovo)


def stop_enrollment(ctx, enrollment_id: int, *, reason: str = "operator", cur=None) -> dict[str, Any]:
    """Stop. `operator` richiede una persona; una ragione di sistema no."""
    if reason not in STOP_REASONS:
        raise ValidationError(f"unknown stop reason {reason!r}")
    actor_user_id = operatore(ctx) if reason == "operator" else None
    with cursore(cur) as (_, c):
        e = repo.stop_enrollment(c, ctx, enrollment_id, reason=reason, actor_user_id=actor_user_id)
        repo.cancel_queued_journey_messages(c, ctx, enrollment_id, reason=f"stopped:{reason}",
                                            actor_user_id=actor_user_id)
        return e


# ---------------------------------------------------------------------------
# AUTOMATION CONTROL (contatto)
# ---------------------------------------------------------------------------

def pause_automations(ctx, contact_id: int, *, reason: str | None = None, cur=None) -> dict[str, Any]:
    """Operatore. Ferma le automazioni del contatto: nessuna nuova iscrizione,
    le aperte in pausa (`contact_control`), i loro `queued` cancellati. I
    messaggi manuali (senza `enrollment_id`) non vengono toccati."""
    utente = operatore(ctx)
    ragione = (reason or "").strip() or None
    with cursore(cur) as (_, c):
        contact_in_scope(c, ctx, contact_id)
        aperte = repo.list_open_enrollments_for_contact(c, ctx, contact_id)
        stima = next((e["stima_id"] for e in aperte if e["stima_id"]), None)
        controllo = repo.upsert_control_paused(c, ctx, contact_id, actor_user_id=utente,
                                               reason=ragione, stima_id=stima)
        for e in aperte:
            if e["status"] == ENR_ACTIVE:
                repo.pause_enrollment(c, ctx, e["id"], actor_user_id=utente,
                                      source=PAUSED_BY_CONTACT_CONTROL)
                repo.cancel_queued_journey_messages(c, ctx, e["id"], reason="contact_paused",
                                                    actor_user_id=utente)
        return controllo


def resume_automations(ctx, contact_id: int, *, now: datetime | None = None, cur=None) -> dict[str, Any]:
    """Operatore. Riabilita le automazioni. Riprende SOLO le iscrizioni messe
    in pausa dal controllo del contatto e non troppo vecchie; quelle messe in
    pausa una per una restano ferme; niente viene inventato."""
    utente = operatore(ctx)
    with cursore(cur) as (_, c):
        contact_in_scope(c, ctx, contact_id)
        controllo = repo.set_control_resumed(c, ctx, contact_id, actor_user_id=utente)
        for e in repo.list_open_enrollments_for_contact(c, ctx, contact_id):
            if e["status"] == ENR_PAUSED and e["paused_source"] == PAUSED_BY_CONTACT_CONTROL:
                resume_enrollment(ctx, e["id"], now=now, cur=c)
        return controllo


def automations_paused(ctx, contact_id: int, *, cur=None) -> bool:
    with cursore(cur) as (_, c):
        return repo.automations_paused(c, ctx, contact_id)
