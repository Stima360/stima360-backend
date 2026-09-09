# P26-6C 055 — live certification on Render TEST

Prepared for review. **Not executed in the session that wrote it.**

This is the run that turns the BLOCKED rows in the 055 report into PASS or
FAIL. Everything else about 055 has been proved statically; the guards
themselves are PL/pgSQL triggers, and nothing but a real PostgreSQL can
exercise them.

## Credentials

The connection comes from the authorized runtime only — the Render service
environment, which already carries `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`
and `DB_PASSWORD` (the same set `database.py` reads). **Do not print them, do
not echo them into a log, and do not paste them into a report.** Nothing below
expands a credential; the commands either read the environment themselves or
print only a database name.

`DATABASE_URL` is not assumed to exist. If `DB_NAME` is absent:
`CREDENTIAL_UNAVAILABLE`, and the run does not proceed.

## Step 0 — preconditions

Refuse to continue unless every check below passes. Each one exits
non-zero on its own; none of them is advisory.

### 0a — the right branch, on a detached HEAD

Render checks out a commit, not a branch, so `git branch --show-current` prints
nothing there and `git symbolic-ref HEAD` fails. The branch name lives in
`RENDER_GIT_BRANCH`; locally it does not exist and the git answer is the right
one.

```bash
BRANCH="${RENDER_GIT_BRANCH:-$(git branch --show-current)}"
test "$BRANCH" = "core-0.1-test" || { echo "BLOCKED: branch is '$BRANCH'"; exit 1; }
```

Then confirm the checkout is the commit that was actually reviewed. Set
`APPROVED_COMMIT` to the SHA from the review before running this.

`git rev-parse HEAD` is the authority and is checked every time — it is what
the runner and the tests will actually read from disk. `RENDER_GIT_COMMIT` is
checked *as well* when present, because the two disagreeing means the checkout
is not what the platform thinks it deployed.

```bash
test -n "$APPROVED_COMMIT" || { echo "BLOCKED: APPROVED_COMMIT is not set"; exit 1; }

HEAD_SHA=$(git rev-parse HEAD)
echo "HEAD            : $HEAD_SHA"
test "$HEAD_SHA" = "$APPROVED_COMMIT" || {
  echo "BLOCKED: HEAD $HEAD_SHA != approved $APPROVED_COMMIT"; exit 1; }

if [ -n "${RENDER_GIT_COMMIT:-}" ]; then
  echo "RENDER_GIT_COMMIT: $RENDER_GIT_COMMIT"
  test "$RENDER_GIT_COMMIT" = "$HEAD_SHA" || {
    echo "BLOCKED: RENDER_GIT_COMMIT $RENDER_GIT_COMMIT != HEAD $HEAD_SHA"; exit 1; }
fi

# The working tree must be clean apart from the known local AGENTS.md change.
DIRTY=$(git status --porcelain | grep -v '^ M AGENTS.md$' | grep -v '\.DS_Store' || true)
test -z "$DIRTY" || { echo "BLOCKED: unexpected working tree changes:"; echo "$DIRTY"; exit 1; }
```

### 0b — the exact database

```bash
test -n "$DB_NAME" || { echo "CREDENTIAL_UNAVAILABLE"; exit 1; }
test "$DB_NAME" = "stima360_db_test" || { echo "BLOCKED: DB_NAME is '$DB_NAME'"; exit 1; }
```

Note the difference in strictness, and that it is deliberate.
`scripts/p26_migrate.py` accepts any database name carrying the TEST marker and
refuses the known production names — correct for a runner that serves several
test databases. This certification, and the live suite it drives, write fixture
rows, so both name `stima360_db_test` exactly and refuse anything else,
including another legitimate test database.

### 0c — pending must be exactly [055]

Read from the runner's own `status`, which compares the migration files against
the live ledger. An earlier draft of this document tested a hand-written array
containing only `055`, which by construction could not notice a *different*
migration also sitting pending — it only ever asked about the one it already
expected.

The exit code is checked BEFORE the output is parsed. An earlier draft piped
`status` into `tee`, which reports the exit code of `tee` and not of the
runner — so a `status` that failed to reach the database would have produced an
empty file, an empty pending list, and a comparison that quietly proceeded.

