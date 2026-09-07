# P26-1 — TEST migration runbook

**Status: a plan, not a record.** Nothing in this document has been executed.
It contains no output from a real run. Every command below is for Giorgio to
run by hand, in order, against `stima360_db_test`.

**Target:** `stima360_db_test` only.
**Branch:** `core-0.1-test` only.
**Migrations:** `027` → `028` → `029` → `030`, in that order, no skipping, no
reordering.

> **PROHIBITED.** This runbook must never be run against `stima360_db`. That is
> the production database and P26-1 is TEST only. If `DB_NAME` resolves to it,
> the runner refuses — but the refusal is the last line of defence, not the
> first. Check before you type.

> **GATE-MA1.** P26-1 does **not** certify platform-wide multi-agency
> isolation while non-CORE legacy Basic compatibility routes remain active
> (`crm`, `next_best_action`, `followup`). A second **real** agency must **not**
> be activated in production until those routes are migrated. TEST-only A/B
> fixtures are allowed, and are what the live matrix in Phase 3 uses.

---

## 0. Before you start — read this first

### 0.1 The runner applies *all* pending migrations in one invocation

`scripts/p26_migrate.py apply` has no `--target` and no `--to-version`. It
applies every pending migration it finds, in order, committing each one
separately. Run naively, a single `apply` would go `027 → 028 → 029 → 030`
without stopping.

That is incompatible with this runbook, which **must** stop between `028` and
`029` to take a fresh backup — `029` is the irreversible one (§16 of the design
spec).

**Therefore this runbook stages the migration files.** `discover_migrations()`
globs `migrations/*.sql` non-recursively, so files moved into
`migrations/.staged/` are invisible to the runner, and `verify_contiguous()` is
satisfied as long as what remains starts at `026` and has no gap.

Create the holding directory once, at the start:

```bash
mkdir -p migrations/.staged
```

Staging is mechanical and reversible; step 9.3 verifies all four files are back
before the final snapshot. **If you would rather not move files, do not
improvise a partial run** — stop and ask for a runner change instead.

### 0.2 Restore path, known before anything is applied

If any checkpoint fails, the recovery is:

```bash
# Restore into a SEPARATE database, never over the live TEST one.
createdb -h "$DB_HOST" -U "$DB_USER" stima360_db_test_restore
pg_restore -h "$DB_HOST" -U "$DB_USER" -d stima360_db_test_restore --clean --if-exists \
  reports/p26_1/pre_029_stima360_db_test.dump
```

Inspect the restored copy, decide, and only then swap. There is no step in this
runbook that drops `stima360_db_test`.

### 0.3 Reference numbers

Taken from the migration files themselves, not from prose:

| Object | Expected after |
|---|---|
| New tables (`agencies`, `operator_users`, `agency_memberships`, `operator_sessions`) | `027` |
| Default Agency row, `slug='stima360'` | `027` |
| New columns, **10** total (contacts 3, leads 3, activities 2, tasks 2) | `028` |
| Plain agency indexes, 4 | `028` |
| `SET NOT NULL` on `agency_id`, 4 | `030` |
| Composite foreign keys, **3** | `030` |
| Unique key `contacts_agency_scope_unq`, 1 | `030` |
| Agency-aware indexes, 9 | `030` |
| `core_agency_integrity()` function, 1 | `030` |
| Triggers, **exactly 2** — `activities` and `tasks` only | `030` |
| Ledger rows for `027`–`030` | one each, no duplicates |

> **Correction recorded.** Design spec §17 step 8 says "eleven columns". The
> column plan in §3, `migrations/028_p26_core_agency_columns.sql` and
> `tests/test_p26_1_migration_028.py` all say **ten**. Ten is correct; the
> spec's "eleven" is a typo. Verify against ten.

---

## Phase 1 — Preconditions

Every step here is read-only. **Stop on the first mismatch.**

### 1.1 Branch and working tree

```bash
git branch --show-current     # must print: core-0.1-test
git log --oneline -1
git status --short
```

**STOP IF** the branch is not `core-0.1-test`, or the tree carries changes you
did not intend. Do not proceed on `main`.

### 1.2 Database identity

```bash
echo "$DB_NAME"
psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c "SELECT current_database(), current_user, version();"
```

**STOP IF** `DB_NAME` is unset, does not contain the `test` marker, or is
`stima360_db`. The database you are connected to must be the database you
believe you are connected to — confirm it from `current_database()`, not from
your shell history.

### 1.3 Ledger state

```bash
python scripts/p26_migrate.py status --operator "<real person>"
```

**Expect:** `026` applied; `027`, `028`, `029`, `030` pending; no problems
reported.

**STOP IF** any of `027`–`030` is already recorded as applied, if the runner
reports the ledger and the files disagree, or if a checksum mismatch appears. A
partially applied set is not a state to push through — it is a state to
diagnose.

