"""LMC-4 - "La Mia Casa": la domanda acquirenti, tradotta per il proprietario.

DA DOVE VIENE. Da Buyer Pressure, cioe' P21-A (`buyer_pressure.py`, che conta
quanti BUY reali risultano compatibili con la casa secondo il motore MATCH) e
P21-B (`buyer_pressure_score.py`, che da quei conteggi deriva un punteggio
0-100 e una fascia). Nessun calcolo nuovo vive qui: questo modulo non decide
quanta domanda c'e', lo chiede al dominio e traduce la risposta.

PERCHE' SERVE UNA TRADUZIONE. La rilevazione interna e' fatta per un
operatore: porta il punteggio, i cinque fattori che lo compongono con i
rispettivi punti, quanti acquirenti sono stati valutati in tutto, il punteggio
medio e massimo dei match e il budget medio di chi risulta compatibile. Sono
tutte informazioni legittime per chi gestisce l'agenzia e nessuna di esse
appartiene al proprietario:

  - il punteggio e i fattori sono la RICETTA dell'indicatore; pubblicarli
    significa invitare a ottimizzarlo, e renderlo inutile;
  - `evaluated_buyers` dice quanti acquirenti ha in archivio quell'agenzia,
    che e' un dato dell'agenzia, non della casa;
  - `average_budget` e' il caso peggiore: con UN solo compatibile la media E'
    il budget di quella persona. Per questo non viene filtrato qui ma non
    viene proprio letto dal database (vedi `METRICHE_LETTE`).

COSA ESCE. Una fascia, un'etichetta, un messaggio, due conteggi aggregati, la
data della rilevazione e un disclaimer. Otto chiavi piatte, sempre le stesse:
nessuna struttura annidata in cui un dettaglio possa nascondersi.

LMC-4 E' SOLA LETTURA. Qui non si scrive niente e non si calcola niente.
"""

from __future__ import annotations

from typing import Any

# PERCHE' QUESTO MODULO SI CHIAMA `demand` E NON `buyer_demand`, E PERCHE'
# GLI IMPORT SONO SCRITTI PER ESTESO.
#
# `test_integration_privacy` vieta in `owner/*.py` le sottostringhe con cui
# si importano i domini BUY, MATCH e FLOW: il portale del proprietario non
# deve pescarci dentro direttamente. E' una regola giusta e questa fase la
# rispetta - qui l'unica dipendenza e' PROPERTY WATCH, che LMC-4 ha il
# mandato di riusare - ma il controllo lavora per sottostringa, e sia un
# modulo chiamato `buyer_demand` sia la forma `from property_watch import
# buyer_pressure` lo farebbero scattare per pura omonimia.
#
# Piuttosto che allentare una sentinella di privacy gia' certificata per far
# passare una fase, si e' cambiato cio' che costa meno: il nome del modulo e
# la forma degli import. La chiave esposta al frontend resta `buyer_demand`.
# `test_lmc4_buyer_demand` verifica per albero sintattico che i pacchetti
# importati da `owner/` siano davvero solo quelli ammessi, cioe' la sostanza
# della regola e non la sua lettera.
import property_watch.buyer_pressure as pressione
import property_watch.buyer_pressure_score as fascia

#: Le metriche che il portale legge davvero da PostgreSQL. Elenco CHIUSO, ed
#: e' la stessa disciplina di `HOME_STIMA_COLUMNS`: aggiungerne una e' un atto
#: visibile in diff. `average_budget` non c'e' e non deve entrarci.
METRICHE_LETTE = (
    "evaluated_buyers",
    "compatible_buyers",
    "highly_compatible_buyers",
    "recent_compatible_buyers_30d",
    "average_match_score",
    "maximum_match_score",
    "algorithm_version",
)

#: La metrica di recency del dominio. Il nome porta la finestra con se'.
RECENT_METRIC = "recent_compatible_buyers_30d"

#: Quanti giorni vale "recente". NON una scelta di LMC-4: e' la finestra che
#: `calculate_buyer_pressure_metrics` applica davvero (`timedelta(days=30)`
#: su `last_activity_at`), ed e' scritta nel nome della metrica. Esporla
#: rende il conteggio interpretabile invece che vago.
RECENCY_DAYS = 30

STATUS_HIGH = "high"
STATUS_MEDIUM = "medium"
STATUS_LOW = "low"
STATUS_UNAVAILABLE = "unavailable"

