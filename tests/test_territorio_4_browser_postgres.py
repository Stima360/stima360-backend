"""STIMA360 TERRITORIO T4 - IL PERCORSO INTERO in browser (Chromium) con backend e
PostgreSQL VERI (schema completo dal runner, migration 102 compresa; database
locale usa-e-getta). Solo il disegno della mappa e' simulato (MapLibre finta:
CDN e tessere non si raggiungono dalle prove), tutto il resto e' vero: Shell,
router, API, trigger della 102, sessione del titolare dell'agenzia 1.

  Edificio TEST -> «Posiziona sulla mappa» -> tocco = punto provvisorio
  (correggibile: nuovo tocco, trascinamento) -> conferma solo a zoom >= 17 ->
  errore del server = nessun «successo» -> conferma vera -> salvataggio
  verificato (data del server, operatore della sessione) -> mappa generale con
  l'edificio evidenziato e cliccabile -> scheda «Correggi posizione».

E, nello stesso percorso, le proprieta' di sicurezza essenziali:
  * coordinate preesistenti mai verificate da sole (edificio B: nessun marker);
  * coordinate cambiate fuori dalla conferma -> verifica decaduta, marker sparito;
  * «Annulla» con un punto provvisorio: nessuna richiesta, nulla salvato;
  * il client non manda mai l'operatore; nessuno sforamento a 390 e 1280 px.
(Altra agenzia -> 404 e Cestino -> 409 sulla stessa rotta: tests/test_territorio_4_postgres.py 05.)
"""
from __future__ import annotations

import json

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _edificio, _q  # noqa: F401
from tests.test_censimento_3_backend_postgres import completo, mondo  # noqa: F401  (fixture)
from tests.test_edifici_1_browser_postgres import server  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

FINTA = r"""
window.__ml = { maps: [], markers: [] };
class Ev { constructor() { this.h = {}; }
  on(e, a, b) { if (b) (this.h[e + ':' + a] ||= []).push(b); else (this.h[e] ||= []).push(a); return this; }
  fire(e, x = {}) { for (const f of [...(this.h[e] || [])]) f(x); } }
window.maplibregl = {
  Map: class extends Ev {
    constructor(o) { super(); this.o = o; this.z = o.zoom; this.sources = {}; this.states = {}; this.removed = false;
      this.container = o.container; window.__ml.maps.push(this);
      const k = document.createElement('canvas'); k.className = 'finta'; k.style.cssText = 'width:100%;height:100%;display:block';
      o.container.appendChild(k); setTimeout(() => this.fire('load'), 0); }
    addControl() {} addLayer() {} fitBounds() {}
    addSource(id, s) { this.sources[id] = s; }
    setFeatureState(f, s) { this.states[f.id] = { ...(this.states[f.id] || {}), ...s }; }
    jumpTo(o) { if (o.zoom !== undefined) this.z = o.zoom; }
    getZoom() { return this.z; }
    setZoom(z) { this.z = z; this.fire('zoomend'); }
    getCanvas() { return { style: {} }; }
    loaded() { return true; }
    remove() { this.removed = true; }
  },
  NavigationControl: class {},
  Marker: class extends Ev {
    constructor(o) { super(); this.o = o; this.el = o.element; window.__ml.markers.push(this); }
    setLngLat(p) { this.p = p; return this; }
    getLngLat() { return { lng: this.p[0], lat: this.p[1] }; }
    addTo(m) { m.container.appendChild(this.el); return this; }
    remove() { this.el.remove(); }
  },
  Popup: class extends Ev {
    setLngLat() { return this; }
    setDOMContent(n) { n.classList.add('riquadro-finto'); if (this.n && this.n.isConnected) this.n.replaceWith(n); this.n = n; return this; }
    addTo() { document.body.appendChild(this.n); return this; }
    remove() { if (this.n) this.n.remove(); this.fire('close'); }
  },
};
"""

MAPPA = "window.__ml.maps[window.__ml.maps.length - 1]"


