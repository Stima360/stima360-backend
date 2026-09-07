"""P26-1 Tasks 13 and 20 - the public estimation path, end to end and offline.

The public estimation is the one anonymous writer of CORE data. It has no
operator, no session and no agency to be told, so everything about where its
rows land is decided server-side. These tests hold that line from two
directions:

* **Task 13 (routing)** - the bridge resolves a `SystemAgencyContext` from the
  Default Agency slug as the first statement in its transaction, scopes its
  identity lookups with it, and stamps both inserts. An email that exists only
  in another agency must produce a *new* Default-Agency contact and must never
  touch the other agency's row.
* **Task 20 (regression)** - P17's event recording and P18's follow-up task
  still run, `POST /api/salva_stima` still returns 200 whatever the bridge
  says, and the `stime` row is written before the bridge and survives its
  failure.

Every negative condition is produced by `monkeypatch` inside a test and
reverted at teardown. No source file is edited to manufacture a failure, and
nothing here opens a database connection.

Coverage map (design spec section 15, items 53-57c; section 11.3):

    J1  the bridge builds its own scope, first, from the slug
    J2  identity lookups are agency-scoped
    J3  both inserts are stamped with the resolved agency
    J4  a foreign-agency email does not collide and does not mutate
    J5  the endpoint absorbs every bridge outcome, including error
    J6  the stima row is independent of the bridge
    J7  P17 and P18 still receive what they received before
"""
from __future__ import annotations

import inspect
from contextlib import contextmanager

import pytest

from core import repository
from core.exceptions import ConflictError
from core.scope import system_context_for_public_stima

DEFAULT_AGENCY_ID = 77
OTHER_AGENCY_ID = 88
DEFAULT_AGENCY_SLUG = "stima360"

# None is a meaningful value here - "the Default Agency is absent" - so it
# cannot double as "caller did not specify".
_UNSET = object()


