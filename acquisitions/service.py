"""CRM-OPS-3 - le Acquisizioni: regole, permessi, transazioni.

UNA transazione per ogni scrittura (`core_cursor(commit=True)`): se un
passo fallisce - l'Agenda rifiuta l'orario, il database rifiuta l'incarico,
un vincolo scatta - non resta nulla di scritto.

L'appuntamento lo crea SEMPRE l'Agenda (`create_appointment_with_cursor`,
lo stesso codice della pagina Agenda: permessi, conflitti, evento, Google)
sul cursore di questa transazione. Qui c'e' solo `appointment_id`.

VISIBILITA' (modello Agenda/CORE, non PROPERTY): owner, admin e platform
admin dentro l'agenzia vedono tutte le acquisizioni; un `agent` solo quelle
assegnate a se'. Una riga non visibile e una inesistente sono la stessa
risposta (404).
"""
from __future__ import annotations

from datetime import timedelta

from pydantic import ValidationError as _SchemaError

from core.database import core_cursor
from core.exceptions import NotFoundError

from appointments import errors as _agenda_errors
from appointments import repository as _agenda_repository
from appointments import service as _agenda
from appointments.enums import default_duration_minutes
from appointments.schemas import AppointmentCreate

from . import errors, repository
from .enums import (
    APPOINTMENT_TYPE,
    EVENT_LABELS_IT,
    HAPPENED_APPOINTMENT_STATUSES,
    LOST_CANCEL_KINDS,
    MISTAKE_LABEL_IT,
    MISTAKE_REASON,
    OPEN_APPOINTMENT_STATUSES,
    INITIAL_STATUS,
    LOST_REASON_LABELS_IT,
    LOST_REASONS,
    MANDATE_FROM_STATUSES,
    MANDATE_ONLY_FROM_ACQUISITION,
    MANUAL_TRANSITIONS,
    OWNER_ROLES,
    REPLACEABLE_APPOINTMENT_STATUSES,
    SALE_TIMING_LABELS_IT,
    SALE_TIMINGS,
    SOURCE_LABELS_IT,
    SOURCES,
    STATUS_LABELS_IT,
    STATUSES,
    TERMINAL_STATUSES,
)

#: Stati dell'immobile su cui non si apre un percorso d'acquisizione e non si
#: genera un incarico.
_IMMOBILE_CHIUSO = ("sold", "archived")
PROPERTY_IN_CENSUS_MESSAGE = "Immobile in censimento: usa Prendi in carico"

MAX_LIST_LIMIT = 200
#: Limite di sicurezza nel seguire una catena di spostamenti dell'Agenda.
_MAX_SPOSTAMENTI = 100


# ---------------------------------------------------------------------------
# contesto e permessi
# ---------------------------------------------------------------------------

def _attore(ctx) -> int:
    user_id = getattr(ctx, "user_id", None)
    if user_id is None:
        raise errors.ForbiddenRole("Le acquisizioni richiedono una sessione operatore")
    return int(user_id)


def _puo_assegnare(ctx) -> bool:
    return bool(getattr(ctx, "may_assign_records", False))


def _vede_tutto(ctx) -> bool:
    return bool(getattr(ctx, "sees_all_agency_records", False))


def _visibile(ctx, row) -> bool:
    return _vede_tutto(ctx) or row["assigned_agent_id"] == _attore(ctx)


def _autore(actor) -> str:
    """Convenzione P26-5 dei campi d'audit di testo: mai l'email."""
    return f"operator:{int(actor)}"


def _agente_richiesto(ctx, cur, agency_id, richiesto) -> int:
    """L'agente dell'appuntamento e dell'acquisizione, mai inferito per chi
    assegna; per un `agent` e' se stesso, e solo se stesso."""
    io = _attore(ctx)
    if richiesto is None:
        if _puo_assegnare(ctx):
            raise errors.AgentRequired("Scegli l'agente dell'appuntamento")
        return io
    richiesto = int(richiesto)
    if not _puo_assegnare(ctx) and richiesto != io:
        raise errors.ForbiddenRole("Un agente puo' gestire solo le proprie acquisizioni")
    if not _agenda_repository.active_membership(cur, agency_id, richiesto):
        raise errors.AgentNotActive("L'agente indicato non e' un membro attivo di questa agenzia")
    return richiesto


def _cataloghi(*, sale_timing=None, source=None):
    if sale_timing is not None and sale_timing not in SALE_TIMINGS:
        raise errors.InvalidData("Tempistica di vendita non valida")
    if source is not None and source not in SOURCES:
        raise errors.InvalidData("Fonte non valida")


