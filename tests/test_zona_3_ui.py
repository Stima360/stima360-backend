"""STIMA360 ZONA-3 / C2 - immobili indipendenti con posizione verificata, senza database.

  A  funzioni pure e statici: solo i verificati senza edificio diventano marker
     (coordinate presenti non bastano, un'unita' di palazzina mai); titolo e
     luogo dell'immobile; il modulo di Zona conferma SOLO con la rotta della
     posizione (mai PATCH generiche), il gancio del wizard e' additivo, la mappa
     legge la lista leggera (nessuna scheda completa), le rotte nuove.
  B  Shell VERA sullo stub DOM (MapLibre finta):
     b01 la mappa disegna il livello degli immobili verificati (marker proprio),
         clic -> riquadro essenziale + pannello -> «Apri scheda immobile»
         (#/immobili/{id}); selezione nell'indirizzo; coordinate non verificate
         = nessun marker; un'unita' di palazzina = nessun marker;
     b02 Zona -> punto -> «Nuovo immobile» -> «Unita' autonoma» -> salva (la
         scheda ESISTENTE) -> la posizione si collega da sola (POST .../position,
         SOLE coordinate, dopo il POST dell'unita') -> mappa con il marker
         dell'immobile evidenziato, punto consumato;
     b03 collegamento fallito: «Immobile salvato, ma posizione non collegata»,
         UN solo POST dell'unita', «Riprova collegamento» con lo stesso id e lo
         stesso punto -> riuscito; un'unita' IN palazzina salvata da Zona va
         alla scheda come prima (nessun POST di posizione);
     b04 «Collega un immobile esistente»: solo gli immobili senza edificio, con
         lo stato della verifica in chiaro; «Collega qui» = conferma; «Annulla»
         non scrive nulla.
Il percorso con backend, PostgreSQL e Chromium veri e' in tests/test_zona_3_browser_postgres.py.
"""
from __future__ import annotations

import json
import subprocess

from tests import test_a30_5_create_ui as a30_5
from tests import test_a30_13b_quick_booking_ui as a30_13b
from tests import test_censimento_4_ui as c4
from tests import test_delete_arch_2b3_ui as t2b3
from tests import test_territorio_zona2_ui as z2
from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)

node = t2b3.node
ASSETS = t2b3.ASSETS
MODELLO = ASSETS / "territorio" / "territorio-model.js"
VISTA = ASSETS / "views" / "territorio.js"
INSERISCI = ASSETS / "views" / "territorio-inserisci.js"
WIZARD = ASSETS / "census" / "census-wizard.js"
API = ASSETS / "census" / "census-api.js"
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
def test_a01_solo_gli_indipendenti_verificati_sono_marker():
    out = _pure("""(() => {
      const ok = { id: 5, title: 'Villa Mare', code: 'IMM-5', address: 'Via Mare', civic_number: '3', city: 'Alba Adriatica', microzone: 'Lido',
                   latitude: '42.8300000', longitude: '13.9300000', position_verified_at: '2026-10-14T07:00:00Z', building_id: null };
      const lista = [ok, { ...ok, id: 6, position_verified_at: null },                       // coordinate presenti, non verificate
                     { ...ok, id: 7, building_id: 9 },                                       // unita' di palazzina: mai
                     { ...ok, id: 8, latitude: '95' }, { ...ok, id: 5 }, { id: 'x' }];      // non valida, doppione, id storto
      const v = m.verifiedProperties(lista);
      return { ids: v.map((x) => x.property.id), lnglat: [v[0].lng, v[0].lat],
               fc: m.propertyFeatureCollection(v).features.map((f) => [f.id, f.properties.property_id, f.geometry.coordinates]),
               titoli: [m.propertyTitle(ok), m.propertyTitle({ id: 2, code: 'IMM-2' }), m.propertyTitle({ id: 3, address: 'Via A', civic_number: '1' }), m.propertyTitle({ id: 4 })],
               dove: [m.propertyWhere(ok), m.propertyWhere({ city: 'Teramo' }), m.propertyWhere({})],
               vista: m.viewForAll([{ lng: 13.9, lat: 42.8 }], [{ lng: 13.95, lat: 42.85 }]),
               solo: m.viewForAll([], v), nulla: m.viewForAll([], []),
               azioni: m.INSERT_ACTIONS.map((a) => a.key) };
    })()""")
    assert out["ids"] == [5] and out["lnglat"] == [13.93, 42.83]
    assert out["fc"] == [[5, 5, [13.93, 42.83]]]
    assert out["titoli"] == ["Villa Mare", "IMM-2", "Via A 1", "Immobile 4"]
    assert out["dove"] == ["Via Mare 3 · Alba Adriatica (Lido)", "Teramo", ""]
    assert out["vista"] == {"bounds": [[13.9, 42.8], [13.95, 42.85]]}
    assert out["solo"] == {"center": [13.93, 42.83], "zoom": 16} and out["nulla"] is None
    assert out["azioni"] == ["building", "unit", "link", "link-property"]


