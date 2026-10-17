"""AGENZIE-TERRITORI C2 - proposte di modifica TERRITORIALI, approvate dal Super Admin.

Decisione del proprietario (definitiva): il Supervisore dell'agenzia
territorialmente competente PROPONE modifiche a un immobile di un'altra
agenzia; soltanto il Super Admin le APPROVA o le RIFIUTA. Nulla cambia
sull'immobile prima dell'approvazione.

Chi fa cosa (tutto deciso qui, nel backend):
  * proporre: sessione d'agenzia con ruolo `agency_owner` (NON `agency_admin`,
    NON `agent`, NON un platform admin "entrato"), immobile leggibile per
    competenza territoriale (C1, `territorial_access.property_access`);
    l'agenzia d'origine modifica come sempre, senza proposte;
  * vedere le proposte di un immobile: chi legge l'immobile (origine o competente);
  * approvare/rifiutare: il Super Admin generale DESIGNATO (ADM-1:
    `STIMA360_SUPER_ADMIN_USER_ID`, `super_admin.require_super_admin`) con il
    ruolo RILETTO nel database
    (`operator_users.is_platform_admin = TRUE` e `status = 'active'`,
    `super_admin.require_super_admin_db`); mai l'email.

I campi proponibili sono una WHITELIST esplicita (`ALLOWED_FIELDS`): solo dati
descrittivi dell'immobile. Fuori: agenzia, comune e indirizzo (cambierebbero la
competenza o la posizione), Cestino, assegnazioni, contatti e lead, incarichi,
prezzo e stato commerciale, documenti/visite/interazioni, censimento e catasto,
superfici, coordinate, edifici e altre unita'.

L'approvazione, in UNA transazione: lock della proposta e dell'immobile,
Super Admin riletto, proposta ancora `pending`, immobile vivo, non trasferito,
ancora una scheda CRM non archiviata e IDENTICO alla versione della proposta
(impronta), competenza ancora valida per la stessa agenzia e lo stesso
territorio, proponente ancora Supervisore attivo. Poi l'UPDATE dei soli campi
della proposta, l'esito e la riga di registro. Se una verifica fallisce nulla
viene scritto sull'immobile: la proposta resta `pending` e consultabile, e il
tentativo si registra (`approval_blocked`) in una transazione a parte.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from psycopg2 import errors
from psycopg2.extras import Json
from pydantic import BaseModel, ConfigDict, Field

from core.database import core_cursor
from core.exceptions import ConflictError, NotFoundError, PermissionDenied, ValidationError
from platform_admin.super_admin import require_super_admin, require_super_admin_db

from . import territorial_access
from .catalog import validate_energy_class
from .schemas import PropertyUpdate

#: I SOLI campi che una proposta territoriale puo' toccare (colonne di `properties`).
ALLOWED_FIELDS: tuple[str, ...] = (
    "title", "rooms", "bedrooms", "bathrooms", "floor", "total_floors", "elevator",
    "year_built", "condition", "energy_class", "public_notes",
)
FIELD_LABELS: dict[str, str] = {
    "title": "Titolo", "rooms": "Locali", "bedrooms": "Camere", "bathrooms": "Bagni", "floor": "Piano",
    "total_floors": "Piani dell'edificio", "elevator": "Ascensore", "year_built": "Anno di costruzione",
    "condition": "Stato di conservazione", "energy_class": "Classe energetica", "public_notes": "Descrizione",
}
STATUSES = ("pending", "approved", "rejected")
_NOTE_MAX = 500
_AUDIT_ACTION = "platform.territorial_change.denied"

# codici delle risposte (corpo {detail, code, ...})
NOT_PROPOSABLE = "TERRITORIAL_CHANGE_NOT_ALLOWED"
ORIGIN_EDIT = "ORIGIN_USE_DIRECT_EDIT"
ROLE_REQUIRED = "TERRITORIAL_SUPERVISOR_REQUIRED"
FIELD_NOT_ALLOWED = "FIELD_NOT_ALLOWED"
NO_CHANGES = "NO_CHANGES"
PENDING_EXISTS = "TERRITORIAL_CHANGE_PENDING"
NOT_PENDING = "REQUEST_NOT_PENDING"
BLOCKED = "TERRITORIAL_CHANGE_BLOCKED"
NOT_INSTALLED = "TERRITORIAL_CHANGES_NOT_INSTALLED"


class TerritorialChangeCreate(BaseModel):
    """Il corpo della proposta: solo `changes` (campo -> nuovo valore) e una nota.
    Identita', ruolo, agenzie, territorio e date li decide il server."""
    model_config = ConfigDict(extra="forbid")
    changes: dict[str, Any]
    note: str | None = Field(None, max_length=500)


