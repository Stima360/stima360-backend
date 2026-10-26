"""ADM-7-E - «Elimina dalla Rete» per agenzie e account, senza cancellare nulla.

«Elimina dalla Rete» riusa gli stati esistenti, nessun secondo stato:
  agenzia  -> `archived` (da attiva: sospensione e poi archiviazione, ADM-1)
  account  -> `disabled` (revoca di tutte le sessioni, ADM-1)
Il ripristino e' `active`. PostgreSQL usa-e-getta e sessioni vere (`adm2`).

  01  agenzia eliminata: accessi negati, sessioni dei membri revocate, dati e
      proprietari conservati, ripetere il comando non cambia nulla, registro
  02  account eliminato: sessioni revocate, login negato, assegnazioni e
      storico conservati, idempotente
  03  Super Admin designato: non si elimina e non perde il ruolo
  04  non autorizzati: 403 (altro amministratore, agente), nulla cambia
  05  ripristino: agenzia e account di nuovo attivi con un NUOVO accesso; le
      sessioni di prima restano revocate
  06  interfaccia (Chromium 390/1280): agenzia e operatore eliminati e ripristinati;
      l'agenzia eliminata esce dall'elenco operativo e si ritrova fra le «Eliminate»
  10  ELIMINA OPERATORE: eliminazione logica di un escluso (solo il designato),
      accesso e assegnazioni negati, dati intatti, `info@stima360.it` e il
      designato protetti, ripristino
  11  ELIMINA OPERATORE in Chromium (390/1280): conferma con l'email, sparisce
      dall'elenco, «Mostra eliminati», ripristino
  12  ELIMINA TERRITORIO: eliminazione logica (solo il designato), un territorio
      presidiato non si elimina, nessuna nuova assegnazione/riattivazione,
      filtro «Eliminati», ripristino, dati intatti
  13  ELIMINA TERRITORIO in Chromium (390/1280) e la Rete senza la scheda
      «Eliminazioni edifici»
  09  ELIMINA AGENZIA: un'agenzia eliminata non riceve nuove operazioni
      (territori, trasferimenti, riattivazioni di assegnazioni, operatori);
      l'agenzia di sistema non si elimina
  07  ELIMINAZIONE BLINDATA - account senza agenzia: `GET /operators` li elenca
      (solo il designato), un amministratore di sola piattaforma si elimina
      dalla Rete (sessioni revocate, login negato) e si ripristina
  08  la scheda «Account» della Rete in Chromium (390/1280)
"""
from __future__ import annotations

import pytest

from tests.test_adm1_super_admin_postgres import PASSWORD, _impronta, _token, _viva, adm1  # noqa: F401
from tests.test_adm2_impact_postgres import adm2  # noqa: F401
from tests.test_c1_lettura_territoriale_postgres import rete  # noqa: F401
from tests.test_censimento_3_backend_postgres import DSN, _q, completo, mondo  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


def _proprietari(m, q):
    return {t: _q(m, f"SELECT id, agency_id FROM {t} WHERE agency_id = %s ORDER BY id", (q,))
            for t in ("properties", "contacts", "stime", "leads", "agency_territory_assignments",
                      "agency_memberships")}


def _login(m, chi):
    from fastapi.testclient import TestClient
    email = _q(m, "SELECT email FROM operator_users WHERE id = %s", (m["persone"][chi],))[0][0]
    c = TestClient(m["app"], base_url="https://testserver")
    return c.post("/api/operator-auth/login", json={"email": email, "password": PASSWORD}).status_code


@pytest.fixture
def adm7e(adm2):
    m = adm2
    yield m
    _q(m, "UPDATE agencies SET status = 'active' WHERE id = %s", (m["quattro"],))
    _q(m, "UPDATE operator_users SET deleted_at = NULL, deleted_by_user_id = NULL")  # ELIMINA OPERATORE (113)
    _q(m, "UPDATE operator_users SET status = 'active' WHERE id = ANY(%s)",
       ([m["persone"]["quattro"], m["persone"]["membro"], m["persone"]["altro"]],))


