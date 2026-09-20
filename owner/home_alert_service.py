"""LMC-12 - il giro delle notifiche PRE-INCARICO, tenant per tenant.

Per ogni agenzia attiva, per ogni grant `owner_stima_access` attivo (a
pagine, con cursore sull'id: keyset, mai OFFSET), legge le due serie della
casa - gli snapshot del valore e le rilevazioni della domanda - chiede a
`owner/home_alerts.py` cosa c'e' da dire, e scrive l'esito con la chiave di
idempotenza che il modulo puro ha deciso. Qui non si decide niente: si
orchestra.

ISOLAMENTO DEI GUASTI. Una casa che solleva conta come `failed` e il giro
passa alla successiva; un'agenzia che solleva conta come fallita nel
riepilogo e le altre proseguono. Il cursore avanza PRIMA di elaborare la
pagina, cosi' un guasto sull'ultimo elemento non rilegge la stessa pagina
all'infinito.

LA PREFERENZA. V1 rispetta soltanto `in_app_enabled` (riga assente = TRUE,
come P5): con la preferenza spenta la decisione non diventa una riga, ma
viene auditata come soppressa - una volta per fatto, non una per giro. Il
riferimento del rilevatore NON avanza su una soppressione, di proposito: la
notifica non e' stata assorbita, e se il proprietario riattiva le notifiche
vedra' il cambiamento cumulativo che si e' perso, non l'ultimo giorno.

COSA NON FA. Non ricalcola valori, non rilancia Buyer Pressure, non manda
email/WhatsApp/SMS, non accoda P29, non crea task CRM, non tocca la timeline
Seller Intelligence. Produce righe in `owner_home_notifications` e audit, e
basta.
"""
from __future__ import annotations

import logging
from typing import Any

from core.exceptions import NotFoundError, ValidationError

from . import home_alerts
from . import repository

logger = logging.getLogger(__name__)

#: Lo scope dell'advisory lock del giro. SEPARATO da quello di LMC-11
#: (`property_watch:valuation_cron`): i due giri possono sovrapporsi senza
#: escludersi, e nessuno dei due deve toccare l'altro.
HOME_ALERT_LOCK_SCOPE = "owner:home_alerts"

#: Gli esiti per decisione. `suppressed` e' la preferenza spenta; `skipped`
#: e' un grant sparito fra la lettura e la scrittura; `failed` e' un guasto.
CYCLE_STATUSES = ("created", "reused", "suppressed", "skipped", "failed")

#: La dimensione di pagina di default, non un tetto.
PAGE_SIZE_DEFAULT = 500


def _vuoto() -> dict[str, int]:
    return {stato: 0 for stato in CYCLE_STATUSES}


def decisions_for_grant(agency_id: int, grant: dict[str, Any]) -> list[dict[str, Any]]:
    """Le decisioni per un grant: le due serie, i due ancoraggi, il modulo puro."""
    account = int(grant["owner_account_id"])
    stima = int(grant["stima_id"])
    ancore = repository.home_alert_anchors(account, stima)
    valori = repository.home_value_series(agency_id, stima)
    domanda = repository.home_demand_series(agency_id, stima)
    return (home_alerts.decide_value_alerts(valori, absorbed_observation_id=ancore["value"])
            + home_alerts.decide_demand_alerts(domanda, absorbed_observation_id=ancore["demand"]))


def process_grant(agency_id: int, grant: dict[str, Any]) -> dict[str, int]:
    """Un grant, tutte le sue decisioni, un conteggio per esito."""
    conteggi = _vuoto()
    account = int(grant["owner_account_id"])
    stima = int(grant["stima_id"])
    decisioni = decisions_for_grant(agency_id, grant)
    if not decisioni:
        return conteggi
    chiavi = {id(d): home_alerts.idempotency_key(d, stima_id=stima, owner_account_id=account)
              for d in decisioni}
    if not grant.get("in_app_enabled", True):
        gia = repository.home_alert_suppressed_keys(account, list(chiavi.values()))
        for decisione in decisioni:
            chiave = chiavi[id(decisione)]
            if chiave not in gia:
                repository.audit_home_notification_suppressed(
                    account, stima, notification_type=decisione["type"], idempotency_key=chiave)
            conteggi["suppressed"] += 1
        return conteggi
    for decisione in decisioni:
        titolo, corpo = home_alerts.compose(decisione)
        try:
            esito = repository.create_home_notification(
                account, stima, notification_type=decisione["type"], title=titolo, body=corpo,
                evidence=home_alerts.evidence(decisione), idempotency_key=chiavi[id(decisione)])
        except (NotFoundError, ValidationError):
            conteggi["skipped"] += 1
            continue
        conteggi[esito if esito in conteggi else "failed"] += 1
    return conteggi


def run_for_agency(agency_id: int, *, page_size: int | None = None) -> dict[str, Any]:
    """Tutti i grant attivi dell'agenzia, a pagine, fino in fondo."""
    dimensione = int(page_size or PAGE_SIZE_DEFAULT)
    if dimensione < 1:
        raise ValueError("page_size")
    totali = _vuoto()
    processati = 0
    dopo = 0
    while True:
        pagina = repository.list_home_alert_grants_page(
            agency_id, after_grant_id=dopo, page_size=dimensione)
        if not pagina:
            break
        avanzamento = max(int(g["grant_id"]) for g in pagina)
        if avanzamento <= dopo:
            logger.error("home_alert_cron_cursor_stalled agency_id=%s after_grant_id=%s",
                         agency_id, dopo)
            break
        dopo = avanzamento  # PRIMA di elaborare: un guasto non rilegge la pagina
        for grant in pagina:
            processati += 1
            try:
                parziali = process_grant(agency_id, grant)
            except Exception as exc:  # noqa: BLE001 - isolamento: la casa dopo continua
                logger.warning("home_alert_cron_grant_failed agency_id=%s grant_id=%s error=%s",
                               agency_id, grant.get("grant_id"), type(exc).__name__)
                totali["failed"] += 1
                continue
            for stato in CYCLE_STATUSES:
                totali[stato] += parziali[stato]
        if len(pagina) < dimensione:
            break
    return {"agency_id": agency_id, "processed": processati, **totali}


def run_for_all_agencies(*, page_size: int | None = None) -> dict[str, Any]:
    """Ogni agenzia attiva, una alla volta; una che solleva non ferma le altre."""
    from property_watch import repository as pw_repository

    esiti = []
    totali = _vuoto()
    processati = 0
    for agency_id in pw_repository.list_active_agency_ids():
        try:
            esito = run_for_agency(agency_id, page_size=page_size)
        except Exception as exc:  # noqa: BLE001 - isolamento fra tenant
            logger.warning("home_alert_cron_agency_failed agency_id=%s error=%s",
                           agency_id, type(exc).__name__)
            esito = {"agency_id": agency_id, "processed": 0, **_vuoto(), "failed": 1}
        esiti.append(esito)
        processati += int(esito["processed"])
        for stato in CYCLE_STATUSES:
            totali[stato] += int(esito[stato])
    return {"agencies": len(esiti), "processed": processati, **totali, "runs": esiti}
