"""STIMA Voice Fase 1 - il contratto con il modello (voice/schemas.py).

Lo schema e' chiuso, gli enum sono quelli del CRM (importati, non copiati),
i riferimenti puntano solo indietro, le date sono parole e non calcoli.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from appointments.enums import APPOINTMENT_TYPES
from core.enums import PRIORITIES
from property.enums import PROPERTY_TYPES
from property.interactions import INTERACTION_TYPES
from property.schemas import BUILDING_TYPES
from voice import schemas
from voice.schemas import INTENTS, parse_plan_output, plan_json_schema


def _cmd(**kw):
    return parse_plan_output({"commands": [kw]}).commands[0]


def test_01_intents_v1_are_exactly_these():
    assert INTENTS == ("add_note", "add_task", "create_contact", "create_unit", "create_building",
                       "link_owner", "activate_seller", "create_appointment", "unsupported")


def _enum_of(schema, model, campo):
    defs = schema["$defs"][model]["properties"][campo]
    if "enum" in defs:
        return set(defs["enum"])
    return {e for a in defs.get("anyOf", []) for e in a.get("enum", [])}


def test_02_enums_are_the_crm_enums():
    s = plan_json_schema()
    assert _enum_of(s, "CreateUnitCommand", "property_type") == set(PROPERTY_TYPES)
    assert _enum_of(s, "CreateBuildingCommand", "building_type") == set(BUILDING_TYPES)
    assert _enum_of(s, "CreateAppointmentCommand", "appointment_type") == set(APPOINTMENT_TYPES)
    assert _enum_of(s, "AddTaskCommand", "priority") == set(PRIORITIES)
    assert _enum_of(s, "AddNoteCommand", "interaction_type") == set(INTERACTION_TYPES)
    assert _enum_of(s, "CreateUnitCommand", "record_kind") == {"crm", "census"}
    assert set(s["$defs"]) >= {"ContactRef", "PropertyRef", "BuildingRef", "WhenRef"}


def test_03_json_schema_is_closed_everywhere():
    def oggetti(nodo):
        if isinstance(nodo, dict):
            if nodo.get("type") == "object" or "properties" in nodo:
                yield nodo
            for v in nodo.values():
                yield from oggetti(v)
        elif isinstance(nodo, list):
            for v in nodo:
                yield from oggetti(v)
    s = plan_json_schema()
    assert all(o.get("additionalProperties") is False for o in oggetti(s))
    json.dumps(s)  # serializzabile per il fornitore


def test_04_extra_fields_are_refused_everywhere():
    with pytest.raises(ValidationError):
        _cmd(intent="create_contact", quote="x", first_name="A", last_name="B", agency_id=1)
    with pytest.raises(ValidationError):
        _cmd(intent="create_unit", quote="x", client_request_id="abc")
    with pytest.raises(ValidationError):
        _cmd(intent="create_appointment", quote="x", when={"date_text": "domani"}, assigned_user_id=3)
    with pytest.raises(ValidationError):
        parse_plan_output({"commands": [], "agency_id": 1})


def test_05_unknown_intent_and_unknown_enum_values_are_refused():
    with pytest.raises(ValidationError):
        _cmd(intent="delete_contact", quote="x")
    with pytest.raises(ValidationError):
        _cmd(intent="create_unit", quote="x", property_type="attico")
    with pytest.raises(ValidationError):
        _cmd(intent="create_appointment", quote="x", when={"date_text": "domani"}, appointment_type="pranzo")
    with pytest.raises(ValidationError):
        _cmd(intent="create_unit", quote="x", record_kind="commerciale")


def test_06_references_need_something_and_point_backwards():
    with pytest.raises(ValidationError):
        _cmd(intent="add_note", quote="x", text="t", contact={})
    with pytest.raises(ValidationError):
        _cmd(intent="add_note", quote="x", text="t")  # ne' contatto ne' immobile
    with pytest.raises(ValidationError):
        parse_plan_output({"commands": [
            {"intent": "activate_seller", "quote": "x", "contact": {"step": 2}, "property": {"address": "via Roma"}},
            {"intent": "create_contact", "quote": "x", "first_name": "A", "last_name": "B"}]})
    with pytest.raises(ValidationError):  # step del tipo sbagliato
        parse_plan_output({"commands": [
            {"intent": "create_building", "quote": "x", "address": "via Roma"},
            {"intent": "link_owner", "quote": "x", "contact": {"step": 1}, "property": {"address": "via Roma"}}]})
    ok = parse_plan_output({"commands": [
        {"intent": "create_contact", "quote": "x", "first_name": "A", "last_name": "B"},
        {"intent": "create_unit", "quote": "x", "owner": {"step": 1}},
        {"intent": "activate_seller", "quote": "x", "contact": {"step": 1}, "property": {"step": 2}}]})
    assert [c.intent for c in ok.commands] == ["create_contact", "create_unit", "activate_seller"]


def test_07_phone_and_email_use_crm_normalization():
    c = _cmd(intent="create_contact", quote="x", first_name="A", last_name="B", email="  Mario@Example.IT ", phone="333 123 4567")
    assert c.email == "mario@example.it"
    assert c.phone == "333 123 4567"  # l'originale si conserva: normalizza il CRM alla scrittura
    with pytest.raises(ValidationError):
        _cmd(intent="create_contact", quote="x", first_name="A", last_name="B", phone="12")


def test_08_dates_are_words_not_timestamps():
    w = schemas.WhenRef(date_text="domani", time_text="alle 15")
    assert w.date_text == "domani"
    assert "start_at" not in schemas.CreateAppointmentCommand.model_fields
    assert "due_at" not in schemas.AddTaskCommand.model_fields


def test_09_contact_needs_a_name_and_building_declares_units_only():
    with pytest.raises(ValidationError):
        _cmd(intent="create_contact", quote="x", phone="333 1234567")
    b = _cmd(intent="create_building", quote="x", units_declared=12)
    assert b.units_declared == 12 and b.building_type == "condominio"
    assert "units" not in schemas.CreateBuildingCommand.model_fields  # mai un elenco di unita'


def test_10_limits():
    with pytest.raises(ValidationError):
        parse_plan_output({"commands": [{"intent": "unsupported", "quote": "x", "description": "d"}] * (schemas.MAX_COMMANDS + 1)})
