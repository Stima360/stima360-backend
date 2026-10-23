"""STIMA Voice Fase 4 - gli adattatori: un passo del piano -> il servizio CRM
che esiste gia'. Nessuna regola di business nuova, nessuna scrittura diretta
alle tabelle del CRM: ogni scrittura passa da un servizio (o, per D-C, dalla
stessa funzione `*_with_cursor` che il servizio usa).

Per ogni intento:
  * il payload del piano diventa lo STESSO modello Pydantic della rotta HTTP
    (stessi validatori, `extra=forbid`);
  * gli id arrivano SOLO dall'esecutore (risolutore, conferma, passi
    precedenti): il modello AI non ne sceglie nessuno;
  * D-D: dove il servizio controlla meno di quanto serve, qui si controlla
    di piu' (mai di meno), prima di scrivere:
      - collegamento proprietario e «Vende»: l'immobile deve essere gestibile
        dall'operatore (`property.lifecycle.may_manage`) e il contatto
        visibile (scope del CRM);
      - appuntamento: mai nel passato; l'agente e' chi parla, salvo un
        agente nominato da chi puo' assegnare (lo decide la politica).

Errori:
  * `StepRefused` - rifiuto SICURO prima di qualunque scrittura;
  * `OutcomeUncertain` - qualcosa e' andato storto DOPO che il servizio ha
    restituito (quindi dopo il suo commit): il passo non si ripete alla
    cieca. E' un `ConnectionError` perche' il registro (098) tratta cosi'
    gli esiti incerti.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from acquisitions import service as acquisitions_service
from acquisitions.schemas import AcquisitionAppointment, AcquisitionCreate, AcquisitionPatch, MandateBody
from appointments import repository as agenda_repository
from appointments import service as agenda
from appointments.schemas import AppointmentCreateBody
from core import database as core_database
from core import repository as core_repository
from core import service as core_service
from core.exceptions import ConflictError
from core.normalization import normalize_email, normalize_phone
from core.schemas import ActivityCreate, ContactCreate, TaskCreate
from core.scope import scoped_source
from crm import sellers
from crm.schemas import SellerActivate
from property import census, composition, interactions
from property import service as property_service
from property.catalog import validate_location
from property.lifecycle import may_manage
from property.schemas import BuildingCreate, CensusUnitCreate, InteractionCreate, PropertyContactCreate, PropertyUpdate

from . import mandate_rules
from . import repository as voice_repository
from . import resolver

# Codici dei rifiuti sicuri (stabili).
CONTACT_DUPLICATE_APPEARED = "VOICE_DUPLICATE_APPEARED"
PROPERTY_NOT_MANAGEABLE = "VOICE_PROPERTY_NOT_MANAGEABLE"
CONTACT_NOT_VISIBLE = "VOICE_CONTACT_NOT_VISIBLE"
APPOINTMENT_IN_PAST = "VOICE_APPOINTMENT_IN_PAST"
RECORD_KIND_MISSING = "VOICE_RECORD_KIND_MISSING"
REFERENCE_MISSING = "VOICE_REFERENCE_MISSING"
UNSUPPORTED = "VOICE_UNSUPPORTED_INTENT"
#: V9: la situazione dell'incarico non e' piu' quella confermata.
MANDATE_STATE_CHANGED = "VOICE_MANDATE_STATE_CHANGED"

OWNER_ROLES = ("owner", "seller")
VOICE_SOURCE = "voice"


class StepRefused(Exception):
    """Rifiuto sicuro: nessuna scrittura e' avvenuta."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


class OutcomeUncertain(ConnectionError):
    """Dopo il commit del servizio: l'effetto c'e', l'esito del passo no."""
    code = "VOICE_OUTCOME_UNCERTAIN"


def error_code(exc: BaseException) -> str:
    """Il codice leggibile di un errore del CRM (o il nome della classe)."""
    codice = getattr(exc, "code", None)
    return str(codice) if codice else type(exc).__name__


