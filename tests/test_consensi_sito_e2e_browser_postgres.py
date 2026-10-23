"""C3.2 - COLLAUDO END-TO-END dei consensi, nel BROWSER, su PostgreSQL usa-e-getta.

Due database (il "sito" con lo schema PROD e il CRM), l'importatore VERO di
SITE-IMPORT-1, un uvicorn vero con il router operatore delle comunicazioni, il
router pubblico e il componente vero della scheda contatto; Chromium a 390 px
(telefono del cliente) e 1280 px (CRM). Dati fittizi; nessuna email parte
(`invia_mail` esplode, il dispatcher usa il provider finto).

  A  Serena Fanini: richiesta dal sito con la casella marketing spuntata,
     importata. La scheda mostra lo storico del sito (Privacy non
     documentata, scelta marketing con data e richiesta, Email e WhatsApp
     «scelto sul sito · da confermare»), avvisa che manca l'informativa; il
     QR apre la pagina solo-revoca; il Super Admin pubblica l'informativa e
     LO STESSO link permette di scegliere, ricordando la scelta del sito.
  B  Mario Rossi: QR dalla scheda, scelta Email, «in attesa», conferma
     (double opt-in), scheda «concesso», dispatcher SPEDISCE un marketing
     email e SOPPRIME un WhatsApp; revoca, scheda «revocato», dispatcher
     SOPPRIME il marketing email.
"""
from __future__ import annotations

import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone

import pytest

from tests.test_censimento_3_backend_postgres import DSN, completo  # noqa: F401 - fixture
from tests.test_delete_arch_0_postgres import operatori  # noqa: F401 - fixture
from tests.test_site_import_1_postgres import PDF, mondo, sito_db  # noqa: F401 - fixture

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: serve PostgreSQL isolato")

BASE = "https://crm-test.example.invalid"
CANALI = ("email", "sms", "whatsapp", "phone")


def _foto(page, nome):
    cartella = os.getenv("C3_SCREENSHOT_DIR")
    if cartella:
        page.screenshot(path=os.path.join(cartella, f"{nome}.png"), full_page=True)


@pytest.fixture
def e2e(mondo, operatori, monkeypatch):
    uvicorn = pytest.importorskip("uvicorn")
    pytest.importorskip("playwright.sync_api")
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles
    from communication.public_router import router as pubblico
    from communication.router import router as operatore
    from operator_auth.context import OperatorContext
    from operator_auth.dependencies import legacy_basic_agency_context
    from tests.test_edifici_1_browser_postgres import SHELL, _porta_libera

    m = mondo
    q = m["q_crm"]
    for sql in ("ALTER TABLE consent_submissions DISABLE TRIGGER trg_consent_submissions_guard",
                "DELETE FROM consent_submissions",
                "ALTER TABLE consent_submissions ENABLE TRIGGER trg_consent_submissions_guard",
                "ALTER TABLE consent_links DISABLE TRIGGER trg_consent_links_guard",
                "DELETE FROM consent_links",
                "ALTER TABLE consent_links ENABLE TRIGGER trg_consent_links_guard",
                "DELETE FROM consent_page_rate_limits",
                "UPDATE consent_notices SET valid_to = GREATEST(valid_from, NOW() - interval '1 second') "
                "WHERE purpose = 'marketing' AND (valid_to IS NULL OR valid_to >= NOW())"):
        q(sql)
    monkeypatch.setenv("PUBLIC_BASE_URL", BASE)
    monkeypatch.setenv("CONSENT_CAPTURE_IP_PEPPER", "p" * 32)
    monkeypatch.delenv("SITE_CONSENT_DOUBLE_OPT_IN", raising=False)
    monkeypatch.setenv("STIMA360_SUPER_ADMIN_USER_ID", str(operatori["platform"]))

    def ctx():
        return OperatorContext(user_id=operatori["admin_a"], agency_id=1, role="agency_admin",
                               is_platform_admin=False, session_id=None, auth_channel="operator_session")

    app = FastAPI()
    app.include_router(operatore)
    app.include_router(pubblico)
    app.dependency_overrides[legacy_basic_agency_context] = ctx

    @app.get("/scheda/{contact_id}", response_class=HTMLResponse)
    def scheda(contact_id: int):
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
        yield {**m, "url": f"http://127.0.0.1:{porta}", "ctx": ctx, "operatori": operatori}
    finally:
        srv.should_exit = True
        filo.join(timeout=10)
        q("UPDATE consent_notices SET valid_to = GREATEST(valid_from, NOW() - interval '1 second') "
          "WHERE purpose = 'marketing' AND (valid_to IS NULL OR valid_to >= NOW())")


def _guardia(cid, canale):
    from consent.guard import can_send_marketing
    from operator_auth.context import SystemAgencyContext
    return can_send_marketing(SystemAgencyContext(agency_id=1, origin="public_stima"), cid, channel=canale)


