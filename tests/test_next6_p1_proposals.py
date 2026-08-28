from __future__ import annotations

import copy
import importlib
import json
import os
import re
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError as PydanticValidationError

from core.exceptions import ConflictError, ValidationError
from integration_p2_support import import_main_app


ROOT = Path(__file__).resolve().parents[1]
BUY_HTML = ROOT / "static/buy_admin/index.html"
BUY_JS = ROOT / "static/buy_admin/assets/app.js"
PROPERTY_JS = ROOT / "static/property_admin/assets/app.js"
CORE_JS = ROOT / "static/core_admin/assets/app.js"
UP = ROOT / "migrations/013_proposal_01.sql"
DOWN = ROOT / "migrations/013_proposal_01_down.sql"
NOW = datetime(2030, 1, 10, 12, 0, tzinfo=timezone.utc)
FUTURE = datetime(2030, 2, 10, 12, 0, tzinfo=timezone.utc)
PAST = datetime(2020, 1, 9, 12, 0, tzinfo=timezone.utc)


def proposal_module(name: str):
    try:
        return importlib.import_module(f"proposal.{name}")
    except ModuleNotFoundError:
        pytest.fail(f"proposal.{name} mancante")


def create_payload(match_id=11, key="11111111-1111-4111-8111-111111111111", **overrides):
    value = {
        "match_id": match_id,
        "amount": Decimal("185000.00"),
        "expires_at": FUTURE,
        "notes": "Proposta iniziale",
        "idempotency_key": UUID(key),
    }
    value.update(overrides)
    return value


def _json_value(value):
    adapted = getattr(value, "adapted", value)
    return copy.deepcopy(adapted)


class ProposalDatabase:
    def __init__(self, *, fail_history=False):
        self.state = {
            "buy_requests": {
                7: {"id": 7, "contact_id": 31, "lead_id": 41, "title": "Casa mare", "archived_at": None},
                8: {"id": 8, "contact_id": 32, "lead_id": None, "title": "Prima casa", "archived_at": None},
            },
            "contacts": {
                31: {"id": 31, "display_name": "Mario Rossi"},
                32: {"id": 32, "display_name": "Anna Verdi"},
            },
            "properties": {
                21: {"id": 21, "title": "Attico", "code": "P21", "archived_at": None},
                22: {"id": 22, "title": "Villa", "code": "P22", "archived_at": None},
            },
            "matches": {
                11: {"id": 11, "buy_request_id": 7, "property_id": 21, "archived_at": None, "commercial_status": "interested"},
                12: {"id": 12, "buy_request_id": 8, "property_id": 21, "archived_at": None, "commercial_status": "offer_candidate"},
                13: {"id": 13, "buy_request_id": 8, "property_id": 22, "archived_at": None, "commercial_status": "interested"},
                14: {"id": 14, "buy_request_id": 7, "property_id": 22, "archived_at": NOW, "commercial_status": "interested"},
            },
            "proposals": [],
            "history": [],
            "interactions": [],
            "tasks": [],
        }
        self.fail_history = fail_history
        self.transactions = 0
        self.commits = 0
        self.rollbacks = 0
        self.queries = []

    @contextmanager
    def cursor(self, commit=False):
        self.transactions += 1
        staged = copy.deepcopy(self.state)
        cursor = ProposalCursor(self, staged)
        try:
            yield object(), cursor
        except Exception:
            if commit:
                self.rollbacks += 1
            raise
        else:
            if commit:
                self.state = staged
                self.commits += 1


