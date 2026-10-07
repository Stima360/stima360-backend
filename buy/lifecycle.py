"""CESTINO-RICHIESTE-1 (FASE H) - il Cestino delle RICHIESTE ACQUIRENTE.

Stesso impianto dei Cestini Immobili (`property/lifecycle.py`), Contatti
(`core/contact_lifecycle.py`) ed Edifici (`property/building_lifecycle.py`):
`buy_requests.deleted_*` (migration 092), il registro append-only
`record_lifecycle_events` (entita' `buy_request`), le guardie della 092 nel
database. Nessun sistema parallelo, nessuna cancellazione fisica, nessuna
cascata, nessuna chiusura o scollegamento automatico.

COS'E' IL CESTINO. Una richiesta inserita per errore, doppia o non valida.
Non sostituisce la chiusura o la sospensione commerciale (`status`) ne'
l'archiviazione (`status = 'archived'`, owner/admin): lo stato resta quello
che e', e torna identico col ripristino.

CHI. Le richieste non hanno uno scope per agente (`assigned_to` e' testo
libero): ogni ruolo dell'agenzia le vede e le gestisce. La politica del
Cestino Contatti: un `agent` si ferma davanti allo STORICO reale della
richiesta (403 HISTORY_REQUIRES_ADMIN); owner, admin e platform admin in
acting no. Il ripristino: owner/admin tutto il Cestino dell'agenzia, un agent
solo cio' che ha spostato lui (stessa regola per l'elenco).

BLOCCHI (assoluti, ricalcolati sotto lock): proposte in bozza o inviate,
vendite pendenti, visite future in programma, task aperti collegati. Nessuno
viene chiuso o scollegato: il blocco dice cosa e dove. Gli abbinamenti (anche
i suggerimenti), i candidati «vendita invisibile» e i suggerimenti di «Oggi»
NON bloccano: escono dalle liste e dai calcoli.

COMUNICAZIONI. Nessun messaggio, journey o automazione e' legato a una
richiesta: sono del contatto, che resta attivo con le sue altre richieste.
Il Cestino di una richiesta non sospende nulla.

L'OPERAZIONE (una transazione): lock della riga, ricontrollo di blocchi e
storico, `deleted_*`, evento nel registro. Il ripristino riporta `deleted_*` a
NULL e nient'altro: stesso id, stesso stato, relazioni intatte; nessun
messaggio, nessuna visita riprogrammata, nessun processo riaperto. Se il
contatto e' nel Cestino il ripristino si ferma e indica di ripristinare prima
il contatto (le guardie della 090 non accettano una richiesta operativa
verso un contatto nel Cestino). Le altre richieste aperte dello stesso
contatto si SEGNALANO come possibili doppioni, senza fondere nulla.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from core import buy_trash as _buy_trash
from core import contact_trash as _contact_trash
from core.contact_lifecycle import (
    ContactLifecycleConflict as LifecycleConflict,
    ContactLifecycleForbidden as LifecycleForbidden,
    ContactLifecycleInvalid as LifecycleInvalid,
    TRASH_NOTE_MAX, TRASH_REASONS,
    _ConCodice,
)
from core.database import core_cursor
from core.exceptions import ConflictError, NotFoundError
from operator_auth import permissions

log = logging.getLogger("stima360.lifecycle")

TRASH_BLOCKED = "TRASH_BLOCKED"
ALREADY_DELETED = "ALREADY_DELETED"
NOT_DELETED = "NOT_DELETED"
NOT_DELETED_BY_YOU = "NOT_DELETED_BY_YOU"
INVALID_TRASH_REASON = "INVALID_TRASH_REASON"
HISTORY_REQUIRES_ADMIN = "HISTORY_REQUIRES_ADMIN"
TRASH_NOT_INSTALLED = "TRASH_NOT_INSTALLED"
RESTORE_BLOCKED = "RESTORE_BLOCKED"
CONTACT_IN_TRASH = "CONTACT_IN_TRASH"
TRASH_LIST_MAX = 200

TRASH_BLOCKED_MESSAGE = "La richiesta ha processi aperti: chiudili prima di spostarla nel Cestino"
ALREADY_DELETED_MESSAGE = "La richiesta è già nel Cestino"
NOT_DELETED_MESSAGE = "La richiesta non è nel Cestino"
NOT_DELETED_BY_YOU_MESSAGE = ("Puoi ripristinare solo le richieste che hai spostato tu nel Cestino: "
                              "chiedi a un amministratore")
INVALID_TRASH_REASON_MESSAGE = "Motivo non valido"
HISTORY_REQUIRES_ADMIN_MESSAGE = ("La richiesta ha uno storico operativo (interazioni, visite, proposte, "
                                  "vendite...): può spostarla nel Cestino solo un amministratore o il titolare")
TRASH_NOT_INSTALLED_MESSAGE = ("Il Cestino delle richieste non è ancora disponibile su questo database "
                               "(migration 092 non applicata)")
RESTORE_BLOCKED_MESSAGE = "Il contatto della richiesta è nel Cestino: ripristina prima il contatto"

#: Stati "aperti" (gli stessi del Cestino Contatti e delle guardie della 092).
BUY_OPEN = ("draft", "active", "paused")
BUY_CLOSED = ("satisfied", "closed", "archived")
PROPOSAL_OPEN = ("draft", "submitted")
TASK_OPEN = ("open", "in_progress")
VISIT_OPEN = ("scheduled", "confirmed")

STATUS_LABELS = {"draft": "bozza", "active": "attiva", "paused": "in pausa", "satisfied": "soddisfatta",
                 "closed": "chiusa", "archived": "archiviata"}
_STATO_PROPOSTA = {"draft": "in bozza", "submitted": "inviata"}


class BuyTrashNotInstalled(_ConCodice, ConflictError):
    """503 TRASH_NOT_INSTALLED (codice deployato prima della 092): il router lo
    traduce con il suo codice, prima di leggere o scrivere."""
    code = TRASH_NOT_INSTALLED


# ---------------------------------------------------------------------------
# aiuti
# ---------------------------------------------------------------------------

def _cestino_installato(cur) -> None:
    cur.execute("SELECT to_regclass('public.record_lifecycle_events') IS NOT NULL"
                "   AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'"
                "                 AND table_name = 'buy_requests' AND column_name = 'deleted_at') AS pronto")
    if not cur.fetchone()["pronto"]:
        raise BuyTrashNotInstalled(TRASH_NOT_INSTALLED_MESSAGE)


def _vede_tutta_agenzia(ctx) -> bool:
    return permissions.sees_all_agency_records(getattr(ctx, "role", None),
                                               getattr(ctx, "is_platform_admin", False))


def _richiesta(cur, agency_id: int, request_id: int, *, lock: bool = False) -> dict:
    """La riga nell'agenzia di chi chiede, nel Cestino o no. Altrove o
    inesistente: 404."""
    cur.execute("SELECT b.*, c.display_name AS contact_name, "
                f"{_contact_trash.deleted_at_sql('c')} AS contact_deleted_at "
                "FROM buy_requests b JOIN contacts c ON c.id = b.contact_id "
                f"WHERE b.id = %s AND b.agency_id = %s{' FOR UPDATE OF b' if lock else ''}",
                (request_id, agency_id))
    riga = cur.fetchone()
    if riga is None:
        raise NotFoundError(f"buy request {request_id} not found")
    return dict(riga)


def _audit(azione: str, ctx, **campi) -> None:
    riga = {"action": azione, "actor_user_id": getattr(ctx, "user_id", None),
            "actor_role": getattr(ctx, "role", None), "agency_id": getattr(ctx, "agency_id", None),
            "at": datetime.now(timezone.utc).isoformat(), **campi}
    log.info("lifecycle %s", json.dumps(riga, default=str))


def _evento(cur, ctx, agency_id: int, request_id: int, azione: str, *, reason=None, note=None,
            before: dict, metadata: dict | None = None) -> None:
    meta = {"actor_role": getattr(ctx, "role", None),
            "platform_admin": bool(getattr(ctx, "is_platform_admin", False)), **(metadata or {})}
    cur.execute("INSERT INTO record_lifecycle_events (agency_id, entity_type, entity_id, action, reason_code, note, "
                "actor_user_id, before_state, metadata) "
                "VALUES (%s, 'buy_request', %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)",
                (agency_id, request_id, azione, reason, note, getattr(ctx, "user_id", None),
                 json.dumps(before, default=str), json.dumps(meta, default=str)))


def _valida_motivo(reason_code, note):
    if reason_code not in TRASH_REASONS:
        raise LifecycleInvalid(INVALID_TRASH_REASON_MESSAGE, INVALID_TRASH_REASON, allowed=list(TRASH_REASONS))
    if note is not None:
        note = str(note).strip() or None
    if note is not None and len(note) > TRASH_NOTE_MAX:
        raise LifecycleInvalid(f"La nota supera {TRASH_NOTE_MAX} caratteri", INVALID_TRASH_REASON)
    return reason_code, note


def _data(valore) -> str | None:
    return valore.strftime("%d/%m/%Y") if valore is not None else None


def _righe(cur, sql: str, params) -> list[dict]:
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# BLOCCHI: processi aperti (nessuno si chiude da qui)
# ---------------------------------------------------------------------------

def trash_blockers(cur, richiesta: dict) -> list[dict]:
    """Ogni voce: `code`, `label`, `items` (con `label` e `href` interno della
    Shell), `link` facoltativo."""
    rid = richiesta["id"]
    scheda = f"#/acquirenti/{rid}"
    blocchi: list[dict] = []

    # 1. proposte in bozza o inviate (passano dall'abbinamento)
    righe = _righe(cur, "SELECT pp.id, pp.status, m.property_id, p.code AS property_code FROM property_proposals pp "
                        "JOIN matches m ON m.id = pp.match_id JOIN properties p ON p.id = m.property_id "
                        "WHERE m.buy_request_id = %s AND pp.status = ANY(%s) ORDER BY pp.id",
                   (rid, list(PROPOSAL_OPEN)))
    if righe:
        for r in righe:
            r["label"] = " · ".join(x for x in (f"Proposta #{r['id']}", r.get("property_code"),
                                                _STATO_PROPOSTA.get(r["status"], r["status"])) if x)
            r["href"] = scheda
        blocchi.append({"code": "PROPOSAL_OPEN",
                        "label": "Proposte d'acquisto in corso: ritirale o concludile prima (tab Proposte della richiesta)",
                        "items": righe, "link": {"href": scheda, "label": "Apri la richiesta"}})

    # 2. vendite pendenti
    righe = _righe(cur, "SELECT s.id, s.property_id, p.code AS property_code FROM property_sales s "
                        "JOIN properties p ON p.id = s.property_id "
                        "WHERE s.buy_request_id = %s AND s.status = 'pending' ORDER BY s.id", (rid,))
    if righe:
        for r in righe:
            r["label"] = " · ".join(x for x in (f"Vendita #{r['id']}", r.get("property_code"), "in corso") if x)
            r["href"] = f"#/immobili/{r['property_id']}"
        blocchi.append({"code": "SALE_PENDING", "label": "Vendite in corso: concludile o annullale prima",
                        "items": righe})

    # 3. visite future in programma, nate da «Visita programmata» sull'abbinamento
    righe = _righe(cur, "SELECT DISTINCT v.id, v.scheduled_at, v.property_id, p.code AS property_code, "
                        "to_char(v.scheduled_at AT TIME ZONE 'Europe/Rome', 'YYYY-MM-DD') AS giorno "
                        "FROM buy_request_interactions i JOIN property_visits v ON v.id = i.property_visit_id "
                        "JOIN properties p ON p.id = v.property_id "
                        "WHERE i.buy_request_id = %s AND i.interaction_type = 'visit_scheduled' "
                        "AND v.status = ANY(%s) AND v.scheduled_at >= NOW() ORDER BY v.scheduled_at, v.id",
                   (rid, list(VISIT_OPEN)))
    if righe:
        for r in righe:
            giorno = "/".join(reversed(r["giorno"].split("-")))            # il giorno di Roma
            r["label"] = " · ".join(x for x in (f"Visita del {giorno}", r.get("property_code")) if x)
            r["href"] = f"#/agenda/giorno/{r['giorno']}"
        blocchi.append({"code": "VISIT_SCHEDULED",
                        "label": "Visite in programma: annullale o registrane l'esito prima (in Agenda)",
                        "items": righe})

    # 4. task aperti collegati alla richiesta
    righe = _righe(cur, "SELECT t.id, t.title, t.due_at FROM buy_request_task_links l JOIN tasks t ON t.id = l.task_id "
                        "WHERE l.buy_request_id = %s AND t.status = ANY(%s) ORDER BY t.due_at NULLS LAST, t.id",
                   (rid, list(TASK_OPEN)))
    if righe:
        for r in righe:
            r["label"] = (r.get("title") or f"Task #{r['id']}") + (f" · scade il {_data(r['due_at'])}" if r.get("due_at") else "")
            r["href"] = "#/attivita"
        blocchi.append({"code": "TASK_OPEN", "label": "Task aperti: completali o annullali prima in Attività",
                        "items": righe, "link": {"href": "#/attivita", "label": "Apri Attività"}})
    return blocchi


# ---------------------------------------------------------------------------
# STORICO: per un agent richiede owner/admin; owner/admin non si fermano
# ---------------------------------------------------------------------------

def protected_history(cur, richiesta: dict) -> list[dict]:
    """Lo storico operativo REALE della richiesta. Ogni voce: `code`, `label`,
    `count`. NON e' storico: gli abbinamenti calcolati (suggerimenti), i
    criteri, gli eventi generati dal sistema (creazione, modifiche)."""
    rid = richiesta["id"]
    voci: list[dict] = []

    def conta(codice, etichetta, sql, params):
        cur.execute(f"SELECT count(*) AS n FROM ({sql}) x", params)
        n = cur.fetchone()["n"]
        if n:
            voci.append({"code": codice, "label": etichetta, "count": n})

    if richiesta.get("status") in BUY_CLOSED:
        voci.append({"code": "REQUEST_CONCLUDED",
                     "label": f"Richiesta {STATUS_LABELS.get(richiesta['status'], richiesta['status'])}", "count": 1})
    conta("BUYER_INTERACTION_HISTORY", "Interazioni con l'acquirente (proposte, visite, esiti)",
          "SELECT id FROM buy_request_interactions WHERE buy_request_id = %s", (rid,))
    conta("PROPOSAL_HISTORY", "Proposte d'acquisto concluse",
          "SELECT pp.id FROM property_proposals pp JOIN matches m ON m.id = pp.match_id "
          "WHERE m.buy_request_id = %s AND pp.status <> ALL(%s)", (rid, list(PROPOSAL_OPEN)))
    conta("SALE_HISTORY", "Vendite registrate",
          "SELECT id FROM property_sales WHERE buy_request_id = %s AND status <> 'pending'", (rid,))
    conta("TASK_HISTORY", "Task completati",
          "SELECT t.id FROM buy_request_task_links l JOIN tasks t ON t.id = l.task_id "
          "WHERE l.buy_request_id = %s AND t.status = 'completed'", (rid,))
    conta("NOTE_HISTORY", "Note scritte nello storico",
          "SELECT id FROM buy_request_history WHERE buy_request_id = %s AND event_type = 'note'", (rid,))
    return voci


def _effetti(cur, richiesta: dict) -> dict:
    """Cosa cambia (detto PRIMA, nella conferma). Le comunicazioni non cambiano:
    nessuna e' legata alla richiesta."""
    cur.execute("SELECT count(*) AS n FROM matches m JOIN properties p ON p.id = m.property_id "
                "WHERE m.buy_request_id = %s AND m.archived_at IS NULL AND (to_jsonb(p)->>'deleted_at') IS NULL",
                (richiesta["id"],))
    abbinamenti = cur.fetchone()["n"]
    cur.execute(f"SELECT count(*) AS n FROM buy_requests b WHERE b.agency_id = %s AND b.contact_id = %s "
                f"AND b.id <> %s AND b.status = ANY(%s) AND {_buy_trash.live('b')}",
                (richiesta["agency_id"], richiesta["contact_id"], richiesta["id"], list(BUY_OPEN)))
    altre = cur.fetchone()["n"]
    return {"status": richiesta.get("status"), "status_label": STATUS_LABELS.get(richiesta.get("status")),
            "matches": abbinamenti, "other_open_requests": altre,
            "contact_id": richiesta["contact_id"], "contact_name": richiesta.get("contact_name"),
            "communications_unchanged": True}


