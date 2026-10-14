"""STIMA360 ZONA-5 - LA CAMPAGNA in browser (Chromium) con backend e PostgreSQL VERI
(schema completo, 102 e 103 comprese; database locale usa-e-getta). Il disegno
della mappa e' simulato (MapLibre finta di T4, qui con i gesti: il doppio clic
DOM diventa l'evento `dblclick` della mappa, `getCanvasContainer`/`unproject`
per la pressione prolungata); il provider della ricerca e' FINTO (nessuna
chiamata a Nominatim: risposta simulata, con e senza civico).

  1. computer (1280): doppio clic -> punto provvisorio -> ricerca inversa
     (civico presente) -> «Indirizzo individuato» -> correzione del civico ->
     conferma dell'indirizzo (obbligatoria: senza, «Conferma punto» e' spento)
     -> conferma del punto -> «Nuovo edificio» precompilato con l'indirizzo
     DICHIARATO -> posizione verificata (102) con il punto del gesto;
  2. smartphone (390 e 320): pressione prolungata -> ricerca inversa SENZA
     civico (resta vuoto) -> civico completato a mano -> conferma -> «Nuovo
     immobile» indipendente -> posizione verificata (103);
  3. collegamento di entita' esistenti preservato (edificio, dal gesto);
  4. «Annulla» senza scritture; nessuna attivazione da trascinamento o zoom;
     doppio clic su un marker = nessun punto; lo zoom del doppio clic spento;
  5. nessuna coordinata inviata al backend se non il punto confermato
     (la ricerca inversa riceve SOLO le due coordinate del punto scelto);
     nessuno scroll orizzontale, tocchi >= 44 px.
"""
from __future__ import annotations

import json

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _edificio, _q  # noqa: F401
from tests.test_censimento_3_backend_postgres import completo, mondo  # noqa: F401  (fixture)
from tests.test_edifici_1_browser_postgres import server  # noqa: F401  (fixture)
from tests.test_territorio_4_browser_postgres import FINTA, MAPPA
from tests.test_zona_3_browser_postgres import _riga
from tests.test_zona_4_browser_postgres import _apri, _edificio_riga
from tests.test_zona_5_postgres import CON_CIVICO, SENZA_CIVICO, _Http

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

# la MapLibre finta con i gesti: doppio clic DOM -> evento `dblclick` della mappa (con lngLat
# e `point`), contenitore e `unproject` lineare per la pressione prolungata, zoom del doppio clic
FINTA_GESTI = FINTA + r"""
(() => {
  const Base = window.maplibregl.Map;
  window.maplibregl.Map = class extends Base {
    constructor(o) {
      super(o);
      this.doubleClickZoom = { disabled: false, disable: () => { this.doubleClickZoom.disabled = true; } };
      this.container.addEventListener('dblclick', (ev) => {
        const r = this.container.getBoundingClientRect();
        const p = [ev.clientX - r.left, ev.clientY - r.top];
        this.fire('dblclick', { lngLat: this.unproject(p), point: p, originalEvent: ev });
      });
    }
    getCanvasContainer() { return this.container; }
    unproject(p) { return { lng: 13.9 + p[0] / 10000, lat: 42.8 + p[1] / 10000 }; }
    queryRenderedFeatures(p) { return (window.__hit || []); }
  };
})();
"""


class _HttpInverso(_Http):
    """La ricerca diretta risponde con la lista; quella inversa con il corpo scelto (per punto)."""

    def __init__(self, inverso):
        super().__init__()
        self.inverso = inverso

    def get(self, url, params=None, headers=None, timeout=None, **kw):
        r = super().get(url, params=params, headers=headers, timeout=timeout, **kw)
        if url.endswith("/reverse"):
            corpo = self.inverso(params or {})
            r.json = lambda: corpo
        return r


def _provider(monkeypatch, inverso):
    from property import geocode
    geocode.clear_cache()
    http = _HttpInverso(inverso)
    monkeypatch.setattr(geocode, "requests", http)
    monkeypatch.setattr(geocode, "MIN_INTERVAL", 0.05)
    return http


