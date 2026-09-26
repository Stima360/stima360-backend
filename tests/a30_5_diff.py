"""A30-5 - l'inventario dichiarato della creazione manuale di un appuntamento.

Settima dichiarazione dell'Agenda, accanto a `a30_1_diff`, `a30_2_diff`,
`a30_2p_diff`, `a30_mount_diff` e `a30_4_diff`: le sentinelle di working tree
ne leggono l'UNIONE. Nessuna migration, nessuna tabella: il contratto di
creazione (`contact_id`, `lead_id`, `stima_id`, `property_id`, disponibilita',
alternative, idempotenza) esisteva gia' da A30-2. L'unica aggiunta di backend
e' una rotta di SOLA LETTURA nel router dell'Agenda,
`GET /api/appointments/lookups/stime`, perche' nessuna ricerca di stime
esistente e' insieme limitata all'agenzia, con testo libero e senza recapiti.
"""

FILE_NUOVI = frozenset({
    # le letture CRM del dialog (lead del cliente, ricerca immobili)
    "static/os_shell/assets/agenda/agenda-lookup.js",
    "tests/a30_5_diff.py",
    "tests/test_a30_5_create_ui.py",
    # la rotta di ricerca stime: SQL, service, isolamento su PostgreSQL
    "tests/test_a30_5_stime_lookup.py",
})

FILE_MODIFICATI = frozenset({
    # la ricerca stime: rotta GET, service con i limiti, query di sola lettura
    "appointments/router.py",
    "appointments/service.py",
    "appointments/repository.py",
    # il client della nuova rotta (unico chiamante di /api/appointments)
    "static/os_shell/assets/agenda/agenda-api.js",
    # l'inventario delle rotte Agenda impara la nuova GET
    "tests/test_a30_mount_api.py",
    # la matrice ostile P26-6 la sonda (anonimo -> 401): solo inventario,
    # nessuna logica del certificatore toccata; e il conteggio delle sonde
    # anonime dell'app vera, che ne deriva (16 + 3 -> 17 + 3)
    "scripts/p26_6_live_cert.py",
    "tests/test_p26_6_agenda_realapp_postgres.py",
    # il dialog Nuovo appuntamento: collegamenti CRM, durata visibile,
    # "Verifica disponibilita'", ORARIO NON DISPONIBILE, 404 della creazione
    "static/os_shell/assets/components/agenda/agenda-dialogs.js",
    # dopo la creazione: rilettura, o il giorno dell'appuntamento, e conferma
    "static/os_shell/assets/views/agenda/agenda-page.js",
    # cinque regole in coda alla sezione A30-4 Agenda
    "static/os_shell/assets/app.css",
    # la lookup entra fra i file dell'Agenda; le tre letture CRM ammesse per
    # nome; la terza chiamata a leggiIntervallo ("Verifica disponibilita'")
    "tests/test_a30_4_agenda_ui.py",
    # le sentinelle che imparano questa dichiarazione
    "tests/test_p27_7_network_contracts.py",
    "tests/test_p29_3_journey_foundation.py",
    "tests/test_p29_3c_orchestrator.py",
    "tests/test_p29_3d_crm_journey.py",
    "tests/test_p29_3e_cron_integration.py",
    "tests/test_p29_3g_final_fixes.py",
})