def _testo(valore):
    if valore is None:
        return None
    valore = valore.strip()
    return valore or None


def _blocca(ctx, cur, agency_id, acquisition_id, versione):
    row = repository.lock_acquisition(cur, agency_id, acquisition_id)
    if row is None or not _visibile(ctx, row):
        raise errors.AcquisitionNotFound("Acquisizione non trovata")
    if versione is not None and row["version"] != versione:
        raise errors.VersionConflict(
            "Questa acquisizione e' stata modificata da un altro operatore: ricarica",
            current_version=row["version"])
    return row


def _aperta(row):
    if row["status"] in TERMINAL_STATUSES:
        raise errors.InvalidTransition(
            f"L'acquisizione e' chiusa ({STATUS_LABELS_IT[row['status']]})")


def _proprietario(cur, agency_id, property_id, contact_id):
    proprietari = repository.property_owners(cur, agency_id, property_id, OWNER_ROLES)
    if not proprietari:
        raise errors.PropertyWithoutOwner(
            "L'immobile non ha proprietari: collega prima un proprietario all'immobile")
    if contact_id not in {p["contact_id"] for p in proprietari}:
        raise errors.OwnerNotLinked(
            "Il referente scelto non e' un proprietario di questo immobile")


def _lead(ctx, cur, lead_id):
    if lead_id is not None and not repository.visible_lead(cur, ctx, lead_id):
        raise NotFoundError("Lead non trovato")


def _lead_venditore(cur, agency_id, *, source, lead_id, property_id, owner_contact_id):
    """VENDITORI-1 REV 2 (R2). Il percorso dichiarato `source='seller_lead'`
    non si fida del `lead_id` arrivato dal browser (il link
    `#/acquisizioni/nuova/<immobile>/<proprietario>/<lead>`): il lead deve
    essere l'opportunita' Venditore indicata - stessa agenzia, `pipeline='sell'`,
    dello stesso proprietario, collegato `seller` a QUELL'immobile. La
    visibilita' resta quella di `_lead`, chiamata prima.

    Solo per `seller_lead`: gli altri flussi (lead generici, storici) restano
    con la sola regola di visibilita'. Chiamata prima di qualunque scrittura:
    se fallisce non esistono ne' l'acquisizione, ne' l'appuntamento, ne' gli
    eventi."""
    if source != "seller_lead" or lead_id is None:
        return
    cur.execute(
        "SELECT 1 FROM leads l JOIN property_leads pl ON pl.lead_id = l.id "
        " WHERE l.id = %s AND l.agency_id = %s AND l.pipeline = 'sell' AND l.contact_id = %s "
        "   AND pl.property_id = %s AND pl.relation_type = 'seller'",
        (lead_id, agency_id, owner_contact_id, property_id))
    if cur.fetchone() is None:
        raise errors.SellerLeadMismatch(
            "Il lead scelto non e' l'opportunita' venditore di questo proprietario su questo immobile")


def _corpo_appuntamento(appuntamento, *, agente, contact_id, property_id):
    """Il corpo dell'Agenda: tipo `seller_meeting`, referente e immobile
    dell'acquisizione, durata di default dell'Agenda se manca la fine."""
    try:
        fine = appuntamento.end_at
        if fine is None:
            fine = appuntamento.start_at + timedelta(
                minutes=default_duration_minutes(APPOINTMENT_TYPE))
        return AppointmentCreate(
            appointment_type=APPOINTMENT_TYPE, status="scheduled",
            start_at=appuntamento.start_at, end_at=fine, assigned_user_id=agente,
            buffer_before_minutes=appuntamento.buffer_before_minutes,
            buffer_after_minutes=appuntamento.buffer_after_minutes,
            contact_id=contact_id, property_id=property_id,
            location_text=appuntamento.location_text, notes=appuntamento.notes,
            client_request_id=appuntamento.client_request_id)
    except _SchemaError as exc:
        primo = (exc.errors() or [{}])[0]
        raise errors.InvalidData(primo.get("msg") or "Dati dell'appuntamento non validi") from exc
    except TypeError as exc:
        raise errors.InvalidData(
            "l'orario deve indicare il fuso (es. 2026-10-01T10:00:00+02:00)") from exc


def _acquisizione_della_catena(cur, agency_id, appointment_id):
    """L'acquisizione di un appuntamento o, se poi e' stato spostato
    dall'Agenda, del suo successore (`appointment_id` segue il reschedule)."""
    corrente = appointment_id
    for _ in range(_MAX_SPOSTAMENTI):
        trovata = repository.find_by_appointment(cur, agency_id, corrente)
        if trovata is not None:
            return trovata
        corrente = repository.successor_appointment(cur, agency_id, corrente)
        if corrente is None:
            return None
    return None


