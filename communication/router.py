"""P29-2.4 - la rotta di dispatch. DICHIARATA, NON ANCORA MONTATA.

PERCHE' NON E' IN main.py

In P29-2.4 il provider e' finto: la rotta non manderebbe niente a nessuno.
Montarla adesso pubblicherebbe su TEST un endpoint di dispatch prima che esista
qualcosa da dispacciare - e una rotta viva che non fa nulla e' una rotta che
qualcuno un giorno chiama credendo che faccia qualcosa. Il mount e' una riga, e
arriva con l'adapter reale.

Cio' che questa fase deve provare - `agency_id` nel payload rifiutato con 422 -
si prova su una app autonoma con TestClient, senza toccare la superficie
pubblica dell'applicazione.

LO SCOPE VIENE DALLA SESSIONE

`legacy_basic_agency_context` risolve l'agenzia dalla sessione autenticata e da
nient'altro (P26-5: nessun fallback, nessun Basic). E' la stessa dipendenza che
usa l'endpoint del cron P18-D2, ed e' il motivo per cui non serve nessun
contesto globale: con N agenzie servono N cron, ciascuno con la propria
sessione.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from operator_auth.context import OperatorContext
from operator_auth.dependencies import legacy_basic_agency_context
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import dispatcher
from .schemas import DispatchRequest

router = APIRouter(prefix="/api/communication", tags=["communication"])


@router.post("/dispatch")
def dispatch(
    payload: DispatchRequest,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    """Un giro di dispatch per l'agenzia della sessione, su UN canale.

    403 se il contesto non e' vincolato a un'agenzia: un platform admin senza
    membership non ha un'agenzia per cui dispacciare, e indovinarne una sarebbe
    esattamente il dispatcher cross-tenant che il design vieta.
    """
    try:
        return dispatcher.dispatch_batch(ctx, channel=payload.channel,
                                         limit=payload.limit)
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
