"""ADM-3 - Rete semplice e continuita' operativa.

PostgreSQL usa-e-getta e sessioni VERE, come ADM-1/ADM-2 (fixture `adm2`).

  01  un'agenzia SOSPESA resta la destinazione delle stime del suo Comune: la
      stima e il lead nascono li', nessun ripiego all'agenzia di sistema; il
      bridge ripetuto non duplica contatto e lead
  02  un'agenzia ARCHIVIATA non e' instradabile: ripiego dichiarato
  03  sospendere / archiviare / riattivare non cancella ne' sposta nulla
  04  calendario personale: inutilizzabile con operatore disabilitato o agenzia
      sospesa, di nuovo utilizzabile alla riattivazione (connessione intatta)
  05  prenotazione pubblica: link non prenotabile con operatore disabilitato o
      agenzia sospesa, di nuovo prenotabile alla riattivazione
  06  follow-up automatici in pausa: la scansione salta le agenzie sospese
  07  UI (Chromium, smartphone e desktop): nessun «Verifica conseguenze»;
      Sospendi / Archivia / Riattiva agenzia, Disabilita / Riattiva operatore,
      Sospendi / Riattiva assegnazione, ciascuno con una conferma
"""
from __future__ import annotations

import threading
import time
import uuid

import pytest

from tests.test_adm1_super_admin_postgres import _impronta, adm1  # noqa: F401
from tests.test_adm2_impact_postgres import COMUNE, adm2  # noqa: F401
from tests.test_c1_lettura_territoriale_postgres import rete  # noqa: F401
from tests.test_censimento_3_backend_postgres import DSN, _q, completo, mondo  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


def _stato(m, sa, verso):
    r = sa.patch(f"/api/platform/agencies/{m['quattro']}", json={"status": verso})
    assert r.status_code == 200, r.text


def _decisione(m, comune=COMUNE):
    from network_routing.service import resolve_agency_for_public_stima
    from operator_auth.enums import DEFAULT_AGENCY_SLUG
    conn = m["psycopg2"].connect(m["dsn"])
    try:
        from psycopg2.extras import RealDictCursor
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            return resolve_agency_for_public_stima(cur, comune=comune, fallback_slug=DEFAULT_AGENCY_SLUG)
    finally:
        conn.close()


def test_01_l_agenzia_sospesa_tiene_i_suoi_lead(adm2):
    from core import service as core_service
    from network_routing.service import RoutingDecision, system_context_for_persisted_public_stima
    m = adm2
    sa, q = m["accedi"]("super"), m["quattro"]
    assert _decisione(m).source == RoutingDecision.TERRITORY
    _stato(m, sa, "suspended")
    try:
        d = _decisione(m)
        assert (d.source, d.agency_id, d.matched_value) == (RoutingDecision.TERRITORY_SUSPENDED, q, COMUNE)
        assert d.agency_id != m["sistema"]
        # la stima nasce con l'agenzia competente, come farebbe il funnel pubblico
        email = f"adm3.{uuid.uuid4().hex[:6]}@x.test"
        stima = _q(m, "INSERT INTO stime (agency_id, comune, via, mq, nome, cognome, email, telefono) "
                      "VALUES (%s, %s, 'Via Lead', 70, 'Lia', 'Sospesa', %s, '3331112223') RETURNING id",
                   (d.agency_id, COMUNE, email))[0][0]
        conn = m["psycopg2"].connect(m["dsn"])
        try:
            from psycopg2.extras import RealDictCursor
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                ctx = system_context_for_persisted_public_stima(cur, stima_id=stima)
        finally:
            conn.close()
        assert ctx.require_agency() == q
        esiti = [core_service.bridge_public_stima(stima, first_name="Lia", last_name="Sospesa", email=email,
                                                  phone="3331112223", marketing_consent=False,
                                                  marketing_consent_at=None, system_ctx=ctx) for _ in range(2)]
        assert esiti[0]["contact_id"] and esiti[0]["lead_id"], esiti
        assert esiti[1]["contact_id"] == esiti[0]["contact_id"] and esiti[1]["lead_id"] == esiti[0]["lead_id"]
        assert _q(m, "SELECT agency_id FROM contacts WHERE id = %s", (esiti[0]["contact_id"],))[0][0] == q
        assert _q(m, "SELECT agency_id FROM leads WHERE id = %s", (esiti[0]["lead_id"],))[0][0] == q
        assert _q(m, "SELECT count(*) FROM contacts WHERE email = %s", (email,))[0][0] == 1
        assert _q(m, "SELECT count(*) FROM stime WHERE email = %s AND agency_id <> %s", (email, q))[0][0] == 0
        # nessun nuovo accesso: il Supervisore dell'agenzia sospesa non entra
        from fastapi.testclient import TestClient
        c = TestClient(m["app"], base_url="https://testserver")
        r = c.post("/api/operator-auth/login", json={"email": "adm1.quattro@x.test", "password": "Adm1-prova-locale!"})
        assert r.status_code == 401
    finally:
        _stato(m, sa, "active")


