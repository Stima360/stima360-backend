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


# ---------------------------------------------------------------------------
# I SOPRALLUOGHI - A30-2P: FACADE VERSO L'AGENDA
#
# Stesso contratto (tenant da `require_agency()`, attore da `ctx.user_id`,
# stessi corpi, stesse risposte), ma la scrittura passa da `appointments`, la
# fonte autorevole: `stima_inspections` ne e' la proiezione, nella stessa
# transazione (appointments/lmc15_facade.py). Le funzioni pubbliche del
# repository LMC-15 restano per compatibilita', ma queste rotte non le usano.
# ---------------------------------------------------------------------------

def _facade():
    # Import differito: `appointments` importa a sua volta il repository
    # LMC-15; cosi' il pacchetto acquisition resta importabile da solo.
    from appointments import lmc15_facade
    return lmc15_facade


def schedule_inspection(ctx, stima_id, payload):
    return _facade().schedule_inspection(
        ctx.require_agency(), stima_id=stima_id,
        scheduled_for=payload.scheduled_for, actor_user_id=_attore(ctx))


def record_completed_inspection(ctx, stima_id, payload):
    return _facade().record_completed_inspection(
        ctx.require_agency(), stima_id=stima_id,
        completed_at=payload.completed_at, actor_user_id=_attore(ctx))


def complete_inspection(ctx, inspection_id, payload):
    return _facade().complete_inspection(
        ctx.require_agency(), inspection_id=inspection_id,
        completed_at=payload.completed_at, actor_user_id=_attore(ctx))


def cancel_inspection(ctx, inspection_id, payload):
    return _facade().cancel_inspection(
        ctx.require_agency(), inspection_id=inspection_id,
        reason=payload.cancelled_reason, actor_user_id=_attore(ctx))
