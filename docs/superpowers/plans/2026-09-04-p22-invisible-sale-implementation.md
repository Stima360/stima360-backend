# P22 Invisible Sale Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an ADMIN-only, human-reviewed shortlist of highly compatible BUY requests for a Property Watch home without creating PROPERTY, MATCH, activity, task, NBA, or communication records.

**Architecture:** A pure `property_watch/invisible_sale.py` module adapts the immutable watch baseline and evaluates a privacy-minimized BUY snapshot through `match.engine.calculate`. Dedicated P22 repository and service modules own atomic persistence, concurrency, review state, read models, and batch isolation. The existing Property Watch router/schemas remain narrow integration points and one OS component renders each valuation independently.

**Tech Stack:** Python 3, FastAPI, Pydantic, psycopg2/PostgreSQL, `Decimal`, SHA-256 canonical JSON, vanilla JavaScript, CSS, pytest, Node.

**Spec:** `docs/superpowers/specs/2026-09-04-p22-invisible-sale-design.md`

## Global constraints

- Work only on `stima360-p22-invisible-sale` from `origin/core-0.1-test` commit `5ca8f361fe0e9ffbe4a41fadc1136bd6d22c9c35`.
- Do not create another branch or worktree.
- Do not modify `main.py`, existing migrations, Render configuration, scheduler code, public/seller UI, P20/P21 calculation behavior, BUY writers, PROPERTY writers, or MATCH persistence.
- Call `match.engine.calculate` directly; never call MATCH writers or write `matches` or `match_runs`.
- Store/expose no names, email, phone, notes, criteria values, or free-text MATCH explanations.
- All P22 writes for one watch use one transaction and lock order: watch → opportunity → candidates by BUY ID.
- All mutation routes are ADMIN-only, body-free, and derive relationships server-side.
- `closed` is terminal.
- Do not access TEST or PROD.
- Do not stage, commit, push, merge, open a PR, or deploy. Giorgio does these after review.

## Approved file map

| Path | Responsibility |
| --- | --- |
| `docs/superpowers/specs/2026-09-04-p22-invisible-sale-design.md` | Approved design |
| `docs/superpowers/plans/2026-09-04-p22-invisible-sale-implementation.md` | This plan |
| `migrations/023_invisible_sale.sql` | Additive schema |
| `migrations/023_invisible_sale_down.sql` | P22-only rollback |
| `property_watch/invisible_sale.py` | Pure calculation/canonicalization |
| `property_watch/invisible_sale_repository.py` | Snapshots, read model, transactions |
| `property_watch/invisible_sale_service.py` | Collection, batch, review, logging |
| `property_watch/router.py` | Six ADMIN endpoints |
| `property_watch/schemas.py` | Strict response models |
| `static/os_shell/assets/components/invisible-sale.js` | P22 UI component |
| `static/os_shell/assets/views/contatto-dettaglio.js` | Lazy mount |
| `static/os_shell/assets/app.css` | Scoped styles |
| `tests/test_p22_invisible_sale.py` | Backend/migration/concurrency/API tests |
| `tests/test_p22_invisible_sale_ui.py` | UI tests |
| `tests/test_next2_router_hardening.py` | Protected-router inventory |

---

### Task 1: Migration contract

**Files:**
- Create: `migrations/023_invisible_sale.sql`
- Create: `migrations/023_invisible_sale_down.sql`
- Create: `tests/test_p22_invisible_sale.py`

**Interfaces:**
- Consumes: `property_watches.id`, `buy_requests.id`.
- Produces: opportunities, candidates, events with the constraints in the spec.

- [ ] **Step 1: Verify branch/base/scope**

```bash
git fetch origin
git branch --show-current
git rev-parse HEAD
git rev-parse origin/core-0.1-test
git status --short
```

Expected: branch `stima360-p22-invisible-sale`; both SHAs `5ca8f361fe0e9ffbe4a41fadc1136bd6d22c9c35`; only the approved spec may be untracked.

- [ ] **Step 2: Write RED migration tests**

```python
def test_p22_migration_has_required_constraints():
    sql = MIGRATION.read_text()
    assert "UNIQUE (watch_id)" in sql
    assert "UNIQUE (opportunity_id, buy_request_id)" in sql
    assert "UNIQUE (idempotency_key)" in sql
    assert "CHECK (revision >= 0)" in sql
    assert "CHECK (decision_version >= 0)" in sql


def test_p22_down_drops_only_p22_tables_in_reverse_order():
    sql = DOWN_MIGRATION.read_text()
    assert sql.index("invisible_sale_events") < sql.index(
        "invisible_sale_candidates"
    ) < sql.index("invisible_sale_opportunities")
```

