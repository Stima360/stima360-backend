from __future__ import annotations

import copy
import importlib
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError as PydanticValidationError

from core.exceptions import ConflictError, NotFoundError
from integration_p2_support import import_main_app


ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "migrations/016_sale_01.sql"
DOWN = ROOT / "migrations/016_sale_01_down.sql"
NOW = datetime(2030, 1, 10, 12, 0, tzinfo=timezone.utc)
KEY_1 = UUID("11111111-1111-4111-8111-111111111111")
KEY_2 = UUID("22222222-2222-4222-8222-222222222222")
KEY_3 = UUID("33333333-3333-4333-8333-333333333333")


def sale_module(name: str):
    try:
        return importlib.import_module(f"sale.{name}")
    except ModuleNotFoundError:
        pytest.fail(f"sale.{name} mancante")


def create_payload(proposal_id=101, key=KEY_1, **overrides):
    value = {
        "proposal_id": proposal_id,
        "sale_price": None,
        "notes": "Vendita test",
        "idempotency_key": key,
    }
    value.update(overrides)
    return value


class SaleDatabase:
    def __init__(self, *, fail_history=False):
        self.state = {
            "contacts": {
                31: {"id": 31, "display_name": "Mario Rossi"},
                32: {"id": 32, "display_name": "Anna Verdi"},
                50: {"id": 50, "display_name": "Seller Uno"},
                51: {"id": 51, "display_name": "Seller Due"},
            },
            "properties": {
                21: {"id": 21, "title": "Attico", "code": "P21", "commercial_status": "under_offer"},
                22: {"id": 22, "title": "Villa", "code": "P22", "commercial_status": "active"},
                23: {"id": 23, "title": "Loft", "code": "P23", "commercial_status": "sold"},
                24: {"id": 24, "title": "Duplex", "code": "P24", "commercial_status": "active"},
            },
            "buy_requests": {
                7: {"id": 7, "contact_id": 31, "status": "active"},
                8: {"id": 8, "contact_id": 32, "status": "active"},
            },
            "matches": {
                11: {"id": 11, "buy_request_id": 7, "property_id": 21},
                12: {"id": 12, "buy_request_id": 8, "property_id": 21},
                13: {"id": 13, "buy_request_id": 8, "property_id": 22},
                14: {"id": 14, "buy_request_id": 7, "property_id": 23},
                15: {"id": 15, "buy_request_id": 8, "property_id": 24},
            },
            "proposals": {
                101: {"id": 101, "match_id": 11, "status": "accepted", "amount": Decimal("185000.00")},
                102: {"id": 102, "match_id": 12, "status": "draft", "amount": Decimal("180000.00")},
                103: {"id": 103, "match_id": 13, "status": "accepted", "amount": Decimal("220000.00")},
                104: {"id": 104, "match_id": 14, "status": "accepted", "amount": Decimal("300000.00")},
                106: {"id": 106, "match_id": 15, "status": "accepted", "amount": Decimal("410000.00")},
            },
            "property_contacts": [
                {"property_id": 21, "contact_id": 50, "role": "owner", "ownership_share": Decimal("60.00")},
                {"property_id": 21, "contact_id": 50, "role": "seller", "ownership_share": None},
                {"property_id": 21, "contact_id": 51, "role": "owner", "ownership_share": Decimal("40.00")},
                {"property_id": 22, "contact_id": 51, "role": "tenant", "ownership_share": None},
                {"property_id": 23, "contact_id": 50, "role": "owner", "ownership_share": Decimal("100.00")},
                {"property_id": 24, "contact_id": 51, "role": "owner", "ownership_share": Decimal("100.00")},
            ],
            "sales": [],
            "sale_sellers": [],
            "history": [],
            "status_history": [],
        }
        self.fail_history = fail_history
        self.transactions = 0
        self.commits = 0
        self.rollbacks = 0
        self.queries = []
        self._next_sale_id = 901
        self._next_seller_id = 1

    @contextmanager
    def cursor(self, commit=False):
        self.transactions += 1
        staged = copy.deepcopy(self.state)
        cursor = SaleCursor(self, staged)
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


