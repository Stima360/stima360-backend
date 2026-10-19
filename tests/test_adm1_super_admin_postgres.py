"""ADM-1 - Super Admin generale, ultimo amministratore, sessioni, agenzia di sistema.

PostgreSQL usa-e-getta (schema completo dal runner, come C1/C2), sessioni VERE
(`operator_auth`: login, cookie, risoluzione a ogni richiesta), ammissione VERA
(`require_platform_admin` con il suo audit), router Platform vero.

  01  il Super Admin designato amministra la Rete; `/me` lo dice
  02  un altro platform admin: 403 su ogni comando della Rete e su C2, riga
      `denied`, nulla cambia
  03  un tenant ordinario: permessi invariati (Platform 403, la sua sessione vive)
  04  l'account designato non si disabilita, nemmeno da se'; resta attivo
  05  ruolo tolto mentre la modifica attende il lock: 403, nulla committato
  06  richieste simultanee: nessuna aggira le guardie, stato finale coerente
  07  operatore disabilitato: sessioni revocate; riattivato: le vecchie restano
      morte, il nuovo accesso funziona
  08  membership sospesa o revocata: sessioni revocate e mai riesumate
  09  agenzia sospesa: sessioni dei membri revocate, acting chiuso, sessione del
      Super Admin intatta; assegnazioni invariate
  10  agenzia di sistema protetta; archiviazione solo da sospesa
  11  nessun dato operativo o storico cancellato (impronta dei conteggi)
"""
from __future__ import annotations

import threading
import time

import pytest

from tests.test_c1_lettura_territoriale_postgres import _operatore, rete  # noqa: F401
from tests.test_censimento_3_backend_postgres import DSN, _q, completo, mondo  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

PASSWORD = "Adm1-prova-locale!"
TABELLE_CONSERVATE = (
    "agencies", "operator_users", "agency_memberships", "agency_territory_assignments",
    "network_territories", "network_territory_aliases", "properties", "buildings", "contacts", "leads",
    "appointments", "acquisitions", "property_documents", "property_territorial_change_requests",
    "property_territorial_change_events", "record_lifecycle_events",
)