class ProposalCursor:
    def __init__(self, database, staged):
        self.database = database
        self.state = staged
        self.result = None
        self.rows = []

    def _derived(self, match_id):
        match = self.state["matches"].get(match_id)
        if not match:
            return None
        buy = self.state["buy_requests"].get(match["buy_request_id"])
        prop = self.state["properties"].get(match["property_id"])
        contact = self.state["contacts"].get(buy["contact_id"]) if buy else None
        if not buy or not prop or not contact:
            return None
        return {
            **match,
            "match_id": match["id"],
            "match_archived_at": match["archived_at"],
            "buy_request_id": buy["id"],
            "buy_title": buy["title"],
            "buy_archived_at": buy["archived_at"],
            "property_id": prop["id"],
            "property_title": prop["title"],
            "property_code": prop["code"],
            "property_archived_at": prop["archived_at"],
            "contact_id": contact["id"],
            "contact_name": contact["display_name"],
            "lead_id": buy["lead_id"],
        }

    def _enriched(self, proposal):
        return {**self._derived(proposal["match_id"]), **copy.deepcopy(proposal), "database_now": NOW}

    def execute(self, query, params=()):
        normalized = " ".join(query.split())
        lowered = normalized.lower()
        params = tuple(params)
        self.database.queries.append((normalized, params))
        self.result = None
        self.rows = []

        if lowered.startswith("select") and "from matches m" in lowered and "join buy_requests b" in lowered and "where m.id=%s" in lowered:
            self.result = copy.deepcopy(self._derived(params[0]))
            return

        if lowered.startswith("select") and "from property_proposals" in lowered and "idempotency_key=%s" in lowered:
            key = str(params[0])
            item = next((x for x in self.state["proposals"] if str(x["idempotency_key"]) == key), None)
            self.result = copy.deepcopy(item)
            return

        if lowered.startswith("select") and "from property_proposals" in lowered and "status in ('draft','submitted')" in lowered:
            match_id = params[0]
            item = next((x for x in self.state["proposals"] if x["match_id"] == match_id and x["status"] in {"draft", "submitted"}), None)
            self.result = copy.deepcopy(item)
            return

        if lowered.startswith("insert into property_proposals"):
            columns = re.search(r"property_proposals\s*\(([^)]+)\)", query, re.IGNORECASE)
            assert columns, query
            names = [name.strip() for name in columns.group(1).split(",")]
            candidate = dict(zip(names, params))
            existing = next((x for x in self.state["proposals"] if x["idempotency_key"] == candidate["idempotency_key"]), None)
            if existing and "on conflict" in lowered:
                self.result = None
                return
            if any(x["match_id"] == candidate["match_id"] and x["status"] in {"draft", "submitted"} for x in self.state["proposals"]):
                raise ConflictError("open proposal already exists for match")
            candidate.update({"id": len(self.state["proposals"]) + 501, "status": "draft", "created_at": NOW, "updated_at": NOW})
            self.state["proposals"].append(candidate)
            self.result = copy.deepcopy(candidate)
            return

        if lowered.startswith("select") and "from property_proposals pp" in lowered and "where pp.id=%s" in lowered:
            item = next((x for x in self.state["proposals"] if x["id"] == params[0]), None)
            self.result = self._enriched(item) if item else None
            return

        if (
            lowered.startswith("select")
            and "from property_proposals pp" in lowered
            and "where" in lowered
            and "pp.status='accepted'" not in lowered
        ):
            values = list(self.state["proposals"])
            pos = 0
            if "pp.match_id=%s" in lowered:
                values = [x for x in values if x["match_id"] == params[pos]]; pos += 1
            if "m.buy_request_id=%s" in lowered:
                values = [x for x in values if self.state["matches"][x["match_id"]]["buy_request_id"] == params[pos]]; pos += 1
            if "m.property_id=%s" in lowered:
                values = [x for x in values if self.state["matches"][x["match_id"]]["property_id"] == params[pos]]; pos += 1
            if "b.contact_id=%s" in lowered:
                values = [x for x in values if self.state["buy_requests"][self.state["matches"][x["match_id"]]["buy_request_id"]]["contact_id"] == params[pos]]; pos += 1
            self.rows = [self._enriched(x) for x in values]
            return

        if lowered.startswith("update property_proposals set"):
            proposal_id = params[-1]
            item = next(x for x in self.state["proposals"] if x["id"] == proposal_id)
            assignments = normalized[normalized.lower().index("set") + 3:normalized.lower().index("where")].split(",")
            value_index = 0
            for assignment in assignments:
                field = assignment.split("=")[0].strip()
                if "%s" in assignment:
                    item[field] = params[value_index]
                    value_index += 1
                elif field == "updated_at":
                    item[field] = NOW
            self.result = copy.deepcopy(item)
            return

        if lowered.startswith("select id from properties") and "for update" in lowered:
            self.result = copy.deepcopy(self.state["properties"].get(params[0]))
            return

        if lowered.startswith("select pp.id") and "pp.status='accepted'" in lowered:
            property_id, excluded_id = params
            item = next((x for x in self.state["proposals"] if x["id"] != excluded_id and x["status"] == "accepted" and self.state["matches"][x["match_id"]]["property_id"] == property_id), None)
            self.result = {"id": item["id"]} if item else None
            return

        if lowered.startswith("insert into buy_request_history"):
            if self.database.fail_history:
                raise RuntimeError("history insert failed")
            fields = ("buy_request_id", "event_type", "match_id", "property_id", "task_id", "reason_code", "description", "old_value", "new_value", "created_by")
            item = dict(zip(fields, params))
            item["old_value"] = _json_value(item["old_value"])
            item["new_value"] = _json_value(item["new_value"])
            self.state["history"].append(item)
            return

        if any(token in lowered for token in ("update matches", "update properties", "update leads", "insert into buy_request_interactions", "insert into tasks")):
            raise AssertionError(f"automazione cross-module vietata: {normalized}")
        raise AssertionError(f"Query non prevista: {normalized}")

    def fetchone(self):
        return copy.deepcopy(self.result)

    def fetchall(self):
        return copy.deepcopy(self.rows)


