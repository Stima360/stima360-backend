"""STIMA360 ZONA-3 / C2 - l'API della posizione verificata degli IMMOBILI
INDIPENDENTI, su PostgreSQL VERO (schema completo dal runner, 103 compresa;
database locale usa-e-getta, mai quello del TEST).

  01  percorso: immobile indipendente senza coordinate -> conferma -> verificata
      (data del server, operatore autenticato) -> la lista leggera della mappa
      lo restituisce con le SOLE colonne essenziali;
  02  coordinate preesistenti (creazione con coordinate, PATCH): MAI verificate
      da sole; non compaiono nella lista della mappa;
  03  invalidazione: PATCH di coordinate diverse -> decade e sparisce dalla
      mappa; stesso valore o cambio d'indirizzo -> resta; nuova conferma;
  04  falsificazione: PATCH/POST con i campi della verifica -> 422; operatore
      non dal client; SQL diretto -> rifiuto;
  05  isolamento e Cestino: altra agenzia -> 404 (anche la lettura dello
      stato) e nulla cambia; nel Cestino -> 409 e fuori dalla mappa; archiviato
      fuori dalla mappa;
  06  unita' di palazzina: conferma -> 409 POSITION_NOT_INDEPENDENT, nulla
      cambia, mai nella lista (la posizione e' dell'edificio, T4 invariato);
  07  validazione e contesto: fuori intervallo, (0,0), non numerico -> 400;
      canale senza operatore -> 403; Super Admin senza agenzia -> 403;
  08  la lista della mappa: solo la propria agenzia, paginata, ordine stabile.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from tests.test_censimento_3_backend_postgres import (  # noqa: F401
    DSN, _edificio, _q, _unita, completo, mondo,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

COLONNE_MAPPA = {"id", "code", "title", "property_type", "record_kind", "commercial_status", "address", "civic_number",
                 "city", "microzone", "latitude", "longitude", "position_verified_at"}


def _immobile(m, chi="owner_a", **kw):
    corpo = {"property_type": "apartment", "city": "Alba Adriatica", "address": f"Via Zona {uuid.uuid4().hex[:6]}",
             "civic_number": "3", "title": "Villa Zona", **kw}
    r = m["api"](chi).post("/api/property/properties", json=corpo)
    assert r.status_code == 201, r.text
    return r.json()


def _riga(m, pid):
    return dict(zip(("lat", "lng", "at", "by"), _q(m, "SELECT latitude, longitude, position_verified_at, "
                                                      "position_verified_by_user_id FROM properties WHERE id = %s", (pid,))[0]))


def _conferma(m, pid, lat="42.8341000", lng="13.9262000", chi="owner_a"):
    return m["api"](chi).post(f"/api/property/properties/{pid}/position", json={"latitude": lat, "longitude": lng})


def _mappa(m, chi="owner_a", **q):
    r = m["api"](chi).get("/api/property/properties/positions", params=q)
    assert r.status_code == 200, r.text
    return r.json()


def _ids_mappa(m, chi="owner_a"):
    return [i["id"] for i in _mappa(m, chi)["items"]]


def test_01_percorso_conferma_verificata_e_lista_della_mappa(mondo):
    m = mondo
    p = _immobile(m)
    stato = m["api"]().get(f"/api/property/properties/{p['id']}/position").json()
    assert stato["status"] == "missing" and stato["independent"] is True and stato["position_verified_at"] is None
    assert p["id"] not in _ids_mappa(m)
    prima = datetime.now(timezone.utc)
    r = _conferma(m, p["id"])
    dopo = datetime.now(timezone.utc)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["property_id"] == p["id"] and corpo["status"] == "verified" and corpo["independent"] is True
    assert corpo["position_verified_by"] == {"id": m["ids"]["owner_a"], "name": "Olga"}
    assert prima <= datetime.fromisoformat(corpo["position_verified_at"]) <= dopo
    riga = _riga(m, p["id"])
    assert str(riga["lat"]) == "42.8341000" and str(riga["lng"]) == "13.9262000" and riga["by"] == m["ids"]["owner_a"]
    # la lista leggera: solo colonne essenziali, nessun dato riservato
    lista = _mappa(m)
    voce = next(i for i in lista["items"] if i["id"] == p["id"])
    assert set(voce) == COLONNE_MAPPA and voce["position_verified_at"] is not None
    assert float(voce["latitude"]) == 42.8341 and float(voce["longitude"]) == 13.9262 and voce["city"] == "Alba Adriatica"
    assert lista["total"] >= 1 and lista["limit"] == 200 and lista["offset"] == 0
    # lo stato lo dice anche la scheda (GET /properties/{id}: p.*)
    scheda = m["api"]().get(f"/api/property/properties/{p['id']}").json()
    assert scheda["position_verified_at"] is not None and scheda["building_id"] is None


def test_02_coordinate_preesistenti_mai_verificate_ne_sulla_mappa(mondo):
    m = mondo
    p = _immobile(m, latitude="42.8300000", longitude="13.9300000")
    assert _riga(m, p["id"])["at"] is None
    assert m["api"]().get(f"/api/property/properties/{p['id']}/position").json()["status"] == "unverified"
    assert p["id"] not in _ids_mappa(m)
    assert m["api"]().patch(f"/api/property/properties/{p['id']}", json={"latitude": "42.8310000"}).status_code == 200
    assert _riga(m, p["id"])["at"] is None and p["id"] not in _ids_mappa(m)


def test_03_invalidazione_solo_se_le_coordinate_cambiano_davvero(mondo):
    m = mondo
    p = _immobile(m)
    assert _conferma(m, p["id"]).status_code == 200
    api = m["api"]()
    assert api.patch(f"/api/property/properties/{p['id']}", json={"latitude": "42.8341", "longitude": "13.9262"}).status_code == 200
    assert _riga(m, p["id"])["at"] is not None and p["id"] in _ids_mappa(m)
    assert api.patch(f"/api/property/properties/{p['id']}", json={"address": "Via Nuova", "asking_price": "250000"}).status_code == 200
    assert _riga(m, p["id"])["at"] is not None
    assert api.patch(f"/api/property/properties/{p['id']}", json={"longitude": "13.9300000"}).status_code == 200
    riga = _riga(m, p["id"])
    assert str(riga["lng"]) == "13.9300000" and riga["at"] is None and riga["by"] is None
    assert p["id"] not in _ids_mappa(m)                                      # decaduta: sparisce dalla mappa
    assert _conferma(m, p["id"], lng="13.9300000").status_code == 200
    assert p["id"] in _ids_mappa(m)


def test_04_nessuna_verifica_falsificata(mondo):
    m = mondo
    p = _immobile(m, latitude="42.8", longitude="13.9")
    api = m["api"]()
    for campo, valore in (("position_verified_at", "2026-10-14T07:00:00Z"), ("position_verified_by_user_id", m["ids"]["owner_a"])):
        assert api.patch(f"/api/property/properties/{p['id']}", json={campo: valore}).status_code == 422, campo
        assert api.post(f"/api/property/properties/{p['id']}/position",
                        json={"latitude": "42.8", "longitude": "13.9", campo: valore}).status_code == 422, campo
    psycopg2 = m["psycopg2"]
    with pytest.raises(psycopg2.Error, match="POSITION_VERIFICATION_RESERVED"):
        _q(m, "UPDATE properties SET position_verified_at = NOW(), position_verified_by_user_id = %s WHERE id = %s",
           (m["ids"]["owner_a"], p["id"]))
    assert _riga(m, p["id"])["at"] is None


def test_05_altra_agenzia_cestino_e_archiviato(mondo):
    m = mondo
    p = _immobile(m)
    assert _conferma(m, p["id"]).status_code == 200
    # altra agenzia: 404 su stato e conferma, nulla cambia, non nella sua mappa
    assert m["api"]("owner_b").get(f"/api/property/properties/{p['id']}/position").status_code == 404
    r = _conferma(m, p["id"], lat="40.0000000", lng="10.0000000", chi="owner_b")
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"
    assert str(_riga(m, p["id"])["lat"]) == "42.8341000"
    assert p["id"] not in _ids_mappa(m, "owner_b") and p["id"] in _ids_mappa(m)
    # Cestino: 409 e fuori dalla mappa (le coordinate e la verifica restano, per il ripristino)
    r = m["api"]().post(f"/api/property/properties/{p['id']}/trash", json={"reason_code": "test_record"})
    assert r.status_code == 200, r.text
    r = _conferma(m, p["id"], lat="40.0000000", lng="10.0000000")
    assert r.status_code == 409 and r.json()["code"] == "PROPERTY_IN_TRASH", r.text
    assert str(_riga(m, p["id"])["lat"]) == "42.8341000" and _riga(m, p["id"])["at"] is not None
    assert p["id"] not in _ids_mappa(m)
    stato = m["api"]().get(f"/api/property/properties/{p['id']}/position").json()
    assert stato["in_trash"] is True and stato["status"] == "verified"
    # archiviato: fuori dalla mappa
    q = _immobile(m)
    assert _conferma(m, q["id"]).status_code == 200 and q["id"] in _ids_mappa(m)
    r = m["api"]().post(f"/api/property/properties/{q['id']}/archive")
    assert r.status_code == 200, r.text
    assert q["id"] not in _ids_mappa(m)


def test_06_unita_di_palazzina_mai_posizionata_da_sola(mondo):
    m = mondo
    b = _edificio(m, city="Alba Adriatica", address="Via Zona Palazzina", civic_number="1", name="Palazzina C2")
    u = _unita(m, building_id=b["id"], floor="1", internal_number="1")
    stato = m["api"]().get(f"/api/property/properties/{u['id']}/position").json()
    assert stato["independent"] is False and stato["building_id"] == b["id"]
    r = _conferma(m, u["id"])
    assert r.status_code == 409 and r.json()["code"] == "POSITION_NOT_INDEPENDENT" and r.json()["building_id"] == b["id"], r.text
    assert _riga(m, u["id"]) == {"lat": None, "lng": None, "at": None, "by": None}
    # l'edificio si posiziona come prima (T4 invariato) e l'unita' non entra nella lista degli immobili
    r = m["api"]().post(f"/api/property/buildings/{b['id']}/position", json={"latitude": "42.8341000", "longitude": "13.9262000"})
    assert r.status_code == 200 and r.json()["status"] == "verified"
    assert u["id"] not in _ids_mappa(m)


def test_07_validazione_e_contesto(mondo):
    m = mondo
    p = _immobile(m)
    for lat, lng in (("95", "13.9"), ("42.8", "181"), ("0", "0"), ("abc", "13.9")):
        r = _conferma(m, p["id"], lat=lat, lng=lng)
        assert r.status_code in (400, 422), (lat, lng, r.text)
        if r.status_code == 400:
            assert r.json()["code"] == "POSITION_INVALID"
    assert _riga(m, p["id"]) == {"lat": None, "lng": None, "at": None, "by": None}
    from operator_auth.context import OperatorContext
    from property import property_position
    from property.schemas import PositionConfirm
    from core.exceptions import PermissionDenied
    from operator_auth.exceptions import PlatformAdminAgencyRequired
    senza_operatore = OperatorContext(user_id=None, agency_id=1, role="agency_owner", is_platform_admin=False,
                                      session_id=None, auth_channel="basic")
    with pytest.raises(PermissionDenied):
        property_position.confirm_position(senza_operatore, p["id"], PositionConfirm(latitude="42.8", longitude="13.9"))
    piattaforma = OperatorContext(user_id=m["ids"]["owner_a"], agency_id=None, role="platform_admin", is_platform_admin=True,
                                  session_id=None, auth_channel="operator_session")
    with pytest.raises((PermissionDenied, PlatformAdminAgencyRequired)):
        property_position.confirm_position(piattaforma, p["id"], PositionConfirm(latitude="42.8", longitude="13.9"))
    assert _riga(m, p["id"])["at"] is None


def test_08_la_lista_della_mappa_e_paginata_e_per_agenzia(mondo):
    m = mondo
    miei = [_immobile(m)["id"] for _ in range(3)]
    for pid in miei:
        assert _conferma(m, pid).status_code == 200
    altrui = _immobile(m, chi="owner_b")
    assert _conferma(m, altrui["id"], chi="owner_b").status_code == 200
    tutti = _ids_mappa(m)
    assert all(pid in tutti for pid in miei) and altrui["id"] not in tutti
    assert tutti == sorted(tutti)                                             # ordine stabile per id
    pagina = _mappa(m, limit=2, offset=0)
    assert len(pagina["items"]) == 2 and pagina["limit"] == 2 and pagina["total"] == len(tutti)
    seconda = _mappa(m, limit=2, offset=2)
    assert [i["id"] for i in pagina["items"]] + [i["id"] for i in seconda["items"]] == tutti[:4]
    assert _ids_mappa(m, "owner_b") == [altrui["id"]]
    assert m["api"]().get("/api/property/properties/positions", params={"limit": 500}).status_code == 422
