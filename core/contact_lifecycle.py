"""CESTINO-CONTATTI-1 (FASE F) - il Cestino dei CONTATTI.

Stesso impianto del Cestino Immobili (`property/lifecycle.py`, DELETE-ARCH
2B1/2B3): `contacts.deleted_*` (migration 090), il registro append-only
`record_lifecycle_events` (entita' `contact`), le guardie della 090 nel
database. Nessun sistema parallelo, nessuna cancellazione fisica, nessuna
cascata.

COS'E' IL CESTINO. Un contatto inserito per errore, doppio o non valido. Non
sostituisce l'archivio (`status = 'archived'`), la chiusura commerciale
(lead, richieste, acquisizioni) ne' l'anonimizzazione GDPR.

CHI. Lo scope dei contatti (`core/scope.py`): owner, admin e platform admin in
acting su tutta l'agenzia; un `agent` solo sui contatti assegnati a se'. Un
contatto fuori scope e' un 404 (D-6). Un agent si ferma davanti allo STORICO
operativo reale (403 HISTORY_REQUIRES_ADMIN); owner e admin no. Il ripristino:
owner/admin tutto il Cestino dell'agenzia, un agent solo cio' che ha spostato
lui.

BLOCCHI (assoluti, ricalcolati sotto lock): processi aperti del contatto -
opportunita' (lead) aperte, acquisizioni aperte di cui e' referente,
appuntamenti futuri, richieste d'acquisto e proposte aperte, vendite in corso,
task aperti -, la proprieta' di un immobile con incarico, un accesso al portale
proprietario non disattivato. Nessuno di questi viene chiuso, scollegato o
disattivato in automatico: il blocco dice cosa e dove.

L'OPERAZIONE (una transazione): lock della riga, ricontrollo di blocchi e
storico, «Sospendi automazioni» del contatto (lo stesso controllo della scheda
Comunicazioni) e annullamento dei messaggi in coda, `deleted_*`, evento nel
registro. Il ripristino riporta `deleted_*` a NULL e nient'altro: stesso id,
relazioni intatte, automazioni ancora sospese e messaggi annullati restano
tali (si riattivano a mano). Email e telefono non sono unici: nessun conflitto
nel database; i possibili doppioni attivi si SEGNALANO (`possible_duplicates`),
senza fondere ne' sovrascrivere nulla.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from operator_auth import permissions
from operator_auth.dependencies import OWNER_ADMIN_MIN_ROLE

from . import property_mandate as _mandato
from .database import core_cursor
from .exceptions import ConflictError, NotFoundError, PermissionDenied, ValidationError
from .scope import scoped_source

log = logging.getLogger("stima360.lifecycle")

# --- codici: gli stessi del Cestino Immobili dove il significato e' lo stesso
TRASH_BLOCKED = "TRASH_BLOCKED"
ALREADY_DELETED = "ALREADY_DELETED"
NOT_DELETED = "NOT_DELETED"
NOT_DELETED_BY_YOU = "NOT_DELETED_BY_YOU"
INVALID_TRASH_REASON = "INVALID_TRASH_REASON"
HISTORY_REQUIRES_ADMIN = "HISTORY_REQUIRES_ADMIN"
TRASH_NOT_INSTALLED = "TRASH_NOT_INSTALLED"

TRASH_REASONS = ("created_by_mistake", "duplicate", "invalid_data", "test_record", "other")
TRASH_NOTE_MAX = 500
TRASH_LIST_MAX = 200

TRASH_BLOCKED_MESSAGE = "Il contatto ha processi aperti: chiudili prima di spostarlo nel Cestino"
ALREADY_DELETED_MESSAGE = "Il contatto è già nel Cestino"
NOT_DELETED_MESSAGE = "Il contatto non è nel Cestino"
NOT_DELETED_BY_YOU_MESSAGE = ("Puoi ripristinare solo i contatti che hai spostato tu nel Cestino: "
                              "chiedi a un amministratore")
INVALID_TRASH_REASON_MESSAGE = "Motivo non valido"
HISTORY_REQUIRES_ADMIN_MESSAGE = ("Il contatto ha uno storico operativo (attività, appuntamenti, opportunità, "
                                  "messaggi...): può spostarlo nel Cestino solo un amministratore o il titolare")
TRASH_NOT_INSTALLED_MESSAGE = ("Il Cestino dei contatti non è ancora disponibile su questo database "
                               "(migration 090 non applicata)")

#: Stati "aperti" (gli stessi delle guardie di riapertura della 090).
LEAD_OPEN = ("open", "paused")
BUY_OPEN = ("draft", "active", "paused")
BUY_CLOSED = ("satisfied", "closed", "archived")
ACQUISITION_CLOSED = ("acquired", "lost")
APPOINTMENT_OPEN = ("requested", "scheduled", "confirmed")
PROPOSAL_OPEN = ("draft", "submitted")
TASK_OPEN = ("open", "in_progress")
VISIT_OPEN = ("scheduled", "confirmed")
OWNER_ROLES = ("owner", "seller")

_PIPELINE = {"sell": "Vendita", "buy": "Acquisto", "general": "Generale"}
_STATO_LEAD = {"open": "aperta", "paused": "in pausa"}
_STATO_BUY = {"draft": "bozza", "active": "attiva", "paused": "in pausa"}
_STATO_ACQ = {"appointment_set": "appuntamento fissato", "inspection_done": "sopralluogo fatto",
              "valuation_presented": "valutazione presentata", "mandate_negotiation": "trattativa d'incarico"}
_STATO_APP = {"requested": "richiesto", "scheduled": "in agenda", "confirmed": "confermato"}
_STATO_ACCOUNT = {"invited": "invitato", "active": "attivo"}


# ---------------------------------------------------------------------------
# eccezioni con codice (stessa forma di property/lifecycle.py::_ConCodice)
# ---------------------------------------------------------------------------

class _ConCodice:
    code = None

    def __init__(self, message, **extra):
        super().__init__(message)
        self.extra = extra


class ContactLifecycleConflict(_ConCodice, ConflictError):
    def __init__(self, message, code, **extra):
        super().__init__(message, **extra)
        self.code = code


class ContactLifecycleForbidden(_ConCodice, PermissionDenied):
    def __init__(self, message, code, **extra):
        super().__init__(message, **extra)
        self.code = code


class ContactLifecycleInvalid(_ConCodice, ValidationError):
    def __init__(self, message, code, **extra):
        super().__init__(message, **extra)
        self.code = code


class ContactTrashNotInstalled(_ConCodice, ConflictError):
    """503 TRASH_NOT_INSTALLED (codice deployato prima della 090): il router
    lo traduce con il suo codice, prima di leggere o scrivere."""
    code = TRASH_NOT_INSTALLED


# ---------------------------------------------------------------------------
# aiuti
# ---------------------------------------------------------------------------

def _presente(cur, tabella: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS presente", (f"public.{tabella}",))
    return bool(cur.fetchone()["presente"])


def _cestino_installato(cur) -> None:
    cur.execute("SELECT to_regclass('public.record_lifecycle_events') IS NOT NULL"
                "   AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'"
                "                 AND table_name = 'contacts' AND column_name = 'deleted_at') AS pronto")
    if not cur.fetchone()["pronto"]:
        raise ContactTrashNotInstalled(TRASH_NOT_INSTALLED_MESSAGE)


def _vede_tutta_agenzia(ctx) -> bool:
    return permissions.sees_all_agency_records(getattr(ctx, "role", None),
                                               getattr(ctx, "is_platform_admin", False))


def _contatto(cur, ctx, contact_id: int, *, lock: bool = False) -> dict:
    """La riga nello scope di chi chiede (agent: solo i suoi). Fuori scope o
    inesistente: 404, indistinguibili (D-6)."""
    source, params = scoped_source(ctx, "contacts", "c")
    cur.execute(f"SELECT c.* FROM {source} AND c.id = %s{' FOR UPDATE OF c' if lock else ''}",
                params + [contact_id])
    riga = cur.fetchone()
    if riga is None:
        raise NotFoundError(f"contact {contact_id} not found")
    return dict(riga)


def _audit(azione: str, ctx, **campi) -> None:
    riga = {"action": azione, "actor_user_id": getattr(ctx, "user_id", None),
            "actor_role": getattr(ctx, "role", None), "agency_id": getattr(ctx, "agency_id", None),
            "at": datetime.now(timezone.utc).isoformat(), **campi}
    log.info("lifecycle %s", json.dumps(riga, default=str))


def _evento(cur, ctx, agency_id: int, contact_id: int, azione: str, *, reason=None, note=None,
            before: dict, metadata: dict | None = None) -> None:
    meta = {"actor_role": getattr(ctx, "role", None),
            "platform_admin": bool(getattr(ctx, "is_platform_admin", False)), **(metadata or {})}
    cur.execute("INSERT INTO record_lifecycle_events (agency_id, entity_type, entity_id, action, reason_code, note, "
                "actor_user_id, before_state, metadata) VALUES (%s, 'contact', %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)",
                (agency_id, contact_id, azione, reason, note, getattr(ctx, "user_id", None),
                 json.dumps(before, default=str), json.dumps(meta, default=str)))


def _valida_motivo(reason_code, note):
    if reason_code not in TRASH_REASONS:
        raise ContactLifecycleInvalid(INVALID_TRASH_REASON_MESSAGE, INVALID_TRASH_REASON,
                                      allowed=list(TRASH_REASONS))
    if note is not None:
        note = str(note).strip() or None
    if note is not None and len(note) > TRASH_NOTE_MAX:
        raise ContactLifecycleInvalid(f"La nota supera {TRASH_NOTE_MAX} caratteri", INVALID_TRASH_REASON)
    return reason_code, note


def _data(valore) -> str | None:
    return valore.strftime("%d/%m/%Y") if valore is not None else None


def _righe(cur, sql: str, params) -> list[dict]:
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# BLOCCHI: processi aperti (nessuno si chiude da qui)
# ---------------------------------------------------------------------------

def trash_blockers(cur, ctx, agency_id: int, contact: dict) -> list[dict]:
    """Ogni voce: `code`, `label`, `items` (con `label` e, se c'e' una pagina,
    `href` interno della Shell), `link` facoltativo, `action` facoltativa."""
    cid = contact["id"]
    blocchi: list[dict] = []
    scheda = f"#/contatti/{cid}"

    # 1. opportunita' aperte (lead): Venditori per la vendita, la scheda per le altre
    righe = _righe(cur, "SELECT id, pipeline, status, stage FROM leads WHERE agency_id = %s AND contact_id = %s "
                        "AND status = ANY(%s) ORDER BY id", (agency_id, cid, list(LEAD_OPEN)))
    if righe:
        for r in righe:
            r["label"] = f"Lead #{r['id']} · {_PIPELINE.get(r['pipeline'], r['pipeline'])} · {_STATO_LEAD.get(r['status'], r['status'])}"
            r["href"] = "#/venditori" if r["pipeline"] == "sell" else scheda
        vendita = any(r["pipeline"] == "sell" for r in righe)
        blocchi.append({
            "code": "LEAD_OPEN",
            "label": ("Opportunità aperte: chiudile prima (Venditori con «Smetti…» per la vendita, "
                      "tab Lead della scheda per le altre)"),
            "items": righe,
            "link": {"href": "#/venditori", "label": "Apri Venditori"} if vendita
            else {"href": scheda, "label": "Apri la scheda"}})

    # 2. referente di un'acquisizione aperta
    if _presente(cur, "acquisitions"):
        righe = _righe(cur, "SELECT a.id, a.status, a.property_id, p.code AS property_code FROM acquisitions a "
                            "LEFT JOIN properties p ON p.id = a.property_id "
                            "WHERE a.agency_id = %s AND a.owner_contact_id = %s AND a.status <> ALL(%s) ORDER BY a.id",
                       (agency_id, cid, list(ACQUISITION_CLOSED)))
        if righe:
            for r in righe:
                r["label"] = (f"Acquisizione #{r['id']}" + (f" · {r['property_code']}" if r.get("property_code") else "")
                              + f" · {_STATO_ACQ.get(r['status'], r['status'])}")
                r["href"] = f"#/acquisizioni/{r['id']}"
            blocchi.append({"code": "ACQUISITION_OPEN",
                            "label": "Referente di acquisizioni aperte: concludile o cambia prima il referente",
                            "items": righe})

    # 3. appuntamenti futuri ancora aperti
    if _presente(cur, "appointments"):
        righe = _righe(cur, "SELECT id, status, start_at, appointment_type, "
                            "to_char(start_at AT TIME ZONE 'Europe/Rome', 'YYYY-MM-DD') AS giorno FROM appointments "
                            "WHERE agency_id = %s AND contact_id = %s AND status = ANY(%s) AND start_at >= NOW() "
                            "ORDER BY start_at, id", (agency_id, cid, list(APPOINTMENT_OPEN)))
        if righe:
            for r in righe:
                giorno = "/".join(reversed(r["giorno"].split("-")))           # il giorno di Roma
                r["label"] = f"Appuntamento del {giorno} · {_STATO_APP.get(r['status'], r['status'])}"
                r["href"] = f"#/agenda/giorno/{r['giorno']}"
            blocchi.append({"code": "FUTURE_APPOINTMENT",
                            "label": "Appuntamenti futuri in Agenda: annullali o concludili prima",
                            "items": righe})

    # 4. visite in programma registrate fuori Agenda
    righe = _righe(cur, "SELECT v.id, v.property_id, v.scheduled_at, p.code AS property_code FROM property_visits v "
                        "JOIN properties p ON p.id = v.property_id "
                        "WHERE p.agency_id = %s AND v.contact_id = %s AND v.status = ANY(%s) "
                        "AND v.scheduled_at >= NOW() ORDER BY v.scheduled_at, v.id",
                   (agency_id, cid, list(VISIT_OPEN)))
    if righe:
        for r in righe:
            r["label"] = f"Visita del {_data(r['scheduled_at'])} · {r.get('property_code') or ''}".rstrip(" ·")
            r["href"] = f"#/immobili/{r['property_id']}"
        blocchi.append({"code": "VISIT_SCHEDULED",
                        "label": "Visite in programma sugli immobili: annullale o registrane l'esito prima",
                        "items": righe})

    # 5. richieste d'acquisto aperte
    #    CESTINO-RICHIESTE-1: una richiesta gia' nel Cestino non e' un processo
    #    aperto (non e' operativa); per ripristinarla servira' prima il contatto
    righe = _righe(cur, "SELECT id, status, title FROM buy_requests b WHERE agency_id = %s AND contact_id = %s "
                        "AND status = ANY(%s) AND (to_jsonb(b)->>'deleted_at') IS NULL ORDER BY id",
                   (agency_id, cid, list(BUY_OPEN)))
    if righe:
        for r in righe:
            r["label"] = (f"Richiesta #{r['id']}" + (f" · {r['title']}" if r.get("title") else "")
                          + f" · {_STATO_BUY.get(r['status'], r['status'])}")
            r["href"] = f"#/acquirenti/{r['id']}"
        blocchi.append({"code": "BUY_REQUEST_OPEN",
                        "label": "Richieste d'acquisto aperte: chiudile prima dalla scheda Acquirente",
                        "items": righe})

    # 6. proposte d'acquisto aperte
    righe = _righe(cur, "SELECT pp.id, pp.status, mt.buy_request_id, mt.property_id FROM property_proposals pp "
                        "JOIN matches mt ON mt.id = pp.match_id JOIN buy_requests br ON br.id = mt.buy_request_id "
                        "WHERE br.agency_id = %s AND br.contact_id = %s AND pp.status = ANY(%s) ORDER BY pp.id",
                   (agency_id, cid, list(PROPOSAL_OPEN)))
    if righe:
        for r in righe:
            r["label"] = f"Proposta #{r['id']} · richiesta #{r['buy_request_id']}"
            r["href"] = f"#/acquirenti/{r['buy_request_id']}"
        blocchi.append({"code": "PROPOSAL_OPEN",
                        "label": "Proposte d'acquisto in corso: ritirale o concludile prima",
                        "items": righe})

    # 7. vendite in corso (come venditore o come acquirente)
    righe = _righe(cur, "SELECT DISTINCT s.id, s.property_id, p.code AS property_code FROM property_sales s "
                        "JOIN properties p ON p.id = s.property_id "
                        "LEFT JOIN property_sale_sellers ss ON ss.sale_id = s.id "
                        "LEFT JOIN buy_requests br ON br.id = s.buy_request_id "
                        "WHERE p.agency_id = %s AND s.status = 'pending' "
                        "AND (ss.contact_id = %s OR br.contact_id = %s) ORDER BY s.id",
                   (agency_id, cid, cid))
    if righe:
        for r in righe:
            r["label"] = f"Vendita #{r['id']} · {r.get('property_code') or ''}".rstrip(" ·")
            r["href"] = f"#/immobili/{r['property_id']}"
        blocchi.append({"code": "SALE_PENDING", "label": "Vendite in corso: concludile o annullale prima",
                        "items": righe})

    # 8. proprietario di un immobile (non nel Cestino) con incarico, di
    #    qualunque stato (FIX-MANDATE-1): mai scollegato in automatico
    righe = _righe(cur, "SELECT DISTINCT p.id, p.code, p.address, p.civic_number, p.city FROM property_contacts pc "
                        "JOIN properties p ON p.id = pc.property_id "
                        f"WHERE p.agency_id = %s AND pc.contact_id = %s AND pc.role = ANY(%s) "
                        f"AND (to_jsonb(p)->>'deleted_at') IS NULL AND {_mandato.real_mandate_sql('p')} ORDER BY p.id",
                   (agency_id, cid, list(OWNER_ROLES)))
    if righe:
        for r in righe:
            luogo = ", ".join(x for x in (" ".join(y for y in (r.get("address"), r.get("civic_number")) if y),
                                          r.get("city")) if x)
            r["label"] = " · ".join(x for x in (r.get("code"), luogo) if x) or f"Immobile #{r['id']}"
            r["href"] = f"#/immobili/{r['id']}"
        blocchi.append({"code": "MANDATE_OWNER",
                        "label": ("Proprietario di immobili con incarico: se è un doppione, rimuovilo prima "
                                  "dai proprietari dell'immobile (resta l'altro contatto)"),
                        "items": righe})

    # 9. task aperti (da completare o annullare in Attività)
    righe = _righe(cur, "SELECT id, title, status, due_at FROM tasks WHERE agency_id = %s AND contact_id = %s "
                        "AND status = ANY(%s) ORDER BY due_at NULLS LAST, id", (agency_id, cid, list(TASK_OPEN)))
    if righe:
        for r in righe:
            r["label"] = (r.get("title") or f"Task #{r['id']}") + (f" · scade il {_data(r['due_at'])}" if r.get("due_at") else "")
            r["href"] = "#/attivita"
        blocchi.append({"code": "TASK_OPEN",
                        "label": "Task aperti: completali o annullali prima in Attività",
                        "items": righe, "link": {"href": "#/attivita", "label": "Apri Attività"}})

    # 10. accesso al portale proprietario non disattivato: lo si disattiva prima
    #     con «Disattiva» (il comando esistente dell'amministrazione OWNER, che
    #     richiede il titolare), mai in automatico
    if _presente(cur, "owner_accounts"):
        righe = _righe(cur, "SELECT id, status, last_login_at FROM owner_accounts WHERE contact_id = %s "
                            "AND status <> 'disabled' ORDER BY id", (cid,))
        if righe:
            # la stessa soglia della superficie OWNER Admin (require_owner_admin_context)
            puo = (bool(getattr(ctx, "is_platform_admin", False))
                   or getattr(ctx, "role", None) == OWNER_ADMIN_MIN_ROLE)
            for r in righe:
                r["label"] = f"Accesso al portale proprietario · {_STATO_ACCOUNT.get(r['status'], r['status'])}"
            blocchi.append({
                "code": "OWNER_PORTAL_ACTIVE",
                "label": ("Ha un accesso al portale proprietario attivo: disattivalo prima "
                          + ("(«Disattiva accesso»)." if puo else "(lo fa il titolare dell'agenzia).")),
                "items": righe,
                "action": {"kind": "owner_account_disable", "account_id": righe[0]["id"], "allowed": puo,
                           "label": "Disattiva accesso",
                           "endpoint": f"/api/owner/admin/accounts/{righe[0]['id']}/disable"}})
    return blocchi


# ---------------------------------------------------------------------------
# STORICO: per un agent richiede owner/admin; owner/admin non si fermano
# ---------------------------------------------------------------------------

def protected_history(cur, ctx, agency_id: int, contact: dict) -> list[dict]:
    """Lo storico operativo REALE del contatto. Ogni voce: `code`, `label`,
    `count`. NON e' storico: le righe «per errore» (lead e acquisizioni
    `created_by_mistake`, appuntamenti `mistake`, attivita' segnate), le
    attivita' generate dal sistema, i collegamenti agli immobili, i ruoli, i
    consensi (registro legale: resta com'e')."""
    from . import repository as core_repository

    cid = contact["id"]
    voci: list[dict] = []

    def conta(codice, etichetta, sql, params, *, tabelle=()):
        if any(not _presente(cur, t) for t in tabelle):
            return
        cur.execute(f"SELECT count(*) AS n FROM ({sql}) x", params)
        n = cur.fetchone()["n"]
        if n:
            voci.append({"code": codice, "label": etichetta, "count": n})

    tipi = list(core_repository.GENERATED_ACTIVITY_TYPES)
    errore = core_repository.MISTAKE_SQL.format(a="a")
    conta("CONTACT_ACTIVITY", "Attività o interazioni registrate",
          f"SELECT a.id FROM activities a WHERE a.agency_id = %s AND a.contact_id = %s "
          f"AND a.activity_type <> ALL(%s) AND NOT {errore}", (agency_id, cid, tipi))
    conta("LEAD_HISTORY", "Opportunità concluse",
          "SELECT id FROM leads WHERE agency_id = %s AND contact_id = %s AND status = 'closed' "
          "AND lost_reason IS DISTINCT FROM 'created_by_mistake'", (agency_id, cid))
    conta("APPOINTMENT_HISTORY", "Appuntamenti avvenuti, passati o annullati",
          "SELECT id FROM appointments WHERE agency_id = %s AND contact_id = %s "
          "AND cancelled_kind IS DISTINCT FROM 'mistake' "
          "AND NOT (status = ANY(%s) AND start_at >= NOW())", (agency_id, cid, list(APPOINTMENT_OPEN)),
          tabelle=("appointments",))
    conta("ACQUISITION_HISTORY", "Acquisizioni concluse",
          "SELECT id FROM acquisitions WHERE agency_id = %s AND owner_contact_id = %s "
          "AND status = ANY(%s) AND lost_reason IS DISTINCT FROM 'created_by_mistake'",
          (agency_id, cid, list(ACQUISITION_CLOSED)), tabelle=("acquisitions",))
    conta("BUY_REQUEST_HISTORY", "Richieste d'acquisto concluse",
          "SELECT id FROM buy_requests WHERE agency_id = %s AND contact_id = %s AND status = ANY(%s)",
          (agency_id, cid, list(BUY_CLOSED)))
    conta("PROPOSAL_HISTORY", "Proposte d'acquisto concluse",
          "SELECT pp.id FROM property_proposals pp JOIN matches mt ON mt.id = pp.match_id "
          "JOIN buy_requests br ON br.id = mt.buy_request_id "
          "WHERE br.agency_id = %s AND br.contact_id = %s AND pp.status <> ALL(%s)",
          (agency_id, cid, list(PROPOSAL_OPEN)))
    conta("SALE_HISTORY", "Vendite registrate",
          "SELECT DISTINCT s.id FROM property_sales s LEFT JOIN property_sale_sellers ss ON ss.sale_id = s.id "
          "LEFT JOIN buy_requests br ON br.id = s.buy_request_id "
          "WHERE s.status <> 'pending' AND (ss.contact_id = %s OR br.contact_id = %s)", (cid, cid))
    conta("VISIT_HISTORY", "Visite registrate",
          "SELECT id FROM property_visits WHERE contact_id = %s AND NOT (status = ANY(%s) AND scheduled_at >= NOW())",
          (cid, list(VISIT_OPEN)))
    conta("BUYER_INTERACTION_HISTORY", "Interazioni da acquirente",
          "SELECT i.id FROM buy_request_interactions i JOIN buy_requests br ON br.id = i.buy_request_id "
          "WHERE br.agency_id = %s AND br.contact_id = %s", (agency_id, cid))
    # il ledger delle comunicazioni si interroga SOLO dalla sua funzione
    # pubblica (sentinella P29-2.1 n4), con lo scope dell'agenzia
    from communication import repository as _communication
    if _communication.contact_has_message_history(cur, ctx, cid):
        voci.append({"code": "COMMUNICATION_HISTORY", "label": "Messaggi inviati o ricevuti", "count": 1})
    conta("OWNER_PORTAL_HISTORY", "Account del portale proprietario",
          "SELECT id FROM owner_accounts WHERE contact_id = %s", (cid,), tabelle=("owner_accounts",))
    return voci


# ---------------------------------------------------------------------------
# deletion-check / trash / restore / elenco
# ---------------------------------------------------------------------------

def deletion_check(ctx, contact_id: int) -> dict:
    """Si puo' spostare nel Cestino? Sola lettura. `history` e' informativa
    per tutti (lo storico resta consultabile); per un agent lo storico e' un
    blocco (HISTORY_REQUIRES_ADMIN)."""
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _cestino_installato(cur)
        contatto = _contatto(cur, ctx, contact_id)
        storia: list[dict] = []
        if contatto.get("deleted_at") is not None:
            blocchi = [{"code": ALREADY_DELETED, "label": ALREADY_DELETED_MESSAGE, "items": []}]
        else:
            blocchi = trash_blockers(cur, ctx, agency_id, contatto)
            storia = protected_history(cur, ctx, agency_id, contatto)
            if not blocchi and storia and not _vede_tutta_agenzia(ctx):
                blocchi = [{"code": HISTORY_REQUIRES_ADMIN, "label": HISTORY_REQUIRES_ADMIN_MESSAGE, "items": storia}]
            effetti = _effetti_comunicazioni(cur, ctx, contact_id)
    risposta = {"can_trash": not blocchi, "blockers": blocchi, "history": storia}
    if contatto.get("deleted_at") is None:
        risposta["effects"] = effetti
    return risposta


def _effetti_comunicazioni(cur, ctx, contact_id: int) -> dict:
    """Cosa fara' il Cestino sulle comunicazioni (detto PRIMA, nella conferma):
    i messaggi in coda che verranno annullati e le automazioni da sospendere.
    Il ledger e i controlli si interrogano SOLO dalle loro funzioni pubbliche."""
    from communication import journey_repository
    from communication import repository as _communication
    effetti = {"queued_messages": 0, "automations_to_pause": False, "automations_already_paused": False}
    if not _communication.ledger_installed(cur):
        return effetti
    effetti["queued_messages"] = _communication.count_queued_for_contact(cur, ctx, contact_id)
    if journey_repository.schema_ready(cur):
        gia = journey_repository.automations_paused(cur, ctx, contact_id)
        effetti["automations_already_paused"] = gia
        effetti["automations_to_pause"] = not gia
    return effetti


def _sospendi_comunicazioni(cur, ctx, contact_id: int) -> dict:
    """«Sospendi automazioni» (lo stesso servizio della scheda Comunicazioni,
    stesso cursore) e annullamento di TUTTI i messaggi in coda del contatto.
    Una pausa gia' presente resta quella che e'."""
    from communication import journey_repository, journey_service
    from communication import repository as _communication
    esito = {"automations_paused": False, "automations_already_paused": False, "cancelled_messages": 0}
    if not _communication.ledger_installed(cur):
        return esito
    if journey_repository.schema_ready(cur):
        if journey_repository.automations_paused(cur, ctx, contact_id):
            esito["automations_already_paused"] = True
        else:
            journey_service.pause_automations(ctx, contact_id, reason="contact_trashed", cur=cur)
            esito["automations_paused"] = True
    esito["cancelled_messages"] = journey_repository.cancel_queued_for_contact(
        cur, ctx, contact_id, actor_user_id=getattr(ctx, "user_id", None))
    return esito


def trash_contact(ctx, contact_id: int, reason_code, note=None) -> dict:
    agency_id = ctx.require_agency()
    reason_code, note = _valida_motivo(reason_code, note)
    with core_cursor(commit=True) as (_, cur):
        _cestino_installato(cur)
        contatto = _contatto(cur, ctx, contact_id, lock=True)
        if contatto.get("deleted_at") is not None:
            raise ContactLifecycleConflict(ALREADY_DELETED_MESSAGE, ALREADY_DELETED)
        blocchi = trash_blockers(cur, ctx, agency_id, contatto)
        if blocchi:
            raise ContactLifecycleConflict(TRASH_BLOCKED_MESSAGE, TRASH_BLOCKED, blockers=blocchi)
        if not _vede_tutta_agenzia(ctx):
            storia = protected_history(cur, ctx, agency_id, contatto)
            if storia:
                raise ContactLifecycleForbidden(HISTORY_REQUIRES_ADMIN_MESSAGE, HISTORY_REQUIRES_ADMIN, history=storia)
        comunicazioni = _sospendi_comunicazioni(cur, ctx, contact_id)
        cur.execute("UPDATE contacts SET deleted_at = NOW(), deleted_by_user_id = %s, deleted_reason = %s, "
                    "updated_at = NOW() WHERE id = %s AND agency_id = %s RETURNING *",
                    (getattr(ctx, "user_id", None), reason_code, contact_id, agency_id))
        riga = dict(cur.fetchone())
        _evento(cur, ctx, agency_id, contact_id, "trash", reason=reason_code, note=note,
                before={k: contatto.get(k) for k in ("status", "archived_at", "assigned_agent_id")},
                metadata={"communications": comunicazioni})
    _audit("trash", ctx, entity_type="contact", entity_id=contact_id, reason=reason_code)
    riga["communications"] = comunicazioni
    return riga


def possible_duplicates(cur, agency_id: int, contact: dict) -> list[dict]:
    """Contatti ATTIVI (fuori dal Cestino) con la stessa email o lo stesso
    telefono normalizzati. Solo un avviso: nulla si fonde o si sovrascrive."""
    email, telefono = contact.get("email_normalized"), contact.get("phone_normalized")
    if not email and not telefono:
        return []
    cur.execute("SELECT id, display_name, first_name, last_name, company_name, email, phone, "
                "(email_normalized IS NOT NULL AND email_normalized = %s) AS same_email, "
                "(phone_normalized IS NOT NULL AND phone_normalized = %s) AS same_phone "
                "FROM contacts c WHERE c.agency_id = %s AND c.id <> %s AND (to_jsonb(c)->>'deleted_at') IS NULL "
                "AND ((email_normalized IS NOT NULL AND email_normalized = %s) "
                "  OR (phone_normalized IS NOT NULL AND phone_normalized = %s)) ORDER BY c.id LIMIT 20",
                (email, telefono, agency_id, contact["id"], email, telefono))
    return [dict(r) for r in cur.fetchall()]


def restore_contact(ctx, contact_id: int) -> dict:
    """Ripristino: i tre campi `deleted_*` a NULL, nient'altro. Le automazioni
    sospese e i messaggi annullati dal Cestino restano tali."""
    agency_id = ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        _cestino_installato(cur)
        contatto = _contatto(cur, ctx, contact_id, lock=True)
        if contatto.get("deleted_at") is None:
            raise ContactLifecycleConflict(NOT_DELETED_MESSAGE, NOT_DELETED)
        if not _vede_tutta_agenzia(ctx) and contatto.get("deleted_by_user_id") != getattr(ctx, "user_id", None):
            raise ContactLifecycleForbidden(NOT_DELETED_BY_YOU_MESSAGE, NOT_DELETED_BY_YOU)
        cur.execute("UPDATE contacts SET deleted_at = NULL, deleted_by_user_id = NULL, deleted_reason = NULL, "
                    "updated_at = NOW() WHERE id = %s AND agency_id = %s RETURNING *", (contact_id, agency_id))
        riga = dict(cur.fetchone())
        doppioni = possible_duplicates(cur, agency_id, riga)
        _evento(cur, ctx, agency_id, contact_id, "restore",
                before={k: contatto.get(k) for k in ("deleted_at", "deleted_by_user_id", "deleted_reason")},
                metadata={"possible_duplicates": [d["id"] for d in doppioni]})
    _audit("restore", ctx, entity_type="contact", entity_id=contact_id)
    riga["possible_duplicates"] = doppioni
    return riga


def list_trash(ctx, *, limit: int = 50, offset: int = 0) -> dict:
    """I contatti nel Cestino dell'agenzia, dal piu' recente. Owner, admin e
    platform admin in acting: tutto; un agent: cio' che ha spostato lui (e che
    il suo scope vede). Nessun dato ricalcolato."""
    from property.interactions import NOME_OPERATORE

    agency_id = ctx.require_agency()
    limit = max(1, min(int(limit), TRASH_LIST_MAX))
    offset = max(0, int(offset))
    source, params = scoped_source(ctx, "contacts", "c")
    filtri = ["c.deleted_at IS NOT NULL"]
    if not _vede_tutta_agenzia(ctx):
        filtri.append("c.deleted_by_user_id = %s")
        params.append(getattr(ctx, "user_id", None))
    with core_cursor() as (_, cur):
        _cestino_installato(cur)
        cur.execute(
            f"""SELECT c.id, c.contact_type, c.display_name, c.first_name, c.last_name, c.company_name,
                       c.email, c.phone, c.status, c.archived_at,
                       c.deleted_at, c.deleted_reason, c.deleted_by_user_id,
                       (SELECT {NOME_OPERATORE.format(a='u')} FROM operator_users u
                         WHERE u.id = c.deleted_by_user_id) AS deleted_by_name,
                       (SELECT e.note FROM record_lifecycle_events e
                         WHERE e.agency_id = c.agency_id AND e.entity_type = 'contact'
                           AND e.entity_id = c.id AND e.action = 'trash'
                         ORDER BY e.occurred_at DESC, e.id DESC LIMIT 1) AS deleted_note
                  FROM {source} AND {' AND '.join(filtri)}
                 ORDER BY c.deleted_at DESC, c.id DESC
                 LIMIT %s OFFSET %s""",
            params + [limit + 1, offset])
        righe = [dict(r) for r in cur.fetchall()]
    return {"items": righe[:limit], "has_more": len(righe) > limit, "limit": limit, "offset": offset}


def trash_info(ctx, contact: dict) -> dict | None:
    """Per la scheda di un contatto nel Cestino: chi, quando, perche', la nota
    e se chi guarda puo' ripristinarlo. None fuori dal Cestino."""
    if not contact or contact.get("deleted_at") is None:
        return None
    from property.interactions import NOME_OPERATORE
    with core_cursor() as (_, cur):
        cur.execute(f"SELECT {NOME_OPERATORE.format(a='u')} AS nome FROM operator_users u WHERE u.id = %s",
                    (contact.get("deleted_by_user_id"),))
        riga = cur.fetchone()
        cur.execute("SELECT note FROM record_lifecycle_events WHERE agency_id = %s AND entity_type = 'contact' "
                    "AND entity_id = %s AND action = 'trash' ORDER BY occurred_at DESC, id DESC LIMIT 1",
                    (contact.get("agency_id"), contact["id"]))
        evento = cur.fetchone()
    puo = _vede_tutta_agenzia(ctx) or contact.get("deleted_by_user_id") == getattr(ctx, "user_id", None)
    return {"deleted_at": contact.get("deleted_at"), "deleted_reason": contact.get("deleted_reason"),
            "deleted_by_user_id": contact.get("deleted_by_user_id"),
            "deleted_by_name": riga["nome"] if riga else None,
            "deleted_note": evento["note"] if evento else None,
            "can_restore": bool(puo)}
