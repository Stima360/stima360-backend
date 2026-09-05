# P26-0 Baseline & Migration Ledger Design

## Document status

- Phase: `P26-0` — design only.
- This document is a **specification**. It authorises no code, no migration, no
  script, and no test. Implementation belongs to a later, separately approved
  phase.
- Base branch observed during the audit: `core-0.1-test`, `HEAD 4de8283`.
- Scope of the audit that produced this spec: **read-only static analysis of
  repository files**. No database connection was opened. No PROD access.
- Approved P26 direction (input constraint, not re-litigated here): shared DB +
  shared schema + `agency_id`; `AuthContext` = agency identity + operator
  identity; application-level scoping first; PostgreSQL RLS only as final
  defense-in-depth; naming is `agency_id`, never `tenant_id`.

## Goal

P26-0 establishes a trustworthy foundation for schema change before any
multi-agency work touches the schema. It does exactly three things:

1. Certifies the real pre-P26 schema as a **baseline**.
2. Introduces a **forward-only migration ledger** starting at version `026`.
3. Defines the **rules, roles, and rollback procedure** every later P26
   migration must obey.

P26-0 deliberately does not create `agencies`, does not add `agency_id`, does
not enable RLS, and does not modify application data.

## Non-goals and explicit scope boundaries

P26-0 does **not**:

- Retro-register migrations `001`–`025` as applied, in any form.
- Create missing down migrations for `021` / `022`.
- Move, rename, or otherwise relocate migrations `001`–`025`.
- Modify, delete, or neutralise `migrate_add_token.py`.
- Replace `ADMIN_USER` / `ADMIN_PASS`.
- Create the `agencies` table or any `agency_id` column.
- Enable Row Level Security or create any policy.
- Modify application data.
- Touch `main`, PROD, deployment configuration, or schedulers.

---

## 1. Audit findings — database connection entry points

The audit answers a single operational question: **is
`database.get_connection()` a real choke point?** RLS as final defense-in-depth
is only meaningful if the answer is qualified precisely.

### 1.1 The choke point

`database.get_connection()` (`database.py:28`) returns a
`psycopg2.connect(host/port/dbname/user/password)` built from `DB_HOST`,
`DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`.

There is **no connection pool**, no SQLAlchemy, no asyncpg, and no runtime use
of `DATABASE_URL`.

### 1.2 Paths that pass through the choke point

| Layer | Mechanism |
|---|---|
| `main.py` | 19 direct `get_connection()` call sites |
| `whatsapp.py` | defines a local alias `get_connection()` (`whatsapp.py:717`) that delegates to `database.get_connection`; 11 uses |
| `core/database.core_cursor` | contextmanager over `get_connection()` |
| `followup/`, `seller_intent/`, `seller_intelligence/`, `property_watch/`, `next_best_action/`, `database_revival/` | each defines its own `*_cursor()` contextmanager, all over `database.get_connection` |
| `owner/`, `buy/`, `match/`, `flow/`, `property/`, `proposal/`, `sale/` | repositories use `core.database.core_cursor`; none opens its own connection |
| `crm/` | no direct database access (service layer over other repositories) |

**Result: the application runtime is 100% behind `database.get_connection()`.**
The choke point is real for runtime traffic.

### 1.3 Paths that bypass the choke point

| # | Location | Mechanism | Guard | RLS risk |
|---|---|---|---|---|
| 1 | `integration_p2_support.py:103` `db_connect()` | own `psycopg2.connect` + `set_session(readonly=)` | `require_test_environment()` (db name + host + branch) | Medium — diagnostic script |
| 2 | `run_flow_01_e2e.py:26` `conn()` | own `psycopg2.connect` | `"test" in DB_NAME` | Medium |
| 3 | `run_buy_021_e2e.py:56` `connect()` | own `psycopg2.connect`, `autocommit=True` | `current_database()` + host endpoint | Medium |
| 4 | `migrate_add_token.py:21-23` | own `psycopg2.connect` over `DATABASE_URL` **or** `PGHOST`/`PGUSER`/`PGPASSWORD`/`PGDATABASE` with defaults `postgres` / `stima360` | **none** | **High** |
| 5 | `tests/conftest.py:28`, `tests/test_core_service_regressions.py:13` | `psycopg2.connect = lambda: None` stub | n/a | None (opens no connection) |
| 6 | Migration application | `psql` / operator shell — **outside Python entirely** | only the in-SQL guards on `010`/`011`/`014`/`015` | **High** |

