"""V3 - PROVA GENERALE DEL FOLLOW-UP PROPRIETARI, su PostgreSQL usa-e-getta.

Tutto vero tranne il server di posta: i 16 TESTI APPROVATI dal registro, le
rotte HTTP del CRM (predisposizione, attivazione, dispatch), il motore delle
sequenze, il dispatcher e l'adapter email reale (`email_smtp`), con la sola
primitiva `invia_mail` sostituita da una che REGISTRA invece di spedire.
Il limite dei destinatari TEST e' attivo come sul CRM TEST.

  01  predisposizione in bozza (16 passi), attivazione, 180 giorni: 16 email
      al solo indirizzo autorizzato, nei giorni e nella fascia previsti,
      oggetti approvati, guida nella sesta, disiscrizione in tutte; il
      cliente non autorizzato non riceve niente; la valutazione parte;
      a fine sequenza nessuna ripartenza.
  02  revoca del consenso e disiscrizione dal link dell'email vera.
  03  nessun doppione: predisposizione e giri ripetuti, stessa email in due
      agenzie.
"""
from __future__ import annotations

import re
import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _q, completo  # noqa: F401
from tests.test_delete_arch_0_postgres import _stima, mondo, operatori  # noqa: F401
from tests.test_fase_b_followup_postgres import (  # noqa: F401
    GIORNO, _contatto, _ctx, _informativa, _iscrizioni, _messaggi, _sistema, fb)
from tests.test_v3_followup_postgres import T0, _tick

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

ROMA = ZoneInfo("Europe/Rome")
AUTORIZZATO = "collaudo@stima360-test.invalid"
BASE = "https://crm-test.example.invalid"


@pytest.fixture
def reale(fb, monkeypatch):
    """Il CRM TEST in miniatura: testi approvati, limite attivo, posta registrata."""
    from datetime import datetime, timezone
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from communication import public_router, repository as ledger, router
    from communication.dependencies import require_dispatch_context
    from communication.providers import email_smtp

    monkeypatch.setenv("PUBLIC_BASE_URL", BASE)
    monkeypatch.setenv("COMMUNICATION_MARKETING_ALLOWED_RECIPIENTS", AUTORIZZATO)

    class _Martedi11(datetime):
        @classmethod
        def now(cls, tz=None):
            ora = datetime(2026, 10, 20, 9, tzinfo=timezone.utc)
            return ora if tz else ora.replace(tzinfo=None)
    monkeypatch.setattr(ledger, "datetime", _Martedi11)

    posta = []

    def invia_mail(destinatario, oggetto, corpo_html, allegato=None, **kw):
        posta.append({"a": destinatario, "oggetto": oggetto, "html": corpo_html, "allegato": allegato, **kw})
        return True
    monkeypatch.setattr(email_smtp, "invia_mail", invia_mail)

    app = FastAPI()
    app.include_router(router.router)
    app.include_router(public_router.router)
    agenzia = {"chi": "owner_a"}
    app.dependency_overrides[require_dispatch_context] = lambda: _ctx(fb, agenzia["chi"])
    _informativa(fb)
    return {"m": fb, "http": TestClient(app), "posta": posta, "agenzia": agenzia}


def _proprietario(m, email, agenzia=1):
    return _contatto(m, email, consenso="granted", quando=T0 - 10 * GIORNO, agenzia=agenzia)


def _predisponi_e_attiva(r):
    h = r["http"]
    bozza = h.post("/api/communication/journeys/followup-v3/provision")
    assert bozza.status_code == 200, bozza.text
    j = bozza.json()["journey"]
    attivata = h.post(f"/api/communication/journeys/{j['id']}/activate")
    assert attivata.status_code == 200, attivata.text
    return j, attivata.json()


def _giro(r, giorno):
    """Un giro come quello del cron: tick, poi dispatch dalla rotta HTTP."""
    _tick(r["m"], T0 + giorno * GIORNO)
    esito = r["http"].post("/api/communication/dispatch", json={"channel": "email", "limit": 50})
    assert esito.status_code == 200, esito.text
    _q(r["m"], "UPDATE communication_messages SET sent_at = scheduled_at "
               "WHERE status = 'sent' AND enrollment_id IS NOT NULL AND sent_at > scheduled_at")
    _tick(r["m"], T0 + giorno * GIORNO)
    return esito.json()


