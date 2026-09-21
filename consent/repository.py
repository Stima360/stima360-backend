"""P29-1.2 - the single write path, at the SQL level.

Una sola funzione scrive: `record_decision`. Dentro una sola transazione fa,
in quest'ordine e senza alternative:

    1. blocca il contatto NELLO SCOPE del chiamante  (FOR UPDATE)
    2. inserisce l'evento in `consent_events`
    3. rilegge l'evento PIU' RECENTE per quel contatto e quello scopo
    4. scrive la proiezione su `contacts` da quell'evento
    5. commit

IL PASSO 3 NON E' RIDONDANTE, ED E' IL CUORE DELLA CORRETTEZZA

La via ovvia sarebbe proiettare l'evento appena inserito. Sarebbe sbagliata
non appena due eventi arrivano vicini: un evento in ritardo, con un
`decided_at` piu' VECCHIO di quello gia' registrato, sovrascriverebbe una
decisione piu' recente. Succede davvero - una revoca via link di
disiscrizione e una nuova accettazione dal funnel possono raggiungere il
server in ordine inverso rispetto a quando sono state prese.

La regola di derivazione e' una sola, dichiarata in migrations/062: lo stato
corrente e' la decisione dell'evento piu' recente per `decided_at`, tie-break
su `id` crescente. Rileggerla dopo l'INSERT, sotto il lock del contatto, e'
l'unico modo perche' la proiezione la rispetti sempre - a prescindere
dall'ordine di arrivo. L'evento in ritardo viene comunque CONSERVATO: lo
storico e' completo, e' la proiezione che non arretra.

IL LOCK

`SELECT ... FOR UPDATE` sul contatto serializza per contatto, non per tabella.
Due decisioni concorrenti sullo stesso contatto si accodano; decisioni su
contatti diversi non si vedono. Senza, due transazioni potrebbero leggere lo
stesso "ultimo evento" e scrivere due proiezioni diverse.

LO SCOPE

`agency_id` viene da `ctx.require_agency()` e da nessun altro posto. Non e' un
argomento, non e' nel payload, e un payload che provasse a fornirlo viene
rifiutato (`_reject_server_owned`). Stessa regola, e stesse parole, di
core/repository.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.scope import scoped_predicate as core_scoped_predicate

from .database import consent_cursor
from .enums import (
    DECISION_GRANTED,
    PROJECTION_COLUMNS,
    PURPOSES,
)
from .exceptions import ConflictError, NotFoundError
from .scope import ProgrammingError, consent_scoped_source

# Le colonne la cui autorita' viene dallo scope, mai dal chiamante. Stesso
# elenco e stesso principio di core.repository.SERVER_OWNED_COLUMNS.
SERVER_OWNED_COLUMNS = ("agency_id",)

# Le colonne che il chiamante puo' davvero fornire per un evento. Elenco
# chiuso: una chiave non prevista e' un difetto del chiamante e deve fallire
# qui, non finire in un INSERT costruito dinamicamente.
EVENT_COLUMNS = (
    "contact_id",
    "purpose",
    "decision",
    "decided_at",
    "source",
    "notice_id",
    "actor_type",
    "actor_ref",
    "evidence_type",
    "evidence_ref",
    "note",
    "idempotency_key",
)


def _row(row):
    return dict(row) if row else None


def _reject_server_owned(data: dict[str, Any]) -> None:
    for column in SERVER_OWNED_COLUMNS:
        if column in data:
            raise ProgrammingError(
                f"{column!r} is derived from the agency scope and must not be supplied"
            )


def _validated_event(ctx, data: dict[str, Any]) -> dict[str, Any]:
    _reject_server_owned(data)
    unknown = set(data) - set(EVENT_COLUMNS)
    if unknown:
        raise ProgrammingError(
            f"unknown consent event columns: {', '.join(sorted(unknown))}"
        )
    if data.get("purpose") not in PURPOSES:
        raise ProgrammingError(f"unsupported consent purpose {data.get('purpose')!r}")
    prepared = {column: data.get(column) for column in EVENT_COLUMNS}
    prepared["agency_id"] = ctx.require_agency()
    return prepared


def lock_contact(cur, ctx, contact_id: int) -> dict[str, Any]:
    """Blocca il contatto, dentro lo scope, e lo restituisce.

    Usa il predicato di CORE e non uno proprio: e' lo stesso contatto, e le
    regole su chi lo vede - compreso il ramo che restringe un agente ai propri
    - stanno in un posto solo.

    Un contatto di un'altra agenzia non e' un errore di autorizzazione: e' un
    contatto che non esiste. Dire "esiste ma non e' tuo" direbbe a un'agenzia
    qualcosa sui contatti di un'altra.
    """
    predicate, params = core_scoped_predicate(ctx, "contacts", "c")
    cur.execute(
        f"SELECT c.* FROM contacts c WHERE c.id = %s AND {predicate} FOR UPDATE",
        [contact_id] + params,
    )
    row = _row(cur.fetchone())
    if row is None:
        raise NotFoundError(f"contact {contact_id} not found")
    return row


def insert_event(cur, ctx, data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Inserisce l'evento. Restituisce (riga, creato).

    ON CONFLICT DO NOTHING sulla chiave di idempotenza. Quando la chiave e'
    NULL non c'e' conflitto possibile - in PostgreSQL due NULL non sono uguali
    in un indice unico - e l'evento viene sempre scritto: e' il comportamento
    voluto, perche' senza chiave il chiamante non ha dichiarato di voler essere
    ripetibile.

    Quando la chiave c'e' ed era gia' usata, non si scrive nulla e si
    restituisce la riga esistente, LETTA NELLO SCOPE. La chiave e' unica su
    tutta la tabella, quindi una chiave di un'altra agenzia esiste ma non e'
    leggibile da qui: si solleva ConflictError invece di restituire una riga
    altrui o di fingere che l'inserimento sia avvenuto.
    """
    prepared = _validated_event(ctx, data)
    cur.execute(
        """
        INSERT INTO consent_events (
            agency_id, contact_id, purpose, decision, decided_at, source,
            notice_id, actor_type, actor_ref, evidence_type, evidence_ref,
            note, idempotency_key
        ) VALUES (
            %(agency_id)s, %(contact_id)s, %(purpose)s, %(decision)s, %(decided_at)s, %(source)s,
            %(notice_id)s, %(actor_type)s, %(actor_ref)s, %(evidence_type)s, %(evidence_ref)s,
            %(note)s, %(idempotency_key)s
        )
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING *
        """,
        prepared,
    )
    row = _row(cur.fetchone())
    if row is not None:
        return row, True

    source, scope_params = consent_scoped_source(ctx, "consent_events", "ce")
    cur.execute(
        f"SELECT ce.* FROM {source} AND ce.idempotency_key = %s",
        scope_params + [prepared["idempotency_key"]],
    )
    existing = _row(cur.fetchone())
    if existing is None:
        raise ConflictError(
            "idempotency key already used outside this agency scope"
        )
    return existing, False


