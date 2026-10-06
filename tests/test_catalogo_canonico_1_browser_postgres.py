"""CATALOGO-CANONICO-1 - prova in BROWSER (Chromium) con backend e PostgreSQL VERI.

La stima arriva dagli endpoint VERI del sito (`main.salva_stima` e
`main.salva_stima_dettagliata`, stesso harness di
tests/test_catalogo_canonico_1_postgres.py); la scheda si apre nella Shell vera
servita da uvicorn (stesso server di EDIFICI-1, solo l'autenticazione
sostituita). A 1280 px e 390 px:

  b01  la scheda nata dalla stima si apre sulla tab Censimento con «Dal sito
       Stima360»: stima, dettagliata, valore da verificare, differenza;
       «Applica» scrive il valore del sito;
  b02  «Modifica immobile»: Stato dal catalogo, Vista mare «Non indicato»
       (null, non "No"), salvataggio e riapertura con i valori salvati;
  b03  Panoramica «Mare e dotazioni»; nessuno scorrimento orizzontale.
Screenshot in `EDIFICI1_SHOTS` (o /tmp).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.test_catalogo_canonico_1_postgres import COMPLETA, _persona, sito  # noqa: F401
from tests.test_censimento_3_backend_postgres import _q  # noqa: F401
from tests.test_edifici_1_browser_postgres import DSN, completo, mondo, server  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


def _sfora(page):
    return page.evaluate("""() => {
      const fuori = [...document.querySelectorAll('#property-tab-content *, dialog[open] *')]
        .filter((e) => e.getClientRects().length && e.getBoundingClientRect().right > window.innerWidth + 0.5)
        .map((e) => e.tagName + '.' + e.className);
      return { scroll: document.documentElement.scrollWidth, inner: window.innerWidth, fuori: fuori.slice(0, 5) };
    }""")


@pytest.mark.parametrize("larghezza", [1280, 390])
def test_b01_b02_b03_dal_sito_alla_scheda_in_chromium(sito, server, larghezza):
    from playwright.sync_api import sync_playwright
    s, m = sito, server
    sid = s.stima({**COMPLETA, **_persona(), "pertinenze": "garage, posto barca, balconi"})["id"]
    pid = s.scheda_di(sid)
    assert s.api().patch(f"/api/property/properties/{pid}", json={"rooms": 4}).status_code == 200
    s.dettaglio({"stima_id": sid, "locali": "3", "classe": "C", "riscaldamento": "Autonomo"})
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

            # --- b01: tab Censimento, «Dal sito Stima360» -------------------------------
            page.goto(f"{m['url']}/os/#/immobili/{pid}")
            page.wait_for_selector("#site-provenance")
            testo = page.text_content("#site-provenance")
            for atteso in ("Dal sito Stima360", f"Stima n. {sid}", "stima dettagliata", "Da verificare", "posto barca",
                           "Locali", "sito: 3", "scheda: 4", "Applica", "Ignora"):
                assert atteso in testo, atteso
            assert "Dal sito" in page.inner_text("#property-tab-content")       # badge sugli accessori
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"catalogo_provenienza_{larghezza}.png"), full_page=True)
            page.click("[data-conflict-apply]")
            page.wait_for_function("() => !document.querySelector('[data-conflict-apply]')")
            assert _q(m, "SELECT rooms FROM properties WHERE id = %s", (pid,))[0][0] == 3

            # --- b02: «Modifica immobile» ----------------------------------------------
            page.click("#property-edit-btn")
            page.wait_for_selector("#property-form")
            assert page.input_value("#pf-sea-view") == "true" and page.input_value("#pf-condition") == "ristrutturato"
            assert page.input_value("#pf-heating") == "Autonomo" and page.input_value("#pf-energy") == "C"
            page.screenshot(path=str(cartella / f"catalogo_form_{larghezza}.png"), full_page=True)
            misure.append(_sfora(page))
            page.select_option("#pf-sea-view", "")
            page.select_option("#pf-condition", "nuovo")
            page.fill("#pf-other-features", "Doppio ingresso")
            page.click("#property-form-submit")
            page.wait_for_selector("#property-form", state="hidden")
            riga = _q(m, "SELECT sea_view, condition, other_features, sea_position FROM properties WHERE id = %s", (pid,))[0]
            assert list(riga) == [None, "nuovo", "Doppio ingresso", "seconda"]
            page.click("#property-edit-btn")
            page.wait_for_selector("#property-form")
            assert page.input_value("#pf-sea-view") == "" and page.input_value("#pf-condition") == "nuovo"
            page.click("#property-form-cancel")

            # --- b03: Panoramica ---------------------------------------------------------
            page.click("[data-tab='panoramica']")
            page.wait_for_selector("#property-site-attributes")
            pan = page.inner_text("#property-site-attributes")
            assert "Seconda fila" in pan and "100-300 m" in pan and "Autonomo" in pan
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"catalogo_panoramica_{larghezza}.png"), full_page=True)
            assert errori == [], errori
            browser.close()
    except Exception as exc:  # noqa: BLE001
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium non disponibile: prova in browser NON eseguita (BLOCKED)")
        raise
    for misura in misure:
        assert misura["scroll"] <= misura["inner"], misura
        assert misura["fuori"] == [], misura
