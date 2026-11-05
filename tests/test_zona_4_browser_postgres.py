"""STIMA360 ZONA-4 + ZONA-6 - LA CAMPAGNA in browser (Chromium, GPS SIMULATO dal
browser stesso) con backend e PostgreSQL VERI (schema completo, 102 e 103
comprese; database locale usa-e-getta). Il disegno della mappa e' simulato
(MapLibre finta di T4); la Geolocation API e' quella VERA di Chromium, con la
posizione e il permesso impostati da Playwright, e viene osservata (contata) da
uno script che non ne cambia il comportamento.

SENTINELLA AGGIORNATA DA STIMA360 ZONA-6: la posizione e' in tempo reale.

  1. all'apertura di Zona UNA richiesta continua (`watchPosition`, mai
     `getCurrentPosition`): punto blu e cerchio, mappa centrata alla prima
     posizione; ci si sposta (Playwright cambia la posizione) -> il punto si
     aggiorna, la vista NO (l'operatore l'aveva trascinata);
  2. «+ → Dalla mia posizione» -> punto provvisorio nel pannello (la posizione
     ATTUALE) -> correzione manuale (trascinamento) -> conferma (zoom >= 17);
  3. «Nuovo edificio» -> procedura esistente -> posizione VERIFICATA con il
     punto CORRETTO (102); poi «Nuovo immobile» indipendente (103); poi collega
     un edificio esistente e un immobile indipendente esistente;
  4. nessuna coordinata GPS inviata al backend (al backend vanno SOLO i punti
     confermati); uscita da Zona -> `clearWatch`;
  5. a 320, 390 e 1280 px: «+» >= 44 px dentro la mappa in basso a destra,
     filtri >= 44 px, nessuno scroll orizzontale;
  6. permesso negato, posizione non disponibile, tempo scaduto: messaggi
     leggibili, nessun indicatore, «Dalla mia posizione» spento.
"""
from __future__ import annotations

import json

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _edificio, _q  # noqa: F401
from tests.test_censimento_3_backend_postgres import completo, mondo  # noqa: F401  (fixture)
from tests.test_edifici_1_browser_postgres import server  # noqa: F401  (fixture)
from tests.test_territorio_4_browser_postgres import FINTA, MAPPA
from tests.test_zona_3_browser_postgres import _immobile, _riga
# SENTINELLA AGGIORNATA DA STIMA360 ZONA-5: «Inserisci qui» cerca anche l'indirizzo del punto (ricerca inversa,
# provider FINTO: nessuna chiamata a Nominatim dalle prove) e il punto si conferma solo dopo l'indirizzo.
from tests.test_zona_5_postgres import _HttpMisto

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

GPS = {"latitude": 42.8330000, "longitude": 13.9250000, "accuracy": 12}

# osserva la Geolocation API VERA senza cambiarla: conta le richieste continue e vieta la richiesta singola;
# la mappa finta impara getSource().setData (come MapLibre), per vedere il punto che si sposta
OSSERVA = r"""
(() => {
  window.__gps = { watch: 0, clear: 0, get: 0, opts: [] };
  const g = navigator.geolocation;
  if (!g) return;
  const vero = g.watchPosition.bind(g);
  const ferma = g.clearWatch.bind(g);
  g.watchPosition = (ok, err, opts) => { window.__gps.watch += 1; window.__gps.opts.push(opts || null); return vero(ok, err, opts); };
  g.clearWatch = (id) => { window.__gps.clear += 1; return ferma(id); };
  g.getCurrentPosition = () => { window.__gps.get += 1; throw new Error('getCurrentPosition non ammesso'); };
})();
window.addEventListener('DOMContentLoaded', () => {
  window.maplibregl.Map.prototype.getSource = function (id) { const s = this.sources[id]; return s ? { setData: (d) => { s.data = d; } } : undefined; };
});
"""


