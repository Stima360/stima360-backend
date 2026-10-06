"""DELETE-ARCH Fase 0 - ciclo di vita degli immobili: chi puo' e cosa blocca.

Nessuna migration, nessun Cestino, nessun `deleted_at`: qui vivono le regole
che il DECISION CONTRACT REV 2 fissa per la Fase 0.

  * ACCESSO (D10 / D19): un `agent` agisce solo sugli immobili assegnati a
    se' (`assigned_agent_id = ctx.user_id`); owner, admin e platform admin in
    acting su tutta l'agenzia. `assigned_agent_id` e' la fonte, nessun
    creatore storico viene inferito.
  * ARCHIVIA / RIATTIVA: operazioni esplicite. `archived_at` e
    `commercial_status='archived'` cambiano insieme, nella stessa
    transazione, da una sola funzione. Archiviare e' rifiutato con processi
    aperti (acquisizione, appuntamenti futuri, vendita o proposta in corso)
    e su un immobile venduto: `409 ARCHIVE_BLOCKED` con l'elenco dei blocchi.
  * RIMUOVI PROPRIETARIO: la riga di collegamento si cancella, il contatto e
    lo storico restano; rifiutato se il contatto e' referente di
    un'acquisizione aperta, se la coppia ha un'opportunita' Venditore viva
    (si usa «Smetti...» prima: non si chiude in silenzio) o se e' l'ultimo
    proprietario di un immobile con incarico. Il principale rimosso viene
    sostituito, nella stessa transazione, dal primo proprietario rimasto
    dello stesso ruolo.
  * SCOLLEGA LEAD: un collegamento `seller` di un lead vivo non si toglie da
    qui: sparirebbe sotto l'opportunita' Venditori.
  * FIGLI (foto, documenti, visite, accessori): stesso accesso dell'immobile;
    le FK RESTRICT dell'Owner Portal diventano errori leggibili, non 500.

Audit minimo (Fase 0, niente tabelle nuove): `property_status_history` per
archivia/riattiva, e per tutto il resto una riga di log strutturato
(`stima360.lifecycle`). Il registro `record_lifecycle_events` arriva con M1.

DELETE-ARCH Fase 2B1 (in fondo al modulo): il Cestino Immobili - trash,
restore, deletion-check - con il registro `record_lifecycle_events` (085).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from psycopg2 import errors as _pg_errors

from core.database import core_cursor
from core.exceptions import ConflictError, NotFoundError, PermissionDenied, ValidationError
from operator_auth import permissions

from . import repository
from .census import CensusNotInstalled

log = logging.getLogger("stima360.lifecycle")

# --- codici (stesso idioma di acquisitions/errors.py e property/census.py) ----
ARCHIVE_BLOCKED = "ARCHIVE_BLOCKED"
NOT_ARCHIVED = "NOT_ARCHIVED"
ALREADY_ARCHIVED = "ALREADY_ARCHIVED"
NOT_ASSIGNED = "NOT_ASSIGNED"
OWNER_OF_OPEN_ACQUISITION = "OWNER_OF_OPEN_ACQUISITION"
SELLER_OPPORTUNITY_OPEN = "SELLER_OPPORTUNITY_OPEN"
LAST_OWNER_WITH_MANDATE = "LAST_OWNER_WITH_MANDATE"
SELLER_LINK_ACTIVE = "SELLER_LINK_ACTIVE"
DOCUMENT_SHARED = "DOCUMENT_SHARED"
VISIT_HAS_FEEDBACK = "VISIT_HAS_FEEDBACK"
CHILD_REFERENCED = "CHILD_REFERENCED"
USE_ARCHIVE_ACTION = "USE_ARCHIVE_ACTION"
USE_UNARCHIVE_ACTION = "USE_UNARCHIVE_ACTION"

NOT_ASSIGNED_MESSAGE = "Questo immobile non è assegnato a te: chiedi a un amministratore"
ARCHIVE_BLOCKED_MESSAGE = "L'immobile ha processi aperti: chiudili prima di archiviare"
NOT_ARCHIVED_MESSAGE = "L'immobile non è archiviato"
ALREADY_ARCHIVED_MESSAGE = "L'immobile è già archiviato"
SOLD_MESSAGE = "Un immobile venduto non si archivia"
OWNER_OF_OPEN_ACQUISITION_MESSAGE = "Il contatto è il referente di un'acquisizione aperta: cambia prima il referente"
SELLER_OPPORTUNITY_OPEN_MESSAGE = "Il contatto vende questo immobile: usa prima «Smetti…» nella scheda Venditori"
LAST_OWNER_WITH_MANDATE_MESSAGE = "Un immobile con incarico deve avere almeno un proprietario"
SELLER_LINK_ACTIVE_MESSAGE = "È l'opportunità Venditore di questo immobile: si chiude da Venditori («Smetti…»), non si scollega"
DOCUMENT_SHARED_MESSAGE = "Il documento è stato condiviso con il proprietario e non si elimina"
VISIT_HAS_FEEDBACK_MESSAGE = "La visita ha un feedback pubblicato al proprietario e non si elimina"
CHILD_REFERENCED_MESSAGE = "L'elemento è collegato ad altri dati (Owner Portal) e non si elimina"
USE_ARCHIVE_ACTION_MESSAGE = ("Lo stato «Archiviato» non si imposta modificando l'immobile: "
                              "usa «Archivia» (POST /api/property/properties/{id}/archive)")
USE_UNARCHIVE_ACTION_MESSAGE = ("L'immobile è archiviato: per cambiarne lo stato usa «Riattiva» "
                                "(POST /api/property/properties/{id}/unarchive)")

OWNER_ROLES = ("owner", "seller")
#: Lo stato a cui torna un immobile riattivato quando lo storico non dice
#: altro (o dice qualcosa che il database non accetterebbe: vedi unarchive).
FALLBACK_STATUS = "draft"
#: Un incarico (`mandate`) non si ricrea con la riattivazione senza
#: un'acquisizione (trigger 081): si torna al fallback e lo si dice.
_NON_RESTORABLE = ("sold", "archived")


class _ConCodice:
    code = None

    def __init__(self, message, **extra):
        super().__init__(message)
        self.extra = extra


class LifecycleConflict(_ConCodice, ConflictError):
    def __init__(self, message, code, **extra):
        super().__init__(message, **extra)
        self.code = code


class LifecycleForbidden(_ConCodice, PermissionDenied):
    code = NOT_ASSIGNED


class _ValidazioneCodificata(_ConCodice, ValidationError):
    def __init__(self, message, code, **extra):
        super().__init__(message, **extra)
        self.code = code


# ---------------------------------------------------------------------------
# accesso
# ---------------------------------------------------------------------------

def may_manage(ctx, prop: dict) -> bool:
    """Chi puo' archiviare, riattivare, rimuovere collegamenti ed eliminare i
    figli di QUESTO immobile. Owner/admin/platform admin: tutta l'agenzia.
    `agent`: solo se `assigned_agent_id` e' il suo (D10). Un immobile non
    assegnato resta dell'agenzia: nessun agente puo' toccarlo."""
    if permissions.sees_all_agency_records(getattr(ctx, "role", None), getattr(ctx, "is_platform_admin", False)):
        return True
    assegnato = prop.get("assigned_agent_id")
    return getattr(ctx, "role", None) == "agent" and assegnato is not None and getattr(ctx, "user_id", None) == assegnato


def require_manage(ctx, prop: dict) -> None:
    if not may_manage(ctx, prop):
        raise LifecycleForbidden(NOT_ASSIGNED_MESSAGE)


def _immobile(cur, agency_id: int, property_id: int, *, lock: bool = False) -> dict:
    cur.execute(f"SELECT * FROM properties WHERE id = %s AND agency_id = %s{' FOR UPDATE' if lock else ''}",
                (property_id, agency_id))
    riga = repository.row(cur.fetchone())
    if riga is None:
        raise NotFoundError(f"property {property_id} not found")
    return riga


def _presente(cur, tabella: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS presente", (f"public.{tabella}",))
    return bool(cur.fetchone()["presente"])


def _audit(azione: str, ctx, **campi) -> None:
    """Audit minimo di Fase 0: una riga JSON per azione. Non e' persistente
    oltre i log del processo, e lo si dichiara: il registro arriva con M1."""
    riga = {"action": azione, "actor_user_id": getattr(ctx, "user_id", None),
            "actor_role": getattr(ctx, "role", None), "agency_id": getattr(ctx, "agency_id", None),
            "at": datetime.now(timezone.utc).isoformat(), **campi}
    log.info("lifecycle %s", json.dumps(riga, default=str))


# ---------------------------------------------------------------------------
# archivia / riattiva
# ---------------------------------------------------------------------------

def _archiviato(prop: dict) -> bool:
    return prop.get("archived_at") is not None or prop.get("commercial_status") == "archived"


def check_status_patch(data: dict, current: dict | None) -> None:
    """DELETE-ARCH Fase 0, review 2: la PATCH generica non archivia e non
    riattiva. `commercial_status` verso `archived` -> USE_ARCHIVE_ACTION;
    qualunque stato su un immobile archiviato (anche uno "zombie" con
    `archived_at` e stato operativo) -> USE_UNARCHIVE_ACTION. Il service la
    chiama sul valore letto, il repository di nuovo sotto `FOR UPDATE`: una
    archiviazione concorrente non apre una finestra."""
    if "commercial_status" not in data or current is None:
        return
    if data["commercial_status"] == "archived" and not _archiviato(current):
        raise LifecycleConflict(USE_ARCHIVE_ACTION_MESSAGE, USE_ARCHIVE_ACTION)
    if _archiviato(current) and data["commercial_status"] != "archived":
        raise LifecycleConflict(USE_UNARCHIVE_ACTION_MESSAGE, USE_UNARCHIVE_ACTION)


def archive_blockers(cur, agency_id: int, prop: dict) -> list[dict]:
    """I processi aperti che impediscono di archiviare, nell'ordine in cui
    la UI li mostra. Ogni voce porta `code`, `label` e gli `items`."""
    pid = prop["id"]
    blocchi = []
    if prop.get("commercial_status") == "sold":
        blocchi.append({"code": "sold", "label": SOLD_MESSAGE, "items": []})
    if _presente(cur, "acquisitions"):
        cur.execute("SELECT id, status FROM acquisitions WHERE property_id = %s AND agency_id = %s "
                    "AND status NOT IN ('acquired', 'lost') ORDER BY id", (pid, agency_id))
        righe = [dict(r) for r in cur.fetchall()]
        if righe:
            blocchi.append({"code": "open_acquisition", "label": "Acquisizione aperta", "items": righe})
    if _presente(cur, "appointments"):
        cur.execute("SELECT id, start_at, appointment_type FROM appointments WHERE property_id = %s AND agency_id = %s "
                    "AND status IN ('requested', 'scheduled', 'confirmed') AND start_at >= NOW() ORDER BY start_at",
                    (pid, agency_id))
        righe = [dict(r) for r in cur.fetchall()]
        if righe:
            blocchi.append({"code": "future_appointment", "label": "Appuntamento futuro aperto", "items": righe})
    cur.execute("SELECT id, status FROM property_sales WHERE property_id = %s AND status = 'pending' ORDER BY id", (pid,))
    righe = [dict(r) for r in cur.fetchall()]
    if righe:
        blocchi.append({"code": "pending_sale", "label": "Vendita in corso", "items": righe})
    cur.execute("SELECT pp.id, pp.status FROM property_proposals pp JOIN matches m ON m.id = pp.match_id "
                "WHERE m.property_id = %s AND pp.status IN ('draft', 'submitted') ORDER BY pp.id", (pid,))
    righe = [dict(r) for r in cur.fetchall()]
    if righe:
        blocchi.append({"code": "open_proposal", "label": "Proposta in corso", "items": righe})
    return blocchi


def archive_property(ctx, property_id: int) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        prop = _immobile(cur, agency_id, property_id, lock=True)
        require_manage(ctx, prop)
        refuse_if_in_trash(prop)                        # DELETE-ARCH 2B1
        if prop.get("archived_at") is not None and prop.get("commercial_status") == "archived":
            raise LifecycleConflict(ALREADY_ARCHIVED_MESSAGE, ALREADY_ARCHIVED)
        blocchi = archive_blockers(cur, agency_id, prop)
        if blocchi:
            raise LifecycleConflict(ARCHIVE_BLOCKED_MESSAGE, ARCHIVE_BLOCKED, blockers=blocchi)
        cur.execute("UPDATE properties SET commercial_status = 'archived', archived_at = NOW(), updated_at = NOW() "
                    "WHERE id = %s RETURNING *", (property_id,))
        riga = repository.row(cur.fetchone())
        if prop.get("commercial_status") != "archived":
            cur.execute("INSERT INTO property_status_history(property_id, field_name, old_value, new_value, note, changed_by) "
                        "VALUES (%s, 'commercial_status', %s, 'archived', 'archived', %s)",
                        (property_id, prop.get("commercial_status"), str(getattr(ctx, "user_id", "") or "")))
        # CENSIMENTO-1: una pertinenza archiviata esce dal padre (stessa regola
        # dell'archiviazione storica, repository.update_property).
        if prop.get("parent_property_id") is not None:
            from . import census as _census
            _census.detach_on_close(ctx, cur, riga, "archived")
            riga = _immobile(cur, agency_id, property_id)
    _audit("archive", ctx, entity_type="property", entity_id=property_id)
    return riga


def previous_status(cur, property_id: int) -> str | None:
    """Lo stato prima dell'ultima archiviazione, dallo storico; None se lo
    storico non lo dice."""
    cur.execute("SELECT old_value FROM property_status_history WHERE property_id = %s "
                "AND field_name = 'commercial_status' AND new_value = 'archived' ORDER BY created_at DESC, id DESC LIMIT 1",
                (property_id,))
    riga = cur.fetchone()
    return riga["old_value"] if riga else None


def unarchive_property(ctx, property_id: int) -> dict:
    """«Riattiva»: `archived_at` a NULL e lo stato precedente dallo storico,
    quando e' deterministico e il database lo accetta; altrimenti `draft`,
    detto in `warnings`. Mai un incarico ricreato per riattivazione."""
    agency_id = ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        prop = _immobile(cur, agency_id, property_id, lock=True)
        require_manage(ctx, prop)
        refuse_if_in_trash(prop)                        # DELETE-ARCH 2B1
        if prop.get("archived_at") is None and prop.get("commercial_status") != "archived":
            raise LifecycleConflict(NOT_ARCHIVED_MESSAGE, NOT_ARCHIVED)
        avvisi = []
        stato = previous_status(cur, property_id)
        if stato is None or stato in _NON_RESTORABLE:
            if stato is not None:
                avvisi.append("previous_status_not_restorable")
            stato = FALLBACK_STATUS
        if prop.get("record_kind") == "census" and stato != FALLBACK_STATUS:
            avvisi.append("census_back_to_draft")       # 083: census = solo draft/archived
            stato = FALLBACK_STATUS
        if stato == "mandate" and prop.get("acquisition_id") is None:
            avvisi.append("mandate_not_restored")       # 081: incarico solo da acquisizione
            stato = FALLBACK_STATUS
        cur.execute("UPDATE properties SET commercial_status = %s, archived_at = NULL, updated_at = NOW() "
                    "WHERE id = %s RETURNING *", (stato, property_id))
        riga = repository.row(cur.fetchone())
        if prop.get("commercial_status") != stato:
            cur.execute("INSERT INTO property_status_history(property_id, field_name, old_value, new_value, note, changed_by) "
                        "VALUES (%s, 'commercial_status', %s, %s, 'unarchive', %s)",
                        (property_id, prop.get("commercial_status"), stato, str(getattr(ctx, "user_id", "") or "")))
    _audit("unarchive", ctx, entity_type="property", entity_id=property_id, restored_status=stato, warnings=avvisi)
    riga["warnings"] = avvisi
    return riga


# ---------------------------------------------------------------------------
# rimuovi proprietario / scollega lead
# ---------------------------------------------------------------------------

def _opportunita_viva(cur, agency_id: int, property_id: int, contact_id: int):
    cur.execute("SELECT l.id, l.status FROM leads l JOIN property_leads pl ON pl.lead_id = l.id "
                "WHERE l.agency_id = %s AND l.contact_id = %s AND l.pipeline = 'sell' AND pl.property_id = %s "
                "AND pl.relation_type = 'seller' AND l.status IN ('open', 'paused') ORDER BY l.id LIMIT 1",
                (agency_id, contact_id, property_id))
    riga = cur.fetchone()
    return dict(riga) if riga else None


def delete_contact(ctx, property_id: int, contact_id: int, role: str) -> None:
    agency_id = ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        prop = _immobile(cur, agency_id, property_id, lock=True)
        require_manage(ctx, prop)
        refuse_if_in_trash(prop)                        # DELETE-ARCH 2B2: congelato
        cur.execute("SELECT * FROM property_contacts WHERE property_id = %s AND contact_id = %s AND role = %s FOR UPDATE",
                    (property_id, contact_id, role))
        link = repository.row(cur.fetchone())
        if link is None:
            raise NotFoundError("property contact link not found")
        if role in OWNER_ROLES:
            cur.execute("SELECT COUNT(*) AS n FROM property_contacts WHERE property_id = %s AND contact_id = %s "
                        "AND role = ANY(%s) AND role <> %s", (property_id, contact_id, list(OWNER_ROLES), role))
            altri_ruoli_del_contatto = cur.fetchone()["n"]
            if altri_ruoli_del_contatto == 0:
                if _presente(cur, "acquisitions"):
                    cur.execute("SELECT id FROM acquisitions WHERE property_id = %s AND agency_id = %s AND owner_contact_id = %s "
                                "AND status NOT IN ('acquired', 'lost') ORDER BY id LIMIT 1", (property_id, agency_id, contact_id))
                    acq = cur.fetchone()
                    if acq:
                        raise LifecycleConflict(OWNER_OF_OPEN_ACQUISITION_MESSAGE, OWNER_OF_OPEN_ACQUISITION, acquisition_id=acq["id"])
                opportunita = _opportunita_viva(cur, agency_id, property_id, contact_id)
                if opportunita:
                    raise LifecycleConflict(SELLER_OPPORTUNITY_OPEN_MESSAGE, SELLER_OPPORTUNITY_OPEN,
                                            lead_id=opportunita["id"], lead_status=opportunita["status"])
                cur.execute("SELECT COUNT(*) AS n FROM property_contacts WHERE property_id = %s AND role = ANY(%s) "
                            "AND NOT (contact_id = %s)", (property_id, list(OWNER_ROLES), contact_id))
                altri_proprietari = cur.fetchone()["n"]
                incarico = prop.get("acquisition_id") is not None or prop.get("mandate_type") is not None
                if altri_proprietari == 0 and incarico and prop.get("commercial_status") not in ("sold", "withdrawn", "archived"):
                    raise LifecycleConflict(LAST_OWNER_WITH_MANDATE_MESSAGE, LAST_OWNER_WITH_MANDATE)
        cur.execute("DELETE FROM property_contacts WHERE property_id = %s AND contact_id = %s AND role = %s",
                    (property_id, contact_id, role))
        promosso = None
        if link.get("is_primary"):
            cur.execute("UPDATE property_contacts SET is_primary = TRUE WHERE id = (SELECT id FROM property_contacts "
                        "WHERE property_id = %s AND role = %s ORDER BY id LIMIT 1) RETURNING contact_id",
                        (property_id, role))
            riga = cur.fetchone()
            promosso = riga["contact_id"] if riga else None
    _audit("unlink_contact", ctx, entity_type="property", entity_id=property_id, contact_id=contact_id,
           role=role, promoted_primary=promosso)


def delete_lead(ctx, property_id: int, lead_id: int) -> None:
    agency_id = ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        prop = _immobile(cur, agency_id, property_id, lock=True)
        require_manage(ctx, prop)
        refuse_if_in_trash(prop)                        # DELETE-ARCH 2B2: congelato
        cur.execute("SELECT pl.relation_type, l.status, l.pipeline FROM property_leads pl JOIN leads l ON l.id = pl.lead_id "
                    "WHERE pl.property_id = %s AND pl.lead_id = %s FOR UPDATE OF pl", (property_id, lead_id))
        link = cur.fetchone()
        if link is None:
            raise NotFoundError("property lead link not found")
        if link["relation_type"] == "seller" and link["pipeline"] == "sell" and link["status"] in ("open", "paused"):
            raise LifecycleConflict(SELLER_LINK_ACTIVE_MESSAGE, SELLER_LINK_ACTIVE, lead_id=lead_id)
        cur.execute("DELETE FROM property_leads WHERE property_id = %s AND lead_id = %s", (property_id, lead_id))
    _audit("unlink_lead", ctx, entity_type="property", entity_id=property_id, lead_id=lead_id)


# ---------------------------------------------------------------------------
# figli: foto, documenti, visite (accessori: property/census.py)
# ---------------------------------------------------------------------------

_LABEL = {"property_documents": "document", "property_photos": "photo", "property_visits": "visit"}


def delete_child(ctx, table: str, item_id: int) -> None:
    agency_id = ctx.require_agency()
    label = _LABEL[table]
    with core_cursor() as (_, cur):
        cur.execute(f"SELECT p.* FROM {table} c JOIN properties p ON p.id = c.property_id WHERE c.id = %s AND p.agency_id = %s",
                    (item_id, agency_id))
        prop = repository.row(cur.fetchone())
        if prop is None:
            raise NotFoundError(f"{label} {item_id} not found")
        require_manage(ctx, prop)
        refuse_if_in_trash(prop)                        # DELETE-ARCH 2B2: congelato
        if table == "property_documents":
            cur.execute("SELECT 1 FROM owner_shared_documents WHERE property_document_id = %s LIMIT 1", (item_id,))
            if cur.fetchone():
                raise LifecycleConflict(DOCUMENT_SHARED_MESSAGE, DOCUMENT_SHARED)
        if table == "property_visits" and _presente(cur, "owner_visit_feedback_publications"):
            cur.execute("SELECT 1 FROM owner_visit_feedback_publications WHERE property_visit_id = %s LIMIT 1", (item_id,))
            if cur.fetchone():
                raise LifecycleConflict(VISIT_HAS_FEEDBACK_MESSAGE, VISIT_HAS_FEEDBACK)
    try:
        repository.delete_child(ctx, table, item_id, label)
    except _pg_errors.ForeignKeyViolation as exc:
        # Una FK RESTRICT non prevista sopra (nuove tabelle dell'Owner Portal)
        # resta un rifiuto leggibile, non un 500.
        codice = {"property_documents": DOCUMENT_SHARED, "property_visits": VISIT_HAS_FEEDBACK}.get(table, CHILD_REFERENCED)
        messaggio = {"property_documents": DOCUMENT_SHARED_MESSAGE, "property_visits": VISIT_HAS_FEEDBACK_MESSAGE}.get(table, CHILD_REFERENCED_MESSAGE)
        raise LifecycleConflict(messaggio, codice) from exc
    _audit("hard_delete", ctx, entity_type=label, entity_id=item_id, property_id=prop["id"])


# ---------------------------------------------------------------------------
# DELETE-ARCH Fase 2B1: CESTINO (trash / restore / deletion-check)
# ---------------------------------------------------------------------------
# Un immobile e' "nel Cestino" quando `deleted_at` e' valorizzato (085). Lo
# stesso id, nessun figlio toccato (contatti, lead, foto, documenti, attivita',
# storici restano); `commercial_status` e `archived_at` non cambiano ne' qui
# ne' al ripristino. Ogni operazione scrive una riga in
# `record_lifecycle_events` (append-only), nella stessa transazione.
#
# Fuori da 2B1, di proposito (Fase 2B2): chiusura/ripristino automatici
# dell'opportunita' Venditore (qui e' un blocco), filtri delle altre letture
# (dashboard, Agenda, Match, ...), guardie sulle tabelle collegate.

TRASH_BLOCKED = "TRASH_BLOCKED"
ALREADY_DELETED = "ALREADY_DELETED"
NOT_DELETED = "NOT_DELETED"
NOT_DELETED_BY_YOU = "NOT_DELETED_BY_YOU"
RESTORE_CONFLICT = "RESTORE_CONFLICT"
PROPERTY_IN_TRASH = "PROPERTY_IN_TRASH"
INVALID_TRASH_REASON = "INVALID_TRASH_REASON"
HISTORY_REQUIRES_ADMIN = "HISTORY_REQUIRES_ADMIN"

TRASH_BLOCKED_MESSAGE = "L'immobile ha processi aperti: chiudili prima di spostarlo nel Cestino"
ALREADY_DELETED_MESSAGE = "L'immobile è già nel Cestino"
NOT_DELETED_MESSAGE = "L'immobile non è nel Cestino"
NOT_DELETED_BY_YOU_MESSAGE = "Puoi ripristinare solo gli immobili che hai spostato tu nel Cestino: chiedi a un amministratore"
RESTORE_CONFLICT_MESSAGE = ("Non si può ripristinare: un altro immobile attivo ha la stessa identità catastale "
                            "o la stessa richiesta di creazione. Correggi prima l'altro immobile")
PROPERTY_IN_TRASH_MESSAGE = "L'immobile è nel Cestino: ripristinalo prima di modificarlo"
INVALID_TRASH_REASON_MESSAGE = "Motivo non valido"
HISTORY_REQUIRES_ADMIN_MESSAGE = ("L'immobile ha uno storico operativo (attività, appuntamenti, acquisizioni, "
                                  "proposte...): può spostarlo nel Cestino solo un amministratore o il titolare")
MANDATE_PRESENT_MESSAGE = "Incarico presente"
SELLER_OPEN_LABEL = "Opportunità Venditore aperta: chiudila prima da Venditori («Smetti…»)"

TRASH_REASONS = ("created_by_mistake", "duplicate", "invalid_data", "test_record", "other")
TRASH_NOTE_MAX = 500

#: I blocchi dell'archiviazione valgono anche per il Cestino, con codici propri.
_CODICI_CESTINO = {"sold": "PROPERTY_SOLD", "open_acquisition": "ACQUISITION_OPEN",
                   "future_appointment": "FUTURE_APPOINTMENT", "pending_sale": "SALE_PENDING",
                   "open_proposal": "PROPOSAL_OPEN"}


class _NonEliminatoDaTe(LifecycleForbidden):
    code = NOT_DELETED_BY_YOU


class _StoricoRichiedeAdmin(LifecycleForbidden):
    code = HISTORY_REQUIRES_ADMIN


class TrashNotInstalled(CensusNotInstalled):
    """503, come CENSUS_NOT_INSTALLED: `trc` la traduce con il suo `code`."""
    code = "TRASH_NOT_INSTALLED"


TRASH_NOT_INSTALLED_MESSAGE = ("Il Cestino non è ancora disponibile su questo database "
                               "(migration 085 non applicata)")


def _cestino_installato(cur) -> None:
    """Codice deployato prima della 085 (stessa chiusura leggibile della 083,
    property/census.py::_assicura_083): 503 TRASH_NOT_INSTALLED prima di
    leggere o scrivere, non un 500."""
    cur.execute("SELECT to_regclass('public.record_lifecycle_events') IS NOT NULL"
                "   AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'"
                "                 AND table_name = 'properties' AND column_name = 'deleted_at') AS pronto")
    if not cur.fetchone()["pronto"]:
        raise TrashNotInstalled(TRASH_NOT_INSTALLED_MESSAGE)


def in_trash(prop: dict | None) -> bool:
    return bool(prop) and prop.get("deleted_at") is not None


def refuse_if_in_trash(prop: dict | None) -> None:
    """La guardia minima di 2B1: nessuna mutazione ordinaria (PATCH,
    archivia, riattiva) su un immobile nel Cestino."""
    if in_trash(prop):
        raise LifecycleConflict(PROPERTY_IN_TRASH_MESSAGE, PROPERTY_IN_TRASH)


def _ha_incarico(prop: dict) -> bool:
    """Incarico reale: nato da un'acquisizione (081) o comunque con dati di
    incarico sull'immobile. Volutamente largo: nel dubbio si blocca."""
    return (prop.get("acquisition_id") is not None or prop.get("mandate_type") is not None
            or prop.get("mandate_start") is not None or prop.get("commercial_status") == "mandate")


