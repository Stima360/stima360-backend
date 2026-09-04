# P25 Live Bug Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Riprodurre e correggere i due bug P25 live senza cambiare le regole di business, quindi validare i flussi contact/property/proposal/sale/BUY/P23/P24.

**Architecture:** Conservare gli endpoint e i confini esistenti. Correggere il punto d'origine di ciascun difetto con il minimo stato UI o isolamento del read model necessario; proteggere i comportamenti tramite test osservabili.

**Tech Stack:** FastAPI, Pydantic, psycopg2/PostgreSQL, pytest, JavaScript ES modules, Node.js syntax checker.

**Spec:** richiesta utente “STIMA360 OS — P25 LIVE BUG FIX + FINAL TECHNICAL VALIDATION”.

## Global Constraints

- Branch `core-0.1-test`; non toccare `main`, PROD o business rules P17-P24.
- Nessuna migrazione, pulizia dati, push o refactor non necessario.
- Test RED prima di ogni modifica di produzione e verifica completa prima del commit.

---

### Task 1: Contact detail HTTP 500

**Files:**
- Modify only the failing read-model component identified by the audit.
- Test: `tests/test_next3_crm_360.py` or a focused new regression file.

**Interfaces:**
- Consumes: `GET /api/crm/contacts/{contact_id}/360` and existing domain list services.
- Produces: the same nine-section `Contact360Response` contract.

- [ ] Trace each aggregate call with a Mario-like contact fixture and identify the exact failing component.
- [ ] Add a regression test that fails for the observed legacy/P25 data shape.
- [ ] Run the focused test and confirm the expected failure.
- [ ] Apply the smallest read-path correction without changing business state.
- [ ] Re-run the focused test and contact-detail regressions.

### Task 2: Property archive double confirmation

**Files:**
- Modify: `static/os_shell/assets/views/immobile-dettaglio.js`
- Test: `tests/test_os_shell_p25_3_property_lifecycle.py`

**Interfaces:**
- Consumes: selected manual commercial status and `DELETE /api/property/properties/{id}`.
- Produces: persisted `commercial_status='archived'` with server-generated `archived_at`.

- [ ] Add an executable UI regression test that selects `archived`, clicks save twice, and asserts one DELETE plus the persisted response state.
- [ ] Run it and confirm it fails because the first re-render loses the selected target.
- [ ] Preserve the pending target across the confirmation re-render; keep `archived_at` server-side.
- [ ] Re-run the UI test, property repository lifecycle tests, and `node --check`.

### Task 3: Lifecycle and final verification

**Files:**
- Modify: no production files unless an existing regression test proves a P25 regression.

**Interfaces:**
- Consumes: proposal transitions and `complete_sale` transaction.
- Produces: property `sold` and BUY request `satisfied` through existing business logic.

- [ ] Run targeted property lifecycle, contact detail, proposal/sale, BUY, P23 and P24 tests.
- [ ] Run `./.venv/bin/python -m pytest tests/ -q` and record exact totals.
- [ ] Run `node --check` for every changed JavaScript file.
- [ ] Run `git diff --check`, inspect `git status --short` and the final diff.
- [ ] Commit the scoped patch locally and confirm no push occurred.
