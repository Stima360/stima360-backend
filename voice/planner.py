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
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

from pydantic import ValidationError

from appointments.enums import APPOINTMENT_TYPE_LABELS_IT, MANDATE_REQUIRED_TYPES
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
#: V8-1: «appuntamento con Mario, acquirente» senza dire per cosa: si
#: chiede il motivo, non si inventa una visita.
APPOINTMENT_PURPOSE_MISSING = "appointment_purpose_missing"
#: V8-2: la visita immobile (`buyer_visit`) nasce solo su un incarico, con
#: il cliente acquirente (le stesse regole dell'Agenda manuale: service e
#: trigger 107). Qui si CHIEDE a monte quel che manca; il service resta la
#: guardia autorevole all'esecuzione.
VISIT_MANDATE_REQUIRED = "visit_mandate_required"
VISIT_CLIENT_REQUIRED = "visit_client_required"
#: V8-0: il cliente indicato per la visita e' il PROPRIETARIO dell'immobile
#: visitato («...vedere la casa di Francesco Ruvolo» con Francesco come
#: cliente): non si salva, si chiede chi visita.
VISIT_CLIENT_IS_OWNER = "visit_client_is_owner"

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
    # V8-1: i sinonimi di un agente immobiliare. «vedere casa», «mostrare
    # l'immobile», «far vedere» = visita immobile; «atto notarile», «stipula»
    # = rogito; «presentazione della stima» = presentazione valutazione.
    ("veder", "buyer_visit"), ("mostr", "buyer_visit"), ("visionar", "buyer_visit"),
    ("atto notarile", "notary"), ("stipul", "notary"), ("stima", "valuation_presentation"),
    # V8-0: «appuntamento di vendita» e' la visita di un cliente su un incarico
    # (`sale_appointment` non si crea piu': la visita immobile ne ha preso il ruolo).
    ("appuntamento di vendita", "buyer_visit"),
    # Fase 7: l'appuntamento di acquisizione e' un `seller_meeting`, lo stesso
    # tipo che il modulo Acquisizioni usa per il suo appuntamento in Agenda
    # (acquisitions/enums.py::APPOINTMENT_TYPE). Crea solo l'appuntamento: non
    # apre un'Acquisizione.
    ("acquisizion", "seller_meeting"), ("proprietari", "seller_meeting"),
)
#: Fase 7: `seller_meeting` SOLO se il comando parla davvero di acquisizione o
#: di proprietario. Anche se il modello lo propone, senza queste parole nel
#: testo dell'agente l'appuntamento resta del tipo dedotto (di norma `other`).
SELLER_MEETING_EVIDENCE = ("acquisizion", "proprietari", "venditor", "venditric", "padron")
#: V8-1: `buyer_visit` SOLO se l'agente parla di visitare, vedere o mostrare
#: un immobile. «Appuntamento con Mario, acquirente» non e' una visita.
BUYER_VISIT_EVIDENCE = ("visit", "veder", "mostr", "visionar", "appuntamento di vendita")
#: V8-0: il tipo che la voce non crea mai, e quello che lo sostituisce.
HIDDEN_TYPE_REPLACEMENTS = {"sale_appointment": "buyer_visit"}
#: V8-1: le parole con cui l'agente dice il RUOLO della persona -> ruolo
#: del CRM (core.enums.CONTACT_ROLES). Deterministiche: un test le fissa.
ROLE_KEYWORDS = (
    ("venditor", "seller"), ("venditric", "seller"), ("proprietari", "owner"), ("padron", "owner"),
    ("acquirent", "buyer"), ("comprator", "buyer"), ("compratric", "buyer"),
    # collaudo 21/10: «Giorgio Censori compra ...» (non «compravendita»: lo spazio conta)
    (" compra ", "buyer"), (" acquista ", "buyer"),
    ("cerca casa", "buyer"), ("cercano casa", "buyer"),
)
SELLER_ROLES = ("owner", "seller")
BUYER_ROLES = ("buyer",)
DEFAULT_APPOINTMENT_TYPE = "other"
DEFAULT_APPOINTMENT_MINUTES = 60
#: Un task con la data ma senza l'ora scade alle 9 del mattino (decisione
#: D8 della roadmap, da confermare).
DEFAULT_TASK_TIME = time(9, 0)

VOICE_SOURCE = "voice"
#: V8-4: le etichette dell'interfaccia, quelle dell'Agenda (non copiate).
APPOINTMENT_TYPE_LABELS = APPOINTMENT_TYPE_LABELS_IT


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
    return Ref(kind, r.step, {k: v for k, v in r.model_dump(exclude_none=True).items() if k != "step"})


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