class TerritorialChangeApprove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(None, max_length=500)


class TerritorialChangeReject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(..., max_length=500)


def _errore(classe, messaggio: str, code: str, **extra):
    e = classe(messaggio)
    e.code = code
    e.extra = extra
    return e


def _installato(cur) -> None:
    cur.execute("SELECT to_regclass('public.property_territorial_change_requests') IS NOT NULL AS ok")
    if not cur.fetchone()["ok"]:
        raise _errore(ConflictError, "Funzione non ancora disponibile su questo database (migration 104)", NOT_INSTALLED)


_COLONNE = ("id, agency_id, city, record_kind, code, title, address, civic_number, commercial_status, "
            "archived_at, updated_at, to_jsonb(properties)->>'deleted_at' AS deleted_at, "
            + ", ".join(f for f in ALLOWED_FIELDS if f != "title"))


def _immobile(cur, property_id: int, *, lock: str | None = None) -> dict | None:
    cur.execute(f"SELECT {_COLONNE} FROM properties WHERE id = %s{' FOR ' + lock if lock else ''}", (property_id,))
    riga = cur.fetchone()
    return dict(riga) if riga else None


def _valore(v):
    """Forma confrontabile e serializzabile (Decimal -> str, ecc.)."""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    return str(v)


def fingerprint(prop: dict) -> str:
    """La versione dell'immobile su cui e' costruita una proposta: cambia con
    QUALUNQUE scrittura (updated_at) e con agenzia, comune, tipo di scheda,
    archiviazione, Cestino e i campi della whitelist."""
    base = {k: _valore(prop.get(k)) for k in ("id", "agency_id", "city", "record_kind", "archived_at",
                                                "deleted_at", "updated_at", *ALLOWED_FIELDS)}
    return hashlib.sha256(json.dumps(base, sort_keys=True, default=str).encode()).hexdigest()


def _etichetta(prop: dict) -> str:
    luogo = " ".join(x for x in (prop.get("address"), prop.get("civic_number")) if x)
    parti = [p for p in (prop.get("code"), prop.get("title"), ", ".join(x for x in (luogo, prop.get("city")) if x)) if p]
    return " · ".join(parti) or f"Immobile #{prop['id']}"


def _valida(changes: Any) -> dict[str, Any]:
    if not isinstance(changes, dict) or not changes:
        raise _errore(ValidationError, "Indica almeno un campo da modificare", NO_CHANGES)
    vietati = sorted(set(changes) - set(ALLOWED_FIELDS))
    if vietati:
        raise _errore(ValidationError, "Campi non modificabili con una proposta territoriale: " + ", ".join(vietati),
                      FIELD_NOT_ALLOWED, fields=vietati)
    try:
        modello = PropertyUpdate.model_validate(changes)
    except Exception as exc:                          # pydantic: tipo o limite non validi
        raise _errore(ValidationError, f"Valori non validi: {exc}", "VALIDATION_ERROR") from exc
    dati = modello.model_dump(exclude_unset=True)
    if "title" in dati and not (dati["title"] or "").strip():
        raise _errore(ValidationError, "Il titolo non puo' essere vuoto", "VALIDATION_ERROR")
    try:
        validate_energy_class(dati.get("energy_class"))
    except ValueError as exc:
        raise _errore(ValidationError, str(exc), "VALIDATION_ERROR") from exc
    return {k: dati[k] for k in ALLOWED_FIELDS if k in dati}


