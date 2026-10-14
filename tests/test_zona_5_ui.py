"""STIMA360 ZONA-5 - scelta del punto con un gesto, indirizzo individuato e conferma, senza database.

  A  funzioni pure e statici: campi dell'indirizzo da un risultato (civico mai
     inventato), «luogo» dichiarato per la procedura guidata, riepilogo; il
     modulo chiama la ricerca inversa UNA volta per gesto e mai scrivendo; lo
     zoom del doppio clic e' spento; la pressione prolungata ha soglie di tempo
     e di spostamento; la mappa generale non cambia (nessun gesto nel suo codice);
  B  Shell VERA sullo stub DOM (MapLibre finta con gesti):
     b01 doppio clic (computer) -> punto provvisorio -> UNA ricerca inversa con le
         coordinate del punto -> «Indirizzo individuato» con via, civico, Comune,
         CAP -> «Conferma punto» spento finche' l'indirizzo non e' confermato ->
         conferma -> punto -> azioni; doppio clic da touch o su un marker: niente;
         zoom del doppio clic spento; nessuna ricerca per zoom/spostamento;
     b02 pressione prolungata (smartphone): ferma -> gesto; con spostamento,
         rilasciata prima, da mouse o su un comando: niente;
     b03 indirizzo: civico assente resta vuoto (dicitura), modifica di un campo
         -> conferma decaduta, punto spostato -> decaduta e nessuna nuova
         ricerca, «Cerca di nuovo» -> una ricerca, servizio giu' -> compilazione
         a mano, nessun indirizzo -> compilazione a mano; «Annulla» -> nulla;
     b04 dal gesto alla procedura guidata: «Nuovo edificio» precompilato con
         l'indirizzo DICHIARATO (Comune del catalogo, via, civico) -> salvataggio
         -> posizione collegata (T4), come in ZONA-2.
Il percorso con backend, PostgreSQL e Chromium veri e' in tests/test_zona_5_browser_postgres.py.
"""
from __future__ import annotations

import json
import subprocess

from tests import test_a30_5_create_ui as a30_5
from tests import test_delete_arch_2b3_ui as t2b3
from tests import test_territorio_zona2_ui as z2
from tests import test_zona_3_ui as z3
from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)

node = t2b3.node
ASSETS = t2b3.ASSETS
MODELLO = ASSETS / "territorio" / "territorio-model.js"
VISTA = ASSETS / "views" / "territorio.js"
INSERISCI = ASSETS / "views" / "territorio-inserisci.js"
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
def test_a01_campi_luogo_dichiarato_e_riepilogo():
    out = _pure("""(() => {
      const inverso = { found: true, road: 'Via Roma', house_number: '25', town: 'Tortoreto', postcode: '64018', province: 'Teramo' };
      const senza = { found: true, road: 'Via Roma', house_number: null, town: 'Tortoreto', postcode: null };
      const diretto = { road: 'Via Roma', house_number: '27', town: 'Tortoreto', precision: 'house', house_number_matches: false };
      return {
        inverso: m.addressFieldsFrom(inverso), senza: m.addressFieldsFrom(senza), diretto: m.addressFieldsFrom(diretto), nulla: m.addressFieldsFrom(null),
        luogo: m.placeFromAddressFields(m.addressFieldsFrom(inverso)), luogoSenza: m.placeFromAddressFields(m.addressFieldsFrom(senza)),
        wizard: m.wizardFieldsFromPlace(m.placeFromAddressFields(m.addressFieldsFrom(inverso)), [{ name: 'Tortoreto', microzones: [] }]),
        wizardSenza: m.wizardFieldsFromPlace(m.placeFromAddressFields(m.addressFieldsFrom(senza)), [{ name: 'Tortoreto', microzones: [] }]),
        riepiloghi: [m.addressSummary(inverso), m.addressSummary(senza), m.addressSummary({ town: 'Teramo' }), m.addressSummary({})],
      };
    })()""")
    assert out["inverso"] == {"road": "Via Roma", "house_number": "25", "town": "Tortoreto", "postcode": "64018"}
    assert out["senza"] == {"road": "Via Roma", "house_number": "", "town": "Tortoreto", "postcode": ""}       # civico assente: vuoto
    assert out["diretto"]["house_number"] == ""                                                             # civico diverso: mai spacciato
    assert out["nulla"] == {"road": "", "house_number": "", "town": "", "postcode": ""}
    assert out["luogo"] == {"road": "Via Roma", "house_number": "25", "town": "Tortoreto", "postcode": "64018", "precision": "house",
                            "house_number_matches": True, "label": "25, Via Roma, 64018 Tortoreto", "declared": True}
    assert out["luogoSenza"]["precision"] == "street" and out["luogoSenza"]["house_number"] is None and out["luogoSenza"]["house_number_matches"] is False
    assert out["wizard"] == {"city": "Tortoreto", "address": "Via Roma", "civic_number": "25"}
    assert out["wizardSenza"] == {"city": "Tortoreto", "address": "Via Roma", "civic_number": ""}
    assert out["riepiloghi"] == ["Via Roma 25, 64018 Tortoreto", "Via Roma, Tortoreto", "Teramo", "Indirizzo non indicato"]


