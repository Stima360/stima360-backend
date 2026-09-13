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

from . import agencies_service, operators_service
from .dependencies import require_platform_admin
from .enums import AUDIT_UNAVAILABLE_MESSAGE, ROUTER_PREFIX
from .exceptions import (
    AgencyNotFound,
    AgencySlugConflict,
    MembershipNotFound,
    OperatorNotFound,
    PasswordRequired,
    PlatformAuditUnavailable,
    PlatformConflict,
)
from .operators_repository import MEMBERSHIP_COLUMNS, OPERATOR_COLUMNS
from .schemas import (
    AgencyCreateRequest,
    AgencyOperatorResponse,
    AgencyResponse,
    AgencyUpdateRequest,
    MembershipResponse,
    MembershipUpdateRequest,
    OperatorCreateRequest,
    OperatorDetailResponse,
    OperatorResponse,
    OperatorUpdateRequest,
    OwnerTransferRequest,
    OwnerTransferResponse,
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


# ---------------------------------------------------------------------------
# P27-3 - OPERATORI, TITOLARI E RUOLI
#
# Sei route, e nessuna DELETE. `revoked` e `disabled` sono stati: la relazione
# fra una persona e un'agenzia e' un fatto storico, e cancellarla toglierebbe
# la differenza fra "non c'e' mai stato" e "non c'e' piu'".
#
# LA SESTA ROUTE E' IL TRASFERIMENTO DI TITOLARITA', E NON E' UN AMPLIAMENTO.
#
# La struttura suggerita ne elencava cinque, con il cambio titolare da far
# passare per la PATCH della membership. Non e' esprimibile li' senza un
# effetto collaterale: assegnare `agency_owner` richiede di degradare il
# titolare in carica, cioe' di cambiare lo stato di una persona che la
# richiesta non nomina. `PUT .../owner` fa le due cose insieme, le audita come
# una, e lascia alla PATCH il compito che le compete.
#
# Il router traduce e non decide: le eccezioni di dominio arrivano dal service
# gia' formate e qui diventano uno status.
# ---------------------------------------------------------------------------

@router.get(
    "/agencies/{agency_id}/operators",
    response_model=list[AgencyOperatorResponse],
)
def list_agency_operators(
    agency_id: int,
    context: OperatorContext = Depends(require_platform_admin),
) -> list[AgencyOperatorResponse]:
    """L'organico di un'agenzia: identita' e membership, in ogni stato."""
    try:
        rows = operators_service.list_agency_operators(agency_id)
    except AgencyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [_agency_operator(row) for row in rows]


@router.get("/operators/{operator_user_id}", response_model=OperatorDetailResponse)
def get_operator(
    operator_user_id: int,
    context: OperatorContext = Depends(require_platform_admin),
) -> OperatorDetailResponse:
    try:
        found = operators_service.get_operator(operator_user_id)
    except OperatorNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return OperatorDetailResponse(
        operator=OperatorResponse(**found["operator"]),
        memberships=[MembershipResponse(**m) for m in found["memberships"]],
    )


@router.post(
    "/agencies/{agency_id}/operators",
    response_model=AgencyOperatorResponse,
    status_code=201,
)
def create_agency_operator(
    agency_id: int,
    payload: OperatorCreateRequest,
    context: OperatorContext = Depends(require_platform_admin),
) -> AgencyOperatorResponse:
    """Crea o riusa l'operatore, e lo lega all'agenzia.

    201 anche quando l'operatore esisteva gia': cio' che nasce e' la
    membership, che prima non c'era, e il corpo riporta un `membership.id` che
    non esisteva.
    """
    try:
        created = operators_service.create_agency_operator(
            context,
            agency_id,
            email=payload.email,
            password=payload.password,
            first_name=payload.first_name,
            last_name=payload.last_name,
            operator_status=payload.status,
            role=payload.role,
            created_fields=payload.created_fields(),
        )
    except AgencyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PasswordRequired as exc:
        # 422 e non 409: non c'e' nulla con cui confliggere, manca un dato per
        # il percorso che questa richiesta ha imboccato. Non e' un 422 che lo
        # schema possa produrre - se la password serva dipende da chi sia
        # quell'email, e lo si sa dopo aver letto `operator_users`.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PlatformConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PlatformAuditUnavailable as exc:
        raise HTTPException(
            status_code=503, detail=AUDIT_UNAVAILABLE_MESSAGE
        ) from exc
    return AgencyOperatorResponse(
        operator=OperatorResponse(**created["operator"]),
        membership=MembershipResponse(**created["membership"]),
    )


@router.patch("/operators/{operator_user_id}", response_model=OperatorResponse)
def update_operator(
    operator_user_id: int,
    payload: OperatorUpdateRequest,
    context: OperatorContext = Depends(require_platform_admin),
) -> OperatorResponse:
    """Aggiorna nome, cognome e stato. Non email, non password, non il flag
    di piattaforma: non sono campi di questo schema."""
    try:
        operator = operators_service.update_operator(
            context, operator_user_id, payload.changed_fields()
        )
    except OperatorNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlatformAuditUnavailable as exc:
        raise HTTPException(
            status_code=503, detail=AUDIT_UNAVAILABLE_MESSAGE
        ) from exc
    return OperatorResponse(**operator)


@router.patch(
    "/agencies/{agency_id}/operators/{operator_user_id}/membership",
    response_model=MembershipResponse,
)
def update_membership(
    agency_id: int,
    operator_user_id: int,
    payload: MembershipUpdateRequest,
    context: OperatorContext = Depends(require_platform_admin),
) -> MembershipResponse:
    """Ruolo e stato della membership. Sospendere, revocare, riattivare.

    `role='agency_owner'` e' 409: vedi `PUT .../owner`.
    """
    try:
        membership = operators_service.update_membership(
            context, agency_id, operator_user_id, payload.changed_fields()
        )
    except (AgencyNotFound, MembershipNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlatformConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PlatformAuditUnavailable as exc:
        raise HTTPException(
            status_code=503, detail=AUDIT_UNAVAILABLE_MESSAGE
        ) from exc
    return MembershipResponse(**membership)


@router.put("/agencies/{agency_id}/owner", response_model=OwnerTransferResponse)
def transfer_owner(
    agency_id: int,
    payload: OwnerTransferRequest,
    context: OperatorContext = Depends(require_platform_admin),
) -> OwnerTransferResponse:
    """Rende l'operatore indicato il titolare, degradando il precedente.

    PUT e non POST: indicare due volte lo stesso titolare lascia l'agenzia
    nello stesso stato, e la seconda volta non scrive nulla.
    """
    try:
        result = operators_service.transfer_owner(
            context, agency_id, payload.operator_user_id
        )
    except (AgencyNotFound, OperatorNotFound, MembershipNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlatformConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PlatformAuditUnavailable as exc:
        raise HTTPException(
            status_code=503, detail=AUDIT_UNAVAILABLE_MESSAGE
        ) from exc
    demoted = result["demoted"]
    return OwnerTransferResponse(
        membership=MembershipResponse(**result["membership"]),
        demoted=MembershipResponse(**demoted) if demoted else None,
    )


def _agency_operator(row: dict) -> AgencyOperatorResponse:
    """Separa la riga piatta della JOIN nelle due proiezioni.

    `list_agency_operators` restituisce identita' e membership sulla stessa
    riga, e i nomi di colonna non collidono se non su `id`: il RealDictCursor
    tiene l'ultimo, che e' quello della membership. Le due proiezioni si
    ricostruiscono quindi per nome, e `operator.id` si prende da
    `operator_user_id`, che la membership porta con se'.
    """
    operator = {key: row[key] for key in OPERATOR_COLUMNS if key != "id"}
    operator["id"] = row["operator_user_id"]
    membership = {key: row[key] for key in MEMBERSHIP_COLUMNS}
    return AgencyOperatorResponse(
        operator=OperatorResponse(**operator),
        membership=MembershipResponse(**membership),
    )
