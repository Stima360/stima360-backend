# P26-1 Agency Identity & CORE Agency Scope — Implementation Plan

## Document status

- Phase: `P26-1` — **implementation plan only**. This document authorises no
  code, no migration, no database access, no commit, no push and no deploy.
- Implements: `docs/superpowers/specs/2026-09-05-p26-1-agency-identity-core-scope-design.md`
  (**APPROVED**). Where this plan and the spec disagree, the spec wins and the
  disagreement is recorded in §0.5.
- Base: branch `core-0.1-test`, HEAD `05387faa0ffcb167e006e7fe8d76374a62071282`.
  P26-0 complete; baseline `P26-BASELINE-001`; migration `026` applied on
  `stima360_db_test`.
- Commit, push and deploy are performed **manually by Giorgio**. No task below
  contains a `git commit`, `git push`, deploy or migration-execution step.
- Task 22 produces the migration *execution* runbook. It does not execute it.

---

## 0. Planning preliminaries

### 0.1 The testing model this repository actually uses

Verified before planning, because it determines the shape of every task:

- `tests/conftest.py` inserts the repository root on `sys.path` and **stubs
  `psycopg2` when the driver is absent**. The suite is designed to run without
  a database.
- Migration tests are **static SQL-text analysis**. Precedents:
  `tests/test_public_stima_sell_pipeline_migration.py` and
  `tests/test_p26_baseline_isolation.py`, whose docstring states: *"These are
  offline tests. Nothing here opens a database connection."*
- Repository tests use **hand-written fake cursors that record or pattern-match
  SQL**. Precedent: `BridgeCursor` in
  `tests/test_public_stima_core_crm_bridge.py`.
- Router tests use `fastapi.testclient.TestClient` against a throwaway app.
  Precedent: `tests/test_seller_intelligence_router.py`.

**Consequence:** every test in Tasks 1–21 is offline. Migrations are verified as
*files* here and *executed* only in Task 22's runbook, by Giorgio, against
`stima360_db_test`. No task requires `DB_HOST` to be set.

### 0.2 RED → GREEN discipline (revision 1)

**Every task ends with its own test surface GREEN.** No task commits a test that
is expected to fail in a later task. Concretely:

- A task may **begin** RED — that is the point — but its own assertions are
  green before it is handed over for review.
- A task may **extend** a shared test file, but every assertion it adds is
  green by the end of that task.
- Each migration owns a **dedicated test file** and its own full RED → GREEN
  cycle: `027` in Task 2, `028` in Task 7, `029` in Task 8, `030` in Task 9.
- Task 1 ships only reusable static-guard infrastructure, exercised against
  migration `026`, which already exists — so **Task 1 finishes GREEN**.

The cross-task carried failure of the previous draft is removed entirely.

### 0.3 Negative controls are test-only (revision 5)

No task instructs anyone to weaken, neutralise or temporarily edit production
code in order to observe a test go red. Two sanctioned mechanisms, both
confined to test execution:

1. **In-test negative control.** A deliberately-unscoped fake function or fake
   repository module defined *inside the test file*, fed to the same harness
   that guards production code. If the harness does not flag it, the harness is
   broken. This proves detection capability without touching runtime source.
2. **Natural RED ordering.** Where a leakage assertion belongs to a fix, it is
   written inside that fix's own task, before the implementation step — so its
   red state is observed naturally.

Monkeypatching a production symbol *within a test* (e.g. forcing
`core.service.bridge_public_stima` to raise) is a test double and is permitted;
editing the file on disk is not.

### 0.4 Frozen interface contract

Every task is written against these signatures. No task may change one without
amending this section first; Task 21's checkpoint re-verifies them.

```python
# operator_auth/context.py
@dataclass(frozen=True)
class OperatorContext:
    user_id: int | None
    agency_id: int | None
    role: str | None
    is_platform_admin: bool
    session_id: int | None
    auth_channel: str                      # 'operator_session' | 'legacy_basic'
    @property
    def sees_all_agency_records(self) -> bool: ...
    @property
    def may_assign_records(self) -> bool: ...
    @property
    def is_agency_bound(self) -> bool: ...   # agency_id is not None  (revision 3)
    def require_agency(self) -> int: ...      # raises PlatformAdminAgencyRequired

@dataclass(frozen=True)
class SystemAgencyContext:
    agency_id: int
    origin: str                             # 'public_stima' only
    role = None; user_id = None; is_platform_admin = False   # class attributes
    is_agency_bound = True
    def require_agency(self) -> int: ...    # returns self.agency_id, never raises

class AgencyScope(Protocol):
    agency_id: int | None
    role: str | None
    user_id: int | None
    is_platform_admin: bool
    def require_agency(self) -> int: ...

# operator_auth/exceptions.py
class PlatformAdminAgencyRequired(Exception): ...   # -> HTTP 403 (revision 3)

# operator_auth/security.py
def generate_session_token() -> str
def hash_session_token(raw: str) -> str
def hash_password(raw: str) -> str
def verify_password(raw: str, stored: str) -> bool
def set_cookie(response, token: str) -> None
def clear_cookie(response) -> None

# operator_auth/permissions.py   (pure, no I/O)
def sees_all_agency_records(role: str | None, is_platform_admin: bool) -> bool
def may_assign_records(role: str | None, is_platform_admin: bool) -> bool
def may_create_agency(role: str | None, is_platform_admin: bool) -> bool
def may_change_agency_owner(role: str | None, is_platform_admin: bool) -> bool
def may_manage_membership(role: str | None, is_platform_admin: bool,
                          target_role: str) -> bool

# operator_auth/repository.py   (all take an open cursor; none opens one)
def find_operator_by_email(cur, email_normalized: str) -> dict | None
def mark_login(cur, operator_user_id: int) -> None
def create_session(cur, operator_user_id: int, token_hash: str, expires_at) -> dict
def resolve_session(cur, token_hash: str, idle_minutes: int) -> dict | None
def touch_session(cur, session_id: int) -> None
def revoke_session(cur, token_hash: str) -> int
def membership_exists(cur, agency_id: int, operator_user_id: int) -> bool

# operator_auth/service.py
def login(email: str, password: str) -> str            # returns the raw token
def logout(raw_token: str | None) -> None
def context_from_token(raw_token: str | None) -> OperatorContext | None

# operator_auth/dependencies.py
def require_operator(request, credentials) -> OperatorContext

# core/scope.py
SCOPED_TABLES: frozenset = {"contacts", "leads", "activities", "tasks"}
AGENT_ASSIGNABLE: frozenset = {"contacts", "leads"}
SYSTEM_CONTEXT_FUNCTIONS: frozenset = {"bridge_public_stima"}
def scoped_source(ctx: AgencyScope, table: str, alias: str) -> tuple[str, list]
def scoped_predicate(ctx: AgencyScope, table: str, alias: str) -> tuple[str, list]
def system_context_for_public_stima(cur) -> SystemAgencyContext

# core/repository.py — ctx prepended to every scoped function
def list_contacts(ctx, limit, offset, search, status)
def get_contact(ctx, contact_id);      def create_contact(ctx, data)
def update_contact(ctx, contact_id, data)
def add_contact_role(ctx, contact_id, data)
def delete_contact_role(ctx, contact_id, role)
def create_lead(ctx, data);  def list_leads(ctx, limit, offset, contact_id, pipeline, stage, status)
def get_lead(ctx, lead_id);  def update_lead(ctx, lead_id, data)
def link_stima(ctx, lead_id, stima_id, relation_type)
def unlink_stima(ctx, lead_id, stima_id)
def create_activity(ctx, data);  def list_activities(ctx, limit, offset, contact_id, lead_id, stima_id)
def delete_activity(ctx, activity_id)
def create_task(ctx, data);  def list_tasks(ctx, limit, offset, contact_id, lead_id, stima_id, status)
def update_task(ctx, task_id, data);  def delete_task(ctx, task_id)
def set_contact_assignment(ctx, contact_id, assigned_agent_id)
def set_lead_assignment(ctx, lead_id, assigned_agent_id)
def bridge_public_stima(stima_id, contact_data, lead_data, relation_type)   # SIGNATURE UNCHANGED
```

`scoped_predicate` returns just `("<alias>.agency_id = %s [AND …]", params)` for
statements that build their own `FROM`/`UPDATE`/`DELETE` clause;
`scoped_source` wraps it with the table and `WHERE`. Both are emitted by the
same code path, so neither can produce an empty predicate (revision 2).

`bridge_public_stima` is the single member of `SYSTEM_CONTEXT_FUNCTIONS`: it
builds its own `SystemAgencyContext` and takes no `ctx` parameter (spec
§2.5.1 rule 4).

### 0.5 Corrections to the approved spec and to the previous plan draft

Clerical only; none changes a decision. Recorded rather than applied silently.

1. **Spec Appendix A says `crm/service.py` has "five call sites".** Audited
   count is **four**: lines 8, 11, 44, 45.
2. **Spec §5.2 says `028_down` drops "eleven columns".** Audited count is
   **ten**: `contacts` and `leads` three each, `activities` and `tasks` two each.
3. **Previous plan draft named four `run_*` scripts for Task 18.** Audited
   count is **two**. `run_integration_01_e2e.py` and
   `run_integration_01_regression.py` create their CORE data **through the
   API**, not by direct `INSERT` — verified: zero
   `INSERT INTO contacts|leads` in either file. Only `run_buy_021_e2e.py`
   (2 inserts, 4 deletes) and `run_flow_01_e2e.py` (2 inserts, 2 deletes) touch
   these tables directly.
4. **Previous plan draft said "five test operators".** The enumerated fixture
   is **six**: `owner_a`, `admin_a`, `agent_a`, `agent_a2`, `owner_b`,
   `platform_admin` (revision 6).

### 0.6 New test files (13)

| File | Owning task(s) |
|---|---|
| `tests/test_p26_1_migration_rules.py` | 1 |
| `tests/test_p26_1_migration_027.py` | 2 |
| `tests/test_p26_1_migration_028.py` | 7 |
| `tests/test_p26_1_migration_029.py` | 8 |
| `tests/test_p26_1_migration_030.py` | 9 |
| `tests/test_p26_1_operator_auth.py` | 3, 4, 5, 6 (incremental, each green) |
| `tests/test_p26_1_scope_enforcement.py` | 4, 10, 11, 15 (incremental, each green) |
| `tests/test_p26_1_core_isolation.py` | 11, 12, 16, 19 (incremental, each green) |
| `tests/test_p26_1_public_stima_routing.py` | 13, 20 |
| `tests/test_p26_1_agency_integrity.py` | 14 |
| `tests/test_p26_1_legacy_basic_surface.py` | 15, 16 |
| `tests/test_p26_1_seed_and_fixtures.py` | 17, 18 |
| `tests/test_p26_1_runbook_certification.py` | 22, 23 |

