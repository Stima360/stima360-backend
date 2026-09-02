# P20-B1 Property Watch Internal Signal Collectors

## 1. Goal

P20-B1 adds two internal-only, append-only signals to an existing active
`property_watches` record:

1. a current internal microzone EUR/sqm reference relative to the watch's
   original valuation baseline; and
2. the number of current STIMA360 PROPERTY records in the same city and
   microzone.

The collectors write only `property_watch_observations`. They do not create a
current-state table, do not update `property_watches`, and do not use external
providers.

## 2. Roadmap boundary P20 vs P21

P20-B1 is collection and observation history only. It resolves the two
internal references, emits a bounded aggregate observation only when the
defined change condition is met, and exposes the result through the existing
Property Watch read model.

P21 is outside this scope. P20-B1 creates no implied P21 behavior: it does not
calculate market formulas, demand, buyer pressure, ranking, recommendations,
or commercial next actions. No downstream module is called by a collector.

## 3. Existing architecture reused

P20-A already provides the complete persistence boundary:

- `property_watches` is one logical watch per `stime` row, with a unique
  non-null `stima_id`, an `active` status, and `ON DELETE SET NULL` history
  preservation.
- `property_watch_observations` is append-only, has `source`, JSONB
  `payload`, `observed_at`, and a globally unique `idempotency_key`.
- `property_watch.repository.ensure_watch_with_baseline()` writes
  `watch_started` atomically and `property_watch.service.get_current_watch_state()`
  derives state from ordered history.
- `property_watch_cursor(commit=True)` is the module transaction helper.
- `main.py` includes `property_watch_router` with `Depends(require_admin)`;
  new Property Watch routes inherit the same admin protection.

The canonical current internal EUR/sqm source is
`zone_valori.prezzo_mq_base`, identified by its database-unique
`(comune, microzona)` pair. This is the source used by the legacy public stima
save flow when no base was submitted. It is deliberately not either
hard-coded `BASE_MQ` dictionary: `valuation.py` and `valuation_base.py`
contain calculator inputs with different values and serve calculation paths,
not the current canonical table lookup.

The watch's original comparison value is
`watch_started.payload.prezzo_mq_base`, captured from the persisted `stime`
record by P20-A. `base_mq` and `eur_mq_finale` are retained baseline context,
but are not substituted for this comparison value.

## 4. Microzone collector design

Implement the strict collector as
`collect_microzone_market_signal_for_stima(stima_id)`, with the
watch-orchestrating entry point `collect_internal_signals_for_stima(stima_id)`.
It resolves the active watch and its `watch_started` payload, then queries:

```sql
SELECT prezzo_mq_base
FROM zone_valori
WHERE comune = %s AND microzona = %s
LIMIT 1
```

The query parameters are the baseline `comune` and `microzona` exactly as
persisted. This intentionally follows the source table's exact, case-sensitive
unique-pair convention. The existing public save path canonicalizes only its
three recognized comune spellings before persisting `stime`; it does not
normalize microzone text. P20-B1 must neither case-fold nor apply the
apostrophe-only lookup normalization used by `valuation.get_base_mq`, because
doing so would select a different record identity from `zone_valori`.

All monetary comparison arithmetic uses `Decimal`, never binary floating point:

- On the first successful collection, compare the current `zone_valori` value
  to `watch_started.payload.prezzo_mq_base`. If equal, write nothing. If
  different, emit `microzone_price_changed` with the baseline as `previous`.
- On later collections, use the `current` field of the latest
  `microzone_price_changed` observation as `previous`. If the current source
  value equals that previous value, write nothing; otherwise emit one new
  change observation.
- A missing or non-finite baseline value, missing `comune` or `microzona`, a
  missing `zone_valori` row, or an unavailable source produces no observation.
  The result is explicitly reported as `baseline_unavailable` or
  `source_unavailable` and logged without payload data. A zero value is still
  a valid persisted NUMERIC value; when it is the previous value,
  `delta_percent` is JSON `null` rather than a division error.

The collector never reaches any external source and never alters
`zone_valori`, `stime`, or PROPERTY data.

## 5. Internal supply collector design

