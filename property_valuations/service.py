"""BLOCCO-2A - regole, permessi e transazioni delle valutazioni dell'agente.

L'agenzia viene sempre dal contesto (`ctx.require_agency()`); autore e
approvatore sono `ctx.user_id`. Nessun `agency_id`, `created_by` o
`approved_by` arriva mai dal client.

Permessi (nessun ampliamento di quelli sugli immobili):

    leggere            ogni operatore dell'agenzia (come la scheda immobile)
    creare una bozza   chi puo' gestire l'immobile (`property.lifecycle.may_manage`:
    o una versione     owner/admin/Super Admin in acting su tutta l'agenzia,
                       l'agente sui soli immobili assegnati a lui)
    modificare         come sopra; un agente solo le bozze che ha creato
    approvare          agency_owner, agency_admin, Super Admin in acting
    proprietario       nessun accesso (il portale non legge questa tabella)

Il prezzo richiesto dell'immobile non viene mai toccato: nessuna statement di
questo modulo scrive su `properties`.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal

from psycopg2 import errors

from core.database import core_cursor
from operator_auth import permissions
from property.lifecycle import in_trash, may_manage

from . import repository
from .enums import (
    ACTOR_REQUIRED_MESSAGE,
    ALREADY_APPROVED_MESSAGE,
    APPROVE_FORBIDDEN_MESSAGE,
    DRAFT_EXISTS_MESSAGE,
    INCOMPLETE_MESSAGE,
    METHOD_LABEL,
    NOT_ALLOWED_MESSAGE,
    NOT_DRAFT_MESSAGE,
    NOT_OWN_DRAFT_MESSAGE,
    PROPERTY_IN_TRASH_MESSAGE,
    PROPERTY_NOT_FOUND_MESSAGE,
    SOURCE_NOT_APPROVED_MESSAGE,
    STATUS_APPROVED,
    STATUS_DRAFT,
    VALUATION_NOT_FOUND_MESSAGE,
    VALUATIONS_UNAVAILABLE_MESSAGE,
    VERSION_CONFLICT_MESSAGE,
)
from .schemas import ValuationContent, amounts, approval_issues

log = logging.getLogger(__name__)

#: Le colonne dell'immobile fotografate nella valutazione: i dati usati.
SNAPSHOT_FIELDS = (
    "id", "code", "title", "property_type", "record_kind", "commercial_status",
    "address", "civic_number", "city", "province", "postal_code", "region", "microzone",
    "surface_sqm", "commercial_surface_sqm", "rooms", "bedrooms", "bathrooms", "floor", "total_floors",
    "elevator", "year_built", "condition", "energy_class", "furnishing", "heating", "condo_fees",
    "asking_price", "mandate_type", "mandate_start", "mandate_end",
)


class ValuationError(Exception):
    """Base: `code` stabile per il client, messaggio in italiano."""

    status = 400

    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code = code
        self.extra = extra


class NotFound(ValuationError):
    status = 404


class Forbidden(ValuationError):
    status = 403


class Conflict(ValuationError):
    status = 409


class Incomplete(ValuationError):
    status = 422


class Unavailable(ValuationError):
    status = 503


# ---------------------------------------------------------------------------
# utilita'
# ---------------------------------------------------------------------------

def _json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def property_snapshot(prop: dict) -> dict:
    return {name: _json_value(prop.get(name)) for name in SNAPSHOT_FIELDS if name in prop}


def _sees_all(ctx) -> bool:
    return permissions.sees_all_agency_records(getattr(ctx, "role", None), getattr(ctx, "is_platform_admin", False))


def _actor(ctx) -> int:
    user_id = getattr(ctx, "user_id", None)
    if user_id is None:
        raise Forbidden("ACTOR_REQUIRED", ACTOR_REQUIRED_MESSAGE)
    return int(user_id)


def _audit(action: str, ctx, **fields) -> None:
    """Come `property.lifecycle._audit`: una riga di log, identificativi e non contenuti."""
    row = {"action": action, "actor_user_id": getattr(ctx, "user_id", None),
           "actor_role": getattr(ctx, "role", None), "agency_id": getattr(ctx, "agency_id", None),
           "at": datetime.now(timezone.utc).isoformat(), **fields}
    log.info("property_valuations %s", json.dumps(row, default=str))


def _require_installed(cur) -> None:
    if not repository.installed(cur):
        raise Unavailable("VALUATIONS_UNAVAILABLE", VALUATIONS_UNAVAILABLE_MESSAGE)


def _property(cur, agency_id: int, property_id: int, *, lock: bool = False) -> dict:
    prop = repository.get_property(cur, agency_id, property_id, lock=lock)
    if prop is None:
        raise NotFound("PROPERTY_NOT_FOUND", PROPERTY_NOT_FOUND_MESSAGE)
    return dict(prop)


def _writable_property(ctx, cur, agency_id: int, property_id: int, *, lock: bool) -> dict:
    prop = _property(cur, agency_id, property_id, lock=lock)
    if in_trash(prop):
        raise Conflict("PROPERTY_IN_TRASH", PROPERTY_IN_TRASH_MESSAGE)
    if not may_manage(ctx, prop):
        raise Forbidden("NOT_ALLOWED", NOT_ALLOWED_MESSAGE)
    return prop


def _valuation(cur, agency_id: int, valuation_id: int, *, lock: bool = False) -> dict:
    row = repository.get(cur, agency_id, valuation_id, lock=lock)
    if row is None:
        raise NotFound("VALUATION_NOT_FOUND", VALUATION_NOT_FOUND_MESSAGE)
    return dict(row)


def _content(row) -> ValuationContent:
    return ValuationContent(**(row.get("content") or {}))


def _may_edit_draft(ctx, row, prop) -> bool:
    if row["status"] != STATUS_DRAFT or in_trash(prop) or not may_manage(ctx, prop):
        return False
    return _sees_all(ctx) or row["created_by_user_id"] == getattr(ctx, "user_id", None)


def serialize(ctx, row, prop) -> dict:
    content = _content(row)
    out = {key: _json_value(value) for key, value in row.items() if key != "content"}
    out["content"] = content.model_dump(mode="json")
    out["method_label"] = METHOD_LABEL
    out["sale_evaluated"] = content.sale is not None
    out["rent_evaluated"] = content.rent is not None
    out["approval_issues"] = approval_issues(content) if row["status"] == STATUS_DRAFT else []
    out["can_edit"] = _may_edit_draft(ctx, row, prop)
    out["can_approve"] = row["status"] == STATUS_DRAFT and _sees_all(ctx) and not in_trash(prop)
    out["can_create_version"] = (row["status"] != STATUS_DRAFT and not in_trash(prop) and may_manage(ctx, prop))
    return out


# ---------------------------------------------------------------------------
# letture
# ---------------------------------------------------------------------------

def list_valuations(ctx, property_id: int) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _require_installed(cur)
        prop = _property(cur, agency_id, property_id)
        rows = repository.list_for_property(cur, agency_id, property_id)
    items = [serialize(ctx, dict(r), prop) for r in rows]
    return {
        "property_id": property_id,
        "method_label": METHOD_LABEL,
        "items": items,
        "has_draft": any(i["status"] == STATUS_DRAFT for i in items),
        "can_create": (not in_trash(prop) and may_manage(ctx, prop)
                       and not any(i["status"] == STATUS_DRAFT for i in items)),
    }


def get_valuation(ctx, valuation_id: int) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _require_installed(cur)
        row = _valuation(cur, agency_id, valuation_id)
        prop = _property(cur, agency_id, row["property_id"])
    return serialize(ctx, row, prop)


# ---------------------------------------------------------------------------
# scritture
# ---------------------------------------------------------------------------

def create_draft(ctx, property_id: int, content: ValuationContent) -> dict:
    """Una bozza nuova sull'immobile, anche senza incarico. Al massimo una bozza
    per immobile: se ce n'e' gia' una si modifica quella."""
    agency_id = ctx.require_agency()
    user_id = _actor(ctx)
    with core_cursor(commit=True) as (_, cur):
        _require_installed(cur)
        prop = _writable_property(ctx, cur, agency_id, property_id, lock=True)
        if repository.has_draft(cur, agency_id, property_id):
            raise Conflict("DRAFT_EXISTS", DRAFT_EXISTS_MESSAGE)
        try:
            row = repository.insert_draft(
                cur, agency_id=agency_id, property_id=property_id,
                version=repository.next_version(cur, agency_id, property_id), previous_valuation_id=None,
                content=content.model_dump(mode="json"), snapshot=property_snapshot(prop),
                amounts=amounts(content), user_id=user_id)
        except errors.UniqueViolation as exc:
            raise Conflict("DRAFT_EXISTS", DRAFT_EXISTS_MESSAGE) from exc
        result = serialize(ctx, dict(row), prop)
    _audit("property_valuation.create", ctx, property_id=property_id, valuation_id=result["id"])
    return result