#: V8-0: «la casa di Francesco Ruvolo», «l'appartamento del signor Rossi»: il
#: nome dopo l'immobile e' il PROPRIETARIO. Le parole dopo «di» che non sono
#: un nome (via, mare...) si scartano. Deterministico; un test lo fissa.
_IMMOBILE_DI = re.compile(
    r"\b(?:casa|appartamento|immobile|villa|villetta|attico|mansarda|bilocale|trilocale|quadrilocale|"
    r"monolocale|abitazione|proprieta|negozio|ufficio|box|garage|terreno|rustico|casale)\s+"
    r"(?:di|del|della|dello|dei|degli)\s+(?:(?:signora|signor|signore|sig)\s+)?"
    r"([a-z][a-z]+(?:\s+[a-z][a-z]+)?)")
_NON_NOMI = frozenset({"via", "viale", "piazza", "corso", "largo", "vicolo", "mare", "campagna", "montagna", "lusso",
                       "pregio", "proprieta", "nostro", "nostra", "vostro", "vostra", "suo", "sua", "loro", "cliente",
                       "fronte", "nuova", "nuovo", "vendita", "affitto", "questo", "questa", "quello", "quella"})
_FINE_NOME = frozenset({"in", "a", "al", "alla", "per", "con", "che", "e", "domani", "oggi", "lunedi", "martedi",
                        "mercoledi", "giovedi", "venerdi", "sabato", "domenica", "alle", "il", "la", "nel", "sul"})


#: Collaudo 21/10: «incarico Ruvolo Francesco», «l'incarico di Rossi»: il nome
#: dopo «incarico» e' del proprietario (l'ordine nome/cognome non e' certo).
_INCARICO_DI = re.compile(r"\bincaric[oa]\s+(?:(?:di|del|della)\s+)?(?:(?:signora|signor|signore|sig)\s+)?"
                          r"([a-z][a-z]+(?:\s+[a-z][a-z]+)?)")
_NON_NOMI_INCARICO = frozenset({"di", "in", "esclusiva", "esclusivo", "vendita", "scaduto", "attivo", "nuovo",
                                "firmato", "per", "su", "a", "con", "che", "e"})


def owner_from(text_folded: str) -> dict[str, str] | None:
    """V8-0: il proprietario nominato con «la casa di X Y» o «incarico X Y», o None."""
    m = _IMMOBILE_DI.search(text_folded)
    if not m:
        m = _INCARICO_DI.search(text_folded)
        if not m or m.group(1).split()[0] in _NON_NOMI_INCARICO:
            return None
    parole = [p for p in m.group(1).split()]
    nome = []
    for p in parole:
        if p in _FINE_NOME:
            break
        nome.append(p)
    if not nome or nome[0] in _NON_NOMI:
        return None
    if len(nome) == 1:
        return {"last_name": nome[0].capitalize()}
    return {"first_name": nome[0].capitalize(), "last_name": nome[1].capitalize()}


def _stessa_persona(a: dict, b: dict) -> bool:
    """Due descrizioni di persona con lo stesso cognome e, se entrambe lo
    dicono, lo stesso nome (senza accenti ne' maiuscole)."""
    ca, cb = dates.fold(a.get("last_name") or ""), dates.fold(b.get("last_name") or "")
    na, nb = dates.fold(a.get("first_name") or ""), dates.fold(b.get("first_name") or "")
    if ca and cb:
        return ca == cb and (not (na and nb) or na == nb)
    # uno dei due e' detto solo per nome: stesso nome = stessa persona (si chiede)
    return bool(na and nb and na == nb)


def role_from(text_folded: str) -> str | None:
    """V8-1: il ruolo detto dall'agente, o None."""
    for parola, ruolo in ROLE_KEYWORDS:
        if parola in text_folded:
            return ruolo
    return None


