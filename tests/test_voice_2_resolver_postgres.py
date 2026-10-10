"""STIMA Voice Fase 2 - il risolutore su PostgreSQL vero, con lo schema
completo del CRM (stessa fixture certificata di CENSIMENTO-1), due agenzie,
titolare, amministratore e due agenti.

Opt-in `P29_TEST_DSN`, SOLO database locale usa-e-getta.

Cosa prova:
  * un agente vede solo i propri contatti, titolare e amministratore tutti;
  * D1: il doppione di un collega si segnala con un SI', senza un dato;
  * nessun dato di un'altra agenzia, del Cestino o (per i riferimenti) degli
    archiviati;
  * immobili ed edifici: visibili a tutta l'agenzia, come nel CRM; gli avvisi
    del censimento sono quelli del censimento;
  * la risoluzione non scrive: transazione READ ONLY e impronta del database
    identica prima e dopo;
  * i tre esempi del mandato, dal fornitore finto alla decisione.
"""
from __future__ import annotations

import argparse
from datetime import datetime

import pytest

from tests.test_censimento_1_fullschema_postgres import (
    DSN, IMPRONTA_CERTIFICATA, MIGRAZIONI, NOME_DB, _cartella_fino_a, _dsn_locale, _dsn_per,
    _env_runner, _fixture, _pre_baseline, _runner,
)

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare STIMA Voice Fase 2")

OPERATORE = "voice.fase2"
NOW = datetime.fromisoformat("2026-10-10T16:00:00+02:00")


@pytest.fixture(scope="module")
def db():
    psycopg2 = pytest.importorskip("psycopg2")
    from psycopg2.extras import DictCursor
    _dsn_locale(DSN)
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (NOME_DB,))
        if cur.fetchone():
            servizio.close()
            pytest.skip(f"sul cluster locale esiste gia' un database {NOME_DB}: non lo tocco")
        cur.execute(f'CREATE DATABASE "{NOME_DB}"')
    dsn = _dsn_per(NOME_DB)
    conn = psycopg2.connect(dsn, cursor_factory=DictCursor)
    conn.autocommit = True
    mp = pytest.MonkeyPatch()
    try:
        import database as legacy
        originale = legacy.get_connection
        legacy.get_connection = lambda: psycopg2.connect(dsn)
        try:
            legacy.crea_tabella_stime()
            legacy.crea_tabella_stime_dettagliate()
            legacy.crea_tabella_zone_valori()
            legacy.migrazione_allinea_stime()
        finally:
            legacy.get_connection = originale
        with conn.cursor() as cur:
            for p in _pre_baseline():
                cur.execute(p.read_text(encoding="utf-8"))
        c = {"conn": conn, "dsn": dsn}
        runner = _runner()
        _env_runner(mp, dsn, NOME_DB)
        args = argparse.Namespace(**(runner.SHARED_DEFAULTS | {
            "operator": OPERATORE, "baseline_fingerprint": IMPRONTA_CERTIFICATA,
            "baseline_artifact": "reports/p26_baseline_TEST_20260905T170601Z.json"}))
        for massimo in (26, 80):
            mp.setattr(runner, "MIGRATIONS_DIR", _cartella_fino_a(massimo))
            assert runner.command_apply(args) == 0
        _fixture(c)
        mp.setattr(runner, "MIGRATIONS_DIR", MIGRAZIONI)
        assert runner.command_apply(args) == 0
        from core import database as core_database
        mp.setattr(core_database, "get_connection", lambda: psycopg2.connect(dsn))
        _dati(c)
        yield c
    finally:
        mp.undo()
        conn.close()
        with servizio.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()", (NOME_DB,))
            cur.execute(f'DROP DATABASE IF EXISTS "{NOME_DB}"')
        servizio.close()


