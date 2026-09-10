"""P26-1 Task 3 - operator authentication security primitives.

Offline tests. Nothing here opens a database connection and nothing imports
FastAPI: the cookie helpers are duck-typed over a response object, exactly as
owner/security.py is, so they are exercised with a recording fake. That is the
established convention in this repository (see the ``Resp`` fake in
tests/test_owner.py) and it keeps the suite runnable without the web stack.

Scope of Task 3: constants and pure functions only. No repository, no service,
no router, no dependency injection, no database. Those are Tasks 4, 5 and 6.

Coverage map from the approved design spec sections 7.1 and 7.2, and D-7:

    S1  constants
    S2  password hashing - format, salt, iterations
    S3  password verification - correct, wrong, malformed
    S4  session token generation and hashing
    S5  cookie policy
    S6  dependency and secrecy constraints (source-level)
"""
from __future__ import annotations

import ast
import base64
import hashlib
import re
from pathlib import Path

import pytest

from operator_auth import context, enums, exceptions, permissions, security

ROOT = Path(__file__).resolve().parents[1]
SECURITY_SOURCE = ROOT / "operator_auth" / "security.py"
ENUMS_SOURCE = ROOT / "operator_auth" / "enums.py"

PASSWORD = "correct horse battery staple"
HASH_PREFIX = "pbkdf2_sha256$"


class RecordingResponse:
    """Duck-typed stand-in for a FastAPI/Starlette Response.

    Records exactly what the helper passed, which is the cookie policy.
    """

    def __init__(self) -> None:
        self.set_args: tuple = ()
        self.set_kwargs: dict = {}
        self.delete_args: tuple = ()
        self.delete_kwargs: dict = {}

    def set_cookie(self, *args, **kwargs) -> None:
        self.set_args = args
        self.set_kwargs = kwargs

    def delete_cookie(self, *args, **kwargs) -> None:
        self.delete_args = args
        self.delete_kwargs = kwargs


# ---------------------------------------------------------------------------
# S1 - constants
# ---------------------------------------------------------------------------

def test_s1_cookie_and_session_constants():
    assert enums.COOKIE_NAME == "stima360_operator_session"
    assert enums.SESSION_MAX_HOURS == 12
    assert enums.SESSION_IDLE_MINUTES == 240
    assert enums.PBKDF2_ITERATIONS == 600_000
    assert enums.DEFAULT_AGENCY_SLUG == "stima360"


def test_s1_exactly_three_agency_roles():
    assert enums.AGENCY_ROLES == ("agency_owner", "agency_admin", "agent")
    assert len(enums.AGENCY_ROLES) == 3


def test_s1_platform_admin_is_not_an_agency_role():
    """Spec section 3.2: a platform admin holds no membership row at all."""
    assert "platform_admin" not in enums.AGENCY_ROLES
    assert not any("platform" in role for role in enums.AGENCY_ROLES)


def test_s1_operator_cookie_does_not_collide_with_the_owner_portal_cookie():
    """owner/ uses stima360_owner_session. Two principals, two cookies.

    Read from the file rather than imported, so this test introduces no
    dependency on owner/.
    """
    owner_enums = (ROOT / "owner" / "enums.py").read_text(encoding="utf-8")
    match = re.search(r"COOKIE_NAME\s*=\s*'([^']+)'", owner_enums)
    assert match, "could not read the owner portal cookie name"
    assert enums.COOKIE_NAME != match.group(1)


def test_s1_max_age_derives_from_the_session_lifetime():
    assert enums.SESSION_MAX_HOURS * 3600 == 43200


# ---------------------------------------------------------------------------
# S2 - password hashing
# ---------------------------------------------------------------------------

def test_s2_hash_has_the_declared_prefix():
    assert security.hash_password(PASSWORD).startswith(HASH_PREFIX)


def test_s2_stored_value_never_contains_the_plaintext():
    stored = security.hash_password(PASSWORD)
    assert PASSWORD not in stored
    assert stored != PASSWORD
    for word in PASSWORD.split():
        assert word not in stored


def test_s2_same_password_hashed_twice_differs():
    """A fresh random salt per hash, so equal passwords are not equal on disk."""
    assert security.hash_password(PASSWORD) != security.hash_password(PASSWORD)


def test_s2_encoded_format_is_exactly_four_dollar_separated_fields():
    parts = security.hash_password(PASSWORD).split("$")
    assert len(parts) == 4, parts
    assert parts[0] == "pbkdf2_sha256"


def test_s2_iteration_count_is_encoded_and_is_600000():
    parts = security.hash_password(PASSWORD).split("$")
    assert parts[1] == str(enums.PBKDF2_ITERATIONS)
    assert int(parts[1]) == 600_000


def test_s2_salt_is_sixteen_random_bytes():
    first = security.hash_password(PASSWORD).split("$")[2]
    second = security.hash_password(PASSWORD).split("$")[2]
    assert len(base64.b64decode(first)) == 16
    assert first != second


def test_s2_digest_is_a_sha256_length_derived_key():
    digest = security.hash_password(PASSWORD).split("$")[3]
    assert len(base64.b64decode(digest)) == 32


def test_s2_digest_matches_an_independent_pbkdf2_computation():
    """Recompute with the stored salt and iterations; the digest must match."""
    _, iterations, salt_b64, digest_b64 = security.hash_password(PASSWORD).split("$")
    expected = hashlib.pbkdf2_hmac(
        "sha256",
        PASSWORD.encode("utf-8"),
        base64.b64decode(salt_b64),
        int(iterations),
    )
    assert base64.b64decode(digest_b64) == expected


def test_s2_hash_satisfies_the_migration_027_check_constraint():
    """027 constrains password_hash to LIKE 'pbkdf2_sha256$%'."""
    assert security.hash_password(PASSWORD).startswith("pbkdf2_sha256$")


# ---------------------------------------------------------------------------
# S3 - password verification
# ---------------------------------------------------------------------------

def test_s3_correct_password_verifies():
    assert security.verify_password(PASSWORD, security.hash_password(PASSWORD)) is True


def test_s3_incorrect_password_does_not_verify():
    stored = security.hash_password(PASSWORD)
    assert security.verify_password("wrong password", stored) is False
    assert security.verify_password(PASSWORD + " ", stored) is False
    assert security.verify_password("", stored) is False


def test_s3_a_hash_of_one_password_does_not_verify_another():
    assert security.verify_password("alpha", security.hash_password("beta")) is False


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "not-a-hash",
        "pbkdf2_sha256$",
        "pbkdf2_sha256$600000",
        "pbkdf2_sha256$600000$onlythree",
        "pbkdf2_sha256$600000$c2FsdA==$aGFzaA==$extra",
        "bcrypt$600000$c2FsdA==$aGFzaA==",
        "pbkdf2_sha256$notanumber$c2FsdA==$aGFzaA==",
        "pbkdf2_sha256$-1$c2FsdA==$aGFzaA==",
        "pbkdf2_sha256$600000$!!!notbase64!!!$aGFzaA==",
        "pbkdf2_sha256$600000$c2FsdA==$!!!notbase64!!!",
        "$$$",
    ],
)
def test_s3_malformed_stored_value_returns_false_and_never_raises(stored):
    assert security.verify_password(PASSWORD, stored) is False


def test_s3_non_string_stored_value_returns_false():
    for stored in (None, 123, b"pbkdf2_sha256$600000$c2FsdA==$aGFzaA==", []):
        assert security.verify_password(PASSWORD, stored) is False


def test_s3_verification_uses_a_constant_time_comparison():
    """A plain == on the digest would leak the prefix length by timing."""
    tree = ast.parse(SECURITY_SOURCE.read_text(encoding="utf-8"))
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "compare_digest" in calls, (
        "verify_password must compare digests with hmac.compare_digest"
    )


# ---------------------------------------------------------------------------
# S4 - session tokens
# ---------------------------------------------------------------------------

def test_s4_two_generated_tokens_differ():
    assert security.generate_session_token() != security.generate_session_token()


def test_s4_token_carries_at_least_32_bytes_of_entropy():
    token = security.generate_session_token()
    # token_urlsafe(32) is 32 random bytes, base64url encoded without padding.
    assert len(token) >= 43, len(token)
    assert re.fullmatch(r"[A-Za-z0-9_-]+", token), token


