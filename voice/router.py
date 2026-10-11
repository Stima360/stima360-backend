"""STIMA Voice Fase 5 - le API (`/api/voice`).

Fase 7: montato in `main.py` come gli altri domini (mount
`require_authenticated_operator`). Resta spento finche' non sono attivi tutti
i livelli di `voice.service.guard`.

Autenticazione: `require_operator` (sessione), come Agenda e CORE. Agenzia,
operatore e ruolo vengono SOLO da li'. I corpi sono chiusi (`extra=forbid`):
nessun campo per agenzia, attore, assegnatario o id decisi dal client, salvo
la scelta fra candidati che l'esecutore convalida.

Errori: sempre `{"detail", "code"}`, mai un messaggio tecnico grezzo; nessun
testo dettato, prompt o risposta del modello nei log o nelle risposte
d'errore.
"""
from __future__ import annotations

import logging
import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from core.exceptions import PermissionDenied
from operator_auth.context import OperatorContext, PlatformAdminAgencyRequired
from operator_auth.dependencies import require_operator

from . import ledger, reconcile, service
from . import provider as voice_provider
from .executor import (
    ACTOR_REQUIRED, CHOICE_INVALID, CHOICE_REQUIRED, DISABLED, PLATFORM_ADMIN_BLOCKED, VoiceRefused,
)

log = logging.getLogger("stima360.voice.api")
router = APIRouter(prefix="/api/voice", tags=["voice"])


class _Corpo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommandIn(_Corpo):
    client_command_id: uuid.UUID
    text: str = Field(..., min_length=1, max_length=voice_provider.MAX_TEXT)


class StepChoice(_Corpo):
    ordinal: int = Field(..., ge=1, le=12)
    choices: dict[str, int] | None = None


class ConfirmIn(_Corpo):
    all: bool = False
    steps: list[StepChoice] = Field(default_factory=list, max_length=12)


class ReconcileIn(_Corpo):
    outcome: Literal["present", "absent"]
    record_id: int | None = Field(None, ge=1)


def get_provider():
    """Il fornitore AI dalla configurazione del server (i test lo
    sostituiscono). Nessuna configurazione = 503, nessuna interpretazione."""
    return voice_provider.from_env()


_STATI = {
    DISABLED: 403, service.GLOBALLY_DISABLED: 403, PLATFORM_ADMIN_BLOCKED: 403, ACTOR_REQUIRED: 403,
    service.PILOT_USER_REQUIRED: 403,
    service.DAILY_LIMIT: 429, CHOICE_REQUIRED: 422, CHOICE_INVALID: 422, service.TEXT_INVALID: 422,
}


def _risposta(status: int, code: str, detail: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail, "code": code, **extra})


def _errore(exc: Exception) -> JSONResponse:
    if isinstance(exc, VoiceRefused):
        return _risposta(_STATI.get(exc.code, 409), exc.code, str(exc))
    if isinstance(exc, ledger.CommandNotFound):
        return _risposta(404, exc.code, "Comando vocale non trovato")
    if isinstance(exc, ledger.LedgerError):
        return _risposta(409, exc.code, str(exc))
    if isinstance(exc, reconcile.ReconcileRefused):
        return _risposta(409, exc.code, str(exc))
    if isinstance(exc, voice_provider.ProviderNotConfigured):
        return _risposta(503, "VOICE_PROVIDER_NOT_CONFIGURED", "Interpretazione vocale non disponibile")
    if isinstance(exc, voice_provider.ProviderUnavailable):
        return _risposta(503, "VOICE_PROVIDER_UNAVAILABLE",
                         "Interpretazione non disponibile: riprova con la stessa richiesta")
    if isinstance(exc, voice_provider.ProviderMalformed):
        return _risposta(502, "VOICE_MODEL_OUTPUT_INVALID",
                         "Il comando non e' stato interpretato in modo valido: nulla e' stato eseguito")
    if isinstance(exc, PlatformAdminAgencyRequired):
        return _risposta(403, "PLATFORM_ADMIN_AGENCY_REQUIRED", "Scegli un'agenzia")
    if isinstance(exc, PermissionDenied):
        return _risposta(403, getattr(exc, "code", None) or "FORBIDDEN", "Operazione non permessa")
    log.error("voice api: errore interno %s", type(exc).__name__)
    return _risposta(500, "VOICE_INTERNAL_ERROR", "Errore interno di STIMA Voice")


def _x(funzione, *args, **kwargs):
    try:
        return funzione(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - tradotto, mai grezzo
        return _errore(exc)


@router.get("/status")
def voice_status(ctx: OperatorContext = Depends(require_operator)):
    """Fase 6: {enabled, speech_input}. Mai un errore: spento per qualunque
    motivo. L'interfaccia mostra STIMA Voice solo con `enabled: true`."""
    return service.status(ctx)


@router.post("/commands")
def submit_command(body: CommandIn, ctx: OperatorContext = Depends(require_operator)):
    """Testo -> piano (con domande e conferme). Stessa chiave = stesso comando."""
    try:
        fornitore = get_provider()
    except Exception as exc:  # noqa: BLE001
        try:
            service.guard(ctx)            # spento vince su «fornitore non configurato»
        except Exception as spento:  # noqa: BLE001
            return _errore(spento)
        return _errore(exc)
    return _x(service.submit, ctx, body.client_command_id, body.text, provider=fornitore)


@router.get("/commands/{command_id}")
def get_command(command_id: int, ctx: OperatorContext = Depends(require_operator)):
    return _x(service.view, ctx, command_id)


@router.post("/commands/{command_id}/confirm")
def confirm(command_id: int, body: ConfirmIn, ctx: OperatorContext = Depends(require_operator)):
    return _x(service.confirm, ctx, command_id, steps=[s.model_dump() for s in body.steps], all_steps=body.all)


@router.post("/commands/{command_id}/execute")
def execute(command_id: int, ctx: OperatorContext = Depends(require_operator)):
    return _x(service.execute, ctx, command_id)


@router.post("/commands/{command_id}/cancel")
def cancel(command_id: int, ctx: OperatorContext = Depends(require_operator)):
    return _x(service.cancel, ctx, command_id)


@router.post("/commands/{command_id}/steps/{ordinal}/retry")
def retry(command_id: int, ordinal: int, ctx: OperatorContext = Depends(require_operator)):
    return _x(service.retry, ctx, command_id, ordinal)


@router.get("/commands/{command_id}/undo")
def undo_status(command_id: int, ctx: OperatorContext = Depends(require_operator)):
    return _x(service.undo_status, ctx, command_id)


@router.post("/commands/{command_id}/undo")
def undo_command(command_id: int, ctx: OperatorContext = Depends(require_operator)):
    return _x(service.undo_command, ctx, command_id)


@router.post("/commands/{command_id}/steps/{ordinal}/undo")
def undo_step(command_id: int, ordinal: int, ctx: OperatorContext = Depends(require_operator)):
    return _x(service.undo_step, ctx, command_id, ordinal)


@router.get("/commands/{command_id}/steps/{ordinal}/reconcile")
def reconcile_inspect(command_id: int, ordinal: int, ctx: OperatorContext = Depends(require_operator)):
    return _x(service.reconcile_inspect, ctx, command_id, ordinal)


@router.post("/commands/{command_id}/steps/{ordinal}/reconcile")
def reconcile_decide(command_id: int, ordinal: int, body: ReconcileIn,
                     ctx: OperatorContext = Depends(require_operator)):
    return _x(service.reconcile_decide, ctx, command_id, ordinal, outcome=body.outcome, record_id=body.record_id)