- [ ] **Step 3: Run RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests/test_p22_invisible_sale.py -k migration
```

Expected: FAIL because the migrations are absent.

- [ ] **Step 4: Create the migration pair**

Create all three tables, exact status checks, non-negative revision/version/count checks, SHA-256 length checks, restrictive foreign keys, unique constraints, UTC timestamps, and indexes for watch lookup, candidate state/BUY lookup, and event chronology. The down file drops events → candidates → opportunities. Do not alter existing tables.

- [ ] **Step 5: Run GREEN**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests/test_p22_invisible_sale.py -k migration
git diff --check
```

---

### Task 2: Pure baseline, candidate, and digest logic

**Files:**
- Create: `property_watch/invisible_sale.py`
- Modify: `tests/test_p22_invisible_sale.py`

**Interfaces produced:**

```python
P22_ALGORITHM_VERSION = "invisible-sale-1.0"
build_ephemeral_property(baseline: dict) -> dict
calculate_candidates(buys: list[dict], ephemeral_property: dict) -> list[dict]
canonicalize_candidate(candidate: dict) -> dict
candidate_digest(candidate: dict) -> str
candidate_set_digest(candidates: list[dict]) -> str
```

- [ ] **Step 1: Write and run RED baseline tests**

Test exact five-field mapping; stripped text; finite positive `mq`/`price_exact`; and every missing, blank, zero, negative, NaN, and infinite value.

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests/test_p22_invisible_sale.py -k baseline
```

Expected: import/collection FAIL because the module is absent.

- [ ] **Step 2: Implement baseline validation**

Use `Decimal(str(value))`, require `is_finite()` and `> 0`, and return only `city`, `microzone`, `property_type`, `surface_sqm`, and `asking_price`.

- [ ] **Step 3: Write RED MATCH boundary tests**

```python
@pytest.mark.parametrize(
    ("score", "hard_fails", "compatibility", "included"),
    [
        ("79.99", 0, "compatible", False),
        ("80.00", 0, "compatible", True),
        ("99.00", 1, "incompatible", False),
        ("87.50", 0, "exception", True),
    ],
)
def test_candidate_threshold(score, hard_fails, compatibility, included, monkeypatch):
    monkeypatch.setattr(
        match_engine,
        "calculate",
        lambda *_: engine_result(score, hard_fails, compatibility),
    )
    result = invisible_sale.calculate_candidates([eligible_buy()], ephemeral_property())
    assert bool(result) is included
```

Also test non-mapping output, non-finite/out-of-range score, invalid compatibility, invalid hard-fail count, blank algorithm version, malformed criteria, and one BUY exception aborting the whole calculation.

- [ ] **Step 4: Write RED privacy/canonicalization tests**

Assert reason codes come only from the fixed group enum; blocking, `not_applicable`, `not_available`, and score below 85 are excluded; arbitrary feature codes and free text never survive; activity uses the UTC maximum; budget fallback is target → max → min; signed zero becomes `0.00`.

- [ ] **Step 5: Write RED order/digest tests**

Assert score DESC, activity DESC, BUY ID ASC; canonical equivalents hash equally; score/reason/activity/budget/MATCH-version changes hash differently; empty digest is stable; review status and collection time do not affect it.

- [ ] **Step 6: Implement the pure module**

```python
SCORE_QUANTUM = Decimal("0.01")
REASON_CODE_ORDER = (
    "location", "budget", "typology", "dimensions",
    "rooms", "features", "condition",
)


def _sha256(value: dict) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()
```

Validate the current MATCH result shape strictly. Never retain explanations, strengths, warnings, blocking reasons, requested/property values, group scores, or raw criteria.

- [ ] **Step 7: Run GREEN**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests/test_p22_invisible_sale.py -k "baseline or candidate or threshold or malformed or reason or digest"
```

---

### Task 3: Read-only watch and BUY snapshots

**Files:**
- Create: `property_watch/invisible_sale_repository.py`
- Modify: `tests/test_p22_invisible_sale.py`

**Interfaces produced:**

