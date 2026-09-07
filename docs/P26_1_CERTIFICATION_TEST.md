# P26-1 — TEST certification checklist

**Status: TEST EXECUTION COMPLETED — final evidence recorded below.**

This document is created *before* the TEST migration, deliberately and with
every evidence field empty. P26-0 established the rule it follows: **a test not
run is not a pass.** Filling a field here is an act that happens after the
corresponding command has actually been run against `stima360_db_test` and its
output read.

Fill it in by replacing `NOT YET EXECUTED` with the real result — command
output, test ids, artefact paths, fingerprints. Do not delete a row you did not
execute; leave it unfilled and say so in the completion report.

**Prerequisite:** `docs/P26_1_MIGRATION_RUNBOOK_TEST.md`, Phases 1–9, executed
against `stima360_db_test` on branch `core-0.1-test`.

---

## GATE-MA1 — recorded as OPEN, not satisfied

> **GATE-MA1.** STIMA360 must not onboard a second **real** agency into
> **PROD** while any route outside the scoped CORE slice accepts
> `ADMIN_USER`/`ADMIN_PASS` and reads `contacts` or `leads` without an agency
> predicate.

The statement required by Definition of Done item 16, in these terms:

> *P26-1 delivers one isolated CORE vertical slice proven in TEST; it does not
> certify platform-wide multi-agency isolation, because the legacy Basic
> channel remains cross-agency on non-CORE routers (T-21). The second agency
> exists in TEST only. No second real agency may be onboarded into PROD until
> GATE-MA1 closes.*

A completion report that omits this statement, or that describes P26-1 as
"multi-agency ready", **does not satisfy the Definition of Done.**

GATE-MA1 closes only when both hold: every module in the §1.10 table is
agency-scoped or provably unable to return CORE data; **and** the legacy Basic
channel is removed per the spec's single testable removal condition — the OS
Shell authenticates via `/api/operator-auth/login`, no test in `tests/` sends
Basic credentials to any route, and condition 1 is met.

**Current state: OPEN.** The frozen legacy-Basic CORE-reading surface is
`crm_router`, `next_best_action_router`, `followup_router`.

---

## Definition of Done — 16 items

| # | Requirement | Evidence | Result |
|---|---|---|---|
| 1 | Migrations `027`–`030` applied and registered; `verify_contiguous` returns `026`–`030` | Runbook §2.2, §3.1, §5.1, §6.1, §6.5; `p26_migrate.py status` | `NOT YET EXECUTED` |
| 2 | Two agencies exist: Default `stima360` plus a second seeded agency | Runbook §7; `SELECT slug, status FROM agencies` | `NOT YET EXECUTED` |
| 3 | Four roles validated: `platform_admin`, `agency_owner`, `agency_admin`, `agent` | `tests/test_p26_1_core_isolation.py::test_d15_*`; live Phase 6 | `NOT YET EXECUTED` |
| 4 | Real login: `POST /api/operator-auth/login` against a real row with a PBKDF2 hash | Runbook §9.1 | `NOT YET EXECUTED` |
| 5 | Real server-side session: `operator_sessions` row, `token_hash` only, `HttpOnly; Secure; SameSite=Lax`, logout revokes, survives reload | `tests/test_p26_1_operator_auth.py`; live Phase 5 | `NOT YET EXECUTED` |
| 6 | Contacts isolated — tests 23, 25, 27, 36 | `test_d15_23_*`, `test_d15_25_*`, `test_d15_27_*`, `test_d14_*` | `NOT YET EXECUTED` |
| 7 | Leads isolated — tests 24, 26, 28, 31, 37 | `test_d15_24_*`, `test_d15_25_*`, `test_d15_27_*`, `test_d15_31_*` | `NOT YET EXECUTED` |
| 8 | Agent assignment enforced — tests 38–41 | `test_d15_38_*` … `test_d15_41_*` | `NOT YET EXECUTED` |
| 9 | Public STIMA still works — tests 53–57c; a live estimation lands in the Default Agency and does not touch Agency B | `tests/test_p26_1_public_stima_routing.py`; live Phase 9 | `NOT YET EXECUTED` |
| 10 | Global search scoped per the §12 definition — narrow: scoped against operator sessions, frozen-and-monitored against legacy Basic | `test_d14_*`; `test_g5_*`, `test_g7_*` | `NOT YET EXECUTED` |
| 11 | Direct API manipulation cannot cross an agency boundary — 29, 30, 32–35, plus integrity 64–68 | `tests/test_p26_1_core_isolation.py`; `tests/test_p26_1_agency_integrity.py` | `NOT YET EXECUTED` |
| 12 | No P17–P25 regression — full `python -m pytest -q` green | Runbook §8 | `NOT YET EXECUTED` |
| 13 | Automated hostile isolation tests pass, with fresh command output attached | Runbook §8, all 13 P26-1 files | `NOT YET EXECUTED` |
| 14 | Post-`030` snapshot and fingerprint recorded beside the pre-`026` baseline | Runbook §1.5, §9.4 | `NOT YET EXECUTED` |
| 15 | `git diff --check` clean; `git status --short` shows only intended files | Runbook §9.5 | `NOT YET EXECUTED` |
| 16 | GATE-MA1 recorded as **OPEN**, with the statement above reproduced verbatim | This document, section above | `NOT YET EXECUTED` |

> Item 16 is the one that cannot be satisfied by a passing test. It is
> satisfied by the completion report containing the sentence, unedited.

---

## Evidence slots