def test_s4_token_generation_is_not_deterministic():
    assert len({security.generate_session_token() for _ in range(50)}) == 50


def test_s4_token_hash_is_sixty_four_lowercase_hex():
    digest = security.hash_session_token(security.generate_session_token())
    assert re.fullmatch(r"[0-9a-f]{64}", digest), digest


def test_s4_token_hash_is_deterministic():
    token = security.generate_session_token()
    assert security.hash_session_token(token) == security.hash_session_token(token)


def test_s4_token_hash_is_sha256_of_the_token():
    token = security.generate_session_token()
    assert security.hash_session_token(token) == hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def test_s4_hash_is_not_the_raw_token():
    token = security.generate_session_token()
    digest = security.hash_session_token(token)
    assert digest != token
    assert token not in digest


def test_s4_token_hash_satisfies_the_migration_027_check_constraint():
    """027 constrains token_hash to ~ '^[0-9a-f]{64}$'."""
    digest = security.hash_session_token(security.generate_session_token())
    assert re.fullmatch(r"^[0-9a-f]{64}$", digest)


def test_s4_different_tokens_hash_differently():
    a, b = security.generate_session_token(), security.generate_session_token()
    assert security.hash_session_token(a) != security.hash_session_token(b)


# ---------------------------------------------------------------------------
# S5 - cookie policy
# ---------------------------------------------------------------------------

def test_s5_set_cookie_carries_the_full_policy():
    response = RecordingResponse()
    security.set_cookie(response, "raw-token-value")
    sent = dict(response.set_kwargs)
    positional = list(response.set_args)
    assert enums.COOKIE_NAME in positional or sent.get("key") == enums.COOKIE_NAME
    assert "raw-token-value" in positional or sent.get("value") == "raw-token-value"
    assert sent["httponly"] is True
    assert sent["secure"] is True
    assert sent["samesite"] == "lax"
    assert sent["path"] == "/"
    assert sent["max_age"] == 43200


def test_s5_clear_cookie_targets_the_same_cookie_and_policy():
    response = RecordingResponse()
    security.clear_cookie(response)
    sent = dict(response.delete_kwargs)
    positional = list(response.delete_args)
    assert enums.COOKIE_NAME in positional or sent.get("key") == enums.COOKIE_NAME
    assert sent["path"] == "/"
    assert sent["httponly"] is True
    assert sent["secure"] is True
    assert sent["samesite"] == "lax"


def test_s5_set_and_clear_agree_on_name_and_path():
    setter, clearer = RecordingResponse(), RecordingResponse()
    security.set_cookie(setter, "t")
    security.clear_cookie(clearer)
    set_name = setter.set_args[0] if setter.set_args else setter.set_kwargs["key"]
    clear_name = clearer.delete_args[0] if clearer.delete_args else clearer.delete_kwargs["key"]
    assert set_name == clear_name == enums.COOKIE_NAME
    assert setter.set_kwargs["path"] == clearer.delete_kwargs["path"] == "/"


def test_s5_cookie_policy_is_not_weakened_in_source():
    """Guards against secure=False or samesite='none' slipping in later."""
    source = SECURITY_SOURCE.read_text(encoding="utf-8")
    assert "secure=False" not in source
    assert "httponly=False" not in source
    assert "samesite='none'" not in source.lower()
    assert 'samesite="none"' not in source.lower()


# ---------------------------------------------------------------------------
# S6 - dependency and secrecy constraints
# ---------------------------------------------------------------------------