def test_01_agenzia_eliminata_dalla_rete(adm7e):
    m = adm7e
    sa, q = m["accedi"]("super"), m["quattro"]
    sessione_titolare = _token(m["accedi"]("quattro"))
    prima, proprietari = _impronta(m), _proprietari(m, q)

    # il comando dell'interfaccia: sospensione e poi esclusione
    assert sa.patch(f"/api/platform/agencies/{q}", json={"status": "suspended"}).status_code == 200
    r = sa.patch(f"/api/platform/agencies/{q}", json={"status": "archived"})
    assert r.status_code == 200 and r.json()["status"] == "archived", r.text
    # ripetere non cambia nulla
    assert sa.patch(f"/api/platform/agencies/{q}", json={"status": "archived"}).status_code == 200

    assert not _viva(sessione_titolare)
    assert _login(m, "quattro") == 401
    assert _impronta(m) == prima
    assert _proprietari(m, q) == proprietari
    cambi = [r[0]["status_change"] for r in _q(
        m, "SELECT metadata FROM platform_audit_log WHERE action = 'platform.agency.update' "
           "AND target_id = %s AND metadata ? 'status_change' ORDER BY id DESC LIMIT 2", (str(q),))]
    assert cambi == [{"from": "suspended", "to": "archived"}, {"from": "active", "to": "suspended"}]
    # resta nell'elenco, archiviata, e si consulta in sola lettura
    assert any(a["id"] == q and a["status"] == "archived" for a in sa.get("/api/platform/agencies").json())
    entra = sa.post(f"/api/platform/agencies/{q}/enter")
    assert entra.status_code == 200 and entra.json()["read_only"] is True
    assert sa.post("/api/platform/agency-context/exit").status_code == 204


def test_02_account_eliminato_dalla_rete(adm7e):
    m = adm7e
    sa, uid = m["accedi"]("super"), m["persone"]["quattro"]
    sessioni = [_token(m["accedi"]("quattro")) for _ in range(2)]
    assegnati = _q(m, "SELECT id FROM contacts WHERE assigned_agent_id = %s ORDER BY id", (uid,))
    membership = _q(m, "SELECT id, agency_id, role, status FROM agency_memberships "
                       "WHERE operator_user_id = %s ORDER BY id", (uid,))
    prima = _impronta(m)

    r = sa.patch(f"/api/platform/operators/{uid}", json={"status": "disabled"})
    assert r.status_code == 200 and r.json()["status"] == "disabled", r.text
    assert sa.patch(f"/api/platform/operators/{uid}", json={"status": "disabled"}).status_code == 200

    assert not any(_viva(s) for s in sessioni)
    assert _login(m, "quattro") == 401
    assert _q(m, "SELECT id FROM contacts WHERE assigned_agent_id = %s ORDER BY id", (uid,)) == assegnati
    assert _q(m, "SELECT id, agency_id, role, status FROM agency_memberships "
                 "WHERE operator_user_id = %s ORDER BY id", (uid,)) == membership
    assert _impronta(m) == prima
    riga = _q(m, "SELECT actor_user_id, result, metadata FROM platform_audit_log "
                 "WHERE action = 'platform.operator.update' AND target_id = %s "
                 "AND metadata ? 'status_change' ORDER BY id DESC LIMIT 1", (str(uid),))[0]
    assert (riga[0], riga[1], riga[2]["status_change"]) == (
        m["persone"]["super"], "success", {"from": "active", "to": "disabled"})


def test_03_il_super_admin_designato_non_si_elimina(adm7e):
    m = adm7e
    sa, designato = m["accedi"]("super"), m["persone"]["super"]
    r = sa.patch(f"/api/platform/operators/{designato}", json={"status": "disabled"})
    assert r.status_code == 409, r.text
    r = sa.patch(f"/api/platform/operators/{designato}/platform-admin", json={"is_platform_admin": False})
    assert r.status_code == 409, r.text
    assert tuple(_q(m, "SELECT status, is_platform_admin FROM operator_users WHERE id = %s",
                    (designato,))[0]) == ("active", True)


def test_04_i_non_autorizzati_ricevono_403(adm7e):
    m = adm7e
    q, uid = m["quattro"], m["persone"]["membro"]
    prima = _impronta(m)
    for chi in ("altro", "agente"):
        c = m["accedi"](chi)
        assert c.patch(f"/api/platform/agencies/{q}", json={"status": "suspended"}).status_code == 403, chi
        assert c.patch(f"/api/platform/operators/{uid}", json={"status": "disabled"}).status_code == 403, chi
    assert _impronta(m) == prima
    assert _q(m, "SELECT status FROM agencies WHERE id = %s", (q,))[0][0] == "active"


def test_05_ripristino(adm7e):
    m = adm7e
    sa, q, uid = m["accedi"]("super"), m["quattro"], m["persone"]["quattro"]
    vecchia = _token(m["accedi"]("quattro"))
    for verso in ("suspended", "archived"):
        assert sa.patch(f"/api/platform/agencies/{q}", json={"status": verso}).status_code == 200
    assert sa.patch(f"/api/platform/operators/{uid}", json={"status": "disabled"}).status_code == 200

    assert sa.patch(f"/api/platform/agencies/{q}", json={"status": "active"}).status_code == 200
    assert _login(m, "quattro") == 401  # l'account e' ancora escluso
    assert sa.patch(f"/api/platform/operators/{uid}", json={"status": "active"}).status_code == 200
    assert _login(m, "quattro") == 204
    assert not _viva(vecchia), "una sessione di prima dell'esclusione non torna valida"


