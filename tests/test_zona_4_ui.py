"""STIMA360 ZONA-4 + ZONA-6 - la posizione dello smartphone su Zona, senza database.

SENTINELLA AGGIORNATA DA STIMA360 ZONA-6: la posizione non si chiede piu' con un
pulsante «La mia posizione» (una richiesta per tocco): all'apertura di Zona parte
`watchPosition`, il punto blu si aggiorna mentre ci si muove, la mappa si centra
UNA volta alla prima posizione (non se l'operatore l'ha gia' spostata), il GPS
si ferma (`clearWatch`) quando si esce da Zona. Il punto passa al flusso di
inserimento da «+ → Dalla mia posizione».

  A  funzioni pure e statici: testo della precisione, zoom per precisione,
     messaggi d'errore, cerchio della precisione; il modulo GPS usa SOLO
     `watchPosition`/`clearWatch` (mai `getCurrentPosition`), nessuna rete,
     nessun salvataggio; la mappa e il pannello non nominano la Geolocation API;
  B  Shell VERA sullo stub DOM con una Geolocation finta che CONTA le richieste:
     b01 all'apertura UNA sola richiesta continua; prima posizione -> mappa
         centrata (zoom dalla precisione), punto + cerchio; nuova posizione ->
         il punto si sposta, la vista NO; «+ → Dalla mia posizione» -> punto
         provvisorio nel pannello (trascinabile, confermabile solo a zoom >= 17)
         -> conferma -> le quattro azioni; nessuna scrittura; uscita da Zona ->
         `clearWatch`, una posizione tardiva non tocca nulla;
     b02 l'operatore sposta la mappa prima della prima posizione: la vista resta
         sua; permesso negato, posizione non disponibile, tempo scaduto,
         browser senza posizione: messaggi leggibili, nessun indicatore,
         «Dalla mia posizione» spento; il permesso negato ferma la richiesta.
Il percorso con backend, PostgreSQL e Chromium veri (GPS simulato) e' in
tests/test_zona_4_browser_postgres.py.
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
GPS = ASSETS / "territorio" / "gps.js"
VISTA = ASSETS / "views" / "territorio.js"
INSERISCI = ASSETS / "views" / "territorio-inserisci.js"
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
def test_a01_precisione_zoom_errori_e_cerchio():
    out = _pure("""(() => {
      const c = m.accuracyCircle(13.9262, 42.8341, 50, 8);
      const anello = c.geometry.coordinates[0];
      const fc = m.gpsFeatureCollection(13.9262, 42.8341, 12);
      return {
        testi: [m.accuracyText(12.4), m.accuracyText(3), m.accuracyText(1500), m.accuracyText(NaN), m.accuracyText(-1)],
        zoom: [m.zoomForAccuracy(10), m.zoomForAccuracy(50), m.zoomForAccuracy(120), m.zoomForAccuracy(900), m.zoomForAccuracy(NaN)],
        errori: [1, 2, 3, 'unsupported', 99, { code: 1 }].map(m.gpsErrorText),
        opzioni: m.GPS_OPTIONS,
        anello: { n: anello.length, chiuso: anello[0][0] === anello[anello.length - 1][0] && anello[0][1] === anello[anello.length - 1][1],
                  est: anello[0][0] > 13.9262, raggioLat: Math.round((anello[2][1] - 42.8341) * 111320) },
        fc: fc.features.map((f) => f.geometry.type), punto: fc.features[1].geometry.coordinates, acc: fc.features[0].properties.accuracy,
      };
    })()""")
    assert out["testi"] == ["Precisione stimata: circa 12 m", "Precisione stimata: pochi metri", "Precisione stimata: circa 1,5 km",
                            "Precisione non nota", "Precisione non nota"]
    assert out["zoom"] == [18, 17, 16, 15, 16]
    assert out["errori"][0].startswith("Permesso negato") and out["errori"][1].startswith("Posizione non disponibile")
    assert out["errori"][2].startswith("Tempo scaduto") and out["errori"][3].startswith("Questo browser non offre")
    assert out["errori"][4].startswith("Posizione non ricevuta") and out["errori"][5] == out["errori"][0]
    assert out["opzioni"] == {"enableHighAccuracy": True, "timeout": 15000, "maximumAge": 0}
    assert out["anello"] == {"n": 9, "chiuso": True, "est": True, "raggioLat": 50}
    assert out["fc"] == ["Polygon", "Point"] and out["punto"] == [13.9262, 42.8341] and out["acc"] == 12


def test_a02_statici_solo_watchposition_fermata_all_uscita_e_senza_rete():
    gps = _codice(GPS)
    assert gps.count("geo.watchPosition(") == 1 and "geo.clearWatch(guardia)" in gps
    for vietata in ("getCurrentPosition", "fetch(", "/api/", "apiPost", "apiPatch", "localStorage", "sessionStorage",
                    "confirmBuildingPosition", "confirmPropertyPosition", "setChosenPoint", "innerHTML"):
        assert vietata not in gps, vietata
    assert "maximumAge: 0" in _codice(MODELLO) and "{ ...GPS_OPTIONS }" in gps
    assert "if (fermo || !vivo()) return;" in gps and "if (!spostata && centra())" in gps
    assert "visibilitychange" in gps and "negato = true" in gps
    # la Geolocation API vive SOLO nel modulo GPS: ne' la mappa ne' il pannello la nominano
    for file in (VISTA, INSERISCI, MODELLO):
        assert "geolocation" not in _codice(file) and "watchPosition" not in _codice(file), file.name
    vista = _codice(VISTA)
    assert "gps = mountGps(area, { map, vivo, onChange: aggiornaGps, centra:" in vista
    assert "onMapClosed(map, () => gps.stop());" in vista
    ins = _codice(INSERISCI)
    assert "function placeProvisional(p)" in ins and "return { placeProvisional, startPick, startSearch, busy };" in ins
    assert "if (confermato) {\n      clearChosenPoint();\n      confermato = false;\n    }" in ins


@node
def test_a03_node_check():
    for file in (MODELLO, GPS, VISTA, INSERISCI):
        esito = subprocess.run([a30_5.NODE, "--check", str(file)], capture_output=True, text=True, timeout=30)
        assert esito.returncode == 0, esito.stderr


# ---------------------------------------------------------------------------
# B - Shell vera sullo stub DOM, con una Geolocation finta che conta
# ---------------------------------------------------------------------------

GEO_FINTA = r"""
// Node ha un `navigator` di sola lettura: qui se ne definisce uno proprio, con la Geolocation finta
Object.defineProperty(globalThis, 'navigator', { value: {}, configurable: true, writable: true });
globalThis.__geo = { watch: [], fermati: [], prossimo: 1 };
navigator.geolocation = {
  watchPosition(ok, err, opts) { const id = __geo.prossimo++; __geo.watch.push({ id, ok, err, opts }); return id; },
  clearWatch(id) { __geo.fermati.push(id); },
  getCurrentPosition() { throw new Error('getCurrentPosition non ammesso'); },
};
// una posizione (o un errore) a tutte le richieste ancora attive
globalThis.__muovi = (lat, lng, accuracy = 12.4) => { for (const w of __geo.watch) if (!__geo.fermati.includes(w.id)) w.ok({ coords: { latitude: lat, longitude: lng, accuracy } }); };
globalThis.__errore = (code) => { for (const w of __geo.watch) if (!__geo.fermati.includes(w.id)) w.err({ code, message: 'finto' }); };
"""

HELPERS = z3.HELPERS + r"""
const gs = () => C().querySelector('[data-gps-status]');
const gps = () => ({ richieste: __geo.watch.length, fermati: __geo.fermati.length, statoHidden: gs().hidden, stato: gs().dataset.gpsState || null,
                     testo: gs().textContent, sorgente: !!M().sources['stima-gps'], livelli: M().layers.filter((l) => l.source === 'stima-gps').map((l) => l.type),
                     punto: M().sources['stima-gps'] ? M().sources['stima-gps'].data.features[1].geometry.coordinates : null });
