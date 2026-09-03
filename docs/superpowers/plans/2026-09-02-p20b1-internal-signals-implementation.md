# P20-B1 Internal Signals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved P20-B1 internal Property Watch collectors for canonical microzone price changes and aggregate internal PROPERTY supply changes, preserving append-only history, idempotency, collector-level failure isolation, and the P20/P21 boundary.

**Architecture:** Reuse the existing `property_watches` and `property_watch_observations` persistence model. Add internal read/collector functions around `zone_valori`, aggregate PROPERTY supply and existing observation history; derive current state from observations only; expose protected manual refresh endpoints without GET side effects or schedulers.

**Tech Stack:** Python, FastAPI, PostgreSQL, psycopg2, Pydantic, pytest.

**Spec:** `docs/superpowers/specs/2026-09-02-p20b1-internal-signals-design.md`

## Global constraints

- Implement only the approved P20-B1 collectors. Do not add P21 behavior,
  external data, market formulas, buyer pressure, demand scores, MATCH or
  ranking changes, NBA, tasks, messages, follow-up, Vendita Invisibile,
  schedulers, or UI automation.
- Reuse `property_watches` and append-only
  `property_watch_observations`; do not add a current-state table and do not
  update a watch to persist derived state.
- The sole current EUR/sqm authority is
  `zone_valori.prezzo_mq_base`, matched by the exact, case-sensitive
  `(comune, microzona)` pair from `watch_started.payload`. Do not use
  `valuation.BASE_MQ` or `valuation_base.BASE_MQ`, case folding, or alternate
  locality normalization.
- The original price comparator is
  `watch_started.payload.prezzo_mq_base`. All price arithmetic and
  idempotency-value formatting use `Decimal`; payload serialization uses the
  module's JSON serializer with `allow_nan=False`.
- The supply query is `COUNT(*)` over only `properties` rows whose exact
  `city` and `microzone` equal the baseline locality, whose
  `commercial_status` is one of `mandate`, `active`, `reserved`,
  `under_offer`, and whose `archived_at IS NULL`. Do not filter the free-text
  `properties.source` field, join to other domains, or disclose listing data.
- A supply count of zero is valid source data and creates the first
  `internal_supply_snapshot` with `current_count: 0`; an empty PROPERTY result
  is never `source_unavailable`.
- The only new production files are
  `property_watch/repository.py`, `property_watch/service.py`,
  `property_watch/router.py`, and `property_watch/schemas.py`. Do not modify
  `main.py`, migrations, or any other production module. The existing router
  inclusion in `main.py` already supplies `require_admin`.
- Add focused cases to `tests/test_p20b1_internal_signals.py`; retain
  `tests/test_p20_property_watch.py` as the P20-A regression suite. No
  production, migration, or test edits are made by this planning task.
- A GET is read-only. Only the two protected POST refresh routes may request
  collection, and the browser supplies no locality, source, status list,
  payload, idempotency key, or actor value.
- Preserve the existing global unique
  `idx_property_watch_observations_idempotency_key`. Use predecessor-aware
  deterministic keys:

  ```text
  property_watch:microzone_price_changed:watch:{watch_id}:after:{prior_observation_id}:current:{canonical_decimal}:v1
  property_watch:internal_supply_snapshot:watch:{watch_id}:after:{watch_started_observation_id}:count:{current_count}:v1
  property_watch:internal_supply_changed:watch:{watch_id}:after:{prior_observation_id}:count:{current_count}:v1
  ```

- `safe_collect_internal_signals_for_stima(stima_id)` must have independent
  fault boundaries for the microzone and supply collectors. A microzone error
  never skips supply; a supply error never removes or invalidates a successful
  microzone result. The active-watch batch continues after any one watch
  fails. Log only `stima_id`, collector name, and exception type; never log
  payloads, source rows, or PII.
- Maintain the approved payload contracts exactly:

  ```json
  {"previous": 1500.0, "current": 1600.0, "delta": 100.0, "delta_percent": 6.6666666667, "comune": "Alba Adriatica", "microzona": "Nord"}
  ```

  for `microzone_price_changed`, with JSON `null` for `delta_percent` when
  `previous` is zero;

  ```json
  {"current_count": 0, "comune": "Alba Adriatica", "microzona": "Nord"}
  ```

  for `internal_supply_snapshot`; and

  ```json
  {"previous_count": 4, "current_count": 6, "delta": 2, "comune": "Alba Adriatica", "microzona": "Nord"}
  ```

  for `internal_supply_changed`.

## Task 1: Add repository read and transaction primitives

**Files:** modify `property_watch/repository.py`; create
`tests/test_p20b1_internal_signals.py`.