Implement `collect_internal_supply_signal_for_stima(stima_id)` alongside the
microzone collector. It uses the same exact persisted baseline `comune` and
`microzona`, then counts only rows in the internal `properties` table:

```sql
SELECT COUNT(*) AS supply_count
FROM properties
WHERE city = %s
  AND microzone = %s
  AND archived_at IS NULL
  AND commercial_status IN ('mandate', 'active', 'reserved', 'under_offer')
```

The table membership is the internal STIMA360 PROPERTY boundary. Do not filter
on `properties.source`: it is a free-text metadata field with no repository
defined internal/external discriminator, while a PROPERTY row is the existing
internal inventory record. The query has no joins to `stime`, leads, contacts,
buyers, matches, proposals, tasks, or messages.

The first successful count emits `internal_supply_snapshot`, including
`current_count: 0` when no PROPERTY row qualifies. A successful `COUNT(*)`
always yields valid source data, so the absence of PROPERTY rows is never
`source_unavailable`. Later runs compare against the `current_count` in the
latest relevant supply observation (`internal_supply_changed` first, otherwise
`internal_supply_snapshot`) and emit `internal_supply_changed` only when that
count differs. Equal counts write nothing.

Missing baseline locality is `baseline_unavailable` and writes nothing. Only a
real inability to read the PROPERTY source, such as an unexpected database
failure, is a failed collector outcome. The safe wrapper logs that failure,
leaves prior state intact, and does not misreport it as source unavailability.

## 6. Exact PROPERTY status policy

The count uses the established `match.enums.ACTIVE_PROPERTY_STATUSES` and
PROPERTY dashboard active KPI policy exactly:
`('mandate', 'active', 'reserved', 'under_offer')`, plus `archived_at IS NULL`.
The policy for every valid `PROPERTY_STATUSES` value is:

| Status | Counted | Reason |
| --- | --- | --- |
| `draft` | No | It is an unfinished pre-market record. |
| `evaluation` | No | It is a prospective evaluation, not inventory. |
| `mandate` | Yes | It is part of the existing active-property set. |
| `active` | Yes | It is part of the existing active-property set. |
| `reserved` | Yes | It remains in the established active-property set. |
| `under_offer` | Yes | It remains in the established active-property set until sold. |
| `sold` | No | The transaction is concluded; existing property logic excludes it. |
| `withdrawn` | No | It is no longer market inventory. |
| `archived` | No | It is historical/non-current and is excluded regardless of `archived_at`. |

Any row with a non-null `archived_at` is excluded even if its status is one of
the four counted values. This matches the repository's current-record
convention and prevents an archived listing from re-entering supply through an
inconsistent status.

## 7. Observation contracts/payloads

Every new observation has `source: "internal"` and is inserted through the
existing `property_watch_observations` repository boundary. Payloads contain
only aggregate locality and numeric reference data.

`microzone_price_changed`:

```json
{
  "previous": 1500.0,
  "current": 1600.0,
  "delta": 100.0,
  "delta_percent": 6.6666666667,
  "comune": "Alba Adriatica",
  "microzona": "Nord"
}
```

`delta` is `current - previous`; `delta_percent` is
`(delta / previous) * 100`, emitted as JSON `null` when `previous` is zero.
The values are computed as `Decimal` and converted through the existing
Property Watch JSON serializer (`Decimal` to JSON number with `allow_nan=False`)
before the psycopg JSON adapter receives them.

`internal_supply_snapshot`:

```json
{
  "current_count": 4,
  "comune": "Alba Adriatica",
  "microzona": "Nord"
}
```

`internal_supply_changed`:

```json
{
  "previous_count": 4,
  "current_count": 6,
  "delta": 2,
  "comune": "Alba Adriatica",
  "microzona": "Nord"
}
```

Supply payloads never contain PROPERTY identifiers, titles, addresses, owner
or contact data, prices, or per-listing status details.

## 8. Idempotency strategy

Each strict source collection locks the target `property_watches` row with
`FOR UPDATE` in one `property_watch_cursor(commit=True)` transaction before
reading its latest relevant observation and before insert. This serializes
collectors for the same watch without locking PROPERTY rows or changing their
lock order.

