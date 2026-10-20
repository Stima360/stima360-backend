"""SEMPLIFICAZIONE-CRM-1 - Edifici, Immobili e Agenda: l'inventario dichiarato.

Popolato da `git status --porcelain` reale: nessun file elencato qui "per
farlo passare", solo cio' che la fase ha davvero toccato o creato. Inventario
CHIUSO: nessun glob, nessun prefisso.

D1  la «Visita immobile» (`buyer_visit`) nasce solo con un incarico di vendita
    reale, un cliente e un agente (service + trigger additivo 107); la 106
    resta intatta e `sale_appointment` resta valido ma non piu' proponibile.
D2  i codici `IMM-...` spariscono dall'interfaccia ordinaria (helper condiviso
    `components/property-label.js`).
D3  la scheda Incarico elenca le visite dell'Agenda e ne programma di nuove.
D4  le etichette dei dodici tipi; D5 il form Agenda dinamico per tipo, il lead
    tra le opzioni avanzate, l'agente preselezionato; D6 «Acquirenti
    compatibili» nella scheda Immobile, unificati con gli Abbinamenti.
"""
from __future__ import annotations

FILE_NUOVI = frozenset({
    "migrations/107_visita_immobile_incarico.sql",
    "migrations/107_visita_immobile_incarico_down.sql",
    "static/os_shell/assets/components/lead-labels.js",
    "static/os_shell/assets/components/match-labels.js",
    "static/os_shell/assets/components/property-label.js",
    "tests/semplificazione_crm_1_diff.py",
    "tests/test_semplificazione_crm_1_static.py",
})

FILE_MODIFICATI = frozenset({
    "appointments/enums.py",
    "appointments/repository.py",
    "appointments/router.py",
    "appointments/service.py",
    "calendar_sync/service.py",
    "match/repository.py",
    "public_booking/enums.py",
    "public_booking/service.py",
    "static/os_shell/assets/agenda/agenda-api.js",
    "static/os_shell/assets/agenda/agenda-lookup.js",
    "static/os_shell/assets/agenda/agenda-model.js",
    "static/os_shell/assets/app.css",
    "static/os_shell/assets/census/census-model.js",
    "static/os_shell/assets/census/census-sheets.js",
    "static/os_shell/assets/census/property-census-tab.js",
    "static/os_shell/assets/census/composition-model.js",
    "static/os_shell/assets/census/composition-panel.js",
    "static/os_shell/assets/views/acquisizione-dettaglio.js",
    "static/os_shell/assets/views/contatto-dettaglio.js",
    "static/os_shell/assets/views/immobile-condiviso.js",
    "static/os_shell/assets/views/territorio.js",
    "static/os_shell/assets/views/territorio-inserisci.js",
    "static/os_shell/assets/views/venditori.js",
    "static/os_shell/assets/components/agenda/agenda-dialogs.js",
    "static/os_shell/assets/components/contact-picker.js",
    "static/os_shell/assets/components/property-form.js",
    "static/os_shell/assets/territorio/territorio-model.js",
    "static/os_shell/assets/views/abbinamenti.js",
    "static/os_shell/assets/views/abbinamento-dettaglio.js",
    "static/os_shell/assets/views/acquirente-dettaglio.js",
    "static/os_shell/assets/views/acquisizioni.js",
    "static/os_shell/assets/views/agenda/agenda-page.js",
    "static/os_shell/assets/views/edificio-dettaglio.js",
    "static/os_shell/assets/views/immobile-dettaglio.js",
    "static/os_shell/assets/views/immobili.js",
    "static/os_shell/assets/views/incarichi.js",
    "static/os_shell/assets/views/incarico-dettaglio.js",
    # le sentinelle aggiornate (chiuse, dichiarate)
    "tests/test_a30_12_public_booking.py",
    "tests/test_a30_4_agenda_ui.py",
    "tests/test_a30_5_create_ui.py",
    "tests/test_a30_9a_calendar_sync_static.py",
    "tests/test_a31_2_buyer_visits_postgres.py",
    "tests/test_a31_4_buyer_visits_ui_runtime.py",
    "tests/test_appuntamenti_vendita_static.py",
    "tests/test_censimento_2_static.py",
    "tests/test_censimento_4_rev2_ui.py",
    "tests/test_censimento_4_ui.py",
    "tests/test_creazione_guidata_1_ui.py",
    "tests/test_crm_ops_3_acquisitions.py",
    "tests/test_crm_ops_4_mandates.py",
    "tests/test_delete_arch_0_postgres.py",
    "tests/test_delete_arch_2b1_postgres.py",
    "tests/test_edifici_1_ui.py",
    "tests/test_fix_mandate_1_ui.py",
    "tests/test_os_final_live_gaps.py",
    "tests/test_p27_7_network_runtime.py",
    "tests/test_p29_3g_final_fixes.py",
    "tests/test_pertinenze_1_ui.py",
    "tests/test_zona_3_ui.py",
    "tests/test_censimento_2_ui.py",
    "tests/test_crm_ops_3_postcommit.py",
    "tests/test_venditori_1_ui.py",
})

#: Le viste A31-4 (abbinamento, acquirente, immobile) chiedono anche
#: `lookupMandates` all'UNICO client dell'Agenda, per verificare l'incarico
#: PRIMA di aprire il dialog (sentinella A30-4 test_04, esenzione chiusa).
IMPORT_AMMESSI_VISTE_A31_4 = (
    "import { openCreateDialog } from '../components/agenda/agenda-dialogs.js';",
    "import { getAgents, lookupMandates } from '../agenda/agenda-api.js';",
    "import { todayKey } from '../agenda/agenda-model.js';",
)

#: La scheda Incarico (D3): le visite dell'Agenda come fonte autorevole
#: (`getList`), la programmazione con il dialog condiviso e il collegamento
#: «Apri in Agenda» alla vista Giorno. Esenzione CHIUSA: una sola vista, queste
#: sole righe `agenda/`, esatte e nell'ordine.
VISTE_CON_DIALOG_AGENDA = {
    "static/os_shell/assets/views/incarico-dettaglio.js": (
        "import { getAgents, getList, lookupMandates } from '../agenda/agenda-api.js';",
        "import { MANDATE_REQUIRED_TYPES, formatTime, romeDateKey, statusLabel, todayKey } "
        "from '../agenda/agenda-model.js';",
        "import { openCreateDialog } from '../components/agenda/agenda-dialogs.js';",
        "{ label: '', render: (v) => `<a class=\"btn ghost\" href=\"#/agenda/giorno/"
        "${escapeHtml(romeDateKey(v.start_at))}\" data-open-agenda=\"${escapeHtml(v.id)}\">"
        "Apri in Agenda</a>` },",
    ),
}
