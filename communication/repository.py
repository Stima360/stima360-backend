"""P29-2.2 - l'unico SQL del dominio COMMUNICATION.

Tre scritture e tre letture, e nessun'altra. Nessuna di queste funzioni apre
una connessione: ricevono il cursore e lo usano, perche' la transazione
appartiene a chi la possiede.

COSA NON C'E' QUI, E NON PER DIMENTICANZA

Non c'e' `claim_due()`, non ci sono le transizioni di stato del dispatch, non
c'e' `FOR UPDATE SKIP LOCKED`, non si scrive `claim_token` e non si tocca
`communication_attempts`. Sono P29-2.3, e anticiparle qui significherebbe avere
un claim che nessun dispatcher chiama - cioe' codice non esercitato in una
posizione dove un difetto costa un doppio invio.

L'UNICA TRANSIZIONE DI QUESTA FASE

    (nessuno) -> queued     enqueue
    queued    -> cancelled  cancel_queued

`cancel_queued` e' un compare-and-set sullo stato atteso, non una UPDATE
incondizionata, e il `rowcount` e' cio' che distingue "annullato" da "era gia'
uscito dalla coda". E' la stessa forma che il design prescrive per la
finalizzazione di P29-2.3, e usarla gia' qui non e' anticipazione: e' l'unico
modo corretto di scrivere QUESTA transizione, perche' fra la lettura e la
scrittura un claim puo' essersi preso il messaggio.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.scope import scoped_predicate as core_scoped_predicate

from .enums import CANCELLABLE_STATUSES, INITIAL_STATUS, STATUS_CANCELLED
from .exceptions import ConflictError, NotFoundError
from .scope import ProgrammingError, communication_scoped_source

#: Le colonne che `enqueue` scrive. Tutte le altre di `communication_messages`
#: appartengono al dispatch e restano al loro default: `status` a 'queued',
#: `attempt_count` a 0, e NULL per claim, provider, esito e timestamp di invio.
#: Elencarle qui - invece di costruire la INSERT dai dati ricevuti - e' cio' che
#: rende impossibile a un chiamante di scrivere una colonna che non gli spetta.
INSERTABLE_COLUMNS = (
    "agency_id", "contact_id", "lead_id", "stima_id", "property_id",
    "channel", "direction", "communication_type", "mode", "reason_code",
    "template_key", "template_version", "subject_snapshot", "rendered_body",
    "destination_snapshot", "actor_type", "actor_user_id",
    "scheduled_at", "idempotency_key", "metadata",
    # P29-3B: la PROVENIENZA di un messaggio di journey. Tre colonne insieme o
    # nessuna (CHECK della 071), scritte all'INSERT e mai piu' (guardia).
    "enrollment_id", "step_no", "run_no",
)

#: Le tre colonne di provenienza vengono NOMINATE nella INSERT solo quando il
#: messaggio le porta: un messaggio manuale o di sistema non le scrive, e non
#: le nomina. Cosi' un ledger senza la 071 - il codice si distribuisce prima
#: che la migration venga applicata - continua ad accettare ogni messaggio
#: che accettava prima, e solo un messaggio di journey richiede lo schema.
PROVENANCE_COLUMNS = ("enrollment_id", "step_no", "run_no")


def utcnow() -> datetime:
    """Adesso, con il fuso. Un istante senza fuso e' un istante di cui non si sa
    dire l'ora vera, e questo ledger esiste per essere riletto."""
    return datetime.now(timezone.utc)


def _row(riga) -> dict[str, Any] | None:
    return dict(riga) if riga is not None else None


