"""STIMA360 TERRITORIO ZONA-2 - IL PERCORSO INTERO in browser (Chromium) con backend
e PostgreSQL VERI (schema completo, 102 compresa; database locale usa-e-getta).
Il disegno della mappa e' simulato (MapLibre finta di T4) e il provider della
ricerca e' finto (nessuna chiamata a Nominatim dalle prove); tutto il resto e'
vero: Shell, router, procedura guidata, API, trigger della 102, sessione del
titolare dell'agenzia 1.

  Zona -> «Cerca indirizzo» -> risultato con il civico -> punto provvisorio ->
  «Conferma punto» (zoom >= 17) -> «Nuovo edificio» = procedura guidata
  ESISTENTE con Comune/via/civico gia' compilati -> «Salva edificio» -> la
  posizione si collega da sola (POST /position, SOLO coordinate; data e
  operatore dal server) -> «Vedi sulla mappa» = Zona con il marker dell'edificio
  appena creato, evidenziato, e il punto consumato.

E, nello stesso file:
  * «Annulla» (prima e dopo la conferma del punto) non scrive nulla;
  * collegamento fallito dopo la creazione: UN solo edificio nel database, stato
    «NON collegata» leggibile, «Riprova» con lo stesso punto -> verificata;
  * «Collega un edificio esistente»: conferma T4 sull'edificio scelto;
  * nessuno sforamento a 390 e 1280 px.
"""
from __future__ import annotations

import json

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _edificio, _q  # noqa: F401
from tests.test_censimento_3_backend_postgres import completo, mondo  # noqa: F401  (fixture)
from tests.test_edifici_1_browser_postgres import server  # noqa: F401  (fixture)
from tests.test_territorio_4_browser_postgres import FINTA, MAPPA
from tests.test_territorio_5bis_postgres import _Http

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

NOMINATIM = [
    {"lat": "42.8330000", "lon": "13.9250000", "display_name": "25, Via Roma, Alba Adriatica, Teramo, Abruzzo, 64011, Italia",
     "address": {"house_number": "25", "road": "Via Roma", "town": "Alba Adriatica", "postcode": "64011"}},
    {"lat": "42.8340000", "lon": "13.9260000", "display_name": "Via Roma, Alba Adriatica, Teramo, Abruzzo, Italia",
     "address": {"road": "Via Roma", "town": "Alba Adriatica"}},
]


def _riga(m, bid):
    return dict(zip(("lat", "lng", "at", "by"), _q(m, "SELECT latitude, longitude, position_verified_at, "
                                                      "position_verified_by_user_id FROM buildings WHERE id = %s", (bid,))[0]))


def _edifici(m):
    return _q(m, "SELECT count(*) FROM buildings WHERE agency_id = 1 AND deleted_at IS NULL")[0][0]


def _apri_zona(page, url):
    page.goto(f"{url}/os/#/edifici/mappa")
    page.wait_for_selector("canvas.finta")
    page.wait_for_function("document.querySelector('[data-map-status]').hidden")
    page.wait_for_selector("[data-to-place] summary")
    page.evaluate("document.querySelector('[data-insert-panel]').open = true")


def _punto_dalla_ricerca(page, testo="Via Roma 25, Alba Adriatica"):
    page.fill("[data-geocode-input]", testo)
    page.click("[data-geocode-submit]")
    page.wait_for_selector("[data-geocode-pick='house']")
    page.click("[data-geocode-pick='house']")
    page.wait_for_selector("[data-insert-point]:not([hidden])")


