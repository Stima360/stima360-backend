"""STIMA Voice - il pianificatore: dall'output del modello a un piano
deterministico, verificato con gli schemi del CRM.

Cosa fa, e solo questo:
  * trasforma ogni comando in un passo con il payload nella forma che i
    servizi del CRM accettano (`ContactCreate`, `CensusUnitCreate`,
    `BuildingCreate`, `TaskCreate`, `InteractionCreate`): se il CRM rifiuta
    il payload, lo rifiuta qui, con i suoi validatori, prima di qualunque
    esecuzione;
  * interpreta le date con `voice.dates`;
  * decide il tipo di scheda (`record_kind`) SOLO con evidenza nel trascritto
    o con un «Vende» che la richiede; altrimenti lo lascia all'agente;
  * collega i passi fra loro (`depends_on`) e calcola l'impronta del piano.

Cosa non fa: non legge il database, non risolve i riferimenti, non decide il
rischio (voice.policy), non esegue.

I passi portano `issues`: fatti deterministici che la politica trasforma in
domande o blocchi. Un codice stabile ciascuno, cosi' i test li contano.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

from pydantic import ValidationError

from core.schemas import ActivityCreate, ContactCreate, TaskCreate
from property.schemas import BuildingCreate, CensusUnitCreate, InteractionCreate

from . import dates
from .schemas import (
    MAX_COMMANDS, BuildingRef, Command, ContactRef, PropertyRef, VoicePlanOutput,
)

# Codici delle issue (stabili).
UNSUPPORTED = "unsupported_intent"
CONTACT_IDENTITY_WEAK = "contact_identity_weak"          # solo un nome: niente cognome/telefono/email
RECORD_KIND_UNKNOWN = "record_kind_unknown"              # nessuna evidenza commerciale ne' di censimento
RECORD_KIND_UNVERIFIED = "record_kind_unverified"        # il modello lo dice, il trascritto no
RECORD_KIND_CONFLICT = "record_kind_conflict"            # evidenze opposte, o census con «Vende»
BUILDING_LOCATION_MISSING = "building_location_missing"  # ne' indirizzo ne' nome
UNITS_INVENTED = "units_invented"                        # unita' identiche "dedotte" da una palazzina
INVALID_PAYLOAD = "invalid_payload"                      # rifiutato dai validatori del CRM
AGENT_NAMED = "agent_named"                              # appuntamento affidato a un altro agente
TOO_MANY_COMMANDS = "too_many_commands"
TASK_WITHOUT_CONTACT = "task_without_contact"              # TaskCreate esige un contatto, lead o stima

#: Parole che nel trascritto (ripiegato) provano l'intento commerciale o di
#: censimento. Deterministiche: un test le fissa.
COMMERCIAL_KEYWORDS = ("vend", "in vendita", "incaric", "mandat", "acquisi", "affitt", "locazion")
CENSUS_KEYWORDS = ("censi", "schedat", "mappat")

#: Parole del vocale -> tipo di appuntamento, quando il modello non lo indica.
APPOINTMENT_KEYWORDS = (
    ("sopralluogo", "inspection"), ("visita", "buyer_visit"), ("firma", "mandate_signing"),
    ("incarico", "mandate_signing"), ("videochiamata", "video_call"), ("video", "video_call"),
    ("telefonata", "call"), ("chiamata", "call"), ("chiam", "call"), ("rogito", "notary"),
    ("notaio", "notary"), ("proposta", "proposal"), ("preliminare", "preliminary_contract"),
    ("compromesso", "preliminary_contract"), ("valutazione", "valuation_presentation"),
    ("tecnic", "technical"),
)
DEFAULT_APPOINTMENT_TYPE = "other"
DEFAULT_APPOINTMENT_MINUTES = 60
#: Un task con la data ma senza l'ora scade alle 9 del mattino (decisione
#: D8 della roadmap, da confermare).
DEFAULT_TASK_TIME = time(9, 0)

VOICE_SOURCE = "voice"


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    field: str | None = None


@dataclass(frozen=True)
class Ref:
    """Un riferimento da risolvere (Fase 2): `kind` e' contact|property|
    building|agent; `step` quando punta a un passo di questo piano."""
    kind: str
    step: int | None
    description: dict[str, Any]


@dataclass
class Step:
    ordinal: int
    intent: str
    quote: str
    payload: dict[str, Any] = field(default_factory=dict)
    refs: dict[str, Ref] = field(default_factory=dict)
    depends_on: tuple[int, ...] = ()
    issues: tuple[Issue, ...] = ()
    #: Istante dell'appuntamento o scadenza del task (fuso Europe/Rome), se
    #: interpretato.
    start_at: datetime | None = None
    end_at: datetime | None = None

    def has(self, code: str) -> bool:
        return any(i.code == code for i in self.issues)


@dataclass
class Plan:
    transcript: str
    recorded_at: datetime
    steps: list[Step]
    clarifications: tuple[str, ...]
    fingerprint: str

    def step(self, ordinal: int) -> Step:
        return self.steps[ordinal - 1]


# ---------------------------------------------------------------------------

def _ref(kind: str, r: ContactRef | PropertyRef | BuildingRef | None) -> Ref | None:
    if r is None:
        return None
    return Ref(kind, r.step, {k: v for k, v in r.model_dump().items() if k != "step" and v is not None})


#: Id fittizio usato SOLO per far passare i validatori del CRM che esigono
#: un record collegato (TaskCreate e ActivityCreate vogliono un contatto): il
#: contatto vero arriva dal risolutore (Fase 2) e non compare nel payload.
_PLACEHOLDER_ID = 1


def _validate(model, payload: dict[str, Any], placeholders: dict[str, Any] | None = None) -> tuple[dict[str, Any], Issue | None]:
    """Prova il payload con lo schema del CRM. Restituisce il payload
    normalizzato dallo schema oppure l'issue con il motivo del CRM."""
    try:
        valido = model(**payload, **(placeholders or {}))
    except ValidationError as exc:
        primo = exc.errors()[0]
        campo = ".".join(str(p) for p in primo.get("loc", ())) or None
        return payload, Issue(INVALID_PAYLOAD, primo.get("msg", "payload non valido"), campo)
    # Solo i campi forniti, normalizzati dallo schema: i default li applica
    # il CRM all'esecuzione, come per una richiesta HTTP.
    pulito = valido.model_dump(exclude_none=True, exclude_unset=True)
    for chiave in (placeholders or {}):
        pulito.pop(chiave, None)
    return pulito, None


