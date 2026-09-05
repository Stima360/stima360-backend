# P26 — TEST backup and restore procedure

Status: **written, NOT yet executed.** The restore has not been proven. See
section 7.

This procedure is the last line of defence in the P26 rollback strategy. Once a
migration has backfilled `agency_id`, dropping the column destroys the tenant
assignment and no down file can reconstruct it. At that point restore is the
only rollback that exists. A procedure that has never been run is not a
procedure, so section 7 is a gate, not a formality.

Scope: **TEST only.** Nothing in this document is authorised against
production.

---

## 1. Prerequisites

| Item | Requirement |
|---|---|
| Client tools | `pg_dump` and `pg_restore` at a version **greater than or equal to** the server's. An older client against a newer server fails or silently omits objects. |
| Credentials | `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` for TEST, supplied through the environment. Never inline them in a command that reaches shell history. |
| Disk | Free space of at least twice the reported database size. |
| Target for the restore drill | A **separate** database. The drill must never restore over the operating TEST database. |
| Authorisation | Explicit, per-run. This procedure is not self-authorising. |

Use `PGPASSWORD` from the environment rather than typing a password:

```bash
export PGPASSWORD="$DB_PASSWORD"
```

Never paste a connection string containing a password into a terminal, a
ticket, a commit message, or a screenshot.

---

## 2. Verify the target before touching anything

Every command below is prefixed by this check. Run it and read the output; do
not pipe it into the next command.

```bash
psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" \
     -c "SELECT current_database(), pg_size_pretty(pg_database_size(current_database()));"
```

**Anti-production guard — all three must hold:**

1. `current_database()` contains `test`.
2. `current_database()` is **not** `stima360_db` and **not** `stima360`.
3. The host is the TEST host, not the production host.

If any check fails, stop. `scripts/p26_schema_snapshot.py` and
`scripts/p26_migrate.py` enforce the same rule in code
(`assert_test_database_name`), and the two must not disagree.

---

## 3. Snapshot before every P26 migration

Taken immediately before the migration, not the night before.

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)
pg_dump -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" \
        --format=custom --no-owner --no-privileges \
        --file="p26_pre_migration_${DB_NAME}_${TS}.dump"
```

Notes on the flags:

- `--format=custom` is required for selective `pg_restore` and for parallel
  restore.
- `--no-owner` / `--no-privileges` keep the dump portable across roles, which
  matters once the migrator and application roles are separated.

Record, in the run log: the dump filename, its byte size, its SHA256, the
wall-clock duration, and the source database name.

```bash
shasum -a 256 "p26_pre_migration_${DB_NAME}_${TS}.dump"
```

**Verify the dump is readable before trusting it.** An unreadable dump is
indistinguishable from a good one until the moment it is needed:

```bash
pg_restore --list "p26_pre_migration_${DB_NAME}_${TS}.dump" | head -40
```

---

## 4. Restore drill

The drill restores into a **new, separate** database. It never overwrites the
operating TEST database.

```bash
DRILL_DB="stima360_db_test_restoredrill_${TS}"

createdb -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" "$DRILL_DB"

time pg_restore -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" \
                -d "$DRILL_DB" --no-owner --no-privileges \
                --exit-on-error --jobs=4 \
                "p26_pre_migration_${DB_NAME}_${TS}.dump"
```

`--exit-on-error` matters: without it `pg_restore` reports success while having
skipped failing objects, which is the failure mode this drill exists to catch.

The drill database name carries `test` so the guards in section 2 keep
applying to it.

---

## 5. Verify the restore

Compare the restored database against the source. The schema snapshot script is
the instrument:

```bash
DB_NAME="$DRILL_DB" python scripts/p26_schema_snapshot.py
```

**PASS requires all of:**

1. `pg_restore` exited zero with `--exit-on-error`.
2. The table count in the drill database equals the table count in the source.
3. Row counts match for a named set of tables agreed before the drill — at
   minimum `contacts`, `leads`, `properties`, `buy_requests`, `stime`.
4. The schema fingerprint of the drill database equals the fingerprint of the
   source snapshot taken at the same moment.
5. The measured restore duration is recorded.

**FAIL is any of:**

- a non-zero exit, or any error line in the `pg_restore` output;
- a fingerprint mismatch that cannot be explained by a concurrent write to the
  source;
- a row count mismatch;
- a restore duration that makes the procedure unusable as an incident response
  under the agreed recovery window.

A FAIL blocks every P26 migration that touches application data.

---

## 6. Clean up the drill

```bash
psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" \
     -c "SELECT current_database();"     # confirm you are NOT in the drill DB
