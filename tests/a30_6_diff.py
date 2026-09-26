"""A30-6 - l'inventario dichiarato dell'import legacy dei sopralluoghi.

Ottava dichiarazione dell'Agenda, accanto a `a30_1_diff`, `a30_2_diff`,
`a30_2p_diff`, `a30_mount_diff`, `a30_4_diff` e `a30_5_diff`: le sentinelle
di working tree ne leggono l'UNIONE. Nessuna migration, nessuna rotta,
nessuna UI: un package NUOVO fuori dal dominio `appointments/` (D5), lo
script idempotente `--census/--dry-run/--apply` e i suoi test. Finche' il
package e' solo in working tree git lo mostra come cartella non tracciata
(`appointments_legacy/`), dopo lo stage come file: sono dichiarati entrambi.
"""

FILE_NUOVI = frozenset({
    # l'adattatore di import (package separato, D5)
    "appointments_legacy/",
    "appointments_legacy/__init__.py",
    "appointments_legacy/stime_dettagliate_import.py",
    # lo script: census, dry-run, apply (+ rollback selettivo D4)
    "scripts/a30_6_legacy_import.py",
    "tests/a30_6_diff.py",
    "tests/test_a30_6_legacy_import.py",
    "tests/test_a30_6_legacy_import_postgres.py",
})

FILE_MODIFICATI = frozenset({
    # le sentinelle che imparano questa dichiarazione
    "tests/test_p29_3_journey_foundation.py",
    "tests/test_p29_3c_orchestrator.py",
    "tests/test_p29_3d_crm_journey.py",
    "tests/test_p29_3e_cron_integration.py",
    "tests/test_p29_3g_final_fixes.py",
})
