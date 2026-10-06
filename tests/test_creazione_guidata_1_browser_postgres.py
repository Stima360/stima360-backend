"""CREAZIONE-GUIDATA-1 - prova in BROWSER (Chromium) con backend e PostgreSQL VERI.

Stesso server della prova di EDIFICI-1 (uvicorn: Shell vera + router veri sul
database usa-e-getta; sostituita solo l'autenticazione, titolare agenzia 1).
A 1280 px e 390 px:

  b01  Commerciale «+ Nuovo immobile»: passo 1 -> «Palazzina con piu' unita'»
       -> nessun candidato -> «Salva edificio» -> foglio unita' gia' aperto
       nella scheda edificio -> due unita' di fila (`crm`) -> barra «salvata»,
       contatori aggiornati;
  b02  Censimento «+ Nuovo», stesso indirizzo: il candidato c'e' -> «Usa
       questo edificio» -> l'unita' nasce `census` nello STESSO edificio
       (nessun secondo edificio, nessun dato dell'edificio cambiato);
  b03  «Unita' autonoma» commerciale: form di sempre con il territorio gia'
       compilato -> scheda del nuovo immobile, nessun edificio.
Nessuno scorrimento orizzontale; screenshot in `EDIFICI1_SHOTS` (o /tmp).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import _q  # noqa: F401
from tests.test_edifici_1_browser_postgres import DSN, completo, mondo, server  # noqa: F401  (fixture riusate)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


def _sfora(page):
    return page.evaluate("""() => {
      const fuori = [...document.querySelectorAll('dialog[open] *, .census-unit-row, .census-saved-bar')]
        .filter((e) => e.getClientRects().length && e.getBoundingClientRect().right > window.innerWidth + 0.5)
        .map((e) => e.tagName + '.' + e.className);
      return { scroll: document.documentElement.scrollWidth, inner: window.innerWidth, fuori: fuori.slice(0, 5) };
    }""")


def _passo1(page, *, via, civico, unita=None, nome=""):
    page.wait_for_selector("[data-wizard-step='1']")
    page.select_option("#wz-city", "Tortoreto")
    page.wait_for_function("() => !document.querySelector('#wz-microzone').disabled")
    page.select_option("#wz-microzone", "Alto")
    page.fill("#wz-address", via)
    page.fill("#wz-civic", civico)
    if unita is None:
        page.check("#wz-unknown")
    else:
        page.fill("#wz-declared", str(unita))
    page.fill("#wz-name", nome)


@pytest.mark.parametrize("larghezza", [1280, 390])
def test_b01_b02_b03_procedura_guidata_in_chromium(server, larghezza):
    from playwright.sync_api import sync_playwright
    m = server
    cartella = Path(os.environ.get("EDIFICI1_SHOTS") or (Path("/tmp") / "edifici1_shots"))
    cartella.mkdir(parents=True, exist_ok=True)
    mobile = larghezza < 768
    misure = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": larghezza, "height": 860 if not mobile else 780})
            errori = []
            page.on("pageerror", lambda e: errori.append(str(e)))

            # --- b01: Commerciale, palazzina con piu' unita' -----------------------------
            page.goto(f"{m['url']}/os/#/immobili")
            page.click("#immobili-new")
            _passo1(page, via="Via dei Gabbiani", civico="7", unita=3, nome="Residenza Gabbiani")
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"guidata_passo1_{larghezza}.png"))
            page.click("[data-next]")
            page.wait_for_selector("[data-path='building']")
            page.screenshot(path=str(cartella / f"guidata_passo2_{larghezza}.png"))
            page.click("[data-path='building']")
            page.wait_for_selector("text=Nessun edificio trovato")
            page.click("[data-create-building]")
            page.wait_for_selector("[data-save-building]")
            page.screenshot(path=str(cartella / f"guidata_passo4_{larghezza}.png"))
            page.click("[data-save-building]")
            page.wait_for_selector("[data-building-ready]")
            page.click("[data-add-units]")
            page.wait_for_selector("dialog[open] [data-unit-form]")
            assert page.locator("#unit-add-mode").count() == 1                 # unita' commerciali
            page.click("dialog[open] [data-chip='floor'][data-value='1']")
            page.fill("dialog[open] #us-internal", "1")
            page.click("dialog[open] [data-submit-another]")
            page.wait_for_function("() => document.querySelector('dialog[open] #us-internal') && document.querySelector('dialog[open] #us-internal').value === ''")
            page.fill("dialog[open] #us-internal", "2")
            page.click("dialog[open] [data-submit]")
            page.wait_for_selector("#unit-saved-bar:not([hidden])")
            page.wait_for_function("() => document.querySelectorAll('#building-units .census-unit-row').length === 2")
            assert page.inner_text("#building-counters").count("2") >= 1
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"guidata_unita_salvate_{larghezza}.png"), full_page=True)
            edificio = _q(m, "SELECT id FROM buildings WHERE address = 'Via dei Gabbiani'")
            assert len(edificio) == 1
            bid = edificio[0][0]
            righe = _q(m, "SELECT record_kind, address, civic_number, address_inherited FROM properties WHERE building_id = %s ORDER BY id", (bid,))
            assert [tuple(r) for r in righe] == [("crm", "Via dei Gabbiani", "7", True)] * 2
            assert _q(m, "SELECT units_declared, name FROM buildings WHERE id = %s", (bid,))[0][:] == [3, "Residenza Gabbiani"]

            # --- b02: Censimento, stesso indirizzo -> riuso dell'edificio -------------------
            page.goto(f"{m['url']}/os/#/immobili/censimento")
            page.click("#census-new")
            _passo1(page, via="Via dei Gabbiani", civico="7")
            page.click("[data-next]")
            page.click("[data-path='single']")
            page.wait_for_selector(f"[data-use-building='{bid}']")
            page.screenshot(path=str(cartella / f"guidata_candidati_{larghezza}.png"))
            misure.append(_sfora(page))
            page.click(f"[data-use-building='{bid}']")
            page.wait_for_selector("[data-building-ready]")
            assert "nessun dato modificato" in page.inner_text("[data-building-ready]")
            page.click("[data-add-units]")
            page.wait_for_selector("dialog[open] [data-unit-form]")
            page.click("dialog[open] [data-chip='floor'][data-value='2']")
            page.click("dialog[open] [data-submit]")
            page.wait_for_selector("#unit-saved-bar:not([hidden])")
            assert _q(m, "SELECT count(*) FROM buildings WHERE address = 'Via dei Gabbiani'")[0][0] == 1
            kinds = [r[0] for r in _q(m, "SELECT record_kind FROM properties WHERE building_id = %s ORDER BY id", (bid,))]
            assert kinds == ["crm", "crm", "census"]
            assert _q(m, "SELECT units_declared, name FROM buildings WHERE id = %s", (bid,))[0][:] == [3, "Residenza Gabbiani"]

            # --- b03: unita' autonoma commerciale ---------------------------------------
            page.goto(f"{m['url']}/os/#/immobili")
            page.click("#immobili-new")
            _passo1(page, via="Contrada Colle", civico="3", unita=1)
            page.click("[data-next]")
            page.click("[data-path='autonomous']")
            page.wait_for_selector("#property-form")
            assert page.input_value("#pf-city") == "Tortoreto" and page.input_value("#pf-address") == "Contrada Colle"
            page.screenshot(path=str(cartella / f"guidata_autonoma_form_{larghezza}.png"))
            page.click("#property-form-submit")
            page.wait_for_selector("#property-header-title")
            villa = _q(m, "SELECT record_kind, building_id, city, microzone, region, province FROM properties WHERE address = 'Contrada Colle'")
            assert [tuple(r) for r in villa] == [("crm", None, "Tortoreto", "Alto", "Abruzzo", "TE")]
            assert errori == [], errori
            browser.close()
    except Exception as exc:  # noqa: BLE001
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium non disponibile: prova in browser NON eseguita (BLOCKED)")
        raise
    for misura in misure:
        assert misura["scroll"] <= misura["inner"], misura
        assert misura["fuori"] == [], misura