Migration filenames:

```
migrations/027_p26_agency_identity.sql        + 027_p26_agency_identity_down.sql
migrations/028_p26_core_agency_columns.sql    + 028_p26_core_agency_columns_down.sql
migrations/029_p26_core_agency_backfill.sql   + 029_p26_core_agency_backfill_down.sql
migrations/030_p26_core_agency_enforce.sql    + 030_p26_core_agency_enforce_down.sql
```

### 0.7 Task dependency graph

```
T1 ─┬─> T2 ──> T7 ──> T8 ──> T9 ─┬─> T14 ─────────────┐
    │                             │                    │
    ├─> T3 ──> T4 ─┬─> T5 ──> T6 ─┴─> T15 ─┬─> T16 ────┤
    │              │                       │           │
    │              └─> T10 ──> T11 ─┬─> T12 ┴───────────┼─> T19 ──> T21 ──> T22 ──> T23
    │                               │                   │
    │                               └─> T13 ──> T20 ────┤
    │                                                   │
    └─> T17 ──> T18 ───────────────────────────────────>┘
```

Critical path: **T1 → T3 → T4 → T10 → T11 → T13 → T20 → T21 → T22 → T23**.

T2/T7/T8/T9 (SQL) and T3–T6 (auth) are independent after T1. T14 needs T9 and
T11. T16 needs T15 (route walk) and T11 (scoped search). T18 needs T17 (the
`agencies` slug the fixtures resolve).

---

## Task 1 — Reusable static migration-guard infrastructure

**Goal.** A tested, reusable assertion helper for P26-0 §6 migration rules.
**This task finishes GREEN** (revision 1): it validates against `026`, which
already exists.

**Files.** New: `tests/test_p26_1_migration_rules.py`.

**Interface.** Module-level helper
`assert_p26_migration_rules(version, *, expect_reversible, directory=None)`,
importable by Tasks 2/7/8/9. Reads `migrations/<version>.sql` and
`migrations/<version>_down.sql` as text. Opens no connection. `directory`
exists so the negative cases can be exercised against `tmp_path`.

**Transaction ownership (atomicity addendum).** From version `027` the runner
owns the UP transaction: `scripts/p26_migrate.py` executes the migration body,
writes the `schema_migrations` row through `register()`, and commits both
together. So an UP file at or above `027` contains **no `BEGIN;` and no
`COMMIT;`**, while every `_down` file — executed manually, never by the runner
— always brackets itself. Versions below `027` keep P26-0 rule 3 unchanged, so
`026` is untouched. See `docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md`.

1. **Failing test.** In `tests/test_p26_1_migration_rules.py`: a test that the
   helper **rejects** a synthetic non-compliant migration written to `tmp_path`
   (a pre-`027` file missing `COMMIT;`, a `027`+ file that brackets itself,
   one containing `CONCURRENTLY`, one containing
   `DELETE FROM schema_migrations`, one missing its `_down` sibling, one whose
   `_down` does not bracket itself) — one `pytest.raises(AssertionError)` case
   per rule; and tests that the helper **accepts** `026_p26_baseline` and a
   runner-owned synthetic `027`. Plus a pure-function test that
   `p26_migrate.verify_contiguous` raises `MigrationError` on a synthetic
   `[026, 028]` sequence and passes on `[026, 027]`.
2. **Exact test command.**
   `python -m pytest -q tests/test_p26_1_migration_rules.py`
3. **Expected failure.** `NameError: name 'assert_p26_migration_rules' is not
   defined` — the helper does not exist yet.
4. **Minimal implementation.** Write the helper in the same test module
   (test-only code; it is not production and does not belong in `scripts/`).
   It asserts: both files exist; transaction ownership by version — below
   `027` the up file opens `BEGIN;` and closes `COMMIT;`, at or above `027` it
   contains neither; the down file always contains both; no `CONCURRENTLY`; no
   `DELETE FROM schema_migrations`; no `current_database()` compared against a
   hard-coded database name; no key matching
   `(?i)(pass|secret|token|key|smtp|api)` written into `agencies.settings`;
   and, when `expect_reversible` is `False`, that the down file contains
   `RAISE EXCEPTION`.
5. **Verification command.**
   `python -m pytest -q tests/test_p26_1_migration_rules.py tests/test_p26_baseline_isolation.py`
6. **Expected PASS.** Both files green. **Task 1 ends fully GREEN.** It asserts
   nothing about `027`–`030`, which do not yet exist.
7. **Review checkpoint.** Confirm the module contains **no** assertion naming
   `027`, `028`, `029` or `030`. Confirm the helper is exercised by both a
   positive case (`026`) and four negative cases — a guard never shown to
   reject is not a guard.

---

## Task 2 — Migration `027`: agency / operator / session schema

**Goal.** The four identity tables and the Default Agency row. Own RED → GREEN.

**Files.** New: `migrations/027_p26_agency_identity.sql`,
`migrations/027_p26_agency_identity_down.sql`,
`tests/test_p26_1_migration_027.py`.

**Interface.** Inputs: none (additive DDL). Outputs: `agencies`,
`operator_users`, `agency_memberships`, `operator_sessions`; one `agencies` row
with `slug='stima360'`. The `schema_migrations` row is written by the runner's
`register()`, **not** by the migration file — it supplies `checksum_up`,
`checksum_down`, `down_available`, `transactional` and `execution_ms`, none of
which a file can know about itself, and a self-insert would collide with
`register()` on the `version` primary key. The UP file therefore carries no
ledger insert and no `BEGIN;`/`COMMIT;`; the `_down` file keeps both.

1. **Failing test.** `tests/test_p26_1_migration_027.py` calls
   `assert_p26_migration_rules("027_p26_agency_identity", expect_reversible=True)`
   then asserts: `operator_users` carries `UNIQUE (email_normalized)` and no
   agency-prefixed variant (spec D-4); `agency_memberships` carries **exactly
   one** `UNIQUE` constraint, `(agency_id, operator_user_id)`, and no
   three-column superset including `id` (spec §3.2 retraction); both partial
   unique indexes present with their exact `WHERE` clauses;
   `operator_sessions` has **no** `agency_id` column (spec D-3); the `agencies`
   insert is guarded by `WHERE NOT EXISTS`; `is_platform_admin` is on
   `operator_users` and absent from the `agency_memberships` role CHECK; no
   `NOT NULL` on any `agency_id`; the down file drops the four tables in
   FK-reverse order.
2. **Exact test command.**
   `python -m pytest -q tests/test_p26_1_migration_027.py`
3. **Expected failure.** `AssertionError: migrations/027_p26_agency_identity.sql
   does not exist`.
4. **Minimal implementation.** The two SQL files exactly as spec §3.1, §3.2 and
   §4. **No operator user, no password, no credential is created here** —
   seeding is Task 17.
5. **Verification command.**
   `python -m pytest -q tests/test_p26_1_migration_027.py tests/test_p26_1_migration_rules.py`
6. **Expected PASS.** Both green. **Task 2 ends GREEN.**
7. **Review checkpoint.** Read the SQL against spec §3.1/§3.2 line by line.
   Confirm `BIGSERIAL`/`BIGINT` throughout; `settings JSONB NOT NULL DEFAULT
   '{}'::jsonb` with no key written; `password_hash` CHECK is
   `LIKE 'pbkdf2_sha256$%'`; down order is `operator_sessions,
   agency_memberships, operator_users, agencies`.

---

## Task 3 — `operator_auth` pure security primitives

**Goal.** Password hashing, session-token hashing, cookie helpers. Pure, no I/O.

**Files.** New: `operator_auth/__init__.py`, `operator_auth/enums.py`,
`operator_auth/security.py`, `tests/test_p26_1_operator_auth.py`.

**Interface.** Per §0.4. `enums.py` exports `COOKIE_NAME =
'stima360_operator_session'`, `SESSION_MAX_HOURS = 12`,
`SESSION_IDLE_MINUTES = 240`, `PBKDF2_ITERATIONS = 600_000`,
`AGENCY_ROLES = ('agency_owner', 'agency_admin', 'agent')`,
`DEFAULT_AGENCY_SLUG = 'stima360'` (spec §7.1).

1. **Failing test.** Spec §15 items 21–22 plus cookie policy: `hash_password`
   output starts `pbkdf2_sha256$`, never equals the input, and differs across
   two calls on the same input (distinct salt); `verify_password` is `True` for
   the right password, `False` for the wrong one, and `False` — never raising —
   for a malformed stored value; `generate_session_token` returns ≥32 URL-safe
   chars and differs across calls; `hash_session_token` returns 64 lowercase hex
   and is deterministic; `set_cookie` emits `httponly`, `secure`,
   `samesite=lax`, `path=/`, `max-age=43200`; `clear_cookie` deletes the same
   name.
2. **Exact test command.**
   `python -m pytest -q tests/test_p26_1_operator_auth.py`
3. **Expected failure.** `ModuleNotFoundError: No module named 'operator_auth'`.
4. **Minimal implementation.** `operator_auth/security.py` using **only the
   standard library** (`hashlib.pbkdf2_hmac`, `secrets`, `hmac`, `base64`) —
   spec D-7 forbids a new dependency. Format
   `pbkdf2_sha256$<iterations>$<b64 salt>$<b64 hash>`; `hmac.compare_digest`
   verification; 16-byte salt. Cookie helpers mirror `owner/security.py` with
   `secure=True` unconditional (spec §7.2).
5. **Verification command.** Same as (2).
6. **Expected PASS.** The file is green. **Task 3 ends GREEN** — it adds no
   assertion about login, sessions or routers.
7. **Review checkpoint.** `grep -n "passlib\|bcrypt\|argon2" requirements.txt
   operator_auth/security.py` returns nothing. No password or token reaches
   `logging`. `security.py` imports nothing from `owner/` (spec §7.2:
   deliberate duplication, no cross-module dependency).

---

## Task 4 — `OperatorContext`, `SystemAgencyContext`, permission predicates