def _riga(m, bid):
    return dict(zip(("lat", "lng", "at", "by"), _q(m, "SELECT latitude, longitude, position_verified_at, "
                                                      "position_verified_by_user_id FROM buildings WHERE id = %s",
                                                      (bid,))[0]))


def _sfora(page):
    return page.evaluate("() => ({ scroll: document.documentElement.scrollWidth, inner: innerWidth })")


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_t4_percorso_completo_e_sicurezza(server, larghezza):
    from playwright.sync_api import sync_playwright
    m = server
    a = _edificio(m, city="Alba Adriatica", address="Via Mappa", civic_number="4", name="Palazzina Faro")
    b = _edificio(m, city="Alba Adriatica", address="Via Antica", civic_number="9", name="Casa Vecchia",
                  latitude="42.8300000", longitude="13.9300000", confirm_similar=True)
    assert _riga(m, b["id"])["at"] is None                                   # preesistenti: mai verificate
    url = m["url"]
    corpi = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": larghezza, "height": 820})
        errori = []
        page.on("pageerror", lambda e: errori.append(str(e)))
        page.on("request", lambda r: corpi.append(r.post_data) if r.method == "POST" and r.url.endswith("/position") else None)
        page.add_init_script(FINTA)

        # 1. scheda dell'edificio B (coordinate non verificate) e dell'edificio A (nessuna)
        page.goto(f"{url}/os/#/edifici/{b['id']}")
        page.wait_for_selector("#building-position")
        assert page.text_content("#building-position") == "Verifica posizione"
        assert "Da verificare" in page.text_content("[data-position-fact]")
        page.goto(f"{url}/os/#/edifici/{a['id']}")
        page.wait_for_selector("#building-position")
        assert page.text_content("#building-position") == "Posiziona sulla mappa"
        assert page.text_content("[data-position-fact] dd") == "Non indicata"

        # 1b. «Annulla» dopo un punto provvisorio: si torna alla scheda e NIENTE e' salvato
        page.click("#building-position")
        page.wait_for_selector("canvas.finta")
        page.wait_for_function("document.querySelector('[data-map-status]').hidden")
        page.evaluate(f"{MAPPA}.setZoom(18)")
        page.evaluate(f"{MAPPA}.fire('click', {{ lngLat: {{ lng: 13.9240, lat: 42.8320 }} }})")
        assert page.is_enabled("[data-place-confirm]")
        page.click("[data-place-cancel]")
        page.wait_for_function(f"location.hash === '#/edifici/{a['id']}'")
        page.wait_for_selector("#building-position")
        assert _riga(m, a["id"]) == {"lat": None, "lng": None, "at": None, "by": None}
        assert corpi == []

        # 2. «Posiziona sulla mappa»
        page.evaluate("window.__ml.markers.length = 0")
        page.click("#building-position")
        page.wait_for_function(f"location.hash === '#/edifici/mappa/posiziona/{a['id']}'")
        page.wait_for_selector("canvas.finta")
        page.wait_for_function("document.querySelector('[data-map-status]').hidden")
        assert page.text_content("[data-place-title]") == "Palazzina Faro"
        assert page.is_disabled("[data-place-confirm]")
        assert "Tocca il punto esatto" in page.text_content("[data-place-hint]")

        # 3. tocco = punto provvisorio; a zoom 15 la conferma resta spenta
        page.evaluate(f"{MAPPA}.fire('click', {{ lngLat: {{ lng: 13.9250, lat: 42.8330 }} }})")
        assert page.locator(".territorio-pin.is-provisional").count() == 2   # segnaposto + legenda
        assert page.is_disabled("[data-place-confirm]")
        assert "Ingrandisci" in page.text_content("[data-place-hint]")
        page.evaluate(f"{MAPPA}.setZoom(18)")
        assert page.is_enabled("[data-place-confirm]")
        # 4. correzione: nuovo tocco (stesso segnaposto) e trascinamento
        page.evaluate(f"{MAPPA}.fire('click', {{ lngLat: {{ lng: 13.9255, lat: 42.8335 }} }})")
        assert page.evaluate("window.__ml.markers.length") == 1
        page.evaluate("(() => { const k = window.__ml.markers[0]; k.setLngLat([13.9262123, 42.8341456]); k.fire('dragend'); })()")
        assert _riga(m, a["id"]) == {"lat": None, "lng": None, "at": None, "by": None}   # niente salvato

        # 5. errore del server: nessun «successo», si resta qui
        page.route("**/api/property/buildings/*/position", lambda r: r.fulfill(
            status=503, content_type="application/json",
            body=json.dumps({"detail": "x", "code": "POSITION_NOT_INSTALLED"})))
        page.click("[data-place-confirm]")
        page.wait_for_selector("[data-place-error]:not([hidden])")
        assert "Posizione non salvata" in page.text_content("[data-place-error]")
        assert page.evaluate("location.hash") == f"#/edifici/mappa/posiziona/{a['id']}"
        assert page.is_enabled("[data-place-confirm]")
        page.unroute("**/api/property/buildings/*/position")
        misura_posiziona = _sfora(page)
        altezza_bottone = page.evaluate("document.querySelector('[data-place-confirm]').getBoundingClientRect().height")

        # 6. conferma vera -> mappa generale, edificio evidenziato
        page.click("[data-place-confirm]")
        page.wait_for_function("location.hash === '#/edifici/mappa'")
        page.wait_for_selector(".riquadro-finto [data-open-building]")
        punti = page.evaluate(f"{MAPPA}.sources['stima-buildings'].data.features.map((f) => f.id)")
        attivo = page.evaluate(f"{MAPPA}.states[{a['id']}]")
        riquadro = page.text_content(".riquadro-finto")
        da_posizionare = page.text_content("[data-to-place] summary")
        misura_mappa = _sfora(page)
        riga = _riga(m, a["id"])

        # 7. la scheda dice chi e quando; il comando diventa «Correggi posizione»
        page.click(".riquadro-finto [data-open-building]")
        page.wait_for_function(f"location.hash === '#/edifici/{a['id']}'")
        page.wait_for_function("document.querySelector('[data-position-fact] dd') && "
                               "document.querySelector('[data-position-fact] dd').textContent.includes('da Olga')")
        fatto = page.text_content("[data-position-fact] dd")
        comando = page.text_content("#building-position")

        # 8. coordinate cambiate fuori dalla conferma: la verifica decade, il marker sparisce
        r = m["api"]().patch(f"/api/property/buildings/{a['id']}", json={"longitude": "13.9300000"})
        assert r.status_code == 200, r.text
        page.goto(f"{url}/os/#/edifici/mappa")
        page.wait_for_selector("[data-to-place] summary")
        dopo = page.evaluate(f"{MAPPA}.sources['stima-buildings'] ? {MAPPA}.sources['stima-buildings'].data.features.length : -1")
        browser.close()

    assert errori == [], errori
    assert punti == [a["id"]]                                                # B (non verificato) non c'e'
    assert attivo == {"selected": True} and "Palazzina Faro" in riquadro
    assert da_posizionare == "Edifici da posizionare: 1"
    assert str(riga["lat"]) == "42.8341456" and str(riga["lng"]) == "13.9262123"
    assert riga["at"] is not None and riga["by"] == m["ids"]["owner_a"]
    assert all(json.loads(c) == {"latitude": "42.8341456", "longitude": "13.9262123"} for c in corpi) and len(corpi) == 2
    assert fatto.startswith("Verificata il ") and fatto.endswith("da Olga") and comando == "Correggi posizione"
    assert _riga(m, a["id"])["at"] is None and dopo == 0
    assert _riga(m, b["id"])["at"] is None
    for misura in (misura_posiziona, misura_mappa):
        assert misura["scroll"] <= misura["inner"], misura
    assert altezza_bottone >= 44
