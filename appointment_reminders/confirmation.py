"""ROADMAP PUNTO 2 - la CONFERMA al cliente di un appuntamento fissato, e il
ritiro dei messaggi in coda quando l'appuntamento cambia.

Gli agganci li chiama `appointments.service` DENTRO la transazione che scrive
l'appuntamento (creazione gia' fissata, «fissa», prenotazione pubblica
confermata, spostamento, annullamento), con il suo cursore. Due regole:

1. NON ROMPONO MAI L'AGENDA. Ogni aggancio lavora in un SAVEPOINT: un errore
   (migrazione 127 assente, template, qualsiasi cosa) viene annullato fino al
   savepoint, registrato nel log senza dati personali, e l'appuntamento si
   salva lo stesso. La conferma e' un effetto, non un prerequisito.
2. SPENTE PER IMPOSTAZIONE, E CHIUSE AI DESTINATARI. `APPOINTMENT_CONFIRMATIONS=1`
   accende SOLO le conferme: e' un interruttore distinto da quello dei promemoria
   (il 24h gia' esistente va col cron, il 2h con `APPOINTMENT_REMINDER_OFFSETS`;
   vedi `settings`). Anche accese, le conferme partono solo verso gli indirizzi
   elencati ESPLICITAMENTE in `APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS`:
   variabile assente = nessun destinatario, `*` = tutti (scelta a mano). Mai
   dedotti dagli indirizzi dei clienti.

COSA SI SCRIVE

Una riga nel registro comunicazioni esistente (`communication.service.enqueue`),
canale email, tipo servizio, motivo `appointment_confirmation`, con la chiave
`appointment_confirmation:v1:<appointment_id>:<start_epoch>`: la stessa
occorrenza (stesso appuntamento, stesso orario) e' UNA conferma. «Fissa» e poi
«Conferma» non ne mandano due; uno spostamento crea una riga nuova
dell'Agenda (id nuovo) e quindi una conferma nuova, con il testo del nuovo
orario. Il dispatcher, prima del provider, rilegge l'appuntamento
(`revalidation`): se intanto e' stato annullato o spostato, la conferma non
parte e nessuna informazione vecchia esce.

COSA SI RITIRA

`on_closed` annulla (`queued` -> `cancelled`) i promemoria e le conferme ancora
in coda dell'appuntamento annullato o spostato, subito, nella stessa
transazione: non si aspetta il giro del dispatcher.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from communication import service as communication_service
from communication.enums import (CHANNEL_EMAIL, MODE_AUTOMATIC, REASON_APPOINTMENT_CONFIRMATION,
                                 REASON_APPOINTMENT_REMINDER, TYPE_SERVICE)
from operator_auth.context import SystemAgencyContext

from . import policy, repository, settings, template

logger = logging.getLogger(__name__)

ENABLED_ENV = settings.CONFIRMATIONS_ENV
#: Lo stesso origin del planner: e' lo stesso flusso (le notifiche degli appuntamenti).
ORIGIN = "appointment_reminder"
METADATA_KIND = "appointment_confirmation"
OCCURRENCE_PREFIX = "appointment_confirmation:v1"
KIND_SCHEDULED = "scheduled"
KIND_RESCHEDULED = "rescheduled"
KINDS = (KIND_SCHEDULED, KIND_RESCHEDULED)
#: I motivi dei messaggi che `on_closed` ritira.
CLOSABLE_REASONS = (REASON_APPOINTMENT_REMINDER, REASON_APPOINTMENT_CONFIRMATION)
#: Gli esiti di `on_scheduled` che non sono un motivo della policy.
OUTCOME_QUEUED = "queued"
OUTCOME_IDEMPOTENT = "queued_idempotent"
OUTCOME_DISABLED = "disabled"
OUTCOME_NOT_MIGRATED = "not_migrated"
OUTCOME_MISSING = "appointment_missing"
OUTCOME_PAST = "appointment_already_started"
OUTCOME_ERROR = "error"


def enabled() -> bool:
    return settings.confirmations_enabled()


def _adesso() -> datetime:
    return datetime.now(timezone.utc)


def occurrence_key(appointment_id: int, start_at: datetime) -> str:
    if isinstance(appointment_id, bool) or not isinstance(appointment_id, int) or appointment_id < 1:
        raise ValueError("appointment_id must be a positive integer")
    start_at = policy._con_fuso(start_at, "start_at")
    return f"{OCCURRENCE_PREFIX}:{appointment_id}:{int(start_at.timestamp())}"


def metadata_for(appointment_id: int, start_at: datetime, kind: str) -> dict[str, Any]:
    """Metadata TECNICI: nessun dato personale."""
    if kind not in KINDS:
        raise ValueError(f"unknown confirmation kind {kind!r}")
    return {
        "kind": METADATA_KIND,
        "appointment_id": appointment_id,
        "start_epoch": int(policy._con_fuso(start_at, "start_at").timestamp()),
        "occurrence_key": occurrence_key(appointment_id, start_at),
        "variant": kind,
    }


def _savepoint(cur, nome: str):
    cur.execute(f"SAVEPOINT {nome}")


def _rollback(cur, nome: str):
    cur.execute(f"ROLLBACK TO SAVEPOINT {nome}")


def _release(cur, nome: str):
    cur.execute(f"RELEASE SAVEPOINT {nome}")


def decide(letto: dict[str, Any] | None, *, now: datetime) -> str | None:
    """PURA: None se la conferma si puo' accodare, altrimenti il motivo."""
    if letto is None:
        return OUTCOME_MISSING
    appuntamento, contatto = letto["appointment"], letto["contact"]
    motivo = policy.ineligibility_reason(appuntamento, contatto,
                                         allowed_recipients=settings.allowed_recipients_for(None))
    if motivo is not None:
        return motivo
    if appuntamento["start_at"] <= now:
        return OUTCOME_PAST
    return None


