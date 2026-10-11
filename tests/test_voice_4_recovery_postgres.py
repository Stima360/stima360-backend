"""STIMA Voice Fase 4 - crash, ripresa, riconciliazione, D-C, su PostgreSQL vero.

Opt-in `P29_TEST_DSN`, SOLO database locale usa-e-getta.

Cosa prova:
  * crash dopo OGNI passo e fra il commit del CRM e il checkpoint del
    registro, per ogni classe di passo:
      - con chiave nativa (unita', edificio, appuntamento): si riprende con la
        STESSA chiave, nessun doppione;
      - «Vende»: si riprende sulla coppia, e l'origine resta quella vera
        (stato precedente congelato prima della prima scrittura);
      - nota e task su contatto (D-C): scrittura CRM e checkpoint nella STESSA
        transazione - o entrambi o nessuno - provato con un crash prima e
        con una connessione persa dopo il commit;
      - contatto, collegamento proprietario, nota su immobile: nessuna chiave,
        il passo diventa `indeterminate` e NON si ripete mai da solo;
  * ripresa da `partial`, nessun retry cieco di un passo fallito;
  * riconciliazione: analisi in sola lettura con tre esiti (presente,
    assente, non dimostrabile) e decisione umana per sbloccare.
"""
from __future__ import annotations

import pytest

from tests.test_voice_2_resolver_postgres import DSN, ctx, db  # noqa: F401  (fixture condivisa)
from tests.test_voice_4_executor_postgres import (  # noqa: F401
    _voce_accesa, comando, conferma_tutto, conta, esegui, esito, passi, q,
)

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare STIMA Voice Fase 4")


class Crash(BaseException):
    """La morte del processo: non e' un'eccezione che il codice gestisce."""


def _crash_dopo(monkeypatch, modulo, nome):
    """Il servizio scrive e committa, poi il processo muore prima del
    checkpoint del registro."""
    originale = getattr(modulo, nome)

    def servizio(*args, **kwargs):
        originale(*args, **kwargs)
        raise Crash()
    monkeypatch.setattr(modulo, nome, servizio)


def _vendita_completa(c, chi, nome, tel, via):
    testo = f"{nome} {tel} vuole vendere il trilocale di {via}"
    return comando(c, chi,
                   {"intent": "create_contact", "quote": f"{nome} {tel}", "first_name": nome.split()[0],
                    "last_name": nome.split()[1], "phone": tel},
                   {"intent": "create_unit", "quote": f"trilocale di {via}", "rooms": 3, "address": via,
                    "civic_number": "1", "owner": {"step": 1}},
                   {"intent": "activate_seller", "quote": "vuole vendere", "contact": {"step": 1},
                    "property": {"step": 2}},
                   transcript=testo)


# ---------------------------------------------------------------------------
# Crash dopo ogni passo di un comando composto
# ---------------------------------------------------------------------------

def test_x1_crash_after_the_unit_resumes_with_the_same_key(db, monkeypatch):
    from property import census
    cid = _vendita_completa(db, "agent_a1", "Ugo Belli", "333 4040401", "Via Uno")
    conferma_tutto(db, "agent_a1", cid)
    _crash_dopo(monkeypatch, census, "create_unit")
    with pytest.raises(Crash):
        esegui(db, "agent_a1", cid)
    monkeypatch.undo()
    assert passi(db, "agent_a1", cid)[2]["state"] == "running"
    report = esegui(db, "agent_a1", cid)
    assert report["status"] == "completed", report
    assert q(db, "SELECT count(*) FROM properties WHERE address = 'Via Uno'")[0][0] == 1
    assert q(db, "SELECT count(*) FROM contacts WHERE phone_normalized = '393334040401'")[0][0] == 1