def _q(c, sql, params=None):
    with c["conn"].cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def _dati(c):
    ids = {}

    def operatore(chiave, email, nome, cognome, agenzia, ruolo):
        uid = _q(c, "INSERT INTO operator_users (email, email_normalized, password_hash, first_name, last_name) "
                    "VALUES (%s, %s, 'pbkdf2_sha256$1$x$y', %s, %s) RETURNING id", (email, email, nome, cognome))[0][0]
        _q(c, "INSERT INTO agency_memberships (agency_id, operator_user_id, role, status) VALUES (%s, %s, %s, 'active')",
           (agenzia, uid, ruolo))
        ids[chiave] = uid

    operatore("owner_a", "o.a@x.test", "Olga", "Ferri", 1, "agency_owner")
    operatore("admin_a", "d.a@x.test", "Dario", "Monti", 1, "agency_admin")
    operatore("agent_a1", "a1@x.test", "Anna", "Neri", 1, "agent")
    operatore("agent_a2", "a2@x.test", "Bruno", "Galli", 1, "agent")
    operatore("owner_b", "o.b@x.test", "Bea", "Sala", 2, "agency_owner")
    operatore("agent_b", "ab@x.test", "Carla", "Riva", 2, "agent")

    def contatto(chiave, agenzia, nome, cognome, *, telefono=None, email=None, agente=None, stato="active",
                 cestino=False, azienda=None):
        from core.normalization import normalize_email, normalize_phone
        cid = _q(c, "INSERT INTO contacts (agency_id, contact_type, display_name, first_name, last_name, company_name, "
                    "phone, phone_normalized, email, email_normalized, assigned_agent_id, status) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                 (agenzia, "company" if azienda else "person", azienda or f"{nome} {cognome}", nome, cognome, azienda,
                  telefono, normalize_phone(telefono), email, normalize_email(email),
                  ids[agente] if agente else None, stato))[0][0]
        if cestino:
            _q(c, "UPDATE contacts SET deleted_at = NOW(), deleted_by_user_id = %s, deleted_reason = 'duplicate' WHERE id = %s",
               (ids["owner_a"], cid))
        ids[chiave] = cid

    contatto("mario", 1, "Mario", "Rossi", telefono="333 1111111", email="mario@x.it", agente="agent_a1")
    contatto("fabio_verdi", 1, "Fabio", "Verdi", telefono="+39 333 2222222", agente="agent_a2")
    contatto("fabio_bianchi", 1, "Fabio", "Bianchi", telefono="333 3333333")             # nessun agente
    contatto("giulia", 1, "Giulia", "Neri", telefono="333 4444444", agente="agent_a1", stato="archived")
    contatto("luca", 1, "Luca", "Conti", telefono="333 5555555", agente="agent_a1", cestino=True)
    contatto("marco1", 1, "Marco", "Galli", agente="agent_a1")
    contatto("marco2", 1, "Marco", "Galli", agente="agent_a1")
    contatto("acme", 1, None, None, azienda="Acme Srl", agente="agent_a1")
    contatto("fabio_b", 2, "Fabio", "Verdi", telefono="333 2222222", email="fv@x.it", agente="agent_b")
    contatto("solo_b", 2, "Sergio", "Lodi", telefono="333 9999999", agente="agent_b")

    def edificio(chiave, agenzia, nome, via, civico, comune):
        ids[chiave] = _q(c, "INSERT INTO buildings (agency_id, building_type, name, address, civic_number, city, units_declared) "
                            "VALUES (%s, 'condominio', %s, %s, %s, %s, 8) RETURNING id",
                         (agenzia, nome, via, civico, comune))[0][0]

    edificio("sole", 1, "Residenza Sole", "Via Dante", "5", "Pineto")
    edificio("sole_b", 2, "Residenza Luna", "Via Dante", "5", "Pineto")

    def immobile(chiave, agenzia, codice, via, civico, comune, *, kind="crm", edificio=None, piano=None, interno=None,
                 tipo="apartment", cestino=False):
        pid = _q(c, "INSERT INTO properties (code, title, property_type, agency_id, address, civic_number, city, record_kind, "
                    "building_id, floor, internal_number, commercial_status) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'draft') RETURNING id",
                 (codice, f"Immobile {codice}", tipo, agenzia, via, civico, comune, kind,
                  ids[edificio] if edificio else None, piano, interno))[0][0]
        if cestino:
            _q(c, "UPDATE properties SET deleted_at = NOW(), deleted_by_user_id = %s, deleted_reason = 'duplicate' WHERE id = %s",
               (ids["owner_a"], pid))
        ids[chiave] = pid

    immobile("roma10", 1, "VR10", "Via Roma", "10", "Pineto")
    immobile("roma12", 1, "VR12", "Via Roma", "12", "Pineto", tipo="villa")
    immobile("dante_2_4", 1, "VD54", "Via Dante", "5", "Pineto", kind="census", edificio="sole", piano="2", interno="4")
    immobile("trieste", 1, "VT1", "Via Trieste", "1", "Pineto", cestino=True)
    immobile("roma10_b", 2, "BR10", "Via Roma", "10", "Pineto")
    c["ids"] = ids


