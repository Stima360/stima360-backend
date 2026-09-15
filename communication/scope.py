"""The agency predicate for communication_messages.

PERCHE' QUESTO FILE ESISTE INVECE DI UNA RIGA IN core/scope.py

Stessa ragione, verificata e non supposta, gia' documentata in
consent/scope.py: `tests/test_p26_1_scope_enforcement.py` blocca
`core.scope.SCOPED_TABLES` sul suo contenuto esatto

    assert SCOPED_TABLES == frozenset({"contacts", "leads", "activities", "tasks"})

e aggiungere un membro farebbe fallire un test di una fase gia' certificata.
P29 non riscrive P26: una collisione con lavoro gia' fatto si segnala, non si
modifica.

Il predicato per la tabella di P29-2 vive quindi qui. La duplicazione e' UNA
riga di SQL, ed e' contenuta: questo modulo scopa una sola tabella, non ha il
ramo per gli agenti, e importa da `core.scope` tutto cio' che puo' - il
predicato su `contacts` passa di la', non da qui.

PERCHE' NON C'E' IL RAMO `agent`

In CORE un agente e' ristretto ai propri contatti e ai propri lead
(AGENT_ASSIGNABLE). Qui non serve un secondo filtro: ogni scrittura parte
SEMPRE da un contatto risolto con `core.scope.scoped_predicate(ctx, 'contacts',
'c')`, che quel ramo ce l'ha gia'. Un agente che non vede il contatto non
arriva mai ad accodargli un messaggio, e un secondo predicato su
`communication_messages` restringerebbe due volte la stessa cosa - con il
rischio, il giorno in cui i due divergessero, di due risposte diverse alla
stessa domanda.

`communication_messages` non ha `assigned_agent_id` e non deve averlo: un
messaggio e' della persona e dell'agenzia, non dell'agente di turno.

PERCHE' `communication_attempts` NON E' QUI

Non ha un lettore in P29-2.2. I tentativi nascono al claim, che e' P29-2.3, e
una sorgente scopata che nessuno chiama non e' protezione: e' codice non
esercitato che sembra protezione. Si aggiunge nella fase che li legge.
"""

from __future__ import annotations

from operator_auth.context import AgencyScope

# L'unica tabella che questo modulo scopa oggi. frozenset, non set: un insieme
# mutabile potrebbe essere allargato a runtime da qualunque modulo che lo
# importi (stessa ragione dichiarata in core/scope.py e in consent/scope.py).
COMMUNICATION_SCOPED_TABLES = frozenset({"communication_messages"})


class ProgrammingError(Exception):
    """Si e' chiesto uno scope per una tabella che questo builder non scopa.

    Deliberatamente NON una sottoclasse di CommunicationError: un nome di
    tabella e' un letterale di sviluppatore e non e' mai input di richiesta,
    quindi arrivare qui e' un difetto di questo codice. Deve emergere come
    guasto, non essere travestito da errore del chiamante. Stessa scelta, e
    stesse parole, di core.scope.ProgrammingError.
    """


def communication_scoped_source(ctx: AgencyScope, table: str, alias: str) -> tuple[str, list]:
    """Return ``('<table> <alias> WHERE <predicate>', params)`` - never without.

    Il nome della tabella e il suo scope escono dalla stessa espressione, come
    in `core.scope.scoped_source` e in `consent.scope.consent_scoped_source`: un
    chiamante non puo' nominare la tabella senza portarsi il predicato, e non
    puo' essere lui a iniziare la clausola WHERE - quindi un predicato
    dimenticato non e' un errore disponibile.

    `table` e' un letterale di sviluppatore e non e' mai input di richiesta;
    viene comunque controllato contro COMMUNICATION_SCOPED_TABLES prima di
    essere interpolato.
    """
    if table not in COMMUNICATION_SCOPED_TABLES:
        raise ProgrammingError(f"{table!r} is not a scoped COMMUNICATION table")
    return f"{table} {alias} WHERE {alias}.agency_id = %s", [ctx.require_agency()]
