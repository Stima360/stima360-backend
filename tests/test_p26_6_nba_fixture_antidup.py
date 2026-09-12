"""Regression for the certification fixture, with isolated HTTP/DB boundaries.

Executes the real fixture section and service functions; no PostgreSQL or
live API is exercised. The FOLLOWUP task must keep suppressing duplicates.
"""
import ast
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load_functions(path, names, namespace):
    tree = ast.parse(path.read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    module = ast.Module(body=nodes, type_ignores=[])
    exec(compile(module, str(path), 'exec'), namespace)
    return namespace


def fixture(contact_status=201, lead_status=201):
    source = (ROOT / 'scripts/p26_6_live_cert.py').read_text()
    start = source.index('    from datetime import datetime as _dt, timedelta as _td, timezone as _tz',
                         source.index('def certify_followup_dedicated'))
    stop = source.index('\n    azioni = {}', start)
    calls, blocked, notes = [], [], []
    cert = SimpleNamespace(created_effects={}, marker=lambda label: 'P26-test-' + label)
    dedicated = {label: {'contact': old, 'jar': label} for label, old in [('C', 10), ('D', 20)]}

    def request(method, path, jar, payload):
        calls.append((path, jar, payload))
        fresh = {'C': 110, 'D': 120}[jar]
        if path == '/api/core/contacts':
            return SimpleNamespace(status=contact_status, json=lambda: {'id': fresh})
        assert path == '/api/core/leads'
        # Contact must already be in the perimeter if lead creation fails.
        assert fresh in cert.created_effects.get('contacts', [])
        return SimpleNamespace(status=lead_status, json=lambda: {'id': fresh + 1000})

    env = dict(etichette=('C', 'D'), dedicate=dedicated, cert=cert,
               http=SimpleNamespace(request=request),
               report=SimpleNamespace(blocked=lambda *args: blocked.append(args),
                                      note=lambda *args: notes.append(args)),
               _corpo=lambda response: '')
    exec(compile('def run():\n' + source[start:stop] + '\n', '<fixture>', 'exec'), env)
    env['run']()
    return calls, cert, blocked


def refresh_for_contact(contact):
    now = datetime.now(timezone.utc)
    candidate = dict(subject_type='lead', subject_id=43, lead_id=43,
                     contact_id=contact, stima_id=None,
                     source_signal='next_action_overdue', signal_at=now - timedelta(days=3))
    engine = load_functions(ROOT / 'next_best_action/engine.py', ['select_winner'],
                            dict(Any=object, PRECEDENCE={'next_action_overdue': 2}, _EPOCH=now))
    saved = []

    def store(ctx, winners):
        saved.extend(winners)
        return dict(created=len(winners), updated=0, removed=0)

    ns = dict(Any=object, DEFAULT_LIMIT=100, OPEN_TASK_STATUSES={'open', 'in_progress'},
              defaultdict=defaultdict, datetime=datetime, timezone=timezone,
              select_winner=engine['select_winner'],
              collect_all_signals_scoped=lambda ctx, limit: [candidate],
              database_revival_service=SimpleNamespace(safe_ensure_today_batch_scoped=lambda ctx: None),
              nba_repository=SimpleNamespace(replace_current_actions_scoped=store),
              core_repository=SimpleNamespace(list_tasks=lambda ctx, **kw:
                  [{'status': 'in_progress'}] if kw['contact_id'] == 10 else []))
    load_functions(ROOT / 'next_best_action/service.py', ['refresh', '_has_open_equivalent_task'], ns)
    return ns['refresh'](object()), saved


class NBAFixtureRegression(unittest.TestCase):
    def test_existing_followup_task_still_suppresses_candidate(self):
        result, saved = refresh_for_contact(10)
        self.assertEqual(result['suppressed_duplicates'], 1)
        self.assertEqual(result['total_active'], 0)
        self.assertEqual(saved, [])

    def test_actual_fixture_contact_can_materialize_candidate(self):
        calls, cert, blocked = fixture()
        leads = [payload for path, jar, payload in calls if path == '/api/core/leads']
        self.assertEqual(len(leads), 2)
        self.assertFalse(blocked)
        self.assertEqual(cert.created_effects['contacts'], [110, 120])
        for payload in leads:
            self.assertNotIn(payload['contact_id'], (10, 20))
            result, saved = refresh_for_contact(payload['contact_id'])
            self.assertEqual(result['suppressed_duplicates'], 0)
            self.assertEqual(result['total_active'], 1)
            self.assertEqual(len(saved), 1)

    def test_contact_failure_prevents_lead_creation(self):
        calls, cert, blocked = fixture(contact_status=500)
        self.assertEqual(len(blocked), 2)
        self.assertFalse(any(path == '/api/core/leads' for path, _, _ in calls))

    def test_lead_failure_keeps_contacts_tracked(self):
        calls, cert, blocked = fixture(lead_status=422)
        self.assertEqual(len(blocked), 2)
        self.assertEqual(cert.created_effects['contacts'], [110, 120])
        self.assertFalse(cert.created_effects.get('leads'))


if __name__ == '__main__':
    unittest.main()