def test_a02_statici_una_ricerca_per_gesto_mai_scrivendo_gesti_con_soglie():
    ins = _codice(INSERISCI)
    assert ins.count("api.reverseGeocode(") == 1 and "async function cercaIndirizzo()" in ins
    assert "map.doubleClickZoom.disable()" in ins and "map.on('dblclick'" in ins
    assert "const PRESSIONE_MS = 600;" in ins and "const PRESSIONE_PX = 10;" in ins
    assert "ev.pointerType !== 'touch' || !ev.isPrimary" in ins and "closest('button, a, input, .maplibregl-ctrl, .maplibregl-marker, [data-gps]')" in ins
    assert "daTocco(e.originalEvent) || suMarker(e)" in ins
    # la ricerca inversa SOLO da gesto / GPS / «Cerca di nuovo»: mai da trascinamento, zoom, spostamento
    for riga in ins.splitlines():
        if "cercaIndirizzo()" in riga and "function" not in riga:
            assert "lookup" in riga or "ricercaIndirizzo" in riga, riga
    assert "map.on('zoomend', aggiorna);" in ins and "map.on('move'" not in ins and "map.on('drag'" not in ins
    drag = ins[ins.index("provvisorio.on('dragend'"):]
    assert "cercaIndirizzo" not in drag[:drag.index("});")] and "mostraIndirizzo('moved'" in drag[:drag.index("});")]
    assert "Confermi che questo è l’indirizzo corretto?" in ins and "Indirizzo individuato" in ins
    assert "indirizzoEl.dataset.addressConfirmed" in ins and "indirizzoOk()" in ins
    api = _codice(API)
    assert "`/reverse-geocode${query({ lat, lon: lng })}`" in api
    vista = _codice(VISTA)
    for vietata in ("dblclick", "pointerdown", "reverseGeocode", "doubleClickZoom"):
        assert vietata not in vista, vietata                                        # la mappa generale non cambia


@node
def test_a03_node_check():
    for file in (MODELLO, INSERISCI, API):
        esito = subprocess.run([a30_5.NODE, "--check", str(file)], capture_output=True, text=True, timeout=30)
        assert esito.returncode == 0, esito.stderr


# ---------------------------------------------------------------------------
# B - Shell vera sullo stub DOM, con una MapLibre finta che conosce i gesti
# ---------------------------------------------------------------------------

GESTI = r"""
__dom.El.prototype.getBoundingClientRect ||= function () { return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 }; };
(() => {
  const Base = globalThis.maplibregl.Map;
  globalThis.maplibregl.Map = class extends Base {
    constructor(o) { super(o); this.doubleClickZoom = { disabled: false, disable: () => { this.doubleClickZoom.disabled = true; } }; this.hit = []; }
    getCanvasContainer() { return this.o.container; }
    unproject(p) { return { lng: 13.9 + p[0] / 10000, lat: 42.8 + p[1] / 10000 }; }
    queryRenderedFeatures() { return this.hit; }
  };
})();
"""

