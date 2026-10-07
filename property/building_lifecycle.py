"""CESTINO-EDIFICI-1 (FASE G) - il Cestino degli EDIFICI (palazzine contenitore).

Stesso impianto del Cestino Immobili (`property/lifecycle.py`, 2B1/2B3) e
Contatti (`core/contact_lifecycle.py`): colonne `buildings.deleted_*`
(migration 091), registro append-only `record_lifecycle_events` (entita'
`building`), guardie nel database. Nessuna cancellazione fisica, nessuna
cascata sulle unita'.

L'EDIFICIO NON E' L'IMMOBILE «INTERO STABILE». Una palazzina (`buildings`) e'
un contenitore di unita'; un immobile con `whole_building = TRUE` e' un
immobile e ha il suo Cestino. Qui si sposta solo il contenitore.

BLOCCO (assoluto, ricalcolato sotto lock): QUALUNQUE riga di `properties` con
`building_id` = l'edificio - attive, archiviate, pertinenze (collegate o da
collegare), «intero stabile», unita' nel Cestino Immobili. Tutte devono poter
tornare nel loro edificio: nessuna viene scollegata, spostata o cestinata da
qui. Il blocco le elenca con il collegamento alla loro scheda (o al Cestino).

CHI. Gli edifici non hanno un assegnatario e chiunque nell'agenzia li crea e
li modifica: chiunque nell'agenzia puo' spostare nel Cestino un edificio
VUOTO. Il ripristino segue la regola di Immobili e Contatti: owner, admin e
platform admin in acting tutto il Cestino dell'agenzia, un agent solo cio' che
ha spostato lui; l'elenco del Cestino lo stesso.

RIPRISTINO: `deleted_*` a NULL e nient'altro (stesso id, dati intatti). Gli
edifici ATTIVI simili (stessa chiave catastale o stesso indirizzo: la regola
«palazzina simile» della creazione) si SEGNALANO, mai uniti.
"""
from __future__ import annotations

import json

from core.building_trash import BuildingInTrash
from core.database import core_cursor
from core.exceptions import NotFoundError
from operator_auth import permissions

from . import census, repository
from .lifecycle import (ALREADY_DELETED, NOT_DELETED, NOT_DELETED_BY_YOU, TRASH_BLOCKED, TRASH_NOTE_MAX,
                        TRASH_REASONS, LifecycleConflict, LifecycleForbidden, TrashNotInstalled, _audit,
                        _valida_motivo)

__all__ = ("deletion_check", "trash_building", "restore_building", "list_trash", "trash_info",
           "TRASH_REASONS", "TRASH_NOTE_MAX")

BUILDING_HAS_UNITS = "BUILDING_HAS_UNITS"
BUILDING_IN_TRASH = BuildingInTrash.code

TRASH_BLOCKED_MESSAGE = ("L'edificio ha unità collegate: restano nel loro edificio. "
                         "Spostalo nel Cestino solo quando è vuoto")
ALREADY_DELETED_MESSAGE = "L'edificio è già nel Cestino"
NOT_DELETED_MESSAGE = "L'edificio non è nel Cestino"
NOT_DELETED_BY_YOU_MESSAGE = ("Puoi ripristinare solo gli edifici che hai spostato tu nel Cestino: "
                              "chiedi a un amministratore")
REPLICA_IN_TRASH_MESSAGE = ("La palazzina di questa richiesta è nel Cestino: "
                            "ripristinala dal Cestino invece di crearla di nuovo")
TRASH_NOT_INSTALLED_MESSAGE = ("Il Cestino degli edifici non è ancora disponibile su questo database "
                               "(migration 091 non applicata)")
#: Quante unita' il blocco elenca una per una (oltre, solo il conteggio).
MAX_VOCI = 50


class _NonEliminatoDaTe(LifecycleForbidden):
    code = NOT_DELETED_BY_YOU


def _cestino_installato(cur) -> None:
    census._assicura_083(cur)
    cur.execute("SELECT to_regclass('public.record_lifecycle_events') IS NOT NULL"
                "   AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'"
                "                 AND table_name = 'buildings' AND column_name = 'deleted_at') AS pronto")
    if not cur.fetchone()["pronto"]:
        raise TrashNotInstalled(TRASH_NOT_INSTALLED_MESSAGE)