### 1.4 Consequence for the RLS roadmap

Entry point **#6** is the decisive one. No migration runner exists in the
repository; migrations are `.sql` files applied by hand. That `psql` session
never passes through `get_connection()` and, if opened as the table owner or a
superuser, bypasses RLS by definition (table ownership, `BYPASSRLS`).

The migration channel must therefore be designed as an **explicitly privileged
channel**, not treated as a forgotten exception. This is formalised in
section 6.

Entry point **#4** is the only unguarded bypass in the repository. Its handling
is specified in section 7.

---

## 2. Audit findings — real state of the migration system

### 2.1 Inventory

- **25 up files** (`001`–`025`); **23 down files**. `021` and `022` have no
  down file.
- **No migration runner** exists in the repository. No Alembic, no `Makefile`,
  no shell runner.
- **No ledger table** exists (`schema_migrations`, `alembic_version`,
  `migration_history`, `migrations`). This is consistent with the probe logic
  in `run_integration_01_schema_check.py:57` and with the conclusion already
  recorded in `INTEGRATION_MIGRATIONS_INVENTORY.md`: application state is
  *not demonstrable* from schema metadata alone.
- **Legacy schema outside migrations:** `stime`, `stime_dettagliate`, and
  `zone_valori` are created by functions in `database.py` under `__main__`, and
  appear in no file under `migrations/`. The real schema is therefore, by
  construction, **not** the sum of the migration files.

### 2.2 The structural blocker: `010`/`011` versus `014`/`015`

| File | In-file guard |
|---|---|
| `010_owner_02_p1.sql` | `IF current_database() <> 'stima360_db_test' THEN RAISE EXCEPTION` |
| `011_owner_02_p5.sql` | same, TEST |
| `014_owner_02_p1_prod.sql` | `IF current_database() <> 'stima360_db' THEN RAISE EXCEPTION` |
| `015_owner_02_p5_prod.sql` | same, PROD |

`014` is byte-identical to `010` apart from the guard and a comment. These are
**environment-specific duplicate pairs**.

The direct consequence: **there is no valid set of "migrations 001–025
applied".** On TEST, `010`/`011` are applied and `014`/`015` must remain
unapplied. On PROD the inverse holds. A single uniform ledger would be false in
both environments.

### 2.3 Idempotency, as measured on the files

| Category | Files |
|---|---|
| **Not idempotent** (bare `CREATE TABLE` / `CREATE INDEX`; re-run raises) | `004`, `005`, `008`, `010`, `011`, `013`, `014`, `015`, `016` |
| Idempotent (`IF NOT EXISTS` throughout) | `001`, `002`, `003`, `006`, `007`, `009`, `017`, `018`, `019`, `020`, `022`, `023`, `024`, `025` |
| **Non-transactional** (no `BEGIN` / `COMMIT`) | `023_invisible_sale.sql` |
| Data migration, not DDL | `021` (`UPDATE leads`, idempotent by predicate) |

### 2.4 Ordering anomaly

The numeric sequence is continuous, but functional order interleaves:
`004_buy_01` → `005_match_01` → `006_buy_02` → `007_match_02`. Not an error in
itself, but it confirms that file numbering is **not** a demonstrated
application order.

---

## 3. Risks of a retroactive ledger