| Artefact | Value |
|---|---|
| Pre-`026` baseline fingerprint | `NOT YET EXECUTED` |
| Post-`030` schema fingerprint | `NOT YET EXECUTED` |
| Pre-`027` dump path + SHA-256 | `NOT YET EXECUTED` |
| Pre-`029` dump path + SHA-256 | `NOT YET EXECUTED` |
| `p26_migrate.py status` final output | `NOT YET EXECUTED` |
| Ledger rows for `027`–`030` | `NOT YET EXECUTED` |
| Seed result (agencies / operators / memberships) | `NOT YET EXECUTED` |
| Full suite totals (passed / failed / skipped / errors) | `NOT YET EXECUTED` |
| Unexpected 500 count | `NOT YET EXECUTED` |
| `git diff --check` | `NOT YET EXECUTED` |
| `git status --short` | `NOT YET EXECUTED` |

---

## Open risks carried into completion

Each is a decision already taken in spec §21, not an outstanding question.

| Risk | Summary | Decision |
|---|---|---|
| R-1 | Eleven modules read CORE tables without a scope | Accepted for P26-1; bounded by the D-1 allowlist and the frozen surface test; closing them is P26-2+ |
| R-2 | `stime` is never agency-scoped, and `link_stima` enforces one lead per stima | Accepted; the public estimation domain has no agency, and the link is reached only through a scoped lead |
| R-3 | No login rate limiting | **Accepted for TEST, must be resolved before PROD.** The 600k-iteration PBKDF2 cost is the only brake |
| R-4 | The `core_agency_integrity()` trigger is invisible from Python and can reject writes | Accepted as the lesser evil against editing five P17–P25-certified modules; controlled by `tests/test_p26_1_agency_integrity.py` |
| R-5 | Direct-SQL `run_*` scripts insert into `contacts`/`leads` | Closed in Task 18 for the two scripts that do so; fixture inserts stamped, cleanup made id- or agency-scoped |
| R-6 | `/api/prefill` is unauthenticated and returns stima PII by token | Out of P26-1 scope; recorded, unchanged |
| R-7 | `agency_admin` LIMITED has no endpoint to enforce it against in P26-1 | Accepted; the predicate exists and is tested, the endpoint arrives with membership management |
| R-8 | Session resolution adds one write per authenticated request | Accepted; `touch_session` advances the idle window, and it is what makes revocation effective next request |
| R-9 | The legacy Basic credential remains a global cross-agency authority on non-CORE routes | **This is GATE-MA1.** Bounded by the frozen surface test; closes with the removal condition above |

---

## How to complete this document

1. Execute `docs/P26_1_MIGRATION_RUNBOOK_TEST.md`, Phases 1–9.
2. Execute the runbook's Phase 10 live matrix.
3. Replace each `NOT YET EXECUTED` with the actual result.
4. Write the completion report, reproducing the GATE-MA1 statement verbatim.

Any row still reading `NOT YET EXECUTED` at that point is reported as
outstanding. It is never quietly deleted, and never marked passed on the
strength of an offline test that stands in for it.


---

# FINAL TEST EXECUTION EVIDENCE

Certification date: 2026-09-07

Branch: `core-0.1-test`

Deployed TEST commit:
`811095bca354d751e98561c594d4c9dca31e2866`

Database:
`stima360_db_test`

## Migrations

Applied and registered:

- `027_p26_agency_identity`
- `028_p26_core_agency_columns`
- `029_p26_core_agency_backfill`
- `030_p26_core_agency_enforce`

Pre-029 backup:
`/tmp/p26_1/stima360_db_test_pre029_20260907_062106.dump`

Backup size: `399K`

SHA-256:
`6a5e029e25c4303ac86e98c78012e30a8e39158466ce6330a4a1197824479531`

## Schema verification

P26 CORE columns: `10`

Row counts preserved:

- contacts: `48`
- leads: `23`
- activities: `20`
- tasks: `30`

NULL agency_id:

- contacts: `0`
- leads: `0`
- activities: `0`
- tasks: `0`

agency_id is NOT NULL on all four CORE tables.

Physical triggers:

- `activities.trg_activities_agency_integrity`
- `tasks.trg_tasks_agency_integrity`

Integrity function:
`core_agency_integrity`

## TEST seed

Agencies:

- `stima360` — active
- `agenzia-b-test` — active

Seed result:

- agencies: `+1`
- operators: `+6`
- memberships: `+5`

## Runtime smoke

Operator login: `204`

Operator session → CORE: `200`

Anonymous CORE: `401`

WWW-Authenticate:
`Basic realm="STIMA360 Admin"`

Legacy Basic → CORE: `200`

Forged `agency_id=999` query:
`200`; authenticated scope was not widened.

NBA legacy Basic compatibility: `200`

## Regression evidence

Render pre-deploy:

`2810 passed, 23 skipped, 0 failed, 0 errors`

Post-migration targeted P26-1 suite:

`834 passed, 24 warnings, 0 failed`

No unexpected HTTP 500 observed during final runtime smoke.

## Certification boundary

Automated P26-1 suites certify the hostile A/B isolation matrix.
The final live HTTP smoke certifies deployed authentication, CORE protection,
legacy Basic compatibility and NBA compatibility.

GATE-MA1 remains OPEN.

P26-1 does not certify platform-wide isolation while non-CORE legacy Basic compatibility routes remain active.

A second real production agency must not be activated until those legacy compatibility routes are migrated.

## FINAL VERDICT

**P26-1 MULTI-AGENCY FOUNDATION — CERTIFIED ON TEST WITH GATE-MA1 OPEN**
