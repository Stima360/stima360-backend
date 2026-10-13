"""CESTINO-GENERALE-1 (CG-1) - il Cestino unico, in SOLA LETTURA.

  * `list_items` / `counts`: l'elenco unificato di tutte le categorie, con
    ricerca, filtri e contatori. Per l'agenzia di chi chiede (le regole di
    visibilita' dei suoi elenchi esistenti) oppure, per il Super Admin
    generale designato, su tutte le agenzie (`scope="platform"`).
  * `relations`: l'anteprima dei collegamenti di un elemento (conteggi per
    tabella letti dal catalogo delle FK), nello stesso perimetro.
  * `building_requests`: le richieste di eliminazione degli edifici
    dell'agenzia (una richiesta IN ATTESA non e' un elemento eliminato).

Nessuna scrittura: ogni funzione apre un cursore in sola lettura
(`SET TRANSACTION READ ONLY`). Il Super Admin e' verificato con i tre
controlli di `platform_admin/super_admin.py`.
"""
from __future__ import annotations

import re
from datetime import date

from core.database import core_cursor
from core.exceptions import NotFoundError, ValidationError
from platform_admin import super_admin as _sa

from . import registry

LIMIT_MAX = 200
Q_MAX = 120
AUDIT_DENIED = "platform.trash.denied"
REASONS = ("created_by_mistake", "duplicate", "invalid_data", "test_record", "other")
REQUEST_STATUSES = ("pending", "approved", "rejected", "withdrawn")

#: etichette delle tabelle collegate (anteprima delle relazioni)
ETICHETTE_RELAZIONI = {
    "activities": "Attività e note",
    "tasks": "Task",
    "appointments": "Appuntamenti",
    "acquisitions": "Acquisizioni",
    "leads": "Opportunità (lead)",
    "property_contacts": "Collegamenti immobile-contatto",
    "property_leads": "Collegamenti immobile-lead",
    "property_documents": "Documenti",
    "property_photos": "Foto",
    "property_visits": "Visite",
    "property_accessories": "Accessori",
    "property_price_history": "Storico prezzi",
    "property_status_history": "Storico stati",
    "property_sales": "Vendite",
    "property_sale_sellers": "Venditori nelle vendite",
    "property_proposals": "Proposte",
    "matches": "Abbinamenti",
    "match_runs": "Calcoli di abbinamento",
    "match_exclusions": "Esclusioni di abbinamento",
    "buy_requests": "Richieste acquirente",
    "buy_request_interactions": "Interazioni acquirente",
    "buy_request_history": "Storico richiesta",
    "buy_request_locations": "Zone cercate",
    "buy_request_typologies": "Tipologie cercate",
    "buy_request_features": "Caratteristiche cercate",
    "buy_request_task_links": "Task collegati",
    "invisible_sale_candidates": "Candidati vendita invisibile",
    "contact_roles": "Ruoli",
    "consent_events": "Consensi (registro)",
    "communication_messages": "Messaggi (registro)",
    "communication_enrollments": "Iscrizioni ai percorsi",
    "communication_automation_controls": "Controlli automazioni",
    "owner_accounts": "Account portale proprietario",
    "owner_property_access": "Accessi del portale proprietario",
    "owner_publications": "Pubblicazioni al proprietario",
    "owner_feedback": "Riscontri del proprietario",
    "owner_notifications": "Notifiche del portale",
    "owner_audit_log": "Registro del portale proprietario",
    "seller_timeline_events": "Storico Venditore",
    "seller_revival_suppressions": "Soppressioni riattivazione",
    "followup_actions": "Azioni di follow-up",
    "next_best_actions": "Azioni consigliate",
    "stima_acquisitions": "Collegamenti stima-acquisizione",
    "property_site_sources": "Collegamenti al sito",
    "site_submissions": "Invii dal sito",
    "properties": "Unità / pertinenze collegate",
}


class TrashInvalid(ValidationError):
    def __init__(self, message, code="INVALID_FILTER", **extra):
        super().__init__(message)
        self.code = code
        self.extra = extra


# ---------------------------------------------------------------------------
# filtri
# ---------------------------------------------------------------------------

def _kinds(valore) -> list[str]:
    if valore in (None, "", "all"):
        return list(registry.CATEGORIE)
    scelte = [v.strip() for v in str(valore).split(",") if v.strip()]
    ignote = [v for v in scelte if v not in registry.CATEGORIE]
    if ignote or not scelte:
        raise TrashInvalid("Categoria non valida", "INVALID_TYPE", allowed=["all", *registry.CATEGORIE])
    return scelte


