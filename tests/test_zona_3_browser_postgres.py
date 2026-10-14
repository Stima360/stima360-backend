"""STIMA360 ZONA-3 / C2 - LA CAMPAGNA in browser (Chromium) con backend e PostgreSQL
VERI (schema completo, 103 compresa; database locale usa-e-getta). Il disegno
della mappa e' simulato (MapLibre finta di T4) e il provider della ricerca e'
finto; tutto il resto e' vero: Shell, router, procedura guidata, scheda unita',
API, trigger della 102 e della 103, sessioni delle agenzie 1 e 2.

  Zona -> «Cerca indirizzo» -> punto confermato -> «Nuovo immobile» -> «Unita'
  autonoma» -> la scheda ESISTENTE -> salvataggio -> collegamento automatico
  (POST /properties/{id}/position, SOLE coordinate; data e operatore dal
  server) -> Zona con il marker dell'immobile (livello proprio, distinto dagli
  edifici), evidenziato -> «Apri scheda immobile» -> #/immobili/{id}.

E, nella stessa campagna: «Annulla» senza scritture; separazione delle agenzie
(l'immobile non e' sulla mappa ne' collegabile dall'agenzia B); Cestino escluso
dalla mappa; unita' di palazzina non posizionabile da sola (409) e mai un marker
proprio; nessun marker per coordinate non verificate; collegamento fallito
senza doppione e con «Riprova collegamento»; immobile indipendente esistente
collegabile; T4 (edificio) invariato; 390 e 1280 px senza sforamento.
"""
from __future__ import annotations

import json

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _edificio, _q, _unita  # noqa: F401
from tests.test_censimento_3_backend_postgres import completo, mondo  # noqa: F401  (fixture)
from tests.test_edifici_1_browser_postgres import server  # noqa: F401  (fixture)
from tests.test_territorio_4_browser_postgres import FINTA, MAPPA
from tests.test_territorio_5bis_postgres import _Http
from tests.test_territorio_zona2_browser_postgres import NOMINATIM, _apri_zona, _punto_dalla_ricerca, _sfora

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


def _riga(m, pid):
    return dict(zip(("lat", "lng", "at", "by", "building"),
                    _q(m, "SELECT latitude, longitude, position_verified_at, position_verified_by_user_id, building_id "
                          "FROM properties WHERE id = %s", (pid,))[0]))


def _immobili(m):
    return _q(m, "SELECT count(*) FROM properties WHERE agency_id = 1 AND id > 25")[0][0]


def _immobile(m, chi="owner_a", **kw):
    corpo = {"property_type": "apartment", "city": "Alba Adriatica", "address": "Via Zona Esistente", "civic_number": "8",
             "title": "Villa Esistente", **kw}
    r = m["api"](chi).post("/api/property/properties", json=corpo)
    assert r.status_code == 201, r.text
    return r.json()


