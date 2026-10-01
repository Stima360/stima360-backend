"""A32-2 - `POST /api/communication/reminders/tick`: un giro dei promemoria.

La chiama il cron del dispatch (`run_communication_dispatch_cron.py`), dopo il
tick delle journey e prima del dispatch, con la STESSA sessione.

LA SOGLIA E' QUELLA DEL DISPATCH

`require_dispatch_context`: owner/admin (e il platform admin vincolato)
passano, un `agent` prende 403 - il giro tocca gli appuntamenti di TUTTA
l'agenzia. Un platform admin senza agenzia vincolata prende 403 da
`PlatformAdminAgencyRequired`: nessuna agenzia si indovina.

IL CORPO NON PORTA NIENTE CHE DECIDA

Solo `limit`, facoltativo. `agency_id`, `now`, un destinatario, un'email, un
`appointment_id`: rifiutati con 422 (`extra = "forbid"`), non ignorati.
L'agenzia viene dalla sessione, l'ora dall'orologio del server.

503 `feature_not_migrated` finche' la 079 non e' applicata: il giro non
accoda niente, e il cron lo registra come `not_migrated`, non come guasto.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from communication.dependencies import require_dispatch_context
from operator_auth.context import OperatorContext
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import planner, repository

#: Il prefisso e' quello del dominio COMMUNICATION, gia' montato e gia'
#: coperto dalle matrici di P26 (prefissi di tenant, matrice ostile): la rotta
#: e' `/reminders/tick` sotto di esso, non un prefisso nuovo.
router = APIRouter(prefix="/api/communication", tags=["communication"])

#: Lo stesso codice macchina delle journey (P29-3C): il cron lo riconosce gia'.
FEATURE_NOT_MIGRATED = "feature_not_migrated"


class ReminderTickRequest(BaseModel):
    """Il corpo del giro: SOLO `limit`. Qualunque altro campo -> 422."""

    limit: int = Field(default=repository.MAX_CANDIDATES, ge=1,
                       le=repository.MAX_CANDIDATES)

    model_config = {"extra": "forbid"}


@router.post("/reminders/tick")
def reminders_tick(
    payload: ReminderTickRequest | None = None,
    ctx: OperatorContext = Depends(require_dispatch_context),
):
    """Un giro dei promemoria per l'agenzia della sessione."""
    limite = payload.limit if payload else repository.MAX_CANDIDATES
    try:
        return planner.tick(ctx, limit=limite)
    except planner.FeatureNotMigrated as exc:
        raise HTTPException(status_code=503, detail={
            "code": FEATURE_NOT_MIGRATED, "message": str(exc)}) from exc
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