def trash_blockers(cur, agency_id: int, prop: dict) -> list[dict]:
    """I blocchi del Cestino, ricalcolati sulla riga letta (bloccata da chi
    sposta nel Cestino). Ogni voce: `code`, `label`, `items`."""
    blocchi = [{**b, "code": _CODICI_CESTINO.get(b["code"], b["code"])}
               for b in archive_blockers(cur, agency_id, prop)]
    if _ha_incarico(prop):
        blocchi.append({"code": "MANDATE_PRESENT", "label": MANDATE_PRESENT_MESSAGE,
                        "items": [{k: prop.get(k) for k in ("acquisition_id", "mandate_type", "mandate_start", "mandate_end")}]})
    # 2B1: l'opportunita' Venditore viva BLOCCA (nessuna chiusura automatica:
    # arriva con la Fase 2B2, insieme al ripristino).
    cur.execute("SELECT l.id, l.status, l.contact_id FROM leads l JOIN property_leads pl ON pl.lead_id = l.id "
                "WHERE l.agency_id = %s AND pl.property_id = %s AND l.pipeline = 'sell' "
                "AND pl.relation_type = 'seller' AND l.status IN ('open', 'paused') ORDER BY l.id",
                (agency_id, prop["id"]))
    righe = [dict(r) for r in cur.fetchall()]
    if righe:
        blocchi.append({"code": SELLER_OPPORTUNITY_OPEN, "label": SELLER_OPEN_LABEL, "items": righe})
    return blocchi


