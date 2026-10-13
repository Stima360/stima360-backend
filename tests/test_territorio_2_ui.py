"""STIMA360 TERRITORIO T2 - la mappa stradale `#/edifici/mappa`. Shell, nessun database.

  A  statici: versione fissa + SRI, stile OpenFreeMap `positron`, nessuna
     chiamata alle API del CRM, nessuna geolocalizzazione, nessun marker, nessuna
     voce nuova nella barra laterale; node --check;
  B  il caricatore (node, documento minimo): una sola richiesta anche con due
     chiamate, errore -> tag tolto e «Riprova» ricomincia, libreria gia'
     presente -> nessuna richiesta, tempo scaduto;
  C  shell sullo stub DOM con una MapLibre finta: la rotta crea UNA mappa
     (stile, centro, attribuzione estesa, zoom), nessuna API del CRM; uscendo la
     mappa viene distrutta e Edifici / scheda edificio si aprono come prima;
     errore della cartografia prima del primo disegno -> messaggio e «Riprova»;
  D  Chromium (Playwright, rete simulata): CDN irraggiungibile -> messaggio e
     «Riprova» (che rifa' UNA richiesta), mappa alta e senza sforamento a 360 e
     1280 px.
"""
from __future__ import annotations

import json
import re
import subprocess

import pytest

from tests import test_a30_5_create_ui as a30_5
from tests import test_a30_13b_quick_booking_ui as a30_13b
from tests import test_delete_arch_2b3_ui as t2b3
from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)

node = t2b3.node
ASSETS = t2b3.ASSETS
LOADER = ASSETS / "territorio" / "maplibre-loader.js"
VISTA = ASSETS / "views" / "territorio.js"
rt = a30_5._rt()
_codice = t2b3._codice

UNPKG_JS = "https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.js"
UNPKG_CSS = "https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.css"
STILE = "https://tiles.openfreemap.org/styles/positron"


# ---------------------------------------------------------------------------
# A - statici
# ---------------------------------------------------------------------------

def test_a01_versione_fissa_sri_e_stile_positron():
    caricatore = LOADER.read_text(encoding="utf-8")
    assert "MAPLIBRE_VERSION = '5.24.0'" in caricatore
    assert "https://unpkg.com/maplibre-gl@${MAPLIBRE_VERSION}/dist/maplibre-gl.js" in caricatore
    assert len(re.findall(r"'sha384-[A-Za-z0-9+/]{64}'", caricatore)) == 2
    assert caricatore.count("crossOrigin = 'anonymous'") == 2
    vista = VISTA.read_text(encoding="utf-8")
    assert f"MAP_STYLE_URL = '{STILE}'" in vista
    assert "attributionControl: { compact: false }" in vista


def test_a02_solo_cartografia_nessun_dato_del_crm():
    for file in (LOADER, VISTA):
        testo = _codice(file)
        for vietata in ("/api/", "apiGet", "apiPost", "apiPatch", "fetch(", "geolocation", "GeolocateControl",
                        "Marker", "latitude", "longitude", "localStorage", "sessionStorage", "key=", "apikey"):
            assert vietata not in testo, (file.name, vietata)
    # un solo dominio di rete per i dati della mappa, uno per la libreria
    domini = set(re.findall(r"https://([a-z0-9.-]+)/", LOADER.read_text(encoding="utf-8") + VISTA.read_text(encoding="utf-8")))
    assert domini == {"unpkg.com", "tiles.openfreemap.org"}, domini


