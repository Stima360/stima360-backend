from __future__ import annotations

import asyncio
import copy
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from core import repository, service
from integration_p2_support import import_project_module


class BridgeCursor:
    def __init__(self, database):
        self.database = database
        self.rows = []
        self.rowcount = 0

    def execute(self, query, params=None):
        sql = " ".join(str(query).split()).lower()
        self.database.sql.append((sql, params))
        self.rows = []
        self.rowcount = 0

        if "pg_advisory_xact_lock" in sql:
            self.rows = [{"locked": True}]
            return

        if "from lead_stime ls" in sql:
            stima_id = int(params[0])
            links = sorted(
                (item for item in self.database.links if item["stima_id"] == stima_id),
                key=lambda item: item["id"],
            )
            if links:
                link = links[0]
                lead = next(item for item in self.database.leads if item["id"] == link["lead_id"])
                self.rows = [{"lead_id": lead["id"], "contact_id": lead["contact_id"]}]
            return

        if "from contacts" in sql and "email_normalized" in sql:
            value = params[0]
            self.rows = [
                copy.deepcopy(item)
                for item in sorted(self.database.contacts, key=lambda item: item["id"])
                if item.get("email_normalized") == value
            ]
            return

        if "from contacts" in sql and "phone_normalized" in sql:
            value = params[0]
            self.rows = [
                copy.deepcopy(item)
                for item in sorted(self.database.contacts, key=lambda item: item["id"])
                if item.get("phone_normalized") == value
            ]
            return

        if "insert into contacts" in sql:
            if self.database.fail_on == "contact":
                raise RuntimeError("controlled contact failure")
            item = {**copy.deepcopy(params), "id": self.database.next_contact_id}
            item.setdefault("archived_at", None)
            self.database.next_contact_id += 1
            self.database.contacts.append(item)
            self.rows = [copy.deepcopy(item)]
            self.rowcount = 1
            return

        if "insert into leads" in sql:
            if self.database.fail_on == "lead":
                raise RuntimeError("controlled lead failure")
            item = {**copy.deepcopy(params), "id": self.database.next_lead_id}
            self.database.next_lead_id += 1
            self.database.leads.append(item)
            self.rows = [copy.deepcopy(item)]
            self.rowcount = 1
            return

        if "insert into lead_stime" in sql:
            if self.database.fail_on == "link":
                raise RuntimeError("controlled link failure")
            lead_id, stima_id, relation_type = params
            existing = next(
                (
                    item
                    for item in self.database.links
                    if item["lead_id"] == lead_id and item["stima_id"] == stima_id
                ),
                None,
            )
            if existing is None:
                existing = {
                    "id": self.database.next_link_id,
                    "lead_id": lead_id,
                    "stima_id": stima_id,
                    "relation_type": relation_type,
                }
                self.database.next_link_id += 1
                self.database.links.append(existing)
                self.rowcount = 1
            self.rows = [copy.deepcopy(existing)]
            return

        raise AssertionError(f"unexpected bridge SQL: {sql}")

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class BridgeDatabase:
    def __init__(self):
        self.contacts = []
        self.leads = []
        self.links = []
        self.next_contact_id = 1
        self.next_lead_id = 1
        self.next_link_id = 1
        self.fail_on = None
        self.sql = []
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def cursor(self, *, commit=False):
        snapshot = copy.deepcopy(
            (
                self.contacts,
                self.leads,
                self.links,
                self.next_contact_id,
                self.next_lead_id,
                self.next_link_id,
            )
        )
        try:
            yield self, BridgeCursor(self)
            if commit:
                self.commits += 1
        except Exception:
            (
                self.contacts,
                self.leads,
                self.links,
                self.next_contact_id,
                self.next_lead_id,
                self.next_link_id,
            ) = snapshot
            self.rollbacks += 1
            raise

    def add_contact(
        self,
        *,
        email=None,
        phone=None,
        status="active",
        archived_at=None,
        display_name="Existing Contact",
    ):
        item = {
            "id": self.next_contact_id,
            "contact_type": "person",
            "display_name": display_name,
            "email_normalized": email,
            "phone_normalized": phone,
            "status": status,
            "archived_at": archived_at,
        }
        self.next_contact_id += 1
        self.contacts.append(item)
        return item


@pytest.fixture
def bridge_database(monkeypatch):
    database = BridgeDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    return database