def test_02_l_agenzia_archiviata_non_e_instradabile(adm2):
    from network_routing.service import RoutingDecision
    m = adm2
    sa = m["accedi"]("super")
    _stato(m, sa, "suspended")
    _stato(m, sa, "archived")
    try:
        d = _decisione(m)
        assert d.source == RoutingDecision.FALLBACK and d.agency_id == m["sistema"]
    finally:
        _stato(m, sa, "active")


def test_03_nessuna_cancellazione_ne_trasferimento(adm2):
    m = adm2
    sa, q = m["accedi"]("super"), m["quattro"]
    prima = _impronta(m)
    proprieta = {t: _q(m, f"SELECT id, agency_id FROM {t} WHERE agency_id = %s ORDER BY id", (q,))
                 for t in ("properties", "contacts", "stime", "agency_territory_assignments", "agency_memberships")}
    for verso in ("suspended", "archived", "active"):
        _stato(m, sa, verso)
    uid = m["persone"]["quattro"]
    for verso in ("disabled", "active"):
        assert sa.patch(f"/api/platform/operators/{uid}", json={"status": verso}).status_code == 200
    assert _impronta(m) == prima
    for t, righe in proprieta.items():
        assert _q(m, f"SELECT id, agency_id FROM {t} WHERE agency_id = %s ORDER BY id", (q,)) == righe, t
    assert _q(m, "SELECT count(*) FROM contacts WHERE assigned_agent_id = %s AND deleted_at IS NULL", (uid,))[0][0] == 2


def test_04_calendario_personale_in_pausa(adm2):
    from calendar_sync import repository as cal
    from calendar_sync.crypto import Ciphertext
    m = adm2
    sa, q, uid = m["accedi"]("super"), m["quattro"], m["persone"]["quattro"]
    conn = m["psycopg2"].connect(m["dsn"])
    conn.autocommit = True
    from psycopg2.extras import RealDictCursor
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        riga = cal.upsert_connection(cur, agency_id=q, user_id=uid, provider_subject="adm3-subject",
                                     refresh_token=Ciphertext(value=b"cifrato-di-prova", key_id="adm3"))
        usabile = lambda: cal.connection_for_user(cur, q, uid)["usable"]  # noqa: E731
        assert usabile() is True
        assert sa.patch(f"/api/platform/operators/{uid}", json={"status": "disabled"}).status_code == 200
        assert usabile() is False and cal.usable_connection_for_user(cur, q, uid) is None
        assert sa.patch(f"/api/platform/operators/{uid}", json={"status": "active"}).status_code == 200
        assert usabile() is True
        _stato(m, sa, "suspended")
        assert usabile() is False
        _stato(m, sa, "active")
        assert usabile() is True
        dopo = cal.connection_for_user(cur, q, uid)
        assert dopo["id"] == riga["id"] and dopo["status"] == "connected"     # la connessione e' intatta
    finally:
        cur.close()
        conn.close()


