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

from psycopg2.errors import UndefinedColumn, UndefinedTable

from core.scope import scoped_predicate as core_scoped_predicate

from .database import consent_cursor
from .enums import (
    CHANNELS,
    DECISION_GRANTED,
    PHONE_CHANNELS,
    PROJECTION_COLUMNS,
    PURPOSES,
    PURPOSE_MARKETING,
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
    "channel",
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
            agency_id, contact_id, purpose, channel, decision, decided_at, source,
            notice_id, actor_type, actor_ref, evidence_type, evidence_ref,
            note, idempotency_key
        ) VALUES (
            %(agency_id)s, %(contact_id)s, %(purpose)s, %(channel)s, %(decision)s, %(decided_at)s,
            %(source)s, %(notice_id)s, %(actor_type)s, %(actor_ref)s, %(evidence_type)s,
            %(evidence_ref)s, %(note)s, %(idempotency_key)s
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


def latest_event(cur, ctx, contact_id: int, purpose: str,
                 channel: str | None = None) -> dict[str, Any] | None:
    """L'evento che determina lo stato corrente, o None.

    La regola di derivazione, in una query sola: piu' recente per `decided_at`,
    tie-break su `id` decrescente. L'ordine delle colonne corrisponde a
    idx_consent_events_current, quindi e' una lettura di indice.

    109: con `channel` si legge l'ultimo evento DI QUEL CANALE (indice
    idx_consent_events_channel_current). Senza, l'ultimo evento dello scopo,
    di qualunque canale o senza: e' la lettura storica, che serve alla
    proiezione sul contatto e a chi guarda lo storico, MAI alla guardia, che
    decide per canale.
    """
    if purpose not in PURPOSES:
        raise ProgrammingError(f"unsupported consent purpose {purpose!r}")
    if channel is not None and channel not in CHANNELS:
        raise ProgrammingError(f"unsupported consent channel {channel!r}")
    source, params = consent_scoped_source(ctx, "consent_events", "ce")
    filtro_canale = "" if channel is None else " AND ce.channel = %s"
    cur.execute(
        f"""
        SELECT ce.* FROM {source}
          AND ce.contact_id = %s
          AND ce.purpose = %s{filtro_canale}
        ORDER BY ce.decided_at DESC, ce.id DESC
        LIMIT 1
        """,
        params + [contact_id, purpose] + ([] if channel is None else [channel]),
    )
    return _row(cur.fetchone())


def project_channel(cur, ctx, contact_id: int, event: dict[str, Any]) -> dict[str, Any]:
    """109: scrive la proiezione PER CANALE da un evento marketing con canale.

    Una riga per (contatto, canale), UPSERT. Concessione: `granted`, con la
    data della concessione e nessuna revoca. Revoca: `revoked`, con la data
    della revoca; la data della concessione revocata resta, per lo stesso
    motivo di `project`.
    """
    if event.get("channel") not in CHANNELS:
        raise ProgrammingError("project_channel needs a marketing event with a channel")
    granted = event["decision"] == DECISION_GRANTED
    cur.execute(
        """
        INSERT INTO contact_channel_consents
               (agency_id, contact_id, channel, status, granted_at, revoked_at,
                source, notice_id, event_id, updated_at)
        VALUES (%(agency_id)s, %(contact_id)s, %(channel)s, %(status)s, %(granted_at)s,
                %(revoked_at)s, %(source)s, %(notice_id)s, %(event_id)s, NOW())
        ON CONFLICT (contact_id, channel) DO UPDATE SET
               status     = EXCLUDED.status,
               granted_at = CASE WHEN EXCLUDED.status = 'granted' THEN EXCLUDED.granted_at
                                 ELSE contact_channel_consents.granted_at END,
               revoked_at = EXCLUDED.revoked_at,
               source     = EXCLUDED.source,
               notice_id  = EXCLUDED.notice_id,
               event_id   = EXCLUDED.event_id,
               updated_at = NOW()
        RETURNING *
        """,
        {
            "agency_id": event["agency_id"], "contact_id": contact_id, "channel": event["channel"],
            "status": "granted" if granted else "revoked",
            "granted_at": event["decided_at"] if granted else None,
            "revoked_at": None if granted else event["decided_at"],
            "source": event["source"], "notice_id": event.get("notice_id"), "event_id": event["id"],
        },
    )
    return _row(cur.fetchone())


def channel_rows(cur, ctx, contact_id: int) -> dict[str, dict[str, Any]]:
    """Le proiezioni per canale di un contatto, per canale, nello scope di agenzia."""
    cur.execute("SELECT * FROM contact_channel_consents WHERE contact_id = %s AND agency_id = %s",
                [contact_id, ctx.require_agency()])
    return {r["channel"]: dict(r) for r in cur.fetchall()}


def project_derived(cur, ctx, contact_id: int, event: dict[str, Any]) -> dict[str, Any]:
    """109: le colonne marketing di `contacts`, DERIVATE dai canali.

    Da C2 `contacts.marketing_consent` non e' un'autorizzazione: e' il
    riassunto che il resto del CRM legge ("almeno un canale concesso"). Qui si
    ricalcola dalle righe per canale, nella stessa transazione:

        qualche canale concesso   -> TRUE,  granted_at = la concessione piu'
                                     recente fra i canali concessi, revoked_at NULL
        nessuno concesso          -> FALSE, revoked_at = la revoca piu' recente

    `source` e `notice_id` sono quelli dell'evento appena scritto.
    """
    righe = channel_rows(cur, ctx, contact_id)
    concessi = [r for r in righe.values() if r["status"] == "granted"]
    columns = PROJECTION_COLUMNS[PURPOSE_MARKETING]
    if concessi:
        assignments = (f"{columns['flag']} = TRUE, {columns['granted_at']} = %s, "
                       f"{columns['revoked_at']} = NULL, {columns['source']} = %s, "
                       f"{columns['notice_id']} = %s")
        values = [max(r["granted_at"] for r in concessi), event["source"], event.get("notice_id")]
    else:
        revocati = [r["revoked_at"] for r in righe.values() if r["revoked_at"] is not None]
        assignments = (f"{columns['flag']} = FALSE, {columns['revoked_at']} = %s, "
                       f"{columns['source']} = %s, {columns['notice_id']} = %s")
        values = [max(revocati) if revocati else event["decided_at"], event["source"],
                  event.get("notice_id")]
    predicate, scope_params = core_scoped_predicate(ctx, "contacts", "c")
    cur.execute(
        f"UPDATE contacts c SET {assignments}, updated_at = NOW() "
        f"WHERE c.id = %s AND {predicate} RETURNING *",
        values + [contact_id] + scope_params,
    )
    row = _row(cur.fetchone())
    if row is None:
        raise NotFoundError(f"contact {contact_id} not found")
    return row


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
    canale = event.get("channel")
    current = latest_event(cur, ctx, data["contact_id"], data["purpose"], canale)
    if current is None:
        # Irraggiungibile: l'evento e' appena stato scritto o letto nella
        # stessa transazione e nello stesso scope. Se accade, qualcosa e'
        # cambiato sotto - meglio fallire e far rollback che proiettare
        # uno stato dedotto da niente.
        raise ConflictError(
            f"no consent event visible for contact {data['contact_id']} "
            f"and purpose {data['purpose']!r} after writing one"
        )
    if canale is not None:
        # 109: la proiezione per canale e' l'autorita'; le colonne di
        # `contacts` ne sono il riassunto, ricalcolato qui.
        project_channel(cur, ctx, data["contact_id"], current)
        contact = project_derived(cur, ctx, data["contact_id"], current)
    elif data["purpose"] == PURPOSE_MARKETING and channel_rows(cur, ctx, data["contact_id"]):
        # Una concessione pre-canale (funnel pubblico) su un contatto che ha
        # gia' righe per canale: resta nello storico, il riassunto non cambia
        # (non autorizza niente).
        contact = select_contact(cur, ctx, data["contact_id"])
    else:
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


# ---------------------------------------------------------------------------
# 108 (Fase B) - la DISISCRIZIONE DI PIATTAFORMA
#
# `communication_email_optouts` e' la lista di chi ha detto "basta marketing
# STIMA360" per tutte le agenzie. Non e' scopata per agenzia di proposito:
# l'indirizzo e' la chiave, e un'agenzia non puo' "non vederla". Qui si legge
# nella STESSA statement del contatto e dell'evento (uno snapshot), e si
# scrive solo in append: la guardia decide, non ripara.
#
# Se la tabella non esiste ancora (codice davanti alla migration), la
# decisione resta quella di sempre: non si inventa una colonna vuota per
# sbaglio, la si dichiara assente.
# ---------------------------------------------------------------------------
_OPTOUT_SELECT = """,
                   cc.id         AS channel_row_id,
                   cc.status     AS channel_status,
                   cc.granted_at AS channel_granted_at,
                   cc.revoked_at AS channel_revoked_at,
                   po.id           AS platform_optout_id,
                   po.opted_out_at AS platform_optout_at"""
_OPTOUT_SELECT_ASSENTE = """,
                   NULL::bigint      AS channel_row_id,
                   NULL::varchar     AS channel_status,
                   NULL::timestamptz AS channel_granted_at,
                   NULL::timestamptz AS channel_revoked_at,
                   NULL::bigint      AS platform_optout_id,
                   NULL::timestamptz AS platform_optout_at"""


def _indirizzo_sql(channel: str) -> str:
    return "c.phone_normalized" if channel in PHONE_CHANNELS else "c.email_normalized"


def _lateral_canale(channel: str) -> str:
    """109: la riga per canale e l'ultimo opt-out su (canale, indirizzo)."""
    return f"""
              LEFT JOIN LATERAL (
                  SELECT cc.id, cc.status, cc.granted_at, cc.revoked_at
                    FROM contact_channel_consents cc
                   WHERE cc.contact_id = c.id AND cc.channel = %s
              ) cc ON TRUE
              LEFT JOIN LATERAL (
                  SELECT po.id, po.opted_out_at
                    FROM communication_optouts po
                   WHERE {_indirizzo_sql(channel)} IS NOT NULL
                     AND po.channel = %s
                     AND po.address_normalized = {_indirizzo_sql(channel)}
                   ORDER BY po.opted_out_at DESC, po.id DESC
                   LIMIT 1
              ) po ON TRUE"""


def platform_optout_available(cur) -> bool:
    cur.execute("SELECT to_regclass('public.communication_optouts') IS NOT NULL AS presente")
    return bool(cur.fetchone()["presente"])


def _sql_decisionale(event_source: str, contact_predicate: str, filtro: str, *,
                     channel: str, con_schema: bool) -> str:
    """UNA statement: contatto, ultimo evento DEL CANALE, riga per canale e
    opt-out su (canale, indirizzo). `con_schema=False` e' la stessa lettura
    senza le tabelle 109, che la guardia tratta come stato incoerente."""
    return f"""
        SELECT c.*,
               ev.id         AS consent_event_id,
               ev.decision   AS consent_event_decision,
               ev.decided_at AS consent_event_decided_at,
               ev.source     AS consent_event_source,
               ev.notice_id  AS consent_event_notice_id
               {_OPTOUT_SELECT if con_schema else _OPTOUT_SELECT_ASSENTE}
          FROM contacts c
          LEFT JOIN LATERAL (
              SELECT ce.id, ce.decision, ce.decided_at, ce.source, ce.notice_id
                FROM {event_source}
                 AND ce.contact_id = c.id
                 AND ce.purpose = %s
                 AND {"ce.channel = %s" if con_schema else "FALSE"}
               ORDER BY ce.decided_at DESC, ce.id DESC
               LIMIT 1
          ) ev ON TRUE
          {_lateral_canale(channel) if con_schema else ""}
         WHERE {filtro} AND {contact_predicate}
    """


def _esegui_decisionale(cur, event_source: str, contact_predicate: str, filtro: str,
                        params_evento, params_contatto, *, channel: str) -> bool:
    """UNA statement, uno snapshot. Restituisce True se lo schema 109 c'era.

    Senza le tabelle 109 (codice arrivato prima della migration) PostgreSQL
    rifiuta la statement: si annulla e si rilegge senza di esse, e la guardia
    rifiuta ogni invio (`deny_inconsistent_state`): fail closed, non "come
    prima", perche' "come prima" sarebbe il vecchio flag che il committente
    ha escluso come autorizzazione.
    """
    if channel not in CHANNELS:
        raise ProgrammingError(f"unsupported consent channel {channel!r}")
    completi = list(params_evento) + [channel, channel, channel] + list(params_contatto)
    try:
        cur.execute(_sql_decisionale(event_source, contact_predicate, filtro,
                                     channel=channel, con_schema=True), completi)
        return True
    except (UndefinedTable, UndefinedColumn):
        cur.connection.rollback()
        ridotti = list(params_evento) + list(params_contatto)
        cur.execute(_sql_decisionale(event_source, contact_predicate, filtro,
                                     channel=channel, con_schema=False), ridotti)
        return False


def insert_platform_optout(cur, data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Una riga di opt-out di piattaforma su (canale, indirizzo). Idempotente."""
    cur.execute(
        """
        INSERT INTO communication_optouts
               (channel, address_normalized, opted_out_at, source, source_agency_id,
                source_contact_id, consent_event_id, actor_type, actor_ref, note, idempotency_key)
        VALUES (%(channel)s, %(address_normalized)s, %(opted_out_at)s, %(source)s,
                %(source_agency_id)s, %(source_contact_id)s, %(consent_event_id)s, %(actor_type)s,
                %(actor_ref)s, %(note)s, %(idempotency_key)s)
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING *
        """,
        data,
    )
    row = _row(cur.fetchone())
    if row is not None:
        return row, True
    cur.execute("SELECT * FROM communication_optouts WHERE idempotency_key = %s",
                [data["idempotency_key"]])
    return _row(cur.fetchone()), False


def contact_address(contact: dict[str, Any], channel: str) -> str | None:
    """L'indirizzo del contatto per il canale: email o numero normalizzato."""
    if channel not in CHANNELS:
        raise ProgrammingError(f"unsupported consent channel {channel!r}")
    return contact.get("phone_normalized" if channel in PHONE_CHANNELS else "email_normalized") or None


def latest_platform_optout(cur, channel: str, address_normalized: str | None) -> dict[str, Any] | None:
    if channel not in CHANNELS:
        raise ProgrammingError(f"unsupported consent channel {channel!r}")
    if not address_normalized or not platform_optout_available(cur):
        return None
    cur.execute(
        """SELECT * FROM communication_optouts WHERE channel = %s AND address_normalized = %s
            ORDER BY opted_out_at DESC, id DESC LIMIT 1""", [channel, address_normalized])
    return _row(cur.fetchone())


def _separa(row: dict[str, Any]):
    """(contatto, evento_o_None, riga_canale_o_None) da una riga decisionale."""
    event = None
    if row.get("consent_event_id") is not None:
        event = {
            "id": row["consent_event_id"],
            "decision": row["consent_event_decision"],
            "decided_at": row["consent_event_decided_at"],
            "source": row["consent_event_source"],
            "notice_id": row["consent_event_notice_id"],
        }
    riga_canale = None
    if row.get("channel_row_id") is not None:
        riga_canale = {
            "id": row["channel_row_id"],
            "status": row["channel_status"],
            "granted_at": row["channel_granted_at"],
            "revoked_at": row["channel_revoked_at"],
        }
    contact = {
        chiave: valore for chiave, valore in row.items()
        if not chiave.startswith("consent_event_") and not chiave.startswith("channel_")
    }
    return contact, event, riga_canale


def read_send_decision_inputs(ctx, contact_id: int, purpose: str, *, channel: str):
    """Gli ingressi della guardia, in UN SOLO statement. Sola lettura.

    Restituisce `(contatto, evento_corrente_del_canale_o_None, riga_canale_o_None)`;
    il contatto porta `platform_optout_at` (per il canale) e
    `consent_schema_ready` (False se le tabelle 109 mancano).

    PERCHE' UNA QUERY SOLA, E NON DUE SULLA STESSA CONNESSIONE: sotto READ
    COMMITTED lo snapshot e' per STATEMENT, e due SELECT consecutive possono
    vedere due commit diversi. Una revoca atterrata fra le due produrrebbe
    una proiezione "di prima" e un evento "di dopo". Uno statement, uno
    snapshot. LEFT JOIN LATERAL perche' serve l'ULTIMO evento e la riga del
    canale, e un contatto senza nessuno dei due deve comunque tornare.

    I DUE SCOPE RESTANO DUE: il contatto passa dal predicato di CORE, l'evento
    da quello del dominio. Nessun commit, nessun lock, nessuna scrittura.
    """
    if purpose not in PURPOSES:
        raise ProgrammingError(f"unsupported consent purpose {purpose!r}")
    contact_predicate, contact_params = core_scoped_predicate(ctx, "contacts", "c")
    event_source, event_params = consent_scoped_source(ctx, "consent_events", "ce")
    with consent_cursor() as (_, cur):
        schema = _esegui_decisionale(cur, event_source, contact_predicate, "c.id = %s",
                                     list(event_params) + [purpose], [contact_id] + list(contact_params),
                                     channel=channel)
        row = _row(cur.fetchone())
    if row is None:
        raise NotFoundError(f"contact {contact_id} not found")
    contact, event, riga_canale = _separa(row)
    contact["consent_schema_ready"] = schema
    return contact, event, riga_canale


def read_send_decision_inputs_bulk(ctx, contact_ids, purpose: str, *, channel: str):
    """Gli stessi ingressi di `read_send_decision_inputs`, per PIU' contatti.

    Un contatto non visibile nello scope semplicemente NON compare nel
    risultato: il chiamante sta chiedendo di quelli che puo' vedere.
    """
    if purpose not in PURPOSES:
        raise ProgrammingError(f"unsupported consent purpose {purpose!r}")
    identificativi = [int(c) for c in contact_ids]
    if not identificativi:
        return {}
    contact_predicate, contact_params = core_scoped_predicate(ctx, "contacts", "c")
    event_source, event_params = consent_scoped_source(ctx, "consent_events", "ce")
    with consent_cursor() as (_, cur):
        schema = _esegui_decisionale(cur, event_source, contact_predicate, "c.id = ANY(%s)",
                                     list(event_params) + [purpose],
                                     [identificativi] + list(contact_params), channel=channel)
        righe = [_row(r) for r in cur.fetchall()]
    risultato = {}
    for row in righe:
        contact, event, riga_canale = _separa(row)
        contact["consent_schema_ready"] = schema
        risultato[contact["id"]] = (contact, event, riga_canale)
    return risultato


def utcnow() -> datetime:
    """L'istante di default per una decisione che non ne dichiara uno.

    Con fuso, sempre. `datetime.utcnow()` produce un naive che PostgreSQL
    interpreta nel fuso della sessione: la stessa riga scritta da due processi
    configurati diversamente finirebbe a due istanti diversi.
    """
    return datetime.now(timezone.utc)
