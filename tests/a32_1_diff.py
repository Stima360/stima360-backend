"""A32-1 - APPOINTMENT REMINDERS FOUNDATION: l'inventario dichiarato della fase.

Popolato da `git status --porcelain` reale (stessa disciplina di
`tests/a30_12_diff.py`): nessun file elencato qui "per farlo passare", solo
cio' che A32-1 ha davvero toccato o creato.

NUOVI: la migration 079 (SOLO il CHECK di `reason_code` + `appointment_reminder`),
il package PURO `appointment_reminders/` (policy + template, nessun DB, nessun
router, nessun dominio COMMUNICATION), questo file e le quattro suite dedicate.

MODIFICATI: `communication/enums.py` (`REASON_APPOINTMENT_REMINDER` in
`REASON_CODES`, specchio del CHECK della 079) e `communication/contact_view.py`
(l'etichetta del motivo nello storico contatto), piu' le sentinelle AUTORIZZATE
dal gate: numerazione migration (la 079 ultima ESATTA) e reason code.
"""
from __future__ import annotations

FILE_NUOVI = frozenset({
    "appointment_reminders/",
    "appointment_reminders/__init__.py",
    "appointment_reminders/policy.py",
    "appointment_reminders/template.py",
    "migrations/079_a32_1_appointment_reminders.sql",
    "migrations/079_a32_1_appointment_reminders_down.sql",
    "tests/a32_1_diff.py",
    "tests/test_a32_1_reminders_policy.py",
    "tests/test_a32_1_reminders_postgres.py",
    "tests/test_a32_1_reminders_static.py",
    "tests/test_a32_1_reminders_template.py",
})

FILE_MODIFICATI = frozenset({
    "communication/contact_view.py",
    "communication/enums.py",
    "tests/p29_3b_diff.py",
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
    "tests/test_p29_3_journey_foundation.py",
})
