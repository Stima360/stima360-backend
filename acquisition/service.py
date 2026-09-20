"""LMC-15 - il servizio: deriva l'attore e lo scope, non li riceve.

E' l'unico punto in cui `operator_user_id` entra nel dominio, e vi entra da
`ctx.user_id` - cioe' dalla sessione autenticata. Il canale Basic legacy non
ha una persona dietro (`OperatorContext.user_id` e' `None` di proposito: e'
un segreto condiviso), quindi su quel canale il ponte non si scrive: un
registro di acquisizione senza attore sarebbe un audit che non dice chi.
"""
from __future__ import annotations

from core.exceptions import PermissionDenied

from . import repository


def _attore(ctx) -> int:
    """La persona dietro la richiesta, o un rifiuto esplicito."""
    user_id = getattr(ctx, "user_id", None)
    if user_id is None:
        raise PermissionDenied(
            "Questa operazione richiede una sessione operatore: "
            "il registro di acquisizione non ammette un attore anonimo"
        )
    return int(user_id)


def link_property_to_stima(ctx, stima_id, payload):
    return repository.create_acquisition_link(
        ctx.require_agency(), stima_id=stima_id,
        property_id=payload.property_id, actor_user_id=_attore(ctx))


def record_mandate(ctx, acquisition_id, payload):
    return repository.record_mandate(
        ctx.require_agency(), acquisition_id=acquisition_id,
        signed_at=payload.mandate_signed_at, reference=payload.mandate_reference,
        actor_user_id=_attore(ctx))


def revoke_link(ctx, acquisition_id, payload):
    return repository.revoke_acquisition_link(
        ctx.require_agency(), acquisition_id=acquisition_id,
        reason=payload.revoked_reason, actor_user_id=_attore(ctx))


def schedule_inspection(ctx, stima_id, payload):
    return repository.create_inspection(
        ctx.require_agency(), stima_id=stima_id,
        scheduled_for=payload.scheduled_for, actor_user_id=_attore(ctx))


def record_completed_inspection(ctx, stima_id, payload):
    return repository.create_completed_inspection(
        ctx.require_agency(), stima_id=stima_id,
        completed_at=payload.completed_at, actor_user_id=_attore(ctx))


def complete_inspection(ctx, inspection_id, payload):
    return repository.complete_inspection(
        ctx.require_agency(), inspection_id=inspection_id,
        completed_at=payload.completed_at, actor_user_id=_attore(ctx))


def cancel_inspection(ctx, inspection_id, payload):
    return repository.cancel_inspection(
        ctx.require_agency(), inspection_id=inspection_id,
        reason=payload.cancelled_reason, actor_user_id=_attore(ctx))
