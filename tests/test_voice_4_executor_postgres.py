"""STIMA Voice Fase 4 - l'esecutore deterministico su PostgreSQL vero.

Schema completo del CRM (fixture di CENSIMENTO-1 via Fase 2), due agenzie,
titolare, amministratore, due agenti. Opt-in `P29_TEST_DSN`, SOLO database
locale usa-e-getta. STIMA Voice si accende SOLO qui, nel database di prova.

Cosa prova:
  * i tre scenari della roadmap (A Fabio vende, B palazzina con 12
    appartamenti, C appuntamento con Fabio), con domande, conferme ed
    esecuzione attraverso i servizi del CRM;
  * dieci ripetizioni dello stesso comando = una sola esecuzione;
  * due esecutori concorrenti = una sola esecuzione;
  * la conferma vale per la situazione confermata: se i dati cambiano, si
    richiede di nuovo; i casi non autorizzati restano bloccati;
  * D-D (piu' restrittivo del CRM), D-E (platform admin bloccato), D-F (un
    solo collegamento proprietario esplicito);
  * SIMILAR_FOUND, CADASTRAL_DUPLICATE, conflitti d'Agenda, appuntamenti nel
    passato, doppioni fra agenti, isolamento fra agenzie.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta

import pytest

from tests.test_voice_2_resolver_postgres import DSN, ctx, db  # noqa: F401  (fixture condivisa)

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare STIMA Voice Fase 4")

NOW = datetime.fromisoformat("2026-10-10T16:00:00+02:00")
DOPO = NOW + timedelta(minutes=5)

CRM_TABLES = ("contacts", "properties", "property_contacts", "buildings", "appointments", "activities",
              "tasks", "leads", "property_leads")


# ---------------------------------------------------------------------------
# strumenti comuni (riusati dagli altri due file della Fase 4)
# ---------------------------------------------------------------------------

def q(c, sql, params=None):
    with c["conn"].cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def abilita(c, agenzia=1, mode="auto"):
    q(c, "INSERT INTO voice_agency_settings (agency_id, enabled, mode) VALUES (%s, TRUE, %s) "
         "ON CONFLICT (agency_id) DO UPDATE SET enabled = TRUE, mode = EXCLUDED.mode", (agenzia, mode))


def conta(c) -> dict:
    return {t: q(c, f"SELECT count(*) FROM {t}")[0][0] for t in CRM_TABLES}


def piano(*commands, transcript="vocale di prova", recorded_at=NOW):
    from voice.planner import build_plan
    from voice.schemas import parse_plan_output
    return build_plan(parse_plan_output({"commands": list(commands)}), transcript, recorded_at)


def comando(c, chi, *commands, transcript="vocale di prova", chiave=None, recorded_at=NOW, **kw):
    """Apre il comando nel registro e lo prepara con l'esecutore (risoluzione,
    politica, guardie della Fase 4, registrazione del piano)."""
    from voice import executor, ledger
    contesto = ctx(c, chi, **kw)
    riga, _ = ledger.open_command(contesto, chiave or uuid.uuid4(), input_kind="text",
                                  request_sha256=ledger.request_fingerprint("text", transcript),
                                  recorded_at=recorded_at)
    executor.prepare(contesto, riga["id"], piano(*commands, transcript=transcript, recorded_at=recorded_at))
    return riga["id"]


def esegui(c, chi, cid, clock=DOPO, **kw):
    from voice import executor
    return executor.execute(ctx(c, chi, **kw), cid, clock=lambda: clock)


def passi(c, chi, cid, **kw):
    from voice import ledger
    return {p["ordinal"]: p for p in ledger.get_command(ctx(c, chi, **kw), cid)["steps"]}


def esito(report, ordinal):
    return next(s for s in report["steps"] if s["ordinal"] == ordinal)


def conferma_tutto(c, chi, cid, choices=None, **kw):
    from voice import executor
    return executor.confirm_all(ctx(c, chi, **kw), cid, choices=choices)


@pytest.fixture(scope="module", autouse=True)
def _voce_accesa(db):
    abilita(db, 1)
    abilita(db, 2)
    yield


# ---------------------------------------------------------------------------
# SCENARIO A - «Fabio vuole vendere un trilocale in via Duca d'Aosta»
# ---------------------------------------------------------------------------

def _scenario_a(c, chi, *, transcript="Fabio vuole vendere un trilocale in via Duca d'Aosta"):
    return comando(c, chi,
                   {"intent": "create_unit", "quote": "un trilocale in via Duca d'Aosta", "rooms": 3,
                    "address": "Via Duca d'Aosta", "owner": {"first_name": "Fabio"}},
                   {"intent": "activate_seller", "quote": "vuole vendere", "contact": {"first_name": "Fabio"},
                    "property": {"step": 1}},
                   transcript=transcript)


def test_a1_fabio_known_asks_first_then_unit_owner_link_and_seller(db):
    """agent_a2 vede un solo Fabio (Verdi), trovato per nome: si chiede la
    conferma. D-F: il proprietario dentro l'unita' diventa UN passo
    `link_owner` esplicito, sempre da confermare. Nulla si scrive prima."""
    prima = conta(db)
    cid = _scenario_a(db, "agent_a2")
    p = passi(db, "agent_a2", cid)
    assert [p[n]["intent"] for n in (1, 2, 3)] == ["create_unit", "link_owner", "activate_seller"]
    assert all(p[n]["decision"] == "ask" for n in (1, 2, 3))
    assert "owner_link_confirmation_required" in p[2]["reasons"]
    assert "contact_confirmation_required" in p[2]["reasons"]
    report = esegui(db, "agent_a2", cid)
    assert all(esito(report, n)["outcome"] == "needs_confirmation" for n in (1, 2, 3))
    assert conta(db) == prima                                   # nessuna scrittura prima delle risposte

    conferma_tutto(db, "agent_a2", cid)
    report = esegui(db, "agent_a2", cid)
    assert report["status"] == "completed", report
    unit_id = esito(report, 1)["result"]["id"]
    fabio = db["ids"]["fabio_verdi"]
    riga = q(db, "SELECT record_kind, assigned_agent_id, city, rooms, cadastral_sheet FROM properties WHERE id = %s",
             (unit_id,))[0]
    assert riga[0] == "crm" and riga[1] == db["ids"]["agent_a2"]
    assert riga[2] is None and riga[3] == 3 and riga[4] is None          # nulla inventato
    assert q(db, "SELECT count(*) FROM property_contacts WHERE property_id = %s AND contact_id = %s AND role = 'owner'",
             (unit_id, fabio))[0][0] == 1
    assert q(db, "SELECT count(*) FROM property_leads pl JOIN leads l ON l.id = pl.lead_id "
                 "WHERE pl.property_id = %s AND pl.relation_type = 'seller' AND l.contact_id = %s AND l.status = 'open'",
             (unit_id, fabio))[0][0] == 1
    assert esito(report, 3)["result"]["origin"] == "created"
    nuovi = conta(db)
    assert nuovi["properties"] == prima["properties"] + 1 and nuovi["contacts"] == prima["contacts"]
    assert nuovi["property_contacts"] == prima["property_contacts"] + 1 and nuovi["leads"] == prima["leads"] + 1


def test_a2_fabio_unknown_asks_for_data_and_writes_nothing(db):
    """agent_a1 non vede nessun Fabio. «Creare Fabio» con il solo nome e'
    un dato mancante: la conferma NON basta, servono cognome o recapito."""
    from voice import executor
    prima = conta(db)
    cid = comando(db, "agent_a1",
                  {"intent": "create_contact", "quote": "Fabio", "first_name": "Fabio"},
                  {"intent": "create_unit", "quote": "un trilocale in via Duca d'Aosta", "rooms": 3,
                   "address": "Via Duca d'Aosta", "owner": {"step": 1}},
                  {"intent": "activate_seller", "quote": "vuole vendere", "contact": {"step": 1},
                   "property": {"step": 2}},
                  transcript="Fabio vuole vendere un trilocale in via Duca d'Aosta")
    p = passi(db, "agent_a1", cid)
    assert "contact_identity_weak" in p[1]["reasons"]
    with pytest.raises(executor.VoiceRefused) as e:
        executor.confirm(ctx(db, "agent_a1"), cid, 1)
    assert e.value.code == "VOICE_CONFIRMATION_NOT_ALLOWED"
    report = esegui(db, "agent_a1", cid)
    assert esito(report, 1)["outcome"] == "needs_answer"
    assert conta(db) == prima


def test_a3_fabio_ambiguous_needs_an_explicit_choice(db):
    """Il titolare vede due Fabio: si sceglie fra i candidati, mai a caso;
    un id fuori dai candidati e' rifiutato."""
    from voice import executor
    cid = _scenario_a(db, "owner_a")
    p = passi(db, "owner_a", cid)
    assert "reference_ambiguous" in p[2]["reasons"]
    with pytest.raises(executor.VoiceRefused) as e:
        executor.confirm(ctx(db, "owner_a"), cid, 2)
    assert e.value.code == "VOICE_CHOICE_REQUIRED"
    with pytest.raises(executor.VoiceRefused) as e:
        executor.confirm(ctx(db, "owner_a"), cid, 2, choices={"contact": db["ids"]["mario"]})
    assert e.value.code == "VOICE_CHOICE_INVALID"
    scelta = {2: {"contact": db["ids"]["fabio_bianchi"]}, 3: {"contact": db["ids"]["fabio_bianchi"]}}
    conferma_tutto(db, "owner_a", cid, choices=scelta)
    report = esegui(db, "owner_a", cid)
    assert report["status"] == "completed", report
    assert esito(report, 2)["result"]["contact_id"] == db["ids"]["fabio_bianchi"]


