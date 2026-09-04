# P21-B Buyer Pressure Score and UI

## 1. Goal, source revision, and fixed boundary

P21-B presents an explainable, conservative Buyer Pressure insight for every
valuation linked to a contact. It derives the insight only from the anonymous,
persisted P21-A aggregate carried by the current Property Watch state:
`buyer_pressure_snapshot` and `buyer_pressure_changed`.

This design is based on `origin/core-0.1-test` at exactly
`a148c66c37aa6ef64b95a6a18c5cba07c306a3ef`.

P21-B is a read-model and manual-refresh presentation feature. It does not
change P21-A collection, input selection, MATCH calculation, readiness,
canonical metric persistence, idempotency, observation types, or manual
refresh routes. It creates no score history, table, migration, route,
scheduler, external call, automatic collection, MATCH run, or database write.

The existing protected endpoint remains the only read surface:

```text
GET /api/property-watch/stime/{stima_id}
```

It remains fully read-only. It must never collect, refresh, invoke MATCH, read
live BUY inputs, create an observation, or write any record. P21-B adds the
top-level `buyer_pressure_insight` field to that response. It is `null` when
`buyer_pressure_metrics` is `null`.

P21-B is not a claim of a buyer for a specific property, a sale probability,
or a pricing recommendation. It is an internal aggregate signal over active
BUY requests and their existing MATCH compatibility with the immutable P21-A
valuation baseline.

## 2. Existing contracts reused without alteration

P21-A already provides the necessary current read model:

```json
{
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
}
```

P21-B uses only the eight aggregate metric values from the latest validated
P21-A observation. It does not inspect BUY requests, IDs, criteria, contacts,
lead details, personal data, addresses, property attributes, normal MATCH
records, or historical observation payloads other than the single current
aggregate selected by P21-A.

The existing P21-A `canonicalize_metrics()` remains authoritative for the
eight-key raw metric contract. The new score helper imports it directly from
`property_watch.buyer_pressure`; it does not add a package export or alter
that P21-A module. `algorithm_version` remains a P21-A/MATCH provenance
attribute. The new, independent scoring algorithm version is always:

```text
buyer-pressure-score-1.0
```

The score version must never replace, infer, or be compared as the P21-A
`algorithm_version`.

## 3. Valid input and corrupt-observation behavior

The pure helper receives a dictionary containing exactly the P21-A metric
payload keys, not the `latest_observation`, `observed_at`, or
`observation_count` metadata:

```text
derive_buyer_pressure_insight(metrics: dict) -> dict
```

The service obtains that dictionary by projecting the eight approved values
from the already selected P21-A current metrics and passing the projection to
P21-A `canonicalize_metrics()`. It then applies these additional invariants:

1. `0 <= highly_compatible_buyers <= compatible_buyers <= evaluated_buyers`.
2. `0 <= recent_compatible_buyers_30d <= compatible_buyers`.
3. When `compatible_buyers == 0`, `average_match_score`,
   `maximum_match_score`, and `average_budget` are all `null`.
4. When `compatible_buyers > 0`, `average_match_score` and
   `maximum_match_score` are non-null values from 55.00 through 100.00, and
   `maximum_match_score >= average_match_score`.
5. `average_budget` is either `null` or the non-negative finite,
   P21-A-canonical decimal value.
6. `algorithm_version` is the non-empty exact string accepted by P21-A
   canonicalization. P21-B does not impose a second MATCH version literal.

P21-A canonicalization already rejects any non-exact key set, boolean count,
negative count, non-finite decimal, invalid decimal representation, or blank
algorithm version. The P21-B helper raises `ValueError` for any additional
invariant failure. It has no database, current-time, I/O, or MATCH dependency.

`get_current_watch_state()` handles a malformed current Buyer Pressure
observation as a safe read failure, not an HTTP failure. On a
`ValueError` raised while canonicalizing or deriving:

- retain every unrelated Property Watch field unchanged;
- return `buyer_pressure_metrics: null` and
  `buyer_pressure_insight: null`;
