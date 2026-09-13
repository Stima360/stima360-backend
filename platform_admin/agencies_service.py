"""Le operazioni di rete su `agencies`. P27-2.

Questo modulo possiede il PERCHE' di ogni operazione sulle agenzie. L'ORDINE
fra la scrittura, il suo audit e il commit vive in `platform_admin/transaction.py`,
dove P27-3 lo ha spostato per averne una copia sola invece di due: il
comportamento e' quello certificato in P27-2, invariato.

    nessuna modifica amministrativa viene committata
    se il suo audit non e' stato scritto.

Il repository non puo' farla rispettare - riceve un cursore e non sa dove
finisce la transazione. Il router non puo' - riceve gia' il risultato. Sta
qui, in sei passi, ed e' l'unica ragione per cui questo file esiste invece che
essere quattro funzioni nel router.

I SEI PASSI, E COSA SUCCEDE QUANDO CIASCUNO FALLISCE

    1. si apre la transazione operativa (`platform_operation_cursor`, che NON
       committa);
    2. si esegue INSERT o UPDATE, senza commit;
    3. si chiama `audit.record()`, che scrive su una connessione SUA e
       committa da sola (decisione D2);
    4. se l'audit fallisce -> l'eccezione esce dal `with`, la transazione
       operativa viene annullata, il router risponde 503. Nulla e' avvenuto e
       nulla e' stato registrato: le due cose sono coerenti;
    5. se l'audit riesce -> si committa l'operazione;
    6. se il COMMIT fallisce dopo un audit riuscito -> esiste una riga
       'success' che descrive qualcosa che non e' andato in porto. Si scrive
       una riga compensativa `result='error'` - best effort, perche' se il
       database non committa probabilmente non scrive nemmeno questa - e si
       propaga l'errore.

DUE CONNESSIONI NON SONO ATOMICHE, E NON SI FINGE CHE LO SIANO

Non c'e' un coordinatore di transazioni distribuite e non se ne introduce uno.
Cio' che si sceglie e' quale delle due incoerenze possibili si accetta:

    audit scritto, operazione non committata   ACCETTATA, e dichiarata dalla
                                               riga compensativa 'error'.
    operazione committata, audit non scritto   MAI.

E' l'asimmetria giusta per un registro. Una riga che descrive un tentativo
fallito e' leggibile e vera; una modifica amministrativa senza traccia non e'
ricostruibile da nulla.

Nota che regge proprio qui: il passo 6 registra l'id di un'agenzia che
potrebbe non esistere. Funziona perche' `platform_audit_log` non ha chiavi
esterne (P27-1) - i suoi id sono istantanee storiche e non riferimenti vivi.
Con una FK, la riga compensativa sarebbe stata rifiutata esattamente nel
momento in cui serviva.

LE LETTURE NON AUDITANO

`list_agencies` e `get_agency` non scrivono una riga operativa. L'audit di
ammissione di P27-1 registra gia' chi e' entrato, quando e su quale route: per
una lettura quello E' la traccia. Una riga in piu' per ogni GET
trasformerebbe il registro degli ATTI in un log di traffico, e la prima
schermata di P27-7 che fa polling lo renderebbe illeggibile. Per create e
update la riga operativa e' invece obbligatoria: li' e' cambiato qualcosa.
"""
from __future__ import annotations

from typing import Any

from psycopg2 import errors

from operator_auth.context import OperatorContext

from . import agencies_repository
from .database import platform_operation_cursor
from .enums import (
    ACTION_AGENCY_CREATE,
    ACTION_AGENCY_UPDATE,
    AGENCY_NOT_FOUND_MESSAGE,
    AGENCY_SLUG_CONFLICT_MESSAGE,
    TARGET_TYPE_AGENCY,
)
from .exceptions import AgencyNotFound, AgencySlugConflict
from .transaction import audit_then_commit


# ---------------------------------------------------------------------------
# Letture
# ---------------------------------------------------------------------------

def list_agencies() -> list[dict[str, Any]]:
    """Tutte le agenzie della rete.

    Usa il cursore che non committa, ed e' la scelta giusta anche per una
    lettura: non c'e' niente da committare, e usare un cursore che lo fa
    renderebbe possibile, un giorno, che una lettura committi qualcosa che non
    doveva.
    """
    with platform_operation_cursor() as (_, cur):
        return agencies_repository.list_agencies(cur)


def get_agency(agency_id: int) -> dict[str, Any]:
    """Una agenzia. Solleva `AgencyNotFound` se non esiste."""
    with platform_operation_cursor() as (_, cur):
        row = agencies_repository.get_agency(cur, agency_id)
    if row is None:
        raise AgencyNotFound(AGENCY_NOT_FOUND_MESSAGE)
    return row