class SaleCursor:
    def __init__(self, database, staged):
        self.database = database
        self.state = staged
        self.result = None
        self.rows = []

    def _joined_sale(self, sale):
        proposal = self.state["proposals"][sale["proposal_id"]]
        prop = self.state["properties"][sale["property_id"]]
        buy = self.state["buy_requests"][sale["buy_request_id"]]
        return {
            **copy.deepcopy(sale),
            "property_title": prop["title"],
            "property_code": prop["code"],
            "buy_title": buy.get("title"),
            "contact_id": buy["contact_id"],
            "proposal_amount": proposal["amount"],
            "proposal_status": proposal["status"],
        }

    def execute(self, query, params=()):
        normalized = " ".join(query.split())
        lowered = normalized.lower()
        params = tuple(params)
        self.database.queries.append((normalized, params))
        self.result = None
        self.rows = []

        if (
            "select pp.id,pp.status,pp.amount,pp.match_id" in lowered
            and "from property_proposals pp" in lowered
            and "join matches m" in lowered
            and "where pp.id=%s" in lowered
        ):
            proposal = self.state["proposals"].get(params[0])
            if not proposal:
                self.result = None
                return
            match = self.state["matches"][proposal["match_id"]]
            self.result = {
                "id": proposal["id"],
                "status": proposal["status"],
                "amount": proposal["amount"],
                "match_id": proposal["match_id"],
                "property_id": match["property_id"],
                "buy_request_id": match["buy_request_id"],
            }
            return

        if lowered.startswith("select ps.*") and "from property_sales ps" in lowered and "where ps.id=%s" in lowered:
            sale = next((s for s in self.state["sales"] if s["id"] == params[0]), None)
            self.result = self._joined_sale(sale) if sale else None
            return

        if lowered.startswith("select ps.*") and "order by ps.created_at desc" in lowered:
            values = list(self.state["sales"])
            pos = 0
            if "ps.status=%s" in lowered:
                values = [s for s in values if s["status"] == params[pos]]
                pos += 1
            if "ps.property_id=%s" in lowered:
                values = [s for s in values if s["property_id"] == params[pos]]
                pos += 1
            if "ps.buy_request_id=%s" in lowered:
                values = [s for s in values if s["buy_request_id"] == params[pos]]
                pos += 1
            self.rows = [self._joined_sale(s) for s in values]
            return

        if lowered.startswith("select * from property_sale_sellers where sale_id=%s"):
            self.rows = [
                copy.deepcopy(s)
                for s in sorted(self.state["sale_sellers"], key=lambda x: x["id"])
                if s["sale_id"] == params[0]
            ]
            return

        if lowered.startswith("select * from property_sales where id=%s"):
            sale = next((s for s in self.state["sales"] if s["id"] == params[0]), None)
            self.result = copy.deepcopy(sale)
            return

        if lowered.startswith("select * from property_sales where idempotency_key=%s"):
            item = next((s for s in self.state["sales"] if s["idempotency_key"] == params[0]), None)
            self.result = copy.deepcopy(item)
            return

        if lowered.startswith("select id from property_sales where proposal_id=%s"):
            item = next(
                (
                    s
                    for s in self.state["sales"]
                    if s["proposal_id"] == params[0] and s["status"] in {"pending", "completed"}
                ),
                None,
            )
            self.result = {"id": item["id"]} if item else None
            return

        if lowered.startswith("select id from property_sales where property_id=%s"):
            item = next(
                (
                    s
                    for s in self.state["sales"]
                    if s["property_id"] == params[0] and s["status"] in {"pending", "completed"}
                ),
                None,
            )
            self.result = {"id": item["id"]} if item else None
            return

        if lowered.startswith("select contact_id,role,ownership_share from property_contacts"):
            rows = [
                pc
                for pc in self.state["property_contacts"]
                if pc["property_id"] == params[0] and pc["role"] in {"owner", "seller"}
            ]
            rows = sorted(rows, key=lambda pc: (pc["contact_id"], pc["role"]))
            self.rows = [copy.deepcopy(r) for r in rows]
            return

        if lowered.startswith("insert into property_sales("):
            columns = re.search(r"property_sales\s*\(([^)]+)\)", query, re.IGNORECASE)
            assert columns, query
            names = [name.strip() for name in columns.group(1).split(",")]
            candidate = dict(zip(names, params))
            existing = next(
                (s for s in self.state["sales"] if s["idempotency_key"] == candidate["idempotency_key"]),
                None,
            )
            if existing and "on conflict" in lowered:
                self.result = None
                return
            candidate.update(
                {
                    "id": self.database._next_sale_id,
                    "status": "pending",
                    "completed_by": None,
                    "cancelled_by": None,
                    "completed_at": None,
                    "cancelled_at": None,
                    "created_at": NOW,
                    "updated_at": NOW,
                }
            )
            self.database._next_sale_id += 1
            self.state["sales"].append(candidate)
            self.result = copy.deepcopy(candidate)
            return

        if lowered.startswith("insert into property_sale_sellers("):
            columns = re.search(r"property_sale_sellers\s*\(([^)]+)\)", query, re.IGNORECASE)
            assert columns, query
            names = [name.strip() for name in columns.group(1).split(",")]
            candidate = dict(zip(names, params))
            candidate["id"] = self.database._next_seller_id
            self.database._next_seller_id += 1
            self.state["sale_sellers"].append(candidate)
            self.result = None
            return

        if lowered.startswith("update property_sales set"):
            sale_id = params[-1]
            item = next(s for s in self.state["sales"] if s["id"] == sale_id)
            body = normalized[normalized.lower().index("set") + 3 : normalized.lower().index("where")]
            assignments = [a.strip() for a in body.split(",")]
            value_index = 0
            for assignment in assignments:
                field, _, rhs = assignment.partition("=")
                field = field.strip()
                rhs = rhs.strip()
                if rhs == "%s":
                    item[field] = params[value_index]
                    value_index += 1
                elif rhs.lower() == "now()":
                    item[field] = NOW
                else:
                    item[field] = rhs.strip("'")
            self.result = copy.deepcopy(item) if "returning" in lowered else None
            return

        if lowered.startswith("select id,commercial_status from properties where id=%s"):
            self.result = copy.deepcopy(self.state["properties"].get(params[0]))
            return

        if lowered.startswith("select id,status from buy_requests where id=%s"):
            self.result = copy.deepcopy(self.state["buy_requests"].get(params[0]))
            return

        if lowered.startswith("select id,status,match_id from property_proposals where id=%s"):
            self.result = copy.deepcopy(self.state["proposals"].get(params[0]))
            return

        if lowered.startswith("select id,property_id,buy_request_id from matches where id=%s"):
            self.result = copy.deepcopy(self.state["matches"].get(params[0]))
            return

        if lowered.startswith("update properties set commercial_status='sold'"):
            item = self.state["properties"][params[0]]
            item["commercial_status"] = "sold"
            self.result = None
            return

        if lowered.startswith("insert into property_status_history("):
            self.state["status_history"].append(
                {
                    "property_id": params[0],
                    "field_name": "commercial_status",
                    "old_value": params[1],
                    "new_value": "sold",
                    "note": params[2],
                    "changed_by": params[3],
                }
            )
            self.result = None
            return

        if lowered.startswith("update buy_requests set status='satisfied'"):
            self.state["buy_requests"][params[0]]["status"] = "satisfied"
            self.result = None
            return

        if lowered.startswith("insert into buy_request_history"):
            if self.database.fail_history:
                raise RuntimeError("history insert failed")
            fields = (
                "buy_request_id", "event_type", "match_id", "property_id", "task_id",
                "reason_code", "description", "old_value", "new_value", "created_by",
            )
            item = dict(zip(fields, params))
            self.state["history"].append(item)
            self.result = None
            return

        raise AssertionError(f"Query non prevista: {normalized}")

    def fetchone(self):
        return copy.deepcopy(self.result)

    def fetchall(self):
        return copy.deepcopy(self.rows)