@dataclass(frozen=True)
class StepInput:
    command_id: int
    ordinal: int
    intent: str
    payload: dict[str, Any]
    ids: dict[str, int]
    key: str | None = None
    now: datetime | None = None
    confirm_similar: bool = False
    #: create_contact: i doppioni che la conferma ha accettato (D1 compreso).
    accepted_duplicates: frozenset[int] = frozenset()
    accepted_hidden: bool = False
    #: activate_seller: lo stato della coppia PRIMA della prima scrittura.
    seller_before: dict[str, Any] = field(default_factory=dict)
    #: V9: i motivi della conferma (fra questi, l'operazione sull'incarico confermata).
    confirmed_reasons: tuple[str, ...] = ()


def is_atomic(intent: str, ref_names) -> bool:
    """D-C: nota e task su un contatto (vedi `voice.ledger.step_class`)."""
    return intent == "add_task" or (intent == "add_note" and "property" not in set(ref_names))


def _dopo(costruisci):
    """Il risultato del passo, costruito DOPO il commit del servizio: un
    errore qui non e' un fallimento sicuro."""
    try:
        return costruisci()
    except Exception as exc:  # noqa: BLE001
        raise OutcomeUncertain(f"esito non leggibile dopo la scrittura: {type(exc).__name__}") from exc


def _provenienza(inp: StepInput) -> dict:
    return {"voice": {"command_id": inp.command_id, "step": inp.ordinal, "request_id": inp.key}}


def _id(inp: StepInput, nome: str) -> int:
    valore = inp.ids.get(nome)
    if valore is None:
        raise StepRefused(REFERENCE_MISSING, f"riferimento {nome} non risolto")
    return int(valore)


# ---------------------------------------------------------------------------
# Letture di supporto (sola lettura, transazione READ ONLY)
# ---------------------------------------------------------------------------

def db_now():
    with voice_repository.read_only() as cur:
        cur.execute("SELECT NOW() AS adesso")
        return cur.fetchone()["adesso"]


def property_row(ctx, property_id: int) -> dict | None:
    with voice_repository.read_only() as cur:
        cur.execute("SELECT * FROM properties WHERE id = %s AND agency_id = %s", (property_id, ctx.require_agency()))
        riga = cur.fetchone()
        return None if riga is None else dict(riga)


def manageable(ctx, property_id: int) -> bool:
    riga = property_row(ctx, property_id)
    return riga is not None and may_manage(ctx, riga)


def contact_visible(ctx, contact_id: int) -> bool:
    with voice_repository.read_only() as cur:
        source, params = scoped_source(ctx, "contacts", "c")
        cur.execute(f"SELECT 1 FROM {source} AND c.id = %s", params + [contact_id])
        return cur.fetchone() is not None


def owner_link_exists(ctx, property_id: int, contact_id: int) -> bool:
    with voice_repository.read_only() as cur:
        cur.execute("SELECT 1 FROM property_contacts pc JOIN properties p ON p.id = pc.property_id "
                    "WHERE pc.property_id = %s AND pc.contact_id = %s AND pc.role = ANY(%s) AND p.agency_id = %s",
                    (property_id, contact_id, list(OWNER_ROLES), ctx.require_agency()))
        return cur.fetchone() is not None


def record_by_key(ctx, intent: str, key: str | None) -> dict | None:
    """Il record che QUESTO comando ha gia' scritto con la sua chiave (dopo
    un crash fra il commit e il checkpoint): non e' un doppione ne' un
    conflitto, e' la stessa richiesta."""
    if not key:
        return None
    with voice_repository.read_only() as cur:
        if intent == "create_unit":
            cur.execute("SELECT id FROM properties WHERE agency_id = %s AND client_request_id = %s",
                        (ctx.require_agency(), key))
        elif intent == "create_building":
            cur.execute("SELECT id FROM buildings WHERE agency_id = %s AND client_request_id = %s",
                        (ctx.require_agency(), key))
        elif intent == "create_appointment":
            riga = agenda_repository.find_by_source_key(cur, agenda.CRM_SOURCE, key)
            return None if riga is None or riga["agency_id"] != ctx.require_agency() else dict(riga)
        else:
            return None
        riga = cur.fetchone()
        return None if riga is None else dict(riga)