def latest_event(cur, ctx, contact_id: int, purpose: str) -> dict[str, Any] | None:
    """L'evento che determina lo stato corrente, o None.

    La regola di derivazione, in una query sola: piu' recente per `decided_at`,
    tie-break su `id` decrescente. L'ordine delle colonne corrisponde a
    idx_consent_events_current, quindi e' una lettura di indice.
    """
    if purpose not in PURPOSES:
        raise ProgrammingError(f"unsupported consent purpose {purpose!r}")
    source, params = consent_scoped_source(ctx, "consent_events", "ce")
    cur.execute(
        f"""
        SELECT ce.* FROM {source}
          AND ce.contact_id = %s
          AND ce.purpose = %s
        ORDER BY ce.decided_at DESC, ce.id DESC
        LIMIT 1
        """,
        params + [contact_id, purpose],
    )
    return _row(cur.fetchone())


def project(cur, ctx, contact_id: int, event: dict[str, Any]) -> dict[str, Any]:
    """Scrive la proiezione su `contacts` a partire da un evento.

    I nomi di colonna vengono da PROJECTION_COLUMNS, una mappa chiusa di
    letterali, e mai da una f-string costruita sul `purpose` ricevuto: un
    valore inatteso non puo' diventare SQL. I VALORI viaggiano sempre come
    parametri.

    Concessione: il flag va a TRUE, l'istante di concessione all'istante
    dell'evento, e l'istante di revoca torna a NULL. La revoca non sparisce -
    resta in `consent_events`, che e' dove la storia vive - ma lo stato
    corrente non e' piu' revocato, ed e' quello che questa tabella dice.

    Revoca: il flag va a FALSE e l'istante di revoca si valorizza.
    L'ISTANTE DI CONCESSIONE NON VIENE AZZERATO: resta la data della
    concessione che e' stata revocata. Svuotarlo cancellerebbe l'unica cosa che
    distingue "ha detto si' e poi no" da "non ha mai detto si'".
    """
    columns = PROJECTION_COLUMNS[event["purpose"]]
    granted = event["decision"] == DECISION_GRANTED

    if granted:
        assignments = (
            f"{columns['flag']} = TRUE, "
            f"{columns['granted_at']} = %s, "
            f"{columns['revoked_at']} = NULL, "
            f"{columns['source']} = %s, "
            f"{columns['notice_id']} = %s"
        )
    else:
        assignments = (
            f"{columns['flag']} = FALSE, "
            f"{columns['revoked_at']} = %s, "
            f"{columns['source']} = %s, "
            f"{columns['notice_id']} = %s"
        )

    predicate, scope_params = core_scoped_predicate(ctx, "contacts", "c")
    values = [event["decided_at"], event["source"], event["notice_id"]]
    cur.execute(
        f"UPDATE contacts c SET {assignments}, updated_at = NOW() "
        f"WHERE c.id = %s AND {predicate} RETURNING *",
        values + [contact_id] + scope_params,
    )
    row = _row(cur.fetchone())
    if row is None:
        raise NotFoundError(f"contact {contact_id} not found")
    return row


