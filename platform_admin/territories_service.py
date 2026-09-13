"""Le operazioni di rete sui territori. P27-5.

Questo modulo possiede il PERCHE' di ogni operazione territoriale. L'ORDINE fra
la scrittura, il suo audit e il commit vive in `platform_admin/transaction.py`,
che e' l'unica copia esistente nel package:

    nessuna modifica amministrativa viene committata
    se il suo audit non e' stato scritto.

P27-5 NON DECIDE A CHI VA UN LEAD.

Questo file non legge `stime`, non legge `leads`, non legge `contacts`, non
conosce `properties` e non chiama nessun modulo di CORE. Consegna il dato
amministrativo - chi presidia cosa - e si ferma li'. La distribuzione delle
opportunita' e' P27-6, e anticiparla qui significherebbe scrivere la regola di
routing dentro il posto che tiene i dati su cui la regola lavora, dove nessun
test di routing andrebbe a cercarla.

L'INVARIANTE DI PRODOTTO E' DEL DATABASE, NON DI QUESTO FILE.

    un territorio canonico ha AL MASSIMO UNA assegnazione attiva.

I controlli espliciti qui sotto producono il messaggio GIUSTO nel caso normale
- "ce l'hai gia' tu" e "ce l'ha un altro" portano a due azioni diverse - ma non
sono cio' che rende vera la regola. Fra un SELECT e la INSERT che lo segue c'e'
una finestra, e in quella finestra due richieste simultanee passano entrambe il
controllo. Cio' che le separa e' `uq_agency_territory_single_active`, e la
traduzione di quella violazione in un 409 e' il motivo per cui la perdente
riceve un conflitto e non un 500 con dentro il nome di un indice.

LO STATO DELL'AGENZIA NON CONTA, E LE ASSEGNAZIONI NON LO SEGUONO.

Un'agenzia sospesa o archiviata resta amministrabile da questa superficie, come
gia' in P27-3: e' il momento in cui serve di piu'. E sospendere un'agenzia non
tocca le sue assegnazioni territoriali - non una riga, non un campo. Sono due
fatti diversi, e farli coincidere significherebbe che riattivare un affiliato
non gli restituisce il territorio, perche' nel frattempo qualcosa lo ha
revocato per conto suo. Quali combinazioni di stato siano ELEGGIBILI per
ricevere un lead lo decide P27-6, leggendo entrambi.
"""
from __future__ import annotations

from typing import Any

from psycopg2 import errors

from operator_auth.context import OperatorContext

from . import agencies_repository, territories_repository
from .database import platform_operation_cursor
from .enums import (
    ACTION_TERRITORY_ASSIGNMENT_CREATE,
    ACTION_TERRITORY_ASSIGNMENT_UPDATE,
    ACTION_TERRITORY_CREATE,
    ACTION_TERRITORY_TRANSFER,
    AGENCY_NOT_FOUND_MESSAGE,
    ASSIGNMENT_ACTIVE,
    ASSIGNMENT_NOT_FOUND_MESSAGE,
    ASSIGNMENT_REVOKED,
    ASSIGNMENT_REVOKED_IS_FINAL_MESSAGE,
    ASSIGNMENT_SUSPENDED,
    TARGET_TYPE_TERRITORY,
    TARGET_TYPE_TERRITORY_ASSIGNMENT,
    TERRITORY_ALREADY_ASSIGNED_HERE_MESSAGE,
    TERRITORY_ASSIGNMENT_RACE_MESSAGE,
    TERRITORY_EXISTS_MESSAGE,
    TERRITORY_NOT_FOUND_MESSAGE,
    TERRITORY_PROTECTED_MESSAGE,
    TRANSFER_NOTHING_TO_TRANSFER_MESSAGE,
    TRANSFER_SAME_AGENCY_MESSAGE,
)
from .exceptions import (
    AgencyNotFound,
    PlatformConflict,
    TerritoryAssignmentNotFound,
    TerritoryNotFound,
)
from .transaction import audit_then_commit

