"""CESTINO-GENERALE-1 (CG-1) - prova in BROWSER (Chromium) con backend e PostgreSQL VERI.

La Shell vera servita da uvicorn con i router veri (Immobili, CORE, Platform e
il nuovo `trash/router.py`, montato SOLO su questa app di prova: `main.py` non
e' toccato). Sostituita solo l'autenticazione: titolare dell'agenzia 1 oppure
Super Admin designato (platform admin senza agenzia).

A 390 px e 1280 px:
  g01  titolare: menu «Cestino» -> scheda «Tutti» (elenco unificato), contatori,
       ricerca, pastiglia di categoria, «Collegamenti»; nessuna scrittura;
  g02  Super Admin senza agenzia: voce «Cestino» nel menu, vista globale con
       selettore Agenzia; «Richieste edifici» apre il pannello esistente;
  g03  server senza `/api/trash` (router non montato): «Tutti» ripiega sulle
       schede di categoria, nessun errore di pagina.
Nessuno scorrimento orizzontale, nessun errore di pagina. Screenshot in
`CG1_SHOTS` (o /tmp/cg1_shots).
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import _q, _unita  # noqa: F401
from tests.test_edifici_1_browser_postgres import DSN, SHELL, _porta_libera, completo, mondo  # noqa: F401
from tests.test_eliminazione_edificio_1_browser_postgres import _sfora

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


def _avvia(m, monkeypatch, *, con_cestino_unico=True):
    uvicorn = pytest.importorskip("uvicorn")
    pytest.importorskip("playwright.sync_api")
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.staticfiles import StaticFiles
    import platform_admin.database as pdb
    from operator_auth.context import OperatorContext
    from operator_auth.dependencies import legacy_basic_agency_context, require_operator
    from platform_admin.dependencies import require_platform_admin
    from platform_admin.router import router as piattaforma
    from platform_admin.super_admin import SUPER_ADMIN_ENV
    from property.router import router as immobili
    from core.router import router as core_router
    from trash.router import router as cestino

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
        "super": {"user_id": superadmin, "agency_id": None, "agency_name": None, "role": None,
                  "is_platform_admin": True, "expires_at": "2030-01-01T00:00:00Z", "acting": None,
                  "home_agency_id": None, "home_agency_name": None},
    }
    stato = {"chi": "titolare"}
    scritture = []

    def tenant():
        if stato["chi"] == "super":
            return OperatorContext(user_id=superadmin, agency_id=None, role=None, is_platform_admin=True,
                                   session_id=None, auth_channel="operator_session")
        m["stato"]["chi"] = "owner_a"
        return m["ctx"]()

    def piattaforma_ctx():
        if stato["chi"] != "super":
            raise HTTPException(status_code=403, detail="riservato alla piattaforma")
        return OperatorContext(user_id=superadmin, agency_id=None, role=None, is_platform_admin=True,
                               session_id=None, auth_channel="operator_session")

    app = FastAPI()

    @app.middleware("http")
    async def registra(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            scritture.append((request.method, request.url.path))
        return await call_next(request)

    @app.get("/api/operator-auth/me")
    def me():
        return sessioni[stato["chi"]]

    @app.get("/api/platform/me")
    def platform_me():
        piattaforma_ctx()
        return {"user_id": superadmin, "is_platform_admin": True, "agency_id": None,
                "session_expires_at": "2030-01-01T00:00:00Z", "acting_agency_id": None, "acting_entered_at": None}

    for r in (immobili, core_router, piattaforma, *((cestino,) if con_cestino_unico else ())):
        app.include_router(r)
    app.dependency_overrides[legacy_basic_agency_context] = tenant
    app.dependency_overrides[require_operator] = tenant
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
    return srv, filo, {"url": f"http://127.0.0.1:{porta}", "come": stato, "scritture": scritture}


@pytest.fixture
def dati(mondo):
    """Un immobile e un contatto dell'agenzia 1 nel Cestino (percorsi VERI di ciclo di vita); ripristinati alla fine."""
    from core import contact_lifecycle
    from operator_auth.context import OperatorContext
    from property import lifecycle
    m = mondo
    uid, agenzia, ruolo = m["ruoli"]["owner_a"]
    o = OperatorContext(user_id=uid, agency_id=agenzia, role=ruolo, is_platform_admin=False, session_id=None,
                        auth_channel="operator_session")
    marca = uuid.uuid4().hex[:6]
    contatto = _q(m, "INSERT INTO contacts (agency_id, display_name, first_name, last_name, email) "
                     "VALUES (1, %s, 'Gino', %s, %s) RETURNING id",
                  (f"Gino Cg{marca}", f"Cg{marca}", f"gino.{marca}@x.test"))[0][0]
    contact_lifecycle.trash_contact(o, contatto, "duplicate")
    immobile = _unita(m, floor="1", internal_number=f"G{marca}")["id"]
    lifecycle.trash_property(o, immobile, "test_record")
    try:
        yield {**m, "contatto": contatto, "immobile": immobile, "marca": marca}
    finally:
        lifecycle.restore_property(o, immobile)
        contact_lifecycle.restore_contact(o, contatto)


