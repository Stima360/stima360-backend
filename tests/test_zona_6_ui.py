"""STIMA360 ZONA-6 - Zona alleggerita, senza database (Shell VERA sullo stub DOM).

  A  funzioni pure: indice di GET /api/crm/sellers/zone, righe della scheda
     rapida (solo quelle disponibili), id «Da ricontattare», punti «Da
     verificare», etichette dei filtri; statici (nessun HTML dinamico).
  B  b01 scheda rapida di un edificio: indirizzo, ultimo contatto, prossima
         azione (scaduta), «Apri scheda completa»; un edificio senza dati
         mostra solo l'indirizzo; lettura fallita -> lo si dice;
     b02 filtri: Tutti / Da verificare / Da ricontattare con i conteggi;
         «Da verificare» accende i punti grigi (e spegne gli altri), dice
         quanti non possono comparire; un punto grigio -> «Verifica
         posizione»; «Da ricontattare» filtra i livelli per id e dice chi
         non sta sulla mappa; lettura fallita -> lo si dice;
     b03 un solo «+»: tre voci; «Scegli un punto» -> avviso sulla mappa ->
         tocco -> pannello col punto; «Annulla» richiude; «Cerca un
         indirizzo» -> pannello con la ricerca; nessuna scrittura.
"""
from __future__ import annotations

import json
import subprocess

from tests import test_a30_5_create_ui as a30_5
from tests import test_delete_arch_2b3_ui as t2b3
from tests import test_territorio_3_ui as t3
from tests import test_territorio_zona2_ui as z2
from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)

node = t2b3.node
ASSETS = t2b3.ASSETS
MODELLO = ASSETS / "territorio" / "territorio-model.js"
VISTA = ASSETS / "views" / "territorio.js"
GPS = ASSETS / "territorio" / "gps.js"
API = ASSETS / "sellers" / "sellers-api.js"
_codice = t2b3._codice
rt = a30_5._rt()