def _imported_modules(path: Path) -> set[str]:
    """Return the top-level modules a file imports.

    A relative import (``from .enums import ...``) is intra-package, not a
    dependency, so it is normalised to ``operator_auth``. Reporting it under
    its bare name would make an internal import indistinguishable from a
    third-party one, which is exactly what this helper exists to tell apart.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                names.add("operator_auth")
            elif node.module:
                names.add(node.module.split(".")[0])
    return names


def test_the_import_helper_distinguishes_relative_from_third_party(tmp_path):
    """Negative control for _imported_modules itself.

    A first run of the S6 test reported the relative `from .enums import ...`
    as a module named 'enums', which would have made an intra-package import
    look like an undeclared dependency.
    """
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from .enums import X\nfrom passlib.hash import Y\nimport hashlib\n",
        encoding="utf-8",
    )
    assert _imported_modules(probe) == {"operator_auth", "passlib", "hashlib"}


def test_s6_crypto_uses_the_standard_library_only():
    """Spec D-7: no new dependency. requirements.txt pins no crypto library."""
    imported = _imported_modules(SECURITY_SOURCE)
    forbidden = {"passlib", "bcrypt", "argon2", "argon2_cffi", "jwt", "jose", "cryptography"}
    assert not (imported & forbidden), imported & forbidden
    assert imported <= {"__future__", "base64", "hashlib", "hmac", "secrets", "operator_auth"}, imported


def test_s6_no_new_requirement_was_added():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for package in ("passlib", "bcrypt", "argon2", "pyjwt", "python-jose", "cryptography"):
        assert package not in requirements, f"{package} must not be added"


def test_s6_no_dependency_on_the_owner_module():
    """Deliberate duplication, not a cross-module dependency (AGENTS.md)."""
    for path in (SECURITY_SOURCE, ENUMS_SOURCE):
        assert "owner" not in _imported_modules(path)


def test_s6_security_module_does_not_import_fastapi():
    """The cookie helpers are duck-typed, like owner/security.py."""
    assert "fastapi" not in _imported_modules(SECURITY_SOURCE)
    assert "starlette" not in _imported_modules(SECURITY_SOURCE)


def test_s6_no_logging_of_secrets():
    source = SECURITY_SOURCE.read_text(encoding="utf-8")
    for forbidden in ("logging", "logger", "print(", "sys.stdout", "sys.stderr"):
        assert forbidden not in source, (
            f"security.py must not contain {forbidden!r}: a password or a raw "
            "session token must never reach a log"
        )


def test_s6_no_hard_coded_secret_material():
    source = SECURITY_SOURCE.read_text(encoding="utf-8")
    assert not re.search(r"(?i)(password|secret|token)\s*=\s*['\"][^'\"]{3,}['\"]", source)


def test_s6_no_database_access():
    imported = _imported_modules(SECURITY_SOURCE) | _imported_modules(ENUMS_SOURCE)
    assert "psycopg2" not in imported
    assert "database" not in imported


# ---------------------------------------------------------------------------
# Scope: Task 3 must not reach into Tasks 4, 5 or 6.
# ---------------------------------------------------------------------------

def test_no_later_operator_auth_module_exists():
    """Fail-closed: a router, dependencies or schemas module here would mean a
    later task had started. Updated by Task 4 (context, exceptions,
    permissions) and Task 5 (database, repository, service)."""
    package = ROOT / "operator_auth"
    present = sorted(item.name for item in package.glob("*.py"))
    assert present == [
        "__init__.py",
        "context.py",
        "database.py",
        "dependencies.py",
        "enums.py",
        "exceptions.py",
        "permissions.py",
        "repository.py",
        "router.py",
        "schemas.py",
        "security.py",
        "service.py",
    ], present


# ===========================================================================
# Task 4 - context semantics and the permission matrix
#
# Structural properties (frozen, field lists, protocol) live in
# tests/test_p26_1_scope_enforcement.py. What follows is behaviour.
# ===========================================================================

OWNER = "agency_owner"
ADMIN = "agency_admin"
AGENT = "agent"


def _ctx(**overrides) -> context.OperatorContext:
    values = {
        "user_id": 1,
        "agency_id": 10,
        "role": OWNER,
        "is_platform_admin": False,
        "session_id": 100,
        "auth_channel": "operator_session",
    }
    values.update(overrides)
    return context.OperatorContext(**values)


# ---------------------------------------------------------------------------
# OperatorContext behaviour
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "role,platform,expected",
    [
        (OWNER, False, True),
        (ADMIN, False, True),
        (AGENT, False, False),
        (None, False, False),
        (None, True, True),
        (AGENT, True, True),
    ],
)
def test_operator_context_sees_all_agency_records(role, platform, expected):
    assert _ctx(role=role, is_platform_admin=platform).sees_all_agency_records is expected


@pytest.mark.parametrize(
    "role,platform,expected",
    [
        (OWNER, False, True),
        (ADMIN, False, True),
        (AGENT, False, False),
        (None, False, False),
        (None, True, True),
    ],
)
def test_operator_context_may_assign_records(role, platform, expected):
    assert _ctx(role=role, is_platform_admin=platform).may_assign_records is expected


def test_require_agency_returns_the_bound_agency():
    assert _ctx(agency_id=42).require_agency() == 42


def test_require_agency_raises_for_an_unbound_platform_admin():
    ctx = _ctx(user_id=None, agency_id=None, role=None, is_platform_admin=True)
    with pytest.raises(exceptions.PlatformAdminAgencyRequired):
        ctx.require_agency()


def test_require_agency_raises_for_any_unbound_context():
    """The condition is 'no agency bound', not 'is a platform admin'."""
    with pytest.raises(exceptions.PlatformAdminAgencyRequired):
        _ctx(agency_id=None, is_platform_admin=False).require_agency()


def test_require_agency_accepts_agency_id_zero_is_not_confused_with_none():
    """A falsy-but-present id must not be treated as unbound."""
    assert _ctx(agency_id=0).require_agency() == 0


def test_legacy_basic_channel_is_agency_bound_and_never_platform_admin():
    """Spec D-2: the shared credential maps to Default-Agency owner only."""
    legacy = _ctx(user_id=None, agency_id=1, role=OWNER,
                  is_platform_admin=False, session_id=None,
                  auth_channel="legacy_basic")
    assert legacy.is_platform_admin is False
    assert legacy.user_id is None
    assert legacy.require_agency() == 1
    assert legacy.sees_all_agency_records is True


def test_declared_auth_channels():
    assert context.AUTH_CHANNELS == ("operator_session", "legacy_basic")


# ---------------------------------------------------------------------------
# SystemAgencyContext behaviour
# ---------------------------------------------------------------------------

def test_system_context_require_agency_returns_the_agency():
    assert context.SystemAgencyContext(agency_id=7, origin="public_stima").require_agency() == 7


def test_system_context_require_agency_never_raises():
    ctx = context.SystemAgencyContext(agency_id=7, origin="public_stima")
    for _ in range(3):
        assert ctx.require_agency() == 7


def test_system_context_origin_public_stima_is_accepted():
    ctx = context.SystemAgencyContext(agency_id=7, origin="public_stima")
    assert ctx.origin == "public_stima"
    assert ctx.origin in context.SYSTEM_CONTEXT_ORIGINS


def test_system_context_origin_set_is_closed_to_one_value_in_p26_1():
    assert context.SYSTEM_CONTEXT_ORIGINS == ("public_stima",)


def test_system_context_rejects_an_unapproved_origin():
    """Fail closed. P26-1 entitles exactly one flow to a system-owned scope."""
    with pytest.raises(ValueError):
        context.SystemAgencyContext(agency_id=1, origin="anything_else")


@pytest.mark.parametrize("origin", ["", "PUBLIC_STIMA", "public_stima ", "admin", None, 0])
def test_system_context_rejects_every_other_origin_shape(origin):
    with pytest.raises(ValueError):
        context.SystemAgencyContext(agency_id=1, origin=origin)


def test_system_context_origin_validation_names_the_permitted_value():
    with pytest.raises(ValueError, match="public_stima"):
        context.SystemAgencyContext(agency_id=1, origin="anything_else")


def test_system_context_never_sees_across_agencies():
    """is_platform_admin is False, so no scope builder can widen it."""
    ctx = context.SystemAgencyContext(agency_id=7, origin="public_stima")
    assert ctx.is_platform_admin is False
    assert ctx.role is None
    assert ctx.user_id is None


# ---------------------------------------------------------------------------
# Permission matrix - approved design spec section 13
# ---------------------------------------------------------------------------

ALL_ROLES = [OWNER, ADMIN, AGENT, None]


@pytest.mark.parametrize("role", ALL_ROLES)
def test_may_create_agency_is_platform_admin_only(role):
    assert permissions.may_create_agency(role, False) is False
    assert permissions.may_create_agency(role, True) is True


@pytest.mark.parametrize(
    "role,expected", [(OWNER, True), (ADMIN, False), (AGENT, False), (None, False)]
)
def test_may_change_agency_owner(role, expected):
    assert permissions.may_change_agency_owner(role, False) is expected
    assert permissions.may_change_agency_owner(role, True) is True


@pytest.mark.parametrize(
    "role,expected", [(OWNER, True), (ADMIN, True), (AGENT, False), (None, False)]
)
def test_sees_all_agency_records(role, expected):
    assert permissions.sees_all_agency_records(role, False) is expected
    assert permissions.sees_all_agency_records(role, True) is True


@pytest.mark.parametrize(
    "role,expected", [(OWNER, True), (ADMIN, True), (AGENT, False), (None, False)]
)
def test_may_assign_records(role, expected):
    assert permissions.may_assign_records(role, False) is expected
    assert permissions.may_assign_records(role, True) is True


@pytest.mark.parametrize("target", [OWNER, ADMIN, AGENT])
def test_platform_admin_may_manage_every_membership_role(target):
    assert permissions.may_manage_membership(None, True, target) is True


@pytest.mark.parametrize("target", [OWNER, ADMIN, AGENT])
def test_agency_owner_may_manage_every_membership_role(target):
    assert permissions.may_manage_membership(OWNER, False, target) is True


def test_agency_admin_may_manage_agents_only():
    """This is the whole of 'admin LIMITED' (spec section 13)."""
    assert permissions.may_manage_membership(ADMIN, False, AGENT) is True
    assert permissions.may_manage_membership(ADMIN, False, ADMIN) is False
    assert permissions.may_manage_membership(ADMIN, False, OWNER) is False


@pytest.mark.parametrize("target", [OWNER, ADMIN, AGENT])
def test_agent_manages_nobody(target):
    assert permissions.may_manage_membership(AGENT, False, target) is False


@pytest.mark.parametrize("target", [OWNER, ADMIN, AGENT])
def test_a_roleless_non_platform_context_manages_nobody(target):
    assert permissions.may_manage_membership(None, False, target) is False


@pytest.mark.parametrize("actor", ALL_ROLES)
def test_an_unknown_target_role_is_never_manageable(actor):
    """Fail closed: an unrecognised target is refused even for a platform admin."""
    for target in ("platform_admin", "superuser", "", None, "AGENT"):
        assert permissions.may_manage_membership(actor, True, target) is False
        assert permissions.may_manage_membership(actor, False, target) is False


@pytest.mark.parametrize("actor", ALL_ROLES)
def test_an_unknown_actor_role_grants_nothing(actor):
    for unknown in ("platform_admin", "superuser", "AgencyOwner", ""):
        assert permissions.sees_all_agency_records(unknown, False) is False
        assert permissions.may_assign_records(unknown, False) is False
        assert permissions.may_change_agency_owner(unknown, False) is False
        assert permissions.may_create_agency(unknown, False) is False


def test_context_properties_delegate_to_the_permission_functions():
    """One source of truth: the matrix is not restated inside the dataclass."""
    for role in ALL_ROLES:
        for platform in (False, True):
            ctx = _ctx(role=role, is_platform_admin=platform)
            assert ctx.sees_all_agency_records is permissions.sees_all_agency_records(
                role, platform
            )
            assert ctx.may_assign_records is permissions.may_assign_records(
                role, platform
            )


# ===========================================================================
# Task 5 - repository and service
#
# Offline. A recording fake cursor stands in for psycopg2, in the style of
# BridgeCursor in tests/test_public_stima_core_crm_bridge.py. It emulates the
# SQL predicates in Python so that revoked / expired / idle sessions are
# genuinely exercised, and every statement is recorded so the tests can assert
# what actually reached the database layer.
#
# Because a fake could mask a missing predicate, the SQL text is asserted
# separately (see the REPOSITORY SQL block at the end).
# ===========================================================================

import contextlib
import pathlib
from datetime import datetime, timedelta, timezone

from operator_auth import repository, service

NOW = lambda: datetime.now(timezone.utc)


# Hashed once for the whole module. PBKDF2 at 600k iterations costs ~0.1s, and
# the fixture is built for every test in this section; recomputing it each time
# would add several seconds to the suite for no additional coverage. The
# hashing itself is asserted in the S2 block above.
_OWNER_PASSWORD = "pw-owner"
_OWNER_PASSWORD_HASH = security.hash_password(_OWNER_PASSWORD)


def _database(**overrides):
    """A single active agency, one active owner, no session yet."""
    data = {
        "operators": [
            {
                "id": 1,
                "email_normalized": "owner@example.test",
                "password_hash": _OWNER_PASSWORD_HASH,
                "status": "active",
                "is_platform_admin": False,
            }
        ],
        "memberships": [
            {"operator_user_id": 1, "agency_id": 10, "role": "agency_owner",
             "status": "active"}
        ],
        # name is NOT NULL in migration 027, so the fake carries one too:
        # a fixture that omits it would let _joined() silently yield None and
        # leave the agency_name projection unproven.
        "agencies": [{"id": 10, "name": "STIMA360", "status": "active"}],
        "sessions": [],
    }
    data.update(overrides)
    return data


class FakeCursor:
    """Records every statement and emulates the predicates the SQL declares."""

    def __init__(self, db):
        self.db = db
        self.calls: list[tuple[str, object]] = []
        self._rows: list[dict] = []
        self.rowcount = 0

    # -- helpers ----------------------------------------------------------
    def _operator(self, operator_id):
        return next((o for o in self.db["operators"] if o["id"] == operator_id), None)

    def _membership(self, operator_id):
        return next(
            (m for m in self.db["memberships"]
             if m["operator_user_id"] == operator_id and m["status"] == "active"),
            None,
        )

    def _agency(self, agency_id):
        return next((a for a in self.db["agencies"] if a["id"] == agency_id), None)

    def _joined(self, operator):
        membership = self._membership(operator["id"])
        agency = self._agency(membership["agency_id"]) if membership else None
        return {
            "agency_id": membership["agency_id"] if membership else None,
            "role": membership["role"] if membership else None,
            "membership_status": membership["status"] if membership else None,
            "agency_name": agency["name"] if agency else None,
            "agency_status": agency["status"] if agency else None,
        }

    # -- cursor protocol ---------------------------------------------------
    def execute(self, sql, params=None):
        norm = " ".join(str(sql).split()).lower()
        self.calls.append((norm, params))
        self._rows = []
        self.rowcount = 0

        if "from operator_users" in norm and "email_normalized" in norm:
            email = params[0]
            operator = next(
                (o for o in self.db["operators"] if o["email_normalized"] == email),
                None,
            )
            if operator:
                self._rows = [{**operator, **self._joined(operator)}]
            return

        if "update operator_users" in norm and "last_login_at" in norm:
            self.rowcount = 1
            return

        if "insert into operator_sessions" in norm:
            operator_user_id, token_hash, expires_at = params
            row = {
                "id": len(self.db["sessions"]) + 1,
                "operator_user_id": operator_user_id,
                "token_hash": token_hash,
                "expires_at": expires_at,
                "last_seen_at": NOW(),
                "revoked_at": None,
            }
            self.db["sessions"].append(row)
            self._rows = [dict(row)]
            return

        if "from operator_sessions s" in norm:
            token_hash, idle_minutes = params
            now = NOW()
            for session in self.db["sessions"]:
                if session["token_hash"] != token_hash:
                    continue
                if session["revoked_at"] is not None:
                    continue
                if session["expires_at"] <= now:
                    continue
                if session["last_seen_at"] <= now - timedelta(minutes=int(idle_minutes)):
                    continue
                operator = self._operator(session["operator_user_id"])
                if operator is None:
                    continue
                self._rows = [{
                    "session_id": session["id"],
                    "expires_at": session["expires_at"],
                    "last_seen_at": session["last_seen_at"],
                    "user_id": operator["id"],
                    "user_status": operator["status"],
                    "is_platform_admin": operator["is_platform_admin"],
                    **self._joined(operator),
                }]
                break
            return

        if "update operator_sessions" in norm and "set last_seen_at" in norm:
            session_id = params[0]
            for session in self.db["sessions"]:
                if session["id"] == session_id:
                    session["last_seen_at"] = NOW()
                    self.rowcount = 1
            return

        if "update operator_sessions" in norm and "set revoked_at" in norm:
            token_hash = params[0]
            for session in self.db["sessions"]:
                if session["token_hash"] == token_hash and session["revoked_at"] is None:
                    session["revoked_at"] = NOW()
                    self.rowcount += 1
            return

        if "from agency_memberships" in norm:
            agency_id, operator_user_id = params
            match = any(
                m for m in self.db["memberships"]
                if m["agency_id"] == agency_id
                and m["operator_user_id"] == operator_user_id
                and m["status"] == "active"
            )
            self._rows = [{"exists": True}] if match else []
            return

        raise AssertionError(f"unexpected statement: {norm}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


@pytest.fixture
def db():
    return _database()


@pytest.fixture
def cursor(db):
    return FakeCursor(db)


@pytest.fixture
def wired(monkeypatch, cursor):
    """Point the service at the fake cursor and record commit intent."""
    commits: list[bool] = []

    def fake_operator_cursor(*, commit=False):
        commits.append(commit)

        @contextlib.contextmanager
        def _cm():
            yield (None, cursor)

        return _cm()

    monkeypatch.setattr(service, "operator_cursor", fake_operator_cursor)
    return {"cursor": cursor, "commits": commits}


def _sql_params(cursor, needle):
    return [params for sql, params in cursor.calls if needle in sql]


# ---------------------------------------------------------------------------
# LOGIN
# ---------------------------------------------------------------------------

def test_login_returns_a_raw_token(wired):
    token = service.login("owner@example.test", "pw-owner")
    assert isinstance(token, str) and len(token) >= 43


def test_login_persists_only_the_token_hash(wired):
    token = service.login("owner@example.test", "pw-owner")
    inserted = _sql_params(wired["cursor"], "insert into operator_sessions")
    assert len(inserted) == 1
    _, token_hash, _ = inserted[0]
    assert token_hash == security.hash_session_token(token)
    assert token_hash != token


def test_login_never_sends_the_raw_token_to_the_database(wired):
    token = service.login("owner@example.test", "pw-owner")
    for sql, params in wired["cursor"].calls:
        assert token not in str(params), sql
        assert token not in sql


def test_login_never_sends_the_password_to_the_database(wired):
    service.login("owner@example.test", "pw-owner")
    for sql, params in wired["cursor"].calls:
        assert "pw-owner" not in str(params), sql


def test_login_sets_expiry_from_the_session_lifetime(wired):
    service.login("owner@example.test", "pw-owner")
    _, _, expires_at = _sql_params(wired["cursor"], "insert into operator_sessions")[0]
    delta = expires_at - NOW()
    assert timedelta(hours=enums.SESSION_MAX_HOURS) - timedelta(minutes=1) < delta
    assert delta <= timedelta(hours=enums.SESSION_MAX_HOURS)


def test_login_updates_last_login_at(wired):
    service.login("owner@example.test", "pw-owner")
    assert _sql_params(wired["cursor"], "last_login_at")


def test_login_commits(wired):
    service.login("owner@example.test", "pw-owner")
    assert wired["commits"] == [True]


def test_login_normalises_the_email(wired):
    service.login("  OWNER@Example.TEST  ", "pw-owner")
    looked_up = _sql_params(wired["cursor"], "email_normalized")[0]
    assert looked_up[0] == "owner@example.test"


@pytest.mark.parametrize(
    "mutate,email,password",
    [
        (lambda d: None, "nobody@example.test", "pw-owner"),          # unknown email
        (lambda d: None, "owner@example.test", "wrong"),              # wrong password
        (lambda d: d["operators"][0].update(status="disabled"),
         "owner@example.test", "pw-owner"),                            # disabled user
        (lambda d: d["memberships"].clear(),
         "owner@example.test", "pw-owner"),                            # no membership
        (lambda d: d["memberships"][0].update(status="suspended"),
         "owner@example.test", "pw-owner"),                            # suspended membership
        (lambda d: d["memberships"][0].update(status="revoked"),
         "owner@example.test", "pw-owner"),                            # revoked membership
        (lambda d: d["agencies"][0].update(status="suspended"),
         "owner@example.test", "pw-owner"),                            # suspended agency
        (lambda d: d["agencies"][0].update(status="archived"),
         "owner@example.test", "pw-owner"),                            # archived agency
    ],
)
def test_login_failures_are_indistinguishable(wired, db, mutate, email, password):
    mutate(db)
    with pytest.raises(exceptions.AuthenticationFailed) as caught:
        service.login(email, password)
    assert str(caught.value) == exceptions.AUTHENTICATION_FAILED_MESSAGE


def test_every_login_failure_raises_the_same_type_and_message(wired, db):
    messages = set()
    for mutate, email, password in [
        (lambda: None, "nobody@example.test", "pw-owner"),
        (lambda: None, "owner@example.test", "wrong"),
    ]:
        mutate()
        with pytest.raises(exceptions.AuthenticationFailed) as caught:
            service.login(email, password)
        messages.add(str(caught.value))
    assert len(messages) == 1


def test_login_failure_writes_no_session(wired, db):
    with pytest.raises(exceptions.AuthenticationFailed):
        service.login("nobody@example.test", "pw-owner")
    assert db["sessions"] == []
    assert not _sql_params(wired["cursor"], "insert into operator_sessions")


def test_platform_admin_without_membership_can_log_in(wired, db):
    db["operators"][0]["is_platform_admin"] = True
    db["memberships"].clear()
    assert service.login("owner@example.test", "pw-owner")


# ---------------------------------------------------------------------------
# LOGIN - timing oracle protection
# ---------------------------------------------------------------------------

def test_verify_password_runs_even_when_the_email_is_unknown(wired, monkeypatch):
    seen = []
    real = security.verify_password
    monkeypatch.setattr(
        service.security, "verify_password",
        lambda raw, stored: seen.append(stored) or real(raw, stored),
    )
    with pytest.raises(exceptions.AuthenticationFailed):
        service.login("nobody@example.test", "pw-owner")
    assert len(seen) == 1, "an unknown email must still pay the hashing cost"
    assert seen[0].startswith("pbkdf2_sha256$")


def test_the_dummy_hash_is_precomputed_not_regenerated_per_login(wired, monkeypatch):
    seen = []
    real = security.verify_password
    monkeypatch.setattr(
        service.security, "verify_password",
        lambda raw, stored: seen.append(stored) or real(raw, stored),
    )
    for _ in range(3):
        with pytest.raises(exceptions.AuthenticationFailed):
            service.login("nobody@example.test", "pw-owner")
    assert len(set(seen)) == 1, "the dummy hash must be fixed, not per-request"
    assert seen[0] == service._DUMMY_PASSWORD_HASH


def test_hash_password_is_never_called_during_a_failed_login(wired, monkeypatch):
    def explode(*_a, **_k):
        raise AssertionError("hash_password must not run on the login path")

    monkeypatch.setattr(service.security, "hash_password", explode)
    with pytest.raises(exceptions.AuthenticationFailed):
        service.login("nobody@example.test", "pw-owner")


def test_the_dummy_hash_has_the_same_shape_and_cost_as_a_real_one(wired):
    algorithm, iterations, salt, digest = service._DUMMY_PASSWORD_HASH.split("$")
    assert algorithm == "pbkdf2_sha256"
    assert int(iterations) == enums.PBKDF2_ITERATIONS
    assert salt and digest


def test_no_password_verifies_against_the_dummy_hash(wired):
    for candidate in ("", "pw-owner", "password", service._DUMMY_PASSWORD_HASH):
        assert security.verify_password(candidate, service._DUMMY_PASSWORD_HASH) is False


# ---------------------------------------------------------------------------
# SESSION RESOLUTION
# ---------------------------------------------------------------------------

def _issue(wired, email="owner@example.test", password="pw-owner"):
    return service.login(email, password)


def test_valid_session_resolves_the_full_scope(wired):
    token = _issue(wired)
    ctx = service.context_from_token(token)
    assert ctx.user_id == 1
    assert ctx.agency_id == 10
    assert ctx.role == "agency_owner"
    assert ctx.is_platform_admin is False
    assert ctx.auth_channel == "operator_session"
    assert ctx.session_id is not None


def test_platform_admin_without_membership_resolves_unbound(wired, db):
    db["operators"][0]["is_platform_admin"] = True
    db["memberships"].clear()
    ctx = service.context_from_token(_issue(wired))
    assert ctx.is_platform_admin is True
    assert ctx.agency_id is None
    assert ctx.role is None


def test_none_token_resolves_to_none(wired):
    assert service.context_from_token(None) is None
    assert service.context_from_token("") is None


def test_unknown_token_resolves_to_none(wired):
    assert service.context_from_token("not-a-real-token") is None


def test_revoked_session_is_rejected(wired, db):
    token = _issue(wired)
    db["sessions"][0]["revoked_at"] = NOW()
    assert service.context_from_token(token) is None


def test_expired_session_is_rejected(wired, db):
    token = _issue(wired)
    db["sessions"][0]["expires_at"] = NOW() - timedelta(seconds=1)
    assert service.context_from_token(token) is None


def test_idle_session_is_rejected(wired, db):
    token = _issue(wired)
    db["sessions"][0]["last_seen_at"] = NOW() - timedelta(
        minutes=enums.SESSION_IDLE_MINUTES + 1
    )
    assert service.context_from_token(token) is None


def test_disabled_user_is_rejected_on_the_next_request(wired, db):
    token = _issue(wired)
    db["operators"][0]["status"] = "disabled"
    assert service.context_from_token(token) is None


def test_suspended_membership_is_rejected_on_the_next_request(wired, db):
    token = _issue(wired)
    db["memberships"][0]["status"] = "suspended"
    assert service.context_from_token(token) is None


def test_suspended_agency_is_rejected_on_the_next_request(wired, db):
    token = _issue(wired)
    db["agencies"][0]["status"] = "suspended"
    assert service.context_from_token(token) is None


def test_successful_resolution_touches_last_seen_at(wired):
    token = _issue(wired)
    wired["cursor"].calls.clear()
    service.context_from_token(token)
    assert _sql_params(wired["cursor"], "set last_seen_at")


def test_failed_resolution_does_not_touch_last_seen_at(wired, db):
    token = _issue(wired)
    db["operators"][0]["status"] = "disabled"
    wired["cursor"].calls.clear()
    assert service.context_from_token(token) is None
    assert not _sql_params(wired["cursor"], "set last_seen_at")


def test_two_sequential_resolutions_both_succeed(wired):
    token = _issue(wired)
    assert service.context_from_token(token) is not None
    assert service.context_from_token(token) is not None


# ---------------------------------------------------------------------------
# LOGOUT
# ---------------------------------------------------------------------------

def test_logout_revokes_the_hashed_token(wired, db):
    token = _issue(wired)
    service.logout(token)
    assert db["sessions"][0]["revoked_at"] is not None
    revoked = _sql_params(wired["cursor"], "set revoked_at")
    assert revoked[0][0] == security.hash_session_token(token)


def test_logout_never_sends_the_raw_token(wired):
    token = _issue(wired)
    wired["cursor"].calls.clear()
    service.logout(token)
    for sql, params in wired["cursor"].calls:
        assert token not in str(params)


def test_logout_of_an_unknown_token_is_silent(wired):
    assert service.logout("never-issued") is None


def test_logout_of_none_is_silent_and_touches_nothing(wired):
    wired["cursor"].calls.clear()
    assert service.logout(None) is None
    assert service.logout("") is None
    assert wired["cursor"].calls == []


def test_logout_leaves_the_session_unusable(wired):
    token = _issue(wired)
    service.logout(token)
    assert service.context_from_token(token) is None


# ---------------------------------------------------------------------------
# REPOSITORY - no connection, and the SQL says what it must
# ---------------------------------------------------------------------------

def test_repository_opens_no_connection():
    source = (ROOT / "operator_auth" / "repository.py").read_text(encoding="utf-8")
    for forbidden in ("operator_cursor", "get_connection", "psycopg2", "connect("):
        assert forbidden not in source, f"repository.py references {forbidden!r}"


def test_repository_functions_all_take_a_cursor_first():
    import inspect

    for name, function in inspect.getmembers(repository, inspect.isfunction):
        if name.startswith("_"):
            continue
        first = list(inspect.signature(function).parameters)[0]
        assert first == "cur", f"{name} must take an open cursor first, got {first!r}"


def test_resolve_session_sql_carries_every_required_predicate(cursor):
    repository.resolve_session(cursor, "0" * 64, 240)
    sql = cursor.calls[-1][0]
    assert "from operator_sessions s" in sql
    assert "join operator_users u" in sql
    assert "left join agency_memberships m" in sql
    assert "left join agencies a" in sql
    assert "m.status = 'active'" in sql
    assert "s.token_hash = %s" in sql
    assert "s.revoked_at is null" in sql
    assert "s.expires_at > now()" in sql
    assert "last_seen_at" in sql


def test_resolve_session_membership_join_is_a_left_join(cursor):
    """A platform admin legitimately has no membership row."""
    repository.resolve_session(cursor, "0" * 64, 240)
    sql = cursor.calls[-1][0]
    assert "left join agency_memberships" in sql
    assert "inner join agency_memberships" not in sql


def test_create_session_writes_no_agency_id(cursor):
    repository.create_session(cursor, 1, "a" * 64, NOW() + timedelta(hours=12))
    sql, params = cursor.calls[-1]
    assert "insert into operator_sessions" in sql
    assert "agency_id" not in sql, "spec D-3: the session pins no agency"
    assert len(params) == 3


def test_no_repository_statement_writes_agency_id_into_operator_sessions():
    source = (ROOT / "operator_auth" / "repository.py").read_text(encoding="utf-8")
    lowered = " ".join(source.lower().split())
    start = lowered.find("insert into operator_sessions")
    assert start != -1
    assert "agency_id" not in lowered[start:start + 400]


def test_revoke_session_matches_on_the_hash_and_returns_a_count(cursor, db):
    db["sessions"].append({
        "id": 1, "operator_user_id": 1, "token_hash": "b" * 64,
        "expires_at": NOW() + timedelta(hours=1), "last_seen_at": NOW(),
        "revoked_at": None,
    })
    assert repository.revoke_session(cursor, "b" * 64) == 1
    assert repository.revoke_session(cursor, "c" * 64) == 0


def test_membership_exists_is_scoped_to_agency_and_operator(cursor):
    assert repository.membership_exists(cursor, 10, 1) is True
    assert repository.membership_exists(cursor, 99, 1) is False
    assert repository.membership_exists(cursor, 10, 99) is False


_LOG_METHODS = {"info", "warning", "warn", "error", "debug", "exception", "critical", "log"}


def _emits_output(path):
    """Return (imported_log_modules, output_calls) found in a module.

    AST rather than substring matching: a module docstring may legitimately
    explain that it does no logging, and prose must not trip a rule about what
    the code does. This is the third rule in this suite to need that
    distinction, so it is made structurally rather than by wording the comment
    around the assertion.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules, calls = set(), []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {a.name.split(".")[0] for a in node.names} & {"logging", "sys"}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules |= {node.module.split(".")[0]} & {"logging", "sys"}
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "print":
                calls.append("print")
            elif isinstance(func, ast.Attribute) and func.attr in _LOG_METHODS:
                calls.append(func.attr)
    return modules, calls


