"""V3 - FOLLOW-UP PROPRIETARI 180 GIORNI, su PostgreSQL usa-e-getta.

Motore (`journey_tick`) e dispatcher VERI, provider finto (`null`): nessuna
email parte. I testi sono TESTI DI PROVA del test, registrati con lo stesso
meccanismo del codice (`templates.templates_v3`); i 16 testi approvati li usa
la prova generale (`test_v3_collaudo_generale_postgres.py`).

  01  solo proprietari/venditori VERIFICATI: ruolo del contatto o immobile
      collegato come owner/seller; fuori acquirenti, richiedenti dal sito
      (collegati come `contact`), ruoli scaduti, immobili nel Cestino.
  02  consenso: senza o revocato non entra; revoca dopo l'ingresso -> il
      messaggio in coda non parte (dispatcher) e la sequenza si ferma (tick).
  03  ogni email porta il link di disiscrizione; usarlo blocca i successivi.
  04  limite dei destinatari TEST: solo gli indirizzi elencati ricevono
      marketing; gli altri restano IN CODA intatti; la valutazione parte.
  05  email 6: link della guida PDF solo se il file approvato c'e'; senza,
      l'email 6 non si compone. La rotta serve il PDF o 404.
  06  email 15: un'attivita' per l'operatore, una sola, solo per la 15.
  07  attivazione: con un'altra versione attiva serve la conferma esplicita;
      predisposizione: 422 senza testi, bozza con i testi.
  08  il quadro dell'amministratore: stato, iscrizioni, prossima email,
      inviate/bloccate; nessun dato personale.
  09  nessun doppione fra agenzie (stessa email, una sola sequenza).
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _q, completo  # noqa: F401
from tests.test_delete_arch_0_postgres import _immobile as _immobile_scheda, mondo, operatori  # noqa: F401
from tests.test_fase_b_followup_postgres import (  # noqa: F401
    GIORNO, _contatto, _ctx, _guardia, _informativa, _iscrizioni, _messaggi, _sistema, fb)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

#: Un martedi' nel passato: tutti i passi risultano gia' dovuti per il claim.
T0 = datetime(2026, 1, 6, 10, 0, tzinfo=timezone.utc)

#: TESTI DI PROVA (non i testi approvati): solo per far girare il meccanismo.
TESTI_PROVA = {n: f"Ciao {{{{nome}}}}, questa e' l'email di prova numero {n}."
               + ("\nLa guida: {{link_guida}}" if n == 6 else "") for n in range(1, 17)}


@pytest.fixture
def v3(fb, monkeypatch):
    from communication import templates
    for chiave, t in templates.templates_v3(TESTI_PROVA).items():
        monkeypatch.setitem(templates.REGISTRY, chiave, t)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://crm-test.example.invalid")
    monkeypatch.delenv("COMMUNICATION_MARKETING_ALLOWED_RECIPIENTS", raising=False)
    # Il claim ammette i messaggi della sequenza solo nella finestra d'invio
    # (lun-sab 9-19, Roma) calcolata sull'ora VERA: la si fissa, come fanno i
    # test della Tappa 6, cosi' l'esito non dipende da quando gira la suite.
    from communication import repository as ledger

    class _Martedi11(datetime):
        @classmethod
        def now(cls, tz=None):
            ora = datetime(2026, 10, 20, 9, tzinfo=timezone.utc)
            return ora if tz else ora.replace(tzinfo=None)
    monkeypatch.setattr(ledger, "datetime", _Martedi11)
    _informativa(fb)
    return fb


def _attiva(m):
    from communication import journey_catalog, journey_service
    j = journey_catalog.ensure_followup_v3(_ctx(m, "owner_a"))["journey"]
    return journey_service.activate_journey(_ctx(m, "owner_a"), j["id"], replace_active=True)


def _tick(m, now, agenzia=1):
    from communication import journey_tick
    return journey_tick.tick(_sistema(agenzia), now=now)


def _proprietario(m, email, **kw):
    return _contatto(m, email, consenso="granted", quando=T0 - 10 * GIORNO, **kw)


def _immobile(m):
    return _immobile_scheda(m)["id"]


def _provider():
    from communication.providers import null
    inviati = []

    def send(message):
        inviati.append(dict(message))
        return null.send(message)
    return SimpleNamespace(NAME=null.NAME, CAPABILITIES=null.CAPABILITIES, send=send), inviati


def _dispatch(m, agenzia=1):
    from communication import dispatcher
    provider, inviati = _provider()
    conteggi = dispatcher.dispatch_batch(_ctx(m, "owner_a"), channel="email", provider=provider, limit=50)
    return conteggi, inviati


def _avanza(m, cid, fino_al_giorno):
    """Tick + dispatch giorno per giorno fino al giorno dato. Il dispatcher
    scrive l'ora VERA dell'invio: la si riporta all'ora simulata, come fanno i
    test della Fase B, altrimenti la regola delle 48 ore guarderebbe a oggi."""
    for giorno in range(0, fino_al_giorno + 1):
        _tick(m, T0 + giorno * GIORNO)
        _dispatch(m)
        _q(m, "UPDATE communication_messages SET sent_at = scheduled_at "
              "WHERE status = 'sent' AND enrollment_id IS NOT NULL AND sent_at > scheduled_at")
        _tick(m, T0 + giorno * GIORNO)


# ---------------------------------------------------------------------------

def test_01_solo_proprietari_verificati(v3):
    m = v3
    _attiva(m)
    ruolo = _proprietario(m, "ruolo@example.it")
    venditore = _proprietario(m, "venditore@example.it", ruolo="seller")
    da_immobile = _proprietario(m, "immobile@example.it", ruolo=None)
    casa = _immobile(m)
    _q(m, "INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s, %s, 'owner')", (casa, da_immobile))
    acquirente = _proprietario(m, "acquirente@example.it", ruolo="buyer")
    senza_ruolo = _proprietario(m, "nessuno@example.it", ruolo=None)
    dal_sito = _proprietario(m, "sito@example.it", ruolo=None)
    _q(m, "INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s, %s, 'contact')", (casa, dal_sito))
    scaduto = _proprietario(m, "scaduto@example.it", ruolo=None)
    _q(m, "INSERT INTO contact_roles (contact_id, role, valid_to) VALUES (%s, 'owner', %s)", (scaduto, T0 - GIORNO))
    nel_cestino = _proprietario(m, "cestino@example.it", ruolo=None)
    vecchia = _immobile(m)
    _q(m, "INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s, %s, 'owner')", (vecchia, nel_cestino))
    _q(m, "UPDATE properties SET deleted_at = %s, deleted_reason = 'test_record' WHERE id = %s", (T0 - GIORNO, vecchia))
    _tick(m, T0)
    iscritti = {e["contact_id"] for e in _iscrizioni(m, agency_id=1)}
    assert iscritti == {ruolo, venditore, da_immobile}, iscritti


def test_02_consenso_e_revoca(v3):
    from consent import service as cs
    m = v3
    _attiva(m)
    mai = _contatto(m, "mai@example.it")                      # proprietario senza consenso
    revocato = _contatto(m, "rev@example.it", consenso="revoked", quando=T0 - 10 * GIORNO)
    dopo = _proprietario(m, "dopo@example.it")
    _tick(m, T0)
    assert {e["contact_id"] for e in _iscrizioni(m, agency_id=1)} == {dopo}
    e = _iscrizioni(m, contact_id=dopo)[0]
    _tick(m, T0 + 4 * GIORNO)                                 # email 1 in coda
    assert [x["status"] for x in _messaggi(m, e["id"])] == ["queued"]
    cs.record_revocation(_sistema(1), contact_id=dopo, purpose="marketing", channel="email",
                         source="unsubscribe_link", actor_type="subject", decided_at=T0 + 4 * GIORNO,
                         idempotency_key=f"rev:{dopo}")
    conteggi, inviati = _dispatch(m)
    assert inviati == [] and conteggi["suppressed"] == 1, conteggi
    assert [x["status"] for x in _messaggi(m, e["id"])] == ["suppressed"]
    _tick(m, T0 + 5 * GIORNO)
    e = _iscrizioni(m, contact_id=dopo)[0]
    assert e["status"] == "stopped" and e["stop_reason"] in ("consent_revoked", "platform_optout")
    assert mai not in {x["contact_id"] for x in _iscrizioni(m)} and revocato not in {x["contact_id"] for x in _iscrizioni(m)}


def test_03_disiscrizione_in_ogni_email(v3):
    from communication import unsubscribe as u
    m = v3
    _attiva(m)
    cid = _proprietario(m, "disiscrivi@example.it")
    _avanza(m, cid, 8)                                        # email 1 (giorno 4) e 2 (giorno 8)
    e = _iscrizioni(m, contact_id=cid)[0]
    spediti = [x for x in _messaggi(m, e["id"]) if x["status"] == "sent"]
    assert [x["step_no"] for x in spediti] == [1, 2]
    for x in spediti:
        assert "Se non vuoi piu' ricevere questi messaggi: https://crm-test.example.invalid" in x["rendered_body"]
        assert x["metadata"].get("unsubscribe_url")
    token = spediti[0]["rendered_body"].split("?t=", 1)[1].split()[0]
    u.process(token)
    _avanza(m, cid, 13)
    e = _iscrizioni(m, contact_id=cid)[0]
    assert e["status"] == "stopped"
    assert not [x for x in _messaggi(m, e["id"]) if x["status"] == "sent" and x["step_no"] > 2]


def test_04_limite_destinatari_test(v3, monkeypatch):
    from communication import service as communication_service
    m = v3
    _attiva(m)
    ammesso = _proprietario(m, "ammesso@example.it")
    reale = _proprietario(m, "cliente.reale@example.it")
    _tick(m, T0)
    _tick(m, T0 + 4 * GIORNO)
    # una mail di valutazione (servizio) verso un indirizzo fuori elenco
    servizio = communication_service.enqueue(
        _sistema(1), contact_id=reale, channel="email", communication_type="service", mode="automatic",
        reason_code="stima_pdf", destination_snapshot="cliente.reale@example.it", subject_snapshot="La tua stima",
        rendered_body="<p>stima</p>", idempotency_key=f"stima:{uuid.uuid4().hex}",
        metadata={"pdf_url": "https://crm-test.example.invalid/pdf/1"})["message"]
    monkeypatch.setenv("COMMUNICATION_MARKETING_ALLOWED_RECIPIENTS", "Ammesso@example.it, altro@example.it")
    conteggi, inviati = _dispatch(m)
    destinatari = sorted(x["destination_snapshot"] for x in inviati)
    assert destinatari == ["ammesso@example.it", "cliente.reale@example.it"], destinatari
    assert next(x for x in inviati if x["destination_snapshot"] == "cliente.reale@example.it")["id"] == servizio["id"]
    trattenuto = [x for x in _messaggi(m, _iscrizioni(m, contact_id=reale)[0]["id"])]
    assert [(x["status"], x["attempt_count"]) for x in trattenuto] == [("queued", 0)], "resta in coda intatto"
    # elenco vuoto: nessun marketing parte, chiuso per difetto
    monkeypatch.setenv("COMMUNICATION_MARKETING_ALLOWED_RECIPIENTS", "")
    conteggi, inviati = _dispatch(m)
    assert inviati == []
    # senza limite: comportamento di sempre
    monkeypatch.delenv("COMMUNICATION_MARKETING_ALLOWED_RECIPIENTS")
    conteggi, inviati = _dispatch(m)
    assert [x["destination_snapshot"] for x in inviati] == ["cliente.reale@example.it"]


def test_05_guida_pdf_email_6(v3, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from communication import guida_pdf, public_router
    m = v3
    _attiva(m)
    cid = _proprietario(m, "guida@example.it")
    _avanza(m, cid, 35)   # giorno 33 = domenica: la finestra (lun-sab) la sposta a lunedi'
    e = _iscrizioni(m, contact_id=cid)[0]
    sesta = next(x for x in _messaggi(m, e["id"]) if x["step_no"] == 6)
    link = "https://crm-test.example.invalid" + guida_pdf.PERCORSO
    assert link in sesta["rendered_body"] and sesta["status"] == "sent"
    assert sesta["subject_snapshot"] == "🎁 Un regalo per te: la guida completa per vendere casa"
    app = FastAPI()
    app.include_router(public_router.router)
    risposta = TestClient(app).get(guida_pdf.PERCORSO)
    assert risposta.status_code == 200 and risposta.headers["content-type"] == "application/pdf"
    assert hashlib.sha256(risposta.content).hexdigest() == guida_pdf.SHA256
    # file non disponibile: rotta 404 e l'email 6 di un altro contatto non si compone
    falso = tmp_path / "guida.pdf"
    falso.write_bytes(b"%PDF-1.4 non la guida approvata")
    monkeypatch.setattr(guida_pdf, "FILE", falso)
    guida_pdf.disponibile.cache_clear()
    try:
        assert TestClient(app).get(guida_pdf.PERCORSO).status_code == 404
        altro = _proprietario(m, "senza.guida@example.it")
        _q(m, "UPDATE communication_enrollments SET status = 'completed', completed_at = %s, next_step_no = NULL, "
              "next_action_at = NULL, next_action_kind = NULL WHERE id = %s", (T0, e["id"]))
        _avanza(m, altro, 35)
        e2 = _iscrizioni(m, contact_id=altro)[0]
        assert [x["step_no"] for x in _messaggi(m, e2["id"]) if x["status"] == "sent"] == [1, 2, 3, 4, 5]
        assert not [x for x in _messaggi(m, e2["id"]) if x["step_no"] == 6], "senza guida l'email 6 non nasce"
    finally:
        guida_pdf.disponibile.cache_clear()


def test_06_email_15_crea_una_attivita(v3):
    m = v3
    _attiva(m)
    cid = _proprietario(m, "risposte@example.it")
    _avanza(m, cid, 157)
    e = _iscrizioni(m, contact_id=cid)[0]
    quindicesima = next(x for x in _messaggi(m, e["id"]) if x["step_no"] == 15)
    assert quindicesima["status"] == "sent"
    attivita = _q(m, "SELECT title, task_type, status, contact_id, metadata->>'source_message_id' FROM tasks "
                     "WHERE agency_id = 1 AND task_type = 'followup_v3_reply'")
    assert [tuple(a) for a in attivita] == [("Controlla la risposta all'email 15 (A/B/C/D)", "followup_v3_reply",
                                             "open", cid, str(quindicesima["id"]))]
    descrizione = _q(m, "SELECT description FROM tasks WHERE task_type = 'followup_v3_reply'")[0][0]
    for voce in ("A = proprietario indeciso", "B = immobile in vendita senza richieste",
                 "C = visite senza proposte", "D = immobile gia' venduto"):
        assert voce in descrizione


def test_07_attivazione_esplicita_e_predisposizione(v3, monkeypatch):
    from communication import journey_catalog, journey_service, templates
    from communication.exceptions import ConflictError, ValidationError
    m = v3
    v2 = journey_catalog.ensure_stima_lead_v2(_ctx(m, "owner_a"))["journey"]
    journey_service.activate_journey(_ctx(m, "owner_a"), v2["id"])
    v3j = journey_catalog.ensure_followup_v3(_ctx(m, "owner_a"))["journey"]
    assert v3j["status"] == "draft" and len(v3j["steps"]) == 16
    with pytest.raises(ConflictError):
        journey_service.activate_journey(_ctx(m, "owner_a"), v3j["id"])
    assert _q(m, "SELECT status FROM communication_journeys WHERE id = %s", (v2["id"],))[0][0] == "active"
    assert _q(m, "SELECT status FROM communication_journeys WHERE id = %s", (v3j["id"],))[0][0] == "draft"
    # senza testi la v3 non si predispone (agenzia 2, ancora senza v3)
    for n in range(1, 17):
        monkeypatch.delitem(templates.REGISTRY, (f"followup_v3_m{n}", 3))
    with pytest.raises(ValidationError):
        journey_catalog.ensure_followup_v3(_ctx(m, "owner_b"))


def test_08_quadro_amministratore(v3, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from communication import journey_service, router
    from communication.dependencies import require_dispatch_context
    m = v3
    _attiva(m)
    cid = _proprietario(m, "quadro@example.it")
    _avanza(m, cid, 8)
    quadro = journey_service.overview(_ctx(m, "owner_a"))
    v3j = next(j for j in quadro["journeys"] if j["version"] == 3)
    assert v3j["status"] == "active" and v3j["steps"] == 16
    assert v3j["enrollments"] == {"active": 1} and v3j["messages"] == {"sent": 2}
    assert v3j["next_action"]["step_no"] == 3 and v3j["next_action"]["day"] == 12
    assert v3j["next_action"]["subject"] == "Il tuo prezzo di vendita supera questo test?"
    assert quadro["followup_v3"]["guide_available"] is True and quadro["followup_v3"]["texts_total"] == 16
    assert quadro["test_recipients_limit"] == {"active": False, "allowed_count": None}
    assert "quadro@example.it" not in str(quadro)
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[require_dispatch_context] = lambda: _ctx(m, "owner_a")
    assert TestClient(app).get("/api/communication/journeys/overview").status_code == 200


def test_09_nessun_doppione_fra_agenzie(v3):
    m = v3
    from communication import journey_catalog, journey_service
    _attiva(m)
    j2 = journey_catalog.ensure_followup_v3(_ctx(m, "owner_b"))["journey"]
    journey_service.activate_journey(_ctx(m, "owner_b"), j2["id"], replace_active=True)
    a = _proprietario(m, "stessa@example.it")
    b = _proprietario(m, "stessa@example.it", agenzia=2)
    _tick(m, T0)
    _tick(m, T0, agenzia=2)
    _tick(m, T0 + GIORNO)
    _tick(m, T0 + GIORNO, agenzia=2)
    aperte = [e for e in _iscrizioni(m) if e["status"] in ("active", "paused")
              and e["contact_id"] in (a, b)]
    assert len(aperte) == 1


def test_10_verifica_coda_in_sola_lettura(v3):
    """Lo script della Shell Render: conteggi e date, nessun indirizzo, nessuna scrittura."""
    import importlib.util
    from pathlib import Path
    m = v3
    _attiva(m)
    cid = _proprietario(m, "coda@example.it")
    _tick(m, T0)
    _tick(m, T0 + 4 * GIORNO)
    spec = importlib.util.spec_from_file_location(
        "v3_verifica_coda", Path(__file__).resolve().parents[1] / "scripts" / "v3_verifica_coda.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    prima = _q(m, "SELECT count(*), max(updated_at) FROM communication_messages")[0]
    with m["conn"].cursor() as cur:
        r = modulo.rapporto(cur)
    assert _q(m, "SELECT count(*), max(updated_at) FROM communication_messages")[0] == prima
    righe = [x for x in r["coda"] if x[1] == "marketing" and x[2] == "m1"]
    assert righe and righe[0][3] == "queued" and righe[0][4] == 1 and righe[0][7] == 1
    assert any(s[1] == "stima_lead" and s[2] == 3 and s[3] == "active" and s[5] == 1 for s in r["sequenze"])
    assert "coda@example.it" not in str(r)
