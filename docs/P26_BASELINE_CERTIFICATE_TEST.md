# P26 Baseline Certificate — TEST

## Verdict

# P26-0 BASELINE + MIGRATION LEDGER — COMPLETE (TEST)

**Certified, applied, and verified on `stima360_db_test` on 2026-09-05.**

| Gate | Result |
|---|---|
| Divergence classification | **PASS** — `manual_change_unexplained` = 0 of 1902 objects, no waivers |
| Schema fingerprint | **PASS** — `p26-snapshot-2`, restore-stable |
| Backup / restore drill | **PASS** — structure and fingerprint |
| Migration `026` applied to TEST | **YES** — see section 9 |
| Migrations 001–025 tracked | **NO** — final and deliberate, see section 4 |
| PROD | **untouched** — never contacted at any point in P26-0 |

The fingerprint is issued under `p26-snapshot-2` and was demonstrated
restore-stable against a real restored database, not a simulation: the source
and the restore-check database produced **the same digest** while their raw
deparsed text genuinely differed. Section 8 records the defect that made the
first digest unusable and how it was corrected.

Scope of this document is TEST. Nothing here certifies PROD, and the PROD
baseline remains an open, separate exercise.

---

## 1. Evidence

The snapshot was executed by the operator from the macOS shell holding the TEST
credentials, using `scripts/p26_schema_snapshot.py` unmodified — a read-only
transaction (`SET TRANSACTION READ ONLY`), SELECT statements only, artefacts
written under `reports/` and nowhere else.

Independent verification performed on the artefact:

| Check | Result |
|---|---|
| `metadata.database_name` | `stima360_db_test` — TEST, not PROD |
| Declared `schema_fingerprint` | `669c18de…028c1` |
| Fingerprint recomputed from the `schema` object | `669c18de…028c1` — **identical** |
| `.sha256` sidecar agrees with the declared digest | yes |
| Section counts vs. operator report | tables 60, columns 764, constraints 802, indexes 216, sequences 59, policies 0 — **all match** |
| `schema_migrations` / `schema_baseline` present | **no** — confirms `026` not applied |

The fingerprint covers the `schema` object only, never the run metadata, so it
is reproducible against an unchanged schema.

---

## 2. Certificate

| Field | Value |
|---|---|
| Baseline version | `P26-BASELINE-001` |
| Environment | TEST |
| Database name | `stima360_db_test` |
| Snapshot artefact | `reports/p26_baseline_TEST_20260905T174620Z.json` |
| Snapshot checksum file | `reports/p26_baseline_TEST_20260905T174620Z.sha256` |
| Snapshot format | `p26-snapshot-2` — restore-stable |
| **Schema fingerprint (SHA256)** | **`84a44a9fdca44d9b4a8842686919eede3cbec052674241a096d2855c70e5f57d`** |
| Restore-stability | **PASS** — same digest from `stima360_db_test_restorecheck`, see section 8.5 |
| Snapshot taken at (UTC) | `2026-09-05T17:46:20Z` |
| Superseded artefact | `reports/p26_baseline_TEST_20260905T170601Z.json`, format `p26-snapshot-1`, digest `669c18de…028c1`. Retained as evidence; **must not be used to certify.** |
| `026_p26_baseline.sql` checksum at certification | `7da4ff8fbc37208576759e2b64101ec59d5efea2332a8e455064d5148327c181` |
| Certified by operator | `giorgio.larasa` — recorded in `schema_baseline.certified_by_operator`, supplied explicitly on the command line, never derived from credentials |
| Pre-baseline tracked | `FALSE` — final, enforced by `schema_baseline_not_retroactive` |
| Migration `026` applied to TEST | **YES** — 2026-09-05, see section 9 |

---

## 3. Divergence classification

Performed against the snapshot named in section 2. Every object in the live
`public` schema was attributed to an origin; nothing was waived.

| Category | Count | Status |
|---|---|---|
| `expected_from_migration_NNN` | 1824 | attributed |
| `legacy_pre_migrations` | 77 | attributed — `database.py` |
| `postgres_default` | 1 | the `plpgsql` extension, created by the server |
| `manual_change_unexplained` | **0** | **GATE PASS** |
| **Total objects examined** | **1902** | |

### 3.0 Breakdown by object class