| # | Risk | Severity | Reason |
|---|---|---|---|
| C1 | Ledger is arithmetically impossible | Critical | `010`/`011` and `014`/`015` are mutually exclusive per environment. "All applied" is false everywhere. |
| C2 | False certification | Critical | Registering `001`–`025` converts an unverified assumption into a system fact. Every later decision (rollback, disaster recovery, new environment) inherits the error with no way to detect it. |
| C3 | Silent TEST/PROD divergence | High | If PROD received only a subset, or manual changes, a retroactive ledger hides the divergence instead of exposing it. Today the divergence is at least suspectable. |
| C4 | Legacy schema uncovered | High | `stime` / `stime_dettagliate` / `zone_valori` belong to no migration. A `001`–`025` ledger implicitly claims a schema that does not describe reality. |
| C5 | Environment rebuild broken | High | Replaying `001`–`025` on a fresh database fails on `004`/`005`/`008`/`013`/`016` (not idempotent) and on `010` *or* `014` (guards). A ledger would imply the replay is a valid procedure. |
| C6 | Catastrophic rollback | Critical | 20 down files contain `DROP TABLE`, up to 6 tables each. A ledger asserting "025 applied" authorises a runner to execute `025_down` → `DROP TABLE`. If the assertion was false, the drop hits data that migration never created. |
| C7 | Checksums unverifiable after the fact | Medium | Several files have modification times later than their presumed application (`012_..._down.sql`, `014`, `015`). There is no proof the current content is what was executed. |

**Conclusion: automatic retro-registration is excluded.** The constraint is not
a preference; it is forced by C1 and C6.

---

## 4. Recommended strategy — certified baseline, forward-only ledger

Model: **certified baseline plus forward-only ledger.** Migrations `001`–`025`
remain *explicitly outside* the ledger, marked as pre-baseline and untracked.

Their absence from the ledger is **semantically meaningful and documented**: it
records that historical application state was never demonstrable, rather than
pretending it was.

### 4.1 Phase 0 — read-only certified snapshot of the TEST schema

A single script, running inside `SET TRANSACTION READ ONLY`, producing a
deterministic artefact:

1. `information_schema.tables`, `columns`, `table_constraints`,
   `key_column_usage`, `constraint_column_usage`
2. `pg_indexes`, `pg_class.relrowsecurity`, `pg_policies`
3. Sequences, defaults, `pg_get_constraintdef` for CHECK constraints
4. Installed extensions
5. **Canonical ordering at every level**, so the fingerprint is reproducible
6. `sha256` of the canonicalised document → **`schema_fingerprint`**

Output: an artefact plus its checksum, written under `reports/` only. The
script performs no write against the database.

`run_integration_01_schema_check.py` already covers roughly 70% of the query
surface, but it **writes two `.md` files into the repository root** and
produces no fingerprint. It is a reference, not a component to reuse as-is.

### 4.2 Phase 1 — human certification

The fingerprint alone certifies nothing. An explicit act is required:

1. Compare the snapshot against the union of objects expected from `001`–`025`
   plus the legacy objects created by `database.py`.
2. Classify **every** divergence into exactly one of:
   `expected_from_migration_NNN`, `legacy_pre_migrations`, or
   `manual_change_unexplained`.
3. **Gate: zero items may remain in `manual_change_unexplained`.** Each
   residual item must be explained, or accepted with a written justification.
4. The output of this classification is the **Baseline Certificate**, versioned
   in the repository as a document (not as SQL).

### 4.3 Phase 2 — establishing the baseline in the database

One migration, `026`, additive, idempotent, transactional. It creates the
ledger and baseline tables and inserts **a single row**: the baseline, carrying
the Phase 0 fingerprint.

It inserts **nothing** for `001`–`025`.

### 4.4 Phase 3 — forward-only

From `027` onward, every migration registers itself in the ledger.

### 4.5 How historical re-execution is prevented

Migrations `001`–`025` **stay exactly where they are**, under `migrations/`.
They are not moved, not renamed, and not relocated into a subdirectory.
Relocation would be a repository change that adds risk without adding a
guarantee — the guarantee comes from the runner and from the schema.

Three independent, non-positional defences:

1. **Runner gate.** The runner parses the numeric prefix of each candidate file
   and structurally ignores any version below `026`. Files `001`–`025` are
   never eligible for execution, regardless of where they sit on disk.
2. **Schema gate.** The ledger table carries a CHECK constraint rejecting any
   version below `026` (section 5). No runner, script, or manual correction can
   register a pre-baseline version without explicitly dropping the constraint.
3. **Registry gate.** The runner refuses any `version` that does not resolve to
   a file it selected under gate 1.

