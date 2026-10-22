"""C3 - raccolta digitale del consenso nel BROWSER, su PostgreSQL usa-e-getta.

Un uvicorn vero con il router operatore delle comunicazioni (contesto
dell'agency_admin della Fase B), il router pubblico e la Shell statica da
`/os/`. Chromium a 390 px (smartphone) e 1280 px.

  01  la scheda contatto: il componente vero `communications.js` montato in
      una pagina minima con i CSS della Shell. I quattro consensi separati,
      «Genera link consenso», link e QR visibili solo alla generazione, la
      sostituzione e la disattivazione con conferma; nessuno sforamento.
  02  il cliente: apre il link, legge l'informativa, sceglie da solo (nessuna
      casella preselezionata), conferma. Email in attesa del double opt-in,
      telefono solo dichiarato; la pagina di conferma con il pulsante (aprirla
      non concede niente), poi il consenso email; SMS resta non attivo.

Nessuna email parte: la conferma resta in coda (il dispatcher non gira).
"""
from __future__ import annotations

import os
import re
import threading
import time

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _q, completo  # noqa: F401
from tests.test_consenso_digitale_postgres import (  # noqa: F401
    CANALI, _con_telefono, _conferma_dal_messaggio, dg,
)
from tests.test_delete_arch_0_postgres import mondo, operatori  # noqa: F401
from tests.test_fase_b_followup_postgres import _ctx, _guardia, _informativa, fb  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

BASE_FINTA = "https://test.stima360.it"


@pytest.fixture
def server(dg):
    uvicorn = pytest.importorskip("uvicorn")
    pytest.importorskip("playwright.sync_api")
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles
    from communication.public_router import router as pubblico
    from communication.router import router as operatore
    from operator_auth.dependencies import legacy_basic_agency_context
    from tests.test_edifici_1_browser_postgres import SHELL, _porta_libera

    m = dg
    app = FastAPI()
    app.include_router(operatore)
    app.include_router(pubblico)
    app.dependency_overrides[legacy_basic_agency_context] = lambda: _ctx(m, "admin_a")

    @app.get("/scheda/{contact_id}", response_class=HTMLResponse)
    def scheda(contact_id: int):
        # Solo la tab Comunicazioni della scheda contatto, col componente vero.
        return f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="/os/assets/app.css"><link rel="stylesheet" href="/os/assets/aurora.css">
