"""ELIMINAZIONE-EDIFICIO-1 - eliminazione CONTROLLATA di un edificio (palazzina)
con tutte le sue unita', autorizzata dal Super Admin (migration 101).

CHI
  * Ogni operatore dell'agenzia (agent, admin, titolare; anche un platform
    admin in acting) RICHIEDE l'eliminazione di un edificio della propria
    agenzia, vede quante unita' sono coinvolte e lo stato della richiesta, e
    ritira la PROPRIA richiesta finche' e' in attesa. Nessun operatore sposta
    da solo un edificio nel Cestino ne' lo ripristina (le rotte tenant di
    CESTINO-EDIFICI-1 rispondono 403: vedi `DIRECT_*`).
  * Il SUPER ADMIN e' l'amministratore della piattaforma: `is_platform_admin`
    letto dalla sessione nel backend (`platform_admin.dependencies.
    require_platform_admin` sul mount e sulla rotta, e di nuovo qui con
    `_super_admin`). Mai un confronto sull'email. Approva o rifiuta, ripristina,
    e dal Cestino avvia la cancellazione definitiva con seconda conferma.
    L'approvazione NON autorizza la cancellazione definitiva: sono due atti.

ISOLAMENTO. Le operazioni tenant leggono solo l'agenzia della sessione (fuori
agenzia: 404). Le operazioni del Super Admin agiscono sull'agenzia scritta
nella richiesta/edificio, mai su un parametro del client, e restano sulla
superficie Platform (`/api/platform`), con una riga di `platform_audit_log`
scritta PRIMA del commit (`audit_then_commit`: senza audit, nessuna modifica).

APPROVAZIONE (una transazione, lock in ordine fisso: richiesta, edificio,
unita' per id): le unita' devono essere esattamente quelle della richiesta e
del dettaglio visto dal Super Admin (impronta), nessun processo aperto su
un'unita' attiva (le regole del Cestino Immobili, `lifecycle.trash_blockers`)
e nessuna pertinenza a cavallo dell'edificio. Poi: unita' attive nel Cestino,
edificio nel Cestino, richiesta `approved` con l'elenco delle unita' spostate
DA QUESTA approvazione e di quelle gia' nel Cestino prima. Nessuna unita'
perde `building_id`: nessun orfano.

RIPRISTINO (Super Admin): l'edificio e SOLO le unita' spostate da quella
approvazione; quelle gia' nel Cestino prima restano nel Cestino.

CANCELLAZIONE DEFINITIVA (Super Admin, solo dal Cestino): seconda conferma
(testo esatto + impronta delle unita' viste) e controllo di TUTTE le
dipendenze, letto dal catalogo delle FK (una FK nuova blocca da sola) piu' i
riferimenti senza FK. Si cancellano solo righe proprie dell'unita' che non
sono storia: il collegamento ai proprietari (i contatti restano), gli
accessori e la riga di creazione dello storico stati. Qualunque altra
dipendenza - attivita', appuntamenti, acquisizioni, incarichi, vendite,
documenti e foto (file esterni), messaggi, sito, portale proprietario,
storico prezzi o di stato, STIMA Voice - BLOCCA con la spiegazione.

STORICO. Tabella `building_deletion_requests` (chi chiede, chi decide e
quando, esito) e `record_lifecycle_events` (richiesta, ritiro, approvazione,
rifiuto, Cestino e ripristino di edificio e unita', cancellazione definitiva).

NESSUN file protetto e' modificato: da `lifecycle.py` e `building_lifecycle.py`
si importano in sola lettura i controlli gia' esistenti.
"""
from __future__ import annotations

import hashlib
import json

from psycopg2 import errors as _pg_errors

from core.database import core_cursor
from core.exceptions import NotFoundError, PermissionDenied, ValidationError
from operator_auth import permissions

from . import building_lifecycle, census, repository
from .lifecycle import (TRASH_REASONS, LifecycleConflict, TrashNotInstalled, _audit, _conflitti_ripristino,
                        _valida_motivo, trash_blockers as _blocchi_unita)

# --- codici -------------------------------------------------------------------
DELETION_REQUEST_PENDING = "DELETION_REQUEST_PENDING"
DELETION_BLOCKED = "DELETION_BLOCKED"
REQUEST_NOT_PENDING = "REQUEST_NOT_PENDING"
REQUEST_STALE = "REQUEST_STALE"
NOT_REQUESTER = "NOT_REQUESTER"
SUPER_ADMIN_REQUIRED = "SUPER_ADMIN_REQUIRED"
BUILDING_IN_TRASH = "BUILDING_IN_TRASH"
NOT_DELETED = "NOT_DELETED"
RESTORE_CONFLICT = "RESTORE_CONFLICT"
NOT_PURGEABLE = "NOT_PURGEABLE"
CONFIRM_MISMATCH = "CONFIRM_MISMATCH"
DIRECT_TRASH_DISABLED = "BUILDING_DELETION_REQUIRES_APPROVAL"
DIRECT_RESTORE_DISABLED = "BUILDING_RESTORE_SUPER_ADMIN"

PURGE_CONFIRM_TEXT = "ELIMINA DEFINITIVAMENTE"

DELETION_REQUEST_PENDING_MESSAGE = "Per questo edificio c'è già una richiesta di eliminazione in attesa"
DELETION_BLOCKED_MESSAGE = ("L'eliminazione è bloccata: risolvi prima i punti indicati "
                            "(nessuna unità è stata modificata)")
REQUEST_NOT_PENDING_MESSAGE = "La richiesta non è più in attesa"
REQUEST_STALE_MESSAGE = ("Le unità dell'edificio sono cambiate dalla richiesta: serve una nuova verifica. "
                         "Il richiedente può ritirarla e richiederla di nuovo, oppure rifiutala")
NOT_REQUESTER_MESSAGE = "Può ritirare la richiesta solo chi l'ha fatta"
SUPER_ADMIN_REQUIRED_MESSAGE = "Operazione riservata al Super Admin della piattaforma"
BUILDING_IN_TRASH_MESSAGE = "L'edificio è già nel Cestino"
NOT_DELETED_MESSAGE = "L'edificio non è nel Cestino"
RESTORE_CONFLICT_MESSAGE = ("Non si può ripristinare: un'unità ha la stessa identità catastale o la stessa "
                            "richiesta di creazione di un immobile attivo. Correggi prima l'altro immobile")
NOT_PURGEABLE_MESSAGE = ("L'edificio non si può eliminare definitivamente: ci sono dati da conservare "
                         "(nulla è stato cancellato)")
CONFIRM_MISMATCH_MESSAGE = f"Per confermare scrivi esattamente «{PURGE_CONFIRM_TEXT}»"
DIRECT_TRASH_DISABLED_MESSAGE = ("Gli edifici non si spostano direttamente nel Cestino: usa «Richiedi eliminazione». "
                                 "La richiesta la autorizza il Super Admin")