def test_the_output_scanner_ignores_docstrings_but_not_code(tmp_path):
    """Negative control: prose about logging must not register as logging."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        '"""This module deliberately does no logging and never calls print."""\n'
        "def f():\n    return 1\n",
        encoding="utf-8",
    )
    assert _emits_output(probe) == (set(), [])
    probe.write_text("import logging\nlogging.getLogger(__name__).info('x')\n", encoding="utf-8")
    modules, calls = _emits_output(probe)
    assert "logging" in modules and "info" in calls


@pytest.mark.parametrize("module", ["service.py", "repository.py", "security.py"])
def test_no_module_on_the_credential_path_emits_output(module):
    """A password, a raw token or a failure cause must never reach a log."""
    modules, calls = _emits_output(ROOT / "operator_auth" / module)
    assert not modules, f"{module} imports {modules}"
    assert not calls, f"{module} calls {calls}"


# ===========================================================================
# Task 6 - HTTP adapter
#
# FastAPI, Starlette and Pydantic are absent from the offline sandbox, so the
# behavioural tests below are guarded with pytest.importorskip and run in the
# project's real virtualenv. The structural tests above and in this section are
# AST/source based and run everywhere; they are what protects the properties
# that must never regress silently (no Basic fallback, no client-supplied
# agency, closed response projection).
#
# Run the guarded tests with the project venv:
#     python -m pytest -q tests/test_p26_1_operator_auth.py -k http_
# ===========================================================================

ROUTER_SOURCE = ROOT / "operator_auth" / "router.py"
DEPS_SOURCE = ROOT / "operator_auth" / "dependencies.py"
SCHEMAS_SOURCE = ROOT / "operator_auth" / "schemas.py"
EXC_SOURCE = ROOT / "operator_auth" / "exceptions.py"

FORBIDDEN_IN_ME = ("email", "password_hash", "session_id", "token_hash", "token")


def _executable_source(path) -> str:
    """Return a module's code with every docstring and comment removed.

    Forbidden-identifier rules must describe what the code *does*. Four rules in
    this suite have now been tripped by a docstring that merely explains the
    rule - naming ADMIN_USER to say the Basic branch is deferred, naming
    agency_id to say it is untrusted. Rewording the prose each time would be
    fixing the symptom; stripping the prose is the fix.
    """
    import ast as _ast

    tree = _ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    for node in _ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (_ast.Module, _ast.ClassDef, _ast.FunctionDef,
                             _ast.AsyncFunctionDef)) and body:
            first = body[0]
            if (isinstance(first, _ast.Expr)
                    and isinstance(first.value, _ast.Constant)
                    and isinstance(first.value.value, str)):
                node.body = body[1:] or [_ast.Pass()]
    return _ast.unparse(tree)



# ---------------------------------------------------------------------------
# Exception cleanup (structural, runs offline)
# ---------------------------------------------------------------------------

def test_authentication_failed_lives_in_exceptions_not_service():
    service_classes = [
        node.name
        for node in ast.walk(ast.parse((ROOT / "operator_auth" / "service.py").read_text()))
        if isinstance(node, ast.ClassDef)
    ]
    assert "AuthenticationFailed" not in service_classes, (
        "the exception is shared with the HTTP adapter; it belongs in exceptions.py"
    )
    assert issubclass(exceptions.AuthenticationFailed, Exception)
    assert exceptions.AUTHENTICATION_FAILED_MESSAGE == "Credenziali non valide."


def test_exceptions_module_stays_pure_domain_code():
    source = _executable_source(EXC_SOURCE)
    for forbidden in ("fastapi", "starlette", "HTTPException", "status_code", "pydantic"):
        assert forbidden not in source, f"exceptions.py references {forbidden!r}"


def test_authentication_failed_defaults_to_the_generic_message():
    assert str(exceptions.AuthenticationFailed()) == exceptions.AUTHENTICATION_FAILED_MESSAGE


# ---------------------------------------------------------------------------
# Structural guards on the HTTP layer (run offline)
# ---------------------------------------------------------------------------

def test_login_request_declares_only_email_and_password():
    tree = ast.parse(SCHEMAS_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "LoginRequest":
            fields = [n.target.id for n in node.body if isinstance(n, ast.AnnAssign)]
            assert fields == ["email", "password"], fields
            return
    raise AssertionError("LoginRequest not found")


def test_request_schema_forbids_extra_fields():
    source = SCHEMAS_SOURCE.read_text(encoding="utf-8")
    assert 'extra = "forbid"' in source


def test_me_response_declares_exactly_the_approved_projection():
    tree = ast.parse(SCHEMAS_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "MeResponse":
            fields = [n.target.id for n in node.body if isinstance(n, ast.AnnAssign)]
            assert fields == [
                "user_id", "agency_id", "agency_name", "role",
                "is_platform_admin", "expires_at",
            ], fields
            for banned in ("email", "password_hash", "session_id", "token_hash"):
                assert banned not in fields
            return
    raise AssertionError("MeResponse not found")


def test_me_handler_builds_its_response_field_by_field():
    """No dict spread: a new database column must not be able to leak."""
    source = ROUTER_SOURCE.read_text(encoding="utf-8")
    assert "**" not in source.split("def me(")[1], (
        "/me must not splat a row into its response"
    )


def test_router_declares_the_three_approved_endpoints():
    source = ROUTER_SOURCE.read_text(encoding="utf-8")
    assert 'prefix="/api/operator-auth"' in source
    assert 'tags=["operator-auth"]' in source
    for route in ('"/login"', '"/logout"', '"/me"'):
        assert route in source


def test_login_and_logout_declare_no_auth_dependency():
    """They must be reachable without a session, or login is impossible."""
    tree = ast.parse(ROUTER_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in {"login", "logout"}:
            defaults = [d for d in node.args.defaults]
            for default in defaults:
                assert not (
                    isinstance(default, ast.Call)
                    and getattr(default.func, "id", None) == "Depends"
                ), f"{node.name} must not depend on authentication"


def test_me_depends_on_require_operator():
    tree = ast.parse(ROUTER_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "me":
            called = {
                default.args[0].id
                for default in node.args.defaults
                if isinstance(default, ast.Call) and default.args
            }
            assert "require_operator" in called, called
            return
    raise AssertionError("/me handler not found")


def test_router_never_logs():
    modules, calls = _emits_output(ROUTER_SOURCE)
    assert not modules and not calls, (modules, calls)


def test_session_path_reads_only_the_cookie():
    """The session channel takes the cookie and nothing else.

    Task 15 gave require_operator a second, legacy channel that must read the
    Authorization header, so this rule now targets the cookie resolver where
    it always belonged. Narrowed in target, unchanged in strength.
    """
    code = _function_source(DEPS_SOURCE, "optional_session")
    assert "request.cookies.get(COOKIE_NAME)" in code
    for forbidden in ("query_params", "path_params", "request.headers", "json()"):
        assert forbidden not in code, f"optional_session reads {forbidden!r}"


def test_the_cookie_is_read_in_exactly_one_place():
    """One reader of the cookie's VALUE means one place to get the rules wrong.

    P26-3 added a second function that names COOKIE_NAME, and the distinction
    between the two is exactly what this test now pins. `optional_session`
    resolves the token: it is the only code that may turn a cookie into a
    session, and therefore the only place the session rules live.
    `session_was_presented` answers a strictly poorer question - was there a
    cookie at all - which it needs so that a REFUSED cookie does not fall
    through to the legacy credential.

    That second function must stay poor. If it ever did anything with the
    value, there would be two readers again and one of them unreviewed, so the
    assertions below are on its shape: a single return of a `bool(...)`, and no
    binding of the value to a name that could carry it further.
    """
    tree = ast.parse(DEPS_SOURCE.read_text(encoding="utf-8"))
    readers = [
        node.name for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and "COOKIE_NAME" in ast.unparse(node)
    ]
    assert readers == ["optional_session", "session_was_presented"], readers

    presence = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "session_was_presented"
    )
    statements = [n for n in presence.body if not (
        isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
    )]
    assert len(statements) == 1 and isinstance(statements[0], ast.Return), (
        "session_was_presented must be one return statement and nothing else"
    )
    returned = statements[0].value
    assert (isinstance(returned, ast.Call)
            and getattr(returned.func, "id", None) == "bool"), (
        "session_was_presented must return a bool, so the cookie's value "
        f"cannot escape it: {ast.unparse(returned)}"
    )
    assert presence.returns is not None and ast.unparse(presence.returns) == "bool"


def test_the_legacy_channel_does_not_parse_the_header_by_hand():
    """The credential arrives through FastAPI's HTTPBasic scheme.

    An earlier form of this branch parsed the Authorization header itself.
    That authenticated correctly but silently dropped the route's OpenAPI
    `security` declaration, because a plain function is not a SecurityBase -
    which is how /api/core briefly came to look unauthenticated to anything
    reading the schema. The scheme is now declared, so the rule changed from
    "reads only this header" to "does not touch headers at all".
    """
    code = _function_source(DEPS_SOURCE, "_verify_legacy_credentials")
    for forbidden in ("request.headers", "b64decode", "query_params",
                      "path_params", "json()", "cookies"):
        assert forbidden not in code, f"_verify_legacy_credentials reads {forbidden!r}"
    assert "require_admin(credentials)" in code, (
        "the comparison must stay delegated to admin_security"
    )


def test_the_basic_scheme_is_declared_so_openapi_stays_honest():
    from operator_auth import dependencies
    from fastapi.security.base import SecurityBase

    assert isinstance(dependencies._basic_scheme, SecurityBase)
    assert dependencies._basic_scheme.auto_error is False, (
        "an absent credential is not yet a failure; the cookie branch may win"
    )


def test_the_401_matches_the_certified_legacy_contract():
    """Byte-identical to what require_admin returned before P26-1."""
    from operator_auth import dependencies

    assert dependencies.NOT_AUTHENTICATED_MESSAGE == "Non autorizzato"
    assert dependencies.BASIC_CHALLENGE == {
        "WWW-Authenticate": 'Basic realm="STIMA360 Admin"'
    }


def _function_source(path, name: str) -> str:
    """One function's executable source, docstring removed."""
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
            return ast.unparse(node)
    raise AssertionError(f"no function named {name!r}")


