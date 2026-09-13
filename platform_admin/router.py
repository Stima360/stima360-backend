"""HTTP adapter della superficie Platform. P27-1.

UN endpoint. Non e' un segnaposto: e' l'oggetto sotto test.

`/api/platform/me` non fa lavoro amministrativo - non legge agenzie, non conta
operatori, non tocca un territorio - perche' nessuna di quelle cose esiste
ancora. Fa l'unica cosa che P27-1 deve dimostrare: che esiste una superficie
separata, che ammette un platform admin, che rifiuta con 403 chiunque altro, e
che ogni ammissione e ogni rifiuto finiscono in `platform_audit_log`.

La dipendenza e' dichiarata QUI oltre che sul mount in `main.py`. Non e' una
ripetizione inutile:

* sul mount e' l'ammissione, ed e' la riga che si legge in `main.py` per sapere
  chi entra su `/api/platform`;
* nella route e' il modo di ottenere il contesto.

FastAPI risolve la dipendenza una volta sola per richiesta (cache per
callable), quindi il costo e' una risoluzione di sessione e una riga di audit,
non due. Se un giorno una route di questo router venisse scritta SENZA
dichiararla, resterebbe comunque protetta dal mount - ed e' questo il motivo
per cui la dichiarazione sul mount non e' ridondante.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from operator_auth.context import OperatorContext
from operator_auth.dependencies import AuthenticatedSession, current_session

from . import agencies_service
from .dependencies import require_platform_admin
from .enums import AUDIT_UNAVAILABLE_MESSAGE, ROUTER_PREFIX
from .exceptions import (
    AgencyNotFound,
    AgencySlugConflict,
    PlatformAuditUnavailable,
)
from .schemas import (
    AgencyCreateRequest,
    AgencyResponse,
    AgencyUpdateRequest,
    PlatformMeResponse,
)

router = APIRouter(prefix=ROUTER_PREFIX, tags=["platform"])


@router.get("/me", response_model=PlatformMeResponse)
def me(
    context: OperatorContext = Depends(require_platform_admin),
    session: AuthenticatedSession = Depends(current_session),
) -> PlatformMeResponse:
    """La sessione del chiamante, come la vede la superficie Platform.

    Entrambe le dipendenze derivano dallo stesso `optional_session` cached,
    quindi questa route costa una risoluzione di sessione, non due.

    `current_session` e' dichiarata solo per la scadenza. Non e' un secondo
    controllo di autorizzazione: quello lo ha gia' fatto
    `require_platform_admin`, e se non fosse passato non saremmo qui.
    """
    return PlatformMeResponse(
        user_id=context.user_id,
        is_platform_admin=context.is_platform_admin,
        agency_id=context.agency_id,
        session_expires_at=session.expires_at,
    )


# ---------------------------------------------------------------------------
# P27-2 - GESTIONE AGENZIE
#
# Quattro route, e nessuna DELETE.
#
# L'assenza e' una decisione, non una mancanza: un'agenzia con dati dentro non
# deve poter sparire da una chiamata HTTP, e `status='archived'` e' gia' il
# modo in cui la rete smette di usarla - `operator_auth` rende inutilizzabile
# un tenant che non sia 'active', quindi archiviare la spegne davvero senza
# distruggere nulla. Aggiungere una DELETE piu' avanti sarebbe una decisione da
# prendere apposta; non averla non richiede nulla.
#
# Tutte e quattro ereditano `require_platform_admin` dal mount in main.py e la
# ridichiarano per ottenere il contesto - una sola risoluzione per richiesta e
# una sola riga di ammissione, come per /me.
#
# Il router traduce, e non decide: le eccezioni di dominio arrivano dal service
# gia' formate, e qui diventano uno status. Nessuna regola di agenzia e'
# scritta in questo file.
# ---------------------------------------------------------------------------

@router.get("/agencies", response_model=list[AgencyResponse])
def list_agencies(
    context: OperatorContext = Depends(require_platform_admin),
) -> list[AgencyResponse]:
    """L'elenco delle agenzie della rete.

    Nessuna riga di audit operativa: l'ammissione di P27-1 ha gia' registrato
    chi e' entrato e su quale route, e per una lettura quello E' la traccia.
    Vedi il docstring di agencies_service.
    """
    return [AgencyResponse(**row) for row in agencies_service.list_agencies()]


@router.get("/agencies/{agency_id}", response_model=AgencyResponse)
def get_agency(
    agency_id: int,
    context: OperatorContext = Depends(require_platform_admin),
) -> AgencyResponse:
    try:
        return AgencyResponse(**agencies_service.get_agency(agency_id))
    except AgencyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/agencies", response_model=AgencyResponse, status_code=201)
def create_agency(
    payload: AgencyCreateRequest,
    context: OperatorContext = Depends(require_platform_admin),
) -> AgencyResponse:
    """Apre una nuova agenzia. 201, con la riga completa.

    201 e non 200: la risposta riporta un `id` che prima non esisteva, ed e' il
    codice che lo dice.
    """
    try:
        row = agencies_service.create_agency(
            context,
            name=payload.name,
            slug=payload.slug,
            status=payload.status,
            settings=payload.settings,
            created_fields=payload.created_fields(),
        )
    except AgencySlugConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PlatformAuditUnavailable as exc:
        raise HTTPException(
            status_code=503, detail=AUDIT_UNAVAILABLE_MESSAGE
        ) from exc
    return AgencyResponse(**row)


@router.patch("/agencies/{agency_id}", response_model=AgencyResponse)
def update_agency(
    agency_id: int,
    payload: AgencyUpdateRequest,
    context: OperatorContext = Depends(require_platform_admin),
) -> AgencyResponse:
    """Aggiorna i soli campi presenti nel corpo: name, status, settings.

    Un corpo vuoto non arriva qui: lo schema lo rifiuta con 422, che e' il
    posto giusto - e' la richiesta a essere malformata, non lo stato del
    sistema a impedire l'operazione. Lo stesso vale per un corpo che contenga
    `slug`: non e' un campo di questo schema, e `extra="forbid"` lo respinge
    con 422 prima che questo gestore parta. Lo slug e' la chiave stabile su cui
    il funnel pubblico risolve l'agenzia (migration 027) e si sceglie una volta,
    alla creazione.

    Non c'e' quindi un `except AgencySlugConflict` qui, e non e' una svista: il
    service non puo' piu' sollevarlo su questo percorso, e un gestore per un
    caso impossibile afferma che il caso esista ancora. Sulla creazione resta,
    dove il conflitto e' reale.
    """
    try:
        row = agencies_service.update_agency(
            context, agency_id, payload.changed_fields()
        )
    except AgencyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlatformAuditUnavailable as exc:
        raise HTTPException(
            status_code=503, detail=AUDIT_UNAVAILABLE_MESSAGE
        ) from exc
    return AgencyResponse(**row)