```bash
set -o pipefail   # in case anything below is ever piped

python scripts/p26_migrate.py status --operator "giorgio.larasa" > /tmp/p26_status.txt 2>&1
STATUS_RC=$?
cat /tmp/p26_status.txt
test "$STATUS_RC" -eq 0 || { echo "BLOCKED: status exited $STATUS_RC"; exit 1; }

# The runner reports the database it actually reached; it must be the TEST one.
grep -qE '^database +: stima360_db_test$' /tmp/p26_status.txt || {
  echo "BLOCKED: status did not confirm stima360_db_test"; exit 1; }

# No PROBLEM lines.
if grep -q '^  PROBLEM' /tmp/p26_status.txt; then
  echo "BLOCKED: status reports a PROBLEM"; exit 1
fi

# Pending must be exactly [055] - not "055 is among the pending".
PENDING=$(awk '$1=="pending"{print $2}' /tmp/p26_status.txt | sort | paste -sd, -)
test "$PENDING" = "055_p26_flow_agency_immutability" || {
  echo "BLOCKED: pending is [$PENDING], expected exactly [055_p26_flow_agency_immutability]"
  exit 1; }

# And the ledger must already hold 026-054.
APPLIED=$(awk '$1=="applied"{c++} END{print c+0}' /tmp/p26_status.txt)
test "$APPLIED" -eq 29 || {
  echo "BLOCKED: $APPLIED applied migrations, expected 29 (026-054)"; exit 1; }
```

`status` opens a read-only transaction and rolls it back; it changes nothing.

## Step 1 — apply 055 through the certified runner

The runner is the only migration channel; it refuses production database names
and owns the transaction.

```bash
python scripts/p26_migrate.py apply --operator "giorgio.larasa"
APPLY_RC=$?
test "$APPLY_RC" -eq 0 || {
  echo "BLOCKED: apply exited $APPLY_RC - stopping before step 2"; exit 1; }
```

Stop immediately if it fails. Do not run step 2 against a database whose
migration state is unknown: the runner owns the transaction and rolls back on
error, so a failed apply leaves 055 unapplied — and the live proof would then
be certifying 054's behaviour while reporting on 055.

The runner reads `DB_*` from the environment; no DSN is passed on the command
line, so nothing sensitive reaches the shell history or the log.

Expected: `055_p26_flow_agency_immutability` applied, ledger 026–055 all
APPLIED, zero pending.

If 055's precondition block raises, 054's triggers are not in the state it
expects — that is a genuine FAIL, and it is one worth reading rather than
retrying.

## Step 2 — the live proof

```bash
P26_RUN_FLOW_LIVE_CERT=1 PYTHONDONTWRITEBYTECODE=1 \
  python -m pytest -q tests/test_p26_6c_flow_immutability_pg.py -v
```

`P26_RUN_FLOW_LIVE_CERT=1` is required. Database configuration alone does not
arm the module: every application host carries `DB_NAME`, so without the flag
an ordinary `pytest tests/` would open a connection and write fixture rows on
whatever database happened to be configured. With the flag absent the module
skips, which is also why a full suite on Render is safe.

The connection itself comes from the same `DB_*` variables the runner uses, so
nothing else needs to be exported and no credential appears on the command
line. (`P26_PG_DSN` overrides them if a DSN is ever more convenient.)

Expected: **29 passed, 0 skipped** — the module's current collection. Confirm
the number with

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest --collect-only -q \
  tests/test_p26_6c_flow_immutability_pg.py | tail -1
```

rather than trusting this document if the two ever disagree.

> A skip is not a pass. If pytest reports any `SKIPPED` in this module, the
> database environment did not reach it and the result for every case in it is
> **BLOCKED** — record it that way, never as green. `-v` is there so the count
> and the names are both visible.

### Safety properties of step 2

Asserted by `tests/test_p26_6c_flow_isolation.py::test_47`, not merely claimed:

- **Refuses any database but the one.** The module fixture reads
  `current_database()` and aborts unless it is exactly `stima360_db_test`. A
  substring test would accept `stima360_db_test_restore` or a staging database
  that happened to carry the word, and this module writes rows.
- **Nothing is committed.** `autocommit = False`; each test runs inside its own
  `SAVEPOINT`, rolled back in teardown; the connection is rolled back and
  closed in the module fixture's `finally`.
- **No sequence is consumed.** Every fixture row uses an explicit negative id,
  so no `nextval` runs and no `setval` is needed to repair anything.
- **Opt-in only, and twice over.** `P26_RUN_FLOW_LIVE_CERT=1` must be set AND a
  database must be configured. The flag is what stops a full suite arming it on
  a host that already has `DB_NAME`;
  `tests/test_p26_6c_flow_isolation.py::test_58` proves it by running the
  module with an unreachable DSN and no flag - reaching `psycopg2.connect`
  would raise, and every test skipping instead is the evidence. A skip is
  reported as BLOCKED, never as a pass.
- **Registered as a connection site.** It opens its own `psycopg2.connect`, so
  it is listed in `ALLOWED_CONNECTION_SITES` and in `P26_DB_ENTRYPOINTS.md` —
  the H11 guard fails otherwise, which is how it was caught.

## Manual equivalent

Only if the run must be done by hand. **Read the ordering note first.**

> The savepoint goes *after* the fixtures, not before. An earlier draft of this
> document opened `SAVEPOINT probes` before the INSERTs, so the first
> `ROLLBACK TO SAVEPOINT` after a probe deleted the fixtures along with the
> probe, and every later probe ran against an empty table — where nothing is
> refused because nothing is there. Every probe would have "passed" while
> testing nothing.

### Collision check — run first, must return zero rows

```sql
SELECT 'agencies' AS t, id FROM agencies WHERE id BETWEEN -9999 AND -9000
UNION ALL SELECT 'flow_events', id FROM flow_events WHERE id BETWEEN -9999 AND -9000
UNION ALL SELECT 'flow_executions', id FROM flow_executions WHERE id BETWEEN -9999 AND -9000
UNION ALL SELECT 'flow_suppressions', id FROM flow_suppressions WHERE id BETWEEN -9999 AND -9000
UNION ALL SELECT 'flow_action_records', id FROM flow_action_records WHERE id BETWEEN -9999 AND -9000
UNION ALL SELECT 'flow_rules', id FROM flow_rules WHERE id BETWEEN -9999 AND -9000;
```

If it returns anything, stop: the fixture ids below are already in use.

### The session

```sql
BEGIN;

