import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static/core_admin/assets/app.js").read_text(encoding="utf-8")
HTML = (ROOT / "static/core_admin/index.html").read_text(encoding="utf-8")


def block(name):
    m = re.search(
        rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{",
        JS,
    )
    assert m, f"Funzione {name} mancante"

    start = JS.find("{", m.start())
    depth = 0

    for i in range(start, len(JS)):
        if JS[i] == "{":
            depth += 1
        elif JS[i] == "}":
            depth -= 1
            if depth == 0:
                return JS[start:i + 1]

    raise AssertionError(f"Funzione {name} non chiusa")


def test_agenda_nav_and_render_registration():
    assert 'data-view="agenda"' in HTML

    render = block("render")
    compact = re.sub(r"\s+", "", render)

    assert (
        "agenda:['Agenda','TaskCORE,follow-upBUYevisitePROPERTY']"
        in compact
    )
    assert "agenda:renderAgenda" in compact


def test_agenda_uses_existing_readonly_endpoints():
    agenda = block("renderAgenda")

    assert "api('/api/buy/requests?limit=200')" in agenda
    assert "api('/api/property/visits?limit=500')" in agenda

    for method in ("POST", "PATCH", "DELETE"):
        assert f"method:'{method}'" not in agenda
        assert f'method:"{method}"' not in agenda


def test_agenda_collects_core_tasks_due_or_overdue():
    agenda = block("renderAgenda")
    compact = re.sub(r"\s+", "", agenda)

    assert "state.tasks.filter" in compact
    assert (
        "!['completed','cancelled'].includes(t.status)"
        in compact
    )
    assert "newDate(t.due_at)<=todayEnd" in compact


def test_agenda_collects_active_buy_followups_due_or_overdue():
    agenda = block("renderAgenda")
    compact = re.sub(r"\s+", "", agenda)

    assert "b.status==='active'" in compact
    assert "b.next_action_at" in compact
    assert "newDate(b.next_action_at)<=todayEnd" in compact
    assert "id:positiveId(b.id)" in compact


def test_agenda_collects_today_property_visits():
    agenda = block("renderAgenda")
    compact = re.sub(r"\s+", "", agenda)

    assert (
        "['scheduled','confirmed'].includes(v.status)"
        in compact
    )
    assert "sameDay(v.scheduled_at)" in compact
    assert "propertyId:positiveId(v.property_id)" in compact


def test_agenda_reuses_p1_safe_links():
    agenda = block("renderAgenda")

    assert 'href="/buy-admin/?id=${item.id}"' in agenda
    assert (
        'href="/property-admin/?id=${item.propertyId}"'
        in agenda
    )

    assert agenda.count('target="_blank"') >= 2
    assert agenda.count('rel="noopener noreferrer"') >= 2


def test_agenda_handles_view_change_and_errors_safely():
    agenda = block("renderAgenda")
    compact = re.sub(r"\s+", "", agenda)

    assert "if(state.view!=='agenda')return" in compact
    assert "catch(e)" in compact
    assert "toast(e.message,true)" in compact


def test_next2_auth_and_p1_deeplink_still_present():
    compact = re.sub(r"\s+", "", JS)

    assert "/api/admin/check" in JS
    assert (
        "state.credentials={username,password}"
        in compact
    )
    assert (
        "headers.Authorization="
        "encodeBasic("
        "state.credentials.username,"
        "state.credentials.password)"
        in compact
    )
    assert "if(r.status===401)" in compact

    dl = block("applyDeepLink")

    assert "URLSearchParams" in dl
    assert "contact360" in dl
    assert "positiveId" in dl
    assert "openContact360" in dl