def test_migration_up_and_down_define_only_the_proposal_domain_and_buy_history_events():
    assert UP.exists(), "migration up P1 mancante"
    assert DOWN.exists(), "migration down P1 mancante"
    up = UP.read_text(encoding="utf-8")
    down = DOWN.read_text(encoding="utf-8")

    assert re.search(r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?property_proposals", up, re.IGNORECASE)
    assert "match_id BIGINT NOT NULL REFERENCES matches(id) ON DELETE RESTRICT" in up
    assert re.search(r"amount\s+NUMERIC\(14,2\)\s+NOT NULL\s+CHECK\s*\(amount\s*>\s*0\)", up, re.IGNORECASE)
    assert "expires_at TIMESTAMPTZ NOT NULL" in up
    assert "idempotency_key UUID NOT NULL UNIQUE" in up
    assert set(re.findall(r"'(draft|submitted|accepted|rejected|expired|withdrawn)'", up)) == {"draft", "submitted", "accepted", "rejected", "expired", "withdrawn"}
    assert re.search(r"UNIQUE INDEX[\s\S]+match_id[\s\S]+WHERE status IN \('draft','submitted'\)", up, re.IGNORECASE)
    for event in ("proposal_created", "proposal_updated", "proposal_status_changed"):
        assert event in up
    for forbidden in ("ALTER TABLE matches", "ALTER TABLE properties", "ALTER TABLE leads", "ALTER TABLE buy_requests ADD"):
        assert forbidden not in up
    assert re.search(r"DROP TABLE(?: IF EXISTS)? property_proposals", down, re.IGNORECASE)
    assert "buy_request_history_event_type_check" in down


def test_schema_requires_positive_amount_expiry_match_and_uuid():
    schemas = proposal_module("schemas")
    valid = schemas.ProposalCreate(**create_payload())
    assert valid.match_id == 11
    assert valid.amount == Decimal("185000.00")
    assert valid.status if hasattr(valid, "status") else "draft"

    for values, field in (
        ({**create_payload(), "amount": 0}, "amount"),
        ({k: v for k, v in create_payload().items() if k != "expires_at"}, "expires_at"),
        ({k: v for k, v in create_payload().items() if k != "match_id"}, "match_id"),
        ({**create_payload(), "idempotency_key": "not-a-uuid"}, "idempotency_key"),
    ):
        with pytest.raises(PydanticValidationError, match=field):
            schemas.ProposalCreate(**values)


@pytest.mark.parametrize(
    "model_name,payload",
    [
        ("ProposalCreate", create_payload(expires_at="2030-01-01T12:00:00")),
        ("ProposalUpdate", {"expires_at": "2030-01-01T12:00:00"}),
    ],
)
def test_proposal_expiry_rejects_datetime_without_timezone(model_name, payload):
    schemas = proposal_module("schemas")

    with pytest.raises(PydanticValidationError, match="expires_at"):
        getattr(schemas, model_name)(**payload)


@pytest.mark.parametrize(
    "expires_at",
    ["2030-01-01T12:00:00Z", "2030-01-01T13:00:00+01:00"],
)
def test_create_accepts_expiry_with_explicit_timezone(expires_at):
    schemas = proposal_module("schemas")

    proposal = schemas.ProposalCreate(**create_payload(expires_at=expires_at))

    assert proposal.expires_at.utcoffset() is not None


@pytest.mark.parametrize("field", ["amount", "expires_at"])
def test_update_cannot_null_required_proposal_fields(field):
    schemas = proposal_module("schemas")
    with pytest.raises(PydanticValidationError, match=field):
        schemas.ProposalUpdate(**{field: None})


@pytest.mark.parametrize("forbidden", ["buy_request_id", "property_id", "contact_id", "lead_id", "created_by", "status"])
def test_create_payload_cannot_impose_relations_actor_or_status(forbidden):
    schemas = proposal_module("schemas")
    with pytest.raises(PydanticValidationError):
        schemas.ProposalCreate(**create_payload(), **{forbidden: 999 if forbidden != "status" else "accepted"})


def test_create_derives_every_relation_and_writes_draft_and_history_atomically(monkeypatch):
    repository = proposal_module("repository")
    database = ProposalDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)

    result = repository.create_proposal(create_payload(), "giorgio")

    assert database.transactions == database.commits == 1
    assert database.rollbacks == 0
    assert result["status"] == "draft"
    assert result["match_id"] == 11
    assert result["buy_request_id"] == 7
    assert result["property_id"] == 21
    assert result["contact_id"] == 31
    assert result["lead_id"] == 41
    assert result["created_by"] == "giorgio"
    assert database.state["history"][-1]["event_type"] == "proposal_created"
    assert database.state["history"][-1]["buy_request_id"] == 7
    assert database.state["history"][-1]["match_id"] == 11
    assert database.state["history"][-1]["property_id"] == 21
    relation_lock = next(query for query, _ in database.queries if "FROM matches m" in query)
    assert "FOR UPDATE" in relation_lock.upper()