-- Refuse anything that is not the TEST database, by exact name.
DO $$
BEGIN
  IF current_database() <> 'stima360_db_test' THEN
    RAISE EXCEPTION 'refusing to run against %', current_database();
  END IF;
END $$;

-- ---- fixtures, OUTSIDE and BEFORE the probe savepoint --------------------
INSERT INTO agencies (id, name, slug, status) VALUES
  (-9101,'P26 6C A','p26-6c-a','active'),
  (-9102,'P26 6C B','p26-6c-b','active');

INSERT INTO flow_rules
  (id, code, code_version, name, event_type, entity_type, is_active, priority,
   cooldown_minutes, parameters, default_parameters, allowed_parameters,
   last_simulation_status)
VALUES (-9601,'P26-6C-CERT',1,'cert','core.lead_created','lead',FALSE,'normal',0,
        '{}'::jsonb,'{}'::jsonb,'{}'::jsonb,'never_run');

INSERT INTO flow_events
  (id, agency_id, event_type, entity_type, entity_id, source_module, payload,
   deduplication_key, status)
VALUES (-9201,-9101,'core.lead_created','lead',1,'core','{}'::jsonb,'p26-6c-cert-a','received'),
       (-9202,-9102,'core.lead_created','lead',2,'core','{}'::jsonb,'p26-6c-cert-b','received'),
       (-9203,-9101,'core.lead_created','lead',3,'core','{}'::jsonb,'p26-6c-cert-orphan','received');

INSERT INTO flow_executions
  (id, agency_id, event_id, rule_id, entity_type, entity_id, execution_mode,
   status, rule_version, parameters_hash)
VALUES (-9301,-9101,-9201,-9601,'lead',1,'live','matched',1,'h'),
       (-9303,-9102,-9202,-9601,'lead',1,'live','matched',1,'h');

INSERT INTO flow_executions
  (id, agency_id, event_id, rule_id, entity_type, entity_id, execution_mode,
   status, rule_version, parameters_hash, retry_of_execution_id)
VALUES (-9302,-9101,-9201,-9601,'lead',1,'live','matched',1,'h',-9301);

INSERT INTO flow_executions
  (id, agency_id, rule_id, entity_type, entity_id, execution_mode, status,
   rule_version, parameters_hash)
VALUES (-9304,-9101,-9601,'lead',9,'live','matched',1,'h');   -- no children

INSERT INTO flow_suppressions (id, agency_id, rule_id, entity_type, entity_id, reason)
VALUES (-9401,-9101,-9601,'lead',1,'cert');

INSERT INTO flow_action_records
  (id, execution_id, action_type, target_module, idempotency_key, payload, status)
VALUES (-9501,-9301,'create_core_task','core','p26-6c-cert','{}'::jsonb,'pending');

-- ---- probes --------------------------------------------------------------
-- Run ONE probe, then `ROLLBACK TO SAVEPOINT probe;` before the next. The
-- savepoint is created here, after the fixtures, so rolling back to it undoes
-- only the probe.
SAVEPOINT probe;

