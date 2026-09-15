"""The agency predicate for consent_events.

PERCHE' QUESTO FILE ESISTE INVECE DI UNA RIGA IN core/scope.py

La cosa giusta sarebbe stata aggiungere 'consent_events' a
`core.scope.SCOPED_TABLES`: una sola sede per le regole di agency, che e' il
punto di quel modulo. Non si e' fatto, e la ragione e' verificata, non
supposta.

`tests/test_p26_1_scope_enforcement.py` (riga 1527) blocca quell'insieme sul
suo contenuto esatto:

    assert SCOPED_TABLES == frozenset({"contacts", "leads", "activities", "tasks"})

Aggiungere un membro farebbe fallire un test di una fase gia' certificata, e
P29 non riscrive P26. La regola del committente e' esplicita: se trovi una
collisione con lavoro gia' fatto, la SEGNALI, non la modifichi.

Quindi il predicato per la tabella di P29 vive qui. La duplicazione e' UNA
riga di SQL, ed e' contenuta: questo modulo scopa una sola tabella, non ha il
ramo per gli agenti, e importa da `core.scope` tutto cio' che puo' - il
predicato su `contacts` passa di la', non da qui.

PERCHE' NON C'E' IL RAMO `agent`

In CORE un agente e' ristretto ai propri contatti e ai propri lead
(AGENT_ASSIGNABLE). Qui non serve un secondo filtro: ogni lettura e ogni
scrittura di consenso parte SEMPRE da un contatto risolto con
`core.scope.scoped_predicate(ctx, 'contacts', 'c')`, che quel ramo ce l'ha
gia'. Un agente che non vede il contatto non arriva mai a nominarne il
consenso, e un secondo predicato su `consent_events` restringerebbe due volte
la stessa cosa - con il rischio, il giorno in cui i due divergessero, di due
risposte diverse alla stessa domanda.

`consent_events` non ha `assigned_agent_id` e non deve averlo: un consenso e'
della persona e dell'agenzia, non dell'agente di turno.
"""

from __future__ import annotations

from operator_auth.context import AgencyScope

# L'unica tabella che questo modulo scopa. frozenset, non set: un insieme
# mutabile potrebbe essere allargato a runtime da qualunque modulo che lo
# importi (stessa ragione dichiarata in core/scope.py).
CONSENT_SCOPED_TABLES = frozenset({"consent_events"})


class ProgrammingError(Exception):
    """Si e' chiesto uno scope per una tabella che questo builder non scopa.

    Deliberatamente NON una sottoclasse di ConsentError: un nome di tabella e'
    un letterale di sviluppatore e non e' mai input di richiesta, quindi
    arrivare qui e' un difetto di questo codice. Deve emergere come guasto, non
    essere travestito da errore del chiamante. Stessa scelta, e stesse parole,
    di core.scope.ProgrammingError.
    """


def consent_scoped_source(ctx: AgencyScope, table: str, alias: str) -> tuple[str, list]:
    """Return ``('<table> <alias> WHERE <predicate>', params)`` - never without.

    Il nome della tabella e il suo scope escono dalla stessa espressione, come
    in `core.scope.scoped_source`: un chiamante non puo' nominare la tabella
    senza portarsi il predicato, e non puo' essere lui a iniziare la clausola
    WHERE - quindi un predicato dimenticato non e' un errore disponibile.

    A differenza di `core.scope` qui non esiste una funzione separata che
    restituisca il solo predicato: in CORE serve perche' UPDATE, DELETE e le
    join con USING non hanno una FROM in cui infilare la sorgente. Questo
    modulo non emette nessuna di quelle forme su `consent_events` - lo storico
    non si aggiorna e non si cancella - quindi una seconda funzione sarebbe
    stata una copia con un solo chiamante.

    `table` e' un letterale di sviluppatore e non e' mai input di richiesta;
    viene comunque controllato contro CONSENT_SCOPED_TABLES prima di essere
    interpolato.
    """
    if table not in CONSENT_SCOPED_TABLES:
        raise ProgrammingError(f"{table!r} is not a scoped CONSENT table")
    return f"{table} {alias} WHERE {alias}.agency_id = %s", [ctx.require_agency()]
