import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from flow.engine import evaluate


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "static/buy_admin/index.html"
JS_PATH = ROOT / "static/buy_admin/assets/app.js"


def read_html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


def read_js() -> str:
    return JS_PATH.read_text(encoding="utf-8")


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


def run_node(body: str) -> dict:
    script = (
        f"const api=require({json.dumps(str(JS_PATH))});"
        "(async()=>{"
        f"{body}"
        "})().catch(error=>{process.stderr.write(error.stack||error.message);process.exit(1);});"
    )
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        env={**os.environ, "TZ": "Europe/Rome"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_next_action_dialog_requires_note_and_local_datetime():
    html = read_html()
    form = re.search(
        r'<form[^>]+id="nextActionForm"[^>]*>(.*?)</form>', html, re.DOTALL
    )
    assert form, "Dialog prossima azione mancante"
    body = form.group(1)
    assert re.search(
        r'<input[^>]+name="next_action_note"[^>]+type="text"[^>]+required', body
    )
    assert re.search(
        r'<input[^>]+name="next_action_at"[^>]+type="datetime-local"[^>]+required',
        body,
    )
    assert re.search(r'id="nextActionCancel"[^>]+type="button"', body)


def test_next_action_cancel_only_closes_and_resets_the_dialog():
    js = read_js()
    bind_ui = function_block(js, "bindUi")
    cancel_line = next(
        (line for line in bind_ui.splitlines() if "#nextActionCancel" in line), None
    )
    assert cancel_line, "Handler annullamento prossima azione mancante"
    assert ".close()" in cancel_line
    assert ".reset()" in cancel_line
    assert "req(" not in cancel_line


def test_next_action_payload_converts_rome_local_time_to_utc_iso():
    result = run_node(
        "const payload=api.buildNextActionPayload({"
        "next_action_note:' Richiamare cliente ',"
        "next_action_at:'2026-08-29T10:00'});"
        "process.stdout.write(JSON.stringify(payload));"
    )
    assert result == {
        "next_action_note": "Richiamare cliente",
        "next_action_at": "2026-08-29T08:00:00.000Z",
    }


@pytest.mark.parametrize(
    "values, expected_error",
    [
        ({"next_action_note": "", "next_action_at": "2026-08-29T10:00"}, "azione"),
        ({"next_action_note": "Richiamare", "next_action_at": ""}, "Data e ora"),
        (
            {"next_action_note": "Richiamare", "next_action_at": "non-una-data"},
            "Data e ora",
        ),
    ],
)
def test_invalid_next_action_never_calls_patch(values, expected_error):
    result = run_node(
        f"let calls=0;let error='';"
        f"try{{await api.saveNextAction(5,{json.dumps(values)},async()=>{{calls+=1;}});}}"
        "catch(exc){error=exc.message;}"
        "process.stdout.write(JSON.stringify({calls,error}));"
    )
    assert result["calls"] == 0
    assert expected_error.lower() in result["error"].lower()


@pytest.mark.parametrize("request_id", [0, -1, "x", None])
def test_next_action_request_id_must_be_positive(request_id):
    values = {
        "next_action_note": "Richiamare cliente",
        "next_action_at": "2026-08-29T10:00",
    }
    result = run_node(
        f"let calls=0;let error='';"
        f"try{{await api.saveNextAction({json.dumps(request_id)},{json.dumps(values)},async()=>{{calls+=1;}});}}"
        "catch(exc){error=exc.message;}"
        "process.stdout.write(JSON.stringify({calls,error}));"
    )
    assert result["calls"] == 0
    assert "id richiesta" in result["error"].lower()


def test_next_action_uses_exact_existing_patch_endpoint_and_complete_payload():
    values = {
        "next_action_note": "Richiamare cliente",
        "next_action_at": "2026-08-29T10:00",
    }
    result = run_node(
        f"const calls=[];"
        f"const payload=await api.saveNextAction('5',{json.dumps(values)},"
        "async(url,options)=>{calls.push({url,options});return {id:5};});"
        "process.stdout.write(JSON.stringify({calls,payload}));"
    )
    assert result == {
        "calls": [
            {
                "url": "/api/buy/requests/5",
                "options": {
                    "method": "PATCH",
                    "body": json.dumps(
                        {
                            "next_action_note": "Richiamare cliente",
                            "next_action_at": "2026-08-29T08:00:00.000Z",
                        },
                        separators=(",", ":"),
                    ),
                },
            }
        ],
        "payload": {
            "next_action_note": "Richiamare cliente",
            "next_action_at": "2026-08-29T08:00:00.000Z",
        },
    }
    assert result["payload"]["next_action_at"] is not None


def test_existing_next_action_values_are_precompiled_without_timezone_shift():
    result = run_node(
        "const values=api.nextActionFormValues({"
        "next_action_note:'Richiamare cliente',"
        "next_action_at:'2026-08-29T08:00:00.000Z'});"
        "process.stdout.write(JSON.stringify(values));"
    )
    assert result == {
        "next_action_note": "Richiamare cliente",
        "next_action_at": "2026-08-29T10:00",
    }


def test_quick_next_action_opens_the_structured_dialog_without_prompts():
    block = function_block(read_js(), "quickNextAction")
    assert "prompt(" not in block
    assert "positiveId(" in block
    assert "nextActionFormValues(" in block
    assert "showModal()" in block


def test_flow_r004_keeps_existing_past_future_and_null_semantics():
    now = datetime.now(timezone.utc)
    parameters = {"overdue_hours": 0}
    past, _ = evaluate(
        "FLOW-R004",
        {"status": "active", "next_action_at": now - timedelta(hours=1)},
        parameters,
    )
    future, _ = evaluate(
        "FLOW-R004",
        {"status": "active", "next_action_at": now + timedelta(hours=1)},
        parameters,
    )
    missing, _ = evaluate(
        "FLOW-R004",
        {"status": "active", "next_action_at": None},
        parameters,
    )
    assert past is True
    assert future is False
    assert missing is False
