"""PERTINENZE-1 - prova in BROWSER (Chromium) con backend e PostgreSQL VERI.

Shell vera servita da uvicorn (stesso server di EDIFICI-1, solo
l'autenticazione sostituita), router Immobili vero, schema completo (089
compresa). A 390 px (smartphone) e 1280 px:

  b01  scheda edificio: pannello «Pertinenze» con la pertinenza «da
       collegare»; «Collega a…» -> la principale della palazzina -> sul DB;
  b02  scheda dell'unita': «+ Aggiungi pertinenza» con tipo, mq decimali e
       «Si'» -> scheda autonoma collegata (posto auto, tipologia garage);
       «Chiarisci» dell'accessorio -> unita' autonoma, accessorio rimosso;
  b03  scheda della pertinenza: «Scollega» in due tocchi -> resta pertinenza
       da collegare (DB), edificio invariato;
  nessuno scorrimento orizzontale, nessun errore di pagina.
Screenshot in `EDIFICI1_SHOTS` (o /tmp/edifici1_shots).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import _edificio, _q, _unita  # noqa: F401
from tests.test_edifici_1_browser_postgres import DSN, completo, mondo, server  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


def _sfora(page):
    return page.evaluate("""() => {
      const fuori = [...document.querySelectorAll('#content *, dialog[open] *')]
        .filter((e) => e.getClientRects().length && e.getBoundingClientRect().right > window.innerWidth + 0.5)
        .map((e) => e.tagName + '.' + e.className);
      return { scroll: document.documentElement.scrollWidth, inner: window.innerWidth, fuori: fuori.slice(0, 5) };
    }""")


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_b01_b02_b03_pertinenze_in_chromium(server, larghezza):
    from playwright.sync_api import sync_playwright
    m = server
    e = _edificio(m, units_declared=4)
    app = _unita(m, building_id=e["id"], floor="2", internal_number="4")
    sciolta = _unita(m, building_id=e["id"], is_pertinenza=True, pertinenza_kind="box", property_type="garage", floor="-1")
    acc = m["api"]().post(f"/api/property/properties/{app['id']}/accessories",
                          json={"kind": "cantina", "cadastral_status": "unknown", "surface_sqm": "6.25"}).json()
    cartella = Path(os.environ.get("EDIFICI1_SHOTS") or (Path("/tmp") / "edifici1_shots"))
    cartella.mkdir(parents=True, exist_ok=True)
    misure = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": larghezza, "height": 860})
            errori = []
            page.on("pageerror", lambda x: errori.append(str(x)))

            # --- b01: scheda edificio ------------------------------------------------
            page.goto(f"{m['url']}/os/#/edifici/{e['id']}")
            page.wait_for_selector("[data-pertinenze-unlinked]")
            pannello = page.inner_text("#building-pertinenze")
            assert "Da collegare (1)" in pannello and "Garage / box" in pannello, pannello
            assert "da collegare" in page.inner_text("#building-split")
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"pertinenze_edificio_{larghezza}.png"), full_page=True)
            page.click(f"[data-link-pertinenza='{sciolta['id']}']")
            page.wait_for_selector(f"[data-principal-id='{app['id']}']")
            misure.append(_sfora(page))
            page.click(f"[data-principal-id='{app['id']}']")
            page.wait_for_function("() => !document.querySelector('[data-pertinenze-unlinked]')")
            assert _q(m, "SELECT parent_property_id, building_id FROM properties WHERE id = %s", (sciolta["id"],))[0] == [app["id"], e["id"]]

            # --- b02: scheda dell'unita' ----------------------------------------------
            page.goto(f"{m['url']}/os/#/immobili/{app['id']}")
            page.wait_for_selector("#census-add-pertinenza")
            page.click("#census-add-pertinenza")
            page.wait_for_selector("[data-pertinenza-form]")
            page.click("[data-chip='kind'][data-value='posto_auto']")
            page.fill("#pa-surface", "12.5")
            page.click("[data-chip='answer'][data-value='yes']")
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"pertinenze_aggiungi_{larghezza}.png"), full_page=True)
            page.click("[data-pertinenza-form] [data-submit]")
            page.wait_for_selector("[data-unit-form]")
            assert "Posto auto" in page.inner_text(".census-sheet-title")
            page.click("[data-unit-form] [data-submit]")
            page.wait_for_function("() => !document.querySelector('dialog[open] [data-unit-form]')")
            riga = _q(m, "SELECT property_type, pertinenza_kind, is_pertinenza, surface_sqm, building_id FROM properties "
                         "WHERE parent_property_id = %s AND pertinenza_kind = 'posto_auto'", (app["id"],))
            assert len(riga) == 1 and riga[0][:3] == ["garage", "posto_auto", True] and str(riga[0][3]) == "12.50"
            assert riga[0][4] == e["id"]
            page.goto(f"{m['url']}/os/#/immobili/{app['id']}")
            page.wait_for_selector(f"[data-resolve='{acc['id']}']")
            page.click(f"[data-resolve='{acc['id']}']")
            page.click("[data-chip='outcome'][data-value='separate']")
            page.click("[data-resolve-form] [data-submit]")
            page.wait_for_function(f"() => !document.querySelector(\"[data-resolve='{acc['id']}']\")")
            assert _q(m, "SELECT count(*) FROM property_accessories WHERE id = %s", (acc["id"],))[0][0] == 0
            assert _q(m, "SELECT count(*) FROM properties WHERE parent_property_id = %s AND pertinenza_kind = 'cantina'",
                      (app["id"],))[0][0] == 1
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"pertinenze_unita_{larghezza}.png"), full_page=True)

            # --- b03: scollega dal lato della pertinenza ---------------------------
            page.goto(f"{m['url']}/os/#/immobili/{sciolta['id']}")
            page.wait_for_selector("#census-unlink-self")
            assert "Pertinenza autonoma · Garage / box" in page.inner_text("#census-nature")
            page.click("#census-unlink-self")
            page.wait_for_function("() => ((document.querySelector('#census-unlink-self') || {}).textContent || '').includes('Confermi')")
            page.click("#census-unlink-self")
            page.wait_for_selector("#census-link-principal")
            assert _q(m, "SELECT parent_property_id, is_pertinenza, building_id FROM properties WHERE id = %s",
                      (sciolta["id"],))[0] == [None, True, e["id"]]
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"pertinenze_scollegata_{larghezza}.png"), full_page=True)
            assert errori == [], errori
            browser.close()
    except Exception as exc:  # noqa: BLE001
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium non disponibile: prova in browser NON eseguita (BLOCKED)")
        raise
    for misura in misure:
        assert misura["scroll"] <= misura["inner"], misura
        assert misura["fuori"] == [], misura