Use the already-created baseline or previous relevant observation ID in each
key, so a later transition back to a previously seen numeric value is a new
historical event rather than an accidental collision:

```text
property_watch:microzone_price_changed:watch:{watch_id}:after:{prior_observation_id}:current:{canonical_decimal}:v1
property_watch:internal_supply_snapshot:watch:{watch_id}:after:{watch_started_observation_id}:count:{current_count}:v1
property_watch:internal_supply_changed:watch:{watch_id}:after:{prior_observation_id}:count:{current_count}:v1
```

`canonical_decimal` is the non-exponent Decimal string used for comparison,
with no locale formatting. The database's unique
`idx_property_watch_observations_idempotency_key` remains the final retry and
concurrent-insert guarantee. On conflict, reread and return the existing
observation. A retry therefore returns the same result; it does not duplicate
history. A distinct later change receives a different prior-observation ID.

## 9. Batch/failure isolation

Provide these service entry points:

- `collect_internal_signals_for_stima(stima_id)`: invokes both strict
  collectors and returns their individual outcomes.
- `safe_collect_internal_signals_for_stima(stima_id)`: invokes the microzone
  and supply collectors inside separate fault boundaries. Each boundary catches
  only its own unexpected exception, logs `stima_id`, collector name, and
  exception type without payload data, and returns an explicit failed outcome
  for that collector.
- `collect_internal_signals_for_active_watches()`: obtains active watches with
  non-null `stima_id` in deterministic `id` order and invokes the safe
  single-watch wrapper once per watch.

The batch has no scheduler in P20-B1. It records a summary of processed,
written, unchanged, unavailable, and failed outcomes. Failure of one watch or
one collector never prevents the other collector or later watches from
running. Each source write is independently committed through the existing
Property Watch transaction helper, so no batch-wide transaction can roll back
already valid observations.

There is no outer catch around both collectors that can skip the second after
the first fails. A microzone error therefore never stops the supply collector;
a supply error never invalidates, purges, or changes an already valid
microzone outcome. An expected unavailable microzone reference is an explicit
no-write outcome and warning, not an exception. Unexpected database or
serialization errors are reported as failed only for their collector; the
batch continues.

## 10. API/admin surface

Keep the existing `GET /api/property-watch/stime/{stima_id}` side-effect-free:
it must only read and derive state. It must never invoke a collector.

Add these protected manual POST routes to `property_watch.router`:

| Method and path | Service call | Response |
| --- | --- | --- |
| `POST /api/property-watch/stime/{stima_id}/internal-signals/refresh` | `safe_collect_internal_signals_for_stima(stima_id)` | Watch ID and one explicit outcome per collector. |
| `POST /api/property-watch/internal-signals/refresh-active` | `collect_internal_signals_for_active_watches()` | Aggregate batch summary and per-watch outcome list. |

They inherit `require_admin` from router registration in `main.py`. Neither
route accepts a source value, locality, status list, observation payload,
idempotency key, or actor ID from the browser. The server derives every input
from the watch baseline and internal database state. P20-B1 adds no static
admin screen; the existing watch state endpoint is the initial admin read
surface.

## 11. Current-state derivation

Extend `get_current_watch_state()` only as a pure read-model derivation over
the ordered append-only observations:

- `baseline` remains the `watch_started` observation.
- `microzone_reference` contains the original
  `prezzo_mq_base`, the latest observed reference (latest
  `microzone_price_changed.current`, otherwise the original), the latest
  change observation/time when present, and the relevant observation count.
- `internal_supply` contains the latest count and its observation/time from
  the latest `internal_supply_changed` or `internal_supply_snapshot`, plus
  the relevant observation count. It is absent until the first successful
  supply snapshot.
- `observations`, total `observation_count`, and `computed_at` retain their
  P20-A semantics.

The response is derived anew for every GET. It stores no denormalized current
state, score, trend, band, demand metric, or timestamp outside the observation
history.

## 12. Privacy/data minimization

The collectors read only baseline locality and price, `zone_valori` price, and
the aggregate `COUNT(*)` over PROPERTY records. They do not read or serialize
the P20-A excluded stima personal fields, PROPERTY contacts, PROPERTY leads,
addresses, notes, document data, buyer data, or user identity.

