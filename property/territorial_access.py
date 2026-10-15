"""AGENZIE-TERRITORI C1 - accesso TERRITORIALE in sola lettura agli immobili.

UNA regola, UN posto. Un immobile resta dell'agenzia che l'ha inserito
(`properties.agency_id`, mai cambiato da qui). In piu' lo LEGGE l'agenzia
territorialmente competente per il suo Comune. Tre esiti, distinti e
verificabili:

  * `origin`       l'agenzia della sessione e' quella dell'immobile: valgono le
                   regole di sempre (questo modulo non le tocca e non le decide);
  * `territorial`  l'agenzia della sessione ha l'assegnazione ATTIVA del Comune
                   dell'immobile: sola lettura (C1; la modifica arriva in C2);
  * negato         tutto il resto -> 404, come un id inesistente (non rivela
                   l'esistenza del record).

LA COMPETENZA E' QUELLA DELLA RETE, NON UNA SECONDA
Si riusa la catena gia' usata per instradare le stime pubbliche
(`network_routing.repository.find_routable_agency_id`): alias ATTIVO della
sorgente `public_stima_comune` con lo stesso valore normalizzato (stessa
`NORMALISED`) -> territorio `municipality` -> assegnazione `active` ->
agenzia attiva. Il Comune e' `properties.city` (il valore del catalogo,
validato dal form): nessuna deduzione da indirizzo libero, GPS o agenzia
dell'operatore. Se la catena non e' completa (alias assente o revocato,
territorio non assegnato o sospeso, comune vuoto) NON c'e' condivisione.
L'indice `uq_territory_alias_active_value` e `uq_agency_territory_single_active`
garantiscono che un valore porti al massimo a UNA agenzia: nessuna ambiguita'.

COSA NON FA (C1)
Nessuna scrittura: le rotte di modifica restano filtrate per `agency_id` e
rispondono 404 all'agenzia competente. Nessuna apertura di edifici, unita'
dell'edificio, censimento, Zona, interazioni, incarichi, documenti, visite,
Cestino, STIMA Voice: restano come prima. La scheda condivisa porta i dati
dell'immobile, i contatti COLLEGATI a quell'immobile (venditore e recapiti) e
la cronologia di stato e prezzo; niente altro della rubrica d'origine.
"""
from __future__ import annotations

from typing import Any

from psycopg2 import errors

from core import property_trash as _property_trash
from core.database import core_cursor
from core.exceptions import NotFoundError
from network_routing.repository import ALIAS_SOURCE_PUBLIC_STIMA, NORMALISED

from . import repository

MODE_ORIGIN = "origin"
MODE_TERRITORIAL = "territorial"
REASON_ORIGIN = "origin_agency"
REASON_TERRITORIAL = "territorial_competence"
TERRITORY_KIND = "municipality"

#: Le chiavi della scheda che la lettura territoriale NON porta (C1): documenti e
#: visite hanno rotte e dati propri dell'agenzia d'origine (file privati,
#: acquirenti di quell'agenzia); l'edificio non e' aperto in C1.
_CHIAVI_ESCLUSE = ("documents", "visits", "building")


def _catena(alias_citta: str) -> str:
    """La catena alias -> territorio -> assegnazione attiva -> agenzia attiva,
    su `<alias_citta>`, con UN parametro: l'agenzia che legge."""
    return (
        "SELECT nta.territory_id FROM network_territory_aliases nta "
        "JOIN network_territories nt ON nt.id = nta.territory_id "
        "JOIN agency_territory_assignments ata ON ata.territory_id = nt.id "
        "JOIN agencies ag ON ag.id = ata.agency_id "
        f"WHERE nta.source = '{ALIAS_SOURCE_PUBLIC_STIMA}' AND nta.status = 'active' "
        f"AND nt.kind = '{TERRITORY_KIND}' AND ata.status = 'active' AND ag.status = 'active' "
        f"AND {alias_citta} IS NOT NULL AND btrim({alias_citta}) <> '' "
        f"AND {NORMALISED.format('nta.match_value')} = {NORMALISED.format(alias_citta)} "
        "AND ata.agency_id = %s"
    )


def territorial_predicate(alias: str, agency_id: int) -> tuple[str, list]:
    """`EXISTS(...)`: l'agenzia `agency_id` e' competente per il Comune della riga
    `alias` di `properties`. Mai vuoto, mai `TRUE`."""
    return f"EXISTS ({_catena(f'{alias}.city')})", [agency_id]


def _competenza(cur, city: str | None, agency_id: int) -> dict[str, Any] | None:
    if not city or not str(city).strip():
        return None
    try:
        cur.execute(
            "SELECT nt.id, nt.label FROM (SELECT %s::text AS city) x "
            f"JOIN network_territories nt ON nt.id IN ({_catena('x.city')}) LIMIT 1",
            (city, agency_id),
        )
    except (errors.UndefinedTable, errors.UndefinedColumn):
        return None                                   # rete non installata: nessuna condivisione
    riga = cur.fetchone()
    return {"id": riga["id"], "label": riga["label"]} if riga else None


