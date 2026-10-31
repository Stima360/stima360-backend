"""BLOCCO-4C - le regole del suggerimento: parole riconosciute e testi fissi.

Nessun fornitore AI: una domanda si riconosce da parole elencate qui, la
risposta si compone da modelli di testo qui, con i soli valori della scheda
(`fields`). Ogni esito dice PERCHE': cosa e' stato riconosciuto, quali dati
sono stati usati, o il motivo del passaggio all'agente.

Ordine di decisione (il primo che vale):
    1. messaggio non di testo, o STOP           -> nessun suggerimento / agente
    2. trattativa, questioni tecniche o legali  -> agente
    3. richiesta di visita                      -> agente (nessun orario confermato)
    4. nessuna domanda riconosciuta             -> agente (domanda ambigua)
    5. immobile del MESSAGGIO assente o incerto -> chiedere il riferimento
    6. immobile non disponibile per la comunicazione -> agente
    7. dati richiesti: tutti registrati         -> risposta
                       alcuni mancanti          -> risposta parziale + agente
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .fields import ACCESSORY_LABELS, AVAILABLE_STATUS, FIELD_LABELS, euro, sqm

ANSWER = "answer"
HANDOFF = "handoff"
ASK_REFERENCE = "ask_reference"
NONE = "none"

#: Domande sui dati della scheda: argomento -> parole (gia' normalizzate).
TOPICS: dict[str, tuple[str, ...]] = {
    "price": ("prezzo", "costo", "costa", "quanto viene", "quanto chiedete", "richiesta economica"),
    "surface": ("metri", "mq", "metratura", "superficie", "quanto e grande", "grandezza"),
    "rooms": ("locali", "vani", "stanze"),
    "bedrooms": ("camere", "camera da letto", "camere da letto"),
    "bathrooms": ("bagni", "bagno", "servizi igienici"),
    "floor": ("piano", "piani"),
    "elevator": ("ascensore",),
    "energy_class": ("classe energetica", "ape", "certificazione energetica"),
    "accessories": ("balcone", "balconi", "terrazzo", "terrazza", "giardino", "garage", "box", "posto auto",
                    "cantina", "soffitta", "taverna", "piscina", "pertinenze"),
    "availability": ("disponibile", "ancora in vendita", "e ancora libero", "venduto", "ancora disponibile"),
}

#: Domande che una regola non deve mai chiudere da sola: vanno all'agente.
HANDOFF_TOPICS: dict[str, tuple[str, ...]] = {
    "negotiation": ("sconto", "trattabile", "trattare", "trattativa", "offerta", "ribasso", "ultimo prezzo",
                    "prezzo finale", "abbassare", "proposta", "caparra"),
    "technical_legal": ("catasto", "catastale", "conformita", "agibilita", "abuso", "abusi", "condono", "sanatoria",
                        "ipoteca", "rogito", "notaio", "contratto", "compromesso", "planimetria", "documenti",
                        "mutuo", "spese condominiali", "condominio", "imu", "tasse", "successione", "usufrutto",
                        "impianti", "certificato", "permesso", "vincoli", "perizia"),
    # `*` = la parola che inizia cosi' (vederlo, visitarla, visionarlo...)
    "visit": ("visit*", "veder*", "vision*", "appuntament*", "sopralluog*", "quando posso venire",
              "posso passare", "posso venire"),
}

#: Le pertinenze che si possono chiedere per nome: parola -> tipo di
#: `property_accessories`. Una pertinenza non registrata NON e' «assente»
#: (la scheda non la nega): passa all'agente.
ACCESSORY_WORDS = {
    "balcone": "balcone", "balconi": "balcone", "terrazzo": "terrazzo", "terrazza": "terrazzo",
    "giardino": "giardino", "garage": "box", "box": "box", "posto auto": "posto_auto", "cantina": "cantina",
    "soffitta": "soffitta", "taverna": "taverna", "piscina": "piscina",
}

HANDOFF_REASONS = {
    "not_text": "Il messaggio non è di testo: lo legge l'agente.",
    "stop": "Il cliente ha scritto STOP: nessuna risposta proposta.",
    "negotiation": "Domanda di trattativa sul prezzo: la segue l'agente.",
    "technical_legal": "Questione tecnica, documentale o legale: la segue l'agente.",
    "visit": "Richiesta di visita: giorno e orario li concorda l'agente, nessun orario è confermato.",
    "ambiguous": "Domanda non riconosciuta o ambigua: la legge l'agente.",
    "property_unavailable": "Lo stato commerciale dell'immobile non permette di comunicarne i dati: la segue l'agente.",
    "missing_data": "Alcuni dati richiesti non sono registrati nella scheda: li fornisce l'agente.",
}
REFERENCE_REASONS = {
    "no_property": "Il messaggio non è collegato a un immobile.",
    "conflicting_reference": "Il messaggio cita un altro immobile rispetto a quello collegato.",
    "property_not_found": "L'immobile collegato non è più disponibile nella scheda dell'agenzia.",
}

GREETING = "Buongiorno, grazie per il messaggio."
TEXTS = {
    "handoff": "Buongiorno, grazie per il messaggio. La sua richiesta la segue direttamente l'agente, "
               "che la ricontatterà al più presto.",
    "visit": "Buongiorno, grazie per l'interesse. Per la visita la ricontatterà l'agente per concordare "
             "insieme giorno e orario.",
    "ask_reference": "Buongiorno, grazie per il messaggio. Per risponderle con precisione, ci indica il "
                     "riferimento dell'annuncio (codice o link) dell'immobile che le interessa?",
    "missing_tail": "Per {what} la ricontatterà l'agente.",
}

TOPIC_ORDER = ("availability", "price", "surface", "rooms", "bedrooms", "bathrooms", "floor", "elevator",
               "energy_class", "accessories")
TOPIC_NAMES = {
    "price": "il prezzo", "surface": "la superficie", "rooms": "i locali", "bedrooms": "le camere",
    "bathrooms": "i bagni", "floor": "il piano", "elevator": "l'ascensore", "energy_class": "la classe energetica",
    "accessories": "le pertinenze", "availability": "la disponibilità",
}


def normalize(text: str | None) -> str:
    raw = unicodedata.normalize("NFKD", (text or "").lower())
    plain = "".join(c for c in raw if not unicodedata.combining(c))
    return " " + re.sub(r"[^a-z0-9]+", " ", plain).strip() + " "


def _has(norm: str, words) -> bool:
    return any((f" {w[:-1]}" in norm) if w.endswith("*") else (f" {w} " in norm) for w in words)


def topics(text: str | None) -> list[str]:
    norm = normalize(text)
    return [t for t in TOPIC_ORDER if _has(norm, TOPICS[t])]


def handoff_topic(text: str | None) -> str | None:
    norm = normalize(text)
    for name in ("negotiation", "technical_legal", "visit"):
        if _has(norm, HANDOFF_TOPICS[name]):
            return name
    return None


@dataclass
class Fact:
    field: str
    label: str
    value: str


@dataclass
class Suggestion:
    outcome: str
    text: str | None
    reason_code: str | None = None
    reason: str | None = None
    topics: list[str] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def _facts_for(topic: str, p: dict, accessories: list[dict], norm: str = "") -> tuple[list[Fact], str | None]:
    """(dati, frase) per un argomento; frase None se il dato non e' registrato."""
    if topic == "price" and p.get("asking_price") is not None:
        v = euro(p["asking_price"])
        return [Fact("asking_price", FIELD_LABELS["asking_price"], v)], f"il prezzo richiesto è {v}"
    if topic == "surface":
        facts = [Fact(k, FIELD_LABELS[k], sqm(p[k])) for k in ("commercial_surface_sqm", "surface_sqm")
                 if p.get(k) is not None]
        if facts:
            return facts, " e ".join(f"la {f.label.lower()} è di {f.value}" for f in facts)
    if topic in ("rooms", "bedrooms", "bathrooms") and p.get(topic) is not None:
        n = int(p[topic])
        words = {"rooms": ("locale", "locali"), "bedrooms": ("camera", "camere"), "bathrooms": ("bagno", "bagni")}[topic]
        return [Fact(topic, FIELD_LABELS[topic], str(n))], f"ha {n} {words[0] if n == 1 else words[1]}"
    if topic == "floor" and (p.get("floor") or "").strip():
        floor = p["floor"].strip()
        total = f" su {int(p['total_floors'])}" if p.get("total_floors") else ""
        return [Fact("floor", FIELD_LABELS["floor"], f"{floor}{total}")], f"è al piano {floor}{total}"
    if topic == "elevator" and p.get("elevator") is not None:
        value = "Sì" if p["elevator"] else "No"
        return [Fact("elevator", FIELD_LABELS["elevator"], value)], ("c'è l'ascensore" if p["elevator"]
                                                                       else "non c'è l'ascensore")
    if topic == "energy_class" and (p.get("energy_class") or "").strip():
        v = p["energy_class"].strip()
        return [Fact("energy_class", FIELD_LABELS["energy_class"], v)], f"la classe energetica è {v}"
    if topic == "accessories":
        asked = {kind for word, kind in ACCESSORY_WORDS.items() if _has(norm, (word,))} if norm else set()
        registered = {a["kind"]: a.get("quantity") for a in accessories if a["kind"] in ACCESSORY_LABELS}
        kinds = sorted(asked) if asked else sorted(registered)
        if not kinds or any(k not in registered for k in kinds):     # chiesta e non registrata: non si nega
            return [], None
        names = [f"{ACCESSORY_LABELS[k].lower()}{f' ({registered[k]})' if (registered[k] or 0) > 1 else ''}"
                 for k in kinds]
        listed = ", ".join(names)
        return [Fact("accessories", FIELD_LABELS["accessories"], listed)], f"tra le pertinenze risultano: {listed}"
    if topic == "availability" and p.get("commercial_status") == AVAILABLE_STATUS:
        return ([Fact("commercial_status", FIELD_LABELS["commercial_status"], "Disponibile")],
                "l'immobile risulta disponibile")
    return [], None