def _finta_errore(codice):
    """La Geolocation API che risponde con un errore (2 non disponibile, 3 tempo scaduto)."""
    return f"""
(() => {{
  const g = navigator.geolocation;
  g.watchPosition = (ok, err) => {{ window.__gps.watch += 1; setTimeout(() => err({{ code: {codice}, message: 'finto' }}), 0); return 7; }};
}})();
"""


def _edificio_riga(m, bid):
    return dict(zip(("lat", "lng", "at", "by"), _q(m, "SELECT latitude, longitude, position_verified_at, "
                                                      "position_verified_by_user_id FROM buildings WHERE id = %s", (bid,))[0]))


def _apri(page, url):
    page.goto(f"{url}/os/#/edifici/mappa")
    page.wait_for_selector("canvas.finta")
    page.wait_for_function("document.querySelector('[data-map-status]').hidden")
    page.wait_for_selector("[data-to-place] summary")


def _gps(page):
    """«+ → Dalla mia posizione» -> punto provvisorio -> indirizzo confermato (ZONA-5)."""
    page.click("[data-zone-add]")
    page.wait_for_selector("[data-zone-add-gps]:not([disabled])")
    page.click("[data-zone-add-gps]")
    page.wait_for_selector("[data-insert-point]:not([hidden])")
    _conferma_indirizzo(page)


def _conferma_indirizzo(page):
    page.wait_for_selector("[data-insert-address][data-address-state='found']")
    page.click("[data-address-confirm]")
    page.wait_for_selector("[data-insert-address][data-address-confirmed='true']")


def _provider_finto(monkeypatch):
    from property import geocode
    geocode.clear_cache()
    monkeypatch.setattr(geocode, "requests", _HttpMisto())
    monkeypatch.setattr(geocode, "MIN_INTERVAL", 0.05)


def _conferma_punto(page, lng, lat):
    page.evaluate(f"(() => {{ const k = window.__ml.markers.filter((k) => k.el.isConnected)[0]; k.setLngLat([{lng}, {lat}]); k.fire('dragend'); }})()")
    page.evaluate(f"{MAPPA}.setZoom(18)")
    # SENTINELLA AGGIORNATA DA STIMA360 ZONA-5: il punto spostato fa decadere l'indirizzo confermato (nessuna
    # nuova ricerca da sola): lo si riconferma, poi si conferma il punto
    if page.evaluate("(() => { const a = document.querySelector('[data-insert-address]'); return !!a && !a.hidden && a.dataset.addressConfirmed !== 'true'; })()"):
        page.click("[data-address-confirm]")
        page.wait_for_selector("[data-insert-address][data-address-confirmed='true']")
    page.click("[data-insert-confirm]")
    page.wait_for_selector("[data-insert-actions]:not([hidden])")


def _wizard_edificio(page):
    page.click("[data-insert-action='building']")
    page.wait_for_selector("[data-insert-sheet][open] [data-wizard-step='1']")
    page.select_option("#wz-city", "Alba Adriatica")
    page.fill("#wz-address", "Via GPS")
    page.fill("#wz-civic", "1")
    page.fill("#wz-declared", "3")
    page.click("[data-wizard-step='1'] [data-next]")
    page.wait_for_selector("[data-wizard-step='2']")
    page.click("[data-path='building']")
    page.wait_for_selector("[data-wizard-step='3'] [data-create-building]")
    page.click("[data-create-building]")
    page.wait_for_selector("[data-wizard-step='4'] [data-save-building]")
    page.click("[data-save-building]")
    page.wait_for_selector("[data-ready-building][data-ready-state='ok']")
    return int(page.get_attribute("[data-ready-building]", "data-ready-building"))