# ---------------------------------------------------------------------------
# deletion-check / trash / restore / elenco / scheda
# ---------------------------------------------------------------------------

def deletion_check(ctx, request_id: int) -> dict:
    """Si puo' spostare nel Cestino? Sola lettura. `history` e' informativa per
    tutti; per un agent lo storico e' un blocco (HISTORY_REQUIRES_ADMIN)."""
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _cestino_installato(cur)
        richiesta = _richiesta(cur, agency_id, request_id)
        storia: list[dict] = []
        effetti = None
        if richiesta.get("deleted_at") is not None:
            blocchi = [{"code": ALREADY_DELETED, "label": ALREADY_DELETED_MESSAGE, "items": []}]
        else:
            blocchi = trash_blockers(cur, richiesta)
            storia = protected_history(cur, richiesta)
            if not blocchi and storia and not _vede_tutta_agenzia(ctx):
                blocchi = [{"code": HISTORY_REQUIRES_ADMIN, "label": HISTORY_REQUIRES_ADMIN_MESSAGE, "items": storia}]
            effetti = _effetti(cur, richiesta)
    risposta = {"can_trash": not blocchi, "blockers": blocchi, "history": storia}
    if effetti is not None:
        risposta["effects"] = effetti
    return risposta


def trash_request(ctx, request_id: int, reason_code, note=None) -> dict:
    agency_id = ctx.require_agency()
    reason_code, note = _valida_motivo(reason_code, note)
    with core_cursor(commit=True) as (_, cur):
        _cestino_installato(cur)
        # FOR UPDATE: un collegamento concorrente (guardie 092, FOR SHARE)
        # aspetta questa transazione, oppure questa aspetta lui e lo vede
        richiesta = _richiesta(cur, agency_id, request_id, lock=True)
        if richiesta.get("deleted_at") is not None:
            raise LifecycleConflict(ALREADY_DELETED_MESSAGE, ALREADY_DELETED)
        blocchi = trash_blockers(cur, richiesta)
        if blocchi:
            raise LifecycleConflict(TRASH_BLOCKED_MESSAGE, TRASH_BLOCKED, blockers=blocchi)
        if not _vede_tutta_agenzia(ctx):
            storia = protected_history(cur, richiesta)
            if storia:
                raise LifecycleForbidden(HISTORY_REQUIRES_ADMIN_MESSAGE, HISTORY_REQUIRES_ADMIN, history=storia)
        effetti = _effetti(cur, richiesta)
        cur.execute("UPDATE buy_requests SET deleted_at = NOW(), deleted_by_user_id = %s, deleted_reason = %s, "
                    "updated_at = NOW() WHERE id = %s AND agency_id = %s RETURNING *",
                    (getattr(ctx, "user_id", None), reason_code, request_id, agency_id))
        riga = dict(cur.fetchone())
        _evento(cur, ctx, agency_id, request_id, "trash", reason=reason_code, note=note,
                before={k: richiesta.get(k) for k in ("status", "archived_at", "contact_id", "lead_id")},
                metadata={"matches": effetti["matches"]})
    _audit("trash", ctx, entity_type="buy_request", entity_id=request_id, reason=reason_code)
    riga["contact_name"] = richiesta.get("contact_name")
    return riga