**Interfaces produced:** `get_collection_context_for_update(cur, stima_id)`,
`list_active_watch_stima_ids()`, `get_zone_value(cur, comune, microzona)`,
`count_internal_supply(cur, comune, microzona)`, and
`get_latest_relevant_observation(cur, watch_id, observation_types)`. Update
`insert_observation()` to pass `dumps=_json_dumps` to `Json`, so every future
observation supports Decimal JSON numbers.

**Interfaces consumed:** the existing `property_watch_cursor`,
`_row`, `_json_dumps`, `property_watches`, and
`property_watch_observations` contracts.

- [ ] Add failing repository tests proving the exact `zone_valori` locality
  lookup, the PROPERTY count allowlist and `archived_at IS NULL`, deterministic
  active-watch ordering, relevant-observation ordering, and Decimal-safe
  serialization in `insert_observation()`.
- [ ] Run `python -m pytest -q tests/test_p20b1_internal_signals.py` and
  confirm RED because the named primitives and Decimal adapter behavior do not
  yet exist.
- [ ] Implement the minimum repository reads. The cursor-taking collection
  context helper must lock the target watch `FOR UPDATE` and return its
  `watch_started` observation; active-watch discovery must select only
  `status = 'active'` and non-null `stima_id`, ordered by watch ID. The
  cursor-taking zone helper must use exact equality and return no value for no
  row. The cursor-taking supply helper must execute the approved aggregate
  query and return integer zero from a successful count.
- [ ] Run `python -m pytest -q tests/test_p20b1_internal_signals.py` and
  confirm GREEN for the Task 1 cases.
- [ ] Run `python -m pytest -q tests/test_p20_property_watch.py` and confirm
  the existing P20-A suite remains GREEN.
- [ ] Run `git diff --check`, then selectively stage
  `property_watch/repository.py tests/test_p20b1_internal_signals.py` and make
  one logical repository-primitives commit.

## Task 2: Implement the microzone price collector

**Files:** modify `property_watch/repository.py`,
`property_watch/service.py`, and `tests/test_p20b1_internal_signals.py`.

**Interfaces produced:** `collect_microzone_market_signal_for_stima(stima_id)`
in service and an atomic repository helper
`collect_microzone_price_change(watch_id, baseline_payload)`.

**Interfaces consumed:** Task 1 cursor-taking collection context, source, and
predecessor interfaces; `record_observation()` conventions;
`watch_started.payload.prezzo_mq_base`; and the existing observation unique
index.

- [ ] Add failing tests for first comparison equality (no write), first
  difference (one `microzone_price_changed`), later equality (no duplicate),
  later change (uses prior event `current`), missing/non-finite baseline,
  missing locality, absent `zone_valori` row, Decimal arithmetic, zero prior
  percent, JSON-safe Decimal payloads, retry deduplication, and a
  return-to-prior price that receives a new predecessor-based key.
- [ ] Run `python -m pytest -q tests/test_p20b1_internal_signals.py -k microzone`
  and confirm RED.
- [ ] Implement the collector using a single
  `property_watch_cursor(commit=True)` transaction that locks the target watch
  `FOR UPDATE`, obtains the latest `microzone_price_changed` or
  `watch_started` predecessor, reads the exact `zone_valori` value, compares
  Decimals, and inserts only a change. Return explicit
  `written`, `unchanged`, `baseline_unavailable`, or `source_unavailable`
  outcome data without PII.
- [ ] Use `watch_started.payload.prezzo_mq_base` for the first predecessor and
  `microzone_price_changed.payload.current` thereafter. Build the exact
  predecessor-aware microzone key, use `ON CONFLICT` plus reread for retries,
  and serialize the payload through `_json_dumps`.
- [ ] Run `python -m pytest -q tests/test_p20b1_internal_signals.py -k microzone`
  and confirm GREEN.
- [ ] Run `python -m pytest -q tests/test_p20_property_watch.py` and confirm
  the existing P20-A suite remains GREEN.
- [ ] Run `git diff --check`, then selectively stage
  `property_watch/repository.py property_watch/service.py tests/test_p20b1_internal_signals.py`
  and make one logical microzone-collector commit.

## Task 3: Implement the aggregate internal supply collector

**Files:** modify `property_watch/repository.py`,
`property_watch/service.py`, and `tests/test_p20b1_internal_signals.py`.

**Interfaces produced:** `collect_internal_supply_signal_for_stima(stima_id)`
in service and an atomic repository helper
`collect_internal_supply_change(watch_id, baseline_payload)`.

**Interfaces consumed:** the Task 1 aggregate count primitive, the Task 2
predecessor/transaction pattern, and the append-only observation contract.

