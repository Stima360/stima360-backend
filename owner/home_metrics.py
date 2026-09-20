"""LMC-13 - le metriche di acquisizione di "La Mia Casa".

COSA MISURA, E PERCHE' NON MISURA DI PIU'.

Una domanda sola: fra le case entrate in La Mia Casa in un periodo, quante
sono avanzate verso un contatto commerciale. E' un modello a COORTE, non a
eventi: "quanto e' successo negli ultimi 30 giorni" direbbe quanta attivita'
c'e' stata, che e' una domanda diversa. Seguendo le stesse case nel tempo si
sa se il prodotto converte.

L'UNITA' E' LA CASA, NON IL GRANT.

Una stima con proprietario, comproprietario e delegato ha tre righe in
`owner_stima_access` ma e' UNA opportunita' di acquisizione, e come tale
conta una volta sola in ogni conteggio `*_homes`. L'unica metrica che conta
persone e' `activated_owners`, e infatti non si chiama `homes`.

La data di ingresso della casa e' `MIN(owner_stima_access.created_at)` fra i
suoi grant coerenti: il primo momento in cui quella casa e' diventata
visibile a qualcuno. Non `stime.data`, che e' TIMESTAMP *senza* fuso e
dipende dal timezone di sessione di chi ha scritto: usarlo come confine di
coorte sbaglierebbe fino a un giorno.

QUESTO MODULO NON LEGGE IL DATABASE.

Riceve i conteggi gia' aggregati - una riga sola, prodotta da una CTE in
`owner/repository.py` - e li compone. Le percentuali si calcolano qui perche'
la divisione per zero e' una decisione di prodotto, non di SQL.

NULL E ZERO NON SONO LA STESSA COSA, ed e' la distinzione piu' importante di
tutto il DTO. `0` vuol dire "misurato, nessun caso". `null` vuol dire "non
misurabile", e ogni `null` porta con se' la ragione in `not_measurable`, cosi'
chi legge l'API non deve indovinare ne' aprire il codice. Sopralluogo e
incarico sono `null` per mancanza di una fonte autorevole, non per mancanza di
casi, e l'audit LMC-13A lo ha dimostrato: `properties` non ha `stima_id`,
`property_leads` e' a compilazione manuale, `leads.stage='won'` non e' scritto
da nessuna riga di codice applicativo, e `stime_dettagliate.sopralluogo` e' un
campo del form pubblico - una preferenza dichiarata dal visitatore, non un
sopralluogo avvenuto. Nessuno dei due si deduce da una corrispondenza di
email, telefono, nome o indirizzo: quella sarebbe una congettura travestita
da metrica.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

#: L'unita' di analisi, dichiarata nel DTO invece di restare implicita.
UNIT = "stima"

#: Le finestre ammesse. Insieme CHIUSO: un `days` arbitrario aprirebbe la
#: porta a coorti che nessuno ha pensato (un giorno solo, o dieci anni) e a
#: query il cui costo nessuno ha misurato.
ALLOWED_DAYS = (7, 30, 90, 365)
DEFAULT_DAYS = 30

#: I conteggi che la coorte produce davvero. Elenco CHIUSO, e l'ordine e'
#: quello del funnel: chi legge il DTO legge i gradini nell'ordine in cui si
#: percorrono.
COHORT_COUNTS = (
    "cohort_homes",
    "activated_owners",
    "viewed_homes",
    "returning_homes",
    "value_interest_homes",
    "demand_interest_homes",
    "updated_homes",
    "strong_interest_homes",
    "consultation_homes",
)

#: Lo STOCK, che non e' della coorte: quante case hanno oggi un accesso
#: valido. Sta fuori da `COHORT_COUNTS` di proposito - non e' un numeratore e
#: non ha un tasso, perche' dividere uno stock per una coorte non significa
#: niente.
STOCK_COUNTS = ("active_homes_now",)

#: Cio' che non e' misurabile, con la ragione. Le chiavi sono quelle del DTO.
NOT_MEASURABLE = {
    "inspection_homes":
        "nessuna fonte autorevole di sopralluogo collegabile alla stima",
    "mandate_homes":
        "nessuna relazione provata fra stima PRE-incarico e property POST-incarico",
}

#: Da quale conteggio nasce ogni tasso. Il denominatore e' SEMPRE
#: `cohort_homes`: tassi con denominatori diversi non si possono confrontare
#: fra loro, e un funnel serve proprio a confrontarli.
RATES = {
    "view_rate": "viewed_homes",
    "return_rate": "returning_homes",
    "strong_interest_rate": "strong_interest_homes",
    "consultation_rate": "consultation_homes",
}

#: I tassi che restano `null` perche' il loro numeratore non esiste.
UNMEASURABLE_RATES = ("inspection_rate", "mandate_rate")


class InvalidPeriod(ValueError):
    """`days` fuori dall'insieme chiuso."""


def validate_days(days: Any) -> int:
    """`days` e' una scelta fra quattro, non un intero qualunque."""
    try:
        valore = int(days)
    except (TypeError, ValueError):
        raise InvalidPeriod("Periodo non ammesso") from None
    if valore not in ALLOWED_DAYS:
        raise InvalidPeriod("Periodo non ammesso")
    return valore


def window(days: int, *, now: datetime | None = None) -> tuple[datetime, datetime]:
    """`[cohort_from, cohort_to)` in UTC reale.

    Il confine e' semiaperto in alto: un evento esattamente a `cohort_to` non
    appartiene a questa finestra ma alla successiva, e cosi' due periodi
    adiacenti non contano mai la stessa casa due volte.
    """
    fine = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return fine - timedelta(days=days), fine


def _rate(numeratore: Any, denominatore: Any) -> float | None:
    """Il tasso, oppure `null` se non c'e' niente da dividere.

    Con la coorte vuota il tasso NON e' zero: zero significherebbe "nessuna
    di quelle case ha convertito", e non ci sono case. E' `null`, cioe' non
    misurabile in quel periodo.
    """
    if not denominatore:
        return None
    return round(int(numeratore) / int(denominatore), 4)


def _iso(quando: datetime) -> str:
    """UTC con la `Z`, non `+00:00`: e' la forma che il resto di OWNER espone."""
    return quando.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build(counts: dict[str, Any], *, days: int,
          cohort_from: datetime, cohort_to: datetime) -> dict[str, Any]:
    """Il DTO, da una riga di conteggi.

    Ogni chiave di `COHORT_COUNTS` e `STOCK_COUNTS` viene letta per nome e
    convertita a intero: cio' che la query non ha prodotto vale 0, mai
    `None`, perche' un conteggio assente da un aggregato SQL significa
    "nessuna riga", non "non misurabile". Niente di cio' che non e' in quegli
    elenchi entra nel DTO, quindi una colonna aggiunta per sbaglio alla query
    - un id, una data, un indirizzo - non puo' uscire di qui.
    """
    numeri = {nome: int(counts.get(nome) or 0)
              for nome in (*COHORT_COUNTS, *STOCK_COUNTS)}
    coorte = numeri["cohort_homes"]
    tassi = {nome: _rate(numeri[fonte], coorte) for nome, fonte in RATES.items()}
    tassi.update({nome: None for nome in UNMEASURABLE_RATES})
    return {
        "period_days": days,
        "cohort_from": _iso(cohort_from),
        "cohort_to": _iso(cohort_to),
        "unit": UNIT,
        **numeri,
        **{nome: None for nome in NOT_MEASURABLE},
        "rates": tassi,
        "not_measurable": dict(NOT_MEASURABLE),
    }
