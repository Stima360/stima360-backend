"""ADM-5B - consultazione in SOLA LETTURA delle agenzie sospese o archiviate.

PostgreSQL usa-e-getta, sessioni vere (fixture `adm2`), e i router veri delle
agenzie montati come in `main.py`: core, immobili, abbinamenti, calendario,
documenti del proprietario (area admin) e STIMA Voice.

  01  il Super Admin designato entra in un'agenzia sospesa e in una archiviata:
      SOLA LETTURA in `/me`, ingresso registrato, nessuna riattivazione
  02  (Chromium) la barra della Shell scrive «SOLA LETTURA»
  03  consultazione: contatti, immobili, scheda, documenti del proprietario
  04  scritture rifiutate (403) sulle API dirette e su Voice; nessuna riga cambia
  05  GET senza scritture: il cruscotto abbinamenti non ricalcola; il callback
      di Google Calendar e' rifiutato
  06  uscita sicura; il designato e' ricontrollato a ogni richiesta
  07  nessuna regressione: agenzia attiva con pieni poteri, operatore ordinario
      invariato, un altro amministratore di piattaforma non entra
"""
from __future__ import annotations

import threading
import time
import uuid

import pytest

from tests.test_adm1_super_admin_postgres import PASSWORD, _impronta, adm1  # noqa: F401
from tests.test_adm2_impact_postgres import adm2  # noqa: F401
from tests.test_c1_lettura_territoriale_postgres import rete  # noqa: F401
from tests.test_censimento_3_backend_postgres import DSN, _q, completo, mondo  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

ME = "/api/operator-auth/me"
EXIT = "/api/platform/agency-context/exit"
READ_ONLY = "sola lettura"