def test_x2_crash_after_the_contact_makes_it_indeterminate_never_repeated(db, monkeypatch):
    from core import service as core_service
    cid = _vendita_completa(db, "agent_a1", "Ivo Ricci", "333 4040402", "Via Due")
    conferma_tutto(db, "agent_a1", cid)
    _crash_dopo(monkeypatch, core_service, "create_contact")
    with pytest.raises(Crash):
        esegui(db, "agent_a1", cid)
    monkeypatch.undo()
    for _ in range(3):
        report = esegui(db, "agent_a1", cid)
    assert esito(report, 1)["state"] == "indeterminate"
    assert esito(report, 2)["outcome"] == "waiting_dependency"
    assert report["status"] == "partial"
    assert q(db, "SELECT count(*) FROM contacts WHERE phone_normalized = '393334040402'")[0][0] == 1
    assert q(db, "SELECT count(*) FROM properties WHERE address = 'Via Due'")[0][0] == 0


def test_x3_crash_after_the_owner_link_is_indeterminate(db, monkeypatch):
    from property import service as property_service
    cid = _vendita_completa(db, "agent_a1", "Lia Moro", "333 4040403", "Via Tre")
    conferma_tutto(db, "agent_a1", cid)
    _crash_dopo(monkeypatch, property_service, "add_contact")
    with pytest.raises(Crash):
        esegui(db, "agent_a1", cid)
    monkeypatch.undo()
    report = esegui(db, "agent_a1", cid)
    p = passi(db, "agent_a1", cid)
    assert p[3]["intent"] == "link_owner" and p[3]["state"] == "indeterminate"
    assert esito(report, 4)["outcome"] == "waiting_dependency"
    assert q(db, "SELECT count(*) FROM property_contacts pc JOIN properties p ON p.id = pc.property_id "
                 "WHERE p.address = 'Via Tre'")[0][0] == 1


def test_x4_crash_after_seller_keeps_the_true_origin(db, monkeypatch):
    """«Vende» non ha chiave: si ripete sulla coppia, che restituisce il lead
    gia' aperto come `reused`. Lo stato precedente, congelato prima della
    prima scrittura, dice che l'ha creato QUESTO comando."""
    from crm import sellers
    cid = _vendita_completa(db, "agent_a1", "Teo Sala", "333 4040404", "Via Quattro")
    conferma_tutto(db, "agent_a1", cid)
    _crash_dopo(monkeypatch, sellers, "activate")
    with pytest.raises(Crash):
        esegui(db, "agent_a1", cid)
    monkeypatch.undo()
    report = esegui(db, "agent_a1", cid)
    assert report["status"] == "completed"
    assert esito(report, 4)["result"]["origin"] == "created"
    assert q(db, "SELECT count(*) FROM leads l JOIN contacts c ON c.id = l.contact_id "
                 "WHERE c.phone_normalized = '393334040404'")[0][0] == 1


@pytest.mark.parametrize("intento,modulo,nome,tabella", [
    ("building", "property.census", "create_building", "buildings"),
    ("appointment", "appointments.service", "create_appointment_idempotent", "appointments"),
])
def test_x5_keyed_intents_resume_without_duplicates(db, monkeypatch, intento, modulo, nome, tabella):
    import importlib
    comandi = {
        "building": {"intent": "create_building", "quote": "palazzina in via Cinque 5 a Tortoreto",
                     "address": "Via Cinque", "civic_number": "5", "city": "Tortoreto"},
        "appointment": {"intent": "create_appointment", "quote": "martedi alle 17 telefonata con Mario",
                        "when": {"date_text": "giovedì", "time_text": "alle 17"}, "contact": {"phone": "333 1111111"}},
    }
    cid = comando(db, "agent_a1", comandi[intento], transcript=comandi[intento]["quote"] + " (censisci)")
    _crash_dopo(monkeypatch, importlib.import_module(modulo), nome)
    with pytest.raises(Crash):
        esegui(db, "agent_a1", cid)
    monkeypatch.undo()
    prima = conta(db)[tabella]
    for _ in range(3):
        report = esegui(db, "agent_a1", cid)
    assert report["status"] == "completed"
    assert conta(db)[tabella] == prima