def _replica(ctx, cur, agency_id, chiave):
    """Una richiesta gia' eseguita (stessa `client_request_id`): la stessa
    acquisizione, oppure IDEMPOTENCY_KEY_REUSED se la chiave e' di altro."""
    if chiave is None:
        return None
    esistente = _agenda_repository.find_by_source_key(cur, _agenda.CRM_SOURCE, chiave)
    if esistente is None:
        return None
    if esistente["agency_id"] != agency_id or esistente["created_by_user_id"] != _attore(ctx):
        raise _agenda_errors.IdempotencyKeyReused(
            "Questa richiesta risulta gia' inviata con dati diversi: ricontrolla e conferma")
    trovata = _acquisizione_della_catena(cur, agency_id, esistente["id"])
    if trovata is None:
        raise _agenda_errors.IdempotencyKeyReused(
            "Questa richiesta risulta gia' inviata con dati diversi: ricontrolla e conferma")
    return trovata


def _traduci_unicita(exc):
    """L'indice parziale "una sola acquisizione aperta per immobile" vince
    anche fra due transazioni concorrenti: diventa un errore leggibile."""
    vincolo = getattr(getattr(exc, "diag", None), "constraint_name", None)
    if vincolo == "idx_acquisitions_open_property":
        return errors.OpenAcquisitionExists(
            "Per questo immobile c'e' gia' un'acquisizione aperta")
    return None


# ---------------------------------------------------------------------------
# OPZIONI
# ---------------------------------------------------------------------------

def options(ctx) -> dict:
    """L'unica fonte di stati, etichette, motivi, tempistiche, fonti e agenti."""
    _attore(ctx)
    return {
        "statuses": [{"value": s, "label": STATUS_LABELS_IT[s],
                      "terminal": s in TERMINAL_STATUSES} for s in STATUSES],
        "manual_transitions": {k: list(v) for k, v in MANUAL_TRANSITIONS.items()},
        "mandate_from_statuses": list(MANDATE_FROM_STATUSES),
        "lost_reasons": [{"value": r, "label": LOST_REASON_LABELS_IT[r]} for r in LOST_REASONS],
        "sale_timings": [{"value": t, "label": SALE_TIMING_LABELS_IT[t]} for t in SALE_TIMINGS],
        "sources": [{"value": s, "label": SOURCE_LABELS_IT[s]} for s in SOURCES],
        "event_labels": dict(EVENT_LABELS_IT),
        "agents": _agenda.list_agents(ctx),
        "can_assign": _puo_assegnare(ctx),
        "appointment_type": APPOINTMENT_TYPE,
        "default_duration_minutes": default_duration_minutes(APPOINTMENT_TYPE),
        "mandate_only_from_acquisition": MANDATE_ONLY_FROM_ACQUISITION,
    }


# ---------------------------------------------------------------------------
# CREAZIONE: acquisizione + appuntamento, UNA transazione
# ---------------------------------------------------------------------------