# I VINCOLI DI CUI QUESTO MODULO SA IL SIGNIFICATO, E SOLO QUELLI.
#
# Stesso criterio di `operators_service.CONFLICT_CONSTRAINTS`: una violazione
# di unicita' il cui vincolo non e' in questa mappa NON viene indovinata,
# risale. Un 409 inventato su un vincolo sconosciuto dice al chiamante che deve
# cambiare la richiesta, quando magari deve cambiarla chi ha scritto il codice.
CONFLICT_CONSTRAINTS = {
    "network_territories_identity_unq": TERRITORY_EXISTS_MESSAGE,
    "uq_agency_territory_single_active": TERRITORY_ASSIGNMENT_RACE_MESSAGE,
}


def _as_conflict(exc: errors.UniqueViolation) -> PlatformConflict:
    """Traduce una violazione ATTESA in un conflitto di dominio, o rilancia.

    Il nome del vincolo si legge da `exc.diag.constraint_name`. Se non c'e' -
    driver diverso, errore rimaneggiato - la violazione risale invece di
    diventare un 409 arbitrario.

    Il messaggio restituito viene sempre dalle costanti: quello di PostgreSQL
    contiene il nome del vincolo, quello della tabella e il valore in
    conflitto.
    """
    constraint = getattr(getattr(exc, "diag", None), "constraint_name", None)
    message = CONFLICT_CONSTRAINTS.get(constraint)
    if message is None:
        raise exc
    return PlatformConflict(message)


def _require_agency(cur, agency_id: int) -> dict[str, Any]:
    """L'agenzia deve esistere. Il suo STATO non conta - vedi il docstring del
    modulo."""
    agency = agencies_repository.get_agency(cur, agency_id)
    if agency is None:
        raise AgencyNotFound(AGENCY_NOT_FOUND_MESSAGE)
    return agency


def _require_territory(cur, territory_id: int) -> dict[str, Any]:
    territory = territories_repository.get_territory(cur, territory_id)
    if territory is None:
        raise TerritoryNotFound(TERRITORY_NOT_FOUND_MESSAGE)
    return territory


# ---------------------------------------------------------------------------
# Letture
#
# Non auditano. L'ammissione di P27-1 registra gia' chi e' entrato, quando e su
# quale route, e per una lettura quello E' la traccia. Una riga operativa per
# ogni GET trasformerebbe il registro degli ATTI in un log di traffico, e la
# prima schermata di P27-7 che fa polling lo renderebbe illeggibile.
# ---------------------------------------------------------------------------