def _sfora(page):
    return page.evaluate("() => ({ scroll: document.documentElement.scrollWidth, inner: innerWidth })")


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_zona2_ricerca_punto_nuovo_edificio_posizione_collegata_e_marker(server, larghezza, monkeypatch):
    from playwright.sync_api import sync_playwright
    from property import geocode
    m = server
    geocode.clear_cache()
    monkeypatch.setattr(geocode, "requests", _Http(body=NOMINATIM))
    monkeypatch.setattr(geocode, "MIN_INTERVAL", 0.05)
    url = m["url"]
    prima = _edifici(m)
    scritture = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": larghezza, "height": 820})
        errori = []
        page.on("pageerror", lambda e: errori.append(str(e)))
        page.on("request", lambda r: scritture.append((r.method, r.url, r.post_data)) if r.method in ("POST", "PATCH", "PUT", "DELETE") else None)
        page.add_init_script(FINTA)

        # 1. Zona: ricerca -> punto provvisorio (zoom 18 sul civico) -> conferma
        _apri_zona(page, url)
        _punto_dalla_ricerca(page)
        assert page.evaluate(f"{MAPPA}.getZoom()") == 18
        assert page.evaluate("window.__ml.markers.length") == 1
        assert page.text_content("[data-insert-point-text]") == "Punto provvisorio: 42.83300, 13.92500"
        assert page.is_enabled("[data-insert-confirm]")
        # 1b. «Annulla» prima della conferma: niente segnaposto, niente scritture
        page.click("[data-insert-point] [data-insert-cancel]")
        page.wait_for_selector("[data-insert-point][hidden]", state="attached")
        assert page.evaluate("window.__ml.markers.filter((k) => k.el.isConnected).length") == 0
        assert scritture == []
        _punto_dalla_ricerca(page)
        page.click("[data-insert-confirm]")
        page.wait_for_selector("[data-insert-actions]:not([hidden])")
        assert page.text_content("[data-insert-chosen]") == "Punto scelto: 42.83300, 13.92500"
        azioni = page.eval_on_selector_all("[data-insert-action]", "els => els.map((e) => e.dataset.insertAction)")
        misura_zona = _sfora(page)
        tocco = page.evaluate("Math.min(...[...document.querySelectorAll('[data-insert-action], [data-insert-change], [data-geocode-submit]')].map((b) => b.getBoundingClientRect().height))")

        # 2. «Nuovo edificio»: la procedura guidata ESISTENTE, gia' compilata
        page.click("[data-insert-action='building']")
        page.wait_for_selector("[data-insert-sheet][open] [data-wizard-step='1']")
        compilato = page.evaluate("""() => ({ city: document.querySelector('#wz-city').value, via: document.querySelector('#wz-address').value,
                                             civico: document.querySelector('#wz-civic').value })""")
        page.fill("#wz-declared", "4")
        page.fill("#wz-name", "Palazzina Zona")
        page.click("[data-wizard-step='1'] [data-next]")
        page.wait_for_selector("[data-wizard-step='2']")
        page.click("[data-path='building']")
        page.wait_for_selector("[data-wizard-step='3'] [data-create-building]")
        page.click("[data-create-building]")
        page.wait_for_selector("[data-wizard-step='4'] [data-save-building]")
        assert scritture == []                                                      # nulla scritto fino a «Salva edificio»
        page.click("[data-save-building]")
        page.wait_for_selector("[data-ready-building][data-ready-state='ok']")
        pronto = page.text_content("[data-ready-building]")
        bid = int(page.get_attribute("[data-ready-building]", "data-ready-building"))
        riga = _riga(m, bid)

        # 3. «Vedi sulla mappa»: Zona con il marker dell'edificio nuovo, evidenziato
        page.click("[data-ready-map]")
        page.wait_for_function(f"location.hash === '#/edifici/mappa/edificio/{bid}'")
        page.wait_for_function(f"{MAPPA} && {MAPPA}.sources['stima-buildings']")
        page.wait_for_selector(f"[data-building-panel='{bid}']")
        punti = page.evaluate(f"{MAPPA}.sources['stima-buildings'].data.features.map((f) => f.id)")
        attivo = page.evaluate(f"{MAPPA}.states[{bid}]")
        consumato = page.evaluate("""() => ({ azioni: document.querySelector('[data-insert-actions]').hidden,
                                              punto: document.querySelector('[data-insert-point]').hidden,
                                              segnaposti: window.__ml.markers.filter((k) => k.el.isConnected).length })""")
        misura_dopo = _sfora(page)
        browser.close()

    assert errori == [], errori
    # SENTINELLA AGGIORNATA DA STIMA360 ZONA-3: la quarta azione collega un immobile indipendente esistente.
    assert azioni == ["building", "unit", "link", "link-property"]
    assert compilato == {"city": "Alba Adriatica", "via": "Via Roma", "civico": "25"}
    assert "Posizione verificata sulla mappa" in pronto
    assert _edifici(m) == prima + 1                                                  # UN edificio
    assert str(riga["lat"]) == "42.8330000" and str(riga["lng"]) == "13.9250000"
    assert riga["at"] is not None and riga["by"] == m["ids"]["owner_a"]                # data e operatore dal server
    assert [s[:2] for s in scritture] == [("POST", f"{url}/api/property/buildings"), ("POST", f"{url}/api/property/buildings/{bid}/position")]
    assert json.loads(scritture[1][2]) == {"latitude": "42.8330000", "longitude": "13.9250000"}
    assert punti == [bid] and attivo == {"selected": True}
    assert consumato == {"azioni": True, "punto": True, "segnaposti": 0}
    for misura in (misura_zona, misura_dopo):
        assert misura["scroll"] <= misura["inner"], misura
    assert tocco >= 44


