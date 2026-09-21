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
    ERROR_CODES,
    ERROR_OUTCOME_UNKNOWN,
    ACTOR_SYSTEM,
    CHANNELS,
    CHANNELS_WITH_SUBJECT,
    COMMUNICATION_TYPES,
    INITIAL_STATUS,
    MODES,
    OPERATOR_MODES,
    REASON_CODES,
    TYPE_MARKETING,
    WRITABLE_DIRECTIONS,
)
from .exceptions import ConflictError, ValidationError

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

    # P29-2.6E: il genitore di lifecycle, validato qui e non solo dal CHECK
    # della 065.
    #
    # Il database rifiuterebbe comunque entrambi i casi, ma un ValidationError
    # dice al chiamante COSA ha sbagliato, mentre una CheckViolation gli dice
    # solo il nome di un vincolo - e arriva dentro la sua transazione, che
    # potrebbe contenere altro. La regola e' la stessa in tutti e due i posti,
    # e il test la confronta con il testo della migration.
    if dati["communication_type"] == TYPE_MARKETING and dati.get("contact_id") is None:
        raise ValidationError(
            "contact_id is required for marketing: consent is read on a CRM "
            "contact, and a marketing message whose consent cannot be checked "
            "must not exist"
        )
    if dati.get("contact_id") is None and dati.get("stima_id") is None:
        raise ValidationError(
            "a message without contact_id needs a stima_id: it would otherwise "
            "have no lifecycle parent to be purged with"
        )

    actor_type, actor_user_id = _attore(ctx, mode)

    provenienza = (dati.get("enrollment_id"), dati.get("step_no"), dati.get("run_no"))
    if any(v is not None for v in provenienza) and not all(
            isinstance(v, int) and v >= 1 for v in provenienza):
        raise ValidationError(
            "enrollment_id, step_no and run_no go together, as positive integers, "
            "or not at all"
        )

    metadata = dati.get("metadata")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValidationError("metadata must be a mapping")

    return {
        "agency_id": ctx.require_agency(),
        "contact_id": None if dati["contact_id"] is None else int(dati["contact_id"]),
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
        "enrollment_id": provenienza[0], "step_no": provenienza[1], "run_no": provenienza[2],
    }


def enqueue(
    ctx,
    *,
    contact_id: int | None,
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
    enrollment_id: int | None = None,
    step_no: int | None = None,
    run_no: int | None = None,
    cur=None,
) -> dict[str, Any]:
    """Mette in coda un messaggio. Restituisce ``{'message': riga, 'created': bool}``.

    P29-3B: `enrollment_id`, `step_no` e `run_no` dicono DA QUALE passo di
    quale iscrizione nasce il messaggio. Vanno insieme o non vanno affatto; un
    messaggio manuale non li porta. Il dispatcher non li legge: non sa cosa
    sia una journey, e non deve saperlo.

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
        "enrollment_id": enrollment_id, "step_no": step_no, "run_no": run_no,
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
    if prepared["contact_id"] is not None:
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


def send_now(ctx, message_id: int, *, cur=None) -> dict[str, Any]:
    """P29-3D - anticipa un messaggio GIA' IN CODA a subito.

    NON spedisce: sposta `scheduled_at` ad adesso, e il prossimo giro del
    dispatcher lo trovera' dovuto. La differenza non e' formale - e' che il
    consenso, il claim e il trasporto restano dove sono, e questa funzione
    non diventa una seconda strada per far partire una email.

    `queued` e' l'unico stato che si puo' anticipare: un messaggio gia'
    spedito non si rimanda, uno annullato non si resuscita. Solleva
    `NotFoundError` se non esiste in questa agenzia, `ConflictError` se non
    e' piu' in coda - gli stessi due casi distinti di `cancel`.
    """
    def _anticipa(c):
        riga = repository.reschedule_queued(c, ctx, message_id, quando=repository.utcnow())
        if riga is not None:
            return riga
        # `select_message` solleva gia' `NotFoundError` per un messaggio che
        # non esiste in questa agenzia: se torna, il messaggio c'e' e il
        # problema e' il suo stato.
        corrente = repository.select_message(c, ctx, message_id)
        raise ConflictError(
            f"message {message_id} is {corrente['status']}, not queued: only a queued "
            "message can be moved to the front")

    if cur is not None:
        return _anticipa(cur)
    with communication_cursor(commit=True) as (_conn, proprio_cur):
        return _anticipa(proprio_cur)


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


# ===========================================================================
# P29-2.3 - claim, fencing, tentativi, recovery.
#
# Il runtime DB-safe che un dispatcher usera'. Qui non c'e' ancora nessun
# dispatcher, nessun provider e nessun gate del consenso: si costruiscono le
# transizioni e le si rende sicure PRIMA che esista qualcuno che le chiami.
#
# `provider` in queste firme e' una ETICHETTA che finisce in una colonna, non un
# oggetto e non un modulo: dice CHI e' stato chiamato, e questa fase non chiama
# nessuno.
# ===========================================================================

import uuid as _uuid

from .enums import (
    DEFAULT_STALE_AFTER_SECONDS,
    OUTCOME_ACCEPTED,
    OUTCOME_INDETERMINATE,
    OUTCOME_REJECTED,
    STATUS_FAILED,
    STATUS_INDETERMINATE,
    STATUS_SENT,
    STATUS_SUPPRESSED,
)

#: Quanti messaggi per giro. Ogni invio e' una chiamata di rete da secondi: un
#: batch grande allunga la finestra fra il claim e l'ultimo invio, e con essa la
#: finestra degli stale.
DEFAULT_CLAIM_BATCH = 10
MAX_CLAIM_BATCH = 50


def _token() -> str:
    """Un token per messaggio. uuid4: non deve essere indovinabile, perche' chi
    lo indovina puo' finalizzare un messaggio che non ha reclamato."""
    return str(_uuid.uuid4())


