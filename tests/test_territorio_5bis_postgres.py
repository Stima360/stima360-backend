"""STIMA360 TERRITORIO T5-bis - ricerca indirizzo. PostgreSQL VERO (database locale
usa-e-getta), provider FINTO (nessuna chiamata a Nominatim dalle prove).

  A  limite globale 1 req/s: 4 richieste CONTEMPORANEE da 4 connessioni diverse
     (come 4 processi/istanze) -> le chiamate al provider distano >= 1 s l'una
     dall'altra; lock_timeout -> 503 GEOCODE_BUSY senza chiamare il provider;
  B  cache: la stessa ricerca (anche con maiuscole/spazi diversi) non torna al
     provider; una diversa si';
  C  contratto: al provider vanno SOLO il testo e i parametri della policy
     (countrycodes=it, limit 5, User-Agent applicativo), mai cookie o dati CRM;
     risultati con la precisione (civico trovato / solo via / zona), max 5;
  D  errori: provider giu' o lento -> 503 GEOCODE_UNAVAILABLE (lock rilasciato,
     niente in cache); spento da configurazione -> 503 GEOCODE_DISABLED senza
     chiamate; query vuota -> 400; anonimo -> 401; Super Admin senza agenzia -> 403;
  E  nessuna scrittura: la ricerca non tocca `buildings`; «Annulla» e T4 invariati;
  F  percorso in Chromium con backend vero: scrive -> «Cerca» -> seleziona ->
     mappa centrata + punto provvisorio -> correzione -> conferma T4 -> verificata.
     Nessuna richiesta durante la digitazione ne' all'apertura.
"""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _edificio, _q  # noqa: F401
from tests.test_censimento_3_backend_postgres import completo, mondo  # noqa: F401  (fixture)
from tests.test_edifici_1_browser_postgres import server  # noqa: F401  (fixture)
from tests.test_territorio_4_browser_postgres import FINTA, MAPPA

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

NOMINATIM = [
    {"lat": "42.7530000", "lon": "13.9560000", "display_name": "25, Via Roma, Giulianova, Teramo, Abruzzo, 64021, Italia",
     "address": {"house_number": "25", "road": "Via Roma", "town": "Giulianova", "postcode": "64021"}},
    {"lat": "42.7540000", "lon": "13.9570000", "display_name": "Via Roma, Giulianova, Teramo, Abruzzo, Italia",
     "address": {"road": "Via Roma", "town": "Giulianova"}},
    {"lat": "42.7500000", "lon": "13.9500000", "display_name": "Giulianova, Teramo, Abruzzo, Italia",
     "address": {"town": "Giulianova"}},
]


class _Http:
    """Un `requests` finto che registra ogni chiamata (momento, parametri, header)."""

    def __init__(self, body=NOMINATIM, status=200, delay=0.0, boom=None):
        self.calls, self.body, self.status, self.delay, self.boom = [], body, status, delay, boom
        self.lock = threading.Lock()

    def get(self, url, params=None, headers=None, timeout=None, **kw):
        with self.lock:
            self.calls.append({"t": time.monotonic(), "url": url, "params": dict(params or {}),
                               "headers": dict(headers or {}), "timeout": timeout, "extra": kw})
        if self.boom:
            raise self.boom
        if self.delay:
            time.sleep(self.delay)
        return SimpleNamespace(status_code=self.status, json=lambda: self.body)


def _ctx(m, chi="owner_a"):
    from operator_auth.context import OperatorContext
    uid, agenzia, ruolo = m["ruoli"][chi]
    return OperatorContext(user_id=uid, agency_id=agenzia, role=ruolo, is_platform_admin=False,
                           session_id=None, auth_channel="operator_session")


@pytest.fixture(autouse=True)
def _cache_pulita():
    from property import geocode
    geocode.clear_cache()
    yield
    geocode.clear_cache()


