"""DELETE-ARCH Fase 1B - Vende «Inserito per errore»: contratti statici.

Compagno di `test_delete_arch_1b_postgres.py`. Nessuna migration nuova; il
codice canonico e' `created_by_mistake`; le viste normali lo escludono; la UI
mostra l'etichetta e non il codice.
"""
from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_s01_nessuna_migration_nuova():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner
    assert runner.discover_migrations()[-1].version == "084_delete_arch_1a_mistakes"
    assert not list((ROOT / "migrations").glob("085*"))


def test_s02_codice_canonico_e_filtri():
    from crm import sellers
    assert sellers.MISTAKE_REASON == "created_by_mistake"
    assert sellers.ORIGINS == ("created", "reused", "reopened")
    assert list(sellers.STATUS_FILTERS) == ["active", "paused", "closed", "all", "mistakes"]
    assert sellers.STATUS_FILTERS["mistakes"][1] == "l.lost_reason = 'created_by_mistake'"
    assert sellers.NOT_MISTAKE_SQL == "l.lost_reason IS DISTINCT FROM 'created_by_mistake'"
    corpo = inspect.getsource(sellers.list_sellers)
    assert 'if status != "mistakes":' in corpo and "filtri.append(NOT_MISTAKE_SQL)" in corpo
    # «mistake» non scrive piu' testo libero in lost_reason e non passa dal ramo generico
    deact = inspect.getsource(sellers.deactivate)
    assert 'if body.outcome == "mistake":' in deact and "_per_errore(" in deact
    errore = inspect.getsource(sellers._per_errore)
    assert "MISTAKE_REASON" in errore and "ACQUISITION_OPEN" in errore
    assert "DELETE FROM leads" not in errore and "DELETE FROM activities" not in errore
    assert "DELETE FROM lead_stime" not in errore and "DELETE FROM tasks" not in errore
    # l'unica riga che si toglie e' la relazione seller di QUELL'immobile e QUEL lead
    assert errore.count("DELETE FROM") == 1
    assert "DELETE FROM property_leads WHERE property_id = %s AND lead_id = %s AND relation_type = 'seller'" in errore


def test_s03_router_filtro_e_platform_fuori_acting():
    testo = (ROOT / "crm" / "router.py").read_text(encoding="utf-8")
    assert 'pattern="^(active|paused|closed|all|mistakes)$"' in testo
    assert "except PlatformAdminAgencyRequired" in testo


def test_s04_ui_etichetta_non_codice():
    js = ROOT / "static" / "os_shell" / "assets" / "views" / "venditori.js"
    testo = js.read_text(encoding="utf-8")
    assert "renderBadge(it.lost_reason_label || it.lost_reason || 'Chiusa', 'gray')" in testo
    assert subprocess.run(["node", "--check", str(js)], capture_output=True).returncode == 0