def _pure(expr):
    script = f"import * as m from '{MODELLO.as_posix()}';\nconsole.log(JSON.stringify({expr}));"
    esito = subprocess.run([a30_5.NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                           timeout=30, env={"PATH": "/usr/bin:/bin"})
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


ZONA = {"buildings": [
    {"building_id": 9, "to_recontact": True, "last_contact": {"type": "call", "occurred_at": "2026-10-12T08:30:00+00:00"},
     "next_action": {"title": "Richiamare", "due_at": "2020-01-01T08:00:00+00:00", "overdue": True, "source": "task"}},
    {"building_id": 11, "to_recontact": True, "last_contact": None,
     "next_action": {"title": "Visita", "due_at": "2020-02-01T08:00:00+00:00", "overdue": True, "source": "task"}},
    {"building_id": 13, "to_recontact": False, "last_contact": {"type": "meeting", "occurred_at": "2026-10-01T08:00:00+00:00"},
     "next_action": {"title": "Dopo", "due_at": "2031-01-01T08:00:00+00:00", "overdue": False, "source": "lead"}}],
    "properties": [{"property_id": 61, "to_recontact": True, "last_contact": None, "next_action": None}]}


# ---------------------------------------------------------------------------
# A - funzioni pure e statici
# ---------------------------------------------------------------------------

@node
def test_a01_indice_righe_rapide_ricontatto_e_punti_da_verificare():
    out = _pure("""(() => {
      const i = m.zoneIndex(%s);
      const g = { unverified: [{ building: { id: 11, latitude: '42.8000000', longitude: '13.9400000' } },
                               { building: { id: 14, latitude: '', longitude: '13.9' } }] };
      return {
        edifici: [...i.buildings.keys()], immobili: [...i.properties.keys()],
        righe9: m.quickFacts(i.buildings.get(9)), righe11: m.quickFacts(i.buildings.get(11)), nulla: m.quickFacts(null),
        ricontatto: m.recontactIds(i), vuoto: m.recontactIds(m.zoneIndex({ buildings: 'x' })),
        punti: m.unverifiedPoints(g).map((p) => [p.building.id, p.lng, p.lat]),
        etichette: [m.filterLabel('all', 3), m.filterLabel('verify', null), m.filterLabel('recontact', 0)],
        filtri: m.ZONE_FILTERS.map((f) => f.key),
      };
    })()""" % json.dumps(ZONA))
    assert out["edifici"] == [9, 11, 13] and out["immobili"] == [61]
    assert out["righe9"] == [{"key": "last", "label": "Ultimo contatto", "text": "Telefonata, 12/10/2026"},
                             {"key": "next", "label": "Prossima azione", "overdue": True, "text": "Richiamare, 01/01/2020 (scaduta)"}]
    assert out["righe11"] == [{"key": "next", "label": "Prossima azione", "overdue": True, "text": "Visita, 01/02/2020 (scaduta)"}]
    assert out["nulla"] == []                                                            # niente dati, niente righe
    assert out["ricontatto"] == {"buildings": [9, 11], "properties": [61]} and out["vuoto"] == {"buildings": [], "properties": []}
    assert out["punti"] == [[11, 13.94, 42.8]]                                           # solo coordinate valide
    assert out["etichette"] == ["Tutti · 3", "Da verificare", "Da ricontattare · 0"]
    assert out["filtri"] == ["all", "verify", "recontact"]


def test_a02_statici_testo_sicuro_e_una_lettura():
    vista = _codice(VISTA)
    assert vista.count("zoneSummary()") == 1                                          # UNA lettura per apertura
    assert "onMapClosed(map, () => gps.stop());" in vista
    for f in (VISTA, MODELLO, GPS):
        corpo = _codice(f)
        assert "innerHTML = `" not in corpo or f == VISTA, f.name
    # nella vista l'unico innerHTML e' lo scheletro statico della pagina (nessun dato dentro)
    assert vista.count("innerHTML") == 1 and "container.innerHTML = `" in vista
    assert "export function zoneSummary()" in _codice(API) and "'/api/crm/sellers/zone'" in _codice(API)


@node
def test_a03_node_check():
    for file in (MODELLO, VISTA, GPS, API, ASSETS / "views" / "territorio-inserisci.js"):
        esito = subprocess.run([a30_5.NODE, "--check", str(file)], capture_output=True, text=True, timeout=30)
        assert esito.returncode == 0, esito.stderr


# ---------------------------------------------------------------------------
# B - Shell vera sullo stub DOM
# ---------------------------------------------------------------------------

# la mappa finta impara i livelli (visibilita' e filtro), come MapLibre
LIVELLI = r"""
maplibregl.Map.prototype.getLayer = function (id) { return this.layers.find((l) => l.id === id); };
maplibregl.Map.prototype.setLayoutProperty = function (id, k, v) { const l = this.getLayer(id); l.layout = { ...(l.layout || {}), [k]: v }; };
maplibregl.Map.prototype.setFilter = function (id, f) { this.getLayer(id).filter = f; };
"""

HELPERS = z2.HELPERS + r"""
const PP = () => C().querySelector('[data-building-panel]');
const R = () => __popups[__popups.length - 1];
const livello = (id) => { const l = M().layers.find((x) => x.id === id); return l ? { vis: (l.layout || {}).visibility || 'visible', filtro: l.filter || null } : null; };
const filtri = () => C().querySelectorAll('[data-zone-filter]').map((b) => [b.dataset.zoneFilter, b.textContent, b.getAttribute('aria-pressed')]);
const nota = () => { const n = C().querySelector('[data-zone-filter-note]'); return n.hidden ? null : n.visibleText(); };
async function filtro(k) { C().querySelector(`[data-zone-filter="${k}"]`).dispatch('click'); await wait(); }
"""


def _rotte(zona=None, liste=None):
    voci = [("GET", "/api/operator-auth/me", [rt.ok(t3.a30_13b._sessione("agency_owner"))]),
            ("GET", "/api/crm/sellers/zone", [zona if zona is not None else rt.ok(ZONA)]),
            ("GET", "/api/property/buildings/9", [rt.ok(t3.SCHEDA_9)]),
            ("GET", "/api/property/buildings?", [liste or rt.ok(t3.QUATTRO)]),
            ("GET", "/api/property/form-options", [rt.ok({"territory": [], "building_types": []})]),
            ("GET", "/api/", [rt.ok({"items": [], "total": 0})])]
    return z2.PRELUDIO + LIVELLI + "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});" for m, p, r in voci)


def _run(staged, scenario, rotte):  # noqa: F811
    return t2b3._run(staged, HELPERS + scenario, rotte, "#/edifici/mappa")