def create_acquisition(ctx, body):
    """Restituisce `(dettaglio, replica)`."""
    agency_id = ctx.require_agency()
    actor = _attore(ctx)
    _cataloghi(sale_timing=body.sale_timing, source=body.source)

    try:
        with core_cursor(commit=True) as (_, cur):
            # 1) l'immobile, bloccato: due creazioni sullo stesso immobile sono in fila
            immobile = repository.lock_property(cur, agency_id, body.property_id)
            if immobile is None:
                raise NotFoundError("Immobile non trovato")
            # 2) la stessa richiesta gia' eseguita -> la stessa acquisizione
            gia = _replica(ctx, cur, agency_id, body.appointment.client_request_id)
            if gia is not None:
                if gia["property_id"] != body.property_id or not _visibile(ctx, gia):
                    raise _agenda_errors.IdempotencyKeyReused(
                        "Questa richiesta risulta gia' inviata con dati diversi: ricontrolla e conferma")
                return _dettaglio(ctx, cur, agency_id, gia), True
            if immobile["archived_at"] is not None or immobile["commercial_status"] in _IMMOBILE_CHIUSO:
                raise errors.InvalidData("L'immobile e' venduto o archiviato")
            # CENSIMENTO-1 Fase 3 (§7): mai un'acquisizione su una scheda di censimento
            if immobile.get("record_kind") == "census":
                raise errors.PropertyInCensus(PROPERTY_IN_CENSUS_MESSAGE)
            if immobile["acquisition_id"] is not None:
                raise errors.MandateAlreadyExists(
                    "L'immobile ha gia' un incarico generato da un'acquisizione")
            _proprietario(cur, agency_id, body.property_id, body.owner_contact_id)
            if repository.open_for_property(cur, agency_id, body.property_id) is not None:
                raise errors.OpenAcquisitionExists(
                    "Per questo immobile c'e' gia' un'acquisizione aperta")
            _lead(ctx, cur, body.lead_id)
            _lead_venditore(cur, agency_id, source=_testo(body.source), lead_id=body.lead_id,
                            property_id=body.property_id, owner_contact_id=body.owner_contact_id)
            agente = _agente_richiesto(ctx, cur, agency_id, body.appointment.assigned_user_id)
            corpo = _corpo_appuntamento(body.appointment, agente=agente,
                                        contact_id=body.owner_contact_id,
                                        property_id=body.property_id)
            # 3) l'appuntamento: lo crea l'Agenda, su QUESTO cursore
            appuntamento, replica = _agenda.create_appointment_with_cursor(ctx, cur, corpo)
            if replica:
                # La chiave e' stata usata in parallelo da un'altra transazione
                # gia' committata: stessa regola del punto 2.
                gia = _replica(ctx, cur, agency_id, body.appointment.client_request_id)
                return _dettaglio(ctx, cur, agency_id, gia), True
            # 4) l'acquisizione e il suo evento
            row = repository.insert_acquisition(cur, {
                "agency_id": agency_id,
                "property_id": body.property_id,
                "owner_contact_id": body.owner_contact_id,
                "assigned_agent_id": agente,
                "appointment_id": appuntamento["id"],
                "lead_id": body.lead_id,
                "status": INITIAL_STATUS,
                "asking_price": body.asking_price,
                "valuation_price": body.valuation_price,
                "sale_timing": body.sale_timing,
                "source": _testo(body.source),
                "notes": _testo(body.notes),
                "created_by_user_id": actor,
            })
            repository.record_event(
                cur, agency_id=agency_id, acquisition_id=row["id"], event_type="created",
                from_status=None, to_status=row["status"], actor_user_id=actor,
                changes={c: row[c] for c in (
                    "property_id", "owner_contact_id", "assigned_agent_id", "appointment_id",
                    "lead_id", "asking_price", "valuation_price", "sale_timing", "source")
                    if row[c] is not None})
            return _dettaglio(ctx, cur, agency_id, row), False
    except Exception as exc:
        tradotta = _traduci_unicita(exc)
        if tradotta is not None:
            raise tradotta from exc
        raise


# ---------------------------------------------------------------------------
# MODIFICHE
# ---------------------------------------------------------------------------

def patch_acquisition(ctx, acquisition_id, body):
    agency_id = ctx.require_agency()
    actor = _attore(ctx)
    cambi = body.changes()
    _cataloghi(sale_timing=cambi.get("sale_timing"), source=cambi.get("source"))
    for campo in ("source", "notes"):
        if campo in cambi:
            cambi[campo] = _testo(cambi[campo])

    with core_cursor(commit=True) as (_, cur):
        row = _blocca(ctx, cur, agency_id, acquisition_id, body.version)
        _aperta(row)
        cambi = {c: v for c, v in cambi.items() if v != row[c]}
        if not cambi:
            return _dettaglio(ctx, cur, agency_id, row)
        if "assigned_agent_id" in cambi:
            if not _puo_assegnare(ctx):
                raise errors.ForbiddenRole(
                    "Solo owner e admin possono riassegnare un'acquisizione")
            if cambi["assigned_agent_id"] is None:
                raise errors.AgentRequired("Un'acquisizione ha sempre un agente")
            _agente_richiesto(ctx, cur, agency_id, cambi["assigned_agent_id"])
        if "owner_contact_id" in cambi:
            if cambi["owner_contact_id"] is None:
                raise errors.OwnerNotLinked("Un'acquisizione ha sempre un referente proprietario")
            _proprietario(cur, agency_id, row["property_id"], cambi["owner_contact_id"])
        if "lead_id" in cambi:
            _lead(ctx, cur, cambi["lead_id"])
        if cambi.keys() & {"lead_id", "owner_contact_id", "source"}:
            _lead_venditore(cur, agency_id, source=cambi.get("source", row["source"]),
                            lead_id=cambi.get("lead_id", row["lead_id"]), property_id=row["property_id"],
                            owner_contact_id=cambi.get("owner_contact_id", row["owner_contact_id"]))
        nuova = repository.update_acquisition(cur, row["id"], cambi)
        repository.record_event(
            cur, agency_id=agency_id, acquisition_id=row["id"], event_type="updated",
            from_status=row["status"], to_status=nuova["status"], actor_user_id=actor,
            changes={c: {"da": row[c], "a": nuova[c]} for c in sorted(cambi)})
        return _dettaglio(ctx, cur, agency_id, nuova)


