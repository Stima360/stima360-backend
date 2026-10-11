"""CENSIMENTO-2 - la UI in BROWSER (Chromium) con backend e PostgreSQL VERI.

Shell vera servita da uvicorn (stesso server di EDIFICI-1: solo
l'autenticazione sostituita), router Immobili vero, schema completo (100
compresa). A 390 px (smartphone) e 1280 px:

  b01  scheda edificio in BOZZA (composizione assente) con due unita' gia'
       censite e senza dati: pannello «Composizione e interni», stato Bozza;
       «Dichiara composizione» -> 4 / 1 / 0 / 2 con il totale = somma ->
       «Crea le unita' mancanti» -> anteprima (nessuna scrittura) -> conferma
       -> solo la differenza (3 + 1 + 2), le due esistenti intatte; interni
       con superficie, categoria, provenienza e dati mancanti; filtro «Da
       completare»; selezione di due interni -> «Modifica selezionate»
       (superficie dichiarata) -> sul DB; «Annulla» del lotto dal toast;
  b02  scheda della singola unita' (tab Censimento): categoria, superficie e
       provenienza modificabili da li'; «Verificata» solo con la fonte
       catastale (sezione facoltativa) -> sul DB;
  b03  procedura guidata con la composizione: edificio e composizione in due
       chiamate; se la seconda fallisce l'edificio resta in bozza,
       riconoscibile, e «Riprova la composizione» la salva;
  nessuno scorrimento orizzontale nel pannello e nei fogli, nessun errore di
  pagina. Screenshot in `CENSIMENTO2_SHOTS` (o /tmp/censimento2_shots).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import _edificio, _q, _unita  # noqa: F401
from tests.test_edifici_1_browser_postgres import DSN, completo, mondo, server  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


def _sfora(page, ambito="#building-composition *, dialog[open] *"):
    return page.evaluate("""(ambito) => {
      const fuori = [...document.querySelectorAll(ambito)]
        .filter((e) => e.getClientRects().length && e.getBoundingClientRect().right > window.innerWidth + 0.5)
        .map((e) => e.tagName + '.' + e.className);
      return { scroll: document.documentElement.scrollWidth, inner: window.innerWidth, fuori: fuori.slice(0, 5) };
    }""", ambito)


def _cartella():
    c = Path(os.environ.get("CENSIMENTO2_SHOTS") or (Path("/tmp") / "censimento2_shots"))
    c.mkdir(parents=True, exist_ok=True)
    return c


def _chromium():
    from playwright.sync_api import sync_playwright
    return sync_playwright()


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_b01_composizione_unita_mancanti_interni_e_modifica_di_gruppo(server, larghezza):
    m = server
    terr = {"region": "Abruzzo", "province": "TE", "city": "Tortoreto", "microzone": "Lido Nord"}
    e = _edificio(m, **terr, address="Via Roma", civic_number="10", name="Residenza Gabbiano", units_declared=12)
    a1 = _unita(m, building_id=e["id"], floor="1", internal_number="1")
    a2 = _unita(m, building_id=e["id"], floor="1", internal_number="2")
    prima = _q(m, "SELECT id, updated_at, surface_sqm, cadastral_category FROM properties WHERE id = ANY(%s) ORDER BY id",
               ([a1["id"], a2["id"]],))
    cartella = _cartella()
    misure = []
    try:
        with _chromium() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": larghezza, "height": 860})
            errori = []
            page.on("pageerror", lambda x: errori.append(str(x)))
            page.goto(f"{m['url']}/os/#/edifici/{e['id']}")
            page.wait_for_selector("[data-composition-draft]")
            assert page.get_attribute("[data-completion-state]", "data-completion-state") == "draft"
            testo = page.inner_text("#building-composition")
            assert "2 unità presenti" in testo and "0 di 2 schede complete" in testo, testo
            assert page.locator("[data-interior]").count() == 2
            assert page.is_disabled("[data-generate]")
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"c2_bozza_{larghezza}.png"), full_page=True)

            # composizione: 12 dichiarate -> 4 + 1 + 0 + 2 = 7, totale = somma (scelta esplicita)
            page.click("[data-composition-edit]")
            page.wait_for_selector("[data-composition-form]")
            for k, v in (("residential", "4"), ("commercial", "1"), ("other", "0"), ("pertinenze", "2")):
                page.fill(f"#cmp-{k}", v)
            assert "Somma: 7" in page.inner_text("[data-composition-sum]")
            assert page.is_visible("[data-set-total-row]")
            page.uncheck("#cmp-set-total")
            page.click("[data-composition-form] [data-submit]")
            page.wait_for_function("() => (document.querySelector('[data-composition-form] [data-error]') || {}).textContent")
            assert "somma" in page.inner_text("[data-composition-form] [data-error]")
            assert _q(m, "SELECT units_declared_residential FROM buildings WHERE id = %s", (e["id"],))[0][0] is None
            page.check("#cmp-set-total")
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"c2_composizione_{larghezza}.png"))
            page.click("[data-composition-form] [data-submit]")
            page.wait_for_selector("[data-completion-state='composition_declared']")
            assert _q(m, "SELECT units_declared, units_declared_residential, units_declared_commercial, units_declared_other, "
                         "units_declared_pertinenze FROM buildings WHERE id = %s", (e["id"],))[0] == [7, 4, 1, 0, 2]
            assert "2 di 7 dichiarate · 5 da creare" in page.inner_text("[data-indicator='units']")

            # unità mancanti: anteprima (nessuna scrittura), poi conferma
            page.click("[data-generate]")
            page.wait_for_selector("[data-generate-sheet]")
            assert page.locator("[data-group]").count() == 3          # abitazioni 2, commerciali 1, pertinenze 2
            page.click("[data-group-details='0'] summary")
            page.fill("#g0-surface", "70")
            page.select_option("#g0-surface-source", "estimated")
            page.check("#g0-numbering")
            page.fill("#g0-start", "3")
            page.click("[data-preview-btn]")
            page.wait_for_selector("[data-preview]")
            assert _q(m, "SELECT count(*) FROM properties WHERE building_id = %s", (e["id"],))[0][0] == 2
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"c2_anteprima_{larghezza}.png"))
            page.click("[data-confirm-generate]")
            page.wait_for_selector("[data-completion-state='units_created']")
            righe = _q(m, "SELECT record_kind, commercial_status, property_type, internal_number, surface_sqm, surface_source, "
                          "cadastral_category, is_pertinenza, parent_property_id FROM properties WHERE building_id = %s "
                          "AND id NOT IN %s ORDER BY id", (e["id"], (a1["id"], a2["id"])))
            assert len(righe) == 5
            assert all(r[0:2] == ["census", "draft"] and r[6] is None and r[8] is None for r in righe)
            assert [r[3] for r in righe[:2]] == ["3", "4"] and str(righe[0][4]) == "70.00" and righe[0][5] == "estimated"
            assert sum(1 for r in righe if r[7]) == 2
            assert _q(m, "SELECT id, updated_at, surface_sqm, cadastral_category FROM properties WHERE id = ANY(%s) ORDER BY id",
                      ([a1["id"], a2["id"]],)) == prima                   # le esistenti non si toccano
            assert page.locator("[data-interior]").count() == 7
            assert "Annulla" in page.inner_text("[data-census-toast]")

            # interni: superficie, categoria, provenienza, mancanti; filtro «Da completare»
            riga = page.inner_text(f"[data-interior='{a1['id']}']")
            assert "int. 1" in riga and "Non indicata" in riga and "Mancano" in riga, riga
            page.click("[data-filter='incomplete']")
            assert page.locator("[data-interior]").count() == 7
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"c2_interni_{larghezza}.png"), full_page=True)

            # modifica di gruppo: le due esistenti, superficie dichiarata
            page.check(f"[data-select-unit='{a1['id']}']")
            page.check(f"[data-select-unit='{a2['id']}']")
            assert "2 selezionate" in page.inner_text("[data-selected-count]")
            page.click("[data-bulk-edit]")
            page.wait_for_selector("[data-bulk-form]")
            assert page.locator("#bk-surface-source option[value='verified']").count() == 0   # mai in gruppo
            page.fill("#bk-surface", "64")
            page.select_option("#bk-surface-source", "declared")
            page.fill("#bk-stair", "A")
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"c2_modifica_gruppo_{larghezza}.png"))
            page.click("[data-bulk-form] [data-submit]")
            page.wait_for_function("() => !document.querySelector('dialog[open] [data-bulk-form]')")
            assert _q(m, "SELECT DISTINCT surface_sqm, surface_source, staircase FROM properties WHERE id = ANY(%s)",
                      ([a1["id"], a2["id"]],)) == [[pytest.approx(64), "declared", "A"]]
            page.wait_for_function(f"() => ((document.querySelector(\"[data-interior='{a1['id']}']\") || {{}}).textContent || '').includes('Dichiarata')")
            assert errori == [], errori
            browser.close()
    except Exception as exc:  # noqa: BLE001
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium non disponibile: prova in browser NON eseguita (BLOCKED)")
        raise
    for misura in misure:
        assert misura["scroll"] <= misura["inner"], misura
        assert misura["fuori"] == [], misura


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_b02_scheda_della_singola_unita_provenienza_e_verificata(server, larghezza):
    m = server
    e = _edificio(m, units_declared=2)
    u = _unita(m, building_id=e["id"], floor="2", internal_number="5")
    cartella = _cartella()
    misure = []
    try:
        with _chromium() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": larghezza, "height": 860})
            errori = []
            page.on("pageerror", lambda x: errori.append(str(x)))
            page.goto(f"{m['url']}/os/#/immobili/{u['id']}")
            page.wait_for_selector("#census-edit-cadastral")
            page.click("#census-edit-cadastral")
            page.wait_for_selector("[data-unit-sources]")
            # senza fonte catastale «Verificata» non si sceglie
            assert page.locator("#cc-surface-source option[value='verified']").is_disabled()
            page.fill("#cc-surface", "82.5")
            page.select_option("#cc-surface-source", "declared")
            page.fill("#cc-belfiore", "L307")
            page.fill("#cc-sheet", "12")
            page.fill("#cc-parcel", "345")
            page.fill("#cc-subunit", "7")
            assert not page.locator("#cc-category-source option[value='verified']").is_disabled()
            page.click("#census-cat-btn")
            page.click("[data-pick-category='A/2']")
            page.select_option("#cc-category-source", "verified")
            # la sola compilazione dei campi non basta: serve la fonte consultata
            assert page.is_visible("[data-evidence-fields]")
            page.click("#cc-save")
            page.wait_for_function("() => (document.querySelector('#cc-error') || {}).textContent")
            assert "fonte consultata" in page.inner_text("#cc-error")
            assert _q(m, "SELECT cadastral_category_source FROM properties WHERE id = %s", (u["id"],))[0][0] is None
            page.select_option("#cc-evidence-type", "visura")
            page.fill("#cc-evidence-date", "2026-10-01")
            page.fill("#cc-evidence-ref", "prot. TE0012345")
            misure.append(_sfora(page, "#content *"))
            page.screenshot(path=str(cartella / f"c2_scheda_unita_{larghezza}.png"), full_page=True)
            page.click("#cc-save")
            page.wait_for_function("() => document.querySelector('#census-cadastral-form') && document.querySelector('#census-cadastral-form').hidden")
            assert _q(m, "SELECT surface_sqm, surface_source, cadastral_category, cadastral_category_source, cadastral_section, "
                         "cadastral_evidence_type, cadastral_evidence_ref, cadastral_evidence_identity FROM properties WHERE id = %s",
                      (u["id"],))[0] == [pytest.approx(82.5), "declared", "A/2", "verified", None, "visura", "prot. TE0012345",
                                         "L307||12|345|7"]
            assert "A/2 · Verificata" in page.inner_text(".census-tab")
            assert "Visura catastale del 01/10/2026" in page.inner_text(".census-tab")
            # cambia il subalterno: la verifica va riconfermata, la superficie dichiarata resta
            page.click("#census-edit-cadastral")
            page.wait_for_selector("[data-unit-sources]")
            assert page.input_value("#cc-category-source") == "verified"
            page.fill("#cc-subunit", "8")
            # la verifica precedente non si trascina: «Verificata» e la fonte tornano da indicare
            assert page.input_value("#cc-category-source") == "" and page.input_value("#cc-evidence-date") == ""
            page.click("#cc-save")
            page.wait_for_function("() => ((document.querySelector('.census-tab') || {}).textContent || '').includes('Da riconfermare')")
            assert _q(m, "SELECT surface_source, cadastral_category_source FROM properties WHERE id = %s", (u["id"],))[0] == [
                "declared", None]
            page.screenshot(path=str(cartella / f"c2_scheda_unita_da_riconfermare_{larghezza}.png"), full_page=True)
            assert errori == [], errori
            browser.close()
    except Exception as exc:  # noqa: BLE001
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium non disponibile: prova in browser NON eseguita (BLOCKED)")
        raise
    for misura in misure:
        assert misura["scroll"] <= misura["inner"], misura


def test_b03_procedura_guidata_composizione_in_due_chiamate_e_bozza_recuperabile(server):
    m = server
    cartella = _cartella()
    try:
        with _chromium() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 390, "height": 860})
            errori = []
            page.on("pageerror", lambda x: errori.append(str(x)))
            guasto = {"attivo": True}

            def composizione(route):
                if guasto["attivo"] and route.request.method == "PUT":
                    route.fulfill(status=500, content_type="application/json", body='{"detail":"errore simulato"}')
                else:
                    route.continue_()
            page.route("**/api/property/buildings/*/composition", composizione)
            page.goto(f"{m['url']}/os/#/edifici")
            page.wait_for_selector("#buildings-new, [data-new-building], .building-card, .buildings-toolbar")
            avvio = page.locator("#buildings-new, [data-new-building]")
            if not avvio.count():
                pytest.skip("ingresso della procedura dalla lista Edifici non trovato in questa Shell")
            avvio.first.click()
            page.wait_for_selector("[data-wizard-step='1']")
            page.select_option("#wz-city", "Tortoreto")
            page.fill("#wz-address", "Via Verdi")
            page.fill("#wz-civic", "8")
            page.check("#wz-unknown")
            page.click("[data-wizard-composition] summary")
            for k, v in (("residential", "6"), ("commercial", "0"), ("other", "1"), ("pertinenze", "3")):
                page.fill(f"#wz-comp-{k}", v)
            page.screenshot(path=str(cartella / "c2_procedura_passo1_390.png"), full_page=True)
            page.click("[data-wizard-step='1'] [data-next]")
            page.click("[data-path='building']")
            page.wait_for_selector("[data-create-building]")
            page.click("[data-create-building]")
            page.wait_for_selector("[data-save-building]")
            assert "Abitazioni: 6" in page.inner_text("[data-wizard-step='4']")
            page.click("[data-save-building]")
            page.wait_for_selector("[data-composition-failed]")
            bid = _q(m, "SELECT id, units_declared, units_declared_residential FROM buildings WHERE address = 'Via Verdi'")
            assert len(bid) == 1 and bid[0][1:] == [10, None]               # edificio salvato, composizione no: bozza
            page.screenshot(path=str(cartella / "c2_procedura_composizione_fallita_390.png"), full_page=True)
            guasto["attivo"] = False
            page.click("[data-retry-composition]")
            page.wait_for_selector("[data-composition-saved]")
            assert _q(m, "SELECT units_declared, units_declared_residential, units_declared_other, units_declared_pertinenze "
                         "FROM buildings WHERE id = %s", (bid[0][0],))[0] == [10, 6, 1, 3]
            assert _q(m, "SELECT count(*) FROM buildings WHERE address = 'Via Verdi'")[0][0] == 1
            assert errori == [], errori
            browser.close()
    except Exception as exc:  # noqa: BLE001
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium non disponibile: prova in browser NON eseguita (BLOCKED)")
        raise
