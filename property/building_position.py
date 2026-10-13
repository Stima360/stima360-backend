"""STIMA360 TERRITORIO T4 - la posizione VERIFICATA di un edificio.

Due operazioni, nient'altro:

  * `position_info`  - lo stato della posizione dell'edificio (coordinate,
    verificata o no, quando e da chi): letta dalla scheda e dalla pagina di
    posizionamento;
  * `confirm_position` - l'operatore conferma il punto scelto sulla mappa:
    coordinate e verifica nella STESSA istruzione, nella stessa transazione.

La regola vera e' nel database (migration 102, `buildings_position_verification`):
la verifica si scrive solo dentro la transazione che accende il segnale
`stima360.building_position_confirm` per QUESTO edificio, la data e' quella
del server, e qualunque altro cambio di coordinate la fa decadere. Qui si
accende il segnale solo dopo aver controllato contesto, agenzia ed edificio.

L'operatore e' SEMPRE quello autenticato (`ctx.user_id`): nessun identificativo
arriva dal client. Permessi: gli stessi della «Modifica palazzina»
(`census.update_building`): ogni operatore attivo dell'agenzia; il Super Admin
solo operando in un'agenzia (`ctx.require_agency()`). Un edificio di un'altra
agenzia e' 404, uno nel Cestino 409 BUILDING_IN_TRASH (stesse letture del
censimento, `census._edificio`). Nessun dato del censimento, delle unita' o
delle stime viene letto per scriverlo o modificato.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from core.database import core_cursor
from core.exceptions import PermissionDenied

from . import census

POSITION_NOT_INSTALLED_MESSAGE = ("La verifica della posizione degli edifici non e' ancora disponibile "
                                  "su questo database (migration 102 non applicata).")
CONFIRM_SETTING = "stima360.building_position_confirm"
_SETTE = Decimal("0.0000001")


class PositionNotInstalled(census.CensusNotInstalled):
    """Codice deployato prima della 102: 503 leggibile, come il censimento senza la 083."""
    code = "POSITION_NOT_INSTALLED"


def _assicura_102(cur) -> None:
    census._assicura_083(cur)
    cur.execute("SELECT count(*) = 2 AS pronto FROM information_schema.columns WHERE table_schema = 'public'"
                "   AND table_name = 'buildings'"
                "   AND column_name IN ('position_verified_at', 'position_verified_by_user_id')")
    if not cur.fetchone()["pronto"]:
        raise PositionNotInstalled(POSITION_NOT_INSTALLED_MESSAGE)


def _coordinata(valore, minimo: int, massimo: int, nome: str) -> Decimal:
    """Un decimale finito nell'intervallo, alla precisione della colonna (NUMERIC(10,7))."""
    if isinstance(valore, bool) or valore is None:
        raise census.CensusInvalid(f"{nome} mancante o non valida", "POSITION_INVALID")
    try:
        d = Decimal(str(valore))
    except (InvalidOperation, ValueError):
        raise census.CensusInvalid(f"{nome} non valida", "POSITION_INVALID") from None
    if not d.is_finite() or d < minimo or d > massimo:
        raise census.CensusInvalid(f"{nome} fuori intervallo", "POSITION_INVALID")
    return d.quantize(_SETTE)


def validate_coordinates(latitude, longitude) -> tuple[Decimal, Decimal]:
    lat = _coordinata(latitude, -90, 90, "Latitudine")
    lng = _coordinata(longitude, -180, 180, "Longitudine")
    if lat == 0 and lng == 0:
        raise census.CensusInvalid("Posizione non valida (0, 0)", "POSITION_INVALID")
    return lat, lng


def _nome(riga) -> str | None:
    nome = " ".join(p for p in (riga.get("first_name"), riga.get("last_name")) if p and str(p).strip())
    return nome or None


def _stato(cur, edificio: dict) -> dict:
    verificata = edificio.get("position_verified_at") is not None
    presenti = edificio.get("latitude") is not None and edificio.get("longitude") is not None
    chi = None
    uid = edificio.get("position_verified_by_user_id")
    if verificata and uid is not None:
        cur.execute("SELECT id, first_name, last_name FROM operator_users WHERE id = %s", (uid,))
        riga = cur.fetchone()
        if riga is not None:
            chi = {"id": riga["id"], "name": _nome(riga)}
    return {
        "building_id": edificio["id"],
        "latitude": edificio.get("latitude"),
        "longitude": edificio.get("longitude"),
        "status": "verified" if verificata else ("unverified" if presenti else "missing"),
        "position_verified_at": edificio.get("position_verified_at"),
        "position_verified_by": chi,
        "in_trash": edificio.get("deleted_at") is not None,
    }


def position_info(ctx, building_id: int) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _assicura_102(cur)
        edificio = census._edificio(cur, agency_id, building_id, trashed_ok=True)
        return _stato(cur, edificio)


def confirm_position(ctx, building_id: int, body) -> dict:
    agency_id = ctx.require_agency()
    user_id = getattr(ctx, "user_id", None)
    if user_id is None:
        # il canale Basic storico non ha un operatore: la verifica deve averne uno
        raise PermissionDenied("La verifica della posizione richiede un operatore autenticato")
    lat, lng = validate_coordinates(body.latitude, body.longitude)
    with core_cursor(commit=True) as (_, cur):
        _assicura_102(cur)
        # FOR UPDATE: altra agenzia -> 404, Cestino -> 409 (dopo il lock)
        census._edificio(cur, agency_id, building_id, lock=True)
        cur.execute("SELECT set_config(%s, %s, true)", (CONFIRM_SETTING, str(int(building_id))))
        cur.execute("UPDATE buildings SET latitude = %s, longitude = %s, position_verified_by_user_id = %s, "
                    "updated_at = NOW() WHERE id = %s AND agency_id = %s RETURNING *",
                    (lat, lng, user_id, building_id, agency_id))
        riga = cur.fetchone()
        # il segnale non deve sopravvivere all'istruzione, qualunque cosa sia successa
        cur.execute("SELECT set_config(%s, '', true)", (CONFIRM_SETTING,))
        if riga is None or riga.get("position_verified_at") is None:
            raise census.CensusConflict("La posizione non e' stata verificata: riprova", "POSITION_NOT_SAVED")
        return _stato(cur, dict(riga))
