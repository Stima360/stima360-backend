import importlib
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.exceptions import ValidationError


ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "static/match_admin/assets/app.js"


def readiness_module():
    try:
        return importlib.import_module("match.readiness")
    except ModuleNotFoundError:
        pytest.fail("match.readiness centralizzato mancante")


def buy(**overrides):
    value = {
        "id": 1,
        "status": "active",
        "archived_at": None,
        "budget_min": None,
        "budget_target": None,
        "budget_max": None,
        "budget_flexibility_percent": 0,
        "surface_min": None,
        "surface_target": None,
        "surface_max": None,
        "rooms_min": None,
        "bedrooms_min": None,
        "bathrooms_min": None,
        "urgency": "flexible",
        "locations": [],
        "typologies": [],
        "features": [],
    }
    value.update(overrides)
    return value


def prop(**overrides):
    value = {
        "id": 2,
        "commercial_status": "active",
        "archived_at": None,
        "city": None,
        "province": None,
        "microzone": None,
        "property_type": "apartment",
        "asking_price": None,
        "surface_sqm": None,
        "commercial_surface_sqm": None,
        "rooms": None,
        "bedrooms": None,
        "bathrooms": None,
        "elevator": None,
        "condition": None,
        "energy_class": None,
        "metadata": {},
    }
    value.update(overrides)
    return value


def test_buy_without_effective_criteria_is_not_ready_but_is_eligible():
    result = readiness_module().buy_readiness(buy())

    assert result == {
        "id": 1,
        "eligible": True,
        "ready": False,
        "can_match": False,
        "reasons": ["Nessun criterio MATCH effettivo impostato"],
        "eligibility_reasons": [],
    }


@pytest.mark.parametrize(
    "criterion",
    [
        {"budget_target": 0},
        {"budget_max": 220000},
        {"surface_min": 0},
        {"surface_target": 80},
        {"surface_max": 100},
        {"rooms_min": 0},
        {"bedrooms_min": 2},
        {"bathrooms_min": 1},
        {"locations": [{"province": "TE"}]},
        {"locations": [{"municipality": "Tortoreto"}]},
        {"locations": [{"microzone": "Centro"}]},
        {"typologies": [{"property_type": "apartment"}]},
        {
            "features": [
                {
                    "feature_code": "elevator",
                    "value_type": "boolean",
                    "value_boolean": False,
                }
            ]
        },
        {
            "features": [
                {
                    "feature_code": "rooms",
                    "value_type": "range",
                    "value_min": 0,
                    "value_max": None,
                }
            ]
        },
        {
            "features": [
                {
                    "feature_code": "condition",
                    "value_type": "text",
                    "value_text": "good",
                }
            ]
        },
    ],
)
def test_each_engine_consumed_buy_criterion_makes_buy_ready(criterion):
    result = readiness_module().buy_readiness(buy(**criterion))

    assert result["eligible"] is True
    assert result["ready"] is True
    assert result["can_match"] is True
    assert result["reasons"] == []


@pytest.mark.parametrize(
    "unused",
    [
        {"budget_min": 150000},
        {"budget_flexibility_percent": 10},
        {"urgency": "immediate"},
        {"locations": [{"region": "Abruzzo"}]},
        {"locations": [{"radius_km": 10}]},
        {
            "features": [
                {
                    "feature_code": "rooms",
                    "value_type": "range",
                    "value_target": 3,
                }
            ]
        },
        {
            "features": [
                {
                    "feature_code": "elevator",
                    "value_type": "boolean",
                    "value_boolean": None,
                }
            ]
        },
    ],
)
def test_fields_not_consumed_by_engine_do_not_make_buy_ready(unused):
    result = readiness_module().buy_readiness(buy(**unused))

    assert result["ready"] is False
    assert result["can_match"] is False


def test_buy_readiness_is_independent_from_eligibility():
    result = readiness_module().buy_readiness(
        buy(status="draft", budget_target=180000)
    )

    assert result["ready"] is True
    assert result["eligible"] is False
    assert result["can_match"] is False
    assert result["reasons"] == []
    assert result["eligibility_reasons"] == ["Stato BUY non attivo"]


