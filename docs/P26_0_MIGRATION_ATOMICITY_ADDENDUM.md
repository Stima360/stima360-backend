# P26-0 Addendum — Migration / Ledger Atomicity

## Status

- **Type:** addendum to `docs/superpowers/specs/2026-09-05-p26-0-baseline-design.md`.
- **The P26-0 spec is not rewritten.** It remains closed and certified exactly
  as approved. This document records a defect found afterwards and the rule
  that supersedes two of its clauses **for versions `027` and above only**.
- **No historical certificate is invalidated.** Baseline `P26-BASELINE-001`,
  the pre-`026` fingerprint
  `84a44a9fdca44d9b4a8842686919eede3cbec052674241a096d2855c70e5f57d`, the
  backup/restore drill and the application of `026` on `stima360_db_test` all
  stand unchanged.
- **Discovered:** before migration `027` was ever applied. No database was
  modified by the defect, because no migration had yet taken the affected code
  path.
- Base: branch `core-0.1-test`, HEAD `05387faa0ffcb167e006e7fe8d76374a62071282`.

---

## 1. The defect

P26-0 §6 rule 5 states:

> **Ledger inside the same transaction.** The `INSERT INTO schema_migrations`
> sits inside the migration's own `BEGIN`/`COMMIT`. Ledger and DDL cannot
> diverge.

The runner was implemented differently. `scripts/p26_migrate.py`
`command_apply` does:

```python
cursor.execute(migration.path.read_text(encoding="utf-8"))
elapsed = int((time.monotonic() - started) * 1000)
register(cursor, migration, operator, elapsed)
connection.commit()
```

`register()` — not the migration file — writes the `schema_migrations` row. It
must, because it supplies `checksum_up`, `checksum_down`, `down_available`,
`transactional` and `execution_ms`, none of which a file can know about itself.

Each design is coherent alone. Combined they leave a gap. With
`connection.autocommit = False`, the server receives:

| # | Statement | Origin | Effect |
|---|---|---|---|
| 1 | `BEGIN` | psycopg2, implicit | transaction T1 opens |
| 2 | `BEGIN;` | migration file | `WARNING: there is already a transaction in progress` — no nesting, no savepoint |
| 3 | the DDL | migration file | inside T1 |
| 4 | `COMMIT;` | migration file | **T1 commits; the schema change is durable** |
| 5 | `INSERT INTO schema_migrations` | `register()` | runs in a new transaction T2 |
| 6 | `connection.commit()` | runner | T2 commits |

**The schema change and its ledger row commit separately.** If step 5 fails —
a constraint violation, the `trg_schema_migrations_guard` trigger firing on a
`database_name` mismatch, a dropped connection, a killed process — then
`except: connection.rollback()` rolls back T2 only. The schema is changed and
the ledger row is absent.

This defeats P26-0 §7.2 window **W1**, *"inside the transaction — automatic
ROLLBACK on error … the primary defence, which is why rule 3 is
non-negotiable"*. W1 covered the DDL but never the registration.

`build_plan` cannot detect the resulting state: it checks that the ledger does
not reference a file absent from disk, never the reverse. Recovery today rests
entirely on every migration being idempotent — recovery by property, not by
design, and it fails outright when `register()` fails deterministically.

## 2. Why `026` is unaffected and remains certified

`026` never executes the affected branch:

```python
if migration.is_baseline:
    apply_baseline(cursor, migration, operator, args)   # 026 goes here
else:
    cursor.execute(...); register(...)                  # the gap lives here
```

`apply_baseline` contains **zero** calls to `register()`. The baseline's record
is written into `schema_baseline` **by the migration file itself**, inside that
file's own `BEGIN`/`COMMIT` — which is genuinely atomic. `build_plan` treats
the baseline specially and never consults `schema_migrations` for it.

So `026` satisfies rule 5 as written, for a reason that does not generalise to
any later migration. `027` would have been the first migration ever to take the
vulnerable path. The defect was latent, never exercised, and is fixed before
first exposure.

`026` is **not modified** by this addendum, in file or in database.

## 3. The superseding rule

**For migration versions `>= 027`, the runner owns the UP transaction.**

- An UP migration file at or above `027` contains **no `BEGIN;` and no
  `COMMIT;`**.
- The runner executes the migration body, calls `register()`, and issues a
  single `connection.commit()`. On any exception it issues
  `connection.rollback()`.
- The schema change and its `schema_migrations` row therefore share **one
  commit**. Either both are durable or neither is.

This supersedes, **for `>= 027` only**:

| P26-0 §6 | Clause | Status for `>= 027` |
|---|---|---|
| rule 3 | *"Every migration opens `BEGIN;` and closes `COMMIT;`"* | Superseded for UP files. The runner brackets them instead. |
| rule 5 | *"The `INSERT INTO schema_migrations` sits inside the migration's own `BEGIN`/`COMMIT`"* | Superseded. `register()` owns the insert, inside the runner's transaction. |

