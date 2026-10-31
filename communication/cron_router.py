"""Il giro del cron delle comunicazioni, autenticato da un SEGRETO DI SERVIZIO.

PERCHE' NON PIU' UN LOGIN DI OPERATORE

Il cron faceva login con email e password di un operatore `agency_admin`:
la password di una persona dentro un processo automatico. Quando l'account
viene disattivato o la password cambia, il cron prende 401 e nessuna email
parte. Un job interno ha bisogno di un'identita' tecnica, non di una persona.

COSA PUO' FARE QUESTA IDENTITA' - E NIENT'ALTRO

Una sola rotta, `POST /api/internal/communication/cron/run`, che esegue il
giro di sempre per UN'agenzia: tick delle sequenze, giro dei promemoria,
dispatch del canale. Nessuna lettura di dati, nessuna attivazione, nessuna
altra funzione amministrativa: il segreto non apre nessun'altra rotta,
perche' nessun'altra rotta lo legge.

  * Il segreto (`COMMUNICATION_CRON_TOKEN`, almeno 32 caratteri) sta nelle
    variabili protette del servizio web e del cron; si confronta a tempo
    costante e non finisce mai in un log.
  * Le agenzie le decide il SERVER (`COMMUNICATION_CRON_AGENCY_IDS`, es. "1"):
    il corpo nomina l'agenzia del giro, ma solo fra quelle consentite; le
    altre prendono 403. Un'agenzia sospesa o chiusa resta della continuita'
    centrale (cron FLOW): qui 409.
  * Senza segreto configurato la rotta e' SPENTA (503): mai aperta per difetto.

Il giro gira dentro questo servizio, con il `SystemAgencyContext` di
quell'agenzia - lo stesso della continuita' centrale - quindi con le stesse
protezioni del dispatch di sempre: consenso ricontrollato prima di ogni invio,
limite dei destinatari TEST (`COMMUNICATION_MARKETING_ALLOWED_RECIPIENTS`),
claim con `SKIP LOCKED`, chiavi di idempotenza.
"""
from __future__ import annotations

import hmac
import logging
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

from operator_auth.context import SystemAgencyContext

from . import dispatcher, journey_tick
from .database import communication_cursor
from .enums import CHANNEL_EMAIL, CHANNELS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/internal/communication", tags=["communication"])

TOKEN_ENV = "COMMUNICATION_CRON_TOKEN"
AGENCIES_ENV = "COMMUNICATION_CRON_AGENCY_IDS"
HEADER = "X-Stima360-Cron-Token"
#: Un segreto piu' corto non e' un segreto: la rotta resta spenta.
MIN_TOKEN_LENGTH = 32

COMPLETED, NOT_MIGRATED, FAILED, SKIPPED = "completed", "not_migrated", "failed", "skipped"


class CronRunRequest(BaseModel):
    """Il giro: l'agenzia (fra quelle consentite dal server), il canale, i limiti."""

    agency_id: int = Field(gt=0)
    channel: str
    limit: int = Field(default=10, ge=1, le=50)
    journey_limit: int = Field(default=500, ge=1, le=2000)

    model_config = {"extra": "forbid"}

    @field_validator("channel")
    @classmethod
    def _canale_noto(cls, valore: str) -> str:
        if valore not in CHANNELS:
            raise ValueError(f"channel must be one of {', '.join(sorted(CHANNELS))}")
        return valore


def agenzie_consentite() -> frozenset[int]:
    """Le agenzie che il segreto puo' servire, dalla configurazione del server."""
    consentite = set()
    for pezzo in (os.getenv(AGENCIES_ENV) or "").replace(";", ",").split(","):
        pezzo = pezzo.strip()
        if pezzo.isdigit() and int(pezzo) > 0:
            consentite.add(int(pezzo))
    return frozenset(consentite)


def _autentica(fornito: str | None) -> None:
    atteso = os.getenv(TOKEN_ENV) or ""
    if len(atteso) < MIN_TOKEN_LENGTH:
        raise HTTPException(status_code=503, detail={"code": "cron_auth_not_configured"})
    if not fornito or not hmac.compare_digest(fornito.encode("utf-8"), atteso.encode("utf-8")):
        logger.warning("communication_cron_auth_rejected")
        raise HTTPException(status_code=401, detail="Not authenticated")


def _stato_agenzia(agency_id: int) -> str | None:
    with communication_cursor() as (_conn, cur):
        cur.execute("SELECT status FROM agencies WHERE id = %s", (agency_id,))
        riga = cur.fetchone()
    return riga["status"] if riga else None


def _passo(nome: str, agency_id: int, esegui) -> dict[str, Any]:
    """Un passo del giro che non ferma il dispatch: l'esito torna come dato."""
    try:
        return {"status": COMPLETED, "counts": esegui()}
    except (journey_tick.FeatureNotMigrated, _planner().FeatureNotMigrated):
        return {"status": NOT_MIGRATED, "counts": None}
    except Exception as exc:  # noqa: BLE001 - un passo guasto non toglie il dispatch
        logger.exception("communication_cron_step_failed step=%s agency_id=%s", nome, agency_id)
        return {"status": FAILED, "counts": None, "reason": type(exc).__name__}


def _planner():
    from appointment_reminders import planner
    return planner


@router.post("/cron/run")
def cron_run(payload: CronRunRequest,
             token: str | None = Header(default=None, alias=HEADER)) -> dict[str, Any]:
    """Un giro intero per un'agenzia consentita: tick, promemoria, dispatch."""
    _autentica(token)
    if payload.agency_id not in agenzie_consentite():
        raise HTTPException(status_code=403, detail={"code": "agency_not_allowed"})
    if _stato_agenzia(payload.agency_id) != "active":
        raise HTTPException(status_code=409, detail={"code": "agency_not_active"})

    ctx = SystemAgencyContext(agency_id=payload.agency_id, origin=dispatcher.DISPATCH_ORIGIN)
    sequenze = _passo("journey_tick", payload.agency_id,
                      lambda: journey_tick.tick(ctx, limit=payload.journey_limit))
    if payload.channel == CHANNEL_EMAIL:
        promemoria = _passo("reminder_tick", payload.agency_id, lambda: _planner().tick(ctx))
    else:
        promemoria = {"status": SKIPPED, "counts": None}
    invio = dispatcher.dispatch_batch(ctx, channel=payload.channel,
                                      provider=dispatcher.adapter_per(payload.channel),
                                      limit=payload.limit)
    return {"journey": sequenze, "reminder": promemoria, "dispatch": invio}


__all__ = ["router", "TOKEN_ENV", "AGENCIES_ENV", "HEADER", "MIN_TOKEN_LENGTH", "agenzie_consentite"]
