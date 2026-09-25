"""A30-1 - l'inventario dichiarato dei file della fase.

Le sentinelle di working tree delle fasi precedenti (LMC-15, P29-3B) leggono
questo elenco invece di smettere di guardare: un file che non compare qui, e
non e' dichiarato da nessun'altra fase, le fa ancora fallire.
"""

FILE_NUOVI = frozenset({
    "migrations/072_a30_1_appointments.sql",
    "migrations/072_a30_1_appointments_down.sql",
    # Finche' il pacchetto non e' tracciato, `git status --porcelain` lo
    # mostra come directory: e' lo stesso insieme di file, visto da git.
    "appointments/",
    "appointments/__init__.py",
    "appointments/enums.py",
    "appointments/schemas.py",
    "appointments/repository.py",
    "appointments/service.py",
    "tests/a30_1_diff.py",
    "tests/test_a30_1_appointments.py",
    "tests/test_a30_1_appointments_postgres.py",
    "docs/A30_1_APPOINTMENTS_MODEL.md",
    "scripts/a30_test_cleanup.py",
})

#: File gia' tracciati che A30-1 modifica, tutti di test o di inventario
#: (collisione dichiarata nel report): le sentinelle che dovevano imparare
#: l'esistenza della 072, l'inventario FK del cleanup P26-6 (test 86i) e il
#: registro dei punti di connessione P26 (test h11 + documento).
FILE_MODIFICATI = frozenset({
    "tests/test_lmc15_acquisition_bridge.py",
    "tests/test_p29_3_journey_foundation.py",
    "tests/test_lmc13_home_metrics.py",
    "tests/test_lmc11_valuation_cron.py",
    "tests/test_lmc7_owner_radar.py",
    "tests/test_lmc3_valuation_snapshot.py",
    "tests/test_p29_3c_orchestrator.py",
    "tests/test_p29_3d_crm_journey.py",
    "tests/test_p29_3e_cron_integration.py",
    "tests/test_p29_3g_final_fixes.py",
    "tests/test_lmc1b_owner_login_link.py",
    "tests/test_lmc2_owner_homes.py",
    "tests/test_lmc8_crm_radar.py",
    "tests/test_lmc9_consultation_request.py",
    "tests/test_p27_6_lead_routing.py",
    "tests/test_p29_1_consent_migrations.py",
    "tests/test_p29_2_1_communication_foundation.py",
    "tests/test_p29_2_2_communication_service.py",
    "tests/test_p29_2_3_claim_sentinels.py",
    "tests/test_p29_2_4_dispatch_sentinels.py",
    "tests/test_p29_2_5e_email_adapter.py",
    "tests/test_p26_6_live_cert_script.py",
    "tests/test_p26_db_entrypoints.py",
    "docs/P26_DB_ENTRYPOINTS.md",
})