def _pressione(page, x, y, muovi=0, attendi=800):
    page.evaluate(f"""() => {{ const c = document.querySelector('[data-map]'); const r = c.getBoundingClientRect();
        const ev = (t, o) => c.dispatchEvent(new PointerEvent(t, {{ bubbles: true, pointerType: 'touch', isPrimary: true, pointerId: 1,
                                                                  clientX: r.left + {x}, clientY: r.top + {y}, ...o }}));
        ev('pointerdown', {{}});
        if ({muovi}) ev('pointermove', {{ clientX: r.left + {x} + {muovi} }}); }}""")
    page.wait_for_timeout(attendi)


def _conferma_indirizzo(page):
    page.click("[data-address-confirm]")
    page.wait_for_selector("[data-insert-address][data-address-confirmed='true']")


def _conferma_punto(page):
    page.evaluate(f"{MAPPA}.setZoom(18)")
    page.click("[data-insert-confirm]")
    page.wait_for_selector("[data-insert-actions]:not([hidden])")


def test_zona5_computer_doppio_clic_indirizzo_conferma_e_nuovo_edificio(server, monkeypatch):
    from playwright.sync_api import sync_playwright
    m = server
    http = _provider(monkeypatch, lambda p: CON_CIVICO)
    url = m["url"]
    esistente = _edificio(m, city="Alba Adriatica", address="Via Gesto Esistente", civic_number="3", name="Palazzina Gesto")
    scritture = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 820})
        errori = []
        page.on("pageerror", lambda e: errori.append(str(e)))
        page.on("request", lambda r: scritture.append((r.method, r.url, r.post_data)) if r.method in ("POST", "PATCH", "PUT", "DELETE") else None)
        page.add_init_script(FINTA_GESTI)
        _apri(page, url)
        apertura = page.evaluate(f"() => ({{ zoomDoppio: {MAPPA}.doubleClickZoom.disabled, scroll: document.documentElement.scrollWidth <= innerWidth }})")
        # zoom e spostamento: nessuna ricerca; doppio clic su un marker: niente
        page.evaluate(f"{MAPPA}.setZoom(16); {MAPPA}.fire('moveend', {{}})")
        page.evaluate("window.__hit = [{ id: 1 }]")
        page.dblclick("[data-map]", position={"x": 300, "y": 300})
        page.wait_for_timeout(300)
        su_marker = page.evaluate("() => ({ punto: !document.querySelector('[data-insert-point]').hidden, segnaposti: window.__ml.markers.filter((k) => k.el.isConnected).length })")
        page.evaluate("window.__hit = []")
        assert http.calls == []
        # 1. il gesto
        page.dblclick("[data-map]", position={"x": 262, "y": 341})
        page.wait_for_selector("[data-insert-address][data-address-state='found']")
        gesto = page.evaluate("""() => ({ punto: !document.querySelector('[data-insert-point]').hidden, p: window.__ml.markers.filter((k) => k.el.isConnected)[0].p,
            titolo: document.querySelector('.territorio-address-title').textContent, domanda: document.querySelector('.territorio-address-question').textContent,
            via: document.querySelector('[data-address-road]').value, civico: document.querySelector('[data-address-civic]').value,
            comune: document.querySelector('[data-address-town]').value, cap: document.querySelector('[data-address-postcode]').value,
            confermaSpenta: document.querySelector('[data-insert-confirm]').disabled, aperto: document.querySelector('[data-insert-panel]').open,
            tocco: Math.min(...[...document.querySelectorAll('[data-address-confirm], [data-address-refresh]')].map((b) => b.getBoundingClientRect().height)),   // i pulsanti; i campi sono quelli Aurora di tutto il CRM
            scroll: document.documentElement.scrollWidth <= innerWidth })""")
        inverse = [c for c in http.calls if c["url"].endswith("/reverse")]
        # 2. correzione del civico, conferma dell'indirizzo (obbligatoria), conferma del punto
        page.evaluate(f"{MAPPA}.setZoom(18)")
        assert page.is_disabled("[data-insert-confirm]")
        page.fill("[data-address-civic]", "25 B")
        _conferma_indirizzo(page)
        confermato = page.text_content("[data-address-status]")
        _conferma_punto(page)
        nota = page.text_content("[data-insert-chosen-note]")
        assert scritture == []
        # 3. «Nuovo edificio» precompilato con l'indirizzo DICHIARATO
        page.click("[data-insert-action='building']")
        page.wait_for_selector("[data-insert-sheet][open] [data-wizard-step='1']")
        compilato = page.evaluate("""() => ({ city: document.querySelector('#wz-city').value, via: document.querySelector('#wz-address').value, civico: document.querySelector('#wz-civic').value })""")
        page.fill("#wz-declared", "4")
        page.click("[data-wizard-step='1'] [data-next]")
        page.wait_for_selector("[data-wizard-step='2']")
        page.click("[data-path='building']")
        page.wait_for_selector("[data-wizard-step='3'] [data-create-building]")
        page.click("[data-create-building]")
        page.wait_for_selector("[data-wizard-step='4'] [data-save-building]")
        page.click("[data-save-building]")
        page.wait_for_selector("[data-ready-building][data-ready-state='ok']")
        bid = int(page.get_attribute("[data-ready-building]", "data-ready-building"))
        page.click("[data-ready-map]")
        page.wait_for_function(f"location.hash === '#/edifici/mappa/edificio/{bid}'")
        page.wait_for_selector(f"[data-building-panel='{bid}']")
        # 4. collegamento di un edificio esistente dal gesto (preservato)
        page.evaluate("document.querySelector('[data-insert-panel]').open = true")
        page.dblclick("[data-map]", position={"x": 400, "y": 400})
        page.wait_for_selector("[data-insert-address][data-address-state='found']")
        _conferma_indirizzo(page)
        _conferma_punto(page)
        page.click("[data-insert-action='link']")
        page.fill("[data-insert-link-input]", "Gesto Esistente")
        page.click("[data-insert-link-submit]")
        page.wait_for_selector(f"[data-insert-link-building='{esistente['id']}']")
        page.click(f"[data-insert-link-building='{esistente['id']}']")
        page.wait_for_function(f"location.hash === '#/edifici/mappa/edificio/{esistente['id']}'")
        # 5. «Annulla» senza scritture
        page.evaluate("document.querySelector('[data-insert-panel]').open = true")
        page.dblclick("[data-map]", position={"x": 420, "y": 420})
        page.wait_for_selector("[data-insert-address][data-address-state='found']")
        page.click("[data-insert-point] [data-insert-cancel]")
        annullato = page.evaluate("() => ({ punto: !document.querySelector('[data-insert-point]').hidden, indirizzo: !document.querySelector('[data-insert-address]').hidden, segnaposti: window.__ml.markers.filter((k) => k.el.isConnected).length })")
        browser.close()

    assert errori == [], errori
    assert apertura == {"zoomDoppio": True, "scroll": True}
    assert su_marker == {"punto": False, "segnaposti": 0}
    g = gesto
    assert g["punto"] is True and abs(g["p"][0] - 13.9262) < 0.0005 and abs(g["p"][1] - 42.8341) < 0.0005     # (bordo del contenitore: ±1 px)
    assert g["aperto"] is True and g["scroll"] is True and g["tocco"] >= 43.5
    lat7, lng7 = f"{g['p'][1]:.7f}", f"{g['p'][0]:.7f}"
    assert g["titolo"] == "Indirizzo individuato" and g["domanda"] == "Confermi che questo è l’indirizzo corretto?"
    assert {k: g[k] for k in ("via", "civico", "comune", "cap")} == {"via": "Via Roma", "civico": "25", "comune": "Alba Adriatica", "cap": "64011"}
    assert g["confermaSpenta"] is True
    assert len(inverse) == 1 and inverse[0]["params"]["lat"] == lat7 and inverse[0]["params"]["lon"] == lng7
    assert set(inverse[0]["params"]) == {"lat", "lon", "format", "zoom", "addressdetails", "accept-language"}       # SOLO il punto
    assert confermato == "Indirizzo confermato. Via Roma 25 B, 64011 Alba Adriatica"
    assert nota == "Indirizzo dichiarato: Via Roma 25 B, 64011 Alba Adriatica"
    assert compilato == {"city": "Alba Adriatica", "via": "Via Roma", "civico": "25 B"}
    riga = _edificio_riga(m, bid)
    assert str(riga["lat"]) == lat7 and str(riga["lng"]) == lng7 and riga["at"] is not None and riga["by"] == m["ids"]["owner_a"]
    corpi = [c for c in scritture if c[1].endswith("/position")]
    assert [c[1].split("/api/property/")[1] for c in corpi] == [f"buildings/{bid}/position", f"buildings/{esistente['id']}/position"]
    assert json.loads(corpi[0][2]) == {"latitude": lat7, "longitude": lng7}
    assert _edificio_riga(m, esistente["id"])["at"] is not None
    assert annullato == {"punto": False, "indirizzo": False, "segnaposti": 0}
    assert len([c for c in http.calls if c["url"].endswith("/reverse")]) == 3                                    # un gesto = una ricerca
    assert all(c["headers"]["User-Agent"].startswith("STIMA360-CRM") for c in http.calls)


