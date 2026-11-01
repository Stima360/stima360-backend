"""SBLOCCO DEFINITIVO FOLLOW-UP (decisioni del titolare), su PostgreSQL usa-e-getta.

  01  ponte della valutazione: stesso telefono + email DIVERSA = contatto
      nuovo (nessuna unione); senza email, o con la stessa, si riconosce dal
      numero come prima.
  02  gestione multipla dei consensi: il quadro (valido / revocato / da
      documentare) e la registrazione per piu' contatti: evento per contatto
      con dichiarazione, prima email subito all'indirizzo autorizzato, rifiuti
      per contatto, seconda registrazione senza doppioni, dichiarazione
      obbligatoria.
  03  separazione di una stima agganciata al contatto sbagliato: contatto
      nuovo, lead spostato, spunta riportata, ENTRAMBI fuori dalle
      automazioni; il giro del cron non li iscrive.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _q, completo  # noqa: F401
from tests.test_delete_arch_0_postgres import mondo, operatori  # noqa: F401
from tests.test_fase_b_followup_postgres import (  # noqa: F401
    GIORNO, _contatto, _ctx, _informativa, _iscrizioni, _sistema, fb)
from tests.test_v3_followup_postgres import T0, _attiva, _tick, v3  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")
AUTORIZZATO = "collaudo@stima360-test.invalid"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "v3_separa_stima.py"


def _stima(m, *, email, telefono, nome="Giorgio", cognome="Censori"):
    return _q(m, "INSERT INTO stime (agency_id, nome, cognome, email, telefono, comune) "
                 "VALUES (1, %s, %s, %s, %s, 'Giulianova') RETURNING id", (nome, cognome, email, telefono))[0][0]


def _ponte(m, stima, *, nome, cognome, email, telefono):
    from core import service
    from operator_auth.context import SystemAgencyContext
    return service.bridge_public_stima(stima, first_name=nome, last_name=cognome, email=email, phone=telefono,
                                       marketing_consent=False, marketing_consent_at=None,
                                       system_ctx=SystemAgencyContext(agency_id=1, origin="public_stima"))


def _contatti_con_telefono(m, tel):
    return _q(m, "SELECT id, email_normalized FROM contacts WHERE phone_normalized = %s ORDER BY id", (tel,))


def test_01_stesso_telefono_email_diversa_non_unisce(v3):
    m = v3
    s1 = _stima(m, email="censori@example.it", telefono="333 111 2222")
    _ponte(m, s1, nome="Giorgio", cognome="Censori", email="censori@example.it", telefono="333 111 2222")
    assert len(_contatti_con_telefono(m, "393331112222")) == 1
    # stesso numero, altra email, altro nome: contatto NUOVO
    s2 = _stima(m, email="canzoni@example.it", telefono="333 111 2222", cognome="Canzoni")
    _ponte(m, s2, nome="Giorgio", cognome="Canzoni", email="canzoni@example.it", telefono="333 111 2222")
    contatti = _contatti_con_telefono(m, "393331112222")
    assert [c[1] for c in contatti] == ["censori@example.it", "canzoni@example.it"]
    nuovo = contatti[1][0]
    assert _q(m, "SELECT l.contact_id FROM lead_stime ls JOIN leads l ON l.id = ls.lead_id WHERE ls.stima_id = %s",
              (s2,)) == [[nuovo]]
    # stesso numero SENZA email: si riconosce dal numero (nessun terzo contatto)
    s3 = _stima(m, email=None, telefono="333 111 2222")
    _ponte(m, s3, nome="Giorgio", cognome="Censori", email=None, telefono="333 111 2222")
    assert len(_contatti_con_telefono(m, "393331112222")) == 2
    # stessa email e stesso numero: lo stesso contatto
    s4 = _stima(m, email="canzoni@example.it", telefono="333 111 2222", cognome="Canzoni")
    _ponte(m, s4, nome="Giorgio", cognome="Canzoni", email="canzoni@example.it", telefono="333 111 2222")
    assert len(_contatti_con_telefono(m, "393331112222")) == 2


def test_02_gestione_multipla_dei_consensi(v3, monkeypatch):
    from communication import consent_bulk
    from communication.providers import email_smtp
    m = v3
    _attiva(m)
    monkeypatch.setenv("COMMUNICATION_MARKETING_ALLOWED_RECIPIENTS", AUTORIZZATO)
    posta = []
    monkeypatch.setattr(email_smtp, "invia_mail", lambda a, o, c, allegato=None, **kw: posta.append(a) or True)
    valido = _contatto(m, "valido@example.it", consenso="granted", quando=T0 - 10 * GIORNO)
    revocato = _contatto(m, "revocato@example.it", consenso="revoked", quando=T0 - 10 * GIORNO)
    spunta = _contatto(m, "spunta@example.it", consenso="pre_canale", quando=T0 - 3 * GIORNO)
    da_fare = _contatto(m, AUTORIZZATO)
    informativa = _informativa(m)
    quadro = consent_bulk.overview(_ctx(m, "owner_a"))
    stati = {c["contact_id"]: c for c in quadro["contacts"]}
    assert stati[valido]["state"] == "valid" and stati[revocato]["state"] == "revoked"
    assert stati[spunta]["state"] == "to_document" and stati[spunta]["site_tick"] is True
    assert stati[da_fare]["state"] == "to_document" and stati[da_fare]["email_masked"] == "c•••@stima360-test.invalid"
    assert quadro["counts"]["valid"] == 1 and "@" not in str([c["display_name"] for c in quadro["contacts"]])
    comune = dict(channels=["email"], decided_at=datetime.now(timezone.utc) - timedelta(minutes=5),
                  evidence_type="in_person", notice_id=informativa, note=None, attestation=True)
    with pytest.raises(Exception):
        consent_bulk.bulk_grant(_ctx(m, "owner_a"), contact_ids=[da_fare], evidence_ref="modulo evento 01/11",
                                **{**comune, "attestation": False})
    esito = consent_bulk.bulk_grant(_ctx(m, "owner_a"), contact_ids=[da_fare, spunta, valido],
                                    evidence_ref="modulo evento 01/11 pag. 3", **comune)
    per = {e["contact_id"]: e for e in esito["results"]}
    assert per[da_fare]["recorded"] and per[da_fare]["start"]["status"] == "sent"
    assert per[spunta]["recorded"] and per[spunta]["start"]["reason"] == "test_recipient_not_allowed"
    assert per[valido]["recorded"] and per[valido]["start"]["reason"] in ("already_enrolled", "test_recipient_not_allowed")
    assert posta == [AUTORIZZATO]
    note = _q(m, "SELECT count(*) FROM consent_events WHERE contact_id = %s AND channel = 'email' "
                 "AND note LIKE %s", (da_fare, "Dichiarazione dell'operatore%"))[0][0]
    assert note == 1
    # con la prova = recapito del cliente: rifiutato, gli altri no
    rifiuto = consent_bulk.bulk_grant(_ctx(m, "owner_a"), contact_ids=[revocato], evidence_ref="revocato@example.it",
                                      **comune)
    assert rifiuto["rejected"] == 1 and not rifiuto["results"][0]["recorded"]
    # seconda registrazione identica: nessun doppione, nessuna seconda email
    di_nuovo = consent_bulk.bulk_grant(_ctx(m, "owner_a"), contact_ids=[da_fare],
                                       evidence_ref="modulo evento 01/11 pag. 3", **comune)
    assert di_nuovo["results"][0]["created"] is False and di_nuovo["results"][0]["start"]["reason"] == "already_enrolled"
    assert posta == [AUTORIZZATO] and len(_iscrizioni(m, contact_id=da_fare)) == 1


def test_03_separazione_della_stima_agganciata_al_contatto_sbagliato(v3):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from communication import audience
    m = v3
    _attiva(m)
    _informativa(m)
    # l'associazione sbagliata, come la faceva il ponte di prima
    censori = _contatto(m, "censori@example.it")
    _q(m, "UPDATE contacts SET phone = '333 111 2222', phone_normalized = '393331112222' WHERE id = %s", (censori,))
    stima = _stima(m, email="canzoni@example.it", telefono="333 111 2222", cognome="Canzoni")
    lead = _q(m, "INSERT INTO leads (agency_id, contact_id, pipeline, status, stage, source) "
                 "VALUES (1, %s, 'sell', 'open', 'new', 'public_stima') RETURNING id", (censori,))[0][0]
    _q(m, "INSERT INTO lead_stime (lead_id, stima_id, relation_type) VALUES (%s, %s, 'related')", (lead, stima))
    from consent import service as cs
    cs.record_grant(_sistema(1), contact_id=censori, purpose="marketing", source="public_stima", actor_type="subject",
                    decided_at=T0 - GIORNO, evidence_type="stima", evidence_ref=str(stima),
                    idempotency_key=f"public_stima:{stima}:marketing")
    spec = importlib.util.spec_from_file_location("v3_separa_stima", SCRIPT)
    separa = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(separa)
    conn = psycopg2.connect(m["dsn"], cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            p = separa.piano(cur, stima, censori)
            assert not p["stessa_email"] and len(p["lead"]) == 1 and len(p["spunte"]) == 1
            esito = separa.esegui(cur, p, m["ids"]["owner_a"])
        conn.commit()
    finally:
        conn.close()
    nuovo = esito["nuovo_contatto"]
    assert esito["lead_spostati"] == [lead] and esito["pause"] == {censori: True, nuovo: True}
    assert _q(m, "SELECT contact_id FROM leads WHERE id = %s", (lead,)) == [[nuovo]]
    assert _q(m, "SELECT email_normalized, phone_normalized, last_name FROM contacts WHERE id = %s", (nuovo,)) == \
        [["canzoni@example.it", "393331112222", "Canzoni"]]
    assert _q(m, "SELECT count(*) FROM consent_events WHERE contact_id = %s AND evidence_ref = %s",
              (nuovo, str(stima)))[0][0] == 1
    assert _q(m, "SELECT count(*) FROM consent_events WHERE contact_id = %s", (censori,))[0][0] == 1, "storico intatto"
    # il cron non li iscrive: pausa manuale su entrambi
    _tick(m, T0)
    assert not [e for e in _iscrizioni(m) if e["contact_id"] in (censori, nuovo)]
    with psycopg2.connect(m["dsn"], cursor_factory=RealDictCursor) as c2, c2.cursor() as cur:
        for cid in (censori, nuovo):
            assert audience.motivo_esclusione(cur, _sistema(1), cid, now=T0, journey_id=1) in (
                "automations_paused", "no_email_consent")
