"""P17-A repository tests.

Follows the exact convention already used in
tests/test_public_stima_core_crm_bridge.py: no real database connection,
seller_intelligence.database.si_cursor is monkeypatched with an in-memory
fake that understands only the SQL shapes this repository actually emits.

FakeDatabase additionally models Postgres' ON DELETE SET NULL behaviour for
the four FKs, so that requirement #9 from the P17-A review (cancellare
l'entita' referenziata non deve mai essere bloccata da Seller Intelligence)
can be exercised as close to real DB semantics as a pure-Python fake allows.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from seller_intelligence import repository


def _unwrap_json_adapter(value):
    """Mirrors a real Postgres JSONB round-trip; see the identical helper
    and comment in tests/test_seller_intelligence_p17b1_integration.py for
    the full explanation (same root cause, found and fixed there first)."""
    return getattr(value, "adapted", value)


class FakeCursor:
    def __init__(self, database):
        self.database = database
        self.rows = []

    def execute(self, query, params=None):
        sql = " ".join(str(query).split()).lower()
        self.database.sql.append((sql, params))

        if "insert into seller_timeline_events" in sql:
            self._handle_insert(params)
            return

        if "from seller_timeline_events where idempotency_key" in sql:
            key = params[0]
            match = next(
                (r for r in self.database.rows if r["idempotency_key"] == key),
                None,
            )
            self.rows = [copy.deepcopy(match)] if match else []
            return

        if "from seller_timeline_events" in sql:
            self._handle_select(sql, params)
            return

        if sql.startswith("delete from "):
            self._handle_delete(sql, params)
            return

        raise AssertionError(f"unexpected seller_intelligence SQL: {sql}")

    def _handle_insert(self, params):
        idempotency_key = params.get("idempotency_key")
        if idempotency_key is not None:
            existing = next(
                (r for r in self.database.rows if r["idempotency_key"] == idempotency_key),
                None,
            )
            if existing is not None:
                # ON CONFLICT ... DO NOTHING: no row returned by this INSERT.
                self.rows = []
                return

        row = {
            "id": self.database.next_id,
            "contact_id": params.get("contact_id"),
            "lead_id": params.get("lead_id"),
            "stima_id": params.get("stima_id"),
            "property_id": params.get("property_id"),
            "event_type": params.get("event_type"),
            "event_source": params.get("event_source"),
            "occurred_at": params.get("occurred_at"),
            "payload": copy.deepcopy(_unwrap_json_adapter(params.get("payload"))),
            "idempotency_key": idempotency_key,
            "created_by": params.get("created_by"),
            "created_at": datetime.now(timezone.utc),
        }
        self.database.next_id += 1
        self.database.rows.append(row)
        self.rows = [copy.deepcopy(row)]

    def _handle_select(self, sql, params):
        params = list(params)
        limit, offset = params[-2], params[-1]
        filter_params = params[:-2]

        filters = []
        for column in ("contact_id", "lead_id", "stima_id", "property_id"):
            if f"{column} = %s" in sql:
                filters.append(column)
        assert len(filters) == len(filter_params), "mismatch tra filtri nel testo SQL e parametri"

        rows = [
            row for row in self.database.rows
            if all(row[col] == value for col, value in zip(filters, filter_params))
        ]
        rows.sort(key=lambda r: (r["occurred_at"], r["id"]), reverse=True)
        self.rows = [copy.deepcopy(r) for r in rows[offset:offset + limit]]

    def _handle_delete(self, sql, params):
        # Simulates the FK's ON DELETE SET NULL for whichever parent table
        # is targeted, exactly as Postgres would apply it - no CHECK exists
        # on seller_timeline_events to reject the resulting NULL.
        table_to_column = {
            "stime": "stima_id",
            "contacts": "contact_id",
            "leads": "lead_id",
            "properties": "property_id",
        }
        target_id = params[0]
        for table, column in table_to_column.items():
            if f"delete from {table} where id" in sql:
                for row in self.database.rows:
                    if row[column] == target_id:
                        row[column] = None
                return
        raise AssertionError(f"unexpected DELETE target: {sql}")

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class FakeDatabase:
    def __init__(self):
        self.rows = []
        self.next_id = 1
        self.sql = []
        self.commits = 0

    @contextmanager
    def cursor(self, *, commit=False):
        yield self, FakeCursor(self)
        if commit:
            self.commits += 1

    def delete_parent_row(self, table, row_id):
        """Test helper: run the DELETE exactly as production code would."""
        with self.cursor(commit=True) as (_, cur):
            cur.execute(f"DELETE FROM {table} WHERE id = %s", (row_id,))


@pytest.fixture
def fake_db(monkeypatch):
    database = FakeDatabase()
    monkeypatch.setattr(repository, "si_cursor", database.cursor)
    return database


# --- requirement 1: creazione evento con stima_id ---------------------------

def test_insert_event_with_stima_id_only(fake_db):
    row = repository.insert_event({
        "contact_id": None, "lead_id": None, "stima_id": 501, "property_id": None,
        "event_type": "stima_richiesta", "event_source": "stima360_it",
        "occurred_at": datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
        "payload": {"comune": "Alba Adriatica"},
        "idempotency_key": "stima_richiesta:501", "created_by": None,
    })
    assert row["id"] == 1
    assert row["stima_id"] == 501
    assert row["contact_id"] is None
    assert fake_db.commits == 1


# --- requirement 2: creazione evento con contact_id --------------------------

def test_insert_event_with_contact_id_only(fake_db):
    row = repository.insert_event({
        "contact_id": 7, "lead_id": None, "stima_id": None, "property_id": None,
        "event_type": "nota_agente", "event_source": "admin",
        "occurred_at": datetime(2026, 9, 1, 11, 0, tzinfo=timezone.utc),
        "payload": {}, "idempotency_key": None, "created_by": "giorgio",
    })
    assert row["contact_id"] == 7
    assert row["created_by"] == "giorgio"


# --- requirement 3: payload JSONB round-trip --------------------------------

def test_payload_jsonb_round_trips_arbitrary_dict(fake_db):
    payload = {"price_exact": 180000, "eur_mq_finale": 2000, "nested": {"a": [1, 2, 3]}}
    row = repository.insert_event({
        "contact_id": 1, "lead_id": None, "stima_id": None, "property_id": None,
        "event_type": "stima_completata", "event_source": None,
        "occurred_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "payload": payload, "idempotency_key": None, "created_by": None,
    })
    assert row["payload"] == payload
    assert row["payload"] is not payload  # deep-copied, not the same object


# --- requirement 4: ordinamento timeline ------------------------------------

def test_timeline_orders_by_occurred_at_desc_with_deterministic_tiebreak(fake_db):
    same_instant = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
    for i in range(3):
        repository.insert_event({
            "contact_id": 1, "lead_id": None, "stima_id": None, "property_id": None,
            "event_type": f"evento_{i}", "event_source": None,
            "occurred_at": same_instant, "payload": {}, "idempotency_key": None, "created_by": None,
        })
    later = repository.insert_event({
        "contact_id": 1, "lead_id": None, "stima_id": None, "property_id": None,
        "event_type": "evento_piu_recente", "event_source": None,
        "occurred_at": datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        "payload": {}, "idempotency_key": None, "created_by": None,
    })

    items = repository.list_timeline(contact_id=1, limit=50, offset=0)

    assert items[0]["id"] == later["id"]
    # Stesso occurred_at: ordinamento deterministico per id decrescente.
    ids_with_same_instant = [item["id"] for item in items[1:4]]
    assert ids_with_same_instant == sorted(ids_with_same_instant, reverse=True)


# --- requirement 5: idempotency_key impedisce duplicati ---------------------

def test_idempotency_key_prevents_duplicate_rows(fake_db):
    first = repository.insert_event({
        "contact_id": None, "lead_id": None, "stima_id": 501, "property_id": None,
        "event_type": "stima_richiesta", "event_source": None,
        "occurred_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "payload": {}, "idempotency_key": "stima_richiesta:501", "created_by": None,
    })
    second = repository.insert_event({
        "contact_id": None, "lead_id": None, "stima_id": 501, "property_id": None,
        "event_type": "stima_richiesta", "event_source": None,
        "occurred_at": datetime(2026, 9, 1, 5, 0, tzinfo=timezone.utc),  # diverso, ma stessa key
        "payload": {"retry": True}, "idempotency_key": "stima_richiesta:501", "created_by": None,
    })

    assert second["id"] == first["id"]
    assert second["payload"] == first["payload"], "il secondo tentativo non deve alterare la riga esistente"
    assert len(fake_db.rows) == 1


# --- requirement 6: idempotency_key NULL permette eventi distinti ----------

def test_null_idempotency_key_allows_multiple_distinct_events(fake_db):
    for _ in range(3):
        repository.insert_event({
            "contact_id": 1, "lead_id": None, "stima_id": None, "property_id": None,
            "event_type": "nota_agente", "event_source": "admin",
            "occurred_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
            "payload": {}, "idempotency_key": None, "created_by": "giorgio",
        })
    assert len(fake_db.rows) == 3
    assert all(row["idempotency_key"] is None for row in fake_db.rows)


# --- requirement 8: event_type sconosciuto viene accettato -----------------

def test_unknown_event_type_is_accepted_without_error(fake_db):
    row = repository.insert_event({
        "contact_id": 1, "lead_id": None, "stima_id": None, "property_id": None,
        "event_type": "un_tipo_mai_visto_prima_p24", "event_source": None,
        "occurred_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "payload": {}, "idempotency_key": None, "created_by": None,
    })
    assert row["event_type"] == "un_tipo_mai_visto_prima_p24"


# --- requirement 9: cancellazione non bloccata, FK diventa NULL ------------

def test_deleting_the_only_referenced_stima_sets_fk_null_without_blocking(fake_db):
    event = repository.insert_event({
        "contact_id": None, "lead_id": None, "stima_id": 501, "property_id": None,
        "event_type": "stima_richiesta", "event_source": "stima360_it",
        "occurred_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "payload": {"comune": "Alba Adriatica"},
        "idempotency_key": "stima_richiesta:501", "created_by": None,
    })
    assert event["stima_id"] == 501

    # Nessuna eccezione: la DELETE (equivalente a POST /api/admin/stime/delete
    # in main.py) deve poter avvenire esattamente come oggi, P17 o non P17.
    fake_db.delete_parent_row("stime", 501)

    survivors = repository.list_timeline(limit=50, offset=0)
    assert len(survivors) == 1
    survivor = survivors[0]
    assert survivor["id"] == event["id"], "la riga evento deve sopravvivere alla cancellazione della stima"
    assert survivor["stima_id"] is None, "la FK deve diventare NULL, non bloccare la DELETE"
    # Era l'unico riferimento non-null: con il CHECK originariamente proposto
    # (rimosso su richiesta di design review) questa riga, a questo punto,
    # violerebbe "almeno un riferimento non-null" - e la SET NULL applicata
    # dalla FK dentro la stessa transazione della DELETE avrebbe fallito,
    # bloccando la cancellazione della stima. Qui invece la riga sopravvive
    # con tutte le FK a NULL, che e' esattamente il comportamento voluto.
    assert survivor["contact_id"] is None
    assert survivor["lead_id"] is None
    assert survivor["property_id"] is None


def test_deleting_referenced_contact_lead_and_property_all_set_null_independently(fake_db):
    event = repository.insert_event({
        "contact_id": 7, "lead_id": 9, "stima_id": None, "property_id": 3,
        "event_type": "appuntamento", "event_source": "admin",
        "occurred_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "payload": {}, "idempotency_key": None, "created_by": "giorgio",
    })

    fake_db.delete_parent_row("contacts", 7)
    fake_db.delete_parent_row("leads", 9)
    fake_db.delete_parent_row("properties", 3)

    survivors = repository.list_timeline(limit=50, offset=0)
    survivor = next(r for r in survivors if r["id"] == event["id"])
    assert (survivor["contact_id"], survivor["lead_id"], survivor["property_id"]) == (None, None, None)
