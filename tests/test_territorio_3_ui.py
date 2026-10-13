"""STIMA360 TERRITORIO T3 - pulsante «Mappa» ed edifici sulla mappa. Shell, nessun database.

Dati SIMULATI (nessun dato reale letto o scritto). Tre edifici richiesti dalla
verifica mirata, piu' uno con coordinate non valide:
  9   posizione VERIFICATA (coordinate + `position_verified_at`)
  10  SENZA coordinate
  11  coordinate presenti ma NON verificabili (nessuna prova di verifica)
  12  coordinate fuori intervallo

  A  funzioni pure (node) di territorio/territorio-model.js: validazione delle
     coordinate, regola di verifica, classificazione senza doppioni, riquadro;
  B  statici: «Mappa» e' un collegamento a #/edifici/mappa accanto a «+ Nuovo»
     (invariato), la mappa non scrive nulla e non trascina nulla, i dati del CRM
     non passano da innerHTML;
  C  shell sullo stub DOM con una MapLibre finta: solo l'edificio 9 diventa un
     punto, «Edifici da posizionare: 3» con i tre gruppi, clic sul punto ->
     marker attivo + riquadro (testo, mai HTML) + scheda letta una volta,
     «Apri scheda edificio» -> #/edifici/9; paginazione completa (201 edifici =
     2 richieste); errore API; risposta arrivata dopo la navigazione buttata;
  D  Chromium (rete simulata): clic VERO su «🗺️ Mappa», «Torna agli edifici»,
     nessuno sforamento a 360 e 1280 px.
"""
from __future__ import annotations

import json
import re
import subprocess

import pytest

from tests import test_a30_5_create_ui as a30_5
from tests import test_a30_13b_quick_booking_ui as a30_13b
from tests import test_delete_arch_2b3_ui as t2b3
from tests import test_territorio_2_ui as t2
from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)

node = t2b3.node
ASSETS = t2b3.ASSETS
MODELLO = ASSETS / "territorio" / "territorio-model.js"
VISTA = ASSETS / "views" / "territorio.js"
EDIFICI = ASSETS / "views" / "edifici.js"
rt = a30_5._rt()
_codice = t2b3._codice

RIEPILOGO = {"units_declared": 6, "units_counted": 4, "units_to_complete": 2, "units_main": 4, "units_pertinenze": 0}
VERIFICATO = {"id": 9, "name": "Palazzina <b>Mare</b>", "address": "Via Roma", "civic_number": "10",
              "city": "Alba Adriatica", "microzone": "Centro", "latitude": "42.8341000", "longitude": "13.9262000",
              "position_verified_at": "2026-10-13T10:00:00+02:00", "census_summary": RIEPILOGO}
SENZA = {"id": 10, "name": None, "address": "Via Napoli", "civic_number": "3", "city": "Alba Adriatica",
         "latitude": None, "longitude": None, "census_summary": {}}
NON_VERIFICABILE = {"id": 11, "name": "Condominio Sole", "address": "Via Bologna", "civic_number": "7",
                    "city": "Tortoreto", "latitude": "42.8000000", "longitude": "13.9400000", "census_summary": {}}
NON_VALIDO = {"id": 12, "name": "Villa Errata", "address": "Via Errore", "civic_number": "1", "city": "Alba Adriatica",
              "latitude": "120.0000000", "longitude": "13.9000000", "census_summary": {}}
QUATTRO = {"items": [VERIFICATO, SENZA, NON_VERIFICABILE, NON_VALIDO], "total": 4, "limit": 200, "offset": 0}
SCHEDA_9 = {**VERIFICATO, "census_summary": {**RIEPILOGO, "units_counted": 5}, "units": [], "archived_units": []}


# ---------------------------------------------------------------------------
# A - funzioni pure
# ---------------------------------------------------------------------------

def _pure(expr):
    script = (f"import * as m from '{MODELLO.as_posix()}';\n"
              f"console.log(JSON.stringify({expr}));")
    esito = subprocess.run([a30_5.NODE, "--input-type=module", "-e", script], capture_output=True,
                           text=True, timeout=30, env={"PATH": "/usr/bin:/bin"})
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


