# P22 Invisible Sale Design

## Implementation identity

- Official base: `origin/core-0.1-test`
- Required base commit: `5ca8f361fe0e9ffbe4a41fadc1136bd6d22c9c35`
- Long-lived branch: `stima360-p22-invisible-sale`
- P22 algorithm version: `invisible-sale-1.0`

Implementation must stop before editing if the remote base does not match the
required commit exactly. `main`, PROD, deployment configuration, schedulers,
and external sources are outside scope.

## Goal

P22 identifies active BUY requests that are highly compatible with a home
observed by Property Watch before that home is published or becomes an official
PROPERTY. It produces an internal, agent-reviewed shortlist only. It neither
represents a mandate nor creates a PROPERTY, MATCH, activity, task, Next Best
Action, or outbound communication.

## Roadmap boundary

P20 owns the monitored valuation and its immutable `watch_started` baseline.
P21-A owns anonymous aggregate demand metrics and P21-B derives a commercial
insight from those metrics. P22 deliberately operates on individually
reviewable, internal BUY candidates.

P22 does not expose the Buyer Pressure formula or score, rank buyers outside
one watch opportunity, expose seller-facing data, contact anyone, or implement
P23 operational automation.

## Architecture

Property Watch remains the owner of the observed home. BUY remains the source
of truth for buyer requirements. MATCH is used only as a pure calculation.
Dedicated P22 modules own shortlist calculation, persistence, and orchestration
so the already-large P20/P21 repository and service modules are not expanded
with P22 internals.

The implementation introduces:

- `property_watch/invisible_sale.py` for pure adaptation, validation,
  candidate calculation, ordering, canonicalization, and hashing;
- `property_watch/invisible_sale_repository.py` for read snapshots,
  transactions, locks, persistence, and read models;
- `property_watch/invisible_sale_service.py` for strict/safe collectors,
  batching, review transitions, and minimal logging;
- the existing Property Watch router and schemas only as API integration
  points.

P22 calls `match.engine.calculate` directly. It must not call MATCH repository
or service functions that create `match_runs`, `matches`, feedback, refresh
jobs, or normal MATCH audit records.

## Immutable ephemeral PROPERTY candidate

The collector reads the earliest persisted `watch_started` observation for the
watch and adapts only its immutable baseline:

```python
{
    "city": baseline["comune"],
    "microzone": baseline["microzona"],
    "property_type": baseline["tipologia"],
    "surface_sqm": baseline["mq"],
    "asking_price": baseline["price_exact"],
}
```

All five values are mandatory. Text values are stripped and must remain
non-empty. `mq` and `price_exact` are converted through `Decimal(str(value))`,
must be finite, and must be strictly positive. No other property attribute is
invented.

An absent or invalid baseline returns `baseline_unavailable`. It creates no P22
opportunity, candidate, or event and does not invoke MATCH.

The ephemeral candidate exists only in memory. P22 never inserts or updates a
PROPERTY.

## Consistent BUY snapshot and eligibility

The repository reads the complete BUY population in one read-only transaction,
including locations, typologies, features, and the latest interaction time.
The snapshot contains the fields already required by
`match.readiness.buy_readiness` and `match.engine.calculate`; it does not contain
contact profile fields, names, email, phone, or notes.

A BUY is eligible only when all of these existing rules hold:

- `status = 'active'`;
- `archived_at IS NULL`;
- `buy_readiness(buy)["can_match"] is True`.

The readiness call is authoritative for the effective-criteria requirement.
Draft, paused, satisfied, closed, archived, and criterion-free requests are
excluded before MATCH calculation.

`last_activity_at` is the greatest non-null UTC value among:

- `created_at`;
- `updated_at`;
- the latest `buy_request_interactions.occurred_at`.

At least one source timestamp must exist. Values are normalized to UTC without
losing microseconds. A malformed timestamp in an otherwise eligible BUY fails
the entire watch collection.

## Strict MATCH result validation

For every eligible BUY, P22 invokes:

```python
match.engine.calculate(buy, ephemeral_property)
```

Before using the result, P22 validates that:

- the result is a mapping;
- `hard_fail_count` is a non-negative integer;
- `score_total` converts to a finite decimal in the inclusive range 0–100;
- `compatibility_status` is exactly `compatible`, `exception`, or
  `incompatible`;
- `algorithm_version` is a non-empty string;
- `criteria` is a list of valid mappings when technical reasons are derived.

