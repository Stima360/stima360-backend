import json
import re
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "static/buy_admin/index.html"
JS_PATH = ROOT / "static/buy_admin/assets/app.js"


def read_html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


def read_js() -> str:
    return JS_PATH.read_text(encoding="utf-8")


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def function_block(js: str, name: str) -> str:
    match = re.search(
        rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{",
        js,
    )
    assert match, f"Funzione {name} mancante"

    start = match.end() - 1
    depth = 0
    quote = None
    escaped = False

    for index in range(start, len(js)):
        character = js[index]

        if escaped:
            escaped = False
            continue
        if quote and character == "\\":
            escaped = True
            continue
        if quote:
            if character == quote:
                quote = None
            continue
        if character in ("'", '"', "`"):
            quote = character
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return js[start : index + 1]

    raise AssertionError(f"Funzione {name} non chiusa")


def run_export(function_name: str, argument: dict, *extra_args):
    invocation_args = [argument, *extra_args]
    script = (
        f"const api=require({json.dumps(str(JS_PATH))});"
        f"const result=api[{json.dumps(function_name)}](...{json.dumps(invocation_args)});"
        "process.stdout.write(JSON.stringify(result));"
    )
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def run_export_error(function_name: str, argument: dict):
    script = (
        f"const api=require({json.dumps(str(JS_PATH))});"
        "try{"
        f"api[{json.dumps(function_name)}]({json.dumps(argument)});"
        "process.exitCode=2;"
        "}catch(error){process.stdout.write(error.message);}")
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout


def test_create_and_edit_expose_all_match_ready_scalar_fields():
    html = read_html()
    required_fields = {
        "status",
        "urgency",
        "budget_min",
        "budget_target",
        "budget_max",
        "budget_flexibility_percent",
        "surface_min",
        "surface_target",
        "surface_max",
        "rooms_min",
        "bedrooms_min",
        "bathrooms_min",
    }

    for form_id in ("form", "editForm"):
        form = re.search(
            rf'<form[^>]+id="{form_id}"[^>]*>(.*?)</form>', html, re.DOTALL
        )
        assert form, f"Form {form_id} mancante"
        body = form.group(1)
        for field in required_fields:
            assert re.search(rf'name="{field}"', body), (
                f"{field} mancante in {form_id}"
            )
        status = re.search(
            r'<select[^>]+name="status"[^>]*>(.*?)</select>', body, re.DOTALL
        )
        assert status and re.search(r'<option[^>]+value="active"', status.group(1))


def test_scalar_payload_preserves_numbers_nulls_and_validates_ranges():
    payload = run_export(
        "buildBuyRequestPayload",
        {
            "status": "active",
            "urgency": "immediate",
            "budget_min": "150000.50",
            "budget_target": "180000",
            "budget_max": "",
            "budget_flexibility_percent": "5",
            "surface_min": "70",
            "surface_target": "85.5",
            "surface_max": "100",
            "rooms_min": "3",
            "bedrooms_min": "2",
            "bathrooms_min": "",
        },
        False,
    )

    assert payload == {
        "status": "active",
        "urgency": "immediate",
        "budget_min": 150000.5,
        "budget_target": 180000,
        "budget_max": None,
        "budget_flexibility_percent": 5,
        "surface_min": 70,
        "surface_target": 85.5,
        "surface_max": 100,
        "rooms_min": 3,
        "bedrooms_min": 2,
        "bathrooms_min": None,
    }

    assert "budget_min" in run_export_error(
        "buildBuyRequestPayload",
        {"status": "active", "urgency": "flexible", "budget_min": "200", "budget_target": "100"},
    )
    assert "surface_target" in run_export_error(
        "buildBuyRequestPayload",
        {"status": "active", "urgency": "flexible", "surface_target": "120", "surface_max": "90"},
    )


@pytest.mark.parametrize(
    "values, expected_field",
    [
        (
            {
                "status": "active",
                "urgency": "flexible",
                "budget_min": "200000",
                "budget_target": None,
                "budget_max": "150000",
            },
            "budget_min",
        ),
        (
            {
                "status": "active",
                "urgency": "flexible",
                "surface_min": "100",
                "surface_target": None,
                "surface_max": "80",
            },
            "surface_min",
        ),
    ],
)
def test_scalar_payload_rejects_min_above_max_when_target_is_missing(
    values, expected_field
):
    assert expected_field in run_export_error("buildBuyRequestPayload", values)


def test_location_typology_and_feature_payloads_follow_match_contract():
    location = run_export(
        "buildLocationPayload",
        {
            "location_type": "municipality",
            "location_value": "Tortoreto",
            "priority": "8",
            "is_required": True,
            "is_excluded": False,
        },
    )
    assert location == {
        "location_type": "municipality",
        "municipality": "Tortoreto",
        "priority": 8,
        "is_required": True,
        "is_excluded": False,
    }

    assert "obbligatoria" in run_export_error(
        "buildLocationPayload",
        {
            "location_type": "province",
            "location_value": "TE",
            "priority": "5",
            "is_required": True,
            "is_excluded": True,
        },
    )

    assert run_export(
        "buildTypologyPayload",
        {"property_type": "apartment", "requirement_level": "required"},
    ) == {"property_type": "apartment", "requirement_level": "required"}

    boolean_feature = run_export(
        "buildFeaturePayload",
        {
            "feature_code": "elevator",
            "requirement_level": "required",
            "value_type": "boolean",
            "value_boolean": "false",
        },
    )
    assert boolean_feature["value_boolean"] is False
    assert "value_min" not in boolean_feature
    assert "value_text" not in boolean_feature

    range_feature = run_export(
        "buildFeaturePayload",
        {
            "feature_code": "energy_score",
            "requirement_level": "preferred",
            "value_type": "range",
            "value_min": "10",
            "value_max": "20",
        },
    )
    assert range_feature["value_min"] == 10
    assert range_feature["value_max"] == 20
    assert "value_boolean" not in range_feature


def test_relations_render_safely_and_use_only_existing_post_delete_endpoints():
    js = read_js()
    render = function_block(js, "renderMatchCriteria")
    assert ".innerHTML" not in render
    assert "document.createElement" in render
    assert ".textContent" in render

    for kind, add_name, delete_name in (
        ("locations", "addLocation", "deleteLocation"),
        ("typologies", "addTypology", "deleteTypology"),
        ("features", "addFeature", "deleteFeature"),
    ):
        add = function_block(js, add_name)
        delete = function_block(js, delete_name)
        assert "positiveId(" in add
        assert "positiveId(" in delete
        assert "method:'POST'" in compact(add)
        assert "method:'DELETE'" in compact(delete)
        assert "method:'PATCH'" not in compact(add + delete)
        assert f"'{kind}'" in add
        assert f"'{kind}'" in delete


def test_detail_and_forms_include_relation_sections_and_conditional_features():
    html = read_html()
    js = read_js()

    for marker in (
        'id="criteria-locations"',
        'id="criteria-typologies"',
        'id="criteria-features"',
        'id="locationForm"',
        'id="typologyForm"',
        'id="featureForm"',
    ):
        assert marker in html

    feature_fields = function_block(js, "updateFeatureFields")
    assert "value_type" in feature_fields
    assert "feature-boolean-fields" in feature_fields
    assert "feature-number-fields" in feature_fields
    assert "feature-text-fields" in feature_fields


def test_auth_deep_links_tasks_and_matches_remain_in_the_buy_admin():
    js = read_js()
    login = function_block(js, "login")
    detail = function_block(js, "detail")
    apply_deep_link = function_block(js, "applyDeepLink")

    assert "/api/admin/check" in login
    assert "Authorization" in function_block(js, "req")
    assert "positiveId(" in apply_deep_link
    assert "/core-admin/?view=contact360&id=${cid}" in detail
    assert "/property-admin/?id=${pid}" in detail
    assert "/match-admin/?id=${mid}" in detail
    assert "/tasks" in js
    assert "/matches/${matchId}/decision" in js


@pytest.mark.parametrize("value, expected", [("8", 8), ("0", None), ("x", None)])
def test_positive_id_remains_the_only_id_acceptance_rule(value, expected):
    assert run_export("positiveId", value) == expected