DIRECT_RESTORE_DISABLED_MESSAGE = "Il ripristino degli edifici è riservato al Super Admin della piattaforma"
NOT_INSTALLED_MESSAGE = ("L'eliminazione controllata degli edifici non è ancora disponibile su questo database "
                         "(migration 101 non applicata)")

#: azioni di `platform_audit_log` (spazio dei nomi `platform.`)
AUDIT_APPROVE = "platform.building_deletion.approve"
AUDIT_REJECT = "platform.building_deletion.reject"
AUDIT_RESTORE = "platform.building_deletion.restore"
AUDIT_PURGE = "platform.building_deletion.purge"

#: righe proprie dell'unita' che la cancellazione definitiva toglie insieme a lei
#: (CASCADE). Il valore e' il predicato delle righe che invece BLOCCANO.
CONTENUTI_PROPRI = {
    "property_contacts": None,          # il collegamento: il contatto resta
    "property_accessories": None,       # cantina/posto auto senza subalterno: dati dell'unita'
    "property_status_history": "old_value IS NOT NULL",   # solo la riga di creazione non e' storia
}
ETICHETTE = {
    "activities": "Attività o interazioni (storico commerciale)",
    "acquisitions": "Acquisizioni",
    "appointments": "Appuntamenti",
    "property_sales": "Vendite",
    "stima_acquisitions": "Incarichi da stima",
    "property_documents": "Documenti (file esterni)",
    "property_photos": "Foto (file esterni)",
    "property_price_history": "Storico dei prezzi",
    "property_status_history": "Storico degli stati",
    "property_site_sources": "Collegamento a una richiesta dal sito",
    "property_leads": "Collegamenti a lead",
    "property_visits": "Visite",
    "matches": "Abbinamenti con acquirenti",
    "match_runs": "Calcoli di abbinamento",
    "match_exclusions": "Esclusioni di abbinamento",
    "seller_timeline_events": "Storico Venditore",
    "site_submissions": "Invii dal sito",
    "buy_request_history": "Storico richieste acquirente",
    "buy_request_interactions": "Interazioni con acquirenti",
    "owner_audit_log": "Registro del portale proprietario",
    "owner_feedback": "Riscontri del portale proprietario",
    "owner_notifications": "Notifiche del portale proprietario",
    "owner_property_access": "Accessi del portale proprietario",
    "owner_publications": "Pubblicazioni al proprietario",
    "properties": "Pertinenze collegate fuori dall'edificio",
}
#: riferimenti senza FK (entity_type, entity_id)
_RIFERIMENTI_LIBERI = ("voice_step_undos", "flow_events", "flow_executions", "flow_suppressions", "owner_audit_log")


class DeletionNotInstalled(TrashNotInstalled):
    """503 BUILDING_DELETION_NOT_INSTALLED (codice deployato prima della 101)."""
    code = "BUILDING_DELETION_NOT_INSTALLED"


class _Vietato(PermissionDenied):
    def __init__(self, message, code, **extra):
        super().__init__(message)
        self.code = code
        self.extra = extra


class _NonValido(ValidationError):
    def __init__(self, message, code, **extra):
        super().__init__(message)
        self.code = code
        self.extra = extra


# ---------------------------------------------------------------------------
# soglie e utilita'
# ---------------------------------------------------------------------------

def _installato(cur) -> None:
    building_lifecycle._cestino_installato(cur)
    cur.execute("SELECT to_regclass('public.building_deletion_requests') IS NOT NULL AS pronto")
    if not cur.fetchone()["pronto"]:
        raise DeletionNotInstalled(NOT_INSTALLED_MESSAGE)


def _super_admin(ctx) -> None:
    """Il controllo del ruolo, nel backend: il flag della sessione."""
    if not bool(getattr(ctx, "is_platform_admin", False)) or getattr(ctx, "user_id", None) is None:
        raise _Vietato(SUPER_ADMIN_REQUIRED_MESSAGE, SUPER_ADMIN_REQUIRED)


def building_label(edificio: dict) -> str:
    via = " ".join(x for x in (edificio.get("address"), edificio.get("civic_number")) if x)
    luogo = ", ".join(x for x in (via or None, edificio.get("city")) if x)
    if edificio.get("name"):
        return f"{edificio['name']} · {luogo}" if luogo else edificio["name"]
    return luogo or f"Edificio #{edificio['id']}"


def _unit_label(u: dict) -> str:
    posto = " ".join(x for x in (f"piano {u['floor']}" if u.get("floor") else None,
                                 f"int. {u['internal_number']}" if u.get("internal_number") else None) if x)
    return " · ".join(x for x in (u.get("code") or f"#{u['id']}", u.get("title"), posto or None) if x)


def _unita(cur, building_id: int, *, lock: bool = False) -> list[dict]:
    """TUTTE le righe di `properties` dell'edificio (attive, archiviate,
    pertinenze, «intero stabile», nel Cestino), per id. Con `lock` la riga
    intera, bloccata (FOR UPDATE, in ordine di id)."""
    if lock:
        cur.execute("SELECT * FROM properties WHERE building_id = %s ORDER BY id FOR UPDATE", (building_id,))
        righe = [dict(r) for r in cur.fetchall()]
        for r in righe:
            r["in_trash"] = r.get("deleted_at") is not None
        return righe
    return building_lifecycle._unita_collegate(cur, building_id)


def fingerprint(unita: list[dict]) -> str:
    """L'impronta dell'insieme delle unita' e del loro stato rispetto al
    Cestino: cambia se un'unita' entra, esce o va/torna dal Cestino."""
    voci = sorted([int(u["id"]), bool(u["in_trash"])] for u in unita)
    return hashlib.sha256(json.dumps(voci, separators=(",", ":")).encode()).hexdigest()


def _riepilogo(unita: list[dict]) -> dict:
    conti = {"active": 0, "archived": 0, "trash": 0}
    voci = []
    for u in unita:
        stato = building_lifecycle._stato_unita(u)
        conti[stato] += 1
        voci.append({"id": u["id"], "label": _unit_label(u), "state": stato, "in_trash": bool(u["in_trash"]),
                     "pertinenza": bool(u.get("pertinenza") or u.get("parent_property_id")),
                     "whole_building": bool(u.get("whole_building"))})
    return {"total": len(unita), **conti, "items": voci}


def _edificio(cur, building_id: int, *, agency_id: int | None = None, lock: bool = False) -> dict:
    filtro, params = "id = %s", [building_id]
    if agency_id is not None:
        filtro += " AND agency_id = %s"
        params.append(agency_id)
    cur.execute(f"SELECT * FROM buildings WHERE {filtro}{' FOR UPDATE' if lock else ''}", params)
    riga = repository.row(cur.fetchone())
    if riga is None:
        raise NotFoundError(f"building {building_id} not found")
    return riga