def make_accepted_sale(repository, database, *, proposal_id=101, price=None, key=KEY_1):
    return repository.create_sale(create_payload(proposal_id=proposal_id, sale_price=price, key=key), "giorgio")


# ---------------------------------------------------------------------------
# MIGRATION
# ---------------------------------------------------------------------------

def test_migration_up_creates_only_sale_domain_and_extends_buy_history_events():
    assert UP.exists(), "migration up P10 mancante"
    assert DOWN.exists(), "migration down P10 mancante"
    up = UP.read_text(encoding="utf-8")
    down = DOWN.read_text(encoding="utf-8")

    assert re.search(r"CREATE TABLE\s+property_sales", up, re.IGNORECASE)
    assert "property_id BIGINT NOT NULL REFERENCES properties(id) ON DELETE RESTRICT" in up
    assert "buy_request_id BIGINT NOT NULL REFERENCES buy_requests(id) ON DELETE RESTRICT" in up
    assert "proposal_id BIGINT NOT NULL REFERENCES property_proposals(id) ON DELETE RESTRICT" in up
    assert re.search(r"sale_price\s+NUMERIC\(14,2\)\s+NOT NULL\s+CHECK\s*\(sale_price\s*>\s*0\)", up, re.IGNORECASE)
    assert "DEFAULT" not in re.search(r"sale_price[^,]+", up, re.IGNORECASE).group(0)
    assert set(re.findall(r"'(pending|completed|cancelled)'", up)) == {"pending", "completed", "cancelled"}
    assert "idempotency_key UUID NOT NULL UNIQUE" in up
    assert re.search(r"UNIQUE INDEX uq_property_sales_proposal_active[\s\S]+WHERE status IN \('pending','completed'\)", up)
    assert re.search(r"UNIQUE INDEX uq_property_sales_property_active[\s\S]+WHERE status IN \('pending','completed'\)", up)

    assert re.search(r"CREATE TABLE\s+property_sale_sellers", up, re.IGNORECASE)
    assert "contact_id BIGINT NOT NULL REFERENCES contacts(id) ON DELETE RESTRICT" in up
    assert re.search(r"role\s+VARCHAR\(30\)\s+NOT NULL\s+CHECK\s*\(role IN \('owner','seller'\)\)", up, re.IGNORECASE)
    assert "UNIQUE(sale_id, contact_id, role)" in up

    assert "sale_completed" in up
    for forbidden in ("ALTER TABLE matches", "ALTER TABLE properties ADD", "ALTER TABLE property_proposals ADD"):
        assert forbidden not in up

    assert "RAISE EXCEPTION" in down
    assert "sale_completed" in down
    assert re.search(r"DROP TABLE(?: IF EXISTS)? property_sale_sellers", down, re.IGNORECASE)
    assert re.search(r"DROP TABLE(?: IF EXISTS)? property_sales", down, re.IGNORECASE)


