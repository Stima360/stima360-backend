"""A30-9A - l'inventario dichiarato delle FONDAMENTA della sincronizzazione
calendario (Google Calendar outbound).

Undicesima dichiarazione dell'Agenda, accanto a `a30_1_diff` ... `a30_8_diff`:
le sentinelle di working tree ne leggono l'UNIONE. Un package NUOVO
(`calendar_sync/`), la migration 074 con la sua down, una dipendenza
(`cryptography`). NON toccati: `appointments/`, `main.py`, la UI, i router.
"""

FILE_NUOVI = frozenset({
    # la cartella NUOVA: finche' non e' tracciata git la mostra come `?? calendar_sync/`
    "calendar_sync/",
    "migrations/074_a30_9a_calendar_sync.sql",
    "migrations/074_a30_9a_calendar_sync_down.sql",
    "calendar_sync/__init__.py",
    "calendar_sync/constants.py",
    "calendar_sync/crypto.py",
    "calendar_sync/provider.py",
    "calendar_sync/fake_provider.py",
    "calendar_sync/repository.py",
    "calendar_sync/service.py",
    "tests/a30_9a_diff.py",
    "tests/test_a30_9a_calendar_sync_static.py",
    "tests/test_a30_9a_calendar_sync_postgres.py",
})

FILE_MODIFICATI = frozenset({
    # la sola dipendenza nuova, realmente usata (crypto.py)
    "requirements.txt",
    # le sentinelle che nominano la 074 in coda alla serie delle migration
    "tests/test_a30_1_appointments.py",
    "tests/test_a30_2p_073.py",
    "tests/test_lmc11_valuation_cron.py",
    "tests/test_lmc13_home_metrics.py",
    "tests/test_lmc15_acquisition_bridge.py",
    "tests/test_lmc1b_owner_login_link.py",
    "tests/test_lmc2_owner_homes.py",
    "tests/test_lmc3_valuation_snapshot.py",
    "tests/test_lmc7_owner_radar.py",
    "tests/test_lmc8_crm_radar.py",
    "tests/test_lmc9_consultation_request.py",
    "tests/test_p27_6_lead_routing.py",
    "tests/test_p29_1_consent_migrations.py",
    "tests/test_p29_2_1_communication_foundation.py",
    "tests/test_p29_2_2_communication_service.py",
    "tests/test_p29_2_3_claim_sentinels.py",
    "tests/test_p29_2_4_dispatch_sentinels.py",
    "tests/test_p29_2_5e_email_adapter.py",
    # `cryptography` ammessa per nome (resta vietata a operator_auth)
    "tests/test_p26_1_operator_auth.py",
    # le FK non-CASCADE della 074 verso `agencies`, esaminate
    "tests/test_p26_6_live_cert_script.py",
    # le sentinelle di working tree che imparano questa dichiarazione
    "tests/test_p29_3_journey_foundation.py",
    "tests/test_p29_3c_orchestrator.py",
    "tests/test_p29_3d_crm_journey.py",
    "tests/test_p29_3e_cron_integration.py",
    "tests/test_p29_3g_final_fixes.py",
})