def _evento(cur, ctx, agency_id: int, entity_type: str, entity_id: int, azione: str, *, reason=None, note=None,
            before: dict | None = None, metadata: dict | None = None) -> None:
    meta = {"actor_role": "platform_admin" if getattr(ctx, "is_platform_admin", False) and azione not in (
                "deletion_requested", "deletion_withdrawn") else getattr(ctx, "role", None),
            "platform_admin": bool(getattr(ctx, "is_platform_admin", False)), **(metadata or {})}
    cur.execute("INSERT INTO record_lifecycle_events (agency_id, entity_type, entity_id, action, reason_code, note, "
                "actor_user_id, before_state, metadata) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)",
                (agency_id, entity_type, entity_id, azione, reason, note, getattr(ctx, "user_id", None),
                 json.dumps(before or {}, default=str), json.dumps(meta, default=str)))


# ---------------------------------------------------------------------------
# BLOCCHI dello spostamento nel Cestino (richiesta e approvazione)
# ---------------------------------------------------------------------------

def _pertinenze_a_cavallo(cur, building_id: int) -> list[dict]:
    """Pertinenze collegate fra questo edificio e fuori (o senza edificio), in
    entrambe le direzioni e in qualunque stato: spostarne meta' nel Cestino
    lascerebbe un collegamento a cavallo. Nel dubbio: blocco."""
    cur.execute("""
        SELECT c.id, c.code, c.title, c.floor, c.internal_number, c.parent_property_id, c.building_id,
               'child_outside' AS verso
          FROM properties c
          JOIN properties p ON p.id = c.parent_property_id
         WHERE p.building_id = %s AND c.building_id IS DISTINCT FROM %s
        UNION ALL
        SELECT u.id, u.code, u.title, u.floor, u.internal_number, u.parent_property_id, u.building_id,
               'parent_outside' AS verso
          FROM properties u
          JOIN properties p ON p.id = u.parent_property_id
         WHERE u.building_id = %s AND p.building_id IS DISTINCT FROM %s
         ORDER BY 1""", (building_id, building_id, building_id, building_id))
    righe = [dict(r) for r in cur.fetchall()]
    if not righe:
        return []
    voci = [{"property_id": r["id"], "label": _unit_label(r) + (
                " — pertinenza fuori dall'edificio di un'unità di questo edificio" if r["verso"] == "child_outside"
                else " — pertinenza di un'unità fuori da questo edificio"),
             "href": f"#/immobili/{r['id']}"} for r in righe]
    return [{"code": "PERTINENZA_FUORI_EDIFICIO",
             "label": ("Pertinenze collegate a cavallo dell'edificio: scollegale o spostale prima, "
                       "così nessun collegamento resta orfano"),
             "items": voci}]


def _blocchi_cestino(cur, agency_id: int, unita: list[dict]) -> list[dict]:
    """I blocchi per spostare nel Cestino edificio e unita' attive. `unita`
    con la riga intera (lock) o la sola forma di elenco: per i processi di
    ogni unita' attiva si rilegge la riga intera."""
    blocchi = []
    for u in unita:
        if u["in_trash"]:
            continue
        riga = u if "agency_id" in u else repository.row(_riga_intera(cur, u["id"]))
        for b in _blocchi_unita(cur, agency_id, riga):
            blocchi.append({**b, "property_id": u["id"], "property_label": _unit_label(riga),
                            "href": f"#/immobili/{u['id']}"})
    return blocchi


def _riga_intera(cur, property_id: int):
    cur.execute("SELECT * FROM properties WHERE id = %s", (property_id,))
    return cur.fetchone()


def _tutti_i_blocchi(cur, agency_id: int, building_id: int, unita: list[dict]) -> list[dict]:
    return _blocchi_cestino(cur, agency_id, unita) + _pertinenze_a_cavallo(cur, building_id)


# ---------------------------------------------------------------------------
# lettura della richiesta
# ---------------------------------------------------------------------------

_NOME = "COALESCE(NULLIF(BTRIM(CONCAT_WS(' ', {a}.first_name, {a}.last_name)), ''), split_part({a}.email, '@', 1))"
_COLONNE_RICHIESTA = f"""
    r.*, a.name AS agency_name,
    (SELECT {_NOME.format(a='u')} FROM operator_users u WHERE u.id = r.requested_by_user_id) AS requested_by_name,
    (SELECT {_NOME.format(a='u')} FROM operator_users u WHERE u.id = r.decided_by_user_id) AS decided_by_name,
    (SELECT {_NOME.format(a='u')} FROM operator_users u WHERE u.id = r.restored_by_user_id) AS restored_by_name,
    (SELECT {_NOME.format(a='u')} FROM operator_users u WHERE u.id = r.purged_by_user_id) AS purged_by_name,
    b.id IS NOT NULL AS building_exists,
    (to_jsonb(b) ->> 'deleted_at') IS NOT NULL AS building_in_trash"""


def _vista_richiesta(r: dict | None) -> dict | None:
    if r is None:
        return None
    r = dict(r)
    istantanea = r.get("units_snapshot") or []
    r["units_total"] = len(istantanea)
    r["units_already_trashed"] = sum(1 for u in istantanea if u.get("in_trash"))
    for k in ("trashed_unit_ids", "already_trashed_unit_ids"):
        if r.get(k) is not None:
            r[k] = [int(x) for x in r[k]]
    return r


def _richiesta(cur, request_id: int, *, agency_id: int | None = None, lock: bool = False) -> dict:
    filtro, params = "r.id = %s", [request_id]
    if agency_id is not None:
        filtro += " AND r.agency_id = %s"
        params.append(agency_id)
    if lock:
        cur.execute(f"SELECT r.* FROM building_deletion_requests r WHERE {filtro} FOR UPDATE", params)
        riga = cur.fetchone()
        if riga is None:
            raise NotFoundError(f"deletion request {request_id} not found")
    cur.execute(f"SELECT {_COLONNE_RICHIESTA} FROM building_deletion_requests r JOIN agencies a ON a.id = r.agency_id "
                f"LEFT JOIN buildings b ON b.id = r.building_id WHERE {filtro}", params)
    riga = cur.fetchone()
    if riga is None:
        raise NotFoundError(f"deletion request {request_id} not found")
    return _vista_richiesta(riga)


def _ultima_richiesta(cur, agency_id: int, building_id: int) -> dict | None:
    cur.execute(f"SELECT {_COLONNE_RICHIESTA} FROM building_deletion_requests r JOIN agencies a ON a.id = r.agency_id "
                "LEFT JOIN buildings b ON b.id = r.building_id "
                "WHERE r.agency_id = %s AND r.building_id = %s ORDER BY r.requested_at DESC, r.id DESC LIMIT 1",
                (agency_id, building_id))
    return _vista_richiesta(cur.fetchone())


