"""CENSIMENTO-2 Fase A - la UI in BROWSER (Chromium) con backend e PostgreSQL VERI.

Stesso banco di test_censimento_2_browser_postgres.py (Shell vera servita da
uvicorn, router Immobili vero, schema completo, database locale usa-e-getta),
a 390 px (smartphone) e 1280 px:

  f01  scheda edificio: «Affidabilita' della dichiarazione» al posto di «Stato
       censimento»; tre indicatori distinti (unita' create, schede complete,
       schede verificate) e la verifica di ogni interno;
  f02  scheda dell'unita': «Dati catastali e superficie» in UNA chiamata alla
       rotta census-data; interno corretto senza conferma = resta provvisorio;
       «Conferma interno» -> confermato; storico sul DB;
  f03  rifiuto del server (duplicato catastale): il modulo resta aperto, i
       valori restano, il motivo e' scritto, nessun messaggio di successo;
  f04  esito incerto (la richiesta arriva, la risposta no): la scheda si
       rilegge dal server, nessun successo dichiarato, valori conservati;
       scheda cambiata nel frattempo (STALE_DATA): differenze mostrate, si
       riprova solo su scelta esplicita.
"""
from __future__ import annotations

import pytest

from tests.test_censimento_2_browser_postgres import _cartella, _chromium, _sfora
from tests.test_censimento_3_backend_postgres import _edificio, _q, _unita  # noqa: F401
from tests.test_edifici_1_browser_postgres import DSN, completo, mondo, server  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

VERIFICA = {"cadastral_evidence_type": "visura", "cadastral_evidence_date": "2026-10-01"}


def _lotto(m, e):
    api = m["api"]()
    r = api.put(f"/api/property/buildings/{e['id']}/composition", json={
        "residential": 3, "commercial": 0, "other": 0, "pertinenze": 0, "set_total": True})
    assert r.status_code == 200, r.text
    import uuid
    gruppi = [{"unit_class": "residential", "count": 3, "property_type": "apartment", "floor": "1", "numbering_start": 1,
               "surface_sqm": "70", "surface_source": "declared"}]
    chiave = str(uuid.uuid4())
    a = api.post(f"/api/property/buildings/{e['id']}/units/generate", json={"client_request_id": chiave, "groups": gruppi})
    r = api.post(f"/api/property/buildings/{e['id']}/units/generate", json={
        "client_request_id": chiave, "groups": gruppi, "dry_run": False, "expected_existing": a.json()["existing"]})
    assert r.status_code == 200, r.text
    return [u["id"] for u in r.json()["units"]]


def _browser(p, larghezza):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": larghezza, "height": 860})
    errori = []
    page.on("pageerror", lambda x: errori.append(str(x)))
    return browser, page, errori


