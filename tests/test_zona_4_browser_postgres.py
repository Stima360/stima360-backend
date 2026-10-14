"""STIMA360 ZONA-4 - LA CAMPAGNA in browser (Chromium, GPS SIMULATO dal browser
stesso) con backend e PostgreSQL VERI (schema completo, 102 e 103 comprese;
database locale usa-e-getta). Il disegno della mappa e' simulato (MapLibre
finta di T4); la Geolocation API e' quella VERA di Chromium, con la posizione e
il permesso impostati da Playwright, e viene osservata (contata) da uno script
che non ne cambia il comportamento.

  1. nessuna richiesta GPS all'apertura di Zona;
  2. tocco su «La mia posizione» -> UNA richiesta (il secondo tocco in attesa
     non ne fa un'altra), mappa centrata sul punto ricevuto, cerchio di
     precisione e punto disegnati, «Precisione stimata: circa 12 m»;
  3. «Inserisci qui» -> punto provvisorio nel pannello -> correzione manuale
     (trascinamento) -> conferma (zoom >= 17);
  4. «Nuovo edificio» -> procedura esistente -> posizione VERIFICATA con il
     punto CORRETTO (102); poi di nuovo GPS -> «Nuovo immobile» indipendente ->
     posizione verificata (103); poi GPS -> collega un edificio esistente e un
     immobile indipendente esistente;
  5. mai `watchPosition`, nessuna richiesta oltre ai tocchi, nessuna coordinata
     GPS inviata al backend (al backend vanno SOLO i punti confermati);
  6. a 320, 390 e 1280 px: pulsante >= 44 px, dentro la mappa, in alto a
     sinistra (lo zoom e' a destra), nessuno scroll orizzontale;
  7. permesso negato, posizione non disponibile, tempo scaduto: messaggi
     leggibili e nessun indicatore.
"""
from __future__ import annotations

import json

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _edificio, _q  # noqa: F401
from tests.test_censimento_3_backend_postgres import completo, mondo  # noqa: F401  (fixture)
from tests.test_edifici_1_browser_postgres import server  # noqa: F401  (fixture)
from tests.test_territorio_4_browser_postgres import FINTA, MAPPA
from tests.test_zona_3_browser_postgres import _immobile, _riga

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

GPS = {"latitude": 42.8330000, "longitude": 13.9250000, "accuracy": 12}

# osserva la Geolocation API VERA senza cambiarla: conta le richieste e vieta il watch
OSSERVA = r"""
(() => {
  window.__gps = { get: 0, watch: 0, opts: [] };
  const g = navigator.geolocation;
  if (!g) return;
  const vero = g.getCurrentPosition.bind(g);
  g.getCurrentPosition = (ok, err, opts) => { window.__gps.get += 1; window.__gps.opts.push(opts || null); return vero(ok, err, opts); };
  g.watchPosition = () => { window.__gps.watch += 1; throw new Error('watchPosition non ammesso'); };
})();
"""