def _nome_agenzia(cur, agency_id: int) -> str | None:
    cur.execute("SELECT name FROM agencies WHERE id = %s", (agency_id,))
    riga = cur.fetchone()
    return riga["name"] if riga else None


def origin_access(prop: dict) -> dict[str, Any]:
    """Il blocco `access` di una scheda letta dall'agenzia d'origine (regole di sempre)."""
    return {"mode": MODE_ORIGIN, "reason": REASON_ORIGIN, "origin_agency_id": prop.get("agency_id"),
            "read_only": False}


def property_access(ctx, property_id: int) -> dict[str, Any] | None:
    """La decisione per QUESTO immobile e QUESTA sessione: `origin`,
    `territorial` oppure None (negato). Legge soltanto."""
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        cur.execute("SELECT id, agency_id, city, to_jsonb(properties)->>'deleted_at' AS deleted_at "
                    "FROM properties WHERE id = %s", (property_id,))
        riga = cur.fetchone()
        if riga is None or riga["deleted_at"] is not None:
            return None
        if riga["agency_id"] == agency_id:
            return {"mode": MODE_ORIGIN, "reason": REASON_ORIGIN, "origin_agency_id": agency_id, "read_only": False}
        territorio = _competenza(cur, riga["city"], agency_id)
        if territorio is None:
            return None
        return {"mode": MODE_TERRITORIAL, "reason": REASON_TERRITORIAL, "origin_agency_id": riga["agency_id"],
                "origin_agency_name": _nome_agenzia(cur, riga["agency_id"]), "territory": territorio,
                "read_only": True}


def shared_property(ctx, property_id: int) -> dict[str, Any]:
    """La scheda di un immobile letto per competenza territoriale (sola lettura).
    404 se l'accesso non e' territoriale (anche per l'origine: quella passa dalla
    scheda di sempre)."""
    accesso = property_access(ctx, property_id)
    if accesso is None or accesso["mode"] != MODE_TERRITORIAL:
        raise NotFoundError(f"property {property_id} not found")
    scheda = repository.get_property(property_id)     # letta per id DOPO la decisione qui sopra
    for chiave in _CHIAVI_ESCLUSE:
        scheda.pop(chiave, None)
    scheda["documents"] = []
    scheda["visits"] = []
    scheda["document_issues"] = None
    scheda["access"] = accesso
    return scheda


def property_view(ctx, property_id: int) -> dict[str, Any]:
    """La scheda per `GET /properties/{id}`: prima quella di sempre (agenzia
    d'origine, regole e errori invariati, con il blocco `access`); solo se per
    questa agenzia non esiste, la lettura territoriale. Negato -> 404."""
    from . import service                             # import locale: service non dipende da qui
    try:
        scheda = service.get_property(ctx, property_id)
    except NotFoundError:
        return shared_property(ctx, property_id)
    scheda["access"] = origin_access(scheda)
    return scheda


def list_shared(ctx, *, limit: int, offset: int, search: str | None = None, city: str | None = None,
                status: str | None = None, record_kind: str = "crm", include_archived: bool = False) -> list[dict]:
    """Gli immobili di ALTRE agenzie che la sessione legge per competenza
    territoriale. Stessi filtri di base della lista (ricerca, comune, stato, tipo
    di scheda, archiviati), stesso ordinamento, paginazione propria. Mai le
    schede dell'agenzia stessa (quelle restano nella lista di sempre)."""
    agency_id = ctx.require_agency()
    predicato, parametri = territorial_predicate("p", agency_id)
    filtri = ["p.agency_id <> %s", predicato, _property_trash.live("p")]
    params: list = [agency_id, *parametri]
    tipo = repository.record_kind_filter(record_kind)
    if tipo is not None:
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
    if city:
        filtri.append("p.city ILIKE %s")
        params.append(f"%{city}%")
    with core_cursor() as (_, cur):
        try:
            cur.execute(
                "SELECT p.id, p.code, p.title, p.property_type, p.commercial_status, p.classification, "
                "p.record_kind, p.address, p.civic_number, p.city, p.province, p.microzone, p.asking_price, "
                "p.surface_sqm, p.rooms, p.updated_at, p.agency_id AS origin_agency_id, ag.name AS origin_agency_name "
                "FROM properties p JOIN agencies ag ON ag.id = p.agency_id "
                f"WHERE {' AND '.join(filtri)} ORDER BY p.updated_at DESC, p.id DESC LIMIT %s OFFSET %s",
                [*params, limit, offset],
            )
        except (errors.UndefinedTable, errors.UndefinedColumn):
            return []
        righe = [dict(r) for r in cur.fetchall()]
    for r in righe:
        r["access"] = {"mode": MODE_TERRITORIAL, "reason": REASON_TERRITORIAL,
                       "origin_agency_id": r["origin_agency_id"], "read_only": True}
    return righe