def update_draft(ctx, valuation_id: int, row_version: int, content: ValuationContent) -> dict:
    """Modifica una bozza alla versione attesa (controllo delle modifiche
    concorrenti). Una valutazione approvata o superata non si modifica."""
    agency_id = ctx.require_agency()
    user_id = _actor(ctx)
    with core_cursor(commit=True) as (_, cur):
        _require_installed(cur)
        current = _valuation(cur, agency_id, valuation_id, lock=True)
        prop = _writable_property(ctx, cur, agency_id, current["property_id"], lock=False)
        if current["status"] != STATUS_DRAFT:
            raise Conflict("NOT_DRAFT", NOT_DRAFT_MESSAGE)
        if not _sees_all(ctx) and current["created_by_user_id"] != user_id:
            raise Forbidden("NOT_OWN_DRAFT", NOT_OWN_DRAFT_MESSAGE)
        if current["row_version"] != row_version:
            raise Conflict("VERSION_CONFLICT", VERSION_CONFLICT_MESSAGE, current_row_version=current["row_version"])
        row = repository.update_draft(
            cur, agency_id=agency_id, valuation_id=valuation_id, row_version=row_version,
            content=content.model_dump(mode="json"), amounts=amounts(content), user_id=user_id)
        if row is None:            # difesa: la riga e' bloccata, non dovrebbe accadere
            raise Conflict("VERSION_CONFLICT", VERSION_CONFLICT_MESSAGE)
        result = serialize(ctx, dict(row), prop)
    _audit("property_valuation.update", ctx, valuation_id=valuation_id)
    return result


