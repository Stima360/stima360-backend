# P26-1 Agency Identity & CORE Agency Scope — Design

## Document status

- Phase: `P26-1` — **design only**.
- This document is a **specification**. It authorises no code, no migration, no
  script, no test, no commit and no deployment. Implementation belongs to a
  later, separately approved phase.
- Base branch observed during the audit: `core-0.1-test`, `HEAD 05387fa`
  (`P26-0: baseline certificate and forward-only migration ledger (TEST)`).
- Scope of the audit that produced this spec: **read-only static analysis of
  repository files**. No database connection was opened. No PROD access. No
  file outside this document was created or modified.
- P26-0 is closed. Baseline `P26-BASELINE-001`, migration `026` applied on
  `stima360_db_test`, pre-`026` fingerprint
  `84a44a9fdca44d9b4a8842686919eede3cbec052674241a096d2855c70e5f57d`.
- This spec is subordinate to the twelve rules in
  `docs/superpowers/specs/2026-09-05-p26-0-baseline-design.md` §6. Where the
  approved P26-1 conceptual architecture and those rules disagree, the rules
  win and the disagreement is recorded in §2.

---

## 1. Current-state audit

### 1.1 Authentication — what actually exists

There are exactly **three** authentication mechanisms in the repository today.

| # | Mechanism | Definition | Applied to |
|---|---|---|---|
| A1 | HTTP Basic against `ADMIN_USER`/`ADMIN_PASS` | `admin_security.require_admin` | `core`, `property`, `buy`, `match`, `crm`, `proposal`, `sale`, `seller_intelligence`, `followup`, `seller_intent`, `property_watch`, `next_best_action` routers (`main.py:54`–`73`) and 6 `@app` admin routes |
| A2 | HTTP Basic against the same two env vars, second implementation | `owner.router_admin.require_owner_admin` (`owner/router_admin.py:44`) | `owner_admin_router`, and `flow_router` (`flow/router.py:8`) |
| A3 | Opaque cookie session | `owner.dependencies.current_owner` (`owner/dependencies.py`) over `owner_sessions` | `owner_portal_router` only |

Findings:

1. **There is no operator identity anywhere.** A1 and A2 authenticate a single
   shared secret pair. `require_admin` returns `credentials.username`, which is
   the value of `ADMIN_USER` — a constant, not a person. Every `created_by`,
   `assigned_to` and `handled_by` column in the schema is therefore free text
   supplied by the caller or by a module constant.
2. **A1 and A2 are duplicate implementations of the same check.** They differ
   only in realm string and in that A1 uses `secrets.compare_digest`.
3. `main.py:215` `POST /api/admin/check` is **unauthenticated by dependency**;
   it is itself the credential check. It returns `{"ok": true}` and sets
   nothing. It exists solely so the OS Shell can validate credentials before
   holding them in memory.
4. **A3 is the design precedent P26-1 must follow.** `owner_sessions`
   (`migrations/009_owner_01.sql:5`) is: `session_token_hash CHAR(64) NOT NULL
   UNIQUE`, `created_at`, `last_seen_at`, `expires_at`, `revoked_at`,
   `CHECK(expires_at>created_at)`. `owner/security.py` generates
   `secrets.token_urlsafe(32)`, persists only `sha256` hex, and sets an
   `HttpOnly; Secure; SameSite=Lax; Path=/` cookie with `max_age =
   SESSION_MAX_HOURS*3600`. Validity is `not revoked_at AND expires_at > now
   AND last_seen_at > now - idle`. Unauthorised access raises **404** with a
   constant message, never 403.

### 1.2 OS Shell authentication

`static/os_shell/assets/core/auth.js` holds `{username, password}` in a module
variable, never in `localStorage`/`sessionStorage`/`document.cookie`. It
validates once against `POST /api/admin/check`, then
`static/os_shell/assets/core/api-client.js` attaches an
`Authorization: Basic …` header to every request and calls `logout()` on any
`401`.

Consequence for P26-1: the Shell has **no session concept at all**. Migrating
it to cookie sessions is a frontend change of `auth.js` + `api-client.js` only;
no other Shell file reads credentials.

### 1.3 Database access layer

`database.get_connection()` (`database.py:28`) is a bare
`psycopg2.connect(...)`. No pool, no ORM, no `DATABASE_URL`. `core.database`
`core_cursor()` (`core/database.py:13`) is a `contextmanager` yielding
`(conn, cur)` with a `RealDictCursor`, committing only when `commit=True`.

`core_cursor` is the shared cursor for `core/`, `owner/`, `buy/`, `match/`,
`flow/`, `property/`, `proposal/`, `sale/`. `followup/`, `seller_intent/`,
`seller_intelligence/`, `property_watch/`, `next_best_action/` and
`database_revival/` each define their own equivalent contextmanager over the
same `get_connection`. P26-0 §1.2 already certified that **100% of application
runtime traffic passes through `database.get_connection()`**.

There is **no request-scoped context object of any kind** — no middleware, no
`ContextVar`, no dependency that carries caller identity into a service or
repository. Every repository function's signature is a flat list of filter
primitives.

### 1.4 CORE schema — the real DDL

From `migrations/001_core_contacts_leads.sql`, verbatim structure:

| Table | PK | Relevant columns | Constraints found |
|---|---|---|---|
| `contacts` | `BIGSERIAL` | `contact_type`, `first_name`, `last_name`, `company_name`, `display_name`, `email`, `email_normalized`, `phone`, `phone_normalized`, `source`, `status`, `marketing_consent`, `notes`, `created_at`, `updated_at`, `archived_at` | `contacts_type_chk`, `contacts_status_chk`, `contacts_identity_chk`. **No UNIQUE constraint of any kind.** |
| `contact_roles` | `BIGSERIAL` | `contact_id` → `contacts(id) ON DELETE CASCADE`, `role`, `is_primary`, `metadata` | `UNIQUE (contact_id, role)` |
| `leads` | `BIGSERIAL` | `contact_id` → `contacts(id) ON DELETE RESTRICT`, `source`, `pipeline`, `stage`, `priority`, `status`, `assigned_to VARCHAR(200)`, `estimated_value`, `next_action_at`, `closed_at` | 5 CHECK constraints. **No UNIQUE constraint.** |
| `lead_stime` | `BIGSERIAL` | `lead_id` → `leads(id) CASCADE`, `stima_id` → `stime(id) CASCADE`, `relation_type` | `UNIQUE (lead_id, stima_id)` |
| `activities` | `BIGSERIAL` | `contact_id` (SET NULL), `lead_id` (SET NULL), `stima_id` (CASCADE), `activity_type`, `occurred_at`, `created_by VARCHAR(200)`, `metadata` | `activities_reference_chk`: at least one of the three ids is NOT NULL |
| `tasks` | `BIGSERIAL` | `contact_id` (SET NULL), `lead_id` (SET NULL), `stima_id` (CASCADE), `title`, `status`, `due_at`, `assigned_to VARCHAR(200)`, `created_by VARCHAR(200)`, `metadata` | `tasks_reference_chk`: same rule |

Indexes on these six tables are all **non-unique single-column** indexes
(`idx_contacts_email_normalized`, `idx_leads_contact_id`, …).

Two consequences that decide the design:

- **`contacts` has no uniqueness to make agency-aware.** The instruction to
  audit "existing globally unique fields that might need `UNIQUE(agency_id,…)`"
  resolves, for `contacts`, to: there are none. `email_normalized` carries a
  plain index only. Deduplication is performed procedurally in
  `core/repository.py:224` `bridge_public_stima`, not by the database.
- **`activities` and `tasks` may legally exist with `contact_id IS NULL AND
  lead_id IS NULL`**, referenced only by `stima_id`. `main.py` relies on this:
  the P18 follow-up call at `main.py` passes `contact_id`/`lead_id` from
  `(bridge_result or {}).get(...)`, which are `None` when the bridge fails or
  is skipped, and the inline comment states `stima_id` alone satisfies
  `tasks_reference_chk`. **Scoping `activities`/`tasks` through their CORE
  parent is therefore not sufficient** — see §1.7 and §3.3.

### 1.5 Uniqueness audit across the whole schema

Every UNIQUE constraint or unique index that touches CORE identity:

| Location | Constraint | Needs `agency_id`? | Reason |
|---|---|---|---|
| `001:49` | `contact_roles UNIQUE (contact_id, role)` | **No** | `contact_id` already determines the agency once `contacts.agency_id` exists. |
| `001:82` | `lead_stime UNIQUE (lead_id, stima_id)` | **No** | Both sides are agency-determined via `leads`. |
| `002:57` | `property_contacts UNIQUE(property_id, contact_id, role)` | Out of scope | PROPERTY is not scoped in P26-1. |
| `002:68` | `property_leads UNIQUE(property_id, lead_id)` | Out of scope | idem |
| `009:2` | `owner_accounts.contact_id BIGINT NOT NULL UNIQUE` | **No** | Agency-determined by the contact. A globally unique owner account per contact remains correct. |
| `016:41` | `property_sales_contacts UNIQUE(sale_id, contact_id, role)` | Out of scope | SALE is not scoped in P26-1. |
| `025:31` | `seller_revival_suppressions UNIQUE (contact_id)` | **No** | Agency-determined by the contact. |
| `017:29` | `idx_seller_timeline_events_idempotency_key` (global unique) | **No** | Keys are built from `stima_id`/entity ids, which are globally unique. |
| `022:18` | `idx_property_watches_stima_id` (global unique) | **No** | `stime` stays unscoped in P26-1 (§11). |

**Result: no existing uniqueness constraint has to become `UNIQUE(agency_id,
…)` in P26-1.** The approved architecture asked this to be audited rather than
assumed; the audited answer is that the required work is zero. The one place
where a *new* agency-aware uniqueness rule is introduced is
`agency_memberships` (§3.2), and the one place where global uniqueness must be
*preserved deliberately* is `operator_users.email_normalized` (§3.2, §2.4).

### 1.6 CORE endpoints — the real surface

`core/router.py` mounts `APIRouter(prefix="/api/core")` with **no router-level
dependency**; auth is attached at `main.py:54`. Full endpoint list:

```
POST   /api/core/contacts
GET    /api/core/contacts                 ?limit&offset&search&status
GET    /api/core/contacts/{contact_id}
PATCH  /api/core/contacts/{contact_id}
POST   /api/core/contacts/{contact_id}/roles
DELETE /api/core/contacts/{contact_id}/roles/{role}
POST   /api/core/leads
GET    /api/core/leads                    ?limit&offset&contact_id&pipeline&stage&status
GET    /api/core/leads/{lead_id}
PATCH  /api/core/leads/{lead_id}
POST   /api/core/leads/{lead_id}/stime/{stima_id}
DELETE /api/core/leads/{lead_id}/stime/{stima_id}
POST   /api/core/activities
GET    /api/core/activities               ?limit&offset&contact_id&lead_id&stima_id
DELETE /api/core/activities/{activity_id}
POST   /api/core/tasks
GET    /api/core/tasks                    ?limit&offset&contact_id&lead_id&stima_id&status
PATCH  /api/core/tasks/{task_id}
DELETE /api/core/tasks/{task_id}
```

There is **no archive/delete endpoint for contacts or leads**. `ContactUpdate`
exposes `archived_at`, so archiving is a `PATCH`. Leads are closed via
`status='closed'`, which `core/service.py` translates into `closed_at`. The
"archive/delete if applicable" item in the approved architecture therefore maps
to `PATCH /api/core/contacts/{id}` and `PATCH /api/core/leads/{id}` — no new
endpoint is required, and none is introduced.

### 1.7 Payload forgery — existing defence

`core/schemas.py` defines `class CoreModel(BaseModel): class Config: extra =
"forbid"`, and **every** CORE request schema inherits it. Pydantic v1 is in use
(`root_validator`; `requirements.txt` pins no Pydantic, FastAPI pulls it).

Consequence: an attacker who posts `{"agency_id": 2, …}` to any `/api/core`
write endpoint receives **422** today, before any handler runs, because the
field is not declared. This is a genuine, already-certified defence. **The
design must not weaken it: `agency_id` and `created_by_user_id` are never
added to any request schema.** `assigned_agent_id` is added only to the
dedicated assignment payloads described in §10, and is validated against the
caller's own agency server-side.

Query-string forgery is a different matter and is **not** currently defended:
`GET /api/core/activities?stima_id=…` and `GET /api/core/tasks?stima_id=…`
accept any integer and filter on it directly (`core/repository.py:404`, `:442`).

### 1.8 Global search

There is **no server-side global search endpoint**. Grep for `search` across
`crm/` and `main.py` returns nothing.

`static/os_shell/assets/core/global-search.js` `searchGlobal()` is a
**client-side fan-out** over four already-authenticated endpoints:

```
GET /api/core/contacts?search=<q>&limit=5
GET /api/property/properties?search=<q>&limit=5
GET /api/buy/requests?search=<q>&limit=5
GET /api/match/matches/<q>            (only when q parses as a positive int)
```

then, when `includeLeads` is set (it is, at
`static/os_shell/assets/components/global-search.js:48`), one
`GET /api/core/leads?contact_id=<id>&limit=5` **per matched contact**.

This is the best possible finding for P26-1: **scoping `/api/core/contacts` and
`/api/core/leads` scopes the CORE half of global search automatically.** No
search-specific code needs a scope predicate. The residual exposure is that
`/api/property/properties?search=` and `/api/buy/requests?search=` remain
unscoped — handled by the route allowlist in §2.1.

A **second, independent free-text contact search** exists and is easy to miss:
`GET /api/owner/admin/lookups/contacts?search=` →
`owner/admin_lookup_repository.py:16` `lookup_contacts`, which runs
`SELECT id,display_name,email FROM contacts WHERE display_name ILIKE %s OR
email ILIKE %s` with no scope whatsoever. It is mounted under
`owner_admin_router`, i.e. behind A2.

### 1.9 Public STIMA → CORE bridge

The real chain, with exact locations:

```
POST /api/salva_stima                     main.py:414   (unauthenticated, public)
  └─ INSERT INTO stime … RETURNING id                     main.py
  └─ core_service.bridge_public_stima(new_id, …)          main.py:518
       └─ core/service.py:94   builds contact_data + lead_data
            source='public_stima', pipeline='sell', stage='new',
            status='open', assigned_to=None
       └─ core/repository.py:224  bridge_public_stima      ← single transaction
  └─ seller_intelligence_service.safe_record_event(…)      never raises
  └─ followup_service.safe_run_followup(…)                 never raises
```

`core/repository.py:224` is one `core_cursor(commit=True)` transaction that:

1. takes `pg_advisory_xact_lock` on `core:public_stima:{stima_id}`;
2. returns `already_linked` if `lead_stime` already has the stima;
3. takes advisory locks on `core:contact:email:{…}` / `core:contact:phone:{…}`;
4. runs `SELECT * FROM contacts WHERE email_normalized=%s … FOR UPDATE` and the
   same for `phone_normalized`;
5. returns `conflict/ambiguous_identity` on >1 match on either axis,
   `conflict/identity_conflict` when email and phone resolve to different
   contacts, `skipped/archived_contact` for an archived match;
6. otherwise reuses the matched contact or inserts a new one;
7. inserts the lead and the `lead_stime` row.

`main.py` wraps the whole call in `try/except Exception` and only logs on
failure — **a bridge exception never fails the public estimation**.

**The identity-match queries in steps 4 are the injection point that matters.**
Unscoped, once two agencies exist, a public estimation routed to the Default
Agency can match a contact belonging to another agency and attach the new
`sell` lead to it. That is a cross-agency **write**, not merely a read. It is
the single most severe finding in this audit (§14, T-1).

