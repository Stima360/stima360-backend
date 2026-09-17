"""La rotta di dispatch. DICHIARATA in P29-2.4, MONTATA in P29-2.6E.

PERCHE' NON ERA IN main.py, E PERCHE' ADESSO C'E'

In P29-2.4 il provider era finto: la rotta non avrebbe mandato niente a
nessuno, e una rotta viva che non fa nulla e' una rotta che qualcuno un giorno
chiama credendo che faccia qualcosa. Con l'adapter email reale (P29-2.5E) e un
cron che la chiama (P29-2.6E), la ragione per tenerla fuori e' venuta meno.

LO SCOPE VIENE DALLA SESSIONE, IL PERMESSO DALLA MATRICE

`require_dispatch_context` (P29-2.6E) e' `legacy_basic_agency_context` piu' una
riga: l'agenzia esce dalla sessione autenticata e da nient'altro (P26-5:
nessun fallback, nessun Basic), e il chiamante deve essere fra quelli che
vedono TUTTI i record dell'agenzia - perche' e' su tutti che il giro agisce.
Un `agent` prende 403. Vedi `communication/dependencies.py` per il perche'.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from operator_auth.context import OperatorContext
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import dispatcher
from .dependencies import require_dispatch_context
from .exceptions import ValidationError
from .schemas import DispatchRequest

router = APIRouter(prefix="/api/communication", tags=["communication"])


@router.post("/dispatch")
def dispatch(
    payload: DispatchRequest,
    ctx: OperatorContext = Depends(require_dispatch_context),
):
    """Un giro di dispatch per l'agenzia della sessione, su UN canale.

    403 a chi non vede tutti i record dell'agenzia - un `agent` non li vede, e
    un giro di dispatch li tocca tutti.

    403 anche se il contesto non e' vincolato a un'agenzia: un platform admin
    senza membership non ha un'agenzia per cui dispacciare, e indovinarne una
    sarebbe esattamente il dispatcher cross-tenant che il design vieta.
    """
    try:
        return dispatcher.dispatch_batch(
            ctx,
            channel=payload.channel,
            # Il trasporto si risolve DAL CANALE, esplicitamente: il default di
            # `dispatch_batch` e' il provider finto, e una rotta montata che ci
            # cadesse sopra direbbe `sent` senza aver mandato niente.
            provider=dispatcher.adapter_per(payload.channel),
            limit=payload.limit,
        )
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValidationError as exc:
        # Un canale senza trasporto reale: `whatsapp` finche' P29-2.5W e'
        # deferita e R3 e' OPEN. 501 e non 422 perche' la richiesta e' ben
        # formata - e' il server a non avere ancora quel pezzo.
        raise HTTPException(status_code=501, detail=str(exc)) from exc
