"""STIMA360 TERRITORIO - ZONA: la mappa come voce principale della barra laterale.

  A  statici: «Zona» in SECTIONS subito prima di «Edifici», con la sua icona,
     la rotta `zona` registrata (ogni voce di SECTIONS ha una rotta), nessuna
     seconda mappa (la voce porta a #/edifici/mappa);
  B  Shell VERA sullo stub DOM: il bottone c'e' (etichetta esatta), il clic apre
     la mappa (#/edifici/mappa, una sola mappa viva), «Zona» e' la voce attiva e
     il titolo della pagina, «Edifici» no; dalla mappa (anche #/edifici/mappa/
     edificio/{id} e .../posiziona/{id}) resta attiva «Zona»; #/edifici e il suo
     pulsante «Mappa» funzionano come prima; le altre sezioni sono invariate
     (voce attiva corretta); #/zona scritto a mano porta alla mappa;
  C  Chromium a 390 e 1280 px: «Zona» visibile e toccabile (>= 40 px), clic ->
     mappa, voce attiva, nessuno sforamento.
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
from tests.test_territorio_4_browser_postgres import FINTA as FINTA_T4
from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)

node = t2b3.node
ASSETS = t2b3.ASSETS
MAIN = ASSETS / "main.js"
rt = a30_5._rt()


# ---------------------------------------------------------------------------
# A - statici
# ---------------------------------------------------------------------------

def test_a01_zona_in_sections_con_icona_rotta_e_stessa_mappa():
    main = MAIN.read_text(encoding="utf-8")
    sezioni = main[main.index("const SECTIONS = ["):main.index("];", main.index("const SECTIONS = ["))]
    nomi = re.findall(r"name:\s*'([a-z]+)'", sezioni)
    assert nomi.index("zona") + 1 == nomi.index("edifici")
    assert "{ name: 'zona', label: 'Zona', target: ['edifici', 'mappa'] }" in sezioni
    assert main.count("registerRoute('zona'") == 1
    assert re.search(r"^\s+zona: \[", main, re.M)                               # icona propria
    assert "SEZIONI_ANCHE_PLATFORM = new Set(['cestino'])" in main               # non per il Super Admin senza agenzia
    # nessuna seconda mappa: nessun import nuovo di viste, la rotta zona non disegna nulla
    assert main.count("renderTerritorio") == 2                                   # import + dispatch di 'edifici', come prima
    assert "window.location.replace('#/edifici/mappa')" in main


@node
def test_a02_node_check():
    esito = subprocess.run([a30_5.NODE, "--check", str(MAIN)], capture_output=True, text=True, timeout=30)
    assert esito.returncode == 0, esito.stderr


# ---------------------------------------------------------------------------
# B - Shell vera sullo stub DOM (con la MapLibre finta di T2)
# ---------------------------------------------------------------------------

PRELUDIO = t2.PRELUDIO + r"""
window.location.replace = (h) => { window.location.hash = h; };
"""

HELPERS = r"""
const nav = () => __dom.byId['nav'];
const attiva = () => nav().querySelectorAll('[data-route]').filter((b) => b.classList.contains('active')).map((b) => b.dataset.route);
const voce = (r) => nav().querySelector(`[data-route="${r}"]`);
const titolo = () => __dom.byId['page-title'].textContent;
async function clicca(r) { voce(r).dispatch('click'); await window._fire('hashchange'); await wait(); await wait(); }
async function vai(h) { window.location.hash = h; await window._fire('hashchange'); await wait(); await wait(); }
"""


def _rotte():
    voci = [("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione("agency_owner"))]),
            ("GET", "/api/property/buildings/9", [rt.ok({"id": 9, "name": "P", "census_summary": {}, "units": [], "archived_units": [], "latitude": None, "longitude": None})]),
            ("GET", "/api/property/form-options", [rt.ok({"territory": [], "building_types": []})]),
            ("GET", "/api/", [rt.ok({"items": [], "total": 0})])]
    return PRELUDIO + "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});" for m, p, r in voci)


def _run(staged, scenario, hash="#/oggi"):  # noqa: F811
    return t2b3._run(staged, HELPERS + scenario, _rotte(), hash)


@node
def test_b01_la_voce_zona_apre_la_mappa_ed_e_attiva(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const vz = voce('zona');
      const prima = { c_e: !!vz, etichetta: vz.querySelector('span').textContent,
                      icona: !!vz.querySelector('svg'), ordine: nav().querySelectorAll('[data-route]').map((b) => b.dataset.route) };
      await clicca('zona');
      const mappa = { hash: window.location.hash, attiva: attiva(), titolo: titolo(), mappe: __maps.length,
                      pagina: !!C().querySelector('[data-map]') };
      await vai('#/edifici/mappa/edificio/9');
      const selezionato = { attiva: attiva(), titolo: titolo() };
      await vai('#/edifici/mappa/posiziona/9');
      const posiziona = { attiva: attiva(), titolo: titolo() };
      report({ prima, mappa, selezionato, posiziona });
    """
    out = _run(staged, scenario)
    assert out["prima"]["c_e"] is True and out["prima"]["etichetta"] == "Zona" and out["prima"]["icona"] is True
    ordine = out["prima"]["ordine"]
    assert ordine.index("zona") + 1 == ordine.index("edifici") and ordine[:3] == ["oggi", "agenda", "contatti"]
    assert out["mappa"] == {"hash": "#/edifici/mappa", "attiva": ["zona"], "titolo": "Zona", "mappe": 1, "pagina": True}
    assert out["selezionato"] == {"attiva": ["zona"], "titolo": "Zona"}
    assert out["posiziona"] == {"attiva": ["zona"], "titolo": "Zona"}