def _gruppo_nel_cestino(cur, building_id: int, *, lock: bool = False) -> dict | None:
    cur.execute("SELECT * FROM building_deletion_requests WHERE building_id = %s AND status = 'approved' "
                f"AND restored_at IS NULL AND purged_at IS NULL{' FOR UPDATE' if lock else ''}", (building_id,))
    return _vista_richiesta(cur.fetchone())


# ---------------------------------------------------------------------------
# TENANT: stato, richiesta, ritiro
# ---------------------------------------------------------------------------

def request_status(ctx, building_id: int) -> dict:
    """Per la scheda dell'edificio: le unita' coinvolte, i blocchi, se si puo'
    chiedere e l'ultima richiesta (qualunque esito)."""
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _installato(cur)
        edificio = _edificio(cur, building_id, agency_id=agency_id)
        unita = _unita(cur, building_id)
        ultima = _ultima_richiesta(cur, agency_id, building_id)
        in_cestino = building_lifecycle.in_trash(edificio)
        blocchi = [] if in_cestino else _tutti_i_blocchi(cur, agency_id, building_id, unita)
        if ultima and ultima["status"] == "pending":
            ultima["stale"] = ultima["units_fingerprint"] != fingerprint(unita)
    in_attesa = bool(ultima and ultima["status"] == "pending")
    return {"building_id": building_id, "building_label": building_label(edificio), "in_trash": in_cestino,
            "units": _riepilogo(unita), "fingerprint": fingerprint(unita), "blockers": blocchi,
            "can_request": not in_cestino and not in_attesa and not blocchi,
            "can_withdraw": in_attesa and ultima.get("requested_by_user_id") == getattr(ctx, "user_id", None),
            "request": ultima, "reasons": list(TRASH_REASONS)}


def create_request(ctx, building_id: int, reason_code="created_by_mistake", note=None) -> dict:
    agency_id = ctx.require_agency()
    reason_code, note = _valida_motivo(reason_code, note)
    with core_cursor(commit=True) as (_, cur):
        _installato(cur)
        cur.execute("SELECT * FROM buildings WHERE id = %s AND agency_id = %s FOR SHARE", (building_id, agency_id))
        edificio = repository.row(cur.fetchone())
        if edificio is None:
            raise NotFoundError(f"building {building_id} not found")
        if building_lifecycle.in_trash(edificio):
            raise LifecycleConflict(BUILDING_IN_TRASH_MESSAGE, BUILDING_IN_TRASH)
        cur.execute("SELECT id FROM building_deletion_requests WHERE building_id = %s AND status = 'pending'",
                    (building_id,))
        gia = cur.fetchone()
        if gia:
            raise LifecycleConflict(DELETION_REQUEST_PENDING_MESSAGE, DELETION_REQUEST_PENDING, request_id=gia["id"])
        unita = _unita(cur, building_id)
        blocchi = _tutti_i_blocchi(cur, agency_id, building_id, unita)
        if blocchi:
            raise LifecycleConflict(DELETION_BLOCKED_MESSAGE, DELETION_BLOCKED, blockers=blocchi)
        istantanea = [{"id": u["id"], "in_trash": bool(u["in_trash"]), "label": _unit_label(u),
                       "state": building_lifecycle._stato_unita(u)} for u in unita]
        try:
            cur.execute("SAVEPOINT richiesta")
            cur.execute("INSERT INTO building_deletion_requests (agency_id, building_id, building_label, reason_code, "
                        "request_note, requested_by_user_id, requested_by_role, units_snapshot, units_fingerprint) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s) RETURNING id",
                        (agency_id, building_id, building_label(edificio), reason_code, note,
                         getattr(ctx, "user_id", None), getattr(ctx, "role", None), json.dumps(istantanea),
                         fingerprint(unita)))
        except _pg_errors.UniqueViolation as exc:       # corsa con una richiesta concorrente
            cur.execute("ROLLBACK TO SAVEPOINT richiesta")
            raise LifecycleConflict(DELETION_REQUEST_PENDING_MESSAGE, DELETION_REQUEST_PENDING) from exc
        rid = cur.fetchone()["id"]
        _evento(cur, ctx, agency_id, "building", building_id, "deletion_requested", reason=reason_code, note=note,
                metadata={"building_deletion_request_id": rid, "units": len(unita),
                          "units_already_in_trash": sum(1 for u in unita if u["in_trash"])})
        riga = _richiesta(cur, rid)
    _audit("deletion_requested", ctx, entity_type="building", entity_id=building_id, request_id=rid)
    return riga


def withdraw_request(ctx, request_id: int) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        _installato(cur)
        r = _richiesta(cur, request_id, agency_id=agency_id, lock=True)
        if r["status"] != "pending":
            raise LifecycleConflict(REQUEST_NOT_PENDING_MESSAGE, REQUEST_NOT_PENDING, status=r["status"])
        if r.get("requested_by_user_id") is None or r["requested_by_user_id"] != getattr(ctx, "user_id", None):
            raise _Vietato(NOT_REQUESTER_MESSAGE, NOT_REQUESTER)
        cur.execute("UPDATE building_deletion_requests SET status = 'withdrawn', decided_by_user_id = %s, "
                    "decided_at = NOW(), updated_at = NOW() WHERE id = %s", (getattr(ctx, "user_id", None), request_id))
        _evento(cur, ctx, agency_id, "building", r["building_id"], "deletion_withdrawn",
                metadata={"building_deletion_request_id": request_id})
        riga = _richiesta(cur, request_id)
    _audit("deletion_withdrawn", ctx, entity_type="building", entity_id=r["building_id"], request_id=request_id)
    return riga


def list_trash_groups(ctx, *, limit: int = 50, offset: int = 0) -> dict:
    """Il Cestino Edifici dell'agenzia, con le unita' raggruppate sotto il
    loro edificio. Owner/admin: tutto; agent: cio' che ha chiesto o spostato
    lui. In sola lettura: ripristino e cancellazione definitiva sono del
    Super Admin."""
    agency_id = ctx.require_agency()
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    vuoto = {"items": [], "has_more": False, "limit": limit, "offset": offset}
    tutti = permissions.sees_all_agency_records(getattr(ctx, "role", None), getattr(ctx, "is_platform_admin", False))
    if not tutti and getattr(ctx, "role", None) != "agent":
        return vuoto
    filtri, params = ["b.agency_id = %s", "(to_jsonb(b) ->> 'deleted_at') IS NOT NULL"], [agency_id]
    if not tutti:
        filtri.append("(b.deleted_by_user_id = %s OR EXISTS (SELECT 1 FROM building_deletion_requests q "
                      "WHERE q.building_id = b.id AND q.requested_by_user_id = %s))")
        params += [getattr(ctx, "user_id", None)] * 2
    with core_cursor() as (_, cur):
        _installato(cur)
        voci = _gruppi(cur, filtri, params, limit, offset)
    return {"items": voci[:limit], "has_more": len(voci) > limit, "limit": limit, "offset": offset}