def _q(valore) -> str | None:
    if valore is None:
        return None
    testo = re.sub(r"\s+", " ", str(valore)).strip().lower()
    if not testo:
        return None
    if len(testo) > Q_MAX:
        raise TrashInvalid(f"La ricerca supera {Q_MAX} caratteri", "INVALID_QUERY")
    return testo


def _like(parola: str) -> str:
    return "%" + parola.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _data(valore, nome):
    if valore in (None, ""):
        return None
    if isinstance(valore, date):
        return valore
    try:
        return date.fromisoformat(str(valore))
    except ValueError as exc:
        raise TrashInvalid(f"Data non valida: {nome}", "INVALID_DATE") from exc


def filtri(*, q=None, reason=None, deleted_by=None, date_from=None, date_to=None, agency_id=None) -> dict:
    """I filtri comuni, validati (mai interpolati: solo parametri)."""
    if reason not in (None, "") and reason not in REASONS:
        raise TrashInvalid("Motivo non valido", "INVALID_REASON", allowed=list(REASONS))
    da, a = _data(date_from, "da"), _data(date_to, "a")
    if da and a and da > a:
        raise TrashInvalid("L'intervallo di date non è valido", "INVALID_DATE")
    return {"q": _q(q), "reason": reason or None, "deleted_by": int(deleted_by) if deleted_by not in (None, "") else None,
            "date_from": da, "date_to": a, "agency_id": int(agency_id) if agency_id not in (None, "") else None}


def _where_filtri(f: dict, *, con_ricerca=True) -> tuple[list[str], list]:
    clausole, params = [], []
    if con_ricerca and f.get("q"):
        for parola in f["q"].split(" "):
            clausole.append("t.search LIKE %s ESCAPE '\\'")
            params.append(_like(parola))
    if f.get("reason"):
        clausole.append("t.deleted_reason = %s")
        params.append(f["reason"])
    if f.get("deleted_by") is not None:
        clausole.append("t.deleted_by_user_id = %s")
        params.append(f["deleted_by"])
    if f.get("date_from"):
        clausole.append("t.deleted_at >= %s::date")
        params.append(f["date_from"])
    if f.get("date_to"):
        clausole.append("t.deleted_at < (%s::date + 1)")
        params.append(f["date_to"])
    if f.get("agency_id") is not None:
        clausole.append("t.agency_id = %s")
        params.append(f["agency_id"])
    return clausole, params


# ---------------------------------------------------------------------------
# la sorgente unificata
# ---------------------------------------------------------------------------

def _installate(cur) -> set[str]:
    """Le categorie il cui Cestino e' installato su questo database (085,
    090, 091, 092): le altre si saltano, mai un errore."""
    cur.execute("SELECT table_name FROM information_schema.columns WHERE table_schema = 'public' "
                "AND column_name = 'deleted_at' AND table_name = ANY(%s)",
                ([c.table for c in registry.CATEGORIE.values()],))
    tabelle = {r["table_name"] for r in cur.fetchall()}
    return {k for k, c in registry.CATEGORIE.items() if c.table in tabelle}


def _sorgente(cur, ctx, kinds: list[str], *, scope: str) -> tuple[str, list]:
    """`SELECT ... UNION ALL SELECT ...` delle categorie richieste, ciascuna
    con la SUA regola di visibilita' (agenzia) o tutte le agenzie (platform)."""
    pronte = _installate(cur)
    parti, params = [], []
    for kind in kinds:
        if kind not in pronte:
            continue
        cat = registry.CATEGORIE[kind]
        if scope == "platform":
            vis, vparams = "TRUE", []
        else:
            vis, vparams = cat.tenant_visibility(ctx)
        parti.append(f"SELECT {cat.select} FROM {registry.FROM[kind]} "
                     f"WHERE {cat.alias}.deleted_at IS NOT NULL AND ({cat.extra_where}) AND ({vis})")
        params += vparams
    if not parti:
        return "SELECT NULL::text AS kind WHERE FALSE", []
    return " UNION ALL ".join(parti), params


_NOME_OPERATORE = ("COALESCE(NULLIF(BTRIM(CONCAT_WS(' ', u.first_name, u.last_name)), ''),"
                   " split_part(u.email, '@', 1))")


def _leggi(ctx, scope: str):
    if scope == "platform":
        _sa.require_super_admin(ctx, audit_action=AUDIT_DENIED)
    elif scope == "agency":
        ctx.require_agency()
    else:  # pragma: no cover - errore di programmazione
        raise ValueError(scope)


def _cursore_lettura(cur, ctx, scope):
    cur.execute("SET TRANSACTION READ ONLY")
    if scope == "platform":
        _sa.require_super_admin_db(cur, ctx, audit_action=AUDIT_DENIED)