def _vede_tutta_agenzia(ctx) -> bool:
    return permissions.sees_all_agency_records(getattr(ctx, "role", None), getattr(ctx, "is_platform_admin", False))


def _edificio(cur, agency_id: int, building_id: int, *, lock: bool = False) -> dict:
    """La riga dell'agenzia, ANCHE nel Cestino (chi controlla, sposta o
    ripristina deve vederla). Fuori agenzia o inesistente: 404 (D-6)."""
    cur.execute(f"SELECT * FROM buildings WHERE id = %s AND agency_id = %s{' FOR UPDATE' if lock else ''}",
                (building_id, agency_id))
    riga = repository.row(cur.fetchone())
    if riga is None:
        raise NotFoundError(f"building {building_id} not found")
    return riga


def in_trash(edificio: dict | None) -> bool:
    return bool(edificio) and edificio.get("deleted_at") is not None


def _evento(cur, ctx, agency_id: int, building_id: int, azione: str, *, reason=None, note=None,
            before: dict, metadata: dict | None = None) -> None:
    meta = {"actor_role": getattr(ctx, "role", None),
            "platform_admin": bool(getattr(ctx, "is_platform_admin", False)), **(metadata or {})}
    cur.execute("INSERT INTO record_lifecycle_events (agency_id, entity_type, entity_id, action, reason_code, note, "
                "actor_user_id, before_state, metadata) VALUES (%s, 'building', %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)",
                (agency_id, building_id, azione, reason, note, getattr(ctx, "user_id", None),
                 json.dumps(before, default=str), json.dumps(meta, default=str)))


# ---------------------------------------------------------------------------
# BLOCCHI
# ---------------------------------------------------------------------------

def _unita_collegate(cur, building_id: int) -> list[dict]:
    """TUTTE le righe di `properties` dell'edificio: attive, archiviate,
    pertinenze, «intero stabile», nel Cestino Immobili. Nessun filtro."""
    cur.execute(f"""
        SELECT p.id, p.code, p.title, p.property_type, p.whole_building, p.parent_property_id,
               p.floor, p.internal_number, p.commercial_status, p.archived_at,
               (to_jsonb(p)->>'deleted_at') IS NOT NULL AS in_trash,
               {census.pertinenza_sql('p')} AS pertinenza
          FROM properties p WHERE p.building_id = %s ORDER BY p.id""", (building_id,))
    return [dict(r) for r in cur.fetchall()]


def _stato_unita(u: dict) -> str:
    if u["in_trash"]:
        return "trash"
    if u.get("archived_at") is not None or u.get("commercial_status") == "archived":
        return "archived"
    return "active"


_STATO_TESTO = {"active": "attiva", "archived": "archiviata", "trash": "nel Cestino Immobili"}


def trash_blockers(cur, edificio: dict) -> list[dict]:
    """Il blocco dell'edificio con unita' collegate. `items`: una voce per
    unita' (fino a MAX_VOCI) con etichetta e collegamento; `counts` per stato."""
    unita = _unita_collegate(cur, edificio["id"])
    if not unita:
        return []
    conti = {"active": 0, "archived": 0, "trash": 0}
    voci = []
    for u in unita:
        stato = _stato_unita(u)
        conti[stato] += 1
        if len(voci) >= MAX_VOCI:
            continue
        natura = ("intero stabile" if u.get("whole_building") else
                  "pertinenza" if u.get("pertinenza") else None)
        posto = " ".join(x for x in (f"piano {u['floor']}" if u.get("floor") else None,
                                     f"int. {u['internal_number']}" if u.get("internal_number") else None) if x)
        etichetta = " · ".join(x for x in (u.get("code") or f"#{u['id']}", u.get("title"), posto or None, natura,
                                           _STATO_TESTO[stato]) if x)
        voci.append({"id": u["id"], "state": stato, "label": etichetta,
                     "href": "#/cestino" if stato == "trash" else f"#/immobili/{u['id']}"})
    def n(numero, uno, molti):
        return f"{numero} {uno if numero == 1 else molti}"
    parti = [n(conti["active"], "attiva", "attive") if conti["active"] else None,
             n(conti["archived"], "archiviata", "archiviate") if conti["archived"] else None,
             f"{conti['trash']} nel Cestino Immobili" if conti["trash"] else None]
    totale = len(unita)
    etichetta = (f"L'edificio ha {totale} {'unità collegata' if totale == 1 else 'unità collegate'} "
                 f"({', '.join(p for p in parti if p)}): restano nel loro edificio e non si spostano da qui. "
                 "Si può spostare nel Cestino solo un edificio vuoto.")
    return [{"code": BUILDING_HAS_UNITS, "label": etichetta, "items": voci, "counts": {**conti, "total": totale},
             "link": {"href": f"#/edifici/{edificio['id']}", "label": "Apri l'edificio"}}]


