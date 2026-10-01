"""A32-2 - il planner: UN giro dei promemoria per UN'agenzia.

    login del cron -> journeys/tick -> reminders/tick (QUI) -> dispatch -> logout

Il planner non manda niente. Legge i candidati, chiede alla policy se e' ora,
e per ogni promemoria dovuto scrive UNA intenzione nel ledger ESISTENTE con
`communication.service.enqueue`. Tutto il resto - claim, revalida finale,
trasporto, esito - e' del dispatcher, che gia' esiste.

L'AGENZIA VIENE DALLA SESSIONE

`SystemAgencyContext(agency_id=<require_agency() della sessione>,
origin="appointment_reminder")`. Non `communication_dispatch`: un origin dice
QUALE flusso agisce, e il promemoria non e' il dispatcher. Nessun allargamento
cross-tenant: il contesto esprime "questa agenzia" e nient'altro.

UN SOLO `now` PER GIRO

Preso una volta, con il fuso, e passato a ogni decisione: due candidati dello
stesso giro non vedono due orologi diversi.

IDEMPOTENZA

La chiave del ledger E' `policy.occurrence_key(...)`:
`appointment_reminder:v1:<appointment_id>:24h:<start_epoch>`. Non contiene
l'email, il nome, l'immobile o l'agente. Lo UNIQUE `(agency_id,
idempotency_key)` del ledger e' l'autorita' finale: un secondo giro trova il
messaggio gia' scritto (`created=False`) e lo conta `queued_idempotent`, qualunque
sia il suo stato - anche `suppressed` per un'email cambiata. Un promemoria per
occorrenza, mai due.

UN CANDIDATO ROTTO NON FERMA IL GIRO

Ogni candidato ha la sua transazione (`enqueue` senza cursore apre e committa
la propria). Un'eccezione su uno conta `errors` e il giro continua. Nessun
retry: il giro dopo e' fra un'ora.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from communication import service as communication_service
from communication.database import communication_cursor
from communication.enums import (CHANNEL_EMAIL, MODE_AUTOMATIC, REASON_APPOINTMENT_REMINDER,
                                 TYPE_SERVICE)
from communication.exceptions import ConflictError
from operator_auth.context import SystemAgencyContext

from . import policy, repository, template

logger = logging.getLogger(__name__)

#: L'origin del contesto di sistema del planner. Suo, e di nessun altro flusso.
REMINDER_ORIGIN = "appointment_reminder"

#: Il `kind` dei metadata tecnici del messaggio.
METADATA_KIND = "appointment_reminder"

#: I motivi di non idoneita' che il giro conta, per nome. Sono quelli della
#: policy: nessun motivo inventato qui.
SKIP_REASONS = (
    policy.REASON_TYPE, policy.REASON_SOURCE, policy.REASON_STATUS,
    policy.REASON_NO_CONTACT, policy.REASON_CONTACT_ARCHIVED, policy.REASON_NO_EMAIL,
    policy.REASON_BOOKING_TOO_LATE, policy.REASON_LEAD_TOO_SHORT,
)

COUNTS = ("scanned", "due", "queued", "queued_idempotent", "not_due", "ineligible",
          "errors")

MESSAGGIO_NON_MIGRATO = (
    "appointment reminders require migration 079_a32_1_appointment_reminders, "
    "which is not applied on this database")


class FeatureNotMigrated(ConflictError):
    """La 079 non c'e' su questo database: il giro non accoda niente."""


def _adesso() -> datetime:
    """L'unico orologio del planner. Un test lo sostituisce."""
    return datetime.now(timezone.utc)


def contesto_di_sistema(ctx_operatore) -> SystemAgencyContext:
    """Lo scope del giro: l'agenzia della SESSIONE, origin del promemoria.

    `require_agency()` solleva `PlatformAdminAgencyRequired` per un platform
    admin non vincolato: il router lo traduce in 403. Nessuna agenzia si
    indovina.
    """
    return SystemAgencyContext(agency_id=ctx_operatore.require_agency(),
                               origin=REMINDER_ORIGIN)