def _pagina(p, larghezza):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": larghezza, "height": 860})
    errori = []
    page.on("pageerror", lambda x: errori.append(str(x)))
    return browser, page, errori


def _vai(page, url, hash_):
    page.goto(f"{url}/os/?n={uuid.uuid4().hex[:8]}{hash_}")


def _cartella():
    c = Path(os.environ.get("CG1_SHOTS") or (Path("/tmp") / "cg1_shots"))
    c.mkdir(parents=True, exist_ok=True)
    return c


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_g01_g02_cestino_unico_in_chromium(dati, monkeypatch, larghezza):
    from playwright.sync_api import sync_playwright
    m = dati
    srv, filo, s = _avvia(m, monkeypatch)
    cartella = _cartella()
    misure = []
    try:
        with sync_playwright() as p:
            browser, page, errori = _pagina(p, larghezza)
            # --- g01: titolare ------------------------------------------------------
            s["come"]["chi"] = "titolare"
            _vai(page, s["url"], "#/cestino")
            page.wait_for_selector("[data-unified-item]")
            page.wait_for_load_state("networkidle")
            assert page.locator('#nav [data-route="cestino"]').count() >= 1
            carte = page.eval_on_selector_all("[data-unified-item]", "els => els.map((e) => e.dataset.unifiedItem)")
            assert f"contact:{m['contatto']}" in carte and f"property:{m['immobile']}" in carte, carte
            assert "Tutti" in page.inner_text("[data-unified-counts]")
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"cg1_tutti_{larghezza}.png"), full_page=True)
            page.fill("[data-unified-filters] [name='q']", f"cg{m['marca']}")
            page.press("[data-unified-filters] [name='q']", "Enter")
            page.wait_for_function("() => document.querySelectorAll('[data-unified-item]').length === 1")
            assert page.eval_on_selector_all("[data-unified-item]", "els => els.map((e) => e.dataset.unifiedItem)") \
                == [f"contact:{m['contatto']}"]
            page.click(f"[data-trash-relations='contact:{m['contatto']}']")
            page.wait_for_selector(f"[data-unified-item='contact:{m['contatto']}'] [data-trash-relations-host] *")
            page.click("[data-trash-count='property']")
            page.wait_for_function("() => [...document.querySelectorAll('[data-unified-item]')]"
                                   ".every((e) => e.dataset.unifiedItem.startsWith('property:'))")
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"cg1_filtri_{larghezza}.png"), full_page=True)

            # --- g02: Super Admin, vista globale ------------------------------------
            s["come"]["chi"] = "super"
            _vai(page, s["url"], "#/cestino")
            page.wait_for_selector("[data-unified-agency]")
            page.wait_for_selector("[data-unified-item]")
            page.wait_for_load_state("networkidle")
            assert page.locator('#nav [data-route="cestino"]').count() >= 1
            schede = page.eval_on_selector_all("[data-trash-tab]", "els => els.map((e) => e.dataset.trashTab)")
            assert schede == ["tutti", "richieste-edifici"], schede
            assert "Agenzia Uno" in page.inner_text("[data-unified-list]") or \
                page.locator("[data-trash-agency]").count() >= 1
            page.select_option("[data-unified-agency]", "1")
            page.wait_for_load_state("networkidle")
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"cg1_globale_{larghezza}.png"), full_page=True)
            page.click("[data-trash-tab='richieste-edifici']")
            page.wait_for_selector("[data-deletion-admin]")
            page.wait_for_selector("[data-deletion-requests] [data-deletion-empty], [data-deletion-requests] [data-deletion-request]")
            page.wait_for_load_state("networkidle")
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"cg1_richieste_edifici_{larghezza}.png"), full_page=True)
            assert errori == [], errori
            browser.close()
    finally:
        srv.should_exit = True
        filo.join(timeout=10)
    assert s["scritture"] == [], s["scritture"]
    for mis in misure:
        assert mis["scroll"] <= mis["inner"] and mis["fuori"] == [], mis


def test_g03_senza_router_ripiega_sulle_schede(dati, monkeypatch):
    from playwright.sync_api import sync_playwright
    srv, filo, s = _avvia(dati, monkeypatch, con_cestino_unico=False)
    try:
        with sync_playwright() as p:
            browser, page, errori = _pagina(p, 390)
            _vai(page, s["url"], "#/cestino")
            # come prima di CG-1: si apre la scheda Immobili; l'avviso resta nella scheda «Tutti»
            page.wait_for_selector("[data-unified-missing]", state="attached")
            page.wait_for_selector("[data-trash-panel='immobili']:not([hidden])")
            assert errori == [], errori
            browser.close()
    finally:
        srv.should_exit = True
        filo.join(timeout=10)
    assert s["scritture"] == [], s["scritture"]
