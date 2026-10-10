"""STIMA Voice Fase 1 - pianificatore (voice/planner.py) e politica
(voice/policy.py): le regole, una per una."""
from __future__ import annotations

from datetime import datetime

import pytest

from appointments.schemas import AppointmentCreate
from core.schemas import ContactCreate, TaskCreate
from property.schemas import BuildingCreate, CensusUnitCreate
from voice import planner, policy
from voice.dates import ROMA
from voice.planner import build_plan, decide_record_kind, fingerprint
from voice.policy import Facts, RefFacts, Settings, decide
from voice.schemas import parse_plan_output

NOW = datetime(2026, 10, 10, 16, 0, tzinfo=ROMA)


def plan(transcript, *commands):
    return build_plan(parse_plan_output({"commands": list(commands)}), transcript, NOW)


CONTATTO = {"intent": "create_contact", "quote": "Marco Rossi 333 1234567", "first_name": "Marco", "last_name": "Rossi", "phone": "333 1234567"}


# --- record_kind: la regola, per esteso ------------------------------------

@pytest.mark.parametrize("claimed, transcript, seller, kind, issue", [
    (None, "fabio vuole vendere un trilocale", False, "crm", None),
    (None, "censisci l appartamento al piano 2", False, "census", None),
    (None, "aggiungi un appartamento in via roma", False, None, planner.RECORD_KIND_UNKNOWN),
    ("crm", "aggiungi un appartamento in via roma", False, None, planner.RECORD_KIND_UNVERIFIED),
    ("census", "fabio vuole vendere un trilocale", False, None, planner.RECORD_KIND_CONFLICT),
    (None, "censisci l appartamento, e in vendita", False, None, planner.RECORD_KIND_CONFLICT),
    (None, "censisci la palazzina. marco vuole vendere il bilocale", False, None, planner.RECORD_KIND_CONFLICT),
    (None, "aggiungi un appartamento in via roma", True, "crm", None),     # un Vende impone crm
    ("census", "censisci l appartamento", True, None, planner.RECORD_KIND_CONFLICT),
    ("crm", "ha preso l incarico per via roma", False, "crm", None),
    (None, "vuole mettere in affitto il bilocale", False, "crm", None),
])
def test_01_record_kind_rule(claimed, transcript, seller, kind, issue):
    k, i = decide_record_kind(claimed, transcript, seller_requires=seller)
    assert k == kind
    assert (i.code if i else None) == issue


def test_01b_the_command_words_decide_when_the_transcript_mixes():
    vocale = "censisci la palazzina di via dante. marco vuole vendere il bilocale di via roma"
    assert decide_record_kind(None, vocale, seller_requires=False, quote_folded="vuole vendere il bilocale") == ("crm", None)
    assert decide_record_kind(None, vocale, seller_requires=False, quote_folded="censisci la palazzina") == ("census", None)
    k, i = decide_record_kind(None, vocale, seller_requires=False, quote_folded="il bilocale di via roma")
    assert k is None and i.code == planner.RECORD_KIND_CONFLICT



def test_02_keywords_are_frozen():
    assert planner.COMMERCIAL_KEYWORDS == ("vend", "in vendita", "incaric", "mandat", "acquisi", "affitt", "locazion")
    assert planner.CENSUS_KEYWORDS == ("censi", "schedat", "mappat")


# --- i payload sono quelli del CRM -----------------------------------------

def test_03_payloads_pass_the_crm_schemas():
    p = plan("Marco Rossi 333 1234567 vuole vendere un trilocale in via Roma a Pineto. Censisci la palazzina di via Dante a Pineto, 8 unità. Ricordami lunedì di richiamarlo",
             CONTATTO,
             {"intent": "create_unit", "quote": "vuole vendere un trilocale", "rooms": 3, "address": "via Roma", "city": "Pineto", "owner": {"step": 1}},
             {"intent": "create_building", "quote": "censisci la palazzina", "address": "via Dante", "city": "Pineto", "units_declared": 8},
             {"intent": "add_task", "quote": "ricordami", "title": "Richiamare Marco Rossi", "due": {"date_text": "lunedì"}, "contact": {"step": 1}},
             {"intent": "create_appointment", "quote": "sopralluogo", "when": {"date_text": "giovedì", "time_text": "alle 15"}, "contact": {"step": 1}})
    assert all(not s.issues for s in p.steps), [(s.intent, s.issues) for s in p.steps]
    ContactCreate(**p.step(1).payload)
    unita = CensusUnitCreate(**p.step(2).payload)
    assert unita.record_kind == "crm" and unita.rooms == 3 and unita.assigned_agent_id is None
    edificio = BuildingCreate(**p.step(3).payload)
    assert edificio.units_declared == 8 and edificio.units_declared_source is None  # provenienza mai dedotta
    TaskCreate(**p.step(4).payload, contact_id=1)
    ap = p.step(5).payload
    AppointmentCreate(appointment_type=ap["appointment_type"], start_at=ap["start_at"], end_at=ap["end_at"], assigned_user_id=1)
    assert ap["appointment_type"] == "inspection" and (ap["end_at"] - ap["start_at"]).total_seconds() == 3600
    assert "client_request_id" not in p.step(2).payload  # la chiave nasce nel registro (Fase 3), mai qui