| Class | Total | From migrations | Legacy | Unexplained |
|---|---|---|---|---|
| Tables | 60 | 57 | 3 | **0** |
| Columns | 764 | 710 | 54 | **0** |
| Indexes | 216 | 210 | 6 | **0** |
| Constraints | 802 | 791 | 11 | **0** |
| Sequences | 59 | 56 | 3 | **0** |
| Extensions | 1 | 1 (`plpgsql`, server default) | 0 | **0** |

Row-level security is disabled on all 60 tables and there are 0 policies,
consistent with every migration file: none enables RLS.

### 3.0.1 Auto-generated names resolved, not waived

Six constraints carry names that appear in no source file. Each was traced to a
PostgreSQL naming rule rather than accepted as unexplained:

| Object | Origin |
|---|---|
| `buy_requests_check1` / `_check2` / `_check3` | the three unnamed table-level `CHECK`s at `004_buy_01.sql` lines 21–23; PostgreSQL suffixes duplicates |
| `buy_request_locations_check1` | the second unnamed table-level `CHECK` at `004_buy_01.sql` line 29 |
| `owner_visit_feedback_publicat_supersedes_feedback_publicat_fkey` | inline `REFERENCES` on `supersedes_feedback_publication_id`, `010_owner_02_p1.sql` line 138; name truncated to 63 characters |
| `owner_visit_feedback_publicat_superseded_by_feedback_publi_fkey` | inline `REFERENCES` on `superseded_by_feedback_publication_id`, `010_owner_02_p1.sql` line 140; same truncation |

Column comparison was performed case-insensitively. `database.py` declares
identifiers such as `mqCantina` unquoted, and PostgreSQL folds them to
`mqcantina`; the 14 apparent mismatches were case folding, not divergence.

### 3.0.2 Declared but absent — 30 legacy columns

The comparison also runs in the opposite direction. Thirty columns that
`database.py` would create are **not** present in TEST. These are not
divergences in the database — nothing was added by hand — but un-executed
legacy code, and they are recorded because they carry an operational risk.

- `stime`: `lead_status`, `note_internal` (from `migrazione_gestionale_stime`)
- `stime_dettagliate`: 28 columns from `migrazione_stime_dettagliate_completa`
  (`nome`, `cognome`, `email`, `telefono`, `indirizzo`, `microzona`, `mq`,
  `locali`, `bagni`, `piano`, `anno`, `stato`, `ascensore`, `pertinenze`,
  `tipologia`, `vistaMare`, `posizioneMare`, `distanzaMare`, `barrieraMare`,
  `mqGiardino`, `mqTerrazzo`, `mqGarage`, `mqPostoAuto`, `mqCantina`,
  `mqSoffitta`, `mqTaverna`, `numBalconi`, `altroDescrizione`)

All 30 originate from `database.py` and none from any file under `migrations/`.

> **Operational warning.** The `if __name__ == "__main__"` block of
> `database.py` calls every one of these functions. Running `python database.py`
> against TEST would add these 30 columns and **invalidate the fingerprint in
> section 2**, silently breaking the baseline. `database.py` must not be
> executed as a script against TEST while this baseline stands.

### 3.1 What the classification had to account for

Established by static analysis during the P26-0 audit, and confirmed by the
live comparison:

**Legacy objects, expected in `legacy_pre_migrations`.** Created by functions
in `database.py` under `__main__`, and present in no file under `migrations/`:

- `stime`
- `stime_dettagliate`
- `zone_valori`

The real schema is therefore, by construction, not the sum of the migration
files. Any classification that does not place these three in the legacy
category is wrong.

**Environment-specific duplicate pairs.** On TEST, `010`/`011` are expected to
be applied and `014`/`015` are expected **not** to be:

| File | In-file guard |
|---|---|
| `010_owner_02_p1.sql` | `current_database() = 'stima360_db_test'` |
| `011_owner_02_p5.sql` | `current_database() = 'stima360_db_test'` |
| `014_owner_02_p1_prod.sql` | `current_database() = 'stima360_db'` |
| `015_owner_02_p5_prod.sql` | `current_database() = 'stima360_db'` |

`014` is byte-identical to `010` apart from the guard and a comment. Because
the two sets are mutually exclusive per environment, the TEST fingerprint will
differ from the PROD fingerprint. That difference is expected and is not a
divergence to be explained away.

**Expected objects from the migration files.** 25 up files, `001`–`025`,
creating the tables inventoried in `INTEGRATION_MIGRATIONS_INVENTORY.md`. Note
that `021` is a data migration (`UPDATE leads`) and creates no object.