def test_a02_statici_conferma_solo_dalla_rotta_gancio_additivo_lista_leggera():
    ins = _codice(INSERISCI)
    assert ins.count("api.confirmPropertyPosition(") == 1 and "esito.status !== 'verified'" in ins
    assert ins.count("api.confirmBuildingPosition(") == 1
    for vietata in ("updateProperty", "createUnit", "createBuilding", "apiPatch", "position_verified_by", "user_id", "fetch("):
        assert vietata not in ins, vietata
    assert "onUnitSaved: (unita) => unitaSalvata(unita)" in ins and "data-unit-recovery-retry" in ins
    assert "Immobile salvato, ma posizione non collegata" in ins
    assert "api.searchProperties(" in ins and "building_id === null" in ins                  # solo gli indipendenti
    wizard = _codice(WIZARD)
    assert "onUnitSaved = null" in wizard and "(await onUnitSaved(unita)) === true" in wizard
    assert "navigate('immobili', [unita.id])" in wizard                                        # senza gancio: come prima
    vista = _codice(VISTA)
    assert "api.listPropertyPositions" in _codice(VISTA) or "loadAllPropertyPositions" in vista
    assert "stima-properties-points" in vista and "data-open-property" in vista and "highlightPropertyAfterConfirm" in vista
    assert "api.getProperty(" not in vista and "searchProperties" not in vista                 # nessuna scheda completa
    api = _codice(API)
    assert "`/properties/${id(propertyId)}/position`" in api and "`/properties/positions${query({ limit, offset })}`" in api
    corpo = api[api.index("export function confirmPropertyPosition"):]
    corpo = corpo[:corpo.index("\n}\n")]
    assert "{ latitude, longitude }" in corpo and "position_verified" not in corpo and "user" not in corpo


@node
def test_a03_node_check():
    for file in (MODELLO, VISTA, INSERISCI, WIZARD, API):
        esito = subprocess.run([a30_5.NODE, "--check", str(file)], capture_output=True, text=True, timeout=30)
        assert esito.returncode == 0, esito.stderr


# ---------------------------------------------------------------------------
# B - Shell vera sullo stub DOM
# ---------------------------------------------------------------------------

HELPERS = z2.HELPERS + r"""
const PP = () => C().querySelector('[data-building-panel]');
const unita = (sel) => D().querySelector(sel);
"""

VILLA = {"id": 61, "code": "IMM-61", "title": "Villa Gabbiano", "property_type": "apartment", "record_kind": "census",
         "commercial_status": "draft", "address": "Via Gabbiano", "civic_number": "4", "city": "Tortoreto", "microzone": "Lido",
         "latitude": "42.8050000", "longitude": "13.9200000", "position_verified_at": "2026-10-14T07:00:00Z"}
NON_VERIFICATA = {**VILLA, "id": 62, "code": "IMM-62", "title": "Casa senza verifica", "position_verified_at": None}
IN_PALAZZINA = {**VILLA, "id": 63, "code": "IMM-63", "title": "Interno 3", "building_id": 7}
SALVATA = {**c4.CREATA, "id": 77, "code": "IMM-77", "building_id": None, "title": "Villa Nuova", "address": "Via Roma", "civic_number": "25",
           "city": "Tortoreto", "latitude": None, "longitude": None, "position_verified_at": None}
