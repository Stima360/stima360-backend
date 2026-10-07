"""CESTINO-EDIFICI-1 - prova in BROWSER (Chromium) con backend e PostgreSQL VERI.

Stesso server delle prove di EDIFICI-1 / CREAZIONE-GUIDATA-1 (uvicorn: Shell
vera + router veri sul database usa-e-getta, migration 091 compresa; solo
l'autenticazione sostituita, titolare agenzia 1). A 390 px e 1280 px:

  b01  navigazione edificio -> unita' (tocco sulla riga dell'unita');
  b02  «Elimina…» di un edificio con un'unita': il blocco elenca l'unita' con
       il suo collegamento, la conferma resta disabilitata, il link apre la
       scheda dell'unita'; nulla cambia nel database;
  b03  «Elimina…» di un edificio vuoto: motivo -> Cestino, lista Edifici;
  b04  creazione guidata: l'edificio nel Cestino NON e' fra i candidati;
  b05  Cestino › Edifici: card, «Apri scheda» in sola lettura, «Ripristina»
       (stesso id) -> di nuovo candidato nella creazione guidata.
Nessuno scorrimento orizzontale, nessun errore di pagina. Screenshot in
`EDIFICI1_SHOTS` (o /tmp/edifici1_shots).
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import _edificio, _q, _unita  # noqa: F401
from tests.test_edifici_1_browser_postgres import DSN, completo, mondo, server  # noqa: F401  (fixture riusate)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


def _sfora(page):
    """Elementi che escono dallo schermo a destra, esclusi quelli dentro un
    contenitore a scorrimento orizzontale dichiarato (le tabelle degli
    elenchi, che scorrono nel loro riquadro: la pagina non scorre)."""
    return page.evaluate("""() => {
      const inScorrimento = (e) => { for (let n = e.parentElement; n; n = n.parentElement) {
        const o = getComputedStyle(n).overflowX; if (o === 'auto' || o === 'scroll') return true; } return false; };
      const fuori = [...document.querySelectorAll('#content *, dialog[open] *')]
        .filter((e) => e.getClientRects().length && e.getBoundingClientRect().right > window.innerWidth + 0.5)
        .filter((e) => !inScorrimento(e))
        .map((e) => e.tagName + '.' + e.className);
      return { scroll: document.documentElement.scrollWidth, inner: window.innerWidth, fuori: fuori.slice(0, 5) };
    }""")


def _candidati(page, url, via, civico):
    """Creazione guidata (Censimento «+ Nuovo»), passo 1 e percorso «unita'
    singola»: restituisce gli id degli edifici candidati mostrati."""
    page.goto(f"{url}/os/#/immobili/censimento")
    page.click("#census-new")
    page.wait_for_selector("[data-wizard-step='1']")
    page.select_option("#wz-city", "Tortoreto")
    page.wait_for_function("() => !document.querySelector('#wz-microzone').disabled")
    page.select_option("#wz-microzone", "Alto")
    page.fill("#wz-address", via)
    page.fill("#wz-civic", civico)
    page.check("#wz-unknown")
    page.click("[data-next]")
    page.click("[data-path='single']")
    page.wait_for_function("() => document.querySelector('[data-use-building]') || "
                           "(document.body.innerText || '').includes('Nessun edificio trovato')")
    return page.evaluate("() => [...document.querySelectorAll('[data-use-building]')].map((b) => Number(b.dataset.useBuilding))")


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_b01_b05_cestino_edifici_in_chromium(server, larghezza):
    from playwright.sync_api import sync_playwright
    m = server
    terr = {"region": "Abruzzo", "province": "TE", "city": "Tortoreto", "microzone": "Alto"}
    via_vuoto = f"Via del Cestino {uuid.uuid4().hex[:5]}"
    vuoto = _edificio(m, **terr, address=via_vuoto, civic_number="1", name="Palazzina sbagliata")
    pieno = _edificio(m, **terr, address=f"Via Piena {uuid.uuid4().hex[:5]}", civic_number="2", name="Residenza Piena")
    unita = _unita(m, building_id=pieno["id"], floor="1", internal_number="4")
    cartella = Path(os.environ.get("EDIFICI1_SHOTS") or (Path("/tmp") / "edifici1_shots"))
    cartella.mkdir(parents=True, exist_ok=True)
    misure = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": larghezza, "height": 860})
            errori = []
            page.on("pageerror", lambda x: errori.append(str(x)))

            # --- b01: edificio -> unita' --------------------------------------------
            page.goto(f"{m['url']}/os/#/edifici/{pieno['id']}")
            page.wait_for_selector(f"[data-open-unit='{unita['id']}']")
            misure.append(_sfora(page))
            page.click(f".census-unit-row [data-open-unit='{unita['id']}']")
            page.wait_for_function(f"() => window.location.hash === '#/immobili/{unita['id']}'")
            page.wait_for_selector("#property-header-title")

            # --- b02: edificio con unita': blocco con collegamento -----------------------
            page.goto(f"{m['url']}/os/#/edifici/{pieno['id']}")
            page.wait_for_selector("#building-trash-btn")
            page.click("#building-trash-btn")
            page.wait_for_selector("[data-blocker='BUILDING_HAS_UNITS']")
            testo = page.inner_text("#building-trash-dialog")
            assert "1 unità collegata (1 attiva)" in testo and "Non si può spostare nel Cestino" in testo, testo
            assert page.is_disabled("#building-trash-dialog [data-trash-confirm]")
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"cestino_edifici_bloccato_{larghezza}.png"))
            page.click(f"#building-trash-dialog a[href='#/immobili/{unita['id']}']")
            page.wait_for_function(f"() => window.location.hash === '#/immobili/{unita['id']}'")
            # la scheda dell'unita' finisce di disegnarsi prima di cambiare pagina:
            # cosi' un suo disegno tardivo non copre la scheda edificio di b03
            page.wait_for_selector("#property-header-title")
            page.wait_for_load_state("networkidle")
            assert _q(m, "SELECT deleted_at FROM buildings WHERE id = %s", (pieno["id"],))[0][0] is None
            assert _q(m, "SELECT building_id FROM properties WHERE id = %s", (unita["id"],))[0][0] == pieno["id"]

            # --- b03: edificio vuoto nel Cestino -----------------------------------------
            page.goto(f"{m['url']}/os/#/edifici/{vuoto['id']}")
            page.wait_for_function("() => (document.querySelector('#building-title') || {}).textContent === 'Palazzina sbagliata'")
            page.wait_for_load_state("networkidle")
            page.click("#building-trash-btn")
            page.wait_for_selector("#building-trash-dialog input[name='trash-reason']")
            page.check("#building-trash-dialog input[value='created_by_mistake']")
            page.fill("#building-trash-dialog [data-trash-note]", "Creata due volte")
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"cestino_edifici_conferma_{larghezza}.png"))
            page.click("#building-trash-dialog [data-trash-confirm]")
            page.wait_for_function("() => window.location.hash === '#/edifici'")
            page.wait_for_selector("text=Edificio spostato nel Cestino")
            assert _q(m, "SELECT deleted_reason FROM buildings WHERE id = %s", (vuoto["id"],))[0][0] == "created_by_mistake"

            # --- b04: creazione guidata senza l'edificio nel Cestino ---------------------
            assert vuoto["id"] not in _candidati(page, m["url"], via_vuoto, "1")
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"cestino_edifici_guidata_senza_{larghezza}.png"))

            # --- b05: Cestino › Edifici, scheda in sola lettura, Ripristina ---------------
            page.goto(f"{m['url']}/os/#/cestino/edifici")
            page.wait_for_selector(f"[data-building-trash-item='{vuoto['id']}']")
            carta = page.inner_text(f"[data-building-trash-item='{vuoto['id']}']")
            assert "Palazzina sbagliata" in carta and "Creato per errore — Creata due volte" in carta, carta
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"cestino_edifici_elenco_{larghezza}.png"), full_page=True)
            page.click(f"[data-building-trash-item='{vuoto['id']}'] [data-building-trash-open]")
            page.wait_for_selector("[data-building-in-trash]")
            assert page.locator("#building-edit").count() == 0 and page.locator("#unit-add-apartment").count() == 0
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"cestino_edifici_scheda_{larghezza}.png"))
            page.click("[data-building-restore-btn]")
            page.wait_for_selector("#building-trash-btn")
            assert page.locator("[data-building-in-trash]").count() == 0
            assert _q(m, "SELECT deleted_at, name FROM buildings WHERE id = %s", (vuoto["id"],))[0] == [None, "Palazzina sbagliata"]
            assert vuoto["id"] in _candidati(page, m["url"], via_vuoto, "1")
            assert errori == [], errori
            browser.close()
    except Exception as exc:  # noqa: BLE001
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium non disponibile: prova in browser NON eseguita (BLOCKED)")
        raise
    for misura in misure:
        assert misura["scroll"] <= misura["inner"], misura
        assert misura["fuori"] == [], misura
