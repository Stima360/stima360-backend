"""V3 - PARTENZA IMMEDIATA dopo un consenso email, su PostgreSQL usa-e-getta.

Regola del titolare: al salvataggio di un consenso email valido il contatto
idoneo entra nel V3 e la PRIMA email parte subito, anche fuori dalla finestra
d'invio; le successive seguono il calendario. Motore, dispatcher e adapter
SMTP veri; la sola primitiva `invia_mail` registra invece di spedire. Il
limite dei destinatari TEST e' attivo.

  01  domenica sera: M1 parte subito; un secondo salvataggio non la ripete.
  02  indirizzo fuori elenco TEST: resta in coda, e lo si dice.
  03  esclusioni con il motivo: senza consenso, revocato, acquirente, pausa.
  04  tetto giornaliero raggiunto: il motivo lo dice.
  05  dopo M1 il calendario: M2 al giorno 8; un messaggio di sequenza NON
      segnato resta legato alla finestra.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _q, completo  # noqa: F401
from tests.test_delete_arch_0_postgres import _lead, _stima, mondo, operatori  # noqa: F401
from tests.test_fase_b_followup_postgres import (  # noqa: F401
    GIORNO, _contatto, _ctx, _iscrizioni, _messaggi, _sistema, fb)
from tests.test_v3_followup_postgres import T0, _attiva, v3  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

AUTORIZZATO = "collaudo@stima360-test.invalid"


@pytest.fixture
def subito(v3, monkeypatch):
    from communication import repository as ledger
    from communication.providers import email_smtp

    class _Domenica23(datetime):     # fuori finestra: lun-sab 9-19
        @classmethod
        def now(cls, tz=None):
            ora = datetime(2026, 10, 25, 22, tzinfo=timezone.utc)
            return ora if tz else ora.replace(tzinfo=None)
    monkeypatch.setattr(ledger, "datetime", _Domenica23)
    monkeypatch.setenv("COMMUNICATION_MARKETING_ALLOWED_RECIPIENTS", AUTORIZZATO)
    posta = []
    monkeypatch.setattr(email_smtp, "invia_mail",
                        lambda a, oggetto, corpo, allegato=None, **kw: posta.append((a, oggetto)) or True)
    _attiva(v3)
    return {"m": v3, "posta": posta}


def _avvia(m, cid):
    from communication import avvio_immediato
    return avvio_immediato.avvia(_ctx(m, "owner_a"), cid)


def _proprietario(m, email, **kw):
    return _contatto(m, email, consenso="granted", quando=T0 - 10 * GIORNO, **kw)


def test_01_parte_subito_anche_di_domenica_e_una_volta_sola(subito):
    m, posta = subito["m"], subito["posta"]
    cid = _proprietario(m, AUTORIZZATO)
    esito = _avvia(m, cid)
    assert esito["status"] == "sent", esito
    assert [a for a, _ in posta] == [AUTORIZZATO]
    e = _iscrizioni(m, contact_id=cid)[0]
    prima = [x for x in _messaggi(m, e["id"]) if x["step_no"] == 1]
    assert [x["status"] for x in prima] == ["sent"] and prima[0]["metadata"].get("partenza_immediata") is True
    # salvare di nuovo: nessuna seconda iscrizione, nessuna seconda email
    di_nuovo = _avvia(m, cid)
    assert di_nuovo["status"] == "not_started" and di_nuovo["reason"] == "already_enrolled", di_nuovo
    assert len(posta) == 1 and len(_iscrizioni(m, contact_id=cid)) == 1


def test_02_fuori_elenco_TEST_resta_in_coda_e_lo_dice(subito):
    m, posta = subito["m"], subito["posta"]
    cid = _proprietario(m, "cliente.reale@example.it")
    esito = _avvia(m, cid)
    assert esito["status"] == "queued" and esito["reason"] == "test_recipient_not_allowed", esito
    assert "destinatari autorizzati" in esito["reason_label"]
    assert posta == []
    e = _iscrizioni(m, contact_id=cid)[0]
    assert [(x["status"], x["attempt_count"]) for x in _messaggi(m, e["id"])] == [("queued", 0)]


def test_03_le_esclusioni_hanno_il_loro_motivo(subito):
    from communication import journey_service
    m, posta = subito["m"], subito["posta"]
    senza = _contatto(m, "senza.consenso@example.it")
    assert _avvia(m, senza)["reason"] == "no_email_consent"
    revocato = _contatto(m, "revocato@example.it", consenso="revoked", quando=T0 - 10 * GIORNO)
    assert _avvia(m, revocato)["reason"] == "no_email_consent"
    acquirente = _proprietario(m, "acquirente@example.it", ruolo="buyer")
    assert _avvia(m, acquirente)["reason"] == "not_owner_or_valuation"
    in_pausa = _proprietario(m, AUTORIZZATO)
    journey_service.pause_automations(_ctx(m, "owner_a"), in_pausa)
    esito = _avvia(m, in_pausa)
    assert esito["status"] == "not_started" and esito["reason"] == "automations_paused", esito
    assert posta == [] and _q(m, "SELECT count(*) FROM communication_enrollments")[0][0] == 0


def test_04_tetto_giornaliero(subito, monkeypatch):
    from communication import audience
    m = subito["m"]
    monkeypatch.setattr(audience, "TETTO_GIORNALIERO", 1)
    primo = _proprietario(m, AUTORIZZATO)
    assert _avvia(m, primo)["status"] == "sent"
    secondo = _proprietario(m, "secondo@example.it")
    esito = _avvia(m, secondo)
    assert esito["status"] == "not_started" and esito["reason"] == "daily_cap_reached", esito


def test_05_dopo_la_prima_il_calendario(subito):
    from communication import journey_tick
    m = subito["m"]
    cid = _proprietario(m, AUTORIZZATO)
    assert _avvia(m, cid)["status"] == "sent"
    journey_tick.tick(_sistema(1))      # il giro: avanza al passo 2
    e = _iscrizioni(m, contact_id=cid)[0]
    assert e["status"] == "active" and e["next_step_no"] == 2
    dal_inizio = (e["next_action_at"] - e["trigger_sent_at"]) / GIORNO
    assert 8 <= dal_inizio < 10, dal_inizio


def test_06_l_iscrizione_del_cron_resta_nella_finestra(subito):
    """Le iscrizioni fatte dal giro del cron (consensi gia' validi) ricevono
    anche loro la prima email subito dovuta, ma NON segnata: di domenica sera
    il claim non la prende, partira' alla riapertura della finestra."""
    from communication import dispatcher, journey_tick
    from communication.providers import email_smtp
    m, posta = subito["m"], subito["posta"]
    cid = _proprietario(m, AUTORIZZATO)
    journey_tick.tick(_sistema(1))
    e = _iscrizioni(m, contact_id=cid)[0]
    prima = _messaggi(m, e["id"])
    assert [(x["step_no"], x["status"]) for x in prima] == [(1, "queued")]
    assert not prima[0]["metadata"].get("partenza_immediata")
    dispatcher.dispatch_batch(_ctx(m, "owner_a"), channel="email", provider=email_smtp, limit=10)
    assert posta == [] and [x["status"] for x in _messaggi(m, e["id"])] == ["queued"]


def _scheda(m):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from communication import router
    from operator_auth.dependencies import legacy_basic_agency_context
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[legacy_basic_agency_context] = lambda: _ctx(m, "owner_a")
    return TestClient(app)


def _consenso_telefono(m, client, cid, **extra):
    informativa = _q(m, "SELECT id FROM consent_notices WHERE purpose = 'marketing' AND valid_to IS NULL "
                        "ORDER BY id DESC LIMIT 1")[0][0]
    corpo = {"channels": ["email"], "decided_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
             "evidence_type": "phone_call", "evidence_ref": "telefonata 01/11 ore 10:05, risposto da Anna",
             "notice_id": informativa, "attestation": True, **extra}
    return client.post(f"/api/communication/contacts/{cid}/consent/grant", json=corpo)


def test_07_registra_consenso_dalla_scheda_parte_subito_e_una_volta(subito):
    m, posta = subito["m"], subito["posta"]
    client = _scheda(m)
    cid = _contatto(m, AUTORIZZATO)                     # proprietario, nessun consenso ancora
    senza = _consenso_telefono(m, client, cid, attestation=False)
    assert senza.status_code == 422, senza.text
    primo = _consenso_telefono(m, client, cid)
    assert primo.status_code == 201, primo.text
    assert primo.json()["start"]["status"] == "sent", primo.json()
    nota = _q(m, "SELECT note, evidence_type, actor_type FROM consent_events WHERE contact_id = %s "
                 "AND channel = 'email'", (cid,))
    assert nota[0][1] == "phone_call" and nota[0][2] == "operator" and "Dichiarazione dell'operatore" in nota[0][0]
    secondo = _consenso_telefono(m, client, cid)
    assert secondo.status_code == 201 and secondo.json()["start"]["reason"] == "already_enrolled"
    assert [a for a, _ in posta] == [AUTORIZZATO]


def test_08_la_revoca_dalla_scheda_ferma_la_sequenza(subito):
    from communication import journey_tick
    m, posta = subito["m"], subito["posta"]
    client = _scheda(m)
    cid = _contatto(m, AUTORIZZATO)
    assert _consenso_telefono(m, client, cid).json()["start"]["status"] == "sent"
    revoca = client.post(f"/api/communication/contacts/{cid}/consent/refusal", json={
        "channels": ["email"], "decided_at": datetime.now(timezone.utc).isoformat(), "scope": "agency",
        "note": "Al telefono ha chiesto di non ricevere piu' email."})
    assert revoca.status_code == 201, revoca.text
    journey_tick.tick(_sistema(1))
    e = _iscrizioni(m, contact_id=cid)[0]
    assert e["status"] == "stopped"
    assert len(posta) == 1


def test_09_una_pausa_dopo_la_prima_non_la_ripete(subito):
    """Con la prima email partita prima del suo giorno di calendario, una pausa
    e la ripresa NON la rimettono in coda: si riprende dalla seconda."""
    from communication import journey_tick
    m, posta = subito["m"], subito["posta"]
    cid = _proprietario(m, AUTORIZZATO)
    assert _avvia(m, cid)["status"] == "sent"
    adesso = datetime.now(timezone.utc)
    app = _q(m, "INSERT INTO appointments (agency_id, assigned_user_id, appointment_type, status, start_at, end_at, "
                "contact_id) VALUES (1, %s, 'seller_meeting', 'requested', %s, %s, %s) RETURNING id",
             (m["ids"]["owner_a"], adesso + timedelta(hours=1), adesso + timedelta(hours=2), cid))[0][0]
    journey_tick.tick(_sistema(1), now=adesso + timedelta(minutes=5))
    assert _iscrizioni(m, contact_id=cid)[0]["status"] == "paused"
    journey_tick.tick(_sistema(1), now=adesso + timedelta(hours=3))
    e = _iscrizioni(m, contact_id=cid)[0]
    assert e["status"] == "active" and e["next_step_no"] == 2, e
    assert [x["step_no"] for x in _messaggi(m, e["id"])].count(1) == 1 and len(posta) == 1
    assert _q(m, "SELECT status FROM appointments WHERE id = %s", (app,)) == [["requested"]]