POS_OK = {"status": 200, "body": {"property_id": 77, "building_id": None, "independent": True, "status": "verified",
                                  "latitude": "42.8030000", "longitude": "13.9150000", "position_verified_at": "2026-10-14T08:00:00Z",
                                  "position_verified_by": {"id": 1, "name": "Olga"}, "in_trash": False}}
POS_KO = {"status": 503, "body": {"detail": "x", "code": "POSITION_NOT_INSTALLED"}}
VERIFICATA_77 = {**VILLA, "id": 77, "code": "IMM-77", "title": "Villa Nuova", "address": "Via Roma", "civic_number": "25",
                 "latitude": "42.8030000", "longitude": "13.9150000", "position_verified_at": "2026-10-14T08:00:00Z"}


def _rotte(*, posizioni, posizione=(POS_OK,), unita=(), ricerca=None, extra=()):
    voci = [("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione("agency_owner"))]),
            ("GET", "/api/property/geocode", [rt.ok(z2.GEOCODE)]),
            ("GET", "/api/property/form-options", [rt.ok(c4.OPZIONI)]),
            *extra,
            ("GET", "/api/property/properties/positions", list(posizioni)),
            ("POST", "/api/property/properties/77/position", list(posizione)),
            ("POST", "/api/property/properties/61/position", [{"status": 200, "body": {**POS_OK["body"], "property_id": 61}}]),
            ("GET", "/api/property/properties?", [rt.ok(ricerca if ricerca is not None else {"items": [VILLA, NON_VERIFICATA, IN_PALAZZINA]})]),
            ("POST", "/api/property/census/units", list(unita or ({"status": 201, "body": SALVATA},))),
            ("GET", "/api/property/buildings/7", [rt.ok(c4.DETTAGLIO)]),
            ("GET", "/api/property/buildings?", [rt.ok({"items": [], "total": 0})]),
            ("POST", "/api/property/buildings", [{"status": 201, "body": z2.SALVATO}]),
            ("GET", "/api/", [rt.ok({"items": [], "total": 0})])]
    return z2.PRELUDIO + "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});" for m, p, r in voci)


def _run(staged, scenario, rotte, hash="#/edifici/mappa"):  # noqa: F811
    return t2b3._run(staged, HELPERS + scenario, rotte, hash)


LISTA = rt.ok({"items": [VILLA, NON_VERIFICATA, IN_PALAZZINA], "total": 3})