- [ ] Add failing tests for all nine PROPERTY statuses, non-null
  `archived_at`, exact city/microzone matching, no joins or listing data in
  payloads, first snapshot at a positive count and at zero, no write for an
  unchanged count, a changed count payload, retry deduplication, and a
  return-to-prior count with a new predecessor-based key.
- [ ] Run `python -m pytest -q tests/test_p20b1_internal_signals.py -k supply`
  and confirm RED.
- [ ] Implement an independent transaction that locks the watch `FOR UPDATE`,
  counts exactly `mandate`, `active`, `reserved`, and `under_offer` with
  `archived_at IS NULL`, and reads the latest
  `internal_supply_changed` or `internal_supply_snapshot` predecessor.
- [ ] Insert `internal_supply_snapshot` on the first successful count,
  including zero. Insert `internal_supply_changed` only when the count changes.
  Use the exact snapshot/change idempotency keys, return the existing
  observation on a key collision, and never classify an empty count as source
  unavailability.
- [ ] Run `python -m pytest -q tests/test_p20b1_internal_signals.py -k supply`
  and confirm GREEN.
- [ ] Run `python -m pytest -q tests/test_p20_property_watch.py` and confirm
  the existing P20-A suite remains GREEN.
- [ ] Run `git diff --check`, then selectively stage
  `property_watch/repository.py property_watch/service.py tests/test_p20b1_internal_signals.py`
  and make one logical supply-collector commit.

## Task 4: Add independent orchestration and active-watch batching

**Files:** modify `property_watch/service.py` and
`tests/test_p20b1_internal_signals.py`.

**Interfaces produced:** `collect_internal_signals_for_stima(stima_id)`,
`safe_collect_internal_signals_for_stima(stima_id)`, and
`collect_internal_signals_for_active_watches()`.

**Interfaces consumed:** both strict collectors from Tasks 2 and 3 and
`repository.list_active_watch_stima_ids()`.

- [ ] Add failing tests proving that orchestration returns a separately named
  microzone and supply outcome; a microzone exception still invokes supply; a
  supply exception preserves the microzone outcome; one failed watch does not
  stop later watch IDs; logged failures include only identifier, collector
  name, and exception type; and batch totals classify outcomes consistently.
- [ ] Run `python -m pytest -q tests/test_p20b1_internal_signals.py -k 'orchestration or batch or isolation'`
  and confirm RED.
- [ ] Implement the strict orchestrator as a direct two-collector call and
  the safe wrapper as two independent `try` boundaries, one per collector.
  Each boundary returns its own `failed` result on an unexpected exception.
  Do not put both calls under one outer catch.
- [ ] Implement the batch as an ascending active-watch iteration that calls
  the safe wrapper per watch and returns per-watch outcomes plus processed,
  written, unchanged, unavailable, and failed totals. Add no scheduler.
- [ ] Run `python -m pytest -q tests/test_p20b1_internal_signals.py -k 'orchestration or batch or isolation'`
  and confirm GREEN.
- [ ] Run `python -m pytest -q tests/test_p20_property_watch.py` and confirm
  the existing P20-A suite remains GREEN.
- [ ] Run `git diff --check`, then selectively stage
  `property_watch/service.py tests/test_p20b1_internal_signals.py` and make
  one logical orchestration-isolation commit.

## Task 5: Extend pure current-state derivation and schemas

**Files:** modify `property_watch/service.py`, `property_watch/schemas.py`,
and `tests/test_p20b1_internal_signals.py`.

**Interfaces produced:** optional `microzone_reference` and
`internal_supply` fields on `PropertyWatchState`, plus matching pure
derivation in `get_current_watch_state(stima_id)`.

**Interfaces consumed:** ordered `list_observations()` history and all three
new observation payload types.

- [ ] Add failing tests for a baseline-only state, first microzone difference,
  latest microzone value after multiple changes, first supply snapshot
  including zero, latest supply change, relevant history/count/times, and no
  reads or writes beyond the existing state read path.
- [ ] Run `python -m pytest -q tests/test_p20b1_internal_signals.py -k state`
  and confirm RED.
- [ ] Derive `microzone_reference` from the original
  `prezzo_mq_base` and latest `microzone_price_changed.current`, falling back
  to the original when no change exists. Derive `internal_supply` from the
  latest supply snapshot/change and leave it absent until the first successful
  snapshot. Preserve `baseline`, full observations, total count, and
  `computed_at`; add no persisted state, score, trend, or band.
- [ ] Run `python -m pytest -q tests/test_p20b1_internal_signals.py -k state`
  and confirm GREEN.