def municipality_supported(payload: dict) -> bool:
    """Fase 5: il comune (e regione/provincia/microzona, se dette) e' nel
    catalogo del CRM? E' la STESSA regola che il censimento applica alla
    scrittura (`property.catalog.validate_location`), anticipata al piano: un
    comune fuori catalogo si chiede prima di scrivere, invece di fallire
    dopo (limite 2 della Fase 4)."""
    try:
        validate_location(payload.get("region"), payload.get("province"), payload.get("city"),
                          payload.get("microzone"))
    except ValueError:
        return False
    return True


def agenda_busy(ctx, agent_user_id: int, start_at, end_at, *, exclude_id: int | None = None) -> bool:
    """L'agente e' occupato in quell'intervallo? `appointments.find_conflicts`
    (sola lettura). Il proprio appuntamento gia' scritto (stessa chiave) non
    e' un conflitto. Un agente non attivo non si puo' fissare: occupato."""
    try:
        return bool(agenda.find_conflicts(ctx, assigned_user_id=agent_user_id, start_at=start_at, end_at=end_at,
                                          exclude_appointment_id=exclude_id))
    except Exception:  # noqa: BLE001 - NotFound: agente non attivo nell'agenzia
        return True


def seller_state(ctx, property_id: int, contact_id: int) -> dict:
    """Lo stato dell'opportunita' Venditore della coppia, come la legge
    `crm.sellers` (aperta, poi sospesa, poi la chiusa piu' recente), senza
    lock: serve a sapere, dopo, se il lead l'ha creato questo comando."""
    with voice_repository.read_only() as cur:
        cur.execute("SELECT NOW() AS adesso")
        adesso = cur.fetchone()["adesso"]
        cur.execute(
            "SELECT l.id, l.status FROM leads l JOIN property_leads pl ON pl.lead_id = l.id "
            "WHERE l.agency_id = %s AND l.contact_id = %s AND l.pipeline = 'sell' "
            "AND pl.property_id = %s AND pl.relation_type = 'seller' "
            "ORDER BY CASE l.status WHEN 'open' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END, l.updated_at DESC LIMIT 1",
            (ctx.require_agency(), contact_id, property_id))
        riga = cur.fetchone()
    return {"status": None if riga is None else riga["status"],
            "lead_id": None if riga is None else int(riga["id"]), "at": adesso.isoformat()}


def _attivazione_propria(ctx, lead_id: int, property_id: int, dal: str) -> str | None:
    """L'origine dell'ultima attivazione «Vende» di quel lead su quell'immobile
    se l'ha scritta QUESTO operatore dopo `dal` (prima scrittura del passo)."""
    with voice_repository.read_only() as cur:
        cur.execute(
            "SELECT metadata->>'origin' AS origin FROM activities "
            "WHERE agency_id = %s AND lead_id = %s AND activity_type = 'status_change' "
            "AND metadata->>'context' = 'seller' AND metadata->>'event' = 'activated' "
            "AND (metadata->>'property_id')::bigint = %s AND created_by_user_id = %s AND created_at >= %s "
            "ORDER BY id DESC LIMIT 1",
            (ctx.require_agency(), lead_id, property_id, ctx.user_id, dal))
        riga = cur.fetchone()
        return None if riga is None else riga["origin"]


# ---------------------------------------------------------------------------
# Contatto: lock dell'identita' + ricontrollo dei doppioni
# ---------------------------------------------------------------------------