**Confirmed by the live comparison.** All three legacy tables are present and
were classified `legacy_pre_migrations`. The `010`/`011` TEST objects are
present; no `014`/`015`-only artefact exists, consistent with the guards. `012`
contributes exactly one object (the replacement `flow_events_source_module_check`
constraint) and `021` contributes none, as predicted.

### 3.2 Scope of the comparison

Stated so the certificate is not read as claiming more than it verified. The
comparison covers the **existence and origin** of tables, columns, indexes,
constraints, sequences, extensions, RLS flags and policies. It does **not**
diff column data types, defaults, or nullability against the SQL sources: those
would require a full SQL type resolver, and an approximate one would produce
false confidence. A hand-edited column *type* on an otherwise expected column
would therefore not be detected by this pass. The snapshot records those
attributes, so the fingerprint will still catch any future change to them.

---

## 4. Migrations 001–025 remain untracked

Final and not pending. This holds regardless of whether the certificate is ever
signed.

Migrations `001`–`025` are **not** recorded in `schema_migrations` and never
will be. The reasons are structural:

1. **A uniform ledger is arithmetically impossible.** `010`/`011` and
   `014`/`015` are mutually exclusive per environment, so "001–025 applied" is
   false in both TEST and PROD.
2. **Historical application state was never demonstrable.** No ledger existed
   while those migrations were applied, and table existence is not evidence of
   application.
3. **The real schema is not the sum of the files.** The three legacy objects
   above belong to no migration.
4. **A false ledger authorises destructive rollback.** 20 of the down files
   contain `DROP TABLE`, several dropping up to six tables. A ledger asserting
   "025 applied" would authorise a runner to execute `025_down`.

The exclusion is enforced structurally, not by convention:

- `schema_migrations.schema_migrations_no_pre_baseline` — a CHECK constraint
  whose regular expression accepts only versions `026` and above. It uses no
  cast, so a malformed version is rejected rather than raising.
- `scripts/p26_migrate.py` — `MIN_VERSION = 26`; files below that are never
  discovered.
- `schema_baseline.schema_baseline_not_retroactive` — a CHECK constraint
  forcing `pre_baseline_tracked = FALSE`.
- `tests/test_p26_baseline_isolation.py` — asserts that `026` contains no
  `INSERT INTO schema_migrations` and no reference to any version `001`–`025`.

Files `001`–`025` **remain in `migrations/` in their original location.** They
were not moved, renamed, or relocated; the forward-only guarantee comes from
the runner and the schema, not from file layout. A test asserts they are still
in place.

---

## 5. Route to the apply — all steps closed

Retained as the audit trail of how the apply was authorised. Every step is
complete; section 9 records the execution.

1. ~~Provide TEST database credentials to the execution environment.~~ **Done.**
2. ~~Run `scripts/p26_schema_snapshot.py`.~~ **Done** — artefact and fingerprint
   in section 2.
3. ~~Classify every divergence.~~ **Done** — section 3, 1902 objects.
4. ~~Reduce `manual_change_unexplained` to 0.~~ **Done** — it is 0, with no
   waivers.
5. ~~Backup / restore drill.~~ **Done** — PASS, `docs/P26_BACKUP_RESTORE_TEST.md`
   section 7.1. Restored into a separate database, never over the source.
6. ~~Fingerprint reissued and proven restore-stable.~~ **Done** — section 8.5.
7. ~~Countersign section 2.~~ **Done** — `giorgio.larasa`, recorded in the
   `schema_baseline` row itself, not only in this document.
8. ~~Apply `026`.~~ **Done** — section 9.
9. ~~Confirm with `p26_migrate.py status`.~~ **Done** — section 9.

The runner enforced the prerequisite structurally: it refuses to apply `026`
without a well-formed fingerprint and an artefact that exists on disk.

**The fingerprint in section 2 expires the moment the TEST schema changes.**
It certifies `stima360_db_test` as it stood at 2026-09-05T17:46:20Z, before
`026`. Applying `026` itself added `schema_migrations` and `schema_baseline`, so
a snapshot taken now will **not** reproduce this digest — that is expected and
correct, not drift. Any later comparison must be against a post-026 snapshot.

A stray `python database.py` would still change the schema silently; see
section 3.0.2.

---

## 6. Status of the artefacts P26-0 was to produce