# ---------------------------------------------------------------------------
# Mutazioni
# ---------------------------------------------------------------------------

def create_agency(
    actor: OperatorContext,
    *,
    name: str,
    slug: str,
    status: str,
    settings: dict[str, Any],
    created_fields: list[str],
) -> dict[str, Any]:
    """Crea un'agenzia. Solleva `AgencySlugConflict` o `PlatformAuditUnavailable`.

    `created_fields` arriva dal router e contiene i nomi dei campi che il
    CHIAMANTE ha indicato esplicitamente - non tutti e quattro. La differenza
    e' l'unica informazione utile: l'elenco completo sarebbe identico su ogni
    riga, mentre sapere che `status` era esplicito distingue "aperta gia'
    sospesa" da "aperta con il valore predefinito". Sono nomi, mai valori.
    """
    with platform_operation_cursor() as (conn, cur):
        # Il controllo esplicito produce il messaggio buono nel caso normale.
        if agencies_repository.slug_exists(cur, slug):
            raise AgencySlugConflict(AGENCY_SLUG_CONFLICT_MESSAGE)

        try:
            row = agencies_repository.create_agency(
                cur, name=name, slug=slug, status=status, settings=settings
            )
        except errors.UniqueViolation as exc:
            # La corsa fra due creazioni dello stesso slug: il controllo sopra
            # e questa INSERT non sono un'operazione sola. Senza questa
            # cattura, chi perde la corsa riceve un 500 con dentro il nome del
            # vincolo e il valore in conflitto.
            raise AgencySlugConflict(AGENCY_SLUG_CONFLICT_MESSAGE) from exc

        audit_then_commit(
            conn,
            actor,
            action=ACTION_AGENCY_CREATE,
            target_type=TARGET_TYPE_AGENCY,
            target_id=row["id"],
            target_agency_id=row["id"],
            metadata={"created_fields": sorted(created_fields)},
        )
        return row


def update_agency(
    actor: OperatorContext, agency_id: int, fields: dict[str, Any]
) -> dict[str, Any]:
    """Aggiorna i soli campi presenti in `fields`.

    Solleva `AgencyNotFound` o `PlatformAuditUnavailable`. NON
    `AgencySlugConflict`: con lo slug non modificabile non esiste piu' un
    conflitto che questa funzione possa incontrare.

    `fields` contiene gia' soltanto i campi che la richiesta ha indicato: lo
    decide lo schema, con `exclude_unset`. Il service non completa i mancanti
    con i valori attuali, perche' riscrivere una colonna con cio' che gia'
    contiene la farebbe comparire fra i `changed_fields` di una PATCH che non
    la nominava.

    LO SLUG NON E' AGGIORNABILE, E QUI NON C'E' NIENTE CHE LO IMPEDISCA.

    Non per distrazione: e' impedito PRIMA e DOPO. Prima, dallo schema, che non
    dichiara il campo e con `extra="forbid"` risponde 422 a una PATCH che lo
    contenga. Dopo, da `UPDATABLE_COLUMNS`, che rifiuta rumorosamente un nome
    che non possiede. Un terzo controllo in mezzo non aggiungerebbe una
    difesa, aggiungerebbe un posto in cui la regola e' scritta a meta'.

    Ne segue che nessuna UPDATE su `agencies` puo' violare un vincolo di
    unicita': l'unico e' `agencies_slug_unq`. La cattura di `UniqueViolation`
    che questa funzione aveva e' stata tolta con il rename che la giustificava
    - un `except` che non puo' scattare afferma che quel conflitto e' ancora
    possibile, e manda chi legge a cercare un percorso che non esiste. Sulla
    CREAZIONE resta, dove la corsa e' reale.
    """
    with platform_operation_cursor() as (conn, cur):
        if agencies_repository.get_agency(cur, agency_id) is None:
            raise AgencyNotFound(AGENCY_NOT_FOUND_MESSAGE)

        row = agencies_repository.update_agency(cur, agency_id, fields)

        if row is None:
            # L'agenzia c'era all'inizio di questa transazione e non c'e' piu'.
            # Nulla in P26 o P27 cancella un'agenzia, quindi questo non
            # dovrebbe accadere; se accade, e' un 404 onesto e non una riga
            # inventata.
            raise AgencyNotFound(AGENCY_NOT_FOUND_MESSAGE)

        audit_then_commit(
            conn,
            actor,
            action=ACTION_AGENCY_UPDATE,
            target_type=TARGET_TYPE_AGENCY,
            target_id=agency_id,
            target_agency_id=agency_id,
            metadata={"changed_fields": sorted(fields)},
        )
        return row
