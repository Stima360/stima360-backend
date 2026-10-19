"""STIMA360 TERRITORIO T4 - posizione VERIFICATA degli edifici, su PostgreSQL VERO
(schema completo dal runner, migration 102 compresa; database locale usa-e-getta,
mai quello del TEST).

  01  percorso: edificio senza coordinate -> conferma -> verificata (data del
      server, operatore autenticato) -> la lista Edifici (mappa) la restituisce;
  02  coordinate preesistenti: MAI verificate da sole (creazione con coordinate);
  03  invalidazione: PATCH di coordinate diverse -> decade; stesso valore numerico
      o cambio d'indirizzo -> resta (D2); nuova conferma -> di nuovo verificata;
  04  falsificazione: PATCH con i campi della verifica -> 422; SQL diretto senza
      segnale -> rifiuto; segnale per un altro edificio -> rifiuto; il segnale
      vale una volta sola; INSERT verificato -> rifiuto;
  05  isolamento e Cestino: altra agenzia -> 404 e nulla cambia; nel Cestino -> 409;
  06  validazione: fuori intervallo, (0,0), non numerico -> errore, nulla cambia;
      operatore assente (canale Basic) -> 403; Super Admin senza agenzia -> 403;
  07  D1: anche l'agente dell'agenzia conferma;
  08  nessun dato del censimento o delle unita' cambia;
  09  operatore cancellato: SET NULL ammesso, la data resta;
  10  down protetto: con una verifica si ferma; senza, toglie e la 102 si riapplica.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import (  # noqa: F401
    DSN, d15_attiva, _q, completo, mondo,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

ROOT = Path(__file__).resolve().parents[1]
SU = (ROOT / "migrations" / "102_territorio_4_position_verification.sql").read_text(encoding="utf-8")
GIU = (ROOT / "migrations" / "102_territorio_4_position_verification_down.sql").read_text(encoding="utf-8")


def _edificio(m, chi="owner_a", **kw):
    corpo = {"city": "Alba Adriatica", "address": f"Via Mappa {uuid.uuid4().hex[:6]}", "civic_number": "1",
             "name": "Palazzina T4", **kw}
    r = m["api"](chi).post("/api/property/buildings", json=corpo)
    assert r.status_code == 201, r.text
    return r.json()


def _riga(m, bid):
    return dict(zip(("lat", "lng", "at", "by"), _q(m, "SELECT latitude, longitude, position_verified_at, "
                                                      "position_verified_by_user_id FROM buildings WHERE id = %s",
                                                      (bid,))[0]))


def _conferma(m, bid, lat="42.8341000", lng="13.9262000", chi="owner_a"):
    return m["api"](chi).post(f"/api/property/buildings/{bid}/position", json={"latitude": lat, "longitude": lng})


def _sql_err(m, sql, params=None):
    psycopg2 = m["psycopg2"]
    with pytest.raises(psycopg2.Error) as e:
        _q(m, sql, params)
    return str(e.value)


def test_01_percorso_conferma_verificata_e_mappa(mondo):
    m = mondo
    b = _edificio(m)
    stato = m["api"]().get(f"/api/property/buildings/{b['id']}/position").json()
    assert stato["status"] == "missing" and stato["position_verified_at"] is None
    prima = datetime.now(timezone.utc)
    r = _conferma(m, b["id"])
    dopo = datetime.now(timezone.utc)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["status"] == "verified"
    assert corpo["position_verified_by"] == {"id": m["ids"]["owner_a"], "name": "Olga"}
    quando = datetime.fromisoformat(corpo["position_verified_at"])
    assert prima <= quando <= dopo                                          # ora del server
    riga = _riga(m, b["id"])
    assert str(riga["lat"]) == "42.8341000" and str(riga["lng"]) == "13.9262000"
    assert riga["by"] == m["ids"]["owner_a"] and riga["at"] is not None
    # la lista che usa la mappa (T3) la restituisce: e' gia' un marker verificato
    lista = m["api"]().get("/api/property/buildings?limit=200&offset=0").json()["items"]
    voce = next(x for x in lista if x["id"] == b["id"])
    assert voce["position_verified_at"] and voce["latitude"] is not None
    scheda = m["api"]().get(f"/api/property/buildings/{b['id']}").json()
    assert scheda["position_verified_by_user_id"] == m["ids"]["owner_a"]


def test_02_coordinate_preesistenti_mai_verificate(mondo):
    m = mondo
    b = _edificio(m, latitude="42.8000000", longitude="13.9000000")
    riga = _riga(m, b["id"])
    assert riga["lat"] is not None and riga["at"] is None and riga["by"] is None
    stato = m["api"]().get(f"/api/property/buildings/{b['id']}/position").json()
    assert stato["status"] == "unverified"
    err = _sql_err(m, "INSERT INTO buildings (agency_id, city, latitude, longitude, position_verified_at, "
                      "position_verified_by_user_id) VALUES (1, 'X', 42.8, 13.9, NOW(), %s)", (m["ids"]["owner_a"],))
    assert "POSITION_VERIFICATION_RESERVED" in err


def test_03_invalidazione_solo_se_le_coordinate_cambiano_davvero(mondo):
    m = mondo
    b = _edificio(m)
    assert _conferma(m, b["id"]).status_code == 200
    api = m["api"]()
    # stesso valore numerico (scala diversa): resta verificata
    assert api.patch(f"/api/property/buildings/{b['id']}", json={"latitude": "42.8341"}).status_code == 200
    assert _riga(m, b["id"])["at"] is not None
    # indirizzo cambiato: resta verificata (D2)
    assert api.patch(f"/api/property/buildings/{b['id']}", json={"civic_number": "12"}).status_code == 200
    assert _riga(m, b["id"])["at"] is not None
    # coordinate cambiate fuori dalla conferma: decade
    assert api.patch(f"/api/property/buildings/{b['id']}", json={"longitude": "13.9300000"}).status_code == 200
    riga = _riga(m, b["id"])
    assert riga["at"] is None and riga["by"] is None and str(riga["lng"]) == "13.9300000"
    assert api.get(f"/api/property/buildings/{b['id']}/position").json()["status"] == "unverified"
    # anche via SQL diretto (script, codice futuro)
    assert _conferma(m, b["id"]).status_code == 200
    _q(m, "UPDATE buildings SET latitude = 42.9 WHERE id = %s", (b["id"],))
    assert _riga(m, b["id"])["at"] is None
    # una nuova conferma esplicita la ripristina
    assert _conferma(m, b["id"], lat="42.9", lng="13.93").json()["status"] == "verified"


def test_04_nessuna_verifica_falsificata(mondo):
    m = mondo
    b = _edificio(m, latitude="42.8", longitude="13.9")
    api = m["api"]()
    for campo, valore in (("position_verified_at", "2026-10-13T10:00:00Z"),
                          ("position_verified_by_user_id", m["ids"]["owner_a"])):
        r = api.patch(f"/api/property/buildings/{b['id']}", json={campo: valore})
        assert r.status_code == 422, (campo, r.text)
        r = api.post(f"/api/property/buildings/{b['id']}/position",
                     json={"latitude": "42.8", "longitude": "13.9", campo: valore})
        assert r.status_code == 422, (campo, r.text)                       # l'operatore non viene dal client
    assert "POSITION_VERIFICATION_RESERVED" in _sql_err(
        m, "UPDATE buildings SET position_verified_at = NOW(), position_verified_by_user_id = %s WHERE id = %s",
        (m["ids"]["owner_a"], b["id"]))
    altro = _edificio(m)
    conn = m["conn"]
    psycopg2 = m["psycopg2"]
    conn.autocommit = False
    try:
        with conn.cursor() as cur:                                         # segnale per un ALTRO edificio
            cur.execute("SELECT set_config('stima360.building_position_confirm', %s, true)", (str(altro["id"]),))
            with pytest.raises(psycopg2.Error, match="POSITION_VERIFICATION_RESERVED"):
                cur.execute("UPDATE buildings SET position_verified_by_user_id = %s WHERE id = %s",
                            (m["ids"]["owner_a"], b["id"]))
        conn.rollback()
        with conn.cursor() as cur:                                         # il segnale vale UNA volta
            cur.execute("SELECT set_config('stima360.building_position_confirm', %s, true)", (str(b["id"]),))
            cur.execute("UPDATE buildings SET position_verified_by_user_id = %s WHERE id = %s",
                        (m["ids"]["owner_a"], b["id"]))
            cur.execute("SELECT current_setting('stima360.building_position_confirm', true)")
            assert cur.fetchone()[0] == ""
            with pytest.raises(psycopg2.Error, match="POSITION_VERIFICATION_RESERVED"):
                cur.execute("UPDATE buildings SET position_verified_by_user_id = %s WHERE id = %s",
                            (m["ids"]["agent_a"], b["id"]))
        conn.rollback()
    finally:
        conn.rollback()
        conn.autocommit = True
    assert _riga(m, b["id"])["at"] is None


def test_05_altra_agenzia_e_cestino(mondo):
    m = mondo
    b = _edificio(m, latitude="42.8", longitude="13.9")
    prima = _riga(m, b["id"])
    r = _conferma(m, b["id"], chi="owner_b")
    assert r.status_code == 404, r.text
    assert m["api"]("owner_b").get(f"/api/property/buildings/{b['id']}/position").status_code == 404
    assert _riga(m, b["id"]) == prima
    _q(m, "UPDATE buildings SET deleted_at = NOW(), deleted_reason = 'test_record' WHERE id = %s", (b["id"],))
    try:
        r = _conferma(m, b["id"])
        assert r.status_code == 409 and r.json()["code"] == "BUILDING_IN_TRASH", r.text
        assert _riga(m, b["id"]) == prima
        assert m["api"]().get(f"/api/property/buildings/{b['id']}/position").json()["in_trash"] is True
    finally:
        _q(m, "UPDATE buildings SET deleted_at = NULL, deleted_reason = NULL, deleted_by_user_id = NULL "
              "WHERE id = %s", (b["id"],))


def test_06_validazione_e_contesto(mondo):
    m = mondo
    from operator_auth.context import OperatorContext
    from operator_auth.exceptions import PlatformAdminAgencyRequired
    from core.exceptions import PermissionDenied
    from property import building_position
    from property.schemas import PositionConfirm
    b = _edificio(m)
    for lat, lng in (("91", "13.9"), ("42.8", "-181"), ("0", "0")):
        r = _conferma(m, b["id"], lat=lat, lng=lng)
        assert r.status_code == 400 and r.json()["code"] == "POSITION_INVALID", (lat, lng, r.text)
    for lat in ("abc", True, None):
        assert _conferma(m, b["id"], lat=lat).status_code == 422, lat
    assert _riga(m, b["id"]) == {"lat": None, "lng": None, "at": None, "by": None}
    basic = OperatorContext(user_id=None, agency_id=1, role="agency_owner", is_platform_admin=False,
                            session_id=None, auth_channel="legacy_basic")
    with pytest.raises(PermissionDenied):
        building_position.confirm_position(basic, b["id"], PositionConfirm(latitude="42.8", longitude="13.9"))
    piattaforma = OperatorContext(user_id=m["ids"]["owner_a"], agency_id=None, role=None, is_platform_admin=True,
                                  session_id=None, auth_channel="operator_session")
    with pytest.raises(PlatformAdminAgencyRequired):
        building_position.confirm_position(piattaforma, b["id"], PositionConfirm(latitude="42.8", longitude="13.9"))
    assert _riga(m, b["id"])["at"] is None


def test_07_anche_l_agente_conferma(mondo):
    m = mondo
    b = _edificio(m)
    r = _conferma(m, b["id"], chi="agent_a")
    assert r.status_code == 200 and r.json()["position_verified_by"]["id"] == m["ids"]["agent_a"]


def test_08_censimento_e_unita_invariati(mondo):
    m = mondo
    b = _edificio(m)
    r = m["api"]().post("/api/property/census/units", json={"building_id": b["id"], "property_type": "apartment",
                                                            "floor": "1", "internal_number": "1"})
    assert r.status_code == 201, r.text
    istantanea = "SELECT row_to_json(p)::text FROM properties p WHERE building_id = %s ORDER BY id"
    unita = _q(m, istantanea, (b["id"],))
    edificio = _q(m, "SELECT to_jsonb(b) - 'latitude' - 'longitude' - 'position_verified_at' "
                     "- 'position_verified_by_user_id' - 'updated_at' FROM buildings b WHERE id = %s", (b["id"],))
    assert _conferma(m, b["id"]).status_code == 200
    assert _q(m, istantanea, (b["id"],)) == unita
    assert _q(m, "SELECT to_jsonb(b) - 'latitude' - 'longitude' - 'position_verified_at' "
                 "- 'position_verified_by_user_id' - 'updated_at' FROM buildings b WHERE id = %s", (b["id"],)) == edificio


def test_09_operatore_cancellato_set_null_ammesso(mondo):
    m = mondo
    from operator_auth.context import OperatorContext
    from property import building_position
    from property.schemas import PositionConfirm
    uid = _q(m, "INSERT INTO operator_users (email, email_normalized, password_hash, first_name) "
                "VALUES ('t4.tmp@x.test', 't4.tmp@x.test', 'pbkdf2_sha256$1$x$y', 'Temp') RETURNING id")[0][0]
    b = _edificio(m)
    ctx = OperatorContext(user_id=uid, agency_id=1, role="agent", is_platform_admin=False,
                          session_id=None, auth_channel="operator_session")
    building_position.confirm_position(ctx, b["id"], PositionConfirm(latitude="42.8", longitude="13.9"))
    if d15_attiva(m):
        # ADM D15 nel database di prova: l'operatore non si cancella piu'
        # fisicamente, quindi il SET NULL non puo' scattare: riferimento e data restano.
        prima = _riga(m, b["id"])
        from psycopg2 import errors
        with pytest.raises(errors.RestrictViolation):
            _q(m, "DELETE FROM operator_users WHERE id = %s", (uid,))
        assert _riga(m, b["id"]) == prima and prima["by"] == uid and prima["at"] is not None
        return
    _q(m, "DELETE FROM operator_users WHERE id = %s", (uid,))
    riga = _riga(m, b["id"])
    assert riga["by"] is None and riga["at"] is not None


def test_10_down_protetto_e_riapplicabile(mondo):
    m = mondo
    b = _edificio(m)
    assert _conferma(m, b["id"]).status_code == 200
    conn, psycopg2 = m["conn"], m["psycopg2"]
    conn.autocommit = False
    try:
        corpo = GIU.replace("BEGIN;", "").replace("COMMIT;", "")
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.Error, match="102 down: esistono posizioni verificate"):
                cur.execute(corpo)
        conn.rollback()
        with conn.cursor() as cur:                                         # a vuoto: toglie tutto, poi si riapplica
            cur.execute("ALTER TABLE buildings DISABLE TRIGGER trg_buildings_position_verification")
            cur.execute("UPDATE buildings SET position_verified_at = NULL, position_verified_by_user_id = NULL")
            cur.execute("ALTER TABLE buildings ENABLE TRIGGER trg_buildings_position_verification")
            cur.execute(corpo)
            cur.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = 'buildings' "
                        "AND column_name LIKE 'position_verified%%'")
            assert cur.fetchone()[0] == 0
            cur.execute(SU)
            cur.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = 'buildings' "
                        "AND column_name LIKE 'position_verified%%'")
            assert cur.fetchone()[0] == 2
        conn.rollback()
    finally:
        conn.rollback()
        conn.autocommit = True
    assert _riga(m, b["id"])["at"] is not None                             # nulla e' cambiato davvero
