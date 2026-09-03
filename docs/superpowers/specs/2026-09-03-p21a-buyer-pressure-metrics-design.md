# P21-A Buyer Pressure Metrics

## 1. Goal and approved boundary

P21-A measures real internal BUY demand for each active Property Watch. It
reuses the existing MATCH algorithm as a pure calculator against the immutable
`watch_started` valuation baseline and persists only anonymous aggregate
metrics in the existing append-only Property Watch history.

P21-A exposes these raw, auditable metrics:

- `evaluated_buyers`
- `compatible_buyers`
- `highly_compatible_buyers`
- `recent_compatible_buyers_30d`
- `average_match_score`
- `maximum_match_score`
- `average_budget`
- `algorithm_version`

P21-A does not calculate a Buyer Pressure score, band, trend, recommendation,
seller message, or next action. Calibration to a 0-100 score and any UI or
commercial messaging belong exclusively to P21-B, after P21-A has produced
representative TEST data.

The implementation base for this design is `origin/core-0.1-test` at
`ec951b2be944b2f64d124a7d7a22c899424e0b3d`.

## 2. Existing contracts reused

The implementation reuses four established contracts without changing their
meaning:

1. `property_watches` owns one logical watch per non-null `stima_id`.
2. `property_watch_observations` is append-only and has a globally unique
   `idempotency_key`; its unrestricted `observation_type` supports the two new
   P21-A event types without a migration.
3. `match.engine.calculate(request, prop)` is a pure function. By contrast,
   `match.repository.calculate_pair()`, `calculate_for_buy()`, and
   `calculate_for_property()` create `match_runs`, upsert `matches`, and write
   requirement rows, so P21-A must never call them.
4. `match.readiness.buy_readiness()` is the canonical BUY eligibility/readiness
   rule: a request can match only when it is active, unarchived, and contains at
   least one effective MATCH criterion.

`main.py` already mounts `property_watch.router` with `Depends(require_admin)`.
The P21-A routes are added to that existing router and inherit the protection;
`main.py` is not modified.

## 3. Chosen architecture

Property Watch remains the owning domain because the target identity, baseline,
locking, append-only history, manual refresh surface, and read model are all
watch-bound. Add a dedicated pure module:

```text
property_watch/buyer_pressure.py
```

The module has no database access. It owns only:

- validation and adaptation of a `watch_started` payload into an ephemeral
  MATCH property candidate;
- filtering through `buy_readiness()`;
- direct invocation of `match.engine.calculate()`;
- deterministic aggregation and canonicalization of anonymous metrics.

The implementation touches only the following production boundaries:

| File | Responsibility |
| --- | --- |
| `property_watch/buyer_pressure.py` | Pure baseline adapter, MATCH invocation, recency rule, and aggregation. |
| `property_watch/repository.py` | Minimal BUY snapshot reads and atomic observation compare/insert under the existing watch lock. |
| `property_watch/service.py` | Strict/safe single-watch collection, batch isolation, logging, and read-model derivation. |
| `property_watch/router.py` | Two protected, body-free manual POST routes. |
| `property_watch/schemas.py` | Strict response models for outcomes, batch totals, and derived state. |

No code is added to `buy` or `match`; P21-A consumes their existing pure
contracts. The focused implementation test lives in
`tests/test_p21a_buyer_pressure_metrics.py`. If the existing router-hardening
inventory asserts exact counts, its test-only expectation must change from four
to six Property Watch operations and from 98 to 100 total operations. That is
an inventory update, not an application behavior change.

## 4. Alternatives considered

### 4.1 Extend Property Watch with a pure helper — selected

This keeps ownership, persistence, locking, routes, and current-state
derivation in one established watch domain while isolating the matching math in
a small testable module. It adds one deliberate dependency on the already pure
MATCH engine and readiness functions without altering normal MATCH behavior.

### 4.2 Create a separate Buyer Pressure domain — rejected for P21-A

A separate package would duplicate watch lookup, observation history,
idempotency, transaction handling, admin registration, and read-model
composition. It would also require another router registration or a
cross-domain coordinator. Those costs provide no independent aggregate or
lifecycle in P21-A.

### 4.3 Create temporary PROPERTY and persisted MATCH rows — rejected

This would invent an agency property for a public valuation, write normal
`match_runs` and `matches`, trigger unrelated freshness/commercial behavior,
and require cleanup. It would also mix prospective seller intelligence with
official inventory. P21-A instead passes a transient dictionary directly to
the pure MATCH engine and leaves all normal PROPERTY/MATCH tables untouched.