```bash
psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT version, applied_at, operator FROM schema_migrations ORDER BY version;"
```

### 1.4 Static validation (no database access)

```bash
python scripts/p26_migrate.py plan --operator "<real person>"
```

**Expect:** exactly four migrations, in order, no gap, `violations=none`.

**STOP IF** the runner reports any violation. A migration that fails static
validation must be fixed in its file before anything is applied — the files are
not yet in the ledger, so editing them is still free.

### 1.5 Pre-migration schema snapshot and fingerprint

```bash
python scripts/p26_schema_snapshot.py
```

Record the artifact path and the SHA-256 fingerprint. This is the "before"
half of the pair; step 9.4 produces the "after".

**STOP IF** the fingerprint differs from the one certified at P26-0 baseline
without an explanation you can state. An unexplained schema difference means
something changed TEST outside the ledger, and this runbook's assumptions no
longer hold.

### 1.6 Full backup

```bash
mkdir -p reports/p26_1
pg_dump -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -Fc \
  -f reports/p26_1/pre_027_stima360_db_test.dump
ls -l reports/p26_1/pre_027_stima360_db_test.dump
sha256sum reports/p26_1/pre_027_stima360_db_test.dump
```

**STOP IF** `pg_dump` exits non-zero, or the file is absent or implausibly
small. **Do not proceed without a backup you have verified exists.**

### 1.7 Pre-migration row counts

```bash
psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT 'contacts' t, COUNT(*) FROM contacts
   UNION ALL SELECT 'leads', COUNT(*) FROM leads
   UNION ALL SELECT 'activities', COUNT(*) FROM activities
   UNION ALL SELECT 'tasks', COUNT(*) FROM tasks;"
```

**Write these four numbers down.** Steps 3.2 and 5.2 compare against them. A
backfill that changes a row count has done something other than backfill.

---

## Phase 2 — Apply `027` (agency identity)

### 2.1 Stage

```bash
mv migrations/028_p26_core_agency_columns.sql   migrations/.staged/
mv migrations/029_p26_core_agency_backfill.sql  migrations/.staged/
mv migrations/030_p26_core_agency_enforce.sql   migrations/.staged/
python scripts/p26_migrate.py plan --operator "<real person>"   # expect: 027 only
```

### 2.2 Apply

```bash
python scripts/p26_migrate.py apply --operator "<real person>"
```

**STOP IF** the command exits non-zero. The runner owns the transaction: the
migration body and its ledger row commit together, so a failure leaves neither.

### 2.3 Verify

```bash
psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT tablename FROM pg_tables
    WHERE tablename IN ('agencies','operator_users','agency_memberships','operator_sessions')
    ORDER BY tablename;"

psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT id, slug, name, status FROM agencies;"

psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT version FROM schema_migrations WHERE version LIKE '027%';"
```

**Expect:** four tables; exactly one `agencies` row with `slug='stima360'` and
`status='active'`; exactly one ledger row for `027`.

**STOP IF** more than one active `stima360` agency exists. Every server-side
Default Agency lookup in P26-1 assumes exactly one, and `029`'s pre-guard will
refuse anyway — but you want to know now, not three steps later.

---

## Phase 3 — Apply `028` (nullable structure)

### 3.1 Stage and apply

```bash
mv migrations/.staged/028_p26_core_agency_columns.sql migrations/
python scripts/p26_migrate.py plan  --operator "<real person>"   # expect: 028 only
python scripts/p26_migrate.py apply --operator "<real person>"
```

### 3.2 Verify

```bash
psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT table_name, column_name, is_nullable
     FROM information_schema.columns
    WHERE table_name IN ('contacts','leads','activities','tasks')
      AND column_name IN ('agency_id','assigned_agent_id','created_by_user_id')
    ORDER BY table_name, column_name;"
```

**Expect:** **10** rows, every one `is_nullable = YES`.

```bash
psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT COUNT(*) FILTER (WHERE agency_id IS NULL) AS nulls, COUNT(*) AS total FROM contacts;"
```

**Expect:** `nulls = total` on all four tables — `028` writes no data. Re-run
the step 1.7 count query and confirm the four numbers are **unchanged**.

**STOP IF** any column arrived `NOT NULL`, carries a `DEFAULT`, or any row
count moved.

---

## Phase 4 — Fresh backup, immediately before `029`

`029` is the irreversible migration. This backup is the restore point for
everything after it, and it must be taken **now**, not reused from step 1.6.

```bash
pg_dump -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -Fc \
  -f reports/p26_1/pre_029_stima360_db_test.dump
ls -l reports/p26_1/pre_029_stima360_db_test.dump
sha256sum reports/p26_1/pre_029_stima360_db_test.dump
```

