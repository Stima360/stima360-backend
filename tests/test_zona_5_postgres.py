"""STIMA360 ZONA-5 - la ricerca INVERSA (dal punto all'indirizzo). PostgreSQL VERO
(database locale usa-e-getta), provider FINTO (nessuna chiamata a Nominatim).

  A  limite globale CONDIVISO: ricerche dirette e inverse CONTEMPORANEE da
     connessioni diverse (come processi/istanze) -> le chiamate al provider
     distano >= 1 s l'una dall'altra, qualunque sia il tipo; lock_timeout ->
     503 GEOCODE_BUSY senza chiamare il provider;
  B  contratto: al provider vanno SOLO le due coordinate (7 decimali) e i
     parametri della policy (jsonv2, zoom 18, addressdetails, lingua), lo
     stesso User-Agent della ricerca diretta, mai dati del CRM; la forma del
     risultato: via, civico (vuoto se assente: mai inventato), Comune, CAP,
     provincia, indirizzo leggibile; risposta senza indirizzo -> found False;
  C  cache per punto (stesso punto = nessuna seconda chiamata; punto diverso si');
  D  validazione e contesto: fuori intervallo, (0,0), non numerico -> 400
     POSITION_INVALID senza chiamare il provider; Super Admin
     senza agenzia -> 403; spento da configurazione -> 503 GEOCODE_DISABLED;
     provider giu' -> 503 GEOCODE_UNAVAILABLE e niente in cache;
  E  nessuna scrittura: la ricerca inversa non tocca `buildings` ne' `properties`.
"""
from __future__ import annotations

import threading
import time

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _q  # noqa: F401
from tests.test_censimento_3_backend_postgres import completo, mondo  # noqa: F401  (fixture)
from tests.test_territorio_5bis_postgres import NOMINATIM, _Http, _ctx

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

CON_CIVICO = {"lat": "42.8341000", "lon": "13.9262000", "display_name": "25, Via Roma, Alba Adriatica, Teramo, Abruzzo, 64011, Italia",
              "address": {"house_number": "25", "road": "Via Roma", "town": "Alba Adriatica", "county": "Teramo", "state": "Abruzzo",
                          "postcode": "64011", "country": "Italia"}}
SENZA_CIVICO = {"lat": "42.8350000", "lon": "13.9270000", "display_name": "Via Roma, Alba Adriatica, Teramo, Abruzzo, Italia",
                "address": {"road": "Via Roma", "town": "Alba Adriatica", "county": "Teramo", "postcode": "64011"}}
NULLA = {"error": "Unable to geocode"}


class _HttpMisto(_Http):
    """Risponde alla ricerca diretta con la lista e a quella inversa con un oggetto."""

    def get(self, url, params=None, headers=None, timeout=None, **kw):
        r = super().get(url, params=params, headers=headers, timeout=timeout, **kw)
        if url.endswith("/reverse"):
            r.json = lambda: CON_CIVICO
        return r


@pytest.fixture(autouse=True)
def _cache_pulita():
    from property import geocode
    geocode.clear_cache()
    yield
    geocode.clear_cache()


def test_a01_ricerca_diretta_e_inversa_si_contendono_lo_stesso_turno(mondo):
    from property import geocode
    m = mondo
    http = _HttpMisto()
    esiti, errori = [], []

    def diretta(i):
        try:
            esiti.append(("search", geocode.search(_ctx(m), f"Via Roma {i}, Giulianova", http=http)))
        except Exception as exc:  # noqa: BLE001
            errori.append(exc)

    def inversa(i):
        try:
            esiti.append(("reverse", geocode.reverse(_ctx(m), f"42.83{i}0000", "13.9262000", http=http)))
        except Exception as exc:  # noqa: BLE001
            errori.append(exc)

    fili = [threading.Thread(target=diretta, args=(1,)), threading.Thread(target=inversa, args=(4,)),
            threading.Thread(target=diretta, args=(2,)), threading.Thread(target=inversa, args=(5,))]
    inizio = time.monotonic()
    for f in fili:
        f.start()
    for f in fili:
        f.join(timeout=30)
    durata = time.monotonic() - inizio
    assert errori == [] and len(esiti) == 4 and sorted(k for k, _ in esiti) == ["reverse", "reverse", "search", "search"]
    momenti = sorted(c["t"] for c in http.calls)
    assert len(momenti) == 4 and sorted(c["url"].rsplit("/", 1)[1] for c in http.calls) == ["reverse", "reverse", "search", "search"]
    distanze = [b - a for a, b in zip(momenti, momenti[1:])]
    assert all(d >= geocode.MIN_INTERVAL - 0.02 for d in distanze), distanze      # mai due nello stesso secondo, di qualunque tipo
    assert durata >= 3 * geocode.MIN_INTERVAL - 0.05