- [ ] Run `python -m pytest -q tests/test_p20_property_watch.py` and confirm
  the existing P20-A suite remains GREEN.
- [ ] Run `git diff --check`, then selectively stage
  `property_watch/service.py property_watch/schemas.py tests/test_p20b1_internal_signals.py`
  and make one logical state-derivation commit.

## Task 6: Expose protected manual refresh routes

**Files:** modify `property_watch/router.py`,
`property_watch/schemas.py`, and `tests/test_p20b1_internal_signals.py`.

**Interfaces produced:** `POST /api/property-watch/stime/{stima_id}/internal-signals/refresh`
and `POST /api/property-watch/internal-signals/refresh-active`, with response
schemas for a per-collector single-watch outcome and aggregate batch outcome.

**Interfaces consumed:** `safe_collect_internal_signals_for_stima(stima_id)`,
`collect_internal_signals_for_active_watches()`, existing Property Watch
exceptions, and inherited `require_admin`.

- [ ] Add failing API tests for both exact paths, inherited OpenAPI security,
  single-watch outcomes, batch summaries, validation/not-found mapping where
  applicable, no client-controlled collector inputs, and confirmation that the
  existing GET state endpoint does not call a collector or write an
  observation.
- [ ] Run `python -m pytest -q tests/test_p20b1_internal_signals.py -k 'route or endpoint or admin or get'`
  and confirm RED.
- [ ] Add only the two POST handlers. Route the single refresh to the safe
  wrapper and batch refresh to the batch collector. Keep GET as a direct call
  to `get_current_watch_state()` and retain the router's existing error style.
  Do not modify `main.py`, because its router inclusion already applies admin
  security.
- [ ] Run `python -m pytest -q tests/test_p20b1_internal_signals.py -k 'route or endpoint or admin or get'`
  and confirm GREEN.
- [ ] Run `python -m pytest -q tests/test_p20_property_watch.py` and confirm
  the existing P20-A suite remains GREEN.
- [ ] Run `git diff --check`, then selectively stage
  `property_watch/router.py property_watch/schemas.py tests/test_p20b1_internal_signals.py`
  and make one logical protected-refresh-API commit.

## Task 7: Run the regression gate and review scope

**Files:** modify only `tests/test_p20b1_internal_signals.py` if an integration
gap is found; otherwise modify no file.

**Interfaces verified:** all Task 1-6 interfaces, the existing
`PropertyWatchState` response, existing P20-A initialization, and inherited
router protection.

- [ ] Add any missing focused regression assertion required to prove
  append-only writes, no PII payload/logging, no GET mutation, no empty-supply
  failure, deterministic retry behavior, and independent collector failure
  isolation.
- [ ] Run `python -m pytest -q tests/test_p20b1_internal_signals.py` and
  confirm it is RED before the missing assertion is implemented.
- [ ] Make the smallest corrective implementation only if the new regression
  exposes a Task 1-6 contract violation; keep production edits inside the four
  permitted Property Watch files.
- [ ] Run `python -m pytest -q tests/test_p20b1_internal_signals.py tests/test_p20_property_watch.py`
  and confirm GREEN.
- [ ] Run `python -m pytest -q` as the final suite gate. The approved baseline
  before P20-B1 is `1088 passed, 23 skipped`; report the actual final result
  and do not hardcode an expected final test count.
- [ ] Run `git diff --check` and `git status --short`. Selectively stage only
  any final focused-test or corrective Property Watch files, then make one
  logical regression-gate commit if a change was necessary.

## Final implementation review

Before opening implementation work for review, verify all ten points:

1. Only the four permitted production files and focused P20-B1 test file
   changed; `main.py` and migrations remain untouched.
2. `zone_valori` is the only current price source and uses exact baseline
   locality matching.
3. Microzone first comparison is against `watch_started.prezzo_mq_base`, and
   unchanged values never append an event.
4. Price arithmetic is Decimal-based, JSON-safe, and handles zero previous
   price with `delta_percent: null`.
5. Supply counts only `mandate`, `active`, `reserved`, and `under_offer` rows
   with `archived_at IS NULL`.
6. Zero qualifying PROPERTY rows append the first valid supply snapshot and
   never become `source_unavailable`.
7. All three observation types have their exact aggregate payloads and
   predecessor-aware idempotency keys; the database unique index remains the
   retry backstop.
8. Microzone and supply errors are isolated per collector; a failed watch does
   not stop the active-watch batch; logs contain no payloads or PII.
9. Current state is derived from observation history only, and every GET is
   side-effect-free.
10. Both refresh POST paths inherit admin protection, accept no client-derived
    collector data, and no scheduler, external source, buyer/MATCH, or P21
    behavior was introduced.
