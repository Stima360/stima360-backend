"""CRM-OPS-4 - INCARICHI: una VISTA, non un'entita'.

Un incarico e' un immobile il cui incarico e' nato da un'acquisizione
(081): `properties.acquisition_id IS NOT NULL` con `mandate_type` e
`mandate_start` valorizzati. Nessuna tabella `mandates`, nessun secondo
inserimento: quando `POST /api/acquisitions/{id}/mandate` riesce, l'immobile
compare qui da solo, perche' qui si legge soltanto.

Solo lettura. La modifica dell'incarico resta `PATCH /api/property/
properties/{id}` (stesse guardie della scheda Immobile); la creazione resta
solo dall'acquisizione.

SCOPE: quello degli immobili - l'agenzia intera per ogni ruolo, come
`GET /api/property/properties` che gia' espone tipo e date dell'incarico.
L'acquisizione d'origine invece segue la regola delle Acquisizioni (l'agente
vede solo le proprie): il suo id si mostra sempre (e' gia' sull'immobile),
ma `acquisition.visible` dice alla UI se il collegamento si puo' aprire.

"Oggi" e' il giorno di Roma: un incarico che scade oggi ha 0 giorni residui
fino a mezzanotte italiana, non fino a quella UTC.
"""
from __future__ import annotations

from core import property_mandate as _mandato
from core.database import core_cursor
from core.exceptions import NotFoundError, ValidationError
from core.scope import scoped_predicate

from . import interactions

EXPIRY_FILTERS = {"within_7": 7, "within_15": 15, "within_30": 30, "expired": None}
SORTS = {
    "expiry": "p.mandate_end ASC NULLS LAST, p.id ASC",
    "start": "p.mandate_start DESC, p.id DESC",
    "last_interaction": "ui.occurred_at DESC NULLS LAST, p.id DESC",
}
MAX_LIMIT = 200
OGGI_ROMA = "(NOW() AT TIME ZONE 'Europe/Rome')::date"

#: FIX-MANDATE-1: la vista Incarichi = la definizione CANONICA di incarico
#: (core/property_mandate.py, la stessa del Cestino) ristretta all'origine
#: «acquisizione», l'unica che questa sezione gestisce. Un incarico nato da
#: un'acquisizione con tipo o inizio azzerati resta un incarico e si vede,
#: con i dati da completare (`missing_fields`); gli incarichi storici (pre-081)
#: e le firme del ponte LMC-15 restano fuori, come deciso da CRM-OPS-4 (sono
#: protetti dal Cestino, che dice dove consultarli). Nessun filtro di stato
#: qui: scaduti, venduti e archiviati sono incarichi (i filtri sono a parte).
E_UN_INCARICO = (f"{_mandato.acquisition_mandate_sql('p')} "
                 # Cestino Immobili (fase 2B2): un immobile nel Cestino non e' un incarico operativo
                 "AND (to_jsonb(p)->>'deleted_at') IS NULL")


def _base(ctx) -> tuple[str, list]:
    """FROM + JOIN comuni a elenco e scheda; i parametri nell'ordine."""
    predicato, params = scoped_predicate(ctx, "activities", "ax")
    sql = f"""
      FROM properties p
      JOIN acquisitions a ON a.id = p.acquisition_id AND a.agency_id = p.agency_id
      LEFT JOIN operator_users ag ON ag.id = p.assigned_agent_id
      LEFT JOIN operator_users aa ON aa.id = a.assigned_agent_id
      LEFT JOIN contacts oc ON oc.id = a.owner_contact_id AND oc.agency_id = p.agency_id
      LEFT JOIN LATERAL (
            SELECT ax.occurred_at, ax.activity_type, ax.id,
                   {interactions.NOME_OPERATORE.format(a='ux')} AS author_name
              FROM activities ax
              LEFT JOIN operator_users ux ON ux.id = ax.created_by_user_id
             WHERE {predicato} AND ax.agency_id = p.agency_id AND ax.property_id = p.id
             ORDER BY ax.occurred_at DESC, ax.id DESC
             LIMIT 1) ui ON TRUE
    """
    return sql, params


