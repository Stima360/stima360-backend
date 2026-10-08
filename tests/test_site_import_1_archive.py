"""SITE-IMPORT-1 - l'archivio dei PDF del sito, senza rete: un doppio HTTP."""
from __future__ import annotations

from datetime import datetime, timezone

from site_import.config import Config, ConfigurationError
from site_import.pdf_archive import GitHubArchive

PDF = b"%PDF-1.4\noriginale\n%%EOF\n"
CREATA = datetime(2026, 9, 1, 10, 0)  # come la scrive il sito: senza fuso, UTC


class R:
    def __init__(self, status, payload=None, content=b""):
        self.status_code, self._payload, self.content = status, payload, content

    def json(self):
        return self._payload


def archivio(risposte, **kw):
    chiamate = []

    def get(url, *, headers, params=None):
        chiamate.append((url, dict(headers), dict(params or {})))
        return risposte.pop(0)

    return GitHubArchive("Stima360/stima360-pdf", "main", kw.pop("token", None), http_get=get, **kw), chiamate


def commit(sha, quando):
    return {"sha": sha, "commit": {"committer": {"date": quando}}}


def test_a1_pdf_nella_finestra_scaricato_alla_versione_esatta():
    a, chiamate = archivio([R(200, [commit("tardi", "2026-11-01T10:00:00Z"),
                                    commit("giusto", "2026-09-01T10:00:05Z")]),
                            R(200, content=PDF)], token="t0k")
    esito = a.fetch(42, CREATA)
    assert esito.status == "ready" and esito.pdf == PDF
    assert esito.provenance["commit"] == "giusto" and esito.provenance["path"] == "stima_42.pdf"
    assert chiamate[1][2] == {"ref": "giusto"}
    assert chiamate[0][1]["Authorization"] == "Bearer t0k"
    assert all("POST" not in c[0] for c in chiamate)


def test_a2_file_sovrascritto_da_un_altra_stima_non_si_associa():
    a, _ = archivio([R(200, [commit("altro", "2026-11-01T10:00:00Z")])])
    assert a.fetch(42, CREATA).status == "unverified"


def test_a3_mancante_non_pdf_errori_di_rete():
    a, _ = archivio([R(200, [])])
    assert a.fetch(1, CREATA).status == "missing"
    a, _ = archivio([R(404, {})])
    assert a.fetch(1, CREATA).status == "missing"
    a, _ = archivio([R(200, [commit("x", "2026-09-01T10:00:01Z")]), R(200, content=b"<html>")])
    assert a.fetch(1, CREATA).status == "invalid"
    a, _ = archivio([R(503, {})])
    assert a.fetch(1, CREATA).status == "unavailable"

    def rotto(*a, **k):
        raise OSError("rete")

    assert GitHubArchive("o/r", http_get=rotto).fetch(1, CREATA).status == "unavailable"
    assert GitHubArchive(None).fetch(1, CREATA).status == "unavailable"


def test_a4_configurazione(monkeypatch):
    monkeypatch.delenv("SITE_DB_URL", raising=False)
    try:
        Config.from_env()
        raise AssertionError("SITE_DB_URL obbligatoria")
    except ConfigurationError:
        pass
    monkeypatch.setenv("SITE_DB_URL", "postgresql://u@h/db")
    monkeypatch.setenv("SITE_PDF_GITHUB_REPO", "solo-nome")
    try:
        Config.from_env()
        raise AssertionError("repo malformato")
    except ConfigurationError:
        pass
    monkeypatch.setenv("SITE_PDF_GITHUB_REPO", "Stima360/stima360-pdf")
    c = Config.from_env()
    assert c.pdf_repo == "Stima360/stima360-pdf" and c.settle_minutes == 10 and c.batch == 100
