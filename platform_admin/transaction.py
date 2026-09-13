"""L'ordine fra la scrittura, il suo audit e il commit. UNA implementazione.

    nessuna modifica amministrativa viene committata
    se il suo audit non e' stato scritto.

P27-2 aveva questa sequenza dentro `agencies_service`, dove serviva a due
operazioni. P27-3 ne porta altre quattro, e a quel punto la scelta e' fra
un'unica copia qui e due copie in due file: due copie sono due posti in cui
invertire l'ordine, e invertirlo non produce un errore - produce una modifica
senza traccia, che nessun test nota se non andandola a cercare.

Il comportamento e' identico a quello certificato in P27-2. Questo modulo non
aggiunge una regola: le da' un posto solo.

I SEI PASSI

    1. il chiamante apre `platform_operation_cursor`, che NON committa;
    2. il chiamante esegue le sue scritture, senza commit;
    3. `audit_then_commit` registra l'audit su una connessione SUA, che
       committa da sola (decisione D2);
    4. se l'audit fallisce -> l'eccezione esce dal `with` del chiamante, la
       transazione operativa viene annullata, il router risponde 503;
    5. se l'audit riesce -> si committa l'operazione;
    6. se il COMMIT fallisce dopo un audit riuscito -> esiste una riga
       'success' che descrive qualcosa che non e' andato in porto. Si scrive
       una riga compensativa `result='error'` - best effort - e si propaga
       l'errore ORIGINALE.

DUE CONNESSIONI NON SONO ATOMICHE, E NON SI FINGE CHE LO SIANO

Non c'e' un coordinatore di transazioni distribuite e non se ne introduce uno.
Si sceglie l'ORDINE, che decide quale delle due incoerenze si accetta:

    audit scritto, operazione non committata   ACCETTATA, e dichiarata dalla
                                               riga compensativa 'error'.
    operazione committata, audit non scritto   MAI.

E' l'asimmetria giusta per un registro: una riga che descrive un tentativo
fallito e' leggibile e vera; una modifica amministrativa senza traccia non e'
ricostruibile da nulla.

La riga compensativa nomina un oggetto che potrebbe non esistere - un'agenzia
mai committata, un operatore mai creato. Funziona perche' `platform_audit_log`
non ha chiavi esterne (P27-1): i suoi id sono istantanee storiche e non
riferimenti vivi. Con una FK sarebbe rifiutata esattamente nel momento in cui
serve.
"""
from __future__ import annotations

import logging
from typing import Any

from operator_auth.context import OperatorContext

from . import audit
from .enums import RESULT_ERROR, RESULT_SUCCESS
from .exceptions import PlatformAuditUnavailable

logger = logging.getLogger(__name__)


def audit_then_commit(
    conn,
    actor: OperatorContext,
    *,
    action: str,
    target_type: str,
    target_id: Any,
    target_agency_id: int | None,
    metadata: dict[str, Any],
) -> None:
    """Passi 3-6.

    Non cattura `PlatformAuditUnavailable`: lasciandola uscire, il `with` del
    chiamante annulla la transazione operativa. Catturarla qui per rilanciare
    qualcos'altro significherebbe decidere qui il rollback, che e' proprio cio'
    che il context manager gia' garantisce.
    """
    audit.record(
        action=action,
        actor=actor,
        result=RESULT_SUCCESS,
        target_type=target_type,
        target_id=target_id,
        target_agency_id=target_agency_id,
        metadata=metadata,
    )

    try:
        conn.commit()
    except Exception:
        _record_commit_failure(
            actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            target_agency_id=target_agency_id,
        )
        raise


def _record_commit_failure(
    actor: OperatorContext,
    *,
    action: str,
    target_type: str,
    target_id: Any,
    target_agency_id: int | None,
) -> None:
    """La riga compensativa. Best effort, e il best effort e' dichiarato.

    Se il commit dell'operazione e' fallito, il database e' probabilmente in
    uno stato in cui non scrivera' nemmeno questa. Si prova, si logga se non
    riesce, e si lascia proseguire l'eccezione vera - che e' il fallimento del
    commit, non il fallimento di annotarlo.

    Sollevare qui sostituirebbe l'errore originale con uno peggiore: chi legge
    il 500 vedrebbe "audit non disponibile" e cercherebbe il problema nel posto
    sbagliato.
    """
    try:
        audit.record(
            action=action,
            actor=actor,
            result=RESULT_ERROR,
            target_type=target_type,
            target_id=target_id,
            target_agency_id=target_agency_id,
            metadata={"commit_failed": True},
        )
    except PlatformAuditUnavailable:
        logger.error(
            "commit fallito per %s su %s %s, e la riga compensativa non e' "
            "stata scritta",
            action,
            target_type,
            target_id,
        )