def _gruppi(cur, filtri: list[str], params: list, limit: int, offset: int) -> list[dict]:
    cur.execute(f"""SELECT b.id, b.agency_id, a.name AS agency_name, b.name, b.building_type, b.address,
                           b.civic_number, b.city, b.province, b.deleted_at, b.deleted_reason, b.deleted_by_user_id,
                           (SELECT {_NOME.format(a='u')} FROM operator_users u
                             WHERE u.id = b.deleted_by_user_id) AS deleted_by_name
                      FROM buildings b JOIN agencies a ON a.id = b.agency_id
                     WHERE {' AND '.join(filtri)}
                     ORDER BY b.deleted_at DESC, b.id DESC LIMIT %s OFFSET %s""", params + [limit + 1, offset])
    edifici = [dict(r) for r in cur.fetchall()]
    for e in edifici:
        unita = _unita(cur, e["id"])
        gruppo = _gruppo_nel_cestino(cur, e["id"])
        spostate = set(gruppo["trashed_unit_ids"]) if gruppo else set()
        e["label"] = building_label(e)
        e["units"] = [{**v, "moved_by_approval": v["id"] in spostate}
                      for v in _riepilogo(unita)["items"]]
        e["units_total"] = len(unita)
        e["units_moved_by_approval"] = sum(1 for v in e["units"] if v["moved_by_approval"])
        e["fingerprint"] = fingerprint(unita)
        if gruppo:
            cur.execute(f"SELECT {_COLONNE_RICHIESTA} FROM building_deletion_requests r "
                        "JOIN agencies a ON a.id = r.agency_id LEFT JOIN buildings b ON b.id = r.building_id "
                        "WHERE r.id = %s", (gruppo["id"],))
            gruppo = _vista_richiesta(cur.fetchone())
        e["request"] = gruppo
    return edifici


def scheda_tenant(esito):
    """La scheda di un edificio nel Cestino vista da un operatore: il
    ripristino non e' suo (Super Admin). Nessun altro campo cambia."""
    if isinstance(esito, dict) and isinstance(esito.get("trash"), dict):
        esito = {**esito, "trash": {**esito["trash"], "can_restore": False, "restore_by": "super_admin"}}
    return esito


def direct_trash_refused(*_a, **_k):
    raise _Vietato(DIRECT_TRASH_DISABLED_MESSAGE, DIRECT_TRASH_DISABLED)


def direct_restore_refused(*_a, **_k):
    raise _Vietato(DIRECT_RESTORE_DISABLED_MESSAGE, DIRECT_RESTORE_DISABLED)


# ---------------------------------------------------------------------------
# SUPER ADMIN: elenco, dettaglio, approva, rifiuta
# ---------------------------------------------------------------------------

STATI = ("pending", "approved", "rejected", "withdrawn")


def admin_list_requests(ctx, *, status: str | None = "pending", limit: int = 50, offset: int = 0) -> dict:
    _super_admin(ctx)
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    filtri, params = [], []
    if status and status != "all":
        if status not in STATI:
            raise _NonValido("Stato non valido", "INVALID_STATUS", allowed=list(STATI))
        filtri.append("r.status = %s")
        params.append(status)
    dove = f"WHERE {' AND '.join(filtri)}" if filtri else ""
    with core_cursor() as (_, cur):
        _installato(cur)
        cur.execute(f"SELECT {_COLONNE_RICHIESTA} FROM building_deletion_requests r "
                    f"JOIN agencies a ON a.id = r.agency_id LEFT JOIN buildings b ON b.id = r.building_id {dove} "
                    "ORDER BY r.requested_at DESC, r.id DESC LIMIT %s OFFSET %s", params + [limit + 1, offset])
        righe = [_vista_richiesta(r) for r in cur.fetchall()]
        for r in righe:
            if r["status"] == "pending" and r["building_exists"]:
                r["stale"] = r["units_fingerprint"] != fingerprint(_unita(cur, r["building_id"]))
    return {"items": righe[:limit], "has_more": len(righe) > limit, "limit": limit, "offset": offset}


def admin_get_request(ctx, request_id: int) -> dict:
    _super_admin(ctx)
    with core_cursor() as (_, cur):
        _installato(cur)
        r = _richiesta(cur, request_id)
        dettaglio = {"request": r, "building": None, "units": None, "fingerprint": None, "blockers": [],
                     "stale": False}
        if r["building_exists"]:
            edificio = _edificio(cur, r["building_id"], agency_id=r["agency_id"])
            unita = _unita(cur, r["building_id"])
            impronta = fingerprint(unita)
            dettaglio.update({"building": {**edificio, "label": building_label(edificio)},
                              "units": _riepilogo(unita), "fingerprint": impronta})
            if r["status"] == "pending":
                dettaglio["stale"] = impronta != r["units_fingerprint"]
                dettaglio["blockers"] = _tutti_i_blocchi(cur, r["agency_id"], r["building_id"], unita)
    dettaglio["can_approve"] = (r["status"] == "pending" and r["building_exists"] and not dettaglio["stale"]
                                and not dettaglio["blockers"])
    dettaglio["can_reject"] = r["status"] == "pending"
    return dettaglio


def _audit_poi_commit(conn, ctx, azione: str, agency_id: int, building_id: int, metadata: dict) -> None:
    from platform_admin.transaction import audit_then_commit
    audit_then_commit(conn, ctx, action=azione, target_type="building", target_id=building_id,
                      target_agency_id=agency_id, metadata=metadata)