def _evento(cur, ctx, agency_id: int, property_id: int, azione: str, *, reason=None, note=None,
            before: dict) -> None:
    cur.execute("INSERT INTO record_lifecycle_events (agency_id, entity_type, entity_id, action, reason_code, note, "
                "actor_user_id, before_state, metadata) VALUES (%s, 'property', %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)",
                (agency_id, property_id, azione, reason, note, getattr(ctx, "user_id", None),
                 json.dumps(before, default=str),
                 json.dumps({"actor_role": getattr(ctx, "role", None),
                             "platform_admin": bool(getattr(ctx, "is_platform_admin", False))})))


def _valida_motivo(reason_code, note):
    if reason_code not in TRASH_REASONS:
        raise _ValidazioneCodificata(INVALID_TRASH_REASON_MESSAGE, INVALID_TRASH_REASON, allowed=list(TRASH_REASONS))
    if note is not None:
        note = str(note).strip() or None
    if note is not None and len(note) > TRASH_NOTE_MAX:
        raise _ValidazioneCodificata(f"La nota supera {TRASH_NOTE_MAX} caratteri", INVALID_TRASH_REASON)
    return reason_code, note


def _agente_senza_privilegi(ctx) -> bool:
    """REVIEW 2: chi deve fermarsi davanti allo storico. Owner, admin e
    platform admin in acting vedono tutta l'agenzia e non si fermano."""
    return not permissions.sees_all_agency_records(getattr(ctx, "role", None),
                                                   getattr(ctx, "is_platform_admin", False))