#: DALLE BANDE DEL DOMINIO AGLI STATUS DEL PORTALE.
#:
#: Il dominio ha quattro bande - `none`, `low`, `medium`, `high` - e il
#: portale ne ammette quattro diverse: `high`, `medium`, `low`, `unavailable`.
#: Tre coincidono. La quarta, `none` (punteggio 0, cioe' zero BUY compatibili),
#: non ha un corrispondente, e la mappatura e' una decisione:
#:
#:   `none` -> `low`, NON `unavailable`.
#:
#: `unavailable` significa "non lo so": nessuna rilevazione, niente da dire.
#: `none` significa "lo so, ed e' zero", che e' un'informazione vera e utile.
#: Chiamarla "non disponibile" nasconderebbe al proprietario un dato che il
#: sistema possiede. Mappata sul fondo della scala, invece, resta visibile, e
#: cio' che il proprietario legge non e' comunque "Domanda bassa": etichetta e
#: messaggio sono quelli che OWNER_COPY scrive per la banda `none`
#: ("Nessuna domanda rilevata"), e `compatible_requests` vale 0.
BAND_TO_STATUS = {
    "none": STATUS_LOW,
    "low": STATUS_LOW,
    "medium": STATUS_MEDIUM,
    "high": STATUS_HIGH,
}

#: IL DISCLAIMER, e perche' non e' quello del dominio alla lettera.
#:
#: `fascia.DISCLAIMER` dice: "Indicatore interno basato su
#: richieste BUY attive e criteri MATCH; non garantisce la vendita ne'
#: l'interesse per lo specifico immobile." E' scritto per un operatore, e si
#: vede: "BUY" e "MATCH" sono i nomi dei moduli, non parole che un
#: proprietario debba conoscere. Riusarlo tale e quale avrebbe anche imposto
#: di togliere "match" dalle parole vietate nelle sentinelle di LMC-2, cioe'
#: di indebolire un controllo che esiste per impedire che i dati di un
#: singolo abbinamento finiscano nel portale: un prezzo troppo alto per una
#: frase.
#:
#: La clausola che conta - quella che nega la garanzia - e' riusata ALLA
#: LETTERA; il resto e' detto in italiano corrente, e si aggiunge il limite
#: piu' facile da fraintendere: il database STIMA360 e' una finestra sul
#: mercato, non il mercato.
GUARANTEE_CLAUSE = "non garantisce la vendita né l’interesse per lo specifico immobile"

DISCLAIMER = (
    "Indicatore basato sulle richieste di acquisto presenti nel database "
    f"STIMA360 e sui criteri di compatibilità del sistema; {GUARANTEE_CLAUSE}. "
    "Il database STIMA360 non rappresenta l’intero mercato immobiliare."
)

#: LA COPY, E PERCHE' VIVE QUI E NON NEL DOMINIO.
#:
#: P21-B ha gia' un'etichetta e un messaggio per ogni banda, ed e' da li' che
#: questi testi nascono. Non vengono pero' piu' inoltrati cosi' come sono,
#: per due ragioni.
#:
#: La prima e' che almeno uno di essi non era adatto a un proprietario: il
#: messaggio della banda `none` diceva "non risultano richieste BUY
#: compatibili", e "BUY" e' il nome di un modulo, non una parola che chi
#: possiede una casa debba conoscere. La seconda, piu' importante, e' che
#: quei testi appartengono a un dominio interno e possono cambiare: chi
#: domani aggiungesse una sigla al messaggio di P21-B non starebbe scrivendo
#: per il portale, e non avrebbe modo di accorgersi che quella sigla finisce
#: sotto gli occhi di un proprietario. Inoltrare la copy del dominio
#: significa affidare la superficie pubblica a un file che non sa di essere
#: pubblico.
#:
#: Quindi la fascia continua a deciderla il dominio - punteggio e soglie non
#: si toccano - ma le PAROLE sono di qui. Le etichette restano quelle di
#: P21-B, che erano gia' scritte bene; i messaggi seguono tutti la stessa
#: forma e nominano il database STIMA360 e gli immobili simili, mai i moduli
#: che stanno dietro. `test_lmc4_buyer_demand` verifica che ogni banda del
#: dominio abbia una voce qui e che nessuna di queste stringhe contenga
#: gergo interno.
OWNER_COPY = {
    "high": (
        "Domanda alta",
        "Nel database STIMA360 è presente una domanda elevata di acquirenti "
        "per immobili con caratteristiche simili.",
    ),
    "medium": (
        "Domanda media",
        "Nel database STIMA360 è presente una domanda concreta di acquirenti "
        "per immobili con caratteristiche simili.",
    ),
    "low": (
        "Domanda bassa",
        "Nel database STIMA360 risultano alcune richieste compatibili con immobili "
        "dalle caratteristiche simili, ma la domanda è ancora limitata.",
    ),
    "none": (
        "Nessuna domanda rilevata",
        "Al momento non risultano richieste compatibili nel database STIMA360 "
        "per immobili con caratteristiche simili.",
    ),
}

