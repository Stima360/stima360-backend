"""P29-2.2 - le due sole scritture del dominio, e le letture per contatto.

    enqueue(ctx, ..., cur=None)   crea l'intenzione di comunicare
    cancel(ctx, message_id)       la ritira, finche' e' ancora in coda

NESSUNA RETE PASSA DI QUI

E' il primo dei cinque confini del design. `enqueue` scrive una riga e ritorna:
non chiama un provider, non rende un template, non interroga il consenso, non
decide se il messaggio si possa mandare. Decide soltanto che ESISTE l'intenzione
di mandarlo.

Il confine e' strutturale e non di disciplina: in questo pacchetto non esiste un
import di `requests` o di `smtplib`, e una sentinella lo verifica.

`cur=None`, E PERCHE' CONTA

Passando un cursore, `enqueue` scrive DENTRO la transazione del chiamante. E'
la forma che `consent.repository.record_decision_with_cursor` ha gia' stabilito
in questo repository, e la ragione qui e' la stessa: un'intenzione di
comunicazione nata da un atto che poi viene annullato non deve sopravvivere a
quell'atto. Se la stima fallisce dopo il commit del messaggio, resta in coda una
mail che parla di qualcosa che non e' successo.

Senza cursore, `enqueue` apre la propria transazione e la committa: e' il caso
del chiamante che non ne ha una.

QUELLO CHE IL CHIAMANTE NON PUO' SCRIVERE

Non `status`: ogni messaggio nasce `queued`, e non esiste un argomento per dire
altrimenti. Non `claim_token`, `attempt_count`, `provider`, `sent_at`, `failed_at`
o `failure_class`: appartengono al dispatch, che in questa fase non esiste. Non
`actor_type`: si DERIVA dal modo, perche' "chi ha premuto invia" e' gia' la
domanda a cui `mode` risponde, e due campi che possono contraddirsi sono un campo
di troppo.
"""

from __future__ import annotations

import json
from typing import Any

from . import repository
from .database import communication_cursor
from .enums import (
    ACTOR_OPERATOR,
    ACTOR_SYSTEM,
    CHANNELS,
    CHANNELS_WITH_SUBJECT,
    COMMUNICATION_TYPES,
    INITIAL_STATUS,
    MODES,
    OPERATOR_MODES,
    REASON_CODES,
    WRITABLE_DIRECTIONS,
)
from .exceptions import ValidationError

#: Quanti messaggi restituisce una lettura per contatto se il chiamante non lo
#: dice. Un tetto esiste perche' una lista senza limite e' una query che cresce
#: con lo storico e che un giorno qualcuno chiamera' su un contatto con
#: diecimila righe.
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 500


def _testo(valore, nome: str, *, obbligatorio: bool, massimo: int) -> str | None:
    if valore is None:
        if obbligatorio:
            raise ValidationError(f"{nome} is required")
        return None
    if not isinstance(valore, str):
        raise ValidationError(f"{nome} must be a string")
    pulito = valore.strip()
    if not pulito:
        if obbligatorio:
            raise ValidationError(f"{nome} must not be blank")
        return None
    if len(pulito) > massimo:
        raise ValidationError(f"{nome} exceeds {massimo} characters")
    return pulito


def _in_insieme(valore, insieme, nome: str) -> str:
    if valore not in insieme:
        raise ValidationError(
            f"{nome} must be one of {', '.join(sorted(insieme))}; got {valore!r}"
        )
    return valore


def _attore(ctx, mode: str) -> tuple[str, int | None]:
    """`actor_type` e `actor_user_id` DERIVATI dal modo, non ricevuti.

    La tabella del design dice chi ha premuto invia: `manual` e `assisted` sono
    atti di una persona, `automatic` e' un atto del sistema. Ricevere
    `actor_type` come parametro accanto a `mode` significherebbe permettere a un
    chiamante di dichiarare un invio automatico firmato da un operatore, o un
    invio manuale di nessuno - due righe che il database accetterebbe e che
    nessuno saprebbe piu' interpretare.

    Un atto di un operatore porta il nome dell'operatore: e' la stessa regola di
    `consent_events_operator_ref_chk`, e qui e' vera per costruzione perche'
    l'identita' viene dal contesto autenticato e non da un argomento.
    """
    if mode not in OPERATOR_MODES:
        return ACTOR_SYSTEM, None
    user_id = getattr(ctx, "user_id", None)
    if user_id is None:
        raise ValidationError(
            f"mode {mode!r} is an operator act and requires an authenticated "
            "operator; this context carries no user_id"
        )
    return ACTOR_OPERATOR, int(user_id)


