"""BLOCCO-1B - dati agenzia e logo: regole, permessi, transazioni.

L'agenzia viene SEMPRE dal contesto autenticato (`ctx.require_agency()`), mai
dalla richiesta: nessuna route di questo modulo accetta un `agency_id`.

Chi puo' cosa (ruoli reali di `operator_auth.enums.AGENCY_ROLES`):

    leggere dati e logo       ogni operatore dell'agenzia (i futuri report li
                              usano per chiunque li generi)
    modificare dati e logo    agency_owner, agency_admin; il Super Admin solo
                              dopo l'ingresso dichiarato nell'agenzia (acting,
                              P28), e mai in consultazione in sola lettura
                              (ADM-5B, bloccata da `optional_session`)
    agent                     solo lettura
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from core.database import core_cursor
from operator_auth.enums import AGENCY_ROLES

from . import repository
from .enums import (
    AGENCY_NOT_FOUND_MESSAGE,
    FORBIDDEN_MESSAGE,
    LOGO_NOT_FOUND_MESSAGE,
    LOGO_UNAVAILABLE_MESSAGE,
    PROFILE_CORRUPTED_MESSAGE,
    SETTINGS_KEY,
)
from .image_validation import normalize_logo
from .schemas import AgencyProfile

log = logging.getLogger(__name__)

_OWNER, _ADMIN, _AGENT = AGENCY_ROLES
_EDITOR_ROLES = frozenset({_OWNER, _ADMIN})
#: Metadati della scrittura, salvati accanto ai dati ma non parte del profilo.
_META_KEYS = ("updated_at", "updated_by")

LOGO_URL = "/api/agency-profile/logo"


class ProfileForbidden(Exception):
    pass


class AgencyNotFound(Exception):
    pass


class ProfileCorrupted(Exception):
    pass


class LogoNotFound(Exception):
    pass


class LogoUnavailable(Exception):
    """La tabella dei loghi non esiste ancora (migrazione 115 non applicata)."""


def may_edit(ctx) -> bool:
    """Fail-closed: un ruolo sconosciuto non concede nulla."""
    return bool(getattr(ctx, "is_platform_admin", False)) or getattr(ctx, "role", None) in _EDITOR_ROLES


def require_editor(ctx) -> int:
    """L'agenzia del contesto, se il chiamante puo' modificarla; altrimenti
    rifiuta PRIMA di leggere il corpo o aprire una transazione."""
    agency_id = ctx.require_agency()
    if not may_edit(ctx):
        raise ProfileForbidden(FORBIDDEN_MESSAGE)
    return agency_id


def _author(ctx) -> str | None:
    """La convenzione P26-5/BLOCCO-1A: `operator:<id>`, mai un operatore inventato."""
    user_id = getattr(ctx, "user_id", None)
    return None if user_id is None else f"operator:{int(user_id)}"


def _audit(action: str, ctx, **fields) -> None:
    """Come `property.lifecycle._audit`: una riga di log, nomi e non valori."""
    row = {"action": action, "actor_user_id": getattr(ctx, "user_id", None),
           "actor_role": getattr(ctx, "role", None), "agency_id": getattr(ctx, "agency_id", None),
           "at": datetime.now(timezone.utc).isoformat(), **fields}
    log.info("agency_profile %s", json.dumps(row, default=str))


def stored_profile(settings) -> dict | None:
    """Il profilo salvato, validato con lo stesso modello della scrittura.

    Chiave assente -> None (agenzia non ancora configurata: nessun errore).
    Chiave presente ma fuori contratto -> ProfileCorrupted: un dato rumoroso
    si nota e si ripara, un valore inventato no (stessa regola di P27-4).
    """
    raw = (settings or {}).get(SETTINGS_KEY)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ProfileCorrupted(PROFILE_CORRUPTED_MESSAGE)
    data = {k: v for k, v in raw.items() if k not in _META_KEYS}
    try:
        return AgencyProfile(**data).model_dump()
    except Exception as exc:
        raise ProfileCorrupted(PROFILE_CORRUPTED_MESSAGE) from exc


def _logo_metadata(row) -> dict | None:
    if not row:
        return None
    uploaded = row.get("uploaded_at")
    return {
        "content_type": row["content_type"],
        "width": int(row["width"]),
        "height": int(row["height"]),
        "size_bytes": int(row["size_bytes"]),
        "sha256": row["sha256"],
        "uploaded_at": uploaded.isoformat() if hasattr(uploaded, "isoformat") else uploaded,
        # La firma nel link: un logo nuovo e' un indirizzo nuovo.
        "url": f"{LOGO_URL}?v={row['sha256'][:16]}",
    }


def _logo_metadata_if_installed(cur, agency_id):
    """(installato, metadati): senza la 115 i dati agenzia restano utilizzabili."""
    if not repository.logos_installed(cur):
        return False, None
    return True, repository.get_logo_metadata(cur, agency_id)


def _require_logos(cur) -> None:
    if not repository.logos_installed(cur):
        raise LogoUnavailable(LOGO_UNAVAILABLE_MESSAGE)


def _response(ctx, agency, logo_row, logo_available=True) -> dict:
    settings = agency["settings"] or {}
    profile = stored_profile(settings)
    raw = settings.get(SETTINGS_KEY) or {}
    return {
        "agency_id": int(agency["id"]),
        "agency_name": agency["name"],
        "configured": profile is not None,
        "profile": profile or {},
        "updated_at": raw.get("updated_at") if profile is not None else None,
        "can_edit": may_edit(ctx),
        "logo": _logo_metadata(logo_row),
        "logo_available": logo_available,
    }


def get_profile(ctx) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        agency = repository.get_agency(cur, agency_id)
        if agency is None:
            raise AgencyNotFound(AGENCY_NOT_FOUND_MESSAGE)
        available, logo = _logo_metadata_if_installed(cur, agency_id)
    return _response(ctx, agency, logo, available)


def update_profile(ctx, profile: AgencyProfile) -> dict:
    """Sostituisce SOLO `settings.report_profile`, sulla riga bloccata.

    `timezone`, `locale` e ogni altra chiave restano come sono: il nuovo JSON
    e' la riga letta sotto lock piu' questa chiave. La Platform aggiorna la
    stessa colonna bloccando la stessa riga (P27-4), quindi le due scritture
    non si sovrascrivono a vicenda.
    """
    agency_id = require_editor(ctx)
    stored = {
        **profile.model_dump(exclude_none=True),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": _author(ctx),
    }
    with core_cursor(commit=True) as (_, cur):
        agency = repository.get_agency(cur, agency_id, lock=True)
        if agency is None:
            raise AgencyNotFound(AGENCY_NOT_FOUND_MESSAGE)
        settings = {**(agency["settings"] or {}), SETTINGS_KEY: stored}
        repository.write_settings(cur, agency_id, settings)
        available, logo = _logo_metadata_if_installed(cur, agency_id)
        result = _response(ctx, {**agency, "settings": settings}, logo, available)
    _audit("agency_profile.update", ctx, fields=sorted(k for k in stored if k not in _META_KEYS))
    return result


def require_logo_upload(ctx) -> int:
    """Prima di leggere il file: permesso e modulo installato. Senza la 115 la
    risposta e' 503 per qualunque file, anche non valido."""
    agency_id = require_editor(ctx)
    with core_cursor() as (_, cur):
        _require_logos(cur)
    return agency_id