_AGENZIA = {"owner_a": 1, "admin_a": 1, "agent_a1": 1, "agent_a2": 1, "owner_b": 2, "agent_b": 2}


def ctx(c, chiave, *, agenzia="dalla membership", ruolo=None, platform=False):
    if agenzia == "dalla membership":
        agenzia = _AGENZIA[chiave]
    from operator_auth.context import OperatorContext
    ruoli = {"owner_a": "agency_owner", "admin_a": "agency_admin", "agent_a1": "agent", "agent_a2": "agent",
             "owner_b": "agency_owner", "agent_b": "agent"}
    return OperatorContext(user_id=c["ids"].get(chiave), agency_id=agenzia, role=ruolo or ruoli.get(chiave),
                           is_platform_admin=platform, session_id=None, auth_channel="session")


def plan(*commands, transcript="vocale di prova"):
    from voice.planner import build_plan
    from voice.schemas import parse_plan_output
    return build_plan(parse_plan_output({"commands": list(commands)}), transcript, NOW)


def resolve(c, p, chi, **kw):
    from voice.resolver import resolve as risolvi
    return risolvi(p, ctx(c, chi, **kw))


def nota(**contatto):
    return {"intent": "add_note", "quote": "nota", "text": "Richiamare", "contact": contatto}


# ---------------------------------------------------------------------------
# CONTATTI: chi vede cosa
# ---------------------------------------------------------------------------

def test_01_agent_sees_only_own_contacts(db):
    p = plan(nota(first_name="Mario", last_name="Rossi"))
    r = resolve(db, p, "agent_a1")
    # trovato, ma SOLO per nome: non e' un identificativo affidabile
    assert r.resolved(1, "contact") == db["ids"]["mario"] and not r.facts.refs[(1, "contact")].exact
    r = resolve(db, p, "agent_a2")
    assert r.resolved(1, "contact") is None and r.facts.refs[(1, "contact")].candidates == 0


def test_02_owner_and_admin_see_the_whole_agency(db):
    p = plan(nota(first_name="Fabio"))
    for chi in ("owner_a", "admin_a"):
        r = resolve(db, p, chi)
        assert {cand.id for cand in r.matches[(1, "contact")].candidates} == {db["ids"]["fabio_verdi"], db["ids"]["fabio_bianchi"]}
        assert not r.matches[(1, "contact")].exact
    assert resolve(db, p, "agent_a1").facts.refs[(1, "contact")].candidates == 0
    assert resolve(db, p, "agent_a2").resolved(1, "contact") == db["ids"]["fabio_verdi"]


def test_03_phone_and_email_match_with_crm_normalization(db):
    for rif in ({"phone": "+39 333 111 1111"}, {"phone": "3331111111"}, {"email": " MARIO@X.IT "}):
        r = resolve(db, plan(nota(**rif)), "agent_a1")
        assert r.resolved(1, "contact") == db["ids"]["mario"] and r.facts.refs[(1, "contact")].exact, rif


def test_04_never_another_agency(db):
    r = resolve(db, plan(nota(phone="333 9999999")), "owner_a")
    assert r.facts.refs[(1, "contact")].candidates == 0
    r = resolve(db, plan(nota(first_name="Sergio", last_name="Lodi")), "owner_a")
    assert r.facts.refs[(1, "contact")].candidates == 0
    r = resolve(db, plan(nota(phone="333 2222222")), "owner_b")
    assert r.resolved(1, "contact") == db["ids"]["fabio_b"]


def test_05_archived_and_trashed_are_not_references(db):
    for rif in ({"first_name": "Giulia", "last_name": "Neri"}, {"phone": "333 4444444"},
                {"first_name": "Luca", "last_name": "Conti"}, {"phone": "333 5555555"}):
        r = resolve(db, plan(nota(**rif)), "agent_a1")
        assert r.facts.refs[(1, "contact")].candidates == 0, rif


