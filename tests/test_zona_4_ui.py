"""STIMA360 ZONA-4 - «La mia posizione» (GPS dello smartphone) su Zona, senza database.

  A  funzioni pure e statici: testo della precisione, zoom per precisione,
     messaggi d'errore, cerchio della precisione; il modulo GPS usa SOLO
     `getCurrentPosition` (mai `watchPosition`), una richiesta fresca
     (maximumAge 0), nessuna rete, nessun salvataggio; la mappa e il pannello
     non nominano la Geolocation API (vive solo in territorio/gps.js);
  B  Shell VERA sullo stub DOM con una Geolocation finta che CONTA le richieste:
     b01 nessuna richiesta all'apertura di Zona; tocco -> UNA richiesta (un
         secondo tocco durante l'attesa non ne fa un'altra) -> mappa centrata
         (zoom dalla precisione), indicatore disegnato (cerchio + punto),
         «Precisione stimata: circa 12 m», «Inserisci qui» -> punto provvisorio
         nel pannello (trascinabile, confermabile solo a zoom >= 17) -> conferma
         -> le quattro azioni; nessuna scrittura; un nuovo tocco = una nuova
         richiesta (mai un watch);
     b02 permesso negato, posizione non disponibile, tempo scaduto, browser
         senza posizione: messaggi leggibili, pulsante di nuovo attivo, nessun
         indicatore; si lascia la pagina durante la richiesta: la risposta
         tardiva non tocca nulla.
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


def test_a02_statici_solo_getcurrentposition_fresca_e_senza_rete():
    gps = _codice(GPS)
    assert gps.count("getCurrentPosition(") == 1 and "typeof geo.getCurrentPosition !== 'function'" in gps
    for vietata in ("watchPosition", "fetch(", "/api/", "apiPost", "apiPatch", "localStorage", "sessionStorage",
                    "confirmBuildingPosition", "confirmPropertyPosition", "setChosenPoint", "innerHTML"):
        assert vietata not in gps, vietata
    assert "maximumAge: 0" in _codice(MODELLO) and "{ ...GPS_OPTIONS }" in gps
    assert "'aria-label': 'La mia posizione'" in gps and "'aria-busy'" in gps and "if (inCorso) return;" in gps
    assert "if (!vivo()) return;" in gps and gps.count("fine();") == 2
    # la Geolocation API vive SOLO nel modulo GPS: ne' la mappa ne' il pannello la nominano
    for file in (VISTA, INSERISCI, MODELLO):
        assert "geolocation" not in _codice(file) and "getCurrentPosition" not in _codice(file), file.name
    vista = _codice(VISTA)
    assert "mountGps(area, { map, vivo, onInsertHere: (p) => inserimento.placeProvisional(p) })" in vista
    ins = _codice(INSERISCI)
    assert "function placeProvisional(p)" in ins and "return { placeProvisional };" in ins
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
globalThis.__geo = { chiamate: [], modo: 'ok', attesa: [] };
navigator.geolocation = {
  getCurrentPosition(ok, err, opts) {
    __geo.chiamate.push({ opts, t: Date.now() });
    const rispondi = () => {
      if (__geo.modo === 'ok') ok({ coords: { latitude: 42.8341, longitude: 13.9262, accuracy: 12.4 } });
      else if (__geo.modo === 'bad') ok({ coords: { latitude: 'x', longitude: null, accuracy: 1 } });
      else err({ code: Number(__geo.modo), message: 'finto' });
    };
    if (__geo.trattieni) __geo.attesa.push(rispondi); else setTimeout(rispondi, 0);
  },
  watchPosition() { throw new Error('watchPosition non ammesso'); },
};
"""

HELPERS = z3.HELPERS + r"""
const G = () => C().querySelector('[data-gps]');
const gq = (s) => G().querySelector(s);
const gps = () => ({ chiamate: __geo.chiamate.length, busy: gq('[data-gps-button]').getAttribute('aria-busy'), disabilitato: !!gq('[data-gps-button]').disabled,
                     statoHidden: gq('[data-gps-status]').hidden, stato: gq('[data-gps-status]').dataset.gpsState || null,
                     testo: gq('[data-gps-text]').textContent, inserisci: !gq('[data-gps-insert]').hidden,
                     sorgente: !!M().sources['stima-gps'], livelli: M().layers.filter((l) => l.source === 'stima-gps').map((l) => l.type) });
async function tocca() { gq('[data-gps-button]').dispatch('click'); await wait(); await wait(); }
"""