# ---------------------------------------------------------------------------
# Interfaccia (Chromium, smartphone e desktop)
# ---------------------------------------------------------------------------

from tests.test_adm3_continuity_postgres import browser_server  # noqa: E402,F401


@pytest.fixture
def ui(browser_server):
    m = browser_server
    designato = m["persone"]["super"]
    # il designato visibile fra gli operatori dell'agenzia 2 (P27-1 D4 lo ammette)
    if not _q(m, "SELECT 1 FROM agency_memberships WHERE operator_user_id = %s AND agency_id = 2", (designato,)):
        _q(m, "INSERT INTO agency_memberships (agency_id, operator_user_id, role, status) "
              "VALUES (2, %s, 'agent', 'active')", (designato,))
    _q(m, "UPDATE agency_memberships SET status = 'active' WHERE operator_user_id = %s AND agency_id = 2",
       (designato,))
    yield m
    _q(m, "UPDATE agency_memberships SET status = 'revoked' WHERE operator_user_id = %s AND agency_id = 2",
       (designato,))
    _q(m, "UPDATE operator_sessions SET acting_agency_id = NULL, acting_entered_at = NULL")
    _q(m, "UPDATE agencies SET status = 'active' WHERE id = %s", (m["quattro"],))
    _q(m, "UPDATE operator_users SET deleted_at = NULL, deleted_by_user_id = NULL")  # ELIMINA OPERATORE (113)
    _q(m, "UPDATE operator_users SET status = 'active' WHERE id = ANY(%s)",
       ([m["persone"]["quattro"], m["persone"]["altro"]],))


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_06_interfaccia_elimina_e_ripristina(ui, larghezza):
    import uuid
    from playwright.sync_api import sync_playwright
    from tests.test_eliminazione_edificio_1_browser_postgres import _sfora
    m = ui
    q, uid, designato = m["quattro"], m["persone"]["quattro"], m["persone"]["super"]
    prima = _impronta(m)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": larghezza, "height": 900})
        ctx.add_cookies([{"name": m["cookie"], "value": m["token"], "url": m["url"]}])
        page = ctx.new_page()
        errori = []
        page.on("pageerror", lambda e: errori.append(str(e)))

        def vai(frammento):
            page.goto(f"{m['url']}/os/?n={uuid.uuid4().hex[:8]}#{frammento}")

        def conferma(atteso):
            page.wait_for_selector("dialog.modal [data-conferma='si']")
            testo = page.inner_text("dialog.modal")
            assert atteso in testo, testo
            page.click("dialog.modal [data-conferma='si']")
            page.wait_for_selector("dialog.modal", state="detached")
            return testo

        stato = lambda: _q(m, "SELECT status FROM agencies WHERE id = %s", (q,))[0][0]  # noqa: E731

        # agenzia attiva -> «Elimina agenzia» (pulsante rosso)
        vai(f"/rete/agenzie/{q}")
        page.wait_for_selector("[data-elimina-agenzia]")
        assert page.inner_text("[data-elimina-agenzia]") == "Elimina agenzia"
        assert "danger" in page.get_attribute("[data-elimina-agenzia]", "class")
        page.click("[data-elimina-agenzia]")
        testo = conferma("Nessun dato viene cancellato")
        assert "restano conservati" in testo and "ripristinarla" in testo
        # torna all'elenco operativo, dove l'agenzia non c'e' piu'
        page.wait_for_selector("#agenzie-lista table")
        assert stato() == "archived"
        assert "Quattro" not in page.inner_text("#agenzie-lista")
        assert _sfora(page)["scroll"] <= larghezza + 1

        # si ritrova fra le eliminate
        page.select_option("#filtro-stato-agenzia", "archived")
        assert "Quattro" in page.inner_text("#agenzie-lista")
        assert "Eliminata" in page.inner_text("#agenzie-lista")

        vai(f"/rete/agenzie/{q}")
        page.wait_for_selector("#entra-agenzia[data-modo='consulta']")
        assert "Eliminata" in page.inner_text("#agenzia-sottotitolo")

        # ripristino
        vai(f"/rete/agenzie/{q}")
        page.click("[data-stato-agenzia='active']")
        conferma("nuovo accesso")
        page.wait_for_selector("[data-elimina-agenzia]")
        assert stato() == "active"

        # operatore -> «Elimina dalla Rete» e ripristino
        page.click("#agenzia-tabs [data-tab='operatori']")
        elimina = f"[data-azione='disabilita'][data-operatore='{uid}']"
        page.wait_for_selector(elimina)
        assert page.inner_text(elimina) == "Elimina dalla Rete"
        conferma_testo = (page.click(elimina), conferma("nessun dato viene cancellato"))[1]
        assert "sessioni verranno chiuse" in conferma_testo
        ripristina = f"[data-azione='riattiva'][data-operatore='{uid}']"
        page.wait_for_selector(ripristina)
        assert _q(m, "SELECT status FROM operator_users WHERE id = %s", (uid,))[0][0] == "disabled"
        page.click(ripristina)
        conferma("nuovo accesso")
        page.wait_for_selector(elimina)
        assert _q(m, "SELECT status FROM operator_users WHERE id = %s", (uid,))[0][0] == "active"

        # il designato non ha comandi di eliminazione
        vai("/rete/agenzie/2")
        page.click("#agenzia-tabs [data-tab='operatori']")
        page.wait_for_selector("#operatori-lista table")
        assert "Super Admin designato" in page.inner_text("#operatori-lista")
        assert page.query_selector(f"[data-operatore='{designato}'][data-azione='disabilita']") is None
        assert page.query_selector(f"[data-operatore='{designato}'][data-azione='piattaforma-togli']") is None
        assert _sfora(page)["scroll"] <= larghezza + 1

        assert not errori, errori
        browser.close()
    assert _impronta(m) == prima