**Rule 3's stated purpose is preserved and extended, not abandoned.** Its
rationale was §7.2 window W1 — automatic rollback on error. Under the new
ownership W1 still applies to the DDL *and* now also covers the ledger
registration, which rule 3's letter left outside. The intent is served better
than before; only the mechanism moves.

Every other P26-0 §6 rule is untouched: numbering, the mandatory down file,
idempotency, no environment guards, no per-environment duplicates,
additive-only, `agency_id` staging, the `CONCURRENTLY` exception, no secrets,
immutable checksums, and the migrator execution role.

### 3.1 DOWN files are unchanged, in both eras

The runner has **no `down` command** — `COMMANDS` is `{status, plan, apply}`.
Down files are executed manually, per P26-0 §7.2 window W2. Under `psql` each
statement autocommits on its own unless the script brackets itself.

**Every down file, at every version, keeps its own `BEGIN;` … `COMMIT;`.**

Without it a down file's guards and its `rolled_back_at` stamp would become
independently durable statements: a guard could pass, the drops could fail, and
the rollback stamp would already be committed. `validate_migration` reads only
the UP file (`migration.path`), so this asymmetry needs no special handling —
but it is stated explicitly here so that no later change "harmonises" the down
files and silently removes their atomicity.

### 3.2 Non-transactional migrations remain exceptional

A `-- NON-TRANSACTIONAL` migration exists solely for `CREATE INDEX
CONCURRENTLY`, which cannot run inside a transaction block. Its rules are
unchanged in both eras: no `BEGIN`/`COMMIT` in the file, a `CONCURRENTLY`
statement required, executed with `connection.autocommit = True`, and its
ledger row necessarily written in a following transaction.

That path is therefore **inherently non-atomic**, exactly as P26-0 §6 rule 10
already documents and accepts. This addendum does not change it and does not
claim to fix it. P26-1 uses no `CONCURRENTLY`, so the path is unused there.

## 4. Enforcement

Two independent checks, deliberately not sharing an implementation.

**Apply time** — `scripts/p26_migrate.py`:

```python
RUNNER_OWNED_TRANSACTION_FROM = 27
```

`validate_migration` gates on `migration.number`. Below the gate the certified
rule is preserved verbatim; at or above it, a file containing `BEGIN` or
`COMMIT` is a violation and `apply` refuses before touching the database.

**Review time** — `tests/test_p26_1_migration_rules.py`, rule **G2** of
`assert_p26_migration_rules()`, which Tasks 2, 7, 8 and 9 call for their own
migration. A test asserts the two constants are equal, so the duplication
cannot drift. Rule **G8** asserts every down file brackets itself, in both
eras.

Negative controls exist for all four wrong shapes: a pre-gate file missing
`BEGIN`, a pre-gate file missing `COMMIT`, a post-gate file that brackets
itself, and a post-gate file with a stray `BEGIN` or `COMMIT`.

## 5. Effect on the ledger and its invariants

No invariant is weakened; one starts actually holding.

- `checksum_up` / `checksum_down` are computed from disk at apply time by
  `discover_migrations()` and recorded by `register()`. Editing an **unapplied**
  migration is free and correct.
- `build_plan`'s drift check compares ledger and disk checksums **only for
  versions already registered**. `026` is never registered (baseline) and `027`
  is not yet applied, so no false drift is possible.
- `schema_migrations_no_pre_baseline`, `schema_migrations_down_consistency`,
  `schema_migrations_rollback_consistency`, the `schema_baseline_*` constraints
  and both `schema_ledger_guard` triggers are untouched.
- The append-only invariant is untouched: no `DELETE FROM schema_migrations`
  anywhere.
- **Strengthened:** "a registered migration's schema change is durable" and
  "its registration is durable" become the same commit. The property the ledger
  exists to express begins to hold for every migration from `027`.

## 6. Scope of the change

| Artefact | Change |
|---|---|
| `scripts/p26_migrate.py` | `RUNNER_OWNED_TRANSACTION_FROM` constant; version-gated branch in `validate_migration`; module docstring |
| `migrations/027_p26_agency_identity.sql` | `BEGIN;` and `COMMIT;` removed; schema semantics unchanged |
| `migrations/027_p26_agency_identity_down.sql` | **unchanged** |
| `migrations/026_p26_baseline.sql` and `_down` | **unchanged** |
| `tests/test_p26_1_migration_rules.py` | G2 version-gated; G8 added; runner-level and negative-control tests |
| `tests/test_p26_1_migration_027.py` | UP has no transaction block; DOWN still does |
| `tests/test_p26_baseline_isolation.py` | **unchanged** — all seven of its `validate_migration` tests use `026`-numbered fixtures, so the gate leaves them green |

No runtime product code, no database, no execution of any migration.

## 7. Consequence for P26-1

Migrations `028`, `029` and `030` are authored under the runner-owned rule from
the outset: no `BEGIN`/`COMMIT` in the UP file, own `BEGIN`/`COMMIT` in the
down file, and no self-written ledger row. The P26-1 design spec and
implementation plan have been corrected accordingly.