def test_06_homonyms_are_ambiguous_with_visible_candidates_only(db):
    from voice.policy import Facts, decide
    p = plan(nota(first_name="Marco", last_name="Galli"))
    r = resolve(db, p, "agent_a1")
    assert {cand.id for cand in r.matches[(1, "contact")].candidates} == {db["ids"]["marco1"], db["ids"]["marco2"]}
    assert decide(p, r.facts).of(1).reasons == ("reference_ambiguous",)
    assert resolve(db, p, "agent_a2").facts.refs[(1, "contact")].candidates == 0
    assert isinstance(r.facts, Facts)


def test_07_company_name_is_a_name_too(db):
    r = resolve(db, plan(nota(company_name="ACME srl")), "agent_a1")
    assert r.resolved(1, "contact") == db["ids"]["acme"] and not r.facts.refs[(1, "contact")].exact


def test_07b_name_alone_never_writes_without_confirmation(db):
    """Correzione della review Fase 2: nome e cognome, anche completi e con un
    solo candidato, non bastano per scrivere. Serve il telefono o l'email,
    oppure la conferma dell'agente sul contatto giusto."""
    from voice.policy import CONTACT_CONFIRM, decide
    for comando in (
        nota(first_name="Mario", last_name="Rossi"),
        {"intent": "add_task", "quote": "x", "title": "Richiamare", "contact": {"first_name": "Mario", "last_name": "Rossi"}},
        {"intent": "link_owner", "quote": "x", "contact": {"first_name": "Mario", "last_name": "Rossi"}, "property": {"code": "VR10"}},
        vende(code="VR10"),
        {"intent": "create_appointment", "quote": "x", "when": {"date_text": "domani", "time_text": "alle 15"},
         "contact": {"first_name": "Mario", "last_name": "Rossi"}},
        {"intent": "create_unit", "quote": "vuole vendere", "address": "Via Nuova", "owner": {"first_name": "Mario", "last_name": "Rossi"}},
    ):
        p = plan(comando, transcript="vuole vendere")
        d = decide(p, resolve(db, p, "agent_a1").facts)
        assert d.of(1).decision == "ask" and CONTACT_CONFIRM in d.of(1).reasons, comando["intent"]


def test_07c_phone_or_email_identify_and_allow_automatic_writes(db):
    from voice.policy import decide
    for rif in ({"first_name": "Mario", "last_name": "Rossi", "phone": "333 1111111"}, {"email": "mario@x.it"}):
        p = plan(nota(**rif))
        r = resolve(db, p, "agent_a1")
        assert r.resolved(1, "contact") == db["ids"]["mario"] and decide(p, r.facts).of(1).decision == "auto", rif


def test_07d_unknown_phone_falls_back_to_name_and_asks(db):
    """Recapito detto ma sconosciuto: si cerca per nome, e si chiede sempre."""
    from voice.policy import CONTACT_CONFIRM, decide
    p = plan(nota(first_name="Mario", last_name="Rossi", phone="333 0000000"))
    r = resolve(db, p, "agent_a1")
    assert r.resolved(1, "contact") == db["ids"]["mario"] and not r.facts.refs[(1, "contact")].exact
    assert decide(p, r.facts).of(1).reasons == (CONTACT_CONFIRM,)


# ---------------------------------------------------------------------------
# DOPPIONI DI CONTATTO e D1
# ---------------------------------------------------------------------------

def nuovo(**campi):
    return {"intent": "create_contact", "quote": "nuovo contatto", **campi}


def test_08_d1_hidden_duplicate_is_a_bare_yes(db):
    from voice.policy import decide
    p = plan(nuovo(first_name="Fabio", last_name="Verdi", phone="333 2222222"))
    r = resolve(db, p, "agent_a1")
    assert 1 in r.facts.duplicate_hidden and 1 not in r.facts.duplicate_visible and 1 not in r.duplicates
    assert decide(p, r.facts).of(1).reasons == ("possible_duplicate_hidden",)
    # nessun dato del collega nella risoluzione: ne' id, ne' nome, ne' numero
    testo = repr(r)
    assert str(db["ids"]["fabio_verdi"]) not in [str(x) for x in _ids_in(r)]
    assert "Verdi" not in testo and "2222222" not in testo