def _identita(agency_id: int, payload: dict) -> list[str]:
    chiavi = []
    telefono, posta = normalize_phone(payload.get("phone")), normalize_email(payload.get("email"))
    if telefono:
        chiavi.append(f"voice:contact:{agency_id}:phone:{telefono}")
    if posta:
        chiavi.append(f"voice:contact:{agency_id}:email:{posta}")
    if not chiavi:
        nome = " ".join(str(payload.get(k) or "").strip().lower()
                        for k in ("first_name", "last_name", "company_name"))
        chiavi.append(f"voice:contact:{agency_id}:name:{nome}")
    return sorted(chiavi)


@contextlib.contextmanager
def _identity_lock(chiavi: list[str]):
    """Lock consultivo di SESSIONE, su una connessione dedicata, per ogni
    identita' (in ordine): due dettati concorrenti dello stesso contatto nuovo
    si mettono in fila e il secondo ricontrolla i doppioni dopo il primo.
    Non ferma una creazione concorrente dal CRM (limite dichiarato)."""
    conn = core_database.get_connection()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            for chiave in chiavi:
                cur.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (chiave,))
        yield
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock_all()")
        finally:
            conn.close()


def _create_contact(ctx, inp: StepInput) -> dict:
    modello = ContactCreate(**inp.payload)
    with _identity_lock(_identita(ctx.require_agency(), inp.payload)):
        with voice_repository.read_only() as cur:
            visibili, nascosto = resolver._contact_duplicates(ctx, cur, SimpleNamespace(payload=inp.payload))
        nuovi = {c.id for c in visibili} - set(inp.accepted_duplicates)
        if nuovi or (nascosto and not inp.accepted_hidden):
            raise StepRefused(CONTACT_DUPLICATE_APPEARED, "un contatto con gli stessi dati e' comparso ora")
        riga = core_service.create_contact(ctx, modello)
    return _dopo(lambda: {"entity": "contact", "id": int(riga["id"]), "created": True})


# ---------------------------------------------------------------------------
# Censimento
# ---------------------------------------------------------------------------

def _create_unit(ctx, inp: StepInput) -> dict:
    dati = dict(inp.payload)
    if not dati.get("record_kind"):
        raise StepRefused(RECORD_KIND_MISSING, "scheda commerciale o di censimento non decisa")
    if "building" in inp.ids:
        dati["building_id"] = _id(inp, "building")
    corpo = CensusUnitCreate(**dati, client_request_id=inp.key, confirm_similar=inp.confirm_similar)
    riga = census.create_unit(ctx, corpo)
    return _dopo(lambda: {"entity": "property", "id": int(riga["id"]), "record_kind": riga["record_kind"],
                          "created": True, "replica": bool(riga.get("replica"))})


def _create_building(ctx, inp: StepInput) -> dict:
    corpo = BuildingCreate(**inp.payload, client_request_id=inp.key, confirm_similar=inp.confirm_similar)
    riga = census.create_building(ctx, corpo)
    return _dopo(lambda: {"entity": "building", "id": int(riga["id"]), "created": True,
                          "replica": bool(riga.get("replica"))})


# ---------------------------------------------------------------------------
# Proprietario e «Vende» (D-D)
# ---------------------------------------------------------------------------

def _gestibile(ctx, property_id: int, contact_id: int) -> None:
    if not manageable(ctx, property_id):
        raise StepRefused(PROPERTY_NOT_MANAGEABLE, "immobile non gestito da chi parla")
    if not contact_visible(ctx, contact_id):
        raise StepRefused(CONTACT_NOT_VISIBLE, "contatto non visibile a chi parla")


def _link_owner(ctx, inp: StepInput) -> dict:
    pid, cid = _id(inp, "property"), _id(inp, "contact")
    _gestibile(ctx, pid, cid)
    esito = {"entity": "property_contact", "property_id": pid, "contact_id": cid, "role": "owner"}
    if owner_link_exists(ctx, pid, cid):
        return {**esito, "created": False, "preexisting": True}
    try:
        property_service.add_contact(ctx, pid, PropertyContactCreate(contact_id=cid, role="owner"))
    except ConflictError as exc:
        if type(exc) is ConflictError:          # UNIQUE: collegato nel frattempo da altri
            return {**esito, "created": False, "preexisting": True}
        raise
    return _dopo(lambda: {**esito, "created": True, "preexisting": False})