def _validated(ctx, dati: dict[str, Any]) -> dict[str, Any]:
    """Tutti i controlli, in un posto solo, prima di toccare il database.

    I CHECK della migration 064 restano l'ultima parola - e' giusto che sia il
    database a garantire cio' che garantisce - ma un rifiuto del database arriva
    come `psycopg2.errors.CheckViolation` con il nome di un vincolo, e un
    chiamante non deve dover leggere i nomi dei vincoli per capire di aver
    passato un canale sbagliato.
    """
    channel = _in_insieme(dati.get("channel"), CHANNELS, "channel")
    mode = _in_insieme(dati.get("mode"), MODES, "mode")
    _in_insieme(dati.get("communication_type"), COMMUNICATION_TYPES, "communication_type")
    _in_insieme(dati.get("reason_code"), REASON_CODES, "reason_code")

    # `inbound` e' ammesso dal database perche' l'inbound di domani entri in
    # QUESTO ledger; nessun percorso di P29-2 lo scrive, e questo modulo non fa
    # eccezione.
    direction = _in_insieme(dati.get("direction"), WRITABLE_DIRECTIONS, "direction")

    subject = _testo(dati.get("subject_snapshot"), "subject_snapshot",
                     obbligatorio=False, massimo=300)
    # L'oggetto esiste per le email e non esiste per WhatsApp. Il bicondizionale
    # e' quello del CHECK `communication_messages_subject_chk`: un WhatsApp con
    # un oggetto e un'email senza sono entrambi errori, e per ragioni diverse.
    if channel in CHANNELS_WITH_SUBJECT and subject is None:
        raise ValidationError(f"subject_snapshot is required for channel {channel!r}")
    if channel not in CHANNELS_WITH_SUBJECT and subject is not None:
        raise ValidationError(f"channel {channel!r} carries no subject_snapshot")

    template_key = _testo(dati.get("template_key"), "template_key",
                          obbligatorio=False, massimo=80)
    template_version = dati.get("template_version")
    if template_version is not None and not isinstance(template_version, int):
        raise ValidationError("template_version must be an integer")
    # Le due meta' dell'identita' di un template stanno insieme o non stanno: una
    # versione senza chiave non si sa leggere, una chiave senza versione non dice
    # quale testo era in uso.
    if (template_key is None) != (template_version is None):
        raise ValidationError(
            "template_key and template_version go together or neither is given"
        )

    actor_type, actor_user_id = _attore(ctx, mode)

    metadata = dati.get("metadata")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValidationError("metadata must be a mapping")

    return {
        "agency_id": ctx.require_agency(),
        "contact_id": int(dati["contact_id"]),
        "lead_id": dati.get("lead_id"),
        "stima_id": dati.get("stima_id"),
        "property_id": dati.get("property_id"),
        "channel": channel,
        "direction": direction,
        "communication_type": dati["communication_type"],
        "mode": mode,
        "reason_code": dati["reason_code"],
        "template_key": template_key,
        "template_version": template_version,
        "subject_snapshot": subject,
        "rendered_body": _testo(dati.get("rendered_body"), "rendered_body",
                                obbligatorio=True, massimo=1_000_000),
        "destination_snapshot": _testo(dati.get("destination_snapshot"),
                                       "destination_snapshot",
                                       obbligatorio=True, massimo=320),
        "actor_type": actor_type,
        "actor_user_id": actor_user_id,
        "scheduled_at": dati.get("scheduled_at") or repository.utcnow(),
        "idempotency_key": _testo(dati.get("idempotency_key"), "idempotency_key",
                                  obbligatorio=True, massimo=300),
        "metadata": json.dumps(metadata),
    }


