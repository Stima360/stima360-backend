"""STIMA360 ZONA-3 (C2) - la posizione VERIFICATA di un IMMOBILE INDIPENDENTE.

Tre operazioni, nient'altro, sul modello di property/building_position.py (T4):

  * `position_info`   - lo stato della posizione dell'immobile (coordinate,
    verificata o no, quando e da chi);
  * `confirm_position` - l'operatore conferma il punto scelto su Zona:
    coordinate e verifica nella STESSA istruzione, nella stessa transazione;
  * `list_verified`   - le SOLE righe leggere che servono alla mappa: gli
    immobili indipendenti dell'agenzia con posizione verificata (pagina dopo
    pagina, come la lista degli edifici). Niente prezzi, note, contatti o
    altri dati riservati: identificazione, indirizzo, coordinate, verifica.

La regola vera e' nel database (migration 103, `properties_position_verification`):
la verifica si scrive solo dentro la transazione che accende il segnale
`stima360.property_position_confirm` per QUESTO immobile, la data e' quella
del server, un immobile con edificio non ha mai una verifica propria (la
posizione e' quella dell'edificio, 102) e qualunque altro cambio di
coordinate la fa decadere. Qui si accende il segnale solo dopo aver
controllato contesto, agenzia e immobile.

L'operatore e' SEMPRE quello autenticato (`ctx.user_id`): nessun
identificativo arriva dal client. Permessi: gli stessi della modifica della
scheda (ogni operatore attivo dell'agenzia; il Super Admin solo operando in
un'agenzia, `ctx.require_agency()`). Un immobile di un'altra agenzia e' 404,
uno nel Cestino 409 PROPERTY_IN_TRASH (stessa guardia di DELETE-ARCH 2B1),
un'unita' di palazzina 409 POSITION_NOT_INDEPENDENT. Nessuna cancellazione,
nessun altro dato della scheda letto per scriverlo o modificato.
"""
from __future__ import annotations

from core.database import core_cursor
from core.exceptions import NotFoundError, PermissionDenied

from core import property_trash as _property_trash

from . import census, lifecycle
from .building_position import PositionNotInstalled, _nome, validate_coordinates

POSITION_NOT_INSTALLED_MESSAGE = ("La verifica della posizione degli immobili non e' ancora disponibile "
                                  "su questo database (migration 103 non applicata).")
CONFIRM_SETTING = "stima360.property_position_confirm"
NOT_INDEPENDENT_MESSAGE = ("L'immobile appartiene a un edificio: la posizione e' quella dell'edificio "
                           "(posizionalo dalla scheda dell'edificio)")
# le SOLE colonne che la mappa legge (contratto leggero, nessun dato riservato)
MAP_COLUMNS = ("id", "code", "title", "property_type", "record_kind", "commercial_status", "address",
               "civic_number", "city", "microzone", "latitude", "longitude", "position_verified_at")
MAP_PAGE_MAX = 200


def _assicura_103(cur) -> None:
    census._assicura_083(cur)
    cur.execute("SELECT count(*) = 2 AS pronto FROM information_schema.columns WHERE table_schema = 'public'"
                "   AND table_name = 'properties'"
                "   AND column_name IN ('position_verified_at', 'position_verified_by_user_id')")
    if not cur.fetchone()["pronto"]:
        raise PositionNotInstalled(POSITION_NOT_INSTALLED_MESSAGE)


def _immobile(cur, agency_id, property_id, *, lock=False, trashed_ok=False) -> dict:
    """L'immobile DI QUESTA agenzia (altrimenti 404, come un id inesistente);
    nel Cestino solo con `trashed_ok` (altrimenti 409 PROPERTY_IN_TRASH)."""
    cur.execute(f"SELECT * FROM properties WHERE id = %s AND agency_id = %s{' FOR UPDATE' if lock else ''}",
                (property_id, agency_id))
    riga = cur.fetchone()
    if riga is None:
        raise NotFoundError(f"property {property_id} not found")
    immobile = dict(riga)
    if not trashed_ok:
        lifecycle.refuse_if_in_trash(immobile)
    return immobile


