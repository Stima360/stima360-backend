"""CENSIMENTO-1 Fase 3 - BACKEND, prove SENZA database.

Cio' che non ha bisogno di PostgreSQL: il retry (solo 40P01, tentativi
limitati, tutta l'operazione da capo), l'impronta di idempotenza, la
normalizzazione catastale in Python (la stessa della 083), la traduzione
degli errori del database, gli schemi (campi protetti -> 422, cataloghi), la
readiness e il contratto delle rotte (14, tutte dietro lo stesso gate, con
`code` accanto a `detail`). Il comportamento sul database vero e' in
`tests/test_censimento_3_backend_postgres.py`.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from psycopg2 import errors as pg_errors

from core.exceptions import ConflictError, NotFoundError, ValidationError
from property import census
from property.schemas import (AccessoryCreate, AccessoryResolve, BuildingCreate, BuildingUpdate,
                              CensusUnitCreate, PertinenzaLink, PropertyCreate, PropertyUpdate, TakeInCharge)


# ---------------------------------------------------------------------------
# A. retry, idempotenza, normalizzazione, traduzione
# ---------------------------------------------------------------------------

def _deadlock():
    return pg_errors.DeadlockDetected("deadlock detected")


def test_a01_il_retry_ripete_tutta_l_operazione_solo_sul_deadlock():
    chiamate = []

    def operazione():
        chiamate.append(1)
        if len(chiamate) < 3:
            raise _deadlock()
        return "ok"

    assert census._esegui(operazione) == "ok" and len(chiamate) == 3
    # esauriti i tentativi: errore leggibile, non un 500
    sempre = lambda: (_ for _ in ()).throw(_deadlock())  # noqa: E731
    with pytest.raises(ConflictError) as exc:
        census._esegui(sempre)
    assert exc.value.code == "RETRY_EXHAUSTED"
    # validazione, autorizzazione, unicita': MAI ritentate
    for errore in (ValidationError("x"), NotFoundError("x"), ConflictError("x")):
        conteggio = []

        def una_volta(errore=errore):
            conteggio.append(1)
            raise errore
        with pytest.raises(type(errore)):
            census._esegui(una_volta)
        assert conteggio == [1]


def test_a02_impronta_stabile_e_indipendente_da_chiave_e_conferma():
    a = {"city": "Fermo", "floor": "1", "surface_sqm": Decimal("45.50"), "client_request_id": uuid.uuid4(),
         "confirm_similar": True, "metadata": {"b": 1, "a": 2}}
    b = {"metadata": {"a": 2, "b": 1}, "surface_sqm": Decimal("45.50"), "floor": "1", "city": "Fermo",
         "confirm_similar": False, "client_request_id": uuid.uuid4()}
    assert census._fingerprint(a) == census._fingerprint(b) and len(census._fingerprint(a)) == 64
    assert census._fingerprint({**a, "floor": "2"}) != census._fingerprint(a)


@pytest.mark.parametrize("valore,atteso", [
    (None, None), ("", ""), ("  ", ""), (" a125 ", "A125"), ("0012", "12"), ("00 12", "12"),
    ("A 7", "A7"), ("0", "0"), ("00", ""), ("345", "345"), ("sub 04", "SUB04"),
])
def test_a03_normalizzazione_come_la_083(valore, atteso):
    assert census._norm(valore) == atteso


def _pg(cls, messaggio, vincolo=None):
    """Un errore psycopg2 con il nome del vincolo in `diag`, come lo
    costruisce il driver (l'attributo non e' assegnabile: si deriva)."""
    class _Diag:
        constraint_name = vincolo

    class _Errore(cls):
        diag = _Diag()
    return _Errore(messaggio)


def test_a04_gli_errori_del_database_diventano_errori_api_senza_sql():
    t = census._tradotto
    e = t(_pg(pg_errors.UniqueViolation, "duplicate", "uq_properties_cadastral_identity"))
    assert isinstance(e, ConflictError) and e.code == "CADASTRAL_DUPLICATE"
    assert isinstance(t(_pg(pg_errors.UniqueViolation, "duplicate", "uq_properties_client_request")), census._ReplicaRace)
    assert isinstance(t(_pg(pg_errors.UniqueViolation, "duplicate", "uq_buildings_client_request")), census._ReplicaRace)
    e = t(_pg(pg_errors.RaiseException, "CENSIMENTO-1: a census property cannot have commercial status active (take it in charge first)"))
    assert isinstance(e, ConflictError) and e.code == "CENSUS_LOCKED" and str(e) == census.CENSUS_LOCKED
    e = t(_pg(pg_errors.RaiseException, "CENSIMENTO-1: property 5 is itself a pertinenza of 4 and cannot be a parent"))
    assert isinstance(e, ValidationError) and e.code == "LINK_INVALID"
    e = t(_pg(pg_errors.RaiseException, "CENSIMENTO-1 tenancy: building 3 does not belong to agency 1 (property)"))
    assert isinstance(e, NotFoundError)
    e = t(_pg(pg_errors.CheckViolation, "violates check", "properties_belfiore_chk"))
    assert isinstance(e, ValidationError) and "Belfiore" not in str(e) or "comune" in str(e)
    assert t(_pg(pg_errors.DeadlockDetected, "deadlock")) is None       # lo gestisce il retry
    assert t(RuntimeError("altro")) is None
    for errore in (e, t(_pg(pg_errors.UniqueViolation, "x", "uq_properties_cadastral_identity"))):
        assert "SELECT" not in str(errore) and "uq_" not in str(errore)


def test_a05_catasto_validato_prima_del_database():
    data = {"cadastral_category": " c/6 ", "cadastral_municipality_code": " a125 "}
    census._check_cadastral(data)
    assert data == {"cadastral_category": "C/6", "cadastral_municipality_code": "A125"}
    with pytest.raises(ValidationError, match="catalogo"):
        census._check_cadastral({"cadastral_category": "A/99"})
    with pytest.raises(ValidationError, match="comune"):
        census._check_cadastral({"cadastral_municipality_code": "12AB"})
    vuoto = {"cadastral_category": "", "cadastral_municipality_code": ""}
    census._check_cadastral(vuoto)
    assert vuoto == {"cadastral_category": None, "cadastral_municipality_code": None}


# ---------------------------------------------------------------------------
# B. schemi: campi protetti e cataloghi
# ---------------------------------------------------------------------------

# SENTINELLA AGGIORNATA DA CREAZIONE-GUIDATA-1: CensusUnitCreate accetta
# `record_kind` ('census' | 'crm') per decisione esplicita della fase C («il tipo
# di scheda lo decide l'ingresso»: dall'elenco Commerciale le unita' in palazzina
# nascono commerciali). Restano rifiutati: un valore diverso, l'assegnazione su
# una scheda di censimento, e `record_kind` su presa in carico, POST e PATCH generici.
@pytest.mark.parametrize("modello,corpo", [
    (BuildingCreate, {"agency_id": 1}), (BuildingCreate, {"client_request_fingerprint": "a" * 64}),
    (BuildingCreate, {"archived_at": None}), (BuildingUpdate, {"agency_id": 1}), (BuildingUpdate, {"client_request_id": str(uuid.uuid4())}),
    (CensusUnitCreate, {"record_kind": "listing"}), (CensusUnitCreate, {"assigned_agent_id": 3}),
    (CensusUnitCreate, {"agency_id": 1}), (CensusUnitCreate, {"address_inherited": True}),
    (CensusUnitCreate, {"commercial_status": "active"}), (CensusUnitCreate, {"mandate_type": "esclusiva"}),
    (CensusUnitCreate, {"title": "x"}), (CensusUnitCreate, {"code": "IMM-1"}),
    (AccessoryCreate, {"kind": "box", "property_id": 1}), (AccessoryResolve, {"outcome": "separate", "agency_id": 1}),
    (TakeInCharge, {"record_kind": "crm"}), (PertinenzaLink, {"pertinenza_id": 1, "agency_id": 1}),
    (PropertyCreate, {"record_kind": "census"}), (PropertyCreate, {"building_id": 1}), (PropertyCreate, {"parent_property_id": 1}),
    (PropertyUpdate, {"record_kind": "crm"}), (PropertyUpdate, {"client_request_id": str(uuid.uuid4())}),
    (PropertyUpdate, {"address_inherited": False}), (PropertyUpdate, {"building_id": None}),
])
def test_b01_i_campi_decisi_dal_server_sono_rifiutati(modello, corpo):
    with pytest.raises(ValueError):
        modello(**corpo)


@pytest.mark.parametrize("modello,corpo", [
    (BuildingCreate, {"building_type": "grattacielo"}), (BuildingCreate, {"census_status": "x"}),
    (BuildingCreate, {"units_declared_source": "x"}), (BuildingCreate, {"units_declared": -1}),
    (CensusUnitCreate, {"property_type": "cantina"}), (CensusUnitCreate, {"surface_sqm": -1}),
    (AccessoryCreate, {"kind": "garage"}), (AccessoryCreate, {"kind": "box", "cadastral_status": "separate"}),
    (AccessoryResolve, {"outcome": "forse"}), (AccessoryResolve, {"outcome": "included", "property_type": "garage"}),
    (AccessoryResolve, {"outcome": "separate", "property_type": "x"}),
])
def test_b02_i_cataloghi_degli_schemi(modello, corpo):
    with pytest.raises(ValueError):
        modello(**corpo)


def test_b03_valori_di_default_del_contratto():
    assert BuildingCreate().building_type == "condominio" and BuildingCreate().census_status == "partial"
    assert BuildingCreate().confirm_similar is False and BuildingCreate().client_request_id is None
    u = CensusUnitCreate()
    assert u.property_type == "apartment" and u.whole_building is False and u.building_id is None and u.parent_property_id is None
    assert AccessoryCreate(kind="cantina").cadastral_status == "included"
    assert TakeInCharge().include_pertinenze is True
    assert AccessoryResolve(outcome="separate", existing_property_id=3).existing_property_id == 3
    # i dati catastali negli schemi generici: sezione '' (assente) e' un valore legittimo
    assert PropertyUpdate(cadastral_section="").cadastral_section == ""


# ---------------------------------------------------------------------------
# C. rotte e readiness
# ---------------------------------------------------------------------------

CENSUS_ROUTES = {
    ("GET", "/api/property/buildings"), ("POST", "/api/property/buildings"),
    ("GET", "/api/property/buildings/{building_id}"), ("PATCH", "/api/property/buildings/{building_id}"),
    ("POST", "/api/property/census/units"), ("GET", "/api/property/properties/{property_id}/census"),
    ("POST", "/api/property/properties/{property_id}/pertinenze/link"),
    ("POST", "/api/property/properties/{property_id}/pertinenze/{pertinenza_id}/unlink"),
    ("POST", "/api/property/properties/{property_id}/accessories"),
    ("PATCH", "/api/property/properties/{property_id}/accessories/{accessory_id}"),
    ("DELETE", "/api/property/properties/{property_id}/accessories/{accessory_id}"),
    ("POST", "/api/property/properties/{property_id}/accessories/{accessory_id}/resolve"),
    ("POST", "/api/property/properties/{property_id}/take-in-charge"),
    ("POST", "/api/property/properties/{property_id}/undo-create"),
}


def test_c01_quattordici_rotte_tutte_dietro_il_gate_di_sessione():
    from operator_auth.dependencies import legacy_basic_agency_context
    from property.router import router
    trovate = set()
    for r in router.routes:
        for m in r.methods:
            if (m, r.path) in CENSUS_ROUTES:
                trovate.add((m, r.path))
                dipendenze = {d.call for d in r.dependant.dependencies}
                assert legacy_basic_agency_context in dipendenze, (m, r.path)
    assert trovate == CENSUS_ROUTES
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 0: 40 -> 42 (POST .../archive e
    # POST .../unarchive, contratto REV 2 D11), entrambe dietro lo stesso gate.
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 2B1: 42 -> 45 (GET
    # .../deletion-check, POST .../trash, POST .../restore), stesso gate.
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 2B3: 45 -> 46 (GET
    # /api/property/trash, l'elenco della pagina «Cestino»), stesso gate.
    # SENTINELLA AGGIORNATA DA CATALOGO-CANONICO-1: 46 -> 50 (provenienza dal
    # sito: site-sources, conflicts, dismiss, relink), stesso gate (verificato qui sotto).
    from operator_auth.dependencies import legacy_basic_agency_context as _gate
    nuove = [r for r in router.routes if "/site-sources" in r.path]
    assert len(nuove) == 4 and all(_gate in {d.call for d in r.dependant.dependencies} for r in nuove)
    assert len(router.routes) == 50


def test_c02_gli_errori_portano_code_accanto_a_detail():
    from property.router import trc
    def fallisce():
        raise census.CensusConflict("simile", "SIMILAR_FOUND", similar=[{"id": 3}])
    r = trc(fallisce)
    assert r.status_code == 409 and r.body == b'{"similar":[{"id":3}],"detail":"simile","code":"SIMILAR_FOUND"}'
    def manca():
        raise NotFoundError("property 9 not found")
    r = trc(manca)
    assert r.status_code == 404 and b'"code":"NOT_FOUND"' in r.body
    def invalido():
        raise ValidationError("x")
    assert trc(invalido).status_code == 400
    assert trc(lambda: None).status_code == 204
    assert trc(lambda: {"a": 1}, status=201).status_code == 201
    # codice deployato prima della 083: 503 leggibile (CRM-OPS-3 RC-1), dall'eccezione
    # ESPLICITA del modulo; un errore qualunque del driver NON viene mascherato
    def senza_083():
        raise census.CensusNotInstalled(census.CENSUS_NOT_INSTALLED_MESSAGE)
    r = trc(senza_083)
    assert r.status_code == 503 and b'"code":"CENSUS_NOT_INSTALLED"' in r.body
    def altro_errore():
        raise pg_errors.UndefinedTable('relation "altro" does not exist')
    with pytest.raises(pg_errors.UndefinedTable):
        trc(altro_errore)
    from fastapi import HTTPException
    from property.router import tr
    with pytest.raises(HTTPException) as exc:
        tr(senza_083)
    assert exc.value.status_code == 503
    # repository: solo le colonne della 083 diventano "modulo non installato"
    from property import repository
    assert repository._census_not_installed(pg_errors.UndefinedColumn('column "cadastral_category" of relation "properties" does not exist')) is not None
    assert repository._census_not_installed(pg_errors.UndefinedColumn('column "altra" of relation "properties" does not exist')) is None


def test_c05_una_creazione_ordinaria_non_porta_le_colonne_della_083(monkeypatch):
    """Ordine DB-first: su un database senza la 083 la POST /properties di
    sempre resta la statement di sempre; le colonne nuove entrano solo se
    inviate."""
    from property import service
    from tests.test_crm_ops_2_property_form import FakeRepo, _ctx
    repo = FakeRepo()
    monkeypatch.setattr(service, "repository", repo)
    service.create_property(_ctx(), PropertyCreate(city=None))
    assert not set(repo.calls[0][1]) & set(service.CENSUS_SCHEMA_FIELDS)
    service.create_property(_ctx(), PropertyCreate(cadastral_category="a/3", staircase="B"))
    assert repo.calls[1][1]["cadastral_category"] == "A/3" and repo.calls[1][1]["staircase"] == "B"


def test_c03_readiness_e_guardie_di_service():
    from match.readiness import CENSUS_NOT_ELIGIBLE_REASON, property_readiness
    assert property_readiness({"id": 1, "commercial_status": "draft", "record_kind": "census"})["eligibility_reasons"][-1] == \
        CENSUS_NOT_ELIGIBLE_REASON == "Immobile in censimento"
    assert property_readiness({"id": 1, "commercial_status": "active", "archived_at": None, "record_kind": "crm"})["eligible"] is True
    assert property_readiness({"id": 1, "commercial_status": "active", "archived_at": None})["eligible"] is True   # storico senza colonna
    from property import service
    with pytest.raises(ConflictError, match="Prendi in carico"):
        service._check_census_guard({"commercial_status": "active"}, {"record_kind": "census"})
    with pytest.raises(ConflictError):
        service._check_census_guard({"mandate_type": "esclusiva"}, {"record_kind": "census"})
    service._check_census_guard({"commercial_status": "archived"}, {"record_kind": "census"})
    service._check_census_guard({"commercial_status": "active"}, {"record_kind": "crm"})
    service._check_census_guard({"commercial_status": "active"}, {})                       # storico senza colonna
    data = {"civic_number": "3"}
    service._check_inherited_address(data, {"address_inherited": True})
    assert data["address_inherited"] is False
    data = {"surface_sqm": 1}
    service._check_inherited_address(data, {"address_inherited": True})
    assert "address_inherited" not in data


def test_c04_acquisizioni_rifiutano_il_censimento_con_un_codice_proprio():
    from acquisitions import errors
    assert issubclass(errors.PropertyInCensus, ConflictError) and errors.PropertyInCensus.code == "PROPERTY_IN_CENSUS"
    from acquisitions import service as acq
    assert acq.PROPERTY_IN_CENSUS_MESSAGE == census.CENSUS_LOCKED
    from acquisitions import repository as acq_repo
    import inspect
    assert "record_kind" in inspect.getsource(acq_repo.lock_property)
