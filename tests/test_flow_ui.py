from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
JS=(ROOT/'static/flow_admin/assets/app.js').read_text()
HTML=(ROOT/'static/flow_admin/index.html').read_text()

def test_ui_route_assets(): assert '/flow-admin/assets/app.js' in HTML and '/flow-admin/assets/app.css' in HTML
def test_ui_has_simulation_before_activation(): assert 'Simula' in JS and 'last_simulation_status' in JS
def test_ui_no_rule_creation(): assert '/rules",{method:\'POST\'' not in JS and 'Crea regola' not in HTML
def test_ui_has_only_internal_navigation(): assert '/match-admin/' in HTML and '/buy-admin/' in HTML
