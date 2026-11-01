"""GESTIONE MULTIPLA DEI CONSENSI (decisione del titolare).

Un quadro di tutti i contatti dell'agenzia con un indirizzo email, con lo
stato del consenso marketing EMAIL di ciascuno, e la registrazione dello
STESSO consenso per piu' contatti in una volta.

COSA NON E'. Non e' un modo per concedere a tutti: ogni contatto scelto
riceve un evento suo, con la stessa prova, la stessa data, la stessa
informativa e la DICHIARAZIONE dell'operatore, scritti uno per uno dalla
stessa funzione della scheda (`consent.operator_service.register_grant`),
con tutti i suoi rifiuti (prova che non puo' essere il recapito del cliente,
informativa che deve coprire il canale, revoca o opt-out successivi). Un
contatto rifiutato non ferma gli altri: l'esito e' per contatto.

Dopo ogni consenso EMAIL registrato parte la partenza immediata del V3
(`avvio_immediato`), con le sue esclusioni e il limite dei destinatari TEST.

STATI DEL QUADRO (per il canale email):
    valid         consenso email valido (la guardia dice si')
    revoked       revocato, rifiutato o disiscritto (la guardia dice no dopo una decisione)
    to_document   spunta del sito senza prova sufficiente, o nessun consenso: serve
                  una prova (riconoscimento) o una registrazione con dichiarazione
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from consent import operator_service
from consent.exceptions import ConflictError, NotFoundError, ValidationError
from consent.guard import can_send_marketing_bulk

from . import avvio_immediato
from .database import communication_cursor

logger = logging.getLogger(__name__)

STATO_VALIDO, STATO_REVOCATO, STATO_DA_DOCUMENTARE = "valid", "revoked", "to_document"
MAX_CONTATTI = 200

#: Le ragioni della guardia che dicono "ha DECISO di no", non "non ha mai detto si'".
_RAGIONI_REVOCA = ("revoked", "refused", "optout", "opted_out", "unsubscribed", "do_not_contact", "banned")


def _stato(decisione) -> str:
    if decisione is not None and decisione.allowed:
        return STATO_VALIDO
    ragione = (getattr(decisione, "reason", None) or "").lower()
    if any(r in ragione for r in _RAGIONI_REVOCA):
        return STATO_REVOCATO
    return STATO_DA_DOCUMENTARE


def overview(ctx, *, limit: int = 2000) -> dict[str, Any]:
    """Tutti i contatti attivi dell'agenzia con email, con lo stato email."""
    agenzia = ctx.require_agency()
    with communication_cursor() as (_conn, cur):
        cur.execute(
            """SELECT c.id, c.display_name, c.email_normalized,
                      EXISTS (SELECT 1 FROM communication_enrollments e
                               WHERE e.agency_id = c.agency_id AND e.contact_id = c.id
                                 AND e.status IN ('active', 'paused')) AS in_sequenza,
                      EXISTS (SELECT 1 FROM communication_automation_controls k
                               WHERE k.agency_id = c.agency_id AND k.contact_id = c.id AND k.paused) AS in_pausa,
                      EXISTS (SELECT 1 FROM consent_events ce
                               WHERE ce.contact_id = c.id AND ce.purpose = 'marketing' AND ce.channel IS NULL
                                 AND ce.source = 'public_stima' AND ce.decision = 'granted') AS spunta_sito
                 FROM contacts c
                WHERE c.agency_id = %s AND c.status = 'active' AND c.deleted_at IS NULL
                  AND c.archived_at IS NULL AND c.email_normalized IS NOT NULL
                ORDER BY c.display_name NULLS LAST, c.id
                LIMIT %s""",
            (agenzia, limit))
        righe = [dict(r) for r in cur.fetchall()]
    decisioni = can_send_marketing_bulk(ctx, [r["id"] for r in righe], channel="email") if righe else {}
    contatti = []
    for r in righe:
        decisione = decisioni.get(r["id"])
        contatti.append({
            "contact_id": r["id"], "display_name": r["display_name"],
            "email_masked": _maschera(r["email_normalized"]),
            "state": _stato(decisione), "reason": getattr(decisione, "reason", None),
            "in_sequence": bool(r["in_sequenza"]), "paused": bool(r["in_pausa"]),
            "site_tick": bool(r["spunta_sito"]),
        })
    conteggi = {s: sum(1 for c in contatti if c["state"] == s)
                for s in (STATO_VALIDO, STATO_REVOCATO, STATO_DA_DOCUMENTARE)}
    return {"contacts": contatti, "counts": conteggi, "total": len(contatti)}


def bulk_grant(ctx, *, contact_ids, channels, decided_at: datetime, evidence_type: str, evidence_ref: str,
               notice_id: int, note: str | None, attestation: bool) -> dict[str, Any]:
    """Lo stesso consenso per piu' contatti, uno per uno. Esito per contatto."""
    if attestation is not True:
        raise ValidationError("l'operatore deve dichiarare che il consenso e' stato realmente ottenuto")
    ids = sorted({int(c) for c in contact_ids})
    if not ids or len(ids) > MAX_CONTATTI:
        raise ValidationError(f"da 1 a {MAX_CONTATTI} contatti per volta")
    esiti = []
    for cid in ids:
        esito: dict[str, Any] = {"contact_id": cid}
        try:
            registrato = operator_service.register_grant(
                ctx, cid, channels=channels, decided_at=decided_at, evidence_type=evidence_type,
                evidence_ref=evidence_ref, notice_id=notice_id, note=note, attestation=True)
            esito.update({"recorded": True, "created": registrato["created"],
                          "channels": registrato["channels"]})
            if "email" in registrato["channels"]:
                esito["start"] = avvio_immediato.avvia(ctx, cid)
        except (ValidationError, ConflictError, NotFoundError) as exc:
            esito.update({"recorded": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - un contatto non ferma gli altri
            logger.exception("consent_bulk_grant_failed contact_id=%s", cid)
            esito.update({"recorded": False, "error": type(exc).__name__})
        esiti.append(esito)
    return {"results": esiti,
            "recorded": sum(1 for e in esiti if e.get("recorded")),
            "rejected": sum(1 for e in esiti if not e.get("recorded"))}


def _maschera(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    nome, dominio = email.split("@", 1)
    return f"{nome[:1]}•••@{dominio}"


__all__ = ["overview", "bulk_grant", "STATO_VALIDO", "STATO_REVOCATO", "STATO_DA_DOCUMENTARE", "MAX_CONTATTI"]