def _activate_seller(ctx, inp: StepInput) -> dict:
    pid, cid = _id(inp, "property"), _id(inp, "contact")
    _gestibile(ctx, pid, cid)
    esito, creato = sellers.activate(ctx, SellerActivate(property_id=pid, contact_id=cid))

    def risultato():
        prima = inp.seller_before or {}
        if creato:
            origine = "created"
        elif esito.get("reopened"):
            origine = "reopened"
        else:
            origine = "reused"
            # ripresa dopo un crash: il lead aperto puo' essere quello che
            # QUESTO comando ha creato (o riaperto) al tentativo precedente
            if prima.get("status") != "open" and prima.get("at"):
                propria = _attivazione_propria(ctx, int(esito["lead_id"]), pid, prima["at"])
                if propria == "created" and prima.get("status") is None:
                    origine = "created"
                elif propria == "reopened" and prima.get("status") in ("paused", "closed"):
                    origine = "reopened"
        return {"entity": "seller_lead", "id": int(esito["lead_id"]), "lead_id": int(esito["lead_id"]),
                "property_id": pid, "contact_id": cid, "origin": origine, "created": origine == "created"}
    return _dopo(risultato)


# ---------------------------------------------------------------------------
# Agenda
# ---------------------------------------------------------------------------

def _create_appointment(ctx, inp: StepInput) -> dict:
    p = inp.payload
    inizio = datetime.fromisoformat(p["start_at"])
    if inp.now is not None and inizio <= inp.now:
        raise StepRefused(APPOINTMENT_IN_PAST, "il momento indicato e' gia' passato")
    corpo = AppointmentCreateBody(
        appointment_type=p["appointment_type"], status="scheduled", start_at=p["start_at"], end_at=p["end_at"],
        assigned_user_id=int(inp.ids.get("agent", ctx.user_id)),
        contact_id=inp.ids.get("contact"), property_id=inp.ids.get("property"),
        location_text=p.get("location_text"), notes=p.get("notes"), client_request_id=inp.key)
    riga, _replica = agenda.create_appointment_idempotent(ctx, corpo)
    # V8-4: con l'inizio e i collegamenti, per «Apri in Agenda» e la scheda Incarico
    return _dopo(lambda: {"entity": "appointment", "id": int(riga["id"]), "created": True,
                          "start_at": riga["start_at"].isoformat() if riga.get("start_at") else None,
                          "property_id": riga.get("property_id"), "contact_id": riga.get("contact_id")})


# ---------------------------------------------------------------------------
# Note e task
# ---------------------------------------------------------------------------

def _note_on_property(ctx, inp: StepInput) -> dict:
    corpo = InteractionCreate(interaction_type=inp.payload.get("interaction_type", "note"),
                              note=inp.payload["note"], contact_id=inp.ids.get("contact"), context="property")
    riga = interactions.create_interaction(ctx, _id(inp, "property"), corpo)
    return _dopo(lambda: {"entity": "activity", "id": int(riga["id"]), "created": True})


def _note_with_cursor(ctx, cur, inp: StepInput) -> dict:
    """La stessa scrittura di `core.service.create_activity` (modello
    completo, nessuna chiave «errore» nei metadati), sul cursore della
    transazione che chiude il passo."""
    modello = ActivityCreate(contact_id=_id(inp, "contact"), activity_type=inp.payload["activity_type"],
                             description=inp.payload.get("description"), metadata=_provenienza(inp))
    riga = core_repository.create_activity_with_cursor(cur, modello.model_dump(), ctx=ctx)
    return {"entity": "activity", "id": int(riga["id"]), "created": True}


