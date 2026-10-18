"""ADM-5A - il registro delle operazioni amministrative, in sola lettura.

PostgreSQL usa-e-getta e sessioni vere (fixture `adm2`, come ADM-2/3/4).

  01  solo il Super Admin designato legge il registro (403 / 401 agli altri)
  02  i cambi di stato di agenzia, operatore, appartenenza, assegnazione e alias
      registrano da ora il valore prima e dopo; l'ordine e' dal piu' recente;
      chi ha agito e' l'id e l'etichetta originali
  03  le ammissioni restano fuori, salvo filtro esplicito
  04  filtri per esito, agenzia, oggetto e periodo; parametri non validi -> 422
  05  paginazione: pagine contigue senza sovrapposizioni, massimo imposto
  06  leggere non scrive nulla oltre all'ammissione, e le righe passate restano
      come erano
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.test_adm1_super_admin_postgres import adm1  # noqa: F401
from tests.test_adm2_impact_postgres import adm2  # noqa: F401
from tests.test_c1_lettura_territoriale_postgres import rete  # noqa: F401
from tests.test_censimento_3_backend_postgres import DSN, _q, completo, mondo  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

URL = "/api/platform/audit"


def _ok(r):
    assert r.status_code in (200, 201), r.text
    return r.json()


def _registro(client, **params):
    return _ok(client.get(URL, params=params))


def test_01_solo_il_super_admin_designato(adm2):
    from fastapi.testclient import TestClient
    m = adm2
    assert m["accedi"]("super").get(URL).status_code == 200
    r = m["accedi"]("altro").get(URL)
    assert r.status_code == 403 and "Super Admin" in r.json()["detail"], r.text
    assert m["accedi"]("agente").get(URL).status_code == 403
    assert TestClient(m["app"], base_url="https://testserver").get(URL).status_code == 401


def test_02_i_cambi_di_stato_registrano_prima_e_dopo(adm2):
    m = adm2
    sa, q, membro = m["accedi"]("super"), m["quattro"], m["persone"]["membro"]
    inizio = datetime.now(timezone.utc) - timedelta(seconds=1)
    passi = [
        ("platform.agency.update", "agency", q, f"/api/platform/agencies/{q}", "suspended", "active"),
        ("platform.operator.update", "operator", membro, f"/api/platform/operators/{membro}", "disabled", "active"),
        ("platform.membership.update", "operator", membro,
         f"/api/platform/agencies/2/operators/{membro}/membership", "suspended", "active"),
        ("platform.territory.assignment.update", "territory_assignment", m["assegnazione"],
         f"/api/platform/agencies/{q}/territories/{m['assegnazione']}", "suspended", "active"),
    ]
    for _azione, _tipo, _id, url, andata, ritorno in passi:
        _ok(sa.patch(url, json={"status": andata}))
        _ok(sa.patch(url, json={"status": ritorno}))
    sa = m["accedi"]("super")  # le sessioni dei membri sono revocate; il Super Admin rientra comunque
    _ok(sa.patch(f"/api/platform/aliases/{m['alias']}", json={"status": "revoked"}))

    for azione, tipo, oggetto, _url, andata, ritorno in passi:
        righe = _registro(sa, action=azione, target_type=tipo, target_id=str(oggetto),
                          since=inizio.isoformat())
        assert len(righe) == 2, (azione, righe)
        recente, prima = righe
        assert recente["created_at"] >= prima["created_at"] and recente["id"] > prima["id"]
        assert prima["metadata"]["status_change"] == {"from": ritorno, "to": andata}, prima
        assert recente["metadata"]["status_change"] == {"from": andata, "to": ritorno}, recente
        for riga in righe:
            assert riga["result"] == "success"
            assert riga["actor_user_id"] == m["persone"]["super"]
            assert riga["actor_label"] == f"operator:{m['persone']['super']}"
    alias = _registro(sa, action="platform.territory.alias.update", target_id=str(m["alias"]))[0]
    assert alias["metadata"]["status_change"] == {"from": "active", "to": "revoked"}
    assert alias["metadata"]["from_status"] == "active"  # le chiavi di prima restano


def test_02b_un_aggiornamento_senza_cambio_di_stato_non_inventa_un_cambio(adm2):
    m = adm2
    sa, q = m["accedi"]("super"), m["quattro"]
    _ok(sa.patch(f"/api/platform/agencies/{q}", json={"name": "Quattro"}))
    riga = _registro(sa, action="platform.agency.update", target_id=str(q), limit=1)[0]
    assert riga["metadata"] == {"changed_fields": ["name"]}


def test_03_le_ammissioni_solo_se_chieste(adm2):
    sa = adm2["accedi"]("super")
    sa.get("/api/platform/me")
    assert all(r["action"] != "platform.admission" for r in _registro(sa, limit=200))
    ammissioni = _registro(sa, action="platform.admission", limit=5)
    assert ammissioni and all(r["action"] == "platform.admission" for r in ammissioni)


def test_04_filtri(adm2):
    m = adm2
    sa, altro, q = m["accedi"]("super"), m["accedi"]("altro"), m["quattro"]
    inizio = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert altro.patch(f"/api/platform/agencies/{q}", json={"status": "suspended"}).status_code == 403
    _ok(sa.patch(f"/api/platform/agencies/{q}", json={"name": "Quattro"}))

    negati = _registro(sa, result="denied", since=inizio.isoformat())
    assert negati and all(r["result"] == "denied" for r in negati)
    assert any(r["actor_user_id"] == m["persone"]["altro"] for r in negati)

    dell_agenzia = _registro(sa, agency_id=q, since=inizio.isoformat())
    assert dell_agenzia and all(r["target_agency_id"] == q for r in dell_agenzia)

    futuro = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert _registro(sa, since=futuro) == []
    assert _registro(sa, since="2000-01-01T00:00:00+00:00", until="2000-01-02T00:00:00+00:00") == []
    prima_dell_inizio = _registro(sa, until=inizio.isoformat(), limit=200)
    assert all(datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) < inizio
               for r in prima_dell_inizio)
    assert _registro(sa, action="platform.non.esiste") == []

    assert sa.get(URL, params={"since": futuro, "until": inizio.isoformat()}).status_code == 422
    assert sa.get(URL, params={"limit": 201}).status_code == 422
    assert sa.get(URL, params={"limit": 0}).status_code == 422
    assert sa.get(URL, params={"offset": -1}).status_code == 422


def test_05_paginazione(adm2):
    m = adm2
    sa, q = m["accedi"]("super"), m["quattro"]
    for nome in ("Quattro A", "Quattro B", "Quattro C", "Quattro"):
        _ok(sa.patch(f"/api/platform/agencies/{q}", json={"name": nome}))
    tutte = _registro(sa, action="platform.agency.update", limit=4)
    pagine = (_registro(sa, action="platform.agency.update", limit=2, offset=0)
              + _registro(sa, action="platform.agency.update", limit=2, offset=2))
    assert [r["id"] for r in pagine] == [r["id"] for r in tutte]
    assert len({r["id"] for r in pagine}) == 4
    assert len(_registro(sa)) <= 50


def test_06_leggere_non_scrive_e_il_passato_resta(adm2):
    m = adm2
    sa = m["accedi"]("super")
    righe_prima = _q(m, "SELECT * FROM platform_audit_log WHERE action <> 'platform.admission' ORDER BY id")
    for _ in range(3):
        _registro(sa, limit=200)
        _registro(sa, action="platform.admission", limit=10)
    righe_dopo = _q(m, "SELECT * FROM platform_audit_log WHERE action <> 'platform.admission' ORDER BY id")
    assert righe_dopo == righe_prima