def list_items(ctx, *, scope: str = "agency", kind=None, limit: int = 50, offset: int = 0, **kw) -> dict:
    """L'elenco unificato, dal piu' recente. `kind`: una categoria, piu'
    categorie separate da virgola, o tutte."""
    _leggi(ctx, scope)
    kinds = _kinds(kind)
    f = filtri(**kw)
    if scope != "platform":
        f["agency_id"] = None                 # l'agenzia e' quella della sessione, mai un parametro
    limit = max(1, min(int(limit), LIMIT_MAX))
    offset = max(0, int(offset))
    with core_cursor() as (_, cur):
        _cursore_lettura(cur, ctx, scope)
        sorgente, params = _sorgente(cur, ctx, kinds, scope=scope)
        clausole, fparams = _where_filtri(f)
        dove = f"WHERE {' AND '.join(clausole)}" if clausole else ""
        cur.execute(f"""
            SELECT t.kind, t.id, t.agency_id, a.name AS agency_name, t.label, t.line, t.status,
                   t.deleted_at, t.deleted_reason, t.deleted_by_user_id, {_NOME_OPERATORE} AS deleted_by_name,
                   ev.note AS deleted_note, t.extra
              FROM ({sorgente}) t
              JOIN agencies a ON a.id = t.agency_id
              LEFT JOIN operator_users u ON u.id = t.deleted_by_user_id
              LEFT JOIN LATERAL (
                    SELECT e.note FROM record_lifecycle_events e
                     WHERE e.agency_id = t.agency_id AND e.entity_type = t.kind AND e.entity_id = t.id
                       AND e.action = 'trash'
                     ORDER BY e.occurred_at DESC, e.id DESC LIMIT 1) ev ON TRUE
             {dove}
             ORDER BY t.deleted_at DESC, t.kind, t.id DESC
             LIMIT %s OFFSET %s""", params + fparams + [limit + 1, offset])
        righe = [dict(r) for r in cur.fetchall()]
    for r in righe:
        r["href"] = registry.href(r["kind"], r["id"])
        r["kind_label"] = registry.CATEGORIE[r["kind"]].label_it
    return {"items": righe[:limit], "has_more": len(righe) > limit, "limit": limit, "offset": offset,
            "scope": scope}


def counts(ctx, *, scope: str = "agency", **kw) -> dict:
    """I contatori per categoria con gli stessi filtri dell'elenco (tranne
    la categoria), il totale e le richieste di eliminazione edifici in attesa."""
    _leggi(ctx, scope)
    f = filtri(**kw)
    if scope != "platform":
        f["agency_id"] = None
    with core_cursor() as (_, cur):
        _cursore_lettura(cur, ctx, scope)
        pronte = _installate(cur)
        sorgente, params = _sorgente(cur, ctx, list(registry.CATEGORIE), scope=scope)
        clausole, fparams = _where_filtri(f)
        dove = f"WHERE {' AND '.join(clausole)}" if clausole else ""
        cur.execute(f"SELECT t.kind, count(*) AS n FROM ({sorgente}) t {dove} GROUP BY t.kind", params + fparams)
        per = {r["kind"]: int(r["n"]) for r in cur.fetchall()}
        attesa = _richieste_in_attesa(cur, ctx, scope, f.get("agency_id"))
    categorie = [{"kind": k, "label": c.label_it, "count": per.get(k, 0), "installed": k in pronte}
                 for k, c in registry.CATEGORIE.items()]
    return {"total": sum(per.values()), "categories": categorie, "building_requests_pending": attesa,
            "scope": scope}


# ---------------------------------------------------------------------------
# richieste di eliminazione degli edifici (agenzia)
# ---------------------------------------------------------------------------

def _richieste_installate(cur) -> bool:
    cur.execute("SELECT to_regclass('public.building_deletion_requests') IS NOT NULL AS pronto")
    return bool(cur.fetchone()["pronto"])


def _vis_richieste(ctx) -> tuple[str, list]:
    """Owner/admin: le richieste dell'agenzia; agent: le proprie; ogni altro
    ruolo nulla."""
    agenzia = ctx.require_agency()
    if registry._gestisce_agenzia(ctx):
        return "r.agency_id = %s", [agenzia]
    if getattr(ctx, "role", None) == "agent":
        return "r.agency_id = %s AND r.requested_by_user_id = %s", [agenzia, getattr(ctx, "user_id", None)]
    return "FALSE", []