def change_status(ctx, acquisition_id, body):
    """Solo le transizioni manuali dichiarate in `enums.MANUAL_TRANSITIONS`."""
    agency_id = ctx.require_agency()
    actor = _attore(ctx)
    stato = body.status
    if stato not in STATUSES:
        raise errors.InvalidData("Stato non valido")
    if stato == "acquired":
        raise errors.InvalidTransition(
            "Un'acquisizione diventa Acquisita solo generando l'incarico")
    if stato == "lost":
        raise errors.InvalidTransition("Per segnarla come persa indica il motivo")

    with core_cursor(commit=True) as (_, cur):
        row = _blocca(ctx, cur, agency_id, acquisition_id, body.version)
        _aperta(row)
        if stato not in MANUAL_TRANSITIONS.get(row["status"], ()):
            raise errors.InvalidTransition(
                f"Da «{STATUS_LABELS_IT[row['status']]}» non si passa a "
                f"«{STATUS_LABELS_IT[stato]}»")
        nuova = repository.update_acquisition(cur, row["id"], {"status": stato})
        repository.record_event(
            cur, agency_id=agency_id, acquisition_id=row["id"], event_type="status_changed",
            from_status=row["status"], to_status=stato, actor_user_id=actor,
            changes={"status": {"da": row["status"], "a": stato}})
        return _dettaglio(ctx, cur, agency_id, nuova)


def _blocca_con_appuntamento(ctx, cur, agency_id, acquisition_id, versione):
    """DELETE-ARCH Fase 1A: l'appuntamento e POI l'acquisizione - lo stesso
    ordine dei lock degli hook dell'Agenda e di `new_appointment` - per
    poterlo annullare nella stessa transazione. `(appuntamento, riga)`."""
    letta = repository.get_acquisition(cur, agency_id, acquisition_id)
    if letta is None or not _visibile(ctx, letta):
        raise errors.AcquisitionNotFound("Acquisizione non trovata")
    attuale = _agenda_repository.lock_appointment(cur, agency_id, letta["appointment_id"])
    row = _blocca(ctx, cur, agency_id, acquisition_id, versione)
    if row["appointment_id"] != letta["appointment_id"]:
        raise errors.VersionConflict(
            "L'appuntamento di questa acquisizione e' appena cambiato: ricarica",
            current_version=row["version"])
    return attuale, row


def mark_lost(ctx, acquisition_id, body):
    """Perdita REALE. Con `cancel_appointment` (DELETE-ARCH Fase 1A) annulla
    anche l'appuntamento ancora aperto, qualificato 'client' o 'agency':
    stesso flusso dell'Agenda, stessa transazione. Mai 'mistake' da qui, e
    mai `created_by_mistake` come motivo (ha la sua azione)."""
    agency_id = ctx.require_agency()
    actor = _attore(ctx)
    motivo = _testo(body.lost_reason)
    if motivo is None:
        raise errors.LostReasonRequired("Indica il motivo per cui l'acquisizione e' persa")
    if motivo not in LOST_REASONS:
        raise errors.InvalidData("Motivo non valido")
    if body.appointment_cancelled_kind not in LOST_CANCEL_KINDS:
        raise errors.InvalidData("Tipo di annullamento non ammesso per una perdita")
    note = _testo(body.lost_notes)

    with core_cursor(commit=True) as (_, cur):
        if body.cancel_appointment:
            attuale, row = _blocca_con_appuntamento(ctx, cur, agency_id, acquisition_id, body.version)
            _aperta(row)
            if attuale is not None and attuale["status"] in OPEN_APPOINTMENT_STATUSES:
                _agenda.cancel_locked(cur, agency_id, actor, attuale,
                                      reason="Acquisizione persa",
                                      kind=body.appointment_cancelled_kind)
        else:
            row = _blocca(ctx, cur, agency_id, acquisition_id, body.version)
            _aperta(row)
        quando = repository.db_now(cur)
        nuova = repository.update_acquisition(cur, row["id"], {
            "status": "lost", "lost_reason": motivo, "lost_notes": note, "lost_at": quando})
        repository.record_event(
            cur, agency_id=agency_id, acquisition_id=row["id"], event_type="lost",
            from_status=row["status"], to_status="lost", actor_user_id=actor,
            changes={"lost_reason": motivo, "lost_notes": note})
        return _dettaglio(ctx, cur, agency_id, nuova)