def put_logo(ctx, data: bytes) -> dict:
    """Carica o sostituisce il logo.

    Il file e' decodificato e normalizzato per intero PRIMA di aprire la
    transazione (`image_validation.normalize_logo`); si salvano i byte
    normalizzati, mai quelli caricati, e impronta e dimensione sono calcolate
    su quelli. La scrittura e' una sola statement: se qualcosa fallisce - file
    rifiutato, database, connessione - il logo precedente resta quello salvato.
    """
    agency_id = require_editor(ctx)
    logo = normalize_logo(data)
    digest = hashlib.sha256(logo.data).hexdigest()
    with core_cursor(commit=True) as (_, cur):
        if repository.get_agency(cur, agency_id) is None:
            raise AgencyNotFound(AGENCY_NOT_FOUND_MESSAGE)
        _require_logos(cur)
        row = repository.upsert_logo(
            cur, agency_id, content_type=logo.content_type, width=logo.width, height=logo.height,
            data=logo.data, sha256=digest, uploaded_by=_author(ctx))
    _audit("agency_profile.logo.put", ctx, content_type=logo.content_type, width=logo.width,
           height=logo.height, size_bytes=len(logo.data), uploaded_bytes=len(data))
    return _logo_metadata(row)


def delete_logo(ctx) -> None:
    agency_id = require_editor(ctx)
    with core_cursor(commit=True) as (_, cur):
        _require_logos(cur)
        if not repository.delete_logo(cur, agency_id):
            raise LogoNotFound(LOGO_NOT_FOUND_MESSAGE)
    _audit("agency_profile.logo.delete", ctx)


def get_logo(ctx) -> dict:
    """Il logo dell'agenzia del contesto. Nessun fallback: senza logo, 404."""
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _require_logos(cur)
        row = repository.get_logo(cur, agency_id)
    if not row:
        raise LogoNotFound(LOGO_NOT_FOUND_MESSAGE)
    return {"content_type": row["content_type"], "data": bytes(row["image_bytes"]),
            "sha256": row["sha256"]}


def report_identity(ctx) -> dict:
    """IL PUNTO DI LETTURA CONDIVISO PER I FUTURI REPORT.

    Dati e logo dell'agenzia del contesto autenticato, mai di un'altra e mai
    con un logo di ripiego. `header_name` e' il nome per i report, oppure, se
    non e' ancora stato scelto, il nome dell'anagrafica (`agencies.name`):
    `display_name_configured` lo dice a chi genera il documento. I campi non
    configurati sono None: nessun dato viene inventato.
    """
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        agency = repository.get_agency(cur, agency_id)
        if agency is None:
            raise AgencyNotFound(AGENCY_NOT_FOUND_MESSAGE)
        # Senza la 115 il report esce con il solo nome, come senza logo.
        logo = repository.get_logo(cur, agency_id) if repository.logos_installed(cur) else None
    profile = stored_profile(agency["settings"]) or {}
    fields = {name: profile.get(name) for name in AgencyProfile.model_fields}
    return {
        "agency_id": int(agency["id"]),
        "agency_name": agency["name"],
        "header_name": fields["display_name"] or agency["name"],
        "display_name_configured": fields["display_name"] is not None,
        **fields,
        "logo": None if not logo else {
            "content_type": logo["content_type"], "width": int(logo["width"]),
            "height": int(logo["height"]), "sha256": logo["sha256"],
            "data": bytes(logo["image_bytes"]),
        },
    }