HELPERS = z3.HELPERS + r"""
const A = () => pq('[data-insert-address]');
const indirizzo = () => ({ visibile: !A().hidden, stato: A().dataset.addressState || null, confermato: A().dataset.addressConfirmed === 'true',
                           testo: pq('[data-address-status]').textContent, via: pq('[data-address-road]').value, civico: pq('[data-address-civic]').value,
                           comune: pq('[data-address-town]').value, cap: pq('[data-address-postcode]').value,
                           bottone: pq('[data-address-confirm]').textContent, bloccato: !!pq('[data-address-confirm]').disabled });
const inverse = () => chiamate().filter((c) => c.url.startsWith('/api/property/reverse-geocode')).map((c) => c.url);
async function doppioClic(lng = 13.9262, lat = 42.8341, extra = {}) {
  M().emit('dblclick', { lngLat: { lng, lat }, point: [10, 10], originalEvent: { preventDefault() {} }, ...extra }); await wait(); await wait();
}
const cont = () => M().getCanvasContainer();
async function pressione({ x = 100, y = 50, tipo = 'touch', target = null, muovi = 0, rilascia = false, attendi = 700 } = {}) {
  cont().dispatch('pointerdown', { pointerType: tipo, isPrimary: true, pointerId: 1, clientX: x, clientY: y, target: target || cont() });
  if (muovi) cont().dispatch('pointermove', { pointerId: 1, clientX: x + muovi, clientY: y });
  if (rilascia) cont().dispatch('pointerup', { pointerId: 1 });
  await wait(attendi); await wait();
}
async function confermaIndirizzo() { pq('[data-address-confirm]').dispatch('click'); await wait(); }
const campoIndirizzo = (sel, v) => { const e = pq(sel); e.value = v; e.dispatch('input'); };
"""

INVERSO = {"found": True, "road": "Via Roma", "house_number": "25", "town": "Tortoreto", "postcode": "64018", "province": "Teramo",
           "label": "25, Via Roma, Tortoreto, TE, 64018, Italia", "precision": "house", "latitude": "42.8341000", "longitude": "13.9262000",
           "cached": False, "attribution": "© OpenStreetMap contributors"}
SENZA_CIVICO = {**INVERSO, "house_number": None, "postcode": None, "precision": "street", "label": "Via Roma, Tortoreto, TE, Italia"}
NIENTE = {**INVERSO, "found": False, "road": None, "house_number": None, "town": None, "postcode": None, "province": None, "label": None, "precision": None}


def _rotte(inverso=(rt.ok(INVERSO),), **kw):
    posizioni = kw.pop("posizioni", [rt.ok({"items": [], "total": 0})])
    return z3._rotte(posizioni=posizioni, extra=[("GET", "/api/property/reverse-geocode", list(inverso)),
                                                ("POST", "/api/property/buildings/51/position", [z2.POSIZIONE_OK])], **kw) + GESTI


def _run(staged, scenario, rotte, hash="#/edifici/mappa"):  # noqa: F811
    return t2b3._run(staged, HELPERS + scenario, rotte, hash)