def appointment_type_for(claimed: str | None, quote_folded: str, transcript_folded: str,
                         role: str | None) -> tuple[str, bool]:
    """V8-1: il tipo di appuntamento e se il MOTIVO e' esplicito.

    Il motivo detto (sopralluogo, visita, compromesso, rogito...) prevale
    sempre sul ruolo. Il tipo proposto dal modello vale, tranne due casi che
    hanno conseguenze (acquisizione; visita, che esige un incarico): li
    devono dire le parole dell'agente. Senza motivo: un venditore o
    proprietario = appuntamento proprietario (`seller_meeting`); un
    acquirente = motivo da chiedere; altrimenti il tipo generico."""
    dalle_parole = appointment_type_from(quote_folded)
    tipo = HIDDEN_TYPE_REPLACEMENTS.get(claimed, claimed) or dalle_parole
    if tipo == "seller_meeting" and not any(p in quote_folded or p in transcript_folded
                                            for p in SELLER_MEETING_EVIDENCE):
        tipo = dalle_parole
    if tipo == "buyer_visit" and not any(p in quote_folded or p in transcript_folded
                                         for p in BUYER_VISIT_EVIDENCE):
        tipo = dalle_parole if dalle_parole != "buyer_visit" else DEFAULT_APPOINTMENT_TYPE
    if tipo != DEFAULT_APPOINTMENT_TYPE:
        return tipo, True
    if role in SELLER_ROLES and any(p in quote_folded or p in transcript_folded for p in SELLER_MEETING_EVIDENCE):
        return "seller_meeting", True
    return DEFAULT_APPOINTMENT_TYPE, role not in BUYER_ROLES


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
        quote_f = dates.fold(c.quote)
        # V8-1: il ruolo lo dicono le parole dell'agente; il modello puo'
        # solo confermarlo o proporlo (non basta a fissare un tipo).
        ruolo = role_from(quote_f) or role_from(folded) or (c.contact.role if c.contact is not None else None)
        tipo, motivo_esplicito = appointment_type_for(c.appointment_type, quote_f, folded, ruolo)
        if not motivo_esplicito:
            issues.append(Issue(APPOINTMENT_PURPOSE_MISSING,
                                "Per che cosa e' l'appuntamento con l'acquirente? (visita di un immobile, proposta, telefonata...)",
                                "appointment_type"))
        minuti = c.duration_minutes or DEFAULT_APPOINTMENT_MINUTES
        quando = dates.resolve_when(c.when.date_text, c.when.time_text, now, require_time=True)
        issues += [Issue(r, _messaggio_data(r), "when") for r in quando.reasons]
        s.start_at = quando.start
        s.end_at = quando.start + timedelta(minutes=minuti) if quando.start else None
        s.payload = {"appointment_type": tipo, "duration_minutes": minuti}
        if quando.start is not None:
            s.payload["start_at"] = quando.start
            s.payload["end_at"] = s.end_at
        elif dates.PAST in quando.reasons and quando.day is not None and quando.at is not None:
            # Collaudo 21/10: l'orario DETTO resta (per mostrarlo e chiederne un
            # altro), ma non e' un inizio: senza `start_at` il passo non si esegue.
            sentito = datetime.combine(quando.day, quando.at).replace(tzinfo=dates.ROMA)
            s.payload["heard_start_at"] = sentito
            s.payload["heard_end_at"] = sentito + timedelta(minutes=minuti)
        for nome in ("location_text", "notes"):
            if getattr(c, nome):
                s.payload[nome] = getattr(c, nome)
        # V8-0: «la casa di Francesco Ruvolo»: Francesco e' il PROPRIETARIO.
        # Se il modello non ha indicato l'immobile, lo si cerca per proprietario.
        proprietario = (c.property.owner.model_dump(exclude_none=True)
                        if c.property is not None and c.property.owner is not None else None)
        detto = owner_from(quote_f) or owner_from(folded)
        if proprietario is None and detto is not None and tipo in MANDATE_REQUIRED_TYPES and c.property is None:
            proprietario = detto
            refs["property"] = Ref("property", None, {"owner": detto})
        if tipo in MANDATE_REQUIRED_TYPES and c.contact is not None and c.contact.step is None:
            cliente = c.contact.model_dump(exclude_none=True)
            if any(p and _stessa_persona(cliente, p) for p in (proprietario, detto)):
                issues.append(Issue(VISIT_CLIENT_IS_OWNER,
                                    f"{' '.join(x for x in (cliente.get('first_name'), cliente.get('last_name')) if x)} "
                                    "e' il proprietario dell'immobile: chi e' il cliente che lo visita?", "contact"))
        if tipo in MANDATE_REQUIRED_TYPES:
            if c.contact is None:
                issues.append(Issue(VISIT_CLIENT_REQUIRED, "Con quale cliente e' la visita?", "contact"))
            if c.property is None and "property" not in refs:
                chi = " ".join(x for x in ((c.contact.first_name if c.contact else None),
                                           (c.contact.last_name if c.contact else None)) if x) or "il cliente"
                issues.append(Issue(VISIT_MANDATE_REQUIRED, f"Quale immobile deve visitare {chi}?", "property"))
        if c.contact is not None:
            rif = _ref("contact", c.contact)
            if ruolo and rif is not None and rif.step is None:
                rif = Ref(rif.kind, rif.step, {**rif.description, "role": ruolo})
            refs["contact"] = rif
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