def test_05_prenotazione_pubblica_in_pausa(adm2):
    from public_booking import repository as pb, security
    from public_booking.service import LinkNotBookable, _resolve_bookable_link
    m = adm2
    sa, q, uid = m["accedi"]("super"), m["quattro"], m["persone"]["quattro"]
    token = security.generate_token()
    conn = m["psycopg2"].connect(m["dsn"])
    conn.autocommit = True
    from psycopg2.extras import RealDictCursor
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        pb.create_link(cur, agency_id=q, assigned_user_id=uid, token_hash=security.hash_token(token), label="ADM3",
                       appointment_type="seller_meeting", duration_minutes=30, buffer_before_minutes=0,
                       buffer_after_minutes=0, created_by_user_id=uid, expires_at=None)
    conn.close()
    assert _resolve_bookable_link(token)["agency_id"] == q
    assert sa.patch(f"/api/platform/operators/{uid}", json={"status": "disabled"}).status_code == 200
    with pytest.raises(LinkNotBookable):
        _resolve_bookable_link(token)
    assert sa.patch(f"/api/platform/operators/{uid}", json={"status": "active"}).status_code == 200
    assert _resolve_bookable_link(token)["agency_id"] == q
    _stato(m, sa, "suspended")
    try:
        with pytest.raises(LinkNotBookable):
            _resolve_bookable_link(token)
    finally:
        _stato(m, sa, "active")
    assert _resolve_bookable_link(token)["agency_id"] == q
    assert _q(m, "SELECT status FROM public_booking_links WHERE agency_id = %s AND label = 'ADM3'", (q,))[0][0] == "active"


def test_06_follow_up_automatici_in_pausa(adm2, monkeypatch):
    from followup import service as followup_service
    m = adm2
    sa, q = m["accedi"]("super"), m["quattro"]
    visitate = []
    monkeypatch.setattr(followup_service, "run_temporal_escalation_scan_for_agency",
                        lambda agency_id, limit=100: visitate.append(agency_id) or
                        {"escalated": 0, "skipped": 0, "failed": 0})
    import followup.database as fdb
    monkeypatch.setattr(fdb, "get_connection", lambda: m["psycopg2"].connect(m["dsn"]))
    followup_service.run_temporal_escalation_scan_for_all_agencies()
    assert q in visitate
    _stato(m, sa, "suspended")
    try:
        visitate.clear()
        followup_service.run_temporal_escalation_scan_for_all_agencies()
        assert q not in visitate
    finally:
        _stato(m, sa, "active")


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