def possible_duplicates(cur, richiesta: dict) -> list[dict]:
    """Le ALTRE richieste aperte (fuori dal Cestino) dello stesso contatto. Solo
    un avviso: nulla si fonde o si chiude."""
    cur.execute(f"SELECT b.id, b.title, b.status, b.budget_target FROM buy_requests b "
                f"WHERE b.agency_id = %s AND b.contact_id = %s AND b.id <> %s AND b.status = ANY(%s) "
                f"AND {_buy_trash.live('b')} ORDER BY b.id LIMIT 20",
                (richiesta["agency_id"], richiesta["contact_id"], richiesta["id"], list(BUY_OPEN)))
    return [dict(r) for r in cur.fetchall()]


def restore_request(ctx, request_id: int) -> dict:
    """Ripristino: i tre campi `deleted_*` a NULL, nient'altro. Stesso stato,
    nessun messaggio, nessuna visita, nessun processo riaperto."""
    agency_id = ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        _cestino_installato(cur)
        richiesta = _richiesta(cur, agency_id, request_id, lock=True)
        if richiesta.get("deleted_at") is None:
            raise LifecycleConflict(NOT_DELETED_MESSAGE, NOT_DELETED)
        if not _vede_tutta_agenzia(ctx) and richiesta.get("deleted_by_user_id") != getattr(ctx, "user_id", None):
            raise LifecycleForbidden(NOT_DELETED_BY_YOU_MESSAGE, NOT_DELETED_BY_YOU)
        # il contatto nel Cestino: prima lui (la 090 lo tiene congelato); lock
        # condiviso come le guardie della 090, cosi' uno spostamento del
        # contatto in corso aspetta o viene visto
        cur.execute(f"SELECT id, display_name, {_contact_trash.deleted_at_sql('contacts')} AS deleted_at "
                    "FROM contacts WHERE id = %s FOR KEY SHARE", (richiesta["contact_id"],))
        contatto = cur.fetchone()
        if contatto is not None and contatto["deleted_at"] is not None:
            nome = contatto.get("display_name") or f"Contatto #{contatto['id']}"
            raise LifecycleConflict(RESTORE_BLOCKED_MESSAGE, RESTORE_BLOCKED, blockers=[{
                "code": CONTACT_IN_TRASH,
                "label": "Il contatto è nel Cestino: ripristinalo prima, poi ripristina la richiesta",
                "items": [{"id": contatto["id"], "label": nome, "href": f"#/contatti/{contatto['id']}"}],
                "link": {"href": "#/cestino/contatti", "label": "Apri il Cestino Contatti"}}])
        cur.execute("UPDATE buy_requests SET deleted_at = NULL, deleted_by_user_id = NULL, deleted_reason = NULL, "
                    "updated_at = NOW() WHERE id = %s AND agency_id = %s RETURNING *", (request_id, agency_id))
        riga = dict(cur.fetchone())
        doppioni = possible_duplicates(cur, riga)
        _evento(cur, ctx, agency_id, request_id, "restore",
                before={k: richiesta.get(k) for k in ("deleted_at", "deleted_by_user_id", "deleted_reason")},
                metadata={"possible_duplicates": [d["id"] for d in doppioni]})
    _audit("restore", ctx, entity_type="buy_request", entity_id=request_id)
    riga["contact_name"] = richiesta.get("contact_name")
    riga["possible_duplicates"] = doppioni
    return riga