@pytest.mark.parametrize("larghezza", [320, 390, 1280])
def test_zona6_posizione_in_tempo_reale_dalla_mia_posizione_edificio_immobile_e_collegamenti(server, larghezza, monkeypatch):
    from playwright.sync_api import sync_playwright
    m = server
    _provider_finto(monkeypatch)
    url = m["url"]
    esistente_b = _edificio(m, city="Alba Adriatica", address="Via Esistente GPS", civic_number="2", name="Palazzina GPS")
    esistente_p = _immobile(m, title="Villa GPS Esistente", address="Via Villa GPS")
    richieste = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": larghezza, "height": 820}, geolocation=GPS, permissions=["geolocation"])
        page = context.new_page()
        errori = []
        page.on("pageerror", lambda e: errori.append(str(e)))
        page.on("request", lambda r: richieste.append((r.method, r.url, r.post_data)) if r.method in ("POST", "PATCH", "PUT", "DELETE") else None)
        page.add_init_script(FINTA)
        page.add_init_script(OSSERVA)

        # 1. apertura: UNA richiesta continua, punto blu, mappa centrata alla prima posizione
        _apri(page, url)
        page.wait_for_function(f"!!{MAPPA}.sources['stima-gps']")
        apertura = page.evaluate(f"""() => {{ const piu = document.querySelector('[data-zone-add]').getBoundingClientRect();
            const mappa = document.querySelector('[data-map]').getBoundingClientRect();
            const filtri = [...document.querySelectorAll('[data-zone-filter]')];
            return {{ watch: window.__gps.watch, get: window.__gps.get, opts: window.__gps.opts[0], centro: {MAPPA}.jump && {MAPPA}.jump.center,
                     zoom: {MAPPA}.getZoom(), punto: {MAPPA}.sources['stima-gps'].data.features[1].geometry.coordinates,
                     vecchio: !!document.querySelector('[data-gps-button]'), statoNascosto: document.querySelector('[data-gps-status]').hidden,
                     piu: {{ h: piu.height, w: piu.width, destra: mappa.right - piu.right, sotto: mappa.bottom - piu.bottom }},
                     filtri: filtri.map((b) => b.dataset.zoneFilter), toccoFiltri: Math.min(...filtri.map((b) => b.getBoundingClientRect().height)),
                     scroll: document.documentElement.scrollWidth <= innerWidth }}; }}""")
        # ci si sposta dopo aver trascinato la mappa: il punto segue, la vista resta
        page.evaluate(f"{MAPPA}.fire('dragstart', {{ originalEvent: {{ type: 'touchstart' }} }}); {MAPPA}.jump = null;")
        context.set_geolocation({"latitude": 42.8340000, "longitude": 13.9260000, "accuracy": 8})
        page.wait_for_function(f"{MAPPA}.sources['stima-gps'].data.features[1].geometry.coordinates[0] === 13.926")
        spostato = page.evaluate(f"() => ({{ jump: {MAPPA}.jump || null, watch: window.__gps.watch }})")
        assert richieste == []

        # 2. «+ → Dalla mia posizione» -> provvisorio (posizione ATTUALE) -> correzione -> conferma; 3a. nuovo edificio
        page.click("[data-zone-add]")
        page.wait_for_selector("[data-zone-add-gps]:not([disabled])")
        menu = page.evaluate("""() => ({ voci: [...document.querySelectorAll('[data-zone-add-menu] [role=menuitem] strong')].map((e) => e.textContent),
                                        nota: document.querySelector('[data-zone-add-gps] span').textContent,
                                        tocco: Math.min(...[...document.querySelectorAll('[data-zone-add-menu] [role=menuitem]')].map((b) => b.getBoundingClientRect().height)),
                                        scroll: document.documentElement.scrollWidth <= innerWidth })""")
        page.click("[data-zone-add-gps]")
        page.wait_for_selector("[data-insert-point]:not([hidden])")
        provvisorio = page.evaluate("""() => ({ p: window.__ml.markers.filter((k) => k.el.isConnected)[0].p, testo: document.querySelector('[data-insert-point-text]').textContent,
                                                 hint: document.querySelector('[data-insert-hint]').textContent })""")
        _conferma_indirizzo(page)
        _conferma_punto(page, 13.9252345, 42.8332345)
        bid = _wizard_edificio(page)
        page.click("[data-ready-map]")
        page.wait_for_function(f"location.hash === '#/edifici/mappa/edificio/{bid}'")
        page.wait_for_selector(f"[data-building-panel='{bid}']")
        edificio = _edificio_riga(m, bid)

        # 3b. di nuovo dalla mia posizione -> «Nuovo immobile» indipendente -> verificato (103)
        _gps(page)
        _conferma_punto(page, 13.9253456, 42.8333456)
        page.click("[data-insert-action='unit']")
        page.wait_for_selector("[data-insert-sheet][open] [data-wizard-step='1']")
        page.select_option("#wz-city", "Alba Adriatica")
        page.check("#wz-unknown")
        page.click("[data-wizard-step='1'] [data-next]")
        page.wait_for_selector("[data-wizard-step='2']")
        page.click("[data-path='autonomous']")
        page.wait_for_selector("[data-unit-form]")
        page.click("[data-unit-form] [data-submit]")
        page.wait_for_function("location.hash.startsWith('#/edifici/mappa/immobile/')")
        pid = int(page.evaluate("location.hash.split('/').pop()"))
        page.wait_for_selector(f"[data-property-panel='{pid}']")
        immobile = _riga(m, pid)

        # 3c. collega un edificio esistente; poi un immobile indipendente esistente
        _gps(page)
        _conferma_punto(page, 13.9254567, 42.8334567)
        page.click("[data-insert-action='link']")
        page.fill("[data-insert-link-input]", "Esistente GPS")
        page.click("[data-insert-link-submit]")
        page.wait_for_selector(f"[data-insert-link-building='{esistente_b['id']}']")
        page.click(f"[data-insert-link-building='{esistente_b['id']}']")
        page.wait_for_function(f"location.hash === '#/edifici/mappa/edificio/{esistente_b['id']}'")
        page.wait_for_selector(f"[data-building-panel='{esistente_b['id']}']")
        _gps(page)
        _conferma_punto(page, 13.9255678, 42.8335678)
        page.click("[data-insert-action='link-property']")
        page.fill("[data-insert-link-input]", "Villa GPS")
        page.click("[data-insert-link-submit]")
        page.wait_for_selector(f"[data-insert-link-property='{esistente_p['id']}']")
        page.click(f"[data-insert-link-property='{esistente_p['id']}']")
        page.wait_for_function(f"location.hash === '#/edifici/mappa/immobile/{esistente_p['id']}'")
        page.wait_for_selector(f"[data-property-panel='{esistente_p['id']}']")
        prima_di_uscire = page.evaluate("() => ({ watch: window.__gps.watch, clear: window.__gps.clear })")
        # 4. uscita da Zona: il GPS si ferma
        page.evaluate("location.hash = '#/edifici'")
        page.wait_for_function("window.__gps.clear === " + str(prima_di_uscire["clear"] + 1))
        fine = page.evaluate("() => ({ get: window.__gps.get, scroll: document.documentElement.scrollWidth <= innerWidth })")
        browser.close()

    assert errori == [], errori
    a = apertura
    # ogni riapertura della mappa (dopo un salvataggio) e' una nuova richiesta continua, e la precedente si ferma
    assert a["watch"] == 1 and a["get"] == 0 and a["opts"] == {"enableHighAccuracy": True, "timeout": 15000, "maximumAge": 0}
    assert a["centro"] == [13.925, 42.833] and a["zoom"] == 18 and a["punto"] == [13.925, 42.833]
    assert a["vecchio"] is False and a["statoNascosto"] is True and a["scroll"] is True
    # (misure in sub-pixel: 43.99997 e' 44)
    assert a["piu"]["h"] >= 43.5 and a["piu"]["w"] >= 43.5 and 0 <= a["piu"]["destra"] <= 24 and 0 <= a["piu"]["sotto"] <= 80
    assert a["filtri"] == ["all", "verify", "recontact"] and a["toccoFiltri"] >= 43.5
    assert spostato == {"jump": None, "watch": 1}                                                      # il punto si sposta, la vista no
    assert menu["voci"] == ["Dalla mia posizione", "Scegli un punto sulla mappa", "Cerca un indirizzo"]
    assert "circa 8 m" in menu["nota"] and menu["tocco"] >= 43.5 and menu["scroll"] is True
    assert provvisorio["p"] == [13.926, 42.834] and provvisorio["testo"] == "Punto provvisorio: 42.83400, 13.92600"
    assert "Punto dal GPS" in provvisorio["hint"] and "circa 8 m" in provvisorio["hint"]
    # il punto salvato e' quello CORRETTO dall'operatore, mai la posizione GPS grezza
    assert str(edificio["lat"]) == "42.8332345" and str(edificio["lng"]) == "13.9252345" and edificio["at"] is not None and edificio["by"] == m["ids"]["owner_a"]
    assert str(immobile["lat"]) == "42.8333456" and str(immobile["lng"]) == "13.9253456" and immobile["at"] is not None and immobile["building"] is None
    eb = _edificio_riga(m, esistente_b["id"])
    assert str(eb["lat"]) == "42.8334567" and eb["at"] is not None
    ep = _riga(m, esistente_p["id"])
    assert str(ep["lat"]) == "42.8335678" and ep["at"] is not None
    corpi = [json.loads(c[2]) for c in richieste if c[1].endswith("/position")]
    assert all(c["latitude"] not in ("42.8330000", "42.8340000") for c in corpi)                       # mai le coordinate GPS grezze
    assert [c[1].split("/api/property/")[1] for c in richieste if c[1].endswith("/position")] == [
        f"buildings/{bid}/position", f"properties/{pid}/position", f"buildings/{esistente_b['id']}/position", f"properties/{esistente_p['id']}/position"]
    # ogni mappa aperta ha la sua richiesta continua; tutte quelle chiuse sono state fermate
    assert prima_di_uscire["watch"] == prima_di_uscire["clear"] + 1
    assert fine == {"get": 0, "scroll": True}