@node
def test_b01_doppio_clic_punto_ricerca_inversa_indirizzo_e_conferma_obbligatoria(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); M().emit('load'); await wait();
      const apertura = { zoomDoppio: M().doubleClickZoom.disabled, inverse: inverse().length, punto: stato().punto, indirizzo: indirizzo().visibile };
      M().setZoom(16); await wait(); M().emit('zoomend'); M().emit('moveend'); await wait();
      const dopoZoom = { inverse: inverse().length, punto: stato().punto };
      // doppio clic da touch o su un marker: niente
      await doppioClic(13.93, 42.83, { originalEvent: { pointerType: 'touch', preventDefault() {} } });
      M().hit = [{ id: 9 }]; await doppioClic(13.93, 42.83); M().hit = [];
      const ignorati = { punto: stato().punto, inverse: inverse().length, segnaposti: K().length };
      // il gesto vero
      await doppioClic();
      const gesto = { ...stato(), indirizzo: indirizzo(), inverse: inverse(), segnaposto: K()[0].p, aperto: !!P().open };
      M().setZoom(18); await wait();
      const senzaConferma = { conferma: stato().conferma, hint: stato().hint };
      await confermaIndirizzo();
      const confermatoIndirizzo = { ...indirizzo(), conferma: stato().conferma, hint: stato().hint };
      await confermaPunto();
      const confermato = { ...stato(), nota: pq('[data-insert-chosen-note]').textContent, scritture: scritte(), inverse: inverse().length,
                           carte: pq('[data-insert-cards]').querySelectorAll('[data-insert-action]').map((b) => b.dataset.insertAction) };
      // un nuovo gesto dopo la conferma: punto nuovo, ricerca nuova, conferma da rifare
      await doppioClic(13.9300, 42.8400);
      const nuovo = { ...stato(), indirizzo: indirizzo(), inverse: inverse().length, segnaposto: K()[0].p };
      report({ apertura, dopoZoom, ignorati, gesto, senzaConferma, confermatoIndirizzo, confermato, nuovo });
    """
    out = _run(staged, scenario, _rotte())
    assert out["apertura"] == {"zoomDoppio": True, "inverse": 0, "punto": False, "indirizzo": False}
    assert out["dopoZoom"] == {"inverse": 0, "punto": False}                                            # zoom e spostamento: niente
    assert out["ignorati"] == {"punto": False, "inverse": 0, "segnaposti": 0}
    g = out["gesto"]
    assert g["punto"] is True and g["azioni"] is False and g["segnaposto"] == [13.9262, 42.8341] and g["trascinabile"] is True and g["aperto"] is True
    assert g["inverse"] == ["/api/property/reverse-geocode?lat=42.8341000&lon=13.9262000"]
    assert g["indirizzo"]["visibile"] is True and g["indirizzo"]["stato"] == "found" and g["indirizzo"]["confermato"] is False
    assert {k: g["indirizzo"][k] for k in ("via", "civico", "comune", "cap")} == {"via": "Via Roma", "civico": "25", "comune": "Tortoreto", "cap": "64018"}
    assert g["indirizzo"]["testo"].startswith("Indirizzo suggerito dalla mappa") and g["indirizzo"]["bottone"] == "Conferma indirizzo"
    assert out["senzaConferma"]["conferma"] is False and "Conferma prima l’indirizzo" in out["senzaConferma"]["hint"]
    ci = out["confermatoIndirizzo"]
    assert ci["confermato"] is True and ci["stato"] == "confirmed" and ci["bottone"] == "Indirizzo confermato" and ci["bloccato"] is True
    assert ci["testo"] == "Indirizzo confermato. Via Roma 25, 64018 Tortoreto" and ci["conferma"] is True
    c = out["confermato"]
    assert c["azioni"] is True and c["nota"] == "Indirizzo dichiarato: Via Roma 25, 64018 Tortoreto" and c["scritture"] == [] and c["inverse"] == 1
    assert c["carte"] == ["building", "unit", "link", "link-property"]
    n = out["nuovo"]
    assert n["punto"] is True and n["azioni"] is False and n["inverse"] == 2 and n["segnaposto"] == [13.93, 42.84]
    assert n["indirizzo"]["stato"] == "found" and n["indirizzo"]["confermato"] is False


@node
def test_b02_pressione_prolungata_solo_ferma_da_touch_e_non_sui_comandi(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); M().emit('load'); await wait();
      await pressione({ muovi: 20 });                                   // trascinamento
      const trascinato = { punto: stato().punto, inverse: inverse().length };
      await pressione({ rilascia: true });                              // rilasciata prima del tempo
      const breve = { punto: stato().punto, inverse: inverse().length };
      await pressione({ tipo: 'mouse' });                               // mouse: non e' una pressione prolungata
      const mouse = { punto: stato().punto, inverse: inverse().length };
      await pressione({ target: gq('[data-gps-button]') });             // su un comando della mappa
      const comando = { punto: stato().punto, inverse: inverse().length };
      await pressione({ attendi: 300 });                                // non ancora: il tempo non e' passato
      const presto = { punto: stato().punto };
      await wait(450);
      const gesto = { ...stato(), indirizzo: indirizzo(), inverse: inverse(), segnaposto: K()[0].p };
      report({ trascinato, breve, mouse, comando, presto, gesto });
    """
    pre = HELPERS + "\nconst gq = (s) => C().querySelector('[data-gps]').querySelector(s);\n"
    out = t2b3._run(staged, pre + scenario, _rotte(), "#/edifici/mappa")
    for nome in ("trascinato", "breve", "mouse", "comando"):
        assert out[nome] == {"punto": False, "inverse": 0}, nome
    assert out["presto"] == {"punto": False}
    g = out["gesto"]
    assert g["punto"] is True and g["segnaposto"] == [13.91, 42.805] and g["indirizzo"]["stato"] == "found"
    assert g["inverse"] == ["/api/property/reverse-geocode?lat=42.8050000&lon=13.9100000"]