**Goal.** Both frozen context types, the `AgencyScope` protocol, and the pure
permission predicate table.

**Files.** New: `operator_auth/context.py`, `operator_auth/exceptions.py`,
`operator_auth/permissions.py`, `tests/test_p26_1_scope_enforcement.py`.
Extended: `tests/test_p26_1_operator_auth.py`.

**Interface.** Per §0.4. The exception is `PlatformAdminAgencyRequired`, the
name used by the approved design spec (§8). An intermediate plan draft called
it `PlatformAdminAgencyRequired`; the spec is authoritative and that rename is
withdrawn. The condition it signals is "this context is not bound to an
agency", which in P26-1 only a platform admin without a membership can reach.

**Ordering note.** `system_context_for_public_stima` lives in `core/scope.py`
and is created in **Task 10**. This task creates only the *type* it returns.
Nothing here imports `core/scope.py`.

1. **Failing test.** Spec §15 item 50 plus the §13 matrix: both dataclasses are
   frozen (`FrozenInstanceError`); `SystemAgencyContext` has no `user_id`,
   `role` or `is_platform_admin` **field** (assert on `dataclasses.fields`)
   while exposing them as class attributes valued `None`, `None`, `False`;
   `SystemAgencyContext.require_agency()` returns the int and never raises;
   `SystemAgencyContext.is_agency_bound is True`;
   `OperatorContext(agency_id=None, is_platform_admin=True)` has
   `is_agency_bound is False` and `require_agency()` raises
   `PlatformAdminAgencyRequired`; `sees_all_agency_records` `True` for
   `agency_owner`/`agency_admin`/platform-admin, `False` for `agent`;
   `may_assign_records` `False` for `agent`, `True` otherwise;
   `may_create_agency` `True` **only** for platform admin;
   `may_change_agency_owner` `True` for platform admin and `agency_owner` only;
   `may_manage_membership` `True` for `agency_admin` **only** when
   `target_role == 'agent'`, `True` for `agency_owner`/platform admin for all
   three targets, `False` for `agent` always (spec §13 "admin LIMITED").
2. **Exact test command.**
   `python -m pytest -q tests/test_p26_1_operator_auth.py tests/test_p26_1_scope_enforcement.py`
3. **Expected failure.**
   `ModuleNotFoundError: No module named 'operator_auth.context'`.
4. **Minimal implementation.** The two frozen dataclasses, the `AgencyScope`
   `Protocol`, `PlatformAdminAgencyRequired`, and five pure predicates.
   `permissions.py` performs no I/O and imports nothing from `core/` or
   `operator_auth/repository.py`.
5. **Verification command.** Same as (2).
6. **Expected PASS.** Both files green. **Task 4 ends GREEN.**
7. **Review checkpoint.** Confirm `SystemAgencyContext` has no field or
   parameter that could carry a role or a user. Confirm
   `python -c "import operator_auth.permissions"` succeeds with no driver
   present. Confirm the predicate table reproduces spec §13 row for row.

---

## Task 5 — `operator_auth` repository and service

**Goal.** Login, per-request session resolution, logout, and the rejection
causes.

**Files.** New: `operator_auth/database.py`, `operator_auth/repository.py`,
`operator_auth/service.py`. Extended: `tests/test_p26_1_operator_auth.py`.

**Interface.** Per §0.4. `operator_auth/database.py` provides
`operator_cursor(*, commit=False)` over `database.get_connection`, mirroring
`core/database.py:13` and the module-local convention of `followup/`,
`seller_intent/` and `property_watch/`. Repository functions take an open
cursor; the service owns the transaction.

1. **Failing test.** Recording-fake-cursor tests covering spec §15 items 9–20:
   `login` with a correct password returns a raw token, persists **only** its
   SHA-256 (assert the recorded `INSERT` params contain the hash and never the
   raw token), and updates `last_login_at`; a wrong password, unknown email,
   `operator_users.status='disabled'`, `agency_memberships.status='suspended'`,
   `agencies.status='suspended'`, and a non-platform-admin with no active
   membership all raise the **same** generic error with an identical message;
   `verify_password` is invoked even when no operator row is found (assert the
   call count — spec §7.3 step 3, the timing-oracle control);
   `context_from_token` returns `None` for revoked, expired and idle-timed-out
   sessions, and populates `agency_id`/`role` from the join for a live one; a
   platform admin with no membership yields `agency_id is None` and
   `is_agency_bound is False`; `logout` issues the revoking `UPDATE` and returns
   normally for an unknown token; `resolve_session`'s SQL contains
   `revoked_at IS NULL`, `expires_at > NOW()`, an idle predicate on
   `last_seen_at`, and `LEFT JOIN`s `agency_memberships` on `status='active'`.
2. **Exact test command.**
   `python -m pytest -q tests/test_p26_1_operator_auth.py`
3. **Expected failure.**
   `ModuleNotFoundError: No module named 'operator_auth.repository'`.
4. **Minimal implementation.** The seven repository functions with spec §7.4's
   resolution query verbatim, plus `login`, `logout`, `context_from_token`.
   `login` normalises the email with the **existing**
   `core.normalization.normalize_email` (spec §7.3 step 1 — reuse).
   `context_from_token` returns `auth_channel='operator_session'` and calls
   `touch_session`.
5. **Verification command.** Same as (2).
6. **Expected PASS.** The file is green. **Task 5 ends GREEN** — it adds no
   router assertion.
7. **Review checkpoint.** `operator_sessions` is never written with an
   `agency_id` (spec D-3). Every rejection path yields the identical message.
   No raw token or password appears in any log call or returned dict.
   `repository.py` opens no connection of its own.

---

## Task 6 — `operator_auth` router

**Goal.** `POST /login`, `POST /logout`, `GET /me`.

**Files.** New: `operator_auth/schemas.py`, `operator_auth/dependencies.py`,
`operator_auth/router.py`. Extended: `tests/test_p26_1_operator_auth.py`.

**Interface.** `APIRouter(prefix="/api/operator-auth", tags=["operator-auth"])`
with **no router-level dependency** (spec §7.3), matching `core/router.py`.
`LoginRequest(BaseModel)` with `email: str`, `password: str`,
`extra = "forbid"`. `require_operator` reads the cookie via
`Request.cookies.get(COOKIE_NAME)`; the legacy-Basic branch arrives in Task 15.

1. **Failing test.** `TestClient` against a throwaway `FastAPI()` with only this
   router mounted (the `tests/test_seller_intelligence_router.py` pattern):
   `POST /login` returns `204` with a `Set-Cookie` carrying `HttpOnly`,
   `Secure`, `SameSite=lax`; the **response body is empty** and contains the raw
   token nowhere (item 10); bad credentials return `401` with the generic
   detail; `POST /logout` returns `204` and clears the cookie; `GET /me`
   without a cookie returns `401`; with a valid session returns `user_id`,
   `agency_id`, `agency_name`, `role`, `is_platform_admin`, `expires_at` and —
   asserted explicitly — **no `email`, no `password_hash`, no `session_id`**
   (item 16); two sequential `GET /me` calls on one cookie both return `200`
   (item 20).
2. **Exact test command.**
   `python -m pytest -q tests/test_p26_1_operator_auth.py`
3. **Expected failure.**
   `ModuleNotFoundError: No module named 'operator_auth.router'`.
4. **Minimal implementation.** The three endpoints plus `require_operator`
   (cookie branch only). `login` calls `service.login`, then
   `security.set_cookie`, returning `Response(status_code=204)`.
5. **Verification command.** Same as (2).
6. **Expected PASS.** The whole file green. **Task 6 ends GREEN.**
7. **Review checkpoint.** The router is **not yet mounted in `main.py`** — that
   is Task 15; mounting it here would put an unreviewed auth surface on the
   app. No endpoint accepts an `agency_id` field. `/me` builds its response
   dict explicitly, never `**row`, so a new column cannot leak.

---

## Task 7 — Migration `028`: nullable CORE agency columns

**Goal.** Ten nullable columns and four plain indexes. Own RED → GREEN.

**Files.** New: `migrations/028_p26_core_agency_columns.sql`,
`migrations/028_p26_core_agency_columns_down.sql`,
`tests/test_p26_1_migration_028.py`.

**Interface.** Inputs: `agencies`, `operator_users` (Task 2). Outputs:
`contacts` and `leads` gain `agency_id`, `assigned_agent_id`,
`created_by_user_id`; `activities` and `tasks` gain `agency_id`,
`created_by_user_id` — **ten columns** (§0.5 correction 2).