def test_a01_limite_globale_con_richieste_contemporanee(mondo):
    from property import geocode
    m = mondo
    http = _Http()
    esiti, errori = [], []

    def una(i):
        try:
            esiti.append(geocode.search(_ctx(m), f"Via Roma {i}, Giulianova", http=http))
        except Exception as exc:  # noqa: BLE001
            errori.append(exc)

    fili = [threading.Thread(target=una, args=(i,)) for i in range(4)]
    inizio = time.monotonic()
    for f in fili:
        f.start()
    for f in fili:
        f.join(timeout=30)
    durata = time.monotonic() - inizio
    assert errori == [] and len(esiti) == 4
    momenti = sorted(c["t"] for c in http.calls)
    assert len(momenti) == 4
    distanze = [b - a for a, b in zip(momenti, momenti[1:])]
    assert all(d >= geocode.MIN_INTERVAL - 0.02 for d in distanze), distanze      # mai due nello stesso secondo
    assert durata >= 3 * geocode.MIN_INTERVAL - 0.05                             # 4 chiamate = almeno 3 s


def test_a02_lock_timeout_risponde_busy_senza_chiamare_il_provider(mondo, monkeypatch):
    from property import geocode
    m = mondo
    psycopg2 = m["psycopg2"]
    # un'altra «istanza» tiene il lock a lungo
    altra = psycopg2.connect(m["dsn"])
    altra.autocommit = False
    with altra.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (geocode.LOCK_KEY,))
    monkeypatch.setattr(geocode, "LOCK_TIMEOUT_MS", 300)
    http = _Http()
    try:
        with pytest.raises(geocode.GeocodeBusy):
            geocode.search(_ctx(m), "Via Roma 25, Giulianova", http=http)
        assert http.calls == []
    finally:
        altra.rollback()
        altra.close()
    # lock libero: la stessa ricerca ora funziona
    assert geocode.search(_ctx(m), "Via Roma 25, Giulianova", http=http)["results"]


def test_b01_cache_delle_richieste_identiche(mondo):
    from property import geocode
    m = mondo
    http = _Http()
    r1 = geocode.search(_ctx(m), "Via Roma 25, Giulianova", http=http, sleep=lambda s: None)
    r2 = geocode.search(_ctx(m), "  via   ROMA 25,  giulianova ", http=http, sleep=lambda s: None)
    r3 = geocode.search(_ctx(m, "agent_a"), "Via Roma 25, Giulianova", http=http, sleep=lambda s: None)
    assert len(http.calls) == 1 and r1["cached"] is False and r2["cached"] is True and r3["cached"] is True
    assert r2["results"] == r1["results"]
    geocode.search(_ctx(m), "Via Roma 26, Giulianova", http=http, sleep=lambda s: None)
    assert len(http.calls) == 2


def test_c01_contratto_con_il_provider_e_forma_dei_risultati(mondo):
    from property import geocode
    m = mondo
    http = _Http()
    esito = geocode.search(_ctx(m), "Via Roma 25, Giulianova", http=http, sleep=lambda s: None)
    c = http.calls[0]
    assert c["url"] == geocode.DEFAULT_URL
    assert c["params"] == {"q": "Via Roma 25, Giulianova", "format": "jsonv2", "countrycodes": "it", "limit": 5,
                           "addressdetails": 1, "accept-language": "it"}
    assert c["headers"]["User-Agent"].startswith("STIMA360-CRM/1.0 (") and "info@stima360.it" in c["headers"]["User-Agent"]
    assert "Cookie" not in c["headers"] and c["extra"] == {} and 1 <= c["timeout"] <= 30
    assert esito["attribution"] == "© OpenStreetMap contributors"
    r = esito["results"]
    assert [x["precision"] for x in r] == ["house", "street", "area"]
    assert r[0]["house_number_matches"] is True and r[0]["latitude"] == "42.7530000" and r[0]["longitude"] == "13.9560000"
    assert r[1]["house_number_matches"] is False and r[1]["house_number"] is None
    assert set(r[0]) == {"label", "latitude", "longitude", "precision", "house_number", "house_number_matches",
                         "road", "town", "postcode"}
    # piu' di 5 risultati dal provider: ne passano 5; coordinate non valide: scartate
    tanti = [dict(NOMINATIM[0], lat=str(42 + i / 100)) for i in range(8)] + [{"lat": "x", "lon": "1"}, {"lat": "95", "lon": "1"}]
    assert len(geocode.shape_results(tanti, "q")) == 5
    assert geocode.shape_results([{"lat": "x", "lon": "1"}, {"lat": "95", "lon": "1"}], "q") == []
    # civico chiesto diverso da quello trovato
    assert geocode.shape_results([NOMINATIM[0]], "Via Roma 7, Giulianova")[0]["house_number_matches"] is False


