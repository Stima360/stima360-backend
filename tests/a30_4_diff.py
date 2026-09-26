"""A30-4 - l'inventario dichiarato della UI Agenda.

Sesta dichiarazione dell'Agenda, accanto a `a30_1_diff`, `a30_2_diff`,
`a30_2p_diff` e `a30_mount_diff`: le sentinelle di working tree ne leggono
l'UNIONE. Nessun file di backend: A30-4 e' solo OS Shell.
"""

FILE_NUOVI = frozenset({
    "static/os_shell/assets/agenda/agenda-model.js",
    "static/os_shell/assets/agenda/agenda-api.js",
    "static/os_shell/assets/components/agenda/agenda-views.js",
    "static/os_shell/assets/components/agenda/agenda-drawer.js",
    "static/os_shell/assets/components/agenda/agenda-dialogs.js",
    "static/os_shell/assets/views/agenda/agenda-page.js",
    # le tre cartelle nuove, come le mostra `git status` finche' non sono
    # tracciate
    "static/os_shell/assets/agenda/",
    "static/os_shell/assets/components/agenda/",
    "static/os_shell/assets/views/agenda/",
    "tests/a30_4_diff.py",
    "tests/test_a30_4_agenda_ui.py",
})

FILE_MODIFICATI = frozenset({
    # import della pagina, registerRoute('agenda') e - dal gate finale - la
    # voce "Agenda" in SECTIONS (SEZIONE_AGENDA rimossa)
    "static/os_shell/assets/main.js",
    # solo la sezione "A30-4 Agenda" aggiunta in coda
    "static/os_shell/assets/app.css",
    # le sentinelle che vedevano l'OS Shell senza Agenda: ora ammettono le tre
    # cartelle dell'Agenda (P27-7) e un solo client di /api/appointments
    "tests/test_p27_7_network_contracts.py",
    "tests/test_a30_mount_api.py",
    # gate finale: l'ordine della sidebar anonima ora contiene "agenda"
    "tests/test_p27_7_network_runtime.py",
    # le sentinelle di working tree che imparano questa dichiarazione
    "tests/test_p29_3_journey_foundation.py",
    "tests/test_p29_3c_orchestrator.py",
    "tests/test_p29_3d_crm_journey.py",
    "tests/test_p29_3e_cron_integration.py",
    "tests/test_p29_3g_final_fixes.py",
})