@node
def test_b02_edifici_e_il_pulsante_mappa_come_prima_e_le_altre_sezioni_invariate(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      await clicca('edifici');
      const edifici = { hash: window.location.hash, attiva: attiva(), titolo: titolo(), mappaBtn: !!C().querySelector('#buildings-map'),
                        href: C().querySelector('#buildings-map').getAttribute('href'), nuovo: C().querySelector('#buildings-new').textContent };
      await vai(C().querySelector('#buildings-map').getAttribute('href'));      // il vecchio pulsante «Mappa»
      const daPulsante = { hash: window.location.hash, attiva: attiva(), mappe: __maps.length };
      await vai('#/edifici/9');
      const scheda = { attiva: attiva(), titolo: titolo() };
      const altre = {};
      for (const r of ['oggi', 'agenda', 'contatti', 'immobili', 'cestino']) {
        await clicca(r);
        altre[r] = [window.location.hash, attiva().join(','), titolo()];
      }
      await vai('#/zona');
      const aMano = { hash: window.location.hash, attiva: attiva(), titolo: titolo(), mappe: __maps.length };
      report({ edifici, daPulsante, scheda, altre, aMano });
    """
    out = _run(staged, scenario)
    e = out["edifici"]
    assert e["hash"] == "#/edifici" and e["attiva"] == ["edifici"] and e["titolo"] == "Edifici"
    assert e["mappaBtn"] is True and e["href"] == "#/edifici/mappa" and e["nuovo"] == "+ Nuovo"
    assert out["daPulsante"] == {"hash": "#/edifici/mappa", "attiva": ["zona"], "mappe": 1}
    assert out["scheda"] == {"attiva": ["edifici"], "titolo": "Edifici"}
    assert out["altre"] == {"oggi": ["#/oggi", "oggi", "Oggi"], "agenda": ["#/agenda", "agenda", "Agenda"],
                            "contatti": ["#/contatti", "contatti", "Contatti"], "immobili": ["#/immobili", "immobili", "Immobili"],
                            "cestino": ["#/cestino", "cestino", "Cestino"]}
    assert out["aMano"]["hash"] == "#/edifici/mappa" and out["aMano"]["attiva"] == ["zona"] and out["aMano"]["titolo"] == "Zona"


# ---------------------------------------------------------------------------
# C - Chromium (rete simulata, MapLibre finta di T2)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("larghezza", [390, 1280])
def test_c01_chromium_zona_visibile_toccabile_e_attiva(larghezza):
    sync_playwright = t2b3._chromium()
    api = [("/api/operator-auth/me", t2b3._ME), ("/api/property/buildings?", {"items": [], "total": 0}),
           ("/api/property/form-options", {"territory": [], "building_types": []})]
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": larghezza, "height": 780})
            t2b3._servi(page, api)
            page.route("https://unpkg.com/**", lambda r: r.abort())
            page.add_init_script(FINTA_T4)
            page.goto("http://stima360.test/os/#/oggi")
            page.wait_for_selector("[data-route='zona']")
            # su smartphone la barra Aurora Glass e' una striscia che scorre in orizzontale (come per
            # Edifici, Immobili...): la voce deve stare DENTRO la striscia e, una volta scorsa, nel viewport
            page.eval_on_selector("[data-route='zona']", "b => b.scrollIntoView({ inline: 'nearest', block: 'nearest' })")
            voce = page.evaluate("""() => { const b = document.querySelector("[data-route='zona']"); const r = b.getBoundingClientRect();
                const nav = document.querySelector('#nav');
                return { testo: b.textContent.trim(), h: r.height, w: r.width,
                         visibile: r.width > 0 && r.height > 0 && r.left >= -0.5 && r.right <= innerWidth + 0.5
                                   && r.top >= -0.5 && r.bottom <= innerHeight + 0.5,
                         nellaStriscia: nav.scrollWidth <= nav.clientWidth + 0.5 || b.offsetLeft + b.offsetWidth <= nav.scrollWidth + 0.5,
                         primaDiEdifici: (() => { const v = [...nav.querySelectorAll('[data-route]')].map((x) => x.dataset.route);
                                                  return v.indexOf('zona') + 1 === v.indexOf('edifici'); })(),
                         scroll: document.documentElement.scrollWidth <= innerWidth }; }""")
            page.click("[data-route='zona']")
            page.wait_for_selector("canvas.finta")
            dopo = page.evaluate("""() => ({ hash: location.hash, attive: [...document.querySelectorAll('#nav .active')].map((b) => b.dataset.route),
                                             titolo: document.querySelector('#page-title').textContent,
                                             scroll: document.documentElement.scrollWidth <= innerWidth })""")
            page.click("[data-route='edifici']")
            page.wait_for_selector("#buildings-map")
            edifici = page.evaluate("[...document.querySelectorAll('#nav .active')].map((b) => b.dataset.route)")
            browser.close()
    except Exception as exc:  # noqa: BLE001
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium non disponibile: misura NON eseguita (BLOCKED)")
        raise
    assert voce["testo"] == "Zona" and voce["visibile"] is True and voce["nellaStriscia"] is True
    assert voce["h"] >= 40 and voce["scroll"] is True and voce["primaDiEdifici"] is True
    assert dopo == {"hash": "#/edifici/mappa", "attive": ["zona"], "titolo": "Zona", "scroll": True}
    assert edifici == ["edifici"]
