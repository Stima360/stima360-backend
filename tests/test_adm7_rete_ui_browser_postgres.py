"""ADM-7 - l'interfaccia della Rete, in Chromium (390 e 1280 px).

PostgreSQL usa-e-getta, sessioni vere, il server e la Shell di ADM-3
(`browser_server`). Si guida l'interfaccia come farebbe il Super Admin:

  * filtro Attive / Sospese / Archiviate nell'elenco agenzie
  * Comune presidiato da un'agenzia non attiva evidenziato nei territori
  * testi delle conferme allineati ad ADM-3 BIS (email e follow-up continuano)
  * «Consulta (sola lettura)» su un'agenzia sospesa, barra SOLA LETTURA, uscita
  * ruolo di piattaforma: concessione e revoca con conferma; il designato non
    ha comandi
  * scheda Registro: righe, filtro, paginazione
  * nessun errore JavaScript, nessuno scorrimento orizzontale
  * un amministratore di piattaforma non designato non vede comandi
"""
from __future__ import annotations

import uuid

import pytest

from tests.test_adm1_super_admin_postgres import adm1  # noqa: F401
from tests.test_adm2_impact_postgres import adm2  # noqa: F401
from tests.test_adm3_continuity_postgres import browser_server  # noqa: F401
from tests.test_c1_lettura_territoriale_postgres import rete  # noqa: F401
from tests.test_censimento_3_backend_postgres import DSN, _q, completo, mondo  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