def test_archived_match_is_rejected_without_writes(monkeypatch):
    repository = proposal_module("repository")
    database = ProposalDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)

    with pytest.raises(ValidationError, match="archived"):
        repository.create_proposal(create_payload(match_id=14), "giorgio")

    assert database.state["proposals"] == []
    assert database.state["history"] == []


@pytest.mark.parametrize(
    "collection,row_id,error",
    [
        ("matches", 11, "match is archived"),
        ("buy_requests", 7, "buy request is archived"),
        ("properties", 21, "property is archived"),
    ],
)
@pytest.mark.parametrize("operation", ["update", "transition"])
def test_existing_proposal_rejects_archived_relations_atomically(
    monkeypatch,
    collection,
    row_id,
    error,
    operation,
):
    repository = proposal_module("repository")
    database = ProposalDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    created = repository.create_proposal(create_payload(), "giorgio")
    database.state[collection][row_id]["archived_at"] = NOW
    original = copy.deepcopy(database.state)

    with pytest.raises(ValidationError, match=error):
        if operation == "update":
            repository.update_proposal(
                created["id"],
                {"amount": Decimal("190000.00")},
                "giorgio",
            )
        else:
            repository.transition_proposal(created["id"], "submitted", "giorgio")

    assert database.state == original
    assert database.state["proposals"][0]["status"] == "draft"
    assert len(database.state["history"]) == 1
    assert database.rollbacks == 1
    relation_queries = [
        query
        for query, params in database.queries
        if "FROM matches m" in query and params == (11,)
    ]
    assert "FOR UPDATE OF m,b,p" in relation_queries[-1]


