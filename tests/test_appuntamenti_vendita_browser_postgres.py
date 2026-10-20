"""APPUNTAMENTI DI VENDITA - prova in BROWSER (Chromium) con backend e PostgreSQL VERI.

La Shell vera servita da uvicorn con i router veri (Agenda, Immobili, CORE) sul
database usa-e-getta dello schema completo (106 compresa); sostituita SOLO
l'autenticazione: la sessione e' quella del titolare dell'agenzia 1.

A 390 px e 1280 px:
  * «+ Nuovo appuntamento» -> tipo «Appuntamento di vendita»: le etichette
    diventano «Incarico *» e «Cliente interessato all'acquisto *»;
  * senza incarico la conferma si ferma con il messaggio, nulla nel database;
  * l'incarico si cerca fra gli incarichi (non fra tutti gli immobili), il
    cliente con il selettore contatti, l'agente dal suo elenco;
  * conferma -> l'appuntamento c'e' nel database con incarico, cliente, agente;
  * il pannello mostra «Apri incarico», che porta a #/incarichi/<immobile>;
  * nessuno scorrimento orizzontale, nessun errore di pagina.
"""
from __future__ import annotations

import threading
import time
import uuid

import pytest

from tests.test_appuntamenti_vendita_postgres import _incarico
from tests.test_delete_arch_0_postgres import (  # noqa: F401  (fixture riusate)
    DSN, _contatto, _futuro, _immobile, _q, completo, mondo, operatori,
)
from tests.test_edifici_1_browser_postgres import SHELL, _porta_libera
from tests.test_eliminazione_edificio_1_browser_postgres import _sfora

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


@pytest.fixture
def server(mondo):
    uvicorn = pytest.importorskip("uvicorn")
    pytest.importorskip("playwright.sync_api")
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles

    from appointments.router import router as agenda
    from core.router import router as core_router
    from operator_auth.dependencies import legacy_basic_agency_context, require_operator
    from property.router import router as immobili

    m = mondo

    def titolare():
        m["stato"]["chi"] = "owner_a"
        return m["ctx"]()

    app = FastAPI()

    @app.get("/api/operator-auth/me")
    def me():
        return {"user_id": m["ids"]["owner_a"], "agency_id": 1, "agency_name": "Agenzia Uno",
                "role": "agency_owner", "is_platform_admin": False,
                "expires_at": "2030-01-01T00:00:00Z"}

    for r in (agenda, immobili, core_router):
        app.include_router(r)
    app.dependency_overrides[legacy_basic_agency_context] = titolare
    app.dependency_overrides[require_operator] = titolare
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


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_appuntamento_di_vendita_in_chromium(server, larghezza):
    from playwright.sync_api import sync_playwright
    m = server
    casa = _incarico(m)
    titolo = _q(m, "SELECT title FROM properties WHERE id = %s", (casa,))[0][0]
    # un immobile qualunque con lo stesso titolo, SENZA incarico: non deve comparire
    _immobile(m, title=titolo)
    nome = f"Carla Acquirente {uuid.uuid4().hex[:4]}"
    cliente = _contatto(m, nome)
    giorno = _futuro(12, 10).date().isoformat()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(viewport={"width": larghezza, "height": 900}).new_page()
        errori = []
        page.on("pageerror", lambda e: errori.append(str(e)))
        page.goto(f"{m['url']}/os/?n={uuid.uuid4().hex[:8]}#/agenda/lista/{giorno}")
        page.wait_for_selector("text=+ Nuovo appuntamento")
        page.click("text=+ Nuovo appuntamento")
        page.wait_for_selector("dialog[open] [data-field='type']")
        dialogo = "dialog[open]"
        page.select_option(f"{dialogo} [data-field='type']", "sale_appointment")
        assert page.inner_text(f"{dialogo} [data-property-label]") == "Incarico *"
        assert "Cliente interessato all’acquisto *" in page.inner_text(f"{dialogo} [data-contact-label]")
        assert "obbligatori" in page.inner_text(f"{dialogo} [data-crm-legend]")
        page.fill(f"{dialogo} [data-field='date']", giorno)

        # senza incarico: fermo, nessuna scrittura
        prima = _q(m, "SELECT count(*) FROM appointments")[0][0]
        page.click(f"{dialogo} [data-submit]")
        page.wait_for_function(
            "() => document.querySelector('dialog[open] [data-error]').textContent.includes('incarico')")
        assert _q(m, "SELECT count(*) FROM appointments")[0][0] == prima

        # l'incarico: solo fra gli incarichi
        page.fill(f"{dialogo} [data-property-search]", titolo)
        page.wait_for_selector(f"{dialogo} [data-property-results] .agenda-lookup-item")
        voci = page.query_selector_all(f"{dialogo} [data-property-results] .agenda-lookup-item")
        assert len(voci) == 1, [v.inner_text() for v in voci]
        voci[0].click()
        page.wait_for_selector(f"{dialogo} [data-property-selected]:not([hidden])")

        # il cliente
        page.fill(f"{dialogo} .contact-picker-search", nome)
        page.wait_for_selector(f"{dialogo} .contact-picker-result")
        page.click(f"{dialogo} .contact-picker-result")

        # l'agente
        page.select_option(f"{dialogo} [data-field='agent']", str(m["ids"]["agent_a"]))
        assert _sfora(page)["scroll"] <= larghezza + 1, _sfora(page)
        page.click(f"{dialogo} [data-submit]")
        page.wait_for_selector("dialog[open] [data-field='type']", state="detached")

        riga = _q(m, "SELECT id, property_id, contact_id, assigned_user_id, status FROM appointments "
                     "WHERE appointment_type = 'sale_appointment' ORDER BY id DESC LIMIT 1")[0]
        assert tuple(riga[1:]) == (casa, cliente, m["ids"]["agent_a"], "scheduled"), riga

        # il pannello: «Apri incarico»
        page.wait_for_selector(f"[data-appointment-id='{riga[0]}']")
        page.click(f"[data-appointment-id='{riga[0]}']")
        page.wait_for_selector("[data-link='mandate']")
        link = page.query_selector("[data-link='mandate']")
        assert link.inner_text() == "Apri incarico"
        assert link.get_attribute("href") == f"#/incarichi/{casa}"
        assert _sfora(page)["scroll"] <= larghezza + 1, _sfora(page)

        assert not errori, errori
        browser.close()