def _punti(page):
    return page.evaluate(f"({MAPPA}.sources['stima-properties'] ? {MAPPA}.sources['stima-properties'].data.features.map((f) => f.id) : null)")


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_zona3_campagna_nuovo_immobile_indipendente_marker_e_scheda(server, larghezza, monkeypatch):
    from playwright.sync_api import sync_playwright
    from property import geocode
    m = server
    geocode.clear_cache()
    monkeypatch.setattr(geocode, "requests", _Http(body=NOMINATIM))
    monkeypatch.setattr(geocode, "MIN_INTERVAL", 0.05)
    url = m["url"]
    # il contesto: un immobile con coordinate NON verificate (mai un marker), un'unita' di palazzina
    # con l'edificio verificato (il marker e' dell'edificio), un immobile di agenzia B verificato
    non_verificato = _immobile(m, title="Casa senza verifica", latitude="42.8200000", longitude="13.9100000")
    b = _edificio(m, city="Alba Adriatica", address="Via Palazzina", civic_number="2", name="Palazzina Zona-3")
    u = _unita(m, building_id=b["id"], floor="1", internal_number="1")
    assert m["api"]().post(f"/api/property/buildings/{b['id']}/position", json={"latitude": "42.8400000", "longitude": "13.9200000"}).status_code == 200
    altrui = _immobile(m, chi="owner_b", title="Villa Altrui")
    assert m["api"]("owner_b").post(f"/api/property/properties/{altrui['id']}/position", json={"latitude": "42.8100000", "longitude": "13.9000000"}).status_code == 200
    prima = _immobili(m)
    scritture = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": larghezza, "height": 820})
        errori = []
        page.on("pageerror", lambda e: errori.append(str(e)))
        page.on("request", lambda r: scritture.append((r.method, r.url, r.post_data)) if r.method in ("POST", "PATCH", "PUT", "DELETE") else None)
        page.add_init_script(FINTA)

        # 0. la mappa: l'edificio verificato c'e'; nessun marker per coordinate non verificate, per
        #    l'unita' di palazzina o per l'immobile di un'altra agenzia
        _apri_zona(page, url)
        page.wait_for_function(f"{MAPPA}.sources['stima-properties'] && {MAPPA}.sources['stima-buildings']")
        assert _punti(page) == []
        assert page.evaluate(f"{MAPPA}.sources['stima-buildings'].data.features.map((f) => f.id)") == [b["id"]]
        legenda = page.text_content("[data-map-legend]")

        # 1. ricerca -> punto -> «Annulla» (nessuna scrittura) -> punto -> conferma -> «Nuovo immobile»
        _punto_dalla_ricerca(page)
        page.click("[data-insert-point] [data-insert-cancel]")
        assert scritture == []
        _punto_dalla_ricerca(page)
        page.click("[data-insert-confirm]")
        page.wait_for_selector("[data-insert-actions]:not([hidden])")
        tocco = page.evaluate("Math.min(...[...document.querySelectorAll('[data-insert-action]')].map((b) => b.getBoundingClientRect().height))")
        misura_zona = _sfora(page)
        page.click("[data-insert-action='unit']")
        page.wait_for_selector("[data-insert-sheet][open] [data-wizard-step='1']")
        compilato = page.evaluate("""() => ({ city: document.querySelector('#wz-city').value, via: document.querySelector('#wz-address').value,
                                             civico: document.querySelector('#wz-civic').value })""")
        page.check("#wz-unknown")
        page.click("[data-wizard-step='1'] [data-next]")
        page.wait_for_selector("[data-wizard-step='2']")
        page.click("[data-path='autonomous']")
        page.wait_for_selector("[data-unit-form]")
        foglio = page.evaluate("""() => ({ via: document.querySelector('#us-address').value, civico: document.querySelector('#us-civic').value })""")
        assert scritture == []                                                      # nulla scritto fino al salvataggio

        # 2. salvataggio con la scheda esistente -> collegamento automatico -> Zona con il marker
        page.click("[data-unit-form] [data-submit]")
        page.wait_for_function("location.hash.startsWith('#/edifici/mappa/immobile/')")
        pid = int(page.evaluate("location.hash.split('/').pop()"))
        page.wait_for_function(f"{MAPPA}.sources['stima-properties'] && {MAPPA}.sources['stima-properties'].data.features.some((f) => f.id === {pid})")
        page.wait_for_selector(f"[data-building-panel][data-property-panel='{pid}']")
        page.wait_for_selector(".riquadro-finto [data-open-property]")
        riga = _riga(m, pid)
        punti = _punti(page)
        attivo = page.evaluate(f"{MAPPA}.states[{pid}]")
        riquadro = page.text_content(".riquadro-finto")
        pannello = page.text_content("[data-property-panel]")
        consumato = page.evaluate("""() => ({ azioni: document.querySelector('[data-insert-actions]').hidden,
                                              segnaposti: window.__ml.markers.filter((k) => k.el.isConnected).length })""")
        misura_mappa = _sfora(page)

        # 3. dal marker alla scheda immobile esistente
        page.click(".riquadro-finto [data-open-property]")
        page.wait_for_function(f"location.hash === '#/immobili/{pid}'")
        page.wait_for_selector("#property-trash-btn, .property-detail, h2")
        browser.close()

    assert errori == [], errori
    assert "Immobili indipendenti" in legenda
    assert compilato == {"city": "Alba Adriatica", "via": "Via Roma", "civico": "25"}
    assert foglio == {"via": "Via Roma", "civico": "25"}
    assert _immobili(m) == prima + 1                                                # UN immobile
    assert riga["building"] is None and str(riga["lat"]) == "42.8330000" and str(riga["lng"]) == "13.9250000"
    assert riga["at"] is not None and riga["by"] == m["ids"]["owner_a"]
    assert [s[:2] for s in scritture] == [("POST", f"{url}/api/property/census/units"), ("POST", f"{url}/api/property/properties/{pid}/position")]
    assert json.loads(scritture[1][2]) == {"latitude": "42.8330000", "longitude": "13.9250000"}
    assert punti == [pid] and attivo == {"selected": True}                          # ne' il non verificato, ne' l'unita', ne' l'altrui
    assert "Apri scheda immobile" in riquadro and "Verificata su Zona" in pannello
    assert consumato == {"azioni": True, "segnaposti": 0}
    assert _riga(m, non_verificato["id"])["at"] is None and _riga(m, u["id"])["at"] is None
    for misura in (misura_zona, misura_mappa):
        assert misura["scroll"] <= misura["inner"], misura
    assert tocco >= 44