def _run(staged, scenario, rotte, hash="#/edifici/mappa"):  # noqa: F811
    return t2b3._run(staged, HELPERS + scenario, rotte, hash)


# SENTINELLA AGGIORNATA DA STIMA360 ZONA-5: «Inserisci qui» cerca anche l'indirizzo del punto (ricerca inversa,
# una sola chiamata) e il punto si conferma solo dopo aver confermato l'indirizzo.
REVERSE = {"found": True, "road": "Via Roma", "house_number": "25", "town": "Tortoreto", "postcode": "64018", "province": "Teramo",
           "label": "25, Via Roma, Tortoreto, TE, 64018, Italia", "precision": "house", "latitude": "42.8341000", "longitude": "13.9262000",
           "cached": False, "attribution": "© OpenStreetMap contributors"}


def _rotte():
    return z3._rotte(posizioni=[rt.ok({"items": [], "total": 0})], extra=[("GET", "/api/property/reverse-geocode", [rt.ok(REVERSE)])]) + GEO_FINTA


@node
def test_b01_nessuna_richiesta_all_apertura_tocco_indicatore_inserisci_qui_e_conferma(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); M().emit('load'); await wait();
      const apertura = { ...gps(), etichetta: gq('[data-gps-button]').getAttribute('aria-label'), testoBottone: gq('[data-gps-button]').visibleText(),
                         icona: !!gq('[data-gps-button]').querySelector('svg'), dentroMappa: !!C().querySelector('[data-map]').querySelector('[data-gps]') };
      __geo.trattieni = true;
      gq('[data-gps-button]').dispatch('click'); await wait();
      const attesa = gps();
      gq('[data-gps-button]').dispatch('click'); await wait();           // tocco ripetuto durante l'attesa
      const doppio = { chiamate: __geo.chiamate.length };
      __geo.attesa.shift()(); await wait(); await wait();
      const ricevuta = { ...gps(), centro: M().jump && M().jump.center, zoom: M().getZoom(), opzioni: __geo.chiamate[0].opts,
                         poligono: M().sources['stima-gps'].data.features[0].geometry.type, punto: M().sources['stima-gps'].data.features[1].geometry.coordinates };
      gq('[data-gps-insert]').dispatch('click'); await wait(); await wait();
      const provvisorio = { ...stato(), hint: stato().hint, segnaposto: K().length ? K()[0].p : null, aperto: !!P().open, gpsNascosto: gq('[data-gps-status]').hidden,
                            indirizzo: pq('[data-insert-address]').dataset.addressState, via: pq('[data-address-road]').value, civico: pq('[data-address-civic]').value,
                            inverse: chiamate().filter((c) => c.url.startsWith('/api/property/reverse-geocode')).map((c) => c.url) };
      pq('[data-address-confirm]').dispatch('click'); await wait();                 // ZONA-5: l'indirizzo si conferma
      M().setZoom(16); await wait();
      const zoomBasso = stato().conferma;
      K()[0].setLngLat([13.9265, 42.8345]); K()[0].fire('dragend'); await wait();
      const spostato = { indirizzo: pq('[data-insert-address]').dataset.addressState, conferma: stato().conferma,
                         inverse: chiamate().filter((c) => c.url.startsWith('/api/property/reverse-geocode')).length };
      pq('[data-address-confirm]').dispatch('click'); await wait();                 // punto spostato: si riconferma l'indirizzo
      M().setZoom(18); await wait();
      await confermaPunto();
      const confermato = { ...stato(), carte: pq('[data-insert-cards]').querySelectorAll('[data-insert-action]').map((b) => b.dataset.insertAction), scritture: scritte() };
      __geo.trattieni = false; __geo.modo = 'ok';
      await tocca();
      const seconda = { chiamate: __geo.chiamate.length, pronto: gps().inserisci };
      gq('[data-gps-insert]').dispatch('click'); await wait();
      const sostituito = { ...stato(), segnaposto: K()[0].p };
      report({ apertura, attesa, doppio, ricevuta, provvisorio, zoomBasso, spostato, confermato, seconda, sostituito });
    """
    out = _run(staged, scenario, _rotte())
    a = out["apertura"]
    assert a["chiamate"] == 0 and a["statoHidden"] is True and a["sorgente"] is False                 # nessuna richiesta all'apertura
    assert a["etichetta"] == "La mia posizione" and a["testoBottone"] == "La mia posizione" and a["icona"] is True and a["dentroMappa"] is True
    assert out["attesa"]["chiamate"] == 1 and out["attesa"]["busy"] == "true" and out["attesa"]["disabilitato"] is True
    assert out["attesa"]["stato"] == "busy" and out["attesa"]["testo"].startswith("Ricerca della posizione")
    assert out["doppio"]["chiamate"] == 1                                                              # mai due richieste
    r = out["ricevuta"]
    assert r["chiamate"] == 1 and r["busy"] == "false" and r["disabilitato"] is False and r["stato"] == "ready"
    assert r["testo"].startswith("Precisione stimata: circa 12 m") and r["inserisci"] is True
    assert r["centro"] == [13.9262, 42.8341] and r["zoom"] == 18 and r["opzioni"] == {"enableHighAccuracy": True, "timeout": 15000, "maximumAge": 0}
    assert r["sorgente"] is True and r["livelli"] == ["fill", "line", "circle"] and r["poligono"] == "Polygon" and r["punto"] == [13.9262, 42.8341]
    p = out["provvisorio"]
    assert p["punto"] is True and p["azioni"] is False and p["segnaposto"] == [13.9262, 42.8341] and p["trascinabile"] is True
    assert p["testo"] == "Punto provvisorio: 42.83410, 13.92620" and "Punto dal GPS" in p["hint"] and "circa 12 m" in p["hint"]
    assert p["aperto"] is True and p["gpsNascosto"] is True
    # SENTINELLA AGGIORNATA DA STIMA360 ZONA-5: dal GPS parte UNA ricerca inversa con le coordinate del punto
    assert p["indirizzo"] == "found" and p["via"] == "Via Roma" and p["civico"] == "25"
    assert p["inverse"] == ["/api/property/reverse-geocode?lat=42.8341000&lon=13.9262000"]
    assert out["zoomBasso"] is False                                                                   # la regola di T4 resta
    assert out["spostato"] == {"indirizzo": "moved", "conferma": False, "inverse": 1}                   # spostato: decade, nessuna nuova ricerca
    c = out["confermato"]
    assert c["azioni"] is True and c["scelto"] == "Punto scelto: 42.83450, 13.92650" and c["scritture"] == []
    assert c["carte"] == ["building", "unit", "link", "link-property"]
    assert out["seconda"] == {"chiamate": 2, "pronto": True}                                            # un tocco = una richiesta fresca
    assert out["sostituito"]["punto"] is True and out["sostituito"]["azioni"] is False and out["sostituito"]["segnaposto"] == [13.9262, 42.8341]


@node
def test_b02_errori_leggibili_browser_senza_posizione_e_uscita_durante_la_richiesta(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); M().emit('load'); await wait();
      const esiti = {};
      for (const modo of ['1', '2', '3', 'bad']) {
        __geo.modo = modo;
        await tocca();
        esiti[modo] = gps();
      }
      // si lascia la pagina DURANTE la richiesta: la risposta tardiva non tocca nulla
      __geo.modo = 'ok'; __geo.trattieni = true;
      gq('[data-gps-button]').dispatch('click'); await wait();
      const mappaPrima = M();
      await vai('#/edifici');
      let errore = null;
      try { __geo.attesa.shift()(); await wait(); } catch (e) { errore = String(e); }
      const dopoUscita = { hash: window.location.hash, errore, sorgente: !!mappaPrima.sources['stima-gps'], rimossa: mappaPrima.removed };
      report({ esiti, dopoUscita, chiamate: __geo.chiamate.length });
    """
    out = _run(staged, scenario, _rotte())
    e = out["esiti"]
    for modo, inizio in (("1", "Permesso negato"), ("2", "Posizione non disponibile"), ("3", "Tempo scaduto"), ("bad", "Posizione non disponibile")):
        assert e[modo]["stato"] == "error" and e[modo]["testo"].startswith(inizio), modo
        assert e[modo]["busy"] == "false" and e[modo]["disabilitato"] is False and e[modo]["inserisci"] is False and e[modo]["sorgente"] is False
    assert out["dopoUscita"] == {"hash": "#/edifici", "errore": None, "sorgente": False, "rimossa": True}
    assert out["chiamate"] == 5

    # browser senza Geolocation API: messaggio, nessuna eccezione
    scenario2 = r"""
      await wait(); await wait(); M().emit('load'); await wait();
      await tocca();
      report({ ...gps() });
    """
    out = t2b3._run(staged, HELPERS + scenario2, _rotte() + "\ndelete navigator.geolocation;\n", "#/edifici/mappa")
    assert out["stato"] == "error" and out["testo"].startswith("Questo browser non offre") and out["chiamate"] == 0