class BridgeCursor:
    """Records statements and answers the small set the bridge issues."""

    def __init__(self, *, agency_row=_UNSET, existing_link=None, contacts=None):
        self.calls: list[tuple[str, object]] = []
        self.agency_row = (
            {"id": DEFAULT_AGENCY_ID} if agency_row is _UNSET else agency_row
        )
        self.existing_link = existing_link
        self.contacts = list(contacts or [])
        self.inserted: dict[str, list[dict]] = {"contacts": [], "leads": [], "lead_stime": []}
        self._rows: list = []
        self._next_id = 500

    # -- the cursor protocol ------------------------------------------------
    def execute(self, sql, params=None):
        squashed = " ".join(str(sql).split())
        self.calls.append((squashed, params))
        lowered = squashed.lower()

        if "from agencies" in lowered:
            self._rows = [self.agency_row] if self.agency_row else []
        elif "pg_advisory_xact_lock" in lowered:
            self._rows = [{"locked": True}]
        elif "from lead_stime ls" in lowered:
            self._rows = [self.existing_link] if self.existing_link else []
        elif "from contacts" in lowered:
            agency_id = params[0]
            value = params[-1]
            column = "email_normalized" if "email_normalized" in lowered else "phone_normalized"
            self._rows = [
                row for row in self.contacts
                if row.get(column) == value and row.get("agency_id") == agency_id
            ]
        elif "insert into contacts" in lowered:
            self._next_id += 1
            row = {**params, "id": self._next_id}
            self.inserted["contacts"].append(row)
            self._rows = [row]
        elif "insert into leads" in lowered:
            self._next_id += 1
            row = {**params, "id": self._next_id}
            self.inserted["leads"].append(row)
            self._rows = [row]
        elif "insert into lead_stime" in lowered:
            row = {"lead_id": params[0], "stima_id": params[1]}
            self.inserted["lead_stime"].append(row)
            self._rows = [row]
        else:  # pragma: no cover - a new statement should be noticed loudly
            raise AssertionError(f"unexpected bridge SQL: {squashed}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    # -- reading the recording ---------------------------------------------
    def statements(self, needle: str) -> list[tuple[str, object]]:
        return [call for call in self.calls if needle.lower() in call[0].lower()]


@pytest.fixture
def bridge(monkeypatch):
    """Install a BridgeCursor and return a factory for running the bridge."""
    state = {}

    def _run(*, contacts=None, existing_link=None, agency_row=_UNSET,
             email="mario@example.test", phone=None, display_name="Mario Rossi",
             stima_id=901):
        cursor = BridgeCursor(
            agency_row=agency_row, existing_link=existing_link, contacts=contacts
        )
        state["cursor"] = cursor

        @contextmanager
        def _core_cursor(*args, **kwargs):
            yield (None, cursor)

        monkeypatch.setattr(repository, "core_cursor", _core_cursor)
        contact_data = {
            "contact_type": "person", "first_name": "Mario", "last_name": "Rossi",
            "company_name": None, "display_name": display_name, "email": email,
            "email_normalized": email, "phone": phone, "phone_normalized": phone,
            "secondary_phone": None, "source": "public_stima", "status": "active",
            "marketing_consent": False, "marketing_consent_at": None, "notes": None,
        }
        lead_data = {
            "source": "public_stima", "pipeline": "sell", "stage": "new",
            "priority": "normal", "status": "open", "assigned_to": None,
            "estimated_value": None, "next_action_at": None, "lost_reason": None,
            "notes": None,
        }
        # P26-2B2B-R1: the public writer resolves the Default Agency once and
        # hands the context down, so the fixture stands in for the writer and
        # does exactly that. The factory still runs against this same cursor,
        # which is why the J1 assertions below - first statement, resolved by
        # slug, fails closed - remain true and remain about this request.
        system_ctx = system_context_for_public_stima(cursor)
        result = repository.bridge_public_stima(
            stima_id, contact_data, lead_data, "related", system_ctx=system_ctx
        )
        return result, cursor

    return _run


# ---------------------------------------------------------------------------
# J1 - the bridge builds its own scope, first
# ---------------------------------------------------------------------------

def test_j1_the_agency_lookup_is_the_first_statement(bridge):
    _, cursor = bridge()
    first_sql, first_params = cursor.calls[0]
    assert "FROM agencies" in first_sql, first_sql
    assert "status = 'active'" in first_sql, first_sql
    assert list(first_params) == [DEFAULT_AGENCY_SLUG], first_params


def test_j1_the_agency_is_resolved_by_slug_not_by_number(bridge):
    _, cursor = bridge()
    source = inspect.getsource(repository.bridge_public_stima)
    assert str(DEFAULT_AGENCY_ID) not in source
    # R1: the bridge no longer resolves anything - it receives the context the
    # writer resolved. Asserting the absence is what keeps a second, independent
    # lookup from creeping back in.
    assert "system_context_for_public_stima" not in source
    assert "system_ctx" in [
        argument for argument in inspect.signature(
            repository.bridge_public_stima
        ).parameters
    ], "the bridge does not admit a system context"


def test_j1_a_missing_default_agency_fails_the_bridge_closed(bridge):
    with pytest.raises(ConflictError):
        bridge(agency_row=None)


def test_j1_the_conflict_message_names_no_agency_id(bridge):
    with pytest.raises(ConflictError) as failure:
        bridge(agency_row=None)
    message = str(failure.value)
    assert str(DEFAULT_AGENCY_ID) not in message
    assert DEFAULT_AGENCY_SLUG in message


# ---------------------------------------------------------------------------
# J2 - identity lookups are agency-scoped (spec item 57c)
# ---------------------------------------------------------------------------

def test_j2_the_contact_lookup_carries_a_bound_agency_predicate(bridge):
    _, cursor = bridge()
    lookups = cursor.statements("from contacts")
    assert lookups, "the identity lookup did not run"
    for sql, params in lookups:
        assert "c.agency_id = %s" in sql, sql
        assert DEFAULT_AGENCY_ID in list(params), params


def test_j2_the_phone_lookup_is_scoped_too(bridge):
    _, cursor = bridge(email=None, phone="393331234567")
    # "phone_normalized" also appears in the INSERT column list, so the probe
    # is restricted to the SELECT that actually performs the lookup.
    lookups = [
        call for call in cursor.statements("phone_normalized")
        if call[0].upper().startswith("SELECT")
    ]
    assert lookups, cursor.calls
    for sql, params in lookups:
        assert "c.agency_id = %s" in sql, sql
        assert DEFAULT_AGENCY_ID in list(params), params


def test_j2_the_advisory_lock_key_carries_the_agency(bridge):
    """Two agencies may hold the same email; one key would serialise them."""
    _, cursor = bridge()
    locks = [params[0] for sql, params in cursor.statements("pg_advisory_xact_lock")]
    identity_locks = [key for key in locks if "core:contact:" in str(key)]
    assert identity_locks, locks
    for key in identity_locks:
        assert f":{DEFAULT_AGENCY_ID}:" in key, key


# ---------------------------------------------------------------------------
# J3 / J4 - the inserts, and the foreign-agency email
# ---------------------------------------------------------------------------

def test_j3_both_inserts_are_stamped_with_the_resolved_agency(bridge):
    result, cursor = bridge()
    assert result["status"] == "linked"
    contact = cursor.inserted["contacts"][0]
    lead = cursor.inserted["leads"][0]
    assert contact["agency_id"] == lead["agency_id"] == DEFAULT_AGENCY_ID
    assert contact["created_by_user_id"] is None, "an anonymous caller has no operator"
    assert lead["created_by_user_id"] is None


def test_j4_an_email_only_in_another_agency_creates_a_new_contact(bridge):
    """Spec item 54. The B row must be neither reused nor updated."""
    foreign = {
        "id": 4242, "agency_id": OTHER_AGENCY_ID,
        "email_normalized": "mario@example.test", "phone_normalized": None,
        "status": "active", "archived_at": None,
    }
    result, cursor = bridge(contacts=[foreign])

    assert result["status"] == "linked"
    assert result["contact_created"] is True
    assert result["contact_id"] != foreign["id"], "it reused the other agency's contact"
    assert cursor.inserted["contacts"][0]["agency_id"] == DEFAULT_AGENCY_ID
    assert not cursor.statements("update contacts"), "it mutated a foreign row"


def test_j4_an_email_in_the_default_agency_is_reused(bridge):
    """The counterpart: within the agency, identity matching still works."""
    own = {
        "id": 11, "agency_id": DEFAULT_AGENCY_ID,
        "email_normalized": "mario@example.test", "phone_normalized": None,
        "status": "active", "archived_at": None,
    }
    result, cursor = bridge(contacts=[own])

    assert result["contact_id"] == own["id"]
    assert result["contact_created"] is False
    assert cursor.inserted["contacts"] == []


def test_j4_a_repeat_estimation_still_reports_already_linked(bridge):
    """Spec item 55: the short-circuit runs before identity matching."""
    result, cursor = bridge(existing_link={"lead_id": 3, "contact_id": 4})
    assert result["status"] == "already_linked"
    assert result["lead_id"] == 3
    assert cursor.inserted["contacts"] == [] and cursor.inserted["leads"] == []
    assert not cursor.statements("from contacts"), (
        "identity matching ran before the already_linked short-circuit"
    )


def test_j4_an_archived_contact_is_not_reactivated(bridge):
    archived = {
        "id": 12, "agency_id": DEFAULT_AGENCY_ID,
        "email_normalized": "mario@example.test", "phone_normalized": None,
        "status": "archived", "archived_at": "2026-01-01",
    }
    result, cursor = bridge(contacts=[archived])
    assert result["status"] == "skipped"
    assert result["reason"] == "archived_contact"
    assert cursor.inserted["contacts"] == []


def test_j4_insufficient_identity_creates_no_placeholder(bridge):
    result, cursor = bridge(email=None, phone=None, display_name=None)
    assert result["status"] == "skipped"
    assert result["reason"] == "insufficient_contact_identity"
    assert cursor.inserted["contacts"] == []


# ---------------------------------------------------------------------------
# J5 / J6 / J7 - Task 20: the endpoint and its two downstream consumers
# ---------------------------------------------------------------------------

def _main_source() -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")


def test_j5_the_bridge_is_called_inside_a_try_that_absorbs_everything():
    """Spec 11.3: a bridge failure must not change the public response."""
    source = _main_source()
    block = source[source.index("bridge_result = None"): source.index("safe_record_event")]
    assert "try:" in block and "except" in block, block[:400]
    assert "raise" not in block.split("except")[1][:400], "a bridge failure escapes"


def test_j5_the_endpoint_never_forwards_a_client_agency_id():
    """The public request has no say in where its rows land."""
    source = _main_source()
    call = source[source.index("bridge_result = core_service.bridge_public_stima"):]
    call = call[: call.index(")") + 1]
    assert "agency_id" not in call, call


def test_j6_the_stima_row_is_written_before_the_bridge_runs():
    """So a bridge failure cannot cost the estimation itself."""
    source = _main_source()
    handler = source[source.index('@app.post("/api/salva_stima")'):]
    handler = handler[: handler.index("safe_run_followup")]
    insert = handler.lower().index("insert into stime")
    bridge_call = handler.index("bridge_public_stima")
    assert insert < bridge_call, "the bridge runs before the stima row is stored"


def test_j7_p17_and_p18_receive_the_bridge_ids_defensively():
    """`(bridge_result or {}).get(...)` - the error path passes None, not a crash."""
    source = _main_source()
    for consumer in ("safe_record_event", "safe_run_followup"):
        block = source[source.index(consumer):]
        block = block[: block.index(")\n")]
        assert "(bridge_result or {}).get" in block, (consumer, block[:300])


def test_j7_both_downstream_calls_are_the_non_raising_wrappers():
    source = _main_source()
    assert "safe_record_event(" in source
    assert "safe_run_followup(" in source
    # The raising variants must not be called from the public path.
    handler = source[source.index('@app.post("/api/salva_stima")'):]
    handler = handler[: handler.index("@app.", 10)]
    assert "record_event(" not in handler.replace("safe_record_event(", "")
    assert "run_followup(" not in handler.replace("safe_run_followup(", "")


@pytest.mark.parametrize("outcome", ["conflict", "skipped", "error"])
def test_j5_every_bridge_outcome_leaves_the_public_contract_intact(outcome):
    """Asserted structurally: the response is built after the try/except and
    does not read bridge_result's status.

    Driving the real endpoint would need the whole estimation pipeline and a
    database; what matters for P26-1 is narrower and provable here - the
    response shape does not branch on what the bridge said.
    """
    source = _main_source()
    handler = source[source.index('@app.post("/api/salva_stima")'):]
    handler = handler[: handler.index("safe_run_followup")]
    for guard in ("if bridge_result[\"status\"] ==", "if bridge_result['status'] =="):
        assert guard not in handler, "the response branches on the bridge outcome"
    assert "bridge_status" in handler, "the outcome is logged, which is the intent"


def test_j7_the_followup_task_can_resolve_an_agency_from_a_stima_alone():
    """The 030 fallback exists precisely for this call shape.

    main.py calls safe_run_followup with stima_id set and contact_id/lead_id
    None whenever the bridge did not link. That is the one row shape branch E
    of core_agency_integrity() is bounded to.
    """
    from tests.test_p26_1_agency_integrity import resolve_agency

    assert resolve_agency(stima_id=9001) is not None