def test_a4_partial_failure_is_kept_and_shown(db, monkeypatch):
    """Se «Vende» fallisce, unita' e collegamento restano e il comando e'
    `partial`, con il passo fallito e il suo codice."""
    from crm import sellers
    cid = _scenario_a(db, "agent_a2", transcript="Fabio vuole vendere un trilocale in via Duca d'Aosta, bis")
    conferma_tutto(db, "agent_a2", cid)

    def rifiuta(ctx_, body):
        raise sellers.SellerError("rifiutato per prova", "SELLER_TEST_REFUSED")
    monkeypatch.setattr(sellers, "activate", rifiuta)
    report = esegui(db, "agent_a2", cid)
    assert report["status"] == "partial"
    assert esito(report, 1)["state"] == "succeeded" and esito(report, 2)["state"] == "succeeded"
    assert esito(report, 3)["state"] == "failed" and esito(report, 3)["error"] == "SELLER_TEST_REFUSED"
    monkeypatch.undo()
    report = esegui(db, "agent_a2", cid)                           # nessun retry cieco
    assert esito(report, 3)["state"] == "failed"
    from voice import executor
    executor.retry(ctx(db, "agent_a2"), cid, 3)
    report = esegui(db, "agent_a2", cid)
    assert report["status"] == "completed"