| Artefact | State |
|---|---|
| `scripts/p26_schema_snapshot.py` | Written. **Executed** against TEST, read-only. |
| `scripts/p26_sql_canonical.py` | Written. Restore-stable canonicaliser, section 8. |
| Snapshot artefact and fingerprint | **PRODUCED** — section 2, digest independently recomputed. |
| This certificate | **Signed and complete.** |
| `migrations/026_p26_baseline.sql` | Written. **Applied to TEST.** |
| `migrations/026_p26_baseline_down.sql` | Written. Not executed — nothing to roll back. |
| `scripts/p26_migrate.py` | Written. **Executed** — `apply` and `status`. |
| `tests/test_p26_baseline_isolation.py` | Written and passing (127 tests). |
| `tests/test_p26_db_entrypoints.py` | Written and passing (27 tests). |
| `tests/test_p26_fingerprint_restore_stability.py` | Written and passing (30 tests). |
| Full regression suite | 1667 passed, 23 skipped, 0 failed. |
| `docs/P26_DB_ENTRYPOINTS.md` | Written. |
| `docs/P26_BACKUP_RESTORE_TEST.md` | Written. Drill **still outstanding**. |

The baseline is certified. `026` is not applied, and nothing has been committed,
pushed, or deployed.

---

## 7. Attempt log

### Attempt 2 — 2026-09-05

Operator reported that TEST credentials were available in the local macOS
environment and requested the operational phase. The attempt did **not**
proceed past the DB check. Recorded facts, all verified rather than assumed:

| Check | Result |
|---|---|
| `.env` in repository root or any parent reachable by the agent | absent |
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` in the agent's execution environment | absent |
| `SELECT current_database(), current_user` | **not executed** — no connection attempted |
| `scripts/p26_schema_snapshot.py` executed | **NO** |
| Writes of any kind against any database | none |
| `migrations/026` applied | **NO** |

Root cause: the agent's execution environment is a Linux sandbox that is
separate from the operator's macOS shell. Credentials exported in that shell,
and the macOS virtualenv at `.venv/bin/python` (a symlink to
`/opt/homebrew/opt/python@3.13/bin/python3.13`), are both outside the sandbox.
Direct TCP/DNS egress from the sandbox is also unavailable.

The guard in `assert_test_database_name` was therefore never reached with a
real value, and no target was guessed. Section 2 remains `PENDING`; the gate on
`manual_change_unexplained` remains **unevaluated, not passed**.

Closure requires either (a) the operator running the P26-0 sequence in the
macOS shell that holds the credentials, or (b) the credentials being made
readable to the execution environment. Nothing else in this certificate
changes as a result of this attempt.

### Attempt 3 — 2026-09-05 — CERTIFIED

Resolved by route (a): the operator ran `scripts/p26_schema_snapshot.py` in the
macOS shell and supplied the artefact. The analysis below was performed on that
artefact; no database connection was opened from the agent's environment, and
no write of any kind was issued against any database.

| Step | Result |
|---|---|
| Fingerprint recomputed from the artefact | matches the declared digest exactly |
| Section counts vs. operator report | all six match |
| Objects examined | 1902 |
| `manual_change_unexplained` | **0** |
| Gate | **PASS** |
| `tests/test_p26_baseline_isolation.py` | 118 passed |
| `tests/test_p26_db_entrypoints.py` | 27 passed |
| Full regression (`tests/`) | 1628 passed, 23 skipped, 0 failed |
| `migrations/026` applied | **NO** |
| Commit / push / deploy | none |

**Execution-environment note, recorded for audit honesty.** The test suites
were run with the sandbox interpreter (CPython 3.10) rather than the macOS
virtualenv `./.venv/bin/python` (CPython 3.13), which is not executable outside
macOS. Both P26-0 suites and the full regression are offline — they perform
static analysis of repository files and in-process assertions, and open no
network or database connection — so the interpreter substitution does not
affect what they assert. It is recorded rather than glossed over because the
run was not made with the interpreter the operator specified. Re-running them
on macOS with `./.venv/bin/python` is the confirming step, and is cheap.

The remaining outstanding items are listed in section 5: operator
countersignature, and the backup/restore drill.

---

## 8. Fingerprint reissue — restore stability

### 8.1 What the restore drill found

The drill of 2026-09-05 restored `stima360_db_test` into
`stima360_db_test_restorecheck`: `pg_restore` exit `0`, ~19.7 s, structural
counts identical on all six sections. Yet the two fingerprints differed:

```
source   669c18de5dc2414b0d59afa8b3e3ce366ec96e588e1bace93c17da4dafa028c1
restored be0e5e9afe5f6dcc38b5dda1015192fcef5f8bad03506bd5e7996afcdfaba4ba
```

The diff contained no missing object of any kind. It contained only
semantically equivalent SQL rewrites.

### 8.2 Root cause

`pg_get_constraintdef` and `pg_get_indexdef` do not return the SQL that was
written. They deparse the expression tree the server stored, and that tree's
shape depends on how the original statement was parsed and type-coerced.

`status VARCHAR(30) ... CHECK(status IN ('draft','submitted'))` is stored as a
comparison against an array, with one coercion applied to the array as a whole:

```
((status)::text = ANY ((ARRAY['draft'::character varying, 'submitted'::character varying])::text[]))
```

A dump writes that text out; a restore re-parses it. The array cast is now
explicit in the source text, so the rebuilt tree carries the coercion on each
element instead:

```
((status)::text = ANY (ARRAY[('draft'::character varying)::text, ('submitted'::character varying)::text]))
```

Same predicate, different bytes. The defect was therefore **in the fingerprint,
not in the restore**: `p26-snapshot-1` hashed a deparse artefact rather than the
schema's meaning, so it could never have verified a restore. A baseline
fingerprint that changes under a faithful restore is worse than no fingerprint,
because it reports a false difference and trains the operator to ignore it.

### 8.3 What was changed

`scripts/p26_sql_canonical.py` — a new module that parses an expression into a
structural tree and rewrites that tree with meaning-preserving rules, then
re-serialises it deterministically. It performs **no** textual substitution on
SQL; fragile `replace`-style normalisation is exactly what could silently erase
a real difference.

The rules, each reversible in meaning:

| Rule | Effect | Why it is safe |
|---|---|---|
| R1 | A parenthesised group holding a single node loses its parentheses | `(status)::text` and `status::text` are the same expression. Multi-node groups keep their parentheses, so precedence is never changed. |
| R2 | A cast applied to an array constructor is distributed over its elements | Casting an array to `T[]` *is* casting each element to `T`. This is the rule that reconciles the two deparse forms. |
| R3 | A cast of a cast of a **literal** collapses to the outer type | Only when the inner type has no length modifier and is `text` or `varchar`, which cannot change the value. `bpchar` is excluded because it pads; a length modifier is excluded because it shortens. |
| R4 | Casting twice to the identical type collapses to one | A no-op by definition. |

Type-name spellings are folded to one canonical form (`character varying` and
`varchar`; `integer` and `int4`), because two schemas differing only in
spelling are the same schema.

The snapshot script now keeps the raw deparsed text in the artefact for audit
and adds a `<field>_canonical` sibling; the digest is taken over a projection
that carries **only** the canonical form. The projection is re-sorted after the
raw text is removed — without that, two equivalent schemas could present the
same rows in a different order and still disagree.

Canonicalisation is applied to `constraints.definition`, `indexes.indexdef`,
`columns.column_default`, and `policies.qual` / `policies.with_check`.

`SNAPSHOT_FORMAT_VERSION` is raised to `p26-snapshot-2` and is itself part of
the fingerprint input, so a digest from the old algorithm can never be mistaken
for one from the new.

**Migration `026` was not modified.** It stores a 64-character hexadecimal
digest and an artefact path; it has no coupling to how the digest is derived.

### 8.4 Evidence

`tests/test_p26_fingerprint_restore_stability.py`, 30 tests, all from the real
drill diff rather than invented examples:

- the three observed shapes — `CHECK` with `ANY (ARRAY…)`, nullable `CHECK`
  with `ANY (ARRAY…)`, and a partial unique index predicate — each reach an
  identical canonical form in both source and restored spelling;
- a guard asserting the two raw fixtures really do differ, so the equivalence
  tests cannot pass vacuously;
- nine pairs of genuinely different definitions — changed literal, added
  allowed value, different column, removed nullability branch, different cast
  target, different index columns, unique versus non-unique — that must and do
  still produce different canonical forms;
- explicit tests that a length modifier and `bpchar` are never collapsed;
- the whole real artefact: every affected definition in the TEST schema (68
  constraints, 3 indexes) rewritten into the restored form, with the canonical
  digest unchanged;
- a test that reproduces the original defect, so the fix cannot be quietly
  reverted.

The read-only scan in `tests/test_p26_baseline_isolation.py` (H8) was extended
to cover the new module, so the canonicaliser is held to the same no-write
obligation as the snapshot script.

### 8.5 Reissue closed — RESTORE EQUALITY PASS

Executed by the operator on 2026-09-05 and verified independently against the
artefacts on disk.

| Artefact | Database | Format | Digest |
|---|---|---|---|
| `p26_baseline_TEST_20260905T174537Z.json` | `stima360_db_test` | `p26-snapshot-2` | `84a44a9f…f57d` |
| `p26_baseline_TEST_20260905T174551Z.json` | `stima360_db_test_restorecheck` | `p26-snapshot-2` | `84a44a9f…f57d` |
| `p26_baseline_TEST_20260905T174620Z.json` | `stima360_db_test` | `p26-snapshot-2` | `84a44a9f…f57d` |

**Source and restore digests are identical.** Structural counts identical on all
six sections in both.

Four independent confirmations, each checked rather than accepted:

1. Every digest was **recomputed from its artefact** and matches the declared
   value; every `.sha256` sidecar agrees.
2. The digest matches the value **predicted offline** before the run, from the
   old raw artefact. A mismatch would have meant the TEST schema had moved; it
   had not.
3. The two source runs, taken at different times, produced **byte-identical**
   payloads — the digest is reproducible, not incidental.
4. The raw `constraints` text of the two databases **genuinely differs**, so the
   dump/restore rewrite really did occur and the equal digests are the
   canonicalisation working, not two identical inputs.

Point 4 is the one that matters. Without it, equal digests would prove nothing.

The fingerprint is **PASS**.

---

## 9. Migration 026 — apply record

**Applied to `stima360_db_test` on 2026-09-05 by `giorgio.larasa`. PASS.**

Command executed:

```
./.venv/bin/python scripts/p26_migrate.py apply \
    --operator "giorgio.larasa" \
    --baseline-fingerprint 84a44a9fdca44d9b4a8842686919eede3cbec052674241a096d2855c70e5f57d \
    --baseline-artifact reports/p26_baseline_TEST_20260905T174620Z.json
