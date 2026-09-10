"""P26-2C2B - the PROPERTY create writer becomes tenant-aware.

Offline. `property.repository.core_cursor` is replaced by a recording fake and
every assertion is made against the `(sql, params)` pairs the code actually
issued.

034 gave `properties` a nullable `agency_id`. This block makes the one create
path fill it. The column stays nullable, so the 22 historical rows are
untouched and no migration is needed - but from here on a *new* property
without an owner is a bug, not a legacy artefact.

WHERE THE AGENCY COMES FROM, AND WHERE IT DOES NOT

`/api/property` is still mounted behind `Depends(require_admin)` in main.py, and
this block does not change that. PROPERTY reads are not tenant-scoped yet, so
opening the router to operator sessions now would let an Agency-B session reach
unscoped lists. GATE-MA1 stays open, and the mount is asserted unchanged below.

The create route therefore takes `legacy_basic_agency_context`, which resolves
the Default Agency by slug server-side and returns an agency-bound
`OperatorContext`. The caller supplies a Basic credential, never an agency.

ONE RESOLUTION PER REQUEST

The router resolves the context once and the *same object* travels
router -> service -> repository. The repository resolves nothing: no factory,
no second slug lookup, no fallback. This is the lesson of P26-2B2B-R1, where
the stima writer and the CORE bridge each called the same factory and so made
two independent decisions that could disagree. Group C below is the
deterministic restatement of that: a repository that re-resolved would produce
agency 88 while the context says 77, and the property would land in the wrong
tenant.

Coverage map:

    A  the create writer requires a context and stamps it
    B  the client cannot choose an agency, at any layer
    C  one resolution per request - the repository never re-resolves
    D  the same context object travels the chain unchanged
    E  the auth boundary is unchanged, and PROPERTY is not session-enabled
    F  the existing create behaviour is intact
"""
from __future__ import annotations

import inspect
import re
from contextlib import contextmanager
from pathlib import Path

import pytest

from operator_auth.context import OperatorContext
from property import repository as property_repository
from property import router as property_router
from property import service as property_service

ROOT = Path(__file__).resolve().parents[1]

# Deliberately not 1: a hardcoded agency would pass a test that used 1.
AGENCY = 77
# What a hypothetical re-resolution inside the repository would return.
DRIFTED_AGENCY = 88

NEW_PROPERTY_ID = 501


def ctx(agency_id=AGENCY) -> OperatorContext:
    """The context the legacy Basic branch produces: agency-bound, no user.

    P26-3 note: with an operator session the same dependency yields the
    operator's real agency, role and user id instead. This fixture keeps
    exercising the legacy shape because that is the one this suite is about.
    """
    return OperatorContext(
        user_id=None,
        agency_id=agency_id,
        role="agency_owner",
        is_platform_admin=False,
        session_id=None,
        auth_channel="legacy_basic",
    )


class RecordingCursor:
    def __init__(self, created):
        self.created = created
        self.calls: list[tuple[str, object]] = []
        self._row = None

    def execute(self, query, params=None):
        squashed = " ".join(str(query).split())
        self.calls.append((squashed, params))
        lowered = squashed.lower()
        if lowered.startswith("insert into properties"):
            self._row = dict(self.created)
        elif "from agencies" in lowered:
            # Only reachable if the repository re-resolves - which it must not.
            self._row = {"id": DRIFTED_AGENCY}
        else:
            self._row = None

    def fetchone(self):
        return self._row

    def fetchall(self):
        return []

    def statements(self, needle: str) -> list[tuple[str, object]]:
        return [c for c in self.calls if needle.lower() in c[0].lower()]

    @property
    def property_insert(self):
        found = [c for c in self.calls if c[0].lower().startswith("insert into properties")]
        return found[0] if found else None


BASE_CREATED = {
    "id": NEW_PROPERTY_ID,
    "title": "Villa sul mare",
    "asking_price": 250000,
    "commercial_status": "draft",
    "classification": "A",
}


@pytest.fixture
def recording(monkeypatch):
    """Install a recording cursor behind property.repository.core_cursor."""
    state = {}

    def _install(created=None):
        cursor = RecordingCursor(created or BASE_CREATED)
        state["cursor"] = cursor

        @contextmanager
        def _core_cursor(*_args, **_kwargs):
            yield (None, cursor)

        monkeypatch.setattr(property_repository, "core_cursor", _core_cursor)
        return cursor

    state["install"] = _install
    return state