@pytest.mark.parametrize(
    "property_values,eligible",
    [
        ({}, True),
        ({"asking_price": 0}, True),
        ({"elevator": False}, True),
        ({"commercial_status": "draft"}, False),
        ({"archived_at": "2026-08-28T10:00:00Z"}, False),
    ],
)
def test_property_missing_data_does_not_create_new_readiness_gate(
    property_values, eligible
):
    result = readiness_module().property_readiness(prop(**property_values))

    assert result["ready"] is True
    assert result["reasons"] == []
    assert result["eligible"] is eligible
    assert result["can_match"] is eligible


def test_aggregate_keeps_eligibility_readiness_and_can_match_distinct():
    result = readiness_module().match_readiness(
        buy(status="draft", budget_max=200000), prop()
    )

    assert result["eligible"] is False
    assert result["ready"] is True
    assert result["can_match"] is False
    assert result["buy"]["ready"] is True
    assert result["property"]["ready"] is True


@contextmanager
def cursor_context(cursor):
    yield None, cursor


class ExplodingCursor:
    def execute(self, *_args, **_kwargs):
        raise AssertionError("query non prevista prima del readiness gate")


def test_single_calculate_stops_before_engine_when_buy_is_not_ready(monkeypatch):
    repository = importlib.import_module("match.repository")
    monkeypatch.setattr(repository, "core_cursor", lambda commit=False: cursor_context(object()))
    monkeypatch.setattr(repository, "_buy", lambda _cur, _id: buy())
    monkeypatch.setattr(repository, "_property", lambda _cur, _id: prop())
    monkeypatch.setattr(repository, "_is_excluded", lambda *_args: False)
    monkeypatch.setattr(
        repository,
        "calculate",
        lambda *_args: (_ for _ in ()).throw(AssertionError("engine invocato")),
    )

    with pytest.raises(ValidationError, match="Nessun criterio MATCH effettivo"):
        repository.calculate_pair(1, 2)


def test_batch_primary_side_is_rejected_before_candidate_query(monkeypatch):
    repository = importlib.import_module("match.repository")
    monkeypatch.setattr(
        repository,
        "core_cursor",
        lambda commit=False: cursor_context(ExplodingCursor()),
    )
    monkeypatch.setattr(repository, "_buy", lambda _cur, _id: buy())

    with pytest.raises(ValidationError, match="Nessun criterio MATCH effettivo"):
        repository.calculate_for_buy(1)


class CandidateCursor:
    def execute(self, *_args, **_kwargs):
        return None

    def fetchall(self):
        return [{"id": 1}, {"id": 2}]


def test_batch_skips_unusable_counterpart_without_blocking_valid_one(monkeypatch):
    repository = importlib.import_module("match.repository")
    monkeypatch.setattr(
        repository,
        "core_cursor",
        lambda commit=False: cursor_context(CandidateCursor()),
    )
    monkeypatch.setattr(repository, "_property", lambda _cur, _id: prop())

    def pair(request_id, _property_id, *_args):
        if request_id == 1:
            raise ValidationError("Nessun criterio MATCH effettivo impostato")
        return {"id": 20, "buy_request_id": 2, "property_id": 9}

    monkeypatch.setattr(repository, "calculate_pair", pair)

    result = repository.calculate_for_property(9)

    assert result["count"] == 1
    assert result["items"] == [{"id": 20, "buy_request_id": 2, "property_id": 9}]
    assert result["errors"] == [
        {
            "buy_request_id": 1,
            "error": "Nessun criterio MATCH effettivo impostato",
        }
    ]


def test_refresh_buy_applies_gate_before_stale_detection(monkeypatch):
    repository = importlib.import_module("match.repository")
    monkeypatch.setattr(
        repository,
        "core_cursor",
        lambda commit=False: cursor_context(ExplodingCursor()),
    )
    monkeypatch.setattr(repository, "_buy", lambda _cur, _id: buy())
    monkeypatch.setattr(
        repository,
        "detect_stale",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("stale detection invocata")),
    )

    with pytest.raises(ValidationError, match="Nessun criterio MATCH effettivo"):
        repository.refresh_for_buy(1)


class ReadinessCursor:
    def __init__(self):
        self.query = ""

    def execute(self, query, _params=None):
        self.query = query

    def fetchone(self):
        if "FROM buy_requests" in self.query:
            return buy()
        return None

    def fetchall(self):
        return []


