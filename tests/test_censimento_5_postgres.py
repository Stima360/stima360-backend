"""CENSIMENTO-1 Fase 5 - conteggi e liste: una scheda di censimento NON e' un
immobile operativo finche' non e' presa in carico.

Sul database VERO (083 applicata), attraverso le rotte reali:

  * l'elenco immobili di default (`GET /api/property/properties`, usato dal tab
    Commerciale, dai selettori operativi e da property_admin) restituisce solo
    le schede operative `record_kind = 'crm'`; le schede `census` si leggono
    chiedendole (`record_kind=census`) o insieme (`record_kind=all`, per la
    ricerca globale e «Collega esistente» del censimento);
  * la dashboard immobili conta solo le operative; le unita' censite hanno il
    loro contatore (`census_units`);
  * «Prendi in carico» sposta la STESSA riga da census a crm: compare
    nell'operativo, sparisce dal censimento, nessun duplicato;
  * con soli record `crm` i numeri sono identici a prima (decisione 3).

Stessa fixture di `test_censimento_3_backend_postgres` (P29_TEST_DSN,
database locale usa-e-getta); senza DSN il modulo e' SKIP.
"""
from __future__ import annotations

import pytest

from tests.test_censimento_3_backend_postgres import (  # noqa: F401  (fixture riusate)
    DSN, _edificio, _q, _unita, completo, mondo,
)

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare CENSIMENTO-1 Fase 5")


def _ids(r):
    assert r.status_code == 200, r.text
    return {x["id"] for x in r.json()["items"]}


def _crm_storici(m):
    """Le schede operative della fixture (agenzia 1), non archiviate."""
    return {r[0] for r in _q(m, "SELECT id FROM properties WHERE agency_id = 1 AND record_kind = 'crm' AND archived_at IS NULL")}


def _scenario(m):
    """1 palazzina, 3 unita' censite (una pertinenza), 1 immobile singolo censito."""
    e = _edificio(m)
    u1 = _unita(m, building_id=e["id"], floor="1", internal_number="1")
    u2 = _unita(m, building_id=e["id"], floor="2", internal_number="2")
    box = _unita(m, building_id=e["id"], parent_property_id=u1["id"], property_type="garage")
    singolo = _unita(m, city="Fermo", address="Via Po", civic_number="3")
    return e, [u1, u2, box, singolo]


def test_01_elenco_operativo_di_default_esclude_le_schede_census(mondo):
    api = mondo["api"]()
    prima = _ids(api.get("/api/property/properties?limit=200"))
    _, censite = _scenario(mondo)
    ids_census = {u["id"] for u in censite}
    dopo = _ids(api.get("/api/property/properties?limit=200"))
    assert dopo == prima, "le unita' censite sono entrate nell'elenco operativo"
    assert not (dopo & ids_census)
    # e restano fuori anche dalla ricerca operativa per indirizzo e dai filtri
    assert not (_ids(api.get("/api/property/properties?search=Via%20Roma&limit=200")) & ids_census)
    assert not (_ids(api.get("/api/property/properties?status=draft&limit=200")) & ids_census)


def test_02_le_schede_census_si_leggono_chiedendole_e_insieme(mondo):
    api = mondo["api"]()
    _, censite = _scenario(mondo)
    ids_census = {u["id"] for u in censite}
    assert _ids(api.get("/api/property/properties?record_kind=census&limit=200")) == ids_census
    tutte = _ids(api.get("/api/property/properties?record_kind=all&limit=200"))
    assert ids_census <= tutte and _crm_storici(mondo) <= tutte
    # «Collega esistente» del censimento e la ricerca globale: entrambi i tipi
    assert ids_census & _ids(api.get("/api/property/properties?record_kind=all&search=Via%20Roma&limit=200"))
    # valore non previsto: rifiutato, non interpretato
    assert api.get("/api/property/properties?record_kind=commerciale").status_code == 422
    # isolamento tra agenzie invariato
    assert not (_ids(mondo["api"]("owner_b").get("/api/property/properties?record_kind=all&limit=200")) & ids_census)


def test_03_dashboard_conta_solo_le_operative_e_le_censite_a_parte(mondo):
    api = mondo["api"]()
    prima = api.get("/api/property/dashboard").json()
    _, censite = _scenario(mondo)
    # una censita classificata e con un documento mancante: non deve contare
    _q(mondo, "UPDATE properties SET classification = 'A' WHERE id = %s", (censite[0]["id"],))
    _q(mondo, "INSERT INTO property_documents (property_id, document_type, title, status) VALUES (%s, 'visura', 'Visura', 'missing')", (censite[0]["id"],))
    dopo = api.get("/api/property/dashboard").json()
    for chiave in ("total", "active", "class_a", "class_b", "class_c", "active_value", "expiring_mandates", "document_issues"):
        assert dopo[chiave] == prima[chiave], chiave
    assert not ({p["id"] for p in dopo["recent_properties"]} & {u["id"] for u in censite})
    assert dopo["census_units"] == len(censite) and prima["census_units"] == 0
    # gli avvisi documentali sono operativi: la censita non ne genera
    avvisi = api.get("/api/property/alerts").json()["items"]
    assert not [a for a in avvisi if a["property_id"] == censite[0]["id"]]
    _q(mondo, "DELETE FROM property_documents WHERE property_id = %s", (censite[0]["id"],))