def claim_due(ctx, *, provider: str, channel: str, limit: int = DEFAULT_CLAIM_BATCH,
              cur=None, token_factory=_token) -> list[dict[str, Any]]:
    """Reclama i messaggi dovuti. Messaggio e tentativo nello stesso commit.

    La transazione deve restare BREVE: nessuna chiamata di rete puo' starci
    dentro, e il provider si chiama DOPO il commit. Passando `cur` il chiamante
    decide lui quando committare - che e' cio' che un dispatcher fara'; senza,
    questa funzione apre la propria e la committa subito.

    `channel` E' OBBLIGATORIO, E NON HA UN DEFAULT

    Non `channel=None` che significherebbe "tutti": un default permissivo e' la
    porta da cui un worker futuro torna, per distrazione, a reclamare messaggi
    che non sa mandare. Chi volesse davvero tutti i canali deve scriverlo a
    mano, canale per canale, e allora e' una scelta e non un incidente.
    """
    if not isinstance(limit, int) or limit < 1 or limit > MAX_CLAIM_BATCH:
        raise ValidationError(f"limit must be between 1 and {MAX_CLAIM_BATCH}")
    provider = _testo(provider, "provider", obbligatorio=True, massimo=40)
    channel = _testo(channel, "channel", obbligatorio=True, massimo=20)
    if channel not in CHANNELS:
        raise ValidationError(f"channel must be one of {', '.join(sorted(CHANNELS))}")

    def _lavora(c):
        return repository.claim_due(c, ctx, limit=limit, provider=provider,
                                    channel=channel, token_factory=token_factory)

    if cur is not None:
        return _lavora(cur)
    with communication_cursor(commit=True) as (_conn, proprio):
        return _lavora(proprio)


#: Cosa dichiara il risultato tardivo, quando la finalizzazione arriva tardi.
#: E' cio' che il worker STAVA per dire: l'osservazione resta, l'autorita' no.
ESITO_TARDIVO = {
    STATUS_SENT: OUTCOME_ACCEPTED,
    STATUS_FAILED: OUTCOME_REJECTED,
    STATUS_INDETERMINATE: OUTCOME_INDETERMINATE,
    STATUS_SUPPRESSED: OUTCOME_REJECTED,
}


def _record_late_result(c, ctx, message_id, claim_token, *, status,
                        provider_message_id=None, error_code=None, error_detail=None):
    """C18: NON e' un ingresso pubblico.

    Una riga `late_result` nasce come CONSEGUENZA di una finalizzazione che ha
    trovato l'ownership persa, mai come atto di un chiamante. Esposta, avrebbe
    permesso di fabbricare a mano righe di audit che raccontano invii mai
    tentati - e l'audit dei tentativi vale esattamente quanto e' difficile
    scriverci dentro una cosa falsa.

    Quando un webhook o un provider asincrono avranno bisogno di un ingresso
    proprio, lo si aprira' nella loro fase, con i loro controlli.
    """
    return repository.record_late_result(
        c, ctx, message_id, claim_token, outcome=ESITO_TARDIVO[status],
        provider_message_id=provider_message_id, error_code=error_code,
        error_detail=error_detail)


def _finalizza(ctx, message_id, claim_token, *, cur, status, **campi):
    """La finalizzazione, e cio' che succede quando arriva tardi.

    Restituisce l'esito se l'ownership era valida, `None` se era persa. `None`
    non e' un errore: e' la risposta alla domanda "sono ancora io il
    proprietario", e il batch continua.

    Sul percorso tardivo si registra cio' che il worker ha osservato - con il
    provider e il numero di tentativo letti dal claim vero, mai ricevuti - e non
    si tocca il messaggio. Se quel token non ha mai posseduto il messaggio non
    si scrive niente: un CAS mancato non e' una licenza a scrivere nell'audit.
    """
    def _lavora(c):
        esito = repository.finalize(c, ctx, message_id, claim_token,
                                    status=status, **campi)
        if esito is not None:
            return esito
        _record_late_result(
            c, ctx, message_id, claim_token, status=status,
            provider_message_id=campi.get("provider_message_id"),
            error_code=campi.get("error_code"),
            error_detail=campi.get("error_detail"))
        return None

    if cur is not None:
        return _lavora(cur)
    with communication_cursor(commit=True) as (_conn, proprio):
        return _lavora(proprio)