def _ids_in(r):
    out = set()
    for m in r.matches.values():
        out |= {cand.id for cand in m.candidates}
    for cands in r.duplicates.values():
        out |= {cand.id for cand in cands}
    return out


def test_09_owner_sees_the_same_duplicate_with_its_data(db):
    p = plan(nuovo(first_name="Fabio", last_name="Verdi", phone="333 2222222"))
    r = resolve(db, p, "owner_a")
    assert r.facts.duplicate_visible[1] == 1 and 1 not in r.facts.duplicate_hidden
    assert [cand.id for cand in r.duplicates[1]] == [db["ids"]["fabio_verdi"]]


def test_10_unassigned_contact_is_hidden_from_agents(db):
    """Fabio Bianchi non e' assegnato a nessuno: per il CRM un agente non lo
    vede (scoped_predicate). Il controllo D1 lo segnala senza dati."""
    r = resolve(db, plan(nuovo(first_name="F", last_name="B", phone="333 3333333")), "agent_a2")
    assert 1 in r.facts.duplicate_hidden


def test_11_no_cross_agency_signal(db):
    r = resolve(db, plan(nuovo(first_name="Sergio", last_name="Lodi", phone="333 9999999")), "agent_a1")
    assert not r.facts.duplicate_hidden and not r.facts.duplicate_visible
    r = resolve(db, plan(nuovo(first_name="Fabio", last_name="Verdi", email="fv@x.it")), "owner_a")
    assert not r.facts.duplicate_hidden and not r.facts.duplicate_visible


def test_12_archived_counts_as_duplicate_trash_does_not(db):
    r = resolve(db, plan(nuovo(first_name="Giulia", last_name="Neri", phone="333 4444444")), "agent_a1")
    assert r.facts.duplicate_visible[1] == 1 and [cand.id for cand in r.duplicates[1]] == [db["ids"]["giulia"]]
    r = resolve(db, plan(nuovo(first_name="Luca", last_name="Conti", phone="333 5555555")), "agent_a1")
    assert not r.facts.duplicate_visible and not r.facts.duplicate_hidden


def test_13_name_duplicate_only_among_visible(db):
    r = resolve(db, plan(nuovo(first_name="Mario", last_name="Rossi")), "agent_a1")
    assert r.facts.duplicate_visible[1] == 1
    r = resolve(db, plan(nuovo(first_name="Mario", last_name="Rossi")), "agent_a2")
    assert not r.facts.duplicate_visible and not r.facts.duplicate_hidden  # D1 solo per recapito


def test_14_new_contact_without_any_match_is_auto(db):
    from voice.policy import decide
    p = plan(nuovo(first_name="Elena", last_name="Costa", phone="333 7777777"))
    r = resolve(db, p, "agent_a1")
    assert decide(p, r.facts).of(1).decision == "auto"


# ---------------------------------------------------------------------------
# IMMOBILI ED EDIFICI
# ---------------------------------------------------------------------------

def vende(**immobile):
    return {"intent": "activate_seller", "quote": "vende", "contact": {"first_name": "Mario", "last_name": "Rossi"},
            "property": immobile}


def test_15_property_by_code_and_by_address(db):
    r = resolve(db, plan(vende(code="vr10")), "agent_a2")      # gli immobili si vedono in tutta l'agenzia
    assert r.resolved(1, "property") == db["ids"]["roma10"] and r.facts.refs[(1, "property")].exact
    r = resolve(db, plan(vende(address="via roma", civic_number="10", city="Pineto")), "agent_a1")
    assert r.resolved(1, "property") == db["ids"]["roma10"] and r.facts.refs[(1, "property")].exact
    r = resolve(db, plan(vende(address="Via Roma")), "agent_a1")
    assert {cand.id for cand in r.matches[(1, "property")].candidates} == {db["ids"]["roma10"], db["ids"]["roma12"]}
    r = resolve(db, plan(vende(address="Via Roma", property_type="villa")), "agent_a1")
    assert r.resolved(1, "property") == db["ids"]["roma12"] and not r.facts.refs[(1, "property")].exact
    r = resolve(db, plan(vende(address="Roma", civic_number="12")), "agent_a1")    # via contenuta
    assert r.resolved(1, "property") == db["ids"]["roma12"] and not r.facts.refs[(1, "property")].exact