# ---------------------------------------------------------------------------
# SCENARIO B - «Censisci una palazzina con 12 appartamenti»
# ---------------------------------------------------------------------------

def test_b1_building_without_address_asks_and_writes_nothing(db):
    prima = conta(db)
    cid = comando(db, "agent_a1", {"intent": "create_building", "quote": "una palazzina con 12 appartamenti",
                                   "units_declared": 12},
                  transcript="Censisci una palazzina con 12 appartamenti")
    p = passi(db, "agent_a1", cid)
    assert p[1]["decision"] == "ask" and "building_location_missing" in p[1]["reasons"]
    report = esegui(db, "agent_a1", cid)
    assert esito(report, 1)["outcome"] == "needs_answer"
    assert conta(db) == prima


def test_b2_twelve_invented_units_are_blocked(db):
    unita = [{"intent": "create_unit", "quote": "appartamento", "record_kind": "census",
              "building": {"step": 1}} for _ in range(11)]
    cid = comando(db, "agent_a1", {"intent": "create_building", "quote": "palazzina in via Marina 3",
                                   "address": "Via Marina", "civic_number": "3", "units_declared": 12}, *unita,
                  transcript="Censisci una palazzina in via Marina 3 con 12 appartamenti")
    p = passi(db, "agent_a1", cid)
    assert all(p[n]["decision"] == "blocked" for n in range(2, 13))


def test_b3_new_building_runs_automatically_with_declared_units_only(db):
    prima = conta(db)
    cid = comando(db, "agent_a1", {"intent": "create_building", "quote": "palazzina in via Gramsci 8 a Tortoreto",
                                   "address": "Via Gramsci", "civic_number": "8", "city": "Tortoreto",
                                   "units_declared": 12},
                  transcript="Censisci una palazzina in via Gramsci 8 a Tortoreto con 12 appartamenti")
    assert passi(db, "agent_a1", cid)[1]["decision"] == "auto"
    report = esegui(db, "agent_a1", cid)
    assert report["status"] == "completed"
    bid = esito(report, 1)["result"]["id"]
    assert q(db, "SELECT units_declared FROM buildings WHERE id = %s", (bid,))[0][0] == 12
    assert q(db, "SELECT count(*) FROM properties WHERE building_id = %s", (bid,))[0][0] == 0
    dopo = conta(db)
    assert dopo["buildings"] == prima["buildings"] + 1 and dopo["properties"] == prima["properties"]


def test_b4_similar_found_needs_a_decision_never_auto_confirmed(db):
    """Via Dante 7 a Tortoreto esiste gia': SIMILAR_FOUND e' una domanda;
    solo la conferma esplicita salva comunque."""
    q(db, "INSERT INTO buildings (agency_id, building_type, name, address, civic_number, city) "
          "VALUES (1, 'condominio', 'Residenza Stella', 'Via Dante', '7', 'Tortoreto')")
    prima = conta(db)
    cid = comando(db, "agent_a1", {"intent": "create_building", "quote": "palazzina in via Dante 7 a Tortoreto",
                                   "address": "Via Dante", "civic_number": "7", "city": "Tortoreto",
                                   "units_declared": 12},
                  transcript="Censisci una palazzina in via Dante 7 a Tortoreto con 12 appartamenti")
    p = passi(db, "agent_a1", cid)
    assert p[1]["decision"] == "ask" and "similar_building_found" in p[1]["reasons"]
    esegui(db, "agent_a1", cid)
    assert conta(db) == prima
    conferma_tutto(db, "agent_a1", cid)
    report = esegui(db, "agent_a1", cid)
    assert report["status"] == "completed"
    assert conta(db)["buildings"] == prima["buildings"] + 1