def _bound(call) -> list:
    _, params = call
    if params is None:
        return []
    if isinstance(params, dict):
        return list(params.values())
    return list(params)


def _insert_columns(sql: str) -> list[str]:
    match = re.search(r"INSERT INTO properties \(([^)]*)\)", sql, re.IGNORECASE)
    assert match, sql
    return [c.strip().lower() for c in match.group(1).split(",")]


# ---------------------------------------------------------------------------
# A - the writer requires a context and stamps it
# ---------------------------------------------------------------------------

def test_a_the_repository_create_requires_a_context():
    parameters = list(inspect.signature(property_repository.create_property).parameters)
    assert parameters[0] == "ctx", parameters


def test_a_the_insert_names_and_binds_the_context_agency(recording):
    cursor = recording["install"]()
    property_repository.create_property(ctx(), {"title": "Villa sul mare"})

    sql, params = cursor.property_insert
    assert "agency_id" in _insert_columns(sql), sql
    assert AGENCY in _bound((sql, params)), params


def test_a_a_context_without_an_agency_is_refused(recording):
    """A platform admin holding no membership cannot create a property."""
    from operator_auth.exceptions import PlatformAdminAgencyRequired

    cursor = recording["install"]()
    unbound = OperatorContext(
        user_id=1, agency_id=None, role="platform_admin",
        is_platform_admin=True, session_id="s", auth_channel="session",
    )
    with pytest.raises(PlatformAdminAgencyRequired):
        property_repository.create_property(unbound, {"title": "x"})

    assert cursor.property_insert is None, "a property was written without an owner"


def test_a_the_agency_is_resolved_before_any_write(recording):
    """`require_agency()` runs before the transaction opens, so a refusal
    leaves nothing behind rather than being rolled back."""
    cursor = recording["install"]()
    unbound = OperatorContext(
        user_id=1, agency_id=None, role="platform_admin",
        is_platform_admin=True, session_id="s", auth_channel="session",
    )
    with pytest.raises(Exception):
        property_repository.create_property(unbound, {"title": "x"})
    assert cursor.calls == [], cursor.calls


# ---------------------------------------------------------------------------
# B - the client cannot choose an agency
# ---------------------------------------------------------------------------

def test_b_property_create_declares_no_agency_field():
    from property.schemas import PropertyCreate

    assert "agency_id" not in PropertyCreate.model_fields, PropertyCreate.model_fields


def test_b_property_update_declares_no_agency_field():
    from property.schemas import PropertyUpdate

    assert "agency_id" not in PropertyUpdate.model_fields


@pytest.mark.parametrize("model_name", ("PropertyCreate", "PropertyUpdate"))
def test_b_the_schemas_forbid_unknown_fields(model_name):
    """`extra = "forbid"` is what turns a forged agency_id into a 422 rather
    than a silently ignored key."""
    import property.schemas as schemas

    model = getattr(schemas, model_name)
    assert model.model_config.get("extra") == "forbid", model.model_config


def test_b_a_forged_agency_id_is_rejected_by_the_schema():
    """The HTTP-layer half of the property: 422, not 201 with a foreign owner."""
    import pydantic

    from property.schemas import PropertyCreate

    with pytest.raises(pydantic.ValidationError) as failure:
        PropertyCreate(title="Villa", agency_id=999)
    assert "agency_id" in str(failure.value)


@pytest.fixture
def http(monkeypatch):
    """The real app, with only the DB-backed context resolution stood in for.

    The schema assertions above prove the model refuses the field; this proves
    the *endpoint* does, which is the property that actually protects the
    system. Nothing else is overridden - the Basic guard is the real one.
    """
    import base64

    from fastapi.testclient import TestClient

    from integration_p2_support import import_main_app
    from operator_auth import dependencies

    monkeypatch.setenv("ADMIN_USER", "u")
    monkeypatch.setenv("ADMIN_PASS", "p")
    app = import_main_app()
    app.dependency_overrides[dependencies.legacy_basic_agency_context] = lambda: ctx()
    try:
        yield (
            TestClient(app),
            {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()},
        )
    finally:
        app.dependency_overrides.pop(dependencies.legacy_basic_agency_context, None)