def mark_created_by_mistake(ctx, acquisition_id, body):
    """DELETE-ARCH Fase 1A - «Segna come creata per errore».

    Un'acquisizione che non e' mai esistita: `lost` / `created_by_mistake`,
    terminale, fuori dalla lista normale e dalle «Perse». L'appuntamento
    ancora aperto si annulla con `cancelled_kind='mistake'` nella STESSA
    transazione (stesso flusso dell'Agenda: visite, Google, promemoria).

    Rifiutata con 409 APPOINTMENT_ALREADY_HAPPENED se l'incontro e' avvenuto:
    appuntamento svolto o cliente assente (anche un predecessore, dal
    registro), o pipeline gia' oltre `appointment_set`. Nessun timer.
    Un appuntamento gia' annullato resta com'e' (era un annullamento vero).
    Nessuna scrittura prima dei controlli; nessuna DELETE."""
    agency_id = ctx.require_agency()
    actor = _attore(ctx)
    note = _testo(body.notes)

    with core_cursor(commit=True) as (_, cur):
        if not repository.mistakes_installed(cur):
            raise errors.MistakesNotInstalled(
                "«Creata per errore» non e' ancora disponibile: manca l'aggiornamento del database")
        attuale, row = _blocca_con_appuntamento(ctx, cur, agency_id, acquisition_id, body.version)
        _aperta(row)
        stato_app = attuale["status"] if attuale is not None else None
        if (row["status"] != INITIAL_STATUS or stato_app in HAPPENED_APPOINTMENT_STATUSES
                or repository.appointment_happened(cur, agency_id, row["id"])):
            raise errors.AppointmentAlreadyHappened(
                "L'appuntamento risulta gia' avvenuto: l'acquisizione non e' un errore. "
                "Segnala come persa con il motivo reale.")
        if stato_app in OPEN_APPOINTMENT_STATUSES:
            _agenda.cancel_locked(cur, agency_id, actor, attuale,
                                  reason="Acquisizione creata per errore", kind="mistake")
        quando = repository.db_now(cur)
        nuova = repository.update_acquisition(cur, row["id"], {
            "status": "lost", "lost_reason": MISTAKE_REASON, "lost_notes": note, "lost_at": quando})
        repository.record_event(
            cur, agency_id=agency_id, acquisition_id=row["id"], event_type="lost",
            from_status=row["status"], to_status="lost", actor_user_id=actor,
            changes={"lost_reason": MISTAKE_REASON, "lost_notes": note,
                     "appointment_id": row["appointment_id"],
                     "appointment_cancelled": stato_app in OPEN_APPOINTMENT_STATUSES})
        return _dettaglio(ctx, cur, agency_id, nuova)


def new_appointment(ctx, acquisition_id, body):
    """Un nuovo appuntamento quando quello attuale e' annullato o mancato.
    Restituisce `(dettaglio, replica)`.

    Ordine dei lock: l'appuntamento attuale -> l'acquisizione -> gli agenti
    (dentro l'Agenda), lo stesso degli hook dell'Agenda."""
    agency_id = ctx.require_agency()
    actor = _attore(ctx)

    with core_cursor(commit=True) as (_, cur):
        gia = _replica(ctx, cur, agency_id, body.appointment.client_request_id)
        if gia is not None:
            if gia["id"] != acquisition_id or not _visibile(ctx, gia):
                raise _agenda_errors.IdempotencyKeyReused(
                    "Questa richiesta risulta gia' inviata con dati diversi: ricontrolla e conferma")
            return _dettaglio(ctx, cur, agency_id, gia), True
        letta = repository.get_acquisition(cur, agency_id, acquisition_id)
        if letta is None or not _visibile(ctx, letta):
            raise errors.AcquisitionNotFound("Acquisizione non trovata")
        attuale = _agenda_repository.lock_appointment(cur, agency_id, letta["appointment_id"])
        row = _blocca(ctx, cur, agency_id, acquisition_id, body.version)
        if row["appointment_id"] != letta["appointment_id"] or attuale is None:
            raise errors.VersionConflict(
                "L'appuntamento di questa acquisizione e' appena cambiato: ricarica",
                current_version=row["version"])
        _aperta(row)
        if attuale["status"] not in REPLACEABLE_APPOINTMENT_STATUSES:
            raise errors.AppointmentStillOpen(
                "L'appuntamento attuale e' ancora valido: spostalo o annullalo dall'Agenda")
        agente = _agente_richiesto(ctx, cur, agency_id, body.appointment.assigned_user_id)
        corpo = _corpo_appuntamento(body.appointment, agente=agente,
                                    contact_id=row["owner_contact_id"],
                                    property_id=row["property_id"])
        appuntamento, replica = _agenda.create_appointment_with_cursor(ctx, cur, corpo)
        if replica:
            raise _agenda_errors.IdempotencyKeyReused(
                "Questa richiesta risulta gia' inviata con dati diversi: ricontrolla e conferma")
        # Solo l'appuntamento cambia: lo stato commerciale resta quello che e'.
        nuova = repository.update_acquisition(cur, row["id"], {"appointment_id": appuntamento["id"]})
        repository.record_event(
            cur, agency_id=agency_id, acquisition_id=row["id"],
            event_type="appointment_replaced", from_status=row["status"],
            to_status=nuova["status"], actor_user_id=actor,
            changes={"old_appointment_id": row["appointment_id"],
                     "old_appointment_status": attuale["status"],
                     "new_appointment_id": appuntamento["id"],
                     "start_at": appuntamento["start_at"]})
        return _dettaglio(ctx, cur, agency_id, nuova), False