@node
def test_a01_coordinate_validate_e_regola_di_verifica():
    out = _pure("""(() => {
      const st = (lat, lng, extra = {}) => m.positionStatus({ id: 1, latitude: lat, longitude: lng, ...extra });
      const ok = { position_verified_at: '2026-10-13T10:00:00Z' };
      return {
        verificata: st('42.8341000', '13.9262000', ok), numeri: st(42.8, 13.9, ok),
        senzaProva: st('42.8', '13.9'), provaVuota: st('42.8', '13.9', { position_verified_at: ' ' }),
        provaNonData: st('42.8', '13.9', { position_verified_at: 'si' }), provaBool: st('42.8', '13.9', { position_verified_at: true }),
        censimentoVerificato: st('42.8', '13.9', { census_status: 'verified' }),
        mancanti: st(null, null, ok), vuote: st('', '  ', ok), unaSola: st('42.8', null, ok),
        fuori: st('91', '13.9', ok), fuoriLng: st('42', '181', ok), testo: st('abc', '13.9', ok),
        esponente: st('4e1', '13.9', ok), booleano: st(true, '13.9', ok), infinito: st(Infinity, 13.9, ok),
        zeroZero: st('0', '0', ok), negativi: st('-33.8688', '151.2093', ok),
        parse: m.parseCoordinates({ latitude: ' 42.5 ', longitude: '13.25' }),
      };
    })()""")
    assert out["verificata"] == out["numeri"] == out["negativi"] == "verified"
    for k in ("senzaProva", "provaVuota", "provaNonData", "provaBool", "censimentoVerificato"):
        assert out[k] == "unverified", k                                    # coordinate presenti != verificate
    assert out["mancanti"] == out["vuote"] == "missing"
    for k in ("unaSola", "fuori", "fuoriLng", "testo", "esponente", "booleano", "infinito", "zeroZero"):
        assert out[k] == "invalid", k
    assert out["parse"] == {"valid": True, "lng": 13.25, "lat": 42.5}


@node
def test_a02_classificazione_senza_doppioni_e_riquadro():
    righe = json.dumps([VERIFICATO, SENZA, NON_VERIFICABILE, NON_VALIDO, VERIFICATO, {"id": None}, {"name": "x"}])
    out = _pure(f"""(() => {{
      const g = m.classifyBuildings({righe});
      const fc = m.verifiedFeatureCollection(g.verified);
      return {{
        conteggi: {{ v: g.verified.length, u: g.unverified.length, i: g.invalid.length, mi: g.missing.length,
                    totale: g.total, daPosizionare: g.toPlace }},
        ids: {{ v: g.verified.map((x) => x.building.id), u: g.unverified.map((x) => x.building.id),
               i: g.invalid.map((x) => x.building.id), mi: g.missing.map((x) => x.building.id) }},
        fc, testo: m.toPlaceText(g.toPlace),
        uno: m.viewFor(g.verified), nessuno: m.viewFor([]),
        due: m.viewFor([{{ lng: 13.9, lat: 42.8 }}, {{ lng: 14.0, lat: 42.7 }}]),
        vuoto: m.classifyBuildings(null),
      }};
    }})()""")
    assert out["conteggi"] == {"v": 1, "u": 1, "i": 1, "mi": 1, "totale": 4, "daPosizionare": 3}
    assert out["ids"] == {"v": [9], "u": [11], "i": [12], "mi": [10]}
    assert out["fc"] == {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": 9, "properties": {"building_id": 9},
         "geometry": {"type": "Point", "coordinates": [13.9262, 42.8341]}}]}
    assert out["testo"] == "Edifici da posizionare: 3"
    assert out["uno"] == {"center": [13.9262, 42.8341], "zoom": 16} and out["nessuno"] is None
    assert out["due"] == {"bounds": [[13.9, 42.7], [14.0, 42.8]]}
    assert out["vuoto"]["total"] == 0 and out["vuoto"]["toPlace"] == 0


# ---------------------------------------------------------------------------
# B - statici
# ---------------------------------------------------------------------------