# ---------------------------------------------------------------------------
# SCHEMAS / ACTOR (server-derived fields can never be accepted from payload)
# ---------------------------------------------------------------------------

def test_schema_requires_positive_price_when_given_and_valid_proposal_and_key():
    schemas = sale_module("schemas")
    valid = schemas.SaleCreate(**create_payload())
    assert valid.proposal_id == 101
    assert valid.sale_price is None

    with pytest.raises(PydanticValidationError, match="sale_price"):
        schemas.SaleCreate(**{**create_payload(), "sale_price": 0})
    with pytest.raises(PydanticValidationError, match="proposal_id"):
        schemas.SaleCreate(**{k: v for k, v in create_payload().items() if k != "proposal_id"})
    with pytest.raises(PydanticValidationError, match="idempotency_key"):
        schemas.SaleCreate(**{**create_payload(), "idempotency_key": "not-a-uuid"})


@pytest.mark.parametrize(
    "forbidden",
    ["created_by", "completed_by", "cancelled_by", "status", "property_id", "buy_request_id"],
)
def test_actor_and_relation_fields_can_never_be_supplied_by_the_caller(forbidden):
    schemas = sale_module("schemas")
    with pytest.raises(PydanticValidationError):
        schemas.SaleCreate(**create_payload(), **{forbidden: 999 if forbidden != "status" else "completed"})
    if forbidden != "proposal_id":
        with pytest.raises(PydanticValidationError):
            schemas.SaleUpdate(**{forbidden: 999 if forbidden != "status" else "completed"})


