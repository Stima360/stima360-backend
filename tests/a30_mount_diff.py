"""A30 MOUNT - l'inventario dichiarato del mount dell'API Agenda.

Quarta dichiarazione dell'Agenda, accanto a `a30_1_diff`, `a30_2_diff` e
`a30_2p_diff`: le sentinelle di working tree ne leggono l'UNIONE.

`main.py` resta sorvegliato riga per riga (`lmc15_main_diff`, `p29_3b_diff`):
il mount vi aggiunge SOLO le righe di `RIGHE_MAIN` - l'import del router,
due righe di commento e il mount con `require_authenticated_operator`, come
LMC-15 - e non ne toglie nessuna.
"""

#: Le righe che il mount A30 e' autorizzato ad AGGIUNGERE a `main.py`, per
#: intero (mai come frammenti).
RIGHE_MAIN = frozenset({
    "from appointments.router import router as appointments_router",
    "# A30: l'API Agenda (`/api/appointments`). Solo il mount, come LMC-15: lo",
    "# scope lo prende ogni rotta da `require_operator`. Nessuna UI in questo step.",
    "app.include_router(appointments_router, "
    "dependencies=[Depends(require_authenticated_operator)])",
})

FILE_NUOVI = frozenset({
    "tests/a30_mount_diff.py",
    "tests/test_a30_mount_api.py",     # import, OpenAPI, 401, collisioni, sentinella main.py
    # P26-6: la sezione APPOINTMENTS (solo rifiuti) contro l'APP VERA, su
    # PostgreSQL usa-e-getta
    "tests/test_p26_6_agenda_realapp_postgres.py",
})

FILE_MODIFICATI = frozenset({
    "main.py",
    # le sentinelle A30 che vedevano l'Agenda NON montata: ora ammettono
    # SOLO import + mount
    "tests/test_a30_1_appointments.py",
    "tests/test_a30_2_appointments.py",
    "tests/test_a30_2p_073.py",
    "tests/test_a30_2p_backfill.py",
    # `main.py` riga per riga: le righe del mount ammesse per nome
    "tests/lmc15_main_diff.py",
    "tests/p29_3b_diff.py",
    # le sentinelle di working tree che imparano questa dichiarazione
    "tests/test_lmc15_acquisition_bridge.py",
    "tests/test_p29_3_journey_foundation.py",
    "tests/test_p29_3c_orchestrator.py",
    "tests/test_p29_3d_crm_journey.py",
    "tests/test_p29_3e_cron_integration.py",
    "tests/test_p29_3g_final_fixes.py",
    # P26-5: `/api/appointments` e' un prefisso di tenant (cookie e SOLO cookie)
    "tests/test_p26_5_basic_containment.py",
    # P26-6: la matrice ostile copre l'Agenda (HOSTILE / REJECTION ONLY) e il
    # prover offline ne esercita la sezione e i difetti
    "scripts/p26_6_live_cert.py",
    "tests/test_p26_6_live_cert_script.py",
    # le diciture "NON MONTATO" diventate false (nessuna modifica funzionale)
    "appointments/router.py",
    "docs/A30_2_SERVICE_API.md",
})