@pytest.mark.parametrize("workflow", ["create", "update", "transition"])
def test_proposal_workflows_lock_relations_before_proposal_rows(monkeypatch, workflow):
    repository = proposal_module("repository")
    database = ProposalDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)

    if workflow == "create":
        repository.create_proposal(create_payload(), "giorgio")
    else:
        created = repository.create_proposal(create_payload(), "giorgio")
        database.queries.clear()
        if workflow == "update":
            repository.update_proposal(
                created["id"],
                {"amount": Decimal("190000.00")},
                "giorgio",
            )
        else:
            repository.transition_proposal(created["id"], "submitted", "giorgio")

    relation_lock_index = next(
        index
        for index, (query, _) in enumerate(database.queries)
        if "FROM matches m" in query and "FOR UPDATE OF m,b,p" in query
    )
    proposal_lock_indices = [
        index
        for index, (query, _) in enumerate(database.queries)
        if "property_proposals" in query and "FOR UPDATE" in query
    ]

    assert proposal_lock_indices
    assert relation_lock_index < min(proposal_lock_indices)
    if workflow != "create":
        proposal_reads = [
            query
            for query, _ in database.queries
            if "FROM property_proposals pp" in query and "WHERE pp.id=%s" in query
        ]
        assert "FOR UPDATE" not in proposal_reads[0]
        assert any("FOR UPDATE OF pp" in query for query in proposal_reads[1:])


def test_idempotency_returns_same_proposal_for_same_payload_and_conflicts_on_different_payload(monkeypatch):
    repository = proposal_module("repository")
    database = ProposalDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    payload = create_payload()

    first = repository.create_proposal(payload, "giorgio")
    second = repository.create_proposal(payload, "giorgio")
    assert second["id"] == first["id"]
    assert len(database.state["proposals"]) == 1
    assert len(database.state["history"]) == 1

    with pytest.raises(ConflictError, match="idempotency"):
        repository.create_proposal({**payload, "amount": Decimal("190000.00")}, "giorgio")


def test_idempotency_compares_timestamptz_round_trip_by_instant(monkeypatch):
    repository = proposal_module("repository")
    database = ProposalDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    utc_payload = create_payload(expires_at=datetime.fromisoformat("2030-01-01T12:00:00+00:00"))

    first = repository.create_proposal(utc_payload, "giorgio")
    database.state["proposals"][0]["expires_at"] = datetime.fromisoformat(
        "2030-01-01T12:00:00+00:00"
    )
    offset_payload = create_payload(
        expires_at=datetime.fromisoformat("2030-01-01T13:00:00+01:00")
    )
    second = repository.create_proposal(offset_payload, "giorgio")

    assert second["id"] == first["id"]
    assert len(database.state["proposals"]) == 1
    assert len(database.state["history"]) == 1


def test_only_one_open_proposal_per_match_but_new_after_terminal_is_allowed(monkeypatch):
    repository = proposal_module("repository")
    database = ProposalDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    first = repository.create_proposal(create_payload(), "giorgio")

    with pytest.raises(ConflictError, match="open proposal"):
        repository.create_proposal(create_payload(key="22222222-2222-4222-8222-222222222222"), "giorgio")

    repository.transition_proposal(first["id"], "withdrawn", "giorgio")
    second = repository.create_proposal(create_payload(key="22222222-2222-4222-8222-222222222222"), "giorgio")
    assert second["id"] != first["id"]


