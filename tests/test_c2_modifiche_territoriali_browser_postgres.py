"""AGENZIE-TERRITORI C2 - in BROWSER (Chromium) con backend e PostgreSQL VERI.

La Shell vera con i router veri (Immobili, Platform). Sessioni di prova:
  owner_b  Supervisore di «Due», competente per Giulianova;
  agent_b  Collaboratore di «Due»;
  super    platform admin (non entrato in un'agenzia).
A 390 e 1280 px:
  1  owner_b apre la scheda di un immobile di «Uno» a Giulianova: vista condivisa in
     sola lettura (agenzia d'origine, nessun salvataggio diretto), «Proponi modifica»,
     modulo con i SOLI campi consentiti, invio -> «In attesa di approvazione»;
     l'immobile non cambia;
  2  agent_b: stessa scheda, nessun «Proponi modifica»;
  3  super: Rete › Modifiche territoriali -> Dettaglio (Prima / Attuale / Proposto) ->
     «Approva» apre la conferma e non scrive -> «Sì, approva» -> applicata;
  4  owner_b rivede la scheda con il valore nuovo e la proposta «Approvata».
Nessun errore di pagina, nessuno scorrimento orizzontale.
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
    from fastapi import FastAPI, HTTPException
    from fastapi.staticfiles import StaticFiles
    import platform_admin.database as pdb
    from operator_auth.context import OperatorContext
    from operator_auth.dependencies import (legacy_basic_agency_context, require_authenticated_operator,
                                            require_operator)
    from platform_admin.dependencies import require_platform_admin
    from platform_admin.router import router as piattaforma
    from property.router import router as immobili

    m = rete
    monkeypatch.setattr(pdb, "get_connection", lambda: m["psycopg2"].connect(m["dsn"]))
    sa = _q(m, "SELECT id FROM operator_users WHERE email_normalized = 'c2.super.b@x.test'")
    sa = sa[0][0] if sa else _q(m, "INSERT INTO operator_users (email, email_normalized, password_hash, first_name, "
                                   "is_platform_admin) VALUES ('c2.super.b@x.test', 'c2.super.b@x.test', 'pbkdf2_sha256$1$x$y', "
                                   "'Super', TRUE) RETURNING id")[0][0]
    scad = "2030-01-01T00:00:00Z"
    sessioni = {
        "owner_b": {"user_id": m["ruoli"]["owner_b"][0], "agency_id": 2, "agency_name": "Due", "role": "agency_owner",
                    "is_platform_admin": False, "expires_at": scad},
        "agent_b": {"user_id": m["ruoli"]["agent_b"][0], "agency_id": 2, "agency_name": "Due", "role": "agent",
                    "is_platform_admin": False, "expires_at": scad},
        "super": {"user_id": sa, "agency_id": None, "agency_name": None, "role": None, "is_platform_admin": True,
                  "expires_at": scad, "acting": None, "home_agency_id": None, "home_agency_name": None},
    }
    stato = {"chi": "owner_b"}

    def tenant():
        if stato["chi"] == "super":
            raise HTTPException(status_code=403, detail="serve un'agenzia")
        m["stato"]["chi"] = stato["chi"]
        return m["ctx"]()

    def piattaforma_ctx():
        if stato["chi"] != "super":
            raise HTTPException(status_code=403, detail="riservato alla piattaforma")
        return OperatorContext(user_id=sa, agency_id=None, role=None, is_platform_admin=True,
                               session_id=None, auth_channel="operator_session")

    app = FastAPI()

    @app.get("/api/operator-auth/me")
    def me():
        return sessioni[stato["chi"]]

    @app.get("/api/platform/me")
    def platform_me():
        piattaforma_ctx()
        return {"user_id": sa, "is_platform_admin": True, "agency_id": None, "session_expires_at": scad,
                "acting_agency_id": None, "acting_entered_at": None}

    for r in (immobili, piattaforma):
        app.include_router(r)
    for dipendenza in (legacy_basic_agency_context, require_operator, require_authenticated_operator):
        app.dependency_overrides[dipendenza] = tenant
    app.dependency_overrides[require_platform_admin] = piattaforma_ctx
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
        for t, g in (("property_territorial_change_events", "trg_property_territorial_change_events_append_only"),
                     ("property_territorial_change_requests", "trg_property_territorial_change_requests_guard")):
            _q(m, f"ALTER TABLE {t} DISABLE TRIGGER {g}")
            _q(m, f"DELETE FROM {t}")
            _q(m, f"ALTER TABLE {t} ENABLE TRIGGER {g}")


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_c2_proposta_e_approvazione_in_chromium(server, larghezza):
    from playwright.sync_api import sync_playwright
    m = server
    p = _immobile(m, "owner_a", "Giulianova", rooms=3, energy_class="E")
    pid = p["id"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": larghezza, "height": 900})
        errori = []
        page.on("pageerror", lambda x: errori.append(str(x)))
        misure = []

        # 1 - Supervisore competente: vista condivisa e proposta
        m["come"]["chi"] = "owner_b"
        _vai(page, m["url"], f"#/immobili/{pid}")
        page.wait_for_selector(f"[data-shared-property='{pid}']")
        testo = page.inner_text("[data-shared-property]")
        assert "Condiviso per competenza territoriale" in testo and "Sola lettura" in testo, testo
        assert page.locator("button:has-text('Salva'), #property-edit, [data-trash-open]").count() == 0
        page.click("[data-tc-open]")
        page.wait_for_selector("[data-tc-form]")
        campi = page.eval_on_selector_all("[data-tc-input]", "els => els.map(e => e.dataset.tcInput)")
        assert campi == ["title", "rooms", "bedrooms", "bathrooms", "floor", "total_floors", "elevator", "year_built",
                         "condition", "energy_class", "public_notes"], campi
        page.fill("[data-tc-input='rooms']", "4")
        page.fill("[data-tc-note]", "Verificato sul posto")
        misure.append(_sfora(page))
        page.screenshot(path=f"/tmp/c2_proposta_{larghezza}.png", full_page=True)
        page.click("[data-tc-submit]")
        page.wait_for_selector("[data-tc-request] [data-tc-status='pending']")
        assert "In attesa di approvazione" in page.inner_text("[data-tc-list]")
        assert page.locator("[data-tc-open]").count() == 0                              # una in attesa per volta
        assert _q(m, "SELECT rooms FROM properties WHERE id = %s", (pid,))[0][0] == 3
        misure.append(_sfora(page))

        # 2 - Collaboratore: nessuna proposta
        m["come"]["chi"] = "agent_b"
        _vai(page, m["url"], f"#/immobili/{pid}")
        page.wait_for_selector("[data-tc-list] [data-tc-request]")
        assert page.locator("[data-tc-open]").count() == 0

        # 3 - Super Admin: Rete › Modifiche territoriali
        m["come"]["chi"] = "super"
        _vai(page, m["url"], "#/rete")
        page.wait_for_selector("[data-tab='modifiche']")
        page.click("[data-tab='modifiche']")
        page.wait_for_selector("[data-tc-admin-request]")
        page.click("[data-tc-admin-open]")
        page.wait_for_selector("[data-tc-admin-body] [data-tc-field='rooms']")
        riga = page.inner_text("[data-tc-admin-body] [data-tc-field='rooms']")
        assert "3" in riga and "4" in riga, riga
        page.click("[data-tc-admin-approve]")
        page.wait_for_selector("[data-tc-admin-confirm]:not([hidden])")
        page.screenshot(path=f"/tmp/c2_approva_{larghezza}.png", full_page=True)
        assert _q(m, "SELECT status FROM property_territorial_change_requests WHERE property_id = %s", (pid,))[0][0] == "pending"
        misure.append(_sfora(page))
        page.click("[data-tc-admin-yes]")
        page.wait_for_selector("[data-tc-admin-empty]")
        assert _q(m, "SELECT rooms FROM properties WHERE id = %s", (pid,))[0][0] == 4
        assert _q(m, "SELECT status FROM property_territorial_change_requests WHERE property_id = %s", (pid,))[0][0] == "approved"

        # 4 - il Supervisore vede il risultato
        m["come"]["chi"] = "owner_b"
        _vai(page, m["url"], f"#/immobili/{pid}")
        page.wait_for_selector("[data-tc-request] [data-tc-status='approved']")
        assert "4" in page.inner_text("[data-shared-property] .detail-grid")

        assert errori == [], errori
        for mis in misure:
            assert mis["scroll"] <= mis["inner"] + 1 and mis["fuori"] == [], mis
        browser.close()