def test_04_crm_validators_reject_what_the_crm_rejects():
    p = plan("x", {"intent": "create_unit", "quote": "x", "record_kind": "census", "rooms": 3})
    assert p.step(1).has(planner.RECORD_KIND_UNVERIFIED)
    p = plan("censisci", {"intent": "create_building", "quote": "x", "address": "via Roma", "units_declared": 5})
    assert not p.step(1).issues
    # TaskCreate esige un contatto: senza riferimento e' una domanda, non un'invenzione
    p = plan("ricordami", {"intent": "add_task", "quote": "x", "title": "Chiamare"})
    assert p.step(1).has(planner.TASK_WITHOUT_CONTACT) and "contact_id" not in p.step(1).payload


# --- appuntamenti -----------------------------------------------------------

@pytest.mark.parametrize("quote, tipo", [
    ("sopralluogo con Marco", "inspection"), ("visita con i Bianchi", "buyer_visit"), ("firma dell'incarico", "mandate_signing"),
    ("telefonata a Marco", "call"), ("videochiamata", "video_call"), ("rogito dal notaio", "notary"),
    ("appuntamento con Marco", "other"), ("presentazione della valutazione", "valuation_presentation"),
])
def test_05_appointment_type_from_words(quote, tipo):
    assert planner.appointment_type_from(quote.lower()) == tipo


def test_06_model_type_wins_over_words_and_duration_default():
    p = plan("x", {"intent": "create_appointment", "quote": "sopralluogo", "appointment_type": "call", "duration_minutes": 30,
                   "when": {"date_text": "domani", "time_text": "alle 10"}})
    assert p.step(1).payload["appointment_type"] == "call"
    assert (p.step(1).end_at - p.step(1).start_at).total_seconds() == 1800
    assert planner.DEFAULT_APPOINTMENT_MINUTES == 60 and planner.DEFAULT_APPOINTMENT_TYPE == "other"


def test_07_dates_issues_reach_the_step():
    p = plan("x", {"intent": "create_appointment", "quote": "x", "when": {"date_text": "domani", "time_text": "alle tre"}})
    assert p.step(1).has("time_ambiguous_hour") and p.step(1).start_at is None
    p = plan("x", {"intent": "create_appointment", "quote": "x", "when": {"date_text": "oggi", "time_text": "alle 9"}})
    assert p.step(1).has("datetime_in_the_past")


# --- dipendenze, unita' inventate, impronta ---------------------------------

def test_08_dependencies_follow_step_references():
    p = plan("vende", CONTATTO,
             {"intent": "create_unit", "quote": "x", "address": "via Roma", "owner": {"step": 1}},
             {"intent": "activate_seller", "quote": "vende", "contact": {"step": 1}, "property": {"step": 2}})
    assert [s.depends_on for s in p.steps] == [(), (1,), (1, 2)]
    assert planner.execution_order(p) == [1, 2, 3]


def test_09_identical_units_hanging_on_a_building_are_invented():
    unita = {"intent": "create_unit", "quote": "palazzina con 3 appartamenti", "record_kind": "census", "building": {"step": 1}}
    p = plan("censisci una palazzina con 3 appartamenti",
             {"intent": "create_building", "quote": "palazzina", "address": "via Roma", "units_declared": 3}, unita, unita, unita)
    assert not p.step(1).issues
    assert all(p.step(i).has(planner.UNITS_INVENTED) for i in (2, 3, 4))
    # due unita' DIVERSE dettate davvero restano
    p = plan("censisci la palazzina",
             {"intent": "create_building", "quote": "palazzina", "address": "via Roma", "units_declared": 2},
             {"intent": "create_unit", "quote": "piano 1", "record_kind": "census", "floor": "1", "building": {"step": 1}},
             {"intent": "create_unit", "quote": "piano 2", "record_kind": "census", "floor": "2", "building": {"step": 1}})
    assert not any(s.has(planner.UNITS_INVENTED) for s in p.steps)