```python
get_watch_and_baseline_for_stima(stima_id: int) -> dict | None
list_eligible_buy_snapshot() -> list[dict]
list_active_watch_refs() -> list[dict]
get_invisible_sale_for_stima(stima_id: int) -> dict
```

- [ ] **Step 1: Write repository RED tests**

With fake cursors/connections prove: earliest immutable `watch_started`; one consistent read-only BUY snapshot; nested locations/typologies/features/latest interaction; active-watch order; and no INSERT/UPDATE/DELETE or writer calls.

- [ ] **Step 2: Run RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests/test_p22_invisible_sale.py -k "snapshot or readonly or eligible_buy"
```

- [ ] **Step 3: Implement snapshots**

Read only fields required by `buy_readiness`, MATCH, activity, and budget. Never join contact/profile/notes tables. Rebuild nested relation arrays deterministically and call `buy_readiness(...)["can_match"]` before MATCH.

- [ ] **Step 4: Implement P22 read model**

Return `not_collected|ready|empty|closed`, current count, algorithm/version/time, and minimized candidates. Current candidates come first in calculation order; stale candidates follow by updated time DESC then BUY ID ASC.

- [ ] **Step 5: Run GREEN**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests/test_p22_invisible_sale.py -k "snapshot or readonly or eligible_buy or read_model"
```

---

### Task 4: Atomic refresh persistence and concurrency

**Files:**
- Modify: `property_watch/invisible_sale_repository.py`
- Modify: `tests/test_p22_invisible_sale.py`

**Interface produced:**

```python
persist_invisible_sale_refresh(
    watch_id: int,
    candidates: list[dict],
    digest: str,
    evaluated_at: datetime,
) -> dict
```

- [ ] **Step 1: Write RED state-transition tests**

Cover first `empty`, first `ready`, pending creation, approved/rejected preservation, absent→stale, stale→pending, count excluding stale, unchanged digest with zero writes, and terminal closed with zero writes.

- [ ] **Step 2: Write RED lock/rollback tests**

Record SQL and assert this order:

```text
property_watches FOR UPDATE
opportunity INSERT ON CONFLICT when absent
opportunity FOR UPDATE
candidates FOR UPDATE in buy_request_id order
writes
one commit
```

Any SQL error must produce one rollback and no independent nested transaction.

- [ ] **Step 3: Write deterministic RED concurrency tests**

Use barriers/events, never `sleep`. Cover two first collectors racing through exact `ON CONFLICT (watch_id) DO NOTHING` recovery; two equivalent pre-lock calculations producing one write; and a concurrent close producing zero refresh writes. Bound test lock acquisition so a failure cannot hang pytest.

- [ ] **Step 4: Write RED audit-cycle tests**

Digest sequence A→B→A must create revisions 1, 2, 3 with distinct event keys. Discovered/stale events include revision. Event conflict recovery must reread the locked state and never create a duplicate or partial write.

- [ ] **Step 5: Implement atomic persistence**

Cursor helpers remain transaction-neutral. The public repository function owns one connection, one commit, and rollback on exception. Lock watch first, create/recover opportunity, lock opportunity, reread state, return on closed/unchanged, increment revision once, lock candidates by BUY ID, apply all candidate/event changes, then commit.

- [ ] **Step 6: Run GREEN**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests/test_p22_invisible_sale.py -k "transition or unchanged or closed or lock or concurrent or conflict or revision"
```

---

### Task 5: Human decisions and terminal close

**Files:**
- Modify: `property_watch/invisible_sale_repository.py`
- Modify: `tests/test_p22_invisible_sale.py`

**Interfaces produced:**

```python
set_candidate_review_status(
    stima_id: int,
    buy_request_id: int,
    target_status: Literal["approved", "rejected"],
) -> dict
close_invisible_sale_for_stima(stima_id: int) -> dict
```

- [ ] **Step 1: Write RED decision tests**

Cover pending→approved, approved→rejected, rejected→approved, same-state retry, stale 409, closed 409, candidate from another opportunity 404, and missing candidate/opportunity/watch 404.

- [ ] **Step 2: Write RED decision-cycle tests**

Approve→reject→approve must produce decision versions 1, 2, 3 and distinct keys. A repeated same-state decision keeps its version and performs zero writes.

- [ ] **Step 3: Write RED close tests**

First close updates only the opportunity and appends one event. Repeated close returns HTTP-compatible current state with zero writes; it never rewrites candidate review states.

- [ ] **Step 4: Implement decision transactions**

Resolve `stima_id → watch → opportunity → candidate` server-side. Reuse global lock order, verify the BUY belongs to that opportunity, increment `decision_version` only for a real transition, insert one versioned event, and commit once.

- [ ] **Step 5: Run GREEN**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests/test_p22_invisible_sale.py -k "approve or reject or decision or close"
```

