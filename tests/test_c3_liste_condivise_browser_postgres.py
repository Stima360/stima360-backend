"""AGENZIE-TERRITORI C3 - l'elenco «Immobili» unificato in BROWSER (Chromium),
backend e PostgreSQL VERI, a 390 e 1280 px.

  owner_b  Supervisore di «Due» (Giulianova);  agent_b Collaboratore di «Due»;
  owner_c  Supervisore di «Tre» (nessun Comune).

  1  owner_b: vista iniziale «Tutti gli accessibili» con i conteggi delle tre viste;
     la riga propria porta «La mia agenzia» (e «Elimina…»), la condivisa porta
     «Condiviso», agenzia d'origine e Comune (e nessun «Elimina…»);
  2  «Condivisi nel territorio» e «La mia agenzia» mostrano solo le loro righe,
     con il totale del pager uguale al conteggio; il filtro Comune restringe;
  3  la riga condivisa apre la scheda in sola lettura di C2 con «Proponi modifica»;
  4  agent_b: stessa riga, scheda senza «Proponi modifica»;
  5  owner_c: «Condivisi nel territorio» vuoto, nessuna scheda di terzi.
Nessun errore di pagina, nessuno scorrimento orizzontale, nessuna scrittura.
"""
from __future__ import annotations

import threading
import time

import pytest

from tests.test_c1_lettura_territoriale_postgres import _immobile, _operatore, rete  # noqa: F401
from tests.test_censimento_3_backend_postgres import _q  # noqa: F401
from tests.test_edifici_1_browser_postgres import DSN, SHELL, _porta_libera, completo, mondo  # noqa: F401
from tests.test_eliminazione_edificio_1_browser_postgres import _sfora, _vai

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


@pytest.fixture
def server(rete, monkeypatch):
    uvicorn = pytest.importorskip("uvicorn")
    pytest.importorskip("playwright.sync_api")
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from operator_auth.dependencies import (legacy_basic_agency_context, require_authenticated_operator,
                                            require_operator)
    from property.router import router as immobili

    m = rete
    scad = "2030-01-01T00:00:00Z"
    sessioni = {
        "owner_b": {"user_id": m["ruoli"]["owner_b"][0], "agency_id": 2, "agency_name": "Due", "role": "agency_owner"},
        "agent_b": {"user_id": m["ruoli"]["agent_b"][0], "agency_id": 2, "agency_name": "Due", "role": "agent"},
        "owner_c": {"user_id": m["ruoli"]["owner_c"][0], "agency_id": m["tre"], "agency_name": "Tre", "role": "agency_owner"},
    }
    stato = {"chi": "owner_b"}

    def tenant():
        m["stato"]["chi"] = stato["chi"]
        return m["ctx"]()

    app = FastAPI()

    @app.get("/api/operator-auth/me")
    def me():
        return {**sessioni[stato["chi"]], "is_platform_admin": False, "expires_at": scad}

    app.include_router(immobili)
    for dipendenza in (legacy_basic_agency_context, require_operator, require_authenticated_operator):
        app.dependency_overrides[dipendenza] = tenant
    app.mount("/os", StaticFiles(directory=str(SHELL), html=True), name="os-shell")
    porta = _porta_libera()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="warning"))
    filo = threading.Thread(target=srv.run, daemon=True)
    filo.start()
    limite = time.monotonic() + 20
    while not srv.started and time.monotonic() < limite:
        time.sleep(0.05)
    assert srv.started
    try:
        yield {**m, "url": f"http://127.0.0.1:{porta}", "come": stato}
    finally:
        srv.should_exit = True
        filo.join(timeout=10)


def _conteggi(page):
    return page.eval_on_selector_all("[data-scope-count]", "els => Object.fromEntries(els.map(e => [e.dataset.scopeCount, e.textContent]))")


def _righe(page):
    return page.eval_on_selector_all(
        "#immobili-list-area tr.row-clickable",
        "rs => rs.map(r => ({id: r.dataset.rowId, origin: (r.querySelector('.origin-line') || {}).dataset?.origin,"
        " text: r.innerText, del: !!r.querySelector('.trash-row-cell')}))")