def test_b5_similar_appearing_after_planning_is_reexamined(db):
    """Piano `auto` (nessun simile); prima dell'esecuzione qualcuno registra
    una palazzina allo stesso indirizzo: la situazione e' cambiata, si chiede."""
    prima = conta(db)
    cid = comando(db, "agent_a1", {"intent": "create_building", "quote": "palazzina in via Volta 2 a Tortoreto",
                                   "address": "Via Volta", "civic_number": "2", "city": "Tortoreto"},
                  transcript="Censisci la palazzina di via Volta 2 a Tortoreto")
    assert passi(db, "agent_a1", cid)[1]["decision"] == "auto"
    q(db, "INSERT INTO buildings (agency_id, building_type, address, civic_number, city) "
          "VALUES (1, 'condominio', 'Via Volta', '2', 'Tortoreto')")
    report = esegui(db, "agent_a1", cid)
    assert esito(report, 1)["outcome"] == "needs_confirmation"
    assert "similar_building_found" in esito(report, 1)["reasons"]
    assert conta(db)["buildings"] == prima["buildings"] + 1          # solo quella inserita a mano


def test_b6_similar_found_raised_by_the_service_fails_safely(db, monkeypatch):
    """Se il simile compare fra la verifica e la scrittura, il censimento lo
    rifiuta (SIMILAR_FOUND): passo fallito, nessuna scrittura, nessuna
    conferma automatica."""
    from property import census
    cid = comando(db, "agent_a1", {"intent": "create_building", "quote": "palazzina in via Fermi 1 a Tortoreto",
                                   "address": "Via Fermi", "civic_number": "1", "city": "Tortoreto"},
                  transcript="Censisci la palazzina di via Fermi 1 a Tortoreto")
    originale = census.create_building
    visti = []

    def con_concorrente(ctx_, body):
        visti.append(body.confirm_similar)
        if not visti[1:]:
            q(db, "INSERT INTO buildings (agency_id, building_type, address, civic_number, city) "
                  "VALUES (1, 'condominio', 'Via Fermi', '1', 'Tortoreto')")
        return originale(ctx_, body)
    monkeypatch.setattr(census, "create_building", con_concorrente)
    report = esegui(db, "agent_a1", cid)
    assert esito(report, 1)["state"] == "failed" and esito(report, 1)["error"] == "SIMILAR_FOUND"
    assert visti == [False]
    assert q(db, "SELECT count(*) FROM buildings WHERE address = 'Via Fermi'")[0][0] == 1


def test_b7_cadastral_duplicate_is_blocked_not_confirmable(db, monkeypatch):
    from property import census
    from voice import executor
    cid = comando(db, "agent_a1", {"intent": "create_unit", "quote": "censisci il trilocale in via Pace 4",
                                   "record_kind": "census", "address": "Via Pace", "civic_number": "4", "rooms": 3},
                  transcript="Censisci il trilocale in via Pace 4")

    def duplicato(ctx_, body):
        raise census.CensusConflict("Unita' gia' censita", "CADASTRAL_DUPLICATE", existing={"id": 1, "code": "X"})
    monkeypatch.setattr(census, "create_unit", duplicato)
    report = esegui(db, "agent_a1", cid)
    assert esito(report, 1)["state"] == "failed" and esito(report, 1)["error"] == "CADASTRAL_DUPLICATE"
    assert executor.guard_reasons_for_facts(cadastral=True) == ("blocked", ("cadastral_duplicate",))


# ---------------------------------------------------------------------------
# SCENARIO C - «Domani alle 15 appuntamento con Fabio»
# ---------------------------------------------------------------------------

def _scenario_c(c, chi, **kw):
    return comando(c, chi, {"intent": "create_appointment", "quote": "Domani alle 15 appuntamento con Fabio",
                            "when": {"date_text": "domani", "time_text": "alle 15"},
                            "contact": {"first_name": "Fabio"}},
                   transcript="Domani alle 15 appuntamento con Fabio", **kw)


def test_c1_appointment_rome_other_60_minutes_assigned_to_the_speaker(db):
    cid = _scenario_c(db, "agent_a2")
    p = passi(db, "agent_a2", cid)
    assert p[1]["decision"] == "ask" and "contact_confirmation_required" in p[1]["reasons"]
    conferma_tutto(db, "agent_a2", cid)
    report = esegui(db, "agent_a2", cid)
    assert report["status"] == "completed", report
    aid = esito(report, 1)["result"]["id"]
    riga = q(db, "SELECT appointment_type, start_at, end_at, assigned_user_id, contact_id, status, timezone "
                 "FROM appointments WHERE id = %s", (aid,))[0]
    assert riga[0] == "other"
    assert riga[1] == datetime.fromisoformat("2026-10-11T15:00:00+02:00")
    assert riga[2] - riga[1] == timedelta(minutes=60)
    assert riga[3] == db["ids"]["agent_a2"] and riga[4] == db["ids"]["fabio_verdi"]
    assert riga[5] == "scheduled" and riga[6] == "Europe/Rome"