def test_update_schema_cannot_null_sale_price_but_can_null_notes():
    schemas = sale_module("schemas")
    with pytest.raises(PydanticValidationError, match="sale_price"):
        schemas.SaleUpdate(sale_price=None)
    updated = schemas.SaleUpdate(notes=None)
    assert updated.notes is None


def test_complete_and_cancel_have_no_body_schemas():
    schemas = sale_module("schemas")
    assert not hasattr(schemas, "SaleComplete")
    assert not hasattr(schemas, "SaleCancel")


# ---------------------------------------------------------------------------
# CREATE
# ---------------------------------------------------------------------------

def test_create_derives_relations_and_snapshots_every_eligible_owner_and_seller_row(monkeypatch):
    repository = sale_module("repository")
    database = SaleDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)

    result = repository.create_sale(create_payload(), "giorgio")

    assert database.transactions == database.commits == 1
    assert database.rollbacks == 0
    assert result["status"] == "pending"
    assert result["property_id"] == 21
    assert result["buy_request_id"] == 7
    assert result["proposal_id"] == 101
    assert result["created_by"] == "giorgio"
    assert result["sale_price"] == Decimal("185000.00")
    sellers = {(s["contact_id"], s["role"]) for s in result["sellers"]}
    assert sellers == {(50, "owner"), (50, "seller"), (51, "owner")}


def test_create_uses_proposal_amount_when_sale_price_is_omitted(monkeypatch):
    repository = sale_module("repository")
    database = SaleDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)

    result = repository.create_sale(create_payload(sale_price=None), "giorgio")
    assert result["sale_price"] == Decimal("185000.00")

    explicit = repository.create_sale(
        create_payload(proposal_id=106, key=KEY_2, sale_price=Decimal("215000.00")), "giorgio"
    )
    assert explicit["sale_price"] == Decimal("215000.00")


def test_create_requires_an_accepted_proposal(monkeypatch):
    repository = sale_module("repository")
    database = SaleDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)

    with pytest.raises(ConflictError, match="accepted"):
        repository.create_sale(create_payload(proposal_id=102), "giorgio")
    assert database.state["sales"] == []


def test_create_rejects_property_with_no_eligible_owner_or_seller(monkeypatch):
    repository = sale_module("repository")
    database = SaleDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)

    with pytest.raises(ConflictError, match="no eligible seller/owner"):
        repository.create_sale(create_payload(proposal_id=103), "giorgio")
    assert database.state["sales"] == []


def test_create_locks_proposal_before_reading_cardinality_and_sellers(monkeypatch):
    repository = sale_module("repository")
    database = SaleDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)

    repository.create_sale(create_payload(), "giorgio")

    proposal_lock_index = next(
        i for i, (q, _) in enumerate(database.queries) if "from property_proposals pp" in q.lower() and "for update" in q.lower()
    )
    insert_index = next(i for i, (q, _) in enumerate(database.queries) if q.lower().startswith("insert into property_sales("))
    assert proposal_lock_index < insert_index


# ---------------------------------------------------------------------------
# IDEMPOTENCY
# ---------------------------------------------------------------------------

def test_idempotency_returns_same_sale_for_same_effective_payload_and_conflicts_otherwise(monkeypatch):
    repository = sale_module("repository")
    database = SaleDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)

    first = repository.create_sale(create_payload(sale_price=Decimal("185000.00")), "giorgio")
    second = repository.create_sale(create_payload(sale_price=Decimal("185000.00")), "giorgio")
    assert second["id"] == first["id"]
    assert len(database.state["sales"]) == 1

    third = repository.create_sale(create_payload(sale_price=None), "giorgio")
    assert third["id"] == first["id"], "sale_price omitted must normalize to the same effective proposal amount"

    with pytest.raises(ConflictError, match="idempotency"):
        repository.create_sale(create_payload(notes="different notes"), "giorgio")


# ---------------------------------------------------------------------------
# CARDINALITY
# ---------------------------------------------------------------------------