def record_decision_with_cursor(cur, ctx, data: dict[str, Any]) -> dict[str, Any]:
    """I quattro passi della decisione, sul cursore del CHIAMANTE.

    Nessuna connessione aperta qui, nessun commit, nessun rollback: la
    transazione appartiene a chi passa il cursore, e con lei la scelta di cosa
    salvare insieme.

    Esiste per P29-1.4. Il bridge pubblico deve scrivere contatto, lead,
    collegamento alla stima E consenso in UN SOLO atto: se il consenso fallisse
    dopo che il lead e' gia' stato committato, resterebbe una persona che ha
    detto si' e un CRM che non lo sa. Aprire una seconda connessione sarebbe
    stato il modo semplice per ottenere esattamente quella finestra.

    La forma - una funzione `_with_cursor` accanto a quella che apre la propria
    transazione - e' quella che `core.repository.create_task_with_cursor` ha
    gia' stabilito in questo repository, e che `followup/repository.py` usa per
    la stessa ragione.
    """
    lock_contact(cur, ctx, data["contact_id"])
    event, created = insert_event(cur, ctx, data)
    current = latest_event(cur, ctx, data["contact_id"], data["purpose"])
    if current is None:
        # Irraggiungibile: l'evento e' appena stato scritto o letto nella
        # stessa transazione e nello stesso scope. Se accade, qualcosa e'
        # cambiato sotto - meglio fallire e far rollback che proiettare
        # uno stato dedotto da niente.
        raise ConflictError(
            f"no consent event visible for contact {data['contact_id']} "
            f"and purpose {data['purpose']!r} after writing one"
        )
    contact = project(cur, ctx, data["contact_id"], current)
    return {
        "event": event,
        "created": created,
        "effective_event": current,
        "contact": contact,
    }


def record_decision(ctx, data: dict[str, Any]) -> dict[str, Any]:
    """L'unico percorso di scrittura del consenso. Una transazione.

    Restituisce un dizionario con l'evento, se e' stato creato adesso, e la
    riga del contatto dopo la proiezione.

    Se qualunque passo solleva, `consent_cursor` esegue il rollback e NON
    viene scritto niente: ne' l'evento senza la proiezione, ne' la proiezione
    senza l'evento. E' l'intero requisito di atomicita', e non e' ottenuto per
    disciplina ma perche' non esiste un secondo commit in questo file.

    E' il guscio transazionale di `record_decision_with_cursor`: i passi sono
    gli stessi, scritti una volta sola. Un chiamante che ha gia' una
    transazione aperta - il bridge pubblico - usa quella e non questa.
    """
    with consent_cursor(commit=True) as (_, cur):
        return record_decision_with_cursor(cur, ctx, data)


