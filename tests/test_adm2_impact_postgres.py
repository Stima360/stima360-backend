"""ADM-2 - anteprima delle conseguenze ("Verifica conseguenze").

PostgreSQL usa-e-getta e sessioni VERE, come ADM-1 (stessa fixture `adm1`).

  01  solo il Super Admin designato legge le anteprime (altro platform admin,
      tenant, anonimo: rifiutati)
  02  i conteggi corrispondono ai dati (confronto con query indipendenti)
  03  l'anteprima non scrive: transazione READ ONLY nel database, impronta dei
      dati invariata, solo la riga di ammissione nel registro
  04  agenzie attive, sospese, archiviate e di sistema
  05  dipendenze territoriali: Comune, instradamento, C1, conflitti; alias;
      operatore (Supervisore unico, Super Admin designato)

ADM-3 ha tolto «Verifica conseguenze» dall'interfaccia ordinaria: gli endpoint
restano come controlli tecnici in sola lettura (provati qui). L'assenza dei
pulsanti e' provata in `test_adm3_continuity_postgres.py`.
"""
from __future__ import annotations

import pytest

from tests.test_adm1_super_admin_postgres import _impronta, adm1  # noqa: F401
from tests.test_c1_lettura_territoriale_postgres import rete  # noqa: F401
from tests.test_censimento_3_backend_postgres import DSN, _q, completo, mondo  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

COMUNE = "Comune Quattro"
NORM = "lower(btrim(regexp_replace({}, '\\s+', ' ', 'g')))"


@pytest.fixture
def adm2(adm1):
    """Dati di prova dell'agenzia Quattro: contatti assegnati al Supervisore, un
    immobile proprio e uno di un'altra agenzia nel Comune, una stima, un alias,
    un'assegnazione sospesa concorrente dell'agenzia 2."""
    m = adm1
    q, owner, t = m["quattro"], m["persone"]["quattro"], m["territorio_quattro"]
    # ogni riga e' reinserita se una pulizia di fixture (C1/Censimento) l'ha tolta
    if not _q(m, "SELECT 1 FROM contacts WHERE display_name LIKE 'ADM2 %%'"):
        for i in range(3):
            _q(m, "INSERT INTO contacts (agency_id, display_name, phone, assigned_agent_id) VALUES (%s, %s, %s, %s)",
               (q, f"ADM2 contatto {i}", f"33300000{i}0", owner if i < 2 else None))
    for agenzia, titolo in ((q, "ADM2 casa propria"), (2, "ADM2 casa di Due")):
        if not _q(m, "SELECT 1 FROM properties WHERE title = %s", (titolo,)):
            _q(m, "INSERT INTO properties (agency_id, title, city) VALUES (%s, %s, %s)", (agenzia, titolo, COMUNE))
    if not _q(m, "SELECT 1 FROM stime WHERE agency_id = %s AND comune = %s", (q, COMUNE)):
        _q(m, "INSERT INTO stime (agency_id, comune, via, mq) VALUES (%s, %s, 'Via Prova', 80)", (q, COMUNE))
    if not _q(m, "SELECT 1 FROM network_territory_aliases WHERE territory_id = %s AND status = 'active'", (t,)):
        _q(m, "INSERT INTO network_territory_aliases (territory_id, source, match_value) "
              "VALUES (%s, 'public_stima_comune', %s)", (t, COMUNE))
    if not _q(m, "SELECT 1 FROM agency_territory_assignments WHERE territory_id = %s AND agency_id = 2", (t,)):
        _q(m, "INSERT INTO agency_territory_assignments (territory_id, agency_id, status) VALUES (%s, 2, 'suspended')",
           (t,))
    m["assegnazione"] = _q(m, "SELECT id FROM agency_territory_assignments WHERE territory_id = %s AND agency_id = %s "
                              "AND status = 'active'", (t, q))[0][0]
    m["alias"] = _q(m, "SELECT id FROM network_territory_aliases WHERE territory_id = %s AND status = 'active'",
                    (t,))[0][0]
    return m


def _url(m):
    return {
        "agenzia": f"/api/platform/agencies/{m['quattro']}/impact",
        "operatore": f"/api/platform/operators/{m['persone']['quattro']}/impact",
        "assegnazione": f"/api/platform/agencies/{m['quattro']}/territories/{m['assegnazione']}/impact",
        "alias": f"/api/platform/aliases/{m['alias']}/impact",
    }


def _voci(anteprima, sezione):
    return {v["key"]: v for v in anteprima[sezione]}


def _uno(m, sql, params=()):
    return _q(m, sql, params)[0][0]


