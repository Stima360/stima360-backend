"""Contratti temporali/editoriali v2; nessun provider reale."""
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import subprocess
import pytest
from communication import templates, unsubscribe, send_window
from communication.journey_catalog import FINESTRA_LAVORATIVA

@pytest.mark.parametrize('instant,expected',[
 ('2026-10-10T06:59:00+00:00','2026-10-10T07:00:00+00:00'),
 ('2026-10-10T07:00:00+00:00','2026-10-10T07:00:00+00:00'),
 ('2026-10-10T16:59:59+00:00','2026-10-10T16:59:59+00:00'),
 ('2026-10-10T17:00:00+00:00','2026-10-12T07:00:00+00:00'),
 ('2026-10-11T12:00:00+00:00','2026-10-12T07:00:00+00:00'),
 ('2026-03-28T18:00:00+00:00','2026-03-30T07:00:00+00:00'),
 ('2026-10-24T17:00:00+00:00','2026-10-26T08:00:00+00:00'),
])
def test_t4_window_dst(instant,expected):
    assert send_window.next_allowed(datetime.fromisoformat(instant),FINESTRA_LAVORATIVA,'Europe/Rome')==datetime.fromisoformat(expected)

@pytest.mark.parametrize('n',range(1,11))
def test_t16_ten_drafts_real_signed_unsubscribe(n,monkeypatch):
    monkeypatch.setenv(unsubscribe.SECRET_ENV,'synthetic-signing-key-for-local-tests-only')
    url=unsubscribe.unsubscribe_url('https://test.stima360.it',1,123)
    subject,body=templates.render(f'stima_lead_m{n}',2,dict(contact_first_name='Anna',agency_name='STIMA360 Alba',unsubscribe_url=url))
    assert 'Anna' in body and 'STIMA360 Alba' in body and url in body and '{{' not in body
    assert unsubscribe.verify(parse_qs(urlparse(url).query)['t'][0])[:2]==(1,123)
    assert subject
    with pytest.raises(Exception):templates.render(f'stima_lead_m{n}',2,dict(contact_first_name='Anna',agency_name='STIMA360 Alba'))


def test_t9_unsigned_links_fail_closed(monkeypatch):
    monkeypatch.delenv(unsubscribe.SECRET_ENV,raising=False)
    with pytest.raises(unsubscribe.UnsubscribeNotConfigured):unsubscribe.issue(1,123)
    assert unsubscribe.verify('invented') is None


def test_t15_closed_entrypoints_unchanged():
    root=Path(__file__).resolve().parents[1]
    paths=['main.py','run_site_import_cron.py','run_calendar_sync_cron.py','render.yaml']
    for path in paths:
        if not (root/path).exists():continue
        before=subprocess.check_output(['git','show',f'HEAD:{path}'],cwd=root)
        if path == 'main.py':
            # Tappa 7: l'unica eccezione e' il consenso booleano esplicito.
            before=before.replace(
                b'consenso_marketing = bool(raw.get("consenso_marketing", False))',
                b'consenso_marketing = raw.get("consenso_marketing") is True')
        assert (root/path).read_bytes()==before,path


def test_rev2_scope_still_rejects_closed_and_unknown_files():
    from tests.tappa6_scope import assert_changed_paths
    for path in ['main.py','run_calendar_sync_cron.py','migrations/096_site_import_baseline.sql','unknown_module.py']:
        with pytest.raises(AssertionError):assert_changed_paths([path])
    assert_changed_paths(['communication/journey_tick.py'])