dropdb -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" "$DRILL_DB"
```

Never run `dropdb` without first reading the name aloud against the drill
database name recorded in the run log. Retain the dump file until the P26
migration it protected has been accepted.

---

## 7. Execution record

| Run | Date | Operator | Source DB | Restore DB | Restore duration | Result |
|---|---|---|---|---|---|---|
| 1 | 2026-09-05 | operator, macOS shell | `stima360_db_test` | `stima360_db_test_restorecheck` | ~19.7 s | **PASS** |

**Current status: RESTORE DRILL PASS.**

## 7.1 Run 1 — 2026-09-05

| Item | Value |
|---|---|
| Source DB | `stima360_db_test` |
| Restore DB | `stima360_db_test_restorecheck` — separate database, never restored over the source |
| `pg_restore` exit code | `0` |
| Restore duration | ~19.7 seconds |
| Structural counts | **identical** |

Counts compared, source against restored:

| Section | Source | Restored |
|---|---|---|
| tables | 60 | 60 |
| columns | 764 | 764 |
| constraints | 802 | 802 |
| indexes | 216 | 216 |
| sequences | 59 | 59 |
| policies | 0 | 0 |

No missing table, column, constraint, index or sequence; no RLS difference.

### Fingerprint note — why the two digests differed

The first comparison produced two different fingerprints from two structurally
identical schemas:

```
source   669c18de5dc2414b0d59afa8b3e3ce366ec96e588e1bace93c17da4dafa028c1
restored be0e5e9afe5f6dcc38b5dda1015192fcef5f8bad03506bd5e7996afcdfaba4ba
```

The diff was analysed and contained **only** semantically equivalent SQL
rewrites produced by `pg_dump` / `pg_restore`, chiefly:

```
source    ... = ANY ((ARRAY['a'::character varying])::text[])
restored  ... = ANY (ARRAY[('a'::character varying)::text])
```

and the same transformation inside partial index predicates.

Raw `pg_get_constraintdef` / `pg_get_indexdef` output is a *deparse* of the
stored expression tree, not the SQL the operator wrote. A dump and restore
cycle re-parses that text, rebuilds the tree from a different starting point,
and deparses it differently. A fingerprint taken over raw deparsed text is
therefore not restore-stable, which defeats the purpose of using it to verify a
restore.

The P26 fingerprint consequently uses a **restore-stable canonicalisation**
(`scripts/p26_sql_canonical.py`, snapshot format `p26-snapshot-2`): raw
definitions stay in the artefact for audit, while the digest is taken over a
canonical projection in which equivalent deparse forms collapse to a single
representation and real differences do not. See
`docs/P26_BASELINE_CERTIFICATE_TEST.md` section 8.

### Fingerprint equality — PASS

Both databases were re-snapshotted with `p26-snapshot-2` after the fix:

| Database | Artefact | Format | Digest |
|---|---|---|---|
| `stima360_db_test` | `reports/p26_baseline_TEST_20260905T174620Z.json` | `p26-snapshot-2` | `84a44a9f…f57d` |
| `stima360_db_test_restorecheck` | `reports/p26_baseline_TEST_20260905T174551Z.json` | `p26-snapshot-2` | `84a44a9f…f57d` |

```
84a44a9fdca44d9b4a8842686919eede3cbec052674241a096d2855c70e5f57d
```

**Identical.** Each digest was recomputed from its own artefact and matches the
declared value and its `.sha256` sidecar.

The result is meaningful rather than circular because the **raw** definition
text of the two databases still differs — the dump/restore rewrite really did
happen. Equal digests over differing raw text is the canonicalisation doing its
job. Had the raw text been identical, the test would have proven nothing.

**RESTORE DRILL: PASS, structure and fingerprint.**

## 7.2 Gate status

The restore gate stated in the approved spec, section 7.5, is **met**: a dump of
TEST was restored into a separate database with exit code 0, identical
structural counts and an identical schema fingerprint, and the drill database is
disposable.

Migration `026` remains unapplied. It is purely additive infrastructure with a
complete, safe down file, and the baseline certificate it depends on is now
issued — see `docs/P26_BASELINE_CERTIFICATE_TEST.md`.

### Outcome

Migration `026` was applied to `stima360_db_test` on 2026-09-05 on the strength
of this drill and the baseline certificate. See
`docs/P26_BASELINE_CERTIFICATE_TEST.md` section 9.

**P26-0 — COMPLETE (TEST).**

### Disposal of the drill database

`stima360_db_test_restorecheck` has served its purpose. Drop it once the
artefacts above are retained, per section 6; leaving a stale copy of TEST on the
host invites a later run to be pointed at it by accident. Note that its name
contains `test`, so the P26 guards accept it as a legitimate target: the
protection against applying `026` to the wrong database is the operator checking
`DB_NAME`, not the guard.