@node
def test_b01_scheda_rapida_indirizzo_ultimo_contatto_prossima_azione_e_scheda_completa(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); M().emit('load'); await wait(); await wait();
      M().clickLayer('stima-buildings-points', 9); await wait(); await wait();
      const r = R().node;
      const scheda = { titolo: r.querySelector('.territorio-popup-title').textContent, dove: r.querySelector('.territorio-popup-where').textContent,
                       fatti: r.querySelectorAll('[data-quick-fact]').map((d) => [d.dataset.quickFact, d.querySelector('dt').textContent, d.querySelector('dd').textContent,
                                                                                  d.classList.contains('is-overdue')]),
                       bottoni: r.querySelectorAll('button').map((b) => b.textContent), pannello: PP().dataset.buildingPanel };
      r.querySelector('[data-open-building="9"]').dispatch('click'); await window._fire('hashchange'); await wait();
      report({ scheda, hash: window.location.hash, scritture: scritte() });
    """
    out = _run(staged, scenario, _rotte())
    s = out["scheda"]
    assert s["titolo"] == "Palazzina <b>Mare</b>" and s["dove"] == "Via Roma 10 · Alba Adriatica · Centro"
    assert s["fatti"] == [["last", "Ultimo contatto", "Telefonata, 12/10/2026", False],
                          ["next", "Prossima azione", "Richiamare, 01/01/2020 (scaduta)", True]]
    assert s["bottoni"] == ["Apri scheda completa"] and s["pannello"] == "9"
    assert out["hash"] == "#/edifici/9" and out["scritture"] == []

    # un edificio senza contatti ne' azioni: solo l'indirizzo e la scheda completa
    vuota = {"buildings": [], "properties": []}
    scenario2 = r"""
      await wait(); await wait(); M().emit('load'); await wait(); await wait();
      M().clickLayer('stima-buildings-points', 9); await wait();
      report({ testo: R().node.visibleText(), fatti: R().node.querySelectorAll('[data-quick-fact]').length });
    """
    out = _run(staged, scenario2, _rotte(zona=rt.ok(vuota)))
    assert out["fatti"] == 0 and "Ultimo contatto" not in out["testo"] and "Apri scheda completa" in out["testo"]
    # lettura fallita: lo si dice, la scheda resta utilizzabile
    out = _run(staged, scenario2, _rotte(zona={"status": 503, "body": {"detail": "x"}}))
    assert out["fatti"] == 0 and "non disponibili ora" in out["testo"] and "Apri scheda completa" in out["testo"]


@node
def test_b02_filtri_tutti_da_verificare_da_ricontattare(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); M().emit('load'); await wait(); await wait();
      const inizio = { filtri: filtri(), nota: nota(), grigio: livello('stima-buildings-unverified-points'), edifici: livello('stima-buildings-points') };
      await filtro('verify');
      const verifica = { filtri: filtri(), nota: nota(), grigio: livello('stima-buildings-unverified-points'), edifici: livello('stima-buildings-points'),
                         punti: M().sources['stima-buildings-unverified'].data.features.map((f) => f.id),
                         legenda: !C().querySelector('[data-legend-unverified]').hidden };
      M().clickLayer('stima-buildings-unverified-points', 11); await wait(); await wait();
      const grigia = { testo: R().node.visibleText(), bottoni: R().node.querySelectorAll('button').map((b) => b.textContent),
                       fatti: R().node.querySelectorAll('[data-quick-fact]').map((d) => d.dataset.quickFact) };
      await filtro('recontact');
      const ricontatto = { filtri: filtri(), nota: nota(), grigio: livello('stima-buildings-unverified-points'), edifici: livello('stima-buildings-points'),
                           chiuso: R().removed, fuori: C().querySelectorAll('[data-recontact-off-map]').map((a) => [a.getAttribute('href'), a.textContent]) };
      await filtro('all');
      const tutti = { nota: nota(), grigio: livello('stima-buildings-unverified-points'), edifici: livello('stima-buildings-points') };
      await filtro('verify');
      M().clickLayer('stima-buildings-unverified-points', 11); await wait(); await wait();
      R().node.querySelector('[data-verify-building="11"]').dispatch('click'); await window._fire('hashchange'); await wait();
      report({ inizio, verifica, grigia, ricontatto, tutti, hash: window.location.hash, scritture: scritte() });
    """
    out = _run(staged, scenario, _rotte())
    i = out["inizio"]
    assert i["filtri"] == [["all", "Tutti · 1", "true"], ["verify", "Da verificare · 1", "false"], ["recontact", "Da ricontattare · 1", "false"]]
    assert i["nota"] is None and i["grigio"] is None and i["edifici"]["filtro"] is None      # nessun livello in piu' all'apertura
    v = out["verifica"]
    assert v["filtri"][1][2] == "true" and v["punti"] == [11] and v["legenda"] is True
    assert v["grigio"]["vis"] == "visible" and v["edifici"]["vis"] == "none"
    assert "1 edificio con la posizione da verificare" in v["nota"]
    assert "1 senza coordinate, 1 con coordinate non valide" in v["nota"] and "Immobili indipendenti" in v["nota"]
    g = out["grigia"]
    assert "Posizione da verificare" in g["testo"] and g["bottoni"] == ["Verifica posizione", "Apri scheda completa"] and g["fatti"] == ["next"]
    r = out["ricontatto"]
    assert r["filtri"][2][2] == "true" and r["chiuso"] is True
    assert r["grigio"]["vis"] == "none" and r["edifici"]["vis"] == "visible"
    assert r["edifici"]["filtro"] == ["in", ["get", "building_id"], ["literal", [9]]]
    assert "1 da ricontattare sulla mappa" in r["nota"]
    # 11 e' da ricontattare ma la posizione non e' verificata: detto e raggiungibile; 61 (immobile) non e' sulla mappa
    assert r["fuori"] == [["#/edifici/11", "Condominio Sole — Via Bologna 7 · Tortoreto"]]
    assert "1 immobile indipendente da ricontattare senza posizione verificata" in r["nota"]
    t = out["tutti"]
    assert t["nota"] is None and t["grigio"]["vis"] == "none" and t["edifici"] == {"vis": "visible", "filtro": None}
    assert out["hash"] == "#/edifici/mappa/posiziona/11" and out["scritture"] == []

    # lettura di ricontatto fallita: il filtro lo dice, la mappa resta intera
    scenario2 = r"""
      await wait(); await wait(); M().emit('load'); await wait(); await wait();
      await filtro('recontact');
      report({ filtri: filtri(), nota: nota(), edifici: livello('stima-buildings-points') });
    """
    out = _run(staged, scenario2, _rotte(zona={"status": 503, "body": {"detail": "Servizio non disponibile"}}))
    assert out["filtri"][2] == ["recontact", "Da ricontattare", "true"]
    assert out["nota"].startswith("«Da ricontattare» non è disponibile ora") and out["edifici"]["filtro"] == ["in", ["get", "building_id"], ["literal", []]]


