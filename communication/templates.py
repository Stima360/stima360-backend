"""P29-3B.2A - il registro dei template, versionato e immutabile. SOLO STRUTTURA.

`template_key` e `template_version` sul ledger erano due colonne senza un
sistema dietro: chi accodava un messaggio costruiva il testo per conto suo.
Questo modulo e' il posto in cui una coppia (chiave, versione) corrisponde a
UN renderer, e a uno solo.

TRE REGOLE

1. Una versione presente qui non si modifica: si aggiunge la successiva.
   Cio' che il ledger dice di aver mandato con `(m1, 1)` deve restare
   ricostruibile, e una sentinella confronta l'impronta di ogni renderer
   con quella fissata nel test.
2. Una versione assente e' un errore, mai un ripiego su un'altra: un
   messaggio mandato "col template piu' vicino" e' un messaggio che nessuno
   ha approvato.
3. Un template di marketing DEVE ricevere `unsubscribe_url`: senza, il
   rendering e' rifiutato prima di produrre una riga di testo. E' la regola di
   P29-3B.0, applicata dove il testo nasce.

IN QUESTO BLOCCO NON C'E' NESSUN TESTO COMMERCIALE. Il registro contiene un
solo template, di prova, che esiste per dimostrare il contratto e non viene
mai spedito da nessun percorso applicativo.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from .exceptions import ValidationError

from .enums import CHANNELS, COMMUNICATION_TYPES, TYPE_MARKETING

#: Il campo che ogni template di marketing deve ricevere. Nominato una volta.
UNSUBSCRIBE_FIELD = "unsubscribe_url"


@dataclass(frozen=True)
class Template:
    key: str
    version: int
    channel: str
    communication_type: str
    required_fields: frozenset[str]
    subject: Callable[[Mapping[str, str]], str] | None
    body: Callable[[Mapping[str, str]], str]

    def __post_init__(self) -> None:
        if self.channel not in CHANNELS:
            raise ValueError(f"template {self.key!r}: unknown channel {self.channel!r}")
        if self.communication_type not in COMMUNICATION_TYPES:
            raise ValueError(f"template {self.key!r}: unknown type {self.communication_type!r}")
        if self.version < 1:
            raise ValueError(f"template {self.key!r}: version must be >= 1")
        if (self.channel == "email") != (self.subject is not None):
            raise ValueError(f"template {self.key!r}: email needs a subject, other channels none")
        if self.communication_type == TYPE_MARKETING and UNSUBSCRIBE_FIELD not in self.required_fields:
            raise ValueError(
                f"template {self.key!r} v{self.version}: a marketing template must require "
                f"{UNSUBSCRIBE_FIELD!r}"
            )


class Rendered(tuple):
    """`(subject, body)`; `subject` e' None fuori dall'email."""


def _prova_soggetto(c: Mapping[str, str]) -> str:
    return f"[prova] {c['agency_name']}"


def _prova_corpo(c: Mapping[str, str]) -> str:
    return (
        f"Questo e' un template di PROVA del registro, per {c['contact_first_name']}.\n"
        f"Non viene spedito da nessun percorso applicativo.\n"
        f"Disiscrizione: {c[UNSUBSCRIBE_FIELD]}\n"
    )


# ---------------------------------------------------------------------------
# P29-3D - LA SEQUENZA DELLA STIMA, v1
#
# COSA QUESTI TESTI NON FANNO, E PERCHE' E' LA PARTE IMPORTANTE
#
# Non dicono "affrettati", non dicono "ho gia' un acquirente", non citano
# percentuali o tempi medi di vendita, non promettono di vendere. Non perche'
# sia proibito scriverlo, ma perche' sarebbe FALSO: il sistema non conosce
# nessuno di quei fatti, e una mail che li afferma fa dire all'agenzia una
# cosa che non puo' sostenere davanti al cliente. Cio' che questi messaggi
# possono dire con verita' e' solo questo: che una stima e' stata fatta, che
# un valore dipende da cose che un algoritmo non vede, e che una persona e'
# disponibile a guardarle.
#
# COSA DICONO, uno per uno:
#
#   M1  ha ricevuto la stima? si e' capita? - e un invito morbido a parlarne
#   M2  perche' il valore vero dipende da microzona, stato, piano, pertinenze
#   M3  la proposta del sopralluogo, ASSISTITA: la manda una persona
#   M4  il rischio del prezzo sbagliato, nei due versi
#   M5  la chiusura: nessuna pressione, la porta resta aperta
#
# LA FIRMA E' L'AGENZIA. Nessun nome di agente viene inventato: se il sistema
# non sa chi segue quel contatto, firmare "Marco" sarebbe una bugia piccola e
# inutile. Firma l'agenzia, che e' chi si assume la comunicazione.
#
# OGNI TESTO E' CORTO. Centoventi-centosessanta parole: una mail che si legge
# sul telefono in trenta secondi. La lunghezza non e' uno stile, e' rispetto.
#
# IMMUTABILI. Una versione qui dentro non si corregge: si aggiunge la
# successiva. Il ledger conserva cio' che e' stato spedito, e deve restare
# ricostruibile parola per parola.
# ---------------------------------------------------------------------------

#: I campi che ogni messaggio della sequenza chiede. `stima_url` porta alla
#: stima gia' fatta, `owner_portal_url` alla propria casa: due luoghi diversi,
#: e ogni messaggio chiede solo quello di cui parla.
BASE_STIMA = frozenset({"contact_first_name", "agency_name", UNSUBSCRIBE_FIELD})


def _chiusura(c: Mapping[str, str]) -> str:
    return (
        f"\n{c['agency_name']}\n"
        f"su Stima360\n\n"
        f"Se non vuoi piu' ricevere questi messaggi: {c[UNSUBSCRIBE_FIELD]}\n"
    )


def _m1_soggetto(c: Mapping[str, str]) -> str:
    return "La tua stima e' arrivata?"


def _m1_corpo(c: Mapping[str, str]) -> str:
    return (
        f"Ciao {c['contact_first_name']},\n\n"
        "ti scrivo solo per sapere se hai ricevuto la stima del tuo immobile "
        "e se ti e' stata chiara.\n\n"
        "E' una valutazione automatica: parte dai dati che ci hai dato e dai "
        "prezzi della zona. Serve per avere un primo ordine di grandezza, non "
        "per sostituire un parere su quel preciso immobile.\n\n"
        f"Puoi rivederla qui: {c['stima_url']}\n\n"
        "Se vuoi capire meglio come e' stata calcolata, o se qualche dato non "
        "torna, rispondi a questa mail: ne parliamo con calma, senza impegno.\n"
        + _chiusura(c)
    )


def _m2_soggetto(c: Mapping[str, str]) -> str:
    return "Cosa cambia il valore, oltre ai metri quadri"


def _m2_corpo(c: Mapping[str, str]) -> str:
    return (
        f"Ciao {c['contact_first_name']},\n\n"
        "una stima automatica lavora sui dati che ha. Su due case identiche "
        "sulla carta, il prezzo reale puo' pero' essere diverso, e dipende da "
        "cose che si vedono solo guardando:\n\n"
        "- la microzona, che cambia anche da una via all'altra\n"
        "- lo stato dell'immobile e dell'edificio\n"
        "- il piano, l'esposizione, l'ascensore\n"
        "- box, cantina, terrazzo e le altre pertinenze\n"
        "- quanta domanda c'e' davvero, adesso, per quel tipo di casa\n\n"
        "Se aggiorni queste informazioni nella scheda della tua casa, la stima "
        f"diventa piu' vicina alla realta': {c['owner_portal_url']}\n"
        + _chiusura(c)
    )


def _m3_soggetto(c: Mapping[str, str]) -> str:
    return "Ti va se la vediamo insieme?"


def _m3_corpo(c: Mapping[str, str]) -> str:
    return (
        f"Ciao {c['contact_first_name']},\n\n"
        "ti propongo una cosa semplice: passo a vedere l'immobile, senza "
        "impegno e senza costi.\n\n"
        "In mezz'ora si capiscono le cose che nessun calcolo puo' sapere - com'e' "
        "tenuto, cosa si vede dalle finestre, cosa conviene sistemare prima e "
        "cosa invece non vale la spesa. Alla fine ti lascio un'idea di prezzo "
        "motivata, che puoi usare come vuoi, anche se decidi di non vendere o "
        "di non farlo con noi.\n\n"
        "Fammi sapere un paio di giorni che ti vanno bene e mi organizzo io.\n"
        + _chiusura(c)
    )


def _m4_soggetto(c: Mapping[str, str]) -> str:
    return "Il prezzo sbagliato costa in due modi"


def _m4_corpo(c: Mapping[str, str]) -> str:
    return (
        f"Ciao {c['contact_first_name']},\n\n"
        "se stai pensando di vendere, la decisione piu' delicata e' il prezzo di "
        "partenza, e si puo' sbagliare in due direzioni.\n\n"
        "Troppo alto: l'immobile resta fermo, chi cerca lo scarta senza nemmeno "
        "visitarlo, e dopo qualche mese si finisce a scendere - spesso sotto il "
        "valore che si sarebbe ottenuto partendo giusti.\n\n"
        "Troppo basso: si vende in fretta, e non si sa mai quanto si e' lasciato "
        "sul tavolo.\n\n"
        "Trovare il punto giusto e' un lavoro che si fa sui dati della zona e "
        "guardando l'immobile. Se vuoi farlo insieme, scrivimi: organizziamo una "
        "consulenza o un sopralluogo, come preferisci.\n"
        + _chiusura(c)
    )


def _m5_soggetto(c: Mapping[str, str]) -> str:
    return "Resto a disposizione"


def _m5_corpo(c: Mapping[str, str]) -> str:
    return (
        f"Ciao {c['contact_first_name']},\n\n"
        "con questo messaggio chiudo: non voglio riempirti la casella.\n\n"
        "La stima e la scheda della tua casa restano dove sono, e puoi "
        f"aggiornarle quando vuoi: {c['owner_portal_url']}\n\n"
        "Se un domani deciderai di vendere, o semplicemente vorrai un parere su "
        "quanto vale oggi, scrivimi: ti rispondo volentieri, anche fra un anno.\n\n"
        "In bocca al lupo per quello che deciderai.\n"
        + _chiusura(c)
    )


#: IL REGISTRO. Chiuso, immutabile, indicizzato per (chiave, versione).
REGISTRY: Mapping[tuple[str, int], Template] = {
    ("registry_probe", 1): Template(
        key="registry_probe", version=1, channel="email",
        communication_type=TYPE_MARKETING,
        required_fields=frozenset({"contact_first_name", "agency_name", UNSUBSCRIBE_FIELD}),
        subject=_prova_soggetto, body=_prova_corpo,
    ),
    ("stima_lead_m1", 1): Template(
        key="stima_lead_m1", version=1, channel="email",
        communication_type=TYPE_MARKETING,
        required_fields=BASE_STIMA | {"stima_url"},
        subject=_m1_soggetto, body=_m1_corpo,
    ),
    ("stima_lead_m2", 1): Template(
        key="stima_lead_m2", version=1, channel="email",
        communication_type=TYPE_MARKETING,
        required_fields=BASE_STIMA | {"owner_portal_url"},
        subject=_m2_soggetto, body=_m2_corpo,
    ),
    ("stima_lead_m3", 1): Template(
        key="stima_lead_m3", version=1, channel="email",
        communication_type=TYPE_MARKETING,
        required_fields=BASE_STIMA,
        subject=_m3_soggetto, body=_m3_corpo,
    ),
    ("stima_lead_m4", 1): Template(
        key="stima_lead_m4", version=1, channel="email",
        communication_type=TYPE_MARKETING,
        required_fields=BASE_STIMA,
        subject=_m4_soggetto, body=_m4_corpo,
    ),
    ("stima_lead_m5", 1): Template(
        key="stima_lead_m5", version=1, channel="email",
        communication_type=TYPE_MARKETING,
        required_fields=BASE_STIMA | {"owner_portal_url"},
        subject=_m5_soggetto, body=_m5_corpo,
    ),
}


def get(key: str, version: int) -> Template:
    """La versione chiesta, o un errore. Nessun fallback."""
    try:
        return REGISTRY[(key, int(version))]
    except (KeyError, TypeError, ValueError):
        raise ValidationError(
            f"template {key!r} version {version!r} is not registered; "
            "a message is never rendered with a template nobody approved"
        ) from None


def render(key: str, version: int, context: Mapping[str, str]) -> Rendered:
    """`(subject, body)` dal template, o un errore se manca un campo richiesto."""
    template = get(key, version)
    mancanti = sorted(f for f in template.required_fields
                      if not (context.get(f) or "").strip())
    if mancanti:
        raise ValidationError(
            f"template {key!r} v{version}: missing required fields {mancanti}"
        )
    soggetto = template.subject(context) if template.subject else None
    return Rendered((soggetto, template.body(context)))


# Tappa 6: bozze v2; approvazione editoriale separata per agenzia.

_V2_M1 = 'Ciao {{contact_first_name}}, la stima online è un buon punto di partenza, ma non racconta tutta la storia di un immobile. Piano, condizioni reali e posizione nella via possono cambiare parecchio il risultato. Se qualcosa non ti torna, rispondi pure a questa email: possiamo rivedere insieme i dati e capire quanto potrebbe valere oggi la tua casa.'
REGISTRY[("stima_lead_m1", 2)] = Template(
    key="stima_lead_m1", version=2, channel="email",
    communication_type=TYPE_MARKETING, required_fields=BASE_STIMA,
    subject=lambda c: 'Hai visto la valutazione della tua casa?',
    body=lambda c, text=_V2_M1: text.replace("{{contact_first_name}}", c["contact_first_name"]) + _chiusura(c),
)

_V2_M2 = 'Ciao {{contact_first_name}}, quando si parla di prezzo al metro quadro si fa una media. Ma un appartamento luminoso, con ascensore e un buon affaccio non è uguale a uno da ristrutturare, anche nella stessa strada. Il punto è capire quali caratteristiche contano davvero per gli acquirenti della tua zona. Se vuoi una valutazione più precisa, contattaci: in STIMA360 possiamo analizzare la tua situazione concreta.'
REGISTRY[("stima_lead_m2", 2)] = Template(
    key="stima_lead_m2", version=2, channel="email",
    communication_type=TYPE_MARKETING, required_fields=BASE_STIMA,
    subject=lambda c: 'Perché due case vicine non valgono sempre uguale',
    body=lambda c, text=_V2_M2: text.replace("{{contact_first_name}}", c["contact_first_name"]) + _chiusura(c),
)

_V2_M3 = 'Ciao {{contact_first_name}}, una casa pubblicata a 200.000 euro non significa necessariamente che venga venduta a quella cifra. I prezzi richiesti sono utili per orientarsi, ma una strategia seria considera domanda, concorrenza e trattative reali. Se stai valutando una vendita, possiamo aiutarti a individuare una fascia di prezzo realistica, senza partire da numeri messi lì a caso. Ti basta rispondere a questa email.'
REGISTRY[("stima_lead_m3", 2)] = Template(
    key="stima_lead_m3", version=2, channel="email",
    communication_type=TYPE_MARKETING, required_fields=BASE_STIMA,
    subject=lambda c: 'Il prezzo degli annunci non è il prezzo di vendita',
    body=lambda c, text=_V2_M3: text.replace("{{contact_first_name}}", c["contact_first_name"]) + _chiusura(c),
)

_V2_M4 = "Ciao {{contact_first_name}}, non sempre rifare una cucina o un bagno aumenta il prezzo abbastanza da recuperare la spesa. Spesso contano di più ordine, luce, piccole riparazioni e una presentazione curata. Prima di investire, vale la pena capire cosa cercano davvero gli acquirenti. Se ci racconti com'è la tua casa, possiamo ragionare insieme sugli interventi utili e su quelli che puoi evitare."
REGISTRY[("stima_lead_m4", 2)] = Template(
    key="stima_lead_m4", version=2, channel="email",
    communication_type=TYPE_MARKETING, required_fields=BASE_STIMA,
    subject=lambda c: 'Prima di vendere, cosa conviene sistemare?',
    body=lambda c, text=_V2_M4: text.replace("{{contact_first_name}}", c["contact_first_name"]) + _chiusura(c),
)

_V2_M5 = 'Ciao {{contact_first_name}}, una vendita può rallentare quando mancano documenti o quando emergono differenze tra stato reale e planimetrie. Verificare per tempo provenienza, dati catastali, situazione urbanistica e attestato energetico permette di affrontare le trattative con più tranquillità. Non serve fare tutto da soli: se stai pensando di mettere in vendita, STIMA360 può aiutarti a organizzare i controlli e i professionisti necessari.'
REGISTRY[("stima_lead_m5", 2)] = Template(
    key="stima_lead_m5", version=2, channel="email",
    communication_type=TYPE_MARKETING, required_fields=BASE_STIMA,
    subject=lambda c: 'I documenti da preparare prima della vendita',
    body=lambda c, text=_V2_M5: text.replace("{{contact_first_name}}", c["contact_first_name"]) + _chiusura(c),
)

_V2_M6 = "Ciao {{contact_first_name}}, non esiste una data perfetta valida per tutti. Conta quanto sei pronto, quali immobili simili sono disponibili e quanta domanda c'è per la tua tipologia di casa. Pubblicare senza un piano può significare dover cambiare strada dopo poche settimane. Se hai in mente di vendere nei prossimi mesi, scrivici: possiamo studiare insieme tempi, prezzo di partenza e percorso di vendita."
REGISTRY[("stima_lead_m6", 2)] = Template(
    key="stima_lead_m6", version=2, channel="email",
    communication_type=TYPE_MARKETING, required_fields=BASE_STIMA,
    subject=lambda c: 'Il momento giusto per pubblicare casa',
    body=lambda c, text=_V2_M6: text.replace("{{contact_first_name}}", c["contact_first_name"]) + _chiusura(c),
)

_V2_M7 = 'Ciao {{contact_first_name}}, le fotografie aiutano a far fermare una persona su un annuncio, ma devono raccontare bene spazi, luce e punti di forza senza creare aspettative sbagliate. Anche la gestione delle visite conta: selezionare gli interessati e preparare le informazioni evita molto tempo perso. Con STIMA360 seguiamo sia la presentazione online sia le persone reali che vengono a vedere casa. Se vuoi sapere come, contattaci.'
REGISTRY[("stima_lead_m7", 2)] = Template(
    key="stima_lead_m7", version=2, channel="email",
    communication_type=TYPE_MARKETING, required_fields=BASE_STIMA,
    subject=lambda c: 'Una buona prima impressione cambia le visite',
    body=lambda c, text=_V2_M7: text.replace("{{contact_first_name}}", c["contact_first_name"]) + _chiusura(c),
)

_V2_M8 = 'Ciao {{contact_first_name}}, spesso il problema non è decidere di vendere: è capire come coordinare vendita, ricerca della prossima casa, proposte, rogito e trasloco. Sono passaggi che sembrano difficili quando li affronti da solo. Noi possiamo costruire con te un piano realistico, con tempi e condizioni da valutare insieme. Raccontaci cosa vorresti cambiare e ti diciamo da dove iniziare.'
REGISTRY[("stima_lead_m8", 2)] = Template(
    key="stima_lead_m8", version=2, channel="email",
    communication_type=TYPE_MARKETING, required_fields=BASE_STIMA,
    subject=lambda c: 'Vuoi cambiare casa senza complicarti la vita?',
    body=lambda c, text=_V2_M8: text.replace("{{contact_first_name}}", c["contact_first_name"]) + _chiusura(c),
)

_V2_M9 = "Ciao {{contact_first_name}}, una buona vendita non dipende solo dal prezzo scritto nell'annuncio. Quando arriva una proposta contano anche tempi, modalità di pagamento, eventuali condizioni e affidabilità dell'acquirente. Essere preparati aiuta a trattare senza fretta e senza rinunciare inutilmente al valore dell'immobile. Se vuoi vendere ma non sai come gestire le trattative, scrivici: è una delle parti del lavoro in cui possiamo davvero esserti utili."
REGISTRY[("stima_lead_m9", 2)] = Template(
    key="stima_lead_m9", version=2, channel="email",
    communication_type=TYPE_MARKETING, required_fields=BASE_STIMA,
    subject=lambda c: "Ricevere un'offerta non significa doverla accettare",
    body=lambda c, text=_V2_M9: text.replace("{{contact_first_name}}", c["contact_first_name"]) + _chiusura(c),
)

_V2_M10 = "Ciao {{contact_first_name}}, una stima automatica è il primo passo. Il successivo è capire come valorizzare e vendere concretamente l'immobile: prezzo, presentazione, promozione, selezione degli acquirenti e gestione di ogni passaggio. Con STIMA360 possiamo preparare un piano di vendita su misura; per chi vende, il nostro modello prevede zero provvigioni di agenzia. Se vuoi parlarne, rispondi a questa email e raccontaci la tua situazione."
REGISTRY[("stima_lead_m10", 2)] = Template(
    key="stima_lead_m10", version=2, channel="email",
    communication_type=TYPE_MARKETING, required_fields=BASE_STIMA,
    subject=lambda c: 'Quanto potrebbe ottenere davvero la tua casa?',
    body=lambda c, text=_V2_M10: text.replace("{{contact_first_name}}", c["contact_first_name"]) + _chiusura(c),
)