def generate_mandate(ctx, acquisition_id, body):
    """L'UNICO modo di generare un incarico nuovo. Atomico: immobile,
    storico, acquisizione ed evento in una transazione, o niente.

    Ordine dei lock: immobile -> acquisizione."""
    agency_id = ctx.require_agency()
    actor = _attore(ctx)

    with core_cursor(commit=True) as (_, cur):
        property_id = repository.property_of(cur, agency_id, acquisition_id)
        if property_id is None:
            raise errors.AcquisitionNotFound("Acquisizione non trovata")
        immobile = repository.lock_property(cur, agency_id, property_id)
        row = _blocca(ctx, cur, agency_id, acquisition_id, body.version)
        if immobile is None:
            raise errors.AcquisitionNotFound("Acquisizione non trovata")
        if row["status"] == "acquired" or immobile["acquisition_id"] is not None:
            raise errors.MandateAlreadyExists("L'incarico e' gia' stato generato")
        if row["status"] not in MANDATE_FROM_STATUSES:
            raise errors.MandateNotAllowed(
                "L'incarico si genera dopo il sopralluogo, da un'acquisizione aperta")
        if immobile["archived_at"] is not None or immobile["commercial_status"] in _IMMOBILE_CHIUSO:
            raise errors.MandateNotAllowed("L'immobile e' venduto o archiviato")

        cambi = {
            "acquisition_id": row["id"],
            "mandate_type": body.mandate_type.strip(),
            "mandate_start": body.mandate_start,
            "mandate_end": body.mandate_end,
            "commercial_status": "mandate",
        }
        if immobile["assigned_agent_id"] is None:
            # L'agente dell'acquisizione diventa l'agente dell'immobile solo se
            # l'immobile non ne ha gia' uno: cambiarlo resta una riassegnazione
            # (CRM-OPS-2, solo chi puo' assegnare).
            cambi["assigned_agent_id"] = row["assigned_agent_id"]
            cambi["assigned_to"] = _agenda_repository.agent_name(
                cur, agency_id, row["assigned_agent_id"])
        prezzo_cambiato = (body.agreed_price is not None
                           and body.agreed_price != immobile["asking_price"])
        if prezzo_cambiato:
            cambi["asking_price"] = body.agreed_price
        aggiornato = repository.write_mandate(cur, agency_id, property_id, cambi)
        autore = _autore(actor)
        nota = f"incarico generato dall'acquisizione {row['id']}"
        if immobile["commercial_status"] != "mandate":
            repository.property_status_history(
                cur, property_id, old=immobile["commercial_status"], new="mandate",
                note=nota, changed_by=autore)
        if prezzo_cambiato:
            repository.property_price_history(
                cur, property_id, old=immobile["asking_price"], new=body.agreed_price,
                reason=nota, changed_by=autore)
        quando = repository.db_now(cur)
        nuova = repository.update_acquisition(cur, row["id"], {
            "status": "acquired", "acquired_at": quando})
        repository.record_event(
            cur, agency_id=agency_id, acquisition_id=row["id"], event_type="mandate_created",
            from_status=row["status"], to_status="acquired", actor_user_id=actor,
            changes={"property_id": property_id,
                     "mandate_type": aggiornato["mandate_type"],
                     "mandate_start": aggiornato["mandate_start"],
                     "mandate_end": aggiornato["mandate_end"],
                     "agreed_price": body.agreed_price,
                     "previous_commercial_status": immobile["commercial_status"]})
        return _dettaglio(ctx, cur, agency_id, nuova)