### 4.6 Runner responsibilities — deliberately bounded

The runner is **not** a schema-diff engine. It does not recompute or compare a
full schema fingerprint on every execution. That would be slow, brittle against
legitimate out-of-band changes, and would turn an audit artefact into a runtime
dependency.

On each execution the runner verifies exactly:

- **baseline present** — `schema_baseline` exists and holds its single row;
- **correct database** — the baseline row's `database_name` matches
  `current_database()`;
- **applied migrations** — which versions `>= 026` are already recorded;
- **checksums of registered files** — the on-disk file for each recorded
  version still matches its stored checksum;
- **order and versions** — versions are contiguous and monotonic from `026`,
  with no gap and no unknown version.

The `schema_fingerprint` remains **primarily audit and certification
evidence**. It proves what the schema was when the baseline was certified. It
is consulted during audits and incident investigation, not on every run.

### 4.7 PROD

**PROD receives no baseline in P26-0.** The cycle 4.1 → 4.3 must be repeated
against PROD as a separate, separately approved phase, with its own fingerprint
and its own certificate.

The PROD fingerprint **will differ** from the TEST fingerprint, because of
`010`/`011` versus `014`/`015`. This is expected, not an error. A single
certificate covering both environments would repeat risk C3.

---

## 5. Ledger schema

Two tables. Separating them prevents the baseline from appearing as "a
migration that was applied".

Neither table carries `agency_id`. They are global infrastructure, not agency
data, and must remain outside tenant scoping and outside RLS.

### 5.1 `schema_migrations`

| Column | Type | Notes |
|---|---|---|
| `version` | `TEXT` PK | e.g. `027_p26_agencies` |
| `filename` | `TEXT NOT NULL` | |
| `checksum_up` | `CHAR(64) NOT NULL` | sha256 of the up file |
| `checksum_down` | `CHAR(64)` | |
| `down_available` | `BOOLEAN NOT NULL DEFAULT FALSE` | |
| `transactional` | `BOOLEAN NOT NULL DEFAULT TRUE` | `FALSE` only for the `CONCURRENTLY` exception (section 6.3) |
| `applied_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | |
| `applied_by_db_user` | `TEXT NOT NULL DEFAULT CURRENT_USER` | |
| `applied_by_operator` | `TEXT NOT NULL` | explicit operator identity (section 5.4) |
| `database_name` | `TEXT NOT NULL DEFAULT current_database()` | |
| `execution_ms` | `INTEGER` | |
| `rolled_back_at` | `TIMESTAMPTZ` | |
| `rolled_back_by_operator` | `TEXT` | |
| `notes` | `TEXT` | |

Constraints:

- `version` matches `^[0-9]{3}_[a-z0-9_]+$`.
- **`schema_migrations_no_pre_baseline`**: the numeric prefix must be `>= 26`.
- `down_available` and `checksum_down` are consistent (both set, or both
  absent).
- `rolled_back_at` and `rolled_back_by_operator` are set together or not at
  all.

### 5.2 `schema_baseline`

| Column | Type | Notes |
|---|---|---|
| `id` | `SMALLINT` PK `DEFAULT 1` | singleton, `CHECK (id = 1)` |
| `baseline_version` | `TEXT NOT NULL` | e.g. `P26-BASELINE-001` |
| `schema_fingerprint` | `CHAR(64) NOT NULL` | audit evidence (section 4.6) |
| `snapshot_artifact` | `TEXT NOT NULL` | reference to the certified snapshot |
| `certified_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | |
| `certified_by_operator` | `TEXT NOT NULL` | |
| `database_name` | `TEXT NOT NULL DEFAULT current_database()` | |
| `pre_baseline_tracked` | `BOOLEAN NOT NULL DEFAULT FALSE` | `CHECK (pre_baseline_tracked = FALSE)` |
| `notes` | `TEXT NOT NULL` | must state that `001`–`025` are untracked and why |

### 5.3 Cross-environment guard

A `BEFORE INSERT OR UPDATE` trigger on both tables raises if
`NEW.database_name <> current_database()`. A TEST dump restored onto PROD (or
the reverse) makes the ledger immediately detectable as foreign, instead of
silently authoritative.

