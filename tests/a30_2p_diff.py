"""A30-2P (pre-backfill) - l'inventario dichiarato dei file della fase.

Si aggiunge a `a30_1_diff.py` e `a30_2_diff.py`: le sentinelle di working tree
leggono l'unione delle tre dichiarazioni.
"""

FILE_NUOVI = frozenset({
    "appointments/backfill.py",
    "scripts/a30_2p_backfill.py",
    "tests/a30_2p_diff.py",
    "tests/test_a30_2p_backfill.py",
    "tests/test_a30_2p_backfill_postgres.py",
    "docs/A30_2P_PROJECTION_BACKFILL.md",
    # A30-2P 073: la migration della facade LMC-15 (solo CHECK) e le sue prove
    "migrations/073_a30_2p_lmc15_facade.sql",
    "migrations/073_a30_2p_lmc15_facade_down.sql",
    "tests/test_a30_2p_073.py",
    "tests/test_a30_2p_073_postgres.py",
    # A30-2P FACADE: il modulo della facade LMC-15 e le sue prove di contratto
    "appointments/lmc15_facade.py",
    "tests/test_a30_2p_facade_postgres.py",
    "tests/test_a30_2p_facade.py",   # sentinella: nessun runtime chiama le scritture legacy
    # pre-live gate: lo script di certificazione LIVE (una transazione, sempre
    # ROLLBACK) e le sue prove su PostgreSQL usa-e-getta
    "scripts/a30_2p_facade_live_cert.py",
    "tests/test_a30_2p_facade_live_cert_postgres.py",
})

#: File dell'Agenda (non ancora tracciati) che A30-2P estende. Gia' nuovi per
#: git: nominati qui perche' il report li dichiari.
FILE_AGENDA_ESTESI = frozenset({
    "appointments/projection.py",    # on_schedule riusa una riga LMC-15 gia' collegata
    "appointments/repository.py",    # _INSERT_COLUMNS: colonne del backfill
    "appointments/service.py",       # docstring: proiezione accesa
    "docs/A30_2_SERVICE_API.md",     # proiezione accesa
    # sentinelle che vedevano la proiezione spenta: ora la spengono solo
    # dentro il test (arresto d'emergenza)
    "tests/test_a30_1_appointments_postgres.py",
    "tests/test_a30_2_appointments.py",
    "tests/test_a30_2_appointments_postgres.py",
    # 073: la nuova fonte nel catalogo, e la sentinella che lo confronta
    "appointments/enums.py",
    "tests/test_a30_1_appointments.py",
    # FACADE: le sentinelle che vedevano la facade assente, e i guard nativi
    # (confirm/reschedule/no_show senza agente -> AGENT_REQUIRED, 073)
    "tests/test_a30_2p_backfill.py",
    "tests/test_a30_2p_073.py",
})

#: File gia' tracciati che A30-2P modifica: le cinque sentinelle di working
#: tree, che imparano questa dichiarazione.
FILE_MODIFICATI = frozenset({
    # FACADE: LMC-15 delega i quattro sopralluoghi all'Agenda (service), una
    # variante `*_in` in piu' (repository), e la suite LMC-15 che ora porta
    # nel database di prova anche la 072 e la 073 (fixture, non contratto)
    "acquisition/service.py",
    "acquisition/repository.py",
    "tests/test_lmc15_acquisition_bridge_postgres.py",
    # 073: le sentinelle di serie che nominano la nuova coda (073), nello
    # stesso stile con cui A30-1 aveva nominato la 072
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
    "tests/test_p29_3_journey_foundation.py",
    "tests/test_p29_3c_orchestrator.py",
    "tests/test_p29_3d_crm_journey.py",
    "tests/test_p29_3e_cron_integration.py",
    "tests/test_p29_3g_final_fixes.py",
})