# ---------------------------------------------------------------------------

def test_01_solo_il_super_admin_designato(adm2):
    from fastapi.testclient import TestClient
    m = adm2
    sa, altro, agente = m["accedi"]("super"), m["accedi"]("altro"), m["accedi"]("agente")
    anonimo = TestClient(m["app"], base_url="https://testserver")
    for nome, url in _url(m).items():
        assert sa.get(url).status_code == 200, (nome, sa.get(url).text)
        r = altro.get(url)
        assert r.status_code == 403 and "Super Admin" in r.json()["detail"], (nome, r.text)
        assert agente.get(url).status_code == 403, nome
        assert anonimo.get(url).status_code == 401, nome
    assert sa.get("/api/platform/agencies/999999/impact").status_code == 404
    assert sa.get("/api/platform/operators/999999/impact").status_code == 404
    assert sa.get(f"/api/platform/agencies/{m['quattro']}/territories/999999/impact").status_code == 404
    assert sa.get("/api/platform/aliases/999999/impact").status_code == 404


def test_02_i_conteggi_corrispondono_ai_dati(adm2):
    m = adm2
    q, owner = m["quattro"], m["persone"]["quattro"]
    sa = m["accedi"]("quattro") and m["accedi"]("super")       # una sessione viva del Supervisore
    a = sa.get(_url(m)["agenzia"]).json()
    assert a["kind"] == "agency" and a["target"]["status"] == "active" and a["scenario"] == "suspend"
    conservati = _voci(a, "preserved")
    for chiave, sql in (
        ("properties", "SELECT count(*) FROM properties WHERE agency_id = %s AND deleted_at IS NULL"),
        ("buildings", "SELECT count(*) FROM buildings WHERE agency_id = %s AND deleted_at IS NULL"),
        ("contacts", "SELECT count(*) FROM contacts WHERE agency_id = %s AND deleted_at IS NULL"),
        ("leads", "SELECT count(*) FROM leads WHERE agency_id = %s"),
        ("appointments", "SELECT count(*) FROM appointments WHERE agency_id = %s"),
        ("acquisitions", "SELECT count(*) FROM acquisitions WHERE agency_id = %s"),
        ("assignments", "SELECT count(*) FROM agency_territory_assignments WHERE agency_id = %s "
                        "AND status IN ('active', 'suspended')"),
        ("memberships", "SELECT count(*) FROM agency_memberships WHERE agency_id = %s"),
    ):
        assert conservati[chiave]["count"] == _uno(m, sql, (q,)), chiave
    assert conservati["contacts"]["count"] >= 3 and conservati["properties"]["count"] >= 1
    accessi = _voci(a, "access_lost")
    assert accessi["members_agency_owner"]["count"] == 1
    assert accessi["member_sessions"]["count"] == _uno(
        m, "SELECT count(*) FROM operator_sessions WHERE operator_user_id = %s AND revoked_at IS NULL "
           "AND expires_at > NOW()", (owner,)) >= 1
    assert _voci(a, "may_stop")["recent_stime"]["count"] == _uno(
        m, "SELECT count(*) FROM stime WHERE agency_id = %s AND data >= NOW() - INTERVAL '90 days'", (q,)) >= 1

    o = sa.get(_url(m)["operatore"]).json()
    assert o["target"]["memberships"][0]["agency_id"] == q and o["target"]["memberships"][0]["role"] == "agency_owner"
    assert _voci(o, "preserved")["contacts"]["count"] == _uno(
        m, "SELECT count(*) FROM contacts WHERE assigned_agent_id = %s AND deleted_at IS NULL", (owner,)) == 2
    assert _voci(o, "access_lost")["sessions"]["count"] >= 1


def test_03_l_anteprima_non_scrive(adm2, monkeypatch):
    from psycopg2 import errors
    from platform_admin import impact_repository, impact_service
    m = adm2
    sa = m["accedi"]("super")
    prima = _impronta(m)
    stato = lambda: (_q(m, "SELECT id, status, name FROM agencies ORDER BY id"),  # noqa: E731
                     _q(m, "SELECT id, status FROM agency_territory_assignments ORDER BY id"),
                     _q(m, "SELECT id, status FROM network_territory_aliases ORDER BY id"),
                     _q(m, "SELECT id, status, is_platform_admin FROM operator_users ORDER BY id"),
                     _q(m, "SELECT count(*) FROM stime"))
    stato_prima = stato()
    ultimo_audit = _uno(m, "SELECT coalesce(max(id), 0) FROM platform_audit_log")
    for _ in range(2):
        for url in _url(m).values():
            assert sa.get(url).status_code == 200
    assert _impronta(m) == prima
    assert stato() == stato_prima
    azioni = {r[0] for r in _q(m, "SELECT DISTINCT action FROM platform_audit_log WHERE id > %s", (ultimo_audit,))}
    assert azioni == {"platform.admission"}, azioni          # solo l'ammissione di ADM-1, nessun atto
    # il database stesso rifiuta una scrittura dentro l'anteprima
    originale = impact_repository.system_agency

    def scrive(cur):
        cur.execute("UPDATE agencies SET name = name WHERE id = %s", (m["quattro"],))
        return originale(cur)

    monkeypatch.setattr(impact_repository, "system_agency", scrive)
    with pytest.raises(errors.ReadOnlySqlTransaction):
        impact_service.agency_impact(m["quattro"])