def test_c2_agenda_conflict_is_not_forced(db):
    from appointments import service as agenda
    from appointments.schemas import AppointmentCreateBody
    occupato = AppointmentCreateBody(appointment_type="call", start_at="2026-10-11T14:30:00+02:00",
                                     end_at="2026-10-11T15:30:00+02:00", assigned_user_id=db["ids"]["agent_a1"],
                                     client_request_id=str(uuid.uuid4()))
    agenda.create_appointment_idempotent(ctx(db, "agent_a1"), occupato)
    q(db, "UPDATE contacts SET assigned_agent_id = %s WHERE id = %s", (db["ids"]["agent_a1"], db["ids"]["fabio_bianchi"]))
    try:
        prima = conta(db)
        cid = _scenario_c(db, "agent_a1")
        p = passi(db, "agent_a1", cid)
        assert "agenda_conflict" in p[1]["reasons"]
        from voice import executor
        with pytest.raises(executor.VoiceRefused):
            executor.confirm(ctx(db, "agent_a1"), cid, 1)
        esegui(db, "agent_a1", cid)
        assert conta(db) == prima
    finally:
        q(db, "UPDATE contacts SET assigned_agent_id = NULL WHERE id = %s", (db["ids"]["fabio_bianchi"],))


def test_c3_never_in_the_past_at_execution_time(db):
    prima = conta(db)
    cid = comando(db, "agent_a2", {"intent": "create_appointment", "quote": "dopodomani alle 10 telefonata con Fabio",
                                   "when": {"date_text": "dopodomani", "time_text": "alle 10"},
                                   "contact": {"phone": "333 2222222"}},
                  transcript="dopodomani alle 10 telefonata con Fabio 333 2222222")
    # SENTINELLA AGGIORNATA DA STIMA VOICE V8-0: un appuntamento si conferma sempre prima del salvataggio.
    assert passi(db, "agent_a2", cid)[1]["reasons"] == ["mode_review"]
    conferma_tutto(db, "agent_a2", cid)
    report = esegui(db, "agent_a2", cid, clock=datetime.fromisoformat("2026-10-12T10:30:00+02:00"))
    assert esito(report, 1)["state"] == "failed" and esito(report, 1)["error"] == "VOICE_APPOINTMENT_IN_PAST"
    assert conta(db) == prima


def test_c4_appointment_replay_after_crash_is_not_its_own_conflict(db, monkeypatch):
    """Crash fra il commit dell'Agenda e il checkpoint: alla ripresa la
    stessa chiave restituisce lo stesso appuntamento, non un conflitto con
    se stesso, e non ne crea un secondo."""
    from appointments import service as agenda
    cid = comando(db, "agent_a2", {"intent": "create_appointment", "quote": "lunedi alle 9 telefonata con Fabio",
                                   "when": {"date_text": "lunedì", "time_text": "alle 9"},
                                   "contact": {"phone": "333 2222222"}},
                  transcript="lunedi alle 9 telefonata con Fabio 333 2222222")
    # SENTINELLA AGGIORNATA DA STIMA VOICE V8-0: un appuntamento si conferma sempre prima del salvataggio.
    conferma_tutto(db, "agent_a2", cid)
    originale = agenda.create_appointment_idempotent

    class Crash(BaseException):
        pass

    def crash(ctx_, body):
        originale(ctx_, body)
        raise Crash()
    monkeypatch.setattr(agenda, "create_appointment_idempotent", crash)
    with pytest.raises(Crash):
        esegui(db, "agent_a2", cid)
    monkeypatch.undo()
    prima = q(db, "SELECT count(*) FROM appointments")[0][0]
    report = esegui(db, "agent_a2", cid)
    assert report["status"] == "completed"
    assert q(db, "SELECT count(*) FROM appointments")[0][0] == prima


# ---------------------------------------------------------------------------
# Ripetizioni e concorrenza
# ---------------------------------------------------------------------------

def test_r1_ten_repetitions_of_the_same_command_execute_once(db):
    from voice import ledger
    chiave = uuid.uuid4()
    testo = "Nuovo contatto Sara Greco 333 6060606"
    cid = comando(db, "agent_a1", {"intent": "create_contact", "quote": testo, "first_name": "Sara",
                                   "last_name": "Greco", "phone": "333 6060606"}, transcript=testo, chiave=chiave)
    prima = conta(db)
    for _ in range(10):
        riga, creato = ledger.open_command(ctx(db, "agent_a1"), chiave, input_kind="text",
                                           request_sha256=ledger.request_fingerprint("text", testo), recorded_at=NOW)
        assert riga["id"] == cid and creato is False
        esegui(db, "agent_a1", cid)
    dopo = conta(db)
    assert dopo["contacts"] == prima["contacts"] + 1
    assert q(db, "SELECT count(*) FROM contacts WHERE phone_normalized = %s", ("393336060606",))[0][0] == 1