def list_territories(
    *,
    kind: str | None = None,
    agency_id: int | None = None,
    assignment_status: str | None = None,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    """I territori della rete, paginati, con l'assegnazione attiva di ciascuno.

    `limit` e `offset` sono keyword-only e senza default. Lo schema della route
    li impone gia' entro i suoi estremi; non averne uno qui significa che una
    seconda chiamata scritta domani non puo' dimenticarli.
    """
    with platform_operation_cursor() as (_, cur):
        return territories_repository.list_territories(
            cur,
            kind=kind,
            agency_id=agency_id,
            assignment_status=assignment_status,
            limit=limit,
            offset=offset,
        )


def get_territory(territory_id: int) -> dict[str, Any]:
    """Un territorio con la sua assegnazione attiva, se ne ha una.

    L'assegnazione viaggia insieme perche' e' la domanda che segue sempre
    quella sul territorio - "questo di chi e'" - e due chiamate per due meta'
    dello stesso fatto invitano a leggerne una sola.
    """
    with platform_operation_cursor() as (_, cur):
        territory = _require_territory(cur, territory_id)
        active = territories_repository.get_active_assignment(cur, territory_id)
    return {**territory, "active_assignment": active}


def list_agency_territories(
    agency_id: int, *, status: str | None = None, limit: int, offset: int
) -> list[dict[str, Any]]:
    """Le assegnazioni di un'agenzia, in ogni stato salvo filtro esplicito.

    Non solo le attive: un affiliato ha una storia territoriale, e mostrargli
    solo il presente renderebbe invisibile cio' che gli e' stato revocato -
    che e' spesso la cosa su cui si sta discutendo.
    """
    with platform_operation_cursor() as (_, cur):
        _require_agency(cur, agency_id)
        return territories_repository.list_agency_assignments(
            cur, agency_id, status=status, limit=limit, offset=offset
        )


# ---------------------------------------------------------------------------
# Mutazioni
# ---------------------------------------------------------------------------

def create_territory(
    actor: OperatorContext,
    *,
    kind: str,
    canonical_key: str,
    label: str,
    created_fields: list[str],
) -> dict[str, Any]:
    """Dichiara un territorio. NON lo assegna a nessuno.

    Le due cose sono separate di proposito. Un endpoint che creasse e
    assegnasse insieme renderebbe impossibile dichiarare la copertura futura
    della rete prima di avere l'affiliato che la prendera', e soprattutto
    produrrebbe UNA riga di audit per DUE atti - la nascita di un posto e la
    consegna di quel posto a qualcuno - che si leggono e si contestano in
    momenti diversi.

    `created_fields` contiene i nomi dei campi che il CHIAMANTE ha indicato,
    mai i loro valori: `canonical_key` e `label` sono dati geografici e non
    entrano nel registro.
    """
    with platform_operation_cursor() as (conn, cur):
        # Il controllo esplicito produce il messaggio buono nel caso normale.
        if territories_repository.territory_identity_exists(
            cur, kind=kind, canonical_key=canonical_key
        ):
            raise PlatformConflict(TERRITORY_EXISTS_MESSAGE)

        try:
            territory = territories_repository.create_territory(
                cur, kind=kind, canonical_key=canonical_key, label=label
            )
        except errors.UniqueViolation as exc:
            # La corsa fra due creazioni della stessa identita': il controllo
            # sopra e questa INSERT non sono un'operazione sola.
            raise _as_conflict(exc) from exc

        audit_then_commit(
            conn,
            actor,
            action=ACTION_TERRITORY_CREATE,
            target_type=TARGET_TYPE_TERRITORY,
            target_id=territory["id"],
            # Un territorio appena dichiarato non appartiene a nessuno, e
            # `target_agency_id` dice a quale agenzia si riferisce l'atto: qui
            # a nessuna. Metterci un'agenzia qualunque - quella dell'attore,
            # per dire - farebbe comparire questo atto nella cronologia di
            # un'agenzia che non c'entra.
            target_agency_id=None,
            metadata={"created_fields": sorted(created_fields)},
        )
        return {**territory, "active_assignment": None}


def assign_territory(
    actor: OperatorContext,
    agency_id: int,
    *,
    territory_id: int,
    created_fields: list[str],
) -> dict[str, Any]:
    """Assegna un territorio libero a un'agenzia.

    LIBERO. Non trasferisce, e non revoca niente di nessun altro.

    I due conflitti possibili hanno messaggi distinti perche' portano a due
    azioni diverse: se ce l'ha gia' questa agenzia non c'e' niente da fare, se
    ce l'ha un'altra c'e' un trasferimento da valutare. Nessuno dei due diventa
    un trasferimento implicito: togliere un territorio a un affiliato e' una
    decisione commerciale, e non deve poter accadere come effetto collaterale
    di una richiesta che nomina soltanto l'affiliato che lo riceve.

    Un'assegnazione `suspended` o `revoked` non blocca: il vincolo guarda solo
    le attive, e sospendere serve proprio a liberare il territorio senza
    cancellare la relazione.
    """
    with platform_operation_cursor() as (conn, cur):
        _require_agency(cur, agency_id)
        _require_territory(cur, territory_id)

        active = territories_repository.get_active_assignment(cur, territory_id)
        if active is not None:
            raise PlatformConflict(
                TERRITORY_ALREADY_ASSIGNED_HERE_MESSAGE
                if active["agency_id"] == agency_id
                else TERRITORY_PROTECTED_MESSAGE
            )

        try:
            assignment = territories_repository.create_assignment(
                cur,
                territory_id=territory_id,
                agency_id=agency_id,
                status=ASSIGNMENT_ACTIVE,
            )
        except errors.UniqueViolation as exc:
            # La corsa. Senza questa cattura chi la perde riceve un 500 con
            # dentro il nome dell'indice.
            raise _as_conflict(exc) from exc

        audit_then_commit(
            conn,
            actor,
            action=ACTION_TERRITORY_ASSIGNMENT_CREATE,
            target_type=TARGET_TYPE_TERRITORY_ASSIGNMENT,
            target_id=assignment["id"],
            target_agency_id=agency_id,
            metadata={"created_fields": sorted(created_fields)},
        )
        return assignment


def update_assignment(
    actor: OperatorContext,
    agency_id: int,
    assignment_id: int,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """Cambia lo stato di un'assegnazione: sospendere, revocare, riattivare.

    NON cambia l'agenzia e non cambia il territorio: non sono campi di questo
    schema e non sono colonne aggiornabili del repository. Spostare
    un'assegnazione da un'agenzia all'altra e' un trasferimento, ha il suo
    endpoint e il suo atto nel registro.

    `revoked` E' TERMINALE. Le transizioni ammesse sono quattro:

        active    -> suspended    si'
        suspended -> active       si'
        active    -> revoked      si'
        suspended -> revoked      si'
        revoked   -> active       NO, 409
        revoked   -> suspended    NO, 409

    Una riga revocata descrive un periodo di presidio FINITO, e riportarla in
    vita riscriverebbe quel periodo invece di aggiungerne uno nuovo: la riga
    direbbe di essere in corso, e quando il presidio precedente sia cominciato
    e finito resterebbe ricostruibile solo da `platform_audit_log` - un
    registro fatto per raccontare gli ATTI, non per essere l'unica fonte dello
    STATO.

    La strada per rimettere un'agenzia su un territorio revocato esiste ed e'
    un'altra: `assign_territory`, che crea una riga nuova. La vecchia resta
    revocata. Vale anche quando l'agenzia e' la stessa, perche'
    `uq_agency_territory_single_active` guarda il solo `territory_id`.

    LA RIATTIVAZIONE DA `suspended` E' IL CASO DELICATO. E' ammessa - e' la
    differenza fra sospendere e revocare - ma il territorio nel frattempo puo'
    essere passato a un altro, e allora e' 409 e NON una revoca silenziosa
    dell'altra.
    """
    with platform_operation_cursor() as (conn, cur):
        _require_agency(cur, agency_id)

        assignment = territories_repository.get_assignment(
            cur, assignment_id, agency_id
        )
        if assignment is None:
            raise TerritoryAssignmentNotFound(ASSIGNMENT_NOT_FOUND_MESSAGE)

        _guard_revoked_is_final(assignment, fields)
        _guard_reactivation(cur, assignment, fields)

        try:
            updated = territories_repository.update_assignment(
                cur, assignment_id, fields
            )
        except errors.UniqueViolation as exc:
            raise _as_conflict(exc) from exc

        if updated is None:
            raise TerritoryAssignmentNotFound(ASSIGNMENT_NOT_FOUND_MESSAGE)

        audit_then_commit(
            conn,
            actor,
            action=ACTION_TERRITORY_ASSIGNMENT_UPDATE,
            target_type=TARGET_TYPE_TERRITORY_ASSIGNMENT,
            target_id=assignment_id,
            target_agency_id=agency_id,
            metadata={"changed_fields": sorted(fields)},
        )
        return updated


def _guard_revoked_is_final(
    assignment: dict[str, Any], fields: dict[str, Any]
) -> None:
    """Da `revoked` non si esce. 409, e prima di qualunque scrittura.

    Il controllo sta QUI e non in uno schema: quale sia lo stato di partenza si
    sa dopo aver letto la riga, e un corpo `{"status": "active"}` e' perfetto
    finche' non si guarda su cosa atterra. E' un conflitto di STATO del
    sistema, non una richiesta malformata - il 409 dice la cosa giusta e il 422
    manderebbe a correggere un payload che non ha nulla che non va.

    `revoked -> revoked` non e' vietato: non riapre niente, e rifiutarlo
    significherebbe che riaffermare uno stato terminale e' un errore.
    """
    if assignment["status"] != ASSIGNMENT_REVOKED:
        return
    if fields.get("status") in (ASSIGNMENT_ACTIVE, ASSIGNMENT_SUSPENDED):
        raise PlatformConflict(ASSIGNMENT_REVOKED_IS_FINAL_MESSAGE)


def _guard_reactivation(
    cur, assignment: dict[str, Any], fields: dict[str, Any]
) -> None:
    """Il messaggio buono quando il territorio e' stato preso da un altro.

    Non e' cio' che rende vera la regola - lo fa il vincolo, e la corsa passa
    solo di li'. Serve a distinguere i due casi: riattivare un'assegnazione di
    un territorio che un'altra agenzia presidia e' un conflitto che va
    raccontato, e `uq_agency_territory_single_active` da solo direbbe soltanto
    che una riga attiva c'era gia'.

    Arriva DOPO `_guard_revoked_is_final`, quindi l'unica riattivazione che
    puo' vedere parte da `suspended`.
    """
    if fields.get("status") != ASSIGNMENT_ACTIVE:
        return
    if assignment["status"] == ASSIGNMENT_ACTIVE:
        return

    active = territories_repository.get_active_assignment(
        cur, assignment["territory_id"]
    )
    if active is None:
        return
    raise PlatformConflict(
        TERRITORY_ALREADY_ASSIGNED_HERE_MESSAGE
        if active["agency_id"] == assignment["agency_id"]
        else TERRITORY_PROTECTED_MESSAGE
    )


def transfer_territory(
    actor: OperatorContext, territory_id: int, agency_id: int
) -> dict[str, Any]:
    """Sposta un territorio dall'agenzia che lo presidia a un'altra.

    UN ATTO ESPLICITO, E MAI L'EFFETTO COLLATERALE DI UNA PATCH.

    Togliere un territorio a un affiliato e' una decisione commerciale. Se
    fosse esprimibile cambiando `agency_id` da `PATCH .../territories/{id}`,
    accadrebbe da una route che dice di aggiornare uno stato, con un audit che
    direbbe `assignment.update` - e nessuno, rileggendo il registro fra un
    anno, troverebbe il momento in cui quel territorio ha cambiato mano.

    L'ORDINE DELLE DUE SCRITTURE E' OBBLIGATORIO.

    Prima si revoca la vecchia, poi si crea la nuova.
    `uq_agency_territory_single_active` e' verificato A OGNI ISTRUZIONE, non
    alla fine della transazione: creare prima la nuova significa che nell'
    istante della INSERT esistono due righe attive per lo stesso territorio, e
    il database rifiuta - immediatamente, non al commit. La stessa lezione del
    trasferimento di titolarita' in P27-3.

    Fra le due istruzioni il territorio attraversa un momento con zero
    assegnazioni attive. E' dentro la stessa transazione e nessuno lo vede: non
    esiste uno stato COMMITTATO con due assegnazioni attive, e non esiste uno
    stato committato senza assegnazione. Se una delle due scritture o l'audit
    falliscono, il `with` annulla entrambe e il territorio resta dov'era.

    La vecchia diventa `revoked` e non `suspended`: `suspended` significa "in
    pausa, torna", e un territorio che e' passato a un altro affiliato non
    torna per conto suo.
    """
    with platform_operation_cursor() as (conn, cur):
        _require_agency(cur, agency_id)
        _require_territory(cur, territory_id)

        current = territories_repository.get_active_assignment(cur, territory_id)
        if current is None:
            raise PlatformConflict(TRANSFER_NOTHING_TO_TRANSFER_MESSAGE)
        if current["agency_id"] == agency_id:
            # Non un no-op silenzioso: un 200 che non ha scritto nulla afferma
            # di aver fatto qualcosa, e su un trasferimento quel qualcosa
            # sarebbe "il territorio ha cambiato mano".
            raise PlatformConflict(TRANSFER_SAME_AGENCY_MESSAGE)

        # 1. chiudi la vecchia. PRIMA, sempre.
        revoked = territories_repository.update_assignment(
            cur, current["id"], {"status": ASSIGNMENT_REVOKED}
        )

        # 2. apri la nuova.
        try:
            assignment = territories_repository.create_assignment(
                cur,
                territory_id=territory_id,
                agency_id=agency_id,
                status=ASSIGNMENT_ACTIVE,
            )
        except errors.UniqueViolation as exc:
            raise _as_conflict(exc) from exc

        # 3. UN audit per UN atto. Le due righe cambiate sono le due meta' di
        #    una cosa sola, e due voci nel registro suggerirebbero che possano
        #    essere avvenute separatamente - che e' precisamente cio' che la
        #    transazione esclude.
        audit_then_commit(
            conn,
            actor,
            action=ACTION_TERRITORY_TRANSFER,
            target_type=TARGET_TYPE_TERRITORY,
            target_id=territory_id,
            # L'agenzia che LO RICEVE. Quella che lo perde e' ricostruibile
            # dalla riga revocata, e mettere anche quella nei metadata
            # duplicherebbe nel registro un dato che le tabelle gia' portano.
            target_agency_id=agency_id,
            metadata={"changed_fields": ["agency_id"]},
        )
        return {"assignment": assignment, "revoked": revoked}
