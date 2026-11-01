"""V3 - il consenso della casella del SITO entra nel follow-up dal giro del cron.

La spunta del sito arriva senza canale e, da sola, non autorizza niente (C2):
diventa un consenso EMAIL solo con il riconoscimento C3.4/C3.5 e le sue prove.
Quel riconoscimento girava all'import del sito e all'apertura della scheda:
senza import attivo e senza che nessuno riaprisse la scheda, il contatto
restava fuori dal V3 per sempre. Ora lo esegue anche il motore delle sequenze,
a ogni giro, prima delle iscrizioni: stesse prove, stesse esclusioni.

  01  spunta riconoscibile + valutazione collegata: il giro registra il
      consenso email (con la sua prova) e iscrive il contatto.
  02  una revoca successiva alla spunta prevale: niente consenso, niente
      iscrizione.
  03  la spunta di un'altra agenzia non e' toccata dal giro dell'agenzia 1.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _q, completo  # noqa: F401
from tests.test_delete_arch_0_postgres import _lead, mondo, operatori  # noqa: F401
from tests.test_fase_b_followup_postgres import GIORNO, _iscrizioni, _sistema, fb  # noqa: F401
from tests.test_site_recognition_postgres import _informativa_sito, _spunta_sito
from tests.test_v3_followup_postgres import T0, _attiva, _tick, v3  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

SPUNTA = T0 - 20 * GIORNO


def _con_valutazione(m, cid, stima):
    _q(m, "INSERT INTO lead_stime (lead_id, stima_id, relation_type) VALUES (%s, %s, 'origin')",
       (_lead(m, cid), stima))


def _consensi_email(m, cid):
    return _q(m, "SELECT source, evidence_type FROM consent_events WHERE contact_id = %s "
                 "AND purpose = 'marketing' AND channel = 'email' AND decision = 'granted'", (cid,))


def test_01_la_spunta_riconoscibile_entra_dal_giro(v3):
    m = v3
    _attiva(m)
    _informativa_sito(m, registrata=SPUNTA - timedelta(hours=2))
    cid, stima = _spunta_sito(m, "spunta.sito@example.it", quando=SPUNTA)
    _con_valutazione(m, cid, stima)
    assert _consensi_email(m, cid) == []
    _tick(m, T0)
    assert [tuple(r) for r in _consensi_email(m, cid)] == [("public_stima", "stima")]
    assert [e["status"] for e in _iscrizioni(m, contact_id=cid)] == ["active"]


def test_02_una_revoca_successiva_prevale(v3):
    from consent import service as cs
    m = v3
    _attiva(m)
    _informativa_sito(m, registrata=SPUNTA - timedelta(hours=2))
    cid, stima = _spunta_sito(m, "spunta.revocata@example.it", quando=SPUNTA)
    _con_valutazione(m, cid, stima)
    cs.record_revocation(_sistema(1), contact_id=cid, purpose="marketing", channel="email",
                         source="unsubscribe_link", actor_type="subject", decided_at=SPUNTA + GIORNO,
                         idempotency_key=f"rev:{cid}")
    _tick(m, T0)
    assert _consensi_email(m, cid) == []
    assert _iscrizioni(m, contact_id=cid) == []


def test_03_la_spunta_di_un_altra_agenzia_non_e_toccata(v3):
    from consent import service as cs
    from consent import site_recognition
    from communication.database import communication_cursor
    from tests.test_fase_b_followup_postgres import _contatto
    m = v3
    _informativa_sito(m, registrata=SPUNTA - timedelta(hours=2))
    cid = _contatto(m, "spunta.altra@example.it", agenzia=2)
    stima = _q(m, "INSERT INTO stime (agency_id, comune, via, mq) VALUES (2, 'Alba', 'Via Sito', 80) RETURNING id")[0][0]
    cs.record_grant(_sistema(2), contact_id=cid, purpose="marketing", source="public_stima", actor_type="subject",
                    decided_at=SPUNTA, evidence_type="stima", evidence_ref=str(stima),
                    idempotency_key=f"public_stima:{stima}:marketing")
    with communication_cursor() as (_c, cur):
        assert site_recognition.pending_contacts(cur, agency_id=1) == []
        assert (2, cid) in site_recognition.pending_contacts(cur, agency_id=2)
    _tick(m, T0)
    assert _consensi_email(m, cid) == []
