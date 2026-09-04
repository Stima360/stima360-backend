# P21-B Buyer Pressure Score and UI Implementation Plan

> **For the implementation worker:** execute this plan only on branch
> `stima360-p21b-buyer-pressure-score-ui`, based on
> `origin/core-0.1-test` at exactly
> `a148c66c37aa6ef64b95a6a18c5cba07c306a3ef`.

**Goal:** Derive a deterministic, explainable Buyer Pressure score from the
anonymous persisted P21-A aggregate and present it in each linked valuation
card on the OS-shell contact overview.

**Architecture:** `property_watch/buyer_pressure_score.py` is a pure,
no-I/O scorer over P21-A-canonical metrics. `property_watch/service.py`
derives the optional insight only while composing the existing read-only watch
GET state. A dedicated OS-shell component lazily resolves known linked stime
IDs, reads each watch independently, and uses the existing manual P21-A POST
only after an explicit operator click.

**Specification:**
`docs/superpowers/specs/2026-09-03-p21b-buyer-pressure-score-ui-design.md`

## Non-negotiable boundaries

- Preserve the P21-A collector, persisted observations, canonicalization,
  idempotency, raw metrics, manual routes, and all normal MATCH behavior.
- `GET /api/property-watch/stime/{stima_id}` is read-only. It must not
  collect, refresh, call MATCH, read live BUY data, create an observation, or
  write.
- Create no score persistence, table, migration, route, router change,
  scheduler, polling loop, external call, current-time field, or automated
  refresh.
- Do not modify `property_watch/repository.py`,
  `property_watch/router.py`, `property_watch/buyer_pressure.py`,
  `property_watch/__init__.py`, `main.py`, P21-A tests, Seller Intent, Seller
  Intelligence, MATCH, BUY, PROPERTY, CRM, FOLLOWUP, deployment, or
  configuration.
- Do not access TEST or PROD. Do not stage, commit, push, create a pull
  request, merge, or deploy. Giorgio reviews the completed branch manually.
- Both documentation files below travel unchanged. The resulting branch
  contains exactly these ten paths and no other changed path:

| Path | Action |
| --- | --- |
| `docs/superpowers/specs/2026-09-03-p21b-buyer-pressure-score-ui-design.md` | Carry unchanged |
| `docs/superpowers/plans/2026-09-03-p21b-buyer-pressure-score-ui-implementation.md` | Carry unchanged |
| `property_watch/buyer_pressure_score.py` | Create |
| `property_watch/service.py` | Modify |
| `property_watch/schemas.py` | Modify |
| `static/os_shell/assets/components/buyer-pressure.js` | Create |
| `static/os_shell/assets/views/contatto-dettaglio.js` | Modify |
| `static/os_shell/assets/app.css` | Modify |
| `tests/test_p21b_buyer_pressure_score.py` | Create |
| `tests/test_p21b_buyer_pressure_ui.py` | Create |

## Fixed score and response contract

The only public scorer is:

```python
def derive_buyer_pressure_insight(metrics: dict) -> dict:
    ...
```

It first invokes `property_watch.buyer_pressure.canonicalize_metrics(metrics)`,
then validates:

```text
0 <= highly_compatible_buyers <= compatible_buyers <= evaluated_buyers
0 <= recent_compatible_buyers_30d <= compatible_buyers
compatible_buyers == 0 => average_match_score, maximum_match_score,
                          average_budget are all null
compatible_buyers > 0 => average_match_score and maximum_match_score are
                         non-null Decimal values in [55.00, 100.00], with
                         maximum_match_score >= average_match_score
average_budget is null or a finite non-negative canonical Decimal
```

It raises `ValueError` for every invalid metric or relationship and has no
clock, database, repository, MATCH, or exception-catching fallback.

Use `Decimal` only. Independently round every component to an integer using
`quantize(Decimal("1"), rounding=ROUND_HALF_UP)`, sum components, and clamp
the final total to 0--100:

| Code | Label | Formula | Maximum |
| --- | --- | --- | ---: |
| `compatible_volume` | `Buyer compatibili` | `min(compatible_buyers, 10) / 10 * 30` | 30 |
| `highly_compatible_volume` | `Buyer altamente compatibili` | `min(highly_compatible_buyers, 5) / 5 * 25` | 25 |
| `recent_compatible_activity` | `Buyer compatibili attivi negli ultimi 30 giorni` | `min(recent_compatible_buyers_30d, 8) / 8 * 20` | 20 |
| `average_match_quality` | `Qualità media dei match compatibili` | `clamp((average_match_score - 55) / 45, 0, 1) * 15`, null is zero | 15 |
| `maximum_match_quality` | `Migliore match compatibile` | `clamp((maximum_match_score - 55) / 45, 0, 1) * 10`, null is zero | 10 |

