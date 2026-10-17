"""ADM-1 - la guardia del Super Admin generale, senza database.

  A  ogni rotta di `/api/platform` passa da `require_super_admin_context`,
     tranne `/me` (il cancello della UI) e l'uscita dall'agenzia
  B  la guardia: designato si', altro platform admin no (403 + `denied`),
     configurazione assente: nessuno
  C  `/me` dice se il chiamante e' il Super Admin designato
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from operator_auth.context import OperatorContext
from platform_admin import audit as platform_audit
from platform_admin import super_admin
from platform_admin.dependencies import require_platform_admin, require_super_admin_context
from platform_admin.router import router

APERTE_A_OGNI_PLATFORM_ADMIN = {("GET", "/api/platform/me"), ("POST", "/api/platform/agency-context/exit")}
# Il designato lo impone il service, con il proprio codice di errore e audit
# (`super_admin.require_super_admin`): eliminazione edifici, Cestino, C2.
GUARDIA_NEL_SERVICE = ("/api/platform/building-deletion", "/api/platform/trash",
                       "/api/platform/territorial-change-requests")


def _dipendenze(dependant):
    for d in dependant.dependencies:
        yield d.call
        yield from _dipendenze(d)


def _ctx(user_id):
    return OperatorContext(user_id=user_id, agency_id=None, role=None, is_platform_admin=True,
                           session_id=None, auth_channel="operator_session")


def test_a_ogni_rotta_della_rete_richiede_il_super_admin_designato():
    controllate = 0
    for rotta in router.routes:
        if not isinstance(rotta, APIRoute):
            continue
        chiamate = set(_dipendenze(rotta.dependant))
        for metodo in rotta.methods:
            if (metodo, rotta.path) in APERTE_A_OGNI_PLATFORM_ADMIN or rotta.path.startswith(GUARDIA_NEL_SERVICE):
                assert require_super_admin_context not in chiamate, rotta.path
                assert require_platform_admin in chiamate, rotta.path
            else:
                assert require_super_admin_context in chiamate, (metodo, rotta.path)
                controllate += 1
    assert controllate == 23, controllate


@pytest.fixture
def registro(monkeypatch):
    righe = []
    monkeypatch.setattr(platform_audit, "record", lambda **kw: righe.append(kw) or len(righe))
    return righe


def test_b_designato_ammesso_altri_respinti(monkeypatch, registro):
    monkeypatch.setenv(super_admin.SUPER_ADMIN_ENV, "41")
    assert require_super_admin_context(_ctx(41)).user_id == 41
    assert registro == []
    with pytest.raises(HTTPException) as exc:
        require_super_admin_context(_ctx(42))
    assert exc.value.status_code == 403 and "Super Admin" in exc.value.detail
    assert [(r["action"], r["result"]) for r in registro] == [("platform.super_admin.required", "denied")]


@pytest.mark.parametrize("valore", ["", "0", "abc", "-3"])
def test_b_senza_configurazione_nessuno_passa(monkeypatch, registro, valore):
    monkeypatch.setenv(super_admin.SUPER_ADMIN_ENV, valore)
    with pytest.raises(HTTPException) as exc:
        require_super_admin_context(_ctx(41))
    assert exc.value.status_code == 403


def test_c_is_designated(monkeypatch):
    monkeypatch.setenv(super_admin.SUPER_ADMIN_ENV, "41")
    assert super_admin.is_designated(41) and super_admin.is_designated("41")
    assert not super_admin.is_designated(42) and not super_admin.is_designated(None)
    monkeypatch.delenv(super_admin.SUPER_ADMIN_ENV)
    assert not super_admin.is_designated(41)


def test_a_i_service_con_guardia_propria_impongono_il_designato():
    """Le rotte escluse sopra non sono aperte: il loro service chiama
    `require_super_admin` (identita' designata) prima di ogni lettura."""
    import inspect

    from property import building_deletion, territorial_changes
    from trash import purge, service

    assert "require_super_admin(ctx" in inspect.getsource(territorial_changes._super_admin)
    assert "require_super_admin(ctx" in inspect.getsource(building_deletion)
    assert "require_super_admin(ctx" in inspect.getsource(service)
    assert "require_super_admin(ctx" in inspect.getsource(purge)
