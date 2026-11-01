"""CONFERMA EMAIL DEL CONSENSO - dal link reale dell'email alla prima email V3.

Caso reale: l'email di conferma accettata dal server SMTP, la richiesta
rimasta `pending` e NESSUNA richiesta di conferma nei log del TEST. Il link
prende l'host da `PUBLIC_BASE_URL`: se punta a un altro servizio (per esempio
il backend del sito, il default storico del codice), il clic arriva li' e il
TEST non lo vede mai.

  01  end-to-end: pagina del consenso -> email di conferma spedita (SMTP
      intercettato) -> il link PRESO DALL'EMAIL punta a questo servizio ->
      gli scanner (GET) non confermano -> il clic conferma: «Consenso email
      confermato»; submission, evento e proiezione aggiornati dalle funzioni
      applicative; la prima email V3 parte subito; un doppio clic non fa altro.
  02  link scaduto: pagina chiara, niente consenso.
"""
from __future__ import annotations

import re
from datetime import timedelta

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _q, completo  # noqa: F401
from tests.test_consenso_digitale_postgres import dg  # noqa: F401
from tests.test_delete_arch_0_postgres import mondo, operatori  # noqa: F401
from tests.test_fase_b_followup_postgres import _contatto, _ctx, _iscrizioni, fb  # noqa: F401
from tests.test_v3_followup_postgres import _attiva, v3  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

BASE = "https://crm-test.example.invalid"
INDIRIZZO = "serena.prova@stima360-test.invalid"


@pytest.fixture
def conferma(dg, v3, monkeypatch):
    from communication.providers import email_smtp
    monkeypatch.setenv("PUBLIC_BASE_URL", BASE)
    monkeypatch.setenv("COMMUNICATION_MARKETING_ALLOWED_RECIPIENTS", INDIRIZZO)
    posta = []
    monkeypatch.setattr(email_smtp, "invia_mail", lambda a, oggetto, corpo, allegato=None, **kw: posta.append(
        {"a": a, "oggetto": oggetto, "testo": kw.get("corpo_testo") or corpo, "html": corpo}) or True)
    return {"m": dg, "posta": posta}


def _dispatch(m):
    from communication import dispatcher
    from communication.providers import email_smtp
    return dispatcher.dispatch_batch(_ctx(m, "owner_a"), channel="email", provider=email_smtp, limit=10)


def _chiedi_conferma(m, cid):
    from communication import consent_page
    link = consent_page.generate_link(_ctx(m, "admin_a"), cid)["url"]
    assert link.startswith(BASE)
    r = m["http"].post(link.replace(BASE, ""), data={"grant_email": "1"})
    assert r.status_code == 200, r.text
    return _q(m, "SELECT id FROM consent_submissions WHERE contact_id = %s", (cid,))[0][0]


def test_01_dal_link_dell_email_alla_prima_email_v3(conferma):
    m, posta = conferma["m"], conferma["posta"]
    _attiva(m)
    cid = _contatto(m, INDIRIZZO)                    # proprietario, nessun consenso ancora
    sid = _chiedi_conferma(m, cid)
    _dispatch(m)
    (email,) = [p for p in posta if "Conferma" in p["oggetto"]]
    url = re.search(r"https?://[^\s<>\"']+/consent/confirm/[A-Za-z0-9_-]+", email["testo"]).group(0)
    assert url.startswith(BASE + "/api/public/communication/consent/confirm/"), url
    assert f'href="{url}"' in email["html"], "il link e' cliccabile anche nella versione HTML (Gmail, mobile)"
    percorso = url.replace(BASE, "")
    # scanner e anteprime: solo GET, nessuna conferma
    for _ in range(2):
        r = m["http"].get(percorso)
        assert r.status_code == 200 and "data-conferma-form" in r.text
    assert _q(m, "SELECT email_status FROM consent_submissions WHERE id = %s", (sid,)) == [["pending"]]
    # il clic sul pulsante
    r = m["http"].post(percorso, headers={"user-agent": "Mozilla/5.0 (iPhone) Gmail"})
    assert r.status_code == 200 and "Consenso email confermato" in r.text and 'data-esito="confirmed"' in r.text
    assert _q(m, "SELECT email_status FROM consent_submissions WHERE id = %s", (sid,)) == [["confirmed"]]
    assert [tuple(x) for x in _q(m, "SELECT channel, decision, source FROM consent_events WHERE contact_id = %s",
                                 (cid,))] == [("email", "granted", "consent_page")]
    assert _q(m, "SELECT status FROM contact_channel_consents WHERE contact_id = %s AND channel = 'email'",
              (cid,)) == [["granted"]]
    # la prima email V3 e' partita subito, all'indirizzo autorizzato
    v3_inviate = [p for p in posta if "Conferma" not in p["oggetto"]]
    assert [p["a"] for p in v3_inviate] == [INDIRIZZO]
    assert [e["status"] for e in _iscrizioni(m, contact_id=cid)] == ["active"]
    # doppio clic: gia' confermato, niente altro
    r = m["http"].post(percorso)
    assert 'data-esito="already_confirmed"' in r.text
    assert len([p for p in posta if "Conferma" not in p["oggetto"]]) == 1
    assert _q(m, "SELECT count(*) FROM consent_events WHERE contact_id = %s", (cid,))[0][0] == 1


def test_02_link_scaduto(conferma, monkeypatch):
    from datetime import datetime, timezone
    from consent import capture
    m = conferma["m"]
    cid = _contatto(m, "scaduto@stima360-test.invalid")
    _chiedi_conferma(m, cid)
    _dispatch(m)
    (email,) = [p for p in conferma["posta"] if p["a"] == "scaduto@stima360-test.invalid"]
    percorso = re.search(r"/api/public/communication/consent/confirm/[A-Za-z0-9_-]+", email["testo"]).group(0)
    # il clic arriva oltre la scadenza (7 giorni)
    oltre = datetime.now(timezone.utc) + capture.CONFIRM_TTL + timedelta(hours=1)
    monkeypatch.setattr(capture, "_ora", lambda now=None: now or oltre)
    r = m["http"].post(percorso)
    assert "scaduto" in r.text and 'data-esito="expired"' in r.text
    assert _q(m, "SELECT count(*) FROM consent_events WHERE contact_id = %s AND channel = 'email'", (cid,))[0][0] == 0