def test_04_agenzie_sospese_archiviate_e_di_sistema(adm2):
    m = adm2
    sa, q = m["accedi"]("super"), m["quattro"]
    try:
        assert sa.patch(f"/api/platform/agencies/{q}", json={"status": "suspended"}).status_code == 200
        a = sa.get(_url(m)["agenzia"]).json()
        assert a["scenario"] == "archive" and a["access_lost"] == []
        assert "archive_order" not in _voci(a, "needs_decision")
        assert _voci(a, "preserved")["properties"]["count"] >= 1          # i dati restano contati
        assert sa.patch(f"/api/platform/agencies/{q}", json={"status": "archived"}).status_code == 200
        assert sa.get(_url(m)["agenzia"]).json()["scenario"] == "already_archived"
        t = sa.get(_url(m)["assegnazione"]).json()
        assert "non e' attivo" in _voci(t, "may_stop")["routing"]["note"]
    finally:
        assert sa.patch(f"/api/platform/agencies/{q}", json={"status": "active"}).status_code == 200
    s = sa.get(f"/api/platform/agencies/{m['sistema']}/impact").json()
    assert "system_agency" in _voci(s, "needs_decision")


def test_05_dipendenze_territoriali_alias_e_operatore(adm2):
    m = adm2
    sa, q = m["accedi"]("super"), m["quattro"]
    t = sa.get(_url(m)["assegnazione"]).json()
    assert t["target"]["territory_label"] == "Comune Quattro" and COMUNE in t["target"]["aliases"]
    assert t["target"]["agency_name"] == "Quattro" and t["scenario"] == "suspend_or_revoke"
    instradamento = _voci(t, "may_stop")["routing"]
    assert instradamento["count"] == 1 and "agenzia di sistema" in instradamento["note"]
    altri = _uno(m, f"SELECT count(*) FROM properties WHERE agency_id <> %s AND deleted_at IS NULL "
                    f"AND {NORM.format('city')} = {NORM.format('%s')}", (q, COMUNE))
    assert _voci(t, "may_stop")["c1_visible"]["count"] == altri >= 1
    assert _voci(t, "preserved")["own_properties"]["count"] == _uno(
        m, f"SELECT count(*) FROM properties WHERE agency_id = %s AND deleted_at IS NULL "
           f"AND {NORM.format('city')} = {NORM.format('%s')}", (q, COMUNE)) >= 1
    assert _voci(t, "may_stop")["recent_stime"]["count"] == _uno(
        m, f"SELECT count(*) FROM stime WHERE {NORM.format('comune')} = {NORM.format('%s')} "
           "AND data >= NOW() - INTERVAL '90 days'", (COMUNE,)) >= 1
    conflitti = [k for k in _voci(t, "needs_decision") if k.startswith("other_")]
    assert len(conflitti) == 1 and "final" in _voci(t, "needs_decision")
    # l'agenzia vede l'immobile di Due per competenza: la sua anteprima lo dice
    assert _voci(sa.get(_url(m)["agenzia"]).json(), "may_stop")["c1_visible"]["count"] >= 1

    al = sa.get(_url(m)["alias"]).json()
    assert al["target"]["match_value"] == COMUNE and al["target"]["agency_name"] == "Quattro"
    assert _voci(al, "may_stop")["c1_visible"]["count"] == altri

    o = sa.get(_url(m)["operatore"]).json()
    unico = _voci(o, "needs_decision")[f"sole_owner_{q}"]
    assert unico["count"] == 1                                           # un Comune attivo presidiato
    s = sa.get(f"/api/platform/operators/{m['persone']['super']}/impact").json()
    assert s["target"]["is_super_admin"] is True and "super_admin" in _voci(s, "needs_decision")