def test_cardinality_blocks_second_active_sale_for_same_proposal(monkeypatch):
    repository = sale_module("repository")
    database = SaleDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    repository.create_sale(create_payload(), "giorgio")

    with pytest.raises(ConflictError, match="proposal"):
        repository.create_sale(create_payload(key=KEY_2), "giorgio")


def test_cardinality_blocks_second_active_sale_for_same_property_even_via_a_different_proposal(monkeypatch):
    repository = sale_module("repository")
    database = SaleDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    database.state["proposals"][105] = {"id": 105, "match_id": 12, "status": "accepted", "amount": Decimal("190000.00")}
    repository.create_sale(create_payload(proposal_id=101), "giorgio")

    with pytest.raises(ConflictError, match="property"):
        repository.create_sale(create_payload(proposal_id=105, key=KEY_2), "giorgio")


def test_cardinality_allows_a_new_sale_on_the_same_property_after_the_first_is_cancelled(monkeypatch):
    repository = sale_module("repository")
    database = SaleDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    database.state["proposals"][105] = {"id": 105, "match_id": 12, "status": "accepted", "amount": Decimal("190000.00")}
    first = repository.create_sale(create_payload(proposal_id=101), "giorgio")
    repository.cancel_sale(first["id"], "giorgio")

    second = repository.create_sale(create_payload(proposal_id=105, key=KEY_2), "giorgio")
    assert second["id"] != first["id"]
    assert {s["status"] for s in database.state["sales"]} == {"cancelled", "pending"}


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------

def test_patch_updates_only_price_and_notes_while_pending(monkeypatch):
    repository = sale_module("repository")
    database = SaleDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    created = repository.create_sale(create_payload(), "giorgio")

    updated = repository.update_sale(created["id"], {"sale_price": Decimal("190000.00"), "notes": None})
    assert updated["sale_price"] == Decimal("190000.00")
    assert updated["notes"] is None
    assert database.state["history"] == []
    assert database.state["status_history"] == []


def test_patch_is_rejected_once_a_sale_is_no_longer_pending(monkeypatch):
    repository = sale_module("repository")
    database = SaleDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    created = repository.create_sale(create_payload(), "giorgio")
    repository.cancel_sale(created["id"], "giorgio")

    with pytest.raises(ConflictError, match="pending"):
        repository.update_sale(created["id"], {"notes": "troppo tardi"})


# ---------------------------------------------------------------------------
# COMPLETE
# ---------------------------------------------------------------------------

def test_complete_marks_property_sold_buy_request_satisfied_and_writes_exactly_one_history_event_each(monkeypatch):
    repository = sale_module("repository")
    database = SaleDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    created = repository.create_sale(create_payload(), "giorgio")

    completed = repository.complete_sale(created["id"], "giorgio")

    assert completed["status"] == "completed"
    assert completed["completed_by"] == "giorgio"
    assert database.state["properties"][21]["commercial_status"] == "sold"
    assert database.state["buy_requests"][7]["status"] == "satisfied"
    assert len(database.state["status_history"]) == 1
    assert database.state["status_history"][0]["new_value"] == "sold"
    assert len(database.state["history"]) == 1
    assert database.state["history"][0]["event_type"] == "sale_completed"
    assert database.state["history"][0]["buy_request_id"] == 7
    assert database.state["history"][0]["property_id"] == 21


def test_complete_on_a_property_already_sold_skips_the_redundant_status_history_write(monkeypatch):
    repository = sale_module("repository")
    database = SaleDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    created = repository.create_sale(create_payload(proposal_id=104), "giorgio")  # property 23 already 'sold'

    repository.complete_sale(created["id"], "giorgio")

    assert database.state["properties"][23]["commercial_status"] == "sold"
    assert database.state["status_history"] == []
    assert len(database.state["history"]) == 1


def test_complete_is_idempotent_on_retry_with_no_additional_writes(monkeypatch):
    repository = sale_module("repository")
    database = SaleDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    created = repository.create_sale(create_payload(), "giorgio")
    repository.complete_sale(created["id"], "giorgio")

    again = repository.complete_sale(created["id"], "giorgio")

    assert again["status"] == "completed"
    assert len(database.state["status_history"]) == 1
    assert len(database.state["history"]) == 1