def bridge(stima_id, **overrides):
    data = {
        "first_name": "Mario",
        "last_name": "Rossi",
        "email": " Mario@Example.COM ",
        "phone": "+39 333 123 4567",
        "marketing_consent": True,
        "marketing_consent_at": datetime(2026, 8, 29, 8, 0, tzinfo=timezone.utc),
    }
    data.update(overrides)
    return service.bridge_public_stima(stima_id, **data)


def test_new_stima_creates_contact_lead_and_link_with_approved_defaults(bridge_database):
    result = bridge(501)

    assert result == {
        "status": "linked",
        "stima_id": 501,
        "contact_id": 1,
        "lead_id": 1,
        "contact_created": True,
        "lead_created": True,
    }
    assert len(bridge_database.contacts) == 1
    assert bridge_database.contacts[0]["email_normalized"] == "mario@example.com"
    assert bridge_database.contacts[0]["phone_normalized"] == "393331234567"
    assert bridge_database.contacts[0]["source"] == "public_stima"
    assert bridge_database.leads == [
        {
            "id": 1,
            "contact_id": 1,
            "source": "public_stima",
            "pipeline": "general",
            "stage": "new",
            "priority": "normal",
            "status": "open",
            "assigned_to": None,
            "estimated_value": None,
            "next_action_at": None,
            "lost_reason": None,
            "notes": None,
        }
    ]
    assert bridge_database.links == [
        {"id": 1, "lead_id": 1, "stima_id": 501, "relation_type": "related"}
    ]


def test_same_email_reuses_contact_but_different_stime_create_distinct_leads(bridge_database):
    existing = bridge_database.add_contact(
        email="mario@example.com",
        phone="393331234567",
    )

    first = bridge(501)
    second = bridge(502)

    assert first["contact_id"] == second["contact_id"] == existing["id"]
    assert first["lead_id"] != second["lead_id"]
    assert len(bridge_database.contacts) == 1
    assert len(bridge_database.leads) == 2
    assert {item["stima_id"] for item in bridge_database.links} == {501, 502}


def test_phone_is_fallback_when_email_is_absent(bridge_database):
    existing = bridge_database.add_contact(phone="393331234567")

    result = bridge(501, email=None)

    assert result["contact_id"] == existing["id"]
    assert result["contact_created"] is False
    assert len(bridge_database.contacts) == 1
    assert len(bridge_database.leads) == 1


def test_same_stima_retry_returns_existing_lead_without_duplicate_writes(bridge_database):
    first = bridge(501)
    second = bridge(501)

    assert second == {
        "status": "already_linked",
        "stima_id": 501,
        "contact_id": first["contact_id"],
        "lead_id": first["lead_id"],
        "contact_created": False,
        "lead_created": False,
    }
    assert len(bridge_database.contacts) == 1
    assert len(bridge_database.leads) == 1
    assert len(bridge_database.links) == 1


def test_different_email_and_phone_contacts_return_conflict_without_writes(bridge_database):
    bridge_database.add_contact(email="mario@example.com", phone="393330000001")
    bridge_database.add_contact(email="other@example.com", phone="393331234567")

    result = bridge(501)

    assert result == {
        "status": "conflict",
        "reason": "identity_conflict",
        "stima_id": 501,
        "contact_id": None,
        "lead_id": None,
        "contact_created": False,
        "lead_created": False,
    }
    assert len(bridge_database.contacts) == 2
    assert bridge_database.leads == []
    assert bridge_database.links == []