# ---------------------------------------------------------------------------
# D-C: nota e task su contatto nella STESSA transazione del checkpoint
# ---------------------------------------------------------------------------

def _nota(c, testo="Cucina da rifare"):
    return comando(c, "agent_a1", {"intent": "add_note", "quote": "nota", "text": testo,
                                   "contact": {"phone": "333 1111111"}})


def test_d1_crash_inside_the_atomic_step_leaves_nothing_and_resumes_once(db, monkeypatch):
    """Il crash arriva DOPO l'INSERT dell'attivita' e PRIMA del commit: il
    rollback toglie sia l'attivita' sia il checkpoint. Alla ripresa il
    passo riparte (classe `replayable`) e scrive UNA attivita'."""
    from core import repository as core_repository
    cid = _nota(db, "Atomico uno")
    originale = core_repository.create_activity_with_cursor

    def a_meta(cur, data, *, ctx=None):
        originale(cur, data, ctx=ctx)
        raise Crash()
    monkeypatch.setattr(core_repository, "create_activity_with_cursor", a_meta)
    with pytest.raises(Crash):
        esegui(db, "agent_a1", cid)
    monkeypatch.undo()
    assert q(db, "SELECT count(*) FROM activities WHERE description = 'Atomico uno'")[0][0] == 0
    p = passi(db, "agent_a1", cid)
    assert p[1]["state"] == "running" and p[1]["step_class"] == "replayable"
    report = esegui(db, "agent_a1", cid)
    assert report["status"] == "completed"
    assert q(db, "SELECT count(*) FROM activities WHERE description = 'Atomico uno'")[0][0] == 1


def test_d2_lost_connection_after_commit_is_read_back_not_retried(db, monkeypatch):
    """La connessione cade DOPO il commit: l'esito si rilegge dal registro
    (il passo e' `succeeded` nella stessa transazione dell'attivita'), nessun
    secondo INSERT."""
    import psycopg2
    from voice import ledger
    cid = _nota(db, "Atomico due")
    originale = ledger._finish_with_cursor

    def poi_cade(cur, *args, **kwargs):
        esito_ = originale(cur, *args, **kwargs)
        cur.connection.commit()
        raise psycopg2.OperationalError("server closed the connection unexpectedly")
    monkeypatch.setattr(ledger, "_finish_with_cursor", poi_cade)
    report = esegui(db, "agent_a1", cid)
    monkeypatch.undo()
    assert esito(report, 1)["state"] == "succeeded"
    for _ in range(3):
        esegui(db, "agent_a1", cid)
    assert q(db, "SELECT count(*) FROM activities WHERE description = 'Atomico due'")[0][0] == 1


def test_d3_task_is_atomic_too_and_carries_its_provenance(db, monkeypatch):
    from core import repository as core_repository
    cid = comando(db, "agent_a1", {"intent": "add_task", "quote": "ricordami", "title": "Atomico tre",
                                   "contact": {"phone": "333 1111111"}})
    originale = core_repository.create_task_with_cursor

    def a_meta(cur, data, **kwargs):
        originale(cur, data, **kwargs)
        raise Crash()
    monkeypatch.setattr(core_repository, "create_task_with_cursor", a_meta)
    with pytest.raises(Crash):
        esegui(db, "agent_a1", cid)
    monkeypatch.undo()
    assert q(db, "SELECT count(*) FROM tasks WHERE title = 'Atomico tre'")[0][0] == 0
    esegui(db, "agent_a1", cid)
    righe = q(db, "SELECT metadata, created_by_user_id FROM tasks WHERE title = 'Atomico tre'")
    assert len(righe) == 1 and righe[0][1] == db["ids"]["agent_a1"]
    assert righe[0][0]["voice"]["command_id"] == cid and righe[0][0]["voice"]["step"] == 1