`database_name` cannot be enforced by a CHECK constraint, because
`current_database()` is not immutable. The trigger is the correct mechanism.

### 5.4 `applied_by_operator` in P26-0

`applied_by_operator` is an **explicit operator identity supplied to the
runner** — a CLI argument or equivalent audit identity — and is mandatory from
the first ledger row.

It is deliberately **not** a foreign key to `agency_users` or any operator
table: that model does not exist yet and belongs to a later phase. Making it a
plain non-null text field now avoids both a forward dependency and the debt of
an anonymous ledger.

It must **never** be derived automatically from `ADMIN_USER`. `ADMIN_USER` /
`ADMIN_PASS` may persist temporarily for backward compatibility and emergency
access, but it is not an operator identity and must not become the definitive
super-admin model. The ledger is the first system component built to require a
real, auditable identity, and it must not inherit that debt.

### 5.5 Idempotent trigger creation

`CREATE TRIGGER IF NOT EXISTS` **does not exist in PostgreSQL** and must not be
assumed. The baseline migration must create its triggers through an explicit
catalogue check:

```sql
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_schema_migrations_guard'
          AND c.relname = 'schema_migrations'
          AND n.nspname = 'public'
          AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_schema_migrations_guard
            BEFORE INSERT OR UPDATE ON schema_migrations
            FOR EACH ROW EXECUTE FUNCTION schema_ledger_guard();
    END IF;
END $$;
```

The guard function itself uses `CREATE OR REPLACE FUNCTION`, which is natively
idempotent. The same catalogue-check pattern applies to the `schema_baseline`
trigger.

Filtering on `NOT t.tgisinternal` matters: constraint-backed internal triggers
share the namespace and would otherwise produce false positives.

---

## 6. Rules for future P26 migrations

1. **Numbering.** From `026`. Sequential, never reused, never reordered.
   Naming: `NNN_p26_<scope>.sql`.
2. **Mandatory pair.** Every `NNN_x.sql` has an `NNN_x_down.sql`. If a true
   down is impossible without data loss, the down file **must still exist** and
   must contain a `RAISE EXCEPTION` stating why (section 7.3). A missing down
   is not permitted — that is precisely what happened with `021` / `022`.
3. **Transactionality.** Every migration opens `BEGIN;` and closes `COMMIT;`.
   The single admitted exception is `CREATE INDEX CONCURRENTLY` (rule 10).
   `023` is the precedent not to repeat.
4. **Idempotency.** `IF NOT EXISTS` / `IF EXISTS` on every object; the
   catalogue-check pattern (5.5) where no `IF NOT EXISTS` form exists. A
   migration must be re-runnable to no effect without raising. This covers the
   case where the runner dies between `COMMIT` and the ledger insert.
5. **Ledger inside the same transaction.** The `INSERT INTO schema_migrations`
   sits inside the migration's own `BEGIN`/`COMMIT`. Ledger and DDL cannot
   diverge.
6. **No environment guards inside the SQL.** The `current_database()` pattern
   of `010`/`014` is abandoned: it is the direct cause of C1. If a change is
   environment-specific, it is a data or configuration problem, not a schema
   problem. The schema must be identical in TEST and PROD.
7. **No per-environment duplicate migrations.** One file, all environments.
8. **Additive-only in P26.** Only `CREATE TABLE`, nullable or defaulted
   `ADD COLUMN`, and `CREATE INDEX`. No `DROP`, no `NOT NULL` without a prior
   backfill, no rename.
9. **`agency_id`, never `tenant_id`.** Introduced as nullable, then backfilled,
   then constrained — in **separate, successive** migrations. Never `NOT NULL`
   at creation on a populated table.
10. **`CREATE INDEX CONCURRENTLY` is the only non-transactional exception.** It
    cannot run inside a transaction block. Such statements go in a **dedicated
    migration**, marked `-- NON-TRANSACTIONAL` in a header the runner parses,
    recorded with `transactional = FALSE`, and executed by the runner outside a
    transaction with its ledger row written in a separate follow-up
    transaction. Non-transactional statements are never mixed with
    transactional DDL in the same file.