def test_readiness_repository_does_not_execute_scoring(monkeypatch):
    repository = importlib.import_module("match.repository")
    monkeypatch.setattr(
        repository,
        "core_cursor",
        lambda commit=False: cursor_context(ReadinessCursor()),
    )
    monkeypatch.setattr(
        repository,
        "calculate",
        lambda *_args: (_ for _ in ()).throw(AssertionError("engine invocato")),
    )
    readiness = getattr(repository, "get_readiness", None)
    assert callable(readiness), "repository.get_readiness mancante"

    result = readiness(1, None)

    assert result["ready"] is False
    assert result["buy"]["id"] == 1
    assert result["property"] is None


def test_readiness_endpoint_accepts_one_or_both_positive_ids(monkeypatch):
    router = importlib.import_module("match.router")
    service = importlib.import_module("match.service")
    endpoint = getattr(service, "get_readiness", None)
    assert callable(endpoint), "service.get_readiness mancante"
    monkeypatch.setattr(
        service,
        "get_readiness",
        lambda buy_request_id, property_id: {
            "eligible": True,
            "ready": True,
            "can_match": True,
            "buy": {"id": buy_request_id},
            "property": {"id": property_id} if property_id else None,
        },
    )
    app = FastAPI()
    app.include_router(router.router)
    client = TestClient(app)

    one = client.get("/api/match/readiness?buy_request_id=1")
    both = client.get("/api/match/readiness?buy_request_id=1&property_id=2")

    assert one.status_code == 200
    assert one.json()["buy"]["id"] == 1
    assert both.status_code == 200
    assert both.json()["property"]["id"] == 2


def node_prelude() -> str:
    return r"""
const elements={};
const innerHTMLWrites=[];
function makeElement(id=''){
  const value={
    id,value:'',hidden:false,style:{},children:[],dataset:{},className:'',textContent:'',
    classList:{add(){},remove(){},toggle(){}},
    addEventListener(){},reset(){},showModal(){},close(){},remove(){},
    append(...nodes){this.children.push(...nodes)},
    appendChild(node){this.children.push(node);return node},
    replaceChildren(...nodes){this.children=[...nodes]},
  };
  Object.defineProperty(value,'innerHTML',{
    get(){return ''},
    set(html){innerHTMLWrites.push({id,html})}
  });
  return value;
}
global.document={
  getElementById(id){return elements[id]||(elements[id]=makeElement(id))},
  querySelectorAll(){return []},
  createElement(tag){return makeElement(tag)}
};
global.window={location:{search:''}};
global.setTimeout=()=>0;
global.btoa=value=>Buffer.from(value,'binary').toString('base64');
"""


def run_node(script: str):
    result = subprocess.run(
        ["node", "-e", node_prelude() + script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_match_admin_preflight_blocks_calculate_and_renders_reasons_as_text():
    result = run_node(
        f"""
const calls=[];
global.fetch=async(url,opt={{}})=>{{
  calls.push({{url,opt}});
  return {{ok:true,status:200,statusText:'OK',json:async()=>({{
    eligible:true,ready:false,can_match:false,
    buy:{{id:1,eligible:true,ready:false,can_match:false,reasons:['<img src=x onerror=alert(1)>'],eligibility_reasons:[]}},
    property:{{id:2,eligible:true,ready:true,can_match:true,reasons:[],eligibility_reasons:[]}}
  }})}};
}};
const app=require({json.dumps(str(JS_PATH))});
if(typeof app.calcSingle!=='function')throw new Error('calcSingle export mancante');
document.getElementById('singleBuy').value='1';
document.getElementById('singleProp').value='2';
(async()=>{{
  await app.calcSingle();
  function texts(node){{return [node.textContent,...node.children.flatMap(texts)].filter(Boolean)}}
  process.stdout.write(JSON.stringify({{calls,texts:texts(elements.calcResult),innerHTMLWrites}}));
}})();
"""
    )

    assert len(result["calls"]) == 1
    assert result["calls"][0]["url"].endswith(
        "/api/match/readiness?buy_request_id=1&property_id=2"
    )
    assert any("NOT READY" in text for text in result["texts"])
    assert "<img src=x onerror=alert(1)>" in result["texts"]
    assert result["innerHTMLWrites"] == []


def test_match_admin_preserves_auth_and_p1_deep_links():
    source = JS_PATH.read_text(encoding="utf-8")

    assert "headers.Authorization=encodeBasic" in source
    assert "/api/admin/check" in source
    assert "positiveId(params.get('id'))" in source
    assert 'href="/buy-admin/?id=${bid}"' in source
    assert 'href="/property-admin/?id=${pid}"' in source
