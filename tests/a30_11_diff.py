"""A30-11B+C - l'inventario dichiarato di questa fase (ORARI DI LAVORO /
AVAILABILITY, vincolo SOFT nel CRM, e il fix del certificatore live),
quattordicesima dichiarazione dell'Agenda accanto a `a30_1_diff` ...
`a30_10_diff`: le sentinelle di working tree ne leggono l'UNIONE, come per
ogni fase precedente.

Nove file NUOVI: quattro nel package `appointments/` (il domain puro
`working_hours.py`, il repository dedicato, gli schemi Pydantic e il
service CRUD con i suoi permessi D6), una migration NUOVA (076: le tre
tabelle `agent_working_hours`/`agent_availability_exceptions`/
`agency_closures`, D2 - nessuna colonna aggiunta ad `appointments`), questo
stesso file, e due file di test NUOVI (l'unità pura e la suite PostgreSQL
di questa fase).

Modificati: `appointments/service.py` (SOLO `availability_check` e
`availability_slots` guadagnano il campo additivo `within_working_hours`;
`_occupa`/`find_conflicts`/`lock_agents`/la state machine restano
esattamente come prima - D2), `appointments/router.py` (le rotte additive
`/agents/{id}/working-hours`, `/agents/{id}/availability-exceptions`,
`/closures`, dichiarate PRIMA di `/{appointment_id}` come `/lookups/stime`),
`scripts/p26_6_live_cert.py` (A30-11C: `AGENDA_OPERAZIONI` resta
l'inventario COMPLETO delle rotte montate - comprese le due DELETE nuove -
mentre il giro anonimo del certificatore live ora itera
`AGENDA_SONDE_ANONIME`, il sottoinsieme che esclude per costruzione ogni
DELETE: l'invarianza "il certificatore live non emette mai una DELETE"
resta intatta, non indebolita), e le sentinelle aggiornate "in un modo
dichiarato" (§44) che assumevano la 075 come cima della serie delle
migration.
"""
from __future__ import annotations

FILE_NUOVI = frozenset({
    "migrations/076_a30_11_working_hours.sql",
    "migrations/076_a30_11_working_hours_down.sql",
    "appointments/working_hours.py",
    "appointments/working_hours_repository.py",
    "appointments/working_hours_schemas.py",
    "appointments/working_hours_service.py",
    "tests/a30_11_diff.py",
    "tests/test_a30_11_working_hours.py",
    "tests/test_a30_11_working_hours_postgres.py",
})

FILE_MODIFICATI = frozenset({
    # SOLO le due rotte di lettura additive (D2: vincolo soft, mai un
    # cambiamento al motore di sovrapposizione o al percorso di scrittura).
    "appointments/service.py",
    # le rotte additive di orari/eccezioni/chiusure, dichiarate prima di
    # "/{appointment_id}" (stessa convenzione di "/lookups/stime").
    "appointments/router.py",
    # A30-11C: AGENDA_OPERAZIONI (inventario) vs AGENDA_SONDE_ANONIME
    # (traffico) - il giro anonimo non emette piu' le due DELETE A30-11B,
    # senza indebolire l'inventario ne' le sentinelle di sicurezza esistenti
    # (test_86m/test_86r/test_96c, invariate).
    "scripts/p26_6_live_cert.py",
    # le sentinelle aggiornate in un modo dichiarato (§44): la 076 e' la
    # nuova cima della serie delle migration.
    "tests/test_a30_1_appointments.py",
    "tests/test_a30_2p_073.py",
    "tests/test_lmc15_acquisition_bridge.py",
    "tests/test_lmc1b_owner_login_link.py",
    "tests/test_p27_6_lead_routing.py",
    "tests/test_p29_2_1_communication_foundation.py",
    "tests/test_p29_2_2_communication_service.py",
    "tests/test_p29_2_3_claim_sentinels.py",
    "tests/test_p29_2_4_dispatch_sentinels.py",
    "tests/test_p29_2_5e_email_adapter.py",
    "tests/test_p29_3_journey_foundation.py",
    "tests/test_lmc11_valuation_cron.py",
    "tests/test_lmc13_home_metrics.py",
    "tests/test_lmc7_owner_radar.py",
    "tests/test_lmc8_crm_radar.py",
    "tests/test_lmc9_consultation_request.py",
    "tests/test_lmc2_owner_homes.py",
    "tests/test_lmc3_valuation_snapshot.py",
    "tests/test_p29_3c_orchestrator.py",
    "tests/test_p29_3d_crm_journey.py",
    "tests/test_p29_3e_cron_integration.py",
    "tests/test_p29_3g_final_fixes.py",
    "tests/test_p29_1_consent_migrations.py",
    # h11/86i (P26-0 e P26-6): il nuovo entrypoint DB DSN-gated
    # (test_a30_11_working_hours_postgres.py) e le tre nuove FK non-CASCADE
    # verso `agencies` (agent_working_hours/agent_availability_exceptions/
    # agency_closures) - stesso trattamento gia' riservato a 072/074.
    "tests/test_a30_mount_api.py",
    "tests/test_p26_db_entrypoints.py",
    "tests/test_p26_6_live_cert_script.py",
    "docs/P26_DB_ENTRYPOINTS.md",
})