</head><body><main id="content"><div id="mount"></div></main>
<script type="module">
import {{ mountCommunications }} from '/os/assets/components/communications.js';
mountCommunications(document.getElementById('mount'), {int(contact_id)});
</script></body></html>"""

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
        yield {**m, "url": f"http://127.0.0.1:{porta}"}
    finally:
        srv.should_exit = True
        filo.join(timeout=10)


def _sfora(page):
    return page.evaluate("""() => {
      const inScorrimento = (e) => { for (let n = e.parentElement; n; n = n.parentElement) {
        const o = getComputedStyle(n).overflowX; if (o === 'auto' || o === 'scroll') return true; } return false; };
      const fuori = [...document.querySelectorAll('body *')]
        .filter((e) => e.getClientRects().length && e.getBoundingClientRect().right > window.innerWidth + 0.5)
        .filter((e) => !inScorrimento(e))
        .map((e) => e.tagName + '.' + e.className);
      return { scroll: document.documentElement.scrollWidth, inner: window.innerWidth, fuori: fuori.slice(0, 5) };
    }""")


def _foto(page, nome):
    """Solo se richiesto: C3_SCREENSHOT_DIR=<cartella locale>."""
    cartella = os.getenv("C3_SCREENSHOT_DIR")
    if cartella:
        page.screenshot(path=os.path.join(cartella, f"{nome}.png"), full_page=True)


def _link_aperti(m, cid):
    return _q(m, "SELECT count(*) FROM consent_links WHERE contact_id = %s AND superseded_at IS NULL "
                 "AND disabled_at IS NULL", (cid,))[0][0]


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_01_la_scheda_contatto_genera_link_e_qr(server, larghezza):
    from playwright.sync_api import sync_playwright
    m = server
    _informativa(m)
    cid = _con_telefono(m, f"scheda-{larghezza}@example.it")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": larghezza, "height": 900})
        page = ctx.new_page()
        errori, dialoghi = [], []
        page.on("pageerror", lambda e: errori.append(str(e)))
        risposta = {"accetta": False}

        def dialogo(d):
            dialoghi.append(d.message)
            d.accept() if risposta["accetta"] else d.dismiss()
        page.on("dialog", dialogo)

        page.goto(f"{m['url']}/scheda/{cid}")
        page.wait_for_selector("[data-consent-capture]")
        # i quattro consensi separati, tutti non attivi
        for c in CANALI:
            assert page.is_visible(f"[data-consent-channel='{c}']"), c
        assert page.inner_text("[data-consent-link-generate]") == "Genera link consenso"
        assert page.query_selector("[data-consent-link-disable]") is None
        assert page.is_hidden("[data-consent-link-output]")
        assert _sfora(page)["scroll"] <= larghezza + 1, _sfora(page)

        page.click("[data-consent-link-generate]")
        page.wait_for_selector("[data-consent-link-output]:not([hidden])")
        url = page.input_value("[data-consent-link-url]")
        assert re.fullmatch(BASE_FINTA + r"/api/public/communication/consent/[A-Za-z0-9_-]{43}", url), url
        qr = page.get_attribute("[data-consent-link-qr]", "src")
        assert qr.startswith("data:image/svg+xml")
        page.wait_for_function("() => document.querySelector('[data-consent-link-qr]').naturalWidth > 0")
        assert "Raccoglie consensi fino al" in page.inner_text("[data-consent-link-expiry]")
        assert page.inner_text("[data-consent-link-generate]") == "Genera nuovo link consenso"
        _foto(page, f"scheda-{larghezza}")
        assert _link_aperti(m, cid) == 1
        assert _q(m, "SELECT count(*) FROM consent_events WHERE contact_id = %s", (cid,))[0][0] == 0
        assert _q(m, "SELECT count(*) FROM communication_messages WHERE contact_id = %s", (cid,))[0][0] == 0
        assert _sfora(page)["scroll"] <= larghezza + 1, _sfora(page)

        # sostituzione: annullata non cambia niente, confermata cambia il link
        page.click("[data-consent-link-generate]")
        assert dialoghi and "non raccogliera" in dialoghi[-1]
        assert page.input_value("[data-consent-link-url]") == url
        risposta["accetta"] = True
        page.click("[data-consent-link-generate]")
        page.wait_for_function("(u) => document.querySelector('[data-consent-link-url]').value !== u", arg=url)
        assert _link_aperti(m, cid) == 1
        assert _q(m, "SELECT count(*) FROM consent_links WHERE contact_id = %s", (cid,))[0][0] == 2

        # disattivazione con conferma: la scheda torna a «Genera link consenso»
        page.click("[data-consent-link-disable]")
        page.wait_for_function(
            "() => document.querySelector('[data-consent-link-generate]')?.textContent === 'Genera link consenso'")
        assert _link_aperti(m, cid) == 0
        assert not errori, errori
        browser.close()


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_02_il_cliente_sceglie_e_conferma_dal_telefono(server, larghezza):
    from playwright.sync_api import sync_playwright
    from communication import consent_page
    m = server
    informativa = _informativa(m)
    cid = _con_telefono(m, f"cliente-{larghezza}@example.it")
    url = consent_page.generate_link(_ctx(m, "admin_a"), cid)["url"].replace(BASE_FINTA, m["url"])
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": larghezza, "height": 900})
        page = ctx.new_page()
        errori = []
        page.on("pageerror", lambda e: errori.append(str(e)))

        risposta = page.goto(url)
        assert risposta.status == 200
        assert risposta.headers["x-frame-options"] == "DENY"
        assert page.query_selector("script") is None
        assert "Informativa marketing di prova" in page.inner_text("body")
        for c in CANALI:
            assert not page.is_checked(f"[name='grant_{c}']"), c
        assert _sfora(page)["scroll"] <= larghezza + 1, _sfora(page)
        _foto(page, f"pagina-{larghezza}")
        assert _q(m, "SELECT count(*) FROM consent_submissions WHERE contact_id = %s", (cid,))[0][0] == 0

        page.check("[name='grant_email']")
        page.check("[name='grant_sms']")
        page.click("form[method='post'] button[type='submit']")
        page.wait_for_selector("[data-esito='recorded']")
        _foto(page, f"esito-{larghezza}")
        assert _sfora(page)["scroll"] <= larghezza + 1, _sfora(page)
        riga = _q(m, "SELECT id, email_status, phone_channels_declared FROM consent_submissions "
                     "WHERE contact_id = %s", (cid,))
        assert len(riga) == 1 and riga[0][1] == "pending" and riga[0][2] == ["sms"]
        for c in CANALI:
            assert not _guardia(m, 1, cid, c).allowed, c

        # la conferma: aprirla mostra il pulsante e non concede niente
        conferma = _conferma_dal_messaggio(m, riga[0][0]).replace(BASE_FINTA, m["url"])
        page.goto(conferma)
        page.wait_for_selector("[data-conferma-form]")
        _foto(page, f"conferma-{larghezza}")
        assert page.query_selector("script") is None
        assert not _guardia(m, 1, cid).allowed
        assert _sfora(page)["scroll"] <= larghezza + 1, _sfora(page)
        page.click("[data-conferma-form] button[type='submit']")
        page.wait_for_selector("[data-esito='confirmed']")
        assert _guardia(m, 1, cid).allowed
        assert not _guardia(m, 1, cid, "sms").allowed, "SMS resta dichiarato finche' il numero non e' verificato"
        ev = _q(m, "SELECT channel, evidence_type, notice_id FROM consent_events WHERE contact_id = %s", (cid,))
        assert [tuple(x) for x in ev] == [("email", "double_opt_in", informativa)]
        assert not errori, errori
        browser.close()