def test_zona2_collegamento_fallito_nessun_doppione_poi_riprova_e_collega_esistente(server, monkeypatch):
    from playwright.sync_api import sync_playwright
    from property import geocode
    m = server
    geocode.clear_cache()
    monkeypatch.setattr(geocode, "requests", _Http(body=NOMINATIM))
    monkeypatch.setattr(geocode, "MIN_INTERVAL", 0.05)
    url = m["url"]
    esistente = _edificio(m, city="Alba Adriatica", address="Via Antica", civic_number="9", name="Casa Antica")
    prima = _edifici(m)
    posizioni = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 820})
        errori = []
        page.on("pageerror", lambda e: errori.append(str(e)))
        page.add_init_script(FINTA)

        # il PRIMO collegamento fallisce (server), il secondo passa
        def rotta(route):
            posizioni.append(route.request.post_data)
            if len(posizioni) == 1:
                route.fulfill(status=503, content_type="application/json", body=json.dumps({"detail": "x", "code": "POSITION_NOT_INSTALLED"}))
            else:
                route.continue_()
        page.route("**/api/property/buildings/*/position", rotta)

        _apri_zona(page, url)
        _punto_dalla_ricerca(page)
        page.click("[data-insert-confirm]")
        page.wait_for_selector("[data-insert-actions]:not([hidden])")
        page.click("[data-insert-action='building']")
        page.wait_for_selector("[data-insert-sheet][open] [data-wizard-step='1']")
        page.fill("#wz-declared", "3")
        page.click("[data-wizard-step='1'] [data-next]")
        page.wait_for_selector("[data-wizard-step='2']")
        page.click("[data-path='building']")
        page.wait_for_selector("[data-wizard-step='3'] [data-create-building]")
        page.click("[data-create-building]")
        page.wait_for_selector("[data-wizard-step='4'] [data-save-building]")
        page.click("[data-save-building]")
        page.wait_for_selector("[data-ready-building][data-ready-state='failed']")
        fallito = page.text_content("[data-ready-building]")
        bid = int(page.get_attribute("[data-ready-building]", "data-ready-building"))
        dopo_fallimento = (_edifici(m), _riga(m, bid))
        assert page.get_attribute("[data-ready-place]", "href") == f"#/edifici/mappa/posiziona/{bid}"
        # «Riprova»: stesso punto, nessun secondo edificio
        page.click("[data-ready-retry]")
        page.wait_for_selector("[data-ready-building][data-ready-state='ok']")
        dopo_riprova = (_edifici(m), _riga(m, bid))
        page.click("[data-ready-map]")
        page.wait_for_function(f"location.hash === '#/edifici/mappa/edificio/{bid}'")
        page.wait_for_selector(f"[data-building-panel='{bid}']")

        # «Collega un edificio esistente» su un nuovo punto (tocco sulla mappa)
        page.evaluate("document.querySelector('[data-insert-panel]').open = true")
        page.click("[data-insert-pick]")
        page.evaluate(f"{MAPPA}.setZoom(18)")
        page.evaluate(f"{MAPPA}.fire('click', {{ lngLat: {{ lng: 13.9301234, lat: 42.8301234 }} }})")
        page.wait_for_selector("[data-insert-point]:not([hidden])")
        page.click("[data-insert-confirm]")
        page.wait_for_selector("[data-insert-actions]:not([hidden])")
        page.click("[data-insert-action='link']")
        page.fill("[data-insert-link-input]", "Antica")
        page.click("[data-insert-link-submit]")
        page.wait_for_selector(f"[data-insert-link-building='{esistente['id']}']")
        page.click(f"[data-insert-link-building='{esistente['id']}']")
        page.wait_for_function(f"location.hash === '#/edifici/mappa/edificio/{esistente['id']}'")
        page.wait_for_function(f"{MAPPA} && {MAPPA}.sources['stima-buildings']")
        punti = page.evaluate(f"{MAPPA}.sources['stima-buildings'].data.features.map((f) => f.id).sort()")
        browser.close()

    assert errori == [], errori
    assert "NON collegata" in fallito and "non viene duplicato" in fallito
    assert dopo_fallimento == (prima + 1, {"lat": None, "lng": None, "at": None, "by": None})   # edificio creato, non posizionato
    assert dopo_riprova[0] == prima + 1                                                           # nessun doppione
    assert str(dopo_riprova[1]["lat"]) == "42.8330000" and dopo_riprova[1]["at"] is not None and dopo_riprova[1]["by"] == m["ids"]["owner_a"]
    assert [json.loads(c) for c in posizioni[:2]] == [{"latitude": "42.8330000", "longitude": "13.9250000"}] * 2
    assert json.loads(posizioni[2]) == {"latitude": "42.8301234", "longitude": "13.9301234"}
    antica = _riga(m, esistente["id"])
    assert str(antica["lat"]) == "42.8301234" and str(antica["lng"]) == "13.9301234" and antica["at"] is not None
    assert sorted(punti) == sorted([bid, esistente["id"]])
    assert _edifici(m) == prima + 1
