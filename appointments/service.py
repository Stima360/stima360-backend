"""A30-1/A30-2 - il servizio dell'Agenda: scope, attore, lock, controllo, scrittura.

Deriva agenzia e attore dal contesto (`ctx.require_agency()`, `ctx.user_id`),
non li riceve mai dal chiamante.

LA PROTEZIONE ANTI-SOVRAPPOSIZIONE, A TRE LIVELLI

    1. vincolo EXCLUDE nel database (072): la garanzia finale, vale anche
       fuori dal service;
    2. lock consultivo per agente nella transazione (`lock_agents`): chi
       controlla e poi scrive per quell'agente e' solo fino al commit;
    3. controllo esplicito (`find_conflicts`) prima della scrittura: serve a
       rispondere con i conflitti e le alternative invece che con un errore
       di database.

ORDINE DEI LOCK, unico: riga appointments -> agenti (id crescente) -> (solo
proiezione, A30-2P, e guardia D5 di A30-7) stima -> riga stima_inspections.

A30-7 - DUE GUARDIE DI "PIANIFICA" / "FISSA SOPRALLUOGO"
    D4: `schedule` rifiuta un inizio gia' passato (SCHEDULE_IN_PAST, 422),
        confrontando ISTANTI (con fuso) con l'orologio del service. Solo la
        pianificazione: le righe storiche non si toccano.
    D5: un sopralluogo legato a una stima non si fissa se la stessa stima ha
        gia' un altro sopralluogo APERTO (stati non terminali della macchina a
        stati): STIMA_INSPECTION_ALREADY_OPEN, 409. La verifica avviene sotto
        il lock della riga `stime`, nella transazione della scrittura: due
        pianificazioni concorrenti si mettono in fila e la seconda vede la
        prima. Vale per `schedule` (la pianificazione di una richiesta): la
        creazione gia' fissata e la facade LMC-15 restano come in A30-2/2P.

A30-8 - ESITO E FOLLOW-UP
    L'esito autorevole e' lo stato terminale (completed = Svolto, no_show =
    Non presentato, cancelled = Annullato). complete/no_show accettano una
    `outcome_note` facoltativa, scritta SOLO nell'evento `status_changed`
    (changes.outcome_note); cancel conserva `cancelled_reason`.
    complete/no_show/cancel accettano un `follow_up` facoltativo: un task CORE
    (`appointment_followup`) creato NELLA STESSA TRANSAZIONE della transizione,
    con contatto/lead/stima letti dalla riga bloccata (mai dal client) e
    l'operatore come autore. Exactly-once per costruzione: un retry trova la
    riga terminale o una version nuova (409) e non crea nulla.
    Guardie di tempo (D6): COMPLETE_TOO_EARLY, NO_SHOW_TOO_EARLY (NOW() del
    database, 422 con `available_from`), RESCHEDULE_IN_PAST (come D4 di A30-7),
    FOLLOW_UP_IN_PAST (NOW() del database). Nessun cambio automatico di lead,
    contatto, stima o mandato.

VISIBILITA' (matrice P26-1, D4, D6)
    owner / admin / platform admin in acting: tutta l'agenzia;
    agent: SOLO gli appuntamenti assegnati a lui. Una richiesta senza agente
    e un appuntamento di un collega sono, per lui, inesistenti (404): nel
    calendario i colleghi compaiono solo come "Occupato" (orario + nome).

PROIEZIONE LMC-15: ACCESA da A30-2P (`projection.PROJECTION_ENABLED`). Un
sopralluogo legato a una stima si fissa, si sposta e si chiude dall'Agenda, e
la riga LMC-15 e' scritta nella stessa transazione. A interruttore spento
(arresto d'emergenza) torna a potersi creare solo come `requested`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.database import core_cursor
from core.exceptions import NotFoundError, ValidationError
# A30-8: il follow-up di un esito e' un task CORE, creato nella transazione
# dell'Agenda con l'helper autorevole (percorso R-4 + autore esplicito).
from core.repository import create_task_with_cursor

from . import availability, errors, projection, repository, state_machine
from . import working_hours
from . import working_hours_repository as _wh_repository
# A30-12: il booking pubblico e' l'unico chiamante di questo modulo che
# porta un `SystemAgencyContext` invece di un `OperatorContext` - lo si
# importa qui solo per il controllo di tipo in
# `create_public_booking_appointment`, mai per costruirne uno.
from operator_auth.context import SystemAgencyContext
# Sola lettura, tenant-safe, su `contacts` (A30-12 D5): la stessa funzione
# di predicato che usa `core.repository.bridge_public_stima`, riusata qui
# sullo stesso cursore della transazione di booking - mai una `core_cursor`
# separata, che il lock d'agente preso sopra non proteggerebbe piu'.
from core.scope import scoped_source
from .enums import (
    APPOINTMENT_STATUSES,
    APPOINTMENT_TYPES,
    BLOCKING_STATUSES,
    CANCELLED_KINDS,
    MISTAKE_KIND,
)

# A30-9B: l'hook verso la sincronizzazione del calendario esterno (Google).
# NO-OP controllato se quel package non e' configurato per questo deployment
# (calendar_sync.integration.on_appointment_mutation, §17): l'Agenda non deve
# mai dipendere da Google per riuscire. Nessuna rete qui, nessuna logica
# Google in questo modulo: solo "questa catena va riconciliata", nella STESSA
# transazione della mutazione.
from calendar_sync import integration as _gcal
from calendar_sync.constants import APPOINTMENT_REMOTE_PRESENT as _GCAL_PRESENT

# A31-2: la proiezione delle visite acquirente (`buyer_visit` con immobile)
# sulla tabella legacy delle visite. Vive FUORI da questo package (sentinella
# A30-2): qui solo le chiamate agli hook, sullo STESSO cursore della
# mutazione, prima del mark dirty Google. Se la proiezione fallisce, la
# mutazione si annulla tutta.
from buyer_visits import integration as _visite

# CRM-OPS-3: le Acquisizioni seguono il LORO appuntamento (`seller_meeting`):
# reschedule -> `appointment_id` al successore; completato -> sopralluogo
# effettuato; annullato/mancato -> solo l'evento. Stesso schema di A31-2:
# il pacchetto vive fuori dall'Agenda, qui solo le chiamate, sullo STESSO
# cursore, prima del mark dirty Google.
from acquisitions import integration as _acquisizioni

try:  # il driver vero; in sviluppo senza psycopg2 il conftest ne mette uno finto
    from psycopg2 import errors as _pg_errors
    _EXCLUSION_VIOLATION = getattr(_pg_errors, "ExclusionViolation", None)
except ImportError:  # pragma: no cover
    _EXCLUSION_VIOLATION = None

# Nomi storici di A30-1, mantenuti: chi li importa da qui continua a funzionare.
AppointmentConflict = errors.AppointmentConflict
InspectionProjectionNotActive = errors.InspectionProjectionNotActive

#: Stati mostrati di default nel calendario: gli annullati e gli spostati no.
DEFAULT_CALENDAR_STATUSES = ("requested", "scheduled", "confirmed", "completed", "no_show")
#: Stati di default per le liste: tutti.
DEFAULT_LIST_STATUSES = APPOINTMENT_STATUSES
MAX_CALENDAR_RANGE = timedelta(days=42)
MAX_LIST_LIMIT = 200

#: Chiave di idempotenza delle creazioni CRM (A30-2 §6).
CRM_SOURCE = "crm_manual"

#: I campi di una creazione confrontati con l'evento `created` per decidere
#: se una replica e' "la stessa richiesta".
_CAMPI_IMPRONTA = (
    "appointment_type", "status", "start_at", "end_at", "assigned_user_id",
    "buffer_before_minutes", "buffer_after_minutes", "stima_id", "contact_id",
    "lead_id", "property_id", "location_text", "notes",
)


def _adesso() -> datetime:
    """L'orologio del processo: `confirmed_at` e `allowed_actions`. Iniettabile nei test. NON e' l'orologio di
    `completed_at`, `cancelled_at` e `no_show_at`: quelli sono il NOW() del
    database della transazione (repository.db_now, A30-2 correzione A e
    A30-2P F9)."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# contesto, ruoli, visibilita'
# ---------------------------------------------------------------------------

def _attore(ctx) -> int:
    user_id = getattr(ctx, "user_id", None)
    if user_id is None:
        raise errors.SessionRequired(
            "L'Agenda richiede una sessione operatore: "
            "un appuntamento senza attore e' un audit che non dice chi")
    return int(user_id)


def _puo_assegnare(ctx) -> bool:
    return bool(getattr(ctx, "may_assign_records", False))


def _solo_agente(ctx):
    """None per chi vede tutta l'agenzia; l'id dell'agent altrimenti."""
    return None if _puo_assegnare(ctx) else _attore(ctx)


def _visibile(ctx, row) -> bool:
    return _puo_assegnare(ctx) or row["assigned_user_id"] == _attore(ctx)