`score_total` is canonicalized with `Decimal(str(value))` and quantized to two
decimal places with `ROUND_HALF_UP`. Signed zero becomes `0.00`.

A BUY becomes a P22 candidate only when:

- `hard_fail_count == 0`;
- `compatibility_status` is `compatible` or `exception`;
- canonical `score_total >= Decimal("80.00")`.

`79.99` is excluded and `80.00` is included. Any malformed result or unexpected
calculation error for one eligible BUY fails the complete watch collection.
No partial P22 write is allowed.

## Privacy-safe technical reason codes

The current MATCH engine does not return a technical `reason_codes` field. P22
therefore derives `reason_codes` only from `criteria[].criterion_group`.

The only persisted and exposed values are this fixed ordered enum:

```text
location, budget, typology, dimensions, rooms, features, condition
```

A group is included once when at least one criterion in the group:

- is not blocking;
- has `result` other than `not_applicable` and `not_available`;
- has canonical criterion score greater than or equal to `85.00`.

Codes are deduplicated and emitted in the fixed order above. Arbitrary
`feature_code` values, requested/property values, explanations, strengths,
warnings, blocking reasons, group scores, and individual criterion output are
never stored or returned by P22.

## Candidate record and deterministic order

Each calculated candidate contains only:

- `buy_request_id`;
- canonical `score_total`;
- `compatibility_status`;
- privacy-safe `reason_codes`;
- UTC `last_activity_at`;
- canonical nullable `budget_reference`;
- `match_algorithm_version`;
- `candidate_digest`.

`budget_reference` uses the first non-null value in this order:

1. `budget_target`;
2. `budget_max`;
3. `budget_min`.

The selected budget must be finite and non-negative, is quantized to two
decimals with `ROUND_HALF_UP`, and normalizes signed zero to `0.00`. Null remains
null.

Candidates sort by:

1. `score_total DESC`;
2. `last_activity_at DESC`;
3. `buy_request_id ASC`.

The same order is used for persistence, API responses, hashing, and tests.

## Canonical representation and digest

The canonical candidate representation is:

```json
{
  "buy_request_id": 123,
  "score_total": "87.50",
  "compatibility_status": "compatible",
  "reason_codes": ["location", "budget"],
  "last_activity_at": "2026-09-04T10:15:30.123456Z",
  "budget_reference": "250000.00",
  "match_algorithm_version": "match-0.1"
}
```

`candidate_digest` is SHA-256 over the UTF-8 canonical JSON of that mapping,
using sorted object keys and compact separators.

The opportunity digest is SHA-256 over:

```json
{
  "algorithm_version": "invisible-sale-1.0",
  "candidates": [/* canonical candidates in deterministic order */]
}
```

The digest deliberately includes candidate activity and budget because their
change must refresh the shortlist. It deliberately excludes collection time,
database row IDs, opportunity revision, and human review status. Therefore:

- identical calculated state causes zero writes;
- a real candidate score, reason, activity, budget, or MATCH-version change is
  detected;
- approve/reject decisions do not create a false collector change;
- an empty candidate set has one stable digest.

## Dedicated persistence

P22 uses one numbered additive migration pair:

- `migrations/023_invisible_sale.sql`;
- `migrations/023_invisible_sale_down.sql`.

The down migration removes only P22 tables in foreign-key-safe reverse order.
The up migration creates the following tables.

### `invisible_sale_opportunities`

- `id` primary key;
- `watch_id` non-null foreign key to Property Watch and `UNIQUE`;
- `status` constrained to `ready`, `empty`, or `closed`;
- `candidate_digest` non-null SHA-256 text;
- `current_candidate_count` non-negative integer;
- `algorithm_version` non-null;
- `revision` non-negative integer, incremented only for a real calculated-set
  transition;
- `last_evaluated_at`, `created_at`, and `updated_at` UTC timestamps.

### `invisible_sale_candidates`

- `id` primary key;
- `opportunity_id` non-null foreign key;
- `buy_request_id` non-null foreign key;
- `UNIQUE(opportunity_id, buy_request_id)`;
- minimized calculated fields defined above;
- `status` constrained to `pending_review`, `approved`, `rejected`, or
  `stale`;
- `decision_version` non-negative integer, incremented only for a real human
  decision transition;
- UTC `created_at` and `updated_at`.

### `invisible_sale_events`