@pytest.mark.parametrize(
    "start,target",
    [
        ("draft", "submitted"),
        ("draft", "withdrawn"),
        ("submitted", "accepted"),
        ("submitted", "rejected"),
        ("submitted", "withdrawn"),
    ],
)
def test_allowed_lifecycle_transitions_write_only_status_and_history(monkeypatch, start, target):
    repository = proposal_module("repository")
    database = ProposalDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    created = repository.create_proposal(create_payload(), "giorgio")
    if start == "submitted":
        repository.transition_proposal(created["id"], "submitted", "giorgio")

    result = repository.transition_proposal(created["id"], target, "giorgio")

    assert result["status"] == target
    assert database.state["history"][-1]["event_type"] == "proposal_status_changed"
    assert database.state["history"][-1]["old_value"]["status"] == start
    assert database.state["history"][-1]["new_value"]["status"] == target
    assert database.state["matches"][11]["commercial_status"] == "interested"
    assert database.state["properties"][21].get("commercial_status") is None
    assert database.state["buy_requests"][7].get("stage") is None
    assert database.state["interactions"] == []
    assert database.state["tasks"] == []


def test_expiry_is_manual_due_only_and_accept_is_forbidden_after_deadline(monkeypatch):
    repository = proposal_module("repository")
    database = ProposalDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    future = repository.create_proposal(create_payload(), "giorgio")
    repository.transition_proposal(future["id"], "submitted", "giorgio")
    with pytest.raises(ValidationError, match="not expired"):
        repository.transition_proposal(future["id"], "expired", "giorgio")

    database = ProposalDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    overdue = repository.create_proposal(create_payload(expires_at=PAST), "giorgio")
    repository.transition_proposal(overdue["id"], "submitted", "giorgio")
    with pytest.raises(ValidationError, match="expired"):
        repository.transition_proposal(overdue["id"], "accepted", "giorgio")
    expired = repository.transition_proposal(overdue["id"], "expired", "giorgio")
    assert expired["status"] == "expired"


@pytest.mark.parametrize("terminal", ["accepted", "rejected", "expired", "withdrawn"])
def test_terminal_states_reject_every_further_transition(monkeypatch, terminal):
    repository = proposal_module("repository")
    database = ProposalDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    expiry = PAST if terminal == "expired" else FUTURE
    created = repository.create_proposal(create_payload(expires_at=expiry), "giorgio")
    if terminal not in {"withdrawn"}:
        repository.transition_proposal(created["id"], "submitted", "giorgio")
    repository.transition_proposal(created["id"], terminal, "giorgio")

    with pytest.raises(ConflictError, match="terminal"):
        repository.transition_proposal(created["id"], "withdrawn", "giorgio")


def test_update_is_draft_only_and_is_atomic_with_history(monkeypatch):
    repository = proposal_module("repository")
    database = ProposalDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    created = repository.create_proposal(create_payload(), "giorgio")

    updated = repository.update_proposal(created["id"], {"amount": Decimal("190000.00"), "notes": None}, "giorgio")
    assert updated["amount"] == Decimal("190000.00")
    assert updated["notes"] is None
    assert database.state["history"][-1]["event_type"] == "proposal_updated"

    repository.transition_proposal(created["id"], "submitted", "giorgio")
    with pytest.raises(ConflictError, match="draft"):
        repository.update_proposal(created["id"], {"amount": Decimal("195000.00")}, "giorgio")


def test_two_buyers_may_propose_same_property_but_only_one_can_be_accepted(monkeypatch):
    repository = proposal_module("repository")
    database = ProposalDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    first = repository.create_proposal(create_payload(match_id=11), "giorgio")
    second = repository.create_proposal(create_payload(match_id=12, key="22222222-2222-4222-8222-222222222222"), "giorgio")
    repository.transition_proposal(first["id"], "submitted", "giorgio")
    repository.transition_proposal(second["id"], "submitted", "giorgio")
    repository.transition_proposal(first["id"], "accepted", "giorgio")

    with pytest.raises(ConflictError, match="accepted proposal"):
        repository.transition_proposal(second["id"], "accepted", "giorgio")

    locks = [query for query, _ in database.queries if "FROM properties" in query and "FOR UPDATE" in query.upper()]
    assert len(locks) == 2
    first_lock = next(index for index, (query, _) in enumerate(database.queries) if "FROM properties" in query and "FOR UPDATE" in query.upper())
    accepted_check = next(index for index, (query, _) in enumerate(database.queries) if "pp.status='accepted'" in query.lower())
    assert first_lock < accepted_check
    assert len(database.state["proposals"]) == 2
    assert {x["status"] for x in database.state["proposals"]} == {"accepted", "submitted"}