# ---------------------------------------------------------------------------
# ELIMINAZIONE BLINDATA - account senza agenzia
# ---------------------------------------------------------------------------

def test_07_account_senza_agenzia_elencati_ed_eliminabili(adm7e):
    m = adm7e
    sa, altro, designato = m["accedi"]("super"), m["persone"]["altro"], m["persone"]["super"]
    p = m["persone"]

    r = sa.get("/api/platform/operators", params={"without_active_membership": "true", "limit": 200})
    assert r.status_code == 200, r.text
    righe = {x["operator"]["id"]: x for x in r.json()}
    assert altro in righe and designato in righe, sorted(righe)
    assert righe[altro]["active_agency_id"] is None and righe[altro]["operator"]["is_platform_admin"] is True
    assert p["agente"] not in righe and p["quattro"] not in righe
    assert all("password_hash" not in x["operator"] for x in r.json())
    tutti = {x["operator"]["id"]: x for x in sa.get("/api/platform/operators", params={"limit": 200}).json()}
    assert tutti[p["agente"]]["active_agency_id"] == 2 and tutti[p["quattro"]]["active_agency_id"] == m["quattro"]
    # paginazione
    pagina = sa.get("/api/platform/operators", params={"limit": 1, "offset": 1}).json()
    assert len(pagina) == 1 and pagina[0]["operator"]["id"] == sorted(tutti)[1]
    assert sa.get("/api/platform/operators", params={"limit": 0}).status_code == 422

    # solo il Super Admin designato
    for chi in ("altro", "agente"):
        assert m["accedi"](chi).get("/api/platform/operators").status_code == 403, chi

    # un amministratore di sola piattaforma si elimina dalla Rete
    sessione = _token(m["accedi"]("altro"))
    prima = _impronta(m)
    r = sa.patch(f"/api/platform/operators/{altro}", json={"status": "disabled"})
    assert r.status_code == 200 and r.json()["status"] == "disabled", r.text
    assert not _viva(sessione)
    assert _login(m, "altro") == 401
    assert _impronta(m) == prima
    assert _q(m, "SELECT count(*) FROM operator_users WHERE id = %s", (altro,))[0][0] == 1
    riga = _q(m, "SELECT actor_user_id, metadata FROM platform_audit_log WHERE action = 'platform.operator.update' "
                 "AND target_id = %s AND metadata ? 'status_change' ORDER BY id DESC LIMIT 1", (str(altro),))[0]
    assert (riga[0], riga[1]["status_change"]) == (designato, {"from": "active", "to": "disabled"})
    stato = {x["operator"]["id"]: x["operator"]["status"] for x in sa.get(
        "/api/platform/operators", params={"without_active_membership": "true", "limit": 200}).json()}
    assert stato[altro] == "disabled", "resta nell'elenco: escluso, non cancellato"

    # il designato resta protetto anche da qui
    assert sa.patch(f"/api/platform/operators/{designato}", json={"status": "disabled"}).status_code == 409

    # ripristino con un nuovo accesso
    assert sa.patch(f"/api/platform/operators/{altro}", json={"status": "active"}).status_code == 200
    assert _login(m, "altro") == 204
    assert not _viva(sessione)


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_08_scheda_account_in_chromium(ui, larghezza):
    import uuid
    from playwright.sync_api import sync_playwright
    from tests.test_eliminazione_edificio_1_browser_postgres import _sfora
    m = ui
    altro, designato, agente = m["persone"]["altro"], m["persone"]["super"], m["persone"]["agente"]
    # nella scheda il designato non ha membership attiva: compare fra gli account senza agenzia
    _q(m, "UPDATE agency_memberships SET status = 'revoked' WHERE operator_user_id = %s", (designato,))
    prima = _impronta(m)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": larghezza, "height": 900})
        ctx.add_cookies([{"name": m["cookie"], "value": m["token"], "url": m["url"]}])
        page = ctx.new_page()
        errori = []
        page.on("pageerror", lambda e: errori.append(str(e)))

        def conferma(atteso):
            page.wait_for_selector("dialog.modal [data-conferma='si']")
            testo = page.inner_text("dialog.modal")
            assert atteso in testo, testo
            page.click("dialog.modal [data-conferma='si']")
            page.wait_for_selector("dialog.modal", state="detached")
            return testo

        stato = lambda: _q(m, "SELECT status FROM operator_users WHERE id = %s", (altro,))[0][0]  # noqa: E731

        page.goto(f"{m['url']}/os/?n={uuid.uuid4().hex[:8]}#/rete")
        page.click("#rete-tabs [data-tab='account']")
        page.wait_for_selector("#account-lista table")
        testo = page.inner_text("#account-lista")
        assert "Altro" in testo and "Amministratore di piattaforma" in testo, testo
        assert "Super Admin designato" in testo and "Agata" not in testo
        assert page.query_selector(f"[data-operatore='{designato}']") is None, "nessun comando sul designato"
        assert _sfora(page)["scroll"] <= larghezza + 1

        elimina = f"#account-lista [data-azione='disabilita'][data-operatore='{altro}']"
        page.click(elimina)
        testo = conferma("nessun dato viene cancellato")
        assert "sessioni verranno chiuse" in testo
        ripristina = f"#account-lista [data-azione='riattiva'][data-operatore='{altro}']"
        page.wait_for_selector(ripristina)
        assert stato() == "disabled"
        page.click(ripristina)
        conferma("nuovo accesso")
        page.wait_for_selector(elimina)
        assert stato() == "active"

        # tutti gli account, con l'agenzia attiva
        page.select_option("#filtro-account", "tutti")
        page.wait_for_selector(f"#account-lista [data-operatore='{agente}']")
        assert "Agata" in page.inner_text("#account-lista")
        assert page.is_disabled("#account-prec")
        assert _sfora(page)["scroll"] <= larghezza + 1

        assert not errori, errori
        browser.close()
    assert _impronta(m) == prima