def _richieste_in_attesa(cur, ctx, scope, agency_id=None) -> int:
    if not _richieste_installate(cur):
        return 0
    if scope == "platform":
        vis, params = ("r.agency_id = %s", [agency_id]) if agency_id is not None else ("TRUE", [])
    else:
        vis, params = _vis_richieste(ctx)
    cur.execute(f"SELECT count(*) AS n FROM building_deletion_requests r WHERE r.status = 'pending' AND ({vis})", params)
    return int(cur.fetchone()["n"])


def building_requests(ctx, *, status: str | None = "pending", limit: int = 50, offset: int = 0) -> dict:
    """Le richieste di eliminazione degli edifici dell'agenzia di chi chiede
    (sola lettura: decide il Super Admin). Una richiesta in attesa NON e' un
    elemento nel Cestino."""
    from property import building_deletion as bd

    ctx.require_agency()
    if status not in (None, "", "all") and status not in REQUEST_STATUSES:
        raise TrashInvalid("Stato non valido", "INVALID_STATUS", allowed=["all", *REQUEST_STATUSES])
    limit = max(1, min(int(limit), LIMIT_MAX))
    offset = max(0, int(offset))
    with core_cursor() as (_, cur):
        cur.execute("SET TRANSACTION READ ONLY")
        if not _richieste_installate(cur):
            return {"items": [], "has_more": False, "limit": limit, "offset": offset, "installed": False}
        vis, params = _vis_richieste(ctx)
        filtri_ = [f"({vis})"]
        if status not in (None, "", "all"):
            filtri_.append("r.status = %s")
            params.append(status)
        cur.execute(f"SELECT {bd._COLONNE_RICHIESTA} FROM building_deletion_requests r "
                    "JOIN agencies a ON a.id = r.agency_id LEFT JOIN buildings b ON b.id = r.building_id "
                    f"WHERE {' AND '.join(filtri_)} ORDER BY r.requested_at DESC, r.id DESC LIMIT %s OFFSET %s",
                    params + [limit + 1, offset])
        righe = [bd._vista_richiesta(r) for r in cur.fetchall()]
    for r in righe:
        r.pop("units_fingerprint", None)
    return {"items": righe[:limit], "has_more": len(righe) > limit, "limit": limit, "offset": offset,
            "installed": True}


# ---------------------------------------------------------------------------
# anteprima delle relazioni
# ---------------------------------------------------------------------------

def _fk_verso(cur, tabella: str) -> list[tuple[str, str]]:
    cur.execute("""
        SELECT c.conrelid::regclass::text AS tabella, a.attname AS colonna
          FROM pg_constraint c
          CROSS JOIN LATERAL unnest(c.conkey, c.confkey) AS k(col, fcol)
          JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.col
          JOIN pg_attribute fa ON fa.attrelid = c.confrelid AND fa.attnum = k.fcol
         WHERE c.contype = 'f' AND c.confrelid = %s::regclass AND fa.attname = 'id'
         ORDER BY 1, 2""", (f"public.{tabella}",))
    return [(r["tabella"], r["colonna"]) for r in cur.fetchall()]


def relations(ctx, kind: str, entity_id: int, *, scope: str = "agency") -> dict:
    """I collegamenti di un elemento del Cestino: per ogni FK verso la sua
    tabella (dal catalogo), quante righe lo nominano. Sola lettura; fuori dal
    perimetro di chi chiede: 404."""
    _leggi(ctx, scope)
    if kind not in registry.CATEGORIE:
        raise TrashInvalid("Categoria non valida", "INVALID_TYPE", allowed=list(registry.CATEGORIE))
    cat = registry.CATEGORIE[kind]
    with core_cursor() as (_, cur):
        _cursore_lettura(cur, ctx, scope)
        sorgente, params = _sorgente(cur, ctx, [kind], scope=scope)
        cur.execute(f"SELECT t.id, t.agency_id, t.label FROM ({sorgente}) t WHERE t.id = %s",
                    params + [int(entity_id)])
        riga = cur.fetchone()
        if riga is None:
            raise NotFoundError(f"{kind} {entity_id} not in trash")
        voci = []
        for tabella, colonna in _fk_verso(cur, cat.table):
            nome = tabella.split(".")[-1]
            cur.execute(f"SELECT count(*) AS n FROM {tabella} WHERE {colonna} = %s", (int(entity_id),))
            n = int(cur.fetchone()["n"])
            if n:
                voci.append({"table": nome, "column": colonna, "count": n,
                             "label": ETICHETTE_RELAZIONI.get(nome, f"Dati collegati ({nome})")})
    voci.sort(key=lambda v: (-v["count"], v["table"]))
    return {"kind": kind, "id": int(entity_id), "label": riga["label"], "agency_id": riga["agency_id"],
            "relations": voci}