def approve(ctx, request_id: int, expected_fingerprint: str, note=None) -> dict:
    _super_admin(ctx)
    note = (str(note).strip() or None) if note is not None else None
    with core_cursor() as (conn, cur):
        _installato(cur)
        r = _richiesta(cur, request_id, lock=True)            # 1. la richiesta
        if r["status"] != "pending":
            raise LifecycleConflict(REQUEST_NOT_PENDING_MESSAGE, REQUEST_NOT_PENDING, status=r["status"])
        agency_id, building_id = r["agency_id"], r["building_id"]
        edificio = _edificio(cur, building_id, agency_id=agency_id, lock=True)   # 2. l'edificio
        if building_lifecycle.in_trash(edificio):
            raise LifecycleConflict(BUILDING_IN_TRASH_MESSAGE, BUILDING_IN_TRASH)
        unita = _unita(cur, building_id, lock=True)            # 3. le unita', per id
        impronta = fingerprint(unita)
        if impronta != r["units_fingerprint"] or impronta != expected_fingerprint:
            raise LifecycleConflict(REQUEST_STALE_MESSAGE, REQUEST_STALE, units=_riepilogo(unita),
                                    fingerprint=impronta)
        blocchi = _tutti_i_blocchi(cur, agency_id, building_id, unita)
        if blocchi:
            raise LifecycleConflict(DELETION_BLOCKED_MESSAGE, DELETION_BLOCKED, blockers=blocchi)
        attive = [u for u in unita if not u["in_trash"]]
        gia = [u for u in unita if u["in_trash"]]
        utente = ctx.user_id
        # la richiesta approvata PRIMA dell'edificio: la guardia della 101 la cerca
        cur.execute("UPDATE building_deletion_requests SET status = 'approved', decided_by_user_id = %s, "
                    "decided_at = NOW(), decision_note = %s, trashed_unit_ids = %s, already_trashed_unit_ids = %s, "
                    "updated_at = NOW() WHERE id = %s",
                    (utente, note, [u["id"] for u in attive], [u["id"] for u in gia], request_id))
        nota = f"Eliminazione dell'edificio approvata dal Super Admin (richiesta n. {request_id})"
        meta = {"building_deletion_request_id": request_id, "building_id": building_id, "cascade": True}
        for u in attive:
            cur.execute("UPDATE properties SET deleted_at = NOW(), deleted_by_user_id = %s, deleted_reason = %s, "
                        "updated_at = NOW() WHERE id = %s AND agency_id = %s", (utente, r["reason_code"], u["id"], agency_id))
            _evento(cur, ctx, agency_id, "property", u["id"], "trash", reason=r["reason_code"], note=nota,
                    before={k: u.get(k) for k in ("commercial_status", "archived_at", "assigned_agent_id",
                                                  "record_kind")}, metadata=meta)
        cur.execute("UPDATE buildings SET deleted_at = NOW(), deleted_by_user_id = %s, deleted_reason = %s, "
                    "updated_at = NOW() WHERE id = %s AND agency_id = %s", (utente, r["reason_code"], building_id, agency_id))
        _evento(cur, ctx, agency_id, "building", building_id, "trash", reason=r["reason_code"], note=nota,
                before={k: edificio.get(k) for k in ("name", "city", "address", "civic_number", "units_declared",
                                                     "census_status", "archived_at")}, metadata=meta)
        _evento(cur, ctx, agency_id, "building", building_id, "deletion_approved", note=note,
                metadata={"building_deletion_request_id": request_id, "trashed_unit_ids": [u["id"] for u in attive],
                          "already_trashed_unit_ids": [u["id"] for u in gia]})
        _audit_poi_commit(conn, ctx, AUDIT_APPROVE, agency_id, building_id,
                          {"request_id": request_id, "trashed_units": len(attive), "already_trashed_units": len(gia)})
    with core_cursor() as (_, cur):
        return _richiesta(cur, request_id)


def reject(ctx, request_id: int, note) -> dict:
    _super_admin(ctx)
    note = str(note or "").strip()
    if not note:
        raise _NonValido("Scrivi il motivo del rifiuto", "REJECT_NOTE_REQUIRED")
    if len(note) > 500:
        raise _NonValido("La nota supera 500 caratteri", "REJECT_NOTE_REQUIRED")
    with core_cursor() as (conn, cur):
        _installato(cur)
        r = _richiesta(cur, request_id, lock=True)
        if r["status"] != "pending":
            raise LifecycleConflict(REQUEST_NOT_PENDING_MESSAGE, REQUEST_NOT_PENDING, status=r["status"])
        cur.execute("UPDATE building_deletion_requests SET status = 'rejected', decided_by_user_id = %s, "
                    "decided_at = NOW(), decision_note = %s, updated_at = NOW() WHERE id = %s",
                    (ctx.user_id, note, request_id))
        _evento(cur, ctx, r["agency_id"], "building", r["building_id"], "deletion_rejected", note=note,
                metadata={"building_deletion_request_id": request_id})
        _audit_poi_commit(conn, ctx, AUDIT_REJECT, r["agency_id"], r["building_id"], {"request_id": request_id})
    with core_cursor() as (_, cur):
        return _richiesta(cur, request_id)


# ---------------------------------------------------------------------------
# SUPER ADMIN: Cestino, ripristino, cancellazione definitiva
# ---------------------------------------------------------------------------

def admin_list_trash(ctx, *, limit: int = 50, offset: int = 0) -> dict:
    _super_admin(ctx)
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    with core_cursor() as (_, cur):
        _installato(cur)
        voci = _gruppi(cur, ["(to_jsonb(b) ->> 'deleted_at') IS NOT NULL"], [], limit, offset)
    return {"items": voci[:limit], "has_more": len(voci) > limit, "limit": limit, "offset": offset}


def restore(ctx, building_id: int) -> dict:
    _super_admin(ctx)
    with core_cursor() as (conn, cur):
        _installato(cur)
        edificio = _edificio(cur, building_id, lock=True)            # 1. l'edificio (l'agenzia e' la sua)
        agency_id = edificio["agency_id"]
        if not building_lifecycle.in_trash(edificio):
            raise LifecycleConflict(NOT_DELETED_MESSAGE, NOT_DELETED)
        gruppo = _gruppo_nel_cestino(cur, building_id, lock=True)    # 2. l'approvazione che lo ha spostato
        unita = _unita(cur, building_id, lock=True)                  # 3. le unita', per id
        da_ripristinare = set(gruppo["trashed_unit_ids"]) if gruppo else set()
        tornano = [u for u in unita if u["id"] in da_ripristinare and u["in_trash"]]
        restano = [u for u in unita if u["in_trash"] and u["id"] not in da_ripristinare]
        conflitti = []
        for u in tornano:
            conflitti += [{**c, "property_id": u["id"], "property_label": _unit_label(u)}
                          for c in _conflitti_ripristino(cur, agency_id, u)]
        if conflitti:
            raise LifecycleConflict(RESTORE_CONFLICT_MESSAGE, RESTORE_CONFLICT, conflicts=conflitti)
        meta = {"building_deletion_request_id": gruppo["id"] if gruppo else None, "building_id": building_id}
        cur.execute("UPDATE buildings SET deleted_at = NULL, deleted_by_user_id = NULL, deleted_reason = NULL, "
                    "updated_at = NOW() WHERE id = %s AND agency_id = %s RETURNING *", (building_id, agency_id))
        riga = repository.row(cur.fetchone())
        _evento(cur, ctx, agency_id, "building", building_id, "restore",
                before={k: edificio.get(k) for k in ("deleted_at", "deleted_by_user_id", "deleted_reason")},
                metadata={**meta, "restored_unit_ids": [u["id"] for u in tornano],
                          "still_in_trash_unit_ids": [u["id"] for u in restano]})
        try:
            cur.execute("SAVEPOINT unita")
            for u in tornano:
                cur.execute("UPDATE properties SET deleted_at = NULL, deleted_by_user_id = NULL, deleted_reason = NULL, "
                            "updated_at = NOW() WHERE id = %s AND agency_id = %s", (u["id"], agency_id))
                _evento(cur, ctx, agency_id, "property", u["id"], "restore",
                        before={k: u.get(k) for k in ("deleted_at", "deleted_by_user_id", "deleted_reason")},
                        metadata=meta)
        except _pg_errors.UniqueViolation as exc:      # due unita' del gruppo con la stessa identita'
            cur.execute("ROLLBACK TO SAVEPOINT unita")
            raise LifecycleConflict(RESTORE_CONFLICT_MESSAGE, RESTORE_CONFLICT, conflicts=[]) from exc
        if gruppo:
            cur.execute("UPDATE building_deletion_requests SET restored_by_user_id = %s, restored_at = NOW(), "
                        "updated_at = NOW() WHERE id = %s", (ctx.user_id, gruppo["id"]))
        doppioni = building_lifecycle.possible_duplicates(cur, agency_id, riga)
        _audit_poi_commit(conn, ctx, AUDIT_RESTORE, agency_id, building_id,
                          {"request_id": gruppo["id"] if gruppo else None, "restored_units": len(tornano),
                           "still_in_trash_units": len(restano)})
    return {"building": {**riga, "label": building_label(riga)}, "restored_unit_ids": [u["id"] for u in tornano],
            "still_in_trash_unit_ids": [u["id"] for u in restano], "possible_duplicates": doppioni}


