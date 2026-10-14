"""STIMA360 TERRITORIO ZONA-2 - inserimento geografico integrato, senza database.

  A  funzioni pure e statici: precompilazione dal risultato della ricerca (Comune
     solo se nel catalogo, civico solo se e' quello cercato), testo del punto, le
     tre azioni; il modulo di Zona cerca SOLO tramite il backend e SOLO dal
     pulsante, scrive SOLO con la conferma T4 (mai crea edifici da se'); la
     mappa generale resta in sola lettura; il gancio della procedura guidata
     e' facoltativo e additivo (chi non lo passa non cambia nulla).
  B  Shell VERA sullo stub DOM (MapLibre finta con segnaposti e zoom):
     b01 Zona -> «Cerca indirizzo» (solo dal pulsante) -> risultato -> punto
         provvisorio -> «Conferma punto» solo a zoom >= 17 -> «Nuovo edificio»
         apre la procedura ESISTENTE con Comune/via/civico gia' compilati ->
         «Salva edificio» -> la posizione si collega da sola (POST /position
         con le SOLE coordinate, dopo il POST dell'edificio) -> «Vedi sulla
         mappa» -> mappa generale con l'edificio evidenziato, punto consumato;
     b02 «Annulla» (prima e dopo la conferma del punto) non scrive nulla; il
         punto resta in memoria passando a un'altra pagina e tornando su Zona;
     b03 collegamento fallito dopo la creazione: UN solo edificio creato (mai un
         secondo POST), stato «NON collegata» leggibile, «Riprova» con lo
         stesso punto, poi riuscito; il punto di Zona riparte anche nella
         pagina di posizionamento (recupero);
     b04 «Collega un edificio esistente»: ricerca, «Collega qui» = conferma T4
         sull'edificio scelto, errore leggibile e riprova; «Nuovo immobile»
         apre la procedura con la nota sugli immobili senza edificio.
Il percorso con backend, PostgreSQL e Chromium veri e' in
tests/test_territorio_zona2_browser_postgres.py.
"""
from __future__ import annotations

import json
import re
import subprocess

from tests import test_a30_5_create_ui as a30_5
from tests import test_a30_13b_quick_booking_ui as a30_13b
from tests import test_censimento_4_ui as c4
from tests import test_delete_arch_2b3_ui as t2b3
from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)

node = t2b3.node
ASSETS = t2b3.ASSETS
MODELLO = ASSETS / "territorio" / "territorio-model.js"
VISTA = ASSETS / "views" / "territorio.js"
INSERISCI = ASSETS / "views" / "territorio-inserisci.js"
POSIZIONA = ASSETS / "views" / "territorio-posiziona.js"
WIZARD = ASSETS / "census" / "census-wizard.js"
_codice = t2b3._codice
rt = a30_5._rt()