def _task_with_cursor(ctx, cur, inp: StepInput) -> dict:
    """La stessa scrittura di `core.service.create_task` (stato `open`, quindi
    nessun `completed_at`), sul cursore della transazione che chiude il passo."""
    p = inp.payload
    modello = TaskCreate(contact_id=_id(inp, "contact"), title=p["title"], description=p.get("description"),
                         task_type=p.get("task_type"), priority=p.get("priority", "normal"),
                         due_at=p.get("due_at"), metadata=_provenienza(inp))
    riga = core_repository.create_task_with_cursor(cur, modello.model_dump(), ctx=ctx)
    return {"entity": "task", "id": int(riga["id"]), "created": True}


# ---------------------------------------------------------------------------
# V9: incarichi e acquisizioni - SOLO i servizi originali del CRM
# ---------------------------------------------------------------------------

def _valuta(ctx, inp: StepInput):
    """La situazione rilevata ADESSO e le regole: deve uscirne la stessa
    operazione confermata, altrimenti nulla si scrive. I servizi delle
    Acquisizioni e degli Immobili ricontrollano poi tutto a modo loro."""
    pid = _id(inp, "property")
    with voice_repository.read_only() as cur:
        situazione = voice_repository.mandate_situation(ctx, cur, pid, inp.payload.get("owner_said"))
    if situazione is None:
        raise StepRefused(REFERENCE_MISSING, "immobile non trovato")
    valutazione = mandate_rules.evaluate(inp.intent, inp.payload, situazione)
    if valutazione.blocks or valutazione.asks:
        raise StepRefused((valutazione.blocks or valutazione.asks)[0], "dati o stato dell'incarico non adatti")
    if valutazione.mode is not None and valutazione.mode not in inp.confirmed_reasons:
        raise StepRefused(MANDATE_STATE_CHANGED, "la situazione e' cambiata dopo la conferma: ricontrolla")
    return pid, situazione, valutazione


def _mandate_prepare(ctx, inp: StepInput) -> dict:
    pid, situazione, v = _valuta(ctx, inp)
    acq = situazione["acquisition"]
    if v.mode == mandate_rules.WILL_GENERATE:
        # L'UNICO modo di generare un incarico: lo stesso di «Genera incarico»
        corpo = MandateBody(version=acq["version"], mandate_type=inp.payload["mandate_type"],
                            mandate_start=inp.payload["mandate_start"], mandate_end=inp.payload.get("mandate_end"),
                            agreed_price=v.values["agreed_price"])
        acquisitions_service.generate_mandate(ctx, acq["id"], corpo)
        return _dopo(lambda: {"entity": "mandate", "id": pid, "property_id": pid, "acquisition_id": acq["id"],
                              "created": True, "mode": v.mode})
    acquisitions_service.patch_acquisition(ctx, acq["id"], AcquisitionPatch(
        version=acq["version"], asking_price=v.values["asking_price"]))
    return _dopo(lambda: {"entity": "acquisition", "id": acq["id"], "property_id": pid, "acquisition_id": acq["id"],
                          "created": False, "mode": v.mode, "price": str(v.values["asking_price"])})


def _mandate_price(ctx, inp: StepInput) -> dict:
    pid, situazione, v = _valuta(ctx, inp)
    prezzo = v.values["price"]
    if prezzo is None:
        raise StepRefused(REFERENCE_MISSING, "prezzo mancante")
    precedente = str(v.values["previous"]) if v.values.get("previous") is not None else None
    if v.mode == mandate_rules.WILL_SET_PROPERTY_PRICE:
        # la stessa PATCH della scheda Immobile: storico prezzi con motivo e autore
        corpo = PropertyUpdate(asking_price=prezzo, change_reason="STIMA Voice: prezzo dell'incarico",
                               changed_by=f"operator:{int(ctx.user_id)}")
        composition.update_property_guarded(ctx, pid, corpo, lambda: property_service.update_property(ctx, pid, corpo))
        return _dopo(lambda: {"entity": "property", "id": pid, "property_id": pid, "mode": v.mode,
                              "price": str(prezzo), "previous_price": precedente})
    acq = situazione["acquisition"]
    acquisitions_service.patch_acquisition(ctx, acq["id"], AcquisitionPatch(
        version=acq["version"], **{v.values["column"]: prezzo}))
    return _dopo(lambda: {"entity": "acquisition", "id": acq["id"], "property_id": pid, "acquisition_id": acq["id"],
                          "mode": v.mode, "price": str(prezzo), "previous_price": precedente})


