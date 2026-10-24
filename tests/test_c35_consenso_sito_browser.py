"""C3.5 - la pagina del sito modificata, in un browser vero (Chromium).

Si servono in locale le copie di `site-preview/c35-consenso/` e si intercetta
la chiamata a `/api/salva_stima`: nessun POST pubblico, nessun invio.

  01  casella spuntata: la richiesta porta la scelta, il testo della casella
      come la pagina lo mostra e l'impronta SHA-256 di privacy.html.
  02  casella NON spuntata: la stima parte (il marketing resta facoltativo),
      la scelta e' false, testo e impronta ci sono lo stesso.
  03  privacy.html irraggiungibile: la stima parte comunque, l'impronta e'
      vuota (nessuna prova inventata).
(Le caselle identiche all'originale: `test_c35_consenso_sito_static.py`.)
"""
from __future__ import annotations

import functools
import hashlib
import json
import shutil
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
C35 = ROOT / "site-preview" / "c35-consenso"
API = "https://stima360-backend.onrender.com/api/salva_stima"
QUERY = "comune=Tortoreto&microzona=Lido+Sud&mq=80&tipologia=Appartamento&via=Via+del+Mare&civico=12"

playwright = pytest.importorskip("playwright.sync_api")


class _Silenzioso(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@pytest.fixture
def pagina(tmp_path):
    def apri(*, con_privacy=True):
        shutil.copy(C35 / "dati_personali.html", tmp_path / "dati_personali.html")
        if con_privacy:
            shutil.copy(C35 / "privacy.html", tmp_path / "privacy.html")
        server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Silenzioso, directory=str(tmp_path)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}/dati_personali.html?{QUERY}"

    servers: list = []
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        richieste: list[dict] = []

        def invia(spunta: bool, *, con_privacy=True):
            url = apri(con_privacy=con_privacy)
            page = browser.new_page()
            page.on("dialog", lambda d: d.dismiss())

            def instrada(route):
                req = route.request
                if req.url == API:
                    richieste.append(json.loads(req.post_data))
                    route.fulfill(status=200, content_type="application/json",
                                  body=json.dumps({"id": 1, "price_exact": 200000, "eur_mq_finale": 2500}))
                elif req.url.startswith("http://127.0.0.1"):
                    route.continue_()
                else:
                    route.abort()

            page.route("**/*", instrada)
            page.goto(url)
            page.fill("#nome", "Serena")
            page.fill("#cognome", "Prova")
            page.fill("#email", "serena.prova@example.invalid")
            page.fill("#telefono", "333 1234567")
            page.check("#privacy")
            if spunta:
                page.check("#marketing")
            page.click("button[type='submit']")
            page.wait_for_function("() => true")
            for _ in range(60):
                if richieste:
                    break
                page.wait_for_timeout(100)
            page.close()
            assert richieste, "nessuna richiesta a salva_stima"
            return richieste.pop()

        try:
            yield invia
        finally:
            browser.close()
            for s in servers:
                s.shutdown()


def _testo_atteso():
    from consent.site_evidence import site_checkbox_evidence
    return site_checkbox_evidence()["text"]


IMPRONTA = hashlib.sha256((C35 / "privacy.html").read_bytes()).hexdigest()


def test_01_spuntata_scelta_testo_e_impronta(pagina):
    corpo = pagina(True)
    assert corpo["consenso_marketing"] is True
    assert corpo["consenso_marketing_testo"] == _testo_atteso()
    assert corpo["informativa_privacy_sha256"] == IMPRONTA
    assert corpo["nome"] == "Serena" and corpo["comune"] == "Tortoreto"


def test_02_non_spuntata_la_stima_parte_comunque(pagina):
    corpo = pagina(False)
    assert corpo["consenso_marketing"] is False
    assert corpo["consenso_marketing_testo"] == _testo_atteso()
    assert corpo["informativa_privacy_sha256"] == IMPRONTA


def test_03_privacy_irraggiungibile_impronta_vuota(pagina):
    corpo = pagina(True, con_privacy=False)
    assert corpo["consenso_marketing"] is True
    assert corpo["informativa_privacy_sha256"] == ""
