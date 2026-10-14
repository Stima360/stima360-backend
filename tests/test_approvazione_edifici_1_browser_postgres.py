"""APPROVAZIONE-EDIFICI-1 - campagna mirata in BROWSER (Chromium) con backend e
PostgreSQL VERI, dati fittizi.

Il caso di partenza: il Super Admin vedeva «In attesa di approvazione» nella
scheda dell'edificio (o nell'elenco) senza trovare «Approva». Il comando vive
nel pannello del Super Admin, Cestino › Richieste edifici › Dettaglio; la scheda
ora lo indica e «Approva» chiede una conferma esplicita.

La Shell vera servita da uvicorn con i router veri (Immobili, Cestino, Platform)
sul database usa-e-getta. Sostituita SOLO l'autenticazione: le sessioni sono
  agente        operatore ordinario dell'agenzia 1;
  titolare_b    titolare dell'agenzia 2 (altra agenzia);
  super         il Super Admin generale DESIGNATO, entrato nell'agenzia 1
                (`acting`), come il titolare sul CRM TEST;
  simile        un amministratore di piattaforma con email simile a quella del
                Super Admin, NON designato.
Il `require_platform_admin` di prova ammette `super` e `simile` (platform
admin) e nega gli altri, come quello vero; la designazione e il ruolo riletto
nel database li verifica il codice vero (`platform_admin/super_admin.py`).

A 390 px e 1280 px, in sequenza (punti 1-10 della campagna):
  1  l'agente richiede l'eliminazione dell'edificio E1 dalla scheda;
     nella scheda vede «In attesa di approvazione», il collegamento
     informativo e nessun «Approva»;
  2  il Super Admin apre la scheda di E1: «In attesa di approvazione», nessun
     «Approva» ma il collegamento «Apri Cestino › Richieste edifici»; lo segue
     e vede la richiesta con edificio, agenzia, richiedente e motivazione;
  3  «Dettaglio» -> «Approva» mostra la conferma e NON scrive; «Annulla» la
     chiude; di nuovo «Approva» -> «Sì, approva» scrive;
  4  edificio e unita' sono nel Cestino (deleted_at), le righe esistono ancora;
  5  l'agente richiede l'eliminazione di E2; il Super Admin la rifiuta con
     motivo: E2 e le unita' restano intatte;
  6  l'operatore ordinario: niente «Approva» (elenco di sola lettura) e 403
     sulle rotte riservate;
  7  `simile`: solo l'avviso al posto del pannello, nessun comando, 403
     SUPER_ADMIN_REQUIRED sull'approvazione, rifiuto registrato nell'audit;
  8  altra agenzia: il titolare B vede solo le proprie richieste e non puo'
     ritirare quella dell'agenzia 1; il Super Admin le vede tutte;
  9  nessuna cancellazione fisica: stesso numero di righe prima e dopo;
 10  nessuno scorrimento orizzontale, nessun errore di pagina.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import _edificio, _q, _unita  # noqa: F401
from tests.test_edifici_1_browser_postgres import DSN, SHELL, _porta_libera, completo, mondo  # noqa: F401
from tests.test_eliminazione_edificio_1_browser_postgres import _sfora, _vai

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

SUPER_EMAIL = "info@stima360.test"
SIMILE_EMAIL = "info.stima360@stima360.test"


def _utente(m, email, nome):
    riga = _q(m, "SELECT id FROM operator_users WHERE email_normalized = %s", (email,))
    if riga:
        _q(m, "UPDATE operator_users SET is_platform_admin = TRUE, status = 'active' WHERE id = %s", (riga[0][0],))
        return riga[0][0]
    return _q(m, "INSERT INTO operator_users (email, email_normalized, password_hash, first_name, is_platform_admin) "
                 "VALUES (%s, %s, 'pbkdf2_sha256$1$x$y', %s, TRUE) RETURNING id", (email, email, nome))[0][0]


@pytest.fixture
def server(mondo, monkeypatch):
    uvicorn = pytest.importorskip("uvicorn")
    pytest.importorskip("playwright.sync_api")
    from fastapi import FastAPI, HTTPException
    from fastapi.staticfiles import StaticFiles
    import platform_admin.database as pdb
    from core.router import router as core_router
    from operator_auth.context import OperatorContext
    from operator_auth.dependencies import (legacy_basic_agency_context, require_authenticated_operator,
                                            require_operator)
    from platform_admin.dependencies import require_platform_admin
    from platform_admin.router import router as piattaforma
    from platform_admin.super_admin import SUPER_ADMIN_ENV
    from property.router import router as immobili
    from trash.router import router as cestino

    m = mondo
    monkeypatch.setattr(pdb, "get_connection", lambda: m["psycopg2"].connect(m["dsn"]))
    superadmin = _utente(m, SUPER_EMAIL, "Super")
    simile = _utente(m, SIMILE_EMAIL, "Simile")
    monkeypatch.setenv(SUPER_ADMIN_ENV, str(superadmin))     # designato: l'id, mai l'email
    scadenza = "2030-01-01T00:00:00Z"
    sessioni = {
        "agente": {"user_id": m["ids"]["agent_a"], "agency_id": 1, "agency_name": "Agenzia Uno", "role": "agent",
                   "is_platform_admin": False, "expires_at": scadenza},
        "titolare_b": {"user_id": m["ids"]["owner_b"], "agency_id": 2, "agency_name": "Agenzia Due",
                       "role": "agency_owner", "is_platform_admin": False, "expires_at": scadenza},
        "super": {"user_id": superadmin, "agency_id": 1, "agency_name": "Agenzia Uno", "role": "agency_owner",
                  "is_platform_admin": True, "expires_at": scadenza,
                  "acting": {"agency_id": 1, "agency_name": "Agenzia Uno", "entered_at": "2026-10-14T08:00:00Z"},
                  "home_agency_id": None, "home_agency_name": None},
        "simile": {"user_id": simile, "agency_id": 1, "agency_name": "Agenzia Uno", "role": "agency_owner",
                   "is_platform_admin": True, "expires_at": scadenza,
                   "acting": {"agency_id": 1, "agency_name": "Agenzia Uno", "entered_at": "2026-10-14T08:00:00Z"},
                   "home_agency_id": None, "home_agency_name": None},
    }
    stato = {"chi": "agente"}

    def tenant():
        chi = stato["chi"]
        if chi in ("agente", "titolare_b"):
            m["stato"]["chi"] = {"agente": "agent_a", "titolare_b": "owner_b"}[chi]
            return m["ctx"]()
        s = sessioni[chi]
        return OperatorContext(user_id=s["user_id"], agency_id=s["agency_id"], role=s["role"], is_platform_admin=True,
                               session_id=None, auth_channel="operator_session")

    def piattaforma_ctx():
        s = sessioni[stato["chi"]]
        if not s["is_platform_admin"]:
            raise HTTPException(status_code=403, detail="riservato alla piattaforma")
        return OperatorContext(user_id=s["user_id"], agency_id=s["agency_id"], role=s["role"], is_platform_admin=True,
                               session_id=None, auth_channel="operator_session")

    app = FastAPI()

    @app.get("/api/operator-auth/me")
    def me():
        return sessioni[stato["chi"]]

    @app.get("/api/platform/me")
    def platform_me():
        c = piattaforma_ctx()
        return {"user_id": c.user_id, "is_platform_admin": True, "agency_id": c.agency_id,
                "session_expires_at": scadenza, "acting_agency_id": c.agency_id, "acting_entered_at": None}

    for r in (immobili, core_router, cestino, piattaforma):
        app.include_router(r)
    for dipendenza in (legacy_basic_agency_context, require_operator, require_authenticated_operator):
        app.dependency_overrides[dipendenza] = tenant
    app.dependency_overrides[require_platform_admin] = piattaforma_ctx
    app.mount("/os", StaticFiles(directory=str(SHELL), html=True), name="os-shell")
    porta = _porta_libera()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="warning"))
    filo = threading.Thread(target=srv.run, daemon=True)
    filo.start()
    limite = time.monotonic() + 20
    while not srv.started and time.monotonic() < limite:
        time.sleep(0.05)
    assert srv.started, "uvicorn non partito"
    try:
        yield {"url": f"http://127.0.0.1:{porta}", "come": stato, "superadmin": superadmin, "simile": simile, **m}
    finally:
        srv.should_exit = True
        filo.join(timeout=10)
        _q(m, "UPDATE buildings SET deleted_at = NULL, deleted_by_user_id = NULL, deleted_reason = NULL "
              "WHERE deleted_at IS NOT NULL")


def _http(m, chi, metodo, percorso, corpo=None):
    """Chiamata diretta all'API del server di prova nei panni di `chi`."""
    m["come"]["chi"] = chi
    dati = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(m["url"] + percorso, data=dati, method=metodo,
                                 headers={"Content-Type": "application/json"} if dati else {})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def _righe(m, e_ids, u_ids):
    return (_q(m, "SELECT count(*) FROM buildings WHERE id = ANY(%s)", (e_ids,))[0][0],
            _q(m, "SELECT count(*) FROM properties WHERE id = ANY(%s)", (u_ids,))[0][0])


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_c01_c10_approvazione_in_chromium(server, larghezza):
    from playwright.sync_api import sync_playwright
    m = server
    terr = {"region": "Abruzzo", "province": "TE", "city": "Alba Adriatica", "microzone": "Nord"}
    e1 = _edificio(m, **terr, address=f"Via Prova Uno {uuid.uuid4().hex[:5]}", civic_number="1", name="Prova casa uno")
    e2 = _edificio(m, **terr, address=f"Via Prova Due {uuid.uuid4().hex[:5]}", civic_number="2", name="Prova casa due")
    u1 = _unita(m, building_id=e1["id"], floor="1", internal_number="1")
    u2 = _unita(m, building_id=e1["id"], floor="2", internal_number="2")
    u3 = _unita(m, building_id=e2["id"], floor="1", internal_number="1")
    e_b = _edificio(m, chi="owner_b", city="Tortoreto", region="Abruzzo", province="TE", microzone="Alto",
                    address=f"Via Prova B {uuid.uuid4().hex[:5]}", civic_number="9", name="Prova casa B")
    edifici = [e1["id"], e2["id"], e_b["id"]]
    unita = [u1["id"], u2["id"], u3["id"]]
    righe_prima = _righe(m, edifici, unita)
    agenzie = dict(_q(m, "SELECT id, name FROM agencies WHERE id IN (1, 2)"))   # i nomi veri, dal database
    cartella = Path(os.environ.get("APPROVAZIONE1_SHOTS") or (Path("/tmp") / "approvazione1_shots"))
    cartella.mkdir(parents=True, exist_ok=True)
    misure = []

    def cestinati():
        return dict(_q(m, "SELECT id, deleted_at IS NOT NULL FROM properties WHERE id = ANY(%s)", (unita,)))

    def stato_richiesta(rid):
        return _q(m, "SELECT status FROM building_deletion_requests WHERE id = %s", (rid,))[0][0]

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": larghezza, "height": 860})
        errori = []
        page.on("pageerror", lambda x: errori.append(str(x)))

        # --- 1. l'operatore ordinario richiede; nella scheda nessun «Approva» ----------
        m["come"]["chi"] = "agente"
        _vai(page, m["url"], f"#/edifici/{e1['id']}")
        page.wait_for_selector("[data-deletion-open]")
        page.click("[data-deletion-open]")
        page.select_option("[data-deletion-reason]", "created_by_mistake")
        page.fill("[data-deletion-note]", "Edificio di prova, creato per errore")
        page.click("[data-deletion-confirm]")
        page.wait_for_selector("[data-deletion-status='pending']")
        pannello = page.inner_text("[data-deletion-panel]")
        assert "In attesa di approvazione" in pannello, pannello
        assert page.locator("[data-deletion-decide='operator'] [data-deletion-decide-link]").count() == 1
        assert page.locator("[data-deletion-approve], [data-deletion-decide='super-admin']").count() == 0
        rid1 = _q(m, "SELECT id FROM building_deletion_requests WHERE building_id = %s AND status = 'pending'",
                  (e1["id"],))[0][0]
        assert cestinati() == {u1["id"]: False, u2["id"]: False, u3["id"]: False}
        misure.append(_sfora(page))

        # --- 2. il Super Admin: dalla scheda al pannello delle richieste -----------------
        m["come"]["chi"] = "super"
        _vai(page, m["url"], f"#/edifici/{e1['id']}")
        page.wait_for_selector("[data-deletion-status='pending']")
        assert page.locator("[data-deletion-panel] [data-deletion-approve]").count() == 0
        page.wait_for_selector("[data-deletion-decide='super-admin'] [data-deletion-decide-link]")
        assert "Apri Cestino › Richieste edifici" in page.inner_text("[data-deletion-decide='super-admin']")
        page.screenshot(path=str(cartella / f"approvazione_scheda_{larghezza}.png"), full_page=True)
        misure.append(_sfora(page))
        page.click("[data-deletion-decide='super-admin'] [data-deletion-decide-link]")
        page.wait_for_selector("[data-deletion-admin]")
        assert page.evaluate("location.hash") == "#/cestino/richieste-edifici"
        page.wait_for_selector(f"[data-deletion-request='{rid1}']")
        carta = page.inner_text(f"[data-deletion-request='{rid1}']")
        for atteso in ("Prova casa uno", agenzie[1], "In attesa di approvazione", "Richiesta da",
                       "Creato per errore", "Edificio di prova, creato per errore", "2 unità",
                       "Apri il dettaglio per approvare o rifiutare."):
            assert atteso in carta, (atteso, carta)
        nome_richiedente = _q(m, "SELECT first_name FROM operator_users WHERE id = %s", (m["ids"]["agent_a"],))[0][0]
        assert nome_richiedente in carta, (nome_richiedente, carta)
        page.screenshot(path=str(cartella / f"approvazione_elenco_{larghezza}.png"), full_page=True)
        misure.append(_sfora(page))

        # --- 3. dettaglio -> «Approva» = conferma (nessuna scrittura) -> «Sì, approva» ---
        page.click(f"[data-deletion-request='{rid1}'] [data-deletion-detail-open]")
        page.wait_for_selector(f"[data-deletion-request='{rid1}'] [data-deletion-approve]")
        assert page.is_hidden(f"[data-deletion-request='{rid1}'] [data-deletion-approve-confirm]")
        page.click(f"[data-deletion-request='{rid1}'] [data-deletion-approve]")
        page.wait_for_selector(f"[data-deletion-request='{rid1}'] [data-deletion-approve-confirm]:not([hidden])")
        conferma = page.inner_text(f"[data-deletion-request='{rid1}'] [data-deletion-approve-confirm]")
        assert "Confermi l’approvazione?" in conferma and "Prova casa uno" in conferma and "2 unità attive" in conferma, conferma
        assert page.is_disabled(f"[data-deletion-request='{rid1}'] [data-deletion-approve]")
        assert stato_richiesta(rid1) == "pending"                      # la conferma da sola non scrive
        assert cestinati() == {u1["id"]: False, u2["id"]: False, u3["id"]: False}
        page.screenshot(path=str(cartella / f"approvazione_conferma_{larghezza}.png"), full_page=True)
        misure.append(_sfora(page))
        page.click(f"[data-deletion-request='{rid1}'] [data-deletion-approve-no]")
        page.wait_for_selector(f"[data-deletion-request='{rid1}'] [data-deletion-approve-confirm]", state="hidden")
        assert page.is_enabled(f"[data-deletion-request='{rid1}'] [data-deletion-approve]")
        assert stato_richiesta(rid1) == "pending"
        page.click(f"[data-deletion-request='{rid1}'] [data-deletion-approve]")
        page.click(f"[data-deletion-request='{rid1}'] [data-deletion-approve-yes]")
        page.wait_for_selector(f"[data-building-group='{e1['id']}']")

        # --- 4. edificio e unita' nel Cestino, righe ancora presenti ---------------------
        assert stato_richiesta(rid1) == "approved"
        assert cestinati() == {u1["id"]: True, u2["id"]: True, u3["id"]: False}
        assert _q(m, "SELECT deleted_at IS NOT NULL FROM buildings WHERE id = %s", (e1["id"],))[0][0] is True
        assert _righe(m, edifici, unita) == righe_prima

        # --- 5. seconda richiesta (E2) e rifiuto: tutto resta -----------------------------
        stato, corpo = _http(m, "agente", "POST", f"/api/property/buildings/{e2['id']}/deletion-request",
                             {"reason_code": "duplicate", "note": "Doppione di prova"})
        assert stato == 201, corpo
        rid2 = corpo["request"]["id"] if isinstance(corpo.get("request"), dict) else corpo["id"]
        m["come"]["chi"] = "super"
        _vai(page, m["url"], "#/cestino/richieste-edifici")
        page.wait_for_selector(f"[data-deletion-request='{rid2}']")
        page.click(f"[data-deletion-request='{rid2}'] [data-deletion-detail-open]")
        page.wait_for_selector(f"[data-deletion-request='{rid2}'] [data-deletion-reject]")
        page.click(f"[data-deletion-request='{rid2}'] [data-deletion-reject]")
        assert "Scrivi il motivo del rifiuto." in page.inner_text(f"[data-deletion-request='{rid2}'] [data-trash-card-error]")
        assert stato_richiesta(rid2) == "pending"
        page.fill(f"[data-deletion-request='{rid2}'] [data-deletion-decision-note]", "Serve ancora: non eliminare")
        page.click(f"[data-deletion-request='{rid2}'] [data-deletion-reject]")
        page.wait_for_selector(f"[data-deletion-request='{rid2}']", state="detached")   # esce dal filtro «Da autorizzare»
        assert stato_richiesta(rid2) == "rejected"
        assert cestinati()[u3["id"]] is False
        assert _q(m, "SELECT deleted_at FROM buildings WHERE id = %s", (e2["id"],))[0][0] is None

        # --- 6. l'operatore ordinario non approva ------------------------------------------
        stato, corpo = _http(m, "agente", "POST", f"/api/platform/building-deletion-requests/{rid2}/approve",
                             {"fingerprint": "0" * 64, "note": ""})
        assert stato == 403, (stato, corpo)
        _vai(page, m["url"], "#/cestino/richieste-edifici")
        page.wait_for_selector("[data-building-requests-list]")
        page.wait_for_selector("[data-building-requests-list] [data-building-request], [data-building-requests-list] [data-trash-empty]")
        assert page.locator("[data-deletion-admin], [data-deletion-approve], [data-deletion-detail-open]").count() == 0
        assert "decide il Super Admin" in page.inner_text("[data-building-requests-host]")
        misure.append(_sfora(page))

        # --- 7. email simile, non designato: avviso, nessun comando, 403 registrato -------
        stato, corpo = _http(m, "simile", "POST", f"/api/platform/building-deletion-requests/{rid2}/approve",
                             {"fingerprint": "0" * 64, "note": ""})
        assert stato == 403 and corpo.get("code") == "SUPER_ADMIN_REQUIRED", (stato, corpo)
        assert _q(m, "SELECT count(*) FROM platform_audit_log WHERE actor_user_id = %s AND result = 'denied' "
                     "AND action LIKE 'platform.building_deletion.%%'", (m["simile"],))[0][0] >= 1
        m["come"]["chi"] = "simile"
        _vai(page, m["url"], "#/cestino/richieste-edifici")
        page.wait_for_selector("[data-deletion-forbidden]")
        assert "riservata al Super Admin generale designato" in page.inner_text("[data-deletion-forbidden]")
        assert page.locator("[data-deletion-request], [data-deletion-approve], [data-deletion-detail-open]").count() == 0
        page.screenshot(path=str(cartella / f"approvazione_simile_{larghezza}.png"), full_page=True)
        misure.append(_sfora(page))

        # --- 8. altra agenzia -------------------------------------------------------------
        stato, corpo = _http(m, "titolare_b", "POST", f"/api/property/buildings/{e_b['id']}/deletion-request",
                             {"reason_code": "duplicate"})
        assert stato == 201, corpo
        rid_b = corpo["request"]["id"] if isinstance(corpo.get("request"), dict) else corpo["id"]
        stato, corpo = _http(m, "titolare_b", "GET", "/api/trash/building-requests?status=all&limit=50&offset=0")
        viste = {r["id"]: r["agency_id"] for r in corpo["items"]}
        assert stato == 200 and viste.get(rid_b) == 2 and set(viste.values()) == {2}, corpo   # solo la propria agenzia
        assert rid1 not in viste and rid2 not in viste
        stato, corpo = _http(m, "titolare_b", "POST", f"/api/property/building-deletion-requests/{rid2}/withdraw", {})
        assert stato == 404, (stato, corpo)
        stato, corpo = _http(m, "titolare_b", "GET", f"/api/property/buildings/{e2['id']}/deletion-request")
        assert stato == 404, (stato, corpo)
        stato, corpo = _http(m, "super", "GET", "/api/platform/building-deletion-requests?status=all&limit=50")
        assert stato == 200 and {rid1, rid2, rid_b} <= {r["id"] for r in corpo["items"]}, corpo
        m["come"]["chi"] = "super"
        _vai(page, m["url"], "#/cestino/richieste-edifici")
        page.wait_for_selector(f"[data-deletion-request='{rid_b}']")
        assert agenzie[2] in page.inner_text(f"[data-deletion-request='{rid_b}']")

        # --- 9. nessuna cancellazione fisica -------------------------------------------------
        assert _righe(m, edifici, unita) == righe_prima
        assert _q(m, "SELECT count(*) FROM building_deletion_requests WHERE id = ANY(%s)", ([rid1, rid2, rid_b],))[0][0] == 3

        # --- 10. pagina pulita --------------------------------------------------------------
        assert errori == [], errori
        for mis in misure:
            assert mis["scroll"] <= mis["inner"] + 1 and mis["fuori"] == [], mis
        browser.close()