```

### 9.1 Resulting `schema_baseline` — one row

| Column | Value |
|---|---|
| `baseline_version` | `P26-BASELINE-001` |
| `schema_fingerprint` | `84a44a9fdca44d9b4a8842686919eede3cbec052674241a096d2855c70e5f57d` |
| `snapshot_artifact` | `reports/p26_baseline_TEST_20260905T174620Z.json` |
| `certified_by_operator` | `giorgio.larasa` |
| `database_name` | `stima360_db_test` |
| `pre_baseline_tracked` | `false` |

### 9.2 Resulting `schema_migrations` — zero rows

**This is correct, not a failure.** `026` installs the ledger, so it cannot
register itself inside it; its record is the `schema_baseline` row above, and
that row is also what makes a re-run a no-op. The first row in
`schema_migrations` will be written by `027`.

An empty ledger alongside a populated baseline is therefore the expected
post-026 state. Anyone reading it as "the migration did not register" has the
relationship backwards.

### 9.3 Runner status after the apply

```
ledger present  : True
baseline present: True
discovered      : 1 migration(s) >= 026
  applied      026_p26_baseline
```

`026` reports as applied because the baseline row exists, which is the same
mechanism that makes the apply idempotent.

### 9.4 Checksums at the time of apply

| File | SHA256 |
|---|---|
| `migrations/026_p26_baseline.sql` | `7da4ff8fbc37208576759e2b64101ec59d5efea2332a8e455064d5148327c181` |
| `migrations/026_p26_baseline_down.sql` | `76a703aace097f6bd0dd971410e9ff0fe121ecebaa7507f2f5b7b4770e2d61e4` |

Recorded here because `026` is the one migration whose checksum the ledger does
not hold — it is not a ledger row. From `027` onward the runner stores
`checksum_up` and refuses a file that changed after it was applied. For `026`
this table is the equivalent evidence, and it is why the file must not be
edited: an edit would be undetectable to the runner.

### 9.5 What is **not** claimed

- **PROD is untouched.** No P26-0 step contacted `stima360_db`. The PROD
  baseline is a separate exercise and nothing here transfers to it. The PROD
  fingerprint will legitimately differ, because `010`/`011` and `014`/`015` are
  mutually exclusive per environment (section 3.1).
- Migrations `001`–`025` remain untracked, permanently (section 4).
- The 30 legacy columns in section 3.0.2 are still absent, and `database.py`
  must still not be run as a script against TEST.

---

## P26-0 — COMPLETE (TEST)

Baseline certified, fingerprint restore-stable, backup and restore drill passed,
migration ledger installed, `026` applied and verified. The forward-only ledger
is live on TEST and starts at `027`.
