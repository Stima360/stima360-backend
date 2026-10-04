"""CENSIMENTO-1 Fase 4 - il residuo noto della Fase 3, chiuso: il PATCH
generico su una scheda in censimento rispondeva 409 con il solo `detail`;
ora porta `code: CENSUS_LOCKED` come ogni altro errore del censimento
(`{detail, code}`), attraverso la ROTTA REALE `PATCH /api/property/properties/{id}`
(che passa da `trc`). Contratti di successo invariati; gli altri errori della
PATCH guadagnano solo un `code` additivo. Prova PostgreSQL in
`test_censimento_3_backend_postgres.py::test_10` (trigger e service reali).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.exceptions import ConflictError, NotFoundError, ValidationError
from property import census, service
from property.router import router


@pytest.fixture
def api(monkeypatch):
    from operator_auth.context import OperatorContext
    from operator_auth.dependencies import legacy_basic_agency_context
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[legacy_basic_agency_context] = lambda: OperatorContext(
        user_id=1, agency_id=1, role="agency_owner", is_platform_admin=False, session_id=None, auth_channel="operator_session")
    return TestClient(app, raise_server_exceptions=False)


def _alza(monkeypatch, exc):
    def finto(ctx, property_id, body):
        raise exc
    monkeypatch.setattr(service, "update_property", finto)


def test_01_la_guardia_del_censimento_porta_il_codice_sulla_rotta_reale(api, monkeypatch):
    _alza(monkeypatch, census.CensusConflict(census.CENSUS_LOCKED, "CENSUS_LOCKED"))
    r = api.patch("/api/property/properties/30", json={"commercial_status": "active"})
    assert r.status_code == 409
    assert r.json() == {"detail": census.CENSUS_LOCKED, "code": "CENSUS_LOCKED"}
    assert "Prendi in carico" in r.json()["detail"]


def test_02_gli_altri_errori_della_patch_restano_leggibili_con_un_codice_additivo(api, monkeypatch):
    _alza(monkeypatch, NotFoundError("Immobile non trovato"))
    r = api.patch("/api/property/properties/30", json={"floor": "1"})
    assert r.status_code == 404 and r.json() == {"detail": "Immobile non trovato", "code": "NOT_FOUND"}
    _alza(monkeypatch, ConflictError("L'incarico puo' essere generato solo da un'acquisizione."))
    r = api.patch("/api/property/properties/30", json={"mandate_type": "esclusiva"})
    assert r.status_code == 409 and r.json()["detail"].startswith("L'incarico") and r.json()["code"] == "CONFLICT"
    _alza(monkeypatch, ValidationError("Comune non presente nel catalogo: X"))
    r = api.patch("/api/property/properties/30", json={"city": "X"})
    assert r.status_code == 400 and r.json()["code"] == "VALIDATION_ERROR"
    _alza(monkeypatch, census.CensusNotInstalled(census.CENSUS_NOT_INSTALLED_MESSAGE))
    r = api.patch("/api/property/properties/30", json={"cadastral_category": "A/3"})
    assert r.status_code == 503 and r.json()["code"] == "CENSUS_NOT_INSTALLED"
    assert api.patch("/api/property/properties/30", json={"record_kind": "crm"}).status_code == 422    # schema: extra=forbid


def test_03_la_risposta_di_successo_e_invariata(api, monkeypatch):
    monkeypatch.setattr(service, "update_property", lambda ctx, i, body: {"id": 30, "code": "IMM-30", "floor": "1"})
    r = api.patch("/api/property/properties/30", json={"floor": "1"})
    assert r.status_code == 200 and r.json() == {"id": 30, "code": "IMM-30", "floor": "1"}


def test_04_solo_la_patch_generica_e_passata_a_trc_nel_router():
    from pathlib import Path
    righe = (Path(__file__).resolve().parents[1] / "property" / "router.py").read_text(encoding="utf-8").splitlines()
    patch = [r for r in righe if r.startswith("def update_property(")]
    assert len(patch) == 1 and "return trc(service.update_property" in patch[0]
    # le altre rotte generiche restano su `tr`: nessun cambio di contratto fuori dal residuo
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 0: `archive_property` (la DELETE
    # deprecata) e le azioni esplicite /archive e /unarchive rispondono con
    # `{detail, code}` (ARCHIVE_BLOCKED + blockers, NOT_ASSIGNED, ...), quindi
    # passano da `trc` come la PATCH; create/get/list restano su `tr`.
    for nome in ("def create_property(", "def get_property(", "def list_properties("):
        riga = next(r for r in righe if r.startswith(nome))
        assert "tr(" in riga and "trc(" not in riga, nome
    for nome in ("def archive_property(", "def archive_property_explicit(", "def unarchive_property("):
        blocco = righe[next(i for i, r in enumerate(righe) if r.startswith(nome)):][:3]
        assert any("trc(service." in r for r in blocco), nome