## 5. Ephemeral property candidate

The only property input is the earliest `watch_started` observation for the
watch. Never reread mutable personal `stime` fields or construct a PROPERTY
row. Map the persisted baseline exactly:

| MATCH property field | `watch_started.payload` source |
| --- | --- |
| `city` | `comune` |
| `microzone` | `microzona` |
| `property_type` | `tipologia` |
| `surface_sqm` | `mq` |
| `asking_price` | `price_exact` |

The candidate intentionally omits province, rooms, bedrooms, bathrooms,
features, condition, address, identifiers, contacts, and metadata. The MATCH
engine's existing missing-property-data behavior applies without substitution
or invented defaults.

A baseline is usable only when:

- `comune`, `microzona`, and `tipologia` are non-empty strings;
- `mq` and `price_exact` are finite numeric values greater than zero;
- booleans are rejected as numeric values.

The original persisted string values and exact Decimal-compatible numbers are
passed to the adapter; there is no case-folding, locality repair, guessed
province, price fallback, or use of `prezzo_mq_base`, `base_mq`, or
`eur_mq_finale` as the asking price. An unusable baseline produces
`baseline_unavailable`, retains the existing history, and writes nothing.

## 6. Privacy-minimized BUY snapshot

Read candidates from the same database through a dedicated read-only
repository function. The parent query selects only fields required by
`buy_readiness()`, `match.engine.calculate()`, the budget aggregation, and the
recency calculation. It must not select `contact_id`, `lead_id`, title, notes,
finance notes, metadata, assigned user, names, email, or phone.

The parent set is constrained in SQL to:

```sql
WHERE status = 'active'
  AND archived_at IS NULL
ORDER BY id ASC
```

For those rows, load only these whitelisted fields:

- request identity for internal assembly only: `id`;
- readiness: `status`, `archived_at`;
- MATCH and aggregate budget: `budget_min`, `budget_target`, `budget_max`,
  `budget_flexibility_percent`;
- MATCH dimensions: `surface_min`, `surface_target`, `surface_max`,
  `rooms_min`, `bedrooms_min`, `bathrooms_min`;
- recency: `created_at`, `updated_at`, and the maximum interaction
  `occurred_at`;
- location criteria: `microzone`, `municipality`, `province`, `priority`,
  `is_required`, `is_excluded`;
- typology criteria: `property_type`, `requirement_level`, `priority`;
- feature criteria: `feature_code`, `requirement_level`, `value_type`,
  `value_boolean`, `value_min`, `value_target`, `value_max`, `value_text`, and
  `weight_override`.

Child rows are read in stable `buy_request_id, id` order and assembled in
memory. The complete parent and child read occurs in one read-only,
repeatable-read transaction so one collection evaluates a coherent BUY
snapshot. No BUY row or interaction is locked or changed.

Each assembled request is passed to `buy_readiness()`. Only `can_match=True`
requests enter the MATCH calculation and `evaluated_buyers`. Requests that
are draft, paused, satisfied, closed, archived, or contain no effective MATCH
criterion are excluded. SQL filtering and readiness are both retained: SQL
minimizes reads, while readiness remains the canonical business gate.

Normal `match_exclusions` cannot apply because the watched valuation has no
PROPERTY identity. Required and excluded locations, typologies, and features
still apply through the MATCH engine and can produce hard failures.

## 7. Recency rule

For each evaluated BUY, define:

```text
last_activity_at = max(
    created_at,
    updated_at,
    latest buy_request_interactions.occurred_at
)
```

The interaction component is omitted when no interaction exists. All values
are timezone-aware and compared in UTC. Capture one `collection_time` for the
whole coherent input snapshot. A compatible request is recent when:

```text
last_activity_at >= collection_time - 30 days
```

The boundary is inclusive. Future timestamps remain recent; P21-A does not
repair source timestamps. `recent_compatible_buyers_30d` counts only requests
that are already in `compatible_buyers`.

## 8. Compatibility definitions

For each ready BUY, invoke only:

```python
match.engine.calculate(buy_request, ephemeral_property)
```

Use the returned `compatibility_status`, `hard_fail_count`, `score_total`, and
`algorithm_version`; do not reproduce the MATCH weights or scoring formula.

A request is compatible only when all are true:

- `hard_fail_count == 0`;
- `compatibility_status != "incompatible"`;
- `score_total >= 55`.

A request is highly compatible only when the same no-hard-fail conditions hold
and `score_total >= 80`. Therefore every highly compatible request is also
compatible. The 55 threshold corresponds to MATCH class `possible` or better;
80 corresponds to `strong` or `excellent`.

Any unexpected exception or malformed MATCH result fails the entire strict
collector for that watch. P21-A never writes partial aggregates after silently
dropping a malformed evaluated BUY. The safe wrapper reports the collector as
failed, while the active-watch batch proceeds to later watches.

## 9. Aggregate metric contract

The persisted current payload contains exactly the approved eight fields:

```json
{
  "evaluated_buyers": 18,
  "compatible_buyers": 13,
  "highly_compatible_buyers": 5,
  "recent_compatible_buyers_30d": 7,
  "average_match_score": 72.35,
  "maximum_match_score": 91.40,
  "average_budget": 245000.00,
  "algorithm_version": "match-0.1"
}
```

Definitions:

- `evaluated_buyers` is the number of active, unarchived, ready BUY requests
  passed to the pure MATCH engine.
- The three compatibility counts follow Sections 7 and 8.
- `average_match_score` and `maximum_match_score` use only compatible BUY
  scores.
- For `average_budget`, select one amount per compatible BUY in this order:
  `budget_target`, otherwise `budget_max`, otherwise `budget_min`. Exclude a
  BUY from the budget mean only when all three are null.
- Counts are non-negative integers.
- Scores and budgets are finite Decimal values rounded to two decimal places
  with `ROUND_HALF_UP`, then serialized as JSON numbers through the existing
  Property Watch JSON serializer.
- `algorithm_version` is imported from `match.enums.ALGORITHM_VERSION`. Every
  result must report the same version; a mismatch is a failed calculation.

If there are no compatible requests, both score fields and `average_budget`
are JSON `null`. If compatible requests exist but none has a usable budget,
only `average_budget` is null.

Zero evaluated BUY requests is a valid result, not source unavailability. The
first successful run writes a zero snapshot with all four counts at zero,
three aggregate numeric values null, and the current algorithm version.

No payload contains request identifiers, criteria, per-request scores,
locations, names, contact fields, notes, or other PII.

## 10. Canonical equality and event contracts

Add two observation types with `source: "internal"`:

- `buyer_pressure_snapshot`: the first valid aggregate for the watch;
- `buyer_pressure_changed`: a later aggregate whose canonical metrics differ
  from the latest relevant Buyer Pressure observation.

Both event types use the same eight-field current payload from Section 9.
Changed events do not embed prior payloads or per-field deltas; the append-only
history already preserves previous aggregates.

Canonical comparison performs these validations before equality:

- exact required key set;
- integer count types, explicitly excluding booleans;
- finite Decimal conversion and two-decimal normalization for numeric metrics;
- null preservation;
- exact `algorithm_version` string.

`collection_time`, database timestamps, observation IDs, dictionary order, and
JSON number presentation do not participate in metric equality. Therefore an
unchanged refresh writes nothing even though it runs at a different time.

Create a SHA-256 digest from a UTF-8 canonical JSON representation with sorted
keys, compact separators, counts as integers, nulls unchanged, and normalized
decimal strings. The digest contains no raw BUY input. Use predecessor-aware
keys:

```text
property_watch:buyer_pressure_snapshot:watch:{watch_id}:after:{watch_started_id}:metrics:{sha256}:v1
property_watch:buyer_pressure_changed:watch:{watch_id}:after:{latest_buyer_pressure_observation_id}:metrics:{sha256}:v1
```

The existing unique index on `property_watch_observations.idempotency_key` is
the final duplicate barrier. On a key conflict, reread and return the existing
observation.

## 11. Transaction and concurrency design

Collection is intentionally split so potentially expensive MATCH calculations
do not hold a watch lock:

1. Open one read-only repeatable-read transaction.
2. Read the active watch and earliest `watch_started` baseline without locking.
3. Capture the database transaction timestamp as `collection_time`.
4. Read the privacy-minimized active BUY parent/child snapshot.
5. Close the read transaction.
6. Validate/adapt the baseline and calculate all metrics in memory.
7. Open `property_watch_cursor(commit=True)`.
8. Lock the same active `property_watches` row with `FOR UPDATE`.
9. Reconfirm its `watch_started` observation ID matches the computed input.
10. Read the latest `buyer_pressure_snapshot` or `buyer_pressure_changed` by
    `observed_at DESC, id DESC`.