def _session_path_source() -> str:
    """The two operator-session functions, executable source only.

    Task 11 added `legacy_basic_agency_context` to this module: the C2
    compatibility bridge that gives a legacy-Basic route an agency-bound scope
    (approved amendment, design spec D-2). The rules below were written against
    the whole module when the cookie branch was all it contained. They are
    narrowed in *target* to the session path, not relaxed in strength - Basic
    must still never produce an operator *session* scope.
    """
    tree = ast.parse(DEPS_SOURCE.read_text(encoding="utf-8"))
    parts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("current_session", "optional_session"):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
            parts.append(ast.unparse(node))
    assert len(parts) == 2, "both session-channel functions must be present"
    return "\n".join(parts)


def test_session_path_has_no_basic_fallback():
    code = _session_path_source()
    for forbidden in ("HTTPBasic", "ADMIN_USER", "ADMIN_PASS", "legacy_basic", "b64decode"):
        assert forbidden not in code, f"the session path must not use Basic; found {forbidden!r}"


def test_the_compatibility_context_is_the_only_basic_aware_function():
    """Exactly one function in this module may know about the legacy channel."""
    tree = ast.parse(DEPS_SOURCE.read_text(encoding="utf-8"))
    basic_aware = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node = ast.parse(ast.unparse(node)).body[0]
                node.body = node.body[1:] or [ast.Pass()]
            if "legacy_basic" in ast.unparse(node) or "require_admin" in ast.unparse(node):
                basic_aware.append(node.name)
    # P26-3 split the choosing from the building: `require_operator` no longer
    # names the legacy channel at all - it asks `_scope_from_session_or_basic`
    # for a scope and refuses if there is none, and that helper names neither
    # `legacy_basic` nor `require_admin` either. Five functions know about the
    # channel, each for exactly one reason, and the list is in source order so
    # that a new one cannot be slipped in unnoticed.
    assert basic_aware == [
        "require_authenticated_operator",  # admits either channel, no scope
        "_default_agency_context",         # builds the legacy scope
        "basic_only_agency_context",       # the same scope, for a Basic-only mount
        "_verify_legacy_credentials",      # verifies the credential, via require_admin
        "legacy_basic_agency_context",     # refuses the way require_admin used to
    ], basic_aware