def test_d01_errori_configurazione_e_contesto(mondo):
    from property import geocode
    from core.exceptions import ValidationError
    from operator_auth.context import OperatorContext
    from operator_auth.exceptions import PlatformAdminAgencyRequired
    m = mondo
    # provider giu' -> 503, niente in cache, lock rilasciato (la ricerca dopo funziona)
    giu = _Http(status=503)
    with pytest.raises(geocode.GeocodeUnavailable):
        geocode.search(_ctx(m), "Via Nuova 1, Teramo", http=giu, sleep=lambda s: None)
    lento = _Http(boom=TimeoutError("read timed out"))
    with pytest.raises(geocode.GeocodeUnavailable):
        geocode.search(_ctx(m), "Via Nuova 1, Teramo", http=lento, sleep=lambda s: None)
    ok = _Http()
    assert geocode.search(_ctx(m), "Via Nuova 1, Teramo", http=ok, sleep=lambda s: None)["cached"] is False
    assert len(ok.calls) == 1                                                       # gli errori non erano in cache
    # spento da configurazione: nessuna chiamata
    spento = _Http()
    with pytest.raises(geocode.GeocodeDisabled):
        geocode.search(_ctx(m), "Via Nuova 2, Teramo", http=spento, env={"STIMA360_GEOCODE_ENABLED": "0"})
    assert spento.calls == []
    # endpoint sostituibile da configurazione
    altro = _Http()
    geocode.search(_ctx(m), "Via Nuova 3, Teramo", http=altro, env={"STIMA360_GEOCODE_URL": "https://geocoder.interno.test/search"},
                   sleep=lambda s: None)
    assert altro.calls[0]["url"] == "https://geocoder.interno.test/search"
    # query troppo corta
    with pytest.raises(ValidationError):
        geocode.search(_ctx(m), "  a ", http=_Http())
    # Super Admin senza agenzia
    piattaforma = OperatorContext(user_id=m["ids"]["owner_a"], agency_id=None, role=None, is_platform_admin=True,
                                  session_id=None, auth_channel="operator_session")
    with pytest.raises(PlatformAdminAgencyRequired):
        geocode.search(piattaforma, "Via Nuova 4, Teramo", http=_Http())
    # rotta vera: 400 e codici
    r = m["api"]().get("/api/property/geocode?q=a")
    assert r.status_code == 400
    r = m["api"]().get("/api/property/geocode")
    assert r.status_code == 422


def test_e01_la_ricerca_non_scrive_nulla(mondo, monkeypatch):
    from property import geocode
    m = mondo
    b = _edificio(m, city="Alba Adriatica", address="Via Roma", civic_number="25")
    prima = _q(m, "SELECT to_jsonb(b)::text FROM buildings b WHERE id = %s", (b["id"],))
    monkeypatch.setattr(geocode, "requests", _Http())
    r = m["api"]().get("/api/property/geocode?q=Via%20Roma%2025,%20Giulianova")
    assert r.status_code == 200 and len(r.json()["results"]) == 3
    assert _q(m, "SELECT to_jsonb(b)::text FROM buildings b WHERE id = %s", (b["id"],)) == prima
    assert _q(m, "SELECT count(*) FROM buildings WHERE position_verified_at IS NOT NULL")[0][0] == 0


