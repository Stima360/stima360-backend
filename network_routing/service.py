"""La decisione: quale agenzia riceve questa stima pubblica.

DETERMINISTICA. Dato lo stesso comune e lo stesso stato della rete, la risposta
e' sempre la stessa riga. Non c'e' round-robin, non c'e' punteggio, non c'e'
bilanciamento di carico, non ci sono SLA ne' priorita' commerciali: sono
assenti perche' introdurrebbero uno stato nascosto - "a chi e' toccato
l'ultimo" - che renderebbe la stessa domanda una risposta diversa.

LA PIPELINE, PER INTERO:

    stime.comune
      -> normalizzazione minima del valore di confronto        (in SQL)
      -> alias `source='public_stima_comune'`, status active
      -> territory_id
      -> territorio, kind 'municipality'
      -> assegnazione attiva
      -> agenzia attiva
      -> stime.agency_id

    alias assente (o qualunque anello rotto) -> ripiego STIMA360 attivo
    nemmeno il ripiego                        -> RoutingUnavailable

Il terzo esito non e' un caso limite da addolcire. Senza un'agenzia valida la
stima non ha un proprietario, e l'unica alternativa a sollevare sarebbe
sceglierne una a caso - cioe' attribuire i dati di una persona a un'azienda che
non c'entra.
"""

from __future__ import annotations

from dataclasses import dataclass

from operator_auth.context import SystemAgencyContext
from operator_auth.enums import DEFAULT_AGENCY_SLUG

from .repository import (
    ALIAS_SOURCE_PUBLIC_STIMA,
    find_fallback_agency_id,
    find_persisted_stima_agency_id,
    find_routable_agency_id,
)

#: L'origine che `core.repository.bridge_public_stima` accetta, e nessun'altra.
#: Ripetuta qui come costante invece di importata da `core`: questo pacchetto
#: non deve dipendere dal CRM (vedi __init__), e il valore e' un contratto
#: stabile dal P26-2B. Un test lo confronta con quello del bridge, cosi' una
#: divergenza si vede qui e non su Render.
PUBLIC_STIMA_ORIGIN = "public_stima"


class RoutingUnavailable(RuntimeError):
    """Nessuna agenzia puo' ricevere il lead, nemmeno quella di ripiego.

    Deterministico e non silenzioso: chi chiama interrompe. La stima non viene
    scritta senza proprietario, e nessuna agenzia viene scelta al posto di
    quella che manca.
    """


class PersistedAgencyMissing(RoutingUnavailable):
    """La stima non ha un'agenzia scritta, oppure non esiste affatto.

    Sottoclasse di `RoutingUnavailable` perche' chi gia' gestisce "nessuna
    agenzia puo' ricevere questo lead" gestisce anche questo caso senza doverlo
    sapere; distinta perche' la causa e' opposta e la diagnosi deve dirlo.

    Non si ricade sul routing corrente e non si ricade sul ripiego: entrambi
    sceglierebbero un'agenzia OGGI per una stima nata ieri, che e' esattamente
    la seconda decisione che non deve esistere.
    """


@dataclass(frozen=True)
class RoutingDecision:
    """Cosa e' stato deciso, e perche'. Congelata: e' un verdetto, non uno stato.

    `matched_value` c'e' anche quando l'alias non e' stato trovato: e' il
    valore che si e' CERCATO, e serve a distinguere "comune assente" (None) da
    "comune presente ma nessun alias lo dichiara" (il valore). Senza, le due
    diagnosi sarebbero la stessa riga di log.
    """

    agency_id: int
    source: str
    matched_value: str | None

    #: I due valori che `source` puo' assumere. Costanti e non stringhe sparse:
    #: chi legge un log deve poter cercare un valore, non ricordarne la forma.
    TERRITORY = "territory"
    FALLBACK = "fallback"


def resolve_agency_for_public_stima(
    cur, *, comune: object, fallback_slug: str
) -> RoutingDecision:
    """L'agenzia che ricevera' questa stima, decisa lato server.

    `comune` arriva dal payload pubblico ed e' l'unico dato geografico che
    esista all'ingresso. Non e' un `agency_id` e non puo' diventarlo: il
    massimo che un client possa fare dichiarando un comune diverso e' farsi
    instradare a un'altra agenzia della RETE - che e' esattamente la funzione
    di un routing territoriale, e non un modo di scegliere un'agenzia.

    Nessun parametro di questa funzione nomina un'agenzia se non per SLUG di
    ripiego, che e' una costante del server.

    Il cursore e' quello del chiamante: la decisione avviene dentro la
    transazione che scrivera' la stima, quindi l'agenzia e' provata attiva
    nell'istante in cui la riga viene scritta e non in uno precedente.

    LA NORMALIZZAZIONE NON E' QUI. Un tempo questo modulo importava un
    `canonical.py` che trasformava 'Alba Adriatica' in 'alba-adriatica' e
    cercava quella chiave: era un secondo algoritmo di identita', e sbagliava
    ogni volta che la `canonical_key` del territorio non era lo slug della sua
    etichetta - cosa che P27-5 permette esplicitamente. Adesso il confronto e'
    fra il valore in ingresso e un alias DICHIARATO, e le tre pieghe che li
    rendono confrontabili vivono in SQL, nella stessa espressione dell'indice
    unico della 059.
    """
    testo = None if comune is None else " ".join(str(comune).split())
    if testo:
        agency_id = find_routable_agency_id(cur, comune=testo)
        if agency_id is not None:
            return RoutingDecision(
                agency_id=agency_id,
                source=RoutingDecision.TERRITORY,
                matched_value=testo,
            )

    ripiego = find_fallback_agency_id(cur, slug=fallback_slug)
    if ripiego is None:
        raise RoutingUnavailable(
            f"nessun alias {ALIAS_SOURCE_PUBLIC_STIMA} instradabile per il "
            f"comune ricevuto e nessuna agenzia attiva con slug "
            f"{fallback_slug!r}: la stima non ha un proprietario e non ne "
            "viene scelto uno a caso"
        )
    return RoutingDecision(
        agency_id=ripiego,
        source=RoutingDecision.FALLBACK,
        matched_value=testo or None,
    )


