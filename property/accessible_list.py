"""AGENZIE-TERRITORI C3 - la lista UNIFICATA degli immobili accessibili.

Una sola query, una sola paginazione: gli immobili dell'agenzia della sessione
(`origin`) e quelli di ALTRE agenzie letti per competenza territoriale
(`territorial`, regola C1) in un unico elenco ordinato come la lista di sempre
(`updated_at DESC, id DESC`). Non si sommano due pagine gia' paginate.

Tre viste (`scope`):
  * `all`     propri + condivisi (insiemi disgiunti per `agency_id`: nessun duplicato);
  * `mine`    soltanto `p.agency_id = agenzia` (stesso insieme della lista di sempre
              con gli stessi filtri);
  * `shared`  soltanto le schede di altre agenzie con la catena C1 completa.

I conteggi (`counts`) sono calcolati con GLI STESSI filtri, per tutte e tre le
viste, nella stessa transazione della pagina.

PERMESSI INVARIATI
Il predicato territoriale e' `territorial_access.territorial_predicate` (C1),
riusato tale e quale. La lista di sempre (`GET /properties`) non cambia ed e'
quella che alimenta i selettori che portano a una scrittura: questa lista serve
SOLO all'elenco «Immobili». Le colonne restituite sono le stesse per ogni riga e
non escono dalla scheda condivisa C1 (niente documenti, visite, conteggi interni
dell'agenzia d'origine); `assigned_agent_id` vale solo per le righe proprie
(serve alla regola di «Elimina…» dell'elenco) ed e' NULL per le condivise.
"""
from __future__ import annotations

from typing import Any

from psycopg2 import errors

from core import property_trash as _property_trash
from core.database import core_cursor
from core.exceptions import ValidationError

from . import repository
from .territorial_access import MODE_ORIGIN, MODE_TERRITORIAL, REASON_ORIGIN, REASON_TERRITORIAL, territorial_predicate

SCOPES = ("all", "mine", "shared")

_COLONNE = (
    "p.id, p.code, p.title, p.property_type, p.commercial_status, p.classification, p.address, p.civic_number, "
    "p.city, p.province, p.microzone, p.asking_price, p.surface_sqm, p.rooms, p.updated_at, "
    "(to_jsonb(p)->>'pertinenza_kind') AS pertinenza_kind, "
    "jsonb_strip_nulls(jsonb_build_object('site_unverified', p.metadata->'site_unverified')) AS metadata, "
    "p.agency_id AS origin_agency_id, ag.name AS origin_agency_name"
)


def _filtri(agency_id: int, *, search, status, classification, city, include_archived) -> tuple[list[str], list]:
    """I filtri della lista di sempre (`repository.list_properties`) usati dall'elenco:
    solo schede commerciali, mai il Cestino, archiviati esclusi salvo richiesta."""
    filtri: list[str] = [_property_trash.live("p")]
    params: list = []
    tipo = repository.record_kind_filter("crm")
    filtri.append(tipo[0])
    params += tipo[1]
    if search:
        filtri.append("(p.title ILIKE %s OR p.code ILIKE %s OR p.address ILIKE %s OR p.city ILIKE %s)")
        params += [f"%{search}%"] * 4
    if status:
        filtri.append("p.commercial_status = %s")
        params.append(status)
    if not include_archived and status != "archived":
        filtri.append("p.archived_at IS NULL AND p.commercial_status <> 'archived'")
    if classification:
        filtri.append("p.classification = %s")
        params.append(classification)
    if city:
        filtri.append("p.city ILIKE %s")
        params.append(f"%{city}%")
    return filtri, params


def _esegui(cur, agency_id: int, scope: str, filtri: list[str], params: list, limit: int, offset: int,
            con_rete: bool) -> tuple[list[dict], dict[str, int]]:
    if con_rete:
        predicato, p_pred = territorial_predicate("p", agency_id)
        condivisi = f"(p.agency_id <> %s AND {predicato})"
        p_condivisi = [agency_id, *p_pred]
    else:                                              # rete non installata: nessuna condivisione
        condivisi, p_condivisi = "FALSE", []
    propri, p_propri = "p.agency_id = %s", [agency_id]
    visibili = f"({propri} OR {condivisi})"
    where = " AND ".join([*filtri, visibili])
    base = [*params, *p_propri, *p_condivisi]

    cur.execute(
        "SELECT COUNT(*) AS tutti, COUNT(*) FILTER (WHERE p.agency_id = %s) AS miei "
        f"FROM properties p WHERE {where}",
        [agency_id, *base],
    )
    c = cur.fetchone()
    conteggi = {"all": int(c["tutti"]), "mine": int(c["miei"]), "shared": int(c["tutti"]) - int(c["miei"])}

    vista = {"all": visibili, "mine": propri, "shared": condivisi}[scope]
    p_vista = {"all": [*p_propri, *p_condivisi], "mine": p_propri, "shared": p_condivisi}[scope]
    cur.execute(
        f"SELECT {_COLONNE}, CASE WHEN p.agency_id = %s THEN p.assigned_agent_id END AS assigned_agent_id "
        "FROM properties p JOIN agencies ag ON ag.id = p.agency_id "
        f"WHERE {' AND '.join([*filtri, vista])} ORDER BY p.updated_at DESC, p.id DESC LIMIT %s OFFSET %s",
        [agency_id, *params, *p_vista, limit, offset],
    )
    return [dict(r) for r in cur.fetchall()], conteggi


def list_accessible(ctx, *, scope: str = "all", limit: int = 50, offset: int = 0, search: str | None = None,
                    status: str | None = None, classification: str | None = None, city: str | None = None,
                    include_archived: bool = False) -> dict[str, Any]:
    if scope not in SCOPES:
        raise ValidationError("scope must be all, mine or shared")
    agency_id = ctx.require_agency()
    filtri, params = _filtri(agency_id, search=search, status=status, classification=classification, city=city,
                             include_archived=include_archived)
    try:
        with core_cursor() as (_, cur):
            righe, conteggi = _esegui(cur, agency_id, scope, filtri, params, limit, offset, True)
    except (errors.UndefinedTable, errors.UndefinedColumn):
        with core_cursor() as (_, cur):
            righe, conteggi = _esegui(cur, agency_id, scope, filtri, params, limit, offset, False)
    for r in righe:
        if r["origin_agency_id"] == agency_id:
            r["access"] = {"mode": MODE_ORIGIN, "reason": REASON_ORIGIN, "origin_agency_id": agency_id,
                           "read_only": False}
        else:
            r["access"] = {"mode": MODE_TERRITORIAL, "reason": REASON_TERRITORIAL,
                           "origin_agency_id": r["origin_agency_id"], "origin_agency_name": r["origin_agency_name"],
                           "read_only": True}
    return {"items": righe, "scope": scope, "total": conteggi[scope], "counts": conteggi,
            "limit": limit, "offset": offset}