- `id` primary key;
- `opportunity_id` non-null foreign key;
- nullable `candidate_id` foreign key;
- `event_type` constrained to `discovered`, `refreshed`, `approved`,
  `rejected`, `stale`, or `closed`;
- nullable `opportunity_revision` and `decision_version`;
- non-null deterministic `idempotency_key` with `UNIQUE`;
- minimal PII-free JSON payload;
- UTC `created_at`.

P22 has no delete endpoint. Foreign keys use restrictive semantics so audit
history cannot be silently removed by deleting referenced watches, BUYs,
opportunities, or candidates.

## Refresh transitions

The first valid collection always creates an opportunity, including a valid
zero-candidate result:

- zero candidates creates `empty`;
- one or more current candidates creates `ready`;
- every new candidate starts as `pending_review`.

For a later changed digest:

- a newly eligible BUY is inserted as `pending_review`;
- a still-eligible candidate retains `approved` or `rejected`;
- a still-eligible undecided candidate remains `pending_review`;
- an absent candidate becomes `stale`;
- a stale candidate that returns becomes `pending_review`;
- `current_candidate_count` counts non-stale candidates only;
- zero non-stale candidates makes the opportunity `empty`;
- one or more non-stale candidates makes it `ready`.

An unchanged digest performs no opportunity, candidate, or event write and
does not update `last_evaluated_at`.

`closed` is terminal. A refresh may calculate before observing the concurrent
close, but after locking it returns `closed` with zero writes. It never reopens
or refreshes candidates. Approve/reject on a closed opportunity returns HTTP
409. Repeating close returns the existing state with HTTP 200 and zero writes.

## Human review transitions

Approve and reject are reversible only while the opportunity is open and the
candidate is current:

- `pending_review` or `rejected` may become `approved`;
- `pending_review` or `approved` may become `rejected`;
- repeating the current decision is idempotent and performs zero writes;
- `stale` candidates cannot be approved or rejected and return HTTP 409;
- missing opportunity/candidate relationships return HTTP 404;
- a BUY ID that belongs to another opportunity is never accepted.

Every real approve/reject transition increments `decision_version` inside the
locked transaction and appends exactly one audit event. Review actions never
create activities, tasks, NBA records, MATCH records, or communications.

## Audit keys that survive repeated state cycles

Idempotency keys include the monotonic opportunity revision or candidate
decision version. This preserves the audit trail for real cycles such as
digest A→B→A and approve→reject→approve while keeping retries idempotent.

```text
invisible_sale:discovered:candidate:{candidate_id}:revision:{revision}:v1
invisible_sale:refreshed:watch:{watch_id}:revision:{revision}:digest:{digest}:v1
invisible_sale:stale:candidate:{candidate_id}:revision:{revision}:v1
invisible_sale:approved:candidate:{candidate_id}:decision:{decision_version}:v1
invisible_sale:rejected:candidate:{candidate_id}:decision:{decision_version}:v1
invisible_sale:closed:opportunity:{opportunity_id}:v1
```

Candidate discovery covers first creation. A returning stale candidate is
represented by the revision-scoped `refreshed` event plus its restored
`pending_review` state; no extra event type is introduced.

Audit payloads contain only technical transition fields such as prior/new
status, revision, candidate count, and digest. They never contain names,
contact data, criteria, explanations, scores per criterion, or notes.

## Transaction, lock order, and concurrent creation

Calculation occurs outside write locks. Persistence uses one transaction and
this lock order everywhere:

1. lock the Property Watch row with `FOR UPDATE`;
2. read the opportunity;
3. if absent, attempt creation with `INSERT ... ON CONFLICT (watch_id) DO
   NOTHING`, then read it again;
4. lock the opportunity with `FOR UPDATE`;
5. reread its status, digest, and revision;
6. if closed or unchanged, return without writes;
7. lock affected candidate rows in `buy_request_id ASC` order;
8. atomically apply opportunity, candidate, and event changes;
9. commit once.

No reused repository helper may open or commit an independent transaction
inside this workflow.

Two collectors that calculate equivalent state may race before the lock. The
unique watch constraint, conflict recovery, lock/reread, digest comparison,
revisioned event keys, and unique event constraint guarantee at most one
effective transition and no duplicate event.

## Service outcomes and failure isolation

The implementation exposes these service operations:

```python
collect_invisible_sale_for_stima(stima_id: int) -> dict
safe_collect_invisible_sale_for_stima(stima_id: int) -> dict
collect_invisible_sale_for_active_watches() -> dict
get_invisible_sale_for_stima(stima_id: int) -> dict
approve_invisible_sale_candidate(stima_id: int, buy_request_id: int) -> dict
reject_invisible_sale_candidate(stima_id: int, buy_request_id: int) -> dict
close_invisible_sale(stima_id: int) -> dict
```

Strict collection returns `baseline_unavailable`, `written`, `unchanged`, or
`closed`. Controlled validation and not-found errors remain exceptions for the
single-watch route. An unexpected BUY calculation error rolls back the entire
watch transaction.

The safe collector catches expected per-watch failures, logs only permitted
IDs and exception classification, and returns `failed`. The active-watch batch
reads active, non-null watches in `watch_id ASC` order, opens no batch-wide
transaction, continues after failure, and returns one ordered outcome per
watch plus explicit totals for `written`, `unchanged`,
`baseline_unavailable`, `closed`, and `failed`.

## Read model

The P22 GET is strictly read-only. It never invokes collection, review, or any
write repository helper.

Before the first valid collection it returns status `not_collected`, count
zero, and an empty candidate list. Otherwise it returns minimized opportunity
and candidate fields only. Current candidates use the calculation order;
stale candidates follow, ordered by `updated_at DESC` then `buy_request_id ASC`.

No P22 data is added to a public, seller, owner, or unauthenticated response.

## Admin API

All routes mount under the existing ADMIN-protected Property Watch router and
accept no request body:

```text
GET  /api/property-watch/stime/{stima_id}/invisible-sale
POST /api/property-watch/stime/{stima_id}/invisible-sale/refresh
POST /api/property-watch/invisible-sale/refresh-active
POST /api/property-watch/stime/{stima_id}/invisible-sale/candidates/{buy_request_id}/approve
POST /api/property-watch/stime/{stima_id}/invisible-sale/candidates/{buy_request_id}/reject
POST /api/property-watch/stime/{stima_id}/invisible-sale/close
```

Path IDs are validated server-side. Refresh, review, and close derive every
relationship and current state from the database. The POST operations declare
no request-body schema in OpenAPI and never read or trust body fields; an
extraneous HTTP body cannot influence the operation. No public or seller
endpoint exists.

## Internal OS UI

The authenticated contact-linked valuation view gains a section titled
**Potenziali acquirenti prima della pubblicazione** for each known linked
valuation.

It displays only:

- opportunity status and current candidate count;
- candidate compatibility score;
- fixed technical reason labels;
- recent activity timestamp;
- budget reference;
- review status;
- an internal link to the existing BUY request.

It provides body-free **Approva**, **Rifiuta**, and **Aggiorna** actions.
Refresh refetches only the targeted valuation. Buttons are disabled while a
request is in flight, duplicate clicks produce one request, and stale component
mounts cannot overwrite the current contact view.

The component uses DOM APIs and `textContent` for dynamic values. It never
renders untrusted dynamic strings through `innerHTML`. It displays no email,
phone, name, notes, criteria, owner, or seller data; performs no polling or
automatic refresh; stores nothing in the browser; and never initiates contact.
Failure or unavailability for one valuation does not block other cards.

## Security and logging

P22 database rows, API responses, logs, and UI contain no names, emails,
phones, notes, full criteria, criterion values, free-text MATCH explanations,
or owner/seller data.

Logs may contain only:

- `stima_id`;
- `watch_id`;
- `opportunity_id`;
- `buy_request_id`;
- exception classification.

Logs never include payloads. P22 has no external source, scraping, scheduler,
Render configuration, public surface, or deployment operation.

## Exact implementation scope

The implementation branch may change only these paths:

```text
docs/superpowers/specs/2026-09-04-p22-invisible-sale-design.md
docs/superpowers/plans/2026-09-04-p22-invisible-sale-implementation.md
migrations/023_invisible_sale.sql
migrations/023_invisible_sale_down.sql
property_watch/invisible_sale.py
property_watch/invisible_sale_repository.py
property_watch/invisible_sale_service.py
property_watch/router.py
property_watch/schemas.py
static/os_shell/assets/components/invisible-sale.js
static/os_shell/assets/views/contatto-dettaglio.js
static/os_shell/assets/app.css
tests/test_p22_invisible_sale.py
tests/test_p22_invisible_sale_ui.py
tests/test_next2_router_hardening.py
```

No modification is permitted to `main.py`, existing migrations, P20/P21
calculation behavior, BUY writers, PROPERTY writers, MATCH persistence,
schedulers, deployment configuration, or public/seller UI.