def list_trash(ctx, *, limit: int = 50, offset: int = 0) -> dict:
    """Le richieste nel Cestino dell'agenzia, dal piu' recente. Owner, admin e
    platform admin in acting: tutto; un agent: cio' che ha spostato lui."""
    from property.interactions import NOME_OPERATORE

    agency_id = ctx.require_agency()
    limit = max(1, min(int(limit), TRASH_LIST_MAX))
    offset = max(0, int(offset))
    filtri, params = ["b.agency_id = %s", "b.deleted_at IS NOT NULL"], [agency_id]
    if not _vede_tutta_agenzia(ctx):
        filtri.append("b.deleted_by_user_id = %s")
        params.append(getattr(ctx, "user_id", None))
    with core_cursor() as (_, cur):
        _cestino_installato(cur)
        cur.execute(
            f"""SELECT b.id, b.title, b.status, b.contact_id, b.budget_target, b.budget_max,
                       c.display_name AS contact_name,
                       ({_contact_trash.deleted_at_sql('c')} IS NOT NULL) AS contact_in_trash,
                       b.deleted_at, b.deleted_reason, b.deleted_by_user_id,
                       (SELECT {NOME_OPERATORE.format(a='u')} FROM operator_users u
                         WHERE u.id = b.deleted_by_user_id) AS deleted_by_name,
                       (SELECT e.note FROM record_lifecycle_events e
                         WHERE e.agency_id = b.agency_id AND e.entity_type = 'buy_request'
                           AND e.entity_id = b.id AND e.action = 'trash'
                         ORDER BY e.occurred_at DESC, e.id DESC LIMIT 1) AS deleted_note
                  FROM buy_requests b JOIN contacts c ON c.id = b.contact_id
                 WHERE {' AND '.join(filtri)}
                 ORDER BY b.deleted_at DESC, b.id DESC
                 LIMIT %s OFFSET %s""",
            params + [limit + 1, offset])
        righe = [dict(r) for r in cur.fetchall()]
    return {"items": righe[:limit], "has_more": len(righe) > limit, "limit": limit, "offset": offset}