def test_accepting_different_properties_is_allowed(monkeypatch):
    repository = proposal_module("repository")
    database = ProposalDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    first = repository.create_proposal(create_payload(match_id=11), "giorgio")
    second = repository.create_proposal(create_payload(match_id=13, key="33333333-3333-4333-8333-333333333333"), "giorgio")
    for proposal in (first, second):
        repository.transition_proposal(proposal["id"], "submitted", "giorgio")
        repository.transition_proposal(proposal["id"], "accepted", "giorgio")
    assert [x["status"] for x in database.state["proposals"]] == ["accepted", "accepted"]


def test_history_failure_rolls_back_proposal_and_every_cross_module_state(monkeypatch):
    repository = proposal_module("repository")
    database = ProposalDatabase(fail_history=True)
    original = copy.deepcopy(database.state)
    monkeypatch.setattr(repository, "core_cursor", database.cursor)

    with pytest.raises(RuntimeError, match="history insert failed"):
        repository.create_proposal(create_payload(), "giorgio")

    assert database.commits == 0
    assert database.rollbacks == 1
    assert database.state == original


def test_list_filters_support_only_the_three_read_surfaces(monkeypatch):
    repository = proposal_module("repository")
    database = ProposalDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    repository.create_proposal(create_payload(match_id=11), "giorgio")
    repository.create_proposal(create_payload(match_id=12, key="22222222-2222-4222-8222-222222222222"), "giorgio")
    repository.create_proposal(create_payload(match_id=13, key="33333333-3333-4333-8333-333333333333"), "giorgio")

    assert len(repository.list_proposals(match_id=11)) == 1
    assert len(repository.list_proposals(buy_request_id=8)) == 2
    assert len(repository.list_proposals(property_id=21)) == 2
    assert len(repository.list_proposals(contact_id=31)) == 1
    assert repository.get_proposal(501)["contact_name"] == "Mario Rossi"


def test_router_uses_real_admin_identity_and_returns_409_for_accepted_conflict(monkeypatch):
    service = proposal_module("service")
    captured = {}

    def create(model, actor):
        captured.update({"actor": actor, "payload": model})
        return {"id": 1, "status": "draft"}

    monkeypatch.setattr(service, "create_proposal", create)
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    app = import_main_app()
    client = TestClient(app, raise_server_exceptions=False)
    body = {**create_payload(), "amount": "185000.00", "expires_at": FUTURE.isoformat(), "idempotency_key": str(create_payload()["idempotency_key"])}
    response = client.post("/api/proposals", json=body, auth=("giorgio", "test-secret"))

    assert response.status_code == 201
    assert captured["actor"] == "giorgio"
    anonymous = client.get("/api/proposals")
    assert anonymous.status_code == 401
    operation = app.openapi()["paths"]["/api/proposals"]["post"]
    assert operation.get("security")

    router_module = proposal_module("router")
    monkeypatch.setattr(
        service,
        "transition_proposal",
        lambda *_args: (_ for _ in ()).throw(router_module.ConflictError("accepted proposal already exists")),
    )
    conflict = client.post("/api/proposals/1/transition", json={"target_status": "accepted"}, auth=("giorgio", "test-secret"))
    assert conflict.status_code == 409