def test_b01_pulsante_mappa_accanto_a_nuovo_invariato():
    testo = EDIFICI.read_text(encoding="utf-8")
    assert '<a href="#/edifici/mappa" id="buildings-map" class="btn ghost buildings-map-btn">🗺️ Mappa</a>' in testo
    assert '<button type="button" id="buildings-new" class="btn primary">+ Nuovo</button>' in testo
    assert testo.index('id="buildings-map"') < testo.index('id="buildings-new"')
    main = (ASSETS / "main.js").read_text(encoding="utf-8")
    sezioni = main[main.index("const SECTIONS = ["):main.index("];", main.index("const SECTIONS = ["))]
    # SENTINELLA AGGIORNATA DA STIMA360 TERRITORIO ZONA: la sidebar ha ORA la voce «Zona» (name 'zona'), che
    # apre la STESSA mappa (target ['edifici', 'mappa']): nessuna voce 'mappa'/'territorio' e nessun altro
    # `target`; la mappa resta dentro Edifici (#/edifici/mappa), nessuna seconda mappa.
    nomi = re.findall(r"name:\s*'([a-z]+)'", sezioni)
    assert "mappa" not in nomi and "territorio" not in nomi
    assert sezioni.count("target:") == 1 and "{ name: 'zona', label: 'Zona', target: ['edifici', 'mappa'] }" in sezioni


def test_b02_la_mappa_legge_e_basta():
    for file in (MODELLO, VISTA):
        codice = _codice(file)
        for vietata in ("apiPost", "apiPatch", "apiPut", "apiDelete", "updateBuilding", "createBuilding", "draggable",
                        "geolocation", "Geolocate", "geocod", "nominatim", "Marker(", "localStorage", "sessionStorage",
                        "stime", "valuation"):
            assert vietata not in codice, (file.name, vietata)
    vista = _codice(VISTA)
    assert "api.listBuildings" in vista and "api.getBuilding(" in vista
    # innerHTML solo per lo scheletro statico della pagina: nessun dato del CRM interpolato
    usi = [m.start() for m in re.finditer(r"innerHTML", vista)]
    assert len(usi) == 1
    scheletro = vista[usi[0]:vista.index("</div>`;", usi[0])]
    assert "${" not in scheletro
    assert "setDOMContent(" in vista and "setHTML" not in vista and "textContent" in vista


@node
def test_b03_node_check():
    for file in (MODELLO, VISTA, EDIFICI):
        esito = subprocess.run([a30_5.NODE, "--check", str(file)], capture_output=True, text=True, timeout=30)
        assert esito.returncode == 0, (file.name, esito.stderr)


# ---------------------------------------------------------------------------
# C - shell sullo stub DOM, con una MapLibre finta che registra tutto
# ---------------------------------------------------------------------------

PRELUDIO = r"""
document.createElementNS = (_ns, tag) => document.createElement(tag);
window._ls = {};
window.addEventListener = function (t, fn) { (this._ls[t] ||= []).push(fn); };
window.removeEventListener = function (t, fn) { this._ls[t] = (this._ls[t] || []).filter((f) => f !== fn); };
window._fire = async function (t) { for (const fn of [...(this._ls[t] || [])]) await fn(); };
globalThis.__maps = [];
globalThis.__popups = [];
globalThis.maplibregl = {
  Map: class {
    constructor(o) { this.o = o; this.h = {}; this.lh = {}; this.sources = {}; this.layers = []; this.states = {};
                     this.fit = null; this.jump = null; this.removed = false; __maps.push(this); }
    on(e, a, b) { if (b) (this.lh[e + ':' + a] ||= []).push(b); else (this.h[e] ||= []).push(a); }
    addControl() {}
    addSource(id, s) { if (this.sources[id]) throw new Error('sorgente doppia ' + id); this.sources[id] = s; }
    addLayer(l) { if (this.layers.some((x) => x.id === l.id)) throw new Error('livello doppio ' + l.id); this.layers.push(l); }
    setFeatureState(f, s) { this.states[f.id] = { ...(this.states[f.id] || {}), ...s }; }
    fitBounds(b, o) { this.fit = [b, o]; }
    jumpTo(o) { this.jump = o; }
    getCanvas() { return { style: {} }; }
    // SENTINELLA AGGIORNATA DA STIMA360 TERRITORIO ZONA-2: lo zoom (la regola di conferma del punto)
    getZoom() { return this.o && Number.isFinite(this.o.zoom) ? this.o.zoom : 14; }
    remove() { this.removed = true; }
    emit(e) { for (const f of this.h[e] || []) f({}); }
    clickLayer(layer, id) { for (const f of this.lh['click:' + layer] || []) f({ features: [{ id }] }); }
  },
  NavigationControl: class {},
  Popup: class {
    constructor(o) { this.o = o; this.h = {}; this.removed = false; __popups.push(this); }
    setLngLat(p) { this.at = p; return this; }
    setDOMContent(n) { this.node = n; return this; }
    addTo(m) { this.map = m; return this; }
    on(e, f) { (this.h[e] ||= []).push(f); return this; }
    remove() { if (this.removed) return; this.removed = true; for (const f of this.h.close || []) f(); }
  },
};
"""