def test_10_fingerprint_depends_on_what_not_on_how_it_was_said():
    a = plan("Marco Rossi 333 1234567 nuovo contatto", CONTATTO)
    b = plan("segna Marco Rossi, 333 1234567", {**CONTATTO, "quote": "segna Marco Rossi"})
    c = plan("x", {**CONTATTO, "last_name": "Rosso"})
    assert a.fingerprint == b.fingerprint != c.fingerprint
    assert fingerprint(a.steps) == a.fingerprint


# --- la politica ------------------------------------------------------------

def _facts(**kw):
    return Facts(**kw)


def test_11_auto_when_everything_is_resolved():
    p = plan("nota", {"intent": "add_note", "quote": "x", "text": "ok", "contact": {"first_name": "Marco", "last_name": "Rossi"}})
    d = decide(p, _facts(refs={(1, "contact"): RefFacts(1, exact=True)}))
    assert d.auto == (1,) and not d.needs_answers


@pytest.mark.parametrize("facts, motivo", [
    (RefFacts(0), policy.REF_NOT_FOUND), (RefFacts(2), policy.REF_AMBIGUOUS), (RefFacts(1, exact=False), policy.CONTACT_CONFIRM),
])
def test_12_references_must_be_unique_and_exact_for_notes(facts, motivo):
    p = plan("nota", {"intent": "add_note", "quote": "x", "text": "ok", "contact": {"first_name": "Marco"}})
    d = decide(p, _facts(refs={(1, "contact"): facts}))
    assert d.of(1).decision == "ask" and d.of(1).reasons == (motivo,)


def test_13_fuzzy_reference_for_other_intents():
    p = plan("vende", {"intent": "activate_seller", "quote": "x", "contact": {"first_name": "Marco"}, "property": {"address": "via Roma"}})
    d = decide(p, _facts(refs={(1, "contact"): RefFacts(1, exact=False), (1, "property"): RefFacts(1, exact=True)}))
    assert d.of(1).reasons == (policy.CONTACT_CONFIRM,)
    d = decide(p, _facts(refs={(1, "contact"): RefFacts(1, exact=True), (1, "property"): RefFacts(1, exact=False)}))
    assert d.of(1).reasons == (policy.REF_FUZZY,)
    nota_su_immobile = plan("nota", {"intent": "add_note", "quote": "x", "text": "ok", "property": {"address": "via Roma"}})
    d = decide(nota_su_immobile, _facts(refs={(1, "property"): RefFacts(1, exact=False)}))
    assert d.of(1).reasons == (policy.NOTE_TARGET_NOT_EXACT,)


@pytest.mark.parametrize("comando", [
    {"intent": "add_note", "quote": "x", "text": "ok", "contact": {"first_name": "Mario", "last_name": "Rossi"}},
    {"intent": "add_task", "quote": "x", "title": "Richiamare", "contact": {"first_name": "Mario", "last_name": "Rossi"}},
    {"intent": "link_owner", "quote": "x", "contact": {"first_name": "Mario", "last_name": "Rossi"}, "property": {"code": "VR10"}},
    {"intent": "activate_seller", "quote": "x", "contact": {"first_name": "Mario", "last_name": "Rossi"}, "property": {"code": "VR10"}},
    {"intent": "create_appointment", "quote": "x", "when": {"date_text": "domani", "time_text": "alle 15"},
     "contact": {"first_name": "Mario", "last_name": "Rossi"}},
    {"intent": "create_unit", "quote": "vuole vendere", "address": "via Roma", "owner": {"first_name": "Mario", "last_name": "Rossi"}},
])
def test_13b_a_contact_found_by_name_needs_confirmation_for_every_write(comando):
    """Correzione della review Fase 2: per qualunque operazione che scrive,
    un contatto trovato solo per nome non basta. Con il recapito si esegue."""
    p = plan("vuole vendere", comando)
    nomi = {k: RefFacts(1, exact=k != "contact" and k != "owner") for k in p.step(1).refs}
    d = decide(p, _facts(refs={(1, k): v for k, v in nomi.items()}))
    assert d.of(1).decision == "ask" and policy.CONTACT_CONFIRM in d.of(1).reasons
    affidabili = {(1, k): RefFacts(1, exact=True) for k in p.step(1).refs}
    assert decide(p, _facts(refs=affidabili)).of(1).decision == "auto"