def test_a02_lock_timeout_risponde_busy_anche_all_inversa(mondo, monkeypatch):
    from property import geocode
    m = mondo
    http = _HttpMisto()
    conn = m["conn"]
    conn.autocommit = False
    with conn.cursor() as cur:                                           # un altro "processo" tiene il turno
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (geocode.LOCK_KEY,))
    monkeypatch.setattr(geocode, "LOCK_TIMEOUT_MS", 300)
    try:
        with pytest.raises(geocode.GeocodeBusy):
            geocode.reverse(_ctx(m), "42.8341000", "13.9262000", http=http)
        assert http.calls == []                                           # il provider non e' stato chiamato
    finally:
        conn.rollback()
        conn.autocommit = True
    assert geocode.reverse(_ctx(m), "42.8341000", "13.9262000", http=http)["found"] is True


def test_b01_contratto_con_il_provider_e_forma_del_risultato(mondo):
    from property import geocode
    m = mondo
    http = _Http(body=CON_CIVICO)
    esito = geocode.reverse(_ctx(m), "42.83412345678", "13.92621", http=http, sleep=lambda s: None)
    c = http.calls[0]
    assert c["url"] == geocode.DEFAULT_URL.replace("/search", "/reverse") == "https://nominatim.openstreetmap.org/reverse"
    assert c["params"] == {"lat": "42.8341235", "lon": "13.9262100", "format": "jsonv2", "zoom": 18, "addressdetails": 1, "accept-language": "it"}
    assert c["headers"]["User-Agent"].startswith(geocode.APP_NAME) and "info@stima360.it" in c["headers"]["User-Agent"]
    assert "Cookie" not in c["headers"] and c["extra"] == {}
    assert {k: esito[k] for k in ("found", "road", "house_number", "town", "postcode", "province", "precision", "cached")} == {
        "found": True, "road": "Via Roma", "house_number": "25", "town": "Alba Adriatica", "postcode": "64011", "province": "Teramo",
        "precision": "house", "cached": False}
    assert esito["label"].startswith("25, Via Roma") and esito["latitude"] == "42.8341235" and esito["attribution"] == "© OpenStreetMap contributors"
    # senza civico: vuoto, mai inventato
    senza = geocode.reverse(_ctx(m), "42.8350000", "13.9270000", http=_Http(body=SENZA_CIVICO), sleep=lambda s: None)
    assert senza["found"] is True and senza["house_number"] is None and senza["road"] == "Via Roma" and senza["precision"] == "street"
    # il provider non ha un indirizzo: found False, campi vuoti, nessun errore
    nulla = geocode.reverse(_ctx(m), "42.9000000", "14.0000000", http=_Http(body=NULLA), sleep=lambda s: None)
    assert nulla["found"] is False and nulla["road"] is None and nulla["house_number"] is None and nulla["town"] is None
    # forma pura
    assert geocode.shape_reverse([], 1, 2)["found"] is False and geocode.shape_reverse({"address": {}}, 1, 2)["found"] is False
    assert geocode.reverse_url({"STIMA360_GEOCODE_URL": "https://geocoder.interno.test/search"}) == "https://geocoder.interno.test/reverse"
    assert geocode.reverse_url({"STIMA360_GEOCODE_REVERSE_URL": "https://altro.test/inverso"}) == "https://altro.test/inverso"


