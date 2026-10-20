"""STIMA Voice V8-2 - la VISITA IMMOBILE a voce esige un incarico, su PostgreSQL.

  01  l'immobile detto per una visita si cerca fra gli incarichi utilizzabili
  02  immobile trovato ma senza incarico: blocco spiegato, nessuna scrittura
  03  via senza civico: restano solo gli immobili con incarico
  04  senza immobile: la domanda «Quale immobile deve visitare ...?»
  05  dalla conferma all'Agenda: `buyer_visit`, `source = crm_manual`,
      incarico, cliente e agente; stessa riga che il dialog manuale scrive
  06  nessun percorso alternativo: l'operazione della voce passa dal service
      dell'Agenda, che rifiuta un immobile senza incarico (e il trigger 107 c'e')
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.test_voice_2_resolver_postgres import DSN, ctx, db, plan, resolve  # noqa: F401
from tests.test_voice_4_executor_postgres import (  # noqa: F401
    abilita, comando, conferma_tutto, esegui, esito, passi, q,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL per V8-2")

NOW = datetime.fromisoformat("2026-10-10T16:00:00+02:00")


@pytest.fixture(scope="module", autouse=True)
def _incarichi(db):
    abilita(db, 1)
    # roma10: incarico REALE nato da un'acquisizione (la sola origine che il
    # trigger CRM-OPS-3 ammette per un incarico nuovo); roma12: nessun incarico
    pid, mario, agente = db["ids"]["roma10"], db["ids"]["mario"], db["ids"]["agent_a1"]
    q(db, "INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s, %s, 'owner') ON CONFLICT DO NOTHING",
      (pid, mario))
    incontro = q(db, "INSERT INTO appointments (agency_id, assigned_user_id, appointment_type, status, start_at, end_at, "
                     "property_id, source) VALUES (1, %s, 'seller_meeting', 'scheduled', NOW() + interval '40 days', "
                     "NOW() + interval '40 days' + interval '1 hour', %s, 'crm_manual') RETURNING id", (agente, pid))[0][0]
    acq = q(db, "INSERT INTO acquisitions (agency_id, property_id, owner_contact_id, assigned_agent_id, appointment_id, "
                "created_by_user_id) VALUES (1, %s, %s, %s, %s, %s) RETURNING id", (pid, mario, agente, incontro, agente))[0][0]
    q(db, "UPDATE properties SET acquisition_id = %s, mandate_type = 'Esclusiva', mandate_start = CURRENT_DATE - 30, "
          "mandate_end = CURRENT_DATE + 180, commercial_status = 'mandate' WHERE id = %s", (acq, pid))
    yield


def visita(prop=None, contact=None, quote="Mario Rossi vuole vedere casa in via Roma 10 domani alle 15"):
    c = {"intent": "create_appointment", "quote": quote, "when": {"date_text": "domani", "time_text": "alle 15"}}
    if contact is not None:
        c["contact"] = contact
    if prop is not None:
        c["property"] = prop
    return c


MARIO = {"first_name": "Mario", "last_name": "Rossi"}


def test_01_la_visita_cerca_fra_gli_incarichi(db):
    from voice.policy import decide
    p = plan(visita({"address": "via Roma", "civic_number": "10"}, MARIO))
    assert p.step(1).payload["appointment_type"] == "buyer_visit"
    r = resolve(db, p, "agent_a1")
    assert r.resolved(1, "property") == db["ids"]["roma10"] and r.facts.refs[(1, "property")].exact
    assert 1 not in r.facts.visit_no_mandate
    d = decide(p, r.facts).of(1)
    assert d.decision == "ask" and d.reasons == ("contact_confirmation_required",)
    assert "VR10" not in r.matches[(1, "property")].candidates[0].label     # senza codice


def test_02_immobile_senza_incarico_blocca(db):
    from voice.policy import VISIT_NO_MANDATE, decide
    p = plan(visita({"address": "via Roma", "civic_number": "12"}, MARIO))
    r = resolve(db, p, "agent_a1")
    assert r.facts.refs[(1, "property")].candidates == 0 and 1 in r.facts.visit_no_mandate
    d = decide(p, r.facts).of(1)
    assert d.decision == "blocked" and VISIT_NO_MANDATE in d.reasons
    # un sopralluogo sullo stesso immobile resta possibile: la regola e' della visita
    p = plan({**visita({"address": "via Roma", "civic_number": "12"}, MARIO), "appointment_type": "inspection",
              "quote": "sopralluogo in via Roma 12"})
    r = resolve(db, p, "agent_a1")
    assert r.resolved(1, "property") == db["ids"]["roma12"] and 1 not in r.facts.visit_no_mandate


def test_03_via_senza_civico_restano_gli_incarichi(db):
    p = plan(visita({"address": "Via Roma"}, MARIO))
    r = resolve(db, p, "agent_a1")
    assert [c.id for c in r.matches[(1, "property")].candidates] == [db["ids"]["roma10"]]
    assert not r.facts.refs[(1, "property")].exact


def test_04_senza_immobile_si_chiede_l_incarico(db):
    from voice import service
    from voice.planner import VISIT_CLIENT_REQUIRED, VISIT_MANDATE_REQUIRED
    from voice.policy import decide
    p = plan(visita(None, MARIO, quote="visita con Mario Rossi domani alle 15"))
    assert p.step(1).has(VISIT_MANDATE_REQUIRED)
    assert [i.message for i in p.step(1).issues if i.code == VISIT_MANDATE_REQUIRED] == ["Quale immobile deve visitare Mario Rossi?"]
    r = resolve(db, p, "agent_a1")
    d = decide(p, r.facts).of(1)
    assert d.decision == "ask" and VISIT_MANDATE_REQUIRED in d.reasons
    assert VISIT_MANDATE_REQUIRED in service.MESSAGES and VISIT_CLIENT_REQUIRED in service.MESSAGES
    p = plan(visita({"address": "via Roma", "civic_number": "10"}, None, quote="visita in via Roma 10 domani alle 15"))
    assert p.step(1).has(VISIT_CLIENT_REQUIRED)


def test_05_dalla_conferma_all_agenda(db):
    cid = comando(db, "agent_a1", visita({"address": "via Roma", "civic_number": "10"}, MARIO),
                  transcript="Mario Rossi vuole vedere casa in via Roma 10 domani alle 15")
    p = passi(db, "agent_a1", cid)
    assert p[1]["decision"] == "ask" and p[1]["reasons"] == ["contact_confirmation_required"]
    conferma_tutto(db, "agent_a1", cid)
    report = esegui(db, "agent_a1", cid)
    assert report["status"] == "completed", report
    aid = esito(report, 1)["result"]["id"]
    riga = q(db, "SELECT appointment_type, source, property_id, contact_id, assigned_user_id, start_at, end_at "
                 "FROM appointments WHERE id = %s", (aid,))[0]
    assert riga[0] == "buyer_visit" and riga[1] == "crm_manual"
    assert riga[2] == db["ids"]["roma10"] and riga[3] == db["ids"]["mario"] and riga[4] == db["ids"]["agent_a1"]
    assert riga[5] == datetime.fromisoformat("2026-10-11T15:00:00+02:00") and riga[6] - riga[5] == timedelta(minutes=60)
    # A31-2: la proiezione della visita acquirente e' quella del dialog manuale
    assert q(db, "SELECT count(*) FROM property_visits WHERE appointment_id = %s", (aid,))[0][0] == 1


def test_06_nessun_percorso_alternativo(db):
    from appointments import errors
    from voice import operations
    from voice.operations import StepInput
    assert q(db, "SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_appointments_visit_links_guard'")[0][0] == 1
    inizio = NOW + timedelta(days=2)
    inp = StepInput(command_id=0, ordinal=1, intent="create_appointment", key="0f3c1d2e-4b5a-4c6d-8e9f-0a1b2c3d4e5f",
                    payload={"appointment_type": "buyer_visit", "start_at": inizio.isoformat(),
                             "end_at": (inizio + timedelta(hours=1)).isoformat()},
                    ids={"contact": db["ids"]["mario"], "property": db["ids"]["roma12"]}, now=NOW)
    prima = q(db, "SELECT count(*) FROM appointments")[0][0]
    with pytest.raises(errors.SaleMandateRequired):
        operations.run(ctx(db, "agent_a1"), inp)
    assert q(db, "SELECT count(*) FROM appointments")[0][0] == prima
    inp = StepInput(command_id=0, ordinal=1, intent="create_appointment", key="1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
                    payload={"appointment_type": "buyer_visit", "start_at": inizio.isoformat(),
                             "end_at": (inizio + timedelta(hours=1)).isoformat()},
                    ids={"contact": db["ids"]["mario"]}, now=NOW)
    with pytest.raises(errors.SaleMandateRequired):
        operations.run(ctx(db, "agent_a1"), inp)
