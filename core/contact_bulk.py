"""SELEZIONE MULTIPLA - spostare nel Cestino e ripristinare PIU' contatti insieme.

Nessuna regola nuova: ogni contatto passa dalle STESSE funzioni dello spostamento
e del ripristino singoli (`contact_lifecycle.trash_locked` / `restore_locked`):
stesso scope (un agent solo i suoi), stessa riservatezza (`private_user_id`),
stessi blocchi (processi aperti, incarichi, portale), stesso storico che ferma un
agent, stesse comunicazioni sospese, stesso evento nel registro. Mai una
cancellazione definitiva.

A BLOCCHI. Il browser manda al piu' `BLOCK_MAX` id per richiesta (la selezione di
migliaia di contatti si divide in blocchi, con l'avanzamento visibile). Un blocco
e' UNA transazione su UNA connessione; ogni contatto sta nel suo SAVEPOINT, cosi'
un contatto bloccato non ferma gli altri e un contatto o e' spostato del tutto o
non e' toccato. Le righe si bloccano in ordine di id (lo stesso ordine per tutti:
niente stalli fra due operazioni concorrenti); un contatto tenuto da un'altra
transazione oltre `LOCK_TIMEOUT` e' «occupato», non un errore. Oltre
`TIME_BUDGET_SECONDS` il blocco si ferma e restituisce gli id non fatti
(`remaining`), che il browser rimanda.

GLI ID NON SONO FIDATI. Arrivano dal browser e potrebbero essere costruiti a
mano: ogni id si rilegge nello scope e nella riservatezza di chi chiede, con
l'agenzia della SESSIONE. Un id di un'altra agenzia, riservato a un altro
operatore, fuori dallo scope di un agent o inesistente e' «non disponibile»
(indistinguibili, come il 404 della scheda) e non viene toccato.

L'OPERAZIONE. Tutti i blocchi di una selezione condividono un `operation_id`
(scritto in `record_lifecycle_events.metadata.bulk_operation`): il Cestino puo'
cosi' mostrare e ripristinare «questa operazione». Un id d'operazione si puo'
continuare solo da chi l'ha iniziata, nella sua agenzia.
"""
from __future__ import annotations

import logging
import time
import uuid

import psycopg2

from . import contact_lifecycle as cl
from .contact_privacy import contact_privacy_predicate
from .database import core_cursor
from .exceptions import NotFoundError, ValidationError
from .scope import scoped_predicate

log = logging.getLogger("stima360.lifecycle")

BLOCK_MAX = 100
TIME_BUDGET_SECONDS = 20
LOCK_TIMEOUT = "5s"
OPERATIONS_LIST_MAX = 50

INVALID_IDS = "INVALID_IDS"
INVALID_OPERATION = "INVALID_OPERATION"
OPERATION_NOT_YOURS = "OPERATION_NOT_YOURS"


class BulkInvalid(cl.ContactLifecycleInvalid):
    pass


class BulkConflict(cl.ContactLifecycleConflict):
    pass


def _ids(raw) -> list[int]:
    try:
        ids = sorted({int(x) for x in (raw or [])})
    except (TypeError, ValueError):
        raise BulkInvalid("Selezione non valida", INVALID_IDS) from None
    if not ids or len(ids) > BLOCK_MAX or ids[0] <= 0:
        raise BulkInvalid(f"Una richiesta contiene da 1 a {BLOCK_MAX} contatti", INVALID_IDS, max=BLOCK_MAX)
    return ids


def _uuid(valore) -> str:
    try:
        return str(uuid.UUID(str(valore)))
    except (TypeError, ValueError):
        raise BulkInvalid("Operazione non valida", INVALID_OPERATION) from None


def _operazione_mia(cur, ctx, agency_id: int, operation_id, azione: str) -> str:
    """Un nuovo id, oppure quello di un'operazione gia' iniziata DA ME, nella MIA
    agenzia, con la stessa azione: un id altrui non si puo' continuare."""
    if operation_id is None:
        return str(uuid.uuid4())
    op = _uuid(operation_id)
    cur.execute("SELECT 1 FROM record_lifecycle_events WHERE entity_type = 'contact' "
                "AND metadata->>'bulk_operation' = %s AND (agency_id <> %s OR actor_user_id IS DISTINCT FROM %s "
                "OR action <> %s) LIMIT 1", (op, agency_id, getattr(ctx, "user_id", None), azione))
    if cur.fetchone() is not None:
        raise BulkConflict("Questa operazione non e' tua: avviane una nuova", OPERATION_NOT_YOURS)
    return op