@pytest.fixture
def b5(adm2, monkeypatch):
    """L'app con i router delle agenzie, montati con le stesse dipendenze di
    `main.py`, sullo stesso database usa-e-getta."""
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    import database as radice
    from calendar_sync.router import router as calendar_router
    from core.router import router as core_router
    from match.router import router as match_router
    from operator_auth.dependencies import require_authenticated_operator, require_operator
    from operator_auth.router import router as auth_router
    from owner.router_admin import router as owner_admin_router
    from platform_admin.dependencies import require_platform_admin
    from platform_admin.router import router as platform_router
    from property.router import router as property_router
    from voice.router import router as voice_router

    m = adm2
    monkeypatch.setattr(radice, "get_connection", lambda: m["psycopg2"].connect(m["dsn"]))
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(platform_router, dependencies=[Depends(require_platform_admin)])
    app.include_router(core_router, dependencies=[Depends(require_operator)])
    for r in (property_router, match_router, calendar_router, voice_router):
        app.include_router(r, dependencies=[Depends(require_authenticated_operator)])
    app.include_router(owner_admin_router)

    def accedi(chi):
        client = TestClient(app, base_url="https://testserver")
        email = _q(m, "SELECT email FROM operator_users WHERE id = %s", (m["persone"][chi],))[0][0]
        r = client.post("/api/operator-auth/login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 204, (chi, r.status_code, r.text)
        return client

    q = m["quattro"]
    m["proprieta"] = _q(m, "SELECT id FROM properties WHERE agency_id = %s AND title = 'ADM2 casa propria'",
                        (q,))[0][0]
    yield {**m, "b5_app": app, "b5_accedi": accedi}
    _q(m, "UPDATE operator_sessions SET acting_agency_id = NULL, acting_entered_at = NULL")
    _q(m, "UPDATE agencies SET status = 'active' WHERE id = %s", (q,))


def _stato(sa, q, verso):
    r = sa.patch(f"/api/platform/agencies/{q}", json={"status": verso})
    assert r.status_code == 200, r.text


def _entra(sa, q):
    r = sa.post(f"/api/platform/agencies/{q}/enter")
    assert r.status_code == 200, r.text
    return r.json()


def _sospesa_e_dentro(m, stato="suspended"):
    sa, q = m["b5_accedi"]("super"), m["quattro"]
    _stato(sa, q, "suspended")
    if stato == "archived":
        _stato(sa, q, "archived")
    assert _entra(sa, q)["read_only"] is True
    return sa


def _righe_protette(m):
    q = m["quattro"]
    righe = {t: _q(m, f"SELECT * FROM {t} WHERE agency_id = %s ORDER BY id", (q,))
             for t in ("contacts", "properties", "stime", "leads")}
    righe["property_documents"] = _q(m, "SELECT * FROM property_documents WHERE property_id IN "
                                        "(SELECT id FROM properties WHERE agency_id = %s) ORDER BY id", (q,))
    righe["matches"] = _q(m, "SELECT * FROM matches ORDER BY id")
    return righe


# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stato", ["suspended", "archived"])
def test_01_ingresso_del_designato_in_sola_lettura(b5, stato):
    m = b5
    q = m["quattro"]
    sa = _sospesa_e_dentro(m, stato)
    acting = sa.get(ME).json()["acting"]
    assert acting["agency_id"] == q and acting["read_only"] is True
    assert _q(m, "SELECT status FROM agencies WHERE id = %s", (q,))[0][0] == stato
    meta = _q(m, "SELECT metadata FROM platform_audit_log WHERE action = 'platform.acting.enter' "
                 "AND target_agency_id = %s ORDER BY id DESC LIMIT 1", (q,))[0][0]
    assert meta["read_only"] is True
    assert sa.post(EXIT).status_code == 204


def test_02_la_barra_della_shell_dice_sola_lettura(b5):
    uvicorn = pytest.importorskip("uvicorn")
    pytest.importorskip("playwright.sync_api")
    from fastapi.staticfiles import StaticFiles
    from playwright.sync_api import sync_playwright
    from operator_auth.enums import COOKIE_NAME
    from tests.test_edifici_1_browser_postgres import SHELL, _porta_libera

    m = b5
    sa = _sospesa_e_dentro(m)
    app = m["b5_app"]
    app.mount("/os", StaticFiles(directory=str(SHELL), html=True), name="os-shell")
    porta = _porta_libera()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="warning"))
    filo = threading.Thread(target=srv.run, daemon=True)
    filo.start()
    limite = time.monotonic() + 20
    while not srv.started and time.monotonic() < limite:
        time.sleep(0.05)
    assert srv.started
    url = f"http://127.0.0.1:{porta}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": 390, "height": 860})
            ctx.add_cookies([{"name": COOKIE_NAME, "value": sa.cookies.get(COOKIE_NAME), "url": url}])
            page = ctx.new_page()
            errori = []
            page.on("pageerror", lambda e: errori.append(str(e)))
            page.goto(f"{url}/os/?n={uuid.uuid4().hex[:8]}")
            page.wait_for_selector("#acting-bar:not([hidden])")
            testo = page.inner_text("#acting-text")
            assert "SOLA LETTURA" in testo and "Quattro" in testo, testo
            assert not errori, errori
            browser.close()
    finally:
        srv.should_exit = True
        filo.join(timeout=10)
    assert sa.post(EXIT).status_code == 204


def test_03_consultazione_di_contatti_immobili_e_documenti(b5):
    m = b5
    sa = _sospesa_e_dentro(m)
    contatti = sa.get("/api/core/contacts")
    assert contatti.status_code == 200, contatti.text
    assert any("ADM2 contatto" in (c.get("display_name") or "") for c in contatti.json()["items"])
    immobili = sa.get("/api/property/properties")
    assert immobili.status_code == 200, immobili.text
    assert any(p["title"] == "ADM2 casa propria" for p in immobili.json()["items"])
    scheda = sa.get(f"/api/property/properties/{m['proprieta']}")
    assert scheda.status_code == 200, scheda.text
    documenti = sa.get("/api/owner/admin/documents")
    assert documenti.status_code == 200, documenti.text
    assert sa.post(EXIT).status_code == 204


def test_04_scritture_rifiutate_anche_dirette_e_voice(b5):
    m = b5
    sa = _sospesa_e_dentro(m)
    prima, impronta = _righe_protette(m), _impronta(m)
    p = m["proprieta"]
    tentativi = [
        sa.post("/api/core/contacts", json={"display_name": "ADM5B non deve nascere"}),
        sa.patch(f"/api/property/properties/{p}", json={"title": "ADM5B rinominata"}),
        sa.put("/api/property/buildings/1/composition", json={}),
        sa.post(f"/api/property/properties/{p}/documents", json={"title": "ADM5B"}),
        sa.delete(f"/api/property/properties/{p}"),
        sa.post("/api/match/detect-stale"),
        sa.post("/api/voice/commands", json={"text": "crea un contatto Mario"}),
    ]
    for r in tentativi:
        assert r.status_code == 403 and READ_ONLY in r.json()["detail"], (r.request.method, r.request.url, r.text)
    assert _righe_protette(m) == prima
    assert _impronta(m) == impronta
    assert sa.post(EXIT).status_code == 204