def test_16_property_never_from_other_agency_or_trash(db):
    r = resolve(db, plan(vende(code="BR10")), "owner_a")
    assert r.facts.refs[(1, "property")].candidates == 0
    r = resolve(db, plan(vende(address="Via Trieste", civic_number="1")), "owner_a")
    assert r.facts.refs[(1, "property")].candidates == 0


def test_17_census_units_are_found_and_labelled(db):
    r = resolve(db, plan(vende(address="Via Dante", civic_number="5")), "agent_a1")
    m = r.matches[(1, "property")]
    assert m.resolved_id == db["ids"]["dante_2_4"] and "censimento" in m.candidates[0].label


def test_18_building_by_name_and_address(db):
    unita = {"intent": "create_unit", "quote": "censisci", "record_kind": "census", "floor": "3", "internal_number": "7"}
    r = resolve(db, plan({**unita, "building": {"name": "residenza sole"}}, transcript="censisci"), "agent_a1")
    assert r.resolved(1, "building") == db["ids"]["sole"] and r.facts.refs[(1, "building")].exact
    r = resolve(db, plan({**unita, "building": {"address": "Via Dante", "civic_number": "5"}}, transcript="censisci"), "agent_a1")
    assert r.resolved(1, "building") == db["ids"]["sole"]
    r = resolve(db, plan({**unita, "building": {"name": "Residenza Luna"}}, transcript="censisci"), "owner_a")
    assert r.facts.refs[(1, "building")].candidates == 0          # e' dell'agenzia 2


def test_19_similar_building_uses_census_rule(db):
    from voice.policy import decide
    p = plan({"intent": "create_building", "quote": "censisci", "address": "via dante", "civic_number": "5", "city": "pineto",
              "units_declared": 8}, transcript="censisci la palazzina")
    r = resolve(db, p, "agent_a1")
    assert 1 in r.facts.similar_building and [cand.id for cand in r.duplicates[1]] == [db["ids"]["sole"]]
    assert decide(p, r.facts).of(1).reasons == ("similar_building_found",)
    r = resolve(db, p, "owner_b")
    assert 1 in r.facts.similar_building and [cand.id for cand in r.duplicates[1]] == [db["ids"]["sole_b"]]
    nuovo_edificio = plan({"intent": "create_building", "quote": "censisci", "address": "Via Verdi", "civic_number": "9",
                           "city": "Pineto", "units_declared": 4}, transcript="censisci la palazzina")
    assert not resolve(db, nuovo_edificio, "agent_a1").facts.similar_building


def test_20_similar_units_in_a_building_and_at_an_address(db):
    from voice.policy import decide
    stessa = {"intent": "create_unit", "quote": "censisci", "record_kind": "census", "floor": "2", "internal_number": "4",
              "building": {"name": "Residenza Sole"}}
    p = plan(stessa, transcript="censisci")
    r = resolve(db, p, "agent_a1")
    assert 1 in r.facts.similar_unit and [cand.id for cand in r.duplicates[1]] == [db["ids"]["dante_2_4"]]
    assert decide(p, r.facts).of(1).reasons == ("similar_unit_found",)
    assert not resolve(db, plan({**stessa, "floor": "3"}, transcript="censisci"), "agent_a1").facts.similar_unit
    commerciale = {"intent": "create_unit", "quote": "vuole vendere", "rooms": 3, "address": "Via Roma", "civic_number": "10",
                   "city": "Pineto"}
    r = resolve(db, plan(commerciale, transcript="vuole vendere"), "agent_a1")
    assert 1 in r.facts.similar_unit and [cand.id for cand in r.duplicates[1]] == [db["ids"]["roma10"]]
    r = resolve(db, plan({**commerciale, "civic_number": "99"}, transcript="vuole vendere"), "agent_a1")
    assert not r.facts.similar_unit


# ---------------------------------------------------------------------------
# AGENTI, PERMESSI, SOLA LETTURA
# ---------------------------------------------------------------------------