def test_04_con_soli_record_crm_i_numeri_sono_identici_a_prima(mondo):
    """Decisione 3: senza schede census il filtro non cambia nulla. Il totale
    e' calcolato qui in SQL con la regola di sempre (archived_at IS NULL)."""
    api = mondo["api"]()
    d = api.get("/api/property/dashboard").json()
    assert d["total"] == _q(mondo, "SELECT count(*) FROM properties WHERE agency_id = 1 AND archived_at IS NULL")[0][0]
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 0 (contratto REV 2, §18.3): gli
    # archiviati (`archived_at`) escono dall'elenco operativo, dalla ricerca e
    # dai contatori `active`; si vedono con `status=archived` o
    # `include_archived=true`. Il confronto "identico a prima" resta sui
    # record non archiviati, che e' cio' che la decisione 3 proteggeva.
    assert d["active"] == _q(mondo, "SELECT count(*) FROM properties WHERE agency_id = 1 AND archived_at IS NULL "
                                    "AND commercial_status IN ('mandate','active','reserved','under_offer')")[0][0]
    assert d["census_units"] == 0
    elenco = _ids(api.get("/api/property/properties?limit=200"))
    assert elenco == {r[0] for r in _q(mondo, "SELECT id FROM properties WHERE agency_id = 1 AND archived_at IS NULL")}
    assert elenco == _ids(api.get("/api/property/properties?record_kind=all&limit=200"))
    assert _ids(api.get("/api/property/properties?include_archived=true&limit=200")) == {
        r[0] for r in _q(mondo, "SELECT id FROM properties WHERE agency_id = 1")}


def test_05_presa_in_carico_sposta_la_stessa_riga_senza_duplicati(mondo):
    api = mondo["api"]()
    _, censite = _scenario(mondo)
    u1, _, box, _ = censite
    righe_prima = _q(mondo, "SELECT count(*) FROM properties")[0][0]
    totale_prima = api.get("/api/property/dashboard").json()["total"]
    r = api.post(f"/api/property/properties/{u1['id']}/take-in-charge", json={"include_pertinenze": True})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == u1["id"] and r.json()["code"] == u1["code"] and r.json()["record_kind"] == "crm"
    operative = _ids(api.get("/api/property/properties?limit=200"))
    censimento = _ids(api.get("/api/property/properties?record_kind=census&limit=200"))
    assert {u1["id"], box["id"]} <= operative and not ({u1["id"], box["id"]} & censimento)
    assert _q(mondo, "SELECT count(*) FROM properties")[0][0] == righe_prima         # nessuna riga nuova
    assert _q(mondo, "SELECT count(*) FROM properties WHERE code = %s", (u1["code"],))[0][0] == 1
    d = api.get("/api/property/dashboard").json()
    assert d["total"] == totale_prima + 2 and d["census_units"] == len(censite) - 2
    # la palazzina conta ancora tutte le sue unita' (lo stato non la svuota)
    edificio = api.get(f"/api/property/buildings/{u1['building_id']}").json()
    assert edificio["counters"]["units_census"] == 3


def test_06_relazioni_esplicite_vedono_entrambi_i_tipi_con_il_tipo_dichiarato(mondo):
    """Un collegamento gia' esistente (contatto/lead -> immobile) non si nasconde:
    la vista per relazione chiede `record_kind=all` e riceve il tipo per il badge."""
    api = mondo["api"]()
    _, censite = _scenario(mondo)
    contatto = _q(mondo, "INSERT INTO contacts (display_name, agency_id) VALUES ('Proprietario F5', 1) RETURNING id")[0][0]
    try:
        r = api.post(f"/api/property/properties/{censite[3]['id']}/contacts", json={"contact_id": contatto, "role": "owner"})
        assert r.status_code in (200, 201), r.text
        legati = api.get(f"/api/property/properties?contact_id={contatto}&record_kind=all&limit=50").json()["items"]
        assert [(x["id"], x["record_kind"]) for x in legati] == [(censite[3]["id"], "census")]
        assert api.get(f"/api/property/properties?contact_id={contatto}&limit=50").json()["items"] == []
    finally:
        _q(mondo, "DELETE FROM property_contacts WHERE contact_id = %s", (contatto,))
        _q(mondo, "DELETE FROM contacts WHERE id = %s", (contatto,))


def test_07_contact_360_tiene_le_unita_censite_collegate(mondo):
    from crm.service import get_contact_360
    _, censite = _scenario(mondo)
    contatto = _q(mondo, "INSERT INTO contacts (display_name, agency_id) VALUES ('Proprietario 360', 1) RETURNING id")[0][0]
    try:
        assert mondo["api"]().post(f"/api/property/properties/{censite[0]['id']}/contacts",
                                   json={"contact_id": contatto, "role": "owner"}).status_code in (200, 201)
        mondo["stato"]["chi"] = "owner_a"
        vista = get_contact_360(mondo["ctx"](), contatto)
        assert [(p["id"], p["record_kind"]) for p in vista["properties"]] == [(censite[0]["id"], "census")]
    finally:
        _q(mondo, "DELETE FROM property_contacts WHERE contact_id = %s", (contatto,))
        _q(mondo, "DELETE FROM contacts WHERE id = %s", (contatto,))