# ---------------------------------------------------------------------------
# ELIMINA AGENZIA - nessuna nuova operazione su un'agenzia eliminata
# ---------------------------------------------------------------------------

def test_09_agenzia_eliminata_non_riceve_nuove_operazioni(adm7e):
    m = adm7e
    sa, q, t = m["accedi"]("super"), m["quattro"], m["territorio_quattro"]
    for verso in ("suspended", "archived"):
        assert sa.patch(f"/api/platform/agencies/{q}", json={"status": verso}).status_code == 200
    prima, proprietari = _impronta(m), _proprietari(m, q)
    email = "nuovo.adm7e.09@x.test"

    rifiuti = {
        "assegna": sa.post(f"/api/platform/agencies/{q}/territories", json={"territory_id": t}),
        "trasferisci": sa.post(f"/api/platform/territories/{t}/transfer", json={"agency_id": q}),
        "riattiva": sa.patch(f"/api/platform/agencies/{q}/territories/{m['assegnazione']}",
                             json={"status": "active"}),
        "operatore": sa.post(f"/api/platform/agencies/{q}/operators",
                             json={"email": email, "password": "Prova-adm7e-09!", "role": "agent"}),
    }
    for nome, r in rifiuti.items():
        assert r.status_code == 409, (nome, r.text)
        assert "eliminata" in r.text, (nome, r.text)
    assert not _q(m, "SELECT 1 FROM operator_users WHERE email_normalized = %s", (email,))
    assert _impronta(m) == prima and _proprietari(m, q) == proprietari

    # l'agenzia di sistema non si elimina
    sistema = _q(m, "SELECT id FROM agencies WHERE slug = 'stima360'")[0][0]
    assert sa.patch(f"/api/platform/agencies/{sistema}", json={"status": "suspended"}).status_code == 409
    assert _q(m, "SELECT status FROM agencies WHERE id = %s", (sistema,))[0][0] == "active"

    # ripristinata, torna utilizzabile
    assert sa.patch(f"/api/platform/agencies/{q}", json={"status": "active"}).status_code == 200
    r = sa.post(f"/api/platform/agencies/{q}/territories", json={"territory_id": t})
    assert r.status_code == 409 and "eliminata" not in r.text, r.text  # gia' sua, non piu' "eliminata"