def _scegli(page, scope):
    page.click(f"[data-scope='{scope}']")
    page.wait_for_function(f"document.querySelector(\"[data-scope='{scope}']\").getAttribute('aria-pressed') === 'true'")
    page.wait_for_selector("#immobili-pager [data-pager-text]")      # load() svuota il pager e lo riscrive a fine giro


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_c3_elenco_unificato_in_chromium(server, larghezza):
    from playwright.sync_api import sync_playwright
    m = server
    cond = _immobile(m, "owner_a", "Giulianova")                # di «Uno», nel Comune di «Due»
    mio = _immobile(m, "owner_b", "Giulianova")                 # di «Due»
    _immobile(m, "owner_a", "Martinsicuro")                     # di «Uno», mai condiviso
    prima = _q(m, "SELECT count(*), max(updated_at) FROM properties")[0]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": larghezza, "height": 900})
        errori = []
        page.on("pageerror", lambda x: errori.append(str(x)))
        misure = []

        # 1 - vista iniziale
        m["come"]["chi"] = "owner_b"
        _vai(page, m["url"], "#/immobili")
        page.wait_for_selector(f"#immobili-list-area tr[data-row-id='{cond['id']}']")
        assert page.get_attribute("[data-scope='all']", "aria-pressed") == "true"
        atteso = page.evaluate("fetch('/api/property/properties/accessible?limit=1').then(r => r.json()).then(j => j.counts)")
        assert _conteggi(page) == {k: f"({v})" for k, v in atteso.items()}, (_conteggi(page), atteso)
        righe = {r["id"]: r for r in _righe(page)}
        assert righe[str(cond["id"])]["origin"] == "shared" and not righe[str(cond["id"])]["del"]
        origine = _q(m, "SELECT name FROM agencies WHERE id = 1")[0][0]
        assert "Condiviso" in righe[str(cond["id"])]["text"] and origine in righe[str(cond["id"])]["text"]
        assert "Giulianova" in righe[str(cond["id"])]["text"]
        assert righe[str(mio["id"])]["origin"] == "mine" and "La mia agenzia" in righe[str(mio["id"])]["text"]
        assert righe[str(mio["id"])]["del"]
        assert f"di {atteso['all']}" in page.inner_text("[data-pager-text]")
        misure.append(_sfora(page))
        page.screenshot(path=f"/tmp/c3_tutti_{larghezza}.png", full_page=True)

        # 2 - le altre viste e il filtro Comune
        _scegli(page, "shared")
        righe = _righe(page)
        assert righe and all(r["origin"] == "shared" and not r["del"] for r in righe)
        assert str(mio["id"]) not in {r["id"] for r in righe}
        assert f"di {atteso['shared']}" in page.inner_text("[data-pager-text]")
        misure.append(_sfora(page))
        page.screenshot(path=f"/tmp/c3_condivisi_{larghezza}.png", full_page=True)
        _scegli(page, "mine")
        righe = _righe(page)
        assert righe and all(r["origin"] == "mine" for r in righe)
        assert str(cond["id"]) not in {r["id"] for r in righe}
        assert f"di {atteso['mine']}" in page.inner_text("[data-pager-text]")
        _scegli(page, "all")
        page.fill("#immobili-city", "Martinsicuro")
        page.wait_for_function("document.querySelector(\"[data-scope-count='all']\").textContent === '(0)'")
        assert "Nessun immobile trovato" in page.inner_text("#immobili-list-area")
        page.fill("#immobili-city", "Giulianova")
        page.wait_for_selector(f"#immobili-list-area tr[data-row-id='{cond['id']}']")
        assert {r["origin"] for r in _righe(page)} == {"mine", "shared"}

        # 3 - la riga condivisa apre la scheda C2 del Supervisore
        page.click(f"#immobili-list-area tr[data-row-id='{cond['id']}']")
        page.wait_for_selector(f"[data-shared-property='{cond['id']}']")
        page.wait_for_selector("[data-tc-open]")
        assert "Sola lettura" in page.inner_text("[data-shared-property]")
        assert page.locator("button:has-text('Salva'), #property-edit, [data-trash-open]").count() == 0
        misure.append(_sfora(page))

        # 4 - Collaboratore: lettura senza proposta
        m["come"]["chi"] = "agent_b"
        _vai(page, m["url"], "#/immobili")
        page.wait_for_selector(f"#immobili-list-area tr[data-row-id='{cond['id']}']")
        page.click(f"#immobili-list-area tr[data-row-id='{cond['id']}']")
        page.wait_for_selector(f"[data-shared-property='{cond['id']}'] [data-tc-empty]")
        assert page.locator("[data-tc-open]").count() == 0

        # 5 - agenzia non competente
        m["come"]["chi"] = "owner_c"
        _vai(page, m["url"], "#/immobili")
        page.wait_for_selector("[data-scope-count='shared']:not(:empty)")
        assert page.inner_text("[data-scope-count='shared']") == "(0)"
        assert str(cond["id"]) not in {r["id"] for r in _righe(page)}
        _scegli(page, "shared")
        assert "Nessun immobile di altre agenzie nel tuo territorio." in page.inner_text("#immobili-list-area")
        misure.append(_sfora(page))

        assert errori == [], errori
        for mis in misure:
            assert mis["scroll"] <= mis["inner"] + 1 and mis["fuori"] == [], mis
        browser.close()
    assert _q(m, "SELECT count(*), max(updated_at) FROM properties")[0] == prima      # nessuna scrittura