def _controlla_agente(ctx, cur, agency_id, assigned_user_id):
    """Chi puo' mettere un appuntamento nell'agenda di chi.

    Matrice P26-1: owner, admin e platform admin assegnano; un `agent` no,
    quindi un agente scrive solo nella propria agenda.
    """
    if assigned_user_id is None:
        return
    if not _puo_assegnare(ctx) and int(assigned_user_id) != int(ctx.user_id):
        raise errors.ForbiddenRole("Un agente puo' fissare appuntamenti solo nella propria agenda")
    if not repository.active_membership(cur, agency_id, assigned_user_id):
        raise errors.AgentNotActive("L'agente indicato non e' un membro attivo di questa agenzia")


def _controlla_stima(cur, agency_id, stima_id):
    if stima_id is None:
        return
    # Una stima di un'altra agenzia e una stima inesistente sono la stessa
    # risposta: il chiamante non impara che l'altra esiste.
    if repository.stima_agency(cur, stima_id) != agency_id:
        raise NotFoundError("Stima non trovata")


def _controlla_collegamenti(cur, agency_id, *, contact_id, lead_id, property_id):
    """Errori puliti prima del trigger della 072 (che resta la garanzia)."""
    for tabella, valore, nome in (("contacts", contact_id, "Contatto"),
                                  ("properties", property_id, "Immobile")):
        if valore is not None:
            r = repository.link_agency(cur, tabella, valore)
            if r is None or r["agency_id"] != agency_id:
                raise NotFoundError(f"{nome} non trovato")
    if lead_id is not None:
        r = repository.link_agency(cur, "leads", lead_id)
        if r is None or r["agency_id"] != agency_id:
            raise NotFoundError("Lead non trovato")
        if contact_id is not None and r["contact_id"] != contact_id:
            raise errors.LinkMismatch("Il lead indicato appartiene a un altro contatto")


# ---------------------------------------------------------------------------
# occupazione: lock + controllo + alternative
# ---------------------------------------------------------------------------