**STOP IF** `pg_dump` fails or the file is missing. Do not continue to `029`
without this dump.

> **`029` is irreversible.** Its down file, `029_p26_core_agency_backfill_down.sql`,
> deliberately does nothing but `RAISE EXCEPTION`. There is no SQL reverse
> migration and there will not be one: the backfill cannot distinguish a row it
> set from a row that already carried the value, so "undoing" it would be a
> guess. **Downgrade from this point is restore-from-backup, using the dump you
> just took and the restore commands in §0.2 — not a down migration.**

---

## Phase 5 — Apply `029` (controlled backfill)

### 5.1 Stage and apply

```bash
mv migrations/.staged/029_p26_core_agency_backfill.sql migrations/
python scripts/p26_migrate.py plan  --operator "<real person>"   # expect: 029 only
python scripts/p26_migrate.py apply --operator "<real person>"
```

`029` carries its own pre-guard and post-guard. The pre-guard refuses unless
exactly one active `slug='stima360'` agency exists; the post-guard counts
remaining `NULL`s across all four tables and raises if any survive.

**STOP IF** the command exits non-zero. A guard that fired is the migration
doing its job — diagnose the cause, restore if needed, and do not re-run
blindly.

### 5.2 Verify

```bash
psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT 'contacts' t, agency_id, COUNT(*) FROM contacts GROUP BY agency_id
   UNION ALL SELECT 'leads', agency_id, COUNT(*) FROM leads GROUP BY agency_id
   UNION ALL SELECT 'activities', agency_id, COUNT(*) FROM activities GROUP BY agency_id
   UNION ALL SELECT 'tasks', agency_id, COUNT(*) FROM tasks GROUP BY agency_id
   ORDER BY 1, 2;"
```

**Expect:** exactly one group per table, its `agency_id` the Default Agency's
id, and its count equal to the corresponding step 1.7 number.

```bash
psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT COUNT(*) FROM contacts WHERE agency_id IS NULL;"   # and the other three
```

**Expect:** zero on all four.

**STOP IF** a second group appears, a count differs from step 1.7, or any
`NULL` remains.

---

## Phase 6 — Apply `030` (constraints and enforcement)

### 6.1 Stage and apply

```bash
mv migrations/.staged/030_p26_core_agency_enforce.sql migrations/
python scripts/p26_migrate.py plan  --operator "<real person>"   # expect: 030 only
python scripts/p26_migrate.py apply --operator "<real person>"
```

`030` orders its work cheapest-failure-first: `SET NOT NULL` runs before the
constraints, so an incomplete backfill aborts before anything expensive is
built.

**STOP IF** the command exits non-zero.

### 6.2 Verify — NOT NULL

```bash
psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT table_name, column_name, is_nullable FROM information_schema.columns
    WHERE column_name = 'agency_id'
      AND table_name IN ('contacts','leads','activities','tasks')
    ORDER BY table_name;"
```

**Expect:** four rows, all `is_nullable = NO`.

### 6.3 Verify — keys and indexes

```bash
psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT conname, contype FROM pg_constraint
    WHERE conname IN ('contacts_agency_scope_unq','contacts_agent_same_agency_fk',
                      'leads_agent_same_agency_fk','leads_contact_same_agency_fk')
    ORDER BY conname;"
```

**Expect:** four rows — one unique key (`contype='u'`) and **three** composite
foreign keys (`contype='f'`).

```bash
psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT indexname FROM pg_indexes
    WHERE indexname LIKE 'idx_%agency%' ORDER BY indexname;"
```

**Expect:** the 4 plain indexes from `028` plus the **9** agency-aware indexes
from `030`.

### 6.4 Verify — the integrity trigger and its exact coverage

```bash
psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT proname FROM pg_proc WHERE proname = 'core_agency_integrity';"

psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT c.relname AS table_name, t.tgname
     FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
    WHERE NOT t.tgisinternal AND t.tgfoid = 'core_agency_integrity'::regproc
    ORDER BY c.relname;"
```

**Expect:** the function exists, and **exactly two** triggers — one on
`activities`, one on `tasks`. Nothing on `contacts`, nothing on `leads`,
nothing anywhere else.

**STOP IF** the trigger appears on any other table. Wider coverage than the
design specifies is not a harmless extra; it means the decision table is being
applied to rows it was never reasoned about.

### 6.5 Verify — the ledger

```bash
psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT version, COUNT(*) FROM schema_migrations
    WHERE version LIKE '027%' OR version LIKE '028%'
       OR version LIKE '029%' OR version LIKE '030%'
    GROUP BY version ORDER BY version;"
```

**Expect:** four rows, each with `count = 1`. No duplicates, no omissions.

---

## Phase 7 — Seed the TEST fixtures