11. Compare canonical metrics and insert atomically only when required.

If the watch disappeared, became inactive, or its baseline identity changed
between phases, write nothing. A missing/inactive watch preserves the existing
single-watch `WatchNotFoundError` contract; a baseline mismatch returns
`baseline_unavailable`.

Persist a written observation with `observed_at=collection_time`. This timestamp
is metadata and never participates in metric equality or the digest. Under the
watch lock, if the latest relevant observation has an `observed_at` later than
`collection_time`, the computed snapshot is stale and returns `superseded`
without writing. Equal timestamps are ordered by observation ID; because the
current collector has not inserted yet, exact-equality handling and the unique
key remain authoritative.

Consequences:

- two concurrent collectors with identical metrics produce at most one row;
- a slower, older calculation cannot replace or append after a newer snapshot;
- different, sequential snapshots append in observation-time order;
- locks are acquired only on `property_watches`, matching the existing lock
  order and never locking BUY, MATCH, PROPERTY, or interaction rows;
- a rollback affects only the current Buyer Pressure observation transaction.

## 12. Service and outcome contracts

Add these entry points:

- `collect_buyer_pressure_for_stima(stima_id)`: strict two-phase collector;
- `safe_collect_buyer_pressure_for_stima(stima_id)`: preserves
  `ValidationError` and `WatchNotFoundError` for HTTP 400/404, catches only
  unexpected exceptions, logs the allowed identifiers/classification, and
  returns `failed`;
- `collect_buyer_pressure_for_active_watches()`: obtains active non-null
  `stima_id` values in existing deterministic watch-ID order and invokes the
  safe collector once per item with a per-watch boundary.

One collector outcome has this stable shape:

```json
{
  "status": "written",
  "watch_id": 3,
  "observation": {}
}
```

Allowed statuses are:

- `written`: snapshot/change inserted or the same deterministic insert returned
  after an idempotency collision;
- `unchanged`: valid current metrics equal the latest canonical metrics;
- `baseline_unavailable`: required immutable baseline data is unusable;
- `superseded`: a newer observation already represents a later input snapshot;
- `failed`: unexpected collector failure in a safe boundary.

For no-write statuses, `observation` is null. When the batch catches a listed
watch that disappeared before collection, `watch_id` is null. The single-watch
route still maps that condition to HTTP 404.

The batch response is deterministic:

```json
{
  "processed": 2,
  "written": 1,
  "unchanged": 0,
  "unavailable": 0,
  "superseded": 0,
  "failed": 1,
  "outcomes": [
    {"stima_id": 7, "status": "failed", "watch_id": null, "observation": null},
    {"stima_id": 11, "status": "written", "watch_id": 21, "observation": {}}
  ]
}
```

`baseline_unavailable` contributes to `unavailable`; unrecognized statuses
contribute to `failed`. A failure for one watch never stops a later watch.

## 13. API surface

Add two body-free routes to the already registered Property Watch router:

| Method and path | Service call | Error mapping |
| --- | --- | --- |
| `POST /api/property-watch/stime/{stima_id}/buyer-pressure/refresh` | `safe_collect_buyer_pressure_for_stima(stima_id)` | Missing/inactive watch 404; invalid ID 400; other failures represented by outcome. |
| `POST /api/property-watch/buyer-pressure/refresh-active` | `collect_buyer_pressure_for_active_watches()` | Per-watch failures represented in ordered outcomes. |

Neither route accepts a request body, source values, thresholds, timestamps,
criteria, status filters, payloads, idempotency keys, BUY/PROPERTY identifiers,
or actor IDs from the client. Inputs are derived exclusively on the server.
Both inherit the current ADMIN dependency from `main.py` without changing it.

No scheduler, background job, external provider, or automatic public-stima
hook invokes these routes in P21-A.

## 14. Read-only current-state derivation

Extend `get_current_watch_state()` as a pure derivation over already ordered
observations. Select the latest observation whose type is
`buyer_pressure_snapshot` or `buyer_pressure_changed`. Before the first valid
snapshot, return:

```json
"buyer_pressure_metrics": null
```

After a snapshot/change, return:

```json
"buyer_pressure_metrics": {
  "evaluated_buyers": 18,
  "compatible_buyers": 13,
  "highly_compatible_buyers": 5,
  "recent_compatible_buyers_30d": 7,
  "average_match_score": 72.35,
  "maximum_match_score": 91.40,
  "average_budget": 245000.00,
  "algorithm_version": "match-0.1",
  "latest_observation": {},
  "observed_at": "2026-09-03T12:00:00Z",
  "observation_count": 2
}
```

The eight values come from the latest validated payload. The metadata contains
the complete latest `PropertyWatchObservation`, its time, and the count of both
Buyer Pressure observation types. Existing `baseline`, `microzone_reference`,
`internal_supply`, all-observation history/count, and `computed_at` semantics
remain unchanged.

The GET never calls a collector, MATCH, BUY queries, or any write repository.
No P21 score, band, recommendation, message, or UI-specific projection appears
in the response.

## 15. Security, privacy, and logging

The data boundary is aggregate-only end to end:

- database reads whitelist only calculation fields;
- the pure helper returns only the approved eight metrics;
- observations and HTTP responses expose no BUY-level records;
- no normal MATCH snapshots or criteria are persisted;
- no logs contain payloads, BUY rows, criteria, scores, budgets, contacts, or
  exception messages.

Permitted structured log fields are only:

- `stima_id`;
- `watch_id` when known;
- collector/error classification.

Unexpected errors are logged using `type(exc).__name__`, never `str(exc)` or
`repr(exc)`. A `baseline_unavailable` or `superseded` outcome may be logged by
classification without source values.

## 16. TDD implementation plan

Implementation starts with
`tests/test_p21a_buyer_pressure_metrics.py`; each behavior is RED before its
minimum production change and GREEN before proceeding.

### 16.1 Pure adapter and aggregation

1. Map the five baseline fields exactly and omit every non-approved property
   field.
2. Reject missing/blank locality or typology, booleans, non-finite/non-positive
   surface, and non-finite/non-positive asking price as
   `baseline_unavailable` without a write.
3. Prove active/unarchived SQL filtering and `buy_readiness().can_match`
   filtering exclude every other status, archived rows, and requests without
   effective criteria.
4. Prove a score of 54.99 is not compatible, 55 is compatible, 79.99 is not
   highly compatible, and 80 is highly compatible.
5. Prove any hard fail or `compatibility_status="incompatible"` excludes the
   request at every numeric score.
6. Prove the inclusive UTC 30-day boundary: exactly 30 days is recent, one
   microsecond earlier is not, and only compatible requests are counted.
7. Prove last activity uses the maximum of creation, update, and latest
   interaction time.
8. Prove budget precedence target -> max -> min, null exclusion, and
   `ROUND_HALF_UP` two-decimal aggregation.
9. Prove score means/maxima use only compatible results and null behavior is
   exact.
10. Prove zero eligible BUY rows yields a valid zero snapshot rather than
    unavailable/failed.
11. Prove `algorithm_version` comes from the MATCH contract and malformed or
    inconsistent results fail the whole calculation rather than producing a
    partial aggregate.
12. Recursively assert that returned metrics contain no forbidden BUY/contact
    identifiers, criteria, personal fields, notes, or per-request scores.

### 16.2 Repository, history, and concurrency

13. Assert the BUY snapshot SQL is active/unarchived, ordered, read-only,
    repeatable-read, and selects no contact/title/notes/metadata fields.
14. Assert child criteria are loaded deterministically and the latest
    interaction is aggregated without selecting interaction notes or actors.
15. Use cursor spies to prove P21-A executes no `INSERT`, `UPDATE`, or `DELETE`
    against `matches`, `match_runs`, `match_requirement_results`, BUY,
    interactions, PROPERTY, or `stime`; the only permitted application write is
    an insert into `property_watch_observations`.
16. First valid collection, including the all-zero case, writes exactly one
    `buyer_pressure_snapshot` with the eight-field payload.
17. An equal retry returns `unchanged` and writes no duplicate.
18. A changed canonical payload writes exactly one `buyer_pressure_changed`
    after the latest relevant observation.
19. Different dictionary order or equivalent Decimal representations compare
    equal; timestamps do not trigger a change.
20. Idempotency keys include the correct predecessor and canonical digest;
    collision handling returns the existing row.
21. Deterministic concurrent collectors with equal metrics produce one row;
    an older slower snapshot returns `superseded` and cannot append over a newer
    observation. Use controlled cursor/call order, not sleeps.
22. Watch deactivation/disappearance or baseline-identity change between read
    and write produces the exact no-write/error contract.