11. **No secrets in DDL or defaults.** No SMTP passwords, API tokens, WhatsApp
    secrets, or storage credentials anywhere in the schema. See section 8.
12. **Immutable checksums.** A file recorded in the ledger is never edited. A
    correction is a new migration.
13. **Execution role.** Migrations run only as the migrator role, never as the
    application role. See section 6.1.

### 6.1 PostgreSQL roles — mandatory precondition before RLS

RLS as final defense-in-depth is meaningless while the application connects as
the table owner. Before any RLS work begins, the following role separation is
**required**:

- A **migrator role** that owns the schema and its objects, and is the only
  role permitted to execute migrations.
- A **separate application role**, used by `database.get_connection()`.
- The application role is **not the owner** of any table. Table owners bypass
  RLS regardless of policy.
- The application role has **`BYPASSRLS` disabled**, and is not a superuser.
- The **migration channel remains explicitly privileged**. This is a deliberate
  design decision, documented and bounded — not an oversight. Migrations must
  be able to alter and backfill across all agencies, so the migrator role
  legitimately sits outside RLS.

The `run_*_e2e.py` scripts (entry points 1–3 in section 1.3) must be
classified as a **privileged TEST channel**, not as application traffic. They
are permitted to bypass the choke point; they are not permitted to run against
PROD.

---

## 7. Rollback strategy

### 7.1 What is genuinely reversible

| P26 operation | Reversible without loss? |
|---|---|
| `CREATE TABLE agencies` (empty) | Yes — `DROP TABLE` |
| `CREATE TABLE agencies` (populated) | **No** — the drop loses created agencies |
| `ADD COLUMN agency_id` (nullable, not backfilled) | Yes |
| `ADD COLUMN agency_id` + backfill | **No** — the drop loses tenant assignment |
| `CREATE INDEX` | Yes |
| `ENABLE ROW LEVEL SECURITY` + policies | Yes |

The point: rollback is free only **before** the backfill. After the backfill,
`DROP COLUMN agency_id` destroys information that cannot be reconstructed.

### 7.2 The three rollback windows

| Window | Mechanism | Cost |
|---|---|---|
| **W1 — inside the transaction** | automatic `ROLLBACK` on error | Zero. This is the primary defence, which is why rule 3 is non-negotiable. |
| **W2 — post-commit, pre-backfill** | execute the `_down.sql` file | Low, reversible. |
| **W3 — post-backfill** | **the down must not run** | The down raises. Recovery is restore from snapshot. |

### 7.3 Down file for a non-reversible migration

The down file exists, and refuses loudly:

```sql
BEGIN;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM contacts WHERE agency_id IS NOT NULL LIMIT 1) THEN
        RAISE EXCEPTION
          'Rollback 0NN refused: agency_id already populated. DROP would lose agency assignment. Procedure: restore from the pre-migration snapshot.';
    END IF;
END $$;
ALTER TABLE contacts DROP COLUMN IF EXISTS agency_id;
UPDATE schema_migrations
   SET rolled_back_at = NOW(),
       rolled_back_by_operator = :operator
 WHERE version = '0NN_p26_...';
COMMIT;
```

A down that fails loudly is safer than a down that drops data.

### 7.4 Ledger rollback semantics

**`DELETE` of a `schema_migrations` row is forbidden.** The ledger is
append-only in effect: a rollback is *recorded*, never erased. Deleting the row
would recreate exactly the condition P26-0 exists to eliminate — a schema
history that cannot be reconstructed from the ledger.

A rollback sets `rolled_back_at` and `rolled_back_by_operator`. Both are
written together; the schema constraint enforces this.

**A rolled-back migration is never silently re-applied under the same
version.** The runner treats a version with a non-null `rolled_back_at` as
consumed: it will not re-execute it, and it will not overwrite the row. Any
correction ships as a **new migration with a new version number**, whose notes
reference the rolled-back one.

This makes the sequence of what was applied, what was reverted, by whom, and
when, fully reconstructible from the ledger alone.

### 7.5 Non-negotiable operational prerequisite

**A database snapshot must be taken immediately before every P26 migration**,
with:

- the snapshot command and the restore command **written down and verified** —
  not "the platform takes backups";
- the **restore tested at least once on TEST** before anything touches PROD;
- the restore duration measured and recorded.

Without this, W3 has no real procedure and rollback remains theoretical. **This
is the single most important gate in the strategy**, because it is the only
defence that exists after the backfill.

### 7.6 Rolling back P26-0 itself

Migration `026` is purely additive: two empty tables, one function, two
triggers. Its down is safe and complete — drop the triggers, the function, and
the two tables. No application data is involved.

**P26-0 is the least risky migration of the entire phase**, and that is
deliberate: the safety infrastructure is built before the schema is touched.

### 7.7 P26-0 does not modify application data

`026` creates infrastructure tables and inserts exactly one row into
`schema_baseline`. It writes no row into any application table, updates no
existing row, and deletes nothing. No `UPDATE`, no `DELETE`, no `TRUNCATE`
against application data appears anywhere in P26-0.

---

## 8. Secrets policy

`agencies` is out of scope for P26-0, but the rule is stated here because it
constrains the schema P26-1 will introduce, and it must not be rediscovered
later.

**`agencies.settings` (JSONB) must never contain secrets.** Specifically
forbidden: SMTP passwords, API tokens, WhatsApp secrets, storage credentials,
signing keys, and any equivalent.

Permitted content: non-sensitive configuration, and **references** to secrets
held in an external store, for example:

```json
{ "smtp_secret_ref": "vault://agency/12/smtp", "locale": "it-IT" }
```

The rule is to be enforced structurally — a CHECK constraint rejecting
forbidden key names — and covered by an automated test, so that it fails at
authoring time rather than at review time.

---

## 9. Handling of `migrate_add_token.py`

**Classification: a risk to be neutralised before RLS. Not modified in P26-0.**

Findings:

- It is the only database entry point in the repository with **no environment
  guard** of any kind.