Observations carry only comune, microzona, numeric values, and aggregate
counts. Logs carry an internal watch/stima identifier, collector name, and
error classification; they never log observation payloads or source rows.

## 13. TDD plan

Add focused Property Watch tests before implementation for:

1. first microzone comparison equal to `watch_started.prezzo_mq_base` writes
   no observation, while a different canonical `zone_valori` value writes one
   `microzone_price_changed` with the exact previous/current/delta/percent and
   locality payload;
2. subsequent equal reference writes no duplicate, and a later changed
   reference compares with the latest relevant change rather than the original
   baseline;
3. first supply collection writes exactly one
   `internal_supply_snapshot`, including `current_count: 0` when no PROPERTY
   rows qualify; zero is valid source data rather than `source_unavailable`;
   equal later counts do not write, and changed counts write
   `internal_supply_changed` with aggregate fields only;
4. all nine PROPERTY statuses and non-null `archived_at` prove that only
   `mandate`, `active`, `reserved`, and `under_offer` are counted;
5. only `properties` rows with the exact same city/microzone pair are counted,
   without joins to buyer, match, CRM, or external data;
6. missing baseline price/locality and unavailable `zone_valori` source are
   explicit fail-open no-write outcomes, logged without PII, while an empty
   PROPERTY result remains the valid zero supply snapshot;
7. Decimal arithmetic and repository serialization produce JSON-safe numeric
   values with `allow_nan=False`, including zero-previous
   `delta_percent: null`;
8. retrying a collector returns the existing row for its deterministic key,
   concurrent same-watch collection produces one row, and a return-to-prior
   value creates a new row because the prior observation ID differs;
9. a microzone collector failure still runs the supply collector and returns
    its outcome;
10. a supply collector failure preserves the already-valid microzone outcome
     and does not purge or invalidate its observation;
11. one watch failure is logged and isolated while the batch processes later
     watches;
12. the GET state remains read-only and correctly derives baseline, latest
    microzone reference, latest supply count, histories, counts, and times;
13. the two POST refresh routes are present and admin-protected, while no GET
    route causes a write.

## 14. Explicit non-goals

P20-B1 does not add external market feeds, web scraping, provider adapters,
market-price formulas, buyer pressure, demand scores, MATCH changes, ranking,
NBA, tasks, messages, follow-up, Vendita Invisibile, scheduled jobs, UI
automation, migrations, or changes to `properties`, `stime`, or
`zone_valori`.

It does not infer availability beyond the exact established PROPERTY status
policy, and it does not expose per-property details.

## 15. TEST rollout plan

Deploy only after the TEST database has the existing P20-A migration 022
tables and the pre-existing canonical `zone_valori` table populated for a
known `(comune, microzona)` pair. Confirm those database conditions separately
from code deployment; no migration is added by P20-B1.

In TEST, initialize a watch from a completed public stima, exercise both
manual protected POST endpoints, and inspect the read-only GET state. Execute
the focused Property Watch tests first, including all TDD cases above, then
the applicable integration route/auth regression. Re-run each POST unchanged
to prove no duplicate observations; modify only TEST source data between
manual runs to prove each change contract. No production endpoint, database,
or scheduler is used in this rollout.

## 16. Risks/open questions

There are no unresolved product choices in this design. The implementation
must preserve these explicit operational constraints:

| Risk | Controlled behavior |
| --- | --- |
| `zone_valori` is unavailable or lacks the exact locality pair | Return and log `source_unavailable`; append nothing and retain the last derived state. |
| A legacy watch baseline lacks a usable price or locality | Return and log `baseline_unavailable`; append nothing. |
| Duplicate manual refreshes or retries | Serialize by watch row and rely on the existing unique idempotency index. |
| A property update races the count | Record the count observed by that transaction; the next manual refresh detects the new aggregate. |
| Conflicting calculator dictionaries drift from database values | Read only `zone_valori` for this collector and never use either `BASE_MQ` dictionary. |
| Archived/status-inconsistent PROPERTY rows inflate supply | Require both the four-status allowlist and `archived_at IS NULL`. |