def test_a03_main_js_rotta_dentro_edifici_nessuna_voce_nuova():
    main = (ASSETS / "main.js").read_text(encoding="utf-8")
    assert main.count("import { renderTerritorio } from './views/territorio.js';") == 1
    assert "if (params[0] === 'mappa') return renderTerritorio(container);" in main
    assert main.count("registerRoute('edifici'") == 1
    sezioni = main[main.index("const SECTIONS = ["):main.index("];", main.index("const SECTIONS = ["))]
    # SENTINELLA AGGIORNATA DA STIMA360 TERRITORIO ZONA: la sidebar ha ORA la voce «Zona» (name 'zona'), che
    # apre la STESSA mappa (target ['edifici', 'mappa']): nessuna voce 'mappa'/'territorio' e nessun altro
    # `target`; la mappa resta dentro Edifici (#/edifici/mappa), nessuna seconda mappa.
    nomi = re.findall(r"name:\s*'([a-z]+)'", sezioni)
    assert "mappa" not in nomi and "territorio" not in nomi
    assert sezioni.count("target:") == 1 and "{ name: 'zona', label: 'Zona', target: ['edifici', 'mappa'] }" in sezioni


@node
def test_a04_node_check():
    for file in (LOADER, VISTA, ASSETS / "main.js"):
        esito = subprocess.run([a30_5.NODE, "--check", str(file)], capture_output=True, text=True, timeout=30)
        assert esito.returncode == 0, (file.name, esito.stderr)


# ---------------------------------------------------------------------------
# B - il caricatore
# ---------------------------------------------------------------------------

DOC_MINIMO = r"""
const head = { children: [], appendChild(n) { this.children.push(n); } };
globalThis.document = {
  head,
  querySelector(sel) {
    return head.children.find((n) => sel.includes('maplibre-css') ? n.tag === 'link' : n.tag === 'script') || null;
  },
  createElement(tag) {
    const n = { tag, attrs: {}, ls: {},
      setAttribute(k, v) { this.attrs[k] = v; },
      addEventListener(t, f) { this.ls[t] = f; },
      remove() { const i = head.children.indexOf(this); if (i >= 0) head.children.splice(i, 1); } };
    return n;
  },
};
const scripts = () => head.children.filter((n) => n.tag === 'script');
const links = () => head.children.filter((n) => n.tag === 'link');
const esito = (p) => p.then(() => 'ok', (e) => 'ko: ' + e.message);
"""