def select_contact(cur, ctx, contact_id: int) -> dict[str, Any]:
    """La riga del contatto, nello scope, SENZA lock.

    Gemella di `lock_contact` e diversa in una cosa sola: niente FOR UPDATE.
    Chi legge per decidere - `can_send_marketing` - non deve serializzare gli
    invii fra loro ne' bloccare chi sta registrando una revoca proprio in
    quell'istante.

    Un contatto di un'altra agenzia e' un contatto che non esiste, qui come in
    `lock_contact`: dire "esiste ma non e' tuo" direbbe a un'agenzia qualcosa
    sui contatti di un'altra.
    """
    predicate, params = core_scoped_predicate(ctx, "contacts", "c")
    cur.execute(
        f"SELECT c.* FROM contacts c WHERE c.id = %s AND {predicate}",
        [contact_id] + params,
    )
    row = _row(cur.fetchone())
    if row is None:
        raise NotFoundError(f"contact {contact_id} not found")
    return row


def read_projection(ctx, contact_id: int) -> dict[str, Any]:
    """Lo stato corrente, dalla proiezione. Sola lettura, nessun commit."""
    with consent_cursor() as (_, cur):
        return select_contact(cur, ctx, contact_id)


def read_send_decision_inputs(ctx, contact_id: int, purpose: str):
    """I due ingressi della guardia, in UN SOLO statement. Sola lettura.

    Restituisce `(contatto, evento_corrente_o_None)`.

    PERCHE' UNA QUERY SOLA, E NON DUE SULLA STESSA CONNESSIONE

    La prima stesura leggeva il contatto e poi l'evento con due SELECT sullo
    stesso cursore, dando per scontato che vedessero lo stesso istante del
    database. E' FALSO sotto READ COMMITTED, che e' l'isolamento predefinito di
    PostgreSQL: li' lo snapshot e' per STATEMENT, non per transazione, e due
    SELECT consecutive possono vedere due commit diversi. Una revoca che
    atterrasse fra le due avrebbe prodotto una proiezione "di prima" e un
    evento "di dopo", cioe' `deny_inconsistent_state` su uno stato che
    incoerente non e' mai stato.

    Le alternative erano alzare l'isolamento (REPEATABLE READ, che cambia il
    comportamento di chi ci sta attorno) o prendere un lock (che serializza gli
    invii fra loro). Una sola query non costa niente a nessuno e ottiene la
    stessa garanzia: uno statement, uno snapshot.

    LEFT JOIN LATERAL, e non una JOIN ordinaria: serve l'ULTIMO evento, cioe'
    un `ORDER BY ... LIMIT 1` correlato alla riga del contatto. LEFT perche' un
    contatto senza eventi deve comunque tornare - e' il caso legacy, che e'
    permesso.

    I DUE SCOPE RESTANO DUE. Il contatto passa dal predicato di CORE - ramo
    dell'agente compreso - e l'evento da quello del dominio: la query e' una,
    le autorita' sulla tenancy restano quelle di sempre.

    Nessun commit, nessun lock, nessuna scrittura: e' una decisione, non un
    atto.
    """
    if purpose not in PURPOSES:
        raise ProgrammingError(f"unsupported consent purpose {purpose!r}")

    contact_predicate, contact_params = core_scoped_predicate(ctx, "contacts", "c")
    event_source, event_params = consent_scoped_source(ctx, "consent_events", "ce")

    sql = f"""
        SELECT c.*,
               ev.id         AS consent_event_id,
               ev.decision   AS consent_event_decision,
               ev.decided_at AS consent_event_decided_at,
               ev.source     AS consent_event_source,
               ev.notice_id  AS consent_event_notice_id
          FROM contacts c
          LEFT JOIN LATERAL (
              SELECT ce.id, ce.decision, ce.decided_at, ce.source, ce.notice_id
                FROM {event_source}
                 AND ce.contact_id = c.id
                 AND ce.purpose = %s
               ORDER BY ce.decided_at DESC, ce.id DESC
               LIMIT 1
          ) ev ON TRUE
         WHERE c.id = %s AND {contact_predicate}
    """
    # L'ordine dei parametri segue l'ordine dei segnaposto NEL TESTO: prima la
    # LATERAL (agenzia dell'evento, purpose), poi la WHERE esterna (id del
    # contatto, scope del contatto).
    params = list(event_params) + [purpose, contact_id] + list(contact_params)

    with consent_cursor() as (_, cur):
        cur.execute(sql, params)
        row = _row(cur.fetchone())

    if row is None:
        raise NotFoundError(f"contact {contact_id} not found")

    # Si separa quello che la query ha unito: il contatto torna a essere una
    # riga di `contacts`, l'evento una riga di `consent_events` o None.
    event = None
    if row.get("consent_event_id") is not None:
        event = {
            "id": row["consent_event_id"],
            "decision": row["consent_event_decision"],
            "decided_at": row["consent_event_decided_at"],
            "source": row["consent_event_source"],
            "notice_id": row["consent_event_notice_id"],
        }
    contact = {
        chiave: valore for chiave, valore in row.items()
        if not chiave.startswith("consent_event_")
    }
    return contact, event