# --- dipendenze della cancellazione definitiva --------------------------------

def _fk_verso(cur, tabella: str) -> list[tuple[str, str]]:
    """(tabella, colonna) di ogni FK che punta a `tabella.id`, dal catalogo."""
    cur.execute("""
        SELECT c.conrelid::regclass::text AS tabella, a.attname AS colonna
          FROM pg_constraint c
          CROSS JOIN LATERAL unnest(c.conkey, c.confkey) AS k(col, fcol)
          JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.col
          JOIN pg_attribute fa ON fa.attrelid = c.confrelid AND fa.attnum = k.fcol
         WHERE c.contype = 'f' AND c.confrelid = %s::regclass AND fa.attname = 'id'
         ORDER BY 1, 2""", (f"public.{tabella}",))
    return [(r["tabella"], r["colonna"]) for r in cur.fetchall()]


def _colonne(cur, tabella: str) -> set[str]:
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s",
                (tabella,))
    return {r["column_name"] for r in cur.fetchall()}


def purge_blockers(cur, building_id: int, unita: list[dict]) -> tuple[list[dict], list[dict]]:
    """(blocchi, contenuti propri che verranno cancellati). Ogni blocco:
    `code`, `label`, `count`, `items` (unita' coinvolte)."""
    ids = [int(u["id"]) for u in unita]
    etichetta = {int(u["id"]): _unit_label(u) for u in unita}
    blocchi: dict[str, dict] = {}
    propri: dict[str, int] = {}

    def blocca(codice, testo, righe):
        righe = [(pid, n) for pid, n in righe if n]
        if not righe:
            return
        voce = blocchi.setdefault(codice, {"code": codice, "label": testo, "count": 0, "items": []})
        for pid, n in righe:
            voce["count"] += n
            if pid is not None and pid not in [i["property_id"] for i in voce["items"]]:
                voce["items"].append({"property_id": pid, "label": etichetta.get(pid, f"#{pid}")})

    for u in unita:
        if not u["in_trash"]:
            blocca("UNIT_NOT_IN_TRASH", "Unità non nel Cestino", [(u["id"], 1)])
    if ids:
        for tabella, colonna in _fk_verso(cur, "properties"):
            nome = tabella.split(".")[-1]
            if nome == "properties" and colonna == "parent_property_id":
                cur.execute("SELECT parent_property_id AS pid, count(*) AS n FROM properties "
                            "WHERE parent_property_id = ANY(%s) AND NOT (id = ANY(%s)) GROUP BY 1", (ids, ids))
                blocca("FK_properties", ETICHETTE["properties"], [(r["pid"], r["n"]) for r in cur.fetchall()])
                continue
            if nome == "properties":
                continue
            filtro = f"{colonna} = ANY(%s)"
            if nome in CONTENUTI_PROPRI:
                cur.execute(f"SELECT count(*) AS n FROM {tabella} WHERE {filtro}", (ids,))
                n = cur.fetchone()["n"]
                if n:
                    propri[nome] = propri.get(nome, 0) + n
                if CONTENUTI_PROPRI[nome] is None:
                    continue
                filtro += f" AND {CONTENUTI_PROPRI[nome]}"
            cur.execute(f"SELECT {colonna} AS pid, count(*) AS n FROM {tabella} WHERE {filtro} GROUP BY 1", (ids,))
            righe = [(r["pid"], r["n"]) for r in cur.fetchall()]
            if righe:
                # il registro delle comunicazioni si legge qui solo per CONTARE (dal catalogo), mai per scriverlo
                testo = ("Messaggi e comunicazioni (registro)" if nome.startswith("communication_")
                         else ETICHETTE.get(nome, f"Dati collegati ({nome})"))
                blocca(f"FK_{nome}", testo, righe)
        # incarico sulla riga stessa
        incaricate = [(u["id"], 1) for u in unita
                      if u.get("acquisition_id") is not None or any(u.get(k) is not None for k in
                                                                    ("mandate_type", "mandate_start", "mandate_end"))]
        if incaricate:
            blocca("MANDATE", "Incarico o dati di incarico sull'unità", incaricate)
    # FK verso l'edificio diverse dalle sue unita'
    for tabella, colonna in _fk_verso(cur, "buildings"):
        nome = tabella.split(".")[-1]
        if nome == "properties" and colonna == "building_id":
            continue
        cur.execute(f"SELECT count(*) AS n FROM {tabella} WHERE {colonna} = %s", (building_id,))
        n = cur.fetchone()["n"]
        if n:
            blocca(f"FK_building_{nome}", ETICHETTE.get(nome, f"Dati collegati all'edificio ({nome})"), [(None, n)])
    # riferimenti senza FK
    for tabella in _RIFERIMENTI_LIBERI:
        if not {"entity_type", "entity_id"} <= _colonne(cur, tabella):
            continue
        # entity_id e' BIGINT in alcune tabelle e testo in altre: confronto sul testo
        cur.execute(f"SELECT entity_type, entity_id::text AS eid, count(*) AS n FROM {tabella} "
                    "WHERE (entity_type = 'property' AND entity_id::text = ANY(%s)) "
                    "   OR (entity_type = 'building' AND entity_id::text = %s) GROUP BY 1, 2",
                    ([str(i) for i in ids], str(building_id)))
        righe = [(int(r["eid"]) if r["entity_type"] == "property" else None, r["n"]) for r in cur.fetchall()]
        if righe:
            testo = ("Storico di STIMA Voice" if tabella == "voice_step_undos"
                     else ETICHETTE.get(tabella, f"Riferimenti in {tabella}"))
            blocca("VOICE_HISTORY" if tabella == "voice_step_undos" else f"REF_{tabella}", testo, righe)
    if {"intent", "result", "state"} <= _colonne(cur, "voice_command_steps"):
        cur.execute("SELECT (result ->> 'id')::bigint AS eid, intent, count(*) AS n FROM voice_command_steps "
                    "WHERE state = 'succeeded' AND result ? 'id' AND result ->> 'id' ~ '^[0-9]+$' "
                    "AND ((intent = 'create_unit' AND (result ->> 'id')::bigint = ANY(%s)) "
                    "  OR (intent = 'create_building' AND (result ->> 'id')::bigint = %s)) GROUP BY 1, 2",
                    (ids, building_id))
        righe = [(r["eid"] if r["intent"] == "create_unit" else None, r["n"]) for r in cur.fetchall()]
        if righe:
            blocca("VOICE_HISTORY", "Storico di STIMA Voice (creato con un comando vocale)", righe)
    return list(blocchi.values()), [{"table": t, "label": _ETICHETTE_PROPRI[t], "count": n} for t, n in propri.items()]