- log exactly the available `stima_id`, `watch_id`, and
  `type(exc).__name__` classification;
- do not log an exception message, representation, payload, metric values,
  BUY data, or any PII.

The latest valid observation is not replaced, repaired, or refreshed by GET.
The next explicit P21-A manual refresh remains the only way to write a later
raw snapshot. A missing P21-A metric state is not an error and returns both
Buyer Pressure fields as `null` without a corruption log.

## 4. Deterministic score

All arithmetic uses `Decimal`. Each component is rounded independently to an
integer using `Decimal("1")` and `ROUND_HALF_UP`; components are never rounded
with binary floating point.

| Ordered factor | Code | Formula before integer rounding | Maximum |
| --- | --- | --- | ---: |
| Buyer compatibili | `compatible_volume` | `min(compatible_buyers, 10) / 10 * 30` | 30 |
| Buyer altamente compatibili | `highly_compatible_volume` | `min(highly_compatible_buyers, 5) / 5 * 25` | 25 |
| Buyer compatibili attivi negli ultimi 30 giorni | `recent_compatible_activity` | `min(recent_compatible_buyers_30d, 8) / 8 * 20` | 20 |
| Qualità media dei match compatibili | `average_match_quality` | `clamp((average_match_score - 55) / 45, 0, 1) * 15`; null is zero | 15 |
| Migliore match compatibile | `maximum_match_quality` | `clamp((maximum_match_score - 55) / 45, 0, 1) * 10`; null is zero | 10 |

`clamp(value, 0, 1)` is inclusive at both ends. The score is the sum of the
five rounded component points, clamped to the inclusive range 0--100. When
`compatible_buyers == 0`, the score is exactly zero and all five factor points
are exactly zero, regardless of any otherwise impossible input. This explicit
rule preserves the meaning of the `none` band.

`evaluated_buyers` and `average_budget` are display-only source metrics. They
receive no points and never appear as factors. No cap, threshold, trend,
recency calculation, budget conversion, or hidden adjustment is applied
beyond the formulas above.

The helper always returns all five factors in the table order. Factor objects
contain exactly these fields and no raw source values:

```json
{
  "code": "compatible_volume",
  "label": "Buyer compatibili",
  "points": 30,
  "max_points": 30
}
```

The sum of the five `points` values must exactly equal `score`; each
`max_points` value must be the fixed maximum in the table.

## 5. Bands and approved commercial language

| Score | `band` | `band_label` | `headline` |
| ---: | --- | --- | --- |
| 0 | `none` | `Nessuna domanda rilevata` | `NESSUNA DOMANDA RILEVATA — 0/100` |
| 1--34 | `low` | `Domanda bassa` | `DOMANDA BASSA — {score}/100` |
| 35--64 | `medium` | `Domanda media` | `DOMANDA MEDIA — {score}/100` |
| 65--100 | `high` | `Domanda alta` | `DOMANDA ALTA — {score}/100` |

The exact message for each band is:

| `band` | `message` |
| --- | --- |
| `none` | `Al momento non risultano richieste BUY compatibili con i dati disponibili.` |
| `low` | `Nel database STIMA360 risultano alcune compatibilità, ma la pressione della domanda è ancora limitata.` |
| `medium` | `Nel database STIMA360 è presente una domanda concreta per immobili con caratteristiche simili.` |
| `high` | `Nel database STIMA360 è presente una domanda elevata di acquirenti compatibili per immobili con caratteristiche simili.` |

Every valid insight has this exact disclaimer:

```text
Indicatore interno basato su richieste BUY attive e criteri MATCH; non garantisce la vendita né l’interesse per lo specifico immobile.
```

The UI and API must not render, derive, concatenate, or imply any of these
prohibited phrases:

```text
abbiamo già il compratore
questi acquirenti vogliono il tuo immobile
vendita garantita
prezzo garantito
acquirenti pronti ad acquistare
```

## 6. Insight response schema

`buyer_pressure_insight` has this exact JSON shape when raw metrics are valid:

```json
{
  "score_version": "buyer-pressure-score-1.0",
  "score": 87,
  "band": "high",
  "band_label": "Domanda alta",
  "headline": "DOMANDA ALTA — 87/100",
  "message": "Nel database STIMA360 è presente una domanda elevata di acquirenti compatibili per immobili con caratteristiche simili.",
  "disclaimer": "Indicatore interno basato su richieste BUY attive e criteri MATCH; non garantisce la vendita né l’interesse per lo specifico immobile.",
  "factors": [
    {
      "code": "compatible_volume",
      "label": "Buyer compatibili",
      "points": 30,
      "max_points": 30
    },
    {
      "code": "highly_compatible_volume",
      "label": "Buyer altamente compatibili",
      "points": 25,
      "max_points": 25
    },
    {
      "code": "recent_compatible_activity",
      "label": "Buyer compatibili attivi negli ultimi 30 giorni",
      "points": 18,
      "max_points": 20
    },
    {
      "code": "average_match_quality",
      "label": "Qualità media dei match compatibili",
      "points": 6,
      "max_points": 15
    },
    {
      "code": "maximum_match_quality",
      "label": "Migliore match compatibile",
      "points": 8,
      "max_points": 10
    }
  ]
}
```

`score` and factor point values are integers. `score` is 0--100. `band` is
one of `none`, `low`, `medium`, and `high`. The score result deliberately has
no `computed_at`, source timestamp, raw count, budget, observation, BUY
identifier, or PII field. The surrounding response retains
`buyer_pressure_metrics` unchanged when valid, including its P21-A
`algorithm_version` and `observed_at`.

New strict Pydantic models in `property_watch/schemas.py` represent the
factor and insight shapes, and `PropertyWatchState` gains:

```python
buyer_pressure_insight: BuyerPressureInsight | None
```

No response model or route declaration changes in `property_watch/router.py`
are necessary.

## 7. Backend composition

Create `property_watch/buyer_pressure_score.py` as a pure module. It imports
only `Decimal`, `ROUND_HALF_UP`, typing support, and P21-A
`canonicalize_metrics`. Its public entry point is:

```python
def derive_buyer_pressure_insight(metrics: dict) -> dict:
    ...
```

It canonicalizes, validates the Section 3 relationships, calculates the five
components, selects the approved band copy, and returns the Section 6
dictionary. It has no current-clock call, no implicit default, and no
exception-catching fallback. Invalid source data is surfaced as `ValueError`
to the service's narrow GET-only corruption boundary.

`property_watch/service.py` remains the read-model composition point:

1. Load the watch and its ordered observations through the existing read-only
   repository calls.
2. Select only the latest `buyer_pressure_snapshot` or
   `buyer_pressure_changed` observation using the existing deterministic
   ordering.
3. Build the current P21-A metric state exactly as today when its payload
   validates.
4. Project its eight raw metric keys, derive `buyer_pressure_insight`, and
   return it as a top-level sibling of `buyer_pressure_metrics`.
5. Use `buyer_pressure_metrics["observed_at"]` as the sole Buyer Pressure
   timestamp made available to the UI. P21-B introduces no timestamp.

This is a pure extension of the existing GET state derivation. It must not
call `collect_buyer_pressure_for_stima`,
`safe_collect_buyer_pressure_for_stima`,
`collect_buyer_pressure_for_active_watches`, P21-A repository input/store
functions, any other collector, `match.engine.calculate`, or a write
repository.

## 8. Contact overview UI

P21-B appears in **Panoramica** of the OS-shell contact detail. It is placed
immediately after **Seller Intelligence** and before **Relazioni operative**.
The section is headed **Domanda buyer** and contains one independently loaded
card for every distinct linked `stima_id`.

The relationship is deliberately read through the existing authoritative
path:

```text
Contatto -> Contact360.leads -> GET /api/core/leads/{lead_id}
-> lead.estimations (lead_stime) -> stima_id
```