def on_scheduled(cur, agency_id: int, appointment_id: int, *, kind: str = KIND_SCHEDULED,
                 now: datetime | None = None) -> str:
    """Accoda la conferma dell'appuntamento, se dovuta. Mai un'eccezione."""
    if kind not in KINDS:
        raise ValueError(f"unknown confirmation kind {kind!r}")
    if not enabled():
        return OUTCOME_DISABLED
    adesso = now or _adesso()
    nome = "appointment_confirmation"
    _savepoint(cur, nome)
    try:
        if not communication_service.reason_code_supported(REASON_APPOINTMENT_CONFIRMATION, cur=cur):
            _release(cur, nome)
            return OUTCOME_NOT_MIGRATED
        letto = repository.appointment_for_confirmation(cur, agency_id, appointment_id)
        motivo = decide(letto, now=adesso)
        if motivo is not None:
            _release(cur, nome)
            return motivo
        appuntamento, contatto = letto["appointment"], letto["contact"]
        agenzia, profilo = letto["agency"], letto["agency"].get("profile") or {}
        oggetto, corpo = template.render_confirmation_email(
            kind=kind,
            appointment_type=appuntamento["appointment_type"],
            start_at=appuntamento["start_at"],
            agency_name=(profilo.get("display_name") or "").strip() or agenzia["name"],
            agency_phone=profilo.get("phone"),
            agency_email=profilo.get("email"),
            customer_name=contatto.get("first_name"),
            property_address=(letto["property_address"]
                              if appuntamento["appointment_type"] == "buyer_visit" else None),
        )
        ctx = SystemAgencyContext(agency_id=agency_id, origin=ORIGIN)
        esito = communication_service.enqueue(
            ctx,
            contact_id=appuntamento["contact_id"],
            channel=CHANNEL_EMAIL,
            communication_type=TYPE_SERVICE,
            mode=MODE_AUTOMATIC,
            reason_code=REASON_APPOINTMENT_CONFIRMATION,
            rendered_body=corpo,
            destination_snapshot=contatto["email"],
            idempotency_key=occurrence_key(appuntamento["id"], appuntamento["start_at"]),
            subject_snapshot=oggetto,
            template_key=template.CONFIRMATION_TEMPLATE_KEY,
            template_version=template.CONFIRMATION_TEMPLATE_VERSION,
            property_id=appuntamento.get("property_id"),
            metadata=metadata_for(appuntamento["id"], appuntamento["start_at"], kind),
            cur=cur,
        )
        _release(cur, nome)
        return OUTCOME_QUEUED if esito["created"] else OUTCOME_IDEMPOTENT
    except Exception:  # noqa: BLE001 - la conferma non rompe mai l'Agenda
        _rollback(cur, nome)
        logger.exception("appointment confirmation failed: agency_id=%s appointment_id=%s",
                         agency_id, appointment_id)
        return OUTCOME_ERROR


def on_closed(cur, agency_id: int, appointment_id: int) -> int:
    """Ritira i promemoria e le conferme in coda dell'appuntamento (annullato
    o spostato). Ritorna quanti; -1 se la lettura/scrittura fallisce (annullata
    fino al savepoint, l'Agenda prosegue)."""
    nome = "appointment_notifications_close"
    _savepoint(cur, nome)
    try:
        ctx = SystemAgencyContext(agency_id=agency_id, origin=ORIGIN)
        quanti = communication_service.cancel_queued_for_appointment(
            ctx, appointment_id=appointment_id, reason_codes=CLOSABLE_REASONS, cur=cur)
        _release(cur, nome)
        return quanti
    except Exception:  # noqa: BLE001
        _rollback(cur, nome)
        logger.exception("appointment notifications close failed: agency_id=%s appointment_id=%s",
                         agency_id, appointment_id)
        return -1
