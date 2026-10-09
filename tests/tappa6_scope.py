"""Perimetro esplicito della Tappa 6, distinto dai manifest delle vecchie fasi."""
from pathlib import Path
import subprocess

ALLOWED = frozenset({
    'communication/contact_view.py', 'communication/dispatcher.py',
    'communication/enums.py', 'communication/journey_catalog.py',
    'communication/journey_enums.py', 'communication/journey_repository.py',
    'communication/journey_service.py', 'communication/journey_tick.py',
    'communication/repository.py', 'communication/templates.py',
    'site_import/service.py',
    # Tappa 7: correzioni riprodotte su dati sito e consenso; perimetro esplicito.
    'property/site_catalog.py',
    'tests/test_tappa7_site_data.py', 'tests/test_tappa7_public_postgres.py',
    'tests/test_tappa7_sale_postgres.py',
    'tests/test_stime_consenso_marketing_migration.py',
    # Tappa 7B: fixture temporali e schema orari allineati alle migration reali.
    'tests/test_a30_1_appointments_postgres.py',
    'tests/test_a30_2_appointments_postgres.py',
    'tests/test_p27_6_postgres_real.py', 'tests/test_p28_acting_postgres_real.py',
    'tests/test_p26_6c_flow_immutability_pg.py',
    'tests/test_private_stima_pdf_postgres.py',
    'tests/test_crm_prod_bootstrap_postgres.py',
    'tests/test_lmc1b_owner_login_link_postgres.py',
    'tests/test_lmc3_valuation_snapshot_postgres.py',
    'reports/TAPPA7_REVIEW.md', 'reports/TAPPA7_FIXES.patch',
    'reports/TAPPA7_CASES.csv',
    'reports/TAPPA7B_REVIEW.md',
    'reports/TAPPA7B_FIXES.patch',
    'migrations/097_tappa6_request_followup.sql',
    'migrations/097_tappa6_request_followup_down.sql',
    'tests/test_p29_3_journey_foundation.py', 'tests/test_p29_3c_orchestrator.py',
    'tests/test_p29_3d_crm_journey.py', 'tests/test_p29_3g_final_fixes.py',
    'tests/test_site_import_1_postgres.py', 'tests/test_tappa6_postgres.py',
    'tests/test_tappa6_contracts.py', 'tests/tappa6_scope.py',
    'reports/TAPPA6_ANALISI_PRE_TRIGGER.md', 'reports/TAPPA6_REVIEW.md',
    'reports/TAPPA6_DIFF.patch',
})
PROTECTED = (
    'main.py', 'run_calendar_sync_cron.py', 'run_site_import_cron.py',
    'migrations/064_p29_communication_foundation.sql',
    'migrations/071_p29_3_journey_automation.sql',
    'migrations/096_site_import_baseline.sql', 'site_import/source.py',
)


def assert_changed_paths(paths):
    unknown = set(paths) - ALLOWED
    assert not unknown, f'Changes outside Tappa 6 scope: {sorted(unknown)}'


def assert_tappa6_scope(root):
    root = Path(root)
    # Include untracked files individually; never hide whole directories.
    changes = subprocess.check_output(
        ['git', 'status', '--porcelain', '--untracked-files=all'], cwd=root, text=True)
    paths = {line[3:] for line in changes.splitlines()}
    # main.py is checked byte-for-byte below, allowing only the consent fix.
    assert_changed_paths(paths - {'main.py'})
    for path in PROTECTED:
        original = subprocess.check_output(['git', 'show', f'HEAD:{path}'], cwd=root)
        if path == 'main.py':
            # Unica modifica autorizzata qui: boolean marketing rigoroso.
            original = original.replace(
                b'consenso_marketing = bool(raw.get("consenso_marketing", False))',
                b'consenso_marketing = raw.get("consenso_marketing") is True')
        assert (root / path).read_bytes() == original, path
