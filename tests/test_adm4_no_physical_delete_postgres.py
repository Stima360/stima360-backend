"""ADM-4 - le operazioni amministrative della Rete non cancellano nulla.

  01  (statico) i moduli della Rete - piattaforma, instradamento, accesso degli
      operatori - non contengono SQL DELETE o TRUNCATE, e il router di
      piattaforma non espone alcuna rotta DELETE
  02  (PostgreSQL usa-e-getta, sessioni vere, fixture `adm2`) revoca e
      riattivazione di un'appartenenza, revoca di un alias (che per regola non
      torna attivo: se ne dichiara uno nuovo),
      sospensione e riattivazione di un'assegnazione, trasferimento di un
      territorio e ritorno: nessuna riga sparisce dalle tabelle della Rete, le
      righe storiche restano con il loro stato, i dati CRM non cambiano ne'
      proprietario ne' quantita'
  03  le stesse operazioni restano del solo Super Admin designato

Sospensione, archiviazione e disattivazione dell'operatore sono gia' provate
da `test_adm3_continuity_postgres::test_03`.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_adm1_super_admin_postgres import _impronta, adm1  # noqa: F401
from tests.test_adm2_impact_postgres import COMUNE, adm2  # noqa: F401
from tests.test_c1_lettura_territoriale_postgres import rete  # noqa: F401
from tests.test_censimento_3_backend_postgres import DSN, _q, completo, mondo  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
MODULI_RETE = ("platform_admin", "network_routing", "operator_auth")
TABELLE_RETE = ("agencies", "operator_users", "agency_memberships", "network_territories",
                "network_territory_aliases", "agency_territory_assignments")


def _codice(percorso: Path) -> str:
    testo = percorso.read_text(encoding="utf-8")
    testo = re.sub(r'"{3}[\s\S]*?"{3}', "", testo)
    return re.sub(r"#[^\n]*", "", testo)


def test_01_nessuna_delete_nei_moduli_della_rete():
    for modulo in MODULI_RETE:
        for f in sorted((ROOT / modulo).glob("*.py")):
            codice = _codice(f)
            assert not re.search(r"\bDELETE\s+FROM\b|\bTRUNCATE\b", codice, re.I), f
    from platform_admin.router import router
    metodi = {(r.path, m) for r in router.routes for m in getattr(r, "methods", ())}
    assert not [x for x in metodi if x[1] == "DELETE"], metodi


def _righe(m):
    return {t: _q(m, f"SELECT id, status FROM {t} ORDER BY id") if t != "network_territories"
            else _q(m, f"SELECT id, NULL FROM {t} ORDER BY id") for t in TABELLE_RETE}


def _proprietari(m):
    return {t: _q(m, f"SELECT id, agency_id FROM {t} ORDER BY id")
            for t in ("properties", "contacts", "stime")}


@pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")
def test_02_le_operazioni_della_rete_conservano_righe_e_storico(adm2):
    m = adm2
    sa, q, t = m["accedi"]("super"), m["quattro"], m["territorio_quattro"]
    membro = m["persone"]["membro"]
    prima, impronta, proprietari = _righe(m), _impronta(m), _proprietari(m)
    assegnazione, alias = m["assegnazione"], m["alias"]

    def ok(r):
        assert r.status_code in (200, 201), r.text
        return r.json()

    ok(sa.patch(f"/api/platform/agencies/2/operators/{membro}/membership", json={"status": "revoked"}))
    ok(sa.patch(f"/api/platform/agencies/2/operators/{membro}/membership", json={"status": "active"}))
    ok(sa.patch(f"/api/platform/aliases/{alias}", json={"status": "revoked"}))
    nuovo = ok(sa.post(f"/api/platform/territories/{t}/aliases",
                       json={"source": "public_stima_comune", "match_value": COMUNE}))
    ok(sa.patch(f"/api/platform/agencies/{q}/territories/{assegnazione}", json={"status": "suspended"}))
    ok(sa.patch(f"/api/platform/agencies/{q}/territories/{assegnazione}", json={"status": "active"}))
    andata = ok(sa.post(f"/api/platform/territories/{t}/transfer", json={"agency_id": m["sistema"]}))
    ritorno = ok(sa.post(f"/api/platform/territories/{t}/transfer", json={"agency_id": q}))

    dopo = _righe(m)
    for tabella in TABELLE_RETE:
        ids_prima = {r[0] for r in prima[tabella]}
        assert ids_prima <= {r[0] for r in dopo[tabella]}, f"righe sparite da {tabella}"
    # lo storico del trasferimento: l'assegnazione di partenza e quella di
    # passaggio restano, revocate, con i loro id
    stato = dict(_q(m, "SELECT id, status FROM agency_territory_assignments WHERE territory_id = %s", (t,)))
    assert stato[assegnazione] == "revoked"
    assert stato[andata["assignment"]["id"]] == "revoked"
    assert stato[ritorno["assignment"]["id"]] == "active"
    assert andata["revoked"]["id"] == assegnazione
    # i dati del CRM non sono stati toccati (le tabelle della Rete crescono di
    # righe nuove, gia' verificate sopra: nessuna e' sparita)
    crm = lambda i: {k: v for k, v in i.items() if k not in TABELLE_RETE}  # noqa: E731
    assert crm(_impronta(m)) == crm(impronta)
    assert _proprietari(m) == proprietari
    # stato di partenza ripristinato per gli altri test della fixture
    assert _q(m, "SELECT status FROM agency_memberships WHERE agency_id = 2 AND operator_user_id = %s",
              (membro,))[0][0] == "active"
    # l'alias revocato resta come storico, accanto al nuovo
    assert dict(_q(m, "SELECT id, status FROM network_territory_aliases WHERE id IN (%s, %s)",
                   (alias, nuovo["id"]))) == {alias: "revoked", nuovo["id"]: "active"}


@pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")
def test_03_solo_il_super_admin_designato(adm2):
    m = adm2
    altro, q, t = m["accedi"]("altro"), m["quattro"], m["territorio_quattro"]
    prima = _righe(m)
    rifiuti = [
        altro.patch(f"/api/platform/agencies/{q}", json={"status": "suspended"}),
        altro.patch(f"/api/platform/operators/{m['persone']['quattro']}", json={"status": "disabled"}),
        altro.patch(f"/api/platform/agencies/2/operators/{m['persone']['membro']}/membership",
                    json={"status": "revoked"}),
        altro.patch(f"/api/platform/aliases/{m['alias']}", json={"status": "revoked"}),
        altro.post(f"/api/platform/territories/{t}/transfer", json={"agency_id": m["sistema"]}),
    ]
    assert [r.status_code for r in rifiuti] == [403] * len(rifiuti), [r.text for r in rifiuti]
    assert _righe(m) == prima
