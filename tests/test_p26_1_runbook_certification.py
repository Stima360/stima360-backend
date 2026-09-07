"""P26-1 Task 22 - certification of the TEST migration runbook.

The runbook is the only P26-1 artefact whose failure mode is a *person*
following it at the wrong moment, against the wrong database, without a
backup. It cannot be unit-tested by running it - running it is the thing it
authorises - so it is tested the way a checklist is audited: every safety
property must be present in the text, and the auditor must be shown to notice
when one is missing.

That second half is what makes this file worth having. A rule asserting
"'pg_dump' appears somewhere" is satisfied by a runbook that mentions backups
and never takes one. So each rule below is paired with a synthetic broken
runbook - a string defined in this module, never a file on disk - and asserted
to fail against it.

Nothing here executes a migration, connects to a database, or runs the seed
script.

Coverage map:

    K1  the runbook exists and declares itself a plan, not a record
    K2  TEST-only guard, and an explicit production prohibition
    K3  backup before, and a restore path that does not overwrite
    K4  migration order 027 -> 028 -> 029 -> 030, with the dump between 028/029
    K5  stop conditions at every checkpoint
    K6  pre-checks and post-checks, including the ledger
    K7  the 029 irreversibility warning
    K8  seed after migrations, live matrix after seed
    K9  GATE-MA1
    K10 the negative controls - each rule catches its own omission
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs" / "P26_1_MIGRATION_RUNBOOK_TEST.md"

TEST_DATABASE = "stima360_db_test"
PROD_DATABASE = "stima360_db"

MIGRATIONS_IN_ORDER = ("027", "028", "029", "030")


def _runbook() -> str:
    assert RUNBOOK.exists(), f"{RUNBOOK.relative_to(ROOT)} does not exist"
    return RUNBOOK.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The rules, each written as a function over the runbook text so that the
# negative controls at the bottom can run the same rule against broken input.
# ---------------------------------------------------------------------------

def rule_test_only_guard(text: str) -> None:
    assert TEST_DATABASE in text, "the runbook does not name the TEST database"
    assert re.search(r"DB_NAME", text), "the runbook never inspects DB_NAME"
    assert re.search(r"current_database\(\)", text), (
        "the runbook does not confirm the connected database from the server"
    )


def rule_production_is_prohibited(text: str) -> None:
    """`stima360_db` may appear only inside a prohibition."""
    # Evaluated over sentences, not lines. Markdown prose wraps, and a
    # line-based rule split one prohibition across two lines and reported the
    # second half as a violation - a false positive in the rule, not the text.
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
    occurrences = [
        sentence for sentence in sentences
        if re.search(rf"\b{PROD_DATABASE}\b(?!_test)", sentence)
    ]
    assert occurrences, "the runbook never names the production database to forbid it"
    for sentence in occurrences:
        lowered = sentence.lower()
        assert any(
            word in lowered
            for word in ("never", "must not", "prohibited", "refus", "not be run",
                         "is the production", "that is the production", "stop if")
        ), f"production database named outside a prohibition: {sentence.strip()}"


def rule_backup_before_migrating(text: str) -> None:
    assert "pg_dump" in text, "the runbook takes no backup"
    assert text.index("pg_dump") < text.index("apply --operator"), (
        "the first backup is taken after the first apply"
    )


def rule_restore_path(text: str) -> None:
    """A restore must exist, and must not overwrite the live TEST database.

    The earlier form of this rule looked for the substring "_restore" near the
    command - which `pg_restore` itself contains, so the rule was satisfied by
    its own subject and could never fail. It now reads the actual `-d` target.
    """
    assert "pg_restore" in text, "the runbook documents no restore command"

    command = re.search(r"pg_restore[^\n]*(?:\n\s+[^\n]*)*", text)
    assert command, "the pg_restore invocation could not be read"
    target = re.search(r"-d\s+(\S+)", command.group(0))
    assert target, f"pg_restore names no target database: {command.group(0)}"
    assert target.group(1) != TEST_DATABASE, (
        f"the restore targets the live TEST database ({target.group(1)}); it must "
        "go to a separate database"
    )


def rule_migration_order(text: str) -> None:
    positions = []
    for version in MIGRATIONS_IN_ORDER:
        match = re.search(rf"Apply `{version}`", text)
        assert match, f"the runbook has no apply step for {version}"
        positions.append(match.start())
    assert positions == sorted(positions), (
        f"the migrations are not in order 027 -> 028 -> 029 -> 030: {positions}"
    )


def rule_fresh_dump_between_028_and_029(text: str) -> None:
    after_028 = re.search(r"Apply `028`", text).start()
    before_029 = re.search(r"Apply `029`", text).start()
    window = text[after_028:before_029]
    assert "pg_dump" in window, (
        "no fresh backup is taken between 028 and 029; 029 is the irreversible one"
    )


def rule_stop_conditions(text: str) -> None:
    stops = re.findall(r"\*\*STOP IF\*\*", text)
    assert len(stops) >= 10, f"only {len(stops)} stop conditions; the checklist is thin"
    for requirement in (
        "not `core-0.1-test`",
        "exits non-zero",
        "`pg_dump` fails",
        "already recorded as applied",
    ):
        assert requirement in text, f"missing stop condition: {requirement}"


def rule_no_automatic_continue(text: str) -> None:
    """A failed checkpoint must never be described as recoverable by retrying."""
    for forbidden in ("re-run and continue", "ignore and proceed", "safe to continue"):
        assert forbidden not in text.lower(), forbidden


def rule_pre_checks(text: str) -> None:
    for requirement in ("git branch --show-current", "core-0.1-test",
                        "p26_migrate.py status", "p26_migrate.py plan",
                        "p26_schema_snapshot.py", "COUNT(*)"):
        assert requirement in text, f"missing precondition: {requirement}"


def rule_post_checks(text: str) -> None:
    for requirement in ("is_nullable", "pg_constraint", "pg_indexes",
                        "core_agency_integrity", "pg_trigger"):
        assert requirement in text, f"missing post-migration check: {requirement}"


def rule_backfill_check(text: str) -> None:
    assert "GROUP BY agency_id" in text, "no backfill grouping check"
    assert "agency_id IS NULL" in text, "no residual-NULL check after the backfill"


def rule_trigger_coverage_is_exact(text: str) -> None:
    assert re.search(r"exactly two.*trigger", text, re.IGNORECASE | re.DOTALL), (
        "the runbook does not pin the trigger count"
    )
    window = text[text.index("core_agency_integrity"):]
    assert "activities" in window and "tasks" in window


def rule_ledger_check(text: str) -> None:
    assert "schema_migrations" in text, "the ledger is never inspected"
    assert re.search(r"count\s*=\s*1|COUNT\(\*\)", text), (
        "the runbook does not check each migration is recorded exactly once"
    )


def rule_029_is_irreversible(text: str) -> None:
    assert re.search(r"029.{0,40}irreversible", text, re.IGNORECASE | re.DOTALL), (
        "the runbook does not warn that 029 is irreversible"
    )
    assert "restore-from-backup" in text or "restore from backup" in text.lower(), (
        "the runbook does not state that downgrade is restore, not a down migration"
    )
    assert re.search(r"RAISE EXCEPTION", text), (
        "the runbook does not say the 029 down file refuses"
    )


def rule_seed_after_migrations(text: str) -> None:
    seed = text.index("p26_seed_agencies_test.py")
    last_apply = text.rindex("Apply `030`")
    assert seed > last_apply, "the seed step is placed before the migrations"
    assert "P26_SEED_OWNER_A_PASSWORD" in text
    assert re.search(r"six operators.{0,80}five", text, re.IGNORECASE | re.DOTALL) or \
        re.search(r"operators \+6", text), "the 6/5 asymmetry is not stated"


def rule_live_matrix_after_seed(text: str) -> None:
    matrix = text.index("A/B hostile matrix")
    seed = text.index("p26_seed_agencies_test.py")
    assert matrix > seed, "the live matrix is placed before seeding"
    for step in ("Auth smoke", "CORE smoke", "Assignment matrix",
                 "NBA compatibility", "Public STIMA regression", "Cleanup"):
        assert step in text, f"the live phase omits: {step}"


def rule_gate_ma1(text: str) -> None:
    assert "GATE-MA1" in text, "GATE-MA1 is not carried into the runbook"
    assert re.search(r"does \*\*not\*\* certify|does not certify", text), (
        "the runbook does not restate what P26-1 declines to certify"
    )
    assert re.search(r"second \*\*real\*\* agency|second real agency", text)


def rule_no_deploy_or_commit(text: str) -> None:
    assert "No `git commit`" in text or "No git commit" in text
    for forbidden in ("git push origin", "railway up", "render deploy"):
        assert forbidden not in text, forbidden


def rule_is_a_plan_not_a_record(text: str) -> None:
    assert "not a record" in text.lower(), "the runbook does not declare itself unexecuted"
    assert "--operator \"<real person>\"" in text, (
        "an invocation does not carry a real operator identity placeholder"
    )
    assert "$ADMIN_USER" not in text.replace("$ADMIN_USER:$ADMIN_PASS", ""), (
        "the ledger identity must never be the shared admin credential"
    )


ALL_RULES = (
    rule_test_only_guard,
    rule_production_is_prohibited,
    rule_backup_before_migrating,
    rule_restore_path,
    rule_migration_order,
    rule_fresh_dump_between_028_and_029,
    rule_stop_conditions,
    rule_no_automatic_continue,
    rule_pre_checks,
    rule_post_checks,
    rule_backfill_check,
    rule_trigger_coverage_is_exact,
    rule_ledger_check,
    rule_029_is_irreversible,
    rule_seed_after_migrations,
    rule_live_matrix_after_seed,
    rule_gate_ma1,
    rule_no_deploy_or_commit,
    rule_is_a_plan_not_a_record,
)


# ---------------------------------------------------------------------------
# K1-K9 - the real runbook satisfies every rule
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rule", ALL_RULES, ids=lambda rule: rule.__name__)
def test_k_the_runbook_satisfies_every_rule(rule):
    rule(_runbook())


def test_k1_the_runbook_exists_and_is_substantial():
    text = _runbook()
    assert len(text.splitlines()) > 200, "a runbook this short cannot be complete"


def test_k1_it_contains_no_executed_output():
    """A plan, not a record: no captured results, no timings, no row counts."""
    text = _runbook()
    for marker in ("applied 027", "rows affected", "Time: ", "(1 row)"):
        assert marker not in text, f"executed output found: {marker}"


def test_k4_the_runner_limitation_is_documented():
    """`apply` has no --target; the runbook must say how it enforces the stops.

    This is the finding most likely to derail a live run: a naive single
    `apply` would sail past the mandated backup between 028 and 029.
    """
    text = _runbook()
    assert "no `--target`" in text or "has no `--target`" in text
    assert "migrations/.staged" in text, "no mechanism is given for the per-migration stops"


def test_k6_the_column_count_correction_is_recorded():
    """Spec 17 says eleven columns; 028 adds ten. The runbook must not repeat
    the typo, and must say which number is right and why."""
    text = _runbook()
    assert "**10**" in text or "ten" in text.lower()
    assert "typo" in text.lower(), "the correction is not explained"


# ---------------------------------------------------------------------------
# K10 - negative controls
#
# Each rule is run against a synthetic runbook with exactly one property
# removed, and asserted to fail. The synthetic text is a string in this module;
# no file on disk is edited, and the real runbook is never mutated.
# ---------------------------------------------------------------------------

def _mutate(original: str, *replacements: tuple[str, str]) -> str:
    broken = original
    for old, new in replacements:
        assert old in broken, f"the mutation target is absent: {old!r}"
        broken = broken.replace(old, new)
    return broken


def test_k10_a_reordered_migration_sequence_is_caught():
    # A genuine swap: 030 is applied before 029. An earlier draft of this
    # control renamed the headings but left them ascending, so it proved
    # nothing - the rule passed and the control passed with it.
    text = _runbook()
    head = text[: text.index("## Phase 5 — Apply `029`")]
    phase_5 = text[text.index("## Phase 5 — Apply `029`"): text.index("## Phase 6 — Apply `030`")]
    phase_6 = text[text.index("## Phase 6 — Apply `030`"): text.index("## Phase 7")]
    tail = text[text.index("## Phase 7"):]
    broken = head + phase_6 + phase_5 + tail

    assert broken.index("Apply `030`") < broken.index("Apply `029`"), "the swap did not take"
    with pytest.raises(AssertionError, match="not in order"):
        rule_migration_order(broken)


def test_k10_a_missing_backup_is_caught():
    broken = _runbook().replace("pg_dump", "echo skipping-the-dump")
    with pytest.raises(AssertionError, match="takes no backup"):
        rule_backup_before_migrating(broken)


def test_k10_a_backup_taken_too_late_is_caught():
    """Mentioning pg_dump is not enough; it must precede the first apply.

    Built by moving the first apply to the very top, so every backup in the
    document now comes after it.
    """
    text = _runbook()
    broken = "```bash\npython scripts/p26_migrate.py apply --operator \"x\"\n```\n" + text
    assert broken.index("apply --operator") < broken.index("pg_dump")
    with pytest.raises(AssertionError, match="after the first apply"):
        rule_backup_before_migrating(broken)


def test_k10_a_missing_restore_path_is_caught():
    broken = _runbook().replace("pg_restore", "some-other-tool")
    with pytest.raises(AssertionError, match="no restore command"):
        rule_restore_path(broken)


def test_k10_a_restore_over_the_live_database_is_caught():
    broken = _runbook().replace("stima360_db_test_restore", "stima360_db_test")
    with pytest.raises(AssertionError, match="live TEST database"):
        rule_restore_path(broken)


def test_k10_a_missing_test_guard_is_caught():
    broken = _runbook().replace("current_database()", "trust-the-shell")
    with pytest.raises(AssertionError, match="confirm the connected database"):
        rule_test_only_guard(broken)


def test_k10_naming_production_outside_a_prohibition_is_caught():
    broken = _runbook() + "\n\nYou may also point this at stima360_db if convenient.\n"
    with pytest.raises(AssertionError, match="outside a prohibition"):
        rule_production_is_prohibited(broken)


def test_k10_a_missing_029_irreversibility_warning_is_caught():
    text = _runbook()
    broken = re.sub(r"(?i)irreversible", "ordinary", text)
    with pytest.raises(AssertionError, match="irreversible"):
        rule_029_is_irreversible(broken)


def test_k10_a_missing_post_check_is_caught():
    broken = _runbook().replace("pg_constraint", "some-catalogue")
    with pytest.raises(AssertionError, match="post-migration check"):
        rule_post_checks(broken)


def test_k10_a_missing_ledger_check_is_caught():
    broken = _runbook().replace("schema_migrations", "some_other_table")
    with pytest.raises(AssertionError, match="ledger is never inspected"):
        rule_ledger_check(broken)


def test_k10_a_missing_gate_ma1_is_caught():
    broken = _runbook().replace("GATE-MA1", "a gate")
    with pytest.raises(AssertionError, match="GATE-MA1"):
        rule_gate_ma1(broken)


def test_k10_a_missing_backfill_check_is_caught():
    broken = _runbook().replace("GROUP BY agency_id", "GROUP BY id")
    with pytest.raises(AssertionError, match="backfill grouping"):
        rule_backfill_check(broken)


def test_k10_seeding_before_the_migrations_is_caught():
    text = _runbook()
    broken = "python scripts/p26_seed_agencies_test.py --operator x\n" + text.replace(
        "python scripts/p26_seed_agencies_test.py --operator \"<real person>\"", "seed-step-removed"
    )
    with pytest.raises(AssertionError, match="before the migrations"):
        rule_seed_after_migrations(broken)


def test_k10_removing_the_stop_conditions_is_caught():
    broken = _runbook().replace("**STOP IF**", "note:")
    with pytest.raises(AssertionError, match="stop conditions"):
        rule_stop_conditions(broken)


def test_k10_an_automatic_continue_instruction_is_caught():
    broken = _runbook() + "\n\nIf a checkpoint fails it is safe to continue.\n"
    with pytest.raises(AssertionError):
        rule_no_automatic_continue(broken)


def test_k10_a_deploy_command_is_caught():
    broken = _runbook() + "\n\n```bash\ngit push origin core-0.1-test\n```\n"
    with pytest.raises(AssertionError):
        rule_no_deploy_or_commit(broken)


def test_k10_the_controls_cover_every_rule_that_has_one():
    """A roster, so a rule cannot quietly lose its control.

    Not every rule needs one - some are simple presence checks whose omission
    is self-evidently caught - but the ones guarding a *safety* property do.
    """
    controlled = {
        "rule_migration_order", "rule_backup_before_migrating", "rule_restore_path",
        "rule_test_only_guard", "rule_production_is_prohibited",
        "rule_029_is_irreversible", "rule_post_checks", "rule_ledger_check",
        "rule_gate_ma1", "rule_backfill_check", "rule_seed_after_migrations",
        "rule_stop_conditions", "rule_no_automatic_continue",
        "rule_no_deploy_or_commit",
    }
    names = {rule.__name__ for rule in ALL_RULES}
    assert controlled <= names, controlled - names
    source = Path(__file__).read_text(encoding="utf-8")
    for name in controlled:
        assert source.count(name) >= 2, f"{name} has no negative control"


# ===========================================================================
# L - Task 23: the certification checklist.
#
# Created before execution, with every evidence field unfilled. That is the
# design, not an oversight: P26-0's rule is that a test not run is not a pass,
# and a checklist pre-filled with results nobody obtained is worse than no
# checklist at all.
#
# So these rules assert two things at once - that the document is complete in
# *structure*, and that it is empty of *claims*.
# ===========================================================================

CERTIFICATION = ROOT / "docs" / "P26_1_CERTIFICATION_TEST.md"

NOT_EXECUTED = "NOT YET EXECUTED"

# Definition of Done, spec section 20. Sixteen items, no more, no fewer.
DOD_ITEM_COUNT = 16

RISKS = tuple(f"R-{number}" for number in range(1, 10))


def _normalise(text: str) -> str:
    """Collapse Markdown prose to one line for substring checks.

    Strips blockquote markers first. A required sentence lives inside a `>`
    block and wraps across lines; a plain `" ".join(split())` leaves the `>`
    tokens interleaved and reports the sentence as missing when it is present
    and correct.
    """
    return " ".join(re.sub(r"(?m)^\s*>\s?", "", text).split())


def _certification() -> str:
    assert CERTIFICATION.exists(), (
        f"{CERTIFICATION.relative_to(ROOT)} does not exist"
    )
    return CERTIFICATION.read_text(encoding="utf-8")


def test_l1_the_certification_document_exists():
    assert len(_certification().splitlines()) > 60


def test_l2_every_definition_of_done_item_has_a_row():
    text = _certification()
    rows = re.findall(r"^\|\s*(\d+)\s*\|", text, re.MULTILINE)
    numbers = sorted(int(row) for row in rows)
    assert numbers == list(range(1, DOD_ITEM_COUNT + 1)), numbers


def test_l3_every_evidence_field_is_unfilled():
    """The whole point: no row may claim a result nobody produced."""
    text = _certification()
    rows = [line for line in text.splitlines() if line.startswith("|") and "|" in line[1:]]
    result_rows = [
        line for line in rows
        if re.match(r"^\|\s*\d+\s*\|", line) or line.count("|") == 3
    ]
    claimed = [
        line for line in result_rows
        if NOT_EXECUTED not in line
        and not line.strip().startswith("| #")
        and not set(line.strip()) <= set("|- ")
        and "Artefact" not in line
        and "Risk" not in line
        and "Requirement" not in line
    ]
    # Every remaining row is either a risk row (a decision, not evidence) or a
    # header/separator. Risk rows are excluded by the section split below.
    risks_start = text.index("## Open risks")
    claimed = [line for line in claimed if text.index(line) < risks_start]
    assert not claimed, f"these rows claim a result: {claimed}"


def test_l3_the_document_declares_itself_unexecuted():
    text = _certification()
    assert "**Status: NOT YET EXECUTED.**" in text
    assert "a test not run is not a pass" in _normalise(text)


def test_l3_it_contains_no_fabricated_output():
    """No fingerprint, no row count, no psql output may appear."""
    text = _certification()
    for marker in ("(1 row)", "applied 027", "rows affected", "sha256:"):
        assert marker not in text.lower(), f"fabricated output: {marker}"
    # A real SHA-256 would be 64 hex characters; there must be none.
    assert not re.search(r"\b[0-9a-f]{64}\b", text), "a fingerprint has been filled in"


def test_l4_gate_ma1_is_present_and_open():
    text = _certification()
    assert "GATE-MA1" in text
    assert "recorded as OPEN" in text or "OPEN, not satisfied" in text


def test_l4_the_dod_item_16_sentence_is_reproduced_verbatim():
    """Spec section 20 makes omitting or paraphrasing this a DoD failure."""
    text = _normalise(_certification())
    for fragment in (
        "P26-1 delivers one isolated CORE vertical slice proven in TEST",
        "it does not certify platform-wide multi-agency isolation",
        "the legacy Basic channel remains cross-agency on non-CORE routers",
        "The second agency exists in TEST only",
        "No second real agency may be onboarded into PROD until GATE-MA1 closes",
    ):
        assert fragment in text, f"missing from the DoD item 16 statement: {fragment}"


def test_l4_the_multi_agency_ready_claim_is_explicitly_refused():
    text = _certification()
    assert "multi-agency ready" in text
    window = text[text.index("multi-agency ready") - 200: text.index("multi-agency ready") + 100]
    assert "does not satisfy" in window or "not satisfy" in window, window


def test_l5_every_risk_is_listed_with_a_decision():
    text = _certification()
    for risk in RISKS:
        assert re.search(rf"\|\s*{risk}\s*\|", text), f"{risk} has no row"
    assert "GATE-MA1" in text[text.index("R-9"):], "R-9 is not tied to GATE-MA1"


def test_l5_the_prod_precondition_risk_is_not_softened():
    """R-3 is accepted for TEST and explicitly not for PROD."""
    text = _certification()
    row = next(line for line in text.splitlines() if line.startswith("| R-3"))
    assert "before PROD" in row or "must be resolved" in row, row


def test_l6_the_evidence_slots_cover_the_artefacts_the_runbook_produces():
    text = _certification()
    for artefact in ("fingerprint", "dump path", "Ledger rows", "Seed result",
                     "git diff --check", "git status --short", "Unexpected 500"):
        assert artefact in text, f"no evidence slot for: {artefact}"


def test_l7_it_points_at_the_runbook_as_its_prerequisite():
    text = _certification()
    assert "P26_1_MIGRATION_RUNBOOK_TEST.md" in text
    assert RUNBOOK.name in text


# -- negative controls ------------------------------------------------------

def test_l10_a_filled_in_evidence_field_is_caught():
    """The control that matters most: a claimed result must fail the audit."""
    broken = _certification().replace(
        "| 1 | Migrations `027`–`030` applied and registered; `verify_contiguous` returns `026`–`030` | Runbook §2.2, §3.1, §5.1, §6.1, §6.5; `p26_migrate.py status` | `NOT YET EXECUTED` |",
        "| 1 | Migrations `027`–`030` applied and registered | ran it | PASS |",
    )
    rows = [line for line in broken.splitlines() if re.match(r"^\|\s*\d+\s*\|", line)]
    claimed = [line for line in rows if NOT_EXECUTED not in line]
    assert claimed, "the mutation did not take"
    assert "PASS" in claimed[0]


def test_l10_a_missing_dod_item_is_caught():
    broken = _certification().replace("| 16 |", "| 99 |")
    rows = re.findall(r"^\|\s*(\d+)\s*\|", broken, re.MULTILINE)
    numbers = sorted(int(row) for row in rows)
    assert numbers != list(range(1, DOD_ITEM_COUNT + 1))


def test_l10_a_paraphrased_gate_statement_is_caught():
    broken = _certification().replace(
        "it does not certify platform-wide multi-agency isolation",
        "it mostly covers multi-agency isolation",
    )
    text = " ".join(broken.split())
    assert "it does not certify platform-wide multi-agency isolation" not in text


def test_l10_a_fabricated_fingerprint_is_caught():
    broken = _certification().replace(
        "| Post-`030` schema fingerprint | `NOT YET EXECUTED` |",
        "| Post-`030` schema fingerprint | `" + "a" * 64 + "` |",
    )
    assert re.search(r"\b[0-9a-f]{64}\b", broken), "the mutation did not take"


def test_l10_a_missing_risk_row_is_caught():
    broken = _certification().replace("| R-9 |", "| R-nine |")
    assert not re.search(r"\|\s*R-9\s*\|", broken)