def approve(ctx, valuation_id: int, row_version: int) -> dict:
    """Approvazione ATOMICA, in una transazione:

        1. immobile bloccato (stesso primo lock di creazioni e altre approvazioni);
        2. bozza bloccata, ancora bozza e alla versione attesa;
        3. completezza verificata (`approval_issues`);
        4. l'approvata precedente diventa superata;
        5. la bozza diventa approvata, con la fotografia dell'immobile.

    Una seconda richiesta per la stessa bozza trova lo stato `approved` e
    risponde 409 ALREADY_APPROVED: nessuna doppia approvazione.
    """
    agency_id = ctx.require_agency()
    user_id = _actor(ctx)
    if not _sees_all(ctx):
        raise Forbidden("APPROVE_FORBIDDEN", APPROVE_FORBIDDEN_MESSAGE)
    with core_cursor(commit=True) as (_, cur):
        _require_installed(cur)
        current = _valuation(cur, agency_id, valuation_id)
        prop = _writable_property(ctx, cur, agency_id, current["property_id"], lock=True)
        current = _valuation(cur, agency_id, valuation_id, lock=True)
        if current["status"] == STATUS_APPROVED:
            raise Conflict("ALREADY_APPROVED", ALREADY_APPROVED_MESSAGE)
        if current["status"] != STATUS_DRAFT:
            raise Conflict("NOT_DRAFT", NOT_DRAFT_MESSAGE)
        if current["row_version"] != row_version:
            raise Conflict("VERSION_CONFLICT", VERSION_CONFLICT_MESSAGE, current_row_version=current["row_version"])
        issues = approval_issues(_content(current))
        if issues:
            raise Incomplete("INCOMPLETE", INCOMPLETE_MESSAGE, issues=issues)
        superseded = repository.supersede_approved(
            cur, agency_id=agency_id, property_id=current["property_id"], by_valuation_id=valuation_id)
        row = repository.approve(cur, agency_id=agency_id, valuation_id=valuation_id, user_id=user_id,
                                 snapshot=property_snapshot(prop))
        if row is None:            # difesa: la riga e' bloccata, non dovrebbe accadere
            raise Conflict("NOT_DRAFT", NOT_DRAFT_MESSAGE)
        result = serialize(ctx, dict(row), prop)
    _audit("property_valuation.approve", ctx, valuation_id=valuation_id,
           superseded_valuation_id=superseded["id"] if superseded else None)
    return result


def create_version(ctx, valuation_id: int) -> dict:
    """Una bozza nuova collegata a una versione approvata o superata, con lo
    stesso contenuto da rivedere. L'approvata resta approvata finche' la nuova
    non viene approvata."""
    agency_id = ctx.require_agency()
    user_id = _actor(ctx)
    with core_cursor(commit=True) as (_, cur):
        _require_installed(cur)
        source = _valuation(cur, agency_id, valuation_id)
        prop = _writable_property(ctx, cur, agency_id, source["property_id"], lock=True)
        if source["status"] == STATUS_DRAFT:
            raise Conflict("SOURCE_NOT_APPROVED", SOURCE_NOT_APPROVED_MESSAGE)
        if repository.has_draft(cur, agency_id, source["property_id"]):
            raise Conflict("DRAFT_EXISTS", DRAFT_EXISTS_MESSAGE)
        content = _content(source)
        try:
            row = repository.insert_draft(
                cur, agency_id=agency_id, property_id=source["property_id"],
                version=repository.next_version(cur, agency_id, source["property_id"]),
                previous_valuation_id=valuation_id, content=content.model_dump(mode="json"),
                snapshot=property_snapshot(prop), amounts=amounts(content), user_id=user_id)
        except errors.UniqueViolation as exc:
            raise Conflict("DRAFT_EXISTS", DRAFT_EXISTS_MESSAGE) from exc
        result = serialize(ctx, dict(row), prop)
    _audit("property_valuation.new_version", ctx, from_valuation_id=valuation_id, valuation_id=result["id"])
    return result
