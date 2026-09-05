# P26 — Database connection entry point inventory

Status: **current as of P26-0**, branch `core-0.1-test`, base commit `4de8283`.

This inventory is the contract behind the eventual Row Level Security work. RLS
is a final layer of defence, and it only defends the sessions it actually
covers. That makes the set of places able to open a database connection a
security boundary, not a piece of trivia.

The set is pinned by `tests/test_p26_db_entrypoints.py`. A connection site that
is not listed here fails that test. Adding one is a deliberate act: update the
whitelist in the test, add a row below with its justification, and say why the
call cannot go through `database.get_connection()`.

---

## 1. The runtime choke point

`database.py` — `get_connection()` builds a `psycopg2` connection from
`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`.

There is no connection pool, no SQLAlchemy, no asyncpg, and no runtime use of
`DATABASE_URL`.

**Every application request resolves here.** Verified by direct inspection:

| Layer | Path to the choke point |
|---|---|
| `main.py` | 19 direct `get_connection()` call sites |
| `whatsapp.py` | local alias `get_connection()` at line 717 that delegates to `database.get_connection`; 11 uses |
| `core/database.py` | `core_cursor()` contextmanager over `get_connection()` |
| `followup/database.py` | `followup_cursor()` |
| `seller_intent/database.py` | `seller_intent_cursor()` |
| `seller_intelligence/database.py` | `si_cursor()` |
| `property_watch/database.py` | `property_watch_cursor()` |
| `next_best_action/database.py` | `next_best_action_cursor()` |
| `database_revival/database.py` | `database_revival_cursor()` |
| `owner/`, `buy/`, `match/`, `flow/`, `property/`, `proposal/`, `sale/` | repositories use `core.database.core_cursor`; none opens a connection |
| `crm/` | no direct database access; service layer over other repositories |

Each cursor helper is a delegation, not a second connection path. The tests
assert that every one of them imports `get_connection` and contains no
`psycopg2.connect` of its own.

**Conclusion: the application runtime is fully behind the choke point.** The
choke point is real for runtime traffic. It is not, by itself, sufficient for
RLS — see sections 3 and 4.

---

## 2. Whitelisted connection sites

Nine files may build their own connection. Each is listed with the reason.

| File | Class | Guard | Justification |
|---|---|---|---|
| `database.py` | choke point | environment supplied | The choke point itself; every application path resolves here. |
| `integration_p2_support.py` | diagnostic | `require_test_environment()` — database name, backend host, branch | Guarded diagnostic helper. Opens sessions with `set_session(readonly=...)`. |
| `run_flow_01_e2e.py` | TEST e2e | refuses a `DB_NAME` without the `test` marker | TEST-only end-to-end script. |
| `run_buy_021_e2e.py` | TEST e2e | verifies `current_database()` and the backend endpoint | TEST-only end-to-end script. |
| `migrate_add_token.py` | **legacy risk** | **none** | See section 3. Not modified in P26-0. |
| `scripts/p26_schema_snapshot.py` | privileged migration channel | `assert_test_database_name()` twice, plus `SET TRANSACTION READ ONLY` | Read-only baseline snapshot. Refuses production names. |
| `scripts/p26_migrate.py` | privileged migration channel | `assert_test_database_name()` twice | The migration runner. See section 4. |
| `tests/conftest.py` | test stub | n/a | Replaces `psycopg2.connect` with a stub; opens no connection. |
| `tests/test_core_service_regressions.py` | test stub | n/a | Same. |

The `run_*_e2e.py` scripts are a **privileged TEST channel**, not application
traffic. They are permitted to bypass the choke point. They are not permitted
to run against production.

---

## 3. `migrate_add_token.py` — recorded risk, deliberately untouched

**Classification: neutralise before RLS. Not modified in P26-0.**

Findings:

- It is the only database entry point in the repository with **no environment
  guard of any kind**.
- It uses a **second, independent set of environment variables** —
  `DATABASE_URL`, or `PGHOST` / `PGUSER` / `PGPASSWORD` / `PGDATABASE` /
  `PGPORT` — with defaults `postgres` and `stima360`. A misconfigured
  environment therefore points it at a plausible database name rather than
  failing loudly.
- It targets a table named `stima` (singular). The schema contains `stime`. On
  the current schema it would raise rather than alter anything, which is why it
  is classified as dead rather than dangerous today.

Why it is not removed in P26-0: a file that looks dead in the repository may
still be wired into a deployment configuration the repository does not contain.

**Required verification before removal, all three negative:**

1. Not invoked by Render — build command, start command, pre-deploy hook, or
   job definition.
2. Not invoked by any cron or scheduled task.
3. Not referenced by any documented operational procedure or runbook.

Until then `tests/test_p26_db_entrypoints.py` asserts the file is unchanged, so
that a silent "fix" cannot substitute for that verification.

---

## 4. The migration channel is explicitly privileged

Before P26-0 there was no migration runner. Migrations were `.sql` files
applied by hand through `psql`. That session never passes through
`get_connection()`, and if opened as the table owner or a superuser it bypasses
row level security by definition.

This is not an oversight to be closed. It is a **deliberate, bounded
privilege**: migrations must be able to alter and backfill across every agency,
so the migrator role legitimately sits outside RLS.

`scripts/p26_migrate.py` makes that channel explicit, auditable, and guarded
rather than ad hoc.

---

## 5. Mandatory role separation before RLS

RLS is meaningless while the application connects as the table owner, because
table owners are not subject to policies. The following separation is a **hard
precondition** for any RLS work, not a parallel workstream:

- A **migrator** role that owns the schema and its objects, and is the only
  role permitted to execute migrations.
- A **separate application role**, used by `database.get_connection()`.
- The application role is **not the owner** of any table.
- The application role has **BYPASSRLS** disabled and is not a superuser.
- The migration channel stays privileged, as described in section 4.

None of this is implemented in P26-0. It is recorded here so the precondition
is not rediscovered late.

---

## 6. What would make this inventory stale

- A new `psycopg2.connect` anywhere — caught by the pinned test.
- A connection pool or ORM being introduced — the choke point stops being a
  single function and this document must be rewritten before RLS.
- `whatsapp.py` ceasing to delegate its alias to `database.get_connection`.
- Any cursor helper acquiring its own connection.