_COLONNE = f"""
    p.id AS property_id, p.code, p.title, p.address, p.civic_number, p.city, p.province,
    p.microzone, p.property_type, p.commercial_status, p.archived_at,
    p.mandate_type, p.mandate_start, p.mandate_end,
    (p.mandate_end - {OGGI_ROMA}) AS days_to_expiry,
    p.asking_price, p.minimum_price,
    p.assigned_agent_id AS agent_id,
    COALESCE({interactions.NOME_OPERATORE.format(a='ag')}, p.assigned_to) AS agent_name,
    a.id AS acquisition_id, a.status AS acquisition_status, a.acquired_at,
    a.assigned_agent_id AS acquisition_agent_id,
    {interactions.NOME_OPERATORE.format(a='aa')} AS acquisition_agent_name,
    a.valuation_price, a.owner_contact_id AS main_owner_id,
    {interactions.NOME_CONTATTO.format(a='oc')} AS main_owner_name,
    oc.phone AS main_owner_phone,
    ui.occurred_at AS last_interaction_at, ui.activity_type AS last_interaction_type,
    ui.author_name AS last_interaction_author
"""


def _proprietari(cur, property_ids, main_by_property) -> dict:
    """I proprietari REALI (`property_contacts`, ruoli owner/seller), uno per
    contatto, il principale dell'acquisizione per primo. Stessa fonte della
    scheda Immobile e delle Acquisizioni: nessuna copia di nomi o recapiti."""
    if not property_ids:
        return {}
    cur.execute(
        f"""
        SELECT pc.property_id, c.id AS contact_id,
               {interactions.NOME_CONTATTO.format(a='c')} AS display_name,
               c.phone, c.email,
               array_agg(DISTINCT pc.role ORDER BY pc.role) AS roles,
               bool_or(pc.is_primary) AS is_primary
          FROM property_contacts pc
          JOIN properties p ON p.id = pc.property_id
          JOIN contacts c ON c.id = pc.contact_id AND c.agency_id = p.agency_id
         WHERE pc.property_id = ANY(%s) AND pc.role IN ('owner', 'seller')
         GROUP BY pc.property_id, c.id
         ORDER BY pc.property_id, bool_or(pc.is_primary) DESC, c.id
        """,
        (list(property_ids),))
    esito: dict = {pid: [] for pid in property_ids}
    for r in cur.fetchall():
        voce = dict(r)
        pid = voce.pop("property_id")
        voce["is_main"] = voce["contact_id"] == main_by_property.get(pid)
        esito[pid].append(voce)
    for pid, voci in esito.items():
        voci.sort(key=lambda v: (not v["is_main"], not v["is_primary"], v["contact_id"]))
    return esito


def _riga(ctx, r, proprietari) -> dict:
    voce = dict(r)
    vede = (getattr(ctx, "sees_all_agency_records", False)
            or voce["acquisition_agent_id"] == getattr(ctx, "user_id", None))
    voce["owners"] = proprietari.get(voce["property_id"], [])
    voce["missing_fields"] = _mandato.missing_fields(voce)          # FIX-MANDATE-1
    voce["other_owners"] = [o for o in voce["owners"] if not o["is_main"]]
    voce["acquisition"] = {
        "id": voce.pop("acquisition_id"), "status": voce.pop("acquisition_status"),
        "acquired_at": voce.pop("acquired_at"),
        "agent_id": voce.pop("acquisition_agent_id"),
        "agent_name": voce.pop("acquisition_agent_name"),
        "visible": bool(vede),
    }
    voce["last_interaction"] = None if voce["last_interaction_at"] is None else {
        "occurred_at": voce["last_interaction_at"],
        "type": voce["last_interaction_type"],
        "type_label": interactions.INTERACTION_LABELS_IT.get(
            voce["last_interaction_type"], voce["last_interaction_type"]),
        "author_name": voce["last_interaction_author"],
    }
    for k in ("last_interaction_type", "last_interaction_author"):
        voce.pop(k)
    if voce["days_to_expiry"] is None:
        voce["expiry_state"] = "open_ended"
    elif voce["days_to_expiry"] < 0:
        voce["expiry_state"] = "expired"
    elif voce["days_to_expiry"] <= 30:
        voce["expiry_state"] = "expiring"
    else:
        voce["expiry_state"] = "active"
    return voce