def protected_history(cur, agency_id: int, prop: dict, ctx=None) -> list[dict]:
    """REVIEW 2 - lo STORICO OPERATIVO REALE dell'immobile: per un agent
    richiede owner/admin (403 HISTORY_REQUIRES_ADMIN), per owner/admin non
    blocca. I processi APERTI restano i blocchi assoluti di `trash_blockers`;
    qui contano i fatti gia' avvenuti. Ogni voce: `code`, `label`, `count`.

    NON e' storico: le righe «per errore» (appuntamento `cancelled_kind =
    'mistake'`, acquisizione e lead `lost_reason = 'created_by_mistake'`,
    attivita' con `metadata.mistake`), le attivita' generate dal sistema
    (`core.repository.GENERATED_ACTIVITY_TYPES`) e le righe tecniche
    (`property_status_history`, `property_price_history`, match calcolati,
    foto/documenti caricati, collegamenti ai contatti)."""
    from core import repository as core_repository

    pid = prop["id"]
    voci = []

    def conta(codice, etichetta, sql, params, *, tabelle=()):
        if any(not _presente(cur, t) for t in tabelle):
            return
        cur.execute(f"SELECT count(*) AS n FROM ({sql}) x", params)
        n = cur.fetchone()["n"]
        if n:
            voci.append({"code": codice, "label": etichetta, "count": n})

    tipi = list(core_repository.GENERATED_ACTIVITY_TYPES)
    errore = core_repository.MISTAKE_SQL.format(a="a")
    conta("PROPERTY_ACTIVITY", "Attività o interazioni registrate sull'immobile",
          f"SELECT a.id FROM activities a WHERE a.agency_id = %s AND a.property_id = %s "
          f"AND a.activity_type <> ALL(%s) AND NOT {errore}", (agency_id, pid, tipi))
    conta("APPOINTMENT_HISTORY", "Appuntamenti avvenuti, passati o annullati",
          "SELECT id FROM appointments WHERE agency_id = %s AND property_id = %s "
          "AND cancelled_kind IS DISTINCT FROM 'mistake' "
          "AND NOT (status IN ('requested', 'scheduled', 'confirmed') AND start_at >= NOW())",
          (agency_id, pid), tabelle=("appointments",))
    conta("ACQUISITION_HISTORY", "Acquisizioni concluse",
          "SELECT id FROM acquisitions WHERE agency_id = %s AND property_id = %s "
          "AND status IN ('acquired', 'lost') AND lost_reason IS DISTINCT FROM 'created_by_mistake'",
          (agency_id, pid), tabelle=("acquisitions",))
    conta("ACQUISITION_HISTORY", "Acquisizioni da stima",
          "SELECT id FROM stima_acquisitions WHERE property_id = %s", (pid,), tabelle=("stima_acquisitions",))
    conta("PROPOSAL_HISTORY", "Proposte d'acquisto concluse",
          "SELECT pp.id FROM property_proposals pp JOIN matches mt ON mt.id = pp.match_id "
          "WHERE mt.property_id = %s AND pp.status NOT IN ('draft', 'submitted')", (pid,))
    conta("SALE_HISTORY", "Vendite registrate",
          "SELECT id FROM property_sales WHERE property_id = %s AND status <> 'pending'", (pid,))
    conta("VISIT_HISTORY", "Visite registrate (fuori Agenda)",
          "SELECT id FROM property_visits WHERE property_id = %s AND appointment_id IS NULL", (pid,))
    conta("BUYER_INTERACTION_HISTORY", "Interazioni con acquirenti",
          "SELECT id FROM buy_request_interactions WHERE property_id = %s", (pid,))
    conta("SELLER_HISTORY", "Opportunità Venditore concluse",
          "SELECT l.id FROM leads l JOIN property_leads pl ON pl.lead_id = l.id "
          "WHERE l.agency_id = %s AND pl.property_id = %s AND pl.relation_type = 'seller' AND l.pipeline = 'sell' "
          "AND l.status = 'closed' AND l.lost_reason IS DISTINCT FROM 'created_by_mistake'", (agency_id, pid))
    # DELETE-ARCH 2B2: il ledger delle comunicazioni si interroga SOLO dalla
    # sua funzione pubblica (sentinella P29-2.1 n4: nessuna tabella privata
    # nominata qui), con lo scope dell'agenzia di chi chiede.
    if ctx is not None:
        from communication import repository as _communication
        if _communication.property_has_message_history(cur, ctx, pid):
            voci.append({"code": "COMMUNICATION_HISTORY", "label": "Messaggi inviati o ricevuti sull'immobile",
                         "count": 1})
    conta("OWNER_PORTAL_HISTORY", "Accessi, pubblicazioni o riscontri del portale proprietario",
          "SELECT id FROM owner_property_access WHERE property_id = %s "
          "UNION ALL SELECT id FROM owner_publications WHERE property_id = %s "
          "UNION ALL SELECT id FROM owner_feedback WHERE property_id = %s "
          "UNION ALL SELECT s.id FROM owner_shared_documents s JOIN property_documents d ON d.id = s.property_document_id "
          "WHERE d.property_id = %s", (pid, pid, pid, pid),
          tabelle=("owner_property_access", "owner_publications", "owner_feedback", "owner_shared_documents"))
    # la stessa voce puo' arrivare da due fonti (acquisizioni): una riga, conteggio sommato
    unite = {}
    for v in voci:
        if v["code"] in unite:
            unite[v["code"]]["count"] += v["count"]
        else:
            unite[v["code"]] = dict(v)
    return list(unite.values())