def _etichette(cur, ctx, ids: list[int]) -> dict[int, str]:
    """I nomi dei contatti del riepilogo, letti con lo scope e la riservatezza di chi chiede."""
    if not ids:
        return {}
    predicato, params = scoped_predicate(ctx, "contacts", "c")
    privacy, pparams = contact_privacy_predicate(ctx, "c")
    cur.execute(f"SELECT c.id, COALESCE(NULLIF(BTRIM(c.display_name), ''), "
                f"NULLIF(BTRIM(CONCAT_WS(' ', c.first_name, c.last_name)), ''), NULLIF(BTRIM(c.company_name), ''), "
                f"'Contatto #' || c.id) AS label FROM contacts c WHERE {predicato} AND {privacy} AND c.id = ANY(%s)",
                params + pparams + [ids])
    return {int(r["id"]): r["label"] for r in cur.fetchall()}


def _giro(ctx, ids, azione: str, operation_id, fare) -> dict:
    """Il ciclo comune: una transazione, un SAVEPOINT per contatto, tempo massimo."""
    agency_id = ctx.require_agency()
    esito = {"done": [], "already": [], "blocked": [], "needs_admin": [], "not_yours": [],
             "not_available": [], "busy": [], "remaining": []}
    inizio = time.monotonic()
    with core_cursor(commit=True) as (_, cur):
        cl._cestino_installato(cur)
        op = _operazione_mia(cur, ctx, agency_id, operation_id, azione)
        cur.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
        for i, cid in enumerate(ids):
            if time.monotonic() - inizio > TIME_BUDGET_SECONDS:
                esito["remaining"] = ids[i:]
                break
            cur.execute("SAVEPOINT bulk_contact")
            try:
                fare(cur, agency_id, cid, op)
            except NotFoundError:
                cur.execute("ROLLBACK TO SAVEPOINT bulk_contact")
                esito["not_available"].append({"id": cid})
            except cl.ContactLifecycleForbidden as exc:
                cur.execute("ROLLBACK TO SAVEPOINT bulk_contact")
                chiave = "needs_admin" if exc.code == cl.HISTORY_REQUIRES_ADMIN else "not_yours"
                esito[chiave].append({"id": cid})
            except cl.ContactLifecycleConflict as exc:
                cur.execute("ROLLBACK TO SAVEPOINT bulk_contact")
                if exc.code in (cl.ALREADY_DELETED, cl.NOT_DELETED):
                    esito["already"].append({"id": cid})
                else:
                    voci = [{"code": b.get("code"), "label": b.get("label")} for b in exc.extra.get("blockers") or []]
                    esito["blocked"].append({"id": cid, "blockers": voci})
            except psycopg2.errors.LockNotAvailable:
                cur.execute("ROLLBACK TO SAVEPOINT bulk_contact")
                esito["busy"].append({"id": cid})
            else:
                cur.execute("RELEASE SAVEPOINT bulk_contact")
                esito["done"].append(cid)
        nomi = _etichette(cur, ctx, [v["id"] for k in ("blocked", "needs_admin", "not_yours", "busy", "already")
                                     for v in esito[k]])
    for chiave in ("blocked", "needs_admin", "not_yours", "busy", "already"):
        for voce in esito[chiave]:
            voce["label"] = nomi.get(voce["id"])
    for cid in esito["done"]:
        cl._audit(azione, ctx, entity_type="contact", entity_id=cid, bulk_operation=op)
    log.info("bulk %s operation=%s done=%d", azione, op, len(esito["done"]))
    esito["operation_id"] = op
    esito["processed"] = len(ids) - len(esito["remaining"])
    return esito


def trash_many(ctx, ids, reason_code, note=None, operation_id=None) -> dict:
    """Sposta nel Cestino un blocco di contatti (vedi il modulo)."""
    reason_code, note = cl._valida_motivo(reason_code, note)
    ids = _ids(ids)

    def fare(cur, agency_id, cid, op):
        cl.trash_locked(cur, ctx, agency_id, cid, reason_code, note, operation_id=op)

    esito = _giro(ctx, ids, "trash", operation_id, fare)
    esito["trashed"] = esito.pop("done")
    return esito