HELPERS = r"""
async function vai(h) { window.location.hash = h; await window._fire('hashchange'); await wait(); await wait(); }
const M = () => __maps[__maps.length - 1];
const P = () => __popups[__popups.length - 1];
const daPos = () => {
  const d = C().querySelector('[data-to-place]');
  if (!d || d.hidden) return null;
  const gruppi = {};
  for (const g of d.querySelectorAll('[data-position-group]')) {
    gruppi[g.dataset.positionGroup] = g.querySelectorAll('[data-building-link]').map((a) => [a.getAttribute('href'), a.textContent]);
  }
  return { sommario: d.querySelector('summary').textContent, gruppi };
};
"""


# Trattiene la lista degli edifici finche' il test non la libera (`__libera()`):
# serve a far arrivare la risposta DOPO una navigazione.
TRATTIENI = r"""
globalThis.__trattieni = new Promise((r) => { globalThis.__libera = r; });
const __fetchVero = globalThis.fetch;
globalThis.fetch = async (url, o) => {
  if (String(url).startsWith('/api/property/buildings?')) await globalThis.__trattieni;
  return __fetchVero(url, o);
};
"""


def _rotte(liste, extra=(), pre=""):
    voci = [("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione("agency_owner"))]),
            *extra,
            ("GET", "/api/property/buildings?", liste),
            ("GET", "/api/property/form-options", [rt.ok({"territory": [], "building_types": []})]),
            ("GET", "/api/", [rt.ok({"items": [], "total": 0})])]
    return PRELUDIO + pre + "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});" for m, p, r in voci)


def _run(staged, scenario, liste, hash="#/edifici/mappa", extra=(), pre=""):  # noqa: F811
    return t2b3._run(staged, HELPERS + scenario, _rotte(liste, extra, pre), hash)


def _get(out, prefisso):
    return [c["url"] for c in out["calls"] if c["m"] == "GET" and c["url"].startswith(prefisso)]