def test_d4_note_on_a_property_is_not_atomic_and_becomes_indeterminate(db, monkeypatch):
    """La nota su un immobile passa da `create_interaction` (transazione
    propria): niente atomicita' provata, quindi at_most_once."""
    from property import interactions
    cid = comando(db, "agent_a1", {"intent": "add_note", "quote": "nota su VR10", "text": "Citofono rotto",
                                   "property": {"code": "VR10"}})
    assert passi(db, "agent_a1", cid)[1]["step_class"] == "at_most_once"
    _crash_dopo(monkeypatch, interactions, "create_interaction")
    with pytest.raises(Crash):
        esegui(db, "agent_a1", cid)
    monkeypatch.undo()
    esegui(db, "agent_a1", cid)
    assert passi(db, "agent_a1", cid)[1]["state"] == "indeterminate"
    assert q(db, "SELECT count(*) FROM activities WHERE description = 'Citofono rotto'")[0][0] == 1


# ---------------------------------------------------------------------------
# Ripresa da partial e nessun retry cieco
# ---------------------------------------------------------------------------

def test_y1_failed_step_is_never_retried_blindly(db, monkeypatch):
    from property import census
    cid = comando(db, "agent_a1", {"intent": "create_building", "quote": "palazzina via Sei 6 Tortoreto",
                                   "address": "Via Sei", "civic_number": "6", "city": "Tortoreto"},
                  transcript="censisci la palazzina di via Sei 6 a Tortoreto")

    def guasto(ctx_, body):
        raise census.CensusConflict("Riprova", "RETRY_EXHAUSTED")
    monkeypatch.setattr(census, "create_building", guasto)
    esegui(db, "agent_a1", cid)
    monkeypatch.undo()
    for _ in range(3):
        report = esegui(db, "agent_a1", cid)
    assert esito(report, 1)["state"] == "failed" and esito(report, 1)["error"] == "RETRY_EXHAUSTED"
    assert q(db, "SELECT count(*) FROM buildings WHERE address = 'Via Sei'")[0][0] == 0
    from voice import executor
    executor.retry(ctx(db, "agent_a1"), cid, 1)
    assert esegui(db, "agent_a1", cid)["status"] == "completed"
    assert q(db, "SELECT count(*) FROM buildings WHERE address = 'Via Sei'")[0][0] == 1


def test_y2_partial_resumes_from_the_first_unfinished_step(db, monkeypatch):
    from crm import sellers
    cid = _vendita_completa(db, "agent_a1", "Gaia Pini", "333 4040405", "Via Sette")
    conferma_tutto(db, "agent_a1", cid)

    def no(ctx_, body):
        raise sellers.SellerError("non ora", "SELLER_TEST_REFUSED")
    monkeypatch.setattr(sellers, "activate", no)
    assert esegui(db, "agent_a1", cid)["status"] == "partial"
    monkeypatch.undo()
    from voice import executor
    executor.retry(ctx(db, "agent_a1"), cid, 4)
    prima = conta(db)
    report = esegui(db, "agent_a1", cid)
    assert report["status"] == "completed"
    dopo = conta(db)
    assert dopo["contacts"] == prima["contacts"] and dopo["properties"] == prima["properties"]
    assert dopo["leads"] == prima["leads"] + 1


# ---------------------------------------------------------------------------
# Riconciliazione (D-G)
# ---------------------------------------------------------------------------

