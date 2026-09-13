"""Le due sole letture che il routing fa. Sola lettura, cursore ricevuto.

Nessun `commit`, nessuna transazione aperta qui: il cursore arriva da chi sta
gia' scrivendo la stima, quindi la decisione e la scrittura stanno nella stessa
transazione e non possono divergere. E' lo stesso motivo per cui P26-2B fa
risolvere l'agenzia predefinita al writer sulla sua connessione.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Il livello su cui si instrada. UNO SOLO, e non per semplificare.
#:
#: All'ingresso pubblico esiste solo `comune`: non c'e' CAP, non c'e' provincia
#: - ne' nel payload di `/api/salva_stima`, ne' nella tabella `stime`. I kind
#: 'postal_code' e 'province' esistono in P27-5 e restano dichiarabili
#: dall'amministratore, ma nessun dato in ingresso potrebbe mai selezionarli:
#: un ramo che li cercasse sarebbe codice che non puo' essere raggiunto.
ROUTING_KIND = "municipality"

#: La sorgente degli alias che questo routing consulta. Una sola, oggi.
ALIAS_SOURCE_PUBLIC_STIMA = "public_stima_comune"

#: LA NORMALIZZAZIONE, SCRITTA UNA VOLTA E RIUSATA TRE.
#:
#: Deve essere IDENTICA a quella dell'indice unico della 059
#: (`uq_territory_alias_active_value`), altrimenti succedono due cose e nessuna
#: si vede subito: l'indice non viene usato dal planner, e - molto peggio - due
#: valori che l'indice considera uguali qui risultano diversi, cosi' il
#: database garantisce un'unicita' su cui questa query non conta.
#:
#: Tre pieghe e nessuna di piu': spazi interni collassati, spazi ai lati via,
#: maiuscole ignorate. Niente slug, niente traslitterazione, niente accenti
#: rimossi: 'Alba Adriatica' resta 'alba adriatica', non diventa
#: 'alba-adriatica'.
#:
#: Un test confronta questa costante con il testo della migration: una
#: divergenza fallisce li' e non su Render.
NORMALISED = "lower(btrim(regexp_replace({}, '\\s+', ' ', 'g')))"


def find_routable_agency_id(cur, *, comune: str) -> int | None:
    """L'agenzia che presidia il comune, se ce n'e' una ROUTABILE.

    LA CATENA, PER INTERO E IN UNA SOLA QUERY:

        alias (source, match_value normalizzato, status active)
          -> territorio, che DEVE essere kind 'municipality'
            -> assegnazione, che deve essere active
              -> agenzia, che deve essere active

    Ogni anello e' una condizione nella WHERE. In Python sarebbero righe lette
    e poi scartate: la query restituirebbe l'agenzia sospesa, e qualcuno un
    giorno userebbe quel risultato prima dello scarto.

    IL FILTRO SUL `kind` E' RIDONDANTE E RESTA. La gestione degli alias rifiuta
    gia' un territorio che non sia un comune, quindi un alias incoerente non
    dovrebbe esistere. "Non dovrebbe" non e' "non puo'": la riga si potrebbe
    scrivere da SQL, e senza questo filtro un alias appeso a una provincia
    instraderebbe l'intera provincia a chi presidia un comune.

    L'IDENTITA' NON E' MAI `label`, e non e' nemmeno `canonical_key` calcolata.
    La corrispondenza fra il testo in ingresso e il territorio e' DICHIARATA
    nella tabella degli alias - e' il difetto che la 059 corregge: P27-5 ha
    separato `label` e `canonical_key` di proposito, e nessun vincolo obbliga
    la chiave di un comune a essere lo slug della sua etichetta.

    NIENTE `LIMIT 1`, E NIENTE `fetchall`. Un `LIMIT 1` sceglierebbe in
    silenzio fra due righe; `fetchall` allargherebbe il contratto del cursore,
    che dal P26-2B e' `execute` + `fetchone`. `COUNT(*) OVER ()` porta il
    totale dentro la prima riga: una lettura sola basta per l'id e per
    accorgersi che di righe ce n'e' piu' d'una.

    Ritorna `None` quando non c'e' alias, o il territorio non e' un comune, o
    nessuna assegnazione/agenzia e' attiva. Casi diversi, stessa risposta: chi
    chiama non deve distinguerli, deve ripiegare.
    """
    cur.execute(
        f"""
        SELECT ag.id AS agency_id,
               COUNT(*) OVER () AS instradabili
          FROM network_territory_aliases al
          JOIN network_territories t ON t.id = al.territory_id
          JOIN agency_territory_assignments a ON a.territory_id = t.id
          JOIN agencies ag ON ag.id = a.agency_id
         WHERE al.source = %s
           AND al.status = 'active'
           AND {NORMALISED.format('al.match_value')}
             = {NORMALISED.format('%s')}
           AND t.kind = %s
           AND a.status = 'active'
           AND ag.status = 'active'
         ORDER BY ag.id
        """,
        (ALIAS_SOURCE_PUBLIC_STIMA, comune, ROUTING_KIND),
    )
    riga = cur.fetchone()
    if riga is None:
        return None
    quante = int(_value(riga, "instradabili", indice=1))
    if quante > 1:
        raise RoutingAmbiguous(
            f"il comune ricevuto porta a {quante} agenzie instradabili: "
            "uq_territory_alias_active_value o "
            "uq_agency_territory_single_active non stanno facendo il loro lavoro"
        )
    return int(_value(riga, "agency_id", indice=0))


def find_fallback_agency_id(cur, *, slug: str) -> int | None:
    """L'agenzia di ripiego, per slug e solo se ATTIVA.

    Stessa domanda che `core.scope.resolve_default_agency_id` pone da P26-1, e
    la stessa risposta: se non c'e' un'agenzia attiva con quello slug, questa
    funzione non ne sceglie un'altra. Ritorna `None` e chi chiama solleva.
    """
    cur.execute(
        "SELECT id FROM agencies WHERE slug = %s AND status = 'active'",
        (slug,),
    )
    riga = cur.fetchone()
    if riga is None:
        return None
    return int(_value(riga, "id", indice=0))


def find_persisted_stima_agency_id(cur, *, stima_id: int) -> int | None:
    """L'agenzia GIA' SCRITTA su quella stima. Nessuna decisione, una lettura.

    QUESTA NON E' UNA QUERY DI ROUTING, ed e' la distinzione su cui regge
    l'idempotenza. Non nomina alias, territori ne' assegnazioni: legge la
    colonna in cui la decisione e' stata incisa quando la stima e' stata
    scritta, e che da quel `COMMIT` in poi e' la fonte di verita' immutabile
    per quel processo.

    La differenza si vede quando la rete cambia. `find_routable_agency_id`
    rispondera' con l'agenzia che presidia il comune OGGI; questa rispondera'
    sempre con quella a cui la stima appartiene dal giorno in cui e' nata. Per
    un secondo passaggio sullo stesso `stima_id` e' la seconda la risposta
    giusta, e l'unica che non sposti un lead gia' consegnato.

    `None` significa due cose diverse e il chiamante le distingue guardando la
    riga: stima inesistente, oppure - impossibile dalla 033 in poi, che ha reso
    `agency_id` NOT NULL - stima senza agenzia.
    """
    cur.execute("SELECT agency_id FROM stime WHERE id = %s", (stima_id,))
    riga = cur.fetchone()
    if riga is None:
        return None
    valore = _value(riga, "agency_id", indice=0)
    return None if valore is None else int(valore)


def _value(riga: Any, chiave: str, *, indice: int) -> Any:
    """Legge una colonna sia da un cursore dict sia da uno a tuple.

    Il funnel pubblico apre `get_connection()`, che consegna cursori a tupla, e
    P26 ha gia' dovuto aprire un secondo cursore dict per la stessa ragione
    (`_public_stima_system_context`). Accettare entrambe le forme qui evita di
    imporre al chiamante quale cursore usare per una lettura di due colonne.

    `indice` e' obbligatorio e non ha valore predefinito: su un cursore a
    tupla la posizione E' l'unico modo di nominare la colonna, e un default a
    zero avrebbe restituito l'id dell'agenzia a chi chiedeva il conteggio -
    in silenzio, e solo sul percorso a tuple.

    LA DISCRIMINAZIONE E' SUL TIPO, NON UN `try/except`. La prima versione
    provava la chiave e ricadeva sulla posizione a qualunque errore: su una
    riga dict a cui MANCASSE la colonna - cioe' una query che non ha restituito
    quel che si crede - avrebbe letto `riga[1]`, che su un dict e' un'altra
    chiave o un `KeyError` travestito. Un dict senza la colonna attesa e' un
    disallineamento fra questo codice e la query, e deve rompersi subito.
    """
    if isinstance(riga, Mapping):
        return riga[chiave]
    return riga[indice]


class RoutingAmbiguous(RuntimeError):
    """Due agenzie instradabili per lo stesso comune: invariante violata.

    Non si sceglie e non si tira a sorte: si solleva. E' la stessa disciplina
    del resto del progetto - un'ambiguita' che il database doveva impedire e
    non ha impedito non si risolve indovinando.
    """