-- Each of 1-8 must RAISE with SQLSTATE P0001 and the quoted message.
-- 1  parent event agency change        -> flow_events.agency_id is immutable
UPDATE flow_events SET agency_id = -9102 WHERE id = -9201;
-- 2  parent execution of a retry       -> flow_executions.agency_id is immutable
UPDATE flow_executions SET agency_id = -9102 WHERE id = -9301;
-- 3  childless event                   -> flow_events.agency_id is immutable
UPDATE flow_events SET agency_id = -9102 WHERE id = -9203;
-- 4  childless execution               -> flow_executions.agency_id is immutable
UPDATE flow_executions SET agency_id = -9102 WHERE id = -9304;
-- 5  suppression                       -> flow_suppressions.agency_id is immutable
UPDATE flow_suppressions SET agency_id = -9102 WHERE id = -9401;
-- 6  cross-agency execution INSERT     -> does not match event
INSERT INTO flow_executions
  (id, agency_id, event_id, rule_id, entity_type, entity_id, execution_mode,
   status, rule_version, parameters_hash)
VALUES (-9310,-9102,-9201,-9601,'lead',1,'live','matched',1,'h');
-- 7  repoint the event link            -> does not match event
UPDATE flow_executions SET event_id = -9202 WHERE id = -9301;
-- 8  repoint the retry link            -> does not match retried execution
UPDATE flow_executions SET retry_of_execution_id = -9303 WHERE id = -9302;
-- 9  action record across agencies     -> would move record
UPDATE flow_action_records SET execution_id = -9303 WHERE id = -9501;
-- 10 NULL agency on INSERT             -> NOT NULL or P0001
INSERT INTO flow_suppressions (id, agency_id, rule_id, entity_type, entity_id, reason)
VALUES (-9410, NULL, -9601, 'lead', 5, 'null-probe');

-- These must SUCCEED. Roll back to the savepoint after each, as above.
-- 11 same-agency rewrite
UPDATE flow_events SET agency_id = -9101, status = 'processed' WHERE id = -9201;
-- 12 the runtime's own execution update
UPDATE flow_executions SET status='executed', completed_at=NOW() WHERE id = -9301;
-- 13 in-agency action-record repoint (the recovery path)
UPDATE flow_action_records SET execution_id = -9302 WHERE id = -9501;
-- 14 ON DELETE SET NULL: the child keeps its agency, the link is blanked
DELETE FROM flow_action_records WHERE execution_id = -9301;
DELETE FROM flow_executions WHERE id = -9302;
DELETE FROM flow_events WHERE id = -9201;
SELECT event_id, agency_id FROM flow_executions WHERE id = -9301;
--   expect: event_id NULL, agency_id -9101
-- 15 ON DELETE CASCADE: the action records go with the execution
--   (re-run from a clean savepoint)
DELETE FROM flow_executions WHERE id = -9302;
DELETE FROM flow_executions WHERE id = -9301;
SELECT count(*) FROM flow_action_records WHERE id = -9501;   -- expect 0

ROLLBACK;   -- unconditional: undoes the probes AND the fixtures
```

`ROLLBACK` at the end is what guarantees nothing persists — it runs whether the
probes raised or not, and it discards the fixtures too, because they were
created inside the same transaction.

### Confirm nothing persisted

```sql
SELECT
  (SELECT count(*) FROM agencies            WHERE id BETWEEN -9999 AND -9000) AS agencies,
  (SELECT count(*) FROM flow_events         WHERE id BETWEEN -9999 AND -9000) AS events,
  (SELECT count(*) FROM flow_executions     WHERE id BETWEEN -9999 AND -9000) AS executions,
  (SELECT count(*) FROM flow_suppressions   WHERE id BETWEEN -9999 AND -9000) AS suppressions,
  (SELECT count(*) FROM flow_action_records WHERE id BETWEEN -9999 AND -9000) AS action_records,
  (SELECT count(*) FROM flow_rules          WHERE id BETWEEN -9999 AND -9000) AS rules;
```

All six must be `0`.

## What a FAIL means

| Probe | If it behaves other than stated |
|-------|---------------------------------|
| 1 | 055 did not apply, or the event guard was replaced. This is the original defect. |
| 2, 4 | The execution guard is missing; retries and action records can be moved between tenants. |
| 3 | The guard only fires when a child exists — immutability is a property of the row. |
| 5 | The suppression guard is missing. |
| 6, 7, 8 | 054's link checks were lost when 055 replaced the function bodies. |
| 9 | The action-record guard is missing; a re-point can cross agencies. |
| 10 | Neither the NOT NULL nor the trigger is refusing a NULL tenant. |
| 11, 12, 13 | These must **succeed**. A failure means the guard fires on any UPDATE, which would break live FLOW execution and the recovery path. |
| 14, 15 | These must **succeed**. A failure means the guard broke foreign-key cascade semantics. |
