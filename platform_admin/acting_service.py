"""Entrare in un'agenzia, e uscirne. P28.

LA REGOLA

    il Superadmin non vede i dati di un'agenzia perche' e' Superadmin. Li vede
    perche' ha dichiarato, con un atto registrato, di stare operando li' dentro
    - e mentre lo fa e' scopato dallo stesso predicate che scopa chiunque
    altro.

Cio' che questo modulo scrive sono DUE COLONNE su una riga di sessione. Non
una membership, non un permesso, non un'eccezione in un filtro. Da quel momento
`resolve_session` restituisce quell'agenzia come agenzia effettiva, e le
centocinquanta route del tenant fanno esattamente cio' che facevano prima,
senza sapere nulla di tutto questo.

L'ASIMMETRIA FRA ENTRARE E USCIRE, CHE E' LA DECISIONE PIU' IMPORTANTE QUI

    ENTRARE  passa da `audit_then_commit`: se il registro non e' scrivibile,
             l'ingresso NON avviene. E' la regola di P27-2, e vale qui come
             altrove - un atto amministrativo che non si riesce a registrare
             non e' avvenuto.

    USCIRE   committa PRIMA e registra DOPO, e se il registro non e'
             scrivibile l'uscita avviene lo stesso.

Sembra un'incoerenza e non lo e'. Le due incoerenze possibili non hanno lo
stesso peso:

    ingresso non registrato   -> qualcuno e' dentro un'agenzia e il registro
                                 non dice chi. INACCETTABILE.
    uscita non registrata     -> il registro dice che e' ancora dentro mentre
                                 e' fuori. Leggibile, correggibile, e nessun
                                 dato in piu' e' accessibile a nessuno.

Applicare qui la regola dell'ingresso produrrebbe il solo esito peggiore di
entrambi: un Superadmin che non riesce a uscire perche' il registro e' rotto -
cioe' intrappolato dentro un'agenzia da un guasto che con quell'agenzia non
c'entra niente. L'uscita, come il 403 di `require_platform_admin`, non sta
concedendo nulla: toglie. E cio' che toglie va tolto comunque.
"""
from __future__ import annotations

from typing import Any

from operator_auth.context import OperatorContext

from . import acting_repository, agencies_repository, transaction
from .database import platform_operation_cursor
from .enums import (
    ACTING_AGENCY_NOT_ACTIVE_MESSAGE,
    ACTING_ALREADY_ACTIVE_MESSAGE,
    ACTING_NEEDS_SESSION_MESSAGE,
    ACTING_SESSION_GONE_MESSAGE,
    ACTION_ACTING_ENTER,
    ACTION_ACTING_EXIT,
    AGENCY_NOT_FOUND_MESSAGE,
    AGENCY_STATUS_ACTIVE,
    TARGET_TYPE_AGENCY,
)
from .exceptions import AgencyNotFound, PlatformConflict
from .transaction import audit_then_commit, commit_then_audit


def _require_session(actor: OperatorContext) -> int:
    """L'id di sessione dell'attore, o un conflitto dichiarato."""
    if actor.session_id is None:
        raise PlatformConflict(ACTING_NEEDS_SESSION_MESSAGE)
    return actor.session_id


