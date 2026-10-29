"""BLOCCO-2B - generazione, archiviazione e download del PDF di una valutazione.

Un artefatto per versione APPROVATA (tabella `property_valuation_pdfs`,
migrazione 117): la prima generazione salva PDF, SHA-256, autore, data,
versione del modello e fotografia dell'intestazione agenzia; ogni download
successivo restituisce gli stessi byte. Una nuova versione approvata ha un
artefatto suo; quelli delle versioni superate restano scaricabili.

Permessi:
    scaricare   ogni operatore che puo' consultare la valutazione (agenzia)
    generare    e' una scrittura: chi puo' gestire l'immobile
                (`property.lifecycle.may_manage`), mai in sola lettura
                (ADM-5B blocca i POST in `optional_session`)

Due richieste simultanee per la stessa versione: entrambe rendono, una sola
salva (`ON CONFLICT DO NOTHING` sulla chiave), e tutte e due restituiscono
l'artefatto salvato. Al salvataggio stato, agenzia e permessi sono ricontrollati
sotto lock condiviso (immobile -> valutazione), e il trigger della 117 esige
ancora la versione approvata.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from core.database import core_cursor
from property.lifecycle import in_trash, may_manage

from . import repository
from .enums import (
    NOT_ALLOWED_MESSAGE,
    PDF_INTEGRITY_MESSAGE,
    PDF_LOGO_UNREADABLE_MESSAGE,
    PDF_NOT_APPROVED_MESSAGE,
    PDF_NOT_FOUND_MESSAGE,
    PDF_SUPERSEDED_MESSAGE,
    PDF_UNAVAILABLE_MESSAGE,
    PROPERTY_IN_TRASH_MESSAGE,
    STATUS_APPROVED,
    STATUS_DRAFT,
)
from .pdf_render import TEMPLATE_VERSION, PdfLogoError, render_valuation_pdf
from .service import (
    Conflict,
    Forbidden,
    NotFound,
    Unavailable,
    ValuationError,
    _actor,
    _audit,
    _pdf_metadata,
    _property,
    _require_installed,
    _valuation,
    serialize,
)


class PdfError(ValuationError):
    """Errore tecnico (logo illeggibile, impronta non corrispondente): 500."""

    status = 500


def _require_pdfs(cur) -> None:
    _require_installed(cur)
    if not repository.pdfs_installed(cur):
        raise Unavailable("PDF_UNAVAILABLE", PDF_UNAVAILABLE_MESSAGE)


def pdf_metadata(row) -> dict | None:
    if not row:
        return None
    return {"valuation_id": row["valuation_id"], "valuation_version": row["valuation_version"],
            **_pdf_metadata(row)}


def _approver_name(cur, user_id) -> str:
    row = repository.operator_name(cur, user_id) if user_id is not None else None
    name = " ".join(x for x in ((row or {}).get("first_name"), (row or {}).get("last_name")) if x)
    return name or f"Operatore n. {user_id}"


def _checked_writable(ctx, row, prop) -> None:
    if row["status"] == STATUS_DRAFT:
        raise Conflict("PDF_NOT_APPROVED", PDF_NOT_APPROVED_MESSAGE)
    if row["status"] != STATUS_APPROVED:
        raise Conflict("PDF_SUPERSEDED", PDF_SUPERSEDED_MESSAGE)
    if in_trash(prop):
        raise Conflict("PROPERTY_IN_TRASH", PROPERTY_IN_TRASH_MESSAGE)
    if not may_manage(ctx, prop):
        raise Forbidden("NOT_ALLOWED", NOT_ALLOWED_MESSAGE)


def _identity(ctx) -> dict:
    """L'intestazione dell'agenzia del contesto (BLOCCO-1B). Un logo assente e'
    lecito (None); un logo presente ma illeggibile o alterato e' un ERRORE, mai
    un PDF silenziosamente senza logo. Gli errori di lettura del database
    risalgono come tali."""
    from agency_profile import service as agency_profile

    identity = agency_profile.report_identity(ctx)
    logo = identity.get("logo")
    if logo is not None and hashlib.sha256(logo["data"]).hexdigest() != logo["sha256"]:
        raise PdfError("LOGO_UNREADABLE", PDF_LOGO_UNREADABLE_MESSAGE)
    return identity


def header_snapshot(identity: dict) -> dict:
    """L'intestazione usata, senza i byte del logo (sono gia' dentro il PDF)."""
    logo = identity.get("logo")
    return {
        **{k: identity.get(k) for k in ("agency_id", "agency_name", "header_name", "display_name_configured",
                                        "legal_name", "vat_number", "tax_code", "legal_address", "phone",
                                        "email", "website")},
        "logo": None if logo is None else {k: logo.get(k) for k in ("sha256", "content_type", "width", "height")},
    }


def generate_pdf(ctx, valuation_id: int) -> dict:
    """Genera e salva il PDF della versione approvata, o restituisce quello gia'
    salvato. `created` dice se questa richiesta lo ha salvato."""
    agency_id = ctx.require_agency()
    user_id = _actor(ctx)

    # 1. lettura: stato, permessi, artefatto esistente, approvatore
    with core_cursor() as (_, cur):
        _require_pdfs(cur)
        row = _valuation(cur, agency_id, valuation_id)
        prop = _property(cur, agency_id, row["property_id"])
        existing = repository.get_pdf(cur, agency_id, valuation_id)
        if existing is None:
            _checked_writable(ctx, row, prop)
        elif not may_manage(ctx, prop):
            raise Forbidden("NOT_ALLOWED", NOT_ALLOWED_MESSAGE)
        approver = _approver_name(cur, row["approved_by_user_id"])
    if existing is not None:
        return {"created": False, "pdf": pdf_metadata(existing)}

    # 2. impaginazione, fuori da ogni transazione
    identity = _identity(ctx)
    generated_at = datetime.now(timezone.utc)
    try:
        pdf_bytes = render_valuation_pdf(serialize(ctx, row, prop), identity, approver_name=approver,
                                         generated_at=generated_at)
    except PdfLogoError as exc:
        raise PdfError("LOGO_UNREADABLE", PDF_LOGO_UNREADABLE_MESSAGE) from exc
    digest = hashlib.sha256(pdf_bytes).hexdigest()

    # 3. salvataggio: tutto ricontrollato sotto lock condiviso
    with core_cursor(commit=True) as (_, cur):
        _require_pdfs(cur)
        locked_prop = repository.share_lock_property(cur, agency_id, row["property_id"])
        if locked_prop is None:
            raise NotFound("PROPERTY_NOT_FOUND", "Immobile non trovato.")
        locked = repository.share_lock_valuation(cur, agency_id, valuation_id)
        if locked is None:
            raise NotFound("VALUATION_NOT_FOUND", "Valutazione non trovata.")
        locked, locked_prop = dict(locked), dict(locked_prop)
        saved = repository.get_pdf(cur, agency_id, valuation_id)
        if saved is None:
            _checked_writable(ctx, locked, locked_prop)
            saved = repository.insert_pdf(
                cur, valuation_id=valuation_id, agency_id=agency_id, property_id=locked["property_id"],
                valuation_version=locked["version"], template_version=TEMPLATE_VERSION,
                header_snapshot=header_snapshot(identity), pdf_bytes=pdf_bytes, sha256=digest, user_id=user_id,
                generated_at=generated_at)
            created = saved is not None
            if saved is None:                      # un'altra richiesta ha salvato per prima
                saved = repository.get_pdf(cur, agency_id, valuation_id)
        else:
            created = False
    if created:
        _audit("property_valuation.pdf.generate", ctx, valuation_id=valuation_id, sha256=digest)
    return {"created": created, "pdf": pdf_metadata(saved)}


def download_pdf(ctx, valuation_id: int) -> dict:
    """I byte salvati, verificati con la loro impronta. Chiunque possa
    consultare la valutazione nella propria agenzia."""
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _require_pdfs(cur)
        row = _valuation(cur, agency_id, valuation_id)
        _property(cur, agency_id, row["property_id"])
        saved = repository.get_pdf(cur, agency_id, valuation_id, with_bytes=True)
    if saved is None:
        raise NotFound("PDF_NOT_FOUND", PDF_NOT_FOUND_MESSAGE)
    data = bytes(saved["pdf_bytes"])
    if hashlib.sha256(data).hexdigest() != saved["sha256"]:
        raise PdfError("PDF_INTEGRITY", PDF_INTEGRITY_MESSAGE)
    return {"data": data, "sha256": saved["sha256"],
            "filename": f"valutazione-immobile-{row['property_id']}-v{row['version']}.pdf"}