The view loads lead details lazily after the contact overview mounts, reusing
the existing per-contact lead-detail cache. Every fulfilled lead detail
contributes only valid numeric `stima_id` values; the resulting known IDs are
deduplicated and ordered ascending numerically. The component then issues one
`GET /api/property-watch/stime/{stima_id}` per ordered known stima through
`Promise.allSettled`. A failed watch GET affects only that known stima card; it
never removes, blocks, retries, or changes another card. A failed lead-detail
request cannot identify a stima and therefore never manufactures an
unavailable card.

No automatic POST occurs at contact rendering, section hydration, tab change,
retry, cache read, or page revisit. The UI does not poll.

### 8.1 Card content

For a valid state, each card shows only:

1. `Stima #{stima_id}`.
2. The exact `headline`, score as `{score}/100`, and `band_label`.
3. The exact approved `message`.
4. The five ordered factor rows as `{label}: {points}/{max_points}`.
5. The raw P21-A metrics with exactly these labels:
   `Buyer valutati`, `Buyer compatibili`, `Buyer altamente compatibili`,
   `Buyer compatibili recenti (30 giorni)`, `Score medio MATCH`, `Score massimo
   MATCH`, and `Budget medio`. The four counts render as integers. Null average
   or maximum scores render `—`; otherwise each renders with two `it-IT`
   decimal places followed by `/100`. A null budget renders `—`; otherwise it
   renders as EUR with two `it-IT` decimal places. These are display-only raw
   metrics: they neither replace nor duplicate the five factors, and
   `Buyer valutati` and `Budget medio` receive no points.
6. The P21-A observation time from `buyer_pressure_metrics.observed_at`,
   formatted through the existing `formatDateTime()` helper.
7. The exact disclaimer.
8. A manual **Aggiorna domanda buyer** button.

The card renders no buyer identity, buyer count beyond the approved aggregates,
lead status, property address, price recommendation, current time, individual
MATCH result, or inferred attribute.

The state copy is exact:

| Condition | Card content |
| --- | --- |
| All lead details succeed and none contains a valid linked stima | `Nessuna stima collegata per il calcolo della domanda buyer.` |
| At least one lead detail fails and at least one other detail supplies a known stima | `Non è stato possibile verificare tutte le stime collegate.` |
| All lead details fail, or no known stima can be established because every available lead detail fails | `Non è stato possibile verificare le stime collegate.` |
| A Property Watch GET is missing or fails for one stima | `Domanda buyer non disponibile per la stima #{stima_id}.` |
| Watch GET succeeds but `buyer_pressure_metrics` or `buyer_pressure_insight` is null | `Domanda buyer non ancora calcolata.` |
| Manual refresh is in flight | `Calcolo domanda buyer in caricamento…` |
| Refresh returns `baseline_unavailable` | `Dati della stima insufficienti per calcolare la domanda buyer.` |
| Refresh returns `failed` or rejects at the transport/API boundary | `Impossibile aggiornare la domanda buyer. Riprova.` |

`written` and `unchanged` are both successful manual refresh outcomes. Neither
adds a claim-specific success sentence. Each immediately refetches only that
stima's GET state and replaces only that card with the latest read result.

### 8.2 Manual refresh interaction

The refresh button calls only the existing body-free P21-A endpoint:

```text
POST /api/property-watch/stime/{stima_id}/buyer-pressure/refresh
```

It passes no body, source field, metric, threshold, timestamp, lead ID, buyer
ID, or other client-controlled value. The button disables before the POST,
remains disabled while its one request is in flight, and ignores further clicks
until the request settles. A per-stima in-flight map prevents duplicate
requests even if DOM events race.

For `written` or `unchanged`, the component invalidates and refetches only the
target stima's cached GET result. It does not invalidate other cards, the
Contact360 response, cached lead details, Seller Intelligence, the timeline,
or the page. For `baseline_unavailable` and `failed`, it retains unaffected
cards, restores the button, and presents the exact relevant state text. A
stale asynchronous result may update a mount only when that mount is still
connected and carries the current request token.