def test_z1_indeterminate_contact_is_not_provable_and_needs_a_human(db, monkeypatch):
    from core import service as core_service
    from voice import reconcile
    cid = comando(db, "agent_a1", {"intent": "create_contact", "quote": "nuovo", "first_name": "Rita",
                                   "last_name": "Fumo", "phone": "333 5050501"},
                  transcript="Nuovo contatto Rita Fumo 333 5050501")
    _crash_dopo(monkeypatch, core_service, "create_contact")
    with pytest.raises(Crash):
        esegui(db, "agent_a1", cid)
    monkeypatch.undo()
    esegui(db, "agent_a1", cid)
    analisi = reconcile.inspect(ctx(db, "agent_a1"), cid, 1)
    assert analisi["outcome"] == "not_provable"            # campi simili NON provano l'attribuzione
    creato = q(db, "SELECT id FROM contacts WHERE phone_normalized = '393335050501'")[0][0]
    assert [x["id"] for x in analisi["candidates"]] == [creato]
    assert passi(db, "agent_a1", cid)[1]["state"] == "indeterminate"      # l'analisi non scrive
    with pytest.raises(reconcile.ReconcileRefused):
        reconcile.decide(ctx(db, "agent_a1"), cid, 1, outcome="present", record_id=db["ids"]["mario"])
    with pytest.raises(Exception):
        reconcile.decide(ctx(db, "agent_a2"), cid, 1, outcome="present", record_id=creato)
    reconcile.decide(ctx(db, "agent_a1"), cid, 1, outcome="present", record_id=creato)
    p = passi(db, "agent_a1", cid)
    assert p[1]["state"] == "succeeded" and p[1]["result"]["id"] == creato
    assert p[1]["result"]["reconciled"] == "human"


def test_z2_indeterminate_link_is_present_when_the_link_exists(db, monkeypatch):
    from property import service as property_service
    from voice import reconcile
    cid = comando(db, "owner_a", {"intent": "link_owner", "quote": "Fabio Bianchi proprietario di VR12",
                                  "contact": {"phone": "333 3333333"}, "property": {"code": "VR12"}})
    conferma_tutto(db, "owner_a", cid)
    _crash_dopo(monkeypatch, property_service, "add_contact")
    with pytest.raises(Crash):
        esegui(db, "owner_a", cid)
    monkeypatch.undo()
    esegui(db, "owner_a", cid)
    assert reconcile.inspect(ctx(db, "owner_a"), cid, 1)["outcome"] == "present"


def test_z3_absence_is_never_proof_human_skip_is_final(db, monkeypatch):
    """Il servizio muore PRIMA di scrivere ma dopo l'inizio del passo: nulla
    nel CRM. L'analisi dice «non dimostrabile», non «assente». La decisione
    umana «assente» chiude il passo come saltato: niente ripetizione."""
    from core import service as core_service
    from voice import reconcile
    cid = comando(db, "agent_a1", {"intent": "create_contact", "quote": "nuovo", "first_name": "Ezio",
                                   "last_name": "Prati", "phone": "333 5050502"},
                  transcript="Nuovo contatto Ezio Prati 333 5050502")

    def muore(*a, **k):
        raise Crash()
    monkeypatch.setattr(core_service, "create_contact", muore)
    with pytest.raises(Crash):
        esegui(db, "agent_a1", cid)
    monkeypatch.undo()
    esegui(db, "agent_a1", cid)
    analisi = reconcile.inspect(ctx(db, "agent_a1"), cid, 1)
    assert analisi["outcome"] == "not_provable" and analisi["candidates"] == []
    reconcile.decide(ctx(db, "agent_a1"), cid, 1, outcome="absent")
    assert passi(db, "agent_a1", cid)[1]["state"] == "skipped"
    for _ in range(3):
        esegui(db, "agent_a1", cid)
    assert q(db, "SELECT count(*) FROM contacts WHERE phone_normalized = '393335050502'")[0][0] == 0


def test_z4_reconcile_refuses_steps_that_are_not_indeterminate(db):
    from voice import reconcile
    cid = _nota(db, "Riconcilia no")
    esegui(db, "agent_a1", cid)
    with pytest.raises(reconcile.ReconcileRefused):
        reconcile.inspect(ctx(db, "agent_a1"), cid, 1)