_ETICHETTE_PROPRI = {
    "property_contacts": "Collegamenti ai proprietari (i contatti restano)",
    "property_accessories": "Accessori delle unità",
    "property_status_history": "Riga di creazione dello storico stati",
}


def purge_check(ctx, building_id: int) -> dict:
    _super_admin(ctx)
    with core_cursor() as (_, cur):
        _installato(cur)
        edificio = _edificio(cur, building_id)
        unita = _unita(cur, building_id, lock=False)
        unita_intere = [{**dict(_riga_intera(cur, u["id"])), "in_trash": u["in_trash"]} for u in unita]
        if not building_lifecycle.in_trash(edificio):
            blocchi = [{"code": NOT_DELETED, "label": NOT_DELETED_MESSAGE, "count": 1, "items": []}]
            propri = []
        else:
            blocchi, propri = purge_blockers(cur, building_id, unita_intere)
        gruppo = _gruppo_nel_cestino(cur, building_id)
    return {"building": {**edificio, "label": building_label(edificio)}, "units": _riepilogo(unita),
            "fingerprint": fingerprint(unita), "eligible": not blocchi, "blockers": blocchi, "will_delete": propri,
            "request": gruppo, "confirm_text": PURGE_CONFIRM_TEXT}


def purge(ctx, building_id: int, confirm_text: str, expected_fingerprint: str) -> dict:
    _super_admin(ctx)
    if (confirm_text or "").strip() != PURGE_CONFIRM_TEXT:
        raise _NonValido(CONFIRM_MISMATCH_MESSAGE, CONFIRM_MISMATCH)
    with core_cursor() as (conn, cur):
        _installato(cur)
        edificio = _edificio(cur, building_id, lock=True)            # 1. l'edificio
        agency_id = edificio["agency_id"]
        if not building_lifecycle.in_trash(edificio):
            raise LifecycleConflict(NOT_DELETED_MESSAGE, NOT_DELETED)
        gruppo = _gruppo_nel_cestino(cur, building_id, lock=True)    # 2. l'approvazione
        unita = _unita(cur, building_id, lock=True)                  # 3. le unita'
        impronta = fingerprint(unita)
        if impronta != expected_fingerprint:
            raise LifecycleConflict(REQUEST_STALE_MESSAGE, REQUEST_STALE, units=_riepilogo(unita),
                                    fingerprint=impronta)
        blocchi, propri = purge_blockers(cur, building_id, unita)
        if blocchi:
            raise LifecycleConflict(NOT_PURGEABLE_MESSAGE, NOT_PURGEABLE, blockers=blocchi)
        meta = {"building_deletion_request_id": gruppo["id"] if gruppo else None, "building_id": building_id,
                "confirmed": True}
        for u in unita:
            _evento(cur, ctx, agency_id, "property", u["id"], "purge",
                    before={k: u.get(k) for k in ("code", "title", "property_type", "floor", "internal_number",
                                                  "record_kind", "deleted_at", "deleted_reason")}, metadata=meta)
        _evento(cur, ctx, agency_id, "building", building_id, "purge",
                before={k: edificio.get(k) for k in ("name", "city", "address", "civic_number", "deleted_at",
                                                     "deleted_reason")},
                metadata={**meta, "unit_ids": [u["id"] for u in unita], "own_content": propri})
        if gruppo:
            cur.execute("UPDATE building_deletion_requests SET purged_by_user_id = %s, purged_at = NOW(), "
                        "updated_at = NOW() WHERE id = %s", (ctx.user_id, gruppo["id"]))
        # pertinenze prima delle loro principali (FK RESTRICT): mai un UPDATE su righe nel Cestino
        rimaste = {u["id"]: u.get("parent_property_id") for u in unita}
        while rimaste:
            foglie = [pid for pid in rimaste if pid not in set(rimaste.values())]
            if not foglie:                               # ciclo: impossibile per il CHECK, ma mai un DELETE parziale
                raise LifecycleConflict(NOT_PURGEABLE_MESSAGE, NOT_PURGEABLE, blockers=[])
            cur.execute("DELETE FROM properties WHERE id = ANY(%s) AND agency_id = %s AND building_id = %s "
                        "AND deleted_at IS NOT NULL", (foglie, agency_id, building_id))
            if cur.rowcount != len(foglie):
                raise LifecycleConflict(NOT_PURGEABLE_MESSAGE, NOT_PURGEABLE, blockers=[])
            for pid in foglie:
                rimaste.pop(pid)
        cur.execute("DELETE FROM buildings WHERE id = %s AND agency_id = %s AND deleted_at IS NOT NULL",
                    (building_id, agency_id))
        if cur.rowcount != 1:
            raise LifecycleConflict(NOT_PURGEABLE_MESSAGE, NOT_PURGEABLE, blockers=[])
        _audit_poi_commit(conn, ctx, AUDIT_PURGE, agency_id, building_id,
                          {"request_id": gruppo["id"] if gruppo else None, "units": len(unita),
                           "own_content": {p["table"]: p["count"] for p in propri}})
    return {"building_id": building_id, "purged_unit_ids": [u["id"] for u in unita], "deleted_own_content": propri}