# ---------------------------------------------------------------------------
# ELIMINA OPERATORE - eliminazione logica (migration 113)
# ---------------------------------------------------------------------------

def _assegnabile(m, agenzia, uid):
    from psycopg2.extras import RealDictCursor
    from appointments import repository as agenda
    from operator_auth.repository import membership_exists
    conn = m["psycopg2"].connect(m["dsn"])
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            return (membership_exists(cur, agenzia, uid), agenda.active_membership(cur, agenzia, uid),
                    any(int(a["id"]) == uid for a in agenda.agents(cur, agenzia)))
    finally:
        conn.close()


def test_10_operatore_eliminato_logicamente(adm7e):
    m = adm7e
    sa, uid = m["accedi"]("super"), m["persone"]["membro"]
    url = f"/api/platform/operators/{uid}"
    assert _assegnabile(m, 2, uid) == (True, True, True)
    sessione = _token(m["accedi"]("membro"))

    # si elimina solo un escluso dalla Rete
    assert sa.post(f"{url}/delete").status_code == 409
    assert sa.patch(url, json={"status": "disabled"}).status_code == 200
    # solo il Super Admin designato (un altro amministratore di piattaforma: 403)
    assert m["accedi"]("altro").post(f"{url}/delete").status_code == 403

    prima, membership = _impronta(m), _q(
        m, "SELECT id, agency_id, role, status FROM agency_memberships WHERE operator_user_id = %s ORDER BY id", (uid,))
    r = sa.post(f"{url}/delete")
    assert r.status_code == 200 and r.json()["deleted_at"], r.text
    assert sa.post(f"{url}/delete").status_code == 200  # ripetere non cambia nulla
    assert _q(m, "SELECT status, deleted_by_user_id FROM operator_users WHERE id = %s", (uid,))[0] == [
        "disabled", m["persone"]["super"]]

    # elenchi: la riga c'e' con il marcatore (la UI la nasconde), nessun dato toccato
    righe = sa.get("/api/platform/agencies/2/operators").json()
    assert [r["operator"]["deleted_at"] is not None for r in righe if r["operator"]["id"] == uid] == [True]
    assert _impronta(m) == prima
    assert _q(m, "SELECT id, agency_id, role, status FROM agency_memberships "
                 "WHERE operator_user_id = %s ORDER BY id", (uid,)) == membership
    # nessun accesso, nessuna nuova assegnazione, nessuna riattivazione senza ripristino
    assert not _viva(sessione) and _login(m, "membro") == 401
    assert _assegnabile(m, 2, uid) == (False, False, False)
    assert sa.patch(url, json={"status": "active"}).status_code == 409
    assert sa.post("/api/platform/agencies/2/operators",
                   json={"email": "adm1.membro@x.test", "role": "agent"}).status_code == 409
    azioni = [r[0] for r in _q(m, "SELECT action FROM platform_audit_log WHERE target_id = %s "
                                  "AND result = 'success' ORDER BY id DESC LIMIT 1", (str(uid),))]
    assert azioni == ["platform.operator.delete"]

    # ripristino: torna escluso, poi «Ripristina operatore» lo riattiva
    r = sa.post(f"{url}/restore")
    assert r.status_code == 200 and r.json()["deleted_at"] is None and r.json()["status"] == "disabled", r.text
    assert sa.post(f"{url}/restore").status_code == 409
    assert sa.patch(url, json={"status": "active"}).status_code == 200
    assert _login(m, "membro") == 204
    assert _assegnabile(m, 2, uid) == (True, True, True)