### 16.3 Service, batch, API, and read model

23. The safe single-watch collector maps an unexpected exception to `failed`
    and logs only permitted fields; expected validation/not-found exceptions
    remain available to the router.
24. With active IDs `[7, 11]`, force watch 7 to fail/disappear and prove watch
    11 still runs, outcomes remain in `[7, 11]` order, and every aggregate total
    includes watch 7 consistently.
25. Prove both POST routes exist, are ADMIN-protected, expose only POST, and
    have no OpenAPI `requestBody`.
26. Prove the single route maps `WatchNotFoundError` to 404 and
    `ValidationError` to 400.
27. Exercise the real HTTP response model with a DB-shaped observation that
    includes `watch_id`, preventing recurrence of the P20-B1 response-schema
    500.
28. Derive the latest raw metrics/count/time from history and return null before
    the first snapshot while preserving existing P20 fields.
29. Monkeypatch every collector/write boundary to fail if called by GET,
    proving GET is side-effect-free and performs no live BUY/MATCH calculation.
30. Assert the full JSON response and captured logs contain no PII or
    per-request data.
31. Update only the router-hardening test inventory required by the two approved
    routes, then run focused P21-A, P20, MATCH/BUY, router/auth, and full-suite
    regressions.

## 17. Explicit non-goals

P21-A does not include:

- Buyer Pressure score, formula, calibration, band, trend, ranking, seller
  recommendation, commercial claim, or message;
- UI or static asset changes;
- scheduler, background refresh, public-flow hook, email, WhatsApp, task, NBA,
  or automated action;
- external data, scraping, portals, providers, comparables, or live market
  feeds;
- migrations or a current-state table;
- creation/update of PROPERTY, BUY, MATCH, `match_runs`, exclusions, proposals,
  visits, sales, leads, contacts, `stime`, or P20 observations;
- P22 Vendita Invisibile, P23 Next Best Action, or later roadmap behavior;
- any `main.py`, deployment, or PROD change.

## 18. TEST rollout and acceptance

Implementation is deployable only to TEST after local gates pass. Before any
live write, verify separately:

- database `stima360_db_test`;
- branch `core-0.1-test`;
- exact deployed implementation commit;
- migration 022 already present;
- one known active watch with a complete immutable baseline;
- representative active/ready BUY criteria that contain no real personal data
  created for the smoke test.

The guarded TEST smoke sequence is:

1. record baseline counts of `matches`, `match_runs`, and Buyer Pressure
   observations;
2. call the single-watch POST and require HTTP 200 plus either a first snapshot
   or a defined no-write status;
3. read the Property Watch GET and verify the exact raw derived metrics;
4. call the same POST again unchanged and prove no duplicate;
5. call the active-watch batch and verify ordered outcomes/totals;
6. prove `matches` and `match_runs` counts are unchanged;
7. verify observations contain only the eight approved aggregate keys;
8. remove only marker-owned TEST fixtures after checking ownership.

No PROD endpoint/database is accessed. No P21-B conclusion is drawn until a
representative sample of P21-A TEST snapshots has been reviewed for
distribution and data quality.

## 19. Risks and fixed controls

There are no unresolved product or technical choices in this design.

| Risk | Fixed control |
| --- | --- |
| Incomplete valuation produces misleading compatibility | Require all five baseline inputs and write nothing when any is unusable. |
| Empty-criteria BUY inflates demand | Require canonical `buy_readiness().can_match`. |
| Normal MATCH tables are polluted | Invoke only the pure engine and prohibit every MATCH write path. |
| One malformed BUY silently lowers totals | Fail the whole watch calculation and retain prior state. |
| PII leaks through broad queries/payloads/logs | Whitelist read columns, aggregate in memory, enforce exact payload keys, and log only IDs/classification. |
| Repeated refresh creates duplicate history | Canonical equality, predecessor-aware SHA-256 key, watch lock, and existing unique index. |
| Older concurrent computation overwrites newer state | Persist input snapshot time as `observed_at` and return `superseded` under lock. |
| One watch failure aborts the batch | Per-watch safe boundary with deterministic continuation and totals. |
| A future MATCH algorithm changes scores | Persist `algorithm_version`; a new version changes the canonical aggregate and creates a new observation. |
| Raw metrics are presented as a commercial certainty | P21-A exposes no score, band, UI, or seller wording; calibration is deferred to P21-B. |