---

### Task 6: Strict/safe collector and ordered batch

**Files:**
- Create: `property_watch/invisible_sale_service.py`
- Modify: `tests/test_p22_invisible_sale.py`

**Interfaces produced:**

```python
collect_invisible_sale_for_stima(stima_id: int) -> dict
safe_collect_invisible_sale_for_stima(stima_id: int) -> dict
collect_invisible_sale_for_active_watches() -> dict
get_invisible_sale_for_stima(stima_id: int) -> dict
approve_invisible_sale_candidate(stima_id: int, buy_request_id: int) -> dict
reject_invisible_sale_candidate(stima_id: int, buy_request_id: int) -> dict
close_invisible_sale(stima_id: int) -> dict
```

- [ ] **Step 1: Write strict collector RED tests**

Assert incomplete baseline returns `baseline_unavailable` before BUY/MATCH/persistence; eligible BUYs use existing readiness; zero eligible BUYs persists empty; failure on any BUY produces no P22 writes; controlled route errors remain exceptions; no MATCH/BUY/PROPERTY/activity/task/NBA writer is invoked.

- [ ] **Step 2: Write safe collector privacy RED tests**

Capture logs. Put names, email, phone, notes, criteria, and engine text into failing input and assert logs contain only permitted IDs and `type(exc).__name__`, never exception text or payloads.

- [ ] **Step 3: Write batch RED tests**

Use watch IDs `[7, 9, 10]`, fail watch 9, and require outcomes for all three in order plus:

```python
assert result["totals"] == {
    "written": 1,
    "unchanged": 1,
    "baseline_unavailable": 0,
    "closed": 0,
    "failed": 1,
}
```

- [ ] **Step 4: Implement orchestration**

Calculate before calling atomic persistence. Use no batch-wide transaction. Safe collection catches per-watch failures and logs only IDs/classification. Review functions delegate to repository transactions without accepting browser-supplied relationship data.

- [ ] **Step 5: Run GREEN**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests/test_p22_invisible_sale.py -k "collector or batch or log or no_side_effect"
```

---

### Task 7: Strict schemas and ADMIN/body-free routes

**Files:**
- Modify: `property_watch/schemas.py`
- Modify: `property_watch/router.py`
- Modify: `tests/test_p22_invisible_sale.py`
- Modify: `tests/test_next2_router_hardening.py`

**Interfaces:** the six routes in the spec, backed only by Task 6 service functions.

- [ ] **Step 1: Write schema RED tests**

Validate not-collected, ready, empty, closed, refresh, batch, and review fixtures. Require `extra="forbid"`, exact enums, safe Decimal serialization, fixed reason-code enum, and no PII fields.

- [ ] **Step 2: Write route RED tests through `TestClient`**

For all six routes assert unauthenticated 401 and authenticated behavior.
Assert POST operations expose no OpenAPI `requestBody`, declare no body model,
and pass only server-derived path/database values to services. An extraneous
HTTP body must never influence service arguments. Assert controlled 404/409/400
conditions never become 500.

- [ ] **Step 3: Write GET read-only guard**

Monkeypatch every P22 collector/review/write function to raise, then GET not-collected, ready, empty, and closed fixtures successfully.

- [ ] **Step 4: Implement schemas and endpoints**

Add P22 response classes only. Reuse the router-wide ADMIN dependency already applied in `main.py`; do not modify `main.py`. Declare no body parameter and map only controlled domain errors.

- [ ] **Step 5: Extend router inventory**

Add all six paths/methods to `tests/test_next2_router_hardening.py` and prove none is duplicated or exposed outside the protected router.

- [ ] **Step 6: Run GREEN**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests/test_p22_invisible_sale.py -k "schema or route or auth or body or get_readonly"
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests/test_next2_router_hardening.py
```

---

### Task 8: Isolated OS component

**Files:**
- Create: `static/os_shell/assets/components/invisible-sale.js`
- Modify: `static/os_shell/assets/views/contatto-dettaglio.js`
- Modify: `static/os_shell/assets/app.css`
- Create: `tests/test_p22_invisible_sale_ui.py`

**Interfaces produced:**