**Not a migration.** Agency B and the six operators are TEST fixtures; putting
them in the ledger would ship test accounts to every environment the ledger is
replayed into.

```bash
export P26_SEED_OWNER_A_PASSWORD=...      # six values, from your password manager
export P26_SEED_ADMIN_A_PASSWORD=...
export P26_SEED_AGENT_A_PASSWORD=...
export P26_SEED_AGENT_A2_PASSWORD=...
export P26_SEED_OWNER_B_PASSWORD=...
export P26_SEED_PLATFORM_PASSWORD=...

python scripts/p26_seed_agencies_test.py --operator "<real person>"
```

**Expect:** `agencies +1`, `operators +6`, `memberships +5`.

Six operators, **five** memberships. `platform_admin` deliberately gets none —
an unbound operator with `is_platform_admin = TRUE` is what a platform admin
*is*. The script is idempotent; a second run adds nothing.

**STOP IF** any password variable is unset (the script refuses and names them
all), or if the counts differ.

---

## Phase 8 — Offline regression, now against the migrated schema

```bash
python -m pytest -q tests/test_p26_1_*.py
python -m pytest -q
```

**Expect:** the twelve P26-1 files green, and the full suite no worse than the
recorded pre-migration baseline.

**STOP IF** anything that was green before is red now. A test that passed
against the un-migrated schema and fails against the migrated one has found a
real difference between what the code assumes and what the database now
enforces.

---

## Phase 9 — Close out

### 9.1 Smoke: auth

```bash
curl -si -X POST "$BACKEND_URL/api/operator-auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"email":"owner.a@test.stima360.local","password":"'"$P26_SEED_OWNER_A_PASSWORD"'"}' | head -20
```

**Expect:** `200` and a `Set-Cookie: stima360_operator_session=…; Secure; HttpOnly`.

### 9.2 Smoke: CORE, both channels

```bash
# operator session
curl -s -o /dev/null -w '%{http_code}\n' -b cookies.txt "$BACKEND_URL/api/core/contacts"
# legacy Basic
curl -s -o /dev/null -w '%{http_code}\n' -u "$ADMIN_USER:$ADMIN_PASS" "$BACKEND_URL/api/core/contacts"
# no credential
curl -s -o /dev/null -w '%{http_code}\n' "$BACKEND_URL/api/core/contacts"
```

**Expect:** `200`, `200`, `401`.

**STOP IF** any request returns `500`. An unexpected 500 after migration means
the code and the schema disagree, and no further step is meaningful until you
know why.

### 9.3 Restore the migration directory

```bash
ls migrations/.staged/            # expect: empty
ls migrations/0[23]*.sql          # expect: 026 through 030, all present
rmdir migrations/.staged
git status --short migrations/
```

**STOP IF** any migration file is still staged. The tree must end with all four
files where they belong.

### 9.4 Post-migration snapshot and fingerprint

```bash
python scripts/p26_schema_snapshot.py
```

Record the post-`030` fingerprint next to the pre-`027` one from step 1.5.

### 9.5 Final tree check

```bash
git diff --check
git status --short
```

**No `git commit`. No `git push`. No deploy.** This runbook ends with a
migrated TEST database and a clean report — nothing more.

---

## Phase 10 — The live certification matrix (defined here, executed later)

This phase is **not** part of the migration. It becomes executable only once
Phases 1–9 have completed and the TEST backend is running against the migrated
database. It is listed here so the handoff is explicit.

1. **Seed** — done in Phase 7; verify Agency B and the six operators exist.
2. **Start or redeploy the TEST backend** against the migrated database.
3. **Auth smoke** — session cookie, legacy Basic, invalid credential, session
   precedence when both are sent.
4. **CORE smoke** — list, read, create, update, delete for `owner_a`.
5. **A/B hostile matrix** — spec §15 items 23–46 against real data: cross-agency
   list, detail, update, delete; forged `agency_id`, `role`,
   `is_platform_admin`; query and header overrides; the agent narrowing; the
   byte-identical 404.
6. **Assignment matrix** — owner/admin same-agency success; other-agency target
   `400`; inactive and missing membership `400`; unassign; agent caller `403`;
   unbound platform admin deriving the agency from the record.
7. **NBA compatibility** — the Oggi view still loads through legacy Basic, and
   its reads are Default-Agency-bound, never `WHERE TRUE`.
8. **Public STIMA regression** — an anonymous estimation still returns `200`,
   creates Default-Agency rows, and accepts no client `agency_id`.
9. **Cleanup** — delete only by ids captured during the run, or with an
   agency-scoped predicate. No broad unscoped `DELETE`. Confirm no P26-1 test
   debris remains.
10. **Final live certification** — record every request, status, response and
    post-reload state; report the unexpected-500 count as zero.

GATE-MA1 continues to apply throughout: TEST A/B fixtures certify the CORE
slice, not the platform.