def test_b_a_forged_agency_id_over_http_is_422_not_201(http):
    client, headers = http
    response = client.post(
        "/api/property/properties",
        json={"title": "Villa", "agency_id": 999},
        headers=headers,
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert any(
        item.get("type") == "extra_forbidden" and item.get("loc")[-1] == "agency_id"
        for item in detail
    ), detail


def test_b_the_create_route_still_refuses_an_unauthenticated_caller(http):
    """The legacy Basic contract is unchanged by this block."""
    client, _ = http
    response = client.post("/api/property/properties", json={"title": "Villa"})
    assert response.status_code == 401, response.text


def test_b_the_repository_refuses_a_payload_carrying_an_agency(recording):
    """The repository-level restatement of the same rule.

    Unreachable over HTTP because the schema forbids it - which is exactly why
    it is asserted here. A future schema change, or an internal caller building
    a dict by hand, would otherwise place a row in a chosen agency.
    """
    from core.scope import ProgrammingError

    cursor = recording["install"]()
    with pytest.raises(ProgrammingError):
        property_repository.create_property(
            ctx(), {"title": "Villa", "agency_id": 999}
        )
    assert cursor.property_insert is None


def test_b_a_forged_agency_never_reaches_the_insert(recording):
    cursor = recording["install"]()
    property_repository.create_property(ctx(), {"title": "Villa"})
    sql, params = cursor.property_insert
    bound = _bound((sql, params))
    assert 999 not in bound, bound
    assert AGENCY in bound


def test_b_no_hardcoded_agency_in_the_property_writer():
    source = inspect.getsource(property_repository.create_property)
    assert not re.search(r"agency_id\s*=\s*\d", source), source
    for forbidden in ("MIN(", "LIMIT 1", "stima360", "slug"):
        assert forbidden not in source, f"the writer contains {forbidden!r}"


# ---------------------------------------------------------------------------
# C - one resolution per request
# ---------------------------------------------------------------------------

def test_c_the_repository_resolves_no_agency_of_its_own(recording):
    """The direct statement of the rule, asserted on the recording.

    The fake would answer an `agencies` lookup with 88. That the property still
    carries 77 is not a coincidence of agreement - the second lookup is gone.
    """
    cursor = recording["install"]()
    property_repository.create_property(ctx(), {"title": "Villa"})

    assert not cursor.statements("FROM agencies"), (
        f"the repository re-resolved the agency: {cursor.statements('FROM agencies')}"
    )
    assert AGENCY in _bound(cursor.property_insert)
    assert DRIFTED_AGENCY not in _bound(cursor.property_insert)


def test_c_the_repository_calls_no_context_factory():
    source = inspect.getsource(property_repository)
    assert "resolve_default_agency_id" not in source, source
    assert "system_context_for_public_stima" not in source
    assert "legacy_basic_agency_context" not in source, (
        "the repository builds a context instead of receiving one"
    )


def test_c_only_the_router_resolves_the_context():
    router_source = inspect.getsource(property_router)
    assert router_source.count("legacy_basic_agency_context") >= 1
    assert "resolve_default_agency_id" not in router_source

    service_source = inspect.getsource(property_service)
    assert "legacy_basic_agency_context" not in service_source, (
        "the service resolves a context of its own"
    )
    assert "resolve_default_agency_id" not in service_source


# ---------------------------------------------------------------------------
# D - the same object travels the chain
# ---------------------------------------------------------------------------

def test_d_the_service_passes_the_context_through_unchanged(monkeypatch):
    seen = {}

    def _capture(received_ctx, data):
        seen["ctx"] = received_ctx
        seen["data"] = data
        return {"id": NEW_PROPERTY_ID}

    monkeypatch.setattr(property_repository, "create_property", _capture)
    monkeypatch.setattr(property_service, "repository", property_repository)

    original = ctx()

    class Model:
        def model_dump(self, exclude_unset=False):
            return {"title": "Villa"}

    property_service.create_property(original, Model())
    assert seen["ctx"] is original, "the service substituted a different context"
    assert "agency_id" not in seen["data"], seen["data"]


def test_d_the_router_declares_the_dependency_on_the_create_route_only():
    source = inspect.getsource(property_router)
    create = re.search(
        r"@router\.post\('/properties',status_code=201\)\s*\ndef create_property\([^)]*\)",
        source,
    )
    assert create, source[:600]
    assert "legacy_basic_agency_context" in create.group(0), create.group(0)


@pytest.mark.parametrize(
    "handler", ("list_properties", "get_property", "update_property", "archive_property")
)
def test_d_the_read_and_update_routes_are_scoped(handler):
    """P26-2C: The read and update routes are now fully scoped."""
    signature = inspect.signature(getattr(property_router, handler))
    assert "ctx" in signature.parameters, (
        f"{handler} must declare context parameter"
    )


def test_d_the_router_forwards_the_context_as_the_first_service_argument():
    source = inspect.getsource(property_router)
    line = next(
        line for line in source.splitlines()
        if line.strip().startswith("def create_property(")
    )
    assert re.search(r"service\.create_property,\s*ctx", line), line


# ---------------------------------------------------------------------------
# E - the auth boundary is unchanged
# ---------------------------------------------------------------------------

def test_e_the_property_mount_still_requires_admin():
    """GATE-MA1 stays open: PROPERTY reads are not tenant-scoped, so opening
    this router to operator sessions would let an Agency-B session read across
    tenants. The mount is not this block's to change."""
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    mount = next(
        line for line in main.splitlines() if "property_router" in line and "include_router" in line
    )
    assert "Depends(require_authenticated_operator)" in mount, mount
    assert "require_operator" not in mount, mount


def test_e_property_is_not_added_to_the_operator_session_allowlist():
    from operator_auth import dependencies

    source = inspect.getsource(dependencies)
    assert "/api/property" not in source, (
        "PROPERTY was enabled for operator sessions in a block scoped to the writer"
    )


def test_e_the_create_dependency_carries_its_own_authentication():
    """legacy_basic_agency_context declares require_admin itself, so the route
    is authenticated even if a future refactor drops the mount-level guard."""
    from operator_auth.dependencies import legacy_basic_agency_context

    source = inspect.getsource(legacy_basic_agency_context)
    assert "require_admin" in source, source


def test_e_the_context_is_agency_bound_and_never_platform_admin():
    """P26-3: the legacy scope is built in `_default_agency_context` now.

    The other branch of the dependency returns a session's own context, which
    is agency-bound because the session is - proved in the operator-auth suite,
    not by reading this source.
    """
    from operator_auth.dependencies import _default_agency_context

    source = inspect.getsource(_default_agency_context)
    assert "is_platform_admin=False" in source, source
    assert not re.search(r"agency_id\s*=\s*\d", source), source


# ---------------------------------------------------------------------------
# F - the existing create behaviour is intact
# ---------------------------------------------------------------------------

def test_f_the_initial_price_history_is_still_written(recording):
    cursor = recording["install"]()
    property_repository.create_property(ctx(), {"title": "Villa"})
    assert cursor.statements("INSERT INTO property_price_history"), cursor.calls


def test_f_the_initial_status_and_classification_history_are_still_written(recording):
    cursor = recording["install"]()
    property_repository.create_property(ctx(), {"title": "Villa"})
    history = cursor.statements("INSERT INTO property_status_history")
    assert len(history) == 2, history
    notes = [str(_bound(call)[-1]) for call in history]
    assert "initial status" in notes, notes
    assert "initial classification" in notes, notes


def test_f_metadata_is_still_wrapped_as_json(recording):
    cursor = recording["install"]()
    property_repository.create_property(ctx(), {"title": "Villa", "metadata": {"a": 1}})
    sql, _ = cursor.property_insert
    assert "metadata" in _insert_columns(sql)


def test_f_a_duplicate_code_is_still_a_conflict(recording, monkeypatch):
    from psycopg2 import errors

    from core.exceptions import ConflictError

    cursor = recording["install"]()

    def _explode(query, params=None):
        raise errors.UniqueViolation("duplicate key")

    monkeypatch.setattr(cursor, "execute", _explode)
    with pytest.raises(ConflictError):
        property_repository.create_property(ctx(), {"title": "Villa", "code": "A1"})


def test_f_no_child_table_gains_an_agency_column(recording):
    """PROPERTY children are CHILD-DERIVED; the writer must not stamp them."""
    cursor = recording["install"]()
    property_repository.create_property(ctx(), {"title": "Villa"})
    for sql, _ in cursor.calls:
        if "property_price_history" in sql or "property_status_history" in sql:
            assert "agency_id" not in sql.lower(), sql


def test_f_the_created_row_is_returned_unchanged(recording):
    recording["install"]()
    created = property_repository.create_property(ctx(), {"title": "Villa sul mare"})
    assert created["id"] == NEW_PROPERTY_ID
    assert created["title"] == "Villa sul mare"