@pytest.mark.parametrize("intento", ["note", "task", "building", "unit", "appointment"])
def test_r2_ten_executions_of_each_intent_write_once(db, intento):
    comandi = {
        "note": ({"intent": "add_note", "quote": "nota", "text": "Richiamare per la cucina",
                  "contact": {"phone": "333 1111111"}}, "activities"),
        "task": ({"intent": "add_task", "quote": "ricordami", "title": "Mandare la stima",
                  "contact": {"phone": "333 1111111"}}, "tasks"),
        "building": ({"intent": "create_building", "quote": "palazzina via Rossini 9 Tortoreto", "address": "Via Rossini",
                      "civic_number": "9", "city": "Tortoreto"}, "buildings"),
        "unit": ({"intent": "create_unit", "quote": "censisci il bilocale in via Puccini 4", "record_kind": "census",
                  "address": "Via Puccini", "civic_number": "4", "rooms": 2}, "properties"),
    }
    comandi["appointment"] = ({"intent": "create_appointment", "quote": "venerdi prossimo alle 16 chiamata con Mario",
                               "when": {"date_text": "venerdì prossimo", "time_text": "alle 16"},
                               "contact": {"phone": "333 1111111"}}, "appointments")
    cmd, tabella = comandi[intento]
    cid = comando(db, "agent_a1", cmd, transcript=f"{cmd['quote']} (censisci)")
    if intento == "appointment":
        # SENTINELLA AGGIORNATA DA STIMA VOICE V8-0: un appuntamento si conferma sempre prima del salvataggio.
        conferma_tutto(db, "agent_a1", cid)
    prima = conta(db)[tabella]
    for _ in range(10):
        esegui(db, "agent_a1", cid)
    assert conta(db)[tabella] == prima + 1


def test_r3_two_concurrent_executors_write_once(db):
    testo = "Nuovo contatto Piero Lodi 333 7171717"
    cid = comando(db, "agent_a1", {"intent": "create_contact", "quote": testo, "first_name": "Piero",
                                   "last_name": "Lodi", "phone": "333 7171717"}, transcript=testo)
    barriera, esiti = threading.Barrier(2), []

    def lavora():
        barriera.wait()
        try:
            esiti.append(esegui(db, "agent_a1", cid)["status"])
        except Exception as exc:  # noqa: BLE001
            esiti.append(type(exc).__name__)
    fili = [threading.Thread(target=lavora) for _ in range(2)]
    for f in fili:
        f.start()
    for f in fili:
        f.join(60)
    assert q(db, "SELECT count(*) FROM contacts WHERE phone_normalized = '393337171717'")[0][0] == 1
    assert "completed" in esiti and set(esiti) <= {"completed", "CommandBusy"}


def test_r4_duplicate_between_agents_is_a_question(db):
    """agent_a1 detta un contatto che esiste gia' per agent_a2 (stesso
    telefono): D1, un SI' senza dati, e una domanda."""
    testo = "Nuovo contatto Fabio Verdi 333 2222222"
    cid = comando(db, "agent_a1", {"intent": "create_contact", "quote": testo, "first_name": "Fabio",
                                   "last_name": "Verdi", "phone": "333 2222222"}, transcript=testo)
    p = passi(db, "agent_a1", cid)
    assert p[1]["decision"] == "ask" and "possible_duplicate_hidden" in p[1]["reasons"]
    prima = conta(db)
    esegui(db, "agent_a1", cid)
    assert conta(db) == prima


def test_r5_duplicate_appearing_during_execution_stops_the_creation(db, monkeypatch):
    """Due dettati concorrenti dello stesso contatto nuovo: il secondo, che
    trova il primo gia' scritto sotto il lock dell'identita', si ferma."""
    from voice import operations
    testo = "Nuovo contatto Nina Ferri 333 8181818"
    uno = comando(db, "agent_a1", {"intent": "create_contact", "quote": testo, "first_name": "Nina",
                                   "last_name": "Ferri", "phone": "333 8181818"}, transcript=testo)
    due = comando(db, "agent_a2", {"intent": "create_contact", "quote": testo, "first_name": "Nina",
                                   "last_name": "Ferri", "phone": "333 8181818"}, transcript=testo)
    assert esegui(db, "agent_a1", uno)["status"] == "completed"
    report = esegui(db, "agent_a2", due)
    assert esito(report, 1)["outcome"] in ("needs_confirmation", "failed")
    assert q(db, "SELECT count(*) FROM contacts WHERE phone_normalized = '393338181818'")[0][0] == 1
    assert operations.CONTACT_DUPLICATE_APPEARED == "VOICE_DUPLICATE_APPEARED"


