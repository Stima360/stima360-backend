# Repository Guidelines

## Project Structure & Architecture

- `main.py` is the FastAPI entrypoint. Domain packages are `core/`, `crm/`, `property/`, `buy/`, `match/`, `proposal/`, `flow/`, and `owner/`.
- Each domain commonly separates `router.py`, `service.py`, `repository.py`, schemas, and models. Preserve these boundaries and existing dependency direction.
- Vanilla JavaScript admin applications live under `static/<module>_admin/`; shared automated tests are under `tests/`.
- Ordered SQL changes and rollbacks live in `migrations/`. Operational scripts and module-specific `README_*.txt` files remain supporting material, not application APIs.

## Operating Rules

- Before changing code, inspect the real call-site, data flow, consumers, and root cause. Avoid speculative changes.
- Prefer the smallest sufficient patch. Do not perform unrelated refactors or touch task-declared closed modules without a proven in-scope regression.
- Preserve existing consumers and architecture. Do not introduce cross-module dependencies or automation without explicit approval.
- If a task says `AUDIT`, `AUDIT ONLY`, `DESIGN ONLY`, `READ ONLY`, or `REVIEW ONLY`, do not modify files. Report findings and stop; if no change is required, state `NO PATCH NEEDED`.
- Do not invent business rules. If code, tests, and specification do not define a required decision, stop and request it.
- Keep technical, operational, and commercial states distinct. Never map one state to another implicitly.

## Git & Worktree Safety

- Check the real working tree before editing. Preserve unrelated user changes and do not edit already-modified out-of-scope files.
- Never discard user work with `git reset`, `git checkout`, `git restore`, or equivalent commands.
- Keep commits strictly in scope. Do not commit or push without an explicit request, and never commit before required gates finish.
- Before an authorized commit, show changed files, relevant diff/status, and verification results.
- Use `git status --short` and `git diff --check` as final checks when pertinent.

## Coding Style & Security

- Follow surrounding code. For new Python, use four-space indentation, `snake_case` functions/variables, and `PascalCase` schema/classes. In JavaScript, use `camelCase`, `const`/`let`, and avoid broad formatting-only changes.
- For auth/security work, first audit routes, real consumers, and authentication; then apply minimum hardening.
- For new APIs, reuse established authentication patterns when appropriate.
- Preserve router-wide protections. Check IDOR and server-side relationships; derive relational IDs from trusted context when possible.
- Do not accept arbitrary `created_by`, actor, owner, or related IDs from the browser when authentication or server context determines them.
- Render API data with DOM APIs and `textContent`; do not introduce unsanitized dynamic `innerHTML`.

## Transactions & Concurrency

- For multi-write workflows, identify the real transaction boundary and prevent intermediate commits or partial state.
- Check whether reused repositories open independent transactions before calling them from an atomic workflow.
- Review lock order, races, idempotency, duplicates, and deadlock risk. Use one consistent lock order across competing workflows.
- Prefer deterministic concurrency tests; do not rely on fragile `sleep` timing when SQL/call order can prove the behavior.

## Database, Migrations & Production

- Treat schema, migrations, and data operations as sensitive. Never run or edit them without explicit scope.
- Never apply a migration implicitly to make a test pass. Verify the exact environment and database target first.
- A green deploy does not prove migrations are applied: report deployed code and database state separately.
- Do not rerun a non-idempotent migration without verification, execute production/live commands implicitly, or alter production data to facilitate tests.
- Never record real credentials, secrets, connection strings, or `.env` values in repository files or output.

## Build, Test & Development Commands

- Install declared dependencies: `python -m pip install -r requirements.txt -r requirements-dev.txt`.
- Run the local FastAPI application: `python main.py`.
- Run a targeted test first: `python -m pytest -q tests/test_<area>.py`.
- Run the full collected suite when required: `python -m pytest -q`.
- Run the guarded integration regression only in its required test environment: `python run_integration_01_regression.py`.
- Syntax-check changed admin JavaScript: `node --check static/<module>_admin/assets/app.js`.
- `run_*_e2e.py` scripts may access integration services or databases; execute them only when the task explicitly authorizes the guarded environment.

## Testing Guidelines

- Tests use pytest and follow `tests/test_*.py`; name cases `test_<observable_behavior>`.
- For bugfixes, reproduce the defect with a RED test when possible, then implement the minimum fix and run targeted tests first.
- Run broader regressions/full suite only when requested or justified as a safety gate. Never hide, reinterpret, or ignore failures.
- Report `CODE FAILURE` separately from `ENVIRONMENT / CONFIGURATION FAILURE`. A test not run is not a pass; report all skips.
- Do not claim `PASS`, fixed, or complete without fresh output from the relevant command.

## Commit & Pull Request Guidelines

- Follow the existing short imperative style, for example `Add proposal lifecycle` or `Fix visit timezone handling`; include a phase identifier only when the approved task uses one.
- Pull requests should state scope, root cause, files changed, migration/deployment impact, and exact test output. Link the tracked work and include screenshots for visible admin UI changes.
- Keep secrets and environment-specific values out of commits, PR descriptions, and screenshots.

## Task Reporting

When pertinent, report concisely: modified files; cause; implemented change; tests and regressions actually run with output; `git diff --check`; `git status --short`; and remaining problems. Never report checks that were not executed.