def appuntamento(agente):
    return {"intent": "create_appointment", "quote": "sopralluogo", "when": {"date_text": "lunedì", "time_text": "alle 10"},
            "contact": {"first_name": "Mario", "last_name": "Rossi"}, "agent_name": agente}


def test_21_agent_name_only_for_who_may_assign(db):
    from voice.policy import decide
    p = plan(appuntamento("Bruno Galli"))
    r = resolve(db, p, "owner_a")
    assert r.resolved(1, "agent") == db["ids"]["agent_a2"] and r.facts.actor_may_assign
    r = resolve(db, p, "agent_a1")
    assert (1, "agent") not in r.matches and not r.facts.actor_may_assign
    assert decide(p, r.facts).of(1).decision == "blocked"
    r = resolve(db, plan(appuntamento("Carla Riva")), "owner_a")      # agente di un'altra agenzia
    assert r.facts.refs[(1, "agent")].candidates == 0


def test_22_platform_admin_without_agency_reads_nothing(db, monkeypatch):
    from operator_auth.exceptions import PlatformAdminAgencyRequired
    from core import database as core_database
    chiamate = []
    monkeypatch.setattr(core_database, "get_connection", lambda: chiamate.append(1) or (_ for _ in ()).throw(AssertionError))
    with pytest.raises(PlatformAdminAgencyRequired):
        resolve(db, plan(nota(first_name="Mario")), "owner_a", agenzia=None, ruolo=None, platform=True)
    assert chiamate == []


def test_23_platform_admin_acting_sees_the_agency(db):
    r = resolve(db, plan(nota(first_name="Fabio")), "owner_a", ruolo="none", platform=True)
    assert r.facts.refs[(1, "contact")].candidates == 2


def test_24_the_transaction_is_read_only(db):
    import psycopg2
    from voice import repository
    with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
        with repository.read_only() as cur:
            cur.execute("UPDATE contacts SET notes = 'x' WHERE id = %s", (db["ids"]["mario"],))


def test_25_resolution_writes_nothing(db):
    def impronta():
        return _q(db, "SELECT (SELECT md5(string_agg(row_to_json(t)::text, '|' ORDER BY id)) FROM contacts t), "
                      "(SELECT md5(string_agg(row_to_json(t)::text, '|' ORDER BY id)) FROM properties t), "
                      "(SELECT md5(string_agg(row_to_json(t)::text, '|' ORDER BY id)) FROM buildings t), "
                      "(SELECT count(*) FROM activities), (SELECT count(*) FROM tasks), (SELECT count(*) FROM leads)")[0]
    prima = impronta()
    p = plan(nuovo(first_name="Fabio", last_name="Verdi", phone="333 2222222"),
             {"intent": "create_unit", "quote": "vuole vendere", "address": "Via Roma", "civic_number": "10", "owner": {"step": 1}},
             {"intent": "activate_seller", "quote": "vuole vendere", "contact": {"step": 1}, "property": {"step": 2}},
             appuntamento("Bruno Galli"), transcript="vuole vendere")
    for chi in ("agent_a1", "agent_a2", "owner_a", "admin_a"):
        resolve(db, p, chi)
    assert impronta() == prima


def test_26_hidden_query_selects_no_contact_columns():
    import inspect
    from voice import repository
    corpo = inspect.getsource(repository.hidden_contact_exists)
    sql = corpo[corpo.index("SELECT EXISTS"):]
    assert "SELECT 1 FROM contacts h" in sql and "AS hidden" in sql
    for colonna in ("h.id,", "display_name", "first_name", "last_name", "count("):
        assert colonna not in sql


# ---------------------------------------------------------------------------
# I TRE ESEMPI DEL MANDATO, dal fornitore finto alla decisione
# ---------------------------------------------------------------------------

def _decidi(db, chi, transcript, output):
    from voice.fake_provider import FakeVoiceProvider
    from voice.planner import build_plan
    from voice.policy import decide
    from voice.resolver import resolve as risolvi
    from voice.schemas import parse_plan_output, plan_json_schema
    f = FakeVoiceProvider()
    f.register(transcript, output)
    p = build_plan(parse_plan_output(f.interpret(transcript, schema=plan_json_schema(), recorded_at=NOW)), transcript, NOW)
    r = risolvi(p, ctx(db, chi))
    return p, r, decide(p, r.facts)


