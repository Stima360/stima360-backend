"""P26-0 - the baseline fingerprint must survive a dump/restore cycle.

Every ``SOURCE``/``RESTORED`` pair below is a real definition taken from
``reports/p26_baseline_TEST_20260905T170601Z.json`` together with the form
``pg_dump``/``pg_restore`` produced for it during the TEST restore drill. They
are not invented examples.

The suite asserts both directions of the contract:

* equivalent deparse forms must reach the same fingerprint representation,
  otherwise a restore looks like a schema change;
* genuinely different definitions must still reach different representations,
  otherwise the fingerprint has been weakened into uselessness.

The second half matters more than the first. A canonicaliser that returned a
constant would satisfy every equivalence test ever written.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def canon():
    return _load(SCRIPTS / "p26_sql_canonical.py", "p26_sql_canonical")


@pytest.fixture(scope="module")
def snapshot_module():
    return _load(SCRIPTS / "p26_schema_snapshot.py", "p26_schema_snapshot")


# ---------------------------------------------------------------------------
# The three real shapes observed in the restore diff
# ---------------------------------------------------------------------------

CHECK_ANY_ARRAY_SOURCE = (
    "CHECK (((activity_type)::text = ANY ((ARRAY['note'::character varying, "
    "'call'::character varying, 'email'::character varying, "
    "'whatsapp'::character varying, 'meeting'::character varying, "
    "'valuation'::character varying, 'status_change'::character varying, "
    "'system'::character varying])::text[])))"
)
CHECK_ANY_ARRAY_RESTORED = (
    "CHECK (((activity_type)::text = ANY (ARRAY[('note'::character varying)::text, "
    "('call'::character varying)::text, ('email'::character varying)::text, "
    "('whatsapp'::character varying)::text, ('meeting'::character varying)::text, "
    "('valuation'::character varying)::text, ('status_change'::character varying)::text, "
    "('system'::character varying)::text])))"
)

CHECK_NULLABLE_ANY_ARRAY_SOURCE = (
    "CHECK (((direction IS NULL) OR ((direction)::text = ANY "
    "((ARRAY['in'::character varying, 'out'::character varying, "
    "'internal'::character varying])::text[]))))"
)
CHECK_NULLABLE_ANY_ARRAY_RESTORED = (
    "CHECK (((direction IS NULL) OR ((direction)::text = ANY "
    "(ARRAY[('in'::character varying)::text, ('out'::character varying)::text, "
    "('internal'::character varying)::text]))))"
)

PARTIAL_UNIQUE_INDEX_SOURCE = (
    "CREATE UNIQUE INDEX uq_property_proposals_open_match ON "
    "public.property_proposals USING btree (match_id) WHERE ((status)::text = ANY "
    "((ARRAY['draft'::character varying, 'submitted'::character varying])::text[]))"
)
PARTIAL_UNIQUE_INDEX_RESTORED = (
    "CREATE UNIQUE INDEX uq_property_proposals_open_match ON "
    "public.property_proposals USING btree (match_id) WHERE ((status)::text = ANY "
    "(ARRAY[('draft'::character varying)::text, ('submitted'::character varying)::text]))"
)

EQUIVALENT_PAIRS = [
    pytest.param(
        CHECK_ANY_ARRAY_SOURCE, CHECK_ANY_ARRAY_RESTORED, id="check_any_array"
    ),
    pytest.param(
        CHECK_NULLABLE_ANY_ARRAY_SOURCE,
        CHECK_NULLABLE_ANY_ARRAY_RESTORED,
        id="check_nullable_any_array",
    ),
    pytest.param(
        PARTIAL_UNIQUE_INDEX_SOURCE,
        PARTIAL_UNIQUE_INDEX_RESTORED,
        id="partial_unique_index_predicate",
    ),
]


@pytest.mark.parametrize("source,restored", EQUIVALENT_PAIRS)
def test_restore_rewrite_reaches_the_same_canonical_form(canon, source, restored):
    assert canon.canonicalise_expression(source) == canon.canonicalise_expression(
        restored
    )


@pytest.mark.parametrize("source,restored", EQUIVALENT_PAIRS)
def test_the_two_raw_forms_really_do_differ(canon, source, restored):
    """Guards the fixtures themselves.

    If the raw strings were accidentally identical the equivalence tests above
    would pass without exercising anything.
    """
    assert source != restored


# ---------------------------------------------------------------------------
# Real differences must survive canonicalisation
# ---------------------------------------------------------------------------

DIFFERENT_PAIRS = [
    pytest.param(
        "CHECK (((s)::text = ANY ((ARRAY['draft'::character varying])::text[])))",
        "CHECK (((s)::text = ANY ((ARRAY['drafts'::character varying])::text[])))",
        id="literal_value_differs",
    ),
    pytest.param(
        "CHECK (((s)::text = ANY ((ARRAY['a'::character varying])::text[])))",
        "CHECK (((s)::text = ANY ((ARRAY['a'::character varying, "
        "'b'::character varying])::text[])))",
        id="allowed_value_added",
    ),
    pytest.param(
        "CHECK (((s)::text = ANY ((ARRAY['a'::character varying])::text[])))",
        "CHECK (((t)::text = ANY ((ARRAY['a'::character varying])::text[])))",
        id="column_differs",
    ),
    pytest.param(
        "CHECK (((direction IS NULL) OR ((direction)::text = 'in'::text)))",
        "CHECK (((direction)::text = 'in'::text))",
        id="nullability_branch_removed",
    ),
    pytest.param("(x)::text", "(x)::integer", id="cast_target_differs"),
    pytest.param("'7'::text", "'7'::integer", id="literal_cast_target_differs"),
    pytest.param(
        "CREATE UNIQUE INDEX i ON public.t USING btree (a) WHERE (b IS NULL)",
        "CREATE UNIQUE INDEX i ON public.t USING btree (a) WHERE (c IS NULL)",
        id="index_predicate_column_differs",
    ),
    pytest.param(
        "CREATE UNIQUE INDEX i ON public.t USING btree (a) WHERE (b IS NULL)",
        "CREATE INDEX i ON public.t USING btree (a) WHERE (b IS NULL)",
        id="index_uniqueness_differs",
    ),
    pytest.param(
        "CREATE INDEX i ON public.t USING btree (a)",
        "CREATE INDEX i ON public.t USING btree (a, b)",
        id="index_column_added",
    ),
]


@pytest.mark.parametrize("left,right", DIFFERENT_PAIRS)
def test_real_differences_are_preserved(canon, left, right):
    assert canon.canonicalise_expression(left) != canon.canonicalise_expression(right)


def test_a_length_modifier_is_never_collapsed(canon):
    """A cast through varchar(3) truncates and must not be treated as lossless."""
    truncating = "((x)::character varying(3))::text"
    plain = "(x)::text"
    assert canon.canonicalise_expression(truncating) != canon.canonicalise_expression(
        plain
    )


def test_bpchar_is_not_treated_as_lossless(canon):
    """character pads to its declared length, so the chain must stay visible."""
    padded = "('a'::character)::text"
    plain = "'a'::text"
    assert canon.canonicalise_expression(padded) != canon.canonicalise_expression(plain)


# ---------------------------------------------------------------------------
# Properties of the canonicaliser itself
# ---------------------------------------------------------------------------

def test_canonicalisation_is_idempotent(canon):
    once = canon.canonicalise_expression(CHECK_ANY_ARRAY_SOURCE)
    assert canon.canonicalise_expression(once) == once


def test_none_stays_none(canon):
    assert canon.canonicalise_expression(None) is None


def test_unparsable_text_is_not_discarded(canon):
    """A definition it cannot parse must still carry its difference."""
    left = canon.canonicalise_expression("CHECK (weird @@@ 'unterminated")
    right = canon.canonicalise_expression("CHECK (weird @@@ 'other")
    assert left and right
    assert left != right


def test_type_spelling_aliases_agree(canon):
    assert canon.canonicalise_expression("x::integer") == canon.canonicalise_expression(
        "x::int4"
    )
    assert canon.canonicalise_expression(
        "x::timestamp with time zone"
    ) == canon.canonicalise_expression("x::timestamptz")


def test_whitespace_and_identifier_case_do_not_matter(canon):
    assert canon.canonicalise_expression(
        "CHECK ((a IS NULL))"
    ) == canon.canonicalise_expression("check  (( a   IS   NULL ))")


# ---------------------------------------------------------------------------
# Integration with the snapshot payload
# ---------------------------------------------------------------------------

def _payload(constraint_definition: str, index_definition: str) -> dict:
    return {
        "snapshot_format": "p26-snapshot-2",
        "tables": [{"table_name": "t", "table_type": "BASE TABLE"}],
        "constraints": [
            {
                "table_name": "t",
                "constraint_name": "t_chk",
                "constraint_type": "c",
                "definition": constraint_definition,
            }
        ],
        "indexes": [
            {"tablename": "t", "indexname": "i", "indexdef": index_definition}
        ],
    }


def test_snapshot_fingerprint_is_restore_stable(snapshot_module):
    source = snapshot_module.canonicalise_payload(
        _payload(CHECK_ANY_ARRAY_SOURCE, PARTIAL_UNIQUE_INDEX_SOURCE)
    )
    restored = snapshot_module.canonicalise_payload(
        _payload(CHECK_ANY_ARRAY_RESTORED, PARTIAL_UNIQUE_INDEX_RESTORED)
    )
    assert snapshot_module.fingerprint(source) == snapshot_module.fingerprint(restored)


def test_snapshot_fingerprint_still_detects_a_real_change(snapshot_module):
    before = snapshot_module.canonicalise_payload(
        _payload(CHECK_ANY_ARRAY_SOURCE, PARTIAL_UNIQUE_INDEX_SOURCE)
    )
    after = snapshot_module.canonicalise_payload(
        _payload(
            CHECK_ANY_ARRAY_SOURCE.replace("'system'", "'systems'"),
            PARTIAL_UNIQUE_INDEX_SOURCE,
        )
    )
    assert snapshot_module.fingerprint(before) != snapshot_module.fingerprint(after)


def test_raw_definitions_are_kept_for_audit(snapshot_module):
    payload = _payload(CHECK_ANY_ARRAY_SOURCE, PARTIAL_UNIQUE_INDEX_SOURCE)
    enriched = snapshot_module.annotate_payload(payload)
    constraint = enriched["constraints"][0]
    assert constraint["definition"] == CHECK_ANY_ARRAY_SOURCE
    assert constraint["definition_canonical"] != CHECK_ANY_ARRAY_SOURCE
    index = enriched["indexes"][0]
    assert index["indexdef"] == PARTIAL_UNIQUE_INDEX_SOURCE
    assert "indexdef_canonical" in index


def test_fingerprint_payload_excludes_the_raw_definitions(snapshot_module):
    """The raw text is audit evidence, never fingerprint input."""
    payload = snapshot_module.canonicalise_payload(
        _payload(CHECK_ANY_ARRAY_SOURCE, PARTIAL_UNIQUE_INDEX_SOURCE)
    )
    assert "definition" not in payload["constraints"][0]
    assert "definition_canonical" in payload["constraints"][0]
    assert "indexdef" not in payload["indexes"][0]
    assert "indexdef_canonical" in payload["indexes"][0]


def test_row_order_does_not_depend_on_the_raw_text(snapshot_module):
    """Two rows equal after canonicalisation must sort identically.

    Sorting the artefact by raw text and then dropping that text would let the
    restore produce the same rows in a different order, and a different digest.
    """
    left = snapshot_module.canonicalise_payload(
        {
            "snapshot_format": "p26-snapshot-2",
            "constraints": [
                {"constraint_name": "b", "definition": CHECK_ANY_ARRAY_SOURCE},
                {"constraint_name": "a", "definition": CHECK_ANY_ARRAY_SOURCE},
            ],
        }
    )
    right = snapshot_module.canonicalise_payload(
        {
            "snapshot_format": "p26-snapshot-2",
            "constraints": [
                {"constraint_name": "a", "definition": CHECK_ANY_ARRAY_RESTORED},
                {"constraint_name": "b", "definition": CHECK_ANY_ARRAY_RESTORED},
            ],
        }
    )
    assert snapshot_module.fingerprint(left) == snapshot_module.fingerprint(right)


def test_snapshot_format_version_was_raised(snapshot_module):
    """A changed fingerprint algorithm must not masquerade as the old one."""
    assert snapshot_module.SNAPSHOT_FORMAT_VERSION == "p26-snapshot-2"


# ---------------------------------------------------------------------------
# The same property, checked against the whole real artefact
# ---------------------------------------------------------------------------
# Three hand-picked pairs prove the rule fires. This exercises it over every
# affected definition in the real TEST schema at once, so a regression that
# only shows up on some other predicate shape still fails the suite.

ARTIFACT = ROOT / "reports" / "p26_baseline_TEST_20260905T170601Z.json"

_ARRAY_CAST = re.compile(r"\(ARRAY\[(.*?)\]\)::text\[\]")


def _to_restored_form(text: str) -> str:
    """Rewrite a source deparse into the form pg_restore produces.

    One cast on the whole array becomes one cast per element. This mirrors the
    difference recorded in the restore drill.
    """

    def repl(match: re.Match) -> str:
        elements = [e.strip() for e in match.group(1).split(", ")]
        return "ARRAY[" + ", ".join(f"({e})::text" for e in elements) + "]"

    return _ARRAY_CAST.sub(repl, text)


@pytest.mark.skipif(not ARTIFACT.exists(), reason="baseline artefact not present")
def test_every_real_definition_is_restore_stable(snapshot_module):
    raw = json.loads(ARTIFACT.read_text(encoding="utf-8"))["schema"]
    raw["snapshot_format"] = snapshot_module.SNAPSHOT_FORMAT_VERSION

    simulated = copy.deepcopy(raw)
    rewritten = 0
    for row in simulated.get("constraints", []):
        new = _to_restored_form(row["definition"])
        rewritten += new != row["definition"]
        row["definition"] = new
    for row in simulated.get("indexes", []):
        new = _to_restored_form(row["indexdef"])
        rewritten += new != row["indexdef"]
        row["indexdef"] = new

    # If nothing was rewritten the assertion below would be vacuous.
    assert rewritten > 50, "the affected shape should appear across the schema"

    source_digest = snapshot_module.fingerprint(
        snapshot_module.canonicalise_payload(raw)
    )
    restored_digest = snapshot_module.fingerprint(
        snapshot_module.canonicalise_payload(simulated)
    )
    assert source_digest == restored_digest


@pytest.mark.skipif(not ARTIFACT.exists(), reason="baseline artefact not present")
def test_the_old_algorithm_really_was_broken(snapshot_module):
    """Reproduces the defect, so the fix cannot be quietly reverted."""
    raw = json.loads(ARTIFACT.read_text(encoding="utf-8"))["schema"]
    simulated = copy.deepcopy(raw)
    for row in simulated.get("constraints", []):
        row["definition"] = _to_restored_form(row["definition"])
    for row in simulated.get("indexes", []):
        row["indexdef"] = _to_restored_form(row["indexdef"])

    # Hashing the raw payload is exactly what p26-snapshot-1 did.
    assert snapshot_module.fingerprint(raw) != snapshot_module.fingerprint(simulated)