def deletion_check(ctx, property_id: int) -> dict:
    """Si puo' spostare nel Cestino? Sola lettura; legge esplicitamente anche
    un immobile gia' nel Cestino (blocco ALREADY_DELETED)."""
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _cestino_installato(cur)
        prop = _immobile(cur, agency_id, property_id)
        require_manage(ctx, prop)
        if in_trash(prop):
            blocchi = [{"code": ALREADY_DELETED, "label": ALREADY_DELETED_MESSAGE, "items": []}]
        else:
            blocchi = trash_blockers(cur, agency_id, prop)
            if not blocchi and _agente_senza_privilegi(ctx):
                storia = protected_history(cur, agency_id, prop, ctx)
                if storia:
                    blocchi = [{"code": HISTORY_REQUIRES_ADMIN, "label": HISTORY_REQUIRES_ADMIN_MESSAGE,
                                "items": storia}]
    return {"can_trash": not blocchi, "blockers": blocchi}


def trash_property(ctx, property_id: int, reason_code, note=None) -> dict:
    agency_id = ctx.require_agency()
    reason_code, note = _valida_motivo(reason_code, note)
    with core_cursor(commit=True) as (_, cur):
        _cestino_installato(cur)
        prop = _immobile(cur, agency_id, property_id, lock=True)
        require_manage(ctx, prop)
        if in_trash(prop):
            raise LifecycleConflict(ALREADY_DELETED_MESSAGE, ALREADY_DELETED)
        blocchi = trash_blockers(cur, agency_id, prop)
        if blocchi:
            raise LifecycleConflict(TRASH_BLOCKED_MESSAGE, TRASH_BLOCKED, blockers=blocchi)
        # REVIEW 2: un agent non manda nel Cestino un immobile con storico
        # operativo reale; owner/admin si'. Ricalcolato sulla riga bloccata.
        if _agente_senza_privilegi(ctx):
            storia = protected_history(cur, agency_id, prop, ctx)
            if storia:
                raise _StoricoRichiedeAdmin(HISTORY_REQUIRES_ADMIN_MESSAGE, history=storia)
        cur.execute("UPDATE properties SET deleted_at = NOW(), deleted_by_user_id = %s, deleted_reason = %s, "
                    "updated_at = NOW() WHERE id = %s AND agency_id = %s RETURNING *",
                    (getattr(ctx, "user_id", None), reason_code, property_id, agency_id))
        riga = repository.row(cur.fetchone())
        _evento(cur, ctx, agency_id, property_id, "trash", reason=reason_code, note=note,
                before={k: prop.get(k) for k in ("commercial_status", "archived_at", "assigned_agent_id", "record_kind")})
    _audit("trash", ctx, entity_type="property", entity_id=property_id, reason=reason_code)
    return riga