def _salta_se_manca(exc):
    if "Executable doesn't exist" in str(exc):
        pytest.skip("Chromium non disponibile: prova in browser NON eseguita (BLOCKED)")


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_f01_etichetta_e_tre_indicatori_separati(server, larghezza):
    m = server
    e = _edificio(m, units_declared=None, census_status="partial")
    a, b, c = _lotto(m, e)
    api = m["api"]()
    for i, pid in enumerate((a, b)):
        assert api.patch(f"/api/property/properties/{pid}/census-data", json={
            "cadastral_municipality_code": "L307", "cadastral_section": "", "cadastral_sheet": "5",
            "cadastral_parcel": "61", "cadastral_subunit": str(i + 1), "cadastral_category": "A/2",
            "cadastral_category_source": "verified", **VERIFICA,
            **({"surface_source": "verified"} if i == 0 else {})}).status_code == 200
    try:
        with _chromium() as p:
            browser, page, errori = _browser(p, larghezza)
            page.goto(f"{m['url']}/os/#/edifici/{e['id']}")
            page.wait_for_selector("[data-indicator='verification']")
            fatti = page.text_content("#building-facts")                          # le etichette sono in maiuscolo da CSS
            assert "Affidabilità della dichiarazione" in fatti and "Stato censimento" not in fatti, fatti
            assert page.inner_text("[data-indicator='units']").count("3 di 3") == 1
            assert "2 di 3 schede complete" in page.inner_text("[data-indicator='data']")
            ver = page.inner_text("[data-indicator='verification']")
            assert "SCHEDE VERIFICATE" in ver.upper()
            assert "1 di 3 schede verificate" in ver and "1 parziale" in ver and "1 da completare" in ver, ver
            assert page.get_attribute(f"[data-interior='{a}'] [data-verification]", "data-verification") == "verified"
            assert "Verifica parziale" in page.inner_text(f"[data-interior='{b}']")
            # D4: la scheda senza categoria dice «Categoria mancante» (dato) e «Verifica da completare»
            # (attendibilita'), mai due «Da verificare»
            riga_c = page.inner_text(f"[data-interior='{c}']")
            assert "Verifica da completare" in riga_c and "Da verificare" not in riga_c, riga_c
            elenco = page.inner_text("#building-units")
            assert "Categoria mancante" in elenco and "Da verificare" not in elenco, elenco
            assert "Interno provvisorio" in page.inner_text(f"[data-interior='{c}']")
            misura = _sfora(page, "#content *")
            page.screenshot(path=str(_cartella() / f"c2a_indicatori_{larghezza}.png"), full_page=True)
            assert errori == [], errori
            browser.close()
    except Exception as exc:  # noqa: BLE001
        _salta_se_manca(exc)
        raise
    assert misura["scroll"] <= misura["inner"] and misura["fuori"] == [], misura


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_f02_una_chiamata_interno_provvisorio_e_conferma(server, larghezza):
    m = server
    e = _edificio(m, units_declared=None)
    a, _b, _c = _lotto(m, e)
    try:
        with _chromium() as p:
            browser, page, errori = _browser(p, larghezza)
            chiamate = []
            page.on("request", lambda r: chiamate.append((r.method, r.url.split("/api/property", 1)[-1]))
                    if "/api/property/properties/" in r.url and r.method != "GET" else None)
            page.goto(f"{m['url']}/os/#/immobili/{a}")
            page.wait_for_selector("#census-confirm-internal")
            assert "Interno provvisorio" in page.inner_text(".census-tab")
            # correzione del numero e della categoria, SENZA conferma: una chiamata, resta provvisorio
            page.click("#census-edit-cadastral")
            page.wait_for_selector("[data-unit-sources]")
            assert page.is_visible("#cc-confirm-internal")
            page.fill("#cc-internal", "1A")
            page.click("#census-cat-btn")
            page.click("[data-pick-category='A/2']")
            page.select_option("#cc-category-source", "declared")
            misura = _sfora(page, "#content *")
            page.click("#cc-save")
            page.wait_for_function("() => ((document.querySelector('.census-tab') || {}).textContent || '').includes('int. 1A')")
            assert chiamate == [("PATCH", f"/properties/{a}/census-data")], chiamate
            assert _q(m, "SELECT internal_number, cadastral_category, cadastral_category_source, "
                         "metadata->'census_batch'->>'provisional_numbering' FROM properties WHERE id = %s", (a,))[0] == [
                "1A", "A/2", "declared", "true"]
            assert page.is_visible("#census-confirm-internal")
            # «Conferma interno»: stessa scheda, cade solo «provvisorio»
            page.click("#census-confirm-internal")
            page.wait_for_function("() => !document.querySelector('#census-confirm-internal')")
            assert "Interno provvisorio" not in page.inner_text(".census-tab")
            assert _q(m, "SELECT metadata->'census_batch'->>'provisional_numbering' FROM properties WHERE id = %s",
                      (a,))[0][0] == "false"
            storia = _q(m, "SELECT metadata->'changes' FROM activities WHERE property_id = %s ORDER BY id", (a,))
            assert [sorted(x[0]) for x in storia] == [["cadastral_category", "cadastral_category_source", "internal_number"],
                                                      ["internal_number_status"]]
            page.screenshot(path=str(_cartella() / f"c2a_interno_confermato_{larghezza}.png"), full_page=True)
            assert errori == [], errori
            browser.close()
    except Exception as exc:  # noqa: BLE001
        _salta_se_manca(exc)
        raise
    assert misura["scroll"] <= misura["inner"], misura


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_f03_rifiuto_modulo_aperto_valori_conservati(server, larghezza):
    m = server
    e = _edificio(m)
    _unita(m, building_id=e["id"], floor="1", cadastral_municipality_code="L307", cadastral_section="",
           cadastral_sheet="5", cadastral_parcel="61", cadastral_subunit="2")
    u = _unita(m, building_id=e["id"], floor="2", surface_sqm="50")
    try:
        with _chromium() as p:
            browser, page, errori = _browser(p, larghezza)
            page.goto(f"{m['url']}/os/#/immobili/{u['id']}")
            page.wait_for_selector("#census-edit-cadastral")
            page.click("#census-edit-cadastral")
            page.wait_for_selector("[data-unit-sources]")
            page.fill("#cc-belfiore", "L307")
            page.fill("#cc-sheet", "5")
            page.fill("#cc-parcel", "61")
            page.fill("#cc-subunit", "2")
            page.click("[data-chip='cadastral_section_mode'][data-value='none']")      # «Nessuna»: identita' completa
            page.fill("#cc-surface", "55")
            page.select_option("#cc-surface-source", "declared")
            page.click("#cc-save")
            page.wait_for_function("() => (document.querySelector('#cc-error') || {}).textContent")
            assert "già censito" in page.inner_text("#cc-error")
            assert not page.locator("#census-cadastral-form").is_hidden()                 # resta aperto
            assert (page.input_value("#cc-subunit"), page.input_value("#cc-surface")) == ("2", "55")   # valori conservati
            assert page.locator("[data-census-toast]").count() == 0 or "salvati" not in page.inner_text("[data-census-toast]")
            assert _q(m, "SELECT surface_sqm, cadastral_subunit FROM properties WHERE id = %s", (u["id"],))[0][1] is None
            assert _q(m, "SELECT count(*) FROM activities WHERE property_id = %s", (u["id"],))[0][0] == 0
            page.screenshot(path=str(_cartella() / f"c2a_rifiuto_{larghezza}.png"), full_page=True)
            assert errori == [], errori
            browser.close()
    except Exception as exc:  # noqa: BLE001
        _salta_se_manca(exc)
        raise


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_f04_esito_incerto_e_scheda_cambiata_nel_frattempo(server, larghezza):
    m = server
    e = _edificio(m)
    u = _unita(m, building_id=e["id"], floor="2", surface_sqm="50")
    try:
        with _chromium() as p:
            browser, page, errori = _browser(p, larghezza)
            persa = {"attiva": True}

            def risposta_persa(route):
                # la richiesta ARRIVA al server (che salva), la risposta no
                if persa["attiva"] and route.request.method == "PATCH":
                    persa["attiva"] = False
                    route.fetch()
                    route.abort("connectionreset")
                    return
                route.continue_()
            page.route("**/census-data", risposta_persa)
            page.goto(f"{m['url']}/os/#/immobili/{u['id']}")
            page.wait_for_selector("#census-edit-cadastral")
            page.click("#census-edit-cadastral")
            page.wait_for_selector("[data-unit-sources]")
            page.fill("#cc-surface", "52")
            page.select_option("#cc-surface-source", "declared")
            page.click("#cc-save")
            page.wait_for_selector("#cc-reconcile:not([hidden])")
            testo = page.inner_text("#cc-reconcile")
            assert "Esito non noto" in testo and "riletta dal server" in testo, testo
            assert "coincidono" in testo                                          # il server aveva salvato
            assert not page.locator("#census-cadastral-form").is_hidden()
            assert page.input_value("#cc-surface") == "52"
            assert page.locator("[data-census-toast]").count() == 0 or "salvati" not in page.inner_text("[data-census-toast]")
            assert _q(m, "SELECT surface_sqm, surface_source FROM properties WHERE id = %s", (u["id"],))[0][1] == "declared"
            assert _q(m, "SELECT count(*) FROM activities WHERE property_id = %s", (u["id"],))[0][0] == 1
            # un collega cambia la scheda mentre il modulo e' aperto: nessuna sovrascrittura inconsapevole
            page.fill("#cc-surface", "60")
            page.select_option("#cc-surface-source", "estimated")
            assert m["api"]().patch(f"/api/property/properties/{u['id']}/census-data",
                                    json={"surface_sqm": "58", "surface_source": "declared"}).status_code == 200
            page.click("#cc-save")
            page.wait_for_function("() => ((document.querySelector('#cc-reconcile') || {}).textContent || '').includes('modificata')")
            testo = page.inner_text("#cc-reconcile")
            assert "salvato 58" in testo.replace("\n", " ") and "inserito 60" in testo.replace("\n", " "), testo
            assert _q(m, "SELECT surface_sqm FROM properties WHERE id = %s", (u["id"],))[0][0] == 58
            misura = _sfora(page, "#content *")
            page.screenshot(path=str(_cartella() / f"c2a_riconciliazione_{larghezza}.png"), full_page=True)
            # scelta esplicita: «Salva» applica i valori inseriti ai dati appena riletti
            page.click("#cc-save")
            page.wait_for_function("() => document.querySelector('#census-cadastral-form') && document.querySelector('#census-cadastral-form').hidden")
            assert _q(m, "SELECT surface_sqm, surface_source FROM properties WHERE id = %s", (u["id"],))[0] == [60, "estimated"]
            assert errori == [], errori
            browser.close()
    except Exception as exc:  # noqa: BLE001
        _salta_se_manca(exc)
        raise
    assert misura["scroll"] <= misura["inner"], misura
