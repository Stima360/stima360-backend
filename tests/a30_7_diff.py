"""A30-7 - l'inventario dichiarato di "Pianifica / Fissa sopralluogo" dall'Agenda.

Nona dichiarazione dell'Agenda, accanto a `a30_1_diff` ... `a30_6_diff`: le
sentinelle di working tree ne leggono l'UNIONE. Nessuna migration, nessuna
tabella, nessun file del sito: la sincronizzazione esplicita delle richieste
dal sito (router nel package `appointments_legacy`, montato in `main.py`), le
due guardie di `schedule` (orario passato, un solo sopralluogo aperto per
stima, sotto il lock della stima) e la UI della pianificazione.
"""

#: Le righe che A30-7 e' autorizzata ad AGGIUNGERE a `main.py`, per intero:
#: l'import del router della sincronizzazione, due righe di commento e il
#: mount con `require_authenticated_operator`, come l'Agenda.
RIGHE_MAIN = frozenset({
    "from appointments_legacy.router import router as appointments_legacy_router",
    "# A30-7: la sincronizzazione esplicita delle richieste di sopralluogo dal sito",
    "# (`/api/appointments/legacy-requests/sync`). Solo il mount, come l'Agenda.",
    "app.include_router(appointments_legacy_router, "
    "dependencies=[Depends(require_authenticated_operator)])",
})

FILE_NUOVI = frozenset({
    # la rotta POST /api/appointments/legacy-requests/sync
    "appointments_legacy/router.py",
    "tests/a30_7_diff.py",
    "tests/test_a30_7_legacy_sync_static.py",
    "tests/test_a30_7_legacy_schedule_postgres.py",
    "tests/test_a30_7_schedule_ui.py",
})

FILE_MODIFICATI = frozenset({
    # filtro per agenzia riusabile dell'import A30-6 (la CLI resta globale)
    "appointments_legacy/stime_dettagliate_import.py",
    # SOLO il mount del router della sincronizzazione
    "main.py",
    # le guardie di schedule: codici, lock della stima, ricerca, service
    "appointments/errors.py",
    "appointments/repository.py",
    "appointments/service.py",
    # la UI Agenda: client, dialog Pianifica, pannello, pagina, stile
    "static/os_shell/assets/agenda/agenda-api.js",
    "static/os_shell/assets/components/agenda/agenda-dialogs.js",
    "static/os_shell/assets/components/agenda/agenda-drawer.js",
    "static/os_shell/assets/views/agenda/agenda-page.js",
    "static/os_shell/assets/app.css",
    # la matrice ostile P26-6: la nuova rotta nell'inventario anonimo
    "scripts/p26_6_live_cert.py",
    "tests/test_p26_6_live_cert_script.py",
    "tests/test_p26_6_agenda_realapp_postgres.py",
    # il mount e le sentinelle che lo leggono
    "tests/test_a30_mount_api.py",
    "tests/test_a30_6_legacy_import.py",
    # A30 UI: i punti di romeIso/leggiIntervallo del nuovo dialog, e il valore
    # `legacy_stime_dettagliate` come fonte ammessa
    "tests/test_a30_4_agenda_ui.py",
    # D4: tre pianificazioni di prova spostate DOPO l'orologio del service
    "tests/test_a30_2_appointments_postgres.py",
    "tests/test_a30_2p_backfill_postgres.py",
    # le sentinelle che imparano questa dichiarazione (p29_3b_diff: le righe
    # di `main.py` ammesse si allargano delle sole RIGHE_MAIN qui sopra)
    "tests/p29_3b_diff.py",
    "tests/lmc15_main_diff.py",
    "tests/test_p27_7_network_contracts.py",
    "tests/test_p29_3_journey_foundation.py",
    "tests/test_p29_3c_orchestrator.py",
    "tests/test_p29_3d_crm_journey.py",
    "tests/test_p29_3e_cron_integration.py",
    "tests/test_p29_3g_final_fixes.py",
})