# ---------------------------------------------------------------------------
# deletion-check / trash / restore / elenco / scheda
# ---------------------------------------------------------------------------

def deletion_check(ctx, building_id: int) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _cestino_installato(cur)
        edificio = _edificio(cur, agency_id, building_id)
        if in_trash(edificio):
            blocchi = [{"code": ALREADY_DELETED, "label": ALREADY_DELETED_MESSAGE, "items": []}]
        else:
            blocchi = trash_blockers(cur, edificio)
    return {"can_trash": not blocchi, "blockers": blocchi}


def trash_building(ctx, building_id: int, reason_code, note=None) -> dict:
    agency_id = ctx.require_agency()
    reason_code, note = _valida_motivo(reason_code, note)
    with core_cursor(commit=True) as (_, cur):
        _cestino_installato(cur)
        # FOR UPDATE: un collegamento concorrente (trigger 083/091, FOR SHARE
        # sull'edificio) attende, oppure si attende lui; chi arriva dopo vede
        # lo stato dell'altro.
        edificio = _edificio(cur, agency_id, building_id, lock=True)
        if in_trash(edificio):
            raise LifecycleConflict(ALREADY_DELETED_MESSAGE, ALREADY_DELETED)
        blocchi = trash_blockers(cur, edificio)
        if blocchi:
            raise LifecycleConflict(TRASH_BLOCKED_MESSAGE, TRASH_BLOCKED, blockers=blocchi)
        cur.execute("UPDATE buildings SET deleted_at = NOW(), deleted_by_user_id = %s, deleted_reason = %s, "
                    "updated_at = NOW() WHERE id = %s AND agency_id = %s RETURNING *",
                    (getattr(ctx, "user_id", None), reason_code, building_id, agency_id))
        riga = repository.row(cur.fetchone())
        _evento(cur, ctx, agency_id, building_id, "trash", reason=reason_code, note=note,
                before={k: edificio.get(k) for k in ("name", "city", "address", "civic_number", "units_declared",
                                                     "census_status", "archived_at")})
    _audit("trash", ctx, entity_type="building", entity_id=building_id, reason=reason_code)
    return riga


def possible_duplicates(cur, agency_id: int, edificio: dict) -> list[dict]:
    """Edifici ATTIVI simili (la regola «palazzina simile» della creazione):
    solo un avviso, nulla si unisce o si modifica."""
    return census._simili_edificio(cur, agency_id, edificio, escluso=edificio["id"])


def restore_building(ctx, building_id: int) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        _cestino_installato(cur)
        edificio = _edificio(cur, agency_id, building_id, lock=True)
        if not in_trash(edificio):
            raise LifecycleConflict(NOT_DELETED_MESSAGE, NOT_DELETED)
        if not _vede_tutta_agenzia(ctx) and edificio.get("deleted_by_user_id") != getattr(ctx, "user_id", None):
            raise _NonEliminatoDaTe(NOT_DELETED_BY_YOU_MESSAGE)
        cur.execute("UPDATE buildings SET deleted_at = NULL, deleted_by_user_id = NULL, deleted_reason = NULL, "
                    "updated_at = NOW() WHERE id = %s AND agency_id = %s RETURNING *", (building_id, agency_id))
        riga = repository.row(cur.fetchone())
        doppioni = possible_duplicates(cur, agency_id, riga)
        _evento(cur, ctx, agency_id, building_id, "restore",
                before={k: edificio.get(k) for k in ("deleted_at", "deleted_by_user_id", "deleted_reason")},
                metadata={"possible_duplicates": [d["id"] for d in doppioni]})
    _audit("restore", ctx, entity_type="building", entity_id=building_id)
    return {**riga, "possible_duplicates": doppioni}