@node
def test_b01_marker_degli_indipendenti_verificati_riquadro_pannello_e_scheda(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); M().emit('load'); await wait();
      const sorgenti = Object.keys(M().sources);
      const punti = M().sources['stima-properties'].data.features.map((f) => f.id);
      const livelli = M().layers.map((l) => [l.id, l.source]);
      const letture = chiamate().filter((c) => c.m === 'GET').map((c) => c.url.split('?')[0]);
      M().clickLayer('stima-properties-points', 61); await wait(); await wait();
      const r = __popups[__popups.length - 1].node;
      const riquadro = { testo: r.visibleText(), apri: !!r.querySelector('[data-open-property="61"]'), hash: window.location.hash,
                         attivo: M().states[61], area: C().querySelector('[data-map]').dataset.activeProperty };
      const pannello = { id: PP().dataset.propertyPanel, hidden: PP().hidden, testo: PP().visibleText(),
                         link: PP().querySelector('[data-panel-open-property]').getAttribute('href') };
      r.querySelector('[data-open-property="61"]').dispatch('click'); await window._fire('hashchange'); await wait(); await wait();
      const scheda = { hash: window.location.hash, mappe: __maps.filter((m) => !m.removed).length };
      await vai('#/edifici/mappa/immobile/61'); M().emit('load'); await wait(); await wait();
      const daRotta = { attivo: M().states[61], pannello: PP().dataset.propertyPanel, centro: M().jump && M().jump.center, zoom: M().jump && M().jump.zoom };
      report({ sorgenti, punti, livelli, letture, riquadro, pannello, scheda, daRotta, scritture: scritte() });
    """
    out = _run(staged, scenario, _rotte(posizioni=[LISTA]))
    assert out["sorgenti"] == ["stima-buildings", "stima-properties"] and out["punti"] == [61]   # ne' 62 (non verificata) ne' 63 (palazzina)
    assert out["livelli"] == [["stima-buildings-points", "stima-buildings"], ["stima-properties-points", "stima-properties"]]
    assert out["letture"].count("/api/property/properties/positions") == 1 and "/api/property/properties/61" not in out["letture"]
    r = out["riquadro"]
    assert "Villa Gabbiano" in r["testo"] and "Via Gabbiano 4 · Tortoreto (Lido)" in r["testo"] and "Appartamento" in r["testo"] and "IMM-61" in r["testo"]
    # (l'indirizzo con la selezione passa da history.replaceState, che lo stub non ha: lo prova Chromium)
    assert r["apri"] is True and r["attivo"] == {"selected": True} and r["area"] == "61"
    p = out["pannello"]
    assert p["id"] == "61" and p["hidden"] is False and p["link"] == "#/immobili/61" and "Verificata su Zona" in p["testo"]
    assert out["scheda"] == {"hash": "#/immobili/61", "mappe": 0}
    assert out["daRotta"]["attivo"] == {"selected": True} and out["daRotta"]["pannello"] == "61"
    assert out["daRotta"]["centro"] == [13.92, 42.805] and out["daRotta"]["zoom"] == 17
    assert out["scritture"] == []


@node
def test_b02_nuovo_immobile_indipendente_posizione_collegata_da_sola_e_marker(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); M().emit('load'); await wait();
      await cerca('Via Roma 25, Tortoreto'); await scegliRisultato('house');
      await confermaPunto();
      await azione('unit');
      const passo1 = { passo: passo(), city: q('#wz-city').value, via: q('#wz-address').value, civico: q('#wz-civic').value };
      q('#wz-unknown').checked = true; q('#wz-unknown').dispatch('change');
      q('[data-wizard-step="1"]').dispatch('submit'); await wait(); await wait();
      D().querySelector('[data-path="autonomous"]').dispatch('click'); await wait(); await wait();
      const foglioUnita = { titolo: D().querySelector('.census-sheet-title').textContent, via: unita('#us-address').value, civico: unita('#us-civic').value,
                       scritture: scritte() };
      unita('[data-unit-form]').dispatch('submit'); await wait(); await wait(); await wait(); await window._fire('hashchange'); await wait(); await wait();
      M().emit('load'); await wait(); await wait();
      const dopo = { hash: window.location.hash, dialogo: !!D(), scritture: scritte(),
                     punti: (M().sources['stima-properties'] || { data: { features: [] } }).data.features.map((f) => f.id),
                     attivo: M().states[77], pannello: PP().dataset.propertyPanel, stato: stato(), segnaposti: K().length };
      report({ passo1, foglio: foglioUnita, dopo });
    """
    out = _run(staged, scenario, _rotte(posizioni=[rt.ok({"items": [], "total": 0}), rt.ok({"items": [VERIFICATA_77], "total": 1})]))
    assert out["passo1"] == {"passo": "1", "city": "Tortoreto", "via": "Via Roma", "civico": "25"}
    assert out["foglio"]["titolo"] == "Nuovo immobile singolo" and out["foglio"]["via"] == "Via Roma" and out["foglio"]["civico"] == "25"
    assert out["foglio"]["scritture"] == []                                              # nulla scritto fino al salvataggio
    d = out["dopo"]
    assert [w[:2] for w in d["scritture"]] == [["POST", "/api/property/census/units"], ["POST", "/api/property/properties/77/position"]]
    assert "building_id" not in d["scritture"][0][2] and d["scritture"][0][2]["city"] == "Tortoreto"
    assert d["scritture"][1][2] == {"latitude": "42.8030000", "longitude": "13.9150000"}    # SOLO le coordinate
    assert d["hash"] == "#/edifici/mappa/immobile/77" and d["dialogo"] is False
    assert d["punti"] == [77] and d["attivo"] == {"selected": True} and d["pannello"] == "77"
    assert d["stato"]["azioni"] is False and d["segnaposti"] == 0                         # punto consumato