# ---------------------------------------------------------------------------
# LETTURE
# ---------------------------------------------------------------------------

def list_acquisitions(ctx, *, statuses=None, agent_id=None, date_from=None, date_to=None,
                      city=None, search=None, limit=50, offset=0, mistakes=False):
    agency_id = ctx.require_agency()
    io = _attore(ctx)
    for s in statuses or ():
        if s not in STATUSES:
            raise errors.InvalidData(f"Stato non valido: {s}")
    if not 1 <= int(limit) <= MAX_LIST_LIMIT or int(offset) < 0:
        raise errors.InvalidData("Paginazione non valida")
    solo = None if _vede_tutto(ctx) else io
    with core_cursor() as (_, cur):
        righe = repository.list_acquisitions(
            cur, agency_id=agency_id, only_agent_id=solo, statuses=statuses,
            agent_id=agent_id, date_from=date_from, date_to=date_to,
            city=_testo(city), search=_testo(search), limit=int(limit), offset=int(offset),
            mistakes=mistakes)
    for r in righe:
        r["status_label"] = (MISTAKE_LABEL_IT if r.get("lost_reason") == MISTAKE_REASON
                             else STATUS_LABELS_IT.get(r["status"], r["status"]))
    return righe


def get_acquisition(ctx, acquisition_id):
    agency_id = ctx.require_agency()
    _attore(ctx)
    with core_cursor() as (_, cur):
        row = repository.get_acquisition(cur, agency_id, acquisition_id)
        if row is None or not _visibile(ctx, row):
            raise errors.AcquisitionNotFound("Acquisizione non trovata")
        return _dettaglio(ctx, cur, agency_id, row)


def _azioni(ctx, row, immobile, appuntamento) -> dict:
    aperta = row["status"] not in TERMINAL_STATUSES
    stato_app = appuntamento["status"] if appuntamento else None
    return {
        "edit": aperta,
        "reassign": aperta and _puo_assegnare(ctx),
        "transitions": list(MANUAL_TRANSITIONS.get(row["status"], ())) if aperta else [],
        "lost": aperta,
        # DELETE-ARCH Fase 1A: offerta solo finche' l'incontro non e' avvenuto
        # (il server ricontrolla anche i predecessori dal registro).
        "mistake": (aperta and row["status"] == INITIAL_STATUS
                    and (appuntamento is None or appuntamento["status"] not in HAPPENED_APPOINTMENT_STATUSES)),
        "new_appointment": aperta and stato_app in REPLACEABLE_APPOINTMENT_STATUSES,
        "mandate": (row["status"] in MANDATE_FROM_STATUSES
                    and immobile is not None and immobile["acquisition_id"] is None
                    and immobile["commercial_status"] not in _IMMOBILE_CHIUSO),
    }


def _dettaglio(ctx, cur, agency_id, row) -> dict:
    immobile = repository.property_summary(cur, agency_id, row["property_id"])
    proprietari = repository.property_owners(cur, agency_id, row["property_id"], OWNER_ROLES)
    for p in proprietari:
        p["is_main"] = p["contact_id"] == row["owner_contact_id"]
    appuntamento = repository.appointment_summary(cur, agency_id, row["appointment_id"])
    esito = dict(row)
    esito.update({
        "status_label": (MISTAKE_LABEL_IT if row["lost_reason"] == MISTAKE_REASON
                         else STATUS_LABELS_IT.get(row["status"], row["status"])),
        "lost_reason_label": (MISTAKE_LABEL_IT if row["lost_reason"] == MISTAKE_REASON
                              else LOST_REASON_LABELS_IT.get(row["lost_reason"]) if row["lost_reason"] else None),
        "created_by_mistake": row["lost_reason"] == MISTAKE_REASON,
        "sale_timing_label": SALE_TIMING_LABELS_IT.get(row["sale_timing"]) if row["sale_timing"] else None,
        "source_label": SOURCE_LABELS_IT.get(row["source"], row["source"]) if row["source"] else None,
        "agent_name": repository.operator_name(cur, row["assigned_agent_id"]),
        "property": immobile,
        "owners": proprietari,
        "appointment": appuntamento,
        "lead": repository.lead_summary(cur, agency_id, row["lead_id"]) if row["lead_id"] else None,
        "events": repository.list_events(cur, agency_id, row["id"]),
        "allowed_actions": _azioni(ctx, row, immobile, appuntamento),
    })
    return esito