@pytest.mark.parametrize(
    "contact_values",
    (
        {"status": "archived"},
        {"archived_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
    ),
)
def test_archived_contact_is_not_reactivated_or_duplicated(bridge_database, contact_values):
    archived = bridge_database.add_contact(email="mario@example.com", **contact_values)

    result = bridge(501)

    assert result["status"] == "skipped"
    assert result["reason"] == "archived_contact"
    assert result["contact_id"] == archived["id"]
    assert len(bridge_database.contacts) == 1
    assert bridge_database.contacts[0]["status"] == archived["status"]
    assert bridge_database.leads == []
    assert bridge_database.links == []


def test_insufficient_identity_skips_without_placeholders(bridge_database):
    result = bridge(
        501,
        first_name=None,
        last_name=" ",
        email="missing@example.com",
        phone=None,
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "insufficient_contact_identity"
    assert bridge_database.contacts == []
    assert bridge_database.leads == []
    assert bridge_database.links == []


def test_failure_after_contact_insert_rolls_back_and_retry_does_not_duplicate(bridge_database):
    bridge_database.fail_on = "lead"
    with pytest.raises(RuntimeError, match="controlled lead failure"):
        bridge(501)

    assert bridge_database.contacts == []
    assert bridge_database.leads == []
    assert bridge_database.links == []
    assert bridge_database.rollbacks == 1

    bridge_database.fail_on = None
    result = bridge(501)

    assert result["status"] == "linked"
    assert len(bridge_database.contacts) == 1
    assert len(bridge_database.leads) == 1
    assert len(bridge_database.links) == 1


class LegacyCursor:
    def __init__(self, connection):
        self.connection = connection
        self.current = None

    def execute(self, query, params=None):
        self.connection.executions.append((" ".join(query.split()), params))
        if "INSERT INTO stime" in query:
            self.current = (501,)
        else:
            self.current = None

    def fetchone(self):
        return self.current

    def close(self):
        self.connection.closed_cursor = True


class LegacyConnection:
    def __init__(self):
        self.executions = []
        self.commit_count = 0
        self.closed_cursor = False
        self.closed = False

    def cursor(self, **_kwargs):
        return LegacyCursor(self)

    def commit(self):
        self.commit_count += 1

    def close(self):
        self.closed = True


class JsonRequest:
    headers = {"content-type": "application/json"}

    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


def test_bridge_failure_preserves_public_response_and_pdf_email_whatsapp_flow(monkeypatch, caplog):
    main_module = import_project_module("main")
    connections = []
    emails = []
    whatsapp = []
    pdf_calls = []
    bridge_calls = []

    def connection_factory():
        connection = LegacyConnection()
        connections.append(connection)
        return connection

    def failing_bridge(stima_id, **data):
        bridge_calls.append((stima_id, data))
        assert connections[0].commit_count == 1
        raise RuntimeError("controlled bridge failure")

    monkeypatch.setattr(main_module, "get_connection", connection_factory)
    monkeypatch.setattr(main_module.core_service, "bridge_public_stima", failing_bridge)
    monkeypatch.setattr(
        main_module,
        "compute_from_payload",
        lambda _payload: {
            "price_exact": 180000,
            "eur_mq_finale": 2000,
            "valore_pertinenze": 5000,
            "base_mq": 1500,
        },
    )
    monkeypatch.setattr(
        main_module,
        "genera_pdf_stima",
        lambda payload, nome_file: pdf_calls.append((payload, nome_file)) or "reports/stima_501.pdf",
    )
    monkeypatch.setattr(main_module, "invia_mail", lambda *args: emails.append(args))
    monkeypatch.setattr(main_module, "invia_whatsapp", lambda *args: whatsapp.append(args))

    response = asyncio.run(
        main_module.salva_stima(
            JsonRequest(
                {
                    "comune": "Alba Adriatica",
                    "microzona": "Centro",
                    "mq": 90,
                    "nome": "Mario",
                    "cognome": "Rossi",
                    "email": "mario@example.com",
                    "telefono": "+39 333 123 4567",
                    "prezzo_mq_base": 1500,
                }
            )
        )
    )

    assert response == {
        "success": True,
        "id": 501,
        "pdf_url": f"{main_module.PUBLIC_BASE_URL}/reports/stima_501.pdf",
        "price_exact": 180000,
        "eur_mq_finale": 2000,
        "valore_pertinenze": 5000,
        "base_mq": 1500,
    }
    assert bridge_calls[0][0] == 501
    assert bridge_calls[0][1]["first_name"] == "Mario"
    assert len(pdf_calls) == 1
    assert len(emails) == 2
    assert len(whatsapp) == 1
    log_text = caplog.text
    assert "bridge_status=error" in log_text
    assert "stima_id=501" in log_text
    assert "mario@example.com" not in log_text
    assert "+39 333 123 4567" not in log_text


def test_public_stima_remains_anonymous_and_core_routes_remain_protected():
    main_module = import_project_module("main")
    paths = main_module.app.openapi()["paths"]

    assert not paths["/api/salva_stima"]["post"].get("security")
    for path, operations in paths.items():
        if path.startswith("/api/core"):
            for method, operation in operations.items():
                if method in {"get", "post", "patch", "delete"}:
                    assert operation.get("security")