When compatible buyers equal zero, the score and all five factor points are
exactly zero. `evaluated_buyers` and `average_budget` are display-only and
receive no points. Factor objects contain exactly `code`, `label`, `points`,
and `max_points`, in the table order; their points sum exactly to score.

The result shape is exactly:

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
    {"code": "compatible_volume", "label": "Buyer compatibili", "points": 30, "max_points": 30},
    {"code": "highly_compatible_volume", "label": "Buyer altamente compatibili", "points": 25, "max_points": 25},
    {"code": "recent_compatible_activity", "label": "Buyer compatibili attivi negli ultimi 30 giorni", "points": 18, "max_points": 20},
    {"code": "average_match_quality", "label": "Qualità media dei match compatibili", "points": 6, "max_points": 15},
    {"code": "maximum_match_quality", "label": "Migliore match compatibile", "points": 8, "max_points": 10}
  ]
}
```

The shown source metrics are:

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

The exact bands and commercial copy are:

| Score | Code | Label | Headline | Message |
| ---: | --- | --- | --- | --- |
| 0 | `none` | `Nessuna domanda rilevata` | `NESSUNA DOMANDA RILEVATA — 0/100` | `Al momento non risultano richieste BUY compatibili con i dati disponibili.` |
| 1--34 | `low` | `Domanda bassa` | `DOMANDA BASSA — {score}/100` | `Nel database STIMA360 risultano alcune compatibilità, ma la pressione della domanda è ancora limitata.` |
| 35--64 | `medium` | `Domanda media` | `DOMANDA MEDIA — {score}/100` | `Nel database STIMA360 è presente una domanda concreta per immobili con caratteristiche simili.` |
| 65--100 | `high` | `Domanda alta` | `DOMANDA ALTA — {score}/100` | `Nel database STIMA360 è presente una domanda elevata di acquirenti compatibili per immobili con caratteristiche simili.` |

Every valid insight uses:

```text
Indicatore interno basato su richieste BUY attive e criteri MATCH; non garantisce la vendita né l’interesse per lo specifico immobile.
```

The implementation must never display or imply:

```text
abbiamo già il compratore
questi acquirenti vogliono il tuo immobile
vendita garantita
prezzo garantito
acquirenti pronti ad acquistare
```

## Task 1: Establish pure score tests

**Files:**

- Create: `tests/test_p21b_buyer_pressure_score.py`

- [ ] Write RED tests for `derive_buyer_pressure_insight(metrics: dict) -> dict`.
  Use a complete P21-A-shaped metrics fixture and assert the exact Section
  score/result dictionary for 13 compatible, 5 highly compatible, 7 recent,
  72.35 average, and 91.40 maximum: components are 30, 25, 18, 6, 8 and the
  score is 87.
- [ ] Add parametrized RED tests for scores at the exact band edges 0, 1, 34,
  35, 64, 65, and 100. Assert the exact code, label, headline, message, and
  common disclaimer.
- [ ] Add RED tests proving independent `ROUND_HALF_UP` components at
  half-boundaries, all three count caps, quality null-to-zero behavior, total
  clamp, exact factor key sets/order, and factor sum equal to final score.
- [ ] Add RED tests for P21-A canonical metric rejection and every cross-field
  invariant. Assert no database/repository/MATCH/clock call is reachable from
  the scorer.
- [ ] Run the focused RED command:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21b_buyer_pressure_score.py -k 'derive or score or band'
```

- [ ] Create `property_watch/buyer_pressure_score.py` with constants for the
  score version, factor definitions, bands, approved copy, and the pure
  signature. Reuse P21-A `canonicalize_metrics`; do not add an import or
  public symbol to P21-A.
- [ ] Implement Decimal component calculation, invariant validation, exact
  factors, band mapping, and response construction with no I/O.