const piu = (sel) => C().querySelector(sel);
async function daMiaPosizione() { piu('[data-zone-add]').dispatch('click'); await wait(); piu('[data-zone-add-gps]').dispatch('click'); await wait(); await wait(); }
"""


def _run(staged, scenario, rotte, hash="#/edifici/mappa"):  # noqa: F811
    return t2b3._run(staged, HELPERS + scenario, rotte, hash)


# SENTINELLA AGGIORNATA DA STIMA360 ZONA-5: il punto dal GPS cerca anche l'indirizzo del punto (ricerca inversa,
# una sola chiamata) e il punto si conferma solo dopo aver confermato l'indirizzo.
REVERSE = {"found": True, "road": "Via Roma", "house_number": "25", "town": "Tortoreto", "postcode": "64018", "province": "Teramo",
           "label": "25, Via Roma, Tortoreto, TE, 64018, Italia", "precision": "house", "latitude": "42.8341000", "longitude": "13.9262000",
           "cached": False, "attribution": "© OpenStreetMap contributors"}


# la sorgente GPS della mappa finta: getSource(...).setData aggiorna i dati come MapLibre
SORGENTE_VIVA = r"""
maplibregl.Map.prototype.getSource = function (id) { const s = this.sources[id]; return s ? { setData: (d) => { s.data = d; } } : undefined; };
"""


def _rotte():
    return z3._rotte(posizioni=[rt.ok({"items": [], "total": 0})], extra=[("GET", "/api/property/reverse-geocode", [rt.ok(REVERSE)])]) + GEO_FINTA + SORGENTE_VIVA


@node
def test_b01_posizione_continua_centrata_una_volta_dalla_mia_posizione_e_stop_all_uscita(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); M().emit('load'); await wait();
      const apertura = { ...gps(), opzioni: __geo.watch[0] && __geo.watch[0].opts, bottoneVecchio: !!C().querySelector('[data-gps-button]'),
                         menuGps: piu('[data-zone-add-gps]').disabled, piu: piu('[data-zone-add]').textContent };
      __muovi(42.8341, 13.9262, 12.4); await wait();
      const prima = { ...gps(), centro: M().jump && M().jump.center, zoom: M().getZoom() };
      M().jump = null;
      __muovi(42.8350, 13.9270, 8); await wait();
      const seconda = { ...gps(), salto: M().jump, zoom: M().getZoom() };
      piu('[data-zone-add]').dispatch('click'); await wait();
      const menu = { aperto: !piu('[data-zone-add-menu]').hidden, gps: piu('[data-zone-add-gps]').disabled,
                     voci: piu('[data-zone-add-menu]').querySelectorAll('[role="menuitem"]').map((b) => b.querySelector('strong').textContent),
                     nota: piu('[data-zone-add-gps]').querySelector('span').textContent };
      piu('[data-zone-add-gps]').dispatch('click'); await wait(); await wait();
      const provvisorio = { ...stato(), segnaposto: K().length ? K()[0].p : null, aperto: !!P().open, nascosto: P().hidden, menuChiuso: piu('[data-zone-add-menu]').hidden,
                            indirizzo: pq('[data-insert-address]').dataset.addressState, via: pq('[data-address-road]').value,
                            inverse: chiamate().filter((c) => c.url.startsWith('/api/property/reverse-geocode')).map((c) => c.url) };
      pq('[data-address-confirm]').dispatch('click'); await wait();
      M().setZoom(16); await wait();
      const zoomBasso = stato().conferma;
      M().setZoom(18); await wait();
      await confermaPunto();
      const confermato = { ...stato(), carte: pq('[data-insert-cards]').querySelectorAll('[data-insert-action]').map((b) => b.dataset.insertAction), scritture: scritte() };
      const mappaPrima = M();
      await vai('#/edifici');
      let errore = null;
      try { __muovi(42.9, 13.9); await wait(); } catch (e) { errore = String(e); }
      const uscita = { hash: window.location.hash, fermati: __geo.fermati, rimossa: mappaPrima.removed, errore,
                       punto: mappaPrima.sources['stima-gps'].data.features[1].geometry.coordinates };
      report({ apertura, prima, seconda, menu, provvisorio, zoomBasso, confermato, uscita });
    """
    out = _run(staged, scenario, _rotte())
    a = out["apertura"]
    assert a["richieste"] == 1 and a["fermati"] == 0 and a["sorgente"] is False and a["bottoneVecchio"] is False   # UNA richiesta continua, subito
    assert a["opzioni"] == {"enableHighAccuracy": True, "timeout": 15000, "maximumAge": 0}
    assert a["stato"] == "busy" and a["testo"].startswith("Ricerca della tua posizione") and a["menuGps"] is True and a["piu"] == "+"
    p = out["prima"]
    assert p["centro"] == [13.9262, 42.8341] and p["zoom"] == 18 and p["statoHidden"] is True                    # centrata alla prima
    assert p["sorgente"] is True and p["livelli"] == ["fill", "line", "circle"] and p["punto"] == [13.9262, 42.8341]
    s2 = out["seconda"]
    assert s2["salto"] is None and s2["zoom"] == 18 and s2["punto"] == [13.927, 42.835] and s2["richieste"] == 1  # il punto si sposta, la vista no
    m = out["menu"]
    assert m["aperto"] is True and m["gps"] is False
    assert m["voci"] == ["Dalla mia posizione", "Scegli un punto sulla mappa", "Cerca un indirizzo"]
    assert "circa 8 m" in m["nota"]
    pr = out["provvisorio"]
    assert pr["punto"] is True and pr["azioni"] is False and pr["segnaposto"] == [13.927, 42.835] and pr["trascinabile"] is True
    assert pr["aperto"] is True and pr["nascosto"] is False and pr["menuChiuso"] is True
    assert pr["indirizzo"] == "found" and pr["via"] == "Via Roma"
    assert pr["inverse"] == ["/api/property/reverse-geocode?lat=42.8350000&lon=13.9270000"]
    assert out["zoomBasso"] is False                                                                   # la regola di T4 resta
    c = out["confermato"]
    assert c["azioni"] is True and c["carte"] == ["building", "unit", "link", "link-property"] and c["scritture"] == []
    u = out["uscita"]
    assert u["hash"] == "#/edifici" and u["fermati"] == [1] and u["rimossa"] is True and u["errore"] is None
    assert u["punto"] == [13.927, 42.835]                                                              # la posizione tardiva non tocca nulla