def finalize_sent(ctx, message_id: int, claim_token: str, *,
                  provider_message_id: str | None = None, cur=None):
    """Il provider ha preso in carico il messaggio.

    Non prende un `provider`: quello lo ha gia' registrato il claim, e riceverlo
    di nuovo permetterebbe di attribuire il tentativo a un sistema diverso da
    quello che lo ha reclamato - con il suo `provider_message_id` al seguito.

    Restituisce `None` se l'ownership era persa. NON e' un errore: e' la
    risposta alla domanda "sono ancora io il proprietario", e in quel caso il
    risultato osservato viene registrato come tardivo, da solo.
    """
    return _finalizza(ctx, message_id, claim_token, cur=cur, status=STATUS_SENT,
                      provider_message_id=provider_message_id)


def finalize_failed(ctx, message_id: int, claim_token: str, *,
                    error_code: str, error_detail: str | None = None, cur=None):
    """Insuccesso CERTO: sappiamo che il provider non ha preso in carico nulla.

    Non si usa quando l'esito e' ignoto - per quello c'e'
    `finalize_indeterminate`, ed e' la distinzione su cui poggia tutto C1.
    """
    _in_insieme(error_code, ERROR_CODES, "error_code")
    return _finalizza(ctx, message_id, claim_token, cur=cur, status=STATUS_FAILED,
                      error_code=error_code, error_detail=error_detail)


def finalize_indeterminate(ctx, message_id: int, claim_token: str, *,
                           error_code: str = ERROR_OUTCOME_UNKNOWN,
                           error_detail: str | None = None, cur=None):
    """Non sappiamo se il messaggio sia partito.

    Timeout, connessione caduta, risposta illeggibile. E' terminale per
    l'automazione: da qui non si rientra in coda, con nessun `attempt_count` e a
    nessuna condizione.
    """
    _in_insieme(error_code, ERROR_CODES, "error_code")
    return _finalizza(ctx, message_id, claim_token, cur=cur, status=STATUS_INDETERMINATE,
                      error_code=error_code, error_detail=error_detail)


def finalize_suppressed(ctx, message_id: int, claim_token: str, *, reason: str, cur=None):
    """Il messaggio non parte, e la ragione non e' del provider.

    Il gate del consenso - P29-2.4 - nega DOPO il claim, quindi il tentativo e'
    gia' aperto e va chiuso. Qui non si e' chiamato nessuno, e infatti non c'e'
    un `provider`: il tentativo si chiude `rejected`/`definite`, perche' l'invio
    non e' avvenuto e lo sappiamo con certezza, e il PERCHE' finisce su
    `suppressed_reason`.
    """
    reason = _testo(reason, "reason", obbligatorio=True, massimo=60)
    return _finalizza(ctx, message_id, claim_token, cur=cur, status=STATUS_SUPPRESSED,
                      suppressed_reason=reason)


def recover_stale(ctx, *, stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
                  limit: int = DEFAULT_CLAIM_BATCH, cur=None) -> list[dict[str, Any]]:
    """Chiude i claim rimasti senza esito. Uno per uno, e condizionalmente.

    Non si distingue - e non si puo' distinguere - uno stale morto PRIMA della
    chiamata al provider da uno morto DOPO: il claim committa prima del
    dispatch, quindi da fuori le due situazioni sono identiche. La policy e'
    quindi fail safe: `indeterminate`, mai un reinvio automatico cieco.

    I candidati che nel frattempo sono stati finalizzati dal loro worker
    vengono semplicemente saltati - il compare-and-set restituisce `None` - e
    non compaiono nel risultato.
    """
    if not isinstance(stale_after_seconds, int) or stale_after_seconds < 60:
        raise ValidationError("stale_after_seconds must be an integer of at least 60")
    if not isinstance(limit, int) or limit < 1 or limit > MAX_CLAIM_BATCH:
        raise ValidationError(f"limit must be between 1 and {MAX_CLAIM_BATCH}")

    def _lavora(c):
        recuperati = []
        for candidato in repository.stale_candidates(
                c, ctx, stale_after_seconds=stale_after_seconds, limit=limit):
            esito = repository.recover_stale_message(
                c, ctx, candidato["id"], candidato["claim_token"],
                stale_after_seconds=stale_after_seconds)
            if esito is not None:
                recuperati.append(esito)
        return recuperati

    if cur is not None:
        return _lavora(cur)
    with communication_cursor(commit=True) as (_conn, proprio):
        return _lavora(proprio)


def list_attempts(ctx, message_id: int, *, cur=None) -> list[dict[str, Any]]:
    """I tentativi di un messaggio, in ordine di storia."""
    def _lavora(c):
        repository.select_message(c, ctx, message_id)
        return repository.list_attempts(c, ctx, message_id)

    if cur is not None:
        return _lavora(cur)
    with communication_cursor() as (_conn, proprio):
        return _lavora(proprio)