### 8.3 Frontend separation, cache, and XSS controls

Create `static/os_shell/assets/components/buyer-pressure.js`. It owns:

- GET and body-free POST calls through existing `apiGet` and `apiPost`;
- stima-specific in-memory result and in-flight-promise caches;
- refresh de-duplication and target-only invalidation;
- all card and state rendering;
- the approved display-only formatting and `formatDateTime()` use.

`static/os_shell/assets/views/contatto-dettaglio.js` is limited to importing
the component, inserting a dedicated mount immediately after Seller
Intelligence, maintaining the per-contact cache, and delegating hydration. It
contains no score arithmetic, band selection, commercial message, refresh
business logic, or direct Property Watch request construction.

All dynamic values are escaped with the existing `escapeHtml()` before HTML
interpolation, including stima IDs, score, labels, factors, messages,
disclaimer, budgets, timestamps, errors, and status-derived values. Fixed
markup may use `innerHTML`; no dynamic value may be interpolated unescaped.
DOM event handlers use captured, validated numeric IDs rather than dynamic
inline event attributes.

Caching is in memory for the current contact-detail render only. P21-B uses no
`localStorage`, `sessionStorage`, IndexedDB, cookie, analytics, beacon,
WebSocket, EventSource, polling timer, external communication, or persisted
individual buyer data.

Add only scoped `buyer-pressure-*` CSS to `static/os_shell/assets/app.css`,
following the existing Seller Intelligence card/grid, detail, muted,
error-box, and disabled button patterns. It must not restyle existing Seller
Intent, timeline, contact, table, or global components.

## 9. Planned implementation file boundary

The future implementation branch contains exactly these ten P21-B paths:

| Path | Action | Responsibility |
| --- | --- | --- |
| `docs/superpowers/specs/2026-09-03-p21b-buyer-pressure-score-ui-design.md` | Carry unchanged | Approved P21-B design. |
| `docs/superpowers/plans/2026-09-03-p21b-buyer-pressure-score-ui-implementation.md` | Carry unchanged | Approved executable implementation plan. |
| `property_watch/buyer_pressure_score.py` | Create | Pure P21-B canonical validation, Decimal scoring, bands, approved copy, and factors. |
| `property_watch/service.py` | Modify | Read-only insight derivation and narrow malformed-observation handling in current watch state. |
| `property_watch/schemas.py` | Modify | Strict insight and factor response models plus the optional state field. |
| `static/os_shell/assets/components/buyer-pressure.js` | Create | Dedicated card retrieval, render, cache, and manual-refresh behavior. |
| `static/os_shell/assets/views/contatto-dettaglio.js` | Modify | Overview mount, cache, and component delegation only. |
| `static/os_shell/assets/app.css` | Modify | Scoped Buyer Pressure card styling only. |
| `tests/test_p21b_buyer_pressure_score.py` | Create | Backend pure score and GET-read-model tests. |
| `tests/test_p21b_buyer_pressure_ui.py` | Create | Static and executable OS-shell Buyer Pressure UI contract tests. |

P21-B must not alter `property_watch/repository.py`,
`property_watch/router.py`, `property_watch/buyer_pressure.py`,
`property_watch/__init__.py`, `main.py`, migrations, P21-A test files, the
P21-A collector, normal MATCH/BUY/PROPERTY code, Seller Intent code,
Seller Intelligence code, CRM, FOLLOWUP, schedulers, deployment
configuration, or TEST/PROD resources.

## 10. Alternatives considered

### 10.1 Pure backend derivation in the existing GET plus overview UI — selected

This uses the canonical anonymous P21-A aggregate already persisted for the
watch, keeps the formula server-side and deterministic, and exposes one
consistent explainable response to the existing protected OS-shell client.
It preserves GET read-only behavior, avoids new storage and routes, makes
corruption handling centralized, and keeps commercial wording and score
semantics out of browser-only code.

### 10.2 Persist a score, band, or message observation/table — rejected

