"""A30-12 - PUBLIC BOOKING LINK: sedicesima dichiarazione dell'Agenda,
accanto a `a30_1_diff`, `a30_2_diff`, `a30_2p_diff`, `a30_mount_diff`,
`a30_7_diff`, `a30_9a_diff`, `a30_9b_diff`, `a30_10_diff`: le sentinelle di
working tree ne leggono l'UNIONE, come per ogni fase precedente.

Popolato da `git status --porcelain` reale (stessa disciplina di
`tests/a30_10_diff.py`): nessun file elencato qui "per farlo passare", solo
l'inventario esatto di cio' che A30-12 ha davvero toccato/creato fino a
questo punto del gate. Il gate non e' chiuso: la suite di test dedicata e' stata scritta e
validata (40/40 nella suite pura, 32/32 nella suite Postgres dopo la
correzione del bug TOCTOU in `appointments/service.py` e
`public_booking/service.py`); resta la verifica formale finale a
chiusura gate (sezioni A-M del report).

NUOVI: la migration 077 (le tre tabelle del booking pubblico), l'intero
pacchetto `public_booking/` (il solo punto che genera/verifica il token,
applica il rate limit, e chiama l'entrypoint autorevole dell'Agenda - mai
una INSERT diretta su `appointments`), questo stesso file, e le due
suite di test dedicate: `tests/test_a30_12_public_booking.py` (pura,
nessun database - security.py, schemas.py, fingerprint del payload,
allowlist di privacy, il guard del ctx-type, l'assenza di
APIRouter( nel re-export-shim, il gate D10 HARD nel codice sorgente,
i default del rate limit) e `tests/test_a30_12_public_booking_postgres.py`
(Postgres reale, opt-in via P29_TEST_DSN: migration 077 up/down/up,
permessi CRUD, risoluzione del token, disponibilita' D10 HARD,
submit/idempotenza/TOCTOU/concorrenza, rate limiting D9, HMAC
dell'IP, regressione CRM-SOFT A30-11).

MODIFICATI: `appointments/errors.py` (`PublicSlotUnavailable`),
`appointments/service.py` (l'entrypoint autorevole
`create_public_booking_appointment`, D1-D10, ordine a 14 passi),
`appointments/router.py` (le 5 rotte OPERATORE di gestione dei link,
`/api/appointments/booking-links...`), `operator_auth/context.py`
(`public_booking` nell'insieme chiuso di `SYSTEM_CONTEXT_ORIGINS`),
`main.py` (il router PUBBLICO montato, stessa forma di
`communication_public_router`), `scripts/p26_6_live_cert.py` (l'inventario
`AGENDA_OPERAZIONI` esteso con le 5 rotte booking-links; nessuna sonda
anonima aggiuntiva - sono autenticate come le altre rotte operatore),
`tests/test_p26_db_entrypoints.py` e `docs/P26_DB_ENTRYPOINTS.md`
(la nuova voce per `tests/test_a30_12_public_booking_postgres.py`, che apre
una connessione Postgres propria per lo stesso motivo delle sue sorelle
A30-1/A30-2/A30-11), e le sentinelle di fase aggiornate "in un modo
dichiarato": ognuna NOMINA cio' che ammette, nessuna ha smesso di guardare.
"""
from __future__ import annotations

FILE_NUOVI = frozenset({
    # la cartella NUOVA: finche' non e' tracciata git la mostra come
    # `?? public_booking/`
    "public_booking/",
    "migrations/077_a30_12_public_booking.sql",
    "migrations/077_a30_12_public_booking_down.sql",
    "public_booking/__init__.py",
    "public_booking/enums.py",
    "public_booking/public_router.py",
    "public_booking/repository.py",
    "public_booking/router.py",
    "public_booking/schemas.py",
    "public_booking/security.py",
    "public_booking/service.py",
    "tests/a30_12_diff.py",
    "tests/test_a30_12_public_booking.py",
    "tests/test_a30_12_public_booking_postgres.py",
})

#: Le righe che A30-12 e' autorizzata ad aggiungere a `main.py`, per intero -
#: il mount del router PUBBLICO del booking (stessa forma di
#: `communication_public_router`, nessuna dipendenza `require_authenticated_operator`:
#: chi apre il link non e' un operatore).
RIGHE_MAIN = frozenset({
    "# A30-12: il booking pubblico. Router PUBBLICO separato (stessa forma di",
    "# communication_public_router), montato piu' sotto senza",
    "# require_authenticated_operator: qui non c'e' un operatore, c'e' il client",
    "# pubblico che apre un link.",
    "from public_booking.public_router import router as public_booking_router",
    "# A30-12: PUBBLICA per natura - chi apre il link non e' un operatore -",
    "# esattamente come communication_public_router sopra.",
    "app.include_router(public_booking_router)",
})

FILE_MODIFICATI = frozenset({
    "appointments/errors.py",
    "appointments/router.py",
    "appointments/service.py",
    "main.py",
    "operator_auth/context.py",
    "scripts/p26_6_live_cert.py",
    "tests/lmc15_main_diff.py",
    "tests/p29_3b_diff.py",
    "tests/test_a30_1_appointments.py",
    "tests/test_a30_2p_073.py",
    "tests/test_a30_mount_api.py",
    "tests/test_lmc15_acquisition_bridge.py",
    "tests/test_lmc1b_owner_login_link.py",
    "tests/test_lmc2_owner_homes.py",
    "tests/test_lmc3_valuation_snapshot.py",
    "tests/test_lmc7_owner_radar.py",
    "tests/test_lmc8_crm_radar.py",
    "tests/test_lmc9_consultation_request.py",
    "tests/test_lmc11_valuation_cron.py",
    "tests/test_lmc13_home_metrics.py",
    "tests/test_p26_1_operator_auth.py",
    "tests/test_p26_5_basic_containment.py",
    "tests/test_p26_6_live_cert_script.py",
    "tests/test_p26_6c_backend_gate_closure.py",
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
    "tests/test_p26_db_entrypoints.py",
    "docs/P26_DB_ENTRYPOINTS.md",
})