def system_context_for_persisted_public_stima(cur, *, stima_id: int):
    """Il contesto del bridge, RICAVATO DALLA STIMA GIA' SCRITTA.

    E' questa la funzione che rende idempotente il secondo passaggio, e la
    ragione per cui esiste separata da quella qui sotto merita di essere detta
    per intero.

    IL DIFETTO CHE CORREGGE. Prima, il contesto che arrivava al bridge era
    quello costruito dalla decisione di routing. Sul primo passaggio i due
    coincidono - la decisione e' appena stata incisa su `stime.agency_id`. Su un
    SECONDO passaggio no: se qualcuno riesegue il bridge sullo stesso
    `stima_id` dopo che un alias e' stato ripuntato, un routing rifatto
    direbbe "agenzia B" per una stima che appartiene ad A. Il trigger
    `trg_lead_stime_agency_coherence` lo avrebbe fermato - ma fermandolo con un
    ERRORE, non completando l'operazione. Coerenza, non idempotenza: il
    retry finiva in eccezione invece di finire bene.

    LA REGOLA. Dal `COMMIT` della stima in poi, `stime.agency_id` e' la fonte
    di verita' immutabile per quel processo. Chi riprende quel processo la
    LEGGE; non la ricalcola, e non guarda gli alias di oggi per decidere.

    NON E' UNA SECONDA DECISIONE DI ROUTING. La decisione e' una sola, presa
    una volta e scritta; questa funzione la rilegge. La distinzione e' visibile
    nella query di `find_persisted_stima_agency_id`, che non nomina alias,
    territori ne' assegnazioni.

    Il trigger 033 resta, e resta utile: e' la difesa del database contro una
    strada che oggi non esiste. Ma smette di essere il meccanismo con cui un
    retry normale si comporta bene.

    Ritorna il solo contesto, senza `RoutingDecision`: qui non si e' deciso
    niente, e fabbricare un verdetto per un atto che non c'e' stato darebbe a
    un log la forma di una scelta appena compiuta.
    """
    agency_id = find_persisted_stima_agency_id(cur, stima_id=stima_id)
    if agency_id is None:
        raise PersistedAgencyMissing(
            f"la stima {stima_id} non esiste o non ha un'agenzia scritta: il "
            "bridge non riparte, e nessuna agenzia viene scelta al suo posto"
        )
    return SystemAgencyContext(agency_id=agency_id, origin=PUBLIC_STIMA_ORIGIN)


def system_context_for_routed_public_stima(cur, *, comune):
    """Il contesto che il funnel pubblico portera' fino al bridge.

    PERCHE' QUI E NON IN `core/scope.py`, che sarebbe stato il posto ovvio.

    P26-1 ha SIGILLATO quel modulo con tre invarianti che i suoi test
    verificano: un solo punto di costruzione di `SystemAgencyContext`, un
    insieme chiuso di import, una superficie pubblica dichiarata nome per nome.
    Una seconda fabbrica la' dentro li viola tutti e tre - il primo tentativo
    di P27-6 lo ha fatto, e `test_p26_1_scope_enforcement` lo ha respinto.

    Non era un cavillo. Quel sigillo dice che il CRM ha UN modo di ottenere uno
    scope di sistema, e che chiunque ne voglia un altro lo costruisce a casa
    propria e se ne assume la responsabilita'. E' esattamente quel che accade
    qui: il routing e' una regola di rete, quindi la fabbrica che ne deriva uno
    scope vive nel pacchetto della rete.

    `core.scope.system_context_for_public_stima` resta intatta e resta il
    ripiego: quando nessun alias dichiara il comune, questa funzione produce lo
    stesso identico contesto che produceva lei - stessa agenzia, stesso tipo,
    stessa origine.

    Ritorna la coppia `(contesto, decisione)`. La decisione non entra nel
    contesto - `SystemAgencyContext` e' congelato e il bridge lo controlla per
    tipo - ma serve al log: senza, "questo lead e' andato ad Alba Adriatica" e
    "questo lead e' andato al ripiego" sarebbero la stessa riga.
    """
    decision = resolve_agency_for_public_stima(
        cur, comune=comune, fallback_slug=DEFAULT_AGENCY_SLUG
    )
    return SystemAgencyContext(
        agency_id=decision.agency_id, origin=PUBLIC_STIMA_ORIGIN
    ), decision