Persisting a second derivative duplicates P21-A facts, produces stale scores
when only the presentation formula changes, introduces new writes, migration
and history semantics, and risks treating commercial copy as an auditable
source event. P21-B has no independent lifecycle that warrants storage.

### 10.3 Calculate the score in the browser from raw metrics — rejected

Browser calculation would duplicate business arithmetic, rounding, validation,
band mapping, and approved copy across clients. It would make the API less
explainable, weaken corruption handling, and allow UI drift while providing no
privacy or latency benefit for five small aggregate components.

### 10.4 Add a Buyer Pressure domain, new score endpoint, or automatic refresh
— rejected

A new domain or route would duplicate the existing Property Watch ownership
and authorization surface. Automatic refresh, polling, or a scheduler would
change P21-A's manual-only collection contract, create writes from a read
flow, and risk collecting data merely because an operator opened a contact.
The existing GET plus explicit P21-A refresh button supplies the needed
experience without those side effects.

## 11. RED--GREEN test design

Tests are written RED before each minimal implementation step and turned GREEN
before proceeding. They use unit seams, imported application routes, and
static/Node checks only; no test accesses TEST or PROD.

### 11.1 Backend score contract

`tests/test_p21b_buyer_pressure_score.py` covers:

1. Exact P21-A canonicalization is called before scoring; extra/missing keys,
   boolean counts, non-finite values, negative values, invalid decimals, and
   blank algorithm versions raise `ValueError`.
2. Every Section 3 cross-field invariant rejects corrupt aggregates; a valid
   zero snapshot produces score zero and five zero-point factors.
3. Volume caps at 10 compatible buyers for 30 points, 5 highly compatible
   buyers for 25 points, and 8 recent compatible buyers for 20 points.
4. Average and maximum quality are zero when null, zero at 55, capped at 15
   and 10 at 100, and use independent Decimal `ROUND_HALF_UP` at fractional
   boundaries.
5. The total clamps 0--100, factor points sum exactly to score, each factor
   has only `code`, `label`, `points`, and `max_points`, and order/labels/codes
   are exactly Section 4.
6. Scores 0, 1, 34, 35, 64, 65, and 100 select the exact band code, label,
   headline, message, and disclaimer. The output contains none of the
   prohibited phrases.
7. Repeated equal input produces byte-for-byte equal dictionaries and the
   pure module performs no database, repository, MATCH, clock, or write call.
8. `score_version` is exactly `buyer-pressure-score-1.0` and remains separate
   from the accepted source `algorithm_version`.

### 11.2 Read-only current-state integration

The same backend test file covers:

1. A watch history containing valid P21-A snapshot/change observations selects
   the latest one in P21-A order, preserves existing P20 fields, retains raw
   `buyer_pressure_metrics`, and adds the exact top-level insight.
2. History with no P21-A Buyer Pressure observation returns both raw metrics
   and insight as null without a write or log.
3. Malformed canonical input and cross-invariant violations return both Buyer
   Pressure fields as null, preserve other state, and log only `stima_id`,
   `watch_id`, and the error class. Captured logs contain no exception text,
   payload, metric, BUY, contact, or PII value.
4. Spies fail if GET invokes any P21-A collector, buyer-input query/store,
   observation insert, P20 collector, MATCH function, or write path.
5. The Pydantic state model accepts the exact valid insight structure and
   rejects extra factor or insight fields.

### 11.3 OS-shell UI contract

`tests/test_p21b_buyer_pressure_ui.py` covers:

1. The overview mount is after Seller Intelligence and before Relazioni
   operative, and the contact view delegates to the dedicated component rather
   than containing a scoring formula or Property Watch business logic.
2. Lazy lead-detail loading follows Contact360 leads to `lead.estimations`,
   filters valid stima IDs, deduplicates, and sorts them numerically ascending.
3. Successful lead details yield deduplicated, numerically ascending known
   stima IDs. A failed watch GET yields only that known stima's exact
   unavailable card and does not prevent other cards from rendering. Failed
   lead details yield the exact partial or total verification warning, never a
   false unavailable stima card or false none message.
