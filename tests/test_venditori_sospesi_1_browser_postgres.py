"""VENDITORI-SOSPESI-1 - prova in BROWSER (Chromium) con backend e PostgreSQL VERI.

La Shell vera servita da uvicorn con i router veri (Immobili, CORE, CRM,
Acquirenti, Abbinamenti, Proposte, Vendite, Acquisizioni) sul database
usa-e-getta; sostituita solo l'autenticazione (titolare dell'agenzia 1). Il caso
di TEST (IMM-113 / #182 / lead #85 sospeso) e' riprodotto con dati propri.

A 390 px e 1280 px, il percorso completo:
  v01  Venditori «Attivi» vuota -> «C’è 1 venditore sospeso.» + «Mostra sospesi»;
  v02  immobile -> «Elimina…» -> bloccato, con la spiegazione e il collegamento
       «Apri Venditori sospesi» -> `#/venditori/sospesi` con la card;
  v03  «Smetti…» -> solo «Non vende più» / «Inserito per errore» -> «Non vende
       più»: lead chiuso, nessuna riattivazione nello storico;
  v04  immobile -> «Elimina…» -> ora si puo' scegliere il motivo e confermare:
       l'immobile va nel Cestino (poi ripristinato dalla prova).
Nessuno scorrimento orizzontale, nessun errore di pagina. Screenshot in
`VS1_SHOTS` (o /tmp/vs1_shots).
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _q, completo  # noqa: F401
from tests.test_edifici_1_browser_postgres import SHELL, _porta_libera
from tests.test_eliminazione_edificio_1_browser_postgres import _sfora
from tests.test_venditori_1_postgres import (  # noqa: F401  (fixture riusate)
    _attiva, _contatto, _disattiva, _immobile, _proprietario, base, m,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


@pytest.fixture
def server(m):
    uvicorn = pytest.importorskip("uvicorn")
    pytest.importorskip("playwright.sync_api")
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from acquisitions.router import router as acquisizioni
    from buy.router import router as acquirenti
    from core.router import router as core
    from crm.router import router as crm
    from match.router import router as abbinamenti
    from operator_auth.dependencies import (legacy_basic_agency_context, require_authenticated_operator,
                                            require_operator)
    from property.router import router as immobili
    from proposal.router import router as proposte
    from sale.router import router as vendite

    sessione = {"user_id": m["ids"]["owner_a"], "agency_id": 1, "agency_name": "Agenzia Uno",
                "role": "agency_owner", "is_platform_admin": False, "expires_at": "2030-01-01T00:00:00Z"}

    def contesto():
        m["stato"]["chi"] = "owner_a"
        return m["ctx"]()

    app = FastAPI()

    @app.get("/api/operator-auth/me")
    def me():
        return sessione

    for r in (immobili, core, crm, acquirenti, abbinamenti, proposte, vendite, acquisizioni):
        app.include_router(r)
    for dipendenza in (legacy_basic_agency_context, require_operator, require_authenticated_operator):
        app.dependency_overrides[dipendenza] = contesto
    app.mount("/os", StaticFiles(directory=str(SHELL), html=True), name="os-shell")
    porta = _porta_libera()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="warning"))
    filo = threading.Thread(target=srv.run, daemon=True)
    filo.start()
    scadenza = time.monotonic() + 20
    while not srv.started and time.monotonic() < scadenza:
        time.sleep(0.05)
    assert srv.started, "uvicorn non partito"
    try:
        yield {"url": f"http://127.0.0.1:{porta}", **m}
    finally:
        srv.should_exit = True
        filo.join(timeout=10)


def _vai(page, url, hash_):
    page.goto(f"{url}/os/?n={time.monotonic_ns()}{hash_}")


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_v01_v04_sospeso_raggiungibile_chiudibile_poi_cestino(server, larghezza):
    from playwright.sync_api import sync_playwright
    s = server
    p = _immobile(s)
    c = _contatto(s, "Gino Sospeso")
    _proprietario(s, p["id"], c)
    lead = _attiva(s, p["id"], c).json()["lead_id"]
    assert _disattiva(s, p["id"], c, "paused").json()["status"] == "paused"
    cartella = Path(os.environ.get("VS1_SHOTS") or (Path("/tmp") / "vs1_shots"))
    cartella.mkdir(parents=True, exist_ok=True)
    misure = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": larghezza, "height": 860})
        errori = []
        page.on("pageerror", lambda x: errori.append(str(x)))

        # --- v01: Attivi vuota, ma le sospese si annunciano ----------------------
        _vai(page, s["url"], "#/venditori")
        page.wait_for_selector("[data-sellers-paused-notice]")
        assert "C’è 1 venditore sospeso." in page.inner_text("[data-sellers-paused-notice]")
        misure.append(_sfora(page))
        page.screenshot(path=str(cartella / f"vs1_attivi_vuota_{larghezza}.png"), full_page=True)

        # --- v02: Elimina bloccato, spiegazione e collegamento ---------------------
        _vai(page, s["url"], f"#/immobili/{p['id']}")
        page.wait_for_selector("#property-trash-btn")
        page.click("#property-trash-btn")
        page.wait_for_selector("[data-blocker='SELLER_OPPORTUNITY_OPEN'] [data-blocker-help]")
        aiuto = page.inner_text("[data-blocker-help]")
        assert "Venditori › Sospesi" in aiuto and "rivolgiti a un amministratore" in aiuto, aiuto
        assert page.is_disabled("[data-trash-confirm]")
        misure.append(_sfora(page))
        page.screenshot(path=str(cartella / f"vs1_elimina_bloccato_{larghezza}.png"), full_page=True)
        page.click("[data-blocker-help-link]")
        page.wait_for_selector(f".seller-card[data-lead-id='{lead}']")
        assert page.evaluate("location.hash") == "#/venditori/sospesi"
        assert page.input_value("#sellers-status") == "paused"

        # --- v03: Smetti -> Non vende più, direttamente --------------------------
        carta = f".seller-card[data-lead-id='{lead}']"
        assert page.locator(f"{carta} [data-resume='{lead}']").count() == 1
        page.click(f"{carta} [data-stop='{lead}']")
        page.wait_for_selector("#sellers-dialog [data-stop-form]")
        scelte = page.eval_on_selector_all("#sellers-dialog input[name='seller-outcome']",
                                           "els => els.map((e) => e.value)")
        assert scelte == ["not_selling", "mistake"], scelte
        misure.append(_sfora(page))
        page.screenshot(path=str(cartella / f"vs1_smetti_{larghezza}.png"), full_page=True)
        page.click("#sellers-dialog [data-submit]")
        page.wait_for_selector("#sellers-feedback .success-box")
        assert _q(s, "SELECT status FROM leads WHERE id = %s", (lead,))[0][0] == "closed"
        eventi = [r[0] for r in _q(s, "SELECT metadata->>'event' FROM activities WHERE lead_id = %s "
                                      "AND metadata ? 'event' ORDER BY id", (lead,))]
        assert eventi == ["activated", "paused", "not_selling"], eventi

        # --- v04: ora il Cestino si puo' usare ------------------------------------
        _vai(page, s["url"], f"#/immobili/{p['id']}")
        page.wait_for_selector("#property-trash-btn")
        page.click("#property-trash-btn")
        page.wait_for_selector("input[name='trash-reason']")
        assert page.locator("[data-blocker='SELLER_OPPORTUNITY_OPEN']").count() == 0
        page.check("input[name='trash-reason'][value='test_record']")
        page.click("[data-trash-confirm]")
        page.wait_for_function("() => !document.querySelector('#property-trash-dialog[open]')")
        assert _q(s, "SELECT deleted_at IS NOT NULL FROM properties WHERE id = %s", (p["id"],))[0][0] is True
        assert errori == [], errori
        browser.close()
    assert s["api"]().post(f"/api/property/properties/{p['id']}/restore").status_code == 200
    for mis in misure:
        assert mis["scroll"] <= mis["inner"] and mis["fuori"] == [], mis