- [ ] Run the same focused command GREEN, then run:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21b_buyer_pressure_score.py
```

## Task 2: Add read-only GET insight derivation

**Files:**

- Modify: `property_watch/service.py`
- Modify: `property_watch/schemas.py`
- Modify: `tests/test_p21b_buyer_pressure_score.py`

- [ ] Add RED service tests with ordered P21-A observations. Assert the latest
  valid snapshot/change retains the existing raw `buyer_pressure_metrics` and
  adds the exact top-level `buyer_pressure_insight` sibling.
- [ ] Add RED tests where no Buyer Pressure observation produces both fields as
  null without logging a corruption error.
- [ ] Add RED tests for malformed canonical P21-A payload and invalid
  cross-field relationships. Assert `buyer_pressure_metrics` and
  `buyer_pressure_insight` become null; all baseline, P20, observation list,
  and computed-state fields remain intact; the log exposes only `stima_id`,
  `watch_id` when known, and `type(exc).__name__`, never payload, values,
  exception text, BUY data, contact data, or PII.
- [ ] Add RED spy tests that fail if GET calls any P21-A collection function,
  input/store repository function, insert, P20 collector, MATCH calculation,
  or write boundary.
- [ ] Add RED schema tests for strict `BuyerPressureFactor` and
  `BuyerPressureInsight` models. Assert factor/insight extra keys are rejected
  and `PropertyWatchState.buyer_pressure_insight` accepts only the exact
  optional shape.
- [ ] Run the focused RED command:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21b_buyer_pressure_score.py -k 'current_state or schema or corrupt or read_only'
```

- [ ] Import only `buyer_pressure_score` into `property_watch/service.py`.
  After the existing P21-A latest-observation selection, project exactly the
  eight P21-A keys, derive the insight, and return it top-level. Wrap only
  canonicalization/derivation `ValueError` in a narrow corruption boundary
  that logs the permitted identifiers/classification and returns both Buyer
  Pressure fields as null.
- [ ] Add strict Pydantic factor and insight models to
  `property_watch/schemas.py`, then add the optional state field. Do not alter
  router declarations or route counts.
- [ ] Run the focused command GREEN, then:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21b_buyer_pressure_score.py \
  tests/test_p21a_buyer_pressure_metrics.py \
  tests/test_p20b1_internal_signals.py \
  tests/test_p20_property_watch.py
```

## Task 3: Build the isolated Buyer Pressure component

**Files:**

- Create: `static/os_shell/assets/components/buyer-pressure.js`
- Create: `tests/test_p21b_buyer_pressure_ui.py`

- [ ] Write RED static/Node tests for component exports and a dedicated
  per-contact cache containing known stima results and in-flight promises.
  The component uses existing `apiGet`, body-free `apiPost`, `escapeHtml`, and
  `formatDateTime`; it does not import a scoring module or duplicate backend
  formula/bands/copy.
- [ ] Add RED executable tests for fulfilled lead details:
  extract valid numeric `lead.estimations[].stima_id`, deduplicate them, order
  them ascending, and issue one `GET /api/property-watch/stime/{stima_id}` per
  known ID through `Promise.allSettled`.
- [ ] Add RED executable tests for lead-resolution states:
  all fulfilled details without stime IDs render exactly `Nessuna stima
  collegata per il calcolo della domanda buyer.`; a mixture of failed details
  and known IDs renders exactly `Non è stato possibile verificare tutte le
  stime collegate.` plus known cards; all detail failures/no known IDs render
  exactly `Non è stato possibile verificare le stime collegate.`. Failed lead
  details never create imaginary stima cards.
- [ ] Add RED tests that a failed GET for a known stima renders only
  `Domanda buyer non disponibile per la stima #{stima_id}.` for that card and
  leaves other fulfilled cards intact.