def _persona(m, email, nome, *, platform_admin=False, agenzia=None, ruolo=None):
    """Operatore con password vera (login reale), attivo; membership attiva se indicata."""
    from operator_auth.security import hash_password
    riga = _q(m, "SELECT id FROM operator_users WHERE email_normalized = %s", (email,))
    if riga:
        uid = riga[0][0]
    else:
        uid = _q(m, "INSERT INTO operator_users (email, email_normalized, password_hash, first_name, is_platform_admin) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                 (email, email, hash_password(PASSWORD), nome, platform_admin))[0][0]
    _q(m, "UPDATE operator_users SET status = 'active', is_platform_admin = %s WHERE id = %s", (platform_admin, uid))
    if agenzia is not None:
        if _q(m, "SELECT 1 FROM agency_memberships WHERE agency_id = %s AND operator_user_id = %s", (agenzia, uid)):
            _q(m, "UPDATE agency_memberships SET status = 'active', role = %s WHERE agency_id = %s AND operator_user_id = %s",
               (ruolo, agenzia, uid))
        else:
            _q(m, "INSERT INTO agency_memberships (agency_id, operator_user_id, role, status) VALUES (%s, %s, %s, 'active')",
               (agenzia, uid, ruolo))
    return uid


def _impronta(m):
    return {t: _q(m, f"SELECT count(*) FROM {t}")[0][0] for t in TABELLE_CONSERVATE}


@pytest.fixture
def adm1(rete, monkeypatch):
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    import operator_auth.database as odb
    import platform_admin.database as pdb
    from operator_auth.router import router as auth_router
    from platform_admin.dependencies import require_platform_admin
    from platform_admin.router import router as platform_router
    from platform_admin.super_admin import SUPER_ADMIN_ENV

    m = rete
    connetti = lambda: m["psycopg2"].connect(m["dsn"])  # noqa: E731
    monkeypatch.setattr(odb, "get_connection", connetti)
    monkeypatch.setattr(pdb, "get_connection", connetti)

    quattro = _q(m, "SELECT id FROM agencies WHERE slug = 'adm1-quattro'")
    quattro = quattro[0][0] if quattro else _q(m, "INSERT INTO agencies (id, name, slug, status) VALUES "
                                                  "((SELECT max(id) + 1 FROM agencies), 'Quattro', 'adm1-quattro', 'active') "
                                                  "RETURNING id")[0][0]
    _q(m, "UPDATE agencies SET status = 'active', name = 'Quattro' WHERE id = %s", (quattro,))
    sistema = _q(m, "SELECT id FROM agencies WHERE slug = 'stima360'")
    assert sistema, "l'agenzia di sistema `stima360` deve esistere nello schema di prova"
    persone = {
        "super": _persona(m, "adm1.super@x.test", "Super", platform_admin=True),
        "altro": _persona(m, "adm1.altro@x.test", "Altro", platform_admin=True),
        "agente": _persona(m, "adm1.agente@x.test", "Agata", agenzia=2, ruolo="agent"),
        "membro": _persona(m, "adm1.membro@x.test", "Mauro", agenzia=2, ruolo="agent"),
        "quattro": _persona(m, "adm1.quattro@x.test", "Quinto", agenzia=quattro, ruolo="agency_owner"),
    }
    monkeypatch.setenv(SUPER_ADMIN_ENV, str(persone["super"]))
    t = _q(m, "SELECT id FROM network_territories WHERE canonical_key = 'adm1-quattro-comune'")
    t = t[0][0] if t else _q(m, "INSERT INTO network_territories (kind, canonical_key, label) "
                                "VALUES ('municipality', 'adm1-quattro-comune', 'Comune Quattro') RETURNING id")[0][0]
    if not _q(m, "SELECT 1 FROM agency_territory_assignments WHERE territory_id = %s AND status = 'active'", (t,)):
        _q(m, "INSERT INTO agency_territory_assignments (territory_id, agency_id, status) VALUES (%s, %s, 'active')",
           (t, quattro))
    _q(m, "UPDATE operator_sessions SET acting_agency_id = NULL, acting_entered_at = NULL")

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(platform_router, dependencies=[Depends(require_platform_admin)])

    def accedi(chi):
        client = TestClient(app, base_url="https://testserver")
        email = _q(m, "SELECT email FROM operator_users WHERE id = %s", (persone[chi],))[0][0]
        r = client.post("/api/operator-auth/login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 204, (chi, r.status_code, r.text)
        return client

    yield {**m, "persone": persone, "quattro": quattro, "sistema": sistema[0][0], "territorio_quattro": t,
           "accedi": accedi, "app": app}
    _q(m, "UPDATE operator_users SET is_platform_admin = TRUE, status = 'active' WHERE id = %s", (persone["super"],))
    _q(m, "UPDATE agencies SET status = 'active' WHERE id IN (%s, %s)", (quattro, sistema[0][0]))


def _token(client):
    from operator_auth.enums import COOKIE_NAME
    return client.cookies.get(COOKIE_NAME)


def _viva(token) -> bool:
    from operator_auth import service
    return service.session_from_token(token) is not None


# ---------------------------------------------------------------------------

def test_01_il_super_admin_designato_amministra_la_rete(adm1):
    m = adm1
    sa = m["accedi"]("super")
    me = sa.get("/api/platform/me")
    assert me.status_code == 200 and me.json()["is_super_admin"] is True, me.text
    assert sa.get("/api/platform/agencies").status_code == 200
    q = m["quattro"]
    assert sa.patch(f"/api/platform/agencies/{q}", json={"name": "Quattro Rinominata"}).status_code == 200
    assert sa.patch(f"/api/platform/agencies/{q}", json={"name": "Quattro"}).status_code == 200
    r = sa.patch(f"/api/platform/operators/{m['persone']['agente']}", json={"first_name": "Agata B."})
    assert r.status_code == 200, r.text
    a = _q(m, "SELECT id FROM agency_territory_assignments WHERE territory_id = %s AND status = 'active'",
           (m["territorio_quattro"],))[0][0]
    for stato in ("suspended", "active"):
        r = sa.patch(f"/api/platform/agencies/{q}/territories/{a}", json={"status": stato})
        assert r.status_code == 200, r.text
    assert sa.get("/api/platform/territorial-change-requests").status_code == 200


def test_02_un_altro_platform_admin_non_gestisce_la_rete(adm1):
    m = adm1
    altro = m["accedi"]("altro")
    me = altro.get("/api/platform/me")
    assert me.status_code == 200 and me.json()["is_super_admin"] is False
    q = m["quattro"]
    prima = _impronta(m)
    stato_prima = _q(m, "SELECT status, name FROM agencies WHERE id = %s", (q,))[0]
    negati_prima = _q(m, "SELECT count(*) FROM platform_audit_log WHERE action = 'platform.super_admin.required' "
                         "AND result = 'denied' AND actor_user_id = %s", (m["persone"]["altro"],))[0][0]
    a = _q(m, "SELECT id FROM agency_territory_assignments WHERE territory_id = %s AND status = 'active'",
           (m["territorio_quattro"],))[0][0]
    richieste = [
        ("get", "/api/platform/agencies", None),
        ("patch", f"/api/platform/agencies/{q}", {"status": "suspended"}),
        ("patch", f"/api/platform/agencies/{q}", {"name": "Dirottata"}),
        ("patch", f"/api/platform/operators/{m['persone']['agente']}", {"status": "disabled"}),
        ("patch", f"/api/platform/operators/{m['persone']['super']}", {"status": "disabled"}),
        ("patch", f"/api/platform/agencies/2/operators/{m['persone']['membro']}/membership", {"status": "revoked"}),
        ("patch", f"/api/platform/agencies/{q}/territories/{a}", {"status": "revoked"}),
        ("post", "/api/platform/territories", {"kind": "municipality", "canonical_key": "adm1-abusivo", "label": "Abusivo"}),
        ("post", f"/api/platform/territories/{m['territorio_quattro']}/transfer", {"agency_id": 2}),
        ("post", f"/api/platform/agencies/{q}/enter", None),
        ("get", "/api/platform/territorial-change-requests", None),
        ("post", "/api/platform/territorial-change-requests/1/approve", {"note": "no"}),
        ("post", "/api/platform/territorial-change-requests/1/reject", {"note": "no"}),
    ]
    for metodo, url, corpo in richieste:
        r = getattr(altro, metodo)(url, json=corpo) if corpo is not None else getattr(altro, metodo)(url)
        assert r.status_code == 403, (metodo, url, r.status_code, r.text)
        assert "Super Admin" in r.json()["detail"], r.text
    assert _impronta(m) == prima
    assert _q(m, "SELECT status, name FROM agencies WHERE id = %s", (q,))[0] == stato_prima
    assert _q(m, "SELECT status FROM operator_users WHERE id = %s", (m["persone"]["agente"],))[0][0] == "active"
    negati = _q(m, "SELECT count(*) FROM platform_audit_log WHERE action = 'platform.super_admin.required' "
                   "AND result = 'denied' AND actor_user_id = %s", (m["persone"]["altro"],))[0][0]
    # le tre rotte C2 registrano il rifiuto con l'azione del proprio modulo
    assert negati - negati_prima == len(richieste) - 3
    # l'uscita dall'agenzia resta aperta a ogni platform admin (idempotente)
    assert altro.post("/api/platform/agency-context/exit").status_code == 204


def test_03_un_tenant_ordinario_conserva_i_suoi_permessi(adm1):
    m = adm1
    agente = m["accedi"]("agente")
    assert agente.get("/api/operator-auth/me").status_code == 200
    assert agente.get("/api/platform/me").status_code == 403
    assert agente.get("/api/platform/agencies").status_code == 403
    assert _viva(_token(agente))


def test_04_l_account_designato_non_si_disabilita(adm1):
    m = adm1
    sa = m["accedi"]("super")
    uid = m["persone"]["super"]
    for stato in ("disabled", "invited"):
        r = sa.patch(f"/api/platform/operators/{uid}", json={"status": stato})
        assert r.status_code == 409, r.text
        assert "Super Admin generale designato" in r.json()["detail"]
    assert tuple(_q(m, "SELECT status, is_platform_admin FROM operator_users WHERE id = %s", (uid,))[0]) == ("active", True)
    assert _viva(_token(sa))
    assert sa.patch(f"/api/platform/operators/{uid}", json={"first_name": "Super A."}).status_code == 200
    assert sa.get("/api/platform/agencies").status_code == 200


def _attendi_lock(m, frammento: str) -> None:
    """Attende che una sessione del database sia in attesa di un lock su una query che contiene `frammento`."""
    limite = time.monotonic() + 20
    while time.monotonic() < limite:
        if _q(m, "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND query LIKE %s",
              (f"%{frammento}%",))[0][0]:
            return
        time.sleep(0.05)
    raise AssertionError("nessuna richiesta in attesa del lock")


def test_05_ruolo_tolto_mentre_la_modifica_attende_il_lock(adm1):
    m = adm1
    sa = m["accedi"]("super")
    uid, q = m["persone"]["super"], m["quattro"]
    tenuta = m["psycopg2"].connect(m["dsn"])
    esito = {}
    try:
        with tenuta.cursor() as cur:
            cur.execute("SELECT 1 FROM operator_users WHERE id = %s FOR UPDATE", (uid,))
            t = threading.Thread(target=lambda: esito.setdefault(
                "r", sa.patch(f"/api/platform/agencies/{q}", json={"status": "suspended"})))
            t.start()
            _attendi_lock(m, "FROM operator_users WHERE id = ")
            cur.execute("UPDATE operator_users SET is_platform_admin = FALSE WHERE id = %s", (uid,))
        tenuta.commit()
        t.join(30)
    finally:
        tenuta.close()
        _q(m, "UPDATE operator_users SET is_platform_admin = TRUE WHERE id = %s", (uid,))
    r = esito["r"]
    assert r.status_code == 403 and r.json()["code"] == "SUPER_ADMIN_REQUIRED", r.text
    assert _q(m, "SELECT status FROM agencies WHERE id = %s", (q,))[0][0] == "active"


def test_06_richieste_simultanee_non_aggirano_le_guardie(adm1):
    m = adm1
    sa = m["accedi"]("super")
    uid, q = m["persone"]["super"], m["quattro"]
    barriera = threading.Barrier(4)
    esiti = []

    def disabilita():
        barriera.wait()
        esiti.append(("disable", sa.patch(f"/api/platform/operators/{uid}", json={"status": "disabled"}).status_code))

    def sospendi():
        barriera.wait()
        esiti.append(("suspend", sa.patch(f"/api/platform/agencies/{q}", json={"status": "suspended"}).status_code))

    def entra():
        barriera.wait()
        esiti.append(("enter", sa.post(f"/api/platform/agencies/{q}/enter").status_code))

    fili = [threading.Thread(target=f) for f in (disabilita, disabilita, sospendi, entra)]
    for f in fili:
        f.start()
    for f in fili:
        f.join(60)
    codici = dict()
    for azione, codice in esiti:
        codici.setdefault(azione, []).append(codice)
    assert codici["disable"] == [409, 409], esiti
    assert codici["suspend"] == [200], esiti
    assert codici["enter"][0] in (200, 409), esiti
    assert tuple(_q(m, "SELECT status, is_platform_admin FROM operator_users WHERE id = %s", (uid,))[0]) == ("active", True)
    assert _q(m, "SELECT status FROM agencies WHERE id = %s", (q,))[0][0] == "suspended"
    # comunque sia andato l'ordine, nessuno resta dentro un'agenzia sospesa CON
    # PIENI POTERI. SENTINELLA AGGIORNATA DA ADM-5B: un ingresso arrivato DOPO la
    # sospensione e' la consultazione in sola lettura del Super Admin
    # designato; un acting aperto PRIMA viene chiuso dalla sospensione (ADM-1).
    dentro = _q(m, "SELECT operator_user_id FROM operator_sessions WHERE acting_agency_id = %s", (q,))
    assert all(r[0] == uid for r in dentro), dentro
    if dentro:
        assert sa.get("/api/operator-auth/me").json()["acting"]["read_only"] is True
        assert sa.post("/api/platform/agency-context/exit").status_code == 204
    assert _viva(_token(sa))
    assert sa.patch(f"/api/platform/agencies/{q}", json={"status": "active"}).status_code == 200


def test_07_operatore_disabilitato_e_riattivato(adm1):
    m = adm1
    uid = m["persone"]["agente"]
    prima = _impronta(m)
    c1, c2 = m["accedi"]("agente"), m["accedi"]("agente")
    t1, t2 = _token(c1), _token(c2)
    assert _viva(t1) and _viva(t2)
    sa = m["accedi"]("super")
    assert sa.patch(f"/api/platform/operators/{uid}", json={"status": "disabled"}).status_code == 200
    assert not _viva(t1) and not _viva(t2)
    assert _q(m, "SELECT count(*) FROM operator_sessions WHERE operator_user_id = %s AND revoked_at IS NULL",
              (uid,))[0][0] == 0
    assert c1.get("/api/operator-auth/me").status_code == 401
    assert sa.patch(f"/api/platform/operators/{uid}", json={"status": "active"}).status_code == 200
    assert not _viva(t1) and not _viva(t2)              # la riattivazione non riesuma nulla
    assert c2.get("/api/operator-auth/me").status_code == 401
    nuova = m["accedi"]("agente")                       # nuova autenticazione
    assert _viva(_token(nuova))
    dopo = _impronta(m)
    assert dopo == prima
    assert _q(m, "SELECT status FROM agency_memberships WHERE operator_user_id = %s AND agency_id = 2", (uid,))[0][0] == "active"


def test_08_membership_sospesa_o_revocata(adm1):
    m = adm1
    uid = m["persone"]["membro"]
    sa = m["accedi"]("super")
    url = f"/api/platform/agencies/2/operators/{uid}/membership"
    vecchia = _token(m["accedi"]("membro"))
    assert sa.patch(url, json={"status": "suspended"}).status_code == 200
    assert not _viva(vecchia)
    assert sa.patch(url, json={"status": "active"}).status_code == 200
    assert not _viva(vecchia)
    ancora = _token(m["accedi"]("membro"))
    assert _viva(ancora)
    assert sa.patch(url, json={"status": "revoked"}).status_code == 200
    assert not _viva(ancora)
    assert _q(m, "SELECT status FROM agency_memberships WHERE operator_user_id = %s AND agency_id = 2", (uid,))[0][0] == "revoked"
    # la riga storica resta, e il Super Admin che non e' membro non perde la sua sessione
    assert _viva(_token(sa))


def test_09_agenzia_sospesa(adm1):
    m = adm1
    q = m["quattro"]
    prima = _impronta(m)
    assegnazioni = _q(m, "SELECT id, status FROM agency_territory_assignments WHERE agency_id = %s ORDER BY id", (q,))
    membro = m["accedi"]("quattro")
    vecchia = _token(membro)
    sa = m["accedi"]("super")
    assert sa.post(f"/api/platform/agencies/{q}/enter").status_code == 200
    assert _q(m, "SELECT count(*) FROM operator_sessions WHERE acting_agency_id = %s", (q,))[0][0] == 1
    assert sa.patch(f"/api/platform/agencies/{q}", json={"status": "suspended"}).status_code == 200
    assert not _viva(vecchia)
    assert _q(m, "SELECT count(*) FROM operator_sessions WHERE acting_agency_id = %s", (q,))[0][0] == 0
    assert _viva(_token(sa))                            # il Super Admin resta collegato
    assert sa.patch(f"/api/platform/agencies/{q}", json={"status": "active"}).status_code == 200
    assert not _viva(vecchia)
    assert membro.get("/api/operator-auth/me").status_code == 401
    assert _viva(_token(m["accedi"]("quattro")))
    assert _q(m, "SELECT id, status FROM agency_territory_assignments WHERE agency_id = %s ORDER BY id", (q,)) == assegnazioni
    assert _impronta(m) == prima


def test_10_agenzia_di_sistema_e_archiviazione(adm1):
    m = adm1
    sa = m["accedi"]("super")
    sistema, q = m["sistema"], m["quattro"]
    for stato in ("suspended", "archived"):
        r = sa.patch(f"/api/platform/agencies/{sistema}", json={"status": stato})
        assert r.status_code == 409 and "agenzia di sistema" in r.json()["detail"], r.text
    assert _q(m, "SELECT status FROM agencies WHERE id = %s", (sistema,))[0][0] == "active"
    assert sa.patch(f"/api/platform/agencies/{sistema}", json={"name": "STIMA360"}).status_code == 200
    r = sa.patch(f"/api/platform/agencies/{q}", json={"status": "archived"})
    assert r.status_code == 409 and "sospesa" in r.json()["detail"], r.text
    assert sa.patch(f"/api/platform/agencies/{q}", json={"status": "suspended"}).status_code == 200
    assert sa.patch(f"/api/platform/agencies/{q}", json={"status": "archived"}).status_code == 200
    assert sa.patch(f"/api/platform/agencies/{q}", json={"status": "active"}).status_code == 200


def test_11_nessun_dato_cancellato_e_audit_completo(adm1):
    m = adm1
    prima = _impronta(m)
    audit_prima = _q(m, "SELECT count(*) FROM platform_audit_log")[0][0]
    sa = m["accedi"]("super")
    q = m["quattro"]
    for stato in ("suspended", "archived", "active"):
        assert sa.patch(f"/api/platform/agencies/{q}", json={"status": stato}).status_code == 200
    uid = m["persone"]["agente"]
    for stato in ("disabled", "active"):
        assert sa.patch(f"/api/platform/operators/{uid}", json={"status": stato}).status_code == 200
    assert _impronta(m) == prima
    aggiornamenti = _q(m, "SELECT count(*) FROM platform_audit_log WHERE id > (SELECT max(id) FROM platform_audit_log) - 100 "
                          "AND action IN ('platform.agency.update', 'platform.operator.update') AND result = 'success'")[0][0]
    assert aggiornamenti >= 5
    assert _q(m, "SELECT count(*) FROM platform_audit_log")[0][0] > audit_prima
