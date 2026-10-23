"""STIMA VOICE V9 - incarichi a voce: l'inventario dichiarato.

Popolato da `git status --porcelain` reale. Inventario CHIUSO: nessun glob.
Creazione dell'incarico con il servizio originale (generate_mandate), dati e
prezzi dell'acquisizione (patch_acquisition), prezzo dell'immobile con lo
storico prezzi (PATCH della scheda Immobile), acquisizione con il suo
appuntamento (create_acquisition), letture della situazione. Migration 111:
solo l'elenco degli intenti del registro della Voce.
"""
from __future__ import annotations

FILE_NUOVI = frozenset({
    "tests/voice_v9_diff.py",
    "voice/mandate_rules.py",
    "migrations/111_voice_mandate_intents.sql",
    "migrations/111_voice_mandate_intents_down.sql",
    "tests/test_voice_9_mandates.py",
    "tests/test_voice_9_mandates_postgres.py",
    "tests/test_voice_9_mandates_browser_postgres.py",
})

FILE_MODIFICATI = frozenset({
    "voice/executor.py",
    "voice/ledger.py",
    "voice/operations.py",
    "voice/planner.py",
    "voice/policy.py",
    "voice/provider.py",
    "voice/repository.py",
    "voice/resolver.py",
    "voice/schemas.py",
    "voice/service.py",
    "static/os_shell/assets/views/voce.js",
    "static/os_shell/assets/voice/voice-model.js",
    "tests/test_voice_1_contract.py",
    "tests/test_voice_1_perimeter.py",
    "tests/test_voice_8_0_general.py",
    # la 111 e' ora l'ultima migration: le sentinelle della numerazione la nominano
    "tests/test_censimento_2_static.py",
    "tests/test_fase_b_followup_static.py",
    "tests/test_semplificazione_crm_1_static.py",
    "tests/test_p29_3_journey_foundation.py",
})