def test_10b_super_admin_mai_eliminabile(adm7e):
    m = adm7e
    sa, designato, altro = m["accedi"]("super"), m["persone"]["super"], m["persone"]["altro"]
    assert sa.post(f"/api/platform/operators/{designato}/delete").status_code == 409
    # l'account info@stima360.it, anche se non fosse il designato configurato
    email = _q(m, "SELECT email, email_normalized FROM operator_users WHERE id = %s", (altro,))[0]
    _q(m, "UPDATE operator_users SET email = 'info@stima360.it', email_normalized = 'info@stima360.it', "
          "status = 'disabled' WHERE id = %s", (altro,))
    try:
        r = sa.post(f"/api/platform/operators/{altro}/delete")
        assert r.status_code == 409, r.text
        assert _q(m, "SELECT deleted_at FROM operator_users WHERE id = %s", (altro,))[0][0] is None
    finally:
        _q(m, "UPDATE operator_users SET email = %s, email_normalized = %s, status = 'active' WHERE id = %s",
           (email[0], email[1], altro))


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_11_elimina_operatore_in_chromium(ui, larghezza):
    import uuid
    from playwright.sync_api import sync_playwright
    from tests.test_eliminazione_edificio_1_browser_postgres import _sfora
    m = ui
    uid, designato = m["persone"]["quattro"], m["persone"]["super"]
    email = _q(m, "SELECT email FROM operator_users WHERE id = %s", (uid,))[0][0]
    _q(m, "UPDATE operator_users SET status = 'disabled' WHERE id = %s", (uid,))
    prima = _impronta(m)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": larghezza, "height": 900})
        ctx.add_cookies([{"name": m["cookie"], "value": m["token"], "url": m["url"]}])
        page = ctx.new_page()
        errori = []
        page.on("pageerror", lambda e: errori.append(str(e)))
        page.goto(f"{m['url']}/os/?n={uuid.uuid4().hex[:8]}#/rete/agenzie/{m['quattro']}")
        page.click("#agenzia-tabs [data-tab='operatori']")
        elimina = f"[data-azione='elimina-operatore'][data-operatore='{uid}']"
        page.wait_for_selector(elimina)
        assert page.inner_text(elimina) == "Elimina"
        assert "danger" in page.get_attribute(elimina, "class")
        assert page.query_selector(f"[data-azione='riattiva'][data-operatore='{uid}']") is not None
        page.click(elimina)
        page.wait_for_selector("dialog.modal [data-conferma='si']")
        assert email in page.inner_text("dialog.modal")
        page.click("dialog.modal [data-conferma='si']")
        page.wait_for_selector(elimina, state="detached")
        assert email not in page.inner_text("#operatori-lista")
        assert _q(m, "SELECT deleted_at IS NOT NULL FROM operator_users WHERE id = %s", (uid,))[0][0] is True
        assert _sfora(page)["scroll"] <= larghezza + 1

        # «Mostra eliminati» -> «Ripristina»
        page.check("#op-mostra-eliminati")
        ripristina = f"[data-azione='ripristina-eliminato'][data-operatore='{uid}']"
        page.wait_for_selector(ripristina)
        assert "Eliminato" in page.inner_text("#operatori-lista")
        page.click(ripristina)
        page.wait_for_selector("dialog.modal [data-conferma='si']")
        page.click("dialog.modal [data-conferma='si']")
        page.wait_for_selector(elimina)
        assert _q(m, "SELECT deleted_at FROM operator_users WHERE id = %s", (uid,))[0][0] is None

        # il designato non ha il pulsante «Elimina»
        page.goto(f"{m['url']}/os/?n={uuid.uuid4().hex[:8]}#/rete/agenzie/2")
        page.click("#agenzia-tabs [data-tab='operatori']")
        page.wait_for_selector("#operatori-lista table")
        assert page.query_selector(f"[data-operatore='{designato}'][data-azione='elimina-operatore']") is None
        assert not errori, errori
        browser.close()
    assert _impronta(m) == prima



# ---------------------------------------------------------------------------
# ELIMINA TERRITORIO - eliminazione logica (migration 114)
# ---------------------------------------------------------------------------

def _territorio_libero(sa, etichetta):
    import uuid
    chiave = f"elimina-{uuid.uuid4().hex[:8]}"
    r = sa.post("/api/platform/territories",
                json={"kind": "municipality", "canonical_key": chiave, "label": etichetta})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _ids(sa, **filtri):
    q = "&".join(f"{k}={v}" for k, v in {"limit": 200, **filtri}.items())
    return {t["id"] for t in sa.get(f"/api/platform/territories?{q}").json()}


