"""STIMA360 ZONA-3 / C1 - verifica della posizione degli IMMOBILI INDIPENDENTI:
il CONTRATTO della migration 103, su PostgreSQL VERO (schema completo dal
runner, 103 compresa; database locale usa-e-getta, mai quello del TEST).
Nessun codice applicativo usa ancora la 103 (il modulo di conferma arriva in
C2): qui la conferma e' il segnale di transazione eseguito a mano, come fara'
C2, e tutto il resto passa dalle rotte ESISTENTI del CRM.

  01  coordinate preesistenti (immobili della fixture e immobile creato con
      coordinate): MAI verificate da sole;
  02  il meccanismo di conferma autorizzato (segnale + operatore, stessa
      transazione) registra la verifica con la data del SERVER;
  03  una modifica esterna delle coordinate (PATCH generica) la invalida; lo
      stesso valore numerico, un cambio d'indirizzo o di prezzo no; collegare
      l'immobile a un edificio la fa decadere; nuova conferma -> di nuovo
      verificata;
  04  scritture dirette non autorizzate rifiutate: PATCH con i campi della
      verifica -> 422; SQL senza segnale -> rifiuto; segnale per un altro
      immobile -> rifiuto; il segnale vale una volta sola; INSERT verificato ->
      rifiuto; conferma su un'unita' di palazzina -> rifiuto; coordinate non
      valide -> rifiuto; senza operatore -> rifiuto;
  05  i normali salvataggi continuano a funzionare: scheda commerciale, unita'
      di censimento in palazzina e autonoma, PATCH di campi qualunque, Cestino;
      `buildings` e la 102 invariate;
  06  operatore cancellato: SET NULL ammesso, la data resta;
  07  down protetto: con una verifica si ferma; senza, toglie e la 103 si riapplica.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import (  # noqa: F401
    DSN, _edificio, _q, _unita, completo, mondo,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

ROOT = Path(__file__).resolve().parents[1]
SU = (ROOT / "migrations" / "103_zona_3_property_position_verification.sql").read_text(encoding="utf-8")
GIU = (ROOT / "migrations" / "103_zona_3_property_position_verification_down.sql").read_text(encoding="utf-8")
SEGNALE = "stima360.property_position_confirm"


def _immobile(m, chi="owner_a", **kw):
    """Una scheda commerciale INDIPENDENTE (nessun edificio) dalla rotta esistente."""
    corpo = {"property_type": "villa" if "villa" in _tipi(m) else "apartment", "city": "Alba Adriatica",
             "address": f"Via Zona {uuid.uuid4().hex[:6]}", "civic_number": "3", **kw}
    r = m["api"](chi).post("/api/property/properties", json=corpo)
    assert r.status_code == 201, r.text
    return r.json()


_TIPI = {}


def _tipi(m):
    if "v" not in _TIPI:
        r = m["api"]().get("/api/property/form-options")
        _TIPI["v"] = {t["value"] for t in r.json().get("property_types", [])} if r.status_code == 200 else set()
    return _TIPI["v"]


def _riga(m, pid):
    return dict(zip(("lat", "lng", "at", "by", "building"),
                    _q(m, "SELECT latitude, longitude, position_verified_at, position_verified_by_user_id, building_id "
                          "FROM properties WHERE id = %s", (pid,))[0]))


def _conferma_sql(m, pid, lat="42.8341000", lng="13.9262000", chi="owner_a", *, segnale=None, operatore=True):
    """La conferma come la fara' C2: UNA transazione, il segnale per QUESTO immobile,
    coordinate e operatore nella stessa UPDATE. Qui a mano, senza codice applicativo."""
    conn = m["conn"]
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config(%s, %s, true)", (SEGNALE, str(segnale if segnale is not None else pid)))
            if operatore:
                cur.execute("UPDATE properties SET latitude = %s, longitude = %s, position_verified_by_user_id = %s WHERE id = %s",
                            (lat, lng, m["ids"][chi], pid))
            else:
                cur.execute("UPDATE properties SET latitude = %s, longitude = %s WHERE id = %s", (lat, lng, pid))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.autocommit = True


def _errore_conferma(m, **kw):
    psycopg2 = m["psycopg2"]
    with pytest.raises(psycopg2.Error) as e:
        _conferma_sql(m, **kw)
    return str(e.value)


def _sql_err(m, sql, params=None):
    psycopg2 = m["psycopg2"]
    with pytest.raises(psycopg2.Error) as e:
        _q(m, sql, params)
    return str(e.value)


def test_01_coordinate_preesistenti_mai_verificate(mondo):
    m = mondo
    # gli immobili della fixture (storici, con o senza coordinate): tutti NON verificati
    assert _q(m, "SELECT count(*) FROM properties WHERE position_verified_at IS NOT NULL OR position_verified_by_user_id IS NOT NULL")[0][0] == 0
    # un immobile creato OGGI con coordinate dalla rotta esistente: coordinate salvate, verifica assente
    p = _immobile(m, latitude="42.8300000", longitude="13.9300000")
    riga = _riga(m, p["id"])
    assert str(riga["lat"]) == "42.8300000" and str(riga["lng"]) == "13.9300000"
    assert riga["at"] is None and riga["by"] is None
    # e una PATCH di coordinate su un immobile mai verificato le salva e basta
    r = m["api"]().patch(f"/api/property/properties/{p['id']}", json={"latitude": "42.8310000"})
    assert r.status_code == 200, r.text
    riga = _riga(m, p["id"])
    assert str(riga["lat"]) == "42.8310000" and riga["at"] is None and riga["by"] is None


def test_02_la_conferma_autorizzata_registra_la_verifica_con_l_ora_del_server(mondo):
    m = mondo
    p = _immobile(m)
    assert _riga(m, p["id"])["lat"] is None
    prima = datetime.now(timezone.utc)
    _conferma_sql(m, p["id"])
    dopo = datetime.now(timezone.utc)
    riga = _riga(m, p["id"])
    assert str(riga["lat"]) == "42.8341000" and str(riga["lng"]) == "13.9262000"
    assert riga["by"] == m["ids"]["owner_a"] and riga["building"] is None
    assert prima <= riga["at"] <= dopo                                      # ora del SERVER, non del client
    # il segnale e' consumato: la transazione successiva non verifica piu' nulla
    assert _q(m, "SELECT coalesce(current_setting(%s, true), '')", (SEGNALE,))[0][0] == ""
    # anche l'agente dell'agenzia puo' confermare (come T4, D1)
    q = _immobile(m)
    _conferma_sql(m, q["id"], chi="agent_a")
    assert _riga(m, q["id"])["by"] == m["ids"]["agent_a"]


def test_03_invalidazione_solo_se_le_coordinate_cambiano_davvero_o_arriva_un_edificio(mondo):
    m = mondo
    p = _immobile(m)
    _conferma_sql(m, p["id"])
    api = m["api"]()
    # stesso valore numerico: resta verificata
    assert api.patch(f"/api/property/properties/{p['id']}", json={"latitude": "42.8341", "longitude": "13.9262"}).status_code == 200
    assert _riga(m, p["id"])["at"] is not None
    # cambio d'indirizzo, prezzo, titolo: resta verificata
    assert api.patch(f"/api/property/properties/{p['id']}", json={"address": "Via Nuova", "civic_number": "9",
                                                                  "asking_price": "250000", "title": "Villa al mare"}).status_code == 200
    assert _riga(m, p["id"])["at"] is not None
    # coordinate DIVERSE da una PATCH generica: decade (coordinate salvate, verifica no)
    assert api.patch(f"/api/property/properties/{p['id']}", json={"longitude": "13.9300000"}).status_code == 200
    riga = _riga(m, p["id"])
    assert str(riga["lng"]) == "13.9300000" and riga["at"] is None and riga["by"] is None
    # nuova conferma: di nuovo verificata
    _conferma_sql(m, p["id"], lat="42.8341000", lng="13.9300000")
    assert _riga(m, p["id"])["at"] is not None
    # collegata a un edificio: la posizione e' quella dell'edificio, la verifica propria decade
    b = _edificio(m, city="Alba Adriatica", address="Via Zona Palazzina", civic_number="1", name="Palazzina Zona-3")
    _q(m, "UPDATE properties SET building_id = %s WHERE id = %s", (b["id"], p["id"]))
    riga = _riga(m, p["id"])
    assert riga["building"] == b["id"] and riga["at"] is None and riga["by"] is None
    assert str(riga["lat"]) == "42.8341000"                                 # le coordinate restano


def test_04_nessuna_verifica_falsificata(mondo):
    m = mondo
    p = _immobile(m, latitude="42.8", longitude="13.9")
    api = m["api"]()
    # dalle rotte esistenti i campi della verifica non esistono nemmeno
    for campo, valore in (("position_verified_at", "2026-10-14T07:00:00Z"), ("position_verified_by_user_id", m["ids"]["owner_a"])):
        assert api.patch(f"/api/property/properties/{p['id']}", json={campo: valore}).status_code == 422, campo
        assert api.post("/api/property/properties", json={"property_type": "apartment", campo: valore}).status_code == 422, campo
    assert _riga(m, p["id"])["at"] is None
    # SQL diretto senza segnale
    assert "POSITION_VERIFICATION_RESERVED" in _sql_err(
        m, "UPDATE properties SET position_verified_at = NOW(), position_verified_by_user_id = %s WHERE id = %s",
        (m["ids"]["owner_a"], p["id"]))
    assert "POSITION_VERIFICATION_RESERVED" in _sql_err(
        m, "UPDATE properties SET position_verified_by_user_id = %s WHERE id = %s", (m["ids"]["owner_a"], p["id"]))
    # INSERT gia' verificato, anche con il segnale acceso
    psycopg2 = m["psycopg2"]
    conn = m["conn"]
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config(%s, %s, true)", (SEGNALE, "0"))
            with pytest.raises(psycopg2.Error, match="POSITION_VERIFICATION_RESERVED"):
                cur.execute("INSERT INTO properties (title, property_type, agency_id, latitude, longitude, position_verified_at, "
                            "position_verified_by_user_id) VALUES ('x', 'apartment', 1, 42.8, 13.9, now(), %s)", (m["ids"]["owner_a"],))
        conn.rollback()
    finally:
        conn.rollback()
        conn.autocommit = True
    # segnale per un ALTRO immobile
    altro = _immobile(m)
    assert "POSITION_VERIFICATION_RESERVED" in _errore_conferma(m, pid=p["id"], segnale=altro["id"])
    # il segnale vale UNA volta: la seconda UPDATE nella stessa transazione e' rifiutata
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config(%s, %s, true)", (SEGNALE, str(p["id"])))
            cur.execute("UPDATE properties SET position_verified_by_user_id = %s WHERE id = %s", (m["ids"]["owner_a"], p["id"]))
            cur.execute("SELECT current_setting(%s, true)", (SEGNALE,))
            assert cur.fetchone()[0] == ""
            with pytest.raises(psycopg2.Error, match="POSITION_VERIFICATION_RESERVED"):
                cur.execute("UPDATE properties SET position_verified_by_user_id = %s WHERE id = %s", (m["ids"]["agent_a"], p["id"]))
        conn.rollback()
    finally:
        conn.rollback()
        conn.autocommit = True
    assert _riga(m, p["id"])["at"] is None
    # coordinate non valide, (0,0), mancanti; operatore assente
    assert "POSITION_INVALID" in _errore_conferma(m, pid=p["id"], lat="95", lng="13.9")
    assert "POSITION_INVALID" in _errore_conferma(m, pid=p["id"], lat="0", lng="0")
    assert "POSITION_INVALID" in _errore_conferma(m, pid=p["id"], lat=None, lng=None)
    assert "POSITION_VERIFICATION_RESERVED" in _errore_conferma(m, pid=p["id"], operatore=False)
    # un'unita' di palazzina non ha mai una verifica propria
    b = _edificio(m, city="Alba Adriatica", address="Via Zona Unita", civic_number="2", name="Palazzina Unita")
    u = _unita(m, building_id=b["id"], floor="1", internal_number="1")
    assert "POSITION_NOT_INDEPENDENT" in _errore_conferma(m, pid=u["id"])
    assert _riga(m, u["id"])["at"] is None and _riga(m, p["id"])["at"] is None


def test_05_i_normali_salvataggi_continuano_a_funzionare(mondo):
    m = mondo
    api = m["api"]()
    # scheda commerciale: creazione, modifica di campi qualunque, Cestino
    p = _immobile(m)
    assert api.patch(f"/api/property/properties/{p['id']}", json={"title": "Casa", "rooms": 4, "latitude": "42.81", "longitude": "13.92"}).status_code == 200
    assert _riga(m, p["id"])["at"] is None
    r = api.post(f"/api/property/properties/{p['id']}/archive")
    assert r.status_code in (200, 404, 405), r.text                          # la rotta del Cestino, se esiste, non e' toccata
    # unita' di censimento in palazzina e autonoma
    b = _edificio(m, city="Alba Adriatica", address="Via Zona Salvataggi", civic_number="5", name="Palazzina Salvataggi")
    u = _unita(m, building_id=b["id"], floor="2", internal_number="3", surface_sqm=70)
    a = _unita(m, city="Alba Adriatica", address="Via Autonoma", civic_number="1", property_type="apartment")
    for pid in (u["id"], a["id"]):
        riga = _riga(m, pid)
        assert riga["at"] is None and riga["by"] is None
    # la 102 e `buildings` sono identiche: una conferma di edificio funziona come prima
    r = api.post(f"/api/property/buildings/{b['id']}/position", json={"latitude": "42.8341000", "longitude": "13.9262000"})
    assert r.status_code == 200 and r.json()["status"] == "verified", r.text
    assert _q(m, "SELECT position_verified_at FROM properties WHERE id = %s", (u["id"],))[0][0] is None   # l'unita' resta senza verifica propria
    assert _q(m, "SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_buildings_position_verification'")[0][0] == 1


def test_06_operatore_cancellato_set_null_ammesso(mondo):
    m = mondo
    p = _immobile(m)
    _conferma_sql(m, p["id"], chi="agent_a")
    riga = _riga(m, p["id"])
    assert riga["by"] == m["ids"]["agent_a"]
    conn = m["conn"]
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM operator_users WHERE id = %s", (m["ids"]["agent_a"],))
            cur.execute("SELECT position_verified_at, position_verified_by_user_id FROM properties WHERE id = %s", (p["id"],))
            at, by = cur.fetchone()
            assert at == riga["at"] and by is None                            # SET NULL ammesso, la data resta
        conn.rollback()                                                       # l'operatore della fixture resta
    finally:
        conn.rollback()
        conn.autocommit = True


def test_07_down_protetto_e_riapplicabile(mondo):
    m = mondo
    p = _immobile(m)
    _conferma_sql(m, p["id"])
    conn, psycopg2 = m["conn"], m["psycopg2"]
    conn.autocommit = False
    try:
        corpo = GIU.replace("BEGIN;", "").replace("COMMIT;", "")
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.Error, match="103 down: esistono posizioni verificate"):
                cur.execute(corpo)
        conn.rollback()
        with conn.cursor() as cur:                                         # a vuoto: toglie tutto, poi si riapplica
            cur.execute("ALTER TABLE properties DISABLE TRIGGER trg_properties_position_verification")
            cur.execute("UPDATE properties SET position_verified_at = NULL, position_verified_by_user_id = NULL")
            cur.execute("ALTER TABLE properties ENABLE TRIGGER trg_properties_position_verification")
            cur.execute(corpo)
            cur.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = 'properties' "
                        "AND column_name LIKE 'position_verified%%'")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = 'buildings' "
                        "AND column_name LIKE 'position_verified%%'")
            assert cur.fetchone()[0] == 2                                  # la 102 non viene toccata
            cur.execute(SU)
            cur.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = 'properties' "
                        "AND column_name LIKE 'position_verified%%'")
            assert cur.fetchone()[0] == 2
        conn.rollback()
    finally:
        conn.rollback()
        conn.autocommit = True
    assert _riga(m, p["id"])["at"] is not None                             # nulla e' cambiato davvero
