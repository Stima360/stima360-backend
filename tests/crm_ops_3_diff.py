"""CRM-OPS-3 - MODULO ACQUISIZIONI: l'inventario dichiarato.

Popolato da `git status --porcelain` reale: nessun file elencato qui "per
farlo passare", solo cio' che CRM-OPS-3 ha davvero toccato o creato.

NUOVI: il package `acquisitions/` (distinto dal ponte LMC-15 `acquisition/`),
la migration 081 con la sua down, le due viste OS (elenco e scheda), questo
file e la suite PostgreSQL / statica.

MODIFICATI: `main.py` (il mount, righe sotto), `appointments/service.py` (le
chiamate agli hook, accanto a quelle delle visite acquirente), PROPERTY
(`property/service.py`, `property/repository.py`: la regola "nessun incarico
nuovo senza acquisizione"), la UI (`main.js`, `app.css`, il form Immobili e
la scheda Immobile) e le sentinelle che nominano CRM-OPS-3.
"""
from __future__ import annotations

FILE_NUOVI = frozenset({
    "acquisitions/__init__.py",
    "acquisitions/enums.py",
    "acquisitions/errors.py",
    "acquisitions/integration.py",
    "acquisitions/repository.py",
    "acquisitions/router.py",
    "acquisitions/schemas.py",
    "acquisitions/service.py",
    "migrations/081_crm_ops_3_acquisitions.sql",
    "migrations/081_crm_ops_3_acquisitions_down.sql",
    "static/os_shell/assets/views/acquisizioni.js",
    "static/os_shell/assets/views/acquisizione-dettaglio.js",
    "tests/crm_ops_3_diff.py",
    "tests/test_crm_ops_3_acquisitions.py",
    "tests/test_crm_ops_3_acquisitions_postgres.py",
})

FILE_MODIFICATI = frozenset({
    "appointments/service.py",
    "main.py",
    "property/repository.py",
    "property/service.py",
    # la matrice ostile live P26-6: il dominio ACQUISITIONS (rejection only)
    "scripts/p26_6_live_cert.py",
    "static/os_shell/assets/app.css",
    "static/os_shell/assets/components/property-form.js",
    "static/os_shell/assets/main.js",
    "static/os_shell/assets/views/immobile-dettaglio.js",
    # le sentinelle che nominano CRM-OPS-3 (aggiornamenti chiusi, dichiarati)
    "tests/lmc15_main_diff.py",
    "tests/test_a30_1_appointments.py",
    "tests/test_a30_2p_073.py",
    "tests/test_a30_4_agenda_ui.py",
    "tests/test_a32_1_reminders_static.py",
    "tests/test_a32_2_reminder_static.py",
    "tests/test_lmc11_valuation_cron.py",
    "tests/test_lmc13_home_metrics.py",
    "tests/test_lmc15_acquisition_bridge.py",
    "tests/test_lmc1b_owner_login_link.py",
    "tests/test_lmc2_owner_homes.py",
    "tests/test_lmc3_valuation_snapshot.py",
    "tests/test_lmc7_owner_radar.py",
    "tests/test_lmc8_crm_radar.py",
    "tests/test_lmc9_consultation_request.py",
    "tests/test_p26_5_basic_containment.py",
    "tests/test_p26_6_live_cert_script.py",
    "tests/test_p27_6_lead_routing.py",
    "tests/test_p27_7_network_contracts.py",
    "tests/test_p27_7_network_runtime.py",
    "tests/test_p29_1_consent_migrations.py",
    "tests/test_p29_2_1_communication_foundation.py",
    "tests/test_p29_2_2_communication_service.py",
    "tests/test_p29_2_3_claim_sentinels.py",
    "tests/test_p29_2_4_dispatch_sentinels.py",
    "tests/test_p29_2_5e_email_adapter.py",
    "tests/test_p29_3_journey_foundation.py",
})

#: Le righe che CRM-OPS-3 aggiunge a `main.py`, per intero.
RIGHE_MAIN = frozenset({
    "from acquisitions.router import router as acquisitions_router",
    "# CRM-OPS-3: le Acquisizioni (`/api/acquisitions`), distinte dal ponte LMC-15.",
    "# Solo il mount; lo scope lo prende ogni rotta da `require_operator`.",
    "app.include_router(acquisitions_router, "
    "dependencies=[Depends(require_authenticated_operator)])",
})

#: Le viste OS di CRM-OPS-3 che usano il dialog e il client dell'Agenda, e le
#: SOLE righe `agenda/` che ciascuna puo' contenere (sentinella A30-4 test_04,
#: esenzione chiusa come quella di A31-4 e CRM-OPS-1B).
VISTE_CON_DIALOG_AGENDA = {
    "static/os_shell/assets/views/acquisizioni.js": (
        "import { openCreateDialog } from '../components/agenda/agenda-dialogs.js';",
        "import { getAgents } from '../agenda/agenda-api.js';",
        "import { addDays, romeIso, statusLabel, todayKey } from '../agenda/agenda-model.js';",
    ),
    "static/os_shell/assets/views/acquisizione-dettaglio.js": (
        "import { openActionDialog } from '../components/agenda/agenda-dialogs.js';",
        "import { getAgents, getAppointment } from '../agenda/agenda-api.js';",
        "import { ACTION_LABELS, formatDuration, durationMinutes, romeDateKey, statusLabel } "
        "from '../agenda/agenda-model.js';",
        # "Apri in Agenda": un collegamento alla rotta della pagina Agenda
        # (vista Giorno), non un import.
        "return giorno ? `#/agenda/giorno/${giorno}` : '#/agenda';",
    ),
}
