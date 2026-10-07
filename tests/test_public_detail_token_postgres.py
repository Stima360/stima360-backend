"""F01/F16: public capabilities on a disposable, local PostgreSQL only.

Reuses the real complete-schema fixture, writer, bridge and property sync.
Only external PDF delivery, mail and WhatsApp are substituted by the fixture.
"""
from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from tests.test_catalogo_canonico_1_postgres import (
    COMPLETA, DSN, JsonRequest, _persona, completo, mondo, sito,
)
from tests.test_censimento_3_backend_postgres import _q

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN not set: isolated PostgreSQL required")


def _quick(s, *, agency=1):
    return s.stima({**COMPLETA, **_persona()}, agency=agency)


def _token(s, sid):
    return str(_q(s.m, "SELECT token FROM stime WHERE id = %s", (sid,))[0][0])


def _counts(s):
    return tuple(_q(s.m, f"SELECT count(*) FROM {table}")[0][0]
                 for table in ("stime_dettagliate", "properties", "property_site_sources", "site_submissions"))


@pytest.mark.parametrize("failure", ["missing", "empty", "unknown", "expired", "no_expiry", "other_stima", "forged_id", "malformed", "token_number", "token_list", "token_object"])
def test_unauthorized_detail_changes_no_rows(sito, failure):
    s = sito
    response = _quick(s)
    sid = response["id"]
    raw = {"stima_id": sid, "token": _token(s, sid), "classe": "A4"}
    if failure == "missing":
        raw.pop("token")
    elif failure == "empty":
        raw["token"] = ""
    elif failure == "unknown":
        raw["token"] = "00000000-0000-4000-8000-000000000001"
    elif failure == "expired":
        _q(s.m, "UPDATE stime SET token_expires = NOW() - INTERVAL '1 minute' WHERE id = %s", (sid,))
    elif failure == "no_expiry":
        _q(s.m, "UPDATE stime SET token_expires = NULL WHERE id = %s", (sid,))
    elif failure == "other_stima":
        other = _quick(s, agency=2)
        raw["token"] = _token(s, other["id"])
    elif failure == "forged_id":
        raw["id"] = sid + 100000
    elif failure == "malformed":
        raw["token"] = "not-a-uuid"
    elif failure == "token_number":
        raw["token"] = 123
    elif failure == "token_list":
        raw["token"] = ["invalid"]
    elif failure == "token_object":
        raw["token"] = {"invalid": True}
    before = _counts(s)
    pid = s.scheda_di(sid)
    energy_before = s.scheda(pid)["energy_class"]
    with pytest.raises(HTTPException) as caught:
        s.dettaglio(raw)
    assert caught.value.status_code == 403
    assert _counts(s) == before
    assert s.scheda(pid)["energy_class"] == energy_before


@pytest.mark.parametrize("supplied_id", [False, True])
@pytest.mark.parametrize("agency", [1, 2])
def test_valid_token_derives_parent_and_updates_the_existing_property(sito, supplied_id, agency):
    s = sito
    response = _quick(s, agency=agency)
    sid, pid = response["id"], s.scheda_di(response["id"])
    before_properties = _q(s.m, "SELECT count(*) FROM properties")[0][0]
    raw = {"token": _token(s, sid), "agency_id": 999999, "classe": "A4", "riscaldamento": "Autonomo"}
    if supplied_id:
        raw["stima_id"] = sid
    assert s.dettaglio(raw) == {"ok": True}
    detail = _q(s.m, "SELECT id, stima_id, agency_id FROM stime_dettagliate ORDER BY id DESC LIMIT 1")[0]
    assert tuple(detail[1:]) == (sid, agency)
    assert s.scheda_di(sid) == pid
    assert _q(s.m, "SELECT count(*) FROM properties")[0][0] == before_properties
    assert s.scheda(pid)["energy_class"] == "A4"
    assert s.scheda(pid)["heating"] == "Autonomo"
    assert s.invio("detail", detail[0])["status"] == "synced"


@pytest.mark.parametrize("expiry", ["expired", "null", "malformed"])
def test_prefill_refuses_non_current_capabilities(sito, expiry):
    s = sito
    sid = _quick(s)["id"]
    token = _token(s, sid)
    if expiry == "malformed":
        token = "not-a-uuid"
    else:
        _q(s.m, "UPDATE stime SET token_expires = " + ("NULL" if expiry == "null" else "NOW() - INTERVAL '1 minute'") + " WHERE id = %s", (sid,))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(s.main.prefill(t=token))
    assert caught.value.status_code == 404