@node
def test_b03_un_solo_piu_scegli_un_punto_annulla_e_cerca_indirizzo(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); M().emit('load'); await wait(); await wait();
      const piu = (s) => C().querySelector(s);
      const inizio = { piu: C().querySelectorAll('[data-zone-add]').length, pannello: P().hidden, menu: piu('[data-zone-add-menu]').hidden,
                       sommario: P().querySelector('summary').hidden, vecchio: !!P().querySelector('[data-insert-pick]') };
      piu('[data-zone-add]').dispatch('click'); await wait();
      const menu = { aperto: !piu('[data-zone-add-menu]').hidden, espanso: piu('[data-zone-add]').getAttribute('aria-expanded'),
                     voci: piu('[data-zone-add-menu]').querySelectorAll('[role="menuitem"]').map((b) => b.querySelector('strong').textContent),
                     gps: piu('[data-zone-add-gps]').disabled };
      piu('[data-zone-add-pick]').dispatch('click'); await wait();
      const tocco = { avviso: !piu('[data-zone-pick-hint]').hidden, menu: piu('[data-zone-add-menu]').hidden, pannello: P().hidden };
      M().emit('click', { lngLat: { lng: 13.9262, lat: 42.8341 } }); await wait();
      const punto = { ...stato(), pannello: P().hidden, avviso: piu('[data-zone-pick-hint]').visibleText() };
      P().querySelectorAll('[data-insert-cancel]')[0].dispatch('click'); await wait();   // quello del punto provvisorio
      const annullato = { ...stato(), pannello: P().hidden, avviso: !piu('[data-zone-pick-hint]').hidden };
      piu('[data-zone-add]').dispatch('click'); await wait();
      piu('[data-zone-add-search]').dispatch('click'); await wait();
      const ricerca = { pannello: P().hidden, aperto: !!P().open, campo: !!pq('[data-geocode-input]') };
      pq('[data-insert-close]').dispatch('click'); await wait();
      report({ inizio, menu, tocco, punto, annullato, ricerca, chiuso: P().hidden, scritture: scritte() });
    """
    out = _run(staged, scenario, _rotte())
    assert out["inizio"] == {"piu": 1, "pannello": True, "menu": True, "sommario": True, "vecchio": False}
    assert out["menu"] == {"aperto": True, "espanso": "true", "voci": ["Dalla mia posizione", "Scegli un punto sulla mappa", "Cerca un indirizzo"],
                           "gps": True}                                                       # nessuna posizione: spento
    assert out["tocco"] == {"avviso": True, "menu": True, "pannello": True}
    p = out["punto"]
    assert p["punto"] is True and p["pannello"] is False and p["segnaposti"] == 1 and "Tocca di nuovo" in p["avviso"]
    assert out["annullato"]["pannello"] is True and out["annullato"]["avviso"] is False and out["annullato"]["segnaposti"] == 0
    assert out["ricerca"] == {"pannello": False, "aperto": True, "campo": True}
    assert out["chiuso"] is True and out["scritture"] == []