def trash_info(cur, ctx, richiesta: dict) -> dict | None:
    """Per la scheda di una richiesta nel Cestino: chi, quando, perche', la
    nota, se chi guarda puo' ripristinarla e se il contatto e' nel Cestino.
    None fuori dal Cestino."""
    if not richiesta or (richiesta.get("deleted_at") if hasattr(richiesta, "get") else None) is None:
        return None
    from property.interactions import NOME_OPERATORE
    cur.execute(f"SELECT {NOME_OPERATORE.format(a='u')} AS nome FROM operator_users u WHERE u.id = %s",
                (richiesta.get("deleted_by_user_id"),))
    riga = cur.fetchone()
    cur.execute("SELECT note FROM record_lifecycle_events WHERE agency_id = %s AND entity_type = 'buy_request' "
                "AND entity_id = %s AND action = 'trash' ORDER BY occurred_at DESC, id DESC LIMIT 1",
                (richiesta.get("agency_id"), richiesta["id"]))
    evento = cur.fetchone()
    cur.execute(f"SELECT {_contact_trash.deleted_at_sql('contacts')} AS deleted_at FROM contacts WHERE id = %s",
                (richiesta.get("contact_id"),))
    contatto = cur.fetchone()
    puo = _vede_tutta_agenzia(ctx) or richiesta.get("deleted_by_user_id") == getattr(ctx, "user_id", None)
    return {"deleted_at": richiesta.get("deleted_at"), "deleted_reason": richiesta.get("deleted_reason"),
            "deleted_by_user_id": richiesta.get("deleted_by_user_id"),
            "deleted_by_name": riga["nome"] if riga else None,
            "deleted_note": evento["note"] if evento else None,
            "contact_in_trash": bool(contatto and contatto["deleted_at"] is not None),
            "can_restore": bool(puo)}