def _vista(r: dict) -> dict[str, Any]:
    v = dict(r)
    v["fields"] = [{"field": k, "label": FIELD_LABELS.get(k, k), "old": c.get("old"), "new": c.get("new")}
                   for k, c in (r.get("changes") or {}).items()]
    return v


_SELECT_RICHIESTA = (
    "SELECT r.*, ao.name AS origin_agency_name, ac.name AS competent_agency_name, "
    "trim(concat_ws(' ', ur.first_name, ur.last_name)) AS requested_by_name, "
    "trim(concat_ws(' ', ud.first_name, ud.last_name)) AS decided_by_name "
    "FROM property_territorial_change_requests r "
    "JOIN agencies ao ON ao.id = r.origin_agency_id JOIN agencies ac ON ac.id = r.competent_agency_id "
    "LEFT JOIN operator_users ur ON ur.id = r.requested_by_user_id "
    "LEFT JOIN operator_users ud ON ud.id = r.decided_by_user_id ")


def _evento(cur, request_id: int, property_id: int, action: str, ctx, agency_id: int | None, metadata: dict) -> None:
    cur.execute("INSERT INTO property_territorial_change_events (request_id, property_id, action, actor_user_id, "
                "actor_agency_id, metadata) VALUES (%s, %s, %s, %s, %s, %s)",
                (request_id, property_id, action, getattr(ctx, "user_id", None), agency_id, Json(metadata)))


# ---------------------------------------------------------------------------
# TENANT: proposta e consultazione
# ---------------------------------------------------------------------------

def _puo_proporre(ctx) -> bool:
    return getattr(ctx, "role", None) == "agency_owner" and not getattr(ctx, "is_platform_admin", False)


def property_requests(ctx, property_id: int) -> dict[str, Any]:
    """Le proposte di QUESTO immobile, per chi lo legge (origine o competente),
    piu' i campi proponibili e se la sessione puo' proporre."""
    accesso = territorial_access.property_access(ctx, property_id)
    if accesso is None:
        raise NotFoundError(f"property {property_id} not found")
    with core_cursor() as (_, cur):
        _installato(cur)
        cur.execute(_SELECT_RICHIESTA + "WHERE r.property_id = %s ORDER BY r.requested_at DESC, r.id DESC LIMIT 50",
                    (property_id,))
        voci = [_vista(dict(r)) for r in cur.fetchall()]
    in_attesa = any(v["status"] == "pending" for v in voci)
    return {"property_id": property_id, "access": accesso, "items": voci,
            "can_propose": accesso["mode"] == territorial_access.MODE_TERRITORIAL and _puo_proporre(ctx) and not in_attesa,
            "allowed_fields": [{"field": f, "label": FIELD_LABELS[f]} for f in ALLOWED_FIELDS]}