@pytest.fixture
def browser_server(adm2):
    uvicorn = pytest.importorskip("uvicorn")
    pytest.importorskip("playwright.sync_api")
    from fastapi.staticfiles import StaticFiles
    from operator_auth.enums import COOKIE_NAME
    from tests.test_edifici_1_browser_postgres import SHELL, _porta_libera

    m = adm2
    app = m["app"]
    app.mount("/os", StaticFiles(directory=str(SHELL), html=True), name="os-shell")
    token = m["accedi"]("super").cookies.get(COOKIE_NAME)
    porta = _porta_libera()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="warning"))
    filo = threading.Thread(target=srv.run, daemon=True)
    filo.start()
    limite = time.monotonic() + 20
    while not srv.started and time.monotonic() < limite:
        time.sleep(0.05)
    assert srv.started
    try:
        yield {**m, "url": f"http://127.0.0.1:{porta}", "token": token, "cookie": COOKIE_NAME}
    finally:
        srv.should_exit = True
        filo.join(timeout=10)


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_07_rete_semplice_in_chromium(browser_server, larghezza):
    from playwright.sync_api import sync_playwright
    from tests.test_eliminazione_edificio_1_browser_postgres import _sfora
    m = browser_server
    q, uid = m["quattro"], m["persone"]["quattro"]
    prima = _impronta(m)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": larghezza, "height": 860})
        ctx.add_cookies([{"name": m["cookie"], "value": m["token"], "url": m["url"]}])
        page = ctx.new_page()
        errori = []
        page.on("pageerror", lambda e: errori.append(str(e)))

        def conferma(atteso):
            page.wait_for_selector("dialog.modal [data-conferma='si']")
            assert atteso in page.inner_text("dialog.modal")
            page.click("dialog.modal [data-conferma='si']")
            page.wait_for_selector("dialog.modal", state="detached")

        page.goto(f"{m['url']}/os/?n={uuid.uuid4().hex[:8]}#/rete/agenzie/{q}")
        page.wait_for_selector("[data-stato-agenzia='suspended']")
        assert "Verifica conseguenze" not in page.inner_text("body")
        assert _sfora(page)["scroll"] <= larghezza + 1
        page.click("[data-stato-agenzia='suspended']")
        conferma("Nessun dato viene cancellato")
        page.wait_for_selector("[data-stato-agenzia='archived']")
        assert _q(m, "SELECT status FROM agencies WHERE id = %s", (q,))[0][0] == "suspended"
        page.click("[data-stato-agenzia='archived']")
        conferma("Nessun dato viene cancellato")
        page.wait_for_selector("[data-stato-agenzia='archived']", state="detached")
        assert _q(m, "SELECT status FROM agencies WHERE id = %s", (q,))[0][0] == "archived"
        # ELIMINA AGENZIA: eliminata, si torna all'elenco; il ripristino e' nella scheda
        page.wait_for_selector("#agenzie-lista")
        page.goto(f"{m['url']}/os/?n={uuid.uuid4().hex[:8]}#/rete/agenzie/{q}")
        page.click("[data-stato-agenzia='active']")
        conferma("nuovo accesso")
        page.wait_for_selector("[data-stato-agenzia='suspended']")
        assert _q(m, "SELECT status FROM agencies WHERE id = %s", (q,))[0][0] == "active"

        page.click("[data-tab='operatori']")
        page.wait_for_selector(f"[data-azione='disabilita'][data-operatore='{uid}']")
        assert "Verifica conseguenze" not in page.inner_text("body")
        page.click(f"[data-azione='disabilita'][data-operatore='{uid}']")
        conferma("nessun dato viene cancellato")
        page.wait_for_selector(f"[data-azione='riattiva'][data-operatore='{uid}']")
        assert _q(m, "SELECT status FROM operator_users WHERE id = %s", (uid,))[0][0] == "disabled"
        page.click(f"[data-azione='riattiva'][data-operatore='{uid}']")
        conferma("nuovo accesso")
        page.wait_for_selector(f"[data-azione='disabilita'][data-operatore='{uid}']")

        page.click("[data-tab='territori']")
        sel = f"[data-assegnazione='{m['assegnazione']}']"
        page.wait_for_selector(f"{sel}[data-verso='suspended']")
        assert "Verifica conseguenze" not in page.inner_text("body")
        page.click(f"{sel}[data-verso='suspended']")
        conferma("agenzia di sistema")
        page.wait_for_selector(f"{sel}[data-verso='active']")
        page.click(f"{sel}[data-verso='active']")
        conferma("torna a presidiare")
        page.wait_for_selector(f"{sel}[data-verso='suspended']")

        page.goto(f"{m['url']}/os/?n={uuid.uuid4().hex[:8]}#/rete/agenzie/{m['sistema']}")
        page.wait_for_selector("#pa-stato")
        assert page.locator("[data-stato-agenzia='suspended']").count() == 0
        assert "Agenzia di sistema" in page.inner_text("#pa-stato")
        page.goto(f"{m['url']}/os/?n={uuid.uuid4().hex[:8]}#/rete/territori/{m['territorio_quattro']}")
        page.wait_for_selector(f"[data-alias='{m['alias']}']")
        assert "Verifica conseguenze" not in page.inner_text("body")
        assert errori == [], errori
        browser.close()
    assert _impronta(m) == prima
