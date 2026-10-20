"""STIMA Voice V8-3 - i nominativi reali del CRM anche con piccole imprecisioni.

  01  «Mario Rosi» trova Mario Rossi: UN candidato, da confermare, mai automatico
  02  «Fabbio» (solo il nome) trova i due Fabio: ambiguo, si sceglie
  03  il ruolo detto mette avanti chi ce l'ha («Fabio, acquirente» = Fabio Verdi)
  04  il ruolo non esclude nessuno: «Fabio, venditore» (nessuno lo e') = ancora due
  05  un nome inventato non trova nessuno e non crea nulla
  06  le regole di visibilita' restano quelle del CRM (l'agente non vede i contatti altrui)
  07  esatto resta esatto: telefono/email identificano, il nome mai
"""
from __future__ import annotations

import pytest

from tests.test_voice_2_resolver_postgres import DSN, db, plan, resolve  # noqa: F401
from tests.test_voice_4_executor_postgres import q  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL per V8-3")


@pytest.fixture(scope="module", autouse=True)
def _ruoli(db):
    q(db, "INSERT INTO contact_roles (contact_id, role) VALUES (%s, 'buyer') ON CONFLICT DO NOTHING",
      (db["ids"]["fabio_verdi"],))
    q(db, "INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s, %s, 'owner') ON CONFLICT DO NOTHING",
      (db["ids"]["roma10"], db["ids"]["mario"]))
    yield


def appt(contact, quote="Domani alle 15 telefonata"):
    return {"intent": "create_appointment", "quote": quote, "appointment_type": "call",
            "when": {"date_text": "domani", "time_text": "alle 15"}, "contact": contact}


def test_01_rosi_trova_rossi_da_confermare(db):
    from voice.policy import decide
    p = plan(appt({"first_name": "Mario", "last_name": "Rosi"}))
    r = resolve(db, p, "agent_a1")
    m = r.matches[(1, "contact")]
    assert [c.id for c in m.candidates] == [db["ids"]["mario"]] and not m.exact
    d = decide(p, r.facts).of(1)
    assert d.decision == "ask" and d.reasons == ("contact_confirmation_required",)


def test_02_solo_il_nome_impreciso_e_ambiguo(db):
    from voice.policy import decide
    p = plan(appt({"first_name": "Fabbio"}))
    r = resolve(db, p, "owner_a")
    assert {c.id for c in r.matches[(1, "contact")].candidates} == {db["ids"]["fabio_verdi"], db["ids"]["fabio_bianchi"]}
    assert decide(p, r.facts).of(1).reasons == ("reference_ambiguous",)


def test_03_il_ruolo_mette_avanti_chi_ce_l_ha(db):
    p = plan(appt({"first_name": "Fabio", "role": "buyer"}))
    r = resolve(db, p, "owner_a")
    m = r.matches[(1, "contact")]
    assert [c.id for c in m.candidates] == [db["ids"]["fabio_verdi"]] and not m.exact
    # il proprietario di un immobile conta come venditore/proprietario
    p = plan(appt({"last_name": "Rossi", "role": "seller"}))
    r = resolve(db, p, "owner_a")
    assert db["ids"]["mario"] in {c.id for c in r.matches[(1, "contact")].candidates}


def test_04_il_ruolo_non_esclude_nessuno(db):
    p = plan(appt({"first_name": "Fabio", "role": "seller"}))
    r = resolve(db, p, "owner_a")
    assert {c.id for c in r.matches[(1, "contact")].candidates} == {db["ids"]["fabio_verdi"], db["ids"]["fabio_bianchi"]}


def test_05_un_nome_inventato_non_trova_nessuno(db):
    from voice.policy import decide
    p = plan(appt({"first_name": "Zxqv", "last_name": "Wplk"}))
    r = resolve(db, p, "owner_a")
    assert r.facts.refs[(1, "contact")].candidates == 0
    assert decide(p, r.facts).of(1).reasons == ("reference_not_found",)
    assert q(db, "SELECT count(*) FROM contacts WHERE first_name = 'Zxqv'")[0][0] == 0


def test_06_la_visibilita_resta_quella_del_crm(db):
    # Fabio Verdi e' dell'agente a2, Fabio Bianchi senza agente: l'agente a1 non li vede, nemmeno «quasi»
    p = plan(appt({"first_name": "Fabbio"}))
    r = resolve(db, p, "agent_a1")
    assert r.facts.refs[(1, "contact")].candidates == 0
    r = resolve(db, p, "agent_a2")
    assert [c.id for c in r.matches[(1, "contact")].candidates] == [db["ids"]["fabio_verdi"]]
    # un'altra agenzia non vede mai Mario, neppure con il cognome preciso
    r = resolve(db, plan(appt({"first_name": "Mario", "last_name": "Rossi"})), "owner_b")
    assert r.facts.refs[(1, "contact")].candidates == 0


def test_07_telefono_identifica_il_nome_mai(db):
    r = resolve(db, plan(appt({"first_name": "Marjo", "phone": "333 1111111"})), "agent_a1")
    assert r.facts.refs[(1, "contact")].exact and r.resolved(1, "contact") == db["ids"]["mario"]
    r = resolve(db, plan(appt({"first_name": "Mario", "last_name": "Rossi"})), "agent_a1")
    assert not r.facts.refs[(1, "contact")].exact