def propose(ctx, property_id: int, changes: dict, note: str | None = None) -> dict[str, Any]:
    agency_id = ctx.require_agency()
    accesso = territorial_access.property_access(ctx, property_id)
    if accesso is None:
        raise NotFoundError(f"property {property_id} not found")      # non rivela l'esistenza
    if accesso["mode"] == territorial_access.MODE_ORIGIN:
        raise _errore(ValidationError, "Immobile della tua agenzia: modificalo direttamente dalla scheda", ORIGIN_EDIT)
    if not _puo_proporre(ctx):
        raise _errore(PermissionDenied, "Solo il Supervisore dell'agenzia competente puo' proporre modifiche", ROLE_REQUIRED)
    if note is not None and len(note) > _NOTE_MAX:
        raise _errore(ValidationError, f"Nota troppo lunga (massimo {_NOTE_MAX} caratteri)", "VALIDATION_ERROR")
    nuovi = _valida(changes)
    with core_cursor(commit=True) as (_, cur):
        _installato(cur)
        prop = _immobile(cur, property_id, lock="SHARE")
        territorio = territorial_access._competenza(cur, prop and prop["city"], agency_id)
        if prop is None or prop["deleted_at"] is not None or prop["agency_id"] == agency_id or territorio is None:
            raise NotFoundError(f"property {property_id} not found")
        if prop["record_kind"] != "crm" or prop["archived_at"] is not None or prop["commercial_status"] == "archived":
            raise _errore(ConflictError, "Questo immobile non accetta proposte (censimento o archiviato)", NOT_PROPOSABLE)
        diff = {k: {"old": _valore(prop.get(k)), "new": _valore(v)} for k, v in nuovi.items()
                if _valore(prop.get(k)) != _valore(v)}
        if not diff:
            raise _errore(ValidationError, "Nessuna differenza rispetto ai dati attuali", NO_CHANGES)
        cur.execute("SAVEPOINT proposta")
        try:
            cur.execute(
                "INSERT INTO property_territorial_change_requests (property_id, property_label, origin_agency_id, "
                "competent_agency_id, territory_id, territory_label, changes, base_fingerprint, request_note, "
                "requested_by_user_id, requested_by_role) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (property_id, _etichetta(prop), prop["agency_id"], agency_id, territorio["id"], territorio["label"],
                 Json(diff), fingerprint(prop), (note or "").strip() or None, ctx.user_id, ctx.role))
        except errors.UniqueViolation as exc:
            cur.execute("ROLLBACK TO SAVEPOINT proposta")
            raise _errore(ConflictError, "C'e' gia' una proposta in attesa per questo immobile", PENDING_EXISTS) from exc
        rid = cur.fetchone()["id"]
        _evento(cur, rid, property_id, "proposed", ctx, agency_id,
                {"fields": sorted(diff), "base_fingerprint": fingerprint(prop), "territory_id": territorio["id"]})
        cur.execute(_SELECT_RICHIESTA + "WHERE r.id = %s", (rid,))
        return _vista(dict(cur.fetchone()))


# ---------------------------------------------------------------------------
# SUPER ADMIN: elenco, dettaglio, approvazione, rifiuto
# ---------------------------------------------------------------------------

def _super_admin(cur, ctx) -> None:
    if not getattr(ctx, "is_platform_admin", False) or getattr(ctx, "user_id", None) is None:
        raise _errore(PermissionDenied, "Operazione riservata al Super Admin della piattaforma", "SUPER_ADMIN_REQUIRED")
    # ADM-1: il SOLO Super Admin generale designato, poi il ruolo riletto nel database.
    require_super_admin(ctx, audit_action=_AUDIT_ACTION)
    require_super_admin_db(cur, ctx, audit_action=_AUDIT_ACTION)