def list_mandates(ctx, *, search=None, agent_id=None, city=None, mandate_type=None,
                  commercial_status=None, expiry=None, sort="expiry", limit=50, offset=0) -> dict:
    agency_id = ctx.require_agency()
    if expiry is not None and expiry not in EXPIRY_FILTERS:
        raise ValidationError("Filtro scadenza non valido")
    if sort not in SORTS:
        raise ValidationError("Ordinamento non valido")
    if not 1 <= int(limit) <= MAX_LIMIT or int(offset) < 0:
        raise ValidationError("Paginazione non valida")
    base, params = _base(ctx)
    filtri = ["p.agency_id = %s", E_UN_INCARICO]
    params = list(params) + [agency_id]
    if commercial_status:
        filtri.append("p.commercial_status = %s")
        params.append(commercial_status)
    else:
        filtri.append("p.archived_at IS NULL")
    if agent_id is not None:
        filtri.append("p.assigned_agent_id = %s")
        params.append(int(agent_id))
    if city:
        filtri.append("p.city ILIKE %s")
        params.append(f"%{city.strip()}%")
    if mandate_type:
        filtri.append("p.mandate_type ILIKE %s")
        params.append(mandate_type.strip())
    if search:
        filtri.append(
            "(p.title ILIKE %s OR p.code ILIKE %s OR p.address ILIKE %s OR p.city ILIKE %s "
            f"OR {interactions.NOME_CONTATTO.format(a='oc')} ILIKE %s)")
        params += [f"%{search.strip()}%"] * 5
    if expiry == "expired":
        filtri.append(f"p.mandate_end < {OGGI_ROMA}")
    elif expiry is not None:
        filtri.append(f"p.mandate_end BETWEEN {OGGI_ROMA} AND {OGGI_ROMA} + %s")
        params.append(EXPIRY_FILTERS[expiry])
    with core_cursor() as (_, cur):
        cur.execute(
            f"SELECT {_COLONNE} {base} WHERE {' AND '.join(filtri)} "
            f"ORDER BY {SORTS[sort]} LIMIT %s OFFSET %s",
            params + [int(limit), int(offset)])
        righe = cur.fetchall()
        proprietari = _proprietari(cur, [r["property_id"] for r in righe],
                                   {r["property_id"]: r["main_owner_id"] for r in righe})
        cur.execute("SELECT DISTINCT mandate_type FROM properties p "
                    f"WHERE p.agency_id = %s AND {E_UN_INCARICO} ORDER BY 1", (agency_id,))
        tipi = [r["mandate_type"] for r in cur.fetchall()]
    return {"items": [_riga(ctx, r, proprietari) for r in righe],
            "mandate_types": tipi,
            "expiry_filters": list(EXPIRY_FILTERS), "sorts": list(SORTS)}


def get_mandate(ctx, property_id: int) -> dict:
    agency_id = ctx.require_agency()
    base, params = _base(ctx)
    with core_cursor() as (_, cur):
        cur.execute(
            f"SELECT {_COLONNE} {base} WHERE p.agency_id = %s AND p.id = %s AND {E_UN_INCARICO}",
            list(params) + [agency_id, property_id])
        r = cur.fetchone()
        if r is None:
            raise NotFoundError(f"mandate {property_id} not found")
        proprietari = _proprietari(cur, [property_id], {property_id: r["main_owner_id"]})
        voce = _riga(ctx, r, proprietari)
        # Il prezzo concordato all'incarico: si LEGGE dall'evento che l'ha
        # registrato (CRM-OPS-3), non si copia da nessuna parte.
        cur.execute("SELECT changes->>'agreed_price' AS agreed_price, occurred_at "
                    "FROM acquisition_events WHERE acquisition_id = %s AND agency_id = %s "
                    "AND event_type = 'mandate_created' ORDER BY id DESC LIMIT 1",
                    (voce["acquisition"]["id"], agency_id))
        ev = cur.fetchone()
        voce["agreed_price"] = ev["agreed_price"] if ev else None
        voce["interaction_options"] = interactions.options()
        return voce