def _pubblica_informativa(m):
    """La schermata Super Admin › Rete › Informative privacy usa questo servizio."""
    from operator_auth.context import OperatorContext
    from platform_admin import consent_notices_service as svc
    attore = OperatorContext(user_id=m["operatori"]["platform"], agency_id=None, role=None,
                             is_platform_admin=True, session_id=None, auth_channel="operator_session")
    return svc.create_notice(attore, purpose="marketing", version=f"collaudo-{uuid.uuid4().hex[:6]}",
                             context="Link personale / QR dal CRM (collaudo locale)",
                             content="TESTO DI PROVA PER IL COLLAUDO LOCALE - non e' l'informativa legale.",
                             valid_from=datetime.now(timezone.utc), valid_to=None, channels=list(CANALI))


def _apri(page, url, selettore):
    page.goto(url)
    page.wait_for_selector(selettore)


def _sfora(page, larghezza):
    return page.evaluate("() => document.documentElement.scrollWidth") <= larghezza + 1


def _accoda_marketing(m, cid, canale, destinazione, chiave):
    from communication import service as comm
    return comm.enqueue(m["ctx"](), contact_id=cid, channel=canale, communication_type="marketing", mode="manual",
                        reason_code="operator_manual", rendered_body="Analisi di mercato di prova",
                        destination_snapshot=destinazione, subject_snapshot="Prova" if canale == "email" else None,
                        idempotency_key=chiave)


def _stato_messaggio(m, mid):
    return m["q_crm"]("SELECT status FROM communication_messages WHERE id = %s", (mid,))[0][0]


# ---------------------------------------------------------------------------

def test_A_serena_fanini_storico_del_sito_e_conferma_digitale(e2e):
    from playwright.sync_api import sync_playwright
    m = e2e
    spunta = datetime(2026, 10, 3, 9, 41, tzinfo=timezone.utc)
    s = m["stima"](minuti_fa=60, nome="Serena", cognome="Fanini", email="serena.fanini@example.invalid",
                   telefono="3471112233", consenso_marketing=True, consenso_marketing_at=spunta)
    m["archivio"].pdf[s] = PDF
    esito = m["giro"]()
    assert esito["stime_imported"] == 1, esito
    passi = m["ledger"]()[s][1]
    assert passi["bridge"]["state"] == "done" and passi["consent"]["status"] == "disabled"
    cid = passi["bridge"]["contact_id"]

    # nel CRM: la spunta del sito e' una prova senza canale, nessun canale autorizzato
    ev = m["q_crm"]("SELECT channel, decision, source, evidence_type, evidence_ref, decided_at FROM consent_events "
                    "WHERE contact_id = %s", (cid,))
    assert [tuple(r[:5]) for r in ev] == [(None, "granted", "public_stima", "stima", str(s))]
    assert ev[0][5] == spunta
    for c in CANALI:
        assert not _guardia(cid, c).allowed, c

    with sync_playwright() as p:
        browser = p.chromium.launch()
        crm = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        tel = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        errori = []
        for pg in (crm, tel):
            pg.on("pageerror", lambda e: errori.append(str(e)))
        crm.on("dialog", lambda d: d.accept())

        # --- la scheda: storico del sito fedele, distinto dai consensi autorizzati
        _apri(crm, f"{m['url']}/scheda/{cid}", "[data-site-record]")
        assert "non documentata nel CRM" in crm.inner_text("[data-site-privacy]")
        scelta = crm.inner_text("[data-site-marketing-choice]")
        assert "Scelta registrata dal sito" in scelta and "Email + WhatsApp" in scelta
        assert "03/10/2026" in scelta and f"n. {s}" in scelta
        assert "non e’ un consenso autorizzato" in crm.inner_text("[data-site-proofs]") \
            or "non e' un consenso autorizzato" in crm.inner_text("[data-site-proofs]")
        for c in ("email", "whatsapp"):
            riga = crm.inner_text(f"[data-consent-channel='{c}']")
            assert "scelto sul sito · da confermare" in riga and "non autorizza invii" in riga, riga
        for c in ("sms", "phone"):
            assert "mai dato" in crm.inner_text(f"[data-consent-channel='{c}']"), c
        assert crm.query_selector("[data-consent-legacy]") is None
        assert crm.is_visible("[data-capture-no-notice]"), "manca l'avviso dell'informativa"
        _foto(crm, "A1_serena_scheda_storico_sito")

        # --- il QR senza informativa corrente: la pagina permette solo di revocare
        crm.click("[data-consent-link-generate]")
        crm.wait_for_selector("[data-consent-link-output]:not([hidden])")
        url = crm.input_value("[data-consent-link-url]").replace(BASE, m["url"])
        _apri(tel, url, "[data-consenso-form]")
        assert "non è possibile raccogliere nuovi consensi" in tel.inner_text("body")
        assert tel.query_selector("input[name^='grant_']") is None
        _foto(tel, "A2_serena_pagina_senza_informativa")

        # --- il Super Admin pubblica l'informativa: LO STESSO link ora raccoglie
        _pubblica_informativa(m)
        _apri(tel, url, "[data-informativa]")
        testo = tel.inner_text("body")
        assert "non è possibile raccogliere" not in testo and "TESTO DI PROVA" in testo
        assert "03/10/2026" in tel.inner_text("[data-scelta-sito]")
        caselle = tel.eval_on_selector_all("input[name^='grant_']", "els => els.map(e => [e.name, e.checked])")
        assert caselle == [[f"grant_{c}", False] for c in CANALI], caselle
        assert _sfora(tel, 390)
        _foto(tel, "A3_serena_pagina_con_informativa")

        # Serena conferma Email e WhatsApp: email in attesa, WhatsApp solo dichiarato
        tel.check("[name='grant_email']")
        tel.check("[name='grant_whatsapp']")
        tel.click("[data-consenso-form] button[type='submit']")
        tel.wait_for_selector("[data-esito='recorded']")
        corpo = m["q_crm"]("SELECT rendered_body FROM communication_messages WHERE contact_id = %s "
                           "AND reason_code = 'consent_confirmation'", (cid,))[0][0]
        conferma = re.search(re.escape(BASE) + r"/api/public/communication/consent/confirm/[A-Za-z0-9_-]{43}",
                             corpo).group(0).replace(BASE, m["url"])
        _apri(tel, conferma, "[data-conferma-form]")
        tel.click("[data-conferma-form] button[type='submit']")
        tel.wait_for_selector("[data-esito='confirmed']")
        assert _guardia(cid, "email").allowed
        assert not _guardia(cid, "whatsapp").allowed

        _apri(crm, f"{m['url']}/scheda/{cid}", "[data-site-record]")
        assert "concesso" in crm.inner_text("[data-consent-channel='email']")
        wa = crm.inner_text("[data-consent-channel='whatsapp']")
        assert "scelto · numero da verificare" in wa and "scelto dal cliente" in wa, wa
        assert crm.query_selector("[data-site-choice]") is None, "la scelta digitale prevale sulla spunta del sito"
        assert crm.query_selector("[data-capture-no-notice]") is None
        assert crm.query_selector("[data-site-marketing-choice]") is not None, "lo storico del sito resta"
        _foto(crm, "A4_serena_scheda_dopo_conferma")
        assert not errori, errori
        browser.close()


