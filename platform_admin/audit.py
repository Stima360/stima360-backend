"""Il writer di platform_audit_log. P27-1.

UNA funzione pubblica, `record()`, e una sola definizione di cosa sia una riga
di audit di piattaforma. Tutto cio' che P27-2 e seguenti dovranno registrare
passa di qui: se il formato dell'attore, la validazione dell'azione o il
confine transazionale dovessero cambiare, cambiano in un posto.

TRE PROPRIETA', TUTTE DELIBERATE

1. **Transazione propria** (D2). La riga sopravvive all'esito dell'operazione
   che descrive. Vedi `database.platform_audit_cursor` per il ragionamento
   completo.

2. **Non inghiotte gli errori.** Un fallimento di scrittura solleva
   `PlatformAuditUnavailable`, e chi chiama decide. Un writer che loggasse e
   basta renderebbe impossibile la regola "un'operazione amministrativa che non
   si riesce a registrare non avviene", che e' la ragione per cui la tabella
   esiste.

3. **Valida i propri letterali.** `action` e `result` sono scritti dallo
   sviluppatore, non dal chiamante HTTP: un refuso li' e' un difetto, e la
   risposta giusta a un difetto e' un errore rumoroso al momento della
   chiamata, non una riga di audit che nessuna query ritrovera' piu'.

COSA NON FINISCE IN `metadata`

`metadata` e' un JSONB libero, che e' esattamente il motivo per cui va
guardato. Non ci vanno credenziali, token, hash, header, corpi di richiesta,
query string, ne' dati personali di un contatto o di un proprietario. Le
chiamate di P27-1 ci mettono il path e il metodo HTTP e nient'altro; la regola
e' scritta qui perche' e' qui che passeranno anche le chiamate delle fasi
successive.
"""
from __future__ import annotations

from typing import Any

from operator_auth.context import OperatorContext
from operator_auth.dependencies import audit_actor

from . import repository
from .database import platform_audit_cursor
from .enums import (
    ACTION_NAMESPACE,
    ANONYMOUS_ACTOR,
    AUDIT_RESULTS,
    RESULT_SUCCESS,
)
from .exceptions import PlatformAuditUnavailable


def actor_label(context: OperatorContext | None) -> str:
    """L'attore, nel formato che l'audit trail di questo progetto usa gia'.

    Delega a `operator_auth.dependencies.audit_actor`, che dal P26-5 e' la
    definizione di quel formato (`operator:<user_id>`), invece di ricostruirlo.
    Due registri con due grafie dello stesso attore non si correlano, e la
    grafia non e' una cosa su cui due moduli devono essere d'accordo per
    convenzione.

    L'id e non l'email, di proposito: `/me` esclude deliberatamente l'email e
    un audit trail non e' il posto in cui reintrodurre un dato personale.
    """
    if context is None or context.user_id is None:
        return ANONYMOUS_ACTOR
    return audit_actor(context)


def record(
    *,
    action: str,
    actor: OperatorContext | None = None,
    result: str = RESULT_SUCCESS,
    target_type: str | None = None,
    target_id: Any = None,
    target_agency_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    """Registra un atto amministrativo e restituisce l'id della riga.

    Keyword-only: vedi `repository.insert_audit_entry`.

    `target_id` viene convertito in testo perche' la colonna e' testo - la
    stessa scelta di `owner_audit_log`. Un id resta leggibile anche quando la
    riga a cui puntava non esiste piu', che e' precisamente cio' che un
    registro storico deve garantire.

    Solleva `PlatformAuditUnavailable` se la riga non e' stata scritta. Non
    ritorna mai None e non ritorna mai un id inventato.
    """
    if not action.startswith(ACTION_NAMESPACE):
        raise ValueError(
            f"azione di audit {action!r} fuori dallo spazio dei nomi "
            f"{ACTION_NAMESPACE!r}"
        )
    if result not in AUDIT_RESULTS:
        raise ValueError(
            f"esito di audit {result!r} non ammesso; attesi {AUDIT_RESULTS}"
        )

    actor_user_id = actor.user_id if actor is not None else None

    try:
        with platform_audit_cursor() as (_, cur):
            return repository.insert_audit_entry(
                cur,
                actor_user_id=actor_user_id,
                actor_label=actor_label(actor),
                action=action,
                result=result,
                target_type=target_type,
                target_id=None if target_id is None else str(target_id),
                target_agency_id=target_agency_id,
                metadata=metadata or {},
            )
    except Exception as exc:
        # Rilanciata come tipo di dominio, con la causa allegata. Il messaggio
        # originale non viene propagato al chiamante HTTP da nessuna parte: un
        # errore di database puo' contenere nomi di oggetti e valori.
        raise PlatformAuditUnavailable(
            "la riga di platform_audit_log non e' stata scritta"
        ) from exc