def test_27_example_seller_new_contact(db):
    t = "Fabio Esposito, 333 6666666, vuole vendere un trilocale in via Duca d'Aosta 3 a Pineto"
    p, r, d = _decidi(db, "agent_a1", t, {"commands": [
        {"intent": "create_contact", "quote": "Fabio Esposito, 333 6666666", "first_name": "Fabio", "last_name": "Esposito", "phone": "333 6666666"},
        {"intent": "create_unit", "quote": "trilocale in via Duca d'Aosta 3", "rooms": 3, "address": "via Duca d'Aosta", "civic_number": "3",
         "city": "Pineto", "owner": {"step": 1}},
        {"intent": "link_owner", "quote": "vuole vendere", "contact": {"step": 1}, "property": {"step": 2}},
        {"intent": "activate_seller", "quote": "vuole vendere", "contact": {"step": 1}, "property": {"step": 2}}]})
    assert [s.decision for s in d.steps] == ["auto"] * 4
    assert p.step(2).payload["record_kind"] == "crm"


def test_28_example_seller_existing_contact_of_a_colleague(db):
    """Lo stesso vocale, ma il numero e' di un contatto di un collega: STIMA
    si ferma sul contatto e i passi dipendenti aspettano. Nessun dato esce."""
    t = "Fabio Verdi, 333 2222222, vuole vendere un trilocale in via Duca d'Aosta 3 a Pineto"
    p, r, d = _decidi(db, "agent_a1", t, {"commands": [
        {"intent": "create_contact", "quote": "Fabio Verdi, 333 2222222", "first_name": "Fabio", "last_name": "Verdi", "phone": "333 2222222"},
        {"intent": "create_unit", "quote": "trilocale", "rooms": 3, "address": "via Duca d'Aosta", "civic_number": "3", "city": "Pineto",
         "owner": {"step": 1}},
        {"intent": "activate_seller", "quote": "vuole vendere", "contact": {"step": 1}, "property": {"step": 2}}]})
    assert [s.decision for s in d.steps] == ["ask", "ask", "ask"]
    assert d.of(1).reasons == ("possible_duplicate_hidden",)
    assert "Verdi" not in repr(r.duplicates) and not r.duplicates


def test_29_example_census_building(db):
    t = "Censisci una palazzina in via Verdi 20 a Pineto con 12 appartamenti"
    p, r, d = _decidi(db, "agent_a1", t, {"commands": [
        {"intent": "create_building", "quote": t, "address": "via Verdi", "civic_number": "20", "city": "Pineto", "units_declared": 12}]})
    assert d.of(1).decision == "auto" and p.step(1).payload["units_declared"] == 12 and len(p.steps) == 1


def test_30_example_appointment(db):
    t = "Domani alle 15 appuntamento con Mario Rossi"
    p, r, d = _decidi(db, "agent_a1", t, {"commands": [
        {"intent": "create_appointment", "quote": t, "when": {"date_text": "domani", "time_text": "alle 15"},
         "contact": {"first_name": "Mario", "last_name": "Rossi"}}]})
    # per nome: trovato, ma STIMA chiede di confermare il contatto
    assert d.of(1).reasons == ("contact_confirmation_required",) and r.resolved(1, "contact") == db["ids"]["mario"]
    t2 = "Domani alle 15 appuntamento con Mario Rossi, 333 1111111"
    p, r, d = _decidi(db, "agent_a1", t2, {"commands": [
        {"intent": "create_appointment", "quote": t2, "when": {"date_text": "domani", "time_text": "alle 15"},
         "contact": {"first_name": "Mario", "last_name": "Rossi", "phone": "333 1111111"}}]})
    assert d.of(1).decision == "auto" and r.resolved(1, "contact") == db["ids"]["mario"]
    p, r, d = _decidi(db, "agent_a2", t, {"commands": [
        {"intent": "create_appointment", "quote": t, "when": {"date_text": "domani", "time_text": "alle 15"},
         "contact": {"first_name": "Mario", "last_name": "Rossi"}}]})
    assert d.of(1).reasons == ("reference_not_found",)       # non e' un suo contatto