def read_send_decision_inputs_bulk(ctx, contact_ids, purpose: str):
    """Gli stessi due ingressi di `read_send_decision_inputs`, per PIU' contatti.

    P29-3C. Il tick delle journey valuta il consenso come condizione di stop
    su tutte le iscrizioni aperte di un'agenzia: una chiamata per iscrizione
    sarebbe la N+1 che il motore ha il mandato di non avere.

    Non e' una decisione diversa ne' una scorciatoia: restituisce gli stessi
    `(contatto, evento)` della versione singola - stessa LATERAL, stessi due
    scope, stesso ordinamento dell'ultimo evento - e chi la chiama li passa
    alla STESSA funzione di decisione. Cio' che cambia e' quante righe una
    query riporta, non cosa significano.

    Un contatto non visibile nello scope semplicemente NON compare nel
    risultato: qui non si solleva `NotFoundError` come nella versione
    singola, perche' il chiamante non sta chiedendo di UN contatto - sta
    chiedendo di quelli che puo' vedere, e l'assenza e' la risposta.

    Sola lettura, nessun lock, nessun commit. Uno statement, uno snapshot:
    la ragione per cui la versione singola ne usa uno solo vale identica qui.
    """
    if purpose not in PURPOSES:
        raise ProgrammingError(f"unsupported consent purpose {purpose!r}")
    identificativi = [int(c) for c in contact_ids]
    if not identificativi:
        return {}

    contact_predicate, contact_params = core_scoped_predicate(ctx, "contacts", "c")
    event_source, event_params = consent_scoped_source(ctx, "consent_events", "ce")

    sql = f"""
        SELECT c.*,
               ev.id         AS consent_event_id,
               ev.decision   AS consent_event_decision,
               ev.decided_at AS consent_event_decided_at,
               ev.source     AS consent_event_source,
               ev.notice_id  AS consent_event_notice_id
          FROM contacts c
          LEFT JOIN LATERAL (
              SELECT ce.id, ce.decision, ce.decided_at, ce.source, ce.notice_id
                FROM {event_source}
                 AND ce.contact_id = c.id
                 AND ce.purpose = %s
               ORDER BY ce.decided_at DESC, ce.id DESC
               LIMIT 1
          ) ev ON TRUE
         WHERE c.id = ANY(%s) AND {contact_predicate}
    """
    params = list(event_params) + [purpose, identificativi] + list(contact_params)

    with consent_cursor() as (_, cur):
        cur.execute(sql, params)
        righe = [_row(r) for r in cur.fetchall()]

    risultato = {}
    for row in righe:
        event = None
        if row.get("consent_event_id") is not None:
            event = {
                "id": row["consent_event_id"],
                "decision": row["consent_event_decision"],
                "decided_at": row["consent_event_decided_at"],
                "source": row["consent_event_source"],
                "notice_id": row["consent_event_notice_id"],
            }
        contact = {
            chiave: valore for chiave, valore in row.items()
            if not chiave.startswith("consent_event_")
        }
        risultato[contact["id"]] = (contact, event)
    return risultato


def utcnow() -> datetime:
    """L'istante di default per una decisione che non ne dichiara uno.

    Con fuso, sempre. `datetime.utcnow()` produce un naive che PostgreSQL
    interpreta nel fuso della sessione: la stessa riga scritta da due processi
    configurati diversamente finirebbe a due istanti diversi.
    """
    return datetime.now(timezone.utc)