4. No linked stima after all successful lead details, absent/null metrics,
   null insight, loading, baseline unavailable, and failed refresh display the
   exact Section 8 copy.
5. A valid card renders only approved aggregate content, exact headline,
   message, disclaimer, ordered factors, all seven exact raw-metric labels and
   format rules, and the P21-A `observed_at` timestamp. It renders no P21-B
   timestamp or individual buyer data.
6. Rendering a card does no POST. A click makes exactly one body-free POST,
   disables the target button while in flight, rejects a double click, and
   handles `written`, `unchanged`, `baseline_unavailable`, and `failed`.
7. `written` and `unchanged` refetch only the target watch/card; all other
   stima caches, cards, lead-detail cache, Seller Intent, timeline, and page
   state remain unchanged.
8. Detached or superseded mounts reject stale results. Dynamic XSS probes in
   every API field are escaped and never become executable markup.
9. Static checks forbid persistent storage, polling, sockets, analytics,
   external communication, automatic POST, individual buyer fields, prohibited
   commercial phrases, and unscoped CSS changes.

### 11.4 Regression gate

After the new tests are green, run these local commands in order:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21b_buyer_pressure_score.py \
  tests/test_p21b_buyer_pressure_ui.py

PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21a_buyer_pressure_metrics.py \
  tests/test_p20b1_internal_signals.py \
  tests/test_p20_property_watch.py

PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p19b_seller_intent_ui.py \
  tests/test_seller_intent_isolation.py \
  tests/test_seller_intent_scoring.py \
  tests/test_seller_intent_router.py \
  tests/test_p17b3_seller_timeline_ui.py \
  tests/test_seller_intelligence_isolation.py \
  tests/test_seller_intelligence_router.py \
  tests/test_next2_router_hardening.py

PYTHONDONTWRITEBYTECODE=1 python -m pytest -q
```

The regression gate proves P21-A canonical metrics and manual POST behavior
remain intact; P20 internal-signal isolation remains intact; P19 Seller Intent
and P17 timeline behavior remain separate; router hardening stays at the
existing protected Property Watch route inventory; and the suite has no
cross-domain regression.

## 12. Acceptance criteria

P21-B is accepted only when all of the following are true:

1. A valid latest P21-A anonymous aggregate derives the exact deterministic
   0--100 score, ordered factors, score version, bands, approved headline,
   message, and disclaimer specified here.
2. The factor-point sum exactly equals the score; the Section 6 example totals
   87 from 30, 25, 18, 6, and 8 points; score is exactly zero without
   compatible buyers; evaluated buyers and budget remain display-only.
3. `GET /api/property-watch/stime/{stima_id}` adds only the optional
   top-level `buyer_pressure_insight` projection and remains read-only: no
   collection, refresh, MATCH, live BUY read, or write occurs.
4. Missing raw metrics yield a null insight; corrupt raw metrics/invariants
   yield graceful null Buyer Pressure fields and minimal safe logging, never a
   leaked payload or PII.
5. The insight has no computed timestamp, while the UI uses only the P21-A
   `metrics.observed_at` timestamp.
6. Contact overview cards derive linked stime IDs only through
   Contact -> Lead -> `lead_stime`, deduplicate/order known IDs ascending,
   isolate failed watch fetches per known stima, and distinguish complete,
   partial, and unavailable lead-detail verification with the exact Section 8
   messages.
7. The UI does not auto-refresh. Its sole write-capable interaction is the
   existing body-free, manual P21-A POST; it is target-scoped and protected
   against repeated clicks.
8. All dynamic UI values are escaped; no persisted cache, polling,
   communication channel, individual buyer data, or prohibited commercial
   wording is introduced.
9. The implementation branch contains exactly the ten Section 9 paths, with
   both documents carried unchanged; specifically no
   repository, router, P21-A collector, migration, scheduler, or other domain
   change is made.
10. The complete focused, adjacent P21-A/P20/P19/P17/router, and full-suite
    local regression gate is green before any later implementation handoff.