def _conflitti_ripristino(cur, agency_id: int, prop: dict) -> list[dict]:
    """Gli indici unici che ignorano le righe nel Cestino (085): identita'
    catastale COMPLETA e `client_request_id` (REVIEW 1, R1). Letti PRIMA
    dell'UPDATE, sulla riga bloccata: un conflitto non lascia nulla a meta'.
    `code` resta univoco anche nel Cestino, quindi non puo' entrare in
    conflitto."""
    conflitti = []
    chiavi = ("cadastral_municipality_code", "cadastral_section", "cadastral_sheet", "cadastral_parcel", "cadastral_subunit")
    if all(prop.get(k) is not None for k in chiavi):
        cur.execute("SELECT id, code FROM properties WHERE agency_id = %s AND id <> %s AND deleted_at IS NULL "
                    "AND cadastral_municipality_code = %s AND cadastral_section = %s AND cadastral_sheet = %s "
                    "AND cadastral_parcel = %s AND cadastral_subunit = %s ORDER BY id",
                    (agency_id, prop["id"], *(prop[k] for k in chiavi)))
        conflitti += [{**dict(r), "index": "cadastral_identity"} for r in cur.fetchall()]
    if prop.get("client_request_id") is not None:
        cur.execute("SELECT id, code FROM properties WHERE agency_id = %s AND id <> %s AND deleted_at IS NULL "
                    "AND client_request_id = %s ORDER BY id", (agency_id, prop["id"], prop["client_request_id"]))
        conflitti += [{**dict(r), "index": "client_request"} for r in cur.fetchall()]
    return conflitti