def _blocchi(cur, r: dict, prop: dict | None) -> list[dict[str, str]]:
    """Le verifiche dell'approvazione, nell'ordine; [] = si puo' applicare."""
    b = []
    if prop is None or prop["deleted_at"] is not None:
        return [{"code": "PROPERTY_IN_TRASH", "label": "L'immobile non esiste piu' o e' nel Cestino"}]
    if prop["agency_id"] != r["origin_agency_id"]:
        b.append({"code": "PROPERTY_TRANSFERRED", "label": "L'immobile appartiene ora a un'altra agenzia"})
    if prop["record_kind"] != "crm" or prop["archived_at"] is not None or prop["commercial_status"] == "archived":
        b.append({"code": "PROPERTY_NOT_EDITABLE", "label": "L'immobile e' stato archiviato o non e' una scheda CRM"})
    if fingerprint(prop) != r["base_fingerprint"]:
        b.append({"code": "PROPERTY_CHANGED", "label": "L'immobile e' stato modificato dopo la proposta"})
    territorio = territorial_access._competenza(cur, prop["city"], r["competent_agency_id"])
    if territorio is None or territorio["id"] != r["territory_id"]:
        b.append({"code": "COMPETENCE_CHANGED", "label": "L'agenzia proponente non e' piu' competente per questo Comune"})
    cur.execute("SELECT 1 FROM agency_memberships m JOIN operator_users u ON u.id = m.operator_user_id "
                "JOIN agencies a ON a.id = m.agency_id WHERE m.agency_id = %s AND m.operator_user_id = %s "
                "AND m.role = 'agency_owner' AND m.status = 'active' AND u.status = 'active' AND a.status = 'active'",
                (r["competent_agency_id"], r["requested_by_user_id"]))
    if r["requested_by_user_id"] is None or cur.fetchone() is None:
        b.append({"code": "PROPOSER_NOT_ENABLED", "label": "Il proponente non e' piu' Supervisore attivo dell'agenzia competente"})
    return b


def admin_list(ctx, *, status: str = "pending", limit: int = 50, offset: int = 0) -> dict[str, Any]:
    if status not in (*STATUSES, "all"):
        raise _errore(ValidationError, "Stato non valido", "VALIDATION_ERROR")
    with core_cursor() as (_, cur):
        _installato(cur)
        _super_admin(cur, ctx)
        dove, params = ("", []) if status == "all" else ("WHERE r.status = %s ", [status])
        cur.execute(_SELECT_RICHIESTA + dove + "ORDER BY r.requested_at DESC, r.id DESC LIMIT %s OFFSET %s",
                    [*params, limit + 1, offset])
        voci = [_vista(dict(r)) for r in cur.fetchall()]
    return {"items": voci[:limit], "has_more": len(voci) > limit, "limit": limit, "offset": offset}


def admin_get(ctx, request_id: int) -> dict[str, Any]:
    with core_cursor() as (_, cur):
        _installato(cur)
        _super_admin(cur, ctx)
        cur.execute(_SELECT_RICHIESTA + "WHERE r.id = %s", (request_id,))
        riga = cur.fetchone()
        if riga is None:
            raise NotFoundError(f"territorial change request {request_id} not found")
        r = dict(riga)
        prop = _immobile(cur, r["property_id"])
        attuali = {k: _valore(prop.get(k)) for k in r["changes"]} if prop else {}
        cur.execute("SELECT e.action, e.occurred_at, e.metadata, e.actor_agency_id, "
                    "trim(concat_ws(' ', u.first_name, u.last_name)) AS actor_name "
                    "FROM property_territorial_change_events e LEFT JOIN operator_users u ON u.id = e.actor_user_id "
                    "WHERE e.request_id = %s ORDER BY e.occurred_at, e.id", (request_id,))
        eventi = [dict(e) for e in cur.fetchall()]
        blocchi = _blocchi(cur, r, prop) if r["status"] == "pending" else []
    vista = _vista(r)
    for f in vista["fields"]:
        f["current"] = attuali.get(f["field"])
    vista.update({"events": eventi, "blockers": blocchi,
                  "can_approve": r["status"] == "pending" and not blocchi,
                  "can_reject": r["status"] == "pending"})
    return vista


