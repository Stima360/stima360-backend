"""CESTINO-RICHIESTE-1 - prova in BROWSER (Chromium) con backend e PostgreSQL VERI.

uvicorn con la Shell vera e i router veri (buy, core, CRM, abbinamenti,
proposte, vendite, immobili) sul database usa-e-getta, migration 092
compresa; solo l'autenticazione sostituita (titolare agenzia 1). A 390 px e
1280 px:

  b01  «Elimina…» di una richiesta con un task aperto: il blocco elenca il
       task con il collegamento ad Attivita', la conferma resta disabilitata;
       nulla cambia nel database;
  b02  «Elimina…» di una richiesta senza processi aperti: effetti espliciti
       (stato invariato, abbinamenti, contatto e comunicazioni), motivo ->
       Cestino, ritorno ad Acquirenti; la richiesta non e' in elenco ne' in
       ricerca, l'altra si';
  b03  Cestino › Richieste: card, «Apri scheda» in sola lettura (nessun
       comando nell'intestazione ne' nelle tab), «Ripristina» sullo stesso id
       con l'avviso dell'altra richiesta aperta; di nuovo in elenco.
Nessuno scorrimento orizzontale, nessun errore di pagina. Screenshot in
`RICHIESTE1_SHOTS` (o /tmp/richieste1_shots).
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import _q  # noqa: F401
from tests.test_edifici_1_browser_postgres import DSN, SHELL, _porta_libera, completo, mondo  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")
IMMOBILE = 18


@pytest.fixture
def server(mondo):
    uvicorn = pytest.importorskip("uvicorn")
    pytest.importorskip("playwright.sync_api")
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles

    from buy.router import router as acquirenti
    from core.router import router as core_router
    from crm.router import router as crm
    from match.router import router as abbinamenti
    from operator_auth.dependencies import (audit_actor, legacy_basic_agency_context, require_authenticated_operator,
                                            require_operator)
    from property.router import router as immobili
    from proposal.router import router as proposte
    from sale.router import router as vendite

    sessione = {"user_id": mondo["ids"]["owner_a"], "agency_id": 1, "agency_name": "Agenzia Uno",
                "role": "agency_owner", "is_platform_admin": False, "expires_at": "2030-01-01T00:00:00Z"}

    def contesto():
        mondo["stato"]["chi"] = "owner_a"
        return mondo["ctx"]()

    app = FastAPI()
    for r in (acquirenti, core_router, crm, abbinamenti, immobili, proposte, vendite):
        app.include_router(r)
    for dipendenza in (legacy_basic_agency_context, require_operator, require_authenticated_operator):
        app.dependency_overrides[dipendenza] = contesto
    app.dependency_overrides[audit_actor] = lambda: "test"

    @app.get("/api/operator-auth/me")
    def me():
        return sessione

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
        yield {"url": f"http://127.0.0.1:{porta}", **mondo}
    finally:
        srv.should_exit = True
        filo.join(timeout=10)


def _dati(m):
    sigla = uuid.uuid4().hex[:5]
    cid = _q(m, "INSERT INTO contacts (agency_id, contact_type, display_name, first_name, last_name) "
                "VALUES (1, 'person', %s, 'Mario', %s) RETURNING id", (f"Mario Prova {sigla}", f"Prova {sigla}"))[0][0]

    def richiesta(titolo):
        return _q(m, "INSERT INTO buy_requests (agency_id, contact_id, title, status, budget_target) "
                     "VALUES (1, %s, %s, 'active', 250000) RETURNING id", (cid, titolo))[0][0]
    bloccata = richiesta(f"Cerca trilocale {sigla}")
    tid = _q(m, "INSERT INTO tasks (agency_id, contact_id, title, status, priority) "
                "VALUES (1, %s, 'Richiamare Mario', 'open', 'normal') RETURNING id", (cid,))[0][0]
    _q(m, "INSERT INTO buy_request_task_links (buy_request_id, task_id) VALUES (%s, %s)", (bloccata, tid))
    sbagliata = richiesta(f"Cerca bilocale {sigla}")
    _q(m, "INSERT INTO matches (buy_request_id, property_id, compatibility_status, score_total, match_class, "
          "algorithm_version) VALUES (%s, %s, 'compatible', 80, 'strong', 'test')", (sbagliata, IMMOBILE))
    return {"contatto": cid, "bloccata": bloccata, "sbagliata": sbagliata, "sigla": sigla}


def _sfora(page):
    return page.evaluate("""() => {
      const inScorrimento = (e) => { for (let n = e.parentElement; n; n = n.parentElement) {
        const o = getComputedStyle(n).overflowX; if (o === 'auto' || o === 'scroll') return true; } return false; };
      const fuori = [...document.querySelectorAll('#content *, dialog[open] *')]
        .filter((e) => e.getClientRects().length && e.getBoundingClientRect().right > window.innerWidth + 0.5)
        .filter((e) => !inScorrimento(e))
        .map((e) => e.tagName + '.' + e.className);
      return { scroll: document.documentElement.scrollWidth, inner: window.innerWidth, fuori: fuori.slice(0, 5) };
    }""")


def _elenco(page, url):
    """L'elenco Acquirenti, letto quando ha finito di caricare."""
    page.goto(f"{url}/os/#/acquirenti")
    page.wait_for_function("() => { const c = document.querySelector('#content'); "
                           "return c && c.innerText.includes('Nuova richiesta') && !c.innerText.includes('Caricamento'); }")
    page.wait_for_load_state("networkidle")
    return page.inner_text("#content")


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_b01_b03_cestino_richieste_in_chromium(server, larghezza):
    from playwright.sync_api import sync_playwright
    m = server
    d = _dati(m)
    cartella = Path(os.environ.get("RICHIESTE1_SHOTS") or (Path("/tmp") / "richieste1_shots"))
    cartella.mkdir(parents=True, exist_ok=True)
    misure = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": larghezza, "height": 860})
            errori = []
            page.on("pageerror", lambda x: errori.append(str(x)))

            # --- b01: processi aperti -> blocco con collegamento -----------------------------
            page.goto(f"{m['url']}/os/#/acquirenti/{d['bloccata']}")
            page.wait_for_selector("#buy-trash-btn")
            page.wait_for_load_state("networkidle")
            page.click("#buy-trash-btn")
            page.wait_for_selector("[data-blocker='TASK_OPEN']")
            testo = page.inner_text("#buy-trash-dialog")
            assert "Non si può spostare nel Cestino" in testo and "Richiamare Mario" in testo, testo
            assert page.is_disabled("#buy-trash-dialog [data-trash-confirm]")
            assert page.locator("#buy-trash-dialog a[href='#/attivita']").count() >= 1
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"cestino_richieste_bloccata_{larghezza}.png"))
            assert _q(m, "SELECT deleted_at FROM buy_requests WHERE id = %s", (d["bloccata"],))[0][0] is None
            assert _q(m, "SELECT status FROM tasks t JOIN buy_request_task_links l ON l.task_id = t.id "
                         "WHERE l.buy_request_id = %s", (d["bloccata"],))[0][0] == "open"

            # --- b02: senza processi aperti -> effetti, motivo, Cestino ----------------------
            page.goto(f"{m['url']}/os/#/acquirenti/{d['sbagliata']}")
            page.wait_for_function(f"() => (document.querySelector('#acquirente-header-title') || {{}}).textContent "
                                   f"=== 'Cerca bilocale {d['sigla']}'")
            page.wait_for_load_state("networkidle")
            page.click("#buy-trash-btn")
            page.wait_for_selector("#buy-trash-dialog input[name='trash-reason']")
            effetti = page.inner_text("#buy-trash-dialog [data-trash-effects]")
            assert "Lo stato resta «Attiva»" in effetti and "1 abbinamento esce dalle liste" in effetti, effetti
            assert "resta attivo, con la sua altra richiesta aperta" in effetti
            assert "Le comunicazioni del contatto non vengono sospese né annullate." in effetti
            page.check("#buy-trash-dialog input[value='created_by_mistake']")
            page.fill("#buy-trash-dialog [data-trash-note]", "Inserita due volte")
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"cestino_richieste_conferma_{larghezza}.png"))
            page.click("#buy-trash-dialog [data-trash-confirm]")
            page.wait_for_function("() => window.location.hash === '#/acquirenti'")
            page.wait_for_selector("text=Richiesta spostata nel Cestino")
            riga = _q(m, "SELECT deleted_reason, status FROM buy_requests WHERE id = %s", (d["sbagliata"],))[0]
            assert riga == ["created_by_mistake", "active"]
            elenco = _elenco(page, m["url"])
            assert f"Cerca bilocale {d['sigla']}" not in elenco and f"Cerca trilocale {d['sigla']}" in elenco
            misure.append(_sfora(page))

            # --- b03: Cestino › Richieste, scheda in sola lettura, Ripristina -----------------
            page.goto(f"{m['url']}/os/#/cestino/richieste")
            page.wait_for_selector(f"[data-buy-trash-item='{d['sbagliata']}']")
            carta = page.inner_text(f"[data-buy-trash-item='{d['sbagliata']}']")
            assert f"Cerca bilocale {d['sigla']}" in carta and "Creato per errore — Inserita due volte" in carta, carta
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"cestino_richieste_elenco_{larghezza}.png"), full_page=True)
            page.click(f"[data-buy-trash-item='{d['sbagliata']}'] [data-buy-trash-open]")
            page.wait_for_selector("[data-buy-in-trash]")
            page.wait_for_load_state("networkidle")
            assert page.locator("#request-edit-btn").count() == 0 and page.locator("#buy-trash-btn").count() == 0
            for scheda in ("criteri", "abbinamenti", "proposte", "task"):
                page.click(f"#request-tabs [data-tab='{scheda}']")
                assert page.locator("#request-tab-content button, #request-tab-content form").count() == 0, scheda
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"cestino_richieste_scheda_{larghezza}.png"))
            page.click("[data-buy-restore-btn]")
            page.wait_for_selector("[data-buy-duplicates]")
            avviso = page.inner_text("[data-buy-duplicates]")
            assert f"Cerca trilocale {d['sigla']}" in avviso and "Nessuna richiesta è stata unita, chiusa o modificata." in avviso
            assert page.locator("[data-buy-in-trash]").count() == 0 and page.locator("#buy-trash-btn").count() == 1
            assert _q(m, "SELECT deleted_at, status FROM buy_requests WHERE id = %s", (d["sbagliata"],))[0] == [None, "active"]
            assert f"Cerca bilocale {d['sigla']}" in _elenco(page, m["url"])
            assert errori == [], errori
            browser.close()
    except Exception as exc:  # noqa: BLE001
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium non disponibile: prova in browser NON eseguita (BLOCKED)")
        raise
    for misura in misure:
        assert misura["scroll"] <= misura["inner"], misura
        assert misura["fuori"] == [], misura