def restore_property(ctx, property_id: int) -> dict:
    """Ripristino: i tre campi `deleted_*` a NULL, nient'altro. Un conflitto
    sull'identita' catastale o sulla `client_request_id` e' un 409
    RESTORE_CONFLICT senza modifiche: il ripristino non corregge dati ne'
    relazioni per riuscire."""
    agency_id = ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        _cestino_installato(cur)
        prop = _immobile(cur, agency_id, property_id, lock=True)
        if not in_trash(prop):
            raise LifecycleConflict(NOT_DELETED_MESSAGE, NOT_DELETED)
        if not permissions.sees_all_agency_records(getattr(ctx, "role", None), getattr(ctx, "is_platform_admin", False)):
            # agent: solo cio' che ha spostato lui nel Cestino
            if getattr(ctx, "role", None) != "agent" or prop.get("deleted_by_user_id") != getattr(ctx, "user_id", None):
                raise _NonEliminatoDaTe(NOT_DELETED_BY_YOU_MESSAGE)
        conflitti = _conflitti_ripristino(cur, agency_id, prop)
        if conflitti:
            raise LifecycleConflict(RESTORE_CONFLICT_MESSAGE, RESTORE_CONFLICT, conflicts=conflitti)
        try:
            cur.execute("UPDATE properties SET deleted_at = NULL, deleted_by_user_id = NULL, deleted_reason = NULL, "
                        "updated_at = NOW() WHERE id = %s AND agency_id = %s RETURNING *", (property_id, agency_id))
        except _pg_errors.UniqueViolation as exc:      # corsa con un inserimento concorrente
            raise LifecycleConflict(RESTORE_CONFLICT_MESSAGE, RESTORE_CONFLICT, conflicts=[]) from exc
        riga = repository.row(cur.fetchone())
        _evento(cur, ctx, agency_id, property_id, "restore",
                before={k: prop.get(k) for k in ("deleted_at", "deleted_by_user_id", "deleted_reason")})
    _audit("restore", ctx, entity_type="property", entity_id=property_id)
    return riga
