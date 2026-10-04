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
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from psycopg2 import errors as _pg_errors

from core.database import core_cursor
from core.exceptions import ConflictError, NotFoundError, PermissionDenied
from operator_auth import permissions

from . import repository

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
