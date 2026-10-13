"""STIMA360 TERRITORIO T5 - immobili attraverso gli edifici, in browser (Chromium)
con backend e PostgreSQL VERI (schema completo, 102 compresa; database locale
usa-e-getta). Solo il disegno della mappa e' simulato (la MapLibre finta di T4:
CDN e tessere non si raggiungono dalle prove).

Un'unica verifica del percorso:
  marker dell'edificio verificato -> pannello «Edificio» (nome, indirizzo,
  numero e elenco delle unita' per piano, con piano/interno/tipologia) ->
  scheda immobile ESISTENTE (#/immobili/{id}) -> «indietro» = mappa con lo
  stesso edificio aperto -> «+ Aggiungi unita'» = foglio ESISTENTE del
  censimento sulla scheda edificio -> «Vedi sulla mappa» -> chiusura del
  riquadro = pannello chiuso.
Nessuna scrittura durante tutto il percorso (il pannello legge e basta), nessun
dato reale; nota sugli immobili senza edificio presente.
"""
from __future__ import annotations

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _edificio, _q, _unita  # noqa: F401
from tests.test_censimento_3_backend_postgres import completo, mondo  # noqa: F401  (fixture)
from tests.test_edifici_1_browser_postgres import server  # noqa: F401  (fixture)
from tests.test_territorio_4_browser_postgres import FINTA, MAPPA

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_t5_marker_unita_scheda_aggiungi_e_ritorno(server, larghezza):
    from playwright.sync_api import sync_playwright
    m = server
    a = _edificio(m, city="Alba Adriatica", address="Via Faro", civic_number="7", name="Residenza Faro")
    r = m["api"]().post(f"/api/property/buildings/{a['id']}/position",
                        json={"latitude": "42.8341000", "longitude": "13.9262000"})
    assert r.status_code == 200 and r.json()["status"] == "verified", r.text
    u1 = _unita(m, building_id=a["id"], floor="1", internal_number="3", surface_sqm=80)
    _unita(m, building_id=a["id"], floor="1", internal_number="4", property_type="apartment")
    _unita(m, building_id=a["id"], floor="T", property_type="commercial", surface_sqm=45)
    unita_prima = _q(m, "SELECT count(*) FROM properties WHERE building_id = %s", (a["id"],))[0][0]
    url = m["url"]
    scritture = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": larghezza, "height": 820})
        errori = []
        page.on("pageerror", lambda e: errori.append(str(e)))
        page.on("request", lambda q: scritture.append((q.method, q.url)) if q.method in ("POST", "PATCH", "PUT", "DELETE") else None)
        page.add_init_script(FINTA)
        # i riquadri creati, per poterne chiudere uno come farebbe l'operatore
        page.add_init_script("""(() => { const P = window.maplibregl.Popup; window.__popups = [];
          window.maplibregl.Popup = class extends P { constructor(...a) { super(...a); window.__popups.push(this); } }; })();""")

        # 1. marker -> pannello con le unita'
        page.goto(f"{url}/os/#/edifici/mappa")
        page.wait_for_function(f"{MAPPA} && {MAPPA}.sources['stima-buildings']")
        assert page.text_content("[data-independent-note]").startswith("Ville, case indipendenti")
        page.evaluate(f"{MAPPA}.fire('click:stima-buildings-points', {{ features: [{{ id: {a['id']} }}] }})")
        page.wait_for_selector("[data-building-panel] [data-units-count]")
        pannello = {
            "titolo": page.text_content(".territorio-building-title"),
            "dove": page.text_content(".territorio-building-where"),
            "conteggio": page.text_content("[data-units-count]"),
            "piani": page.eval_on_selector_all(".territorio-floor", "els => els.map((e) => e.textContent)"),
            "unita": page.eval_on_selector_all("[data-unit-link]", "els => els.map((e) => [e.getAttribute('href'), e.textContent])"),
            "aggiungi": page.get_attribute("[data-add-unit]", "href"),
            "hash": page.evaluate("location.hash"),
            "scroll": page.evaluate("document.documentElement.scrollWidth <= innerWidth"),
            "tocco": page.evaluate("Math.min(...[...document.querySelectorAll('.territorio-building-actions .btn')].map((b) => b.getBoundingClientRect().height))"),
        }

        # 2. unita' -> scheda immobile esistente; «indietro» -> mappa con lo stesso edificio aperto
        page.click(f"[data-unit-link='{u1['id']}']")
        page.wait_for_function(f"location.hash === '#/immobili/{u1['id']}'")
        page.wait_for_selector("#property-trash-btn, .property-detail, h2")
        page.go_back()
        page.wait_for_function(f"location.hash === '#/edifici/mappa/edificio/{a['id']}'")
        page.wait_for_selector(f"[data-building-panel='{a['id']}'] [data-units-count]")
        ritorno_attivo = page.evaluate(f"{MAPPA}.states[{a['id']}]")

        # 3. «+ Aggiungi unita'» -> foglio ESISTENTE del censimento sulla scheda edificio
        page.click("[data-add-unit]")
        page.wait_for_function(f"location.hash === '#/edifici/{a['id']}'")
        page.wait_for_selector("#unit-sheet[open]")
        foglio = page.text_content("#unit-sheet")
        page.keyboard.press("Escape")

        # 4. «Vedi sulla mappa» dalla scheda edificio (posizione verificata)
        page.wait_for_selector("#building-on-map:not([hidden])")
        page.click("#building-on-map")
        page.wait_for_function(f"location.hash === '#/edifici/mappa/edificio/{a['id']}'")
        page.wait_for_selector(f"[data-building-panel='{a['id']}'] [data-units-count]")

        # 5. chiusura del riquadro: pannello chiuso, indirizzo della mappa generale
        page.evaluate("window.__popups[window.__popups.length - 1].remove()")
        chiuso = page.evaluate("""() => ({ hidden: document.querySelector('[data-building-panel]').hidden,
                                           hash: location.hash })""")
        browser.close()

    assert errori == [], errori
    assert pannello["titolo"] == "Residenza Faro"
    assert pannello["dove"] == "Via Faro 7 · Alba Adriatica"
    assert pannello["conteggio"] == "Unità immobiliari: 3"
    assert len(pannello["unita"]) == 3 and len(pannello["piani"]) >= 2
    assert any(h == f"#/immobili/{u1['id']}" and "int. 3" in t and "piano 1" in t.lower() for h, t in pannello["unita"])
    assert pannello["aggiungi"] == f"#/edifici/{a['id']}/aggiungi/censimento"
    assert pannello["hash"] == f"#/edifici/mappa/edificio/{a['id']}"
    assert pannello["scroll"] is True and pannello["tocco"] >= 44
    assert ritorno_attivo == {"selected": True}
    assert "unità" in foglio.lower() or "Unità" in foglio
    assert chiuso == {"hidden": True, "hash": "#/edifici/mappa"}
    assert scritture == [], scritture                                        # il percorso legge e basta
    assert _q(m, "SELECT count(*) FROM properties WHERE building_id = %s", (a["id"],))[0][0] == unita_prima