def _finta_errore(codice):
    """La Geolocation API che risponde con un errore (2 non disponibile, 3 tempo scaduto)."""
    return f"""
(() => {{
  const g = navigator.geolocation;
  g.getCurrentPosition = (ok, err) => {{ window.__gps.get += 1; setTimeout(() => err({{ code: {codice}, message: 'finto' }}), 0); }};
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
    """Tocco -> posizione ricevuta -> «Inserisci qui» -> punto provvisorio."""
    page.click("[data-gps-button]")
    page.wait_for_selector("[data-gps-status][data-gps-state='ready']")
    page.click("[data-gps-insert]")
    page.wait_for_selector("[data-insert-point]:not([hidden])")


def _conferma_punto(page, lng, lat):
    page.evaluate(f"(() => {{ const k = window.__ml.markers.filter((k) => k.el.isConnected)[0]; k.setLngLat([{lng}, {lat}]); k.fire('dragend'); }})()")
    page.evaluate(f"{MAPPA}.setZoom(18)")
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
def test_zona4_gps_inserisci_qui_edificio_immobile_e_collegamenti(server, larghezza):
    from playwright.sync_api import sync_playwright
    m = server
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

        # 1. apertura: nessuna richiesta; il pulsante e' nella mappa, in alto a sinistra, >= 44 px
        _apri(page, url)
        apertura = page.evaluate("""() => { const b = document.querySelector('[data-gps-button]'); const r = b.getBoundingClientRect();
            const mappa = document.querySelector('[data-map]').getBoundingClientRect();
            return { get: window.__gps.get, watch: window.__gps.watch, h: r.height, w: r.width, testo: b.textContent.trim(),
                     etichetta: b.getAttribute('aria-label'), sinistra: r.left - mappa.left, sopra: r.top - mappa.top,
                     dentro: r.right <= mappa.right - 56 && r.bottom <= mappa.bottom,      // lo zoom (in alto a destra) resta libero
                     statoNascosto: document.querySelector('[data-gps-status]').hidden,
                     scroll: document.documentElement.scrollWidth <= innerWidth }; }""")

        # 2. tocco (doppio: UNA richiesta), mappa centrata, indicatore, precisione
        # due tocchi di fila, nello stesso istante (prima che la risposta arrivi): UNA richiesta
        page.evaluate("(() => { const b = document.querySelector('[data-gps-button]'); b.click(); b.click(); })()")
        page.wait_for_selector("[data-gps-status][data-gps-state='ready']")
        ricevuta = page.evaluate(f"""() => ({{ get: window.__gps.get, opts: window.__gps.opts[0], centro: {MAPPA}.jump && {MAPPA}.jump.center, zoom: {MAPPA}.getZoom(),
            sorgente: {MAPPA}.sources['stima-gps'].data.features.map((f) => f.geometry.type), testo: document.querySelector('[data-gps-text]').textContent,
            scroll: document.documentElement.scrollWidth <= innerWidth,
            tocco: Math.min(...[...document.querySelectorAll('[data-gps-insert], [data-gps-close]')].map((b) => b.getBoundingClientRect().height)) }})""")
        assert richieste == []

        # 3. «Inserisci qui» -> provvisorio -> correzione -> conferma; 4a. nuovo edificio verificato col punto CORRETTO
        page.click("[data-gps-insert]")
        page.wait_for_selector("[data-insert-point]:not([hidden])")
        provvisorio = page.evaluate("""() => ({ p: window.__ml.markers.filter((k) => k.el.isConnected)[0].p, testo: document.querySelector('[data-insert-point-text]').textContent,
                                                 hint: document.querySelector('[data-insert-hint]').textContent, gpsNascosto: document.querySelector('[data-gps-status]').hidden })""")
        _conferma_punto(page, 13.9252345, 42.8332345)
        bid = _wizard_edificio(page)
        page.click("[data-ready-map]")
        page.wait_for_function(f"location.hash === '#/edifici/mappa/edificio/{bid}'")
        page.wait_for_selector(f"[data-building-panel='{bid}']")
        edificio = _edificio_riga(m, bid)

        # 4b. di nuovo GPS -> «Nuovo immobile» indipendente -> verificato (103)
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

        # 4c. GPS -> collega un edificio esistente; GPS -> collega un immobile indipendente esistente
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
        fine = page.evaluate("() => ({ get: window.__gps.get, watch: window.__gps.watch, scroll: document.documentElement.scrollWidth <= innerWidth })")
        browser.close()

    assert errori == [], errori
    a = apertura
    assert a["get"] == 0 and a["watch"] == 0 and a["statoNascosto"] is True and a["scroll"] is True
    # (misure in sub-pixel: 43.99997 e' 44)
    assert a["h"] >= 43.5 and a["w"] >= 43.5 and a["testo"] == "La mia posizione" and a["etichetta"] == "La mia posizione"
    assert 0 <= a["sinistra"] <= 24 and 0 <= a["sopra"] <= 24 and a["dentro"] is True        # in alto a sinistra, lontano dallo zoom
    r = ricevuta
    assert r["get"] == 1 and r["opts"] == {"enableHighAccuracy": True, "timeout": 15000, "maximumAge": 0}
    assert r["centro"] == [13.925, 42.833] and r["zoom"] == 18 and r["sorgente"] == ["Polygon", "Point"]
    assert r["testo"].startswith("Precisione stimata: circa 12 m") and r["scroll"] is True and r["tocco"] >= 43.5
    assert provvisorio["p"] == [13.925, 42.833] and provvisorio["testo"] == "Punto provvisorio: 42.83300, 13.92500"
    assert "Punto dal GPS" in provvisorio["hint"] and "circa 12 m" in provvisorio["hint"] and provvisorio["gpsNascosto"] is True
    # il punto salvato e' quello CORRETTO dall'operatore, mai la posizione GPS grezza
    assert str(edificio["lat"]) == "42.8332345" and str(edificio["lng"]) == "13.9252345" and edificio["at"] is not None and edificio["by"] == m["ids"]["owner_a"]
    assert str(immobile["lat"]) == "42.8333456" and str(immobile["lng"]) == "13.9253456" and immobile["at"] is not None and immobile["building"] is None
    eb = _edificio_riga(m, esistente_b["id"])
    assert str(eb["lat"]) == "42.8334567" and eb["at"] is not None
    ep = _riga(m, esistente_p["id"])
    assert str(ep["lat"]) == "42.8335678" and ep["at"] is not None
    corpi = [json.loads(c[2]) for c in richieste if c[1].endswith("/position")]
    assert all(c["latitude"] != "42.8330000" and c["longitude"] != "13.9250000" for c in corpi)   # mai le coordinate GPS grezze
    assert [c[1].split("/api/property/")[1] for c in richieste if c[1].endswith("/position")] == [
        f"buildings/{bid}/position", f"properties/{pid}/position", f"buildings/{esistente_b['id']}/position", f"properties/{esistente_p['id']}/position"]
    assert fine == {"get": 4, "watch": 0, "scroll": True}                                           # quattro tocchi, quattro richieste, nessun watch


def test_zona4_permesso_negato_non_disponibile_e_tempo_scaduto(server):
    from playwright.sync_api import sync_playwright
    url = server["url"]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        esiti = {}
        # permesso NEGATO: nessun permesso concesso al contesto -> la Geolocation API vera rifiuta
        context = browser.new_context(viewport={"width": 390, "height": 820})
        page = context.new_page()
        page.add_init_script(FINTA)
        page.add_init_script(OSSERVA)
        _apri(page, url)
        page.click("[data-gps-button]")
        page.wait_for_selector("[data-gps-status][data-gps-state='error']")
        esiti["negato"] = page.evaluate(f"() => ({{ testo: document.querySelector('[data-gps-text]').textContent, get: window.__gps.get, sorgente: !!{MAPPA}.sources['stima-gps'], attivo: !document.querySelector('[data-gps-button]').disabled }})")
        context.close()
        # non disponibile (2) e tempo scaduto (3): la risposta della API, simulata
        for codice, nome in ((2, "nonDisponibile"), (3, "scaduto")):
            context = browser.new_context(viewport={"width": 390, "height": 820}, geolocation=GPS, permissions=["geolocation"])
            page = context.new_page()
            page.add_init_script(FINTA)
            page.add_init_script(OSSERVA)
            page.add_init_script(_finta_errore(codice))
            _apri(page, url)
            page.click("[data-gps-button]")
            page.wait_for_selector("[data-gps-status][data-gps-state='error']")
            esiti[nome] = page.evaluate(f"() => ({{ testo: document.querySelector('[data-gps-text]').textContent, get: window.__gps.get, sorgente: !!{MAPPA}.sources['stima-gps'], attivo: !document.querySelector('[data-gps-button]').disabled }})")
            # si puo' riprovare: un altro tocco = un'altra richiesta
            page.click("[data-gps-button]")
            page.wait_for_function("window.__gps.get === 2")
            context.close()
        browser.close()
    assert esiti["negato"]["testo"].startswith("Permesso negato") and esiti["negato"]["get"] == 1 and esiti["negato"]["sorgente"] is False
    assert esiti["nonDisponibile"]["testo"].startswith("Posizione non disponibile") and esiti["nonDisponibile"]["sorgente"] is False
    assert esiti["scaduto"]["testo"].startswith("Tempo scaduto") and esiti["scaduto"]["sorgente"] is False
    assert all(e["attivo"] is True for e in esiti.values())