def test_B_mario_rossi_qr_double_opt_in_dispatcher_e_revoca(e2e):
    from playwright.sync_api import sync_playwright
    from communication import dispatcher
    m = e2e
    _pubblica_informativa(m)
    cid = m["q_crm"]("INSERT INTO contacts (agency_id, display_name, first_name, last_name, email, email_normalized, "
                     "phone, phone_normalized, status) VALUES (1, 'Mario Rossi', 'Mario', 'Rossi', "
                     "'mario.rossi@example.invalid', 'mario.rossi@example.invalid', '333 123 4567', '393331234567', "
                     "'active') RETURNING id")[0][0]

    with sync_playwright() as p:
        browser = p.chromium.launch()
        crm = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        tel = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        errori = []
        for pg in (crm, tel):
            pg.on("pageerror", lambda e: errori.append(str(e)))
        crm.on("dialog", lambda d: d.accept())

        _apri(crm, f"{m['url']}/scheda/{cid}", "[data-consent-capture]")
        assert crm.query_selector("[data-site-record]") is None, "Mario non viene dal sito"
        assert crm.query_selector("[data-capture-no-notice]") is None
        crm.click("[data-consent-link-generate]")
        crm.wait_for_selector("[data-consent-link-output]:not([hidden])")
        crm.wait_for_function("() => document.querySelector('[data-consent-link-qr]').naturalWidth > 0")
        url = crm.input_value("[data-consent-link-url]").replace(BASE, m["url"])
        _foto(crm, "B1_mario_qr_generato")

        _apri(tel, url, "[data-informativa]")
        assert tel.query_selector("[data-scelta-sito]") is None
        tel.check("[name='grant_email']")
        tel.click("[data-consenso-form] button[type='submit']")
        tel.wait_for_selector("[data-esito='recorded']")
        assert not _guardia(cid, "email").allowed

        _apri(crm, f"{m['url']}/scheda/{cid}", "[data-consent-pending='email']")
        assert "in attesa di conferma" in crm.inner_text("[data-consent-channel='email']")
        assert "programmato" in crm.inner_text("body") and "Conferma consenso email" in crm.inner_text("body")
        _foto(crm, "B2_mario_email_in_attesa")

        corpo = m["q_crm"]("SELECT rendered_body FROM communication_messages WHERE contact_id = %s "
                           "AND reason_code = 'consent_confirmation'", (cid,))[0][0]
        conferma = re.search(re.escape(BASE) + r"/api/public/communication/consent/confirm/[A-Za-z0-9_-]{43}",
                             corpo).group(0).replace(BASE, m["url"])
        _apri(tel, conferma, "[data-conferma-form]")
        tel.click("[data-conferma-form] button[type='submit']")
        tel.wait_for_selector("[data-esito='confirmed']")
        _foto(tel, "B3_mario_email_confermata")

        _apri(crm, f"{m['url']}/scheda/{cid}", "[data-consent-channel='email']")
        assert "concesso" in crm.inner_text("[data-consent-channel='email']")
        for c in ("sms", "whatsapp", "phone"):
            assert "mai dato" in crm.inner_text(f"[data-consent-channel='{c}']"), c
        _foto(crm, "B4_mario_scheda_concesso")

        # --- il dispatcher: email marketing SPEDITA, WhatsApp SOPPRESSO
        dispatcher.dispatch_batch(m["ctx"](), channel="email")        # svuota la conferma gia' in coda
        email1 = _accoda_marketing(m, cid, "email", "mario.rossi@example.invalid", f"e2e:{cid}:e1")
        wa = _accoda_marketing(m, cid, "whatsapp", "393331234567", f"e2e:{cid}:w1")
        esito_e = dispatcher.dispatch_batch(m["ctx"](), channel="email")
        esito_w = dispatcher.dispatch_batch(m["ctx"](), channel="whatsapp")
        assert esito_e["sent"] == 1 and esito_e["suppressed"] == 0, esito_e
        assert esito_w["suppressed"] == 1 and esito_w["sent"] == 0, esito_w
        assert _stato_messaggio(m, email1["message"]["id"]) == "sent"
        assert _stato_messaggio(m, wa["message"]["id"]) == "suppressed"

        # --- revoca dallo stesso link
        _apri(tel, url, "[name='revoke_email']")
        tel.check("[name='revoke_email']")
        tel.click("[data-consenso-form] button[type='submit']")
        tel.wait_for_selector("[data-esito='recorded']")
        _foto(tel, "B5_mario_revoca")
        assert _guardia(cid, "email").reason == "deny_revoked"

        _apri(crm, f"{m['url']}/scheda/{cid}", "[data-consent-channel='email']")
        assert "revocato" in crm.inner_text("[data-consent-channel='email']")
        _foto(crm, "B6_mario_scheda_revocato")

        # --- dopo la revoca il dispatcher SOPPRIME il marketing email
        email2 = _accoda_marketing(m, cid, "email", "mario.rossi@example.invalid", f"e2e:{cid}:e2")
        esito_e2 = dispatcher.dispatch_batch(m["ctx"](), channel="email")
        assert esito_e2["suppressed"] == 1 and esito_e2["sent"] == 0, esito_e2
        assert _stato_messaggio(m, email2["message"]["id"]) == "suppressed"
        assert not errori, errori
        browser.close()


