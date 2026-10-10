"""Contratto HTTP delle viste Stime: scope server-side e PDF privato."""

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from crm import router as crm_router
from crm import valuations
from operator_auth.context import OperatorContext
from operator_auth.dependencies import legacy_basic_agency_context


def _client(agency_id=1):
    app = FastAPI()
    app.include_router(crm_router.router)
    if agency_id is not None:
        app.dependency_overrides[legacy_basic_agency_context] = lambda: OperatorContext(
            user_id=6, agency_id=agency_id, role="agency_owner",
            is_platform_admin=False, session_id=1, auth_channel="operator_session",
        )
    return TestClient(app, raise_server_exceptions=False)


def test_stime_routes_require_operator_session():
    client = _client(None)
    for path in ("/api/crm/stime", "/api/crm/stime/9", "/api/crm/stime/9/pdf"):
        assert client.get(path).status_code == 401


def test_list_and_detail_reuse_server_agency_scope(monkeypatch):
    calls = []

    def listing(ctx, **kwargs):
        calls.append(("list", ctx.require_agency(), kwargs))
        return {"items": [], "total": 0, "stats": {"total": 0, "detailed": 0}}

    def detail(ctx, stima_id):
        calls.append(("detail", ctx.require_agency(), stima_id))
        return {"id": stima_id}

    monkeypatch.setattr(valuations, "list_stime", listing)
    monkeypatch.setattr(valuations, "get_stima", detail)
    client = _client(35)
    assert client.get("/api/crm/stime?view=detailed&search=Rossi&limit=10&offset=20").status_code == 200
    assert client.get("/api/crm/stime/9").json() == {"id": 9}
    assert calls == [
        ("list", 35, {"view": "detailed", "search": "Rossi", "limit": 10, "offset": 20}),
        ("detail", 35, 9),
    ]
    assert client.get("/api/crm/stime?view=invalid").status_code == 422
    assert client.get("/api/crm/stime?limit=101").status_code == 422


def test_pdf_uses_existing_authorization_and_private_cache(monkeypatch):
    from crm import router as module

    def download(stima_id, *, agency_id):
        if agency_id != 35 or stima_id != 9:
            raise HTTPException(status_code=404, detail="PDF non disponibile")
        return b"%PDF-1.4\n%%EOF"

    monkeypatch.setattr(module.stima_pdf, "download", download)
    client = _client(35)
    response = client.get("/api/crm/stime/9/pdf")
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
    assert response.headers["content-type"] == "application/pdf"
    assert "no-store" in response.headers["cache-control"]
    assert client.get("/api/crm/stime/8/pdf").status_code == 404
    assert _client(1).get("/api/crm/stime/9/pdf").status_code == 404