def test_complete_rejects_a_cancelled_sale(monkeypatch):
    repository = sale_module("repository")
    database = SaleDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    created = repository.create_sale(create_payload(), "giorgio")
    repository.cancel_sale(created["id"], "giorgio")

    with pytest.raises(ConflictError, match="cancelled"):
        repository.complete_sale(created["id"], "giorgio")


# ---------------------------------------------------------------------------
# ROLLBACK (relation-chain consistency)
# ---------------------------------------------------------------------------

def test_complete_rejects_when_proposal_is_no_longer_accepted_with_full_rollback(monkeypatch):
    repository = sale_module("repository")
    database = SaleDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    created = repository.create_sale(create_payload(), "giorgio")
    database.state["proposals"][101]["status"] = "withdrawn"
    snapshot = copy.deepcopy(database.state)

    with pytest.raises(ConflictError, match="no longer accepted"):
        repository.complete_sale(created["id"], "giorgio")

    assert database.state == snapshot
    assert database.rollbacks == 1


def test_complete_rejects_relation_chain_mismatch_between_sale_proposal_and_match_with_full_rollback(monkeypatch):
    repository = sale_module("repository")
    database = SaleDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    created = repository.create_sale(create_payload(), "giorgio")
    # simulate the match having been repointed to a different property after the sale was created
    database.state["matches"][11]["property_id"] = 22
    snapshot = copy.deepcopy(database.state)

    with pytest.raises(ConflictError, match="relation chain mismatch"):
        repository.complete_sale(created["id"], "giorgio")

    assert database.state == snapshot
    assert database.rollbacks == 1


def test_history_failure_rolls_back_every_write_of_complete(monkeypatch):
    repository = sale_module("repository")
    database = SaleDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    created = repository.create_sale(create_payload(), "giorgio")
    database.fail_history = True
    snapshot = copy.deepcopy(database.state)

    with pytest.raises(RuntimeError, match="history insert failed"):
        repository.complete_sale(created["id"], "giorgio")

    assert database.state == snapshot
    assert database.state["properties"][21]["commercial_status"] != "sold"
    assert database.state["buy_requests"][7]["status"] != "satisfied"


# ---------------------------------------------------------------------------
# CANCEL
# ---------------------------------------------------------------------------

def test_cancel_is_pending_only_and_touches_nothing_else(monkeypatch):
    repository = sale_module("repository")
    database = SaleDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    created = repository.create_sale(create_payload(), "giorgio")

    cancelled = repository.cancel_sale(created["id"], "giorgio")

    assert cancelled["status"] == "cancelled"
    assert cancelled["cancelled_by"] == "giorgio"
    assert database.state["properties"][21]["commercial_status"] == "under_offer"
    assert database.state["buy_requests"][7]["status"] == "active"
    assert database.state["history"] == []
    assert database.state["status_history"] == []


def test_cancel_is_idempotent_on_retry(monkeypatch):
    repository = sale_module("repository")
    database = SaleDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    created = repository.create_sale(create_payload(), "giorgio")
    repository.cancel_sale(created["id"], "giorgio")

    again = repository.cancel_sale(created["id"], "giorgio")
    assert again["status"] == "cancelled"


def test_cancel_rejects_a_completed_sale(monkeypatch):
    repository = sale_module("repository")
    database = SaleDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    created = repository.create_sale(create_payload(), "giorgio")
    repository.complete_sale(created["id"], "giorgio")

    with pytest.raises(ConflictError, match="completed"):
        repository.cancel_sale(created["id"], "giorgio")


# ---------------------------------------------------------------------------
# READ
# ---------------------------------------------------------------------------

def test_list_sales_filters_by_status_property_and_buy_request(monkeypatch):
    repository = sale_module("repository")
    database = SaleDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    database.state["proposals"][105] = {"id": 105, "match_id": 13, "status": "accepted", "amount": Decimal("1.00")}
    first = repository.create_sale(create_payload(proposal_id=101), "giorgio")
    repository.cancel_sale(first["id"], "giorgio")

    assert len(repository.list_sales(status="cancelled")) == 1
    assert len(repository.list_sales(property_id=21)) == 1
    assert len(repository.list_sales(buy_request_id=7)) == 1
    assert repository.list_sales(buy_request_id=8) == []