def test_c01_cache_per_punto(mondo):
    from property import geocode
    m = mondo
    http = _Http(body=CON_CIVICO)
    r1 = geocode.reverse(_ctx(m), "42.8341000", "13.9262000", http=http, sleep=lambda s: None)
    r2 = geocode.reverse(_ctx(m), "42.8341", "13.9262", http=http, sleep=lambda s: None)           # stesso punto (7 decimali)
    r3 = geocode.reverse(_ctx(m, "agent_a"), "42.8341000", "13.9262000", http=http, sleep=lambda s: None)
    assert len(http.calls) == 1 and r1["cached"] is False and r2["cached"] is True and r3["cached"] is True
    geocode.reverse(_ctx(m), "42.8342000", "13.9262000", http=http, sleep=lambda s: None)          # punto diverso
    assert len(http.calls) == 2
    # la cache della ricerca diretta e' separata
    geocode.search(_ctx(m), "Via Roma 25, Giulianova", http=_Http(body=NOMINATIM), sleep=lambda s: None)
    assert geocode.reverse(_ctx(m), "42.8341000", "13.9262000", http=http, sleep=lambda s: None)["cached"] is True


def test_d01_validazione_configurazione_e_contesto(mondo):
    from property import geocode
    from core.exceptions import ValidationError
    from operator_auth.context import OperatorContext
    from operator_auth.exceptions import PlatformAdminAgencyRequired
    m = mondo
    http = _Http(body=CON_CIVICO)
    for lat, lon in (("95", "13.9"), ("42.8", "181"), ("0", "0"), ("abc", "13.9"), (None, "13.9")):
        with pytest.raises(ValidationError):
            geocode.reverse(_ctx(m), lat, lon, http=http)
    assert http.calls == []
    piattaforma = OperatorContext(user_id=m["ids"]["owner_a"], agency_id=None, role=None, is_platform_admin=True,
                                  session_id=None, auth_channel="operator_session")
    with pytest.raises(PlatformAdminAgencyRequired):
        geocode.reverse(piattaforma, "42.8341000", "13.9262000", http=http)
    with pytest.raises(geocode.GeocodeDisabled):
        geocode.reverse(_ctx(m), "42.8341000", "13.9262000", http=http, env={"STIMA360_GEOCODE_ENABLED": "0"})
    assert http.calls == []
    giu = _Http(status=503)
    with pytest.raises(geocode.GeocodeUnavailable):
        geocode.reverse(_ctx(m), "42.8341000", "13.9262000", http=giu, sleep=lambda s: None)
    assert geocode.reverse(_ctx(m), "42.8341000", "13.9262000", http=http, sleep=lambda s: None)["cached"] is False   # niente in cache
    # la rotta vera: 400 con codice, 422 senza parametri, 401 anonimo
    r = m["api"]().get("/api/property/reverse-geocode", params={"lat": "95", "lon": "13.9"})
    assert r.status_code == 400 and r.json()["code"] == "POSITION_INVALID"
    assert m["api"]().get("/api/property/reverse-geocode").status_code == 422
    # (l'anonimo -> 401 lo garantisce il gate di sessione sulla rotta: tests/test_censimento_3_backend.py::test_c01)


def test_e01_la_ricerca_inversa_non_scrive_nulla(mondo, monkeypatch):
    from property import geocode
    m = mondo
    monkeypatch.setattr(geocode, "requests", _Http(body=CON_CIVICO))
    monkeypatch.setattr(geocode, "MIN_INTERVAL", 0.05)
    prima = (_q(m, "SELECT count(*), coalesce(max(updated_at)::text, '') FROM buildings")[0],
             _q(m, "SELECT count(*), coalesce(max(updated_at)::text, '') FROM properties")[0])
    r = m["api"]().get("/api/property/reverse-geocode", params={"lat": "42.8341000", "lon": "13.9262000"})
    assert r.status_code == 200 and r.json()["found"] is True and r.json()["house_number"] == "25"
    assert "latitude" in r.json() and set(r.json()) >= {"road", "house_number", "town", "postcode", "province", "label", "attribution"}
    dopo = (_q(m, "SELECT count(*), coalesce(max(updated_at)::text, '') FROM buildings")[0],
            _q(m, "SELECT count(*), coalesce(max(updated_at)::text, '') FROM properties")[0])
    assert dopo == prima