def test_browser_pdf_email_and_whatsapp_share_one_server_capability(sito, monkeypatch):
    s = sito
    mails, whatsapp, outbox = [], [], []
    monkeypatch.setattr(s.main, "invia_mail", lambda *args, **kwargs: (mails.append((args, kwargs)), True)[1])
    monkeypatch.setattr(s.main, "invia_whatsapp", lambda *args: (whatsapp.append(args), True)[1])
    monkeypatch.setattr(s.main.communication_service, "enqueue", lambda *args, **kwargs: (outbox.append(kwargs), {"message": {"id": 1}, "created": True})[1])
    response = _quick(s)
    sid = response["id"]
    token = _token(s, sid)
    assert response["token"] == token
    detail_query = parse_qs(urlparse(response["detail_url"]).query)
    redirect_query = parse_qs(urlparse(response["pdf_redirect_url"]).query)
    assert detail_query == {"token": [token]}
    assert redirect_query == {"token": [token]}
    assert whatsapp[0][3] == response["detail_url"]
    assert mails
    assert outbox[0]["stima_id"] == sid
    assert response["pdf_redirect_url"] in outbox[0]["rendered_body"]
    assert any(response["detail_url"] in str(item) for item in mails)
    # Consumer-visible prefill chooses the server estimation, not a client ST id.
    prefilled = asyncio.run(s.main.prefill(t=token))
    assert prefilled["id"] == sid
    assert s.dettaglio({"token": token, "stima_id": prefilled["id"], "classe": "B"}) == {"ok": True}
    assert s.scheda(s.scheda_di(sid))["energy_class"] == "B"


@pytest.mark.parametrize("transport", ["json", "form"])
def test_http_detail_uses_the_token_for_json_and_browser_form(sito, transport):
    from fastapi.testclient import TestClient
    s = sito
    sid = _quick(s)["id"]
    raw = JsonRequest({"token": _token(s, sid), "stima_id": str(sid), "classe": "B"}).payload
    with TestClient(s.main.app) as client:
        response = client.post("/api/salva_stima_dettagliata", **({"json": raw} if transport == "json" else {"data": raw}))
        assert response.status_code == 200, response.text
        assert response.json()["ok"] is True
        assert response.json()["receipt"]["status"] == "completed"
        rejected = client.post("/api/salva_stima_dettagliata", json=JsonRequest({"stima_id": sid, "classe": "A4"}).payload)
        assert rejected.status_code == 403
    assert s.scheda(s.scheda_di(sid))["energy_class"] == "B"


@pytest.mark.parametrize("other_agency", [1, 2])
@pytest.mark.parametrize("other_expired", [False, True])
def test_ambiguous_token_cannot_prefill_an_arbitrary_stima(sito, other_agency, other_expired):
    s = sito
    first, second = _quick(s), _quick(s, agency=other_agency)
    token = _token(s, first["id"])
    _q(s.m, "UPDATE stime SET token=%s, token_expires="
       + ("NOW() - INTERVAL '1 minute'" if other_expired else "NOW() + INTERVAL '1 day'")
       + " WHERE id=%s", (token, second["id"]))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(s.main.prefill(t=token))
    assert caught.value.status_code == 404


@pytest.mark.parametrize("other_agency", [1, 2])
@pytest.mark.parametrize("other_expired", [False, True])
def test_ambiguous_token_changes_neither_stima_nor_property(sito, other_agency, other_expired):
    s = sito
    first, second = _quick(s), _quick(s, agency=other_agency)
    token = _token(s, first["id"])
    _q(s.m, "UPDATE stime SET token=%s, token_expires="
       + ("NOW() - INTERVAL '1 minute'" if other_expired else "NOW() + INTERVAL '1 day'")
       + " WHERE id=%s", (token, second["id"]))
    counts = _counts(s)
    property_ids = [s.scheda_di(row["id"]) for row in (first, second)]
    before = [s.scheda(pid)["energy_class"] for pid in property_ids]
    for sid in (None, first["id"], second["id"]):
        raw = {"token": token, "classe": "A4"}
        if sid is not None:
            raw["stima_id"] = sid
        with pytest.raises(HTTPException) as caught:
            s.dettaglio(raw)
        assert caught.value.status_code == 403
        assert _counts(s) == counts
        assert [s.scheda(pid)["energy_class"] for pid in property_ids] == before
