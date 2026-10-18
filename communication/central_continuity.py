"""ADM-3 - continuita' centrale dei follow-up email delle agenzie SOSPESE.

Decisioni del proprietario: i follow-up email continuano tramite STIMA360
centrale anche quando l'agenzia e' sospesa (D1), senza promemoria degli
appuntamenti (D2), con mittente e firma STIMA360 (D3). I lead restano
dell'agenzia: nessun dato cambia proprietario.

NESSUN LOGIN, NESSUNA ROTTA, NESSUNA CREDENZIALE

Un'agenzia sospesa non ha sessioni (ADM-1): nessun operatore, nemmeno
tecnico, entra per lei. Questo modulo gira DENTRO un processo server-only (il
cron `run_flow_p2b_cron.py`) e lavora su un `SystemAgencyContext` costruito
dall'id dell'agenzia letta dal database, con l'origine che il dispatcher usa
gia' (`communication_dispatch`). Non e' montato su nessun router: non esiste
un modo di invocarlo da HTTP.

COSA FA, PER OGNI AGENZIA SOSPESA (una alla volta, ciascuna isolata)

    1. un giro del motore delle sequenze (`journey_tick.tick`): i passi dovuti
       si compongono con la firma STIMA360 e il segno nei metadata;
    2. un giro di dispatch email (`dispatcher.dispatch_batch`) con il filtro
       centrale (`repository.CENTRAL_CONTINUITY_FILTER`): solo l'email della
       stima e i passi firmati STIMA360. Consenso, finestra oraria, claim con
       token, recupero degli stale e limite del lotto sono quelli di sempre.

Le agenzie ATTIVE non passano di qui: il loro comportamento non cambia.
"""
from __future__ import annotations

import logging
from typing import Any

from operator_auth.context import SystemAgencyContext

from . import dispatcher, journey_tick
from .database import communication_cursor
from .enums import CHANNEL_EMAIL

logger = logging.getLogger(__name__)

AGENCY_SUSPENDED = "suspended"


def suspended_agency_ids() -> list[int]:
    with communication_cursor() as (_conn, cur):
        cur.execute("SELECT id FROM agencies WHERE status = %s ORDER BY id", (AGENCY_SUSPENDED,))
        return [int(r["id"]) for r in cur.fetchall()]


def run_for_agency(agency_id: int, *, provider=None) -> dict[str, Any]:
    """Un giro centrale per UNA agenzia sospesa. Solleva se qualcosa fallisce."""
    ctx = SystemAgencyContext(agency_id=agency_id, origin=dispatcher.DISPATCH_ORIGIN)
    sequenze = journey_tick.tick(ctx)
    invio = dispatcher.dispatch_batch(
        ctx, channel=CHANNEL_EMAIL,
        provider=provider if provider is not None else dispatcher.adapter_per(CHANNEL_EMAIL),
        central_continuity=True)
    return {"agency_id": agency_id, "journeys": sequenze, "dispatch": invio}


def run_for_suspended_agencies(*, provider=None) -> dict[str, Any]:
    """Un giro per ogni agenzia sospesa. Un errore su A non ferma B."""
    esiti, falliti = [], []
    for agency_id in suspended_agency_ids():
        try:
            esiti.append(run_for_agency(agency_id, provider=provider))
        except Exception as exc:  # noqa: BLE001 - un'agenzia non ferma le altre
            logger.error("central_continuity agency_id=%s failed: %s", agency_id, type(exc).__name__)
            falliti.append({"agency_id": agency_id, "error": type(exc).__name__})
    return {"agencies": len(esiti) + len(falliti), "runs": esiti, "failed": falliti}
