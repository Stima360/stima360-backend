"""A31-4 - BUYER VISITS UI -> AGENDA: l'inventario dichiarato della fase.

Popolato da `git status --porcelain` reale: nessun file elencato qui "per
farlo passare", solo cio' che A31-4 ha davvero toccato o creato. Inventario
CHIUSO: nessun glob, nessun prefisso.

UI: i tre caller della OS Shell (abbinamento, acquirente, immobile) programmano
una NUOVA visita futura con il dialog condiviso dell'Agenda
(`openCreateDialog`, esteso con opzioni generiche e facoltative); la sola
scrittura resta la POST BUY/PROPERTY esistente verso la facade A31-3.

BACKEND (autorizzato): il percorso legacy scoped owner/admin e' SPENTO -
`buy/repository.py` (nessun fallback senza agente) e `property/repository.py`
(visita futura aperta senza agente = errore; lo storico resta legacy).

SENTINELLE AGGIORNATE (autorizzate, ciascuna con il suo commento
"SENTINELLA AGGIORNATA DA A31-4"): A31-3 static test_02/06/08, A31-3 postgres
test_n3, A30-4 test_04 (esenzione chiusa: 3 viste x 3 import esatti), P25.5
(la "Visita programmata" non passa piu' dal dialog esito), P27-7 test_d3 e
P29-3G test_18 (le dichiarazioni di working tree).
"""
from __future__ import annotations

FILE_NUOVI = frozenset({
    "tests/a31_4_diff.py",
    "tests/test_a31_4_buyer_visits_ui_static.py",
    "tests/test_a31_4_buyer_visits_ui_runtime.py",
    "tests/test_a31_4_legacy_lane_closed_postgres.py",
})

FILE_MODIFICATI = frozenset({
    # il dialog condiviso dell'Agenda: opzioni generiche A31-4
    "static/os_shell/assets/components/agenda/agenda-dialogs.js",
    # i tre caller della OS Shell
    "static/os_shell/assets/views/abbinamento-dettaglio.js",
    "static/os_shell/assets/views/acquirente-dettaglio.js",
    "static/os_shell/assets/views/immobile-dettaglio.js",
    # il percorso legacy scoped owner/admin spento
    "buy/repository.py",
    "property/repository.py",
    # le sentinelle autorizzate
    "tests/test_a31_3_buyer_visits_facade_static.py",
    "tests/test_a31_3_buyer_visits_facade_postgres.py",
    "tests/test_a30_4_agenda_ui.py",
    "tests/test_os_shell_p25_5_buyer_workflow.py",
    "tests/test_p27_7_network_contracts.py",
    "tests/test_p29_3g_final_fixes.py",
})