- It uses a **second, independent set of environment variables**
  (`DATABASE_URL`, `PGHOST`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`, `PGPORT`),
  with defaults `postgres` and `stima360` — so a misconfigured environment
  points it at a plausible database name rather than failing.
- It targets a table named `stima` (singular). The schema contains `stime`. On
  the current schema it would fail rather than alter anything.

Consequence for RLS: it opens a connection that does not pass through
`database.get_connection()`, so it sits outside whatever session context the
choke point establishes.

**Required sequence, in a later phase:**

1. Verify that it is not invoked by Render (build command, start command,
   pre-deploy hook, job definitions).
2. Verify that it is not invoked by any cron or scheduled task.
3. Verify that it is not referenced by any documented operational procedure or
   runbook.
4. Only after all three verifications come back negative, remove or neutralise
   it.

It must not be deleted on the strength of static analysis alone. A file that
appears dead in the repository may still be wired into a deployment
configuration that the repository does not contain.

Until then it is recorded in the risk register and covered by the entry-point
inventory.

---

## 10. Artefacts P26-0 must eventually produce

Listed as the deliverables of the implementation phase. **None is produced by
this document.**

| Artefact | Purpose |
|---|---|
| Read-only TEST schema snapshot script | Phase 0 — deterministic snapshot plus sha256 fingerprint, writing only under `reports/` |
| Baseline certificate | Phase 1 — fingerprint plus the full divergence classification, versioned as a document |
| Migration `026` and its down | Phase 2 — ledger and baseline tables, additive, transactional, idempotent |
| Migration runner | Phase 3 — forward-only, structural `>= 026` gate, checksum verification, mandatory operator identity, explicit `CONCURRENTLY` handling |
| Runner tests | Coverage of the gates below |
| DB entry-point inventory | Section 1, versioned, with the four known bypasses whitelisted and justified |
| Proven backup/restore procedure | Section 7.5 — written **and actually executed once on TEST**, with measured restore time |

### 10.1 Test coverage the runner and baseline require

| # | Test | Verifies |
|---|---|---|
| H1 | baseline migration is additive | no `DROP`, `ALTER ... DROP`, `TRUNCATE`, or `DELETE FROM` against application tables |
| H2 | baseline migration is transactional | opens `BEGIN`, closes `COMMIT` |
| H3 | baseline migration is idempotent in shape | `IF NOT EXISTS` on tables and indexes; catalogue-check pattern for triggers; `CREATE OR REPLACE` for the function |
| H4 | no retroactive registration | `026` contains no `INSERT INTO schema_migrations`; the only insert is into `schema_baseline` |
| H5 | ledger rejects pre-baseline versions | the `>= 26` constraint is present and correct |
| H6 | no secrets in schema | no `password`, `smtp_pass`, `api_token`, `secret`, `credential`, `access_key` identifiers in P26 DDL |
| H7 | baseline down is complete and safe | drops exactly the five created objects and nothing else |
| H8 | snapshot script is read-only | contains `SET TRANSACTION READ ONLY`; contains no `INSERT`/`UPDATE`/`DELETE`/`CREATE`/`ALTER`/`DROP`; writes only under `reports/` |
| H9 | fingerprint is deterministic | two runs over the same canonicalised input yield the same sha256 (offline fixture, no database) |
| H10 | runner ignores pre-baseline files | given `001`–`030`, the runner selects only `>= 026` |
| H11 | no new DB connection entry points | no `psycopg2.connect` outside `database.py` plus the explicit four-item whitelist; fails when a new one appears |
| H12 | operator identity is not derived from `ADMIN_USER` | `applied_by_operator` is never populated from `os.getenv("ADMIN_USER")` |
| H13 | ledger rollback never deletes | no `DELETE FROM schema_migrations` in any migration, down file, or runner path |
| H14 | rolled-back version is not re-applied | the runner skips a version whose `rolled_back_at` is set, and does not overwrite the row |

**H11 is the most important test for the RLS roadmap.** It is the only
mechanism preventing regression of the choke point over time.

---

## 11. Definition of Done — P26-0 implementation phase

P26-0 implementation is complete when **all** of the following hold:

1. The read-only TEST schema snapshot has run and produced an artefact with a
   reproducible sha256 fingerprint.
2. Every divergence between the real schema and migrations `001`–`025` is
   classified; **zero** items remain in `manual_change_unexplained`.
3. The TEST Baseline Certificate is written, carries the fingerprint, and is
   versioned.
4. Migration `026` exists, is additive, transactional, and idempotent, and
   **registers nothing for `001`–`025`**.
5. `026`'s down file exists and is verified complete and non-destructive.
6. The migration runner exists, structurally ignores versions below `026`,
   verifies baseline presence, database identity, applied versions, registered
   checksums, and version ordering, and requires an explicit operator identity.
7. The runner handles the `CREATE INDEX CONCURRENTLY` exception explicitly and
   correctly, and refuses non-transactional statements in ordinary migrations.
8. Ledger rollback semantics are implemented as `rolled_back_at` /
   `rolled_back_by_operator` only, with no delete path anywhere.
9. H1–H14 pass, with fresh output attached.
10. The backup/restore procedure is written **and has actually been executed
    once on TEST**, with measured restore time.
11. The DB entry-point inventory is versioned, and the four known bypasses are
    whitelisted with justification.
12. Migrations `001`–`025` remain in their current location, unmoved and
    unmodified.
13. `migrate_add_token.py` remains unmodified, and is recorded in the risk
    register with its pre-removal verification checklist.
14. The `agencies.settings` no-secrets rule is documented and its enforcement
    mechanism specified.
15. No application data was modified.
16. Nothing ran against PROD. No commit to `main`. No deploy.

**Exit gate to P26-1:** items 1–16 all green. Until item 10 (restore actually
tested) is satisfied, no migration touching application data may proceed.

---

## 12. Open decisions carried into P26-1

1. **`migrate_add_token.py`** — requires the three-way verification of section
   9 (Render, cron, runbooks) before removal.
2. **PostgreSQL role separation** (section 6.1) — a hard precondition for RLS,
   not a parallel workstream. RLS designed over an owner-connected application
   role provides no defence.
3. **Snapshot and restore, actually proven** (section 7.5) — the whole strategy
   rests here. After the `agency_id` backfill, restore is the only rollback
   that exists.