def test_session_path_does_not_construct_a_context():
    """A session scope must come from the database, never be assembled here.

    `legacy_basic_agency_context` does construct one, deliberately and from a
    server-side lookup; it is asserted separately in
    tests/test_p26_1_scope_enforcement.py (the C2 block).
    """
    code = _session_path_source()
    assert "OperatorContext(" not in code
    assert "session_from_token" in _executable_source(DEPS_SOURCE)


def test_require_operator_returns_only_a_context_or_raises():
    """Two channels, two returns - and every one of them yields a scope.

    Task 15 added the legacy branch, so the single-return assertion is replaced
    by the property it was standing in for: no path returns anything but an
    OperatorContext, and the fall-through raises rather than returning None.
    """
    tree = ast.parse(DEPS_SOURCE.read_text(encoding="utf-8"))
    node = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "require_operator"
    )
    # P26-3: the two channels moved into `_scope_from_session_or_basic`, so
    # this function now has one return - and the property being asserted is
    # unchanged: it yields a context or raises, never None.
    returns = [ast.unparse(n.value) for n in ast.walk(node) if isinstance(n, ast.Return)]
    assert returns == ["context"], returns
    raises = [ast.unparse(n) for n in ast.walk(node) if isinstance(n, ast.Raise)]
    assert any("401" in r for r in raises), raises
    assert "None" not in returns

    # And the helper it delegates to is the one that knows both channels.
    helper = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_scope_from_session_or_basic"
    )
    # ast.walk is breadth-first, so compare the set: what matters is that the
    # only things it can produce are the two scopes and None.
    helper_returns = {ast.unparse(n.value) for n in ast.walk(helper) if isinstance(n, ast.Return)}
    assert helper_returns == {"session.context", "_default_agency_context()", "None"}, helper_returns