def approve(ctx, request_id: int, note: str | None = None) -> dict[str, Any]:
    if note is not None and len(note) > _NOTE_MAX:
        raise _errore(ValidationError, f"Nota troppo lunga (massimo {_NOTE_MAX} caratteri)", "VALIDATION_ERROR")
    bloccata = None
    with core_cursor(commit=True) as (_, cur):
        _installato(cur)
        _super_admin(cur, ctx)
        cur.execute("SELECT * FROM property_territorial_change_requests WHERE id = %s FOR UPDATE", (request_id,))
        riga = cur.fetchone()
        if riga is None:
            raise NotFoundError(f"territorial change request {request_id} not found")
        r = dict(riga)
        if r["status"] != "pending":
            raise _errore(ConflictError, "La proposta e' gia' stata decisa", NOT_PENDING, status=r["status"])
        prop = _immobile(cur, r["property_id"], lock="UPDATE")
        blocchi = _blocchi(cur, r, prop)
        if blocchi:
            bloccata = (r, blocchi)
        else:
            nuovi = {k: c["new"] for k, c in r["changes"].items() if k in ALLOWED_FIELDS}
            if set(nuovi) != set(r["changes"]):                       # difesa: mai un campo fuori whitelist
                raise _errore(ConflictError, "La proposta contiene campi non consentiti", FIELD_NOT_ALLOWED)
            cur.execute(f"UPDATE properties SET {', '.join(f'{k} = %s' for k in nuovi)}, updated_at = NOW() "
                        "WHERE id = %s AND agency_id = %s RETURNING updated_at",
                        [*nuovi.values(), r["property_id"], r["origin_agency_id"]])
            aggiornato = cur.fetchone()
            if aggiornato is None:
                raise _errore(ConflictError, "Immobile non aggiornabile", BLOCKED)
            esito = {"applied_fields": sorted(nuovi), "property_updated_at": aggiornato["updated_at"].isoformat()}
            cur.execute("UPDATE property_territorial_change_requests SET status = 'approved', decided_by_user_id = %s, "
                        "decided_at = NOW(), decision_note = %s, applied_at = NOW(), apply_result = %s, updated_at = NOW() "
                        "WHERE id = %s", (ctx.user_id, (note or "").strip() or None, Json(esito), request_id))
            _evento(cur, request_id, r["property_id"], "approved", ctx, None, esito)
            cur.execute(_SELECT_RICHIESTA + "WHERE r.id = %s", (request_id,))
            return _vista(dict(cur.fetchone()))
    # verifica fallita: nulla scritto sull'immobile; il tentativo si registra a parte
    r, blocchi = bloccata
    with core_cursor(commit=True) as (_, cur):
        _evento(cur, request_id, r["property_id"], "approval_blocked", ctx, None,
                {"blockers": [b["code"] for b in blocchi]})
    raise _errore(ConflictError, "Approvazione bloccata: " + "; ".join(b["label"] for b in blocchi), BLOCKED,
                  blockers=blocchi)


def reject(ctx, request_id: int, note: str | None) -> dict[str, Any]:
    nota = (note or "").strip()
    if not nota:
        raise _errore(ValidationError, "Scrivi il motivo del rifiuto", "VALIDATION_ERROR")
    if len(nota) > _NOTE_MAX:
        raise _errore(ValidationError, f"Nota troppo lunga (massimo {_NOTE_MAX} caratteri)", "VALIDATION_ERROR")
    with core_cursor(commit=True) as (_, cur):
        _installato(cur)
        _super_admin(cur, ctx)
        cur.execute("SELECT * FROM property_territorial_change_requests WHERE id = %s FOR UPDATE", (request_id,))
        riga = cur.fetchone()
        if riga is None:
            raise NotFoundError(f"territorial change request {request_id} not found")
        if riga["status"] != "pending":
            raise _errore(ConflictError, "La proposta e' gia' stata decisa", NOT_PENDING, status=riga["status"])
        cur.execute("UPDATE property_territorial_change_requests SET status = 'rejected', decided_by_user_id = %s, "
                    "decided_at = NOW(), decision_note = %s, updated_at = NOW() WHERE id = %s",
                    (ctx.user_id, nota, request_id))
        _evento(cur, request_id, riga["property_id"], "rejected", ctx, None, {"note": nota})
        cur.execute(_SELECT_RICHIESTA + "WHERE r.id = %s", (request_id,))
        return _vista(dict(cur.fetchone()))