def contact_in_scope(cur, ctx, contact_id: int) -> dict[str, Any]:
    """Il contatto, se il chiamante puo' vederlo. Altrimenti NotFoundError.

    Passa da `core.scope`, non da `communication.scope`: e' un contatto, e la
    regola su chi vede quali contatti - compreso il restringimento di un agente
    ai propri - e' gia' scritta li'. Riscriverla qui vorrebbe dire avere due
    risposte alla stessa domanda il giorno in cui una delle due cambia.

    Nessun `FOR UPDATE`. `consent.repository.lock_contact` prende il lock perche'
    deve poi scrivere la proiezione sulla stessa riga; qui non si scrive nulla su
    `contacts`, e un lock che non serve e' una serializzazione regalata.
    """
    predicate, params = core_scoped_predicate(ctx, "contacts", "c")
    cur.execute(
        f"SELECT c.id, c.agency_id FROM contacts c WHERE c.id = %s AND {predicate}",
        [contact_id] + params,
    )
    contact = _row(cur.fetchone())
    if contact is None:
        raise NotFoundError(f"contact {contact_id} not found in this scope")
    return contact


def insert_message(cur, ctx, prepared: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Inserisce il messaggio. Restituisce (riga, creato).

    ON CONFLICT (agency_id, idempotency_key) DO NOTHING. Quando la chiave era
    gia' usata NELLA STESSA AGENZIA non si scrive nulla e si restituisce la riga
    esistente, letta nello scope: l'operazione e' ripetibile per costruzione e
    non per disciplina del chiamante.

    La chiave e' unica PER TENANT e non globalmente, quindi la stessa stringa in
    un'altra agenzia non e' un conflitto e produce un secondo messaggio. E' la
    decisione C3 del design, ed e' il motivo per cui il ramo "chiave gia' usata
    fuori dallo scope" - che in `consent/` esiste perche' li' l'unicita' e'
    globale - qui non puo' verificarsi: se la INSERT non ha scritto, la riga
    esistente e' necessariamente di questa agenzia, e quindi leggibile.
    """
    attive = [c for c in INSERTABLE_COLUMNS
              if c not in PROVENANCE_COLUMNS or prepared.get(c) is not None]
    colonne = ", ".join(attive)
    segnaposti = ", ".join(f"%({c})s" for c in attive)
    cur.execute(
        f"""
        INSERT INTO communication_messages ({colonne})
        VALUES ({segnaposti})
        ON CONFLICT (agency_id, idempotency_key) DO NOTHING
        RETURNING *
        """,
        prepared,
    )
    row = _row(cur.fetchone())
    if row is not None:
        return row, True

    source, scope_params = communication_scoped_source(ctx, "communication_messages", "m")
    cur.execute(
        f"SELECT m.* FROM {source} AND m.idempotency_key = %s",
        scope_params + [prepared["idempotency_key"]],
    )
    existing = _row(cur.fetchone())
    if existing is None:
        # Irraggiungibile: la INSERT non ha scritto, quindi il conflitto c'e'
        # stato, e il conflitto e' su (agency_id, idempotency_key) di QUESTA
        # agenzia. Se accade, qualcosa e' cambiato sotto - meglio fallire che
        # restituire un messaggio inventato.
        raise ConflictError(
            f"idempotency key {prepared['idempotency_key']!r} conflicted but the "
            "existing message is not readable in this scope"
        )
    return existing, False


def select_message(cur, ctx, message_id: int) -> dict[str, Any]:
    """Un messaggio, nello scope. Altrimenti NotFoundError."""
    source, params = communication_scoped_source(ctx, "communication_messages", "m")
    cur.execute(f"SELECT m.* FROM {source} AND m.id = %s", params + [message_id])
    message = _row(cur.fetchone())
    if message is None:
        raise NotFoundError(f"communication message {message_id} not found in this scope")
    return message


def list_by_contact(cur, ctx, contact_id: int, *, limit: int) -> list[dict[str, Any]]:
    """I messaggi di un contatto, dal piu' recente.

    `created_at DESC, id DESC`: `created_at` e' l'ordine che una persona si
    aspetta, `id` e' il tie-break che rende l'ordine TOTALE e quindi il test
    riproducibile. E' la stessa disciplina di
    `consent.repository.latest_event`, dove un ordine parziale avrebbe reso non
    deterministica la derivazione dello stato.
    """
    source, params = communication_scoped_source(ctx, "communication_messages", "m")
    cur.execute(
        f"SELECT m.* FROM {source} AND m.contact_id = %s "
        "ORDER BY m.created_at DESC, m.id DESC LIMIT %s",
        params + [contact_id, limit],
    )
    return [dict(r) for r in cur.fetchall()]


def cancel_queued(cur, ctx, message_id: int) -> dict[str, Any]:
    """`queued` -> `cancelled`, e solo quella.

    Compare-and-set sullo stato atteso: fra il momento in cui un chiamante legge
    un messaggio in coda e il momento in cui chiede di annullarlo, un dispatcher
    puo' averlo reclamato. Una UPDATE incondizionata glielo strapperebbe di mano
    mentre sta parlando con un provider, e nessuno se ne accorgerebbe.

    `rowcount` e' cio' che distingue i due esiti, ed e' il database a deciderlo:
    1 significa annullato, 0 significa che il messaggio non era piu' in coda.
    Il caso "non esiste" e il caso "non e' piu' in coda" vengono distinti da una
    lettura successiva, cosi' il chiamante riceve NotFoundError o ConflictError e
    non un generico fallimento.
    """
    stati = sorted(CANCELLABLE_STATUSES)
    cur.execute(
        """
        UPDATE communication_messages
           SET status = %s, updated_at = NOW()
         WHERE id = %s
           AND agency_id = %s
           AND status = ANY(%s)
        RETURNING *
        """,
        [STATUS_CANCELLED, message_id, ctx.require_agency(), stati],
    )
    cancelled = _row(cur.fetchone())
    if cancelled is not None:
        return cancelled

    # Zero righe. Perche'? Le due risposte sono diverse per il chiamante, e
    # `select_message` solleva gia' NotFoundError se il messaggio non e' suo.
    esistente = select_message(cur, ctx, message_id)
    raise ConflictError(
        f"communication message {message_id} is {esistente['status']!r}, "
        f"only {', '.join(stati)} can be cancelled"
    )


#: Il valore con cui ogni messaggio nasce, riesportato perche' il service non
#: debba conoscere il nome della colonna per affermarlo.
INITIAL_STATUS = INITIAL_STATUS


# ===========================================================================
# P29-2.3 - il runtime DB-safe: claim, fencing, tentativi, recovery.
#
# Niente di cio' che segue parla con la rete. Costruisce le transizioni che un
# dispatcher USERA' - in P29-2.4, con il gate del consenso, e in P29-2.5 con un
# provider vero - e le rende sicure prima che esista qualcuno che le chiami.
#
# IL FENCING TOKEN, IN UNA RIGA
#
# `claim_token` non e' un correlation id. Un correlation id si LEGGE per
# collegare dei log; un fencing token si mette nel WHERE, ed e' il database a
# rifiutare la scrittura di chi non lo possiede. La prova del rifiuto e' il
# `rowcount`, e nessuna di queste funzioni si fida di altro.
# ===========================================================================

from .enums import (  # noqa: E402  (import in coda: il modulo e' cresciuto per fasi)
    CLAIMED_STATUS,
    FAILURE_DEFINITE,
    FAILURE_INDETERMINATE,
    INITIAL_OUTCOME,
    OUTCOME_ACCEPTED,
    OUTCOME_INDETERMINATE,
    OUTCOME_REJECTED,
    STATUS_FAILED,
    STATUS_INDETERMINATE,
    STATUS_SENT,
    STATUS_SUPPRESSED,
)

#: `error_code` con cui la recovery dichiara cio' che non sa.
ERROR_OUTCOME_UNKNOWN = "outcome_unknown"


def claim_due(cur, ctx, *, limit: int, provider: str, channel: str,
              token_factory) -> list[dict[str, Any]]:
    """Reclama fino a `limit` messaggi dovuti. Messaggio e tentativo nello stesso commit.

    Non apre e non chiude una transazione: quella e' del chiamante, e deve
    restare BREVE perche' nessuna chiamata di rete puo' starci dentro. Il
    provider si chiama DOPO il commit.

    IL BLOCCO E' `FOR UPDATE SKIP LOCKED`

    Due cron sovrapposti non prendono la stessa riga: il secondo salta quelle
    bloccate invece di aspettarle. Senza `SKIP LOCKED` il secondo worker
    resterebbe fermo sul primo messaggio finche' il primo worker non committa -
    cioe' finche' non ha finito di parlare con un provider - e un batch da dieci
    diventerebbe una coda seriale.

    UN TOKEN PER MESSAGGIO, NON PER BATCH

    Il fencing deve valere sul singolo messaggio: con un token di batch, un
    worker che ha perso UN messaggio per stale li avrebbe persi tutti, o peggio
    li terrebbe tutti. `token_factory` e' un argomento e non una chiamata
    interna cosi' che un test possa renderla deterministica senza monkeypatch.

    `attempt_count` si incrementa AL CLAIM e non all'esito: un processo che
    muore subito dopo il commit ha comunque consumato un tentativo, e non puo'
    esistere un ciclo infinito di claim-e-morte. L'`attempt_no` del tentativo e'
    il valore restituito dal RETURNING, quindi i due non possono divergere.

    IL CANALE FILTRA PRIMA DEL LOCK, E NON DOPO

    `channel` entra nel WHERE della SELECT dei candidati, quindi **prima** di
    `FOR UPDATE SKIP LOCKED`: un worker email non blocca nemmeno per un istante
    una riga WhatsApp. Filtrare dopo il claim sarebbe tutt'altra cosa - il
    messaggio incompatibile sarebbe gia' `sending`, con un token e un tentativo
    aperto, e ogni uscita da quello stato sarebbe una bugia: `indeterminate`
    direbbe che abbiamo provato, `failed` lo autorizzerebbe a rientrare in coda
    per essere riscartato, e `sending -> queued` non esiste nella macchina a
    stati (§7.2). Qui il messaggio sbagliato non viene toccato: resta `queued`,
    con il suo `attempt_count` intatto e senza nessun tentativo che racconti un
    invio mai tentato.
    """
    agency = ctx.require_agency()

    # 1. I candidati. `ORDER BY scheduled_at ASC, id ASC`: FIFO, con `id` come
    #    tie-break che rende l'ordine TOTALE e quindi il test riproducibile.
    source, scope_params = communication_scoped_source(ctx, "communication_messages", "m")
    cur.execute(
        f"""
        SELECT m.id
          FROM {source}
           AND m.status = %s
           AND m.channel = %s
           AND m.scheduled_at <= NOW()
         ORDER BY m.scheduled_at ASC, m.id ASC
         LIMIT %s
           FOR UPDATE SKIP LOCKED
        """,
        scope_params + [INITIAL_STATUS, channel, limit],
    )
    candidati = [r["id"] for r in cur.fetchall()]

    reclamati: list[dict[str, Any]] = []
    for message_id in candidati:
        token = token_factory()

        # 2-4. Il messaggio passa a sending e consuma un tentativo. `status` nel
        #      WHERE anche sotto il lock: se un domani il blocco cambiasse forma,
        #      la transizione resterebbe condizionata.
        cur.execute(
            """
            UPDATE communication_messages
               SET status = %s,
                   claimed_at = NOW(),
                   claim_token = %s,
                   attempt_count = attempt_count + 1,
                   last_attempt_at = NOW(),
                   updated_at = NOW()
             WHERE id = %s
               AND agency_id = %s
               AND status = %s
            RETURNING *
            """,
            [CLAIMED_STATUS, token, message_id, agency, INITIAL_STATUS],
        )
        message = _row(cur.fetchone())
        if message is None:
            # Irraggiungibile sotto il lock. Se accade, il messaggio non e' piu'
            # in coda: si salta, non si forza.
            continue

        # 5. Il tentativo nasce APERTO, nella stessa transazione. Non esiste un
        #    commit osservabile in cui un messaggio e' `sending` e nessun
        #    tentativo lo racconta.
        cur.execute(
            """
            INSERT INTO communication_attempts (
                agency_id, message_id, attempt_no, claim_token, provider,
                started_at, outcome, late_result
            ) VALUES (%s, %s, %s, %s, %s, NOW(), %s, FALSE)
            RETURNING *
            """,
            [agency, message_id, message["attempt_count"], token, provider,
             INITIAL_OUTCOME],
        )
        reclamati.append({"message": message, "attempt": _row(cur.fetchone())})

    return reclamati


#: Come ogni stato terminale si traduce in tre cose: l'esito del tentativo, la
#: classe di fallimento DEL TENTATIVO, e la classe di fallimento DEL MESSAGGIO.
#:
#: LE DUE CLASSI NON SONO LA STESSA COLONNA, E PER `suppressed` DIVERGONO.
#:
#: Il design (§9.3) scrive `failure_class = ...` una volta sola, come se il
#: valore fosse unico. Applicandolo alla lettera la 064 lo rifiuta, ed e' la
#: 064 ad avere ragione:
#:
#:   communication_messages_failure_class_coherence_chk
#:       (status IN ('failed','indeterminate')) = (failure_class IS NOT NULL)
#:
#: `suppressed` non e' uno di quei due stati, quindi sul MESSAGGIO la classe
#: deve restare NULL - un messaggio soppresso non e' fallito, e' stato fermato.
#: Sul TENTATIVO invece l'esito e' `rejected`, e
#:
#:   communication_attempts_failure_class_chk
#:       outcome NOT IN ('rejected','indeterminate') OR failure_class IS NOT NULL
#:
#: la richiede. Le due tabelle rispondono a due domande diverse - "com'e' finita
#: la comunicazione" e "com'e' finita la chiamata" - e qui la differenza si vede.
#:
#: `suppressed` e' anche il caso che il design non risolve esplicitamente sul
#: lato tentativo, ed e' dichiarato qui invece di essere deciso dentro un ramo:
#: il gate del consenso nega DOPO il claim, quindi il tentativo e' gia' aperto e
#: va chiuso. L'esito e' `rejected` - l'invio non e' avvenuto, e lo sappiamo con
#: certezza, quindi `definite` - mentre il PERCHE' vive su
#: `communication_messages.suppressed_reason`, che e' la colonna fatta per
#: quello. Non e' `indeterminate`: qui non c'e' nulla di ignoto.
#:
#: `failed_at` segue la stessa logica: lo portano i due stati di insuccesso,
#: non `sent` e non `suppressed`.
ESITO_DEL_TENTATIVO = {
    #                     esito tentativo      classe tentativo      classe messaggio
    STATUS_SENT:         (OUTCOME_ACCEPTED,      None,                None),
    STATUS_FAILED:       (OUTCOME_REJECTED,      FAILURE_DEFINITE,    FAILURE_DEFINITE),
    STATUS_INDETERMINATE: (OUTCOME_INDETERMINATE, FAILURE_INDETERMINATE, FAILURE_INDETERMINATE),
    STATUS_SUPPRESSED:   (OUTCOME_REJECTED,      FAILURE_DEFINITE,    None),
}

#: Gli stati che portano `failed_at`.
STATI_DI_INSUCCESSO = frozenset({STATUS_FAILED, STATUS_INDETERMINATE})


def regular_attempt(cur, ctx, message_id: int, claim_token: str) -> dict[str, Any] | None:
    """Il tentativo REGOLARE di quel claim, o None se quel claim non e' mai esistito.

    E' la prova che un worker ha davvero posseduto il messaggio: il tentativo
    regolare nasce nella stessa transazione del claim, con lo stesso token, e
    nessun altro percorso lo scrive.

    Serve a due cose, ed entrambe sono contratti che il chiamante non deve poter
    aggirare:

    C15 - IL PROVIDER NON SI RICEVE, SI LEGGE. Il claim ha gia' registrato CHI
    sarebbe stato chiamato. Accettare di nuovo un `provider` dalla
    finalizzazione permetterebbe di attribuire lo stesso tentativo a un provider
    diverso da quello che l'ha reclamato - e con lui un `provider_message_id`
    che appartiene a un altro sistema. Il database lo sa gia': glielo si chiede.

    C16 - UN CAS MANCATO NON E' UNA LICENZA. Un `rowcount = 0` significa "non
    sei piu' tu il proprietario", non "eri il proprietario e sei arrivato
    tardi". Un token inventato produce esattamente lo stesso `rowcount = 0` di
    un token reale sorpassato dalla recovery, e senza questa lettura i due casi
    sarebbero indistinguibili: chiunque potrebbe fabbricare righe di audit
    scrivendo un uuid a caso. `attempt_no` viene da qui e non dal chiamante, per
    la stessa ragione.

    `late_result = FALSE` nel predicato: la riga tardiva non e' una prova di
    possesso, e' la conseguenza di una prova gia' avvenuta.
    """
    source, params = communication_scoped_source(ctx, "communication_attempts", "a")
    cur.execute(
        f"SELECT a.* FROM {source} AND a.message_id = %s AND a.claim_token = %s "
        "AND a.late_result = FALSE",
        params + [message_id, claim_token],
    )
    return _row(cur.fetchone())


def finalize(
    cur, ctx, message_id: int, claim_token: str, *, status: str,
    provider_message_id: str | None = None,
    error_code: str | None = None, error_detail: str | None = None,
    suppressed_reason: str | None = None,
) -> dict[str, Any] | None:
    """Compare-and-set sul messaggio, poi chiusura del tentativo. UNA implementazione.

    Restituisce ``{'message': …, 'attempt': …}`` se l'ownership era valida,
    ``None`` se era persa. `None` NON e' un errore: e' la risposta esatta alla
    domanda "sono ancora io il proprietario di questo messaggio?", e il
    chiamante la usa per registrare un risultato tardivo (§9.4) invece di
    sollevare e far cadere il resto del batch.

    Le quattro transizioni terminali passano tutte di qui. Non ci sono quattro
    implementazioni del fencing - ce n'e' una, e i quattro ingressi pubblici
    stanno nel service, ciascuno con i campi che il suo stato richiede.

    `claim_token` e `claimed_at` si azzerano: il messaggio non e' piu' di
    nessuno, ed e' cio' che rende il WHERE di una seconda finalizzazione falso
    PER COSTRUZIONE invece che per un controllo applicativo.
    """
    if status not in ESITO_DEL_TENTATIVO:
        raise ProgrammingError(f"{status!r} is not a terminal status reachable from a claim")

    agency = ctx.require_agency()
    esito, classe_tentativo, classe_messaggio = ESITO_DEL_TENTATIVO[status]
    concluso = status == STATUS_SENT
    fallito = status in STATI_DI_INSUCCESSO

    # C15: il provider si legge dal claim, non si riceve. Se il tentativo
    # regolare non c'e', quel token non ha mai posseduto questo messaggio e non
    # c'e' niente da finalizzare - nemmeno da provare.
    regolare = regular_attempt(cur, ctx, message_id, claim_token)
    if regolare is None:
        return None
    provider = regolare["provider"]

    # 1. IL FENCING. Quattro colonne nel WHERE, e la quarta e' il token.
    cur.execute(
        """
        UPDATE communication_messages
           SET status = %(status)s,
               sent_at = CASE WHEN %(concluso)s THEN NOW() ELSE sent_at END,
               failed_at = CASE WHEN %(fallito)s THEN NOW() ELSE failed_at END,
               failure_class = %(classe_messaggio)s,
               error_code = %(error_code)s,
               error_detail = %(error_detail)s,
               provider = COALESCE(%(provider)s, provider),
               provider_message_id = %(provider_message_id)s,
               suppressed_reason = %(suppressed_reason)s,
               claim_token = NULL,
               claimed_at = NULL,
               updated_at = NOW()
         WHERE agency_id = %(agency)s
           AND id = %(message_id)s
           AND status = %(atteso)s
           AND claim_token = %(claim_token)s
        RETURNING *
        """,
        {
            "status": status, "concluso": concluso, "fallito": fallito,
            "classe_messaggio": classe_messaggio,
            "error_code": error_code, "error_detail": error_detail,
            "provider": provider, "provider_message_id": provider_message_id,
            "suppressed_reason": suppressed_reason, "agency": agency,
            "message_id": message_id, "atteso": CLAIMED_STATUS,
            "claim_token": claim_token,
        },
    )
    message = _row(cur.fetchone())
    if message is None:
        # rowcount = 0: ownership persa. Il messaggio appartiene alla storia
        # successiva e non si tocca. NON si solleva: il batch continua.
        return None

    # 2. SOLO ORA il tentativo. `outcome = 'in_progress'` nel WHERE: un tentativo
    #    gia' chiuso non si riapre, e il trigger della 064 lo rifiuterebbe.
    cur.execute(
        """
        UPDATE communication_attempts
           SET outcome = %s,
               finished_at = NOW(),
               provider_message_id = %s,
               failure_class = %s,
               error_code = %s,
               error_detail = %s
         WHERE agency_id = %s
           AND message_id = %s
           AND claim_token = %s
           AND outcome = %s
        RETURNING *
        """,
        [esito, provider_message_id, classe_tentativo, error_code, error_detail,
         agency, message_id, claim_token, INITIAL_OUTCOME],
    )
    return {"message": message, "attempt": _row(cur.fetchone())}


def record_late_result(
    cur, ctx, message_id: int, claim_token: str, *, outcome: str,
    provider_message_id: str | None = None,
    error_code: str | None = None, error_detail: str | None = None,
) -> tuple[dict[str, Any] | None, bool]:
    """Il risultato arrivato quando l'ownership non c'era piu'. UNA RIGA NUOVA.

    Non riscrive il tentativo originale, e non puo': quello e' gia' stato chiuso
    - dalla recovery, con `indeterminate` - e il trigger della 064 rifiuta
    l'UPDATE di un tentativo chiuso. Riaprirlo cancellerebbe il fatto che a un
    certo istante NON SAPEVAMO, che e' esattamente l'informazione che lo stato
    `indeterminate` esiste per conservare.

    Due righe raccontano la verita' - abbiamo dichiarato ignoto, e poi e'
    arrivata questa risposta - mentre una riga riscritta racconterebbe che lo
    sapevamo da sempre.

    `UNIQUE (message_id, attempt_no, late_result)` ammette al massimo una riga
    regolare e una tardiva per tentativo: un secondo risultato tardivo e'
    rifiutato dal database, non da un controllo qui.

    NON TOCCA IL MESSAGGIO. Non lo riporta a `sent`, non sblocca un retry, non
    cancella l'`indeterminate`. E' materiale per la riconciliazione di P29-2.8.
    """
    # C16: senza il tentativo regolare, quel token non ha mai posseduto questo
    # messaggio. Nessuna riga, e nessuna eccezione: chi chiama sta gestendo un
    # CAS mancato, e un CAS mancato per token inventato non e' un errore da
    # sollevare - e' semplicemente niente da registrare.
    regolare = regular_attempt(cur, ctx, message_id, claim_token)
    if regolare is None:
        return None, False

    failure_class = None if outcome == OUTCOME_ACCEPTED else (
        FAILURE_INDETERMINATE if outcome == OUTCOME_INDETERMINATE else FAILURE_DEFINITE
    )
    # C17: `ON CONFLICT DO NOTHING`. Lo UNIQUE a tre colonne resta e resta
    # l'autorita'; ma un secondo arrivo dello STESSO risultato tardivo non deve
    # sollevare, perche' una `unique_violation` abortisce la transazione e con
    # essa il resto del batch - proprio nel percorso che il design descrive come
    # "registra, logga e prosegui".
    cur.execute(
        """
        INSERT INTO communication_attempts (
            agency_id, message_id, attempt_no, claim_token, provider,
            started_at, finished_at, outcome, provider_message_id,
            failure_class, error_code, error_detail, late_result
        ) VALUES (%s, %s, %s, %s, %s, NOW(), NOW(), %s, %s, %s, %s, %s, TRUE)
        ON CONFLICT (message_id, attempt_no, late_result) DO NOTHING
        RETURNING *
        """,
        [ctx.require_agency(), message_id, regolare["attempt_no"], claim_token,
         regolare["provider"], outcome, provider_message_id, failure_class,
         error_code, error_detail],
    )
    creata = _row(cur.fetchone())
    if creata is not None:
        return creata, True

    source, params = communication_scoped_source(ctx, "communication_attempts", "a")
    cur.execute(
        f"SELECT a.* FROM {source} AND a.message_id = %s AND a.attempt_no = %s "
        "AND a.late_result = TRUE",
        params + [message_id, regolare["attempt_no"]],
    )
    return _row(cur.fetchone()), False


def stale_candidates(cur, ctx, *, stale_after_seconds: int, limit: int) -> list[dict[str, Any]]:
    """I claim senza esito piu' vecchi della soglia. Sola lettura.

    Restituisce anche il `claim_token` OSSERVATO: e' quello che la recovery
    rimettera' nel WHERE, cosi' che fra la lettura e la scrittura un worker
    tornato vivo e che ha finalizzato legittimamente non venga sovrascritto.
    """
    source, params = communication_scoped_source(ctx, "communication_messages", "m")
    cur.execute(
        f"""
        SELECT m.id, m.claim_token, m.claimed_at, m.attempt_count
          FROM {source}
           AND m.status = %s
           AND m.claimed_at < NOW() - make_interval(secs => %s)
         ORDER BY m.claimed_at ASC, m.id ASC
         LIMIT %s
        """,
        params + [CLAIMED_STATUS, stale_after_seconds, limit],
    )
    return [dict(r) for r in cur.fetchall()]


def recover_stale_message(
    cur, ctx, message_id: int, claim_token: str, *, stale_after_seconds: int
) -> dict[str, Any] | None:
    """Uno stale -> `indeterminate`. Compare-and-set, non UPDATE incondizionata.

    Le quattro condizioni del WHERE sono tutte necessarie: l'agenzia, il
    messaggio, lo stato ancora atteso, il token ancora quello osservato, e la
    soglia ancora superata. Fra la lettura del candidato e questa scrittura il
    worker originale puo' essere tornato vivo e aver finalizzato: in quel caso
    `rowcount` e' 0 e la recovery NON FA NULLA.

    `attempt_count` non si tocca - era gia' stato consumato al claim - e non
    nasce nessun tentativo nuovo: quel claim e' accaduto una volta sola e la
    storia deve dirlo una volta sola. Nessun retry automatico, a nessuna
    condizione.
    """
    agency = ctx.require_agency()
    cur.execute(
        """
        UPDATE communication_messages
           SET status = %s,
               failure_class = %s,
               error_code = %s,
               failed_at = NOW(),
               claim_token = NULL,
               claimed_at = NULL,
               updated_at = NOW()
         WHERE agency_id = %s
           AND id = %s
           AND status = %s
           AND claim_token = %s
           AND claimed_at < NOW() - make_interval(secs => %s)
        RETURNING *
        """,
        [STATUS_INDETERMINATE, FAILURE_INDETERMINATE, ERROR_OUTCOME_UNKNOWN,
         agency, message_id, CLAIMED_STATUS, claim_token, stale_after_seconds],
    )
    message = _row(cur.fetchone())
    if message is None:
        return None

    cur.execute(
        """
        UPDATE communication_attempts
           SET outcome = %s,
               failure_class = %s,
               error_code = %s,
               finished_at = NOW(),
               recovered_at = NOW()
         WHERE agency_id = %s
           AND message_id = %s
           AND claim_token = %s
           AND outcome = %s
        RETURNING *
        """,
        [OUTCOME_INDETERMINATE, FAILURE_INDETERMINATE, ERROR_OUTCOME_UNKNOWN,
         agency, message_id, claim_token, INITIAL_OUTCOME],
    )
    return {"message": message, "attempt": _row(cur.fetchone())}


def list_attempts(cur, ctx, message_id: int) -> list[dict[str, Any]]:
    """I tentativi di un messaggio, in ordine. Sola lettura, tenant scoped.

    `attempt_no ASC, late_result ASC`: il tardivo viene DOPO il regolare dello
    stesso tentativo, che e' l'ordine in cui le due righe si leggono come una
    storia - abbiamo dichiarato ignoto, e poi e' arrivata questa risposta.
    """
    source, params = communication_scoped_source(ctx, "communication_attempts", "a")
    cur.execute(
        f"SELECT a.* FROM {source} AND a.message_id = %s "
        "ORDER BY a.attempt_no ASC, a.late_result ASC, a.id ASC",
        params + [message_id],
    )
    return [dict(r) for r in cur.fetchall()]