def metadata_for(appointment_id: int, start_at: datetime) -> dict[str, Any]:
    """I metadata TECNICI del messaggio. Nessun dato personale."""
    return {
        "kind": METADATA_KIND,
        "appointment_id": appointment_id,
        "offset": policy.OFFSET_24H,
        "occurrence_key": policy.occurrence_key(appointment_id, start_at),
        "start_epoch": int(start_at.timestamp()),
    }


def _conteggi_vuoti() -> dict[str, Any]:
    conteggi: dict[str, Any] = {k: 0 for k in COUNTS}
    conteggi["skipped_by_reason"] = {r: 0 for r in SKIP_REASONS}
    return conteggi


def _accoda(ctx: SystemAgencyContext, candidato: dict[str, Any],
            agenzia: str) -> bool:
    """Rende e accoda UN promemoria dovuto. True se creato, False se c'era gia'."""
    appuntamento = candidato["appointment"]
    contatto = candidato["contact"]
    oggetto, corpo = template.render_email(
        appointment_type=appuntamento["appointment_type"],
        start_at=appuntamento["start_at"],
        agency_name=agenzia,
        customer_name=contatto.get("first_name"),
        property_address=(candidato["property_address"]
                          if appuntamento["appointment_type"] == "buyer_visit" else None),
    )
    chiave = policy.occurrence_key(appuntamento["id"], appuntamento["start_at"])
    esito = communication_service.enqueue(
        ctx,
        contact_id=appuntamento["contact_id"],
        channel=CHANNEL_EMAIL,
        communication_type=TYPE_SERVICE,
        mode=MODE_AUTOMATIC,
        reason_code=REASON_APPOINTMENT_REMINDER,
        rendered_body=corpo,
        destination_snapshot=contatto["email"],
        idempotency_key=chiave,
        subject_snapshot=oggetto,
        template_key=template.TEMPLATE_KEY,
        template_version=template.TEMPLATE_VERSION,
        metadata=metadata_for(appuntamento["id"], appuntamento["start_at"]),
    )
    return bool(esito["created"])


def tick(ctx_operatore, *, limit: int = repository.MAX_CANDIDATES,
         now: datetime | None = None) -> dict[str, Any]:
    """UN giro per l'agenzia della sessione. Restituisce i conteggi.

    `now` e' un argomento per i test; la rotta non lo passa mai (e il suo
    corpo non lo accetta).
    """
    ctx = contesto_di_sistema(ctx_operatore)
    adesso = now if now is not None else _adesso()
    if adesso.tzinfo is None or adesso.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    with communication_cursor() as (_conn, cur):
        if not repository.schema_ready(cur):
            raise FeatureNotMigrated(MESSAGGIO_NON_MIGRATO)
        agenzia = repository.agency_name(cur, ctx.agency_id)
        candidati = repository.candidates(cur, ctx.agency_id, now=adesso, limit=limit)

    conteggi = _conteggi_vuoti()
    for candidato in candidati:
        conteggi["scanned"] += 1
        appuntamento = candidato["appointment"]
        try:
            decisione = policy.is_reminder_eligible(
                now=adesso, appointment=appuntamento, contact=candidato["contact"])
            if decisione.reason is not None:
                conteggi["ineligible"] += 1
                conteggi["skipped_by_reason"][decisione.reason] += 1
                continue
            if not decisione.due:
                conteggi["not_due"] += 1
                continue
            conteggi["due"] += 1
            creato = _accoda(ctx, candidato, agenzia)
            conteggi["queued" if creato else "queued_idempotent"] += 1
        except Exception:  # noqa: BLE001 - un candidato rotto non ferma il giro
            # Nessun dato personale nel log: solo id tecnici.
            logger.exception("appointment reminder candidate failed: agency_id=%s "
                             "appointment_id=%s", ctx.agency_id, appuntamento.get("id"))
            conteggi["errors"] += 1
    return conteggi