`link_stima` (`core/repository.py`) additionally enforces **one lead per
stima globally** — it rejects a second lead for the same `stima_id` with a
`ConflictError` even from a different lead. That rule is agency-blind by
construction and stays that way in P26-1 (§11, §21 R-2).

### 1.10 Modules that read CORE tables outside `core/`

Every module that reads `contacts`/`leads`/`lead_stime`/`contact_roles` in
non-test code:

| Module | Where | What it exposes |
|---|---|---|
| `crm/` | `crm/service.py` `get_contact_360` | The entire 360 view of any contact id: contact, roles, leads, properties, buy requests, matches, visits, activities, tasks |
| `owner/` | `admin_lookup_repository.py:16,33`; `repository.py:18,23` | Free-text contact search; owner-account ↔ contact join |
| `property/` | `repository.py:66,67,238,247` | `property_contacts`→`contacts`, `property_leads`→`leads`, visits→contact names |
| `buy/` | `repository.py:62,63,70,237` | `buy_requests`→`contacts` display names, `leads` |
| `match/` | `repository.py:301,319` | buy request → contact join |
| `proposal/` | `repository.py:29,57,193` | buy request → contact join |
| `seller_intent/` | `repository.py:18,23,41,54` | `leads`, `lead_stime`, `tasks` |
| `followup/` | `repository.py:245,246,333,362` | `tasks` ⋈ `leads` |
| `flow/` | `adapters.py:17,18,19,58` | `leads`, activity/task counts, `SELECT id FROM leads WHERE status='open'` |
| `next_best_action/` | `repository.py:158`, `signals.py:228,229` | `contacts` display names, `lead_stime`⋈`leads` |
| `database_revival/` | `eligibility.py:82,100,107,111,143,144,185,186` | `leads`⋈`contacts` batch selection |

**None of these will be agency-scoped in P26-1.** That is the central
compatibility problem, resolved in §2.1.

### 1.11 Migration runner constraints inherited from P26-0

`scripts/p26_migrate.py` will **statically reject** a `027`+ migration that:

- has no `NNN_x_down.sql` sibling (`validate_migration`);
- is transactional and **contains** `BEGIN;` or `COMMIT;` at line start — from
  `027` the runner owns the transaction (atomicity addendum; below `027` the
  inverse rule still applies, unchanged, so `026` is unaffected);
- is transactional but contains `CONCURRENTLY`;
- is marked `-- NON-TRANSACTIONAL` in its first 15 lines but opens a
  transaction, or contains no `CONCURRENTLY`;
- contains `DELETE FROM schema_migrations`;
- creates a gap or duplicate in the `026,027,028,…` sequence
  (`verify_contiguous`).

`assert_test_database_name` blocks `stima360_db`/`stima360` and requires the
literal marker `test` in `DB_NAME`. `assert_operator_identity` rejects an
`--operator` equal to `ADMIN_USER`. The `schema_migrations_no_pre_baseline`
CHECK enforces the same version floor at the database level.

---

## 2. Exact architectural decisions

### 2.1 D-1 — Agency operators reach only agency-scoped routes (route allowlist)

**The problem.** §2 of the approved architecture states an ABSOLUTE RULE: an
agency user must never read, modify, search, infer or access another agency's
data. §11 places PROPERTY, BUY, MATCH, sales/proposals and the intelligence
modules explicitly out of scope. §1.10 above shows eleven modules that read
`contacts`/`leads` without a scope. Those two statements cannot both hold if an
agency operator can call those routes.

**The decision.** P26-1 introduces the new operator identity as an authority
that is accepted **only** on an explicit allowlist:

```
/api/operator-auth/*        (login, logout, me)
/api/core/*                 (the P26-1 scoped slice)
```

Every other router keeps `Depends(require_admin)` / `Depends(require_owner_admin)`
**unchanged, byte for byte**. An operator session presents no `Authorization`
header, so those routers answer `401` to an agency operator. No route loses
protection; no P17–P25 workflow changes.

This is a bounded, honest statement of what P26-1 delivers: *one fully isolated
vertical slice*, with the isolation guaranteed rather than asserted. Extending
the allowlist is the explicit content of P26-2+.

### 2.2 D-2 — Legacy `ADMIN_USER`/`ADMIN_PASS` maps to the Default Agency, never to `platform_admin`

`require_admin` stays. On `/api/core` only, the dependency becomes
`require_operator`, which accepts **either**:

- a valid `stima360_operator_session` cookie → a real `OperatorContext`; or
- valid HTTP Basic `ADMIN_USER`/`ADMIN_PASS` → the synthetic context

```
OperatorContext(user_id=None,
                agency_id=<id of agency slug 'stima360'>,
                role='agency_owner',
                is_platform_admin=False,
                session_id=None,
                auth_channel='legacy_basic')
```

On the CORE slice the legacy credential is therefore **owner of the Default
Agency and nothing more**, and `user_id=None` is honest: the shared login is not
a person, so `created_by_user_id` stays `NULL` for legacy writes rather than
being attributed to an invented operator. This mirrors P26-0's refusal to
record migrations `001`–`025`.

#### 2.2.1 The contradiction this creates, stated plainly

An earlier draft of this spec claimed the legacy credential is "confined to the
Default Agency, where all legacy data lives". **That claim is false and is
retracted.** It holds only on `/api/core`, and only while exactly one agency
holds data.

Basic and the operator session are **two different authorities**. The D-1 route
allowlist governs *operator sessions*; it does nothing whatever to Basic.
`require_admin` and `require_owner_admin` are unchanged by P26-1 and remain
**global** on twelve routers, eleven of which read `contacts`/`leads` without an
agency predicate (§1.10). The moment Agency B holds data — which §17 step 12
creates deliberately, for the isolation tests — the same `ADMIN_USER`/
`ADMIN_PASS` pair reads it through, among others:

```
GET /api/crm/contacts/{B contact}/360        → full dossier: contact, roles,
                                                leads, properties, buy requests,
                                                matches, visits, activities, tasks
GET /api/owner/admin/lookups/contacts?search= → unscoped free-text over contacts
GET /api/property/properties?search=          → B's property/contact joins
GET /api/buy/requests?search=                 → B's contact display names
GET /api/seller-intelligence/timeline?contact_id={B} → B's timeline
GET /api/next-best-action                     → B's contact names
```

**So: during P26-1 the legacy Basic credential remains a technically global,
cross-agency authority on every non-CORE router.** This is stated here, in the
threat model as T-21, in §12, §18, §20 and §21 R-9, rather than being softened
anywhere.

#### 2.2.2 The smallest safe transitional plan

Four controls. None of them is the route allowlist, because the allowlist is
the wrong authority.

- **C-1 — Reclassify, do not rename.** `ADMIN_USER`/`ADMIN_PASS` is designated
  the **platform maintenance channel**: a single, shared, non-attributable,
  cross-agency credential held by STIMA360 staff. That is what the audit shows
  it to be (§1.1). P26-1 stops describing it as anything else. It is never an
  agency identity, and the approved architecture's prohibition is honoured in
  the only way that is true: it does not *become* the multi-agency identity —
  `operator_users` does — and it gains no new powers in P26-1.
- **C-2 — No agency operator is ever issued it.** Structurally: the pair exists
  only as a server environment variable. P26-1 adds no endpoint, no UI, no
  provisioning flow and no seed script that discloses it; agency operators are
  provisioned exclusively through `operator_users` + `agency_memberships`,
  which never carries it. An agency operator can only obtain it if a human
  hands over the platform's shared secret. **That is a credential-handling
  control, not a code control**, and it is recorded as such rather than counted
  as a technical guarantee.
- **C-3 — Deny it any cross-agency power where P26-1 does reach.** On
  `/api/core` it maps to Default-Agency `agency_owner` (above), never to
  `is_platform_admin`. It cannot use the new assignment endpoints to reach
  another agency's operators (§3.4 G-2/G-3).
- **C-4 — Freeze and monitor the residual surface.**
  `tests/test_p26_1_legacy_basic_surface.py` walks `app.routes`, computes the
  set of routes that (a) accept `require_admin`/`require_owner_admin` and
  (b) transitively read a CORE table, and asserts it equals a **frozen expected
  list** committed with the test. The hole is thereby enumerated, bounded and
  regression-guarded: adding a twelfth unscoped module, or widening an existing
  one, fails the build. An undocumented cross-agency surface cannot grow
  silently.

#### 2.2.3 GATE-MA1 — the blocking certification gate

> **GATE-MA1.** STIMA360 must not onboard a second **real** agency into
> **PROD** while any route outside the scoped CORE slice accepts
> `ADMIN_USER`/`ADMIN_PASS` and reads `contacts` or `leads` without an agency
> predicate.

P26-1 delivers **one isolated CORE vertical slice, proven in TEST**. It does
**not** deliver, and must not be reported as delivering, platform-wide
multi-agency isolation. GATE-MA1 closes only when both hold:

1. every module in the §1.10 table is agency-scoped, or is provably unable to
   return CORE data (P26-2+); and
2. the legacy Basic channel is removed per the removal condition below.

Until then, a second agency exists **in TEST only**, as an isolation fixture.
This gate is restated in §20 (Definition of Done, item 16) so it cannot be
satisfied by accident.

**Removal condition for the legacy channel (single, testable):**
`auth_channel='legacy_basic'` is removed from `require_operator`, and
`require_admin`/`require_owner_admin` are replaced platform-wide, when and only
when (a) the OS Shell authenticates via `/api/operator-auth/login`, (b) no test
in `tests/` sends Basic credentials to any route, and (c) GATE-MA1 condition 1
is met. Until then the branch stays and is covered by its own test.

### 2.3 D-3 — Agency scope is resolved per request, never pinned in the session

`operator_sessions` stores **no `agency_id`**. Membership, membership status,
user status and agency status are re-read on every request through one JOIN
(§7.4). A suspended membership, a disabled user or a suspended agency therefore
takes effect on the **next request**, not at the next login. This satisfies the
approved requirements "disabled user rejected" and "disabled membership
rejected" literally, and matches `owner_sessions`, which likewise stores only
`owner_account_id` and resolves everything else at read time.

### 2.4 D-4 — `operator_users.email_normalized` stays **globally** unique

The approved architecture asks for an audit of globally unique fields that
might need `UNIQUE(agency_id, …)`. For the one field being introduced, the
audited answer is the opposite of the default expectation:

Login happens **before** any agency context exists. The submitted credential is
an email address and nothing else. If `email` were unique per agency,
`SELECT … WHERE email_normalized=%s` could return several rows and login would
require an agency selector in the login form — a mechanism the approved
architecture does not authorise and which leaks the existence of agencies to an
unauthenticated caller.

Therefore: `UNIQUE (email_normalized)`, global. One human = one operator
account = one login. Multi-agency membership (P26-2+) is then a pure
`agency_memberships` change with no schema demolition — which is precisely why
membership was separated from `operator_users` in the approved design.

### 2.5 D-5 — Repository scoping is a mandatory first parameter plus a static gate

The codebase has no ORM, no query builder and no request context. Three options
were considered:

| Option | Verdict |
|---|---|
| Optional `agency_id=None` filter parameter | **Rejected.** Explicitly forbidden by the approved architecture ("avoid optional agency filters"). Forgetting it fails open. |
| PostgreSQL RLS | **Rejected for P26-1.** Out of scope (§11), and P26-0 §6.1 makes role separation a hard precondition that does not yet exist. |
| Mandatory `ctx` parameter + centralised predicate builder + static test | **Adopted.** |

Concretely:

1. Every function in `core/repository.py` that touches `contacts`, `leads`,
   `activities` or `tasks` takes `ctx: OperatorContext` as its **first
   positional parameter**. Omitting it is a `TypeError` at call time — it fails
   closed, loudly, in the first test that exercises the path.
2. No such function writes its own `FROM contacts` / `FROM leads` /
   `FROM activities` / `FROM tasks`. All four come from a single new module
   `core/scope.py`:

   ```python
   SCOPED_TABLES = frozenset({"contacts", "leads", "activities", "tasks"})

   def scoped_source(ctx, table, alias) -> tuple[str, list]:
       """Return ('<table> <alias> WHERE <predicate>', params).

       There is no code path through this function that omits the predicate.
       """
   ```

   Because the predicate is emitted by the same expression that emits the table
   name, a query cannot name a scoped table without carrying its scope.
3. `tests/test_p26_1_scope_enforcement.py` parses `core/repository.py` with
   `ast` and fails if any string literal in the module matches
   `\b(FROM|JOIN|UPDATE|INTO)\s+(contacts|leads|activities|tasks)\b` outside
   the `core/scope.py` builder, and fails if any public function in the module
   violates rule 4 below. This mirrors the repository's existing
   static-analysis test convention (`tests/test_p26_db_entrypoints.py`,
   `tests/test_owner_07_p7.py`).

The service layer is a thin pass-through today (`core/service.py`) and stays
so: it gains `ctx` as first parameter and forwards it. The router obtains `ctx`
from `Depends(require_operator)`.

#### 2.5.1 The public bridge has no operator session — resolved, not excepted

`core/repository.py:224` `bridge_public_stima` writes `contacts` and `leads`
for an **unauthenticated** caller (`POST /api/salva_stima`). A rule demanding
an `OperatorContext` on every scoped function would either be violated by the
one legitimate anonymous writer, or force a generic context that a request
could shape. Neither is acceptable.

Resolution: a **second, distinct context type** that no request can construct.

```python
# operator_auth/context.py
@dataclass(frozen=True)
class SystemAgencyContext:
    """Server-generated agency scope for flows that have no operator.

    Deliberately carries no user_id, no role and no is_platform_admin:
    it can express 'this agency' and nothing else.
    """
    agency_id: int
    origin: str          # 'public_stima' — the only value in P26-1
```

Four properties make it safe, and each is statically testable:

- **It has exactly one factory**, `core/scope.py::system_context_for_public_stima(cur)`,
  which takes an **open cursor and nothing else**. It resolves
  `SELECT id FROM agencies WHERE slug = 'stima360' AND status = 'active'`
  inside the caller's transaction and raises `ConflictError` if absent. There
  is no `agency_id` parameter anywhere in the signature, so the value is a
  query result and can never be an argument.
- **It is a different type from `OperatorContext`**, so it cannot acquire
  `role='agent'`, `is_platform_admin=True` or a `user_id` by any assignment —
  it is `frozen`, and those fields do not exist on it.
- **No HTTP-facing module may import it.** `main.py`, every `*/router*.py` and
  every schema module are forbidden the import by test 52c.
- **`origin` is a closed set.** Adding a second value is a deliberate edit that
  fails test 52d until the allowlist is updated.

Both types satisfy one minimal structural protocol, which is all
`scoped_source` needs (§9.1):

```python
class AgencyScope(Protocol):
    agency_id: int | None
    role: str | None
    user_id: int | None
    is_platform_admin: bool
```

`SystemAgencyContext` presents `role=None`, `user_id=None`,
`is_platform_admin=False`. `scoped_source` therefore emits exactly
`WHERE agency_id = %s` for it: no agent narrowing, no cross-agency branch, no
`WHERE TRUE`. The bridge's identity lookups go through the same builder as
every other query — **rule 2 holds for the bridge with no exception at all.**

**Rule 4 — the one static derogation, and its bound.** `bridge_public_stima`
cannot *receive* `ctx` first, because it must *create* it from the cursor it
opens. The AST rule is therefore stated as a disjunction rather than being
weakened:

> Every public function in `core/repository.py` that touches a scoped table
> either **(a)** takes `ctx` as its first positional parameter, or **(b)** is a
> member of the frozen allowlist
> `SYSTEM_CONTEXT_FUNCTIONS = {"bridge_public_stima"}` and calls
> `system_context_for_public_stima(cur)` as the first statement inside its
> `core_cursor` block.

Test 52b asserts branch (b) structurally — membership, the first-statement
position, and that the allowlist has **exactly one** member. A second
system-context writer cannot be added without editing the test, which is
precisely the deliberate act that should be required. The rule now describes
the real codebase instead of contradicting it.

### 2.6 D-6 — `404` for cross-agency, `403` for role-denied inside one's own agency

The rule is decided by *what is being hidden*:

- **Cross-agency (entity exists but belongs to another agency): `404`,
  `{"detail": "Risorsa non trovata"}`.** The response is byte-identical to a
  genuinely absent id, so the API cannot be used to enumerate other agencies'
  primary keys. This aligns with the established repository precedent —
  `owner/router_portal.py` `nf()` and `owner/router_admin_lookups.py` `_read()`
  both convert every authorisation failure into `HTTPException(404,
  'Risorsa non trovata')` — and with `core/router.py`'s existing
  `NotFoundError → 404` translation, so no existing status code changes.
- **Role-denied within the caller's own agency (an `agent` attempting to assign
  a record, or an `agency_admin` attempting to change the agency owner):
  `403`.** Nothing is being concealed here: the caller legitimately knows the
  entity exists. Returning `404` would be actively misleading and would make
  the permission matrix untestable from the outside.
- **Unauthenticated or invalid/expired/revoked session: `401`**, unchanged.

Enumeration risk from the `403` branch is nil, because a `403` is only ever
returned for a resource the caller can already see.

### 2.7 D-7 — Password hashing uses the standard library

`requirements.txt` contains no `passlib`, `bcrypt` or `argon2-cffi`. Adding a
dependency is scope expansion under AGENTS.md. P26-1 uses
`hashlib.pbkdf2_hmac('sha256', password, salt, 600_000)` with a 16-byte
`secrets.token_bytes` salt, stored as
`pbkdf2_sha256$<iterations>$<b64 salt>$<b64 hash>` in a `TEXT` column, verified
with `hmac.compare_digest`. The stored format carries its own iteration count,
so the cost can be raised later without a migration.

Passwords are never logged, never returned by any endpoint, and never placed in
`agencies.settings`.

### 2.8 D-8 — `agencies.settings` is non-sensitive by construction

`settings JSONB NOT NULL DEFAULT '{}'::jsonb`. P26-1 defines **no key** in it
and reads it nowhere. It exists so P26-2+ has a home for display preferences.
P26-0 §8 (no secrets in schema) is restated as a CHECK-free but
test-enforced rule: `tests/test_p26_1_agency_identity.py` asserts that no
migration file and no seeding script writes a key matching
`(?i)(pass|secret|token|key|smtp|api)` into `settings`.

---

## 3. Exact DB schema proposal

All identifiers are `BIGSERIAL`/`BIGINT`, matching `contacts`/`leads`/every
CORE table. No UUIDs — the repository uses none.

### 3.1 `agencies`

```sql
CREATE TABLE IF NOT EXISTS agencies (
    id         BIGSERIAL PRIMARY KEY,
    name       VARCHAR(200) NOT NULL,
    slug       VARCHAR(100) NOT NULL,
    status     VARCHAR(20)  NOT NULL DEFAULT 'active',
    settings   JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT agencies_name_chk   CHECK (BTRIM(name) <> ''),
    CONSTRAINT agencies_slug_chk   CHECK (slug ~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?$'),
    CONSTRAINT agencies_status_chk CHECK (status IN ('active','suspended','archived')),
    CONSTRAINT agencies_slug_unq   UNIQUE (slug)
);
```

`slug` is the stable machine key the public-STIMA default lookup uses
(`'stima360'`), so it must not depend on a hard-coded `id = 1`.

### 3.2 `operator_users`, `agency_memberships`, `operator_sessions`

```sql
CREATE TABLE IF NOT EXISTS operator_users (
    id                BIGSERIAL PRIMARY KEY,
    email             VARCHAR(320) NOT NULL,
    email_normalized  VARCHAR(320) NOT NULL,
    password_hash     TEXT         NOT NULL,
    first_name        VARCHAR(100),
    last_name         VARCHAR(100),
    status            VARCHAR(20)  NOT NULL DEFAULT 'active',
    is_platform_admin BOOLEAN      NOT NULL DEFAULT FALSE,
    last_login_at     TIMESTAMPTZ,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT operator_users_email_chk   CHECK (BTRIM(email) <> ''),
    CONSTRAINT operator_users_status_chk  CHECK (status IN ('invited','active','disabled')),
    CONSTRAINT operator_users_hash_chk    CHECK (password_hash LIKE 'pbkdf2_sha256$%'),
    CONSTRAINT operator_users_email_unq   UNIQUE (email_normalized)   -- D-4: global
);

CREATE TABLE IF NOT EXISTS agency_memberships (
    id               BIGSERIAL PRIMARY KEY,
    agency_id        BIGINT      NOT NULL REFERENCES agencies(id)       ON DELETE RESTRICT,
    operator_user_id BIGINT      NOT NULL REFERENCES operator_users(id) ON DELETE CASCADE,
    role             VARCHAR(20) NOT NULL,
    status           VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT agency_memberships_role_chk   CHECK (role IN ('agency_owner','agency_admin','agent')),
    CONSTRAINT agency_memberships_status_chk CHECK (status IN ('active','suspended','revoked')),
    CONSTRAINT agency_memberships_unq        UNIQUE (agency_id, operator_user_id)
);

-- P26-1 only: one operator holds at most one ACTIVE membership.
-- Dropping this index is the whole of the P26-2 multi-agency change.
CREATE UNIQUE INDEX IF NOT EXISTS uq_agency_memberships_single_active
    ON agency_memberships (operator_user_id) WHERE status = 'active';

-- Exactly one active agency_owner per agency.
CREATE UNIQUE INDEX IF NOT EXISTS uq_agency_memberships_single_owner
    ON agency_memberships (agency_id) WHERE role = 'agency_owner' AND status = 'active';

CREATE TABLE IF NOT EXISTS operator_sessions (
    id               BIGSERIAL PRIMARY KEY,
    operator_user_id BIGINT      NOT NULL REFERENCES operator_users(id) ON DELETE CASCADE,
    token_hash       CHAR(64)    NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at       TIMESTAMPTZ NOT NULL,
    revoked_at       TIMESTAMPTZ,
    CONSTRAINT operator_sessions_expiry_chk CHECK (expires_at > created_at),
    CONSTRAINT operator_sessions_hash_chk   CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT operator_sessions_token_unq  UNIQUE (token_hash)
);
```

Notes:

