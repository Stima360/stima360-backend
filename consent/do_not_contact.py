"""BLOCCO-6 - il DIVIETO TOTALE DI CONTATTO di un contatto (migrazione 124).

Distinto dalla rinuncia al marketing, e i due non si convertono mai l'uno
nell'altro:

    rinuncia marketing   (consent_events, communication_optouts, STOP)
        ferma il MARKETING su un canale; le comunicazioni di servizio richieste
        dall'interessato restano possibili.
    divieto di contatto  (contact_do_not_contact)
        ferma OGNI comunicazione in uscita verso quel contatto, di qualunque
        tipo (servizio compreso) e su qualunque canale, finche' un operatore
        non lo revoca con una nota.

Il divieto si legge al momento dell'invio, sempre dal database, da ogni
percorso che spedisce: il dispatch delle comunicazioni, la risposta manuale
WhatsApp (4B) e il motore delle risposte automatiche. Fail closed: se la
tabella esiste ma la lettura non riesce, chi chiama non invia.

Chi registra e chi revoca e' l'operatore della sessione (mai un id dal
browser); il contatto e' cercato nello scope dell'agenzia: quello di
un'altra agenzia non esiste.
"""
from __future__ import annotations

from typing import Any

from core.exceptions import PermissionDenied

from . import repository
from .database import consent_cursor
from .exceptions import ConflictError, ValidationError

SOURCES = ("subject_request", "agency_decision")
SOURCE_LABELS = {"subject_request": "Richiesta dell'interessato", "agency_decision": "Decisione dell'agenzia"}
TEXT_MIN = 5
TEXT_MAX = 1000
REASON = "do_not_contact"


def installed(cur) -> bool:
    cur.execute("SELECT to_regclass('public.contact_do_not_contact') IS NOT NULL AS presente")
    return bool(cur.fetchone()["presente"])


def active(cur, agency_id: int, contact_id: int | None) -> bool:
    """C'e' un divieto aperto? Senza la 124 non ne esiste nessuno."""
    if contact_id is None or not installed(cur):
        return False
    cur.execute("SELECT 1 FROM contact_do_not_contact WHERE agency_id = %s AND contact_id = %s AND lifted_at IS NULL",
                (agency_id, contact_id))
    return cur.fetchone() is not None


def _operator(ctx) -> int:
    if getattr(ctx, "user_id", None) is None or getattr(ctx, "agency_id", None) is None:
        raise PermissionDenied("a do-not-contact order requires an identified operator of the agency")
    return int(ctx.user_id)


def _text(value, name: str) -> str:
    text = (value or "").strip() if isinstance(value, str) else ""
    if not TEXT_MIN <= len(text) <= TEXT_MAX:
        raise ValidationError(f"{name} must be {TEXT_MIN}-{TEXT_MAX} characters")
    return text


def _view(row: dict | None) -> dict[str, Any]:
    if row is None:
        return {"active": False}
    return {"active": row["lifted_at"] is None, "id": row["id"], "source": row["source"],
            "source_label": SOURCE_LABELS.get(row["source"], row["source"]), "reason": row["reason"],
            "set_by_user_id": row["set_by_user_id"], "set_at": row["set_at"].isoformat(),
            "lifted_by_user_id": row["lifted_by_user_id"],
            "lifted_at": row["lifted_at"].isoformat() if row["lifted_at"] else None, "lift_note": row["lift_note"]}


def _require(cur):
    if not installed(cur):
        raise ConflictError("do-not-contact is not installed on this environment (migration 124)")


def state(ctx, contact_id: int) -> dict[str, Any]:
    agency_id = ctx.require_agency()
    with consent_cursor() as (_, cur):
        repository.select_contact(cur, ctx, contact_id)
        if not installed(cur):
            return {"installed": False, "active": False, "history": []}
        cur.execute("SELECT * FROM contact_do_not_contact WHERE agency_id = %s AND contact_id = %s "
                    "ORDER BY set_at DESC, id DESC LIMIT 20", (agency_id, contact_id))
        rows = [dict(r) for r in cur.fetchall()]
    current = next((r for r in rows if r["lifted_at"] is None), None)
    return {"installed": True, **_view(current), "history": [_view(r) for r in rows]}


def set_ban(ctx, contact_id: int, *, source: str, reason: str) -> dict[str, Any]:
    user_id = _operator(ctx)
    agency_id = ctx.require_agency()
    if source not in SOURCES:
        raise ValidationError(f"source must be one of {', '.join(SOURCES)}")
    reason = _text(reason, "reason")
    with consent_cursor(commit=True) as (_, cur):
        _require(cur)
        repository.select_contact(cur, ctx, contact_id)
        cur.execute("INSERT INTO contact_do_not_contact (agency_id, contact_id, source, reason, set_by_user_id) "
                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (agency_id, contact_id) WHERE lifted_at IS NULL "
                    "DO NOTHING RETURNING *", (agency_id, contact_id, source, reason, user_id))
        if cur.fetchone() is None:
            raise ConflictError("a do-not-contact order is already active for this contact")
    return state(ctx, contact_id)


def lift_ban(ctx, contact_id: int, *, note: str) -> dict[str, Any]:
    user_id = _operator(ctx)
    agency_id = ctx.require_agency()
    note = _text(note, "note")
    with consent_cursor(commit=True) as (_, cur):
        _require(cur)
        repository.select_contact(cur, ctx, contact_id)
        cur.execute("UPDATE contact_do_not_contact SET lifted_at = NOW(), lifted_by_user_id = %s, lift_note = %s "
                    "WHERE agency_id = %s AND contact_id = %s AND lifted_at IS NULL RETURNING id",
                    (user_id, note, agency_id, contact_id))
        if cur.fetchone() is None:
            raise ConflictError("no active do-not-contact order for this contact")
    return state(ctx, contact_id)