1. **Failing test.** `tests/test_p26_1_migration_028.py` calls
   `assert_p26_migration_rules("028_p26_core_agency_columns",
   expect_reversible=True)` then asserts: exactly ten `ADD COLUMN IF NOT EXISTS`
   statements with spec §5.2's FK actions (`agency_id` → `ON DELETE RESTRICT`;
   operator references → `ON DELETE SET NULL`); `assigned_agent_id` present for
   `contacts`/`leads` and **absent** for `activities`/`tasks` (spec §3.3); **no
   `DEFAULT`** on any `agency_id` (a default would defeat `030`'s proof); no
   `NOT NULL` anywhere; the four `idx_*_agency_id` indexes present; the down
   file drops exactly ten columns and four indexes.
2. **Exact test command.**
   `python -m pytest -q tests/test_p26_1_migration_028.py`
3. **Expected failure.** `AssertionError:
   migrations/028_p26_core_agency_columns.sql does not exist`.
4. **Minimal implementation.** The two SQL files exactly as spec §5.2.
5. **Verification command.**
   `python -m pytest -q tests/test_p26_1_migration_028.py tests/test_p26_1_migration_027.py`
6. **Expected PASS.** Both green. **Task 7 ends GREEN.**
7. **Review checkpoint.** Count `ADD COLUMN` lines: exactly ten.
   `grep -ci "not null" migrations/028_p26_core_agency_columns.sql` → `0`.
   `grep -ci "default" ` on the same file → `0`.

---

## Task 8 — Migration `029`: controlled legacy backfill

**Goal.** Assign legacy rows to the Default Agency, verify zero `NULL`, and ship
a down file that refuses. Own RED → GREEN.

**Files.** New: `migrations/029_p26_core_agency_backfill.sql`,
`migrations/029_p26_core_agency_backfill_down.sql`,
`tests/test_p26_1_migration_029.py`.

**Interface.** Inputs: the ten columns (Task 7), the `agencies` row (Task 2).
Outputs: four tables fully populated; a `DO $do$` guard that raises on any
survivor.

1. **Failing test.** `tests/test_p26_1_migration_029.py` calls
   `assert_p26_migration_rules("029_p26_core_agency_backfill",
   expect_reversible=False)` — which asserts the down file contains
   `RAISE EXCEPTION` — then asserts: four `UPDATE`s, one per table, each
   resolving the agency by a **`slug = 'stima360'` subselect**, never a literal
   `id = 1` (spec §6.2); each carries `WHERE agency_id IS NULL` (P26-0 rule 4);
   a `DO $do$` block with `RAISE EXCEPTION` sums remaining `NULL`s across all
   four tables; the file writes **no** `assigned_agent_id` and no
   `created_by_user_id` (spec §6.2 — no legacy operator identity exists to
   attribute); the down file contains **no `UPDATE` and no `DELETE`** and names
   `docs/P26_BACKUP_RESTORE_TEST.md`.
2. **Exact test command.**
   `python -m pytest -q tests/test_p26_1_migration_029.py`
3. **Expected failure.** `AssertionError:
   migrations/029_p26_core_agency_backfill.sql does not exist`.
4. **Minimal implementation.** The two SQL files exactly as spec §6.2 and §16.
5. **Verification command.**
   `python -m pytest -q tests/test_p26_1_migration_029.py tests/test_p26_1_migration_028.py`
6. **Expected PASS.** Both green. **Task 8 ends GREEN.**
7. **Review checkpoint.** The down file cannot succeed. The guard sums all four
   tables, not one. Re-running the up file is a zero-row no-op.

---

## Task 9 — Migration `030`: integrity enforcement

**Goal.** `NOT NULL`, three composite FKs, nine agency-aware indexes, the
`core_agency_integrity()` trigger. Own RED → GREEN, and the `026`–`030`
contiguity assertion lands here because this is the last migration.

**Files.** New: `migrations/030_p26_core_agency_enforce.sql`,
`migrations/030_p26_core_agency_enforce_down.sql`,
`tests/test_p26_1_migration_030.py`.

**Interface.** Inputs: a backfilled schema (Task 8). Outputs: four `NOT NULL`
columns; `contacts_agency_scope_unq`; `leads_contact_same_agency_fk`,
`contacts_agent_same_agency_fk`, `leads_agent_same_agency_fk`; nine indexes;
one function; two triggers.

1. **Failing test.** `tests/test_p26_1_migration_030.py` calls
   `assert_p26_migration_rules("030_p26_core_agency_enforce",
   expect_reversible=True)` then asserts, per spec §3.4/§3.5/§5.3: four
   `SET NOT NULL`; `contacts_agency_scope_unq UNIQUE (agency_id, id)` present
   and **no `leads_agency_scope_unq`** (nothing references `leads(agency_id,
   id)` — the §3.4 removal); both assignment FKs reference
   `agency_memberships (agency_id, operator_user_id)` with **no** extra unique
   key added (spec §3.2 retraction); the nine `idx_*_agency_*` indexes present;
   file statement order is `SET NOT NULL` → `ADD CONSTRAINT` → `CREATE INDEX` →
   `CREATE TRIGGER`; constraint idempotency uses the `pg_constraint`
   catalogue-check `DO $do$` pattern; both triggers declared
   `BEFORE INSERT OR UPDATE OF agency_id, contact_id, lead_id, stima_id`; the
   down file drops triggers, function, three FKs, one unique key, nine indexes
   and four `DROP NOT NULL`. Finally:
   `p26_migrate.verify_contiguous(p26_migrate.discover_migrations())` yields
   `026, 027, 028, 029, 030`.
2. **Exact test command.**
   `python -m pytest -q tests/test_p26_1_migration_030.py`
3. **Expected failure.** `AssertionError:
   migrations/030_p26_core_agency_enforce.sql does not exist`.
4. **Minimal implementation.** The two SQL files exactly as spec §3.4, §3.5,
   §3.6 and §5.3, with `core_agency_integrity()` verbatim from spec §3.6.
5. **Verification command.**
   `python -m pytest -q tests/test_p26_1_migration_*.py`
6. **Expected PASS.** All five migration test files green. **Task 9 ends
   GREEN**, and the migration set is complete.
7. **Review checkpoint.** Read `core_agency_integrity()` against spec §3.6 step
   by step: three pairwise reference comparisons, then the explicit-value
   comparison, then derive, then the *bounded* fallback
   (`stima_id IS NOT NULL AND lead_id IS NULL AND contact_id IS NULL`), then a
   terminal `RAISE`. Confirm there is **no** early
   `IF NEW.agency_id IS NOT NULL THEN RETURN NEW` before the reference
   checks — that is the rejected derive-only form.

---

## Task 10 — `core/scope.py`

**Goal.** The mandatory predicate builder and the server-only public-stima
context factory.

**Files.** New: `core/scope.py`. Extended: `tests/test_p26_1_scope_enforcement.py`.

**Interface.** Per §0.4. `scoped_predicate` and `scoped_source` share one code
path; neither can return an empty predicate.

1. **Failing test.** `scoped_source` raises for a table outside
   `SCOPED_TABLES`; for `agency_owner` emits exactly `agency_id = %s` with one
   param; for `agent` on `contacts`/`leads` emits
   `agency_id = %s AND … assigned_agent_id = %s` with two params; for `agent`
   on `activities`/`tasks` emits the single-agency predicate only (spec §9.1);
   for a platform admin with `agency_id=None` emits `WHERE TRUE`; for a
   `SystemAgencyContext` emits the single-agency predicate and **never**
   `WHERE TRUE`; every returned `scoped_source` string contains `WHERE`; every
   returned `scoped_predicate` string is non-empty;
   `system_context_for_public_stima` declares **no parameter named
   `agency_id`** (assert via `inspect.signature`), queries on `slug` and
   `status='active'`, raises `ConflictError` when absent, and returns
   `origin='public_stima'`; `SYSTEM_CONTEXT_FUNCTIONS ==
   {"bridge_public_stima"}` — exactly one member.
2. **Exact test command.**
   `python -m pytest -q tests/test_p26_1_scope_enforcement.py`
3. **Expected failure.** `ModuleNotFoundError: No module named 'core.scope'`.
4. **Minimal implementation.** `core/scope.py` per spec §9.1, importing
   `SystemAgencyContext` from `operator_auth.context` and `ConflictError` from
   `core.exceptions`.
5. **Verification command.** Same as (2).
6. **Expected PASS.** The file is green. **Task 10 ends GREEN** — it adds no
   assertion about `core/repository.py`, which Task 11 owns.
7. **Review checkpoint.** Confirm by inspection that no branch of
   `scoped_predicate` returns an empty string. Confirm the `WHERE TRUE` branch
   is guarded by `is_platform_admin and agency_id is None`, and is therefore
   unreachable for `SystemAgencyContext`.

---

## Task 11 — Thread `ctx` through CORE, with a security-property harness

**Goal.** Every CORE read and write carries an agency predicate, proved by
testing the **security property** rather than banning table literals
(revision 2).

**Files.** Modified: `core/repository.py`, `core/service.py`, `core/router.py`,
`crm/service.py`, `tests/test_core_service_regressions.py`. Extended:
`tests/test_p26_1_scope_enforcement.py`. New:
`tests/test_p26_1_core_isolation.py`.

**Interface.** Per §0.4. `_ensure_exists` becomes
`_ensure_exists_scoped(ctx, cur, table, entity_id, label)`, the single helper
guarding `create_lead`, `link_stima`, `add_contact_role` and
`_validate_references` (spec §9.2).

### 11.1 The static-enforcement strategy (replaces the banned-literal rule)

The previous draft forbade any SQL literal matching
`FROM|JOIN|UPDATE|INTO (contacts|leads|activities|tasks)`. **That rule is
withdrawn.** It outlawed legitimate statements (`INSERT INTO contacts`,
`UPDATE contacts`, `DELETE FROM activities`) and its only escape was to hide
table names behind dynamic SQL — obfuscation adopted to satisfy a test, which
makes the code *harder* to audit. Replacement, in two layers:

**Layer A — static (AST), proves structure.**

- **A1 Coverage discovery.** Enumerate every public function in
  `core/repository.py`. Mark a function *scoped* when any string constant in
  its body mentions a `SCOPED_TABLES` name. Table names are used **only to
  discover** which functions to police — never to forbid.
- **A2 Context admission.** Every scoped function either takes `ctx` as its
  first positional parameter, or is a member of `SYSTEM_CONTEXT_FUNCTIONS`.
- **A3 Registry is singular.** `SYSTEM_CONTEXT_FUNCTIONS == {"bridge_public_stima"}`.
- **A4 Bridge shape.** `bridge_public_stima`'s first statement inside its
  `with core_cursor(...)` block is a call to `system_context_for_public_stima`.
- **A5 No agency from request data.** No function in `core/repository.py` or
  `core/service.py` reads `agency_id`, `created_by_user_id` or
  `assigned_agent_id` out of a caller-supplied mapping — assert the absence of
  `data["agency_id"]`-style `Subscript` nodes and `.get("agency_id")` calls on
  the payload parameter.
- **A6 Schemas stay closed.** No CORE request schema declares `agency_id`,
  `created_by_user_id` or `assigned_agent_id` (inspect Pydantic `__fields__`).

**Layer B — behavioural (`RecordingCursor`), proves the security property.**

A test-only cursor records every `(sql, params)` pair. A table-driven test
invokes **every** scoped repository function with fixture contexts, then
asserts on each recorded statement that touches a scoped table:

| Statement kind | Assertion |
|---|---|
| `SELECT` | the statement mentions `agency_id` **and** `ctx.agency_id` is among the bound params |
| `UPDATE` / `DELETE` | same — the predicate must be bound, not merely present in text |
| `INSERT INTO <scoped table>` | the column list includes `agency_id`, and the bound value is `ctx.agency_id` — **never** a value taken from `data` |
| platform admin, `agency_id=None` | reads are permitted unscoped; writes must not reach the cursor at all (§11.2) |

- **B1 Fail-closed registry.** The harness enumerates functions via
  `inspect.getmembers(core.repository, inspect.isfunction)` and asserts each
  public one is covered by the table. **A new, unlisted repository function
  fails the suite** — the guard fails closed on addition.
- **B2 Negative control, test-only** (§0.3). The test module defines its own
  deliberately-unscoped fake function and asserts the harness **flags** it. A
  harness never shown to reject has not been shown to detect. No production
  file is touched.

This tests what actually matters — *the executed statement carries a bound
agency predicate* — and leaves the author free to write plain, greppable SQL.

### 11.2 Platform-admin write semantics (revision 3)

Explicit and uniform:

- **Reads.** A platform admin with `agency_id is None` reads cross-agency
  (`WHERE TRUE`). Unchanged from the spec.
- **Generic CORE creates require an agency-bound context.**
  `create_contact`, `create_lead`, `create_activity` and `create_task` call
  `ctx.require_agency()`, which raises `PlatformAdminAgencyRequired` when
  `agency_id is None`. **A platform admin with no membership cannot create
  through the generic agency CORE endpoints in P26-1.** No default agency is
  inferred for a platform-admin write — inferring one would silently attribute
  data to an agency the caller never named.
- **HTTP mapping: `403`.** Under spec D-6, `403` is the code for a
  role/permission outcome on a visible entity, and `404` is reserved for
  hiding. Nothing is hidden here: the endpoint exists and the caller knows it.
  Detail: `"Questa operazione richiede un contesto di agenzia."` No third
  status code is introduced.
- A platform-specific "create inside agency X" flow is **out of scope for
  P26-1** and is not stubbed, routed or hinted at.

1. **Failing test.** *Structural* (`test_p26_1_scope_enforcement.py`): A1–A6
   above. *Behavioural* (`test_p26_1_core_isolation.py`): the Layer-B harness
   including B1 and B2; `get_contact`/`get_lead` with a foreign id raise
   `NotFoundError`; `update_*` emit `WHERE id = %s AND <predicate>` and raise
   `NotFoundError` on `rowcount == 0`; `create_contact`/`create_lead` stamp
   `agency_id` and `created_by_user_id` from `ctx`, never from `data`;
   `list_activities`/`list_tasks` filtered by `stima_id` alone still carry the
   agency predicate (spec §9.4); `contact_roles` and `lead_stime` are read only
   after the scoped parent resolves (spec §6.1); archiving via `PATCH` and lead
   closing still set `archived_at`/`closed_at` (spec §1.6 — no new endpoint);
   and per §11.2, an unbound platform-admin context raises
   `PlatformAdminAgencyRequired` from all four create functions **before any
   statement reaches the cursor** (assert the recorder is empty).
2. **Exact test command.**
   `python -m pytest -q tests/test_p26_1_scope_enforcement.py tests/test_p26_1_core_isolation.py tests/test_core_service_regressions.py`
3. **Expected failure.** `TypeError: list_contacts() missing 1 required
   positional argument: 'ctx'`, and the Layer-A coverage test failing with
   `core/repository.py: scoped function 'list_contacts' does not admit a
   context`.
4. **Minimal implementation.** Rewrite each `core/repository.py` function to
   the §9.2 shape; prepend `ctx` in `core/service.py` and forward it; take
   `ctx = Depends(require_operator)` in every `core/router.py` handler; map
   `PlatformAdminAgencyRequired → 403` in `core/router.py`'s `_translate`; update
   `crm/service.py`'s **four** CORE call sites (§0.5 correction 1); update the
   **four** existing test call sites in `tests/test_core_service_regressions.py`
   and `tests/test_public_stima_core_crm_bridge.py` to supply a context
   fixture. No behaviour change beyond scoping.
5. **Verification command.**
   `python -m pytest -q tests/test_p26_1_scope_enforcement.py tests/test_p26_1_core_isolation.py tests/test_core_service_regressions.py tests/test_next3_crm_360.py`
6. **Expected PASS.** All four green. **Task 11 ends GREEN.**
7. **Review checkpoint.** Confirm the SQL in `core/repository.py` remains
   plain and greppable — `grep -n "INSERT INTO contacts" core/repository.py`
   **should** find a match, and that is correct. Confirm `crm/service.py`'s
   non-CORE calls (`list_properties`, `list_buy_requests`, `list_matches`,
   `list_visits_by_contact`) were **not** given a `ctx`. Confirm B2's negative
   control lives in the test file and no production file was edited to produce
   a red.

---

## Task 12 — Assignment endpoints

**Goal.** The two new endpoints, with role enforcement and record-derived
membership validation.

**Files.** Modified: `core/schemas.py`, `core/repository.py`,
`core/service.py`, `core/router.py`. Extended: `tests/test_p26_1_core_isolation.py`.

**Interface.** `AssignmentUpdate(CoreModel)` with the single field
`assigned_agent_id: int | None`. Endpoints
`PATCH /api/core/contacts/{contact_id}/assignment` and
`PATCH /api/core/leads/{lead_id}/assignment`.

### 12.1 Assignment algorithm (revision 3) — one rule for every role

The previous draft validated membership against `ctx.agency_id`, which is
`None` for an unbound platform admin and therefore inconsistent. Replaced by a
**record-derived** rule that needs no role branch:

1. **Resolve the target record server-side** through `scoped_source(ctx, …)`.
   Not found under the caller's scope → `404`.
2. **Derive `target_agency_id` from the fetched record's own `agency_id`.**
   Never from `ctx.agency_id`, never from the request.
3. **Check the role:** `ctx.may_assign_records` → else `403`.
4. **Validate the target operator** with
   `membership_exists(cur, target_agency_id, assigned_agent_id)` → else `400`.
5. `assigned_agent_id = null` clears the assignment and skips step 4.

For `agency_owner`/`agency_admin`, `target_agency_id == ctx.agency_id`
necessarily, because step 1 fetched the record under their scope — so the rule
collapses to the spec's behaviour with no special case. For a platform admin it
is the *record's* agency that governs, which is the only coherent answer.
`ctx.require_agency()` is **not** called on this path, so an unbound platform
admin can assign within a resolved record's agency without a default being
inferred. Step 3 precedes step 4 but follows step 1, so a `403` never reveals
whether a foreign record exists.

1. **Failing test.** An `agent` gets `403`; an `agency_owner` assigning an
   operator of another agency gets `400`; an `agency_owner` targeting another
   agency's record gets `404`; a valid assignment returns the updated row;
   `null` clears it; a **platform admin with `agency_id=None`** assigns
   successfully when the target operator belongs to the *record's* agency, and
   gets `400` when they do not — the §12.1 rule, asserted directly; posting
   `{"assigned_agent_id": 1, "status": "archived"}` to the assignment endpoint
   returns `422`; posting `{"assigned_agent_id": 1}` to the plain
   `PATCH /contacts/{id}` returns `422` (spec §10 — the separation that stops a
   reassignment riding on an update).
2. **Exact test command.**
   `python -m pytest -q tests/test_p26_1_core_isolation.py`
3. **Expected failure.** `404 Not Found` from `TestClient` — the route does not
   exist.
4. **Minimal implementation.** `AssertionUpdate` schema; `membership_exists`
   (declared §0.4, implemented Task 5); `set_contact_assignment` /
   `set_lead_assignment` implementing §12.1; the two router handlers.
5. **Verification command.** Same as (2).
6. **Expected PASS.** The file is green. **Task 12 ends GREEN.**
7. **Review checkpoint.** `ContactUpdate` and `LeadUpdate` still lack
   `assigned_agent_id`. Confirm step order 1 → 3 → 4. Confirm no code path on
   this endpoint calls `ctx.require_agency()`. Confirm the `400` message names
   neither the target operator's agency nor their email.

---

## Task 13 — Public STIMA bridge agency routing

**Goal.** Route the bridge through `SystemAgencyContext` and close T-1.

**Files.** Modified: `core/repository.py` (`bridge_public_stima`),
`core/service.py`, `tests/test_public_stima_core_crm_bridge.py`. New:
`tests/test_p26_1_public_stima_routing.py`.

**Interface.** `bridge_public_stima(stima_id, contact_data, lead_data,
relation_type)` — **signature unchanged**. `main.py:518` is **not touched**.

1. **Failing test.** Spec §15 items 53–57c: a public estimation creates a
   contact and lead stamped with the Default Agency; an email existing **only**
   in Agency B produces a **new** Default-Agency contact and never updates B's
   row (assert both the returned ids and that no recorded `UPDATE` touched B);
   a repeat estimation still returns `already_linked`; the P18 task carrying
   `stima_id` alone resolves via the §3.6 fallback; a raising bridge still
   returns `200` from `POST /api/salva_stima` — driven by **monkeypatching
   `core.service.bridge_public_stima` to raise** (a test double per §0.3, not a
   source edit); with the Default Agency row absent the bridge raises
   `ConflictError` and the endpoint still returns `200` with
   `bridge_status=error`; and item 57c — **the recorded contact-lookup SQL
   carries a bound `agency_id`**, asserted through the Task 11 `RecordingCursor`
   rather than only on the outcome.
2. **Exact test command.**
   `python -m pytest -q tests/test_p26_1_public_stima_routing.py tests/test_public_stima_core_crm_bridge.py`
3. **Expected failure.** In the new file, `AssertionError: contact lookup SQL
   carries no bound agency predicate`. **In the existing file**, `BridgeCursor`
   returns `[]` for the identity lookups — see risk R-1.
4. **Minimal implementation.** Insert
   `ctx = system_context_for_public_stima(cur)` as the **first statement**
   inside the existing `core_cursor(commit=True)` block; route both identity
   lookups through `scoped_predicate(ctx, 'contacts', 'c')`; stamp
   `agency_id = ctx.agency_id` on both inserts, leaving `created_by_user_id`
   and `assigned_agent_id` `NULL`; extend the advisory-lock scopes to
   `core:contact:{agency_id}:email:{…}` / `…:phone:{…}`. Update
   `BridgeCursor`'s three affected branch conditions and add an `agencies`
   branch — a **fixture update, not a behaviour change**.
5. **Verification command.**
   `python -m pytest -q tests/test_p26_1_public_stima_routing.py tests/test_public_stima_core_crm_bridge.py tests/test_p26_1_scope_enforcement.py`
6. **Expected PASS.** All three green. **Task 13 ends GREEN**, including A4.
7. **Review checkpoint.** `git diff --stat main.py` is empty. The
   `already_linked` short-circuit still runs **before** the identity match. The
   `ConflictError` message contains no agency id or slug beyond
   `'default agency missing'`.

---

## Task 14 — `activities` / `tasks` integrity paths

**Goal.** Prove the `core_agency_integrity()` decision table, including every
rejection.

**Files.** New: `tests/test_p26_1_agency_integrity.py`.

**Interface.** Tests the SQL of `migrations/030_*.sql` (Task 9) and the write
paths of `core/repository.py` (Task 11). Offline: no plpgsql interpreter is
available, so the *logic* is asserted against a Python reference implementation
of the same decision table, and the *SQL* is asserted to implement that table
branch for branch.

1. **Failing test.** Spec §15 items 58–69, including the four required cases:
   `lead_id` in A + `contact_id` in B → **rejected**; explicit `agency_id` = A +
   `lead_id` in B → **rejected**; coherent explicit agency → **accepted, value
   preserved**; coherent multiple references → **accepted**. Plus: `lead_id`
   only → lead's agency; `contact_id` only → contact's agency; `stima_id` with
   a link → agency via `lead_stime`; `stima_id` with no link and both others
   `NULL` → Default Agency; `contact_id` in A + `stima_id` resolving to B →
   rejected; `UPDATE … SET lead_id = <B lead>` on an A task → rejected; an
   unresolvable shape that is **not** the fallback shape → rejected, not
   defaulted; and an ordering assertion that the trigger appears in `030` and
   nowhere in `029`.
2. **Exact test command.**
   `python -m pytest -q tests/test_p26_1_agency_integrity.py`
3. **Expected failure.** `AssertionError: no RAISE EXCEPTION found for a
   lead/contact agency mismatch` against the `030` SQL text.
4. **Minimal implementation.** None to runtime code. If Task 9's SQL already
   satisfies every assertion, this task ships **only** the test file. Any
   correction goes into `migrations/030_p26_core_agency_enforce.sql` **before**
   it is ever executed — the file is not yet in the ledger, so P26-0 rule 12
   does not bind it (see risk R-4).
5. **Verification command.**
   `python -m pytest -q tests/test_p26_1_agency_integrity.py tests/test_p26_1_migration_030.py`
6. **Expected PASS.** Both green. **Task 14 ends GREEN.**
7. **Review checkpoint.** Walk the decision table once by hand against the SQL.
   Confirm the fallback branch tests **all three** conditions — a two-condition
   version silently widens it.

---

## Task 15 — Legacy Basic transition and allowlist enforcement

**Goal.** Mount the new router, swap the CORE dependency, freeze the known
Basic cross-agency surface.

**Files.** Modified: `main.py`, `operator_auth/dependencies.py`. New:
`tests/test_p26_1_legacy_basic_surface.py`. Extended:
`tests/test_p26_1_scope_enforcement.py`.

**Interface.** `require_operator` resolves, in order: valid session cookie → a
real `OperatorContext`; else valid Basic `ADMIN_USER`/`ADMIN_PASS` →
`OperatorContext(user_id=None, agency_id=<default agency id>,
role='agency_owner', is_platform_admin=False, session_id=None,
auth_channel='legacy_basic')`; else `401`.

### 15.1 The `main.py` change constraint (revision 7)

The previous draft gated review on "exactly two changed lines". A magic line
count is brittle — an import, a reformat or a comment breaks it without any
behaviour change, and it would pass a semantically wrong edit that happened to
fit two lines. Replaced by **semantic static assertions** over `main.py`'s AST:

- **M1** `main.py` imports and mounts the `operator_auth` router exactly once.
- **M2** the `include_router` call for `core_router` carries
  `dependencies=[Depends(require_operator)]` and **not** `require_admin`.
- **M3** every **other** `include_router` call's `dependencies=` argument is
  **byte-identical to HEAD** — the test carries a frozen map of
  `router symbol → dependency expression source`, built from
  `05387fa`, and fails on any drift.
- **M4** every `@app.<method>` route decorator's `dependencies=` argument is
  byte-identical to HEAD, by the same frozen map.
- **M5** no `require_admin` or `require_owner_admin` definition is modified:
  the SHA-256 of `admin_security.py` and of `owner/router_admin.py`'s
  `require_owner_admin` function source matches the frozen value.

M3–M5 state the real constraint — *no other router or dependency behaviour
changes* — and are insensitive to formatting.

1. **Failing test.** M1–M5 above, plus spec §15 items 52, 52e and 70–73: a
   cookie-only request to every route **outside** the allowlist
   `{/api/operator-auth/*, /api/core/*}` returns `401` (route walk over
   `app.routes`); legacy Basic on `GET /api/core/contacts` never returns an
   Agency B row; legacy Basic cannot assign an Agency B operator (`400`);
   `/api/operator-auth/me` under legacy Basic reports
   `is_platform_admin = false`; the computed set of routes accepting
   `require_admin`/`require_owner_admin` **and** transitively reading a CORE
   table equals a **frozen expected list committed with the test**; and item 73
   — `GET /api/crm/contacts/{B id}/360` under legacy Basic **does** return
   data, asserted explicitly so the residual is a tested known state.
2. **Exact test command.**
   `python -m pytest -q tests/test_p26_1_legacy_basic_surface.py tests/test_p26_1_scope_enforcement.py`
3. **Expected failure.** `AssertionError: /api/core/contacts returned 401 for a
   valid operator cookie` — `core_router` still carries `require_admin`.
4. **Minimal implementation.** Mount `operator_auth_router`; swap the CORE
   router's dependency; add the legacy branch to `require_operator`.
   **`require_admin` and `require_owner_admin` are not touched. No non-CORE
   router changes.** The frozen lists are written from the spec §1.10 audit
   table and from `git show 05387fa:main.py`.
5. **Verification command.**
   `python -m pytest -q tests/test_p26_1_legacy_basic_surface.py tests/test_p26_1_scope_enforcement.py tests/test_next2_admin_security.py tests/test_next2_router_hardening.py`
6. **Expected PASS.** All four green. **Task 15 ends GREEN.**
7. **Review checkpoint.** Confirm the legacy context has
   `is_platform_admin=False` and `user_id=None`. Confirm the test file carries
   a comment naming GATE-MA1 and stating that a **shrinking** frozen surface is
   a deliberate spec change, not a test to be quietly relaxed.

---

## Task 16 — Global search and CORE leakage tests

**Goal.** Prove the §12 definition in all three of its narrow parts, with a
test-only negative control.

**Files.** Extended: `tests/test_p26_1_core_isolation.py`,
`tests/test_p26_1_legacy_basic_surface.py`.

**Interface.** No production code.

1. **Failing test.** Spec §15 items 36–37 and §12(a)(b)(c):
   `GET /api/core/contacts?search=<B contact's email>` returns empty `items` for
   `owner_a`; `GET /api/core/leads?contact_id=<B contact>` returns empty (not
   `404` — spec §12 item 2); a search term matching rows in **both** agencies
   returns only A's; the cookie-only route walk is re-asserted as clause (b);
   the frozen Basic surface as clause (c). **Negative control (§0.3):** the
   test module builds a `SearchProbe` fake repository whose `list_contacts`
   deliberately omits the agency predicate, runs the same three leakage
   assertions against it, and asserts they **fail** — proving the assertions
   can detect a leak. The fake exists only in the test module; no production
   file is modified.
2. **Exact test command.**
   `python -m pytest -q tests/test_p26_1_core_isolation.py tests/test_p26_1_legacy_basic_surface.py`
3. **Expected failure.** `AssertionError: SearchProbe negative control was not
   detected` — the control is written first and fails until the leakage
   assertions are strong enough to catch it.
4. **Minimal implementation.** Strengthen the leakage assertions until the
   negative control is detected. If a real leak surfaces, the fix belongs to
   Task 11 or 15 and that task's tests are re-run.
5. **Verification command.**
   `python -m pytest -q tests/test_p26_1_core_isolation.py tests/test_p26_1_legacy_basic_surface.py tests/test_next4_p3_global_search.py`
6. **Expected PASS.** All three green. **Task 16 ends GREEN.**
7. **Review checkpoint.** Confirm the negative control is a class in the test
   module, not a patched import of a production symbol on disk. Confirm no test
   asserts that global search is scoped against **legacy Basic** — spec §12
   explicitly does not claim that.

---

## Task 17 — TEST-only agency and operator seed script

**Goal.** Create Agency B and the **six** test operators (revision 6), outside
the migrations.

**Files.** New: `scripts/p26_seed_agencies_test.py`,
`tests/test_p26_1_seed_and_fixtures.py`.

**Interface.** CLI
`python scripts/p26_seed_agencies_test.py --operator "<name>"`. Reads six
passwords from the environment — `P26_SEED_OWNER_A_PASSWORD`,
`P26_SEED_ADMIN_A_PASSWORD`, `P26_SEED_AGENT_A_PASSWORD`,
`P26_SEED_AGENT_A2_PASSWORD`, `P26_SEED_OWNER_B_PASSWORD`,
`P26_SEED_PLATFORM_PASSWORD` — and refuses to run if any is unset. Creates
Agency B; the six operators `owner_a`, `admin_a`, `agent_a`, `agent_a2`,
`owner_b`, `platform_admin`; and **five** memberships — the `platform_admin`
gets **no** membership, which is what makes it a platform admin (spec §3.2).
Idempotent via `WHERE NOT EXISTS` on `email_normalized`.

1. **Failing test.** In `tests/test_p26_1_seed_and_fixtures.py`: the script
   calls `assert_test_database_name` before any write (assert via `ast`, not by
   executing); the source contains **no password literal** — a regex finds no
   string assigned to a name matching `(?i)pass|secret|token`; all six
   credentials are read via `os.getenv`; the script contains no `DROP` and no
   `DELETE FROM (contacts|leads|stime)`; it is not under `migrations/`; it
   creates exactly six operators and exactly five memberships; the
   `platform_admin` operator is created with `is_platform_admin=TRUE` and
   **no** `agency_memberships` row.
2. **Exact test command.**
   `python -m pytest -q tests/test_p26_1_seed_and_fixtures.py`
3. **Expected failure.** `AssertionError:
   scripts/p26_seed_agencies_test.py does not exist`.
4. **Minimal implementation.** The script, reusing `assert_test_database_name`
   and `assert_operator_identity` from `scripts/p26_migrate.py` (spec §17 step
   12) and `hash_password` from `operator_auth/security.py`. **It is never
   executed in this phase.**
5. **Verification command.** Same as (2).
6. **Expected PASS.** The file is green. **Task 17 ends GREEN.**
7. **Review checkpoint.**
   `grep -rniE "password *= *['\"]" scripts/p26_seed_agencies_test.py` returns
   nothing. The script refuses a `DB_NAME` without the `test` marker. No seeded
   row lands in `agencies.settings`. Six operators, five memberships — the
   asymmetry is the design, not an omission.

---

## Task 18 — TEST/E2E fixture and cleanup isolation

**Goal.** Make the two direct-SQL E2E scripts survive `030` **and** make their
cleanup incapable of crossing an agency boundary (revision 4).

**Files.** Modified: `run_buy_021_e2e.py`, `run_flow_01_e2e.py`. Extended:
`tests/test_p26_1_seed_and_fixtures.py`.

### 18.1 Re-audit result

Only **two** scripts touch `contacts`/`leads` by direct SQL (§0.5 correction 3).
Audited statement by statement:

| Script | Statement | Verdict |
|---|---|---|
| `run_buy_021_e2e.py` | `INSERT INTO contacts(...)` (line ~114), `INSERT INTO leads(...)` (~121) | need `agency_id` after `030` |
| `run_buy_021_e2e.py` | `cleanup_current()`: `DELETE FROM leads WHERE source=%s`, `DELETE FROM contacts WHERE source=%s` | **unsafe.** String-scoped. The run **already captures ids** in `self.ids["contacts"]` / `self.ids["leads"]` → convert to id-based |
| `run_buy_021_e2e.py` | `cleanup_stale()`: `DELETE FROM leads\|contacts WHERE source LIKE PREFIX%` | **unsafe and cannot be id-based** — it sweeps rows from *previous* runs whose ids are unknown → must add an agency predicate |
| `run_flow_01_e2e.py` | `INSERT INTO contacts(...)` (31), `INSERT INTO leads(...)` (32) | need `agency_id` after `030` |
| `run_flow_01_e2e.py` | `DELETE FROM leads WHERE id=%s AND notes=%s`, `DELETE FROM contacts WHERE id=%s AND display_name=%s` | **already id-scoped — safe.** No change; add a test that it stays so |

The previous draft's blanket claim that cleanup clauses "remain correct" was
right for `run_flow_01_e2e.py` and **wrong** for `run_buy_021_e2e.py`.
`run_buy_021_e2e.py`'s own comment calls titles/codes/source "il confine di
sicurezza" — a *string* boundary, which stops being sufficient the moment a
second agency exists.

**Rule.** Every direct cleanup either deletes **by ids captured in that run**
(preferred), or carries
`AND agency_id = (SELECT id FROM agencies WHERE slug = 'stima360')`.

1. **Failing test.** Static scan of both scripts asserting: every
   `INSERT INTO contacts|leads` names an `agency_id` column whose value is a
   `SELECT id FROM agencies WHERE slug` subselect, never a numeric literal;
   every `DELETE FROM contacts|leads` either filters on `id = %s` / `id = ANY`
   **or** contains the `agencies WHERE slug` agency predicate; and — the
   fail-closed form — **no** `DELETE FROM contacts|leads` in either script
   matches on `source`, `display_name`, `notes` or `title` *alone*.
2. **Exact test command.**
   `python -m pytest -q tests/test_p26_1_seed_and_fixtures.py`
3. **Expected failure.** `AssertionError: run_buy_021_e2e.py cleanup_stale
   deletes from contacts with no id or agency predicate`.
4. **Minimal implementation.** Add `agency_id` (slug subselect) to the four
   fixture inserts; convert `cleanup_current()`'s two CORE deletes to
   `WHERE id = ANY(%s)` over the captured `self.ids`; add the agency predicate
   to `cleanup_stale()`'s two CORE deletes. **No other change** — these are
   guarded-environment scripts and spec §19 forbids speculative refactors.
   `run_flow_01_e2e.py`'s cleanup is left exactly as it is.
5. **Verification command.**
   `python -m pytest -q tests/test_p26_1_seed_and_fixtures.py && python -m py_compile run_buy_021_e2e.py run_flow_01_e2e.py`
6. **Expected PASS.** Tests green; both compile. **Task 18 ends GREEN.**
7. **Review checkpoint.** Confirm `run_integration_01_e2e.py` and
   `run_integration_01_regression.py` were **not** touched — they create CORE
   data through the API and need nothing. Confirm neither script is executed in
   this phase. Confirm the non-CORE deletes (`buy_requests`, `properties`,
   `flow_*`) were left alone: those tables are out of P26-1 scope.

---

## Task 19 — Full hostile isolation suite

**Goal.** The complete Agency A vs Agency B matrix from spec §12 and §15.

**Files.** Extended: `tests/test_p26_1_core_isolation.py` to its full form
(items 23–46).

**Interface.** Fixture: agencies A and B; the six operators of Task 17;
`contact_a`/`lead_a` assigned to `agent_a`; `contact_b`/`lead_b`; one
**unassigned** A contact.

1. **Failing test.** Spec §15 items 23–46: cross-agency list (23, 24);
   cross-agency detail → `404` (25, 26); cross-agency `PATCH` → `404` **and the
   row is unmodified** (27, 28); forged `agency_id` in a create body → `422`
   (29); in an update body → `422` (30); `POST /leads {"contact_id": <B>}` →
   `404`, no row created (31); forged `?agency_id=<B>` ignored (32); `stima_id`
   pivot on activities and tasks → empty (33, 34); cross-agency
   `DELETE /activities/{B}` → `404`, row survives (35); search leakage (36,
   37); `agent_a` sees `lead_a` while `agent_a2` gets `404` (38); the
   unassigned A contact is invisible to `agent_a`, visible to `owner_a` (39);
   `agent` assignment → `403` (40); cross-agency assignment target → `400`
   (41); `admin_a` sees every A record (42); `admin_a` may create an `agent`
   membership but not an `agency_admin` one (43); `platform_admin` reads both
   agencies **and** is refused a generic create with `403` per §11.2 (44); the
   cross-agency `404` body is **byte-identical** to a genuinely absent id (45);
   `link_stima`'s `409` semantics unchanged within one agency (46). Revoked
   session, disabled membership and disabled user are covered by Task 5's
   items 12–14 and re-asserted here at HTTP level.
2. **Exact test command.**
   `python -m pytest -q tests/test_p26_1_core_isolation.py`
3. **Expected failure.** Cases 27, 28, 35 and 45 are the likeliest still-red
   after Tasks 11–12: they assert *side-effect absence* and *response
   byte-equality*, which a scoped `UPDATE`/`DELETE` satisfies only if
   `rowcount == 0` maps to `NotFoundError` with the constant detail string.
4. **Minimal implementation.** Whatever Tasks 11–12 left incomplete — typically
   the `rowcount == 0 → NotFoundError` mapping on `delete_activity` and
   `delete_task`, and the constant `'Risorsa non trovata'` detail. No new
   surface.
5. **Verification command.**
   `python -m pytest -q tests/test_p26_1_core_isolation.py tests/test_p26_1_scope_enforcement.py`
6. **Expected PASS.** Both green. **Task 19 ends GREEN.**
7. **Review checkpoint.** For case 45 compare with
   `assert response_foreign.content == response_absent.content` — status parity
   alone is insufficient. For 27/28, re-read the row after the `404` and assert
   every field is unchanged.

---

## Task 20 — Public STIMA regression suite

**Goal.** Prove P17/P18 and the public estimation are untouched.

**Files.** Extended: `tests/test_p26_1_public_stima_routing.py`.

**Interface.** No production code. All negative conditions are produced by
monkeypatching within the test (§0.3), never by editing a source file.

1. **Failing test.** Spec §11.3's compatibility table: a public estimation still
   records the P17 `stima_richiesta` event with the `contact_id`/`lead_id` from
   `bridge_result`; the P18 `FOLLOWUP_STIMA_RICHIESTA` task is still created and
   carries a resolved `agency_id`; `safe_record_event` and `safe_run_followup`
   still never raise; `POST /api/salva_stima` returns `200` when the bridge
   returns `conflict`, `skipped` **and** `error` — each produced by
   `monkeypatch.setattr(core.service, "bridge_public_stima", …)`; the `stime`
   row is written before the bridge runs and is unaffected by a bridge failure;
   `lead_stime` linking is unchanged.
2. **Exact test command.**
   `python -m pytest -q tests/test_p26_1_public_stima_routing.py tests/test_seller_intelligence_p17b1_integration.py tests/test_followup_p18c_integration.py`
3. **Expected failure.** `AssertionError: P18 task carries agency_id=None` — the
   monkeypatched `error` path is written first, and the task's `agency_id`
   assertion fails until Task 14's fallback branch is confirmed present.
4. **Minimal implementation.** None to runtime code. If an assertion fails for
   a reason other than the monkeypatched condition, the fix belongs to Task 13
   or 14 and that task's tests are re-run.
5. **Verification command.**
   `python -m pytest -q tests/test_p26_1_public_stima_routing.py tests/test_seller_intelligence_p17b1_integration.py tests/test_followup_p18c_integration.py tests/test_p17b3_seller_timeline_ui.py`
6. **Expected PASS.** All four green. **Task 20 ends GREEN.**
7. **Review checkpoint.** Confirm every negative condition is a `monkeypatch`
   inside a test, reverted automatically at teardown. Confirm no test writes to
   module state in a way that leaks into another test file.

---

## Task 21 — Full regression gate

**Goal.** The whole suite green: P17–P25, P26-0, P26-1.

**Files.** None modified by this task. Any failure is repaired in the task that
owns the file.

1. **Failing test.** The existing suite, unchanged, plus the 13 new files.
2. **Exact test command.** `python -m pytest -q`
3. **Expected failure.** Realistic candidates, all consequences of the `ctx`
   threading: `tests/test_core_service_regressions.py`,
   `tests/test_next3_crm_360.py`, `tests/test_public_stima_core_crm_bridge.py`,
   `tests/test_owner_08_p8_2.py`, `tests/test_owner_08_p8_3b.py`,
   `tests/test_followup_isolation.py`,
   `tests/test_database_revival_isolation.py`,
   `tests/test_next6_p2a_flow_execution_safety.py` — the eight files importing
   `core.service`/`core.repository` or faking CORE SQL. Each should already
   have been repaired by its owning task; anything red here is a genuine
   regression.
4. **Minimal implementation.** Supply a context fixture at each broken call
   site. **Do not change production behaviour to make a test pass** — a failure
   for any reason other than the new `ctx` parameter or the new SQL shape is
   reported, not patched.
5. **Verification command.** `python -m pytest -q 2>&1 | tail -20`
6. **Expected PASS.** `126 files, 0 failed` — 113 existing plus 13 new. Zero
   skips introduced by P26-1; any pre-existing skip reported unchanged.
   **Task 21 ends GREEN**, and because every preceding task already ended
   green, a red here is by definition a cross-file interaction that no single
   task's own suite could have caught — it is reported as such, not absorbed.
7. **Review checkpoint.** Re-verify the §0.4 interface contract against the real
   signatures in the tree. Confirm 13 new test files. Confirm no test was
   deleted, renamed or `xfail`-ed to reach green.

---

## Task 22 — TEST migration execution runbook

**Goal.** Produce the exact ordered runbook. **No execution in this phase.**

**Files.** New: `docs/P26_1_MIGRATION_RUNBOOK_TEST.md`,
`tests/test_p26_1_runbook_certification.py`.

**Interface.** A document. Every command written out for Giorgio to run
manually against `stima360_db_test`.

1. **Failing test.** The runbook exists; it names the four migrations in the
   order `027 → 028 → backup → 029 → 030`; it contains a `pg_dump` step
   positioned **between** `028` and `029` (spec §16 — `029` is the irreversible
   one); it names `scripts/p26_schema_snapshot.py` at least twice (pre and
   post); it contains no `git commit`, `git push` or deploy command; and it
   names `stima360_db` **only** inside a prohibition sentence.
2. **Exact test command.**
   `python -m pytest -q tests/test_p26_1_runbook_certification.py`
3. **Expected failure.** `AssertionError:
   docs/P26_1_MIGRATION_RUNBOOK_TEST.md does not exist`.
4. **Minimal implementation.** Write the runbook as spec §17 steps 1–16: clean
   tree check; pre-`027` snapshot + fingerprint; `pg_dump`;
   `p26_migrate.py status` then `plan` (read-only); record pre-migration
   `COUNT(*)` for the four tables; apply `027`, verify four tables and one
   `agencies` row; apply `028`, verify ten columns all `NULL` and the four
   counts unchanged; **fresh `pg_dump` immediately before `029`**; apply `029`,
   verify `GROUP BY agency_id` yields one group equal to the pre-counts and
   zero `NULL`; apply `030`, verify `NOT NULL`, three FKs, one unique key, nine
   indexes and two triggers in `pg_catalog`; run the seed script (six
   operators, five memberships); run the 13 new test files; run the full suite;
   post-`030` snapshot and fingerprint. Each step carries its **stop
   condition** and its restore checkpoint.
5. **Verification command.** Same as (2).
6. **Expected PASS.** Green. **Task 22 ends GREEN.**
7. **Review checkpoint.** The runbook contains no executed output — it is a
   plan, not a record. Every `p26_migrate.py` invocation carries
   `--operator "<real person>"` and never `$ADMIN_USER`. The `029` step states
   that its down file **will refuse** and that recovery is restore-only.

---

## Task 23 — Final certification checklist

**Goal.** Map delivery to the approved Definition of Done, GATE-MA1 explicit.

**Files.** New: `docs/P26_1_CERTIFICATION_TEST.md`. Extended:
`tests/test_p26_1_runbook_certification.py`.

**Interface.** A document, completed **after** Giorgio executes Task 22's
runbook. Created in this phase with every evidence field marked
`NOT YET EXECUTED`.

1. **Failing test.** The file exists; it contains one row per DoD item 1–16
   (spec §20); it contains the literal `GATE-MA1`; it contains the required
   sentence from DoD item 16 stating that P26-1 **does not** certify
   platform-wide multi-agency isolation and that no second real agency may be
   onboarded into PROD; it lists risks R-1 through R-9; and every evidence
   field reads `NOT YET EXECUTED`.
2. **Exact test command.**
   `python -m pytest -q tests/test_p26_1_runbook_certification.py`
3. **Expected failure.** `AssertionError:
   docs/P26_1_CERTIFICATION_TEST.md does not exist`.
4. **Minimal implementation.** The checklist: 16 DoD rows each naming its
   evidence (test ids, command, artefact); the GATE-MA1 statement verbatim from
   spec §2.2.3; risks R-1…R-9 with their spec §21 decisions; slots for the
   post-`030` fingerprint, `git diff --check` and `git status --short`.
   **Evidence fields stay `NOT YET EXECUTED`** — P26-0's rule that a test not
   run is not a pass.
5. **Verification command.**
   `python -m pytest -q tests/test_p26_1_runbook_certification.py && python -m pytest -q`
6. **Expected PASS.** Everything green; the certification document present and
   honestly unfilled. **Task 23 ends GREEN.**
7. **Review checkpoint.** No evidence field claims a result. DoD item 16's
   wording is reproduced exactly, not paraphrased — the spec makes a report
   omitting it a DoD failure.

---

## Spec coverage map

| Spec requirement | Task |
|---|---|
| 027/028/029/030 split | 2, 7, 8, 9 |
| nullable → backfill → constrain in separate migrations | 7 → 8 → 9 |
| `029` irreversible down RAISEs | 1 (helper), 8 |
| `operator_users.email_normalized` globally unique (D-4) | 2 |
| one active membership per operator | 2 |
| exactly one active `agency_owner` per agency | 2 |
| `platform_admin` not an `agency_memberships` role | 2, 4, 17 |
| `operator_sessions` has no `agency_id` (D-3) | 2, 5 |
| `SystemAgencyContext` cannot take `agency_id` from a client | 4, 10, 11 (A3, A4) |
| public bridge default agency resolved server-side | 10, 13 |
| `core_agency_integrity` validates all references | 9, 14 |
| explicit agency mismatch raises | 9, 14 |
| lead/contact/stima mismatch raises | 9, 14 |
| agent sees only assigned contacts/leads | 10, 19 |
| unassigned records invisible to agent | 19 (case 39) |
| owner/admin see whole agency | 19 (case 42) |
| `platform_admin` controlled cross-agency **read** | 10, 19 (case 44) |
| `platform_admin` write requires agency binding | 11 (§11.2), 12 (§12.1), 19 (case 44) |
| `404` cross-agency, `403` role-denied (D-6) | 11, 12, 19 (cases 40, 45) |
| `CoreModel extra="forbid"` preserved | 11 (A6), 12, 19 (cases 29, 30) |
| `agency_id` never from request data | 11 (A5, A6, Layer B) |
| reads carry an agency predicate | 11 (Layer B) |
| UPDATE/DELETE carry an agency predicate | 11 (Layer B) |
| INSERT stamps `agency_id` server-side | 11 (Layer B) |
| no unapproved repository function reaches scoped tables | 11 (A2, B1 fail-closed) |
| non-CORE routers not opened to sessions | 15 (item 52, M3–M5) |
| legacy Basic residual documented and frozen | 15 (52e, 73) |
| GATE-MA1 blocks PROD multi-agency | 15, 23 |
| TEST cleanup cannot cross agency boundaries | 18 |
| no RLS / territories / billing / SSO / 2FA / multi-agency membership | absent by construction; Task 21 checkpoint confirms |
| no PROPERTY/BUY/MATCH scope | 15 (M3–M5: no non-CORE router change) |
| no OS Shell cookie migration | 15 (legacy Basic branch retained) |
| no secrets in `agencies.settings` | 1, 2, 17 |
| no test credentials in migrations/repo | 2, 17 |
| P17–P25 preserved | 20, 21 |

---

## Implementation risks

**R-1 — `BridgeCursor` will break *silently*, and it guards the
highest-severity control.** Its branch condition is
`"from contacts" in sql and "email_normalized" in sql`; after Task 13 the SQL
gains an `agency_id` predicate, so the substring still matches but the
**parameter tuple shifts by one** — the fake indexes the wrong element and
returns wrong rows rather than failing. Mitigation: Task 13 step 4 updates the
fake explicitly, and Task 13's new file asserts on the recorded SQL
independently, so a silently-wrong fake cannot produce a green suite alone.

**R-2 — Leakage assertions need a negative control, not a source edit.**
Addressed in §0.3 and Task 16: the control is a fake class in the test module,
and the control is written *first*, so Task 16 begins genuinely red. No
production file is ever mutated to test a test.

**R-3 — `030`'s trigger logic cannot be executed offline.** No plpgsql
interpreter is available, so Task 14 asserts the SQL implements a decision
table and tests that table in Python. Runtime behaviour is first exercised in
Task 22's runbook, by Giorgio, against `stima360_db_test`. A genuine coverage
boundary, stated rather than papered over.

**R-4 — Editing `030` during Task 14 is legitimate; the window is narrow.**
P26-0 rule 12 forbids editing a migration recorded in the ledger. `030` is not
yet applied, so it is not recorded, so Task 14 may correct it. That freedom
ends the moment Giorgio runs the Task 22 runbook.

**R-5 — Layer B's coverage table must be maintained.** B1 fails closed when a
new repository function appears, which is correct — but it means adding a
legitimate function requires updating the test table in the same change. That
is the intended friction; it is recorded so it is not mistaken for a broken
test.

**R-6 — `require_operator`'s legacy branch needs a DB round-trip.** Every
legacy-Basic CORE request resolves `slug='stima360'`. Caching it in a module
global would break the Task 17 seed and any restore. Task 15 resolves it per
request; on the connection-per-request architecture this is one extra query, in
the same class as spec R-8.

**R-7 — Task 12's step order is security-relevant.** Resolve (1) → role (3) →
membership (4). Inverting 1 and 3 makes the `403`/`404` difference reveal
whether a foreign record exists. Called out in Task 12's checkpoint.

**R-8 — M3–M5's frozen maps are built from HEAD `05387fa`.** If Giorgio commits
an unrelated `main.py` change before Task 15, the frozen map must be rebuilt
from the new base, or it will report a false drift. Task 15 step 4 names the
source commit explicitly for this reason.

---

## Spec coverage gaps

**None.** Every requirement in the approved spec's preservation list and every
DoD item maps to a task above.

Two boundaries, both explicit in the approved spec and neither a gap in this
plan:

- The trigger's runtime behaviour is proven only in Task 22's execution (R-3),
  not in the offline suite.
- Spec DoD item 16 is satisfied by *recording* GATE-MA1 as open, not by closing
  it. Closing it is P26-2+.