@node
def test_c01_solo_il_verificato_diventa_un_punto_gli_altri_da_posizionare(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      M().emit('load'); await wait();
      const m = M();
      report({ sorgenti: m.sources, livelli: m.layers.map((l) => [l.id, l.type, l.source]),
               vista: m.jump, fit: m.fit, daPos: daPos(),
               statoEdifici: C().querySelector('[data-buildings-status]').hidden,
               scritture: (globalThis.__calls || []).length });
    """
    out = _run(staged, scenario, [rt.ok(QUATTRO)])
    fc = out["sorgenti"]["stima-buildings"]["data"]
    assert [f["id"] for f in fc["features"]] == [9]                          # SOLO il verificato
    assert fc["features"][0]["geometry"]["coordinates"] == [13.9262, 42.8341]
    assert out["livelli"] == [["stima-buildings-points", "circle", "stima-buildings"]]
    assert out["vista"] == {"center": [13.9262, 42.8341], "zoom": 16} and out["fit"] is None
    assert out["statoEdifici"] is True
    d = out["daPos"]
    assert d["sommario"] == "Edifici da posizionare: 3"
    assert d["gruppi"] == {
        "missing": [["#/edifici/10", "Via Napoli 3 — Via Napoli 3 · Alba Adriatica"]],
        "unverified": [["#/edifici/11", "Condominio Sole — Via Bologna 7 · Tortoreto"]],
        "invalid": [["#/edifici/12", "Villa Errata — Via Errore 1 · Alba Adriatica"]],
    }
    assert "9" not in json.dumps(d["gruppi"]).replace("Via", "")             # il verificato non e' «da posizionare»
    assert [c["m"] for c in out["calls"]] == ["GET"] * len(out["calls"])     # nessuna scrittura
    assert len(_get(out, "/api/property/buildings?")) == 1
    assert "limit=200" in _get(out, "/api/property/buildings?")[0] and "offset=0" in _get(out, "/api/property/buildings?")[0]


@node
def test_c02_clic_sul_punto_attivo_riquadro_di_solo_testo_e_apri_scheda(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      M().emit('load'); await wait();
      M().clickLayer('stima-buildings-points', 9); await wait(); await wait();
      const n = P().node;
      const primo = { at: P().at, stato: M().states[9], attivo: C().querySelector('[data-map]').dataset.activeBuilding,
                      titolo: n.querySelector('.territorio-popup-title').textContent,
                      figli: n.querySelector('.territorio-popup-title').children.length,
                      testo: n.textContent, bottone: n.querySelector('[data-open-building]').textContent };
      // stesso punto di nuovo: la scheda NON si rilegge
      M().clickLayer('stima-buildings-points', 9); await wait(); await wait();
      const secondo = { popup: __popups.length, primoChiuso: __popups[0].removed, stato: M().states[9],
                        letture: calls.filter((c) => c.url === '/api/property/buildings/9').length };
      P().node.querySelector('[data-open-building]').dispatch('click'); await wait();
      await window._fire('hashchange'); await wait(); await wait();
      report({ primo, secondo, hash: window.location.hash, rimossa: __maps[0].removed });
    """
    out = _run(staged, scenario, [rt.ok(QUATTRO)], extra=[("GET", "/api/property/buildings/9", [rt.ok(SCHEDA_9)])])
    p = out["primo"]
    assert p["at"] == [13.9262, 42.8341] and p["stato"] == {"selected": True} and p["attivo"] == "9"
    assert p["titolo"] == "Palazzina <b>Mare</b>" and p["figli"] == 0         # testo, mai HTML
    assert "Via Roma 10" in p["testo"] and "Alba Adriatica · Centro" in p["testo"]
    assert "Unità dichiarate: 6 · censite: 5 · da completare: 2" in p["testo"]   # dalla scheda (getBuilding)
    assert p["bottone"] == "Apri scheda edificio"
    s = out["secondo"]
    assert s["popup"] == 2 and s["primoChiuso"] is True and s["stato"] == {"selected": True}
    assert s["letture"] == 1                                                    # la scheda: una volta sola
    assert out["hash"] == "#/edifici/9" and out["rimossa"] is True


@node
def test_c03_paginazione_completa_senza_tetto(staged):  # noqa: F811
    pagina1 = {"items": [{**SENZA, "id": i} for i in range(1000, 1200)], "total": 201, "limit": 200, "offset": 0}
    pagina2 = {"items": [{**VERIFICATO, "id": 2000}], "total": 201, "limit": 200, "offset": 200}
    scenario = r"""
      await wait(); await wait(); await wait();
      M().emit('load'); await wait();
      report({ daPos: daPos().sommario, punti: M().sources['stima-buildings'].data.features.map((f) => f.id) });
    """
    out = _run(staged, scenario, [rt.ok(pagina1), rt.ok(pagina2)])
    urls = _get(out, "/api/property/buildings?")
    assert [u.split("offset=")[1].split("&")[0] for u in urls] == ["0", "200"], urls
    assert out["daPos"] == "Edifici da posizionare: 200" and out["punti"] == [2000]


@node
def test_c04_nessun_edificio_mappa_comunque_e_errore_api(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      M().emit('load'); await wait();
      report({ daPos: daPos(), punti: M().sources['stima-buildings'].data.features.length, vista: M().jump,
               mappaViva: !M().removed });
    """
    out = _run(staged, scenario, [rt.ok({"items": [], "total": 0})])
    assert out["daPos"]["sommario"] == "Edifici da posizionare: 0" and out["daPos"]["gruppi"] == {}
    assert out["punti"] == 0 and out["vista"] is None and out["mappaViva"] is True
    scenario = r"""
      await wait(); await wait();
      M().emit('load'); await wait();
      const s = C().querySelector('[data-buildings-status]');
      report({ testo: s.querySelector('[data-buildings-status-text]').textContent,
               riprova: !s.querySelector('[data-buildings-retry]').hidden, daPos: daPos(),
               sorgenti: Object.keys(M().sources), mappaViva: !M().removed });
    """
    out = _run(staged, scenario, [{"status": 500, "body": {"detail": "boom"}}])
    assert out["testo"].startswith("Impossibile leggere gli edifici") and out["riprova"] is True
    assert out["daPos"] is None and out["sorgenti"] == [] and out["mappaViva"] is True


@node
def test_c05_risposta_arrivata_dopo_la_navigazione_non_disegna(staged):  # noqa: F811
    scenario = r"""
      // si lascia la mappa PRIMA che la lista degli edifici risponda, poi la si libera
      const vista = { mappe: __maps.length, daPos: !!C().querySelector('[data-to-place]') };
      await vai('#/oggi');
      __libera(); await wait(); await wait();
      report({ mappe: __maps.length, sorgenti: __maps.map((m) => Object.keys(m.sources).length),
               rimosse: __maps.map((m) => m.removed), daPos: !!C().querySelector('[data-to-place]') });
    """
    out = _run(staged, scenario, [rt.ok(QUATTRO)], pre=TRATTIENI)
    assert out["sorgenti"] == [0] * out["mappe"] and all(out["rimosse"])
    assert out["daPos"] is False


@node
def test_c06_pulsante_mappa_dall_elenco_edifici(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const a = C().querySelector('#buildings-map');
      const visto = { href: a.getAttribute('href'), testo: a.textContent, nuovo: C().querySelector('#buildings-new').textContent };
      await vai(a.getAttribute('href'));
      report({ visto, mappe: __maps.length, titolo: __dom.byId['page-title'].textContent,
               indietro: C().querySelector('[data-back-to-buildings]').textContent });
    """
    out = _run(staged, scenario, [rt.ok(QUATTRO)], hash="#/edifici")
    assert out["visto"] == {"href": "#/edifici/mappa", "testo": "🗺️ Mappa", "nuovo": "+ Nuovo"}
    # SENTINELLA AGGIORNATA DA STIMA360 TERRITORIO ZONA: dal pulsante «Mappa» si apre la stessa mappa, il cui
    # titolo di pagina e' ora «Zona» (voce attiva «Zona»).
    assert out["mappe"] == 1 and out["titolo"] == "Zona"
    assert out["indietro"] == "← Torna agli edifici"


# ---------------------------------------------------------------------------
# D - Chromium (rete simulata)
# ---------------------------------------------------------------------------

_FINTA = r"""
window.__ml = { create: 0, remove: 0, features: null, popups: 0 };
window.maplibregl = {
  Map: class {
    constructor(o) { window.__ml.create++; this.c = o.container; this.h = {}; this.lh = {};
      const k = document.createElement('canvas'); k.className = 'finta';
      k.style.width = '100%'; k.style.height = '100%'; k.style.display = 'block'; o.container.appendChild(k);
      setTimeout(() => (this.h.load || []).forEach((f) => f({})), 0); }
    on(e, a, b) { if (b) (this.lh[e + ':' + a] ||= []).push(b); else (this.h[e] ||= []).push(a); }
    addControl() {}
    addSource(id, s) { window.__ml.features = s.data.features.map((f) => f.id);
      for (const f of s.data.features) { const b = document.createElement('button'); b.type = 'button';
        b.className = 'punto-finto'; b.dataset.id = f.id; b.textContent = 'punto ' + f.id;
        b.style.position = 'absolute'; b.style.left = '40%'; b.style.top = '40%';
        b.onclick = () => (this.lh['click:stima-buildings-points'] || []).forEach((g) => g({ features: [{ id: f.id }] }));
        this.c.appendChild(b); } }
    addLayer() {} setFeatureState() {} fitBounds() {} jumpTo() {}
    getCanvas() { return { style: {} }; }
    // SENTINELLA AGGIORNATA DA STIMA360 TERRITORIO ZONA-2: lo zoom (la regola di conferma del punto)
    getZoom() { return this.o && Number.isFinite(this.o.zoom) ? this.o.zoom : 14; }
    remove() { window.__ml.remove++; }
  },
  NavigationControl: class {},
  Popup: class {
    on() { return this; } setLngLat() { return this; }
    setDOMContent(n) { n.classList.add('riquadro-finto'); if (this.n && this.n.isConnected) this.n.replaceWith(n); this.n = n; return this; }
    addTo() { window.__ml.popups++; document.body.appendChild(this.n); return this; }
    remove() { this.n && this.n.remove(); }
  },
};
"""


@pytest.mark.parametrize("larghezza", [360, 1280])
def test_d01_chromium_clic_vero_su_mappa_punto_riquadro_e_ritorno(larghezza):
    sync_playwright = t2b3._chromium()
    api = [("/api/operator-auth/me", t2b3._ME), ("/api/property/buildings/9", SCHEDA_9),
           ("/api/property/buildings?", QUATTRO),
           ("/api/property/form-options", {"territory": [], "building_types": []})]
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": larghezza, "height": 780})
            t2b3._servi(page, api)
            page.route("https://unpkg.com/**", lambda r: r.abort())
            page.add_init_script(_FINTA)
            page.goto("http://stima360.test/os/#/edifici")
            page.wait_for_selector("#buildings-map")
            lista = page.evaluate("""() => { const a = document.querySelector('#buildings-map'), n = document.querySelector('#buildings-new');
              const r = a.getBoundingClientRect(); return { h: r.height, right: r.right, scroll: document.documentElement.scrollWidth,
              nuovo: n.textContent, nuovoVisibile: n.getBoundingClientRect().height > 0 }; }""")
            page.click("#buildings-map")
            page.wait_for_selector("button.punto-finto")
            url = page.evaluate("location.hash")
            mappa = t2._misura(page)
            pendenti = page.evaluate("""() => { const d = document.querySelector('[data-to-place]');
              return { sommario: d.querySelector('summary').textContent, hidden: d.hidden }; }""")
            punti = page.evaluate("window.__ml.features")
            page.click("button.punto-finto")
            page.wait_for_selector(".riquadro-finto [data-open-building]")
            page.wait_for_function("!document.querySelector('.riquadro-finto').textContent.includes('Aggiornamento')")
            riquadro = page.evaluate("""() => { const n = document.querySelector('.riquadro-finto');
              return { testo: n.textContent, html: n.querySelector('.territorio-popup-title').innerHTML }; }""")
            page.click(".riquadro-finto [data-open-building]")
            page.wait_for_function("location.hash === '#/edifici/9'")
            page.goto("http://stima360.test/os/#/edifici/mappa")
            page.wait_for_selector("[data-back-to-buildings]")
            page.click("[data-back-to-buildings]")
            page.wait_for_selector("#buildings-area")
            ritorno = page.evaluate("location.hash")
            browser.close()
    except Exception as exc:  # noqa: BLE001
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium non disponibile: misura NON eseguita (BLOCKED)")
        raise
    assert lista["h"] >= 36 and lista["right"] <= larghezza and lista["scroll"] <= larghezza
    assert lista["nuovo"] == "+ Nuovo" and lista["nuovoVisibile"] is True
    assert url == "#/edifici/mappa"
    assert mappa["scroll"] <= mappa["inner"] and mappa["mappa"]["h"] >= 300 and mappa["mappa"]["right"] <= larghezza
    assert pendenti == {"sommario": "Edifici da posizionare: 3", "hidden": False}
    assert punti == [9]
    assert "Palazzina <b>Mare</b>" in riquadro["testo"] and "&lt;b&gt;" in riquadro["html"]
    assert ritorno == "#/edifici"
