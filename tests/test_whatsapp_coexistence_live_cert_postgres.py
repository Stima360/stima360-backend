"""WHATSAPP COEXISTENCE - la sezione della matrice ostile P26-6
(`certify_whatsapp_coexistence`) eseguita contro l'app VERA su PostgreSQL VERO,
non contro il doppio: due agenzie con un collegamento Coexistence ciascuna e un
messaggio con allegato ciascuna (fixture `meta`: Meta finta, ogni chiamata contata).

    L1  ogni prova e' un PASS sul server vero, e gli «altrui» sono quelli VERI
        dell'altra agenzia (non id inventati): verifica, sincronizzazione e
        allegato dell'altra agenzia rispondono 404
    L2  la matrice non chiama MAI Meta (nessuna chiamata Graph, nessun download)
        e non cambia nulla nel registro dei collegamenti
"""
from __future__ import annotations

import pytest

from tests.test_blocco_4a_whatsapp_inbound_postgres import (  # noqa: F401 - fixture
    NUMBER_B, _change, _message, _payload, _q, _sender, app, banco, wa,
)
from tests.test_whatsapp_coexistence_postgres import COEX, WABA, meta  # noqa: F401 - fixture
from tests.test_whatsapp_due_caselle_postgres import NUMBER_A2, _inbox2, dc  # noqa: F401 - fixture

pytestmark = pytest.mark.skipif(
    not __import__("os").getenv("P29_TEST_DSN"), reason="P29_TEST_DSN non impostata")


class _Http:
    """L'interfaccia di `HttpProbe` sopra il TestClient dell'app vera: `jar` e' il
    client gia' entrato con la sessione di quell'agenzia; senza, il client anonimo."""

    def __init__(self, cert, anonimo):
        self.cert, self.anonimo, self.chiamate = cert, anonimo, []

    def request(self, method, path, *, jar=None, payload=None, headers=None):
        client = jar or self.anonimo
        r = client.request(method, path, json=payload, headers=headers or {})
        self.chiamate.append((method, path, r.status_code))
        return self.cert.Response(r.status_code, r.headers, r.content)


def _carica_cert():
    import importlib.util
    from pathlib import Path

    percorso = Path(__file__).resolve().parent.parent / "scripts" / "p26_6_live_cert.py"
    spec = importlib.util.spec_from_file_location("p26_6_live_cert_vero", percorso)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_l1_l2_la_matrice_sul_server_vero_rifiuta_l_altra_agenzia(meta, monkeypatch):
    import io

    import requests
    from fastapi.testclient import TestClient

    import main

    cert = _carica_cert()
    ids = meta["ids"]
    _inbox2(meta, access="agency", automations=False)
    owner_a, owner_b = meta["login"]("owner_a"), meta["login"]("owner_b")

    # A: collegamento vero (Meta finta) sul numero personale; B: un collegamento nel registro
    r = owner_a.post(f"{COEX}/onboardings", json={"code": "codice-finto", "waba_id": WABA,
                                                  "phone_number_id": NUMBER_A2})
    assert r.status_code == 200, r.text
    _q(meta, "INSERT INTO whatsapp_coexistence_onboardings (agency_id, requested_by_user_id, waba_id, phone_number_id) "
             "VALUES (%s, %s, '2000000000000077', %s)", (ids["b"], ids["owner_b"], NUMBER_B))
    # un messaggio con allegato per agenzia
    for numero, media in ((NUMBER_A2, "media.a.1"), (NUMBER_B, "media.b.1")):
        foto = _message(_sender(), kind="image", image={"mime_type": "image/jpeg", "id": media})
        assert meta["post"](_payload(_change(numero, [foto]))).status_code == 200
    collegamenti = {agenzia: owner.get(f"{COEX}/onboardings").json()["items"]
                    for agenzia, owner in (("A", owner_a), ("B", owner_b))}
    assert len(collegamenti["A"]) == 1 and len(collegamenti["B"]) == 1

    graph_prima = len(meta["meta"]["graph"].calls)
    registro_prima = _q(meta, "SELECT id, status, steps::text, updated_at FROM whatsapp_coexistence_onboardings "
                              "ORDER BY id")
    scaricati = []

    def nessun_download(*a, **k):
        scaricati.append(a)
        raise AssertionError("download da Meta durante la matrice")

    monkeypatch.setattr(requests, "get", nessun_download)

    report = cert.Report(stream=io.StringIO())
    http = _Http(cert, TestClient(main.app, base_url="https://testserver"))
    dominio = next(d for d in cert.DOMAINS if d.prefix == "/api/whatsapp-coexistence")
    contesto = {"agencies": {"A": ids["a"], "B": ids["b"]}}
    cert.certify_whatsapp_coexistence(report, http, cert, dominio, {"A": owner_a, "B": owner_b}, {}, contesto)

    righe = [(k, i, t) for k, i, t in report.rows]
    assert [r for r in righe if r[0] != cert.PASS] == [], righe
    assert len(righe) >= 6 + 6 + 6 * 2
    # gli «altrui» sono quelli VERI: l'id del collegamento e del messaggio dell'altra agenzia
    for ident in ("verifica-altrui", "sync-altrui", "allegato-altrui"):
        testi = [t for _, i, t in righe if ident in i]
        assert len(testi) == 2 and all("nessuno visibile" not in t for t in testi), testi
    id_b = collegamenti["B"][0]["id"]
    assert any(f"/onboardings/{id_b}/verify" in p and s == 404 for _, p, s in http.chiamate), http.chiamate
    # L2: nessuna chiamata a Meta, nessun download, registro invariato
    assert len(meta["meta"]["graph"].calls) == graph_prima and scaricati == []
    assert _q(meta, "SELECT id, status, steps::text, updated_at FROM whatsapp_coexistence_onboardings "
                    "ORDER BY id") == registro_prima