@pytest.mark.parametrize("larghezza", [320, 390])
def test_zona5_smartphone_pressione_prolungata_civico_assente_e_nuovo_immobile(server, larghezza, monkeypatch):
    from playwright.sync_api import sync_playwright
    m = server
    http = _provider(monkeypatch, lambda p: SENZA_CIVICO)
    url = m["url"]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": larghezza, "height": 820}, has_touch=True)
        page = context.new_page()
        errori = []
        page.on("pageerror", lambda e: errori.append(str(e)))
        page.add_init_script(FINTA_GESTI)
        _apri(page, url)
        # trascinamento e rilascio rapido: niente
        _pressione(page, 100, 100, muovi=30)
        page.evaluate("""() => document.querySelector('[data-map]').dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1 }))""")
        _pressione(page, 100, 100, attendi=200)
        page.evaluate("""() => document.querySelector('[data-map]').dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1 }))""")
        page.wait_for_timeout(700)
        niente = page.evaluate("() => ({ punto: !document.querySelector('[data-insert-point]').hidden, segnaposti: window.__ml.markers.filter((k) => k.el.isConnected).length })")
        assert http.calls == []
        # la pressione prolungata ferma
        _pressione(page, 150, 50)
        page.wait_for_selector("[data-insert-address][data-address-state='found']")
        gesto = page.evaluate("""() => ({ p: window.__ml.markers.filter((k) => k.el.isConnected)[0].p, civico: document.querySelector('[data-address-civic]').value,
            via: document.querySelector('[data-address-road]').value, stato: document.querySelector('[data-address-status]').textContent,
            scroll: document.documentElement.scrollWidth <= innerWidth,
            tocco: Math.min(...[...document.querySelectorAll('[data-address-confirm], [data-address-refresh], [data-insert-confirm]')].map((b) => b.getBoundingClientRect().height)) })""")
        page.fill("[data-address-civic]", "7")
        _conferma_indirizzo(page)
        _conferma_punto(page)
        page.click("[data-insert-action='unit']")
        page.wait_for_selector("[data-insert-sheet][open] [data-wizard-step='1']")
        compilato = page.evaluate("""() => ({ city: document.querySelector('#wz-city').value, via: document.querySelector('#wz-address').value, civico: document.querySelector('#wz-civic').value })""")
        page.check("#wz-unknown")
        page.click("[data-wizard-step='1'] [data-next]")
        page.wait_for_selector("[data-wizard-step='2']")
        page.click("[data-path='autonomous']")
        page.wait_for_selector("[data-unit-form]")
        foglio = page.evaluate("""() => ({ via: document.querySelector('#us-address').value, civico: document.querySelector('#us-civic').value })""")
        page.click("[data-unit-form] [data-submit]")
        page.wait_for_function("location.hash.startsWith('#/edifici/mappa/immobile/')")
        pid = int(page.evaluate("location.hash.split('/').pop()"))
        page.wait_for_selector(f"[data-property-panel='{pid}']")
        browser.close()

    assert errori == [], errori
    assert niente == {"punto": False, "segnaposti": 0}
    assert abs(gesto["p"][0] - 13.915) < 0.0005 and abs(gesto["p"][1] - 42.805) < 0.0005
    assert gesto["civico"] == "" and gesto["via"] == "Via Roma" and "Civico non trovato" in gesto["stato"]
    assert gesto["scroll"] is True and gesto["tocco"] >= 43.5
    assert compilato == {"city": "Alba Adriatica", "via": "Via Roma", "civico": "7"} and foglio == {"via": "Via Roma", "civico": "7"}
    riga = _riga(m, pid)
    assert str(riga["lat"]) == f"{gesto['p'][1]:.7f}" and str(riga["lng"]) == f"{gesto['p'][0]:.7f}" and riga["at"] is not None and riga["building"] is None
    assert len([c for c in http.calls if c["url"].endswith("/reverse")]) == 1
