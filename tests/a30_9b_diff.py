"""A30-9B - l'inventario dichiarato di questa fase (OAuth + hook Agenda +
worker reale verso Google Calendar), dodicesima dichiarazione dell'Agenda
accanto a `a30_1_diff` ... `a30_9a_diff`: le sentinelle di working tree ne
leggono l'UNIONE, come per ogni fase precedente.

Cinque file NUOVI nel package (`config.py`, `oauth.py`, `google_provider.py`,
`router.py`, `integration.py`), un runner NUOVO (`run_calendar_sync_cron.py`,
dichiarato anche in `RUNNER_A30_9B` per le sentinelle che lo confrontano
singolarmente), due file NUOVI della UI (il client `calendar-sync-api.js` e
il pannello `agenda-calendar-sync-panel.js`), e le tre righe che questa fase
aggiunge a `main.py` (solo il mount, §37).

Modificati: i tre punti di coupling dichiarati (`appointments/service.py`,
`appointments/lmc15_facade.py`, `main.py`), `calendar_sync/__init__.py` e
`calendar_sync/repository.py` (nuove funzioni A30-9B, nessuna esistente
cambiata di comportamento), `requirements.txt` (le due dipendenze OAuth),
`static/os_shell/assets/app.css` e `.../agenda-page.js` (il pannello), e le
sentinelle aggiornate "in un modo dichiarato" (§44) altrove in questo file.
"""
from __future__ import annotations

FILE_NUOVI = frozenset({
    "calendar_sync/config.py",
    "calendar_sync/oauth.py",
    "calendar_sync/google_provider.py",
    "calendar_sync/router.py",
    "calendar_sync/integration.py",
    "run_calendar_sync_cron.py",
    "static/os_shell/assets/agenda/calendar-sync-api.js",
    "static/os_shell/assets/components/agenda/agenda-calendar-sync-panel.js",
    "tests/a30_9b_diff.py",
    "tests/test_a30_9b_calendar_google_static.py",
    "tests/test_a30_9b_calendar_google_postgres.py",
    "tests/test_a30_9b_calendar_google_ui.py",
})

FILE_MODIFICATI = frozenset({
    # i tre punti di coupling dichiarati (§20, §44)
    "appointments/service.py",
    "appointments/lmc15_facade.py",
    "main.py",
    # il package A30-9A, esteso (nessuna funzione esistente cambia contratto)
    "calendar_sync/__init__.py",
    "calendar_sync/repository.py",
    # le due dipendenze OAuth (google-auth, google-auth-oauthlib)
    "requirements.txt",
    # la UI: il pannello nell'Agenda
    "static/os_shell/assets/app.css",
    "static/os_shell/assets/views/agenda/agenda-page.js",
    # le sentinelle aggiornate in un modo dichiarato (§44): il confine
    # calendar_sync<->appointments/main si allarga di tre punti esatti, i
    # runner dichiarati imparano il secondo, e "main.py"/i domini vietati
    # imparano il mount di questa fase.
    "tests/test_a30_9a_calendar_sync_static.py",
    "tests/test_a30_1_appointments.py",
    "tests/test_lmc15_acquisition_bridge.py",
    "tests/test_lmc13_home_metrics.py",
    "tests/test_a30_4_agenda_ui.py",
    "tests/test_a30_5_create_ui.py",
    "tests/lmc15_main_diff.py",
    "tests/p29_3b_diff.py",
    "tests/test_p29_3_journey_foundation.py",
    "tests/test_p29_3c_orchestrator.py",
    "tests/test_p29_3d_crm_journey.py",
    "tests/test_p29_3e_cron_integration.py",
    "tests/test_p26_5_basic_containment.py",
    "tests/test_p26_6_live_cert_script.py",
    "tests/test_p27_7_network_contracts.py",
})

#: L'UNICO runner nuovo che questa fase introduce (stesso principio di
#: `p29_3e_diff.RUNNER_TOCCATO`, qui per un runner NUOVO invece che toccato):
#: nominato una volta, importato dalle sentinelle di fase precedenti che
#: altrimenti pretenderebbero che nessun runner sia mai nato.
RUNNER_A30_9B = "run_calendar_sync_cron.py"

RIGHE_MAIN = frozenset({
    "from calendar_sync.router import router as calendar_sync_router",
    "# A30-9B: le rotte Google Calendar (`/api/calendar/google`). Solo il mount",
    "# (§37): nessuna logica OAuth/Google in questo file. Ogni rotta - tranne il",
    "# callback, che resta comunque legata alla sessione (§7, §30) - prende lo",
    "# scope da `require_operator`, come l'Agenda.",
    "app.include_router(calendar_sync_router, dependencies=[Depends(require_authenticated_operator)])",
})