## TDD implementation plan

Every task starts with a focused failing test, records the RED failure, applies
the minimum implementation, and reruns GREEN. Coverage must include:

1. exact baseline adaptation, invalid/missing values, and
   `baseline_unavailable` with zero P22/MATCH/BUY/PROPERTY writes;
2. active/unarchived/readiness/effective-criteria filtering and one consistent
   read-only BUY snapshot;
3. direct pure MATCH invocation, strict malformed-result validation, hard-fail
   exclusion, `79.99` exclusion, and `80.00` inclusion;
4. fixed reason-code derivation without arbitrary feature codes or free text;
5. UTC activity maximum, budget fallback, decimal normalization, deterministic
   ordering, minimized candidate shape, candidate digest, and empty digest;
6. ready/empty opportunity creation, pending candidates, approved/rejected
   preservation, stale transition, stale return to pending, and terminal closed
   behavior;
7. canonical equivalence with zero writes and real score/reason/activity/budget
   changes producing one revision;
8. digest cycle A→B→A producing three legitimate revisions without event-key
   collision;
9. approve→reject→approve producing three legitimate decisions, repeated same
   decisions producing zero writes, and stale/closed decisions returning 409;
10. concurrent first creation through exact `ON CONFLICT` recovery and two
    equivalent concurrent refreshes producing one effective transition;
11. deterministic lock order and no independent nested transaction;
12. complete rollback when one eligible BUY calculation fails and batch
    continuation in watch order with explicit outcomes/totals;
13. migration constraints, foreign keys, checks, uniqueness, indexes, and safe
    down order;
14. ADMIN authentication, routes with no OpenAPI request body, proof that an
    extraneous body cannot influence service arguments, IDOR relationship
    validation, controlled 404/409 mappings, and idempotent close;
15. P22 GET with zero collector/review/repository writes for not-collected,
    ready, empty, and closed states;
16. absence of writes to `matches`, `match_runs`, BUY, PROPERTY, activities,
    tasks, NBA, and communications;
17. absence of PII/free text in database fields, API output, audit payloads,
    logs, and UI;
18. UI ordering, state labels, BUY links, target-only refresh, duplicate-click
    prevention, stale-mount protection, failure isolation, and XSS-safe DOM
    rendering with no automatic contact.

Regression gates include P20 Property Watch, P21-A metrics, P21-B score/UI,
MATCH/BUY behavior, P17/Seller Intent, router hardening, migration checks,
JavaScript syntax checks, and the complete test suite. No TEST or PROD database
is accessed during implementation or local validation.

## Alternatives considered

1. **Dedicated P22 modules and persistence — chosen.** Preserves a minimized,
   reviewable, auditable internal workflow without changing Property Watch,
   BUY, PROPERTY, or MATCH semantics.
2. **Put all P22 logic in existing Property Watch repository/service.**
   Rejected because those files already own P20/P21 flows and would mix
   calculation, review, and persistence responsibilities.
3. **Temporary PROPERTY and persisted MATCH.** Rejected because it invents an
   acquisition and contaminates official portfolio and MATCH history.
4. **P21 aggregates only.** Rejected because aggregate demand cannot identify a
   reviewable internal BUY candidate.
5. **Immediate task or BUY contact.** Rejected because it removes human control
   and belongs to P23 or later commercial automation.

The chosen design keeps Property Watch as owner of the observed home, BUY as
the existing source of truth, MATCH as a pure calculation, P22 as a dedicated
internal shortlist workflow, and agents as the only decision-makers.

## Acceptance criteria

P22 is complete only when all of the following are proven:

- no fake PROPERTY or normal MATCH persistence is created;
- only active, unarchived, ready BUYs are evaluated;
- only no-hard-fail scores of at least `80.00` enter the shortlist;
- stored/API/UI candidate data is minimized and free of PII and arbitrary
  criterion text;
- empty, changed, unchanged, stale, reactivated, reviewed, and closed states
  follow the exact transitions above;
- retries and concurrent equivalent collections create at most one effective
  transition;
- real repeated cycles retain complete audit history without key collision;
- every write workflow is atomic per watch and batch failures are isolated;
- GET is demonstrably read-only and all mutations are ADMIN-only/body-free;
- UI actions are manual, scoped, safe against duplicate/stale requests, and do
  not contact anyone;
- only the approved paths changed and all required regression gates pass.