def test_get_sale_returns_the_seller_snapshot(monkeypatch):
    repository = sale_module("repository")
    database = SaleDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    created = repository.create_sale(create_payload(), "giorgio")

    fetched = repository.get_sale(created["id"])
    assert len(fetched["sellers"]) == 3

    with pytest.raises(NotFoundError):
        repository.get_sale(999999)


# ---------------------------------------------------------------------------
# AUTH
# ---------------------------------------------------------------------------

def test_router_requires_admin_identity_for_every_route_including_reads(monkeypatch):
    service = sale_module("service")
    captured = {}

    def create(model, actor):
        captured.update({"actor": actor})
        return {"id": 1, "status": "pending"}

    monkeypatch.setattr(service, "create_sale", create)
    monkeypatch.setattr(service, "list_sales", lambda **_: [])
    monkeypatch.setattr(service, "get_sale", lambda sale_id: {"id": sale_id, "status": "pending"})
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    app = import_main_app()
    client = TestClient(app, raise_server_exceptions=False)

    # P10 contract: the whole /api/sales router is registered with
    # dependencies=[Depends(require_admin)] in main.py, so EVERY route,
    # GET included, requires admin credentials -- not only the writes.
    body = {**create_payload(), "idempotency_key": str(KEY_1)}
    anonymous_create = client.post("/api/sales", json=body)
    assert anonymous_create.status_code == 401

    anonymous_list = client.get("/api/sales")
    assert anonymous_list.status_code == 401

    anonymous_get = client.get("/api/sales/1")
    assert anonymous_get.status_code == 401

    authorized_create = client.post("/api/sales", json=body, auth=("giorgio", "test-secret"))
    assert authorized_create.status_code == 201
    assert captured["actor"] == "giorgio"

    authorized_list = client.get("/api/sales", auth=("giorgio", "test-secret"))
    assert authorized_list.status_code == 200

    authorized_get = client.get("/api/sales/1", auth=("giorgio", "test-secret"))
    assert authorized_get.status_code == 200

    operation = app.openapi()["paths"]["/api/sales/{sale_id}/complete"]["post"]
    assert operation.get("security")


def test_router_translates_domain_errors_to_the_expected_http_status(monkeypatch):
    service = sale_module("service")
    router_module = sale_module("router")
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    app = import_main_app()
    client = TestClient(app, raise_server_exceptions=False)

    monkeypatch.setattr(
        service,
        "complete_sale",
        lambda *_args: (_ for _ in ()).throw(router_module.ConflictError("sale is cancelled")),
    )
    conflict = client.post("/api/sales/1/complete", auth=("giorgio", "test-secret"))
    assert conflict.status_code == 409

    monkeypatch.setattr(
        service,
        "get_sale",
        lambda *_args: (_ for _ in ()).throw(router_module.NotFoundError("sale 1 not found")),
    )
    missing = client.get("/api/sales/1", auth=("giorgio", "test-secret"))
    assert missing.status_code == 404


# ---------------------------------------------------------------------------
# REGRESSION
# ---------------------------------------------------------------------------

def test_p10_sale_module_is_not_imported_by_any_p0_p9_module():
    for directory in ("match", "flow", "owner", "core", "property", "buy", "crm"):
        for path in (ROOT / directory).glob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert not re.search(r"(?:from|import)\s+sale\b", source), f"{path} importa sale/"


def test_p10_repository_never_calls_into_another_domains_module():
    source = (ROOT / "sale/repository.py").read_text(encoding="utf-8")
    for forbidden in ("proposal.repository", "property.repository", "match.", "flow.", "owner."):
        assert forbidden not in source
    assert "from buy.repository import history" in source


def test_p10_router_is_registered_with_the_shared_admin_dependency():
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "from sale.router import router as sale_router" in main_source
    assert "app.include_router(sale_router, dependencies=[Depends(require_admin)])" in main_source