@node
def test_b03_civico_assente_modifiche_spostamento_cerca_di_nuovo_errori_e_annulla(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); M().emit('load'); await wait();
      await doppioClic();
      const senzaCivico = indirizzo();
      campoIndirizzo('[data-address-civic]', '25 B');
      await confermaIndirizzo();
      const completato = indirizzo();
      campoIndirizzo('[data-address-town]', 'Alba Adriatica');
      const modificato = { ...indirizzo(), conferma: stato().conferma };
      await confermaIndirizzo();
      K()[0].setLngLat([13.9265, 42.8345]); K()[0].fire('dragend'); await wait();
      const spostato = { ...indirizzo(), conferma: stato().conferma, inverse: inverse().length };
      pq('[data-address-refresh]').dispatch('click'); await wait(); await wait();
      const ricercato = { ...indirizzo(), inverse: inverse() };
      // servizio giu': compilazione a mano e conferma
      await doppioClic(13.95, 42.85);
      const giu = indirizzo();
      campoIndirizzo('[data-address-road]', 'Contrada Colle'); campoIndirizzo('[data-address-town]', 'Tortoreto');
      await confermaIndirizzo();
      const aMano = indirizzo();
      // nessun indirizzo per il punto
      await doppioClic(13.96, 42.86);
      const niente = indirizzo();
      pq('[data-insert-point]').querySelector('[data-insert-cancel]').dispatch('click'); await wait();
      const annullato = { ...stato(), indirizzo: indirizzo().visibile, scritture: scritte() };
      report({ senzaCivico, completato, modificato, spostato, ricercato, giu, aMano, niente, annullato });
    """
    out = _run(staged, scenario, _rotte(inverso=[rt.ok(SENZA_CIVICO), rt.ok(INVERSO), {"status": 503, "body": {"detail": "x", "code": "GEOCODE_UNAVAILABLE"}}, rt.ok(NIENTE)]))
    s = out["senzaCivico"]
    assert s["stato"] == "found" and s["civico"] == "" and s["via"] == "Via Roma" and "Civico non trovato" in s["testo"]
    assert out["completato"]["confermato"] is True and out["completato"]["testo"] == "Indirizzo confermato. Via Roma 25 B, Tortoreto"
    assert out["modificato"]["confermato"] is False and out["modificato"]["stato"] == "edited" and out["modificato"]["conferma"] is False
    assert out["spostato"]["confermato"] is False and out["spostato"]["stato"] == "moved" and out["spostato"]["conferma"] is False and out["spostato"]["inverse"] == 1
    assert out["ricercato"]["stato"] == "found" and out["ricercato"]["civico"] == "25" and len(out["ricercato"]["inverse"]) == 2
    assert out["ricercato"]["inverse"][1] == "/api/property/reverse-geocode?lat=42.8345000&lon=13.9265000"
    assert out["giu"]["stato"] == "error" and out["giu"]["testo"].startswith("Ricerca dell’indirizzo non riuscita") and out["giu"]["via"] == ""
    assert out["aMano"]["confermato"] is True and out["aMano"]["testo"] == "Indirizzo confermato. Contrada Colle, Tortoreto"
    assert out["niente"]["stato"] == "missing" and out["niente"]["testo"].startswith("Nessun indirizzo trovato") and out["niente"]["via"] == ""
    assert out["annullato"]["punto"] is False and out["annullato"]["indirizzo"] is False and out["annullato"]["scritture"] == []


@node
def test_b04_dal_gesto_alla_procedura_guidata_precompilata_e_posizione_collegata(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); M().emit('load'); await wait();
      await doppioClic(13.915, 42.803);
      campoIndirizzo('[data-address-civic]', '25');
      await confermaIndirizzo();
      M().setZoom(18); await wait();
      await confermaPunto();
      await azione('building');
      const passo1 = { passo: passo(), city: q('#wz-city').value, via: q('#wz-address').value, civico: q('#wz-civic').value };
      campo('#wz-declared', '6'); q('[data-wizard-step="1"]').dispatch('submit'); await wait(); await wait();
      D().querySelector('[data-path="building"]').dispatch('click'); await wait(); await wait();
      D().querySelector('[data-create-building]').dispatch('click'); await wait();
      D().querySelector('[data-save-building]').dispatch('click'); await wait(); await wait(); await wait();
      const salvato = { passo: passo(), pronto: pronto(), scritture: scritte() };
      report({ passo1, salvato });
    """
    out = _run(staged, scenario, _rotte(inverso=[rt.ok(SENZA_CIVICO)], posizioni=[rt.ok({"items": [], "total": 0})]))
    assert out["passo1"] == {"passo": "1", "city": "Tortoreto", "via": "Via Roma", "civico": "25"}         # l'indirizzo DICHIARATO
    s = out["salvato"]
    assert s["passo"] == "5" and s["pronto"]["stato"] == "ok"
    assert [w[:2] for w in s["scritture"]] == [["POST", "/api/property/buildings"], ["POST", "/api/property/buildings/51/position"]]
    assert s["scritture"][0][2]["city"] == "Tortoreto" and s["scritture"][0][2]["address"] == "Via Roma" and s["scritture"][0][2]["civic_number"] == "25"
    assert s["scritture"][1][2] == {"latitude": "42.8030000", "longitude": "13.9150000"}