def test_r6_identity_lock_rechecks_duplicates_before_writing(db):
    """Il ricontrollo sotto il lock dell'identita', da solo: un contatto con
    lo stesso telefono comparso DOPO la valutazione ferma la creazione, senza
    scrivere (deterministico, senza thread)."""
    from voice import operations
    prima = conta(db)
    inp = operations.StepInput(command_id=0, ordinal=1, intent="create_contact",
                               payload={"contact_type": "person", "first_name": "Mario", "last_name": "Rossi",
                                        "phone": "333 1111111", "source": "voice"}, ids={})
    with pytest.raises(operations.StepRefused) as e:
        operations.run(ctx(db, "agent_a1"), inp)
    assert e.value.code == "VOICE_DUPLICATE_APPEARED"
    nascosto = operations.StepInput(command_id=0, ordinal=1, intent="create_contact",
                                    payload={"contact_type": "person", "first_name": "Mario", "last_name": "Rossi",
                                             "phone": "333 1111111", "source": "voice"}, ids={})
    with pytest.raises(operations.StepRefused):
        operations.run(ctx(db, "agent_a2"), nascosto)          # per agent_a2 e' un doppione NASCOSTO
    assert conta(db) == prima


def test_r7_scenario_a_executed_ten_times_writes_once(db):
    cid = _scenario_a(db, "agent_a2", transcript="Fabio vuole vendere un trilocale in via Duca d'Aosta, ter")
    conferma_tutto(db, "agent_a2", cid)
    assert esegui(db, "agent_a2", cid)["status"] == "completed"
    dopo_primo = conta(db)
    for _ in range(9):
        assert esegui(db, "agent_a2", cid)["status"] == "completed"
    assert conta(db) == dopo_primo


# ---------------------------------------------------------------------------
# Permessi, D-D, D-E, isolamento
# ---------------------------------------------------------------------------

def test_p1_platform_admin_is_blocked_even_in_acting(db):
    from voice import executor, ledger
    admin = ctx(db, "owner_a", ruolo=None, platform=True)
    with pytest.raises(executor.VoiceRefused) as e:
        executor.prepare(admin, 1, piano({"intent": "add_note", "quote": "n", "text": "x",
                                          "contact": {"phone": "333 1111111"}}))
    assert e.value.code == "VOICE_PLATFORM_ADMIN_BLOCKED"
    for funzione in (executor.execute, executor.confirm_all):
        with pytest.raises(executor.VoiceRefused):
            funzione(admin, 1)
    assert ledger  # import usato


def test_p2_disabled_agency_is_refused(db):
    from voice import executor
    q(db, "UPDATE voice_agency_settings SET enabled = FALSE WHERE agency_id = 2")
    try:
        with pytest.raises(executor.VoiceRefused) as e:
            comando(db, "agent_b", {"intent": "add_note", "quote": "n", "text": "x", "contact": {"phone": "333 9999999"}})
        assert e.value.code == "VOICE_DISABLED"
    finally:
        q(db, "UPDATE voice_agency_settings SET enabled = TRUE WHERE agency_id = 2")


def test_p3_agent_cannot_link_owner_on_a_property_not_assigned_to_him(db):
    """D-D: il CRM lascerebbe collegare (`add_contact` controlla solo
    l'agenzia). La voce no: un agente collega proprietari solo sugli immobili
    che gestisce."""
    q(db, "UPDATE properties SET assigned_agent_id = %s WHERE id = %s", (db["ids"]["agent_a1"], db["ids"]["roma12"]))
    prima = conta(db)
    cid = comando(db, "agent_a2", {"intent": "link_owner", "quote": "Fabio e' il proprietario di VR12",
                                   "contact": {"phone": "333 2222222"}, "property": {"code": "VR12"}})
    p = passi(db, "agent_a2", cid)
    assert p[1]["decision"] == "blocked" and "voice_property_not_manageable" in p[1]["reasons"]
    from voice import executor
    with pytest.raises(executor.VoiceRefused):
        executor.confirm(ctx(db, "agent_a2"), cid, 1)
    esegui(db, "agent_a2", cid)
    assert conta(db) == prima


def test_p4_agent_census_unit_owner_link_is_blocked(db):
    """Un agente non gestisce una scheda di censimento (mai assegnata): il
    collegamento del proprietario su quella scheda e' bloccato."""
    cid = comando(db, "agent_a2",
                  {"intent": "create_unit", "quote": "censisci il bilocale in via Leopardi 2", "record_kind": "census",
                   "address": "Via Leopardi", "civic_number": "2", "owner": {"phone": "333 2222222"}},
                  transcript="Censisci il bilocale in via Leopardi 2, proprietario Fabio")
    p = passi(db, "agent_a2", cid)
    assert p[2]["intent"] == "link_owner" and p[2]["decision"] == "blocked"


def test_p5_owner_and_admin_link_on_any_agency_property(db):
    cid = comando(db, "owner_a", {"intent": "link_owner", "quote": "Mario e' il proprietario di VR10",
                                  "contact": {"phone": "333 1111111"}, "property": {"code": "VR10"}})
    conferma_tutto(db, "owner_a", cid)
    report = esegui(db, "owner_a", cid)
    assert report["status"] == "completed"
    assert esito(report, 1)["result"]["created"] is True
    # l'amministratore, su un immobile assegnato a un agente
    q(db, "UPDATE properties SET assigned_agent_id = %s WHERE id = %s", (db["ids"]["agent_a1"], db["ids"]["roma12"]))
    cid = comando(db, "admin_a", {"intent": "link_owner", "quote": "Fabio Bianchi e' il proprietario di VR12",
                                  "contact": {"phone": "333 3333333"}, "property": {"code": "VR12"}})
    conferma_tutto(db, "admin_a", cid)
    report = esegui(db, "admin_a", cid)
    assert report["status"] == "completed" and esito(report, 1)["result"]["created"] is True