def _alternative(cur, *, assigned_user_id, start_at, end_at, before, after, escluso=None):
    durata = int((end_at - start_at).total_seconds() // 60)
    occupato = repository.busy_intervals(
        cur, assigned_user_id=assigned_user_id, date_from=start_at,
        date_to=start_at + availability.ALTERNATIVES_HORIZON + timedelta(days=1),
        exclude_appointment_id=escluso)
    return availability.alternatives(start_at, duration=durata, busy=occupato,
                                     buffer_before=before, buffer_after=after)


def _occupa(cur, *, assigned_user_id, start_at, end_at, before, after, escluso=None,
            gia_bloccato=False):
    """Livelli 2 e 3: lock dell'agente, poi controllo. Chiamare DOPO i lock di riga."""
    if not gia_bloccato:
        repository.lock_agents(cur, [assigned_user_id])
    conflitti = repository.find_conflicts(
        cur, assigned_user_id=assigned_user_id, start_at=start_at, end_at=end_at,
        buffer_before_minutes=before, buffer_after_minutes=after,
        exclude_appointment_id=escluso)
    if conflitti:
        raise errors.AppointmentConflict(
            "Orario non disponibile per l'agente", conflitti,
            _alternative(cur, assigned_user_id=assigned_user_id, start_at=start_at,
                         end_at=end_at, before=before, after=after, escluso=escluso))


def _traduci_esclusione(exc):
    if _EXCLUSION_VIOLATION is not None and isinstance(exc, _EXCLUSION_VIOLATION):
        return errors.AppointmentConflict("Orario non disponibile per l'agente")
    return None


def _in_transazione(funzione):
    """Una transazione, un commit; la violazione dell'EXCLUDE diventa un
    conflitto leggibile anche se il controllo applicativo e' stato saltato."""
    try:
        with core_cursor(commit=True) as (_, cur):
            return funzione(cur)
    except Exception as exc:
        tradotta = _traduci_esclusione(exc)
        if tradotta is not None:
            raise tradotta from exc
        raise


# ---------------------------------------------------------------------------
# A30-7: le guardie della pianificazione
# ---------------------------------------------------------------------------

def _non_nel_passato(start_at) -> None:
    """D4: istanti con fuso, mai orologi locali ingenui."""
    if start_at < _adesso():
        raise errors.ScheduleInPast(
            "L'orario scelto e' gia' passato: scegli un orario futuro")


def _sopralluogo_unico(ctx, cur, agency_id, *, appointment_type, stima_id, escluso=None):
    """D5: al massimo UN sopralluogo aperto per stima. Chiamare dopo i lock di
    riga e di agente (ordine dei lock); il lock della stima resta fino al
    commit, quindi la proiezione che segue lavora sulla stessa riga bloccata."""
    if appointment_type != "inspection" or stima_id is None:
        return
    repository.lock_stima(cur, agency_id, stima_id)
    altro = repository.open_inspection_for_stima(
        cur, agency_id, stima_id, statuses=state_machine.OPEN_STATUSES,
        exclude_appointment_id=escluso)
    if altro is None:
        return
    dati = {}
    if _visibile(ctx, altro):
        dati["existing_appointment_id"] = altro["id"]
    raise errors.StimaInspectionAlreadyOpen(
        "Esiste gia' un sopralluogo aperto per questa stima", **dati)


# ---------------------------------------------------------------------------
# A30-8: il follow-up di un esito
# ---------------------------------------------------------------------------

FOLLOW_UP_TASK_TYPE = "appointment_followup"
#: Titolo neutro, senza dati personali, quando l'operatore non ne scrive uno.
FOLLOW_UP_DEFAULT_TITLE = "Follow-up appuntamento"


def _riferimenti_follow_up(cur, agency_id, row) -> dict:
    """I riferimenti del task, SOLO dalla riga dell'appuntamento gia' bloccata
    e autorizzata. La stima e' un riferimento morbido (nessuna FK): se non
    esiste piu' nella stessa agenzia non si aggancia."""
    riferimenti = {c: row[c] for c in ("contact_id", "lead_id", "stima_id")}
    if (riferimenti["stima_id"] is not None
            and repository.stima_agency(cur, riferimenti["stima_id"]) != agency_id):
        riferimenti["stima_id"] = None
    return riferimenti


def _prepara_follow_up(cur, agency_id, row, follow_up, db_now):
    """Controlli prima di scrivere: un collegamento CRM e una scadenza futura.
    Restituisce i riferimenti (o None se non c'e' follow-up)."""
    if follow_up is None:
        return None
    riferimenti = _riferimenti_follow_up(cur, agency_id, row)
    if all(v is None for v in riferimenti.values()):
        raise errors.FollowUpRequiresLink(
            "Il follow-up richiede un contatto, un lead o una stima collegati all'appuntamento")
    if follow_up.due_at <= db_now:
        raise errors.FollowUpInPast("La scadenza del follow-up deve essere nel futuro")
    return riferimenti


def _crea_follow_up(cur, agency_id, actor, row, follow_up, riferimenti, *, esito) -> dict:
    """Il task CORE del follow-up, nella transazione della transizione.

    `create_task_with_cursor` sul percorso R-4 con l'autore esplicito: i
    riferimenti vengono dalla riga (esistenza + agenzia derivata dal trigger
    030/033), l'autore deve poter agire in quell'agenzia. Nessuna ricerca
    agent-scoped (D5): chi arriva qui ha gia' superato `_su_riga`.
    """
    agente = row["assigned_user_id"]
    task = create_task_with_cursor(cur, {
        **riferimenti,
        "title": follow_up.title or FOLLOW_UP_DEFAULT_TITLE,
        "description": follow_up.note,
        "task_type": FOLLOW_UP_TASK_TYPE,
        "priority": "normal",
        "status": "open",
        "due_at": follow_up.due_at,
        "completed_at": None,
        # Solo informativo, mai una fonte di autorizzazione (D3).
        "assigned_to": repository.agent_name(cur, agency_id, agente) if agente else None,
        "created_by": None,
        "metadata": {"appointment_id": row["id"], "outcome": esito},
    }, created_by_user_id=actor)
    if task["agency_id"] != agency_id:
        # Irraggiungibile: 072 tiene i riferimenti nell'agenzia della riga.
        raise errors.LinkMismatch("Il follow-up non appartiene all'agenzia dell'appuntamento")
    return task


def _extra_evento(outcome_note, task) -> dict:
    extra = {}
    if outcome_note is not None:
        extra["outcome_note"] = outcome_note
    if task is not None:
        extra["follow_up_task_id"] = task["id"]
    return extra


# ---------------------------------------------------------------------------
# CREAZIONE (idempotente)
# ---------------------------------------------------------------------------

def _stesso_valore(salvato, richiesto) -> bool:
    if richiesto is None:
        return salvato is None
    if isinstance(richiesto, datetime):
        if salvato is None:
            return False
        try:
            return datetime.fromisoformat(str(salvato)) == richiesto
        except ValueError:
            return False
    return salvato == richiesto


def _replica(ctx, agency_id, actor, esistente, payload, cur):
    """La chiave esiste gia': replica, oppure riuso vietato. Mai un dato di
    un'altra agenzia o di un altro attore nella risposta."""
    if esistente["agency_id"] != agency_id or esistente["created_by_user_id"] != actor:
        raise errors.IdempotencyKeyReused(
            "Questa richiesta risulta gia' inviata con dati diversi: ricontrolla e conferma")
    impronta = repository.created_event_changes(cur, esistente["id"]) or {}
    for campo in _CAMPI_IMPRONTA:
        if not _stesso_valore(impronta.get(campo), getattr(payload, campo)):
            raise errors.IdempotencyKeyReused(
                "Questa richiesta risulta gia' inviata con dati diversi: ricontrolla e conferma")
    return repository.get_appointment(cur, agency_id, esistente["id"])


def _creazione(ctx, payload):
    """Il lavoro UNICO di una creazione `crm_manual`: restituisce la funzione
    `(cur) -> (riga, replica)`. Chi apre la transazione lo decide il chiamante:
    `create_appointment_idempotent` (la propria) o
    `create_appointment_with_cursor` (quella di chi chiama, A31-3). Stessa
    macchina a stati, stessi permessi, stesso controllo conflitti, stessa
    proiezione, stesso evento, stesso mark dirty Google: il codice e' questo.
    """
    agency_id = ctx.require_agency()
    actor = _attore(ctx)
    chiave = payload.client_request_id
    blocca = payload.status in BLOCKING_STATUSES
    proietta = blocca and projection.is_projectable(payload.appointment_type, payload.stima_id)
    if proietta:
        projection.require_active()

    valori = {
        "agency_id": agency_id,
        "assigned_user_id": payload.assigned_user_id,
        "appointment_type": payload.appointment_type,
        "status": payload.status,
        "start_at": payload.start_at,
        "end_at": payload.end_at,
        "buffer_before_minutes": payload.buffer_before_minutes,
        "buffer_after_minutes": payload.buffer_after_minutes,
        "stima_id": payload.stima_id,
        "contact_id": payload.contact_id,
        "lead_id": payload.lead_id,
        "property_id": payload.property_id,
        "location_text": payload.location_text,
        "notes": payload.notes,
        "source": CRM_SOURCE,
        "source_record_id": chiave,
        "created_by_user_id": actor,
    }
    if payload.status == "confirmed":
        valori["confirmed_at"] = _adesso()

    def _lavoro(cur):
        if chiave is not None:
            esistente = repository.find_by_source_key(cur, CRM_SOURCE, chiave)
            if esistente is not None:
                return _replica(ctx, agency_id, actor, esistente, payload, cur), True
        _controlla_agente(ctx, cur, agency_id, payload.assigned_user_id)
        _controlla_stima(cur, agency_id, payload.stima_id)
        _controlla_collegamenti(cur, agency_id, contact_id=payload.contact_id,
                                lead_id=payload.lead_id, property_id=payload.property_id)
        if blocca:
            repository.lock_agents(cur, [payload.assigned_user_id])
            # Riletta DOPO il lock: due richieste identiche e simultanee per lo
            # stesso agente sono in fila, e la seconda deve vedere la prima
            # come replica, non come conflitto contro se stessa.
            if chiave is not None:
                esistente = repository.find_by_source_key(cur, CRM_SOURCE, chiave)
                if esistente is not None:
                    return _replica(ctx, agency_id, actor, esistente, payload, cur), True
            _occupa(cur, assigned_user_id=payload.assigned_user_id,
                    start_at=payload.start_at, end_at=payload.end_at,
                    before=payload.buffer_before_minutes,
                    after=payload.buffer_after_minutes, gia_bloccato=True)
        riga = repository.insert_appointment(cur, valori, actor_user_id=actor)
        if riga is None:
            # Un'altra transazione ha appena usato la stessa chiave (requested,
            # senza lock d'agente): l'indice unico ha deciso.
            esistente = repository.find_by_source_key(cur, CRM_SOURCE, chiave)
            return _replica(ctx, agency_id, actor, esistente, payload, cur), True
        if proietta:
            projection.on_schedule(cur, agency_id, riga, actor_user_id=actor)
            riga = repository.get_appointment(cur, agency_id, riga["id"])
        # A31-2: CREATE scheduled/confirmed di una visita acquirente con
        # immobile -> la sua proiezione (D1: mai per `requested`).
        _visite.on_create(cur, agency_id, riga, actor_user_id=actor)
        # A30-9B, matrice §18: CREATE gia' `scheduled`/`confirmed` -> mark
        # dirty; una CREATE `requested` non ha bisogno di riga di sync.
        if riga["status"] in _GCAL_PRESENT:
            _gcal.on_appointment_mutation(cur, agency_id, riga["id"])
        return riga, False

    return _lavoro


def create_appointment_idempotent(ctx, payload):
    """Crea un appuntamento `crm_manual`. Restituisce `(riga, replica)`.

    Con `client_request_id` (A30-2 §6): la stessa richiesta ripetuta dallo
    stesso attore restituisce l'appuntamento gia' creato (`replica=True`),
    senza un evento nuovo; la stessa chiave con dati diversi, o di un altro
    attore o agenzia, e' IDEMPOTENCY_KEY_REUSED.
    """
    return _in_transazione(_creazione(ctx, payload))


def create_appointment_with_cursor(ctx, cur, payload):
    """A31-3 - la STESSA creazione sul cursore del chiamante.

    Per i domini che devono scrivere l'appuntamento insieme alle proprie
    righe in UNA transazione (BUY: interazione, match, storico). Nessun
    commit, nessuna connessione: se qualcosa fallisce l'eccezione risale e il
    chiamante annulla tutto. La violazione dell'EXCLUDE diventa lo stesso
    APPOINTMENT_CONFLICT di `_in_transazione`. Restituisce `(riga, replica)`.
    """
    try:
        return _creazione(ctx, payload)(cur)
    except Exception as exc:
        tradotta = _traduci_esclusione(exc)
        if tradotta is not None:
            raise tradotta from exc
        raise


def create_appointment(ctx, payload):
    """Come `create_appointment_idempotent`, restituisce solo la riga (A30-1)."""
    riga, _ = create_appointment_idempotent(ctx, payload)
    return riga


# ---------------------------------------------------------------------------
# A30-12 - BOOKING PUBBLICO: entrypoint AUTOREVOLE dedicato.
#
# CORREZIONE ARCHITETTURALE OBBLIGATORIA del gate A30-12B: NON riusa
# `create_appointment_idempotent` con un `SystemAgencyContext` - quella
# funzione pretende `_attore(ctx)` (un utente reale), forza
# `source='crm_manual'` e applica controlli di assegnazione pensati per un
# operatore autenticato. Il booking pubblico non ha nessuna delle tre cose:
# nessun operatore dietro, `source='booking_link'` sempre, e l'unico
# controllo di assegnazione che ha senso e' "l'agente del link e' ancora un
# membro attivo di questa agenzia".
#
# Ordine obbligatorio (le 14 fasi del gate), tutte nella STESSA transazione:
#   1-2. link e agenzia sono gia' risolti dal chiamante (`public_booking`),
#        che ha gia' verificato token attivo/non scaduto/non revocato PRIMA
#        di aprire questa transazione - un fallimento li' non deve mai
#        toccare un lock d'agente;
#   3.   membership ancora attiva (puo' essere cambiata dopo la creazione
#        del link: si ri-verifica qui, non ci si fida della sola guardia di
#        scrittura del link);
#   4.   lock_agents - stesso namespace/ordine di `_occupa`, cosi' un
#        booking pubblico e una scrittura CRM per lo stesso agente si
#        mettono in fila l'uno dietro l'altro, mai in corsa;
#   5.   RI-LETTURA di orari/eccezioni/chiusure DOPO il lock - la difesa
#        TOCTOU: un GET che mostrava lo slot libero e una chiusura inserita
#        nel frattempo devono produrre un rifiuto, non una prenotazione;
#   6.   D10, HARD: un agente SENZA alcuna riga di orario settimanale non
#        e' "sempre disponibile" (il comportamento permissivo di
#        `is_within` con `effective=None`, corretto per il CRM SOFT) - qui
#        e' l'esatto opposto, zero slot pubblici;
#   7.   find_conflicts, stessa regola EXCLUDE di sempre;
#   8.   risolvi/crea il contatto, sullo STESSO cursore (mai una transazione
#        propria: altrimenti il lock d'agente non protegge piu' l'intera
#        prenotazione);
#   9.   idempotenza sotto lock: un retry dello stesso submission_token
#        trova la riga gia' creata e la restituisce, senza un secondo
#        evento - la garanzia finale resta l'indice UNIQUE
#        (source, source_record_id) della 072, questo e' solo il percorso
#        veloce che evita di tentarlo due volte;
#   10.  INSERT tramite `repository.insert_appointment` - MAI un INSERT
#        scritto qui o nel package `public_booking`: e' questo il solo
#        punto autorevole;
#   11.  l'evento `created` lo scrive gia' `insert_appointment` con
#        `actor_user_id=None` ("NULL = sistema/import", il commento della
#        guardia 072 - un percorso gia' supportato, mai nuovo);
#   12.  collega la riga di `public_booking_submissions` all'appuntamento
#        appena creato - un UPDATE mirato per `submission_hash`, non una
#        chiamata al package `public_booking` (nessuna dipendenza
#        circolare: l'Agenda non importa `public_booking`, ne conosce solo
#        questa singola colonna per questo singolo scopo, come CORE
#        `bridge_public_stima` scrive nel dominio `consent/`);
#   13.  A30-9B, stessa regola di ogni altra CREATE gia' `scheduled`: mark
#        dirty, mai una chiamata Google dentro la transazione;
#   14.  commit - lo gestisce `_in_transazione`, come ogni altra scrittura
#        di questo modulo.
# ---------------------------------------------------------------------------

#: A30-12: la sorgente riservata dalla 072, mai usata prima di questo gate.
PUBLIC_BOOKING_SOURCE = "booking_link"

#: I campi confrontati per decidere se un retry dello stesso
#: submission_token e' "la stessa richiesta" (stesso principio di
#: `_CAMPI_IMPRONTA`, ma per il booking pubblico: qui non c'e' un payload
#: CRM, c'e' uno slot e un'identita' dichiarata dal client).
PUBLIC_BOOKING_CAMPI_IMPRONTA = ("start_at", "end_at", "client_name",
                                 "client_phone", "client_email")


def _contatto_pubblico_coerente(cur, agency_id, contact_data):
    """A30-12 D5: cerca silenziosamente un contatto DI QUESTA agenzia per
    email/telefono normalizzati; se trova un solo match coerente lo
    collega, altrimenti (nessun match, o match ambigui/in conflitto fra
    loro) crea un nuovo contatto. Mai bloccare una prenotazione per
    ambiguita' della rubrica: chi sta prenotando e' una persona reale in
    questo momento.

    Stesso principio di `core.repository.bridge_public_stima` (lock
    consultivo per identita', poi `FOR UPDATE`), ma sul cursore di QUESTA
    transazione: il lock d'agente gia' preso sopra deve restare la sola
    cosa che serializza l'intero booking, e una `core_cursor` separata
    aprirebbe una seconda transazione che quel lock non protegge piu'.

    Restituisce `(contact_id, creato: bool)`.
    """
    sys_ctx = _ContattoScope(agency_id)

    ambiti_identita = []
    if contact_data.get("email_normalized"):
        ambiti_identita.append(f"public_booking:contact:{agency_id}:email:{contact_data['email_normalized']}")
    if contact_data.get("phone_normalized"):
        ambiti_identita.append(f"public_booking:contact:{agency_id}:phone:{contact_data['phone_normalized']}")
    for ambito in sorted(ambiti_identita):
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked", (ambito,))

    email_matches = []
    if contact_data.get("email_normalized"):
        source, params = scoped_source(sys_ctx, "contacts", "c")
        cur.execute(f"SELECT * FROM {source} AND c.email_normalized = %s "
                    f"AND c.status <> 'archived' ORDER BY c.id FOR UPDATE",
                    params + [contact_data["email_normalized"]])
        email_matches = [dict(r) for r in cur.fetchall()]

    phone_matches = []
    if contact_data.get("phone_normalized"):
        source, params = scoped_source(sys_ctx, "contacts", "c")
        cur.execute(f"SELECT * FROM {source} AND c.phone_normalized = %s "
                    f"AND c.status <> 'archived' ORDER BY c.id FOR UPDATE",
                    params + [contact_data["phone_normalized"]])
        phone_matches = [dict(r) for r in cur.fetchall()]

    contatto = None
    if len(email_matches) == 1 and len(phone_matches) <= 1:
        if not phone_matches or phone_matches[0]["id"] == email_matches[0]["id"]:
            contatto = email_matches[0]
    if contatto is None and len(phone_matches) == 1 and not email_matches:
        contatto = phone_matches[0]

    if contatto is not None:
        return contatto["id"], False

    # P29-1.4: le due colonne del consenso NON compaiono in questa INSERT -
    # un contatto nasce SENZA consenso (il loro default e' gia' NULL, mai
    # scritto qui): il booking pubblico non raccoglie alcun consenso
    # marketing, e nominarle esplicitamente e' esattamente il pattern che
    # la guardia di P29-1.5 sorveglia (`LETTORI_LEGITTIMI`).
    cur.execute(
        """INSERT INTO contacts(
            contact_type, first_name, last_name, company_name, display_name,
            email, email_normalized, phone, phone_normalized, secondary_phone,
            source, status, notes,
            agency_id, created_by_user_id
        ) VALUES(
            %(contact_type)s, %(first_name)s, %(last_name)s, %(company_name)s, %(display_name)s,
            %(email)s, %(email_normalized)s, %(phone)s, %(phone_normalized)s, %(secondary_phone)s,
            %(source)s, %(status)s, %(notes)s,
            %(agency_id)s, NULL
        ) RETURNING id""",
        {**contact_data, "agency_id": agency_id},
    )
    return cur.fetchone()["id"], True


class _ContattoScope:
    """Il minimo che `core.scope.scoped_source` richiede (`AgencyScope`),
    per riusare quell'unica funzione di predicato senza costruire un
    `SystemAgencyContext` con un origin che CORE non ammetterebbe - CORE
    conosce solo i propri origin (`SYSTEM_CONTEXT_FUNCTIONS`), e questo
    modulo non e' uno di quelli: legge `contacts` in sola lettura scoped,
    non scrive attraverso `core.repository`."""

    role = None
    is_platform_admin = False
    user_id = None

    def __init__(self, agency_id):
        self.agency_id = agency_id

    def require_agency(self):
        return self.agency_id


def create_public_booking_appointment(ctx, *, link, submission_hash, start_at, contact_data):
    """A30-12: l'unico punto che crea un appuntamento dal booking pubblico.

    `ctx` deve essere un `SystemAgencyContext(origin='public_booking')` -
    rifiutato altrimenti, prima di aprire qualunque cursore (stesso
    principio del guard-before-cursor di `bridge_public_stima`). `link` e'
    la riga di `public_booking_links` gia' risolta e validata (token
    attivo, non scaduto, non revocato) dal chiamante `public_booking`, che
    ha gia' scritto la riga `public_booking_submissions` (con
    `client_ip_hash`) PRIMA di chiamare questa funzione: qui basta il suo
    `submission_hash` per ritrovarla al passo 12.
    """
    if type(ctx) is not SystemAgencyContext or ctx.origin != "public_booking":
        raise TypeError(
            "create_public_booking_appointment richiede un "
            "SystemAgencyContext(origin='public_booking')")
    agency_id = ctx.require_agency()
    assigned_user_id = link["assigned_user_id"]
    end_at = start_at + timedelta(minutes=int(link["duration_minutes"]))
    before = int(link["buffer_before_minutes"])
    after = int(link["buffer_after_minutes"])

    def _lavoro(cur):
        # 3. membership ancora attiva.
        if not repository.active_membership(cur, agency_id, assigned_user_id):
            raise errors.PublicSlotUnavailable(
                "Questo link non e' piu' associato a un agente attivo")
        # 4. lock d'agente.
        repository.lock_agents(cur, [assigned_user_id])
        # 5. ri-lettura DOPO il lock (difesa TOCTOU). Il range passato ad
        # `effective_inputs` va ALLARGATO di un giorno per lato (stessa
        # ragione di `_finestra_effettiva`, CRM SOFT): `exception_date` e
        # `closure_date` sono colonne DATE confrontate con `BETWEEN` contro
        # `start_at`/`end_at` - un appuntamento breve (es. 30 minuti, non a
        # mezzanotte) altrimenti non includerebbe MAI la mezzanotte della
        # propria giornata, e una chiusura/eccezione dello stesso giorno
        # sfuggirebbe silenziosamente al ri-controllo TOCTOU. La FINESTRA
        # su cui si valuta `is_within` resta invece quella esatta,
        # `start_at`/`end_at`: solo le righe recuperate sono piu' ampie.
        ingressi = _wh_repository.effective_inputs(
            cur, agency_id, assigned_user_id,
            (start_at.astimezone(working_hours.ROMA) - timedelta(days=1)).date(),
            (end_at.astimezone(working_hours.ROMA) + timedelta(days=1)).date())
        # 6. D10 HARD: nessun orario configurato = zero slot pubblici,
        # l'ESATTO opposto del ramo permissivo che `is_within` applica per
        # il CRM SOFT quando `effective is None`.
        if not ingressi["has_weekly_config"]:
            raise errors.PublicSlotUnavailable(
                "Questo agente non ha orari pubblici configurati")
        effettiva = working_hours.effective_windows(start_at, end_at, **ingressi)
        if not working_hours.is_within(start_at, end_at, effettiva):
            raise errors.PublicSlotUnavailable(
                "Questo orario e' fuori dalla disponibilita' pubblica dell'agente")
        # 7. find_conflicts.
        conflitti = repository.find_conflicts(
            cur, assigned_user_id=assigned_user_id, start_at=start_at, end_at=end_at,
            buffer_before_minutes=before, buffer_after_minutes=after)
        if conflitti:
            raise errors.PublicSlotUnavailable("Questo orario non e' piu' disponibile")
        # 9a. idempotenza sotto lock, PRIMA di creare un contatto inutile.
        esistente = repository.find_by_source_key(
            cur, PUBLIC_BOOKING_SOURCE, submission_hash)
        if esistente is not None:
            return repository.get_appointment(cur, agency_id, esistente["id"])
        # 8. risolvi/crea il contatto (stesso cursore, stessa transazione).
        contact_id, _creato = _contatto_pubblico_coerente(cur, agency_id, contact_data)
        # 9b. ri-verifica: un'altra richiesta identica potrebbe aver vinto
        # fra la 9a e qui (fra la lettura e la creazione del contatto non
        # c'e' nessun lock che lo impedisca).
        esistente = repository.find_by_source_key(
            cur, PUBLIC_BOOKING_SOURCE, submission_hash)
        if esistente is not None:
            return repository.get_appointment(cur, agency_id, esistente["id"])
        # 10. INSERT tramite il repository autorevole dell'Agenda.
        valori = {
            "agency_id": agency_id,
            "assigned_user_id": assigned_user_id,
            "appointment_type": link["appointment_type"],
            "status": "scheduled",
            "start_at": start_at,
            "end_at": end_at,
            "buffer_before_minutes": before,
            "buffer_after_minutes": after,
            "contact_id": contact_id,
            "source": PUBLIC_BOOKING_SOURCE,
            "source_record_id": submission_hash,
            "created_by_user_id": None,
        }
        riga = repository.insert_appointment(cur, valori, actor_user_id=None)
        if riga is None:
            # L'indice unico ha deciso: un'altra transazione concorrente ha
            # appena inserito la stessa chiave.
            esistente = repository.find_by_source_key(
                cur, PUBLIC_BOOKING_SOURCE, submission_hash)
            return repository.get_appointment(cur, agency_id, esistente["id"])
        # 12. collega la submission all'appuntamento appena creato.
        cur.execute(
            "UPDATE public_booking_submissions "
            "SET appointment_id = %s, status = 'succeeded', completed_at = NOW() "
            "WHERE submission_hash = %s",
            (riga["id"], submission_hash),
        )
        # 13. A30-9B: mark dirty, mai una chiamata Google qui dentro.
        if riga["status"] in _GCAL_PRESENT:
            _gcal.on_appointment_mutation(cur, agency_id, riga["id"])
        return riga

    # 14. commit (o rollback): stessa `_in_transazione` di ogni altra
    # scrittura di questo modulo, con la stessa traduzione delle violazioni
    # EXCLUDE in un errore leggibile.
    return _in_transazione(_lavoro)


# ---------------------------------------------------------------------------
# AZIONI SU UNA RIGA ESISTENTE
# ---------------------------------------------------------------------------

def _su_riga(ctx, appointment_id, azione, versione, lavoro):
    """Blocca la riga, verifica visibilita', ruolo, versione e transizione,
    poi esegue `lavoro(cur, agency_id, actor, row)`. Una transazione."""
    agency_id = ctx.require_agency()
    actor = _attore(ctx)

    def _lavoro(cur):
        row = repository.lock_appointment(cur, agency_id, appointment_id)
        if row is None or not _visibile(ctx, row):
            raise errors.AppointmentNotFound("Appuntamento non trovato")
        if azione in state_machine.MANAGER_ONLY_ACTIONS and not _puo_assegnare(ctx):
            raise errors.ForbiddenRole("Solo owner e admin possono riassegnare un appuntamento")
        if versione is not None and row["version"] != versione:
            raise errors.VersionConflict(
                "Questo appuntamento e' stato modificato da un altro operatore: ricarica",
                current_version=row["version"])
        state_machine.check_transition(azione, row["status"])
        return lavoro(cur, agency_id, actor, row)

    return _in_transazione(_lavoro)


def _agente_presente(row, azione):
    """A30-2P (073): le sole righe senza agente in uno stato aperto sono le
    richieste e i sopralluoghi della facade LMC-15. Confermare, spostare o
    registrare un'assenza su una di queste richiede prima un agente: il
    database non le ammetterebbe (e il cliente riceverebbe un 500)."""
    if row["assigned_user_id"] is None:
        raise errors.AgentRequired(
            f"Per {azione} questo appuntamento scegli prima un agente (riassegna)")


def schedule_appointment(ctx, appointment_id, body):
    """FISSA SOPRALLUOGO / fissa una richiesta: requested -> scheduled.
    L'agente e' obbligatorio ed esplicito (D2)."""
    def lavoro(cur, agency_id, actor, row):
        proietta = projection.is_projectable(row["appointment_type"], row["stima_id"])
        if proietta:
            projection.require_active()
        _controlla_agente(ctx, cur, agency_id, body.assigned_user_id)
        _non_nel_passato(body.start_at)
        _occupa(cur, assigned_user_id=body.assigned_user_id, start_at=body.start_at,
                end_at=body.end_at, before=row["buffer_before_minutes"],
                after=row["buffer_after_minutes"], escluso=row["id"])
        _sopralluogo_unico(ctx, cur, agency_id, appointment_type=row["appointment_type"],
                           stima_id=row["stima_id"], escluso=row["id"])
        nuova = repository.update_appointment(
            cur, row["id"], {"assigned_user_id": body.assigned_user_id,
                             "start_at": body.start_at, "end_at": body.end_at,
                             "status": "scheduled"},
            actor_user_id=actor, event_type="status_changed", from_status=row["status"],
            azione="schedule")
        if proietta:
            projection.on_schedule(cur, agency_id, nuova, actor_user_id=actor)
            nuova = repository.get_appointment(cur, agency_id, row["id"])
        # A31-2: SCHEDULE -> la proiezione nasce (se non c'e' gia').
        _visite.on_status(cur, agency_id, nuova, actor_user_id=actor)
        # A30-9B, matrice §18: SCHEDULE requested->scheduled -> mark dirty.
        _gcal.on_appointment_mutation(cur, agency_id, nuova["id"])
        return nuova
    return _su_riga(ctx, appointment_id, "schedule", body.version, lavoro)


def confirm_appointment(ctx, appointment_id, body):
    def lavoro(cur, agency_id, actor, row):
        if row["status"] == "confirmed":
            return repository.get_appointment(cur, agency_id, row["id"])   # idempotente
        _agente_presente(row, "confermare")
        nuova = repository.update_appointment(
            cur, row["id"], {"status": "confirmed", "confirmed_at": _adesso()},
            actor_user_id=actor, event_type="status_changed", from_status=row["status"],
            azione="confirm")
        # A31-2: CONFIRM -> stato della proiezione `confirmed`.
        _visite.on_status(cur, agency_id, nuova, actor_user_id=actor)
        return nuova
    return _su_riga(ctx, appointment_id, "confirm", body.version, lavoro)


def reassign_appointment(ctx, appointment_id, body):
    """D5 rev. 2: STESSA riga, controllo di disponibilita' del nuovo agente
    (escludendo la riga stessa), evento di audit. Lo stato non cambia: un
    `confirmed` resta `confirmed`. Nessun effetto su LMC-15 (nessun agente)."""
    def lavoro(cur, agency_id, actor, row):
        nuovo = body.assigned_user_id
        if row["assigned_user_id"] == nuovo:
            return repository.get_appointment(cur, agency_id, row["id"])
        _controlla_agente(ctx, cur, agency_id, nuovo)
        if row["status"] in BLOCKING_STATUSES:
            repository.lock_agents(cur, [row["assigned_user_id"], nuovo])
            _occupa(cur, assigned_user_id=nuovo, start_at=row["start_at"],
                    end_at=row["end_at"], before=row["buffer_before_minutes"],
                    after=row["buffer_after_minutes"], escluso=row["id"],
                    gia_bloccato=True)
        nuova = repository.update_appointment(
            cur, row["id"], {"assigned_user_id": nuovo}, actor_user_id=actor,
            event_type="updated", from_status=row["status"], azione="reassign")
        # A31-2 D3: REASSIGN -> solo lo snapshot `assigned_to` della proiezione.
        _visite.on_reassign(cur, agency_id, nuova)
        # A30-9B, matrice §18/§22: REASSIGN -> mark dirty (l'evento si sposta
        # dal calendario del vecchio agente a quello del nuovo).
        _gcal.on_appointment_mutation(cur, agency_id, nuova["id"])
        return nuova
    return _su_riga(ctx, appointment_id, "reassign", body.version, lavoro)


def reschedule_appointment(ctx, appointment_id, payload):
    """Sposta un appuntamento: la riga vecchia diventa `rescheduled` (non
    blocca piu'), ne nasce una nuova collegata con `rescheduled_from_id`.
    Tutto in una transazione: o entrambe, o nessuna. `version` e' facoltativa
    per il service (A30-1) e obbligatoria per la API (`RescheduleBody`)."""
    versione = getattr(payload, "version", None)

    def lavoro(cur, agency_id, actor, vecchia):
        # A30-8 D6: come `schedule` (A30-7 D4), istanti con fuso contro
        # l'orologio del service.
        if payload.start_at < _adesso():
            raise errors.RescheduleInPast(
                "Il nuovo orario e' gia' passato: scegli un orario futuro")
        proietta = projection.is_projectable(vecchia["appointment_type"], vecchia["stima_id"])
        if proietta:
            projection.require_active()
        agente = (payload.assigned_user_id if payload.assigned_user_id is not None
                  else vecchia["assigned_user_id"])
        if agente is None:
            _agente_presente(vecchia, "spostare")
        _controlla_agente(ctx, cur, agency_id, agente)
        # ...poi gli agenti: il vecchio (che si libera) e il nuovo, in ordine
        # crescente, dentro `lock_agents`.
        repository.lock_agents(cur, [vecchia["assigned_user_id"], agente])
        _occupa(cur, assigned_user_id=agente, start_at=payload.start_at,
                end_at=payload.end_at, before=vecchia["buffer_before_minutes"],
                after=vecchia["buffer_after_minutes"], escluso=appointment_id,
                gia_bloccato=True)
        repository.mark_rescheduled(cur, appointment_id, from_status=vecchia["status"],
                                    actor_user_id=actor)
        valori = {c: vecchia[c] for c in (
            "agency_id", "appointment_type", "buffer_before_minutes",
            "buffer_after_minutes", "stima_id", "contact_id", "lead_id",
            "property_id", "location_text", "notes")}
        valori.update({
            "assigned_user_id": agente,
            "status": "scheduled",
            "start_at": payload.start_at,
            "end_at": payload.end_at,
            # Lo spostamento resta nel mondo del predecessore: una riga di
            # prova genera una riga di prova della stessa corsa (Q7). Il
            # database lo pretende comunque.
            "source": ("a30_test" if vecchia["source"] == "a30_test" else CRM_SOURCE),
            "test_run_id": vecchia["test_run_id"],
            "rescheduled_from_id": appointment_id,
            "created_by_user_id": actor,
        })
        nuova = repository.insert_appointment(cur, valori, actor_user_id=actor)
        if proietta:
            projection.on_reschedule(cur, agency_id, vecchia, nuova)
            nuova = repository.get_appointment(cur, agency_id, nuova["id"])
        # A31-2: RESCHEDULE -> la STESSA visita passa al successore.
        _visite.on_reschedule(cur, agency_id, vecchia, nuova, actor_user_id=actor)
        # CRM-OPS-3: l'acquisizione segue la riga nuova.
        _acquisizioni.on_reschedule(cur, agency_id, vecchia, nuova, actor_user_id=actor)
        # A30-9B, matrice §18/§21: RESCHEDULE -> stessa catena, mark dirty
        # sulla riga viva; l'id evento remoto non cambia (deterministico sulla
        # radice della catena), il worker aggiorna lo STESSO evento Google.
        _gcal.on_appointment_mutation(cur, agency_id, nuova["id"])
        return nuova

    return _su_riga(ctx, appointment_id, "reschedule", versione, lavoro)


def _annulla(cur, agency_id, actor, row, *, reason, kind, follow_up=None):
    """L'annullamento, sulla riga gia' bloccata dal chiamante. UNA sola
    implementazione per l'annullamento reale e per "creato per errore"
    (DELETE-ARCH Fase 1A): stessa transizione, stessa proiezione LMC-15,
    stesse visite (A31-2), stesso evento d'acquisizione (CRM-OPS-3), stessa
    marcatura Google (A30-9B); i promemoria (A32) leggono lo stato e si
    fermano da soli. Nessuna DELETE: cambia solo la qualifica
    (`cancelled_kind`), e con `mistake` la riga esce dall'Agenda normale.

    A30-2P F9: `cancelled_at` e' il NOW() del DATABASE della STESSA
    transazione. LMC-15 registra `cancelled_at = NOW()` nella stessa
    transazione, quindi i due istanti coincidono per costruzione (Q10
    `cancelled_at_diverso` = 0). Regole invariate: motivo obbligatorio per
    un sopralluogo con stima, state machine."""
    state_machine.check_transition("cancel", row["status"])
    if kind is not None and kind not in CANCELLED_KINDS:
        raise ValidationError("Tipo di annullamento non ammesso")
    # REVIEW 1: `kind` assente = `agency`; con la 084 assente `{}` (la
    # colonna non c'e', annullamento come prima) e `mistake` e' rifiutato.
    qualifica = repository.cancelled_kind_changes(cur, kind)
    if not qualifica and kind == MISTAKE_KIND:
        raise errors.MistakesNotInstalled(
            "«Creato per errore» non e' ancora disponibile: manca l'aggiornamento del database")
    proiettato = projection.is_projectable(row["appointment_type"], row["stima_id"])
    if proiettato and not reason:
        raise errors.ReasonRequired(
            "Per annullare un sopralluogo legato a una stima serve il motivo")
    db_now = repository.db_now(cur)
    riferimenti = _prepara_follow_up(cur, agency_id, row, follow_up, db_now)
    if row["stima_inspection_id"] is not None:
        projection.on_cancel(cur, agency_id, row, reason=reason, actor_user_id=actor)
    task = (None if riferimenti is None else
            _crea_follow_up(cur, agency_id, actor, row, follow_up, riferimenti,
                            esito="cancelled"))
    cambi = {"status": "cancelled", "cancelled_at": db_now, "cancelled_reason": reason,
             **qualifica}
    nuova = repository.update_appointment(
        cur, row["id"], cambi,
        actor_user_id=actor, event_type="status_changed", from_status=row["status"],
        azione="cancel", event_extra=_extra_evento(None, task))
    # A31-2: CANCEL -> stato della proiezione `cancelled`.
    _visite.on_status(cur, agency_id, nuova, actor_user_id=actor)
    # CRM-OPS-3: CANCEL -> evento sull'acquisizione, che resta aperta.
    _acquisizioni.on_status(cur, agency_id, nuova, actor_user_id=actor)
    # A30-9B, matrice §18: CANCEL -> mark dirty (l'evento remoto, se
    # esiste, va rimosso).
    _gcal.on_appointment_mutation(cur, agency_id, nuova["id"])
    return nuova


def cancel_locked(cur, agency_id, actor, row, *, reason, kind):
    """DELETE-ARCH Fase 1A: l'annullamento chiamato dalle Acquisizioni nella
    LORO transazione, sulla riga che hanno gia' bloccato (ordine dei lock:
    appuntamento -> acquisizione, lo stesso degli hook). Nessun follow-up."""
    return _annulla(cur, agency_id, actor, row, reason=reason, kind=kind)


def cancel_appointment(ctx, appointment_id, body):
    """`kind` (DELETE-ARCH Fase 1A): 'client', 'agency' o 'mistake'
    («Creato per errore»); assente = come prima."""
    def lavoro(cur, agency_id, actor, row):
        return _annulla(cur, agency_id, actor, row, reason=body.reason, kind=body.kind,
                        follow_up=body.follow_up)
    return _su_riga(ctx, appointment_id, "cancel", body.version, lavoro)


def complete_appointment(ctx, appointment_id, body):
    """D11 rev. 2 + Q3: solo da `start_at`; `completed_at` e' il momento reale,
    dichiarato o - se assente - il NOW() del DATABASE, mai `start_at` d'ufficio.

    Correzione A del gate A30-2: l'"adesso" di questa mutazione e' il NOW()
    della STESSA transazione in cui si scrivono l'appuntamento e (a proiezione
    accesa) `stima_inspections`. LMC-15 registra `completed_recorded_at` con
    lo stesso NOW(), quindi `completed_at <= completed_recorded_at` vale per
    costruzione: nessuno skew d'orologio da tollerare, nessun valore corretto
    in silenzio. A30-8: nota di esito nell'evento, follow-up facoltativo."""
    def lavoro(cur, agency_id, actor, row):
        db_now = repository.db_now(cur)
        state_machine.check_time_guard("complete", start_at=row["start_at"],
                                       end_at=row["end_at"], now=db_now)
        quando = state_machine.resolve_completed_at(
            start_at=row["start_at"], declared=body.completed_at, db_now=db_now)
        riferimenti = _prepara_follow_up(cur, agency_id, row, body.follow_up, db_now)
        if row["stima_inspection_id"] is not None:
            projection.on_complete(cur, agency_id, row, completed_at=quando, actor_user_id=actor)
        task = (None if riferimenti is None else
                _crea_follow_up(cur, agency_id, actor, row, body.follow_up, riferimenti,
                                esito="completed"))
        nuova = repository.update_appointment(
            cur, row["id"], {"status": "completed", "completed_at": quando},
            actor_user_id=actor, event_type="status_changed", from_status=row["status"],
            azione="complete", event_extra=_extra_evento(body.outcome_note, task))
        # A31-2: COMPLETE -> stato `completed` (+ updated_at, FLOW-R007).
        # D4: la nota di esito resta nell'evento, mai in outcome/feedback.
        _visite.on_status(cur, agency_id, nuova, actor_user_id=actor)
        # CRM-OPS-3: COMPLETE -> appointment_set diventa inspection_done.
        _acquisizioni.on_status(cur, agency_id, nuova, actor_user_id=actor)
        return nuova
    return _su_riga(ctx, appointment_id, "complete", body.version, lavoro)


def no_show_appointment(ctx, appointment_id, body):
    """D11 rev. 2: l'assenza si registra solo da `end_at` in poi (regola
    invariata). A30-2P F9: UN solo orologio, il NOW() del DATABASE della
    STESSA transazione, sia per la guardia sia per `no_show_at` - lo stesso
    istante che LMC-15 registra come `cancelled_at` della riga proiettata
    (Q10 `no_show_at_diverso` = 0). Cosi' `no_show_at >= end_at` vale per
    costruzione, anche se l'orologio del processo e' avanti di qualche
    millisecondo."""
    def lavoro(cur, agency_id, actor, row):
        _agente_presente(row, "registrare l'assenza su")
        db_now = repository.db_now(cur)
        state_machine.check_time_guard("no_show", start_at=row["start_at"],
                                       end_at=row["end_at"], now=db_now)
        riferimenti = _prepara_follow_up(cur, agency_id, row, body.follow_up, db_now)
        if row["stima_inspection_id"] is not None:
            projection.on_no_show(cur, agency_id, row, actor_user_id=actor)
        task = (None if riferimenti is None else
                _crea_follow_up(cur, agency_id, actor, row, body.follow_up, riferimenti,
                                esito="no_show"))
        nuova = repository.update_appointment(
            cur, row["id"], {"status": "no_show", "no_show_at": db_now},
            actor_user_id=actor, event_type="status_changed", from_status=row["status"],
            azione="no_show", event_extra=_extra_evento(body.outcome_note, task))
        # A31-2: NO_SHOW -> stato della proiezione `no_show` (D4 come sopra).
        _visite.on_status(cur, agency_id, nuova, actor_user_id=actor)
        # CRM-OPS-3: NO_SHOW -> evento sull'acquisizione, che resta aperta.
        _acquisizioni.on_status(cur, agency_id, nuova, actor_user_id=actor)
        return nuova
    return _su_riga(ctx, appointment_id, "no_show", body.version, lavoro)


#: A30-9B, matrice §18: i SOLI campi di un PATCH che sono esportati verso
#: Google. `PatchBody` (appointments/schemas.py) oggi non ne consente
#: nessuno (solo note, luogo e collegamenti): il controllo resta comunque
#: esplicito, cosi' un futuro campo temporale/agente nel PATCH non passerebbe
#: silenziosamente senza mark dirty.
_GCAL_RELEVANT_PATCH_FIELDS = frozenset({"appointment_type", "start_at", "end_at",
                                         "assigned_user_id"})


def patch_appointment(ctx, appointment_id, body):
    """Solo note, luogo e collegamenti; mai orari, agente, tipo o stato."""
    cambi = body.changes()

    def lavoro(cur, agency_id, actor, row):
        if not cambi:
            return repository.get_appointment(cur, agency_id, row["id"])
        finale = {c: cambi.get(c, row[c]) for c in ("contact_id", "lead_id", "property_id")}
        _controlla_collegamenti(cur, agency_id, **finale)
        # A31-2 D5: l'immobile di una visita gia' proiettata non cambia (409,
        # prima di qualunque scrittura).
        _visite.before_patch(cur, agency_id, row, cambi)
        # CRM-OPS-3: idem per l'appuntamento di un'acquisizione.
        _acquisizioni.before_patch(cur, agency_id, row, cambi)
        nuova = repository.update_appointment(
            cur, row["id"], cambi, actor_user_id=actor, event_type="updated",
            from_status=row["status"], azione="patch")
        # A31-2: PATCH contatto/lead -> allineati nella proiezione.
        _visite.on_patch(cur, agency_id, row, nuova, actor_user_id=actor)
        # A30-9B, matrice §18: mark dirty SOLO se il PATCH ha cambiato un
        # campo esportato verso Google; un PATCH di sole note/luogo/
        # collegamenti non tocca la sincronizzazione.
        if _GCAL_RELEVANT_PATCH_FIELDS & cambi.keys():
            _gcal.on_appointment_mutation(cur, agency_id, nuova["id"])
        return nuova
    return _su_riga(ctx, appointment_id, "patch", body.version, lavoro)


# ---------------------------------------------------------------------------
# LETTURE
# ---------------------------------------------------------------------------

def _valida_elenco(valori, ammessi, nome):
    if valori is None:
        return None
    sbagliati = [v for v in valori if v not in ammessi]
    if sbagliati:
        raise ValidationError(f"{nome} non ammesso: {', '.join(sbagliati)}")
    return list(valori)


#: A30-5: quanti risultati al massimo e quanto testo si accetta.
STIMA_LOOKUP_MAX = 20
STIMA_LOOKUP_MIN_CHARS = 2
STIMA_LOOKUP_MAX_CHARS = 100


def lookup_stime(ctx, *, search=None, lead_id=None, contact_id=None, limit=10):
    """A30-5: le stime selezionabili per un appuntamento, SOLO dell'agenzia
    della sessione. Sola lettura: nessuna scrittura, nessun evento.

    Senza criteri (niente testo, niente lead o cliente) non si elenca l'intero
    archivio: la risposta e' vuota. `stima_id` resta poi verificato alla
    creazione (`_controlla_stima`): questa ricerca serve a scegliere, non e'
    la garanzia.
    """
    agency_id = ctx.require_agency()
    _attore(ctx)
    testo = (search or "").strip()
    if len(testo) > STIMA_LOOKUP_MAX_CHARS:
        raise ValidationError(f"search: al massimo {STIMA_LOOKUP_MAX_CHARS} caratteri")
    if testo and len(testo) < STIMA_LOOKUP_MIN_CHARS:
        testo = ""
    if not (1 <= int(limit) <= STIMA_LOOKUP_MAX):
        raise ValidationError(f"limit: tra 1 e {STIMA_LOOKUP_MAX}")
    if not testo and lead_id is None and contact_id is None:
        return []
    with core_cursor() as (_, cur):
        return repository.lookup_stime(cur, agency_id, search=testo or None, lead_id=lead_id,
                                       contact_id=contact_id, limit=int(limit))


def list_agents(ctx):
    agency_id = ctx.require_agency()
    io = _attore(ctx)
    with core_cursor() as (_, cur):
        agenti = repository.agents(cur, agency_id)
    return [{**a, "is_me": a["id"] == io} for a in agenti]


def _luogo(r):
    return r.get("location_text") or r.get("property_city")


def calendar(ctx, *, date_from, date_to, agent_ids=None, types=None, statuses=None,
             show_colleagues=True, mistakes=False):
    """UNA vista aggregata per l'intervallo (A30-4): appuntamenti visibili e,
    per un agent, gli impegni dei colleghi come "Occupato" (D4). NESSUNA
    visita acquirente: `property_visits` resta fuori da A30-2."""
    agency_id = ctx.require_agency()
    io = _attore(ctx)
    if date_to <= date_from:
        raise ValidationError("L'intervallo deve terminare dopo il suo inizio")
    if date_to - date_from > MAX_CALENDAR_RANGE:
        raise errors.RangeTooLarge("L'intervallo del calendario e' al massimo di 42 giorni")
    types = _valida_elenco(types, APPOINTMENT_TYPES, "Tipo")
    # DELETE-ARCH Fase 1A: `mistakes=True` e' il filtro esplicito «Creati per
    # errore» (solo quelli, annullati); senza, quelle righe non compaiono mai.
    statuses = _valida_elenco(statuses, APPOINTMENT_STATUSES, "Stato") or list(
        ("cancelled",) if mistakes else DEFAULT_CALENDAR_STATUSES)
    solo = _solo_agente(ctx)
    with core_cursor() as (_, cur):
        righe = repository.calendar_rows(
            cur, agency_id=agency_id, date_from=date_from, date_to=date_to,
            statuses=statuses, types=types, agent_ids=agent_ids, only_agent_id=solo,
            mistakes=mistakes)
        occupati = []
        if solo is not None and show_colleagues:
            occupati = repository.colleague_busy_rows(
                cur, agency_id=agency_id, date_from=date_from, date_to=date_to,
                viewer_id=io, agent_ids=agent_ids)
        agenti = repository.agents(cur, agency_id)
    items = [{
        "kind": "appointment", "id": r["id"], "status": r["status"],
        "type": r["appointment_type"], "start_at": r["start_at"], "end_at": r["end_at"],
        "agent_id": r["assigned_user_id"], "agent_name": r["agent_name"],
        "version": r["version"], "contact_name": r["contact_name"], "place": _luogo(r),
        "is_request": r["status"] == "requested", "source": r["source"],
        "is_test": r["source"] == "a30_test", "readonly": False,
        "stima_id": r["stima_id"], "contact_id": r["contact_id"],
        "lead_id": r["lead_id"], "property_id": r["property_id"],
        "cancelled_kind": r.get("cancelled_kind"),
    } for r in righe]
    items += [{
        "kind": "busy", "agent_id": b["assigned_user_id"], "agent_name": b["agent_name"],
        "start_at": b["start_at"], "end_at": b["end_at"], "label": "Occupato",
        "readonly": True,
    } for b in occupati]
    items.sort(key=lambda x: (x["start_at"], x.get("agent_id") or 0))
    return {
        "range": {"from": date_from, "to": date_to, "timezone": "Europe/Rome"},
        "agents": [{**a, "is_me": a["id"] == io} for a in agenti],
        "items": items,
    }


def list_appointments(ctx, *, statuses=None, types=None, stima_id=None, lead_id=None,
                      contact_id=None, property_id=None, date_from=None, date_to=None,
                      limit=50, offset=0, mistakes=False):
    agency_id = ctx.require_agency()
    _attore(ctx)
    statuses = _valida_elenco(statuses, APPOINTMENT_STATUSES, "Stato") or list(
        ("cancelled",) if mistakes else DEFAULT_LIST_STATUSES)
    types = _valida_elenco(types, APPOINTMENT_TYPES, "Tipo")
    limit = max(1, min(int(limit), MAX_LIST_LIMIT))
    with core_cursor() as (_, cur):
        return repository.list_appointments(
            cur, agency_id=agency_id, statuses=statuses, types=types, stima_id=stima_id,
            lead_id=lead_id, contact_id=contact_id, property_id=property_id,
            date_from=date_from, date_to=date_to, only_agent_id=_solo_agente(ctx),
            limit=limit, offset=max(0, int(offset)), mistakes=mistakes)


def get_appointment_detail(ctx, appointment_id):
    """Il pannello: la riga, i riepiloghi collegati in sola lettura, le azioni
    e i collegamenti ammessi. D9 rev. 2: niente link a stima e lead."""
    agency_id = ctx.require_agency()
    io = _attore(ctx)
    with core_cursor() as (_, cur):
        row = repository.get_appointment(cur, agency_id, appointment_id)
        if row is None or not _visibile(ctx, row):
            raise errors.AppointmentNotFound("Appuntamento non trovato")
        collegati = repository.detail_links(cur, row)
    azioni = state_machine.allowed_actions(
        row, is_manager=_puo_assegnare(ctx), is_own=row["assigned_user_id"] == io,
        now=_adesso())
    if (projection.is_projectable(row["appointment_type"], row["stima_id"])
            and not projection.PROJECTION_ENABLED):
        # A proiezione spenta (arresto d'emergenza) questi comandi
        # fallirebbero: non si offrono.
        azioni = [a for a in azioni if a not in ("schedule", "reschedule", "complete",
                                                 "no_show")]
    link = []
    if collegati["contact"] is not None:
        link.append("contact")
    if collegati["property"] is not None:
        link.append("property")
    return {"appointment": row, **collegati, "allowed_actions": azioni, "allowed_links": link}


def list_events(ctx, appointment_id):
    agency_id = ctx.require_agency()
    _attore(ctx)
    with core_cursor() as (_, cur):
        row = repository.get_appointment(cur, agency_id, appointment_id)
        if row is None or not _visibile(ctx, row):
            raise errors.AppointmentNotFound("Appuntamento non trovato")
        return repository.list_events(cur, appointment_id)


def _redigi_conflitti(ctx, agente, conflitti):
    """Un agent che guarda la disponibilita' di un collega vede solo
    "Occupato", mai il tipo o l'id dell'appuntamento altrui (D4)."""
    if _puo_assegnare(ctx) or agente == _attore(ctx):
        return conflitti
    return [{"start_at": c["start_at"], "end_at": c["end_at"], "label": "Occupato"}
            for c in conflitti]


def _finestra_effettiva(cur, agency_id, assigned_user_id, date_from, date_to):
    """A30-11 (D2, vincolo SOFT): gli intervalli UTC in cui l'agente lavora
    davvero in `[date_from, date_to)`, o `None` se non ha alcuna
    configurazione (D1: legacy, nessun vincolo). Sola lettura: mai chiamata
    dal percorso di scrittura (`_occupa` resta esattamente come prima)."""
    ingressi = _wh_repository.effective_inputs(
        cur, agency_id, assigned_user_id,
        (date_from.astimezone(working_hours.ROMA) - timedelta(days=1)).date(),
        (date_to.astimezone(working_hours.ROMA) + timedelta(days=1)).date())
    return working_hours.effective_windows(
        date_from, date_to, has_weekly_config=ingressi["has_weekly_config"],
        weekly_rows=ingressi["weekly_rows"], exception_rows=ingressi["exception_rows"],
        closure_rows=ingressi["closure_rows"])


def _alternative_effettiva(cur, *, agency_id, assigned_user_id, start_at, end_at, before, after,
                           escluso=None):
    """Come `_alternative`, ma filtrata sull'availability EFFETTIVA (A30-11,
    D2): usata SOLO da `availability_check` e da `availability_slots`, mai
    dal percorso di scrittura (`_occupa` chiama ancora `_alternative`,
    invariata). Un agente legacy (nessuna configurazione) non e' filtrato:
    `_finestra_effettiva` restituisce `None` e `is_within` e' sempre vero."""
    durata = int((end_at - start_at).total_seconds() // 60)
    orizzonte_fine = start_at + availability.ALTERNATIVES_HORIZON + timedelta(days=1)
    occupato = repository.busy_intervals(
        cur, assigned_user_id=assigned_user_id, date_from=start_at,
        date_to=orizzonte_fine, exclude_appointment_id=escluso)
    grezze = availability.alternatives(
        start_at, duration=durata, busy=occupato, buffer_before=before, buffer_after=after,
        count=availability.ALTERNATIVES_COUNT * 4)
    effettiva = _finestra_effettiva(cur, agency_id, assigned_user_id, start_at, orizzonte_fine)
    filtrate = [a for a in grezze
                if working_hours.is_within(a["start_at"], a["end_at"], effettiva)]
    return filtrate[:availability.ALTERNATIVES_COUNT]


def availability_slots(ctx, *, assigned_user_id, date_from, date_to, duration, step=30,
                       buffer_before_minutes=0, buffer_after_minutes=0,
                       exclude_appointment_id=None):
    """Gli slot liberi/occupati di un agente nella finestra RICHIESTA (D7:
    nessun orario lavorativo IMPOSTO dal chiamante - resta la finestra a
    decidere l'ambito). Ogni slot porta anche `within_working_hours` (A30-11,
    campo ADDITIVO): l'orario EFFETTIVO dell'agente, se configurato, `True`
    per un agente legacy (D1). `available` resta cio' che era: libero da
    conflitti con altri appuntamenti, invariato per compatibilita'. Sola
    lettura, nessun lock."""
    agency_id = ctx.require_agency()
    _attore(ctx)
    for buffer in (buffer_before_minutes, buffer_after_minutes):
        if not 0 <= buffer <= 240:
            raise ValidationError("I buffer vanno da 0 a 240 minuti")
    if date_to <= date_from:
        raise ValidationError("La finestra deve terminare dopo il suo inizio")
    with core_cursor() as (_, cur):
        if not repository.active_membership(cur, agency_id, assigned_user_id):
            raise errors.AgentNotActive("L'agente indicato non e' un membro attivo di questa agenzia")
        occupato = repository.busy_intervals(
            cur, assigned_user_id=assigned_user_id, date_from=date_from, date_to=date_to,
            exclude_appointment_id=exclude_appointment_id)
        effettiva = _finestra_effettiva(cur, agency_id, assigned_user_id, date_from, date_to)
    try:
        slot = availability.slots(date_from, date_to, duration=duration, step=step,
                                  busy=occupato, buffer_before=buffer_before_minutes,
                                  buffer_after=buffer_after_minutes)
    except ValueError as exc:
        if "finestra" in str(exc):
            raise errors.RangeTooLarge(
                "La finestra di disponibilita' e' al massimo di 7 giorni") from exc
        raise ValidationError(str(exc)) from exc
    for s in slot:
        s["within_working_hours"] = working_hours.is_within(s["start_at"], s["end_at"], effettiva)
    return {"assigned_user_id": assigned_user_id, "from": date_from, "to": date_to,
            "duration": duration, "step": step, "timezone": "Europe/Rome", "slots": slot}


def availability_check(ctx, body):
    """Verifica un intervallo esatto: disponibile, conflitti e alternative.

    A30-11 (D2, vincolo SOFT): `available` ora richiede ANCHE che l'agente
    lavori in quell'orario (se configurato; D1: un agente legacy non ne e'
    toccato). `within_working_hours` (campo ADDITIVO) distingue le due
    cause: un `False` con `conflicts` vuoti e' "fuori orario", non
    "occupato". `alternatives` cerca solo dentro l'orario effettivo."""
    agency_id = ctx.require_agency()
    _attore(ctx)
    with core_cursor() as (_, cur):
        if not repository.active_membership(cur, agency_id, body.assigned_user_id):
            raise errors.AgentNotActive("L'agente indicato non e' un membro attivo di questa agenzia")
        conflitti = repository.find_conflicts(
            cur, assigned_user_id=body.assigned_user_id, start_at=body.start_at,
            end_at=body.end_at, buffer_before_minutes=body.buffer_before_minutes,
            buffer_after_minutes=body.buffer_after_minutes,
            exclude_appointment_id=body.exclude_appointment_id)
        effettiva = _finestra_effettiva(cur, agency_id, body.assigned_user_id,
                                        body.start_at, body.end_at)
        dentro_orario = working_hours.is_within(body.start_at, body.end_at, effettiva)
        alternative = []
        if conflitti or not dentro_orario:
            alternative = _alternative_effettiva(
                cur, agency_id=agency_id, assigned_user_id=body.assigned_user_id,
                start_at=body.start_at, end_at=body.end_at, before=body.buffer_before_minutes,
                after=body.buffer_after_minutes, escluso=body.exclude_appointment_id)
    return {"available": not conflitti and dentro_orario,
            "within_working_hours": dentro_orario,
            "conflicts": _redigi_conflitti(ctx, body.assigned_user_id, conflitti),
            "alternatives": alternative}


def find_conflicts(ctx, *, assigned_user_id, start_at, end_at,
                   buffer_before_minutes=0, buffer_after_minutes=0,
                   exclude_appointment_id=None):
    """Sola lettura (A30-1): cosa occupa l'agente in quell'intervallo."""
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        if not repository.active_membership(cur, agency_id, assigned_user_id):
            raise NotFoundError("Agente non trovato")
        return repository.find_conflicts(
            cur, assigned_user_id=assigned_user_id, start_at=start_at, end_at=end_at,
            buffer_before_minutes=buffer_before_minutes,
            buffer_after_minutes=buffer_after_minutes,
            exclude_appointment_id=exclude_appointment_id)
