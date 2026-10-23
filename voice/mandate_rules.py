"""STIMA VOICE V9 - le regole degli incarichi a voce. Pure: nessun database.

Ricevono la SITUAZIONE reale dell'immobile (letta dal repository con i
permessi di chi parla) e decidono, per un passo, l'unica operazione che una
conferma puo' sbloccare, i blocchi e le domande. Le regole sono quelle dei
servizi del CRM, anticipate per poterle dire prima di scrivere; l'esecuzione
le ricontrolla e i servizi originali restano l'ultima parola:

  * un incarico nasce SOLO da un'acquisizione arrivata dopo il sopralluogo
    (`acquisitions.enums.MANDATE_FROM_STATUSES`), con tipo e data d'inizio,
    su un immobile commerciale non venduto ne' archiviato, senza un incarico
    gia' esistente (`acquisitions.service.generate_mandate`);
  * prima di quel momento si possono solo preparare i dati sull'acquisizione
    aperta (`patch_acquisition`): nessuno stato inventato, nessun sopralluogo
    dato per fatto;
  * un'acquisizione nasce con il suo appuntamento in Agenda
    (`create_acquisition`), su un immobile con il proprietario collegato e
    senza un'altra acquisizione aperta;
  * un agente lavora sulle proprie acquisizioni; il prezzo richiesto
    dell'immobile lo cambia chi gestisce l'immobile (`property.lifecycle.may_manage`).

Nessuna firma e' dedotta o dichiarata: generare l'incarico e' cio' che fa il
pulsante «Genera incarico» dell'acquisizione, ne' piu' ne' meno.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from acquisitions.enums import MANDATE_FROM_STATUSES, STATUS_LABELS_IT
from core import property_mandate

from . import dates

# --- l'operazione che la conferma sblocca (confermabile, nell'impronta) ------
WILL_GENERATE = "mandate_will_generate"
WILL_UPDATE_ACQUISITION = "mandate_will_update_acquisition"
WILL_SET_PROPERTY_PRICE = "mandate_will_set_property_price"
WILL_SET_ACQUISITION_PRICE = "mandate_will_set_acquisition_price"
WILL_SET_VALUATION = "mandate_will_set_valuation"
WILL_START_ACQUISITION = "mandate_will_start_acquisition"
MODES = (WILL_GENERATE, WILL_UPDATE_ACQUISITION, WILL_SET_PROPERTY_PRICE, WILL_SET_ACQUISITION_PRICE,
         WILL_SET_VALUATION, WILL_START_ACQUISITION)

# --- blocchi (nessuna risposta li scioglie) ----------------------------------
ALREADY_EXISTS = "mandate_already_exists"
PROPERTY_CENSUS = "mandate_property_census"
PROPERTY_CLOSED = "mandate_property_closed"
ACQUISITION_OTHER_AGENT = "mandate_acquisition_other_agent"
ACQUISITION_NOT_READY = "mandate_acquisition_not_ready"
NO_OPEN_ACQUISITION = "mandate_no_open_acquisition"
NOTHING_TO_PRICE = "mandate_nothing_to_price"
PRICE_UNCHANGED = "mandate_price_unchanged"
NOT_MANAGEABLE = "mandate_property_not_manageable"
OWNER_NOT_LINKED = "mandate_owner_not_linked"
ACQUISITION_ALREADY_OPEN = "acquisition_already_open"
ACQUISITION_OWNER_MISSING = "acquisition_owner_missing"
ACQUISITION_OWNER_AMBIGUOUS = "acquisition_owner_ambiguous"
BLOCKS = (ALREADY_EXISTS, PROPERTY_CENSUS, PROPERTY_CLOSED, ACQUISITION_OTHER_AGENT, ACQUISITION_NOT_READY,
          NO_OPEN_ACQUISITION, NOTHING_TO_PRICE, PRICE_UNCHANGED, NOT_MANAGEABLE, OWNER_NOT_LINKED,
          ACQUISITION_ALREADY_OPEN, ACQUISITION_OWNER_MISSING, ACQUISITION_OWNER_AMBIGUOUS)

# --- domande (una risposta le scioglie; mai con una semplice conferma) ------
NO_ACQUISITION = "mandate_no_acquisition"
TYPE_MISSING = "mandate_type_missing"
START_MISSING = "mandate_start_missing"
PRICE_MISSING = "mandate_price_missing"
PRICE_KIND_UNCLEAR = "mandate_price_kind_unclear"
PROPERTY_CHOICE = "mandate_property_choice"
ASKS = (NO_ACQUISITION, TYPE_MISSING, START_MISSING, PRICE_MISSING, PRICE_KIND_UNCLEAR, PROPERTY_CHOICE)

#: Gli intenti degli incarichi che SCRIVONO (sempre con conferma, sempre da soli).
WRITE_INTENTS = frozenset({"mandate_prepare", "mandate_price", "acquisition_start"})
#: Sola lettura: nessuna scrittura, si esegue senza conferma.
READ_INTENTS = frozenset({"mandate_info"})
INTENTS = WRITE_INTENTS | READ_INTENTS

_CHIUSI = ("sold", "archived")


# ---------------------------------------------------------------------------
# Prezzi detti
# ---------------------------------------------------------------------------

_NUMERO = r"\d+(?:[.,]\d+)*"
MIN_PRICE = Decimal("1000")


def price_from_text(text: str | None) -> Decimal | None:
    """«185.000 euro», «185 mila», «185mila €», «1,2 milioni», «175000» ->
    Decimal in euro; None se non e' un prezzo chiaro. Nessuna parola-numero
    («centottantacinquemila»): il riconoscimento vocale scrive le cifre."""
    if not text:
        return None
    t = dates.fold(str(text)).replace("€", " euro ")
    t = re.sub(r"\b(prezzo|richiesto|richiesta|concordato|di|a|ad|da|e|euro|eur|circa|il|la|lo)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    m = re.fullmatch(rf"({_NUMERO})\s*(mila|mille|k|milioni|milione|mln)?", t)
    if not m:
        return None
    numero, scala = m.group(1), m.group(2)
    if scala:
        # «1,2 milioni», «185.5 mila»: una sola cifra decimale o due dopo il separatore
        cifre = (numero.replace(",", ".") if re.fullmatch(r"\d+[.,]\d{1,2}", numero)
                 else numero.replace(".", "").replace(",", ""))
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?", numero):
        cifre = numero.replace(".", "").replace(",", ".")       # 185.000 / 185.000,50
    elif re.fullmatch(r"\d{1,3}(?:,\d{3})+", numero):
        cifre = numero.replace(",", "")                          # 185,000
    elif re.fullmatch(r"\d+(?:,\d{1,2})?", numero):
        cifre = numero.replace(",", ".")
    else:
        return None
    try:
        valore = Decimal(cifre)
    except InvalidOperation:
        return None
    valore *= {None: 1, "mila": 1000, "mille": 1000, "k": 1000, "milioni": 1_000_000, "milione": 1_000_000,
               "mln": 1_000_000}[scala]
    # Un incarico di vendita sotto i mille euro non e' un prezzo detto bene
    # («185» per «185 mila»): meglio chiederlo che scriverlo.
    if valore < MIN_PRICE or valore >= Decimal("1e12"):
        return None
    return valore.quantize(Decimal("0.01"))


def _prezzo(valore) -> Decimal | None:
    if valore is None or valore == "":
        return None
    return Decimal(str(valore)).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# La situazione e la valutazione
# ---------------------------------------------------------------------------

@dataclass
class Evaluation:
    mode: str | None = None
    blocks: list[str] = field(default_factory=list)
    asks: list[str] = field(default_factory=list)
    #: I dati obbligatori che mancano, in parole (per la scheda).
    missing: list[str] = field(default_factory=list)
    #: I valori che l'operazione usera' (prezzo concordato proposto, ecc.).
    values: dict = field(default_factory=dict)


def has_mandate(sit: dict) -> bool:
    return property_mandate.is_real_mandate(sit.get("property"))


def is_closed(prop: dict) -> bool:
    return prop.get("archived_at") is not None or prop.get("commercial_status") in _CHIUSI


def effective_state(sit: dict) -> str:
    """Lo stato vero, in parole: dell'incarico se esiste, se no dell'acquisizione."""
    prop = sit.get("property") or {}
    if has_mandate(sit):
        return {property_mandate.STATE_ACTIVE: "Incarico in corso", property_mandate.STATE_EXPIRED: "Incarico scaduto",
                property_mandate.STATE_SOLD: "Incarico concluso: venduto",
                property_mandate.STATE_WITHDRAWN: "Incarico ritirato",
                property_mandate.STATE_ARCHIVED: "Incarico su immobile archiviato"}[property_mandate.mandate_state(prop)]
    acq = sit.get("acquisition")
    if acq:
        return f"Acquisizione: {STATUS_LABELS_IT.get(acq['status'], acq['status'])} (incarico non ancora generato)"
    return "Nessuna acquisizione e nessun incarico"


def missing_data(sit: dict, payload: dict | None = None) -> list[str]:
    """Cosa manca, nel percorso del CRM, per arrivare all'incarico (o per completarlo)."""
    payload = payload or {}
    prop = sit.get("property") or {}
    if has_mandate(sit):
        mancano = {"mandate_type": "Tipo di incarico", "mandate_start": "Data di inizio"}
        esito = [mancano[c] for c in property_mandate.missing_fields(prop)]
        if _prezzo(prop.get("asking_price")) is None:
            esito.append("Prezzo richiesto")
        return esito
    if prop.get("record_kind") == "census":
        return ["Presa in carico dell'immobile (oggi e' una scheda di censimento)"]
    if not sit.get("owners"):
        return ["Proprietario collegato all'immobile"]
    acq = sit.get("acquisition")
    if acq is None:
        return ["Acquisizione con l'appuntamento in Agenda", "Sopralluogo svolto", "Tipo di incarico",
                "Data di inizio"]
    esito = []
    if acq["status"] not in MANDATE_FROM_STATUSES:
        esito.append("Sopralluogo svolto (si registra completando l'appuntamento di acquisizione)")
    if not (payload.get("mandate_type") or "").strip():
        esito.append("Tipo di incarico")
    if not payload.get("mandate_start"):
        esito.append("Data di inizio")
    if _prezzo(payload.get("agreed_price")) is None and _prezzo(acq.get("asking_price")) is None:
        esito.append("Prezzo")
    return esito