def _pure(expr):
    script = f"import * as m from '{MODELLO.as_posix()}';\nconsole.log(JSON.stringify({expr}));"
    esito = subprocess.run([a30_5.NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                           timeout=30, env={"PATH": "/usr/bin:/bin"})
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# A - funzioni pure e statici
# ---------------------------------------------------------------------------

@node
def test_a01_precompilazione_dal_risultato_e_testo_del_punto():
    comuni = json.dumps([{"name": "Tortoreto", "microzones": ["Lido"], "region": "Abruzzo", "province": "TE"},
                         {"name": "Alba Adriatica", "microzones": [], "region": "Abruzzo", "province": "TE"}])
    out = _pure(f"""(() => {{
      const comuni = {comuni};
      const casa = {{ road: 'Via Roma', house_number: '25', town: 'tortoreto', precision: 'house', house_number_matches: true }};
      return {{
        casa: m.wizardFieldsFromPlace(casa, comuni),
        accento: m.wizardFieldsFromPlace({{ ...casa, town: 'Álba Adriática' }}, comuni).city,
        fuori: m.wizardFieldsFromPlace({{ ...casa, town: 'Giulianova' }}, comuni),
        civicoDiverso: m.wizardFieldsFromPlace({{ ...casa, house_number: '27', house_number_matches: false }}, comuni).civic_number,
        via: m.wizardFieldsFromPlace({{ road: 'Via Roma', town: 'Tortoreto', precision: 'street' }}, comuni),
        nulla: m.wizardFieldsFromPlace(null, comuni),
        testo: [m.chosenPointText({{ lat: 42.834145678, lng: 13.926212345 }}), m.chosenPointText(null), m.chosenPointText({{ lat: 'x', lng: 1 }})],
        azioni: m.INSERT_ACTIONS.map((a) => [a.key, a.label]),
      }};
    }})()""")
    assert out["casa"] == {"city": "Tortoreto", "address": "Via Roma", "civic_number": "25"}
    assert out["accento"] == "Alba Adriatica"
    assert out["fuori"] == {"city": "", "address": "Via Roma", "civic_number": "25"}     # Comune fuori catalogo: da scegliere
    assert out["civicoDiverso"] == ""                                                    # mai un civico diverso
    assert out["via"] == {"city": "Tortoreto", "address": "Via Roma", "civic_number": ""}
    assert out["nulla"] == {"city": "", "address": "", "civic_number": ""}
    assert out["testo"] == ["42.83415, 13.92621", "", ""]
    # SENTINELLA AGGIORNATA DA STIMA360 ZONA-3: la quarta azione collega un immobile indipendente esistente.
    assert out["azioni"] == [["building", "Nuovo edificio"], ["unit", "Nuovo immobile"], ["link", "Collega un edificio esistente"],
                             ["link-property", "Collega un immobile esistente"]]


def test_a02_il_modulo_di_zona_legge_cerca_dal_backend_e_scrive_solo_con_la_conferma_t4():
    codice = _codice(INSERISCI)
    for vietata in ("geolocation", "Geolocate", "nominatim", "openstreetmap.org", "fetch(", "apiPost", "apiPatch", "apiPut",
                    "updateBuilding", "createBuilding", "createUnit", "updateProperty", "position_verified_by", "user_id",
                    "localStorage", "sessionStorage", "innerHTML = `${", "addEventListener('keyup'",
                    "oninput", "latitude:", "longitude:"):
        assert vietata not in codice, vietata
    # SENTINELLA AGGIORNATA DA STIMA360 ZONA-5: un solo gestore `input` (i campi dell'indirizzo: fa solo decadere
    # la conferma, nessuna ricerca); la ricerca inversa e' una sola chiamata, mai mentre si scrive.
    assert codice.count("addEventListener('input'") == 1
    gestore = codice[codice.index("addEventListener('input'"):]
    assert "api." not in gestore[:gestore.index("});")]
    assert codice.count("api.geocode(") == 1 and codice.count("api.reverseGeocode(") == 1 and "addEventListener('submit'" in codice
    assert codice.count("api.confirmBuildingPosition(") == 1 and "esito.status !== 'verified'" in codice
    assert codice.count("startCreation(") == 1 and "recordKind: 'census'" in codice and "onBuildingReady" in codice
    assert "canConfirm(" in codice and "pointForSave(" in codice                        # stessa regola di conferma di T4
    assert "sessionEpoch()" in codice                                                   # il punto vive con la sessione
    # innerHTML solo per lo scheletro statico: nessun dato del CRM interpolato
    for m in re.finditer(r"innerHTML = `", codice):
        scheletro = codice[m.start():codice.index("`;", m.start() + 12)]
        assert "${" not in scheletro.replace("${geocodeSearchHtml(", "")
    # la mappa generale resta in sola lettura: monta il pannello, non scrive
    vista = _codice(VISTA)
    assert "mountInserimento(" in vista and "onLinked" in vista
    for vietata in ("apiPost", "createBuilding", "confirmBuildingPosition", "Marker(", "draggable", "geocod"):
        assert vietata not in vista, vietata


def test_a03_gancio_facoltativo_nella_procedura_guidata_e_recupero_nel_posizionamento():
    wizard = _codice(WIZARD)
    assert "onBuildingReady = null" in wizard
    assert wizard.count("await edificioPronto(true)") == 1 and wizard.count("await edificioPronto(false)") == 2
    assert "typeof onBuildingReady !== 'function' || !stato.building" in wizard       # senza gancio: nulla cambia
    assert "data-building-ready-extra" in wizard
    posiziona = _codice(POSIZIONA)
    assert "chosenPoint()" in posiziona and "clearChosenPoint()" in posiziona
    assert posiziona.count("draggable: true") == 1 and posiziona.count("draggable: false") == 1
    assert "if (daZona) clearChosenPoint();" in posiziona


@node
def test_a04_node_check():
    for file in (MODELLO, VISTA, INSERISCI, POSIZIONA, WIZARD):
        esito = subprocess.run([a30_5.NODE, "--check", str(file)], capture_output=True, text=True, timeout=30)
        assert esito.returncode == 0, esito.stderr


# ---------------------------------------------------------------------------
# B - Shell vera sullo stub DOM
# ---------------------------------------------------------------------------

PRELUDIO = r"""
document.createElementNS = (_ns, tag) => document.createElement(tag);
window._ls = {};
window.addEventListener = function (t, fn) { (this._ls[t] ||= []).push(fn); };
window.removeEventListener = function (t, fn) { this._ls[t] = (this._ls[t] || []).filter((f) => f !== fn); };
window._fire = async function (t) { for (const fn of [...(this._ls[t] || [])]) await fn(); };
globalThis.__maps = []; globalThis.__markers = []; globalThis.__popups = [];
globalThis.maplibregl = {
  Map: class {
    constructor(o) { this.o = o; this.z = o.zoom; this.h = {}; this.lh = {}; this.sources = {}; this.layers = []; this.states = {};
                     this.jump = null; this.removed = false; __maps.push(this); }
    on(e, a, b) { if (b) (this.lh[e + ':' + a] ||= []).push(b); else (this.h[e] ||= []).push(a); }
    addControl() {}
    addSource(id, s) { this.sources[id] = s; }
    addLayer(l) { this.layers.push(l); }
    setFeatureState(f, s) { this.states[f.id] = { ...(this.states[f.id] || {}), ...s }; }
    fitBounds() {}
    jumpTo(o) { this.jump = o; if (o && o.zoom !== undefined) this.z = o.zoom; }
    getZoom() { return this.z; }
    setZoom(z) { this.z = z; this.emit('zoomend'); }
    getCanvas() { return { style: {} }; }
    loaded() { return true; }
    remove() { this.removed = true; }
    emit(e, x = {}) { for (const f of this.h[e] || []) f(x); }
    clickLayer(layer, id) { for (const f of this.lh['click:' + layer] || []) f({ features: [{ id }] }); }
  },
  NavigationControl: class {},
  Marker: class {
    constructor(o) { this.o = o; this.el = o.element; this.h = {}; this.removed = false; this.d = !!o.draggable; __markers.push(this); }
    on(e, f) { (this.h[e] ||= []).push(f); return this; }
    setLngLat(p) { this.p = p; return this; }
    getLngLat() { return { lng: this.p[0], lat: this.p[1] }; }
    addTo(m) { this.map = m; return this; }
    setDraggable(d) { this.d = !!d; }
    remove() { this.removed = true; }
    fire(e) { for (const f of this.h[e] || []) f({}); }
  },
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
const K = () => __markers.filter((k) => !k.removed && !(k.map && k.map.removed));   // map.remove() porta via i suoi segnaposti
const P = () => C().querySelector('[data-insert-panel]');
const pq = (s) => P().querySelector(s);
const visibile = (s) => { const e = pq(s); return !!e && !e.hidden; };
const campo = (sel, v, ev = 'input') => { const e = q(sel); e.value = v; e.dispatch(ev); };
async function cerca(testo) { pq('[data-geocode-input]').value = testo; pq('[data-geocode-form]').dispatch('submit'); await wait(); await wait(); }
async function scegliRisultato(prec) { pq(`[data-geocode-pick="${prec}"]`).dispatch('click'); await wait(); }
async function confermaPunto() { pq('[data-insert-confirm]').dispatch('click'); await wait(); }
async function azione(k) { pq(`[data-insert-action="${k}"]`).dispatch('click'); await wait(); await wait(); }
const scritte = () => chiamate().filter((c) => ['POST', 'PATCH', 'PUT', 'DELETE'].includes(c.m)).map((c) => [c.m, c.url, c.body]);
const stato = () => ({ punto: visibile('[data-insert-point]'), azioni: visibile('[data-insert-actions]'), link: visibile('[data-insert-link]'),
                       conferma: !!pq('[data-insert-confirm]') && !pq('[data-insert-confirm]').disabled,
                       segnaposti: K().length, trascinabile: K().length ? K()[0].d : null,
                       testo: pq('[data-insert-point-text]') ? pq('[data-insert-point-text]').textContent : null,
                       scelto: pq('[data-insert-chosen]') ? pq('[data-insert-chosen]').textContent : null,
                       hint: pq('[data-insert-hint]') ? pq('[data-insert-hint]').textContent : null });
const passo = () => { const el = D() && D().querySelector('[data-wizard-step]'); return el ? el.dataset.wizardStep : null; };
const pronto = () => { const b = D() && D().querySelector('[data-ready-building]'); return b ? { stato: b.dataset.readyState, testo: b.visibleText() } : null; };
"""

GEOCODE = {"query": "Via Roma 25, Tortoreto", "cached": False, "attribution": "© OpenStreetMap contributors", "results": [
    {"label": "25, Via Roma, Tortoreto, TE, 64018, Italia", "latitude": "42.8030000", "longitude": "13.9150000", "precision": "house",
     "house_number": "25", "house_number_matches": True, "road": "Via Roma", "town": "Tortoreto", "postcode": "64018"},
    {"label": "Via Roma, Tortoreto, TE, Italia", "latitude": "42.8031000", "longitude": "13.9151000", "precision": "street",
     "house_number": None, "house_number_matches": False, "road": "Via Roma", "town": "Tortoreto", "postcode": "64018"},
    {"label": "Tortoreto, TE, Italia", "latitude": "42.8000000", "longitude": "13.9100000", "precision": "area",
     "house_number": None, "house_number_matches": False, "road": None, "town": "Tortoreto", "postcode": None},
]}
SALVATO = {**c4.DETTAGLIO, "id": 51, "name": "Palazzina Zona", "civic_number": "25", "units": [], "latitude": None, "longitude": None,
           "position_verified_at": None, "replica": False, "similar": [],
           "census_summary": {**c4.DETTAGLIO["census_summary"], "units_counted": 0, "units_to_complete": 6}}
VERIFICATO = {**SALVATO, "latitude": "42.8030000", "longitude": "13.9150000", "position_verified_at": "2026-10-13T10:00:00Z",
              "position_verified_by_user_id": 1}
POSIZIONE_OK = {"status": 200, "body": {"building_id": 51, "status": "verified", "latitude": "42.8030000", "longitude": "13.9150000",
                                        "position_verified_at": "2026-10-13T10:00:00Z", "position_verified_by_user_id": 1}}
POSIZIONE_KO = {"status": 503, "body": {"detail": "Posizione non disponibile", "code": "POSITION_NOT_INSTALLED"}}


def _rotte(*, liste, posizione=(POSIZIONE_OK,), edifici=(), extra=(), dettaglio=VERIFICATO):
    voci = [("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione("agency_owner"))]),
            ("GET", "/api/property/geocode", [rt.ok(GEOCODE)]),
            ("GET", "/api/property/form-options", [rt.ok(c4.OPZIONI)]),
            *extra,
            ("POST", "/api/property/buildings/51/position", list(posizione)),
            ("GET", "/api/property/buildings/51", [rt.ok(dettaglio)]),
            ("GET", "/api/property/buildings/7", [rt.ok(c4.DETTAGLIO)]),
            ("GET", "/api/property/buildings?", list(liste)),
            ("POST", "/api/property/buildings", list(edifici or ({"status": 201, "body": SALVATO},))),
            ("GET", "/api/", [rt.ok({"items": [], "total": 0})])]
    return PRELUDIO + "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});" for m, p, r in voci)


def _run(staged, scenario, rotte, hash="#/edifici/mappa"):  # noqa: F811
    return t2b3._run(staged, HELPERS + scenario, rotte, hash)


VUOTA = rt.ok({"items": [], "total": 0})
CON_51 = rt.ok({"items": [VERIFICATO], "total": 1})


@node
def test_b01_zona_cerca_punto_conferma_nuovo_edificio_posizione_collegata_e_ritorno(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); M().emit('load'); await wait();
      const inizio = { pannello: !!P(), stato: stato(), letture: chiamate().filter((c) => c.url.includes('/geocode')).length };
      pq('[data-geocode-input]').value = 'Via Roma 25, Tortoreto'; pq('[data-geocode-input]').dispatch('input'); await wait();
      const digitando = chiamate().filter((c) => c.url.includes('/geocode')).length;
      await cerca('Via Roma 25, Tortoreto');
      const risultati = pq('[data-geocode-results]').querySelectorAll('[data-geocode-pick]').map((b) => b.dataset.geocodePick);
      await scegliRisultato('house');
      const dopoScelta = { ...stato(), zoom: M().getZoom(), centro: M().jump && M().jump.center, p: K()[0].p };
      M().setZoom(16); await wait();
      const zoomBasso = { conferma: stato().conferma, hint: stato().hint };
      M().setZoom(18); await wait();
      const zoomAlto = { conferma: stato().conferma };
      await confermaPunto();
      const confermato = { ...stato(), carte: pq('[data-insert-cards]').querySelectorAll('[data-insert-action]').map((b) => b.dataset.insertAction),
                           nota: pq('[data-insert-chosen-note]').textContent, scritture: scritte() };
      await azione('building');
      const passo1 = { passo: passo(), city: q('#wz-city').value, via: q('#wz-address').value, civico: q('#wz-civic').value };
      campo('#wz-declared', '6'); campo('#wz-name', 'Palazzina Zona');
      q('[data-wizard-step="1"]').dispatch('submit'); await wait(); await wait();
      const passo2 = { passo: passo(), attivo: D().querySelectorAll('[data-path]').find((b) => b.classList.contains('active')).dataset.path };
      D().querySelector('[data-path="building"]').dispatch('click'); await wait(); await wait();
      const passo3 = { passo: passo() };
      D().querySelector('[data-create-building]').dispatch('click'); await wait();
      const primaDiSalvare = { passo: passo(), scritture: scritte() };
      D().querySelector('[data-save-building]').dispatch('click'); await wait(); await wait(); await wait();
      const salvato = { passo: passo(), pronto: pronto(), scritture: scritte(), titolo: D().querySelector('.census-sheet-title').textContent };
      D().querySelector('[data-ready-map]').dispatch('click'); await window._fire('hashchange'); await wait(); await wait(); M().emit('load'); await wait(); await wait();
      const mappa = { hash: window.location.hash, dialogo: !!D(), punti: (M().sources['stima-buildings'] || { data: { features: [] } }).data.features.map((f) => f.id),
                      attivo: M().states[51], stato: stato(), segnaposti: K().length, pannello: !!C().querySelector('[data-building-panel="51"]') };
      report({ inizio, digitando, risultati, dopoScelta, zoomBasso, zoomAlto, confermato, passo1, passo2, passo3, primaDiSalvare, salvato, mappa });
    """
    out = _run(staged, scenario, _rotte(liste=[VUOTA, VUOTA, CON_51]))
    assert out["inizio"]["pannello"] is True and out["inizio"]["letture"] == 0
    assert out["inizio"]["stato"]["punto"] is False and out["inizio"]["stato"]["azioni"] is False and out["inizio"]["stato"]["segnaposti"] == 0
    assert out["digitando"] == 0                                                       # scrivere NON cerca
    assert out["risultati"] == ["house", "street", "area"]
    d = out["dopoScelta"]
    assert d["punto"] is True and d["azioni"] is False and d["segnaposti"] == 1 and d["trascinabile"] is True
    assert d["zoom"] == 18 and d["centro"] == [13.915, 42.803] and d["p"] == [13.915, 42.803]
    assert d["testo"] == "Punto provvisorio: 42.80300, 13.91500" and "suggerito dalla ricerca" in d["hint"]
    assert out["zoomBasso"] == {"conferma": False, "hint": out["zoomBasso"]["hint"]} and "Ingrandisci" in out["zoomBasso"]["hint"]
    assert out["zoomAlto"]["conferma"] is True
    c = out["confermato"]
    assert c["punto"] is False and c["azioni"] is True and c["trascinabile"] is False and c["scritture"] == []
    # SENTINELLA AGGIORNATA DA STIMA360 ZONA-3: quattro carte (anche «Collega un immobile esistente»).
    assert c["scelto"] == "Punto scelto: 42.80300, 13.91500" and c["carte"] == ["building", "unit", "link", "link-property"]
    # SENTINELLA AGGIORNATA DA STIMA360 ZONA-5: il risultato scelto diventa l'indirizzo DICHIARATO (pannello
    # «Indirizzo individuato», gia' confermato perche' scelto dall'operatore) e la nota lo riporta.
    assert c["nota"] == "Indirizzo dichiarato: Via Roma 25, 64018 Tortoreto"
    assert out["passo1"] == {"passo": "1", "city": "Tortoreto", "via": "Via Roma", "civico": "25"}
    assert out["passo2"] == {"passo": "2", "attivo": "building"}
    assert out["passo3"] == {"passo": "3"}
    assert out["primaDiSalvare"] == {"passo": "4", "scritture": []}                   # nulla scritto fino a «Salva edificio»
    s = out["salvato"]
    assert s["passo"] == "5" and s["titolo"] == "Edificio salvato"
    assert [w[:2] for w in s["scritture"]] == [["POST", "/api/property/buildings"], ["POST", "/api/property/buildings/51/position"]]
    assert s["scritture"][0][2]["city"] == "Tortoreto" and s["scritture"][0][2]["civic_number"] == "25" and s["scritture"][0][2]["name"] == "Palazzina Zona"
    assert s["scritture"][1][2] == {"latitude": "42.8030000", "longitude": "13.9150000"}   # SOLO le coordinate
    assert s["pronto"]["stato"] == "ok" and "Posizione verificata sulla mappa" in s["pronto"]["testo"]
    m = out["mappa"]
    assert m["hash"] == "#/edifici/mappa/edificio/51" and m["dialogo"] is False
    assert m["punti"] == [51] and m["attivo"] == {"selected": True} and m["pannello"] is True
    assert m["stato"]["azioni"] is False and m["stato"]["punto"] is False and m["segnaposti"] == 0   # punto consumato


@node
def test_b02_annulla_non_scrive_e_il_punto_resta_in_memoria_fra_le_pagine(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); M().emit('load'); await wait();
      // tocco sulla mappa SOLO in modalita' «scegli il punto»
      M().emit('click', { lngLat: { lng: 13.9262, lat: 42.8341 } }); await wait();
      const senzaModo = stato();
      pq('[data-insert-pick]').dispatch('click'); await wait();
      M().emit('click', { lngLat: { lng: 13.9262, lat: 42.8341 } }); await wait();
      const conModo = { ...stato(), premuto: pq('[data-insert-pick]').getAttribute('aria-pressed') };
      // trascinamento = correzione
      K()[0].setLngLat([13.9265, 42.8345]); K()[0].fire('dragend'); await wait();
      const trascinato = stato().testo;
      pq('[data-insert-cancel]').dispatch('click'); await wait();
      const annullato = { ...stato(), premuto: pq('[data-insert-pick]').getAttribute('aria-pressed'), scritture: scritte() };
      // di nuovo: scelta, conferma, poi via su un'altra pagina e ritorno
      pq('[data-insert-pick]').dispatch('click'); await wait();
      M().emit('click', { lngLat: { lng: 13.9262, lat: 42.8341 } }); await wait();
      M().setZoom(17); await wait();
      await confermaPunto();
      const confermato = stato();
      await vai('#/edifici');
      const altrove = { hash: window.location.hash, mappe: __maps.filter((m) => !m.removed).length };
      await vai('#/edifici/mappa'); M().emit('load'); await wait();
      const ritorno = { ...stato(), nota: pq('[data-insert-chosen-note]').textContent, centro: M().jump && M().jump.center };
      P().querySelectorAll('[data-insert-cancel]').find((b) => !b.parentNode.parentNode.hidden).dispatch('click'); await wait();
      await vai('#/edifici'); await vai('#/edifici/mappa'); M().emit('load'); await wait();
      const dopoAnnulla = { ...stato(), scritture: scritte() };
      report({ senzaModo, conModo, trascinato, annullato, confermato, altrove, ritorno, dopoAnnulla });
    """
    out = _run(staged, scenario, _rotte(liste=[VUOTA]))
    assert out["senzaModo"]["segnaposti"] == 0 and out["senzaModo"]["punto"] is False
    assert out["conModo"]["segnaposti"] == 1 and out["conModo"]["punto"] is True and out["conModo"]["premuto"] == "true"
    assert out["conModo"]["testo"] == "Punto provvisorio: 42.83410, 13.92620"
    assert out["trascinato"] == "Punto provvisorio: 42.83450, 13.92650"
    a = out["annullato"]
    assert a["segnaposti"] == 0 and a["punto"] is False and a["azioni"] is False and a["premuto"] == "false" and a["scritture"] == []
    assert out["confermato"]["azioni"] is True and out["confermato"]["scelto"] == "Punto scelto: 42.83410, 13.92620"
    assert out["altrove"] == {"hash": "#/edifici", "mappe": 0}
    r = out["ritorno"]
    assert r["azioni"] is True and r["scelto"] == "Punto scelto: 42.83410, 13.92620" and r["segnaposti"] == 1
    assert r["nota"].startswith("Punto scelto in attesa") and r["centro"] == [13.9262, 42.8341]
    assert out["dopoAnnulla"]["azioni"] is False and out["dopoAnnulla"]["segnaposti"] == 0 and out["dopoAnnulla"]["scritture"] == []


@node
def test_b03_collegamento_fallito_nessun_doppione_riprova_e_recupero_dal_posizionamento(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); M().emit('load'); await wait();
      await cerca('Via Roma 25, Tortoreto'); await scegliRisultato('house');
      await confermaPunto();
      await azione('building');
      campo('#wz-declared', '6'); q('[data-wizard-step="1"]').dispatch('submit'); await wait(); await wait();
      D().querySelector('[data-path="building"]').dispatch('click'); await wait(); await wait();
      D().querySelector('[data-create-building]').dispatch('click'); await wait();
      D().querySelector('[data-save-building]').dispatch('click'); await wait(); await wait(); await wait();
      const fallito = { passo: passo(), pronto: pronto(), scritture: scritte().map((w) => w.slice(0, 2)),
                        riprova: !!D().querySelector('[data-ready-retry]'), posiziona: D().querySelector('[data-ready-place]').getAttribute('href'),
                        vedi: !!D().querySelector('[data-ready-map]') };
      // recupero 1: «Riprova» con lo stesso punto -> nessun secondo edificio
      D().querySelector('[data-ready-retry]').dispatch('click'); await wait(); await wait();
      const riprovato = { pronto: pronto(), scritture: scritte().map((w) => w.slice(0, 2)) };
      report({ fallito, riprovato });
    """
    out = _run(staged, scenario, _rotte(liste=[VUOTA, VUOTA], posizione=[POSIZIONE_KO, POSIZIONE_OK]))
    f = out["fallito"]
    assert f["passo"] == "5" and f["pronto"]["stato"] == "failed"
    assert "NON collegata" in f["pronto"]["testo"] and "non viene duplicato" in f["pronto"]["testo"]
    assert f["scritture"] == [["POST", "/api/property/buildings"], ["POST", "/api/property/buildings/51/position"]]
    assert f["riprova"] is True and f["vedi"] is False and f["posiziona"] == "#/edifici/mappa/posiziona/51"
    r = out["riprovato"]
    assert r["pronto"]["stato"] == "ok"
    assert r["scritture"] == [["POST", "/api/property/buildings"], ["POST", "/api/property/buildings/51/position"],
                              ["POST", "/api/property/buildings/51/position"]]                      # UN solo edificio

    # recupero 2: la pagina di posizionamento riparte dal punto di Zona (dopo un fallimento)
    scenario2 = r"""
      await wait(); await wait(); M().emit('load'); await wait();
      await cerca('Via Roma 25, Tortoreto'); await scegliRisultato('house');
      await confermaPunto();
      await azione('building');
      campo('#wz-declared', '6'); q('[data-wizard-step="1"]').dispatch('submit'); await wait(); await wait();
      D().querySelector('[data-path="building"]').dispatch('click'); await wait(); await wait();
      D().querySelector('[data-create-building]').dispatch('click'); await wait();
      D().querySelector('[data-save-building]').dispatch('click'); await wait(); await wait(); await wait();
      const fallito = pronto().stato;
      await vai(D().querySelector('[data-ready-place]').getAttribute('href')); M().emit('load'); await wait();
      const posiziona = { hash: window.location.hash, segnaposti: K().length, p: K().length ? K()[0].p : null, zoom: M().getZoom(),
                          hint: C().querySelector('[data-place-hint]').textContent, conferma: !C().querySelector('[data-place-confirm]').disabled };
      C().querySelector('[data-place-confirm]').dispatch('click'); await wait(); await wait(); await window._fire('hashchange'); await wait(); await wait();
      M().emit('load'); await wait();
      const dopo = { hash: window.location.hash, scritture: scritte().map((w) => w.slice(0, 2)), stato: stato() };
      report({ fallito, posiziona, dopo });
    """
    out = _run(staged, scenario2, _rotte(liste=[VUOTA, VUOTA, CON_51], posizione=[POSIZIONE_KO, POSIZIONE_OK], dettaglio=SALVATO))
    assert out["fallito"] == "failed"
    p = out["posiziona"]
    assert p["hash"] == "#/edifici/mappa/posiziona/51" and p["segnaposti"] == 1 and p["p"] == [13.915, 42.803] and p["zoom"] == 18
    assert p["hint"].startswith("Punto scelto su Zona") and p["conferma"] is True
    assert out["dopo"]["hash"] == "#/edifici/mappa" and out["dopo"]["stato"]["azioni"] is False and out["dopo"]["stato"]["segnaposti"] == 0
    assert out["dopo"]["scritture"] == [["POST", "/api/property/buildings"], ["POST", "/api/property/buildings/51/position"],
                                        ["POST", "/api/property/buildings/51/position"]]


@node
def test_b04_collega_un_edificio_esistente_e_nuovo_immobile_con_la_nota(staged):  # noqa: F711
    scenario = r"""
      await wait(); await wait(); M().emit('load'); await wait();
      pq('[data-insert-pick]').dispatch('click'); await wait();
      M().emit('click', { lngLat: { lng: 13.915, lat: 42.803 } }); await wait();
      M().setZoom(18); await wait();
      await confermaPunto();
      const nota = pq('[data-insert-independent-note]').textContent;
      await azione('link');
      const apertura = { link: stato().link, scritture: scritte() };
      pq('[data-insert-link-input]').value = 'Roma'; pq('[data-insert-link]').dispatch('submit'); await wait(); await wait();
      const trovati = pq('[data-insert-link-results]').querySelectorAll('[data-insert-candidate]').map((li) => [li.dataset.insertCandidate, li.visibleText()]);
      const lettura = chiamate().filter((c) => c.m === 'GET' && c.url.startsWith('/api/property/buildings?')).map((c) => c.url);
      pq('[data-insert-link-building="51"]').dispatch('click'); await wait(); await wait();
      const errore = { testo: pq('[data-insert-link-error]').textContent, nascosto: pq('[data-insert-link-error]').hidden,
                       bottone: pq('[data-insert-link-building="51"]').textContent, azioni: stato().azioni };
      pq('[data-insert-link-building="51"]').dispatch('click'); await wait(); await wait(); await window._fire('hashchange'); await wait(); await wait();
      M().emit('load'); await wait();
      const dopo = { hash: window.location.hash, scritture: scritte(), punti: (M().sources['stima-buildings'] || { data: { features: [] } }).data.features.map((f) => f.id),
                     stato: stato() };
      // «Nuovo immobile»: la procedura esistente, percorso da scegliere, con la nota gia' letta
      pq('[data-insert-pick]').dispatch('click'); await wait();
      M().emit('click', { lngLat: { lng: 13.915, lat: 42.803 } }); await wait(); M().setZoom(18); await wait();
      await confermaPunto();
      await azione('unit');
      const immobile = { passo: passo(), city: q('#wz-city').value, via: q('#wz-address').value, titolo: D().querySelector('.census-sheet-title').textContent };
      const c = q('#wz-city'); c.value = 'Tortoreto'; c.dispatch('change'); await wait();
      campo('#wz-declared', '1'); q('[data-wizard-step="1"]').dispatch('submit'); await wait(); await wait();
      const percorsi = { passo: passo(), attivo: D().querySelectorAll('[data-path]').filter((b) => b.classList.contains('active')).length,
                         voci: D().querySelectorAll('[data-path]').map((b) => b.dataset.path) };
      D().querySelector('[data-cancel]').dispatch('click'); await wait();
      const chiuso = { dialogo: !!D(), scritture: scritte().length, stato: stato() };
      report({ nota, apertura, trovati, lettura, errore, dopo, immobile, percorsi, chiuso });
    """
    liste = [VUOTA, rt.ok({"items": [{**SALVATO, "name": "Palazzina Zona"}, c4.EDIFICIO], "total": 2}), CON_51]
    out = _run(staged, scenario, _rotte(liste=liste, posizione=[POSIZIONE_KO, POSIZIONE_OK]))
    # SENTINELLA AGGIORNATA DA STIMA360 ZONA-3: la nota dice ora che il punto diventa la posizione
    # verificata dell'immobile indipendente al salvataggio (103) e che le unita' di palazzina stanno sull'edificio.
    assert out["nota"].startswith("Ville, case indipendenti") and "posizione verificata" in out["nota"] and "edificio" in out["nota"]
    assert out["apertura"] == {"link": True, "scritture": []}
    assert [t[0] for t in out["trovati"]] == ["51", "7"] and "Senza coordinate" in out["trovati"][0][1]
    assert out["lettura"][-1].startswith("/api/property/buildings?") and "search=Roma" in out["lettura"][-1]
    e = out["errore"]
    assert e["nascosto"] is False and e["testo"].startswith("Posizione non collegata.") and e["bottone"] == "Collega qui" and e["azioni"] is True
    d = out["dopo"]
    assert d["hash"] == "#/edifici/mappa/edificio/51" and d["punti"] == [51] and d["stato"]["azioni"] is False
    assert [w[:2] for w in d["scritture"]] == [["POST", "/api/property/buildings/51/position"], ["POST", "/api/property/buildings/51/position"]]
    assert all(w[2] == {"latitude": "42.8030000", "longitude": "13.9150000"} for w in d["scritture"])
    assert out["immobile"] == {"passo": "1", "city": "", "via": "", "titolo": "Dove si trova?"}      # dal tocco: nulla da precompilare
    assert out["percorsi"] == {"passo": "2", "attivo": 0, "voci": ["autonomous", "building", "single"]}
    assert out["chiuso"]["dialogo"] is False and out["chiuso"]["scritture"] == 2 and out["chiuso"]["stato"]["azioni"] is True