def test_zona6_permesso_negato_non_disponibile_e_tempo_scaduto(server):
    from playwright.sync_api import sync_playwright
    url = server["url"]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        esiti = {}
        leggi = f"""() => ({{ testo: document.querySelector('[data-gps-status]').textContent, stato: document.querySelector('[data-gps-status]').dataset.gpsState,
                             watch: window.__gps.watch, clear: window.__gps.clear, sorgente: !!{MAPPA}.sources['stima-gps'],
                             spento: document.querySelector('[data-zone-add-gps]').disabled }})"""
        # permesso NEGATO: nessun permesso concesso al contesto -> la Geolocation API vera rifiuta
        context = browser.new_context(viewport={"width": 390, "height": 820})
        page = context.new_page()
        page.add_init_script(FINTA)
        page.add_init_script(OSSERVA)
        _apri(page, url)
        page.wait_for_selector("[data-gps-status][data-gps-state='error']")
        esiti["negato"] = page.evaluate(leggi)
        context.close()
        # non disponibile (2) e tempo scaduto (3): la risposta della API, simulata
        for codice, nome in ((2, "nonDisponibile"), (3, "scaduto")):
            context = browser.new_context(viewport={"width": 390, "height": 820}, geolocation=GPS, permissions=["geolocation"])
            page = context.new_page()
            page.add_init_script(FINTA)
            page.add_init_script(OSSERVA)
            page.add_init_script(_finta_errore(codice))
            _apri(page, url)
            page.wait_for_selector("[data-gps-status][data-gps-state='error']")
            esiti[nome] = page.evaluate(leggi)
            context.close()
        browser.close()
    assert esiti["negato"]["testo"].startswith("Permesso negato") and esiti["negato"]["watch"] == 1 and esiti["negato"]["clear"] == 1
    assert esiti["nonDisponibile"]["testo"].startswith("Posizione non disponibile") and esiti["nonDisponibile"]["clear"] == 0
    assert esiti["scaduto"]["testo"].startswith("Tempo scaduto")
    for e in esiti.values():
        assert e["stato"] == "error" and e["sorgente"] is False and e["spento"] is True