def run_buy_js(function_name, argument):
    script = (
        f"const app=require({json.dumps(str(BUY_JS))});"
        f"const result=app[{json.dumps(function_name)}]({json.dumps(argument)});"
        "process.stdout.write(JSON.stringify(result));"
    )
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_buy_ui_builds_create_payload_from_match_only_and_converts_local_expiry():
    payload = run_buy_js(
        "buildProposalCreatePayload",
        {
            "match_id": "11",
            "amount": "185000.00",
            "expires_at": "2030-02-10T13:00",
            "notes": "Nota",
            "idempotency_key": "11111111-1111-4111-8111-111111111111",
            "property_id": "999",
            "contact_id": "998",
        },
    )
    assert payload["match_id"] == 11
    assert payload["amount"] == 185000
    assert payload["notes"] == "Nota"
    assert payload["idempotency_key"] == "11111111-1111-4111-8111-111111111111"
    assert payload["expires_at"].endswith("Z")
    assert set(payload) == {"match_id", "amount", "expires_at", "notes", "idempotency_key"}


def test_buy_ui_has_explicit_writer_no_manual_relational_ids_and_double_submit_guard():
    html = BUY_HTML.read_text(encoding="utf-8")
    js = BUY_JS.read_text(encoding="utf-8")
    form = re.search(r'<form[^>]+id="proposalForm"[^>]*>(.*?)</form>', html, re.DOTALL)
    assert form
    assert 'name="match_id"' in form.group(1) and 'type="hidden"' in form.group(1)
    assert not re.search(r'name="(?:buy_request_id|property_id|contact_id|lead_id|created_by)"', form.group(1))
    assert "Crea proposta" in js
    assert "/api/proposals" in js
    assert re.search(r"if\s*\(proposalSubmitPending\)\s*return", js)
    assert re.search(r"proposalSubmitPending\s*=\s*true", js)
    assert re.search(r"finally\s*\{[\s\S]*?proposalSubmitPending\s*=\s*false", js)
    assert "crypto.randomUUID()" in js
    assert "offer_candidate" in js and "createProposal" not in js[js.index("buildMatchDecisionPayload"):js.index("childCollectionUrl")]


def test_buy_ui_exposes_only_lifecycle_actions_for_current_status():
    assert run_buy_js("proposalActions", {"id": 1, "status": "draft", "expires_at": FUTURE.isoformat()}) == ["edit", "submitted", "withdrawn"]
    assert run_buy_js("proposalActions", {"id": 1, "status": "submitted", "expires_at": FUTURE.isoformat()}) == ["accepted", "rejected", "withdrawn"]
    assert run_buy_js("proposalActions", {"id": 1, "status": "submitted", "expires_at": PAST.isoformat()}) == ["accepted", "rejected", "withdrawn", "expired"]
    for status in ("accepted", "rejected", "expired", "withdrawn"):
        assert run_buy_js("proposalActions", {"id": 1, "status": status, "expires_at": PAST.isoformat()}) == []


def test_property_and_contact360_are_read_only_proposal_consumers_with_safe_rendering():
    property_js = PROPERTY_JS.read_text(encoding="utf-8")
    core_js = CORE_JS.read_text(encoding="utf-8")
    assert "/api/proposals?property_id=" in property_js
    assert "/api/proposals?contact_id=" in core_js
    for source in (property_js, core_js):
        assert "Proposte" in source
        assert "textContent" in source
    assert not re.search(r"api\([^\n]*?/api/proposals[^\n]*?method\s*:\s*['\"](?:POST|PATCH)", property_js)
    assert not re.search(r"api\([^\n]*?/api/proposals[^\n]*?method\s*:\s*['\"](?:POST|PATCH)", core_js)


def test_next5_match_engine_readiness_flow_and_owner_remain_untouched_by_proposal_domain():
    forbidden_import_roots = ("match/engine.py", "match/readiness.py")
    for path in forbidden_import_roots:
        source = (ROOT / path).read_text(encoding="utf-8")
        assert not re.search(r"(?:from|import)\s+proposal\b", source)
    for directory in ("flow", "owner"):
        for path in (ROOT / directory).glob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert not re.search(r"(?:from|import)\s+proposal\b", source)