def restore_many(ctx, ids, operation_id=None) -> dict:
    """Ripristina un blocco di contatti (stesse regole del ripristino singolo).
    `operation_id`, se c'e', e' l'operazione di ripristino gia' iniziata."""
    ids = _ids(ids)

    def fare(cur, agency_id, cid, op):
        cl.restore_locked(cur, ctx, agency_id, cid, operation_id=op)

    esito = _giro(ctx, ids, "restore", operation_id, fare)
    esito["restored"] = esito.pop("done")
    return esito


# ---------------------------------------------------------------------------
# le operazioni massive nel Cestino («Ripristina questa operazione»)
# ---------------------------------------------------------------------------

def _visibili_nel_cestino(ctx) -> tuple[str, list]:
    """Gli stessi contatti del Cestino di chi chiede (contact_lifecycle.list_trash):
    scope, riservatezza e, per chi non gestisce l'agenzia, solo cio' che ha spostato lui."""
    predicato, params = scoped_predicate(ctx, "contacts", "c")
    privacy, pparams = contact_privacy_predicate(ctx, "c")
    sql = f"{predicato} AND {privacy} AND (to_jsonb(c)->>'deleted_at') IS NOT NULL"
    params = [*params, *pparams]
    if not cl._vede_tutta_agenzia(ctx):
        sql += " AND c.deleted_by_user_id = %s"
        params.append(getattr(ctx, "user_id", None))
    return sql, params


#: l'operazione dell'ULTIMO spostamento nel Cestino di ogni contatto (un contatto
#: ripristinato e rispostato appartiene alla nuova operazione, non alla vecchia).
_ULTIMA_OPERAZIONE = """(SELECT e2.metadata->>'bulk_operation' FROM record_lifecycle_events e2
                          WHERE e2.agency_id = c.agency_id AND e2.entity_type = 'contact' AND e2.entity_id = c.id
                            AND e2.action = 'trash' ORDER BY e2.occurred_at DESC, e2.id DESC LIMIT 1)"""


def list_operations(ctx, limit: int = 20) -> dict:
    """Le operazioni massive con contatti ancora nel Cestino, visibili a chi chiede."""
    from property.interactions import NOME_OPERATORE

    agency_id = ctx.require_agency()
    limit = max(1, min(int(limit), OPERATIONS_LIST_MAX))
    vis, params = _visibili_nel_cestino(ctx)
    with core_cursor() as (_, cur):
        cl._cestino_installato(cur)
        cur.execute(
            f"""SELECT t.operation_id, count(*) AS in_trash, min(c.deleted_at) AS deleted_at,
                       min(c.deleted_reason) AS reason_code, min(c.deleted_by_user_id) AS deleted_by_user_id,
                       (SELECT {NOME_OPERATORE.format(a='u')} FROM operator_users u
                         WHERE u.id = min(c.deleted_by_user_id)) AS deleted_by_name
                  FROM contacts c CROSS JOIN LATERAL (SELECT {_ULTIMA_OPERAZIONE} AS operation_id) t
                 WHERE c.agency_id = %s AND {vis} AND t.operation_id IS NOT NULL
                 GROUP BY t.operation_id ORDER BY min(c.deleted_at) DESC LIMIT %s""",
            [agency_id, *params, limit])
        righe = [dict(r) for r in cur.fetchall()]
    return {"items": righe}


def operation_ids(ctx, operation_id) -> dict:
    """Gli id dei contatti di un'operazione ancora nel Cestino e ripristinabili da chi chiede."""
    agency_id = ctx.require_agency()
    op = _uuid(operation_id)
    vis, params = _visibili_nel_cestino(ctx)
    with core_cursor() as (_, cur):
        cl._cestino_installato(cur)
        cur.execute(f"SELECT c.id FROM contacts c WHERE c.agency_id = %s AND {vis} AND {_ULTIMA_OPERAZIONE} = %s "
                    "ORDER BY c.id", [agency_id, *params, op])
        ids = [int(r["id"]) for r in cur.fetchall()]
    if not ids:
        raise NotFoundError(f"operation {op} not found")
    return {"operation_id": op, "ids": ids, "total": len(ids)}