def _keywords(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def _evidenza(testo: str) -> set[str]:
    trovate = set()
    if _keywords(testo, COMMERCIAL_KEYWORDS):
        trovate.add("crm")
    if _keywords(testo, CENSUS_KEYWORDS):
        trovate.add("census")
    return trovate


def decide_record_kind(claimed: str | None, transcript_folded: str, *, seller_requires: bool,
                       quote_folded: str = "") -> tuple[str | None, Issue | None]:
    """La regola: un «Vende» impone `crm`; altrimenti servono parole chiave,
    prima nelle parole del comando (`quote`), poi nel trascritto intero se
    e' univoco; senza evidenza, o con evidenze opposte, decide l'agente."""
    if seller_requires:
        if claimed == "census":
            return None, Issue(RECORD_KIND_CONFLICT, "La scheda e' di censimento ma l'immobile e' in vendita: commerciale o censimento?", "record_kind")
        return "crm", None
    nel_comando, nel_vocale = _evidenza(quote_folded), _evidenza(transcript_folded)
    if len(nel_comando) == 1:
        evidenza = next(iter(nel_comando))
    elif len(nel_vocale) == 1:
        evidenza = next(iter(nel_vocale))
    elif nel_comando or nel_vocale:
        return None, Issue(RECORD_KIND_CONFLICT, "Il vocale parla sia di vendita sia di censimento: che scheda serve?", "record_kind")
    else:
        evidenza = None
    if claimed is None:
        if evidenza is None:
            return None, Issue(RECORD_KIND_UNKNOWN, "Scheda commerciale o di censimento?", "record_kind")
        return evidenza, None
    if evidenza is None:
        return None, Issue(RECORD_KIND_UNVERIFIED, "Non ho trovato nel vocale se e' una scheda commerciale o di censimento", "record_kind")
    if evidenza != claimed:
        return None, Issue(RECORD_KIND_CONFLICT, "Il vocale dice il contrario di quanto interpretato: che scheda serve?", "record_kind")
    return claimed, None


def appointment_type_from(quote_folded: str) -> str:
    for parola, tipo in APPOINTMENT_KEYWORDS:
        if parola in quote_folded:
            return tipo
    return DEFAULT_APPOINTMENT_TYPE


# ---------------------------------------------------------------------------

def build_plan(output: VoicePlanOutput, transcript: str, recorded_at: datetime) -> Plan:
    if recorded_at.tzinfo is None:
        raise ValueError("recorded_at must be timezone-aware")
    folded = dates.fold(transcript)
    seller_targets = {c.property.step for c in output.commands
                      if c.intent == "activate_seller" and c.property.step is not None}

    steps: list[Step] = []
    for ordinal, comando in enumerate(output.commands, start=1):
        steps.append(_step(ordinal, comando, folded, recorded_at, ordinal in seller_targets))
    _unita_inventate(steps)
    if len(output.commands) > MAX_COMMANDS:  # difensivo: lo schema lo vieta gia'
        for s in steps[MAX_COMMANDS:]:
            s.issues += (Issue(TOO_MANY_COMMANDS, "Troppi comandi in un solo vocale"),)
    return Plan(transcript, recorded_at, steps, tuple(output.clarifications), fingerprint(steps))


def _step(ordinal: int, c: Command, folded: str, now: datetime, seller_requires: bool) -> Step:
    s = Step(ordinal=ordinal, intent=c.intent, quote=c.quote)
    issues: list[Issue] = []
    refs: dict[str, Ref] = {}

    if c.intent == "unsupported":
        issues.append(Issue(UNSUPPORTED, f"Non previsto dai comandi vocali: {c.description}"))

    elif c.intent == "create_contact":
        payload = {"contact_type": "company" if c.company_name and not (c.first_name or c.last_name) else "person",
                   "first_name": c.first_name, "last_name": c.last_name, "company_name": c.company_name,
                   "phone": c.phone, "email": c.email, "notes": c.notes, "source": VOICE_SOURCE}
        s.payload, errore = _validate(ContactCreate, {k: v for k, v in payload.items() if v is not None})
        if errore:
            issues.append(errore)
        if not any((c.last_name, c.phone, c.email, c.company_name)):
            issues.append(Issue(CONTACT_IDENTITY_WEAK, "Solo il nome: serve cognome, telefono o email per creare il contatto", "last_name"))

    elif c.intent == "create_unit":
        kind, errore = decide_record_kind(c.record_kind, folded, seller_requires=seller_requires,
                                          quote_folded=dates.fold(c.quote))
        if errore:
            issues.append(errore)
        payload = {"property_type": c.property_type, "address": c.address, "civic_number": c.civic_number,
                   "city": c.city, "floor": c.floor, "internal_number": c.internal_number, "rooms": c.rooms,
                   "bathrooms": c.bathrooms, "surface_sqm": c.surface_sqm, "internal_notes": c.notes}
        if kind is not None:
            payload["record_kind"] = kind
        s.payload, errore = _validate(CensusUnitCreate, {k: v for k, v in payload.items() if v is not None})
        if errore:
            issues.append(errore)
        if kind is None:
            s.payload.pop("record_kind", None)
        if c.building is not None:
            refs["building"] = _ref("building", c.building)
        if c.owner is not None:
            refs["owner"] = _ref("contact", c.owner)

    elif c.intent == "create_building":
        payload = {"building_type": c.building_type, "name": c.name, "address": c.address,
                   "civic_number": c.civic_number, "city": c.city, "units_declared": c.units_declared,
                   "floors_above_ground": c.floors_above_ground, "notes": c.notes}
        # `units_declared_source` resta vuoto: la provenienza del numero
        # (rilievo, catasto, proprietario) non si deduce dal vocale.
        s.payload, errore = _validate(BuildingCreate, {k: v for k, v in payload.items() if v is not None})
        if errore:
            issues.append(errore)
        if not (c.address or c.name):
            issues.append(Issue(BUILDING_LOCATION_MISSING, "Dove si trova la palazzina? Serve l'indirizzo o un nome", "address"))

    elif c.intent == "add_task":
        s.payload, errore = _validate(TaskCreate, {"title": c.title, "description": c.description,
                                                   "priority": c.priority, "task_type": VOICE_SOURCE},
                                      placeholders={"contact_id": _PLACEHOLDER_ID})
        if errore:
            issues.append(errore)
        if c.contact is None:
            # Regola del CRM (core/schemas.py TaskCreate): un task vive su un
            # contatto, un lead o una stima. A voce: su un contatto.
            issues.append(Issue(TASK_WITHOUT_CONTACT, "A quale contatto si riferisce il promemoria?", "contact"))
        if c.due is not None:
            quando = dates.resolve_when(c.due.date_text, c.due.time_text, now, require_time=False,
                                        default_time=DEFAULT_TASK_TIME)
            issues += [Issue(r, _messaggio_data(r), "due") for r in quando.reasons]
            s.start_at = quando.start
            if quando.start is not None:
                s.payload["due_at"] = quando.start
        if c.contact is not None:
            refs["contact"] = _ref("contact", c.contact)

    elif c.intent == "add_note":
        if c.property is not None:
            s.payload, errore = _validate(InteractionCreate, {"interaction_type": c.interaction_type, "note": c.text})
            refs["property"] = _ref("property", c.property)
            if c.contact is not None:
                refs["contact"] = _ref("contact", c.contact)
        else:
            s.payload, errore = _validate(ActivityCreate, {"activity_type": c.interaction_type, "description": c.text},
                                          placeholders={"contact_id": _PLACEHOLDER_ID})
            refs["contact"] = _ref("contact", c.contact)
        if errore:
            issues.append(errore)

    elif c.intent in ("link_owner", "activate_seller"):
        refs["contact"] = _ref("contact", c.contact)
        refs["property"] = _ref("property", c.property)

    elif c.intent == "create_appointment":
        tipo = c.appointment_type or appointment_type_from(dates.fold(c.quote))
        minuti = c.duration_minutes or DEFAULT_APPOINTMENT_MINUTES
        quando = dates.resolve_when(c.when.date_text, c.when.time_text, now, require_time=True)
        issues += [Issue(r, _messaggio_data(r), "when") for r in quando.reasons]
        s.start_at = quando.start
        s.end_at = quando.start + timedelta(minutes=minuti) if quando.start else None
        s.payload = {"appointment_type": tipo, "duration_minutes": minuti}
        if quando.start is not None:
            s.payload["start_at"] = quando.start
            s.payload["end_at"] = s.end_at
        for nome in ("location_text", "notes"):
            if getattr(c, nome):
                s.payload[nome] = getattr(c, nome)
        if c.contact is not None:
            refs["contact"] = _ref("contact", c.contact)
        if c.property is not None:
            refs["property"] = _ref("property", c.property)
        if c.agent_name:
            refs["agent"] = Ref("agent", None, {"name": c.agent_name})
            issues.append(Issue(AGENT_NAMED, f"Appuntamento da affidare a {c.agent_name}: serve il permesso di assegnare", "agent_name"))

    s.refs = {k: v for k, v in refs.items() if v is not None}
    s.depends_on = tuple(sorted({r.step for r in s.refs.values() if r.step is not None}))
    s.issues = tuple(issues)
    return s


def _messaggio_data(code: str) -> str:
    return {
        dates.UNRECOGNIZED_DATE: "Non ho capito il giorno",
        dates.UNRECOGNIZED_TIME: "Non ho capito l'orario",
        dates.AMBIGUOUS_HOUR: "Mattina o pomeriggio?",
        dates.AMBIGUOUS_WEEK: "Quale giorno, esattamente?",
        dates.NONEXISTENT_LOCAL: "Quell'orario non esiste nella notte del cambio d'ora",
        dates.DOUBLE_LOCAL: "Quell'orario cade due volte nella notte del cambio d'ora",
        dates.PAST: "Il momento indicato e' gia' passato",
    }.get(code, code)


def _unita_inventate(steps: list[Step]) -> None:
    """«Palazzina con 12 appartamenti» NON sono 12 schede. Se il modello ha
    comunque prodotto unita' identiche agganciate alla stessa palazzina, sono
    inventate: si bloccano tutte, resta solo la palazzina con le unita'
    dichiarate. Il censimento le aggiungera' una per una, come oggi."""
    per_edificio: dict[int, list[Step]] = {}
    for s in steps:
        if s.intent == "create_unit" and "building" in s.refs and s.refs["building"].step is not None:
            per_edificio.setdefault(s.refs["building"].step, []).append(s)
    for gruppo in per_edificio.values():
        visti: dict[str, int] = {}
        for s in gruppo:
            chiave = json.dumps({k: v for k, v in s.payload.items() if k != "record_kind"}, sort_keys=True, default=str)
            visti[chiave] = visti.get(chiave, 0) + 1
        for s in gruppo:
            chiave = json.dumps({k: v for k, v in s.payload.items() if k != "record_kind"}, sort_keys=True, default=str)
            if visti[chiave] > 1:
                s.issues += (Issue(UNITS_INVENTED, "Unita' dedotte dal numero dichiarato, non dettate: si aggiungono con il censimento"),)


def fingerprint(steps: list[Step]) -> str:
    """L'impronta del piano: cio' che verrebbe eseguito, non le parole."""
    canonico = [{"intent": s.intent, "payload": s.payload,
                 "refs": {k: {"kind": r.kind, "step": r.step, "description": r.description} for k, r in sorted(s.refs.items())},
                 "issues": sorted(i.code for i in s.issues)} for s in steps]
    return hashlib.sha256(json.dumps(canonico, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def execution_order(plan: Plan) -> list[int]:
    """Ordine di esecuzione: le dipendenze prima (lo schema garantisce che
    puntino indietro, quindi l'ordine del vocale e' gia' topologico)."""
    for s in plan.steps:
        for d in s.depends_on:
            if d >= s.ordinal:
                raise ValueError("dependency cycle")
    return [s.ordinal for s in plan.steps]