@node
def test_b03_collegamento_fallito_nessun_doppione_riprova_e_unita_in_palazzina_come_prima(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); M().emit('load'); await wait();
      await cerca('Via Roma 25, Tortoreto'); await scegliRisultato('house');
      await confermaPunto();
      await azione('unit');
      q('#wz-unknown').checked = true; q('#wz-unknown').dispatch('change');
      q('[data-wizard-step="1"]').dispatch('submit'); await wait(); await wait();
      D().querySelector('[data-path="autonomous"]').dispatch('click'); await wait(); await wait();
      unita('[data-unit-form]').dispatch('submit'); await wait(); await wait(); await wait();
      const fallito = { aperto: !!D(), titolo: D() && D().querySelector('.census-sheet-title').textContent,
                        recupero: D() && D().querySelector('[data-unit-recovery]') ? D().querySelector('[data-unit-recovery]').dataset.unitRecovery : null,
                        errore: D() && D().querySelector('[data-unit-recovery-error]').textContent,
                        scheda: D() && D().querySelector('[data-unit-recovery-open]').getAttribute('href'),
                        scritture: scritte().map((w) => w.slice(0, 2)), hash: window.location.hash, punto: !!chosen() };
      D().querySelector('[data-unit-recovery-retry]').dispatch('click'); await wait(); await wait(); await window._fire('hashchange'); await wait(); await wait();
      M().emit('load'); await wait();
      const riprovato = { hash: window.location.hash, dialogo: !!D(), scritture: scritte().map((w) => w.slice(0, 2)),
                          punti: (M().sources['stima-properties'] || { data: { features: [] } }).data.features.map((f) => f.id) };
      report({ fallito, riprovato });
    """
    pre = HELPERS + "\nconst chosen = () => C().querySelector('[data-insert-actions]') && !C().querySelector('[data-insert-actions]').hidden;\n"
    out = t2b3._run(staged, pre + scenario, _rotte(posizioni=[rt.ok({"items": [], "total": 0}), rt.ok({"items": [VERIFICATA_77], "total": 1})],
                                                   posizione=[POS_KO, POS_OK]), "#/edifici/mappa")
    f = out["fallito"]
    assert f["aperto"] is True and f["titolo"] == "Immobile salvato, ma posizione non collegata" and f["recupero"] == "77"
    assert "non è ancora disponibile" in f["errore"] and f["scheda"] == "#/immobili/77"
    assert f["scritture"] == [["POST", "/api/property/census/units"], ["POST", "/api/property/properties/77/position"]]
    assert f["hash"] == "#/edifici/mappa" and f["punto"] is True                           # il punto resta su Zona
    r = out["riprovato"]
    assert r["scritture"] == [["POST", "/api/property/census/units"], ["POST", "/api/property/properties/77/position"],
                              ["POST", "/api/property/properties/77/position"]]              # UN solo immobile
    assert r["hash"] == "#/edifici/mappa/immobile/77" and r["dialogo"] is False and r["punti"] == [77]

    # unita' IN palazzina salvata da Zona (percorso «palazzina» -> edificio esistente -> unita'): scheda come
    # prima, nessun POST di posizione dell'immobile (la posizione e' dell'edificio)
    scenario2 = r"""
      await wait(); await wait(); M().emit('load'); await wait();
      pq('[data-insert-pick]').dispatch('click'); await wait();
      M().emit('click', { lngLat: { lng: 13.915, lat: 42.803 } }); await wait(); M().setZoom(18); await wait();
      await confermaPunto();
      await azione('unit');
      const c = q('#wz-city'); c.value = 'Tortoreto'; c.dispatch('change'); await wait();
      q('#wz-unknown').checked = true; q('#wz-unknown').dispatch('change');
      q('[data-wizard-step="1"]').dispatch('submit'); await wait(); await wait();
      D().querySelector('[data-path="autonomous"]').dispatch('click'); await wait(); await wait();
      unita('[data-unit-form]').dispatch('submit'); await wait(); await wait(); await wait(); await window._fire('hashchange'); await wait(); await wait();
      report({ hash: window.location.hash, scritture: scritte().map((w) => w.slice(0, 2)) });
    """
    in_palazzina = {"status": 201, "body": {**SALVATA, "id": 78, "building_id": 7}}
    out = _run(staged, scenario2, _rotte(posizioni=[rt.ok({"items": [], "total": 0})], unita=[in_palazzina]))
    assert out["scritture"] == [["POST", "/api/property/census/units"]] and out["hash"] == "#/immobili/78"


@node
def test_b04_collega_un_immobile_esistente_solo_indipendenti_e_annulla(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); M().emit('load'); await wait();
      pq('[data-insert-pick]').dispatch('click'); await wait();
      M().emit('click', { lngLat: { lng: 13.915, lat: 42.803 } }); await wait(); M().setZoom(18); await wait();
      await confermaPunto();
      await azione('link-property');
      const apertura = { link: stato().link, etichetta: pq('[data-insert-link-label]').textContent, tipo: pq('[data-insert-link]').dataset.insertLinkKind };
      pq('[data-insert-link-input]').value = 'Villa'; pq('[data-insert-link]').dispatch('submit'); await wait(); await wait();
      const trovati = pq('[data-insert-link-results]').querySelectorAll('[data-insert-candidate-property]').map((li) => [li.dataset.insertCandidateProperty,
                        li.querySelector('[data-property-position-status]').dataset.propertyPositionStatus, li.visibleText()]);
      const statoRicerca = pq('[data-insert-link-status]').textContent;
      const lettura = chiamate().filter((c) => c.m === 'GET' && c.url.startsWith('/api/property/properties?')).map((c) => c.url);
      // «Annulla» prima di collegare: niente scritture, punto via
      pq('[data-insert-actions]').querySelector('[data-insert-cancel]').dispatch('click'); await wait();   // lo stub non ha i selettori discendenti
      const annullato = { stato: stato(), scritture: scritte() };
      // di nuovo: punto, «Collega un immobile esistente», «Collega qui»
      pq('[data-insert-pick]').dispatch('click'); await wait();
      M().emit('click', { lngLat: { lng: 13.915, lat: 42.803 } }); await wait(); M().setZoom(18); await wait();
      await confermaPunto();
      await azione('link-property');
      pq('[data-insert-link-input]').value = 'Villa'; pq('[data-insert-link]').dispatch('submit'); await wait(); await wait();
      pq('[data-insert-link-property="61"]').dispatch('click'); await wait(); await wait(); await window._fire('hashchange'); await wait(); await wait();
      M().emit('load'); await wait();
      const dopo = { hash: window.location.hash, scritture: scritte(), attivo: M().states[61], stato: stato() };
      report({ apertura, trovati, statoRicerca, lettura, annullato, dopo });
    """
    out = _run(staged, scenario, _rotte(posizioni=[LISTA]))
    assert out["apertura"] == {"link": True, "etichetta": "Quale immobile indipendente?", "tipo": "property"}
    assert [t[:2] for t in out["trovati"]] == [["61", "verified"], ["62", "unverified"]]            # 63 (palazzina) esclusa
    assert "Posizione verificata" in out["trovati"][0][2] and "Villa Gabbiano" in out["trovati"][0][2]
    assert "1 unità di palazzina escluse" in out["statoRicerca"]
    assert "search=Villa" in out["lettura"][-1] and "record_kind=all" in out["lettura"][-1]
    assert out["annullato"]["stato"]["azioni"] is False and out["annullato"]["scritture"] == []
    d = out["dopo"]
    assert d["hash"] == "#/edifici/mappa/immobile/61" and d["attivo"] == {"selected": True} and d["stato"]["azioni"] is False
    assert [w[:2] for w in d["scritture"]] == [["POST", "/api/property/properties/61/position"]]
    assert d["scritture"][0][2] == {"latitude": "42.8030000", "longitude": "13.9150000"}