def test_12_territorio_eliminato_logicamente(adm7e):
    m = adm7e
    sa = m["accedi"]("super")
    t = _territorio_libero(sa, "Comune da eliminare")

    # presidiato: e' in uso dalla piattaforma (instrada i lead), non si elimina
    presidiato = m["territorio_quattro"]
    r = sa.post(f"/api/platform/territories/{presidiato}/delete")
    assert r.status_code == 409 and "presidiato" in r.text, r.text
    # solo il Super Admin designato
    assert m["accedi"]("altro").post(f"/api/platform/territories/{t}/delete").status_code == 403
    assert m["accedi"]("quattro").post(f"/api/platform/territories/{t}/delete").status_code in (401, 403)

    prima = _impronta(m)
    r = sa.post(f"/api/platform/territories/{t}/delete")
    assert r.status_code == 200 and r.json()["deleted_at"], r.text
    assert sa.post(f"/api/platform/territories/{t}/delete").status_code == 200  # idempotente
    assert _impronta(m) == prima
    assert t not in _ids(sa) and t in _ids(sa, deleted="true")
    assert sa.get(f"/api/platform/territories/{t}").json()["deleted_at"]
    # nessuna nuova assegnazione
    r = sa.post("/api/platform/agencies/2/territories", json={"territory_id": t})
    assert r.status_code == 409 and "eliminato" in r.text, r.text
    assert not _q(m, "SELECT 1 FROM agency_territory_assignments WHERE territory_id = %s", (t,))
    azioni = [r[0] for r in _q(m, "SELECT action FROM platform_audit_log WHERE target_id = %s "
                                  "AND target_type = 'territory' AND result = 'success' ORDER BY id", (str(t),))]
    assert azioni == ["platform.territory.create", "platform.territory.delete"]

    # ripristino: torna operativo e assegnabile
    r = sa.post(f"/api/platform/territories/{t}/restore")
    assert r.status_code == 200 and r.json()["deleted_at"] is None, r.text
    assert sa.post(f"/api/platform/territories/{t}/restore").status_code == 409
    assert t in _ids(sa)
    r = sa.post("/api/platform/agencies/2/territories", json={"territory_id": t})
    assert r.status_code == 201, r.text
    assegnazione = r.json()["id"]

    # un'assegnazione SOSPESA non blocca l'eliminazione, ma non si riattiva su un eliminato
    url = f"/api/platform/agencies/2/territories/{assegnazione}"
    assert sa.patch(url, json={"status": "suspended"}).status_code == 200
    assert sa.post(f"/api/platform/territories/{t}/delete").status_code == 200
    r = sa.patch(url, json={"status": "active"})
    assert r.status_code == 409 and "eliminato" in r.text, r.text
    assert _q(m, "SELECT status FROM agency_territory_assignments WHERE id = %s", (assegnazione,))[0][0] == "suspended"
    assert sa.patch(url, json={"status": "revoked"}).status_code == 200  # chiudere resta possibile


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_13_elimina_territorio_in_chromium(ui, larghezza):
    import uuid
    from playwright.sync_api import sync_playwright
    from tests.test_eliminazione_edificio_1_browser_postgres import _sfora
    m = ui
    etichetta = f"Borgo Prova {uuid.uuid4().hex[:5]}"
    chiave = f"borgo-{uuid.uuid4().hex[:8]}"
    t = _q(m, "INSERT INTO network_territories (kind, canonical_key, label) VALUES ('municipality', %s, %s) "
              "RETURNING id", (chiave, etichetta))[0][0]
    prima = _impronta(m)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": larghezza, "height": 900})
        ctx.add_cookies([{"name": m["cookie"], "value": m["token"], "url": m["url"]}])
        page = ctx.new_page()
        errori = []
        page.on("pageerror", lambda e: errori.append(str(e)))
        page.goto(f"{m['url']}/os/?n={uuid.uuid4().hex[:8]}#/rete")
        page.wait_for_selector("#rete-tabs [data-tab='territori']")
        # la scheda «Eliminazioni edifici» non c'e' piu'
        assert page.query_selector("[data-tab='eliminazioni']") is None
        assert "Eliminazioni edifici" not in page.inner_text("#rete-tabs")
        page.click("#rete-tabs [data-tab='territori']")
        elimina = f"[data-territorio-comando='elimina'][data-territorio='{t}']"
        page.wait_for_selector(elimina)
        assert page.inner_text(elimina) == "Elimina" and "danger" in page.get_attribute(elimina, "class")
        page.click(elimina)
        page.wait_for_selector("dialog.modal [data-conferma='si']")
        assert etichetta in page.inner_text("dialog.modal")
        page.click("dialog.modal [data-conferma='si']")
        page.wait_for_selector(elimina, state="detached")
        assert "#/rete/territori/" not in page.url  # il clic sul comando non apre la riga
        assert etichetta not in page.inner_text("#territori-lista")
        assert _q(m, "SELECT deleted_at IS NOT NULL FROM network_territories WHERE id = %s", (t,))[0][0] is True
        assert _sfora(page)["scroll"] <= larghezza + 1

        page.select_option("#filtro-eliminati", "eliminati")
        ripristina = f"[data-territorio-comando='ripristina'][data-territorio='{t}']"
        page.wait_for_selector(ripristina)
        page.click(ripristina)
        page.wait_for_selector("dialog.modal [data-conferma='si']")
        page.click("dialog.modal [data-conferma='si']")
        page.wait_for_selector(ripristina, state="detached")
        assert _q(m, "SELECT deleted_at FROM network_territories WHERE id = %s", (t,))[0][0] is None
        assert not errori, errori
        browser.close()
    assert _impronta(m) == prima