```javascript
export async function loadInvisibleSale(stimaId)
export function renderInvisibleSale(container, state, options)
export async function refreshInvisibleSale(stimaId)
export async function reviewInvisibleSaleCandidate(stimaId, buyRequestId, decision)
export function mountInvisibleSale(container, linkedStimaIds, mountToken)
```

- [ ] **Step 1: Write UI RED tests**

Cover title/allowed fields/states, API order, numeric BUY links, exact body-free POST calls, target-only refetch, disabled in-flight buttons, duplicate-click suppression, stale-mount protection, per-valuation failure isolation, absence of polling/storage/contact/task/NBA calls, and hostile dynamic strings rendered as inert text.

- [ ] **Step 2: Run RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests/test_p22_invisible_sale_ui.py
```

Expected: FAIL because the component is absent.

- [ ] **Step 3: Implement the component**

Use DOM creation and `textContent` for every dynamic value; never interpolate API data into HTML. Keep request state per valuation, use `Promise.allSettled` for linked valuations, and reject stale mount results.

- [ ] **Step 4: Mount without disturbing P21-B**

Import and mount P22 only after linked `stima_id` values resolve. Preserve contact overview, timeline, Seller Intent, Property Watch, and Buyer Pressure behavior.

- [ ] **Step 5: Add scoped styles**

Prefix selectors with `.invisible-sale`. Do not add global resets or unrelated visual changes.

- [ ] **Step 6: Run GREEN and syntax checks**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests/test_p22_invisible_sale_ui.py
node --check static/os_shell/assets/components/invisible-sale.js
node --check static/os_shell/assets/views/contatto-dettaglio.js
```

---

### Task 9: Regression and final handoff

**Files:** modify only an approved P22 test file if a fresh failure proves a missing in-scope regression.

- [ ] **Step 1: Run focused P22 gates**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests/test_p22_invisible_sale.py tests/test_p22_invisible_sale_ui.py
node --check static/os_shell/assets/components/invisible-sale.js
node --check static/os_shell/assets/views/contatto-dettaglio.js
```

- [ ] **Step 2: Run P20/P21 regressions**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21a_buyer_pressure_metrics.py \
  tests/test_p21b_buyer_pressure_score.py \
  tests/test_p21b_buyer_pressure_ui.py \
  tests/test_p20b1_internal_signals.py \
  tests/test_p20_property_watch.py
```

- [ ] **Step 3: Run MATCH/BUY/P17/P19/router regressions**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_match_engine.py \
  tests/test_next5_p1c_match_readiness.py \
  tests/test_next2_router_hardening.py \
  tests/test_p17b3_seller_timeline_ui.py \
  tests/test_seller_intelligence_isolation.py \
  tests/test_p19b_seller_intent_ui.py \
  tests/test_seller_intent_isolation.py
```

If an exact filename differs, use `rg --files tests` to select the repository’s existing equivalent; do not omit that regression area.

- [ ] **Step 4: Run the full suite**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q
```

Expected: exit 0. Report exact pass/skip/warning totals.

- [ ] **Step 5: Prove forbidden writes and PII are absent**

```bash
rg -n "INSERT INTO (matches|match_runs|buy_requests|properties)|UPDATE (matches|match_runs|buy_requests|properties)|DELETE FROM (matches|match_runs|buy_requests|properties)" \
  property_watch/invisible_sale.py \
  property_watch/invisible_sale_repository.py \
  property_watch/invisible_sale_service.py

rg -n "email|phone|telefono|nome|cognome|notes|note|blocking_reasons|strengths|warnings|requested_value|property_value" \
  property_watch/invisible_sale.py \
  property_watch/invisible_sale_repository.py \
  property_watch/invisible_sale_service.py \
  static/os_shell/assets/components/invisible-sale.js
```

Expected: first search has no matches. Any second-search match must be a deny-list test or fixed non-dynamic label, never stored/exposed data.

- [ ] **Step 6: Verify exact scope**

```bash
git diff --check
git status --short
git diff --stat
git diff --name-only
git ls-files --others --exclude-standard
```

Expected: only the fifteen approved paths; nothing staged.

- [ ] **Step 7: Stop for Giorgio’s review**

Report branch/base SHA, exact paths, RED/GREEN evidence, test totals, Node results, migration impact, privacy/forbidden-write searches, diff check, status, and residual problems. Do not commit, push, merge, deploy, or access TEST/PROD.
