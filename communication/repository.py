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
from .scope import communication_scoped_source

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
)


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
    colonne = ", ".join(INSERTABLE_COLUMNS)
    segnaposti = ", ".join(f"%({c})s" for c in INSERTABLE_COLUMNS)
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