TRASH_LIST_MAX = 200


def list_trash(ctx, *, limit: int = 50, offset: int = 0) -> dict:
    """Gli edifici nel Cestino dell'agenzia, dal piu' recente (stessa regola
    del ripristino: owner/admin tutto, agent solo i suoi)."""
    from .interactions import NOME_OPERATORE

    agency_id = ctx.require_agency()
    limit = max(1, min(int(limit), TRASH_LIST_MAX))
    offset = max(0, int(offset))
    vuoto = {"items": [], "has_more": False, "limit": limit, "offset": offset}
    tutti = _vede_tutta_agenzia(ctx)
    if not tutti and getattr(ctx, "role", None) != "agent":
        return vuoto
    filtri, params = ["b.agency_id = %s", "b.deleted_at IS NOT NULL"], [agency_id]
    if not tutti:
        filtri.append("b.deleted_by_user_id = %s")
        params.append(getattr(ctx, "user_id", None))
    with core_cursor() as (_, cur):
        _cestino_installato(cur)
        cur.execute(
            f"""SELECT b.id, b.name, b.building_type, b.address, b.civic_number, b.city, b.province,
                       b.units_declared, b.census_status, b.deleted_at, b.deleted_reason, b.deleted_by_user_id,
                       (SELECT {NOME_OPERATORE.format(a='u')} FROM operator_users u
                         WHERE u.id = b.deleted_by_user_id) AS deleted_by_name,
                       (SELECT e.note FROM record_lifecycle_events e
                         WHERE e.agency_id = b.agency_id AND e.entity_type = 'building'
                           AND e.entity_id = b.id AND e.action = 'trash'
                         ORDER BY e.occurred_at DESC, e.id DESC LIMIT 1) AS deleted_note
                  FROM buildings b
                 WHERE {' AND '.join(filtri)}
                 ORDER BY b.deleted_at DESC, b.id DESC
                 LIMIT %s OFFSET %s""",
            params + [limit + 1, offset])
        righe = [dict(r) for r in cur.fetchall()]
    return {"items": righe[:limit], "has_more": len(righe) > limit, "limit": limit, "offset": offset}


def trash_info(cur, ctx, edificio: dict) -> dict | None:
    """Per la scheda di un edificio nel Cestino: chi, quando, perche', la nota
    e se chi guarda puo' ripristinarlo. None fuori dal Cestino."""
    if not in_trash(edificio):
        return None
    from .interactions import NOME_OPERATORE
    cur.execute(f"SELECT {NOME_OPERATORE.format(a='u')} AS nome FROM operator_users u WHERE u.id = %s",
                (edificio.get("deleted_by_user_id"),))
    riga = cur.fetchone()
    cur.execute("SELECT note FROM record_lifecycle_events WHERE agency_id = %s AND entity_type = 'building' "
                "AND entity_id = %s AND action = 'trash' ORDER BY occurred_at DESC, id DESC LIMIT 1",
                (edificio.get("agency_id"), edificio["id"]))
    evento = cur.fetchone()
    puo = _vede_tutta_agenzia(ctx) or edificio.get("deleted_by_user_id") == getattr(ctx, "user_id", None)
    return {"deleted_at": edificio.get("deleted_at"), "deleted_reason": edificio.get("deleted_reason"),
            "deleted_by_user_id": edificio.get("deleted_by_user_id"),
            "deleted_by_name": riga["nome"] if riga else None,
            "deleted_note": evento["note"] if evento else None, "can_restore": bool(puo)}