@node
def test_b02_vista_dell_operatore_errori_leggibili_e_browser_senza_posizione(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); M().emit('load'); await wait();
      M().emit('dragstart', { originalEvent: { type: 'touchstart' } });      // l'operatore sposta la mappa
      __muovi(42.8341, 13.9262); await wait();
      const spostata = { salto: M().jump, punto: gps().punto };
      report({ spostata });
    """
    out = _run(staged, scenario, _rotte())
    assert out["spostata"] == {"salto": None, "punto": [13.9262, 42.8341]}                              # la vista resta sua
    esiti = {}
    for codice, inizio in ((2, "Posizione non disponibile"), (3, "Tempo scaduto"), (1, "Permesso negato")):
        scenario = r"""
          await wait(); await wait(); M().emit('load'); await wait();
          __errore(%d); await wait();
          piu('[data-zone-add]').dispatch('click'); await wait();
          report({ ...gps(), menuGps: piu('[data-zone-add-gps]').disabled, nota: piu('[data-zone-add-gps]').querySelector('span').textContent });
        """ % codice
        esiti[codice] = _run(staged, scenario, _rotte())
        e = esiti[codice]
        assert e["stato"] == "error" and e["testo"].startswith(inizio) and e["sorgente"] is False, codice
        assert e["menuGps"] is True and e["nota"].startswith(inizio), codice
    assert esiti[1]["fermati"] == 1 and esiti[2]["fermati"] == 0                                         # negato: si ferma
    # browser senza Geolocation API: messaggio, nessuna eccezione
    scenario2 = r"""
      await wait(); await wait(); M().emit('load'); await wait();
      report({ ...gps() });
    """
    out = t2b3._run(staged, HELPERS + scenario2, _rotte() + "\ndelete navigator.geolocation;\n", "#/edifici/mappa")
    assert out["stato"] == "error" and out["testo"].startswith("Questo browser non offre") and out["richieste"] == 0