def test_01_collaudo_completo_180_giorni(reale):
    from communication import followup_v3_texts as testi, guida_pdf
    from communication import service as communication_service
    r, m = reale, reale["m"]
    j, attiva = _predisponi_e_attiva(r)
    assert j["status"] == "draft" and len(j["steps"]) == 16
    assert [s["delay_seconds"] // 86400 for s in j["steps"]] == [g for g, _ in testi.CALENDARIO]
    assert attiva["status"] == "active"
    assert _q(m, "SELECT count(*) FROM communication_messages")[0][0] == 0, "nessun arretrato"

    prova = _proprietario(m, AUTORIZZATO)
    cliente = _proprietario(m, "cliente.reale@example.it")
    acquirente = _contatto(m, "acquirente@example.it", consenso="granted", quando=T0 - 10 * GIORNO, ruolo="buyer")
    # la valutazione di un cliente reale, gia' in coda: deve partire comunque
    sid = _stima(m)
    stima = communication_service.enqueue(
        _sistema(1), contact_id=cliente, stima_id=sid, channel="email", communication_type="service", mode="automatic",
        reason_code="stima_pdf", destination_snapshot="cliente.reale@example.it",
        subject_snapshot="La tua stima", rendered_body="<p>stima invariata</p>",
        idempotency_key=f"stima:{uuid.uuid4().hex}",
        metadata={"pdf_url": f"{BASE}/pdf/{sid}.pdf"})["message"]

    for giorno in range(0, 186):
        _giro(r, giorno)

    # ---- la valutazione: partita, identica, verso il cliente
    valutazioni = [p for p in r["posta"] if p["a"] == "cliente.reale@example.it"]
    assert [(p["oggetto"], p["html"]) for p in valutazioni] == [("La tua stima", "<p>stima invariata</p>")]
    assert _q(m, "SELECT status FROM communication_messages WHERE id = %s", (stima["id"],))[0][0] == "sent"

    # ---- il marketing: solo all'indirizzo autorizzato, 16 email, in ordine
    marketing = [p for p in r["posta"] if p["a"] != "cliente.reale@example.it"]
    assert {p["a"] for p in marketing} == {AUTORIZZATO}
    assert [p["oggetto"] for p in marketing] == [o for _, o in testi.CALENDARIO]
    for p in marketing:
        assert p["allegato"] is None
        assert "Il team STIMA360" not in p["corpo_testo"]
        assert "Se non vuoi piu' ricevere questi messaggi: " + BASE in p["corpo_testo"]
        assert p["intestazioni"]["List-Unsubscribe"].startswith("<" + BASE)
        assert 'href="' + BASE in p["html"]
    sesta = marketing[5]
    link = BASE + guida_pdf.PERCORSO
    assert f"🎁 Puoi scaricarla gratuitamente qui: {link}" in sesta["corpo_testo"]
    assert f'<a href="{link}">' in sesta["html"]
    assert "allegat" not in sesta["corpo_testo"].lower()
    pdf = r["http"].get(guida_pdf.PERCORSO)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")

    # ---- giorni e fascia oraria: mai prima del giorno previsto, lun-sab 9-19 a Roma
    e = _iscrizioni(m, contact_id=prova)[0]
    inizio = e["trigger_sent_at"] if e.get("trigger_sent_at") else T0
    spediti = sorted((x for x in _messaggi(m, e["id"]) if x["status"] == "sent"), key=lambda x: x["step_no"])
    assert [x["step_no"] for x in spediti] == list(range(1, 17))
    for x, (giorno, _) in zip(spediti, testi.CALENDARIO):
        locale = x["scheduled_at"].astimezone(ROMA)
        assert locale.isoweekday() <= 6 and 9 <= locale.hour < 19, (x["step_no"], locale)
        ritardo = (x["scheduled_at"] - inizio) / GIORNO
        assert giorno <= ritardo < giorno + 3, (x["step_no"], ritardo)

    # ---- il cliente non autorizzato: nessuna email, il marketing resta fermo in coda
    e_cliente = _iscrizioni(m, contact_id=cliente)[0]
    fermi = _messaggi(m, e_cliente["id"])
    assert fermi and all(x["status"] == "queued" and x["attempt_count"] == 0 for x in fermi)
    assert acquirente not in {x["contact_id"] for x in _iscrizioni(m)}

    # ---- fine al giorno 180, nessuna ripartenza
    assert e["status"] == "completed"
    prima = _q(m, "SELECT count(*) FROM communication_messages WHERE enrollment_id = %s", (e["id"],))[0][0]
    for giorno in (200, 260, 365, 400):
        _giro(r, giorno)
    assert len([x for x in _iscrizioni(m) if x["contact_id"] == prova]) == 1
    assert _q(m, "SELECT count(*) FROM communication_messages WHERE enrollment_id = %s", (e["id"],))[0][0] == prima
    assert [p["a"] for p in r["posta"]].count(AUTORIZZATO) == 16


def test_02_revoca_e_disiscrizione(reale, monkeypatch):
    from consent import service as cs
    from communication import unsubscribe
    r, m = reale, reale["m"]
    monkeypatch.setenv("COMMUNICATION_MARKETING_ALLOWED_RECIPIENTS", "revoca@stima360-test.invalid, link@stima360-test.invalid")
    _predisponi_e_attiva(r)
    revoca = _proprietario(m, "revoca@stima360-test.invalid")
    link = _proprietario(m, "link@stima360-test.invalid")
    for giorno in range(0, 9):
        _giro(r, giorno)
    assert [p["a"] for p in r["posta"]].count("link@stima360-test.invalid") == 2
    # revoca registrata nel CRM, con l'email 3 gia' in coda
    _tick(m, T0 + 12 * GIORNO)
    cs.record_revocation(_sistema(1), contact_id=revoca, purpose="marketing", channel="email",
                         source="unsubscribe_link", actor_type="subject", decided_at=T0 + 12 * GIORNO,
                         idempotency_key=f"rev:{revoca}")
    # disiscrizione dal link stampato nella seconda email vera
    seconda = [p for p in r["posta"] if p["a"] == "link@stima360-test.invalid"][1]
    token = re.search(r"\?t=([^\s<\"]+)", seconda["corpo_testo"]).group(1)
    unsubscribe.process(token)
    for giorno in range(12, 60):
        _giro(r, giorno)
    assert [p["a"] for p in r["posta"]].count("revoca@stima360-test.invalid") == 2
    assert [p["a"] for p in r["posta"]].count("link@stima360-test.invalid") == 2
    for cid in (revoca, link):
        e = _iscrizioni(m, contact_id=cid)[0]
        assert e["status"] == "stopped", e
        assert not [x for x in _messaggi(m, e["id"]) if x["status"] == "sent" and x["step_no"] > 2]


def test_03_nessun_doppione(reale, monkeypatch):
    r, m = reale, reale["m"]
    monkeypatch.setenv("COMMUNICATION_MARKETING_ALLOWED_RECIPIENTS", "doppio@stima360-test.invalid")
    j, _ = _predisponi_e_attiva(r)
    di_nuovo = r["http"].post("/api/communication/journeys/followup-v3/provision")
    assert di_nuovo.status_code == 200 and di_nuovo.json()["created"] is False
    assert di_nuovo.json()["journey"]["id"] == j["id"]
    r["agenzia"]["chi"] = "owner_b"
    _predisponi_e_attiva(r)
    r["agenzia"]["chi"] = "owner_a"
    a = _proprietario(m, "doppio@stima360-test.invalid")
    b = _proprietario(m, "doppio@stima360-test.invalid", agenzia=2)
    for giorno in range(0, 6):
        _tick(m, T0 + giorno * GIORNO, agenzia=2)
        _giro(r, giorno)
        _tick(m, T0 + giorno * GIORNO)
    aperte = [e for e in _iscrizioni(m) if e["contact_id"] in (a, b) and e["status"] in ("active", "paused")]
    assert len(aperte) == 1
    # una sola email 1 in tutta la piattaforma, in qualunque delle due agenzie
    assert _q(m, "SELECT count(*) FROM communication_messages WHERE destination_snapshot = %s",
              ("doppio@stima360-test.invalid",))[0][0] == 1
    assert [p["a"] for p in r["posta"]].count("doppio@stima360-test.invalid") <= 1
