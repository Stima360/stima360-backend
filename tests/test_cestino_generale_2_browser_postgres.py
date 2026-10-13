"""CESTINO-GENERALE-2 (CG-2) - «Elimina…» dagli elenchi, in BROWSER (Chromium)
con backend e PostgreSQL VERI.

La Shell vera servita da uvicorn con i router veri (Immobili, CORE,
Acquirenti, Platform); sostituita solo l'autenticazione: titolare
dell'agenzia 1, agente dell'agenzia 1, Super Admin designato.

A 390 px e 1280 px:
  e01  Immobili (titolare): Elimina dalla riga -> foglio della scheda ->
       motivo -> Cestino; la riga sparisce; Ripristina dal Cestino -> torna;
  e02  Immobili bloccato (vendita sospesa): blocco con l'aiuto e il
       collegamento, conferma disabilitata, nulla cambia;
  e03  Immobili (agente): il comando c'e' solo sulla riga assegnata a lui;
  e04  Contatti: Elimina dalla riga -> Cestino -> Ripristina -> torna;
  e05  Richieste acquirente: idem;
  e06  Edifici: Elimina dalla card -> RICHIESTA (l'edificio resta dov'e') ->
       il Super Admin approva -> edificio e unita' nel Cestino, la card sparisce.
Nessuno scorrimento orizzontale della pagina, nessun errore di pagina.
Screenshot in `CG2_SHOTS` (o /tmp/cg2_shots).
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import _edificio, _q, _unita  # noqa: F401
from tests.test_edifici_1_browser_postgres import DSN, SHELL, _porta_libera, completo, mondo  # noqa: F401
from tests.test_eliminazione_edificio_1_browser_postgres import _sfora

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


@pytest.fixture
def server(mondo, monkeypatch):
    uvicorn = pytest.importorskip("uvicorn")
    pytest.importorskip("playwright.sync_api")
    from fastapi import FastAPI, HTTPException
    from fastapi.staticfiles import StaticFiles
    import platform_admin.database as pdb
    from buy.router import router as acquirenti
    from core.router import router as core_router
    from operator_auth.context import OperatorContext
    from operator_auth.dependencies import (legacy_basic_agency_context, require_authenticated_operator,
                                            require_operator)
    from platform_admin.dependencies import require_platform_admin
    from platform_admin.router import router as piattaforma
    from platform_admin.super_admin import SUPER_ADMIN_ENV
    from property.router import router as immobili

    m = mondo
    monkeypatch.setattr(pdb, "get_connection", lambda: m["psycopg2"].connect(m["dsn"]))
    with m["conn"].cursor() as cur:
        cur.execute("SELECT id FROM operator_users WHERE email = 'super.browser@x.test'")
        riga = cur.fetchone()
        if riga is None:
            cur.execute("INSERT INTO operator_users (email, email_normalized, password_hash, first_name, "
                        "is_platform_admin) VALUES ('super.browser@x.test', 'super.browser@x.test', "
                        "'pbkdf2_sha256$1$x$y', 'Super', TRUE) RETURNING id")
            riga = cur.fetchone()
    superadmin = riga[0]
    monkeypatch.setenv(SUPER_ADMIN_ENV, str(superadmin))
    sessioni = {
        "titolare": {"user_id": m["ids"]["owner_a"], "agency_id": 1, "agency_name": "Agenzia Uno",
                     "role": "agency_owner", "is_platform_admin": False, "expires_at": "2030-01-01T00:00:00Z"},
        "agente": {"user_id": m["ids"]["agent_a"], "agency_id": 1, "agency_name": "Agenzia Uno", "role": "agent",
                   "is_platform_admin": False, "expires_at": "2030-01-01T00:00:00Z"},
        "super": {"user_id": superadmin, "agency_id": None, "agency_name": None, "role": None,
                  "is_platform_admin": True, "expires_at": "2030-01-01T00:00:00Z", "acting": None,
                  "home_agency_id": None, "home_agency_name": None},
    }
    stato = {"chi": "titolare"}

    def tenant():
        if stato["chi"] == "super":
            raise HTTPException(status_code=403, detail="serve un'agenzia")
        m["stato"]["chi"] = {"titolare": "owner_a", "agente": "agent_a"}[stato["chi"]]
        return m["ctx"]()

    def piattaforma_ctx():
        if stato["chi"] != "super":
            raise HTTPException(status_code=403, detail="riservato alla piattaforma")
        return OperatorContext(user_id=superadmin, agency_id=None, role=None, is_platform_admin=True,
                               session_id=None, auth_channel="operator_session")

    app = FastAPI()

    @app.get("/api/operator-auth/me")
    def me():
        return sessioni[stato["chi"]]

    @app.get("/api/platform/me")
    def platform_me():
        piattaforma_ctx()
        return {"user_id": superadmin, "is_platform_admin": True, "agency_id": None,
                "session_expires_at": "2030-01-01T00:00:00Z", "acting_agency_id": None, "acting_entered_at": None}

    for r in (immobili, core_router, acquirenti, piattaforma):
        app.include_router(r)
    for dipendenza in (legacy_basic_agency_context, require_operator, require_authenticated_operator):
        app.dependency_overrides[dipendenza] = tenant
    app.dependency_overrides[require_platform_admin] = piattaforma_ctx
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
        yield {"url": f"http://127.0.0.1:{porta}", "come": stato, "superadmin": superadmin, **m}
    finally:
        srv.should_exit = True
        filo.join(timeout=10)
        _q(m, "ALTER TABLE properties DISABLE TRIGGER trg_properties_trash_freeze")
        _q(m, "UPDATE properties SET parent_property_id = NULL WHERE id > 25 AND parent_property_id IS NOT NULL")
        _q(m, "ALTER TABLE properties ENABLE TRIGGER trg_properties_trash_freeze")
        _q(m, "UPDATE buildings SET deleted_at = NULL, deleted_by_user_id = NULL, deleted_reason = NULL "
              "WHERE deleted_at IS NOT NULL")


def _vai(page, url, hash_):
    page.goto(f"{url}/os/?n={uuid.uuid4().hex[:8]}{hash_}")


def _nel_cestino(m, tabella, ident):
    return _q(m, f"SELECT deleted_at IS NOT NULL FROM {tabella} WHERE id = %s", (ident,))[0][0]


def _cerca(page, campo, testo, selettore):
    page.fill(campo, testo)
    page.wait_for_selector(selettore)
    page.wait_for_load_state("networkidle")


def _conferma_foglio(page, motivo="test_record"):
    page.wait_for_selector("[data-row-delete-dialog] input[name='trash-reason']")
    page.check(f"[data-row-delete-dialog] input[name='trash-reason'][value='{motivo}']")
    page.click("[data-row-delete-dialog] [data-trash-confirm]")
    page.wait_for_function("() => !document.querySelector('[data-row-delete-dialog][open]')")


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_e01_e06_elimina_dagli_elenchi_in_chromium(server, larghezza):
    from playwright.sync_api import sync_playwright
    s = server
    sfx = uuid.uuid4().hex[:5]
    # dati propri (mai dati reali): due immobili commerciali, uno assegnato all'agente, uno bloccato
    def immobile(via):
        r = s["api"]().post("/api/property/properties", json={"title": f"Trilocale {via}", "property_type": "apartment",
                                                              "city": "Fermo", "address": f"Via {via}", "civic_number": "1"})
        assert r.status_code in (200, 201), r.text
        return r.json()
    libero = immobile(f"Libera{sfx}")
    bloccato = immobile(f"Bloccata{sfx}")
    assegnato = immobile(f"Agente{sfx}")
    _q(s, "UPDATE properties SET assigned_agent_id = %s WHERE id = %s", (s["ids"]["agent_a"], assegnato["id"]))
    venditore = _q(s, "INSERT INTO contacts (agency_id, display_name) VALUES (1, %s) RETURNING id", (f"Vend {sfx}",))[0][0]
    _q(s, "INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s, %s, 'owner')", (bloccato["id"], venditore))
    lead = _q(s, "INSERT INTO leads (agency_id, contact_id, pipeline, status, stage) VALUES (1, %s, 'sell', 'paused', 'new') "
                 "RETURNING id", (venditore,))[0][0]
    _q(s, "INSERT INTO property_leads (property_id, lead_id, relation_type) VALUES (%s, %s, 'seller')", (bloccato["id"], lead))
    contatto = _q(s, "INSERT INTO contacts (agency_id, display_name, first_name, last_name, email) VALUES (1, %s, 'Gino', %s, %s) "
                     "RETURNING id", (f"Gino Elenco{sfx}", f"Elenco{sfx}", f"gino.{sfx}@x.test"))[0][0]
    acquirente = _q(s, "INSERT INTO contacts (agency_id, display_name) VALUES (1, %s) RETURNING id", (f"Bruna Compra{sfx}",))[0][0]
    richiesta = _q(s, "INSERT INTO buy_requests (agency_id, contact_id, title) VALUES (1, %s, %s) RETURNING id",
                   (acquirente, f"Bilocale {sfx}"))[0][0]
    terr = {"region": "Abruzzo", "province": "TE", "city": "Tortoreto", "microzone": "Alto"}
    e = _edificio(s, **terr, address=f"Via Elenco {sfx}", civic_number="5", name=f"Palazzina Elenco {sfx}")
    u1 = _unita(s, building_id=e["id"], floor="1", internal_number="1")
    cartella = Path(os.environ.get("CG2_SHOTS") or (Path("/tmp") / "cg2_shots"))
    cartella.mkdir(parents=True, exist_ok=True)
    misure = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": larghezza, "height": 860})
        errori = []
        page.on("pageerror", lambda x: errori.append(str(x)))

        # --- e01: Immobili, titolare --------------------------------------------
        s["come"]["chi"] = "titolare"
        _vai(page, s["url"], "#/immobili")
        page.wait_for_selector("#immobili-search")
        _cerca(page, "#immobili-search", f"Libera{sfx}", f"[data-row-delete='property:{libero['id']}']")
        misure.append(_sfora(page))
        page.screenshot(path=str(cartella / f"cg2_immobili_{larghezza}.png"), full_page=True)
        page.click(f"[data-row-delete='property:{libero['id']}']")
        assert page.evaluate("location.hash") == "#/immobili"                     # nessuna scheda aperta
        page.wait_for_selector("[data-row-delete-dialog] input[name='trash-reason']")
        misure.append(_sfora(page))
        page.screenshot(path=str(cartella / f"cg2_immobili_foglio_{larghezza}.png"), full_page=True)
        _conferma_foglio(page)
        page.wait_for_function(f"() => !document.querySelector(\"[data-row-delete='property:{libero['id']}']\")")
        assert _nel_cestino(s, "properties", libero["id"]) is True
        _vai(page, s["url"], "#/cestino/immobili")
        page.wait_for_selector(f"[data-trash-restore='{libero['id']}']")
        page.click(f"[data-trash-restore='{libero['id']}']")
        page.wait_for_function(f"() => !document.querySelector(\"[data-trash-restore='{libero['id']}']\")")
        assert _nel_cestino(s, "properties", libero["id"]) is False
        _vai(page, s["url"], "#/immobili")
        page.wait_for_selector("#immobili-search")
        _cerca(page, "#immobili-search", f"Libera{sfx}", f"[data-row-delete='property:{libero['id']}']")

        # --- e02: bloccato da una vendita sospesa -----------------------------------
        _cerca(page, "#immobili-search", f"Bloccata{sfx}", f"[data-row-delete='property:{bloccato['id']}']")
        page.click(f"[data-row-delete='property:{bloccato['id']}']")
        page.wait_for_selector("[data-row-delete-dialog] [data-blocker='SELLER_OPPORTUNITY_OPEN']")
        assert "Venditori › Sospesi" in page.inner_text("[data-row-delete-dialog]")
        assert page.is_disabled("[data-row-delete-dialog] [data-trash-confirm]")
        misure.append(_sfora(page))
        page.screenshot(path=str(cartella / f"cg2_immobili_bloccato_{larghezza}.png"), full_page=True)
        page.click("[data-row-delete-dialog] [data-trash-close]")
        assert _nel_cestino(s, "properties", bloccato["id"]) is False
        assert _q(s, "SELECT status FROM leads WHERE id = %s", (lead,))[0][0] == "paused"

        # --- e03: agente, solo la riga assegnata --------------------------------------
        s["come"]["chi"] = "agente"
        _vai(page, s["url"], "#/immobili")
        page.wait_for_selector("#immobili-search")
        _cerca(page, "#immobili-search", sfx, f"[data-row-delete='property:{assegnato['id']}']")
        comandi = page.eval_on_selector_all("[data-row-delete]", "els => els.map((e) => e.dataset.rowDelete)")
        assert comandi == [f"property:{assegnato['id']}"], comandi
        s["come"]["chi"] = "titolare"

        # --- e04: Contatti ---------------------------------------------------------------
        _vai(page, s["url"], "#/contatti")
        page.wait_for_selector("#contatti-search")
        _cerca(page, "#contatti-search", f"Elenco{sfx}", f"[data-row-delete='contact:{contatto}']")
        page.click(f"[data-row-delete='contact:{contatto}']")
        assert page.evaluate("location.hash") == "#/contatti"
        misure.append(_sfora(page))
        page.screenshot(path=str(cartella / f"cg2_contatti_foglio_{larghezza}.png"), full_page=True)
        _conferma_foglio(page, "invalid_data")
        page.wait_for_function(f"() => !document.querySelector(\"[data-row-delete='contact:{contatto}']\")")
        assert _nel_cestino(s, "contacts", contatto) is True
        _vai(page, s["url"], "#/cestino/contatti")
        page.wait_for_selector(f"[data-contact-restore='{contatto}']")
        page.click(f"[data-contact-restore='{contatto}']")
        page.wait_for_function(f"() => !document.querySelector(\"[data-contact-restore='{contatto}']\")")
        assert _nel_cestino(s, "contacts", contatto) is False

        # --- e05: Richieste acquirente --------------------------------------------------
        _vai(page, s["url"], "#/acquirenti")
        page.wait_for_selector("#acquirenti-search")
        _cerca(page, "#acquirenti-search", f"Bilocale {sfx}", f"[data-row-delete='buy_request:{richiesta}']")
        misure.append(_sfora(page))
        page.screenshot(path=str(cartella / f"cg2_acquirenti_{larghezza}.png"), full_page=True)
        page.click(f"[data-row-delete='buy_request:{richiesta}']")
        _conferma_foglio(page, "duplicate")
        page.wait_for_function(f"() => !document.querySelector(\"[data-row-delete='buy_request:{richiesta}']\")")
        assert _nel_cestino(s, "buy_requests", richiesta) is True
        _vai(page, s["url"], "#/cestino/richieste")
        page.wait_for_selector(f"[data-buy-restore='{richiesta}']")
        page.click(f"[data-buy-restore='{richiesta}']")
        page.wait_for_function(f"() => !document.querySelector(\"[data-buy-restore='{richiesta}']\")")
        assert _nel_cestino(s, "buy_requests", richiesta) is False

        # --- e06: Edifici: richiesta, approvazione del Super Admin, Cestino ----------------
        _vai(page, s["url"], "#/edifici")
        page.wait_for_selector("#buildings-search")
        _cerca(page, "#buildings-search", f"Elenco {sfx}", f"[data-row-delete='building:{e['id']}']")
        misure.append(_sfora(page))
        page.screenshot(path=str(cartella / f"cg2_edifici_{larghezza}.png"), full_page=True)
        page.click(f"[data-row-delete='building:{e['id']}']")
        page.wait_for_selector("[data-row-delete-dialog] [data-deletion-reason]")
        assert page.evaluate("location.hash") == "#/edifici"
        misure.append(_sfora(page))
        page.screenshot(path=str(cartella / f"cg2_edifici_richiesta_{larghezza}.png"), full_page=True)
        page.click("[data-row-delete-dialog] [data-deletion-confirm]")
        page.wait_for_selector("[data-row-delete-dialog] [data-deletion-status='pending']")
        rid = _q(s, "SELECT id FROM building_deletion_requests WHERE building_id = %s AND status = 'pending'", (e["id"],))[0][0]
        assert _nel_cestino(s, "buildings", e["id"]) is False                       # nulla nel Cestino prima del Super Admin
        assert _nel_cestino(s, "properties", u1["id"]) is False
        page.click("[data-row-delete-dialog] [data-trash-close]")
        s["come"]["chi"] = "super"
        _vai(page, s["url"], "#/cestino/richieste-edifici")
        page.wait_for_selector(f"[data-deletion-request='{rid}'] [data-deletion-detail-open]")
        page.click(f"[data-deletion-request='{rid}'] [data-deletion-detail-open]")
        page.wait_for_selector(f"[data-deletion-request='{rid}'] [data-deletion-approve]")
        page.click(f"[data-deletion-request='{rid}'] [data-deletion-approve]")
        page.wait_for_selector(f"[data-building-group='{e['id']}']")
        assert _nel_cestino(s, "buildings", e["id"]) is True and _nel_cestino(s, "properties", u1["id"]) is True
        s["come"]["chi"] = "titolare"
        _vai(page, s["url"], "#/edifici")
        page.wait_for_selector("#buildings-search")
        page.fill("#buildings-search", f"Elenco {sfx}")
        page.wait_for_load_state("networkidle")
        time.sleep(0.6)
        assert page.locator(f"[data-row-delete='building:{e['id']}']").count() == 0
        assert errori == [], errori
        browser.close()
    for mis in misure:
        assert mis["scroll"] <= mis["inner"] and mis["fuori"] == [], mis