def test_A2_vecchio_flag_senza_evento_mostrato_senza_contraddizioni(e2e):
    """Un contatto del sito con il SOLO flag storico (import precedente agli
    eventi): la scheda lo dice una volta, come spunta storica senza
    riferimento, e non lo trasforma in un consenso."""
    from playwright.sync_api import sync_playwright
    m = e2e
    cid = m["q_crm"]("INSERT INTO contacts (agency_id, display_name, first_name, email, email_normalized, status, "
                     "source) VALUES (1, 'Storico Flag', 'Storico', 'flag@example.invalid', 'flag@example.invalid', "
                     "'active', 'public_stima') RETURNING id")[0][0]
    m["q_crm"]("UPDATE contacts SET marketing_consent = TRUE, marketing_consent_at = '2025-05-04 10:00+00' "
               "WHERE id = %s", (cid,))
    with sync_playwright() as p:
        browser = p.chromium.launch()
        crm = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        _apri(crm, f"{m['url']}/scheda/{cid}", "[data-site-record]")
        testo = crm.inner_text("[data-site-legacy-flag]")
        assert "Spunta marketing storica" in testo and "04/05/2025" in testo and "non autorizza invii" in testo
        assert crm.query_selector("[data-site-no-marketing]") is None
        assert crm.query_selector("[data-consent-legacy]") is None, "detto una volta sola"
        for c in CANALI:
            assert "mai dato" in crm.inner_text(f"[data-consent-channel='{c}']"), c
            assert not _guardia(cid, c).allowed
        _foto(crm, "A5_flag_storico_senza_evento")
        browser.close()
