"""A30-8 - l'inventario dichiarato di "Esito appuntamento / Follow-up".

Decima dichiarazione dell'Agenda, accanto a `a30_1_diff` ... `a30_7_diff`: le
sentinelle di working tree ne leggono l'UNIONE. Nessuna migration, nessuna
tabella, nessun package nuovo, `main.py` intatto.

Una sola modifica fuori dall'Agenda, autorizzata dal gate (opzione C):
`core/repository.py`, dove `create_task_with_cursor` guadagna un autore
esplicito (`created_by_user_id`) SOLO sul percorso R-4 senza `ctx`.
"""

FILE_NUOVI = frozenset({
    "tests/a30_8_diff.py",
    "tests/test_a30_8_outcome_postgres.py",
    "tests/test_a30_8_outcome_static.py",
    "tests/test_a30_8_outcome_ui.py",
})

FILE_MODIFICATI = frozenset({
    # CORE (opzione C): l'autore esplicito sul percorso R-4
    "core/repository.py",
    # l'Agenda: codici, corpi, guardie temporali, esito, follow-up
    "appointments/errors.py",
    "appointments/schemas.py",
    "appointments/service.py",
    "appointments/state_machine.py",
    # l'evento con dati non-colonna (nota, task) e il nome dell'agente
    "appointments/repository.py",
    # la UI: modello, dialog, pannello, messaggio di successo, stile
    "static/os_shell/assets/agenda/agenda-model.js",
    "static/os_shell/assets/components/agenda/agenda-dialogs.js",
    "static/os_shell/assets/components/agenda/agenda-drawer.js",
    "static/os_shell/assets/views/agenda/agenda-page.js",
    "static/os_shell/assets/app.css",
    # D6: i test certificati che aspettavano 409 sui casi TEMPORALI
    "tests/test_a30_2_appointments.py",
    "tests/test_a30_2_appointments_postgres.py",
    "tests/test_a30_2p_backfill_postgres.py",
    "tests/test_a30_2p_facade_postgres.py",
    # le sentinelle che imparano questa dichiarazione
    "tests/test_a30_1_appointments.py",          # service.py -> core.repository
    "tests/test_a30_4_agenda_ui.py",             # romeIso della scadenza del follow-up
    "tests/test_p27_7_network_contracts.py",     # agenda-model.js
    "tests/test_p29_3_journey_foundation.py",
    "tests/test_p29_3c_orchestrator.py",
    "tests/test_p29_3d_crm_journey.py",
    "tests/test_p29_3e_cron_integration.py",
    "tests/test_p29_3g_final_fixes.py",
})