def decide(message: dict, prop: dict | None, accessories: list[dict], *, reference_problem: str | None) -> Suggestion:
    """Il suggerimento per UN messaggio. `prop` e' l'immobile collegato a QUEL
    messaggio (mai l'ultimo della conversazione), gia' letto nell'agenzia."""
    if message.get("message_type") != "text":
        return Suggestion(HANDOFF, TEXTS["handoff"], "not_text", HANDOFF_REASONS["not_text"])
    text = message.get("text_body")
    if normalize(text).strip() == "stop":
        return Suggestion(NONE, None, "stop", HANDOFF_REASONS["stop"])
    asked = topics(text)
    special = handoff_topic(text)
    if special in ("negotiation", "technical_legal"):
        return Suggestion(HANDOFF, TEXTS["handoff"], special, HANDOFF_REASONS[special], topics=asked)
    if special == "visit":
        return Suggestion(HANDOFF, TEXTS["visit"], "visit", HANDOFF_REASONS["visit"], topics=asked)
    if not asked:
        return Suggestion(HANDOFF, TEXTS["handoff"], "ambiguous", HANDOFF_REASONS["ambiguous"])
    if reference_problem is not None or prop is None:
        code = reference_problem or "no_property"
        return Suggestion(ASK_REFERENCE, TEXTS["ask_reference"], code, REFERENCE_REASONS[code], topics=asked)
    if prop.get("commercial_status") != AVAILABLE_STATUS:
        return Suggestion(HANDOFF, TEXTS["handoff"], "property_unavailable", HANDOFF_REASONS["property_unavailable"],
                          topics=asked)

    facts, sentences, missing = [], [], []
    for topic in asked:
        f, sentence = _facts_for(topic, prop, accessories, normalize(text))
        if sentence is None:
            missing.append(topic)
        else:
            facts.extend(f)
            sentences.append(sentence)
    if not sentences:
        return Suggestion(HANDOFF, TEXTS["handoff"], "missing_data", HANDOFF_REASONS["missing_data"], topics=asked,
                          missing=missing)
    ref = prop.get("code") or prop.get("title") or f"n. {prop['id']}"
    body = f"{GREETING} Per l'immobile {ref}: " + "; ".join(sentences) + "."
    if missing:
        what = ", ".join(TOPIC_NAMES[t] for t in missing)
        body += " " + TEXTS["missing_tail"].format(what=what)
        return Suggestion(HANDOFF, body, "missing_data", HANDOFF_REASONS["missing_data"], topics=asked, facts=facts,
                          missing=missing)
    return Suggestion(ANSWER, body, None, None, topics=asked, facts=facts)


#: Riferimenti a un immobile scritti nel testo: codici come «IMM-4», «RIF 123».
CODE_PATTERN = re.compile(r"\b([A-Za-z]{2,6}[-_ ]?\d{1,8})\b")


def cited_codes(text: str | None) -> list[str]:
    return sorted({re.sub(r"[_ ]", "-", c.upper()) for c in CODE_PATTERN.findall(text or "")})


def _code_key(code: str | None) -> str:
    return re.sub(r"[_ ]", "-", (code or "").upper())


def conflicting_reference(text: str | None, linked_code: str | None, cited_agency_codes: list[str]) -> bool:
    """True se il testo cita un immobile diverso da quello collegato: un codice
    di un altro immobile dell'agenzia, o un codice con lo stesso formato di
    quello collegato (stesso prefisso) ma diverso, anche se non esiste."""
    linked = _code_key(linked_code)
    if any(_code_key(c) != linked for c in cited_agency_codes):
        return True
    prefix = re.match(r"[A-Z]+", linked)
    if not prefix:
        return False
    return any(c.startswith(prefix.group(0)) and c != linked for c in cited_codes(text))