@pytest.fixture
def adm7(browser_server):
    m = browser_server
    yield m
    _q(m, "UPDATE operator_sessions SET acting_agency_id = NULL, acting_entered_at = NULL")
    _q(m, "UPDATE agencies SET status = 'active' WHERE id = %s", (m["quattro"],))
    _q(m, "UPDATE operator_users SET is_platform_admin = FALSE WHERE id = %s", (m["persone"]["membro"],))


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_rete_adm7_in_chromium(adm7, larghezza):
    from playwright.sync_api import sync_playwright
    from tests.test_eliminazione_edificio_1_browser_postgres import _sfora
    m = adm7
    q, membro = m["quattro"], m["persone"]["membro"]
    m["accedi"]("membro")  # il login riuscito che la promozione richiede
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": larghezza, "height": 900})
        ctx.add_cookies([{"name": m["cookie"], "value": m["token"], "url": m["url"]}])
        page = ctx.new_page()
        errori = []
        page.on("pageerror", lambda e: errori.append(str(e)))

        def vai(frammento):
            page.goto(f"{m['url']}/os/?n={uuid.uuid4().hex[:8]}#{frammento}")

        def dialogo():
            page.wait_for_selector("dialog.modal [data-conferma='si']")
            return page.inner_text("dialog.modal")

        def conferma(si=True):
            page.click(f"dialog.modal [data-conferma='{'si' if si else 'no'}']")
            page.wait_for_selector("dialog.modal", state="detached")

        def nessuno_sfora():
            assert _sfora(page)["scroll"] <= larghezza + 1, _sfora(page)

        # --- testi delle conferme e sospensione dall'interfaccia ----------------
        vai(f"/rete/agenzie/{q}")
        page.wait_for_selector("[data-stato-agenzia='suspended']")
        page.click("[data-stato-agenzia='suspended']")
        testo = dialogo()
        assert "continuano a nome di STIMA360" in testo and "promemoria degli appuntamenti" in testo, testo
        assert "follow-up automatici, le prenotazioni online e i calendari collegati si fermano" not in testo
        conferma()
        page.wait_for_selector("[data-stato-agenzia='archived']")
        assert _q(m, "SELECT status FROM agencies WHERE id = %s", (q,))[0][0] == "suspended"
        page.click("[data-stato-agenzia='archived']")
        assert "continuano a nome di STIMA360" in dialogo()
        conferma(si=False)
        page.click("[data-stato-agenzia='active']")
        assert "riprendono" in dialogo()
        conferma(si=False)

        # --- Consulta (sola lettura), barra, uscita -----------------------------
        page.wait_for_selector("#entra-agenzia[data-modo='consulta']")
        assert "Consulta (sola lettura)" in page.inner_text("#entra-agenzia")
        page.click("#entra-agenzia")
        assert "SOLA LETTURA" in dialogo()
        conferma()
        page.wait_for_selector("#acting-bar:not([hidden])")
        assert "SOLA LETTURA" in page.inner_text("#acting-text")
        assert _q(m, "SELECT status FROM agencies WHERE id = %s", (q,))[0][0] == "suspended"
        page.click("#acting-exit-btn")
        page.wait_for_selector("#acting-bar", state="hidden")
        assert _q(m, "SELECT count(*) FROM operator_sessions WHERE acting_agency_id = %s", (q,))[0][0] == 0

        # --- filtro agenzie e Comuni presidiati da agenzie non attive -----------
        vai("/rete")
        page.wait_for_selector("#filtro-stato-agenzia")
        page.select_option("#filtro-stato-agenzia", "suspended")
        assert "Quattro" in page.inner_text("#agenzie-lista")
        page.select_option("#filtro-stato-agenzia", "active")
        assert "Quattro" not in page.inner_text("#agenzie-lista")
        nessuno_sfora()
        page.click("#rete-tabs [data-tab='territori']")
        page.wait_for_selector("#territori-lista table")
        assert "Agenzia sospesa" in page.inner_text("#territori-lista")
        nessuno_sfora()

        # --- ruolo di piattaforma nella scheda Operatori -------------------------
        vai("/rete/agenzie/2")
        page.click("#agenzia-tabs [data-tab='operatori']")
        concedi = f"[data-azione='piattaforma-concedi'][data-operatore='{membro}']"
        page.wait_for_selector(concedi)
        nessuno_sfora()
        page.click(concedi)
        assert "Non diventa il Super Admin designato" in dialogo()
        conferma()
        togli = f"[data-azione='piattaforma-togli'][data-operatore='{membro}']"
        page.wait_for_selector(togli)
        assert "Amministratore di piattaforma" in page.inner_text("#operatori-lista")
        assert _q(m, "SELECT is_platform_admin FROM operator_users WHERE id = %s", (membro,))[0][0] is True
        page.click(togli)
        assert "sessioni verranno chiuse" in dialogo()
        conferma()
        page.wait_for_selector(concedi)
        assert _q(m, "SELECT is_platform_admin FROM operator_users WHERE id = %s", (membro,))[0][0] is False
        if larghezza == 390:
            assert page.eval_on_selector("#operatori-lista thead", "e => getComputedStyle(e).display") == "none"

        # --- Registro -------------------------------------------------------------
        vai("/rete")
        page.click("#rete-tabs [data-tab='registro']")
        page.wait_for_selector("#registro-lista table")
        assert page.is_disabled("#reg-prec")
        page.select_option("#reg-azione", "platform.operator.platform_admin.update")
        page.click("#registro-filtri button[type='submit']")
        page.wait_for_function(
            "() => document.querySelector('#registro-lista').innerText.includes('ruolo di piattaforma: si’ → no')")
        testo = page.inner_text("#registro-lista")
        assert "Ruolo di piattaforma" in testo and "ruolo di piattaforma: no → si’" in testo, testo
        assert "Ingresso in agenzia" not in testo
        nessuno_sfora()

        assert not errori, errori
        browser.close()


def test_un_amministratore_non_designato_non_vede_comandi(adm7):
    from playwright.sync_api import sync_playwright
    from operator_auth.enums import COOKIE_NAME
    m = adm7
    altro = m["accedi"]("altro").cookies.get(COOKIE_NAME)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        ctx.add_cookies([{"name": m["cookie"], "value": altro, "url": m["url"]}])
        page = ctx.new_page()
        for frammento in ("/rete", f"/rete/agenzie/{m['quattro']}"):
            page.goto(f"{m['url']}/os/?n={uuid.uuid4().hex[:8]}#{frammento}")
            page.wait_for_selector(".error-box")
            assert "riservata al Super Admin" in page.inner_text(".error-box")
            assert page.query_selector("[data-tab='registro']") is None
            assert page.query_selector("#entra-agenzia, [data-azione], [data-stato-agenzia]") is None
        browser.close()