def enter_agency(actor: OperatorContext, agency_id: int) -> dict[str, Any]:
    """Entra nell'agenzia indicata. Restituisce il contesto che ne risulta.

    L'ORDINE DEI CONTROLLI, E PERCHE' QUESTO

    1. **C'e' una sessione?** Senza, non esiste il posto in cui scrivere.
    2. **Sto gia' dentro da qualche parte?** Prima dell'esistenza dell'agenzia,
       perche' "esci prima" e' la risposta giusta anche quando la seconda
       agenzia non esiste: il chiamante ha comunque commesso l'errore di
       provare a entrare due volte, e dirgli 404 lo manderebbe a cercare un
       problema che non ha.
    3. **L'agenzia esiste?** 404.
    4. **E' attiva?** 409.

    Nessuno dei quattro legge qualcosa dal client oltre `agency_id`, che e' un
    identificatore nel path - la forma che tutte le ventitre route di questa
    superficie usano gia'. Non costruisce uno scope: nomina un bersaglio, che
    viene verificato qui e poi persistito lato server. Il valore che finira' a
    scopare le query e' quello riletto dalla riga di sessione alla richiesta
    successiva, non questo.

    NON tocca `agency_memberships`, e non ne ha nemmeno il modo: il repository
    di questo percorso conosce una tabella sola.
    """
    session_id = _require_session(actor)

    with platform_operation_cursor() as (conn, cur):
        sessione = acting_repository.get_session_acting(cur, session_id)
        if sessione is None:
            raise PlatformConflict(ACTING_SESSION_GONE_MESSAGE)

        if sessione.get("acting_agency_id") is not None:
            # Anche quando l'agenzia richiesta e' la stessa in cui si e' gia'.
            # Un secondo ingresso senza un'uscita in mezzo scriverebbe un
            # `acting_entered_at` nuovo su una permanenza mai interrotta, e il
            # registro direbbe che qualcuno e' entrato due volte dallo stesso
            # posto senza esserne mai uscito.
            raise PlatformConflict(ACTING_ALREADY_ACTIVE_MESSAGE)

        agenzia = agencies_repository.get_agency(cur, agency_id)
        if agenzia is None:
            raise AgencyNotFound(AGENCY_NOT_FOUND_MESSAGE)
        if agenzia["status"] != AGENCY_STATUS_ACTIVE:
            raise PlatformConflict(ACTING_AGENCY_NOT_ACTIVE_MESSAGE)

        toccate = acting_repository.set_acting(
            cur, session_id=session_id, agency_id=agency_id
        )
        if toccate == 0:
            raise PlatformConflict(ACTING_SESSION_GONE_MESSAGE)

        audit_then_commit(
            conn,
            actor,
            action=ACTION_ACTING_ENTER,
            target_type=TARGET_TYPE_AGENCY,
            target_id=agency_id,
            # Qui `target_agency_id` E' l'agenzia: l'atto riguarda proprio lei,
            # a differenza di un alias o di un territorio, che riguardano un
            # posto e non chi lo presidia oggi.
            target_agency_id=agency_id,
            metadata={
                "session_id": session_id,
                "agency_slug": agenzia["slug"],
            },
        )

        return {
            "acting_agency_id": agency_id,
            "acting_agency_name": agenzia["name"],
            "acting_agency_slug": agenzia["slug"],
        }


def exit_agency(actor: OperatorContext) -> None:
    """Esce dall'agenzia in cui si sta operando. Idempotente, e non fallisce.

    Se non si sta operando da nessuna parte non succede niente e non viene
    registrato niente: un registro che annota anche i non-eventi diventa
    illeggibile proprio quando serve.

    L'ordine e' COMMIT-POI-AUDIT, invertito rispetto a ogni altra scrittura
    della superficie. La ragione sta nel docstring del modulo: fra "uscita non
    registrata" e "Superadmin intrappolato dentro un'agenzia" il secondo e' il
    danno peggiore, e sceglierlo per coerenza formale con l'ingresso sarebbe
    scegliere la regola contro la cosa che la regola protegge.
    """
    session_id = _require_session(actor)

    with platform_operation_cursor() as (conn, cur):
        sessione = acting_repository.get_session_acting(cur, session_id)
        if sessione is None or sessione.get("acting_agency_id") is None:
            return

        lasciata = int(sessione["acting_agency_id"])
        acting_repository.clear_acting(cur, session_id=session_id)

        # `commit_then_audit` e non `audit_then_commit`: e' l'unica deroga
        # all'ordine di P27-2, vive accanto alla regola che deroga, ed e'
        # riservata alle operazioni che TOLGONO un accesso. Vedi il docstring
        # di quella funzione.
        commit_then_audit(
            conn,
            actor,
            action=ACTION_ACTING_EXIT,
            target_type=TARGET_TYPE_AGENCY,
            target_id=lasciata,
            target_agency_id=lasciata,
            metadata={"session_id": session_id},
        )