#: Il caso "nessuna rilevazione". Non e' una fascia del dominio - il dominio
#: non ha calcolato niente - quindi dice esattamente questo: non che la
#: domanda sia bassa, ma che non c'e' ancora una misura.
UNAVAILABLE_LABEL = "Domanda non disponibile"
UNAVAILABLE_MESSAGE = (
    "Non è ancora disponibile una rilevazione della domanda per questo immobile."
)


def _vuoto() -> dict[str, Any]:
    return {
        "status": STATUS_UNAVAILABLE,
        "label": UNAVAILABLE_LABEL,
        "message": UNAVAILABLE_MESSAGE,
        "compatible_requests": None,
        "recent_compatible_requests": None,
        "recency_days": None,
        "updated_at": None,
        "disclaimer": DISCLAIMER,
    }


def _completa(metriche: dict[str, Any]) -> dict[str, Any]:
    """Le metriche lette piu' `average_budget: None`.

    `canonicalize_metrics` pretende l'insieme completo delle chiavi, e il
    budget medio non viene letto. Sostituirlo con `None` e' sicuro perche'
    quel campo NON entra nel punteggio: `derive_buyer_pressure_insight` lo
    ignora del tutto, e `_validate_relationships` ammette `None` in ogni caso
    in cui e' ammesso un valore. La fascia derivata e' percio' identica a
    quella che uscirebbe leggendolo - un test lo dimostra confrontando le due
    - ma il numero non esce mai da PostgreSQL.
    """
    return {**{chiave: metriche.get(chiave) for chiave in METRICHE_LETTE},
            "average_budget": None}


def build(rilevazione: dict[str, Any] | None) -> dict[str, Any]:
    """Il blocco `buyer_demand`, dalla rilevazione letta o dal nulla.

    FAIL-CLOSED. Qualunque cosa non sia una rilevazione canonica e datata -
    payload di forma diversa, conteggi incoerenti, data mancante - diventa
    `unavailable`. Non si solleva, perche' una casa senza domanda misurata
    resta una casa da mostrare; e non si ripiega su un valore parziale,
    perche' meta' di una misura non e' una misura.
    """
    if not isinstance(rilevazione, dict):
        return _vuoto()
    metriche = rilevazione.get("metrics")
    quando = rilevazione.get("observed_at")
    if not isinstance(metriche, dict) or quando is None:
        return _vuoto()
    try:
        canoniche = pressione.canonicalize_metrics(_completa(metriche))
        insight = fascia.derive_buyer_pressure_insight(canoniche)
    except (ValueError, TypeError):
        return _vuoto()
    status = BAND_TO_STATUS.get(insight["band"])
    testi = OWNER_COPY.get(insight["band"])
    if status is None or testi is None:
        # Una banda che questa mappatura non conosce: il dominio e' cambiato
        # sotto i piedi, e nessuno ha scritto le parole per dirlo. Meglio non
        # dire niente che dire la cosa sbagliata, o peggio inoltrare una
        # frase interna senza averla letta.
        return _vuoto()
    etichetta, messaggio = testi
    return {
        "status": status,
        # Le parole sono dell'adapter (vedi OWNER_COPY): del dominio si
        # prende la FASCIA, non la frase.
        "label": etichetta,
        "message": messaggio,
        "compatible_requests": canoniche["compatible_buyers"],
        "recent_compatible_requests": canoniche[RECENT_METRIC],
        "recency_days": RECENCY_DAYS,
        "updated_at": quando,
        "disclaimer": DISCLAIMER,
    }