@node
def test_b01_una_sola_richiesta_errore_riprova_e_tempo_scaduto():
    script = DOC_MINIMO + f"import * as m from '{LOADER.as_posix()}';\n" + r"""
const p1 = m.loadMapLibre(); const p2 = m.loadMapLibre();
const stessa = p1 === p2;
const s1 = scripts()[0];
const primo = { scripts: scripts().length, links: links().length, src: s1.src, integrity: s1.integrity,
                cors: s1.crossOrigin, css: links()[0].href, cssCors: links()[0].crossOrigin };
s1.ls.error();
const e1 = await esito(p1);
const dopoErrore = scripts().length;
const p3 = m.loadMapLibre();
const s3 = scripts()[0];
globalThis.maplibregl = { Map: function () {} };
s3.ls.load();
const e3 = await esito(p3);
const p4 = m.loadMapLibre();
const e4 = await esito(p4);
const finale = { scripts: scripts().length, links: links().length };
delete globalThis.maplibregl;
head.children.length = 0;
const e5 = await esito(m.loadMapLibre({ timeoutMs: 5 }));
console.log(JSON.stringify({ stessa, primo, e1, dopoErrore, e3, e4, finale, e5, rimasti: scripts().length }));
"""
    esito = subprocess.run([a30_5.NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                           timeout=30, env={"PATH": "/usr/bin:/bin"})
    assert esito.returncode == 0, esito.stderr
    out = json.loads(esito.stdout.strip().splitlines()[-1])
    assert out["stessa"] is True
    assert out["primo"]["scripts"] == 1 and out["primo"]["links"] == 1
    assert out["primo"]["src"] == UNPKG_JS and out["primo"]["css"] == UNPKG_CSS
    assert out["primo"]["integrity"].startswith("sha384-") and out["primo"]["cors"] == "anonymous"
    assert out["primo"]["cssCors"] == "anonymous"
    assert out["e1"].startswith("ko:") and out["dopoErrore"] == 0          # tag tolto: si puo' riprovare
    assert out["e3"] == "ok" and out["e4"] == "ok"
    assert out["finale"] == {"scripts": 1, "links": 1}                     # nessun secondo caricamento
    assert out["e5"].startswith("ko: Tempo scaduto") and out["rimasti"] == 0


# ---------------------------------------------------------------------------
# C - shell sullo stub DOM, con una MapLibre finta
# ---------------------------------------------------------------------------

# Il browser vero tiene piu' ascoltatori per evento e sa toglierli; lo stub di
# P26-4 ne tiene uno solo. Qui, e SOLO in questo driver, come il browser.
PRELUDIO = r"""
// main.js (Aurora Glass) disegna le icone del menu in SVG: lo stub non ha createElementNS.
document.createElementNS = (_ns, tag) => document.createElement(tag);
window._ls = {};
window.addEventListener = function (t, fn) { (this._ls[t] ||= []).push(fn); };
window.removeEventListener = function (t, fn) { this._ls[t] = (this._ls[t] || []).filter((f) => f !== fn); };
window._fire = async function (t) { for (const fn of [...(this._ls[t] || [])]) await fn(); };
globalThis.__maps = [];
globalThis.maplibregl = {
  Map: class {
    constructor(o) { this.o = o; this.h = {}; this.controls = []; this.removed = false; __maps.push(this); }
    on(e, f) { (this.h[e] ||= []).push(f); }
    addControl(c, p) { this.controls.push([c.kind, p, c.o]); }
    // SENTINELLA AGGIORNATA DA TERRITORIO T3: i metodi che il livello degli edifici usa
    addSource() {} addLayer() {} setFeatureState() {} fitBounds() {} jumpTo() {}
    getCanvas() { return { style: {} }; }
    // SENTINELLA AGGIORNATA DA STIMA360 TERRITORIO ZONA-2: lo zoom (la regola di conferma del punto)
    getZoom() { return this.o && Number.isFinite(this.o.zoom) ? this.o.zoom : 14; }
    remove() { this.removed = true; }
    emit(e) { for (const f of this.h[e] || []) f({}); }
  },
  NavigationControl: class { constructor(o) { this.kind = 'navigation'; this.o = o; } },
  Popup: class { setLngLat() { return this; } setDOMContent() { return this; } addTo() { return this; } on() { return this; } remove() {} },
};
"""

HELPERS = r"""
const stato = () => { const s = C().querySelector('[data-map-status]');
  return s ? { hidden: !!s.hidden, testo: C().querySelector('[data-map-status-text]').textContent, riprova: !C().querySelector('[data-map-retry]').hidden } : null; };
async function vai(h) { window.location.hash = h; await window._fire('hashchange'); await wait(); await wait(); }
"""


def _rotte(extra=()):
    voci = [("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione("agency_owner"))]),
            *extra,
            ("GET", "/api/property/form-options", [rt.ok({"territory": [], "building_types": []})]),
            ("GET", "/api/", [rt.ok({"items": [], "total": 0})])]
    return PRELUDIO + "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});" for m, p, r in voci)


def _run(staged, scenario, hash="#/edifici/mappa", extra=()):  # noqa: F811
    return t2b3._run(staged, HELPERS + scenario, _rotte(extra), hash)


# Le richieste della Shell all'avvio (sessione, voce «Voce» del menu) non sono della mappa.
_AVVIO = ("/api/operator-auth/", "/api/voice/status")


def _crm(out):
    return [c["url"] for c in out["calls"] if c["url"].startswith("/api/") and not c["url"].startswith(_AVVIO)]


@node
def test_c01_la_rotta_crea_una_mappa_e_non_chiama_il_crm(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const prima = stato();
      const m = __maps[0];
      const opzioni = { n: __maps.length, style: m.o.style, center: m.o.center, zoom: m.o.zoom,
                        attribuzione: m.o.attributionControl, contenitore: m.o.container === C().querySelector('[data-map]') && !!m.o.container,
                        controlli: m.controls.map(([k, p, o]) => [k, p, o]) };
      m.emit('load'); await wait();
      report({ prima, opzioni, dopo: stato(), titolo: __dom.byId['page-title'].textContent,
               indietro: C().querySelector('[data-back-to-buildings]').getAttribute('href') });  // T3: «Torna agli edifici»
    """
    out = _run(staged, scenario)
    assert out["prima"] == {"hidden": False, "testo": "Caricamento della mappa…", "riprova": False}
    o = out["opzioni"]
    assert o["n"] == 1 and o["style"] == STILE and o["contenitore"] is True
    assert o["center"] == [13.9258, 42.8337] and o["zoom"] == 14
    assert o["attribuzione"] == {"compact": False}
    assert o["controlli"] == [["navigation", "top-right", {"showCompass": False}]]
    assert out["dopo"]["hidden"] is True
    # SENTINELLA AGGIORNATA DA STIMA360 TERRITORIO ZONA: sulla mappa il titolo della pagina e' «Zona»
    # (la voce principale che la apre); «indietro» porta sempre all'elenco edifici.
    assert out["titolo"] == "Zona" and out["indietro"] == "#/edifici"
    # SENTINELLA AGGIORNATA DA TERRITORIO T3: la mappa legge ora gli edifici dell'agenzia (solo GET
    # della lista, una pagina qui); nessun'altra chiamata al CRM e nessuna scrittura.
    assert [u.split("?")[0] for u in _crm(out)] == ["/api/property/buildings"], _crm(out)
    assert all(c["m"] == "GET" for c in out["calls"]), out["calls"]


@node
def test_c02_uscendo_la_mappa_si_distrugge_e_edifici_resta_quello_di_prima(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      __maps[0].emit('load');
      await vai('#/edifici');
      const lista = { rimossa: __maps[0].removed, mappe: __maps.length, area: !!C().querySelector('#buildings-area') };
      await vai('#/edifici/mappa');
      const seconda = { mappe: __maps.length, primaRimossa: __maps[0].removed, secondaViva: !__maps[1].removed };
      await vai('#/edifici/9');
      report({ lista, seconda, finale: __maps[1].removed, mappe: __maps.length });
    """
    out = _run(staged, scenario, extra=[("GET", "/api/property/buildings/9", [rt.ok({"id": 9})])])
    assert out["lista"] == {"rimossa": True, "mappe": 1, "area": True}
    assert out["seconda"] == {"mappe": 2, "primaRimossa": True, "secondaViva": True}
    assert out["finale"] is True and out["mappe"] == 2
    urls = _crm(out)
    assert any(u.startswith("/api/property/buildings?") for u in urls), urls       # la lista Edifici
    assert "/api/property/buildings/9" in urls, urls                               # la scheda, come prima


@node
def test_c03_cartografia_che_non_risponde_messaggio_e_riprova(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      __maps[0].emit('error'); await wait();
      const errore = stato();
      C().querySelector('[data-map-retry]').dispatch('click'); await wait(); await wait();
      const dopo = { mappe: __maps.length, primaRimossa: __maps[0].removed, stato: stato() };
      __maps[1].emit('load'); __maps[1].emit('error'); await wait();
      report({ errore, dopo, caricata: stato() });
    """
    out = _run(staged, scenario)
    assert out["errore"]["hidden"] is False and out["errore"]["riprova"] is True
    assert "La cartografia non risponde" in out["errore"]["testo"]
    assert out["dopo"]["mappe"] == 2 and out["dopo"]["primaRimossa"] is True
    assert out["dopo"]["stato"]["testo"] == "Caricamento della mappa…"
    assert out["caricata"]["hidden"] is True                               # dopo il disegno: tessere singole, nessun allarme


# ---------------------------------------------------------------------------
# D - Chromium, rete simulata (nessun accesso a CDN o tessere reali)
# ---------------------------------------------------------------------------

_FINTA = r"""
window.__ml = { create: 0, remove: 0 };
window.maplibregl = {
  Map: class {
    constructor(o) { window.__ml.create++; const c = document.createElement('canvas'); c.className = 'finta';
      c.style.width = '100%'; c.style.height = '100%'; c.style.display = 'block'; o.container.appendChild(c);
      this.h = {}; setTimeout(() => (this.h.load || []).forEach((f) => f({})), 0); }
    on(e, f) { (this.h[e] ||= []).push(f); }
    addControl() {}
    // SENTINELLA AGGIORNATA DA TERRITORIO T3: i metodi che il livello degli edifici usa
    addSource() {} addLayer() {} setFeatureState() {} fitBounds() {} jumpTo() {}
    getCanvas() { return { style: {} }; }
    // SENTINELLA AGGIORNATA DA STIMA360 TERRITORIO ZONA-2: lo zoom (la regola di conferma del punto)
    getZoom() { return this.o && Number.isFinite(this.o.zoom) ? this.o.zoom : 14; }
    remove() { window.__ml.remove++; }
  },
  NavigationControl: class {},
  Popup: class { setLngLat() { return this; } setDOMContent() { return this; } addTo() { return this; } on() { return this; } remove() {} },
};
"""


def _misura(page):
    return page.evaluate("""() => {
      const doc = document.documentElement, m = document.querySelector('[data-map]');
      const r = m ? m.getBoundingClientRect() : null;
      return { scroll: doc.scrollWidth, inner: innerWidth, mappa: r && { w: r.width, h: r.height, right: r.right },
               stato: document.querySelector('[data-map-status]').hidden,
               testo: document.querySelector('[data-map-status-text]').textContent,
               riprova: (() => { const b = document.querySelector('[data-map-retry]');
                                 return b.hidden ? null : b.getBoundingClientRect().height; })(),
               tag: document.querySelectorAll('script[data-maplibre-js]').length };
    }""")


@pytest.mark.parametrize("larghezza", [360, 1280])
def test_d01_chromium_cdn_irraggiungibile_poi_mappa_senza_sforamento(larghezza):
    sync_playwright = t2b3._chromium()
    api = [("/api/operator-auth/me", t2b3._ME), ("/api/property/form-options", {"territory": [], "building_types": []})]
    richieste = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": larghezza, "height": 780})
            t2b3._servi(page, api)

            def cdn(route):
                richieste.append(route.request.url)
                return route.abort()
            page.route("https://unpkg.com/**", cdn)
            page.route("https://tiles.openfreemap.org/**", lambda r: r.abort())
            page.goto("http://stima360.test/os/#/edifici/mappa")
            page.wait_for_selector("[data-map-retry]:not([hidden])")
            errore = _misura(page)
            page.click("[data-map-retry]")
            page.wait_for_function("document.querySelector('[data-map-status-text]').textContent.includes('Impossibile')")
            dopo_riprova = len([u for u in richieste if u.endswith(".js")])
            browser.close()

            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": larghezza, "height": 780})
            t2b3._servi(page, api)
            page.route("https://unpkg.com/**", lambda r: (_ for _ in ()).throw(AssertionError("richiesta al CDN")))
            page.add_init_script(_FINTA)
            page.goto("http://stima360.test/os/#/edifici/mappa")
            page.wait_for_selector("canvas.finta")
            page.wait_for_function("document.querySelector('[data-map-status]').hidden")
            mappa = _misura(page)
            page.evaluate("location.hash = '#/edifici'")
            page.wait_for_selector("#buildings-area")
            vita = page.evaluate("window.__ml")
            browser.close()
    except Exception as exc:  # noqa: BLE001
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium non disponibile: misura NON eseguita (BLOCKED)")
        raise
    assert "Impossibile caricare la mappa" in errore["testo"] and errore["riprova"] >= 44
    assert errore["tag"] == 0 and errore["scroll"] <= errore["inner"]
    assert [u for u in richieste if u.endswith(".js")][:1] == [UNPKG_JS]
    assert dopo_riprova == 2                                               # «Riprova» = una richiesta in piu', non di piu'
    assert mappa["stato"] is True and mappa["scroll"] <= mappa["inner"]
    assert mappa["mappa"]["h"] >= 300 and mappa["mappa"]["right"] <= larghezza
    assert vita == {"create": 1, "remove": 1}
