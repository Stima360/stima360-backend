"""ELIMINAZIONE-EDIFICIO-1 - prova in BROWSER (Chromium) con backend e PostgreSQL VERI.

La Shell vera servita da uvicorn con i router veri (Immobili e Platform) sul
database usa-e-getta (migration 101 compresa). Sostituita SOLO
l'autenticazione: la sessione e' quella di un agente dell'agenzia 1 (utente
ordinario) oppure del Super Admin (platform admin senza agenzia); il
`require_platform_admin` di prova ammette solo il secondo (403 al primo),
come quello vero. `platform_audit_log` e' scritto davvero.

A 390 px e 1280 px:
  b01  agente: scheda edificio -> «Richiedi eliminazione…», unita' coinvolte,
       motivo, conferma -> «In attesa»; nulla cambia nel database;
  b02  Super Admin: Rete › Eliminazioni edifici -> dettaglio -> «Approva»:
       edificio e unita' nel Cestino insieme;
  b03  agente: Cestino › Edifici (edificio e unita' raggruppati, nessun
       ripristino) e scheda nel Cestino («riservato al Super Admin»);
  b04  Super Admin: «Ripristina» -> edificio e unita' spostate tornano, quella
       gia' nel Cestino resta;
  b05  agente richiede di nuovo, Super Admin approva, poi «Elimina
       definitivamente…»: riepilogo, conferma disabilitata finche' il testo
       non e' esatto, poi le righe non ci sono piu' e il contatto resta.
Nessuno scorrimento orizzontale, nessun errore di pagina. Screenshot in
`EDIFICI1_SHOTS` (o /tmp/edifici1_shots).
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

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


def _sfora(page):
    return page.evaluate("""() => {
      const inScorrimento = (e) => { for (let n = e.parentElement; n; n = n.parentElement) {
        const o = getComputedStyle(n).overflowX; if (o === 'auto' || o === 'scroll') return true; } return false; };
      const fuori = [...document.querySelectorAll('#content *')]
        .filter((e) => e.getClientRects().length && e.getBoundingClientRect().right > window.innerWidth + 0.5)
        .filter((e) => !inScorrimento(e))
        .map((e) => e.tagName + '.' + e.className);
      return { scroll: document.documentElement.scrollWidth, inner: window.innerWidth, fuori: fuori.slice(0, 5) };
    }""")


@pytest.fixture
def server(mondo, monkeypatch):
    uvicorn = pytest.importorskip("uvicorn")
    pytest.importorskip("playwright.sync_api")
    from fastapi import FastAPI, HTTPException
    from fastapi.staticfiles import StaticFiles
    import platform_admin.database as pdb
    from operator_auth.context import OperatorContext
    from operator_auth.dependencies import legacy_basic_agency_context, require_operator
    from platform_admin.dependencies import require_platform_admin
    from platform_admin.router import router as piattaforma
    from property.router import router as immobili
    from core.router import router as core_router

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
    # ELIMINAZIONE-EDIFICIO-2: il Super Admin generale e' l'account DESIGNATO nella configurazione del servizio
    from property.building_deletion import SUPER_ADMIN_ENV
    monkeypatch.setenv(SUPER_ADMIN_ENV, str(superadmin))
    sessioni = {
        "agente": {"user_id": m["ids"]["agent_a"], "agency_id": 1, "agency_name": "Agenzia Uno", "role": "agent",
                   "is_platform_admin": False, "expires_at": "2030-01-01T00:00:00Z"},
        "super": {"user_id": superadmin, "agency_id": None, "agency_name": None, "role": None,
                  "is_platform_admin": True, "expires_at": "2030-01-01T00:00:00Z", "acting": None,
                  "home_agency_id": None, "home_agency_name": None},
    }
    stato = {"chi": "agente"}

    def tenant():
        m["stato"]["chi"] = "agent_a"
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

    for r in (immobili, core_router, piattaforma):
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
    _q(m, "ALTER TABLE properties DISABLE TRIGGER trg_properties_trash_freeze")
    _q(m, "UPDATE properties SET parent_property_id = NULL WHERE id > 25 AND parent_property_id IS NOT NULL")
    _q(m, "ALTER TABLE properties ENABLE TRIGGER trg_properties_trash_freeze")
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
    """Navigazione vera (nuovo documento): la sessione si rilegge da /me, come dopo un accesso."""
    page.goto(f"{url}/os/?n={uuid.uuid4().hex[:8]}{hash_}")


def _apri_rete(page, url):
    # SENTINELLA AGGIORNATA DA CESTINO-GENERALE-1: «Rete › Eliminazioni edifici» e' ora un
    # collegamento a «Cestino › Richieste edifici», dove vive lo STESSO pannello del Super Admin
    # (renderEliminazioni, stesse rotte). Il percorso passa dal collegamento della Rete.
    _vai(page, url, "#/rete")
    page.wait_for_selector("[data-tab='eliminazioni']")
    page.click("[data-tab='eliminazioni']")
    page.wait_for_selector("[data-deletion-moved-link]")
    page.click("[data-deletion-moved-link]")
    page.wait_for_selector("[data-deletion-admin]")
    page.wait_for_load_state("networkidle")


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_b01_b05_eliminazione_edificio_in_chromium(server, larghezza):
    from playwright.sync_api import sync_playwright
    m = server
    terr = {"region": "Abruzzo", "province": "TE", "city": "Tortoreto", "microzone": "Alto"}
    e = _edificio(m, **terr, address=f"Via Elimina {uuid.uuid4().hex[:5]}", civic_number="3", name="Palazzina da eliminare")
    u1 = _unita(m, building_id=e["id"], floor="1", internal_number="1")
    u2 = _unita(m, building_id=e["id"], floor="2", internal_number="2")
    gia = _unita(m, building_id=e["id"], floor="3", internal_number="3")
    assert m["api"]().post(f"/api/property/properties/{gia['id']}/trash", json={"reason_code": "duplicate"}).status_code == 200
    _q(m, "INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s, %s, 'owner')", (u1["id"], m["mario"]))
    cartella = Path(os.environ.get("EDIFICI1_SHOTS") or (Path("/tmp") / "edifici1_shots"))
    cartella.mkdir(parents=True, exist_ok=True)
    misure = []
    unita = [u1["id"], u2["id"], gia["id"]]

    def nel_cestino():
        return dict(_q(m, "SELECT id, deleted_at IS NOT NULL FROM properties WHERE id = ANY(%s)", (unita,)))

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": larghezza, "height": 860})
            errori = []
            page.on("pageerror", lambda x: errori.append(str(x)))

            # --- b01: l'agente richiede l'eliminazione ---------------------------------
            m["come"]["chi"] = "agente"
            _vai(page, m["url"], f"#/edifici/{e['id']}")
            page.wait_for_selector("[data-deletion-open]")
            assert page.locator("#building-trash-btn").count() == 0
            testo = page.inner_text("[data-deletion-panel]")
            assert "Coinvolge 3 unità: 2 attive, 1 già nel Cestino." in testo, testo
            page.click("[data-deletion-open]")
            page.select_option("[data-deletion-reason]", "duplicate")
            page.fill("[data-deletion-note]", "Creata due volte")
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"eliminazione_richiesta_{larghezza}.png"), full_page=True)
            page.click("[data-deletion-confirm]")
            page.wait_for_selector("[data-deletion-status='pending']")
            assert "In attesa di approvazione" in page.inner_text("[data-deletion-panel]")
            assert page.locator("[data-deletion-withdraw]").count() == 1
            rid = _q(m, "SELECT id FROM building_deletion_requests WHERE building_id = %s AND status = 'pending'",
                     (e["id"],))[0][0]
            assert nel_cestino() == {u1["id"]: False, u2["id"]: False, gia["id"]: True}
            assert _q(m, "SELECT deleted_at FROM buildings WHERE id = %s", (e["id"],))[0][0] is None

            # --- b02: il Super Admin approva ---------------------------------------------
            m["come"]["chi"] = "super"
            _apri_rete(page, m["url"])
            page.wait_for_selector(f"[data-deletion-request='{rid}']")
            page.click(f"[data-deletion-request='{rid}'] [data-deletion-detail-open]")
            page.wait_for_selector(f"[data-deletion-request='{rid}'] [data-deletion-approve]")
            dettaglio = page.inner_text(f"[data-deletion-request='{rid}']")
            assert "Coinvolge 3 unità" in dettaglio and "Creata due volte" in dettaglio, dettaglio
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"eliminazione_approva_{larghezza}.png"), full_page=True)
            # SENTINELLA AGGIORNATA DA APPROVAZIONE-EDIFICI-1: «Approva» apre la conferma, poi «Sì, approva»
            page.click(f"[data-deletion-request='{rid}'] [data-deletion-approve]")
            page.wait_for_selector(f"[data-deletion-request='{rid}'] [data-deletion-approve-confirm]:not([hidden])")
            page.click(f"[data-deletion-request='{rid}'] [data-deletion-approve-yes]")
            page.wait_for_selector(f"[data-building-group='{e['id']}']")
            assert _q(m, "SELECT status FROM building_deletion_requests WHERE id = %s", (rid,))[0][0] == "approved"
            assert nel_cestino() == {u1["id"]: True, u2["id"]: True, gia["id"]: True}
            assert _q(m, "SELECT deleted_at IS NOT NULL FROM buildings WHERE id = %s", (e["id"],))[0][0] is True

            # --- b03: l'agente vede il Cestino raggruppato, senza ripristino ---------------
            m["come"]["chi"] = "agente"
            _vai(page, m["url"], "#/cestino/edifici")
            page.wait_for_selector(f"[data-building-group='{e['id']}']")
            carta = page.inner_text(f"[data-building-group='{e['id']}']")
            assert "3 unità nel Cestino con l’edificio (2 spostate dall’approvazione, 1 già nel Cestino prima)" in carta, carta
            assert "solo il Super Admin" in carta
            assert page.locator("[data-group-restore], [data-group-purge], [data-building-restore]").count() == 0
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"eliminazione_cestino_{larghezza}.png"), full_page=True)
            page.click(f"[data-building-group='{e['id']}'] [data-building-trash-open]")
            page.wait_for_selector("[data-building-in-trash]")
            assert "riservato al Super Admin" in page.inner_text("[data-building-in-trash]")
            assert page.locator("[data-building-restore-btn]").count() == 0
            page.wait_for_selector("[data-deletion-status='approved']")

            # --- b04: il Super Admin ripristina -------------------------------------------
            m["come"]["chi"] = "super"
            _apri_rete(page, m["url"])
            page.wait_for_selector(f"[data-building-group='{e['id']}'] [data-group-restore]")
            page.click(f"[data-building-group='{e['id']}'] [data-group-restore]")
            page.wait_for_function(f"() => !document.querySelector(\"[data-building-group='{e['id']}']\")")
            assert nel_cestino() == {u1["id"]: False, u2["id"]: False, gia["id"]: True}
            assert _q(m, "SELECT deleted_at FROM buildings WHERE id = %s", (e["id"],))[0][0] is None

            # --- b05: nuova richiesta, approvazione, cancellazione definitiva -------------------
            m["come"]["chi"] = "agente"
            _vai(page, m["url"], f"#/edifici/{e['id']}")
            page.wait_for_selector("[data-deletion-open]")
            page.click("[data-deletion-open]")
            page.click("[data-deletion-confirm]")
            page.wait_for_selector("[data-deletion-status='pending']")
            rid2 = _q(m, "SELECT id FROM building_deletion_requests WHERE building_id = %s AND status = 'pending'",
                      (e["id"],))[0][0]
            m["come"]["chi"] = "super"
            _apri_rete(page, m["url"])
            page.click(f"[data-deletion-request='{rid2}'] [data-deletion-detail-open]")
            page.wait_for_selector(f"[data-deletion-request='{rid2}'] [data-deletion-approve]")
            page.click(f"[data-deletion-request='{rid2}'] [data-deletion-approve]")  # SENTINELLA AGGIORNATA DA APPROVAZIONE-EDIFICI-1
            page.click(f"[data-deletion-request='{rid2}'] [data-deletion-approve-yes]")
            page.wait_for_selector(f"[data-building-group='{e['id']}'] [data-group-purge]")
            page.click(f"[data-building-group='{e['id']}'] [data-group-purge]")
            page.wait_for_selector("[data-purge-ready]")
            riepilogo = page.inner_text("[data-purge-ready]")
            assert "non si può annullare" in riepilogo and "i contatti restano" in riepilogo, riepilogo
            assert page.is_disabled("[data-purge-confirm]")
            page.fill("[data-purge-text]", "elimina")
            assert page.is_disabled("[data-purge-confirm]")
            page.fill("[data-purge-text]", "ELIMINA DEFINITIVAMENTE")
            assert not page.is_disabled("[data-purge-confirm]")
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"eliminazione_definitiva_{larghezza}.png"), full_page=True)
            page.click("[data-purge-confirm]")
            page.wait_for_function(f"() => !document.querySelector(\"[data-building-group='{e['id']}']\")")
            assert _q(m, "SELECT count(*) FROM buildings WHERE id = %s", (e["id"],))[0][0] == 0
            assert _q(m, "SELECT count(*) FROM properties WHERE id = ANY(%s)", (unita,))[0][0] == 0
            assert _q(m, "SELECT count(*) FROM contacts WHERE id = %s", (m["mario"],))[0][0] == 1
            assert _q(m, "SELECT purged_by_user_id FROM building_deletion_requests WHERE id = %s",
                      (rid2,))[0][0] == m["superadmin"]
            assert errori == [], errori
            browser.close()
    except Exception as exc:  # noqa: BLE001
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium non disponibile: prova in browser NON eseguita (BLOCKED)")
        raise
    for misura in misure:
        assert misura["scroll"] <= misura["inner"], misura
        assert misura["fuori"] == [], misura