def test_router_is_mounted_in_main():
    """Task 15 wired it in. This guard fired then, as designed, and is now
    retired into its positive form: mounted exactly once, with no router-level
    dependency, so /login stays reachable unauthenticated."""
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "from operator_auth.router import router as operator_auth_router" in main_source
    mounts = [
        line for line in main_source.splitlines()
        if "include_router(operator_auth_router" in line
    ]
    assert len(mounts) == 1, mounts
    assert "dependencies" not in mounts[0], mounts[0]


# ---------------------------------------------------------------------------
# Behavioural HTTP tests - require FastAPI, so guarded for the project venv
# ---------------------------------------------------------------------------

@pytest.fixture
def http_client(wired):
    pytest.importorskip("fastapi", reason="FastAPI is absent from the offline sandbox")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from operator_auth.router import router as operator_router

    app = FastAPI()
    app.include_router(operator_router)
    # https, not http. The session cookie is deliberately Secure, so a
    # standards-conforming client stores it but refuses to send it back over a
    # plaintext origin - which is the whole point of the flag. Testing over
    # http://testserver would silently exercise a cookie-less request path and
    # prove nothing about the authenticated endpoints.
    return TestClient(app, base_url="https://testserver")


def _set_cookie_header(response):
    return response.headers.get("set-cookie", "")


