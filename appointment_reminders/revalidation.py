"""A32-2 - la revalida FINALE di un promemoria, fra il claim e il provider.

Il dispatcher la chiama per i soli messaggi `reason_code='appointment_reminder'`,
dopo il claim e immediatamente prima del trasporto. Fra l'accodamento e adesso
l'appuntamento puo' essere stato annullato, spostato, chiuso; il contatto
archiviato; l'email cambiata. Una decisione presa al planner sarebbe una
decisione su ieri: qui si RILEGGE tutto dal database, nell'agenzia del
messaggio, e si ridecide.

`revalidate(ctx, message)` restituisce None (si puo' mandare) oppure il
motivo - stabile, <= 60 caratteri, senza dati personali - con cui il
dispatcher sopprime il messaggio (`finalize_suppressed`). Nessun retry: una
soppressione e' terminale, e il planner non ricrea il messaggio perche' la
chiave dell'occorrenza e' la stessa (UNIQUE nel ledger).

I MOTIVI

Quelli della policy, quando la policy e' cio' che non regge piu'
(`status_not_allowed`, `contact_archived`, `less_than_3h_left`, ...); altrimenti
l'insieme chiuso `REVALIDATION_REASONS` qui sotto.

FAIL CLOSED

Un metadata illeggibile, un appuntamento sparito, una chiave che non torna:
nessun invio. Un guasto del database durante la rilettura NON e' mascherato:
si propaga come oggi una lettura del gate del consenso, il messaggio resta
`sending` e `recover_stale` lo chiude `indeterminate` - il provider non viene
chiamato in nessun caso.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from communication.database import communication_cursor

from . import policy, repository, template

REASON_APPOINTMENT_MISSING = "appointment_missing"
REASON_METADATA_INVALID = "reminder_metadata_invalid"
REASON_OCCURRENCE_CHANGED = "occurrence_changed"
REASON_CONTACT_CHANGED = "contact_changed"
REASON_DESTINATION_CHANGED = "destination_changed"
REASON_NOT_DUE_NOW = "reminder_not_due_now"

#: L'insieme chiuso dei motivi propri della revalida.
REVALIDATION_REASONS = frozenset({
    REASON_APPOINTMENT_MISSING, REASON_METADATA_INVALID, REASON_OCCURRENCE_CHANGED,
    REASON_CONTACT_CHANGED, REASON_DESTINATION_CHANGED, REASON_NOT_DUE_NOW,
})

#: Le chiavi ESATTE dei metadata scritti dal planner.
METADATA_KEYS = frozenset({"kind", "appointment_id", "offset", "occurrence_key",
                           "start_epoch"})
METADATA_KIND = "appointment_reminder"


def _adesso() -> datetime:
    """L'orologio della revalida. Un test lo sostituisce."""
    return datetime.now(timezone.utc)


def _intero_positivo(valore: Any) -> bool:
    return isinstance(valore, int) and not isinstance(valore, bool) and valore >= 1


def _metadata_validi(message: dict[str, Any]) -> dict[str, Any] | None:
    """(A) i metadata del planner, esatti; altrimenti None."""
    meta = message.get("metadata")
    if not isinstance(meta, dict) or set(meta) != METADATA_KEYS:
        return None
    if meta["kind"] != METADATA_KIND or meta["offset"] != policy.OFFSET_24H:
        return None
    if not _intero_positivo(meta["appointment_id"]) or not _intero_positivo(meta["start_epoch"]):
        return None
    if not isinstance(meta["occurrence_key"], str):
        return None
    if message.get("template_key") != template.TEMPLATE_KEY \
            or message.get("template_version") != template.TEMPLATE_VERSION:
        return None
    if message.get("channel") != "email" or message.get("communication_type") != "service":
        return None
    return meta


def decide(message: dict[str, Any], letto: dict[str, Any] | None,
           *, agency_id: int, now: datetime) -> str | None:
    """PURA: la decisione dati il messaggio e cio' che il database dice ADESSO.

    L'ordine dei controlli e' fisso, cosi' il motivo e' deterministico.
    """
    meta = _metadata_validi(message)                                   # A
    if meta is None:
        return REASON_METADATA_INVALID
    if message.get("agency_id") != agency_id:                           # B
        return REASON_METADATA_INVALID
    if letto is None:                                                   # B/C
        return REASON_APPOINTMENT_MISSING
    appuntamento, contatto = letto["appointment"], letto["contact"]
    if appuntamento["id"] != meta["appointment_id"] \
            or appuntamento["agency_id"] != agency_id:                  # C
        return REASON_APPOINTMENT_MISSING
    chiave = policy.occurrence_key(appuntamento["id"], appuntamento["start_at"])
    if int(appuntamento["start_at"].timestamp()) != meta["start_epoch"] \
            or chiave != meta["occurrence_key"]:                        # D
        return REASON_OCCURRENCE_CHANGED
    if appuntamento["contact_id"] is not None \
            and appuntamento["contact_id"] != message.get("contact_id"):  # I
        return REASON_CONTACT_CHANGED
    motivo = policy.ineligibility_reason(appuntamento, contatto)        # E F G H J
    if motivo is not None:
        return motivo
    if contatto["email"] != message.get("destination_snapshot"):        # K
        return REASON_DESTINATION_CHANGED
    decisione = policy.send_decision(now=now, start_at=appuntamento["start_at"],
                                     created_at=appuntamento["created_at"])
    if decisione.reason is not None:                                    # L M
        return decisione.reason
    if appuntamento["start_at"] - now < policy.MIN_LEAD:                # M
        return policy.REASON_LEAD_TOO_SHORT
    if not policy.in_window(now) or not decisione.due:                  # N O
        return REASON_NOT_DUE_NOW
    if message.get("idempotency_key") != chiave:                        # P
        return REASON_OCCURRENCE_CHANGED
    return None


def revalidate(ctx, message: dict[str, Any]) -> str | None:
    """Rilegge dal database (nell'agenzia del contesto) e decide. Solo letture."""
    now = _adesso()
    meta = _metadata_validi(message)
    letto = None
    if meta is not None and message.get("agency_id") == ctx.agency_id:
        with communication_cursor() as (_conn, cur):
            letto = repository.appointment_for_revalidation(
                cur, ctx.agency_id, meta["appointment_id"])
    return decide(message, letto, agency_id=ctx.agency_id, now=now)