def enqueue(
    ctx,
    *,
    contact_id: int,
    channel: str,
    communication_type: str,
    mode: str,
    reason_code: str,
    rendered_body: str,
    destination_snapshot: str,
    idempotency_key: str,
    direction: str = "outbound",
    subject_snapshot: str | None = None,
    template_key: str | None = None,
    template_version: int | None = None,
    lead_id: int | None = None,
    stima_id: int | None = None,
    property_id: int | None = None,
    scheduled_at=None,
    metadata: dict[str, Any] | None = None,
    cur=None,
) -> dict[str, Any]:
    """Mette in coda un messaggio. Restituisce ``{'message': riga, 'created': bool}``.

    Ripetibile: chiamarla due volte con la stessa `idempotency_key` nella stessa
    agenzia restituisce lo STESSO messaggio con ``created=False``, e non ne crea
    un secondo. La stessa chiave in un'altra agenzia non e' un conflitto e
    produce un messaggio suo, perche' l'unicita' e' per tenant.

    Con `cur` scrive nella transazione del chiamante e non committa: la scelta di
    cosa salvare insieme resta a chi possiede la transazione. Senza, apre la
    propria e la committa.

    Non manda niente e non decide se si possa mandare. Il consenso si interroga
    immediatamente prima del dispatch, che e' P29-2.4, e non qui: una decisione
    presa adesso su un messaggio che partira' domani sarebbe una decisione su
    ieri.
    """
    dati = {
        "contact_id": contact_id, "channel": channel, "direction": direction,
        "communication_type": communication_type, "mode": mode,
        "reason_code": reason_code, "rendered_body": rendered_body,
        "destination_snapshot": destination_snapshot,
        "idempotency_key": idempotency_key, "subject_snapshot": subject_snapshot,
        "template_key": template_key, "template_version": template_version,
        "lead_id": lead_id, "stima_id": stima_id, "property_id": property_id,
        "scheduled_at": scheduled_at, "metadata": metadata,
    }
    prepared = _validated(ctx, dati)

    if cur is not None:
        return _enqueue_with_cursor(cur, ctx, prepared)

    with communication_cursor(commit=True) as (_conn, proprio_cur):
        return _enqueue_with_cursor(proprio_cur, ctx, prepared)


def _enqueue_with_cursor(cur, ctx, prepared: dict[str, Any]) -> dict[str, Any]:
    """I due passi, sul cursore ricevuto. Nessun commit, nessun rollback.

    Il contatto si risolve PRIMA di inserire, e nello scope del chiamante: la FK
    composita garantisce gia' che il messaggio non finisca nell'agenzia
    sbagliata, ma non che un agente possa accodare un messaggio a un contatto che
    non gli e' assegnato. Quella regola vive in `core.scope`, e qui la si
    interroga invece di riscriverla.
    """
    repository.contact_in_scope(cur, ctx, prepared["contact_id"])
    message, created = repository.insert_message(cur, ctx, prepared)
    return {"message": message, "created": created}


def cancel(ctx, message_id: int, *, cur=None) -> dict[str, Any]:
    """Ritira un messaggio dalla coda. `queued` -> `cancelled`, e solo quella.

    Solleva `NotFoundError` se il messaggio non esiste o non e' di questa
    agenzia, `ConflictError` se non e' piu' in coda - perche' i due casi
    richiedono cose diverse a chi chiama, e un unico errore generico lo
    costringerebbe a indovinare.
    """
    if cur is not None:
        return repository.cancel_queued(cur, ctx, message_id)
    with communication_cursor(commit=True) as (_conn, proprio_cur):
        return repository.cancel_queued(proprio_cur, ctx, message_id)


def get_message(ctx, message_id: int, *, cur=None) -> dict[str, Any]:
    """Un messaggio, nello scope."""
    if cur is not None:
        return repository.select_message(cur, ctx, message_id)
    with communication_cursor() as (_conn, proprio_cur):
        return repository.select_message(proprio_cur, ctx, message_id)


def list_for_contact(ctx, contact_id: int, *, limit: int = DEFAULT_LIST_LIMIT, cur=None):
    """I messaggi di un contatto, dal piu' recente.

    Il contatto si risolve nello scope prima di leggere: senza, un agente
    potrebbe chiedere i messaggi di un contatto che non gli e' assegnato e
    riceverli, perche' il predicato di agenzia da solo non lo restringe.
    """
    if not isinstance(limit, int) or limit < 1 or limit > MAX_LIST_LIMIT:
        raise ValidationError(f"limit must be between 1 and {MAX_LIST_LIMIT}")

    def _leggi(c):
        repository.contact_in_scope(c, ctx, contact_id)
        return repository.list_by_contact(c, ctx, contact_id, limit=limit)

    if cur is not None:
        return _leggi(cur)
    with communication_cursor() as (_conn, proprio_cur):
        return _leggi(proprio_cur)


#: Riesportato perche' un test possa affermare lo stato iniziale senza conoscere
#: il nome della colonna.
INITIAL_STATUS = INITIAL_STATUS