def test_http_login_returns_204_with_an_empty_body(http_client):
    response = http_client.post(
        "/api/operator-auth/login",
        json={"email": "owner@example.test", "password": "pw-owner"},
    )
    assert response.status_code == 204
    assert response.content == b""


def test_http_login_sets_the_cookie_with_the_full_policy(http_client):
    response = http_client.post(
        "/api/operator-auth/login",
        json={"email": "owner@example.test", "password": "pw-owner"},
    )
    header = _set_cookie_header(response).lower()
    assert "stima360_operator_session=" in header
    assert "httponly" in header
    assert "secure" in header
    assert "samesite=lax" in header
    assert "path=/" in header
    assert "max-age=43200" in header


def test_http_login_puts_the_raw_token_only_in_the_cookie(http_client):
    response = http_client.post(
        "/api/operator-auth/login",
        json={"email": "owner@example.test", "password": "pw-owner"},
    )
    token = http_client.cookies.get("stima360_operator_session")
    assert token
    assert token.encode() not in response.content
    for name, value in response.headers.items():
        if name.lower() != "set-cookie":
            assert token not in value


def test_http_login_failure_is_401_with_the_generic_message(http_client):
    response = http_client.post(
        "/api/operator-auth/login",
        json={"email": "owner@example.test", "password": "wrong"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Credenziali non valide."
    assert "set-cookie" not in {k.lower() for k in response.headers}


def test_http_unknown_email_is_indistinguishable_from_a_wrong_password(http_client):
    wrong = http_client.post(
        "/api/operator-auth/login",
        json={"email": "owner@example.test", "password": "wrong"},
    )
    unknown = http_client.post(
        "/api/operator-auth/login",
        json={"email": "nobody@example.test", "password": "pw-owner"},
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


@pytest.mark.parametrize(
    "extra",
    [
        {"agency_id": 2},
        {"role": "agency_owner"},
        {"is_platform_admin": True},
        {"session_id": 1},
        {"whatever": "x"},
    ],
)
def test_http_login_rejects_any_undeclared_field(http_client, extra):
    response = http_client.post(
        "/api/operator-auth/login",
        json={"email": "owner@example.test", "password": "pw-owner", **extra},
    )
    assert response.status_code == 422, response.text


def test_http_logout_revokes_and_returns_204(http_client, db):
    http_client.post(
        "/api/operator-auth/login",
        json={"email": "owner@example.test", "password": "pw-owner"},
    )
    response = http_client.post("/api/operator-auth/logout")
    assert response.status_code == 204
    assert db["sessions"][0]["revoked_at"] is not None


def test_http_logout_without_a_cookie_is_204(http_client):
    http_client.cookies.clear()
    assert http_client.post("/api/operator-auth/logout").status_code == 204


def test_http_logout_with_an_unknown_cookie_is_204(http_client):
    http_client.cookies.set("stima360_operator_session", "never-issued")
    assert http_client.post("/api/operator-auth/logout").status_code == 204


def test_http_logout_clears_the_cookie(http_client):
    http_client.post(
        "/api/operator-auth/login",
        json={"email": "owner@example.test", "password": "pw-owner"},
    )
    response = http_client.post("/api/operator-auth/logout")
    header = _set_cookie_header(response).lower()
    assert "stima360_operator_session=" in header
    assert "path=/" in header


def test_http_me_without_a_cookie_is_401(http_client):
    http_client.cookies.clear()
    assert http_client.get("/api/operator-auth/me").status_code == 401


def test_http_me_with_a_revoked_session_is_401(http_client):
    http_client.post(
        "/api/operator-auth/login",
        json={"email": "owner@example.test", "password": "pw-owner"},
    )
    http_client.post("/api/operator-auth/logout")
    assert http_client.get("/api/operator-auth/me").status_code == 401


def test_http_me_returns_the_approved_projection(http_client, db):
    db["agencies"][0]["name"] = "STIMA360"
    http_client.post(
        "/api/operator-auth/login",
        json={"email": "owner@example.test", "password": "pw-owner"},
    )
    body = http_client.get("/api/operator-auth/me").json()
    assert set(body) == {
        "user_id", "agency_id", "agency_name", "role",
        "is_platform_admin", "expires_at",
    }
    assert body["user_id"] == 1
    assert body["agency_id"] == 10
    assert body["agency_name"] == "STIMA360"
    assert body["role"] == "agency_owner"
    assert body["is_platform_admin"] is False


def test_http_me_discloses_no_secret_or_identifier(http_client):
    http_client.post(
        "/api/operator-auth/login",
        json={"email": "owner@example.test", "password": "pw-owner"},
    )
    response = http_client.get("/api/operator-auth/me")
    body = response.json()
    for banned in FORBIDDEN_IN_ME:
        assert banned not in body
    assert "owner@example.test" not in response.text
    assert "pbkdf2_sha256$" not in response.text
    token = http_client.cookies.get("stima360_operator_session")
    assert token not in response.text


def test_http_me_for_a_platform_admin_without_membership(http_client, db):
    db["operators"][0]["is_platform_admin"] = True
    db["memberships"].clear()
    http_client.post(
        "/api/operator-auth/login",
        json={"email": "owner@example.test", "password": "pw-owner"},
    )
    body = http_client.get("/api/operator-auth/me").json()
    assert body["is_platform_admin"] is True
    assert body["agency_id"] is None
    assert body["agency_name"] is None
    assert body["role"] is None


def test_http_me_resolves_the_session_once_per_request(http_client, monkeypatch):
    """Both dependencies share FastAPI's per-request cache."""
    http_client.post(
        "/api/operator-auth/login",
        json={"email": "owner@example.test", "password": "pw-owner"},
    )
    calls = []
    real = service.session_from_token
    monkeypatch.setattr(
        service, "session_from_token",
        lambda token: calls.append(token) or real(token),
    )
    assert http_client.get("/api/operator-auth/me").status_code == 200
    assert len(calls) == 1, f"expected one resolution per request, got {len(calls)}"


def test_http_no_agency_selector_is_accepted(http_client):
    """A header or query agency_id must not change the resolved scope."""
    http_client.post(
        "/api/operator-auth/login",
        json={"email": "owner@example.test", "password": "pw-owner"},
    )
    body = http_client.get(
        "/api/operator-auth/me?agency_id=999", headers={"X-Agency-Id": "999"}
    ).json()
    assert body["agency_id"] == 10