def _mandate_info(ctx, inp: StepInput) -> dict:
    """Sola lettura: il passo registra la domanda; la risposta e' la scheda."""
    pid = _id(inp, "property")
    return {"entity": "mandate_info", "id": pid, "property_id": pid, "created": False}


def _acquisition_start(ctx, inp: StepInput) -> dict:
    pid = _id(inp, "property")
    with voice_repository.read_only() as cur:
        situazione = voice_repository.mandate_situation(ctx, cur, pid, inp.payload.get("owner_said"))
    acq = (situazione or {}).get("acquisition")
    if acq and inp.key and str(acq.get("appointment_request_id") or "") == inp.key:
        # ripresa dopo un'interruzione: questa richiesta l'ha gia' creata
        return {"entity": "acquisition", "id": acq["id"], "property_id": pid, "acquisition_id": acq["id"],
                "created": False, "replica": True, "start_at": acq["appointment_start"].isoformat()
                if acq.get("appointment_start") else None}
    pid, situazione, v = _valuta(ctx, inp)
    p = inp.payload
    inizio = datetime.fromisoformat(p["start_at"])
    if inp.now is not None and inizio <= inp.now:
        raise StepRefused(APPOINTMENT_IN_PAST, "il momento indicato e' gia' passato")
    corpo = AcquisitionCreate(
        property_id=pid, owner_contact_id=v.values["owner_contact_id"], asking_price=v.values["asking_price"],
        appointment=AcquisitionAppointment(start_at=p["start_at"], end_at=p.get("end_at"),
                                           assigned_user_id=int(ctx.user_id), client_request_id=inp.key))
    dettaglio, replica = acquisitions_service.create_acquisition(ctx, corpo)
    return _dopo(lambda: {"entity": "acquisition", "id": int(dettaglio["id"]), "property_id": pid,
                          "acquisition_id": int(dettaglio["id"]), "created": not replica, "replica": bool(replica),
                          "mode": v.mode, "appointment_id": dettaglio.get("appointment_id"),
                          "start_at": p["start_at"]})


# ---------------------------------------------------------------------------

_SERVIZI = {
    "create_contact": _create_contact,
    "create_unit": _create_unit,
    "create_building": _create_building,
    "link_owner": _link_owner,
    "activate_seller": _activate_seller,
    "create_appointment": _create_appointment,
    "mandate_prepare": _mandate_prepare,
    "mandate_price": _mandate_price,
    "mandate_info": _mandate_info,
    "acquisition_start": _acquisition_start,
}


def run(ctx, inp: StepInput) -> dict:
    """Un passo con transazione propria del servizio."""
    if inp.intent == "add_note":
        return _note_on_property(ctx, inp)
    funzione = _SERVIZI.get(inp.intent)
    if funzione is None:
        raise StepRefused(UNSUPPORTED, f"intento non eseguibile: {inp.intent}")
    return funzione(ctx, inp)


def run_with_cursor(ctx, cur, inp: StepInput) -> dict:
    """Un passo atomico (D-C), sul cursore della transazione del checkpoint."""
    if inp.intent == "add_task":
        return _task_with_cursor(ctx, cur, inp)
    if inp.intent == "add_note":
        return _note_with_cursor(ctx, cur, inp)
    raise StepRefused(UNSUPPORTED, f"intento non atomico: {inp.intent}")
