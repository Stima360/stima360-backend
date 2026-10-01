"""A32-2 - APPOINTMENT REMINDER PLANNER + DISPATCH INTEGRATION: l'inventario.

Popolato da `git status --porcelain` reale (stessa disciplina di
`tests/a32_1_diff.py`): nessun file elencato qui "per farlo passare", solo
cio' che A32-2 ha davvero toccato o creato.

NUOVI: nel package `appointment_reminders/` il repository (sole letture,
agency-scoped), il planner (enqueue nel ledger esistente), la revalida finale
e la rotta del giro; questo file e le suite dedicate.

MODIFICATI: `operator_auth/context.py` (l'origin `appointment_reminder`),
`communication/dispatcher.py` (il ramo stretto della revalida, SOLO per
`reason_code='appointment_reminder'`), `run_communication_dispatch_cron.py`
(journeys -> reminders -> dispatch) e `main.py` (il mount).
"""
from __future__ import annotations

FILE_NUOVI = frozenset({
    "appointment_reminders/planner.py",
    "appointment_reminders/repository.py",
    "appointment_reminders/revalidation.py",
    "appointment_reminders/router.py",
    "tests/a32_2_diff.py",
    "tests/test_a32_2_reminder_cron.py",
    "tests/test_a32_2_reminder_dispatch.py",
    "tests/test_a32_2_reminder_planner.py",
    "tests/test_a32_2_reminder_postgres.py",
    "tests/test_a32_2_reminder_static.py",
})

FILE_MODIFICATI = frozenset({
    "communication/dispatcher.py",
    "main.py",
    "operator_auth/context.py",
    "run_communication_dispatch_cron.py",
    # le sentinelle AUTORIZZATE dal gate (collisioni dichiarate)
    "tests/lmc15_main_diff.py",
    "tests/p29_3b_diff.py",
    "tests/test_a32_1_reminders_static.py",
    "tests/test_lmc1b_owner_login_link.py",
    "tests/test_p26_1_operator_auth.py",
    "tests/test_p29_2_4_dispatch_sentinels.py",
    "tests/test_p29_3_journey_foundation.py",
    "tests/test_p29_3e_cron_integration.py",
    "tests/test_p29_3g_final_fixes.py",
})

#: Le righe che A32-2 aggiunge a `main.py`, per intero.
RIGHE_MAIN = frozenset({
    "from appointment_reminders.router import router as appointment_reminders_router",
    "# A32-2: il giro dei promemoria degli appuntamenti (`/api/communication/reminders/tick`).",
    "# Solo il mount, come il dispatch: la soglia la prende la rotta da "
    "`require_dispatch_context`.",
    "app.include_router(appointment_reminders_router, "
    "dependencies=[Depends(require_authenticated_operator)])",
})