def test_14_duplicates_visible_hidden_and_the_d1_switch():
    p = plan("x", CONTATTO)
    assert decide(p, _facts(duplicate_visible={1: 2})).of(1).reasons == (policy.DUPLICATE_VISIBLE,)
    assert decide(p, _facts(duplicate_hidden=frozenset({1}))).of(1).reasons == (policy.DUPLICATE_HIDDEN,)
    assert decide(p, _facts(duplicate_hidden=frozenset({1})), Settings(hidden_duplicate_check=False)).of(1).decision == "auto"


def test_15_blocked_and_asking_issues_are_disjoint_and_cover_the_planner():
    assert not (policy.BLOCKING_ISSUES & policy.ASKING_ISSUES)
    codici = {getattr(planner, n) for n in dir(planner) if n.isupper() and isinstance(getattr(planner, n), str)
              and n not in ("VOICE_SOURCE", "DEFAULT_APPOINTMENT_TYPE")}
    codici -= {planner.DEFAULT_APPOINTMENT_TYPE}
    assert codici <= (policy.BLOCKING_ISSUES | policy.ASKING_ISSUES), codici - (policy.BLOCKING_ISSUES | policy.ASKING_ISSUES)


def test_16_dependents_wait_or_are_blocked():
    p = plan("vende", {"intent": "create_contact", "quote": "x", "first_name": "Giulia"},
             {"intent": "create_unit", "quote": "x", "address": "via Roma", "owner": {"step": 1}},
             {"intent": "activate_seller", "quote": "vende", "contact": {"step": 1}, "property": {"step": 2}})
    d = decide(p, _facts())
    assert [s.decision for s in d.steps] == ["ask", "ask", "ask"]
    assert d.of(2).reasons == (policy.DEPENDS_ON_QUESTION,) and policy.DEPENDS_ON_QUESTION in d.of(3).reasons
    p = plan("x", {"intent": "unsupported", "quote": "x", "description": "cancella"},
             {"intent": "add_note", "quote": "x", "text": "t", "contact": {"first_name": "Marco", "last_name": "Rossi"}})
    d = decide(p, _facts(refs={(2, "contact"): RefFacts(1, True)}))
    assert d.of(1).decision == "blocked" and d.of(2).decision == "auto"  # indipendente: non dipende dal bloccato


def test_17_agent_named_depends_on_the_actor_permission():
    cmd = {"intent": "create_appointment", "quote": "lo fa Giulia", "when": {"date_text": "domani", "time_text": "alle 10"},
           "contact": {"first_name": "Marco", "last_name": "Rossi"}, "agent_name": "Giulia"}
    p = plan("x", cmd)
    refs = {(1, "contact"): RefFacts(1, True)}
    assert decide(p, _facts(refs=refs, actor_may_assign=False)).of(1).reasons == (policy.AGENT_CANNOT_ASSIGN,)
    assert decide(p, _facts(refs=refs, actor_may_assign=True)).of(1).reasons == (policy.REF_NOT_FOUND,)


def test_18_modes_only_move_auto_to_ask():
    p = plan("x", {"intent": "add_note", "quote": "x", "text": "ok", "contact": {"first_name": "Marco", "last_name": "Rossi"}}, CONTATTO)
    f = _facts(refs={(1, "contact"): RefFacts(1, True)})
    assert [s.decision for s in decide(p, f, Settings(mode="review")).steps] == ["ask", "ask"]
    assert [s.decision for s in decide(p, f, Settings(mode="assisted")).steps] == ["auto", "ask"]
    assert [s.decision for s in decide(p, f, Settings(mode="auto")).steps] == ["auto", "auto"]
    bloccato = plan("x", {"intent": "unsupported", "quote": "x", "description": "d"})
    assert decide(bloccato, f, Settings(mode="review")).of(1).decision == "blocked"  # mai meno severo


def test_19_similar_unit_is_a_question_not_a_block():
    p = plan("vende", {"intent": "create_unit", "quote": "vuole vendere", "address": "via Roma", "civic_number": "10"})
    d = decide(p, _facts(similar_unit=frozenset({1})))
    assert d.of(1).decision == "ask" and d.of(1).reasons == (policy.SIMILAR_UNIT,)