def test_05_i_get_non_scrivono(b5, monkeypatch):
    from match import repository as match_repository
    m = b5
    sa = _sospesa_e_dentro(m)
    chiamate = []
    originale = match_repository.detect_stale_scoped
    monkeypatch.setattr(match_repository, "detect_stale_scoped",
                        lambda *a, **k: chiamate.append(1) or originale(*a, **k))
    prima = _righe_protette(m)
    for url in ("/api/core/contacts", "/api/property/properties", f"/api/property/properties/{m['proprieta']}",
                "/api/match/dashboard", "/api/match/dashboard/stale", "/api/owner/admin/documents",
                "/api/voice/status"):
        assert sa.get(url).status_code in (200, 404, 503), url
    assert chiamate == [], "il cruscotto abbinamenti ha ricalcolato in sola lettura"
    callback = sa.get("/api/calendar/google/callback", params={"code": "x", "state": "y"})
    assert callback.status_code == 403 and READ_ONLY in callback.json()["detail"]
    assert _righe_protette(m) == prima
    assert sa.post(EXIT).status_code == 204
    # fuori dalla consultazione, su un'agenzia attiva, il ricalcolo torna
    _stato(sa, m["quattro"], "active")
    _entra(sa, m["quattro"])
    assert sa.get("/api/match/dashboard").status_code == 200
    assert chiamate, "su un'agenzia attiva il cruscotto deve ricalcolare come prima"
    assert sa.post(EXIT).status_code == 204


def test_06_uscita_sicura_e_designato_ricontrollato(b5, monkeypatch):
    from operator_auth.designation import SUPER_ADMIN_ENV
    m = b5
    q = m["quattro"]
    sa = _sospesa_e_dentro(m)
    assert sa.post(EXIT).status_code == 204
    assert sa.get(ME).json()["acting"] is None
    assert sa.get("/api/core/contacts").status_code == 403  # nessuna agenzia: nessuno scope
    assert _q(m, "SELECT count(*) FROM platform_audit_log WHERE action = 'platform.acting.exit' "
                 "AND target_agency_id = %s", (q,))[0][0] >= 1
    assert _q(m, "SELECT status FROM agencies WHERE id = %s", (q,))[0][0] == "suspended"
    # di nuovo dentro; poi il designato cambia: alla richiesta dopo l'acting sparisce
    assert _entra(sa, q)["read_only"] is True
    monkeypatch.setenv(SUPER_ADMIN_ENV, "999999")
    assert sa.get(ME).json()["acting"] is None
    assert _q(m, "SELECT count(*) FROM operator_sessions WHERE acting_agency_id = %s", (q,))[0][0] == 0


def test_07_nessuna_regressione(b5):
    m = b5
    q = m["quattro"]
    sa, altro = m["b5_accedi"]("super"), m["b5_accedi"]("altro")
    # agenzia attiva: pieni poteri, come prima
    assert _entra(sa, q)["read_only"] is False
    assert sa.get(ME).json()["acting"]["read_only"] is False
    nuovo = sa.post("/api/core/contacts", json={"display_name": "ADM5B attiva"})
    assert nuovo.status_code == 201, nuovo.text
    assert sa.post(EXIT).status_code == 204
    # operatore ordinario dell'agenzia attiva: scrive come prima
    titolare = m["b5_accedi"]("quattro")
    r = titolare.post("/api/core/contacts", json={"display_name": "ADM5B titolare"})
    assert r.status_code == 201, r.text
    # agenzia sospesa: il titolare non entra (ADM-1), un altro amministratore
    # di piattaforma non consulta
    _stato(sa, q, "suspended")
    assert titolare.get("/api/core/contacts").status_code == 401
    r = altro.post(f"/api/platform/agencies/{q}/enter")
    assert r.status_code == 403, r.text
    assert _q(m, "SELECT count(*) FROM operator_sessions WHERE acting_agency_id = %s", (q,))[0][0] == 0
