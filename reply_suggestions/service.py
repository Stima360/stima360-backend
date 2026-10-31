"""BLOCCO-4C - il suggerimento di risposta a UN messaggio ricevuto.

Calcolato a ogni richiesta dai dati attuali, nello scope dell'operatore che
chiede (stessa visibilita' della conversazione del 4A: un agente vede solo i
messaggi dei propri contatti), e mai salvato: nessuna riga in coda, nessun
invio, nessun registro. Il testo passa al compositore del 4B solo per mano
dell'operatore, che lo puo' cambiare e deve confermarne l'invio.

L'immobile e' quello collegato a QUEL messaggio, mai l'ultimo della
conversazione: un contatto puo' scrivere di piu' immobili. L'impronta
(`fingerprint`) riassume dati e testo usati: ricalcolata prima del
trasferimento, dice se qualcosa e' cambiato nel frattempo.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from core.database import core_cursor
from inbound_messages import repository as inbound_repository
from operator_auth.permissions import sees_all_agency_records

from . import repository, rules

NOT_FOUND_MESSAGE = "Messaggio non trovato."
OUTCOME_LABELS = {
    rules.ANSWER: "Risposta proposta",
    rules.HANDOFF: "Passaggio all'agente",
    rules.ASK_REFERENCE: "Chiedere il riferimento dell'immobile",
    rules.NONE: "Nessuna risposta proposta",
}


class SuggestionError(Exception):
    status = 400

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class NotFound(SuggestionError):
    status = 404


def _agent_scope(ctx) -> int | None:
    return None if sees_all_agency_records(ctx.role, ctx.is_platform_admin) else ctx.user_id


def _fingerprint(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def compute(cur, agency_id: int, message: dict) -> dict:
    """Il suggerimento per un messaggio GIA' letto nello scope di chi chiede.
    Riusato dal motore del 4D, che opera come sistema dell'agenzia."""
    prop = (repository.communicable_property(cur, agency_id, message["property_id"])
            if message.get("property_id") else None)
    acc = repository.accessories(cur, prop["id"]) if prop else []
    cited = repository.properties_by_codes(cur, agency_id, rules.cited_codes(message.get("text_body")))
    followup = repository.followup_assigned(cur, agency_id, message.get("contact_id"))

    problem = None
    if message.get("property_id") and prop is None:
        problem = "property_not_found"
    elif prop is not None and rules.conflicting_reference(message.get("text_body"), prop.get("code"),
                                                          [c["code"] for c in cited]):
        problem = "conflicting_reference"
    s = rules.decide(message, prop, acc, reference_problem=problem, followup_assigned=followup)

    sources = [{"field": f.field, "label": f.label, "value": f.value,
                "source": f"Scheda immobile {prop.get('code') or prop['id']} · {f.label}"} for f in s.facts]
    used = {"message_id": message["id"], "property_id": prop["id"] if prop else None, "outcome": s.outcome,
            "text": s.text, "reason_code": s.reason_code, "sources": sources}
    return {
        "message_id": message["id"],
        "outcome": s.outcome,
        "outcome_label": OUTCOME_LABELS[s.outcome],
        "text": s.text,
        "handoff": ({"code": s.reason_code, "reason": s.reason} if s.reason_code else None),
        "topics": [rules.TOPIC_NAMES[t] for t in s.topics],
        "missing": [rules.TOPIC_NAMES[t] for t in s.missing],
        "sources": sources,
        "property": ({"id": prop["id"], "code": prop.get("code"), "title": prop.get("title")} if prop else None),
        "cited_properties": [{"id": c["id"], "code": c["code"]} for c in cited],
        "followup_assigned": followup,
        "fingerprint": _fingerprint(used),
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "sending": "manual_only",
    }


def suggest(ctx, message_id: int) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        if not inbound_repository.installed(cur):
            raise NotFound("NOT_FOUND", NOT_FOUND_MESSAGE)
        message = inbound_repository.get_message(cur, agency_id, message_id, agent_user_id=_agent_scope(ctx))
        if message is None:
            raise NotFound("NOT_FOUND", NOT_FOUND_MESSAGE)
        return compute(cur, agency_id, message)