def _stato(cur, immobile: dict) -> dict:
    verificata = immobile.get("position_verified_at") is not None
    presenti = immobile.get("latitude") is not None and immobile.get("longitude") is not None
    chi = None
    uid = immobile.get("position_verified_by_user_id")
    if verificata and uid is not None:
        cur.execute("SELECT id, first_name, last_name FROM operator_users WHERE id = %s", (uid,))
        riga = cur.fetchone()
        if riga is not None:
            chi = {"id": riga["id"], "name": _nome(riga)}
    return {
        "property_id": immobile["id"],
        "building_id": immobile.get("building_id"),
        "independent": immobile.get("building_id") is None,
        "latitude": immobile.get("latitude"),
        "longitude": immobile.get("longitude"),
        "status": "verified" if verificata else ("unverified" if presenti else "missing"),
        "position_verified_at": immobile.get("position_verified_at"),
        "position_verified_by": chi,
        "in_trash": immobile.get("deleted_at") is not None,
    }


def position_info(ctx, property_id: int) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _assicura_103(cur)
        immobile = _immobile(cur, agency_id, property_id, trashed_ok=True)
        return _stato(cur, immobile)


def confirm_position(ctx, property_id: int, body) -> dict:
    agency_id = ctx.require_agency()
    user_id = getattr(ctx, "user_id", None)
    if user_id is None:
        # il canale Basic storico non ha un operatore: la verifica deve averne uno
        raise PermissionDenied("La verifica della posizione richiede un operatore autenticato")
    lat, lng = validate_coordinates(body.latitude, body.longitude)
    with core_cursor(commit=True) as (_, cur):
        _assicura_103(cur)
        # FOR UPDATE: altra agenzia -> 404, Cestino -> 409 (dopo il lock)
        immobile = _immobile(cur, agency_id, property_id, lock=True)
        if immobile.get("building_id") is not None:
            raise census.CensusConflict(NOT_INDEPENDENT_MESSAGE, "POSITION_NOT_INDEPENDENT",
                                        building_id=immobile["building_id"])
        cur.execute("SELECT set_config(%s, %s, true)", (CONFIRM_SETTING, str(int(property_id))))
        cur.execute("UPDATE properties SET latitude = %s, longitude = %s, position_verified_by_user_id = %s, "
                    "updated_at = NOW() WHERE id = %s AND agency_id = %s RETURNING *",
                    (lat, lng, user_id, property_id, agency_id))
        riga = cur.fetchone()
        # il segnale non deve sopravvivere all'istruzione, qualunque cosa sia successa
        cur.execute("SELECT set_config(%s, '', true)", (CONFIRM_SETTING,))
        if riga is None or riga.get("position_verified_at") is None:
            raise census.CensusConflict("La posizione non e' stata verificata: riprova", "POSITION_NOT_SAVED")
        return _stato(cur, dict(riga))


def list_verified(ctx, *, limit: int = MAP_PAGE_MAX, offset: int = 0) -> dict:
    """Gli immobili INDIPENDENTI dell'agenzia con posizione VERIFICATA, per la
    mappa: fuori Cestino, non archiviati, senza edificio. Solo le colonne di
    MAP_COLUMNS, ordinati per id (paginazione stabile)."""
    agency_id = ctx.require_agency()
    limit = max(1, min(int(limit), MAP_PAGE_MAX))
    offset = max(0, int(offset))
    with core_cursor() as (_, cur):
        _assicura_103(cur)
        dove = ("WHERE p.agency_id = %s AND p.building_id IS NULL AND p.position_verified_at IS NOT NULL"
                " AND p.latitude IS NOT NULL AND p.longitude IS NOT NULL"
                f" AND {_property_trash.live('p')}"
                " AND p.archived_at IS NULL AND p.commercial_status <> 'archived'")
        cur.execute(f"SELECT count(*) AS n FROM properties p {dove}", (agency_id,))
        totale = cur.fetchone()["n"]
        colonne = ", ".join(f"p.{c}" for c in MAP_COLUMNS)
        cur.execute(f"SELECT {colonne} FROM properties p {dove} ORDER BY p.id LIMIT %s OFFSET %s",
                    (agency_id, limit, offset))
        return {"items": [dict(r) for r in cur.fetchall()], "total": totale, "limit": limit, "offset": offset}
