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

from fastapi import APIRouter, Depends

from operator_auth.context import OperatorContext
from operator_auth.dependencies import AuthenticatedSession, current_session

from .dependencies import require_platform_admin
from .enums import ROUTER_PREFIX
from .schemas import PlatformMeResponse

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
