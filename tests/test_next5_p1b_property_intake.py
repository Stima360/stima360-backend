import json
import re
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "static/property_admin/assets/app.js"


def read_js() -> str:
    return JS_PATH.read_text(encoding="utf-8")


def function_block(js: str, name: str) -> str:
    match = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", js)
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


def node_prelude() -> str:
    return """
const element={
  value:'',hidden:false,innerHTML:'',textContent:'',
  classList:{toggle(){},add(){},remove(){}},
  addEventListener(){},reset(){},append(){},remove(){},
  querySelector(){return element},querySelectorAll(){return []}
};
global.document={
  querySelector(){return element},querySelectorAll(){return []},
  getElementById(){return element},createElement(){return Object.assign({},element)}
};
global.window={};
global.setTimeout=()=>0;
"""


def run_export(function_name: str, *arguments):
    script = (
        node_prelude()
        + f"const api=require({json.dumps(str(JS_PATH))});"
        + f"const fn=api[{json.dumps(function_name)}];"
        + "if(typeof fn!=='function')throw new Error('export missing');"
        + f"const result=fn(...{json.dumps(arguments)});"
        + "process.stdout.write(JSON.stringify(result));"
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


def run_export_error(function_name: str, argument: dict) -> str:
    script = (
        node_prelude()
        + f"const api=require({json.dumps(str(JS_PATH))});"
        + f"const fn=api[{json.dumps(function_name)}];"
        + "if(typeof fn!=='function')throw new Error('export missing');"
        + "try{"
        + f"fn({json.dumps(argument)});process.exitCode=2;"
        + "}catch(error){process.stdout.write(error.message)}"
    )
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout


def complete_values(**overrides):
    values = {
        "code": "P-10",
        "title": "Appartamento centro",
        "commercial_status": "active",
        "classification": "A",
        "property_type": "apartment",
        "city": "Tortoreto",
        "province": "TE",
        "microzone": "Lido Centro",
        "address": "Via Roma",
        "surface_sqm": "0",
        "commercial_surface_sqm": "85.5",
        "rooms": "0",
        "bedrooms": "2",
        "bathrooms": "",
        "elevator": "false",
        "condition": "good",
        "energy_class": "A4",
        "asking_price": "0",
        "mandate_end": "",
        "assigned_to": "",
        "internal_notes": "",
    }
    values.update(overrides)
    return values


def test_payload_serializes_all_match_fields_and_preserves_zero_false_and_null():
    payload = run_export("buildPropertyPayload", complete_values())
    assert payload == {
        "code": "P-10",
        "title": "Appartamento centro",
        "commercial_status": "active",
        "classification": "A",
        "property_type": "apartment",
        "city": "Tortoreto",
        "province": "TE",
        "microzone": "Lido Centro",
        "address": "Via Roma",
        "surface_sqm": 0,
        "commercial_surface_sqm": 85.5,
        "rooms": 0,
        "bedrooms": 2,
        "bathrooms": None,
        "elevator": False,
        "condition": "good",
        "energy_class": "A4",
        "asking_price": 0,
        "mandate_end": None,
        "assigned_to": None,
        "internal_notes": None,
    }

    empty = run_export(
        "buildPropertyPayload",
        complete_values(
            province="",
            microzone="",
            commercial_surface_sqm="",
            rooms="",
            bedrooms="",
            bathrooms="",
            elevator="",
            condition="",
            energy_class="",
        ),
    )
    for field in (
        "province",
        "microzone",
        "commercial_surface_sqm",
        "rooms",
        "bedrooms",
        "bathrooms",
        "elevator",
        "condition",
        "energy_class",
    ):
        assert empty[field] is None

    assert run_export("buildPropertyPayload", complete_values(elevator="true"))["elevator"] is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("surface_sqm", "-1"),
        ("commercial_surface_sqm", "-1"),
        ("asking_price", "-1"),
        ("rooms", "-1"),
        ("bedrooms", "1.5"),
        ("bathrooms", "NaN"),
    ],
)
def test_payload_rejects_invalid_or_negative_numbers(field, value):
    assert field in run_export_error("buildPropertyPayload", complete_values(**{field: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("commercial_status", "invalid"),
        ("property_type", "invalid"),
        ("classification", "D"),
        ("elevator", "unknown"),
    ],
)
def test_payload_rejects_values_outside_real_enums(field, value):
    assert field in run_export_error("buildPropertyPayload", complete_values(**{field: value}))


def test_create_and_edit_render_and_prefill_every_p1b_field():
    form = function_block(read_js(), "propertyForm")
    field_ids = {
        "province": "pprovince",
        "microzone": "pmicrozone",
        "commercial_surface_sqm": "pcommercialsqm",
        "rooms": "prooms",
        "bedrooms": "pbedrooms",
        "bathrooms": "pbathrooms",
        "elevator": "pelevator",
        "condition": "pcondition",
        "energy_class": "penergy",
    }
    for field, field_id in field_ids.items():
        assert f'id="{field_id}"' in form
        assert f"p?.{field}" in form
    assert "buildPropertyPayload(" in form
    assert "metadata" not in form.lower()


def test_existing_property_endpoints_and_create_detail_flow_are_reused():
    js = read_js()
    form = function_block(js, "propertyForm")
    detail = function_block(js, "openDetail")
    assert "'/properties'" in form
    assert "`/properties/${" in form
    assert "method:p?'PATCH':'POST'" in re.sub(r"\s+", "", form)
    assert "positiveId(" in form
    assert "openDetail(" in form
    assert "`/properties/${id}`" in detail


@pytest.mark.parametrize(
    "status,archived_at,expected",
    [
        ("mandate", None, True),
        ("active", None, True),
        ("reserved", None, True),
        ("under_offer", None, True),
        ("draft", None, False),
        ("active", "2026-08-28T10:00:00Z", False),
    ],
)
def test_match_eligibility_requires_matchable_status_and_no_archive(status, archived_at, expected):
    assert run_export(
        "isPropertyMatchEligible",
        {"commercial_status": status, "archived_at": archived_at},
    ) is expected


def test_match_detail_is_complete_and_escapes_api_values():
    markup = run_export(
        "renderMatchData",
        {
            "commercial_status": "active",
            "archived_at": None,
            "property_type": "apartment",
            "city": "<script>city</script>",
            "province": "TE",
            "microzone": "Lido Centro",
            "asking_price": 190000,
            "surface_sqm": 80,
            "commercial_surface_sqm": 85,
            "rooms": 3,
            "bedrooms": 2,
            "bathrooms": 1,
            "elevator": False,
            "condition": "good",
            "energy_class": "A4",
        },
    )
    assert "Dati usati da MATCH" in markup
    assert "Eleggibile" in markup
    for value in ("apartment", "TE", "Lido Centro", "190", "80", "85", "3", "2", "1", "No", "good", "A4"):
        assert value in markup
    assert "<script>city</script>" not in markup
    assert "&lt;script&gt;city&lt;/script&gt;" in markup


def test_auth_deep_link_and_existing_non_match_readiness_remain_separate():
    js = read_js()
    assert "/api/admin/check" in function_block(js, "login")
    assert "Authorization" in function_block(js, "api")
    assert "positiveId(" in function_block(js, "applyDeepLink")
    assert "openDetail(id)" in function_block(js, "applyDeepLink")
    assert "readiness_score" not in function_block(js, "renderMatchData")
    assert "metadata" not in function_block(js, "renderMatchData").lower()