- `is_platform_admin` lives on `operator_users`, **not** as a fourth
  `agency_memberships.role`. This is the approved separation ("not modeled as
  owner of every agency") expressed structurally: a platform admin has no
  membership row and therefore no agency scope by default.
- **`agency_memberships` carries exactly one uniqueness constraint.** An
  earlier draft of this spec added a second, `UNIQUE (agency_id,
  operator_user_id, id)`, claiming it was the referencable key the §3.4
  composite FKs need. **That claim was wrong and is retracted.** PostgreSQL
  requires a foreign key's referenced column list to be covered by a UNIQUE
  constraint or primary key on *exactly* those columns; `FOREIGN KEY (agency_id,
  assigned_agent_id) REFERENCES agency_memberships (agency_id,
  operator_user_id)` is therefore satisfied by `agency_memberships_unq` alone.
  A superset including `id` does not satisfy the FK and would only add an index
  nothing reads. It is removed. No other requirement — query, ordering or
  ledger — needs it.
- `operator_sessions` deliberately has **no `agency_id`** (D-3).

Indexes for `027`:

```sql
CREATE INDEX IF NOT EXISTS idx_operator_sessions_user       ON operator_sessions (operator_user_id);
CREATE INDEX IF NOT EXISTS idx_operator_sessions_expires_at ON operator_sessions (expires_at);
CREATE INDEX IF NOT EXISTS idx_agency_memberships_operator  ON agency_memberships (operator_user_id);
CREATE INDEX IF NOT EXISTS idx_agency_memberships_agency    ON agency_memberships (agency_id, role, status);
```

`operator_sessions.token_hash` needs no separate index: `UNIQUE` creates one,
and it is the only lookup key.

### 3.3 Columns added to CORE tables

Four tables, three columns each. `activities` and `tasks` are included because
of the `stima_id`-only rows proven in §1.4/§1.7 — scoping them through their
CORE parent is impossible for rows that have no CORE parent.

```sql
-- contacts, leads, activities, tasks
ADD COLUMN IF NOT EXISTS agency_id          BIGINT REFERENCES agencies(id)       ON DELETE RESTRICT;
ADD COLUMN IF NOT EXISTS assigned_agent_id  BIGINT REFERENCES operator_users(id) ON DELETE SET NULL;  -- contacts, leads only
ADD COLUMN IF NOT EXISTS created_by_user_id BIGINT REFERENCES operator_users(id) ON DELETE SET NULL;
```

- `assigned_agent_id` is added to `contacts` and `leads` only. `activities` and
  `tasks` already carry a free-text `assigned_to VARCHAR(200)` written by
  `flow/`, `followup/` and `buy/`; they are visible through their agency, not
  through per-record assignment, in P26-1.
- `ON DELETE RESTRICT` on `agency_id`: an agency with data cannot be deleted by
  accident. P26-1 defines no agency-deletion endpoint.
- `ON DELETE SET NULL` on the two operator references: removing a person must
  never cascade-delete customer records.
- `leads.assigned_to VARCHAR(200)` is **left completely untouched**. It is
  written by P17–P25 code paths and read by `followup/` and `flow/`.
  `assigned_agent_id` is the authoritative assignment for visibility;
  `assigned_to` degrades to a legacy display/routing string. They are never
  synchronised implicitly — AGENTS.md forbids mapping one state onto another.

### 3.4 Structural cross-agency guarantees (added in `030`)

Three composite foreign keys turn "a service must remember to check" into "the
database refuses":

```sql
-- Prerequisite referencable key for G-1 only.
-- contacts has no (agency_id, id) uniqueness today, so G-1's FK needs one.
-- agency_memberships already has UNIQUE (agency_id, operator_user_id),
-- which is exactly the column list G-2/G-3 reference: no extra key is added.
ALTER TABLE contacts ADD CONSTRAINT contacts_agency_scope_unq UNIQUE (agency_id, id);

-- G-1: a lead can never point at a contact in another agency
ALTER TABLE leads ADD CONSTRAINT leads_contact_same_agency_fk
    FOREIGN KEY (agency_id, contact_id) REFERENCES contacts (agency_id, id);

-- G-2/G-3: a record can only be assigned to an operator of its own agency
ALTER TABLE contacts ADD CONSTRAINT contacts_agent_same_agency_fk
    FOREIGN KEY (agency_id, assigned_agent_id)
    REFERENCES agency_memberships (agency_id, operator_user_id);
ALTER TABLE leads ADD CONSTRAINT leads_agent_same_agency_fk
    FOREIGN KEY (agency_id, assigned_agent_id)
    REFERENCES agency_memberships (agency_id, operator_user_id);
```

G-2/G-3 rely on PostgreSQL's default `MATCH SIMPLE` semantics: a composite FK
is **not** checked when any of its columns is `NULL`. Since `assigned_agent_id`
is nullable, unassigned records are unaffected; an assigned record is verified
against a real membership in the same agency. This is exactly the desired
behaviour and is asserted by a dedicated test.

G-1 additionally makes the §14 T-3 attack (create a lead against another
agency's contact) impossible at the storage layer even if every application
check were removed.

### 3.5 Agency-aware indexes (added in `030`)

```sql
CREATE INDEX IF NOT EXISTS idx_contacts_agency_created    ON contacts   (agency_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_contacts_agency_agent      ON contacts   (agency_id, assigned_agent_id);
CREATE INDEX IF NOT EXISTS idx_contacts_agency_email      ON contacts   (agency_id, email_normalized);
CREATE INDEX IF NOT EXISTS idx_contacts_agency_phone      ON contacts   (agency_id, phone_normalized);
CREATE INDEX IF NOT EXISTS idx_leads_agency_created       ON leads      (agency_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_leads_agency_agent         ON leads      (agency_id, assigned_agent_id);
CREATE INDEX IF NOT EXISTS idx_leads_agency_contact       ON leads      (agency_id, contact_id);
CREATE INDEX IF NOT EXISTS idx_activities_agency_occurred ON activities (agency_id, occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_agency_due           ON tasks      (agency_id, due_at, id DESC);
```

Each one mirrors an `ORDER BY` that already exists in `core/repository.py`
(`created_at DESC, id DESC` for contacts and leads at `:45`/`:137`,
`occurred_at DESC, id DESC` at `:404`, `due_at NULLS LAST, created_at DESC, id
DESC` at `:442`) with `agency_id` prefixed. The two agency-aware
email/phone indexes exist specifically to keep the public-STIMA bridge's
identity lookup (§11) fast once it becomes agency-scoped.

The pre-existing single-column indexes are **not dropped** — P26-0 §6 rule 8
is additive-only, and other modules (§1.10) still query these tables without an
agency predicate.

`CREATE INDEX CONCURRENTLY` is **not** used. On TEST-scale tables a plain
`CREATE INDEX` is correct and keeps `030` inside the single runner-owned
transaction that also carries its ledger row. If PROD volumes later require
concurrency, that is a separate
`-- NON-TRANSACTIONAL` migration under rule 10, and it belongs to the PROD
rollout phase, not to P26-1.

### 3.6 Agency integrity for `activities` and `tasks`

`activities` and `tasks` are written by `flow/`, `followup/`, `buy/`,
`property/` and `core/` — modules that P26-1 does not modify. Requiring each to
supply `agency_id` would be a cross-module change of exactly the kind AGENTS.md
forbids without explicit approval.

`030` therefore installs one trigger per table. **The trigger validates before
it derives.** A trigger that only filled in a missing value would be a
convenience; a row carrying an explicit `agency_id` that contradicts its own
references, or referencing a lead and a contact in two different agencies,
would pass straight through it. Both are cross-agency corruption, and neither
is caught by any other control: `activities` and `tasks` have no composite FK
equivalent to §3.4 G-1, because their three reference columns are
independently nullable.

**Integrity rule, in full.**

1. Resolve the agency implied by every non-`NULL` reference: `lead_id` →
   `leads.agency_id`; `contact_id` → `contacts.agency_id`; `stima_id` →
   `lead_stime` → `leads.agency_id`.
2. Every pair of resolved agencies must be equal. Any disagreement **raises**.
3. If `NEW.agency_id` is supplied and a resolved agency exists, the two must be
   equal. A mismatch **raises**. The supplied value is kept, never overwritten.
4. If `NEW.agency_id` is `NULL`, it is set to the resolved agency.
5. The Default Agency fallback applies in exactly one shape and no other.

```sql
CREATE OR REPLACE FUNCTION core_agency_integrity() RETURNS trigger AS $fn$
DECLARE
    a_lead     BIGINT;
    a_contact  BIGINT;
    a_stima    BIGINT;
    resolved   BIGINT;
BEGIN
    -- 1. resolve every available reference
    IF NEW.lead_id IS NOT NULL THEN
        SELECT agency_id INTO a_lead FROM leads WHERE id = NEW.lead_id;
    END IF;
    IF NEW.contact_id IS NOT NULL THEN
        SELECT agency_id INTO a_contact FROM contacts WHERE id = NEW.contact_id;
    END IF;
    IF NEW.stima_id IS NOT NULL THEN
        SELECT l.agency_id INTO a_stima
          FROM lead_stime ls JOIN leads l ON l.id = ls.lead_id
         WHERE ls.stima_id = NEW.stima_id
         ORDER BY ls.id LIMIT 1;
    END IF;

    -- 2. every non-null reference must agree with every other
    IF a_lead IS NOT NULL AND a_contact IS NOT NULL AND a_lead <> a_contact THEN
        RAISE EXCEPTION
            'P26-1 agency integrity on %: lead % is in agency %, contact % is in agency %',
            TG_TABLE_NAME, NEW.lead_id, a_lead, NEW.contact_id, a_contact;
    END IF;
    IF a_lead IS NOT NULL AND a_stima IS NOT NULL AND a_lead <> a_stima THEN
        RAISE EXCEPTION
            'P26-1 agency integrity on %: lead % is in agency %, stima % resolves to agency %',
            TG_TABLE_NAME, NEW.lead_id, a_lead, NEW.stima_id, a_stima;
    END IF;
    IF a_contact IS NOT NULL AND a_stima IS NOT NULL AND a_contact <> a_stima THEN
        RAISE EXCEPTION
            'P26-1 agency integrity on %: contact % is in agency %, stima % resolves to agency %',
            TG_TABLE_NAME, NEW.contact_id, a_contact, NEW.stima_id, a_stima;
    END IF;

    resolved := COALESCE(a_lead, a_contact, a_stima);

    -- 3. an explicit value must agree with the references, and is preserved
    IF NEW.agency_id IS NOT NULL THEN
        IF resolved IS NOT NULL AND NEW.agency_id <> resolved THEN
            RAISE EXCEPTION
                'P26-1 agency integrity on %: explicit agency_id % contradicts agency % derived from the row references',
                TG_TABLE_NAME, NEW.agency_id, resolved;
        END IF;
        RETURN NEW;
    END IF;

    -- 4. derive
    IF resolved IS NOT NULL THEN
        NEW.agency_id := resolved;
        RETURN NEW;
    END IF;

    -- 5. bounded fallback: the P18 post-failed-bridge shape, and only that
    IF NEW.stima_id IS NOT NULL
       AND NEW.lead_id IS NULL
       AND NEW.contact_id IS NULL THEN
        SELECT id INTO NEW.agency_id
          FROM agencies WHERE slug = 'stima360' AND status = 'active';
        IF NEW.agency_id IS NULL THEN
            RAISE EXCEPTION 'P26-1 agency integrity on %: default agency missing', TG_TABLE_NAME;
        END IF;
        RETURN NEW;
    END IF;

    RAISE EXCEPTION
        'P26-1 agency integrity on %: agency_id could not be resolved from contact_id=%, lead_id=%, stima_id=%',
        TG_TABLE_NAME, NEW.contact_id, NEW.lead_id, NEW.stima_id;
END;
$fn$ LANGUAGE plpgsql;
```

Attached to both tables on insert **and** on a reference-changing update:

```sql
CREATE TRIGGER trg_activities_agency_integrity
    BEFORE INSERT OR UPDATE OF agency_id, contact_id, lead_id, stima_id
    ON activities FOR EACH ROW EXECUTE FUNCTION core_agency_integrity();
-- identical trigger on tasks
```

Firing on `UPDATE OF` those four columns closes a hole the insert-only form
leaves open: repointing an existing task at another agency's lead. The column
list keeps unrelated updates (`status`, `completed_at`, `updated_at` — the
common path in `core/repository.py` and `followup/repository.py:333`) free of
trigger cost.

**Why the fallback is bounded to that one shape.** It is reachable by exactly
one real P26-1 flow: `main.py` calls
`followup_service.safe_run_followup(..., stima_id=new_id, contact_id=(bridge_result or {}).get('contact_id'), lead_id=…)`
*after* the bridge, and both ids are `None` when the bridge returned
`skipped`, `conflict` or `error`. The resulting `tasks` row carries `stima_id`
alone, with no `lead_stime` link to resolve through — the shape `main.py`'s own
comment describes as satisfying `tasks_reference_chk` unaided. No other writer
produces it. Every other unresolvable shape raises rather than silently
defaulting, so the Default Agency can never become a dumping ground for rows
whose real agency was simply not looked up.

**Ordering dependency.** The trigger is created in `030`, after the `029`
backfill has completed. It therefore never fires during the backfill, and the
backfill's `UPDATE … SET agency_id` needs no exemption. Reversing that order
would make `029` fire the trigger on every row; it must not be reordered.

Created idempotently with the `pg_trigger` catalogue-check pattern already
proven in `migrations/026_p26_baseline.sql`.

The trigger is deliberate and has a cost: it is invisible to a reader of the
Python code, and it converts some previously-successful writes into errors. Both
are accepted, recorded here, and covered by
`tests/test_p26_1_agency_integrity.py` (§15, tests 58–67). The alternative —
editing five modules certified in P17–P25 — is strictly worse, and the
validating form is what makes the trigger a control rather than a convenience.

---

## 4. Migration `027` design — `027_p26_agency_identity`

**Scope.** Agency and operator identity only. Touches no existing table.

Contents, in order:

1. `CREATE TABLE agencies` (§3.1).
2. `CREATE TABLE operator_users` (§3.2).
3. `CREATE TABLE agency_memberships` (§3.2).
4. `CREATE TABLE operator_sessions` (§3.2).
5. The four indexes and two partial unique indexes of §3.2.
6. The Default Agency row, idempotently:

```sql
INSERT INTO agencies (name, slug, status, settings)
SELECT 'STIMA360', 'stima360', 'active', '{}'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM agencies WHERE slug = 'stima360');
```

**Corrected — the migration writes no ledger row.** An earlier draft of this
spec listed a seventh step: `INSERT INTO schema_migrations (…) VALUES
('027_p26_agency_identity', …)` inside the same transaction, citing P26-0
rule 5. **That instruction is withdrawn.** `scripts/p26_migrate.py`
`register()` owns the `schema_migrations` insert: it supplies `checksum_up`,
`checksum_down`, `down_available`, `transactional` and `execution_ms`, none of
which a file can know about itself. A self-insert would collide with
`register()` on the `version` primary key at apply time. Migration `026`
contains no self-ledger insert either.

**Transaction ownership.** From `027` the **runner** owns the UP transaction:
it executes the migration body, calls `register()`, and commits both in one
transaction, rolling back on any exception. `027` and every later UP file
therefore contains **no `BEGIN;` and no `COMMIT;`**. The paired `_down.sql`
keeps its own `BEGIN;`/`COMMIT;`, because down files are executed manually and
must bracket themselves. This supersedes P26-0 §6 rules 3 and 5 for versions
`>= 027`; see `docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md`.

Compliance with P26-0 §6: no `BEGIN;`/`COMMIT;` in the UP file (rule 3 as
superseded by the atomicity addendum); every object `IF NOT EXISTS` and the row
guarded by `WHERE NOT EXISTS` (rule 4); DDL and ledger row in one
runner-owned transaction (rule 5 as superseded); no `current_database()` guard
(rule 6); one file for all environments (rule 7); additive only (rule 8);
`agency_id` naming not yet applicable (rule 9); no `CONCURRENTLY` (rule 10); no
secrets (rule 11).

The Default Agency row is a **data** write inside a migration. It is admitted
because the table is created by the same migration and is empty by
construction, and because the approved architecture §7 requires the agency to
be real before any backfill can reference it. **No operator user is created by
`027`** — see §17 for why seeding is a script.

**`027_p26_agency_identity_down.sql`** is a genuine reversal:

```sql
-- The down file keeps its own transaction: it is executed manually, not by
-- the runner, so it must bracket itself.
BEGIN;
DROP TABLE IF EXISTS operator_sessions;
DROP TABLE IF EXISTS agency_memberships;
DROP TABLE IF EXISTS operator_users;
DROP TABLE IF EXISTS agencies;
UPDATE schema_migrations
   SET rolled_back_at = NOW(), rolled_back_by_operator = current_setting('p26.operator')
 WHERE version = '027_p26_agency_identity';
COMMIT;
```

Drop order is FK-reverse. The down is only valid while `028` has not run: once
`contacts.agency_id` references `agencies`, `DROP TABLE agencies` fails on the
`RESTRICT` FK — which is the correct, loud failure, not a silent cascade.

---

## 5. Migration `028` design — and why the approved 2-migration split becomes 4

### 5.1 The split recommendation

The approved architecture proposes:

```
027_p26_agency_identity
028_p26_core_agency_scope   ← columns + backfill + constraints + indexes
```

P26-0 §6 rule 9 (already certified and in force) reads:

> `agency_id`, never `tenant_id`. Introduced as **nullable**, then
> **backfilled**, then **constrained** — in **separate, successive**
> migrations. Never `NOT NULL` at creation on a populated table.

The proposed `028` performs all three steps in one file. **The rule wins.**
Recommended split:

| Version | Name | Content | Reversible? |
|---|---|---|---|
| `027` | `p26_agency_identity` | 4 identity tables, indexes, Default Agency row | Yes |
| `028` | `p26_core_agency_columns` | nullable `agency_id`/`assigned_agent_id`/`created_by_user_id` on 4 tables + plain indexes | Yes |
| `029` | `p26_core_agency_backfill` | `UPDATE … SET agency_id = <default>` on 4 tables | **No** (RAISE) |
| `030` | `p26_core_agency_enforce` | `SET NOT NULL`, composite FKs, agency-aware indexes, derive triggers | Yes |

Four benefits that are not merely procedural:

1. `029` can be run, its row counts verified against the pre-migration counts,
   and **only then** may `030` run. A single fused migration offers no such
   gate.
2. If `030` fails on a `NOT NULL` violation, the failure names the exact rows
   that `029` missed, and `028`+`029` remain applied and correct.
3. `029` is the only irreversible step, so exactly one down file has to refuse
   (rule 2 / P26-0 §7.3) instead of the whole change being irreversible.
4. `verify_contiguous` in `scripts/p26_migrate.py` is satisfied by
   `026,027,028,029,030` with no gap.

### 5.2 `028_p26_core_agency_columns`

```sql
ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS agency_id          BIGINT REFERENCES agencies(id)       ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS assigned_agent_id  BIGINT REFERENCES operator_users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS created_by_user_id BIGINT REFERENCES operator_users(id) ON DELETE SET NULL;

ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS agency_id          BIGINT REFERENCES agencies(id)       ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS assigned_agent_id  BIGINT REFERENCES operator_users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS created_by_user_id BIGINT REFERENCES operator_users(id) ON DELETE SET NULL;

ALTER TABLE activities
    ADD COLUMN IF NOT EXISTS agency_id          BIGINT REFERENCES agencies(id)       ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS created_by_user_id BIGINT REFERENCES operator_users(id) ON DELETE SET NULL;

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS agency_id          BIGINT REFERENCES agencies(id)       ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS created_by_user_id BIGINT REFERENCES operator_users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_contacts_agency_id   ON contacts   (agency_id);
CREATE INDEX IF NOT EXISTS idx_leads_agency_id      ON leads      (agency_id);
CREATE INDEX IF NOT EXISTS idx_activities_agency_id ON activities (agency_id);
CREATE INDEX IF NOT EXISTS idx_tasks_agency_id      ON tasks      (agency_id);

-- No BEGIN/COMMIT and no ledger insert: the runner owns the transaction
-- and register() writes the schema_migrations row (atomicity addendum).
```

No `DEFAULT` on `agency_id`: a default would silently make every future insert
correct-looking and would remove the ability of `030`'s `SET NOT NULL` to prove
that the backfill actually covered every row.

`028_down` drops the eleven columns and four indexes. It destroys assignment
data, which is acceptable and expected in the `028` rollback window (§16).

### 5.3 `030_p26_core_agency_enforce`

```sql
ALTER TABLE contacts   ALTER COLUMN agency_id SET NOT NULL;
ALTER TABLE leads      ALTER COLUMN agency_id SET NOT NULL;
ALTER TABLE activities ALTER COLUMN agency_id SET NOT NULL;
ALTER TABLE tasks      ALTER COLUMN agency_id SET NOT NULL;

-- §3.4 contacts_agency_scope_unq + three composite FKs
-- §3.5 nine agency-aware indexes
-- §3.6 core_agency_integrity() + two BEFORE INSERT OR UPDATE triggers

-- No BEGIN/COMMIT and no ledger insert: the runner owns the transaction
-- and register() writes the schema_migrations row (atomicity addendum).
```

`SET NOT NULL` scans each table and aborts the whole transaction on the first
`NULL`. That abort **is** the verification gate: `030` cannot succeed on a
partially backfilled database.

Order matters and is fixed: `SET NOT NULL` first (cheapest failure), then the
composite FKs (which validate existing rows), then indexes, then the
`core_agency_integrity()` triggers **last**. The triggers go last for the
reason given in §3.6: they must not fire on anything `029` or the earlier
statements of `030` touch. `SET NOT NULL` and `ADD CONSTRAINT` do not fire row
triggers, so this ordering is a defence-in-depth choice rather than a
correctness requirement — but it is fixed, not incidental.

The `ADD CONSTRAINT … UNIQUE`/`FOREIGN KEY` statements have no
`IF NOT EXISTS` form in PostgreSQL. Idempotency uses the catalogue-check
`DO $do$ … pg_constraint … $do$` pattern, the same mechanism `026` uses for
`pg_trigger` (P26-0 §5.5, rule 4).

`030_down` drops the two triggers and `core_agency_integrity()`, drops the
three composite FKs and the single unique key `contacts_agency_scope_unq`,
drops the nine indexes, and `DROP NOT NULL` on the four columns. Fully
reversible.

---

## 6. Legacy backfill strategy — `029_p26_core_agency_backfill`

### 6.1 Which tables require backfill

Exactly four: `contacts`, `leads`, `activities`, `tasks`. Determined by §3.3,
not assumed.

`contact_roles` and `lead_stime` require **no** backfill and **no**
`agency_id`: neither is reachable by any endpoint except through its parent
(`GET /api/core/contacts/{id}` reads `contact_roles WHERE contact_id = %s`;
`GET /api/core/leads/{id}` reads `lead_stime WHERE lead_id = %s`;
`POST`/`DELETE /api/core/leads/{id}/stime/{stima_id}` both resolve the lead
first). Once the parent lookup is scoped, the child is unreachable
cross-agency. Adding `agency_id` to them would be denormalisation with no
enforcement value.

### 6.2 The migration

```sql
UPDATE contacts   SET agency_id = (SELECT id FROM agencies WHERE slug='stima360') WHERE agency_id IS NULL;
UPDATE leads      SET agency_id = (SELECT id FROM agencies WHERE slug='stima360') WHERE agency_id IS NULL;
UPDATE activities SET agency_id = (SELECT id FROM agencies WHERE slug='stima360') WHERE agency_id IS NULL;
UPDATE tasks      SET agency_id = (SELECT id FROM agencies WHERE slug='stima360') WHERE agency_id IS NULL;

-- Fail loudly rather than leave a NULL for 030 to find.
DO $do$
DECLARE n BIGINT;
BEGIN
    SELECT (SELECT COUNT(*) FROM contacts   WHERE agency_id IS NULL)
         + (SELECT COUNT(*) FROM leads      WHERE agency_id IS NULL)
         + (SELECT COUNT(*) FROM activities WHERE agency_id IS NULL)
         + (SELECT COUNT(*) FROM tasks      WHERE agency_id IS NULL) INTO n;
    IF n > 0 THEN
        RAISE EXCEPTION 'P26-1 backfill incomplete: % rows still NULL', n;
    END IF;
END
$do$;

-- No BEGIN/COMMIT and no ledger insert: the runner owns the transaction
-- and register() writes the schema_migrations row (atomicity addendum).
```

`WHERE agency_id IS NULL` makes re-execution a zero-row no-op (rule 4). The
subselect on `slug` returns `NULL` if `027` did not run, and `SET agency_id =
NULL` would then leave the guard to raise — the failure is loud either way.

`assigned_agent_id` and `created_by_user_id` are **not** backfilled. There is
no operator identity in the legacy data to attribute them to, and inventing one
would repeat exactly the error P26-0 refused when it declined to retro-register
migrations `001`–`025`. They stay `NULL`, which §9.3 defines precisely: an
unassigned record is invisible to `agent`, visible to `agency_owner`/
`agency_admin`.

### 6.3 FK and ordering constraints

- `029` **must** follow `027` (the `agencies` row must exist) and `028` (the
  column must exist). `verify_contiguous` plus `build_plan` in
  `scripts/p26_migrate.py` already enforce ascending order; no extra mechanism
  is needed.
- `030`'s composite FK `leads (agency_id, contact_id) → contacts (agency_id,
  id)` validates every existing lead. Because `029` assigns *all* legacy rows
  to the same agency, every existing `(agency_id, contact_id)` pair resolves.
  Should any lead reference a contact id that does not exist, the FK would
  fail — but `leads.contact_id` is already `NOT NULL REFERENCES contacts(id)`,
  so that cannot occur.
- Verification between `029` and `030` (§17, step 7) compares
  `COUNT(*) GROUP BY agency_id` on the four tables against the pre-`028` totals
  recorded in the same session. Equality is the pass condition.

### 6.4 Related tables that would break isolation if left unscoped

Recorded here as the audited answer, not as P26-1 work:

| Table | Reaches CORE via | Exposed by | Handled in P26-1 by |
|---|---|---|---|
| `property_contacts`, `property_leads`, `property_visits` | `contact_id`, `lead_id` | `/api/property/*` | Route allowlist (D-1) |
| `buy_requests`, `buy_request_task_links` | `contact_id`, `lead_id`, `task_id` | `/api/buy/*` | Route allowlist |
| `property_matches` | `buy_requests` → `contacts` | `/api/match/*` | Route allowlist |
| `property_proposals`, `property_sales`, `property_sales_contacts` | `contacts` | `/api/proposals`, `/api/sales` | Route allowlist |
| `owner_accounts` | `contact_id UNIQUE` | `/api/owner/admin/*` | Route allowlist |
| `seller_timeline_events` | `contact_id`, `lead_id` | `/api/seller-intelligence/timeline?contact_id=` | Route allowlist |
| `followup_actions` | `tasks` ⋈ `leads` | `/api/followup/*` | Route allowlist |
| `next_best_actions` | `contact_id` | `/api/next-best-action` | Route allowlist |
| `seller_revival_suppressions` | `contact_id UNIQUE` | no HTTP route (batch) | No exposure |
| `flow_*` | `leads` via `flow/adapters.py` | `/api/flow/*` | Route allowlist |
| `stime`, `stime_dettagliate` | `lead_stime` | `/api/admin/stime*`, `/api/prefill` | Out of scope (§19, R-2/R-6) |

---

## 7. Operator authentication / session design

New package `operator_auth/`, following the established per-domain layout
(`__init__.py`, `enums.py`, `security.py`, `context.py`, `dependencies.py`,
`repository.py`, `service.py`, `router.py`, `schemas.py`).

### 7.1 Constants (`operator_auth/enums.py`)

```python
COOKIE_NAME               = 'stima360_operator_session'
SESSION_MAX_HOURS         = 12     # absolute lifetime; forces a daily re-login
SESSION_IDLE_MINUTES      = 240    # 4h idle window; survives a working morning
PBKDF2_ITERATIONS         = 600_000
AGENCY_ROLES              = ('agency_owner', 'agency_admin', 'agent')
DEFAULT_AGENCY_SLUG       = 'stima360'
```

`owner/enums.py` uses 12h/60min. The absolute lifetime is kept identical; the
idle window is widened to 4h because an operator works inside the OS Shell all
day, whereas an owner opens the portal briefly. Both numbers are stated here so
they are a decision, not a copy.

### 7.2 Crypto (`operator_auth/security.py`)

Four functions, intentionally **duplicated** from `owner/security.py` rather
than imported: AGENTS.md forbids introducing cross-module dependencies, and
`operator_auth` must not depend on `owner`.

```python
def generate_session_token() -> str:      # secrets.token_urlsafe(32)
def hash_session_token(raw: str) -> str:  # hashlib.sha256(...).hexdigest()
def hash_password(raw: str) -> str:       # pbkdf2_sha256$<iter>$<salt>$<hash>
def verify_password(raw: str, stored: str) -> bool:  # hmac.compare_digest
def set_cookie(response, token) -> None
def clear_cookie(response) -> None
```

`set_cookie` uses `httponly=True, secure=True, samesite='lax', path='/',
max_age=SESSION_MAX_HOURS*3600` — byte-identical policy to
`owner/security.py`. `Secure` is unconditional: TEST and PROD both serve over
HTTPS, and a conditional flag would be a configuration foot-gun.

`SameSite=Lax` (not `Strict`): the OS Shell is same-origin with the API, so
`Lax` is sufficient against cross-site POST, and `Strict` would break a
top-level navigation back into the Shell.

### 7.3 Endpoints (`/api/operator-auth`, no router-level dependency)

```
POST /api/operator-auth/login    {email, password}      → 204 + Set-Cookie
POST /api/operator-auth/logout                          → 204 + cookie cleared
GET  /api/operator-auth/me       (Depends require_operator) → context projection
```

`login`:

1. Normalise the email with the existing `core.normalization.normalize_email`
   (reuse, not reimplementation).
2. `SELECT … FROM operator_users WHERE email_normalized = %s`.
3. **Always** call `verify_password` — against the found hash, or against a
   fixed dummy hash of the same shape when no row was found. This removes the
   timing difference that would otherwise reveal which addresses are registered.
4. Reject with a single generic `401 {"detail": "Credenziali non valide."}`
   when: no row, wrong password, `status <> 'active'`, no `active` membership
   **and** `is_platform_admin = FALSE`, or the membership's agency is not
   `active`. One message for all six cases — the reason is never disclosed.
5. Generate the token, `INSERT INTO operator_sessions (operator_user_id,
   token_hash, expires_at)` with `expires_at = NOW() + interval`, update
   `last_login_at`, set the cookie. The raw token is returned **only** in the
   `Set-Cookie` header, never in a response body.

`logout`: `UPDATE operator_sessions SET revoked_at = NOW() WHERE token_hash=%s
AND revoked_at IS NULL`, then `clear_cookie`. Returns `204` whether or not a
session was found — no oracle.

`me`: `{"user_id", "agency_id", "agency_name", "role", "is_platform_admin",
"expires_at"}`. No email, no hash, no session id.

### 7.4 Session resolution — one query

```sql
SELECT s.id                AS session_id,
       s.expires_at, s.last_seen_at,
       u.id                AS user_id,
       u.status            AS user_status,
       u.is_platform_admin,
       m.agency_id, m.role, m.status AS membership_status,
       a.status            AS agency_status
  FROM operator_sessions s
  JOIN operator_users    u ON u.id = s.operator_user_id
  LEFT JOIN agency_memberships m
         ON m.operator_user_id = u.id AND m.status = 'active'
  LEFT JOIN agencies a ON a.id = m.agency_id
 WHERE s.token_hash  = %s
   AND s.revoked_at IS NULL
   AND s.expires_at  > NOW()
   AND s.last_seen_at > NOW() - (%s || ' minutes')::interval
```

`LEFT JOIN` because a `platform_admin` legitimately has no membership. The
`uq_agency_memberships_single_active` partial index guarantees at most one row,
so the join cannot multiply.

Rejection (→ `401`, session cookie cleared) when: no row;
`user_status <> 'active'`; the operator is not a platform admin and either
`membership_status IS NULL` or `agency_status <> 'active'`.

On success, `UPDATE operator_sessions SET last_seen_at = NOW() WHERE id = %s`
in the same request. This is one extra write per request — the same cost
`owner/` already pays and the price of a genuinely enforceable idle timeout.

---

## 8. `OperatorContext` design

```python
# operator_auth/context.py
@dataclass(frozen=True)
class OperatorContext:
    user_id: int | None          # None only for auth_channel='legacy_basic'
    agency_id: int | None        # None only for a platform_admin with no membership
    role: str | None             # 'agency_owner' | 'agency_admin' | 'agent' | None
    is_platform_admin: bool
    session_id: int | None
    auth_channel: str            # 'operator_session' | 'legacy_basic'

    @property
    def sees_all_agency_records(self) -> bool:
        return self.is_platform_admin or self.role in ('agency_owner', 'agency_admin')

    @property
    def may_assign_records(self) -> bool:
        return self.sees_all_agency_records

    def require_agency(self) -> int:
        if self.agency_id is None:
            raise PlatformAdminAgencyRequired()
        return self.agency_id
```

`frozen=True` is load-bearing: no service or repository can mutate the scope
mid-request. There is no setter, no `with_agency()`, no override.

**Required flow, exactly as approved:**

```
HTTP request
  → Depends(require_operator)          operator_auth/dependencies.py
  → OperatorContext (frozen)
  → core/service.py   f(ctx, …)
  → core/scope.py     scoped_source(ctx, table, alias)
  → core/repository.py  parameterised SQL carrying the predicate
  → PostgreSQL
```

`require_operator` is the **only** producer of an `OperatorContext`. It is
defined once and imported; no other module constructs the dataclass outside
tests.

### 8.1 The second context: `SystemAgencyContext`

P26-1 has **two** context types and no third. The second exists because the
public estimation flow has no operator at all (§2.5.1):

```python
# operator_auth/context.py
@dataclass(frozen=True)
class SystemAgencyContext:
    agency_id: int
    origin: str          # 'public_stima' — the only value in P26-1
```

| | `OperatorContext` | `SystemAgencyContext` |
|---|---|---|
| Produced by | `require_operator` (a FastAPI dependency) | `core/scope.py::system_context_for_public_stima(cur)` |
| Input | the request's cookie or Basic header | an open cursor — **no `agency_id` parameter exists** |
| `agency_id` | from the operator's active membership | from `SELECT id FROM agencies WHERE slug='stima360' AND status='active'` |
| `user_id` / `role` / `is_platform_admin` | present | **absent by type** |
| May be imported by a router or `main.py` | yes | **no** (test 52c) |
| Effective scope | agency, narrowed to assigned records for `agent` | exactly one agency, never narrowed, never widened |

Neither type is constructible from client input: `OperatorContext` derives
`agency_id` from a server-side membership join, and `SystemAgencyContext`
derives it from a server-side slug lookup. The approved rule — *"`agency_id`
coming from query params, path/body payload or frontend state MUST NEVER be
trusted"* — is satisfied by construction in both, because in neither case is
`agency_id` an argument that a caller supplies.

**Extended flow:**

```
HTTP request (authenticated)          POST /api/salva_stima (anonymous)
  → Depends(require_operator)           → core/service.bridge_public_stima
  → OperatorContext (frozen)            → core/repository.bridge_public_stima
        \                                     → system_context_for_public_stima(cur)
         \                                    → SystemAgencyContext (frozen)
          \                                  /
           → core/scope.scoped_source(ctx, table, alias)   [AgencyScope protocol]
           → core/repository.py  parameterised SQL carrying the predicate
           → PostgreSQL
```

Naming follows the repository's convention of plain module-level functions and
`PascalCase` dataclasses (AGENTS.md). `OperatorContext` is preferred over the
`AuthContext` mentioned in the P26-0 preamble because P26-1 has two distinct
authenticated principals — the *operator* (this) and the *owner*
(`owner/dependencies.current_owner`) — and a generic name would blur them.
`SystemAgencyContext` is named for what it is: a scope with no principal.

---

## 9. Repository / service scoping strategy

### 9.1 The predicate builder

```python
# core/scope.py
SCOPED_TABLES = frozenset({'contacts', 'leads', 'activities', 'tasks'})
AGENT_ASSIGNABLE = frozenset({'contacts', 'leads'})

def scoped_source(ctx: AgencyScope, table, alias):
    """Return ('<table> <alias> WHERE <predicate>', params) — never without.

    Accepts either context type (§8.1). SystemAgencyContext presents
    role=None / user_id=None / is_platform_admin=False, so it takes the
    plain single-agency branch: no agent narrowing, no WHERE TRUE.
    """
    if table not in SCOPED_TABLES:
        raise ProgrammingError(f'{table} is not a scoped CORE table')
    if ctx.is_platform_admin and ctx.agency_id is None:
        return f'{table} {alias} WHERE TRUE', []
    parts  = [f'{alias}.agency_id = %s']
    params = [ctx.require_agency()]
    if ctx.role == 'agent' and table in AGENT_ASSIGNABLE:
        parts.append(f'{alias}.assigned_agent_id = %s')
        params.append(ctx.user_id)
    return f'{table} {alias} WHERE ' + ' AND '.join(parts), params


def system_context_for_public_stima(cur) -> SystemAgencyContext:
    """The ONLY factory for a SystemAgencyContext.

    Takes an open cursor and nothing else: there is no agency_id parameter,
    so the value can never originate in a request.
    """
    cur.execute("SELECT id FROM agencies WHERE slug = %s AND status = 'active'",
                (DEFAULT_AGENCY_SLUG,))
    row = cur.fetchone()
    if row is None:
        raise ConflictError('default agency missing')
    return SystemAgencyContext(agency_id=row['id'], origin='public_stima')
```

The `WHERE TRUE` branch is reachable only by a `platform_admin` holding no
membership. `SystemAgencyContext` sets `is_platform_admin=False` and always
carries a resolved integer `agency_id`, so the public bridge can never reach
that branch — it is structurally confined to the single-agency predicate.

An `agent` sees `activities` and `tasks` for the whole agency, because those
tables have no `assigned_agent_id` (§3.3) and their existing free-text
`assigned_to` is written by automation, not by assignment. Stated plainly so it
is a decision rather than an omission; narrowing it belongs to P26-2.

### 9.2 Applying it

Every existing `core/repository.py` function is rewritten to the shape:

```python
def list_contacts(ctx, limit, offset, search, status):
    source, params = scoped_source(ctx, 'contacts', 'c')
    where, extra = [], []
    if status: where.append('c.status = %s'); extra.append(status)
    if search:  …
    clause = (' AND ' + ' AND '.join(where)) if where else ''
    params = params + extra + [limit, offset]
    with core_cursor() as (_, cur):
        cur.execute(f'SELECT c.* FROM {source}{clause} '
                    f'ORDER BY c.created_at DESC, c.id DESC LIMIT %s OFFSET %s', params)
```

The `WHERE` keyword is emitted by `scoped_source`, so every added filter is an
`AND` on top of an already-scoped set. A developer cannot accidentally replace
the scope with their own `WHERE`.

Writes:

- `create_contact` / `create_lead` set `agency_id = ctx.require_agency()` and
  `created_by_user_id = ctx.user_id` **from the context**, never from the
  payload (which cannot carry them — §1.7).
- `update_contact` / `update_lead` / `update_task` run
  `UPDATE … WHERE id = %s AND <scope predicate>`; `cur.rowcount == 0` raises
  `NotFoundError` → `404` (D-6). The existing `RETURNING *` / `if not row`
  shape already produces this; only the predicate is added.
- `_ensure_exists(cur, table, id, label)` (`core/repository.py:18`) becomes
  `_ensure_exists_scoped(ctx, cur, table, id, label)` and appends the same
  predicate. This single helper guards `create_lead`, `link_stima`,
  `add_contact_role` and `_validate_references` — four call sites closed at
  once.

### 9.3 Visibility semantics, stated exhaustively

| Caller | `contacts` / `leads` | `activities` / `tasks` |
|---|---|---|
| `platform_admin`, no membership | all agencies | all agencies |
| `platform_admin` with a membership | own agency | own agency |
| `agency_owner` | all in own agency | all in own agency |
| `agency_admin` | all in own agency | all in own agency |
| `agent` | own agency **and** `assigned_agent_id = self` | all in own agency |
| legacy Basic (D-2) | all in Default Agency | all in Default Agency |

A record with `assigned_agent_id IS NULL` is **invisible to every `agent`**.
That is the literal reading of the approved rule ("agent sees only records
assigned to their operator user"), and it has a real operational consequence:
public-STIMA contacts and leads land unassigned (§11) and are therefore visible
only to owner/admin until someone assigns them. This is a deliberate,
documented behaviour, not a defect. The assignment endpoint in §10 is what
makes it workable.

### 9.4 `stima_id`-only filters

`GET /api/core/activities?stima_id=` and `GET /api/core/tasks?stima_id=` keep
working, because the scope predicate is `agency_id`-based, not parent-based.
A forged `stima_id` belonging to another agency's lead now returns an empty
`items` list rather than that agency's rows. The `030` trigger (§3.6) is what
guarantees every such row has a truthful `agency_id`.

---

## 10. CORE endpoint impact map

`ctx` is `Depends(require_operator)` on every row. "New" marks the only two
endpoints P26-1 adds to `/api/core`.

| Endpoint | Change | Cross-agency result |
|---|---|---|
| `POST /api/core/contacts` | `agency_id`, `created_by_user_id` from `ctx` | n/a (cannot target another agency) |
| `GET /api/core/contacts` | scoped; `search` runs inside the scope | other agencies absent from `items` |
| `GET /api/core/contacts/{id}` | scoped lookup; `contact_roles` inherit | `404` |
| `PATCH /api/core/contacts/{id}` | scoped `UPDATE … AND scope` | `404` |
| `POST /api/core/contacts/{id}/roles` | scoped `_ensure_exists_scoped` | `404` |
| `DELETE /api/core/contacts/{id}/roles/{role}` | `DELETE … WHERE contact_id IN (scoped)` | `404` |
| `POST /api/core/leads` | scoped `contact_id` check; `agency_id`/`created_by_user_id` from `ctx` | `404` on the contact |
| `GET /api/core/leads` | scoped | absent |
| `GET /api/core/leads/{id}` | scoped; `lead_stime` inherit | `404` |
| `PATCH /api/core/leads/{id}` | scoped `UPDATE` | `404` |
| `POST /api/core/leads/{id}/stime/{sid}` | scoped lead resolution | `404` |
| `DELETE /api/core/leads/{id}/stime/{sid}` | scoped lead resolution | `404` |
| `POST /api/core/activities` | scoped `_validate_references`; `agency_id` from `ctx` | `404` on the referenced entity |
| `GET /api/core/activities` | scoped, incl. `stima_id` filter (§9.4) | absent |
| `DELETE /api/core/activities/{id}` | `DELETE … AND scope` | `404` |
| `POST /api/core/tasks` | as activities | `404` |
| `GET /api/core/tasks` | scoped | absent |
| `PATCH /api/core/tasks/{id}` | scoped `UPDATE` | `404` |
| `DELETE /api/core/tasks/{id}` | scoped `DELETE` | `404` |
| **`PATCH /api/core/contacts/{id}/assignment`** | **New.** `{assigned_agent_id: int \| null}`. `403` if `not ctx.may_assign_records`; `404` if the contact is out of scope; `400` if the target operator has no active membership in `ctx.agency_id` | `404` |
| **`PATCH /api/core/leads/{id}/assignment`** | **New.** Identical rules | `404` |

Archive/delete: as established in §1.6 there is no dedicated endpoint. Archiving
a contact is `PATCH … {"status":"archived","archived_at":…}`; closing a lead is
`PATCH … {"status":"closed"}`. Both inherit the scoped `UPDATE`, so no separate
work exists.

The two assignment endpoints are separate from `PATCH /contacts/{id}` on
purpose: `ContactUpdate`/`LeadUpdate` must keep `extra = "forbid"` with no
`assigned_agent_id` field, so that a plain update can never smuggle a
reassignment past the `may_assign_records` check.

### 10.1 Non-CORE endpoints — no change, and why

`crm`, `property`, `buy`, `match`, `proposal`, `sale`, `flow`, `owner`,
`seller_intelligence`, `followup`, `seller_intent`, `property_watch`,
`next_best_action` and all `@app` routes in `main.py` are **unchanged**. Their
`Depends(require_admin)` / `Depends(require_owner_admin)` lines are not
touched.

Two authorities, two different outcomes, and the difference must not be
blurred:

- **Operator session:** cannot reach any of them. D-1 confines the session to
  `/api/operator-auth/*` and `/api/core/*`; a cookie-only request to any route
  here returns `401` (test 52).
- **Legacy Basic:** reaches all of them, **cross-agency**, exactly as it does
  today. D-2 confines the legacy credential to the Default Agency *on
  `/api/core` only*. Everywhere in this table it remains global. See §2.2.1,
  T-21, R-9 and GATE-MA1.

That asymmetry is the honest statement of what "no change" buys: it preserves
every P17–P25 workflow at the cost of leaving the platform maintenance channel
un-narrowed, and P26-1 does not claim otherwise.

---

## 11. Public STIMA routing design

### 11.1 Where the default agency is injected

**Not** in `main.py`. `main.py:518` keeps calling
`core_service.bridge_public_stima(new_id, …)` with an unchanged signature.

The injection point is `core/repository.py:224` `bridge_public_stima`. As the
**first statement inside its existing `core_cursor(commit=True)` block**, before
the advisory lock, it builds its scope:

```python
ctx = system_context_for_public_stima(cur)   # §8.1, §9.1
```

The factory resolves `SELECT id FROM agencies WHERE slug='stima360' AND
status='active'` inside that same transaction and raises
`ConflictError('default agency missing')` if absent. `main.py` already catches
every exception from this call and logs `bridge_status=error` without failing
the public estimation, so the public flow degrades exactly as it does today for
any other bridge failure. `027` guarantees the row exists.

Three properties follow, and they are the whole point of using a context object
rather than a bare `SELECT`:

- The default agency is resolved **server-side, from the database, inside the
  transaction**. It is never a parameter, never a constant in `main.py`, and
  never reachable from the request body — which carries only estimation fields.
- Every subsequent query in the bridge goes through
  `scoped_source(ctx, 'contacts', 'c')`, the same builder every authenticated
  path uses, so §2.5 rule 2 holds here with **no exception**.
- `bridge_public_stima` is the single member of `SYSTEM_CONTEXT_FUNCTIONS`
  (§2.5.1 rule 4), and test 52b asserts both the membership and the
  first-statement position.

Placing this in the repository rather than in `main.py` or `core/service.py`
means every present and future caller of the bridge is routed identically.
`core/service.py:94` `bridge_public_stima` keeps its signature and gains
nothing — it is a data-shaping function and stays one.

### 11.2 What changes inside the bridge

Three changes, all mandatory for correctness:

1. **The identity-match queries become agency-scoped**, through the shared
   builder rather than by hand. Today (`core/repository.py:224` ff.):

   ```sql
   SELECT * FROM contacts WHERE email_normalized = %s ORDER BY id FOR UPDATE
   SELECT * FROM contacts WHERE phone_normalized = %s ORDER BY id FOR UPDATE
   ```

   become, via `source, params = scoped_source(ctx, 'contacts', 'c')`:

   ```sql
   SELECT c.* FROM contacts c WHERE c.agency_id = %s AND c.email_normalized = %s ORDER BY c.id FOR UPDATE
   SELECT c.* FROM contacts c WHERE c.agency_id = %s AND c.phone_normalized = %s ORDER BY c.id FOR UPDATE
   ```

   **This is the single most important change in P26-1.** Without it, a public
   estimation routed to the Default Agency can match a contact belonging to
   another agency and attach a `sell` lead to it — a cross-agency *write*
   through an *unauthenticated* endpoint. §14 T-1.

   The new `idx_contacts_agency_email` / `idx_contacts_agency_phone` indexes
   (§3.5) exist precisely to keep these two lookups on an index.

2. **The contact and lead inserts stamp the agency from `ctx`.** `contact_data`
   and `lead_data` (built in `core/service.py:94`) gain
   `agency_id = ctx.agency_id`; `created_by_user_id` and `assigned_agent_id`
   stay `NULL` — the public form has no operator (see §9.3 for the visibility
   consequence).

3. **The `already_linked` short-circuit is unchanged.** It resolves through
   `lead_stime` → `leads`, which are agency-stamped, and it runs before any
   identity match. Its behaviour is identical.

The advisory-lock scopes `core:contact:email:{…}` / `core:contact:phone:{…}`
gain the agency id (`core:contact:{agency_id}:email:{…}`) so two agencies
receiving the same email concurrently do not serialise against each other. This
is a throughput refinement, not a correctness requirement.

### 11.3 What must not break

| Flow | Preserved by |
|---|---|
| `POST /api/salva_stima` (public, unauthenticated) | Signature unchanged; `require_operator` is never applied to it |
| `stime` INSERT and token/price UPDATE | Not touched; `stime` gains no column |
| `contacts` / `leads` creation from the bridge | Unchanged except for the three stamped columns |
| `lead_stime` link, one-lead-per-stima rule | Unchanged |
| P17 `seller_intelligence_service.safe_record_event` | Unchanged; still receives `contact_id`/`lead_id` from `bridge_result` |
| P18 `followup_service.safe_run_followup` | Unchanged; the `tasks` row it writes gets its `agency_id` from the §3.6 trigger |
| P17 seller timeline / `/api/seller-intelligence/timeline` | Unchanged (route allowlist, D-1) |
| `/api/prefill`, `/api/stima_base`, `/api/salva_stima_dettagliata` | Untouched; they read `stime` only |

Territory-based routing is **out of scope** (§19). The default is the literal
constant `slug='stima360'`; there is no configuration key, no header, and no
query parameter that can change it. That is deliberate: a routable default is
an unauthenticated agency selector.

---

## 12. Global search implications

Per §1.8 there is no server-side search to scope. The consequences are exact:

1. `GET /api/core/contacts?search=` becomes scoped by §9.2. Its `ILIKE` runs
   over six columns **inside** the scoped set, so the free-text path cannot
   reach another agency's rows. This closes the CORE half of `searchGlobal`.
2. The `includeLeads` follow-up
   (`GET /api/core/leads?contact_id=<id>&limit=5`) is scoped by the same
   predicate. A forged `contact_id` from another agency yields an empty list,
   not a `404` — and because the Shell only ever passes ids it received from
   the already-scoped contact search, the behaviour is invisible to legitimate
   use.
3. `GET /api/property/properties?search=` and `GET /api/buy/requests?search=`
   are **not** scoped in P26-1. They are unreachable to an *operator session*
   under D-1. They **remain fully reachable, cross-agency, to the legacy Basic
   channel** — the correction of §2.2.1 applies here verbatim, and the earlier
   claim that the legacy credential is "confined to the Default Agency" is
   retracted for every route outside `/api/core`.
4. `GET /api/owner/admin/lookups/contacts?search=`
   (`owner/admin_lookup_repository.py:16`) is the **second** unscoped free-text
   contact search over `contacts` and is easy to overlook. It is behind
   `require_owner_admin` (A2): unreachable to an operator session under D-1,
   **fully reachable cross-agency to legacy Basic**. Recorded as §21 R-1 and
   §14 T-21; it must be scoped in the phase that opens `/api/owner/admin/*` to
   agency operators, and it is one of the routes frozen by the C-4 surface test.
5. `GET /api/match/matches/{id}` is an id probe, not a search. Unreachable to an
   operator session under D-1.

**Definition-of-Done wording — deliberately narrow.** "Global search scoped" is
satisfied for P26-1 when, and only when:

- **(a)** both CORE search paths are scoped (tests 36, 37); and
- **(b)** every non-CORE search path is provably unreachable **by an operator
  session** (test 52, which walks `app.routes` and fails if any route outside
  the D-1 allowlist accepts a cookie-only request); and
- **(c)** the set of non-CORE search paths reachable **by legacy Basic** equals
  the frozen list in the C-4 surface test (test 52e).

It is **not** claimed that global search is scoped against every authority.
Against legacy Basic it is not, and cannot be within P26-1's scope. That
residual is what GATE-MA1 (§2.2.3) blocks on.

---

## 13. Permission matrix

`agency_admin` LIMITED, defined exactly and minimally:

> **`agency_admin` may create, suspend and reactivate memberships whose `role`
> is `agent`, within its own agency, and may reset an `agent`'s password. It
> may not create, modify, suspend or delete a membership whose `role` is
> `agency_admin` or `agency_owner`; it may not change the agency owner; it may
> not modify `agencies.name`, `agencies.slug`, `agencies.status` or
> `agencies.settings`; and it may not delete an `operator_users` row.**

No new machinery: a single comparison of the target membership's `role` against
`{'agent'}`.

| Capability | `platform_admin` | `agency_owner` | `agency_admin` | `agent` |
|---|---|---|---|---|
| Create agency | YES | NO | NO | NO |
| Manage agency users | YES | YES | LIMITED (above) | NO |
| Change agency owner | YES | YES | NO | NO |
| See all agency records | YES | YES | YES | NO |
| See assigned records | YES | YES | YES | YES |
| Assign records | YES | YES | YES | NO |
| Cross-agency access | YES | NO | NO | NO |

Enforcement points:

- *See all / see assigned* — `core/scope.py` (§9.1). Data-layer.
- *Assign records* — `ctx.may_assign_records` on the two assignment endpoints
  (§10), `403` on failure (D-6).
- *Create agency*, *manage agency users*, *change agency owner* — P26-1 defines
  **no HTTP endpoint** for any of these. Agencies, operators and memberships
  are created in TEST by the seeding script of §17. The matrix rows are the
  authorisation rules those future endpoints must implement; encoding them now
  in a `operator_auth/permissions.py` predicate table (pure functions, no
  routes) makes the P26-2 endpoints a wiring exercise and lets the test matrix
  (§15) assert the rules today. Creating the endpoints themselves would be
  scope expansion.
- *Cross-agency* — `scoped_source` returns `WHERE TRUE` only for a
  `platform_admin` with no membership; for everyone else `require_agency()`
  raises rather than returning an unscoped query.

---

## 14. Security threat model

The canonical scenario from the approved architecture: Agency A (operator A,
contact A, lead A), Agency B (operator B, contact B, lead B); operator A
manually requests B's ids.

| # | Threat | Vector | Control | Residual |
|---|---|---|---|---|
| T-1 | **Cross-agency write via the public bridge** | `POST /api/salva_stima` with an email that exists in Agency B; the unscoped identity match attaches a Default-Agency lead to B's contact | §11.2 change 1: `agency_id = %s AND email_normalized = %s`. Unauthenticated, so no auth control can substitute | None once applied. **Highest severity in this audit.** |
| T-2 | Cross-agency read by id | `GET /api/core/{contacts,leads,tasks,activities}/{B id}` | `scoped_source` predicate; `404` (D-6) | None |
| T-3 | Cross-agency write by FK | `POST /api/core/leads {"contact_id": <B contact>}` | `_ensure_exists_scoped` → `404`; **and** the `leads_contact_same_agency_fk` composite FK (§3.4 G-1) at the storage layer | None. Two independent layers |
| T-4 | Forged `agency_id` in a body | `POST/PATCH` with `{"agency_id": <B>}` | `CoreModel.Config.extra='forbid'` → `422` before the handler (§1.7); the field is never added to a schema | None |
| T-5 | Forged `agency_id` in a query string | `?agency_id=<B>` | FastAPI ignores undeclared query params; no CORE handler declares one | None |
| T-6 | Query-string pivot via `stima_id` | `GET /api/core/activities?stima_id=<B's stima>` | Scope is `agency_id`-based, not parent-based (§9.4); the §3.6 trigger *validates* rather than merely fills, so `agency_id` cannot disagree with the row's own references | None |
| T-7 | Cross-agency assignment | `PATCH /leads/{A id}/assignment {"assigned_agent_id": <B operator>}` | Membership check → `400`; **and** `leads_agent_same_agency_fk` (§3.4 G-2/G-3) | None |
| T-8 | Search leakage, operator session | `GET /api/core/contacts?search=<B's email>` | The `ILIKE` disjunction sits inside the scoped set (§12.1) | None for CORE. Non-CORE search unreachable **to a session** (D-1) — see T-21 for Basic |
| T-9 | Disabled user with a live cookie | Session issued, then `operator_users.status='disabled'` | Resolved per request (D-3); `401` on the next call | Window = one in-flight request |
| T-10 | Suspended/revoked membership with a live cookie | idem on `agency_memberships.status` | idem | idem |
| T-11 | Suspended agency | idem on `agencies.status` | idem | idem |
| T-12 | Session fixation / theft | Stolen cookie | `HttpOnly` + `Secure` + `SameSite=Lax`; opaque 32-byte token; only the SHA-256 stored; logout revokes | A stolen cookie is valid until expiry. No device binding — accepted for P26-1 |
| T-13 | Session token in the database | DB read access | Only `token_hash CHAR(64)` is stored; the raw token exists solely in the `Set-Cookie` header | None |
| T-14 | Password disclosure | DB read access | PBKDF2-SHA256, 600k iterations, per-user 16-byte salt; `CHECK (password_hash LIKE 'pbkdf2_sha256$%')` blocks a plaintext write | None |
| T-15 | User enumeration at login | Differential response or timing | One generic `401` for all six failure causes; the hash is always computed (§7.3 step 3) | None material |
| T-16 | Credential stuffing / brute force | Repeated `POST /login` | **No control.** No rate limiter exists in the repository | **OPEN — §21 R-3** |
| T-17 | Privilege escalation to `platform_admin` | Any operator-facing path | `is_platform_admin` is set only by direct SQL; no endpoint reads or writes it; on `/api/core` legacy Basic maps to `agency_owner` of the Default Agency, never to platform admin (D-2) | None |
| T-18 | Reaching an unscoped router **with a cookie** | `GET /api/property/properties` with only the session cookie | Those routers require Basic; a cookie-only request gets `401` (D-1), asserted by route-walking test 52 | None |
| **T-21** | **Cross-agency read via the legacy Basic channel on a non-CORE router** | `GET /api/crm/contacts/{B id}/360`, `GET /api/owner/admin/lookups/contacts?search=`, `/api/property`, `/api/buy`, `/api/match`, `/api/proposals`, `/api/sales`, `/api/flow`, `/api/seller-intelligence/timeline?contact_id=`, `/api/followup`, `/api/seller-intent`, `/api/next-best-action` — all with `ADMIN_USER`/`ADMIN_PASS` | **Partial only.** C-1 reclassifies the credential as a platform maintenance channel; C-2 keeps it out of agency operators' hands (a credential-handling control, not a code control); C-4 freezes and regression-guards the exact surface (test 52e). D-1 is *not* a control here — it governs sessions, not Basic | **OPEN and accepted for P26-1 TEST. Blocks PROD multi-agency onboarding via GATE-MA1 (§2.2.3). §21 R-9.** |
| **T-22** | **Cross-agency corruption of `activities`/`tasks` by reference mismatch** | Insert a task with `lead_id` in Agency A and `contact_id` in Agency B, or with an explicit `agency_id` contradicting its references | §3.6 `core_agency_integrity()` raises on any disagreement between references, and on an explicit `agency_id` that contradicts them; fires on `INSERT` **and** on `UPDATE OF` the four reference columns | None. A derive-only trigger would have left this fully open |
| T-19 | CSRF against a state-changing CORE route | Cross-site `POST` | `SameSite=Lax` blocks cross-site `POST`; CORS `allow_origins` is limited to `stima360.it`/`www.stima360.it` (`main.py`) | Same-site XSS would bypass; no CORE route renders `innerHTML` (AGENTS.md rule) |
| T-20 | Timing/error-shape oracle for ids | Comparing `404` shapes | Cross-agency and truly-absent both return `HTTPException(404, 'Risorsa non trovata')` — identical body and status | None |

---

## 15. Automated test matrix

New files, following `tests/test_*.py` and the existing static-analysis style.

**`tests/test_p26_1_agency_identity.py`** — migration/DDL statics
1. `027`–`030` exist with a `_down` sibling each (P26-0 rule 2)
2. each UP file contains **neither** `BEGIN;` **nor** `COMMIT;` (runner-owned
   transaction, atomicity addendum); each `_down` file contains **both**; none
   contains `CONCURRENTLY` (rule 10)
3. none contains `DELETE FROM schema_migrations` (rule 12)
4. no `NOT NULL` on `agency_id` before `030` (rule 9)
5. no `current_database()` guard (rule 6)
6. no secret-shaped key written into `agencies.settings` (D-8)
7. `verify_contiguous(discover_migrations())` yields `026,027,028,029,030`
8. `029_down` contains `RAISE EXCEPTION`

**`tests/test_p26_1_operator_auth.py`** — auth behaviour
9. login with correct credentials → `204` + `HttpOnly; Secure; SameSite=Lax` cookie
10. the response body of `login` never contains the raw token
11. wrong password / unknown email → the same `401` body
12. `status='disabled'` → `401`
13. membership `status='suspended'` → `401`
14. agency `status='suspended'` → `401`
15. `platform_admin` with no membership → `200`, `agency_id: null`
16. `me` returns no email and no hash
17. `logout` revokes → the next request is `401`
18. session past `expires_at` → `401`
19. session past the idle window → `401`
20. reload persistence: two sequential requests on one cookie both succeed and `last_seen_at` advances
21. `password_hash` starts with `pbkdf2_sha256$` and never equals the plaintext
22. only `token_hash` is stored; the raw token appears in no row

**`tests/test_p26_1_core_isolation.py`** — the hostile matrix
Fixture: agencies A and B; operators `owner_a`, `admin_a`, `agent_a`,
`agent_a2`, `owner_b`; `contact_a`/`lead_a` (assigned to `agent_a`),
`contact_b`/`lead_b`; one contact in A left unassigned.

23. cross-agency list: `owner_a` `GET /contacts` never returns `contact_b`
24. cross-agency list: `owner_a` `GET /leads` never returns `lead_b`
25. cross-agency detail: `owner_a` `GET /contacts/{contact_b}` → `404`
26. cross-agency detail: `owner_a` `GET /leads/{lead_b}` → `404`
27. cross-agency `PATCH /contacts/{contact_b}` → `404` **and** the row is unmodified
28. cross-agency `PATCH /leads/{lead_b}` → `404` and unmodified
29. create with forged `agency_id` in the body → `422`
30. update with forged `agency_id` in the body → `422`
31. `POST /leads {"contact_id": <contact_b>}` → `404`, no row created
32. forged `?agency_id=<B>` on `GET /contacts` is ignored, result unchanged
33. `GET /activities?stima_id=<B's stima>` → empty `items`
34. `GET /tasks?stima_id=<B's stima>` → empty `items`
35. `DELETE /activities/{B activity}` → `404`, the row survives
36. global search: `GET /contacts?search=<contact_b email>` → empty for `owner_a`
37. global search: `GET /leads?contact_id=<contact_b>` → empty for `owner_a`
38. `agent_a` sees `lead_a`; `agent_a2` gets `404` on the same id
39. `agent_a` does not see the unassigned A contact; `owner_a` does
40. `agent_a` `PATCH /leads/{lead_a}/assignment` → `403`
41. `owner_a` assigning `assigned_agent_id = <owner_b>` → `400`
42. `admin_a` sees every A record
43. `admin_a` may create an `agent` membership; may not create an `agency_admin` one
44. `platform_admin` sees both agencies
45. cross-agency `404` body is byte-identical to a genuinely absent id
46. `409` semantics of `link_stima` are unchanged within one agency

**`tests/test_p26_1_scope_enforcement.py`** — structural (D-5, §2.5.1)
47. every public function in `core/repository.py` that touches a scoped table satisfies branch (a) — `ctx` first — **or** branch (b) of the §2.5.1 rule 4 disjunction
48. no SQL literal in `core/repository.py` names a scoped table outside `core/scope.py`
49. `scoped_source` raises for a table not in `SCOPED_TABLES`
50. `OperatorContext` **and** `SystemAgencyContext` are frozen: assignment raises `FrozenInstanceError`
51. no CORE request schema declares `agency_id`, `created_by_user_id` or `assigned_agent_id`
52. route walk: every route outside the D-1 allowlist rejects a cookie-only request (T-18)
52b. `SYSTEM_CONTEXT_FUNCTIONS == {"bridge_public_stima"}` — exactly one member — and that function's first statement inside its `core_cursor` block is `system_context_for_public_stima(cur)`
52c. no HTTP-facing module (`main.py`, any `*/router*.py`, any `*/schemas.py`) imports `SystemAgencyContext`
52d. `system_context_for_public_stima` declares no parameter named `agency_id`, and `origin` is confined to `{"public_stima"}`
52e. **legacy-Basic surface freeze (C-4):** the computed set of routes that accept `require_admin`/`require_owner_admin` **and** transitively read a CORE table equals the frozen expected list committed with the test (T-21)

**`tests/test_p26_1_public_stima_routing.py`** — bridge (T-1)
53. a public estimation creates a contact and a lead in the Default Agency
54. an email that exists **only** in Agency B creates a **new** Default-Agency contact and never touches B's row
55. a repeat estimation for the same stima still returns `already_linked`
56. the P18 task created with `stima_id` only receives the Default Agency via the §3.6 fallback
57. `bridge_public_stima` raising still returns `200` from `POST /api/salva_stima`
57b. with the Default Agency row absent, the bridge raises `ConflictError` and `POST /api/salva_stima` still returns `200` with `bridge_status=error`
57c. the bridge's contact lookup executes with an `agency_id` predicate — asserted on the generated SQL, not only on the outcome

**`tests/test_p26_1_agency_integrity.py`** — trigger (§3.6, T-22)
58. `activities`/`tasks` insert with `lead_id` only → agency of the lead
59. with `contact_id` only → agency of the contact
60. with `stima_id` only, link present → agency via `lead_stime` → `leads`
61. with `stima_id` only, no link → Default Agency (the bounded fallback)
62. **coherent explicit `agency_id`** (matching the references) → accepted, value preserved unchanged
63. **coherent multiple references** (lead + contact + stima, all in agency A) → accepted, resolves to A
64. **`lead_id` in agency A + `contact_id` in agency B** → **rejected**, `RAISE EXCEPTION`, no row written
65. **explicit `agency_id` = A + `lead_id` in agency B** → **rejected**, `RAISE EXCEPTION`, no row written
66. `contact_id` in A + `stima_id` resolving to B → **rejected**
67. `UPDATE tasks SET lead_id = <agency B lead>` on an agency-A task → **rejected** (the `UPDATE OF` branch)
68. an unresolvable shape that is *not* the §3.6 step-5 fallback shape → **rejected**, not silently defaulted
69. `029`'s backfill `UPDATE` is unaffected: the trigger does not exist until `030` (ordering assertion on the migration files)

**`tests/test_p26_1_legacy_basic_surface.py`** — C-4 / T-21 / GATE-MA1
70. legacy Basic on `/api/core` is scoped to the Default Agency: `GET /api/core/contacts` never returns an Agency B row
71. legacy Basic on `/api/core` cannot assign an Agency B operator (`400`)
72. legacy Basic reports `is_platform_admin = false` via `/api/operator-auth/me`
73. **the documented residual is real and bounded:** `GET /api/crm/contacts/{B id}/360` with legacy Basic **does** return data — asserted explicitly, so the hole is a tested known state rather than an untested assumption, and any future scoping of that route fails this test and forces the spec to be updated

**Regression gate (existing suite, unchanged files)**
74. `python -m pytest -q` — all 113 existing test files green, in particular
    `test_public_stima_core_crm_bridge.py`, `test_seller_intelligence_*`,
    `test_seller_intent_*`, `test_p20*`–`test_p22*`, `test_owner_*`,
    `test_next4_p3_global_search.py`, `test_p26_*`

---

## 16. Rollback / recovery strategy

Four windows, matching the four migrations.

| Window | State | Rollback | Data loss |
|---|---|---|---|
| After `027` | Identity tables exist; CORE untouched | `027_down` drops all four | Operator accounts and sessions created since `027` |
| After `028` | Nullable columns exist; all `NULL` | `028_down` drops the columns, then `027_down` | Any assignment written since `028` |
| After `029` | Columns backfilled | **`029_down` RAISES.** Recovery is a restore from the pre-`029` `pg_dump` | n/a — the down refuses |
| After `030` | Constraints, indexes, triggers live | `030_down` (drop triggers → FKs → unique keys → indexes → `DROP NOT NULL`) returns to the post-`029` state | None |

`029_down_p26_core_agency_backfill.sql` exists and contains only:

```sql
BEGIN;
DO $do$ BEGIN
    RAISE EXCEPTION 'P26-1: 029 is a data backfill and cannot be reversed. '
                    'It cannot distinguish rows it set from rows written afterwards. '
                    'Recover by restoring the pre-029 backup (docs/P26_BACKUP_RESTORE_TEST.md).';
END $do$;
COMMIT;
```

This is the P26-0 §7.3 pattern: the file exists (rule 2) and refuses loudly.

Mandatory prerequisite, per P26-0 §7.5: a fresh `pg_dump` of
`stima360_db_test` **immediately before `029`**, plus the schema snapshot from
`scripts/p26_schema_snapshot.py`, both with their fingerprints recorded. The
`026`-era backup/restore drill (`docs/P26_BACKUP_RESTORE_TEST.md`, PASS) is the
procedure.

Application-level rollback is independent of the schema: reverting the code
while `027`–`030` remain applied is safe. The new columns are nullable-tolerant
from the old code's perspective (`INSERT` statements in `core/repository.py`
name their columns explicitly and never use `SELECT *` for writes), so old code
inserting a row would leave `agency_id` `NULL` — which `030`'s `NOT NULL`
rejects. Therefore: **application rollback below the `030` code level requires
rolling `030` back first.** Stated so it is not discovered during an incident.

---

## 17. Deployment sequence — TEST

All steps on `core-0.1-test`, against `stima360_db_test`. `main`, PROD and
deployment configuration are untouched throughout.

1. `git status --short` clean apart from the intended change; branch confirmed.
2. `python scripts/p26_schema_snapshot.py` → artifact + fingerprint recorded.
3. `pg_dump` of `stima360_db_test` → archived, fingerprint recorded.
4. `python scripts/p26_migrate.py status --operator "<real person>"` → `026`
   applied, `027`–`030` pending, no problems.
5. `python scripts/p26_migrate.py plan --operator "<real person>"` → exactly
   four migrations, in order, no gap. (Read-only.)
6. Record pre-migration counts: `SELECT COUNT(*) FROM contacts / leads /
   activities / tasks`.
7. Apply `027`. Verify: four tables exist, one `agencies` row with
   `slug='stima360'`, ledger row present.
8. Apply `028`. Verify: eleven columns exist and are `NULL` on every row; the
   four counts from step 6 are unchanged.
9. **Fresh `pg_dump` immediately before `029`** (§16).
10. Apply `029`. Verify: `SELECT agency_id, COUNT(*) … GROUP BY agency_id` on
    all four tables returns a single group equal to the step-6 counts; zero
    `NULL`.
11. Apply `030`. Verify: `NOT NULL` on the four columns, three composite FKs,
    the single unique key `contacts_agency_scope_unq`, nine indexes, and the
    two `core_agency_integrity()` triggers present in `pg_catalog`.
12. Run `scripts/p26_seed_agencies_test.py` (**new, TEST-guarded, not a
    migration**): creates Agency B, and the five operator users with
    memberships needed by §15. It reuses
    `assert_test_database_name` from `scripts/p26_migrate.py` and refuses to
    run against any database not carrying the `test` marker. Passwords come
    from environment variables and are never written to a repository file.
13. `python -m pytest -q tests/test_p26_1_*.py` — the seven new files.
14. `python -m pytest -q` — the full suite, for the P17–P25 regression gate.
15. Fresh schema snapshot; record the post-`030` fingerprint alongside the
    pre-`026` one.
16. `git diff --check`, `git status --short`, then report.

Why seeding is a script and not a migration: the schema must be identical in
TEST and PROD (P26-0 rule 6/7), and Agency B plus five operator accounts are
TEST fixtures. Putting them in a migration would either ship test users to PROD
or reintroduce the environment-conditional migration pattern that P26-0
explicitly abandoned.

---

## 18. Compatibility considerations

| Consumer | Impact | Mitigation |
|---|---|---|
| `POST /api/salva_stima` (public) | Bridge stamps `agency_id`; identity match narrows | §11; the endpoint's request/response contract is byte-identical |
| OS Shell (`static/os_shell/`) | None required in P26-1 | Basic auth keeps working via D-2. Migration of `auth.js` + `api-client.js` to the cookie session is a separate, later change; only those two files read credentials |
| 6 legacy admin frontends (`static/*_admin/`) | None | Their routers are untouched |
| `main.py` `@app` admin routes | None | `require_admin` untouched |
| `crm/`, `property/`, `buy/`, `match/`, `proposal/`, `sale/`, `flow/`, `owner/` | None to their code. **They remain cross-agency readable via legacy Basic** | D-1 keeps *operator sessions* out. It does **not** confine Basic — see §2.2.1, T-21, GATE-MA1. C-4's frozen surface test bounds and monitors the residual |
| `seller_intelligence/`, `followup/`, `seller_intent/`, `property_watch/`, `next_best_action/`, `database_revival/` | Their `INSERT`s into `activities`/`tasks` keep working unchanged, **provided the row's references are internally coherent** | The §3.6 trigger derives `agency_id` for them without a line changing. It now also *rejects* a row whose references span two agencies — a write these modules never legitimately perform, and one that would be corruption if it succeeded (T-22) |
| `run_*_e2e.py` and `run_*_cron.py` | Insert into `contacts`/`leads` directly (`run_buy_021_e2e.py:114,121`, `run_flow_01_e2e.py:31,32`) | These bypass the choke point and would hit `030`'s `NOT NULL`. They must add `agency_id` to their fixture inserts. Recorded as §21 R-5 — a required, mechanical change to four scripts, executed only in the guarded TEST environment |
| `crm/service.py` `get_contact_360` | Calls `get_contact`, `list_leads`, `list_activities`, `list_tasks`, whose signatures gain `ctx` | `crm` is behind the allowlist, so it passes the legacy-Basic context (D-2). A one-line change per call site, no behaviour change |
| `tests/` (113 files) | Existing CORE tests call `core.service.*` positionally | They gain a `ctx` fixture. The legacy-Basic HTTP tests are unaffected because D-2 keeps Basic working on `/api/core` |
| Pydantic v1 | `root_validator`, `extra='forbid'` | No schema change that would require v2 semantics |
| `scripts/p26_migrate.py` | `RUNNER_OWNED_TRANSACTION_FROM` + a version-gated branch in `validate_migration` | Required by the atomicity addendum so the migration body and its ledger row share one commit. `026`'s rule is preserved verbatim; all P26-0 tests stay green |

---

## 19. Explicit OUT OF SCOPE

P26-1 does **not** design, implement, prepare or partially deliver:

- PostgreSQL Row Level Security, policies, or the migrator/application role
  separation that P26-0 §6.1 makes its precondition
- Territories, geographic or postcode-based routing of public estimations
- Teams, sub-teams, offices
- Billing, subscriptions, plans, quotas
- Google / Microsoft SSO, magic links, 2FA, password-reset email flows
- Multi-agency membership (the `uq_agency_memberships_single_active` index is
  the deliberate P26-1 lock)
- A configurable permission builder or any role beyond the four named
- Record-sharing UI or any explicit sharing mechanism between agents
- Agency scope for PROPERTY, BUY, MATCH, PROPOSAL, SALE, FLOW, OWNER,
  SELLER INTELLIGENCE, FOLLOW-UP, SELLER INTENT, PROPERTY WATCH,
  NEXT BEST ACTION, DATABASE REVIVAL
- `agency_id` on `stime` or `stime_dettagliate`
- HTTP endpoints for creating agencies, operators or memberships (§13)
- Login rate limiting, account lockout, CAPTCHA (§21 R-3)
- Migration of the OS Shell to cookie authentication (§18)
- Removal of `ADMIN_USER` / `ADMIN_PASS` (§2.2 states the removal condition
  only)
- Any change to `main`, PROD, deployment configuration or schedulers

Dependencies on these are identified above where they exist; none is expanded.

---

## 20. Definition of Done

P26-1 is complete only when, on `stima360_db_test`:

1. Migrations `027`, `028`, `029`, `030` are applied and registered in
   `schema_migrations`, with `verify_contiguous` returning `026`–`030`.
2. **Two real agencies** exist: Default `stima360` plus a second seeded agency.
3. **Four roles validated**: `platform_admin`, `agency_owner`, `agency_admin`,
   `agent`, each proven by a passing test in §15.
4. **Real login** works: `POST /api/operator-auth/login` with a real
   `operator_users` row and a PBKDF2 hash.
5. **Real server-side session**: a row in `operator_sessions`; only
   `token_hash` persisted; an `HttpOnly; Secure; SameSite=Lax` cookie;
   `logout` revokes; the session survives a page reload (test 20).
6. **Contacts isolated** — tests 23, 25, 27, 36 pass.
7. **Leads isolated** — tests 24, 26, 28, 31, 37 pass.
8. **Agent assignment enforced** — tests 38–41 pass.
9. **Public STIMA still works** — tests 53–57c pass; a live public estimation
   produces a Default-Agency contact and lead and does not touch Agency B; the
   bridge's scope comes from `SystemAgencyContext`, asserted structurally by
   tests 52b–52d.
10. **Global search scoped** — tests 36, 37, 52 and 52e pass, per the §12
    definition, which is explicitly narrow: scoped **against operator
    sessions**, frozen-and-monitored **against legacy Basic**.
11. **Direct API manipulation cannot cross an agency boundary** — the whole of
    `tests/test_p26_1_core_isolation.py` passes, in particular 29, 30, 32–35,
    and `tests/test_p26_1_agency_integrity.py` tests 64–68 prove the storage
    layer rejects incoherent cross-agency rows.
12. **No P17–P25 regression** — the full `python -m pytest -q` suite is green.
13. **Automated hostile isolation tests PASS** — all seven new files, with fresh
    command output attached to the report.
14. A post-`030` schema snapshot and fingerprint are recorded alongside the
    pre-`026` baseline.
15. `git diff --check` clean; `git status --short` shows only the intended
    files.
16. **GATE-MA1 is recorded as OPEN, not satisfied.** (§2.2.3) The completion
    report states in these terms: *P26-1 delivers one isolated CORE vertical
    slice proven in TEST; it does not certify platform-wide multi-agency
    isolation, because the legacy Basic channel remains cross-agency on
    non-CORE routers (T-21). The second agency exists in TEST only. No second
    real agency may be onboarded into PROD until GATE-MA1 closes.* A completion
    report that omits this statement, or that describes P26-1 as
    "multi-agency ready", does not satisfy the Definition of Done.

---

## 21. Open risks and blockers

**No blocker prevents P26-1 from proceeding.** The risks below are real,
identified from the code, and each carries a decision.

**R-1 — Eleven modules read CORE tables without a scope.** (§1.10, §6.4)
`/api/crm/contacts/{id}/360` and `/api/owner/admin/lookups/contacts?search=`
are the two most direct leaks — the first returns an entire contact dossier by
id, the second is an unscoped free-text search over `contacts`. Severity
against an **operator session**: contained by D-1, asserted by test 52.
Severity against **legacy Basic**: not contained — see R-9, which is the same
surface viewed from the other authority. Decision: the allowlist is a P26-1
deliverable; scoping these modules is the defining work of P26-2 and must not
be done opportunistically.

**R-9 — The legacy Basic credential remains a global cross-agency authority on
every non-CORE router.** (§2.2.1, T-21) This is the most significant residual
risk in P26-1 and it is **not** mitigated by the D-1 route allowlist, because
Basic and the operator session are different authorities. Severity: an actor
holding `ADMIN_USER`/`ADMIN_PASS` reads every agency's CORE data through at
least twelve routes. Mitigations are C-1 to C-4 (§2.2.2), of which only C-4 is
a code control; C-2 is credential handling and is recorded as such rather than
counted as a technical guarantee. Decision: **accepted for P26-1 in TEST,
where the second agency is a test fixture; blocking for PROD via GATE-MA1
(§2.2.3).** The surface is frozen by test 52e and its reality asserted by test
73, so it cannot grow silently and cannot be quietly forgotten.

**R-2 — `stime` is never agency-scoped, and `link_stima` enforces one lead per
stima globally.** (§1.9) A stima therefore belongs, transitively, to exactly one
agency's lead. In P26-1 every public stima routes to the Default Agency, so no
conflict can arise. Once a second agency creates leads, a second lead for the
same stima returns `409` regardless of agency — a confusing but non-leaking
error (the message contains the other lead's id, which is an id disclosure of
low value). Decision: accepted and documented for P26-1; revisit when
territories arrive.

**R-3 — No login rate limiting.** (T-16) The repository contains no rate
limiter, no lockout and no CAPTCHA. `POST /api/operator-auth/login` is
therefore brute-forceable at network speed. The 600k-iteration PBKDF2 cost is
the only brake, and it is a CPU cost on the server as much as on the attacker.
Decision: **accepted for TEST, must be resolved before PROD.** Adding a limiter
now is scope expansion; shipping this to PROD without one is not acceptable.
Recorded as an explicit PROD precondition.

**R-4 — The `core_agency_integrity()` trigger is invisible from Python, and it
can now reject writes.** (§3.6) Two distinct costs. First, a future developer
reading `followup/repository.py` will see an `INSERT INTO tasks` with no
`agency_id` and may conclude the column is optional. Second, because the
trigger validates rather than merely derives, a caller that builds a row whose
references span two agencies gets an exception where it previously got a row —
correctly, but from a layer it cannot see. Decision: both accepted as the
lesser evil against editing five P17–P25-certified modules; the rejection
behaviour is the control that closes T-22 and is not optional. Mitigated by
`tests/test_p26_1_agency_integrity.py` (tests 58–69) and by this section. No
current writer produces a cross-agency row, so no existing flow changes
behaviour — test 74 (full suite) is the evidence.

**R-5 — Four `run_*` scripts insert into `contacts`/`leads` directly.**
(`run_buy_021_e2e.py:114,121`, `run_flow_01_e2e.py:31,32`, and the two
regression runners that clean up by `DELETE FROM leads/contacts`.) They bypass
`get_connection`-level conventions and will fail against `030`'s `NOT NULL`.
Decision: a mechanical fixture change, in scope for the P26-1 implementation
phase, executed only in the guarded TEST environment.

**R-6 — `/api/prefill` is unauthenticated and returns stima PII by token.**
(`main.py:1034`) Pre-existing, unchanged by P26-1, and outside its scope
(`stime` is not scoped). Recorded because a reader of this spec will otherwise
assume the public surface was fully audited without noticing it.

**R-7 — `agency_admin` LIMITED has no endpoint to enforce it against in
P26-1.** (§13) The rules are encoded as pure predicates and tested, but the
management endpoints do not exist. Decision: deliberate. The risk is that
P26-2 implements the endpoints without importing the predicates; mitigated by
test 43, which will fail the moment an endpoint appears that does not honour
them.

**R-8 — Session-resolution adds one write per authenticated request.** (§7.4)
`UPDATE operator_sessions SET last_seen_at = NOW()` on every call, on a
connection-per-request architecture with no pool (`database.py:28`). At OS
Shell volumes this is negligible; it is recorded because it is the one
performance cost P26-1 introduces on the hot path, and because it is the
unavoidable price of D-3's instant revocation.

---

## Appendix A — Files this specification anticipates changing

Listed so the implementation phase has a bounded surface. **Nothing in this
list has been created or modified by this document.**

*New:* `migrations/027…030_*.sql` + four `_down` files;
`operator_auth/{__init__,enums,security,context,dependencies,repository,service,router,schemas,permissions}.py`
— `context.py` defines **both** `OperatorContext` and `SystemAgencyContext`
(§8.1); `core/scope.py` — `scoped_source`, `SCOPED_TABLES`,
`SYSTEM_CONTEXT_FUNCTIONS` and the single factory
`system_context_for_public_stima` (§9.1); `scripts/p26_seed_agencies_test.py`;
**seven** `tests/test_p26_1_*.py`:
`agency_identity`, `operator_auth`, `core_isolation`, `scope_enforcement`,
`public_stima_routing`, `agency_integrity`, `legacy_basic_surface`.

*Modified:* `main.py` (mount `operator_auth` router; swap `require_admin` for
`require_operator` on the CORE router only — two lines);
`core/{router,service,repository}.py` (thread `ctx`; `bridge_public_stima`
builds its own `SystemAgencyContext` per §11.1); `crm/service.py` (pass `ctx`
through, five call sites); the four `run_*` fixture scripts (R-5).

*Explicitly unmodified:* `admin_security.py`, `admin_auth.py`, `database.py`,
`owner/`, `flow/`, `property/`, `buy/`, `match/`, `proposal/`, `sale/`,
`seller_intelligence/`, `followup/`, `seller_intent/`, `property_watch/`,
`next_best_action/`, `database_revival/`, `static/`, `scripts/p26_migrate.py`,
`migrations/001`–`026`.