def evaluate(intent: str, payload: dict, sit: dict | None) -> Evaluation:
    """La valutazione di UN passo sull'immobile risolto (`sit` None = immobile
    non ancora individuato: nessuna operazione)."""
    v = Evaluation()
    if sit is None or intent not in INTENTS:
        return v
    prop = sit["property"]
    acq = sit.get("acquisition")
    if intent == "mandate_info":
        v.missing = missing_data(sit, payload)
        return v

    if intent == "mandate_prepare":
        if has_mandate(sit):
            v.blocks.append(ALREADY_EXISTS)
        elif prop.get("record_kind") == "census":
            v.blocks.append(PROPERTY_CENSUS)
        elif is_closed(prop):
            v.blocks.append(PROPERTY_CLOSED)
        elif acq is None:
            v.asks.append(NO_ACQUISITION)
        elif not acq.get("visible"):
            v.blocks.append(ACQUISITION_OTHER_AGENT)
        else:
            detto = _prezzo(payload.get("agreed_price"))
            if acq["status"] in MANDATE_FROM_STATUSES:
                v.mode = WILL_GENERATE
                prezzo = detto if detto is not None else _prezzo(acq.get("asking_price"))
                v.values = {"agreed_price": prezzo, "price_from_acquisition": detto is None and prezzo is not None}
                if not (payload.get("mandate_type") or "").strip():
                    v.asks.append(TYPE_MISSING)
                if not payload.get("mandate_start"):
                    v.asks.append(START_MISSING)
                if prezzo is None:
                    v.asks.append(PRICE_MISSING)
            elif detto is None:
                v.asks += [PRICE_MISSING, ACQUISITION_NOT_READY]
                v.values = {"asking_price": _prezzo(acq.get("asking_price"))}
            elif detto == _prezzo(acq.get("asking_price")):
                v.blocks.append(ACQUISITION_NOT_READY)
            else:
                v.mode = WILL_UPDATE_ACQUISITION
                v.values = {"asking_price": detto, "previous": _prezzo(acq.get("asking_price"))}
        v.missing = missing_data(sit, payload)
        if v.blocks or v.asks:
            v.mode = None
        return v

    if intent == "mandate_price":
        prezzo = _prezzo(payload.get("price"))
        tipo = payload.get("price_kind")
        if tipo is None:
            if has_mandate(sit):
                tipo = "property"
            elif acq is not None:
                v.asks.append(PRICE_KIND_UNCLEAR)
                return v
            else:
                v.blocks.append(NOTHING_TO_PRICE)
                return v
        if tipo == "property":
            if not sit.get("manageable"):
                v.blocks.append(NOT_MANAGEABLE)
            elif prezzo is not None and prezzo == _prezzo(prop.get("asking_price")):
                v.blocks.append(PRICE_UNCHANGED)
            else:
                v.mode = WILL_SET_PROPERTY_PRICE
                v.values = {"price": prezzo, "previous": _prezzo(prop.get("asking_price"))}
        else:
            colonna = "asking_price" if tipo == "acquisition" else "valuation_price"
            if acq is None:
                v.blocks.append(NO_OPEN_ACQUISITION)
            elif not acq.get("visible"):
                v.blocks.append(ACQUISITION_OTHER_AGENT)
            elif prezzo is not None and prezzo == _prezzo(acq.get(colonna)):
                v.blocks.append(PRICE_UNCHANGED)
            else:
                v.mode = WILL_SET_ACQUISITION_PRICE if tipo == "acquisition" else WILL_SET_VALUATION
                v.values = {"price": prezzo, "previous": _prezzo(acq.get(colonna)), "column": colonna}
        if v.blocks:
            v.mode = None
        return v

    # acquisition_start
    if has_mandate(sit):
        v.blocks.append(ALREADY_EXISTS)
    elif prop.get("record_kind") == "census":
        v.blocks.append(PROPERTY_CENSUS)
    elif is_closed(prop):
        v.blocks.append(PROPERTY_CLOSED)
    elif acq is not None:
        v.blocks.append(ACQUISITION_ALREADY_OPEN if acq.get("visible") else ACQUISITION_OTHER_AGENT)
    elif not sit.get("owners"):
        v.blocks.append(ACQUISITION_OWNER_MISSING)
    elif sit.get("owner_choice") is None:
        v.blocks.append(ACQUISITION_OWNER_AMBIGUOUS)
    else:
        v.mode = WILL_START_ACQUISITION
        v.values = {"owner_contact_id": sit["owner_choice"], "asking_price": _prezzo(payload.get("asking_price"))}
    v.missing = missing_data(sit, payload)
    return v