def test_zona3_fallimento_senza_doppione_esistente_collegabile_cestino_e_agenzie(server, monkeypatch):
    from playwright.sync_api import sync_playwright
    from property import geocode
    m = server
    geocode.clear_cache()
    monkeypatch.setattr(geocode, "requests", _Http(body=NOMINATIM))
    monkeypatch.setattr(geocode, "MIN_INTERVAL", 0.05)
    url = m["url"]
    esistente = _immobile(m, title="Villa Esistente")
    b = _edificio(m, city="Alba Adriatica", address="Via Palazzina Due", civic_number="5", name="Palazzina Due")
    u = _unita(m, building_id=b["id"], floor="2", internal_number="4")
    prima = _immobili(m)
    posizioni = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 820})
        errori = []
        page.on("pageerror", lambda e: errori.append(str(e)))
        page.add_init_script(FINTA)

        def rotta(route):
            posizioni.append((route.request.url, route.request.post_data))
            if len(posizioni) == 1:
                route.fulfill(status=503, content_type="application/json", body=json.dumps({"detail": "x", "code": "POSITION_NOT_INSTALLED"}))
            else:
                route.continue_()
        page.route("**/api/property/properties/*/position", rotta)

        # A. nuovo immobile indipendente, il PRIMO collegamento fallisce: salvato, non duplicato, «Riprova»
        _apri_zona(page, url)
        _punto_dalla_ricerca(page)
        page.click("[data-insert-confirm]")
        page.wait_for_selector("[data-insert-actions]:not([hidden])")
        page.click("[data-insert-action='unit']")
        page.wait_for_selector("[data-insert-sheet][open] [data-wizard-step='1']")
        page.check("#wz-unknown")
        page.click("[data-wizard-step='1'] [data-next]")
        page.wait_for_selector("[data-wizard-step='2']")
        page.click("[data-path='autonomous']")
        page.wait_for_selector("[data-unit-form]")
        page.click("[data-unit-form] [data-submit]")
        page.wait_for_selector("[data-unit-recovery]")
        pid = int(page.get_attribute("[data-unit-recovery]", "data-unit-recovery"))
        titolo = page.text_content("[data-insert-sheet] .census-sheet-title")
        dopo_fallimento = (_immobili(m), _riga(m, pid))
        assert page.get_attribute("[data-unit-recovery-open]", "href") == f"#/immobili/{pid}"
        page.click("[data-unit-recovery-retry]")
        page.wait_for_function(f"location.hash === '#/edifici/mappa/immobile/{pid}'")
        page.wait_for_function(f"{MAPPA}.sources['stima-properties'] && {MAPPA}.sources['stima-properties'].data.features.some((f) => f.id === {pid})")
        dopo_riprova = (_immobili(m), _riga(m, pid))

        # B. «Collega un immobile esistente»: solo gli indipendenti (l'unita' di palazzina e' esclusa)
        page.evaluate("document.querySelector('[data-insert-panel]').open = true")
        page.click("[data-insert-pick]")
        page.evaluate(f"{MAPPA}.setZoom(18)")
        page.evaluate(f"{MAPPA}.fire('click', {{ lngLat: {{ lng: 13.9301234, lat: 42.8301234 }} }})")
        page.wait_for_selector("[data-insert-point]:not([hidden])")
        page.click("[data-insert-confirm]")
        page.wait_for_selector("[data-insert-actions]:not([hidden])")
        page.click("[data-insert-action='link-property']")
        page.fill("[data-insert-link-input]", "Esistente")
        page.click("[data-insert-link-submit]")
        page.wait_for_selector(f"[data-insert-link-property='{esistente['id']}']")
        stato_prima = page.get_attribute(f"[data-insert-candidate-property='{esistente['id']}'] [data-property-position-status]", "data-property-position-status")
        page.click(f"[data-insert-link-property='{esistente['id']}']")
        page.wait_for_function(f"location.hash === '#/edifici/mappa/immobile/{esistente['id']}'")
        page.wait_for_function(f"{MAPPA}.sources['stima-properties'] && {MAPPA}.sources['stima-properties'].data.features.some((f) => f.id === {esistente['id']})")
        punti = sorted(_punti(page))
        # l'unita' di palazzina non compare fra i collegabili
        page.evaluate("document.querySelector('[data-insert-panel]').open = true")
        page.click("[data-insert-pick]")
        page.evaluate(f"{MAPPA}.fire('click', {{ lngLat: {{ lng: 13.9311234, lat: 42.8311234 }} }})")
        page.wait_for_selector("[data-insert-point]:not([hidden])")
        page.evaluate(f"{MAPPA}.setZoom(18)")
        page.click("[data-insert-confirm]")
        page.wait_for_selector("[data-insert-actions]:not([hidden])")
        page.click("[data-insert-action='link-property']")
        page.fill("[data-insert-link-input]", "Palazzina Due")
        page.click("[data-insert-link-submit]")
        page.wait_for_function("!document.querySelector('[data-insert-link-status]').hidden && !document.querySelector('[data-insert-link-status]').textContent.includes('Ricerca')")
        esclusa = page.text_content("[data-insert-link-status]")
        assert page.locator(f"[data-insert-link-property='{u['id']}']").count() == 0
        browser.close()

    assert errori == [], errori
    assert titolo == "Immobile salvato, ma posizione non collegata"
    assert dopo_fallimento[0] == prima + 1 and dopo_fallimento[1]["at"] is None          # salvato, non posizionato
    assert dopo_riprova[0] == prima + 1                                                   # nessun doppione
    assert str(dopo_riprova[1]["lat"]) == "42.8330000" and dopo_riprova[1]["at"] is not None and dopo_riprova[1]["by"] == m["ids"]["owner_a"]
    assert [json.loads(c[1]) for c in posizioni[:2]] == [{"latitude": "42.8330000", "longitude": "13.9250000"}] * 2
    assert posizioni[0][0] == posizioni[1][0] == f"{url}/api/property/properties/{pid}/position"
    assert stato_prima == "missing"
    riga = _riga(m, esistente["id"])
    assert str(riga["lat"]) == "42.8301234" and str(riga["lng"]) == "13.9301234" and riga["at"] is not None
    assert punti == sorted([pid, esistente["id"]])
    assert "unità di palazzina" in esclusa
    # l'unita' di palazzina non si posiziona da sola nemmeno dall'API (409), nulla cambia
    r = m["api"]().post(f"/api/property/properties/{u['id']}/position", json={"latitude": "42.8", "longitude": "13.9"})
    assert r.status_code == 409 and r.json()["code"] == "POSITION_NOT_INDEPENDENT"
    assert _riga(m, u["id"])["at"] is None
    # separazione delle agenzie e Cestino: l'immobile verificato non e' nella mappa di B ne' collegabile da B;
    # nel Cestino sparisce dalla mappa di A e non si collega
    assert m["api"]("owner_b").get("/api/property/properties/positions").json()["items"] == []
    assert m["api"]("owner_b").post(f"/api/property/properties/{pid}/position", json={"latitude": "40", "longitude": "10"}).status_code == 404
    assert m["api"]().post(f"/api/property/properties/{pid}/trash", json={"reason_code": "test_record"}).status_code == 200
    assert pid not in [i["id"] for i in m["api"]().get("/api/property/properties/positions").json()["items"]]
    assert m["api"]().post(f"/api/property/properties/{pid}/position", json={"latitude": "40", "longitude": "10"}).status_code == 409
    assert str(_riga(m, pid)["lat"]) == "42.8330000"
    assert _immobili(m) == prima + 1