- [ ] Run the focused RED command:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21b_buyer_pressure_ui.py -k 'lead or watch_get or unavailable'
```

- [ ] Implement the component's validated ID extraction, `Promise.allSettled`
  lead/watch loading, in-memory cache, and render helpers. Keep all state
  local to the current contact-detail render; do not use persistent storage,
  polling, sockets, analytics, or external communication.
- [ ] Implement the exact section/card state copy. A valid card renders the
  headline, `{score}/100`, band label, exact message/disclaimer, five
  server-provided factors, and these exact raw-metric labels:
  `Buyer valutati`, `Buyer compatibili`, `Buyer altamente compatibili`,
  `Buyer compatibili recenti (30 giorni)`, `Score medio MATCH`, `Score massimo
  MATCH`, `Budget medio`.
- [ ] Format the four raw counts as integers; null average/maximum as `—`,
  otherwise `it-IT` two decimals with `/100`; null budget as `—`, otherwise
  EUR `it-IT` two decimals. The raw metrics remain separate from factors and
  receive no points. Use only `metrics.observed_at` with `formatDateTime()`;
  create no P21-B timestamp.
- [ ] Run the focused command GREEN.

## Task 4: Delegate from the contact overview and style narrowly

**Files:**

- Modify: `static/os_shell/assets/views/contatto-dettaglio.js`
- Modify: `static/os_shell/assets/app.css`
- Modify: `tests/test_p21b_buyer_pressure_ui.py`

- [ ] Write RED source tests that the overview mount appears after Seller
  Intelligence and before Relazioni operative. Assert the view imports and
  delegates to `buyer-pressure.js`, reuses lazy lead details/cache, and does
  not contain score arithmetic, band selection, commercial copy, or direct
  Property Watch request construction.
- [ ] Write RED Node tests for valid card markup and each exact UI state:
  absent/null metrics or insight (`Domanda buyer non ancora calcolata.`),
  in-flight (`Calcolo domanda buyer in caricamento…`), unavailable baseline
  (`Dati della stima insufficienti per calcolare la domanda buyer.`), and
  failed refresh (`Impossibile aggiornare la domanda buyer. Riprova.`).
- [ ] Write RED tests that rendering makes no POST. A click invokes exactly
  one `POST /api/property-watch/stime/{stima_id}/buyer-pressure/refresh` with
  no body, disables the target button while in flight, and defeats a double
  click through a per-stima in-flight map.
- [ ] Write RED tests for `written` and `unchanged`: invalidate/refetch only
  that stima's GET/card, preserving every other card, watch cache, lead-detail
  cache, Contact360 response, Seller Intent, timeline, and page state. Assert
  `baseline_unavailable` and `failed` do not invalidate unrelated cards.
- [ ] Write RED stale-mount and XSS tests. Only a connected mount with the
  current request token updates; probes in every dynamic ID, label, message,
  factor, timestamp, error, and raw metric are escaped through `escapeHtml`.
- [ ] Run the focused RED command:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21b_buyer_pressure_ui.py -k 'overview or refresh or stale or xss or format'
```

- [ ] Insert only the delegated mount/cache wiring in
  `contatto-dettaglio.js`. Keep the view limited to placement, local cache,
  lazy lead-detail delegation, and mount lifecycle.
- [ ] Add only `buyer-pressure-*` selectors in `app.css`, following current
  card/grid/detail/muted/error/disabled patterns. Do not modify global,
  Seller Intent, timeline, table, or existing contact selectors.
- [ ] Implement manual POST behavior in the component with bodyless
  `apiPost(path)` semantics, target-only cache invalidation, and exact outcome
  rendering.
- [ ] Run the focused command GREEN, then syntax-check the changed modules:

```bash
node --check static/os_shell/assets/components/buyer-pressure.js
node --check static/os_shell/assets/views/contatto-dettaglio.js
```

## Task 5: Run the complete local regression gate

**Files:** no further edits unless a failing in-scope P21-B contract requires
the smallest correction in one of the eight implementation files.

- [ ] Confirm the working tree names exactly the ten permitted paths and the
  two documentation files are unchanged.
- [ ] Run focused P21-B tests:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21b_buyer_pressure_score.py \
  tests/test_p21b_buyer_pressure_ui.py
```

- [ ] Run P21-A/P20 regression:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21a_buyer_pressure_metrics.py \
  tests/test_p20b1_internal_signals.py \
  tests/test_p20_property_watch.py
```

- [ ] Run P19/P17/router regression:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p19b_seller_intent_ui.py \
  tests/test_seller_intent_isolation.py \
  tests/test_seller_intent_scoring.py \
  tests/test_seller_intent_router.py \
  tests/test_p17b3_seller_timeline_ui.py \
  tests/test_seller_intelligence_isolation.py \
  tests/test_seller_intelligence_router.py \
  tests/test_next2_router_hardening.py
```

- [ ] Run the full suite:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q
```

- [ ] Perform only local final checks:

```bash
git diff --check
git status --short
git diff --stat
git diff --name-only
```

- [ ] Stop uncommitted for Giorgio's manual review. Do not stage, commit,
  push, create a pull request, merge, deploy, or access TEST/PROD.

## Completion conditions

1. The branch starts from the exact required source revision and contains
   exactly the ten listed P21-B paths.
2. The two documentation paths remain unchanged during implementation.
3. The pure scorer produces only the exact deterministic score shape and
   approved language; the Section score example is 87 with factors
   30, 25, 18, 6, and 8.
4. GET derives the optional insight from persisted P21-A aggregates only and
   remains read-only under success, null state, and corrupt-observation paths.
5. The UI resolves only known linked stime IDs, distinguishes none/partial/all
   lead verification failure exactly, isolates watch failures, and never
   auto-refreshes.
6. The manual bodyless P21-A refresh is double-click safe, stale-mount safe,
   target-scoped, and does not invalidate unrelated UI state.
7. Dynamic content is escaped; no individual buyer data, persistence,
   communication channel, prohibited language, route, persistence, migration,
   scheduler, or cross-domain change is introduced.
8. All four regression suites complete locally before the manual review
   handoff.