def test_p6_link_already_present_is_not_attributed_to_the_command(db):
    cid = comando(db, "owner_a", {"intent": "link_owner", "quote": "Mario e' il proprietario di VR10",
                                  "contact": {"phone": "333 1111111"}, "property": {"code": "VR10"}},
                  transcript="di nuovo: Mario proprietario di VR10")
    conferma_tutto(db, "owner_a", cid)
    report = esegui(db, "owner_a", cid)
    assert esito(report, 1)["result"]["created"] is False and esito(report, 1)["result"]["preexisting"] is True


def test_p7_agent_cannot_assign_appointments_to_colleagues(db):
    cid = comando(db, "agent_a1", {"intent": "create_appointment", "quote": "domani alle 11 visita con Mario",
                                   "when": {"date_text": "domani", "time_text": "alle 11"},
                                   "contact": {"phone": "333 1111111"}, "agent_name": "Bruno Galli"},
                  transcript="domani alle 11 visita con Mario, la fa Bruno Galli")
    assert passi(db, "agent_a1", cid)[1]["decision"] == "blocked"


def test_p8_agencies_and_actors_are_isolated(db):
    from voice import executor, ledger
    cid = comando(db, "agent_a1", {"intent": "add_note", "quote": "n", "text": "solo mia",
                                   "contact": {"phone": "333 1111111"}})
    for altro in ("agent_a2", "owner_a", "agent_b", "owner_b"):
        with pytest.raises(ledger.CommandNotFound):
            executor.execute(ctx(db, altro), cid, clock=lambda: DOPO)
    # il Mario dell'agenzia 1 non esiste per l'agenzia 2
    cid_b = comando(db, "agent_b", {"intent": "add_note", "quote": "n", "text": "x", "contact": {"phone": "333 1111111"}})
    assert passi(db, "agent_b", cid_b)[1]["decision"] == "ask"
    assert "reference_not_found" in passi(db, "agent_b", cid_b)[1]["reasons"]


def test_p9_permission_lost_before_execution_is_reexamined(db):
    """Il contatto era dell'agente al momento del piano; prima di eseguire
    viene riassegnato a un collega: la nota non parte."""
    q(db, "UPDATE contacts SET assigned_agent_id = %s WHERE id = %s", (db["ids"]["agent_a1"], db["ids"]["fabio_bianchi"]))
    prima = conta(db)
    cid = comando(db, "agent_a1", {"intent": "add_note", "quote": "nota", "text": "Richiamare",
                                   "contact": {"phone": "333 3333333"}})
    assert passi(db, "agent_a1", cid)[1]["decision"] == "auto"
    q(db, "UPDATE contacts SET assigned_agent_id = %s WHERE id = %s", (db["ids"]["agent_a2"], db["ids"]["fabio_bianchi"]))
    try:
        report = esegui(db, "agent_a1", cid)
        assert esito(report, 1)["outcome"] in ("needs_answer", "blocked")
        assert conta(db) == prima
    finally:
        q(db, "UPDATE contacts SET assigned_agent_id = NULL WHERE id = %s", (db["ids"]["fabio_bianchi"],))


def test_p10_confirmation_goes_stale_when_the_situation_changes(db):
    """Conferma data con UN Fabio visibile; prima dell'esecuzione ne compare
    un secondo: la conferma non vale piu'."""
    cid = comando(db, "agent_a2", {"intent": "create_appointment", "quote": "Domani alle 18 appuntamento con Fabio",
                                   "when": {"date_text": "domani", "time_text": "alle 18"},
                                   "contact": {"first_name": "Fabio"}},
                  transcript="Domani alle 18 appuntamento con Fabio")
    conferma_tutto(db, "agent_a2", cid)
    q(db, "INSERT INTO contacts (agency_id, contact_type, display_name, first_name, last_name, assigned_agent_id) "
          "VALUES (1, 'person', 'Fabio Neri', 'Fabio', 'Neri', %s)", (db["ids"]["agent_a2"],))
    prima = conta(db)
    report = esegui(db, "agent_a2", cid)
    assert esito(report, 1)["outcome"] in ("needs_confirmation", "needs_answer")
    assert conta(db) == prima


def test_p11_ambiguous_note_target_is_never_written_automatically(db):
    """Due «Marco Galli» visibili: la nota non si scrive senza scelta."""
    prima = conta(db)
    cid = comando(db, "agent_a1", {"intent": "add_note", "quote": "nota", "text": "Ok",
                                   "contact": {"first_name": "Marco", "last_name": "Galli"}})
    assert passi(db, "agent_a1", cid)[1]["decision"] == "ask"
    esegui(db, "agent_a1", cid)
    assert conta(db) == prima