# ---------------------------------------------------------------------------
# F - il percorso in Chromium con backend vero (provider finto nel backend)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("larghezza", [390, 1280])
def test_f01_percorso_cerca_seleziona_correggi_conferma(server, larghezza, monkeypatch):
    from playwright.sync_api import sync_playwright
    from property import geocode
    m = server
    http = _Http()
    monkeypatch.setattr(geocode, "requests", http)
    monkeypatch.setattr(geocode, "MIN_INTERVAL", 0.05)
    a = _edificio(m, city="Alba Adriatica", address="Via Roma", civic_number="25", name="Palazzina Roma")
    url = m["url"]
    richieste = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": larghezza, "height": 820})
        errori = []
        page.on("pageerror", lambda e: errori.append(str(e)))
        page.on("request", lambda r: richieste.append((r.method, r.url)) if "/api/property/geocode" in r.url
                or (r.method in ("POST", "PATCH") and "/position" in r.url) else None)
        page.add_init_script(FINTA)
        page.goto(f"{url}/os/#/edifici/mappa/posiziona/{a['id']}")
        page.wait_for_selector("canvas.finta")
        page.wait_for_function("document.querySelector('[data-map-status]').hidden")
        nota = page.text_content(".territorio-search-note")
        # 1. scrivere NON cerca
        page.fill("[data-geocode-input]", "Via Roma 25, Giulianova")
        page.wait_for_timeout(400)
        dopo_digitazione = len(richieste)
        # 2. «Cerca»
        page.click("[data-geocode-submit]")
        page.wait_for_selector("[data-geocode-pick]")
        risultati = page.eval_on_selector_all("[data-geocode-pick]", "els => els.map((e) => [e.dataset.geocodePick, e.textContent])")
        attrib = page.text_content("[data-geocode-attrib]")
        stato = page.text_content("[data-geocode-status]")
        # 3. selezione del risultato con il civico -> mappa centrata, punto provvisorio
        page.click("[data-geocode-pick='house']")
        page.wait_for_selector(".territorio-pin.is-provisional.maplibregl-marker, [data-map] .territorio-pin.is-provisional")
        centro = page.evaluate(f"({MAPPA}.o.center, {MAPPA}.z)")
        punto = page.evaluate("(() => { const k = window.__ml.markers[0]; return k.p; })()")
        suggerimento = page.text_content("[data-place-hint]")
        # il risultato «solo via» avvisa che il civico non c'e'
        page.click("[data-geocode-pick='street']")
        avviso_via = page.text_content("[data-place-hint]")
        page.click("[data-geocode-pick='house']")
        riga = _q(m, "SELECT latitude, position_verified_at FROM buildings WHERE id = %s", (a["id"],))[0]
        # 4. correzione manuale (trascinamento) e conferma T4
        page.evaluate("(() => { const k = window.__ml.markers[0]; k.setLngLat([13.9561234, 42.7530567]); k.fire('dragend'); })()")
        page.evaluate(f"{MAPPA}.setZoom(18)")
        assert page.is_enabled("[data-place-confirm]")
        page.click("[data-place-confirm]")
        page.wait_for_function(f"location.hash === '#/edifici/mappa/edificio/{a['id']}'")
        browser.close()

    assert errori == [], errori
    assert "OpenStreetMap" in nota and "Conferma posizione" in nota
    assert dopo_digitazione == 0                                                  # nessuna richiesta durante la digitazione
    assert [r for r in richieste if "/geocode" in r[1]] == [("GET", f"{url}/api/property/geocode?q=Via+Roma+25%2C+Giulianova")] or \
        len([r for r in richieste if "/geocode" in r[1]]) == 1                    # UNA ricerca, dal pulsante
    assert len(http.calls) == 1 and http.calls[0]["params"]["q"] == "Via Roma 25, Giulianova"
    assert [r[0] for r in risultati] == ["house", "street", "area"] and "Civico trovato" in risultati[0][1]
    assert "Civico non trovato" in risultati[1][1]
    assert attrib == "Risultati © OpenStreetMap contributors" and "3 risultati" in stato
    assert centro == 18 and punto == [13.956, 42.753]
    assert "suggerito dalla ricerca" in suggerimento
    assert "Civico non trovato" in avviso_via
    assert riga[0] is None and riga[1] is None                                    # niente salvato dalla ricerca
    dopo = _q(m, "SELECT latitude, longitude, position_verified_at, position_verified_by_user_id FROM buildings WHERE id = %s", (a["id"],))[0]
    assert str(dopo[0]) == "42.7530567" and str(dopo[1]) == "13.9561234"          # il punto CORRETTO a mano, non il risultato
    assert dopo[2] is not None and dopo[3] == m["ids"]["owner_a"]
    assert [r for r in richieste if "/position" in r[1]] == [("POST", f"{url}/api/property/buildings/{a['id']}/position")]
