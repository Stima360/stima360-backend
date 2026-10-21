"""STIMA VOICE V8 - creazione intelligente degli appuntamenti: l'inventario dichiarato.

Popolato da `git status --porcelain` reale. Inventario CHIUSO: nessun glob.
V8-1 ruoli e sinonimi; V8-2 incarico obbligatorio a monte; V8-3 ricerca
tollerante dei contatti; V8-4 scheda riepilogativa, conferma detta e
collegamento all'appuntamento; V8-5 microfono nel dialog «Nuovo appuntamento».
"""
from __future__ import annotations

FILE_NUOVI = frozenset({
    "tests/voice_v8_diff.py",
    "tests/test_voice_8_1_language.py",
    "tests/test_voice_8_2_visits_postgres.py",
    "tests/test_voice_8_3_contacts.py",
    "tests/test_voice_8_3_contacts_postgres.py",
    "tests/test_voice_8_4_summary.py",
    "tests/test_voice_8_4_summary_postgres.py",
    "tests/test_voice_8_5_agenda_mic.py",
    "tests/test_voice_8_5_interpret_postgres.py",
    "tests/test_voice_8_0_general.py",
    "tests/test_voice_8_0_provision_postgres.py",
    # collaudo reale del 21/10: 429 del fornitore, chi visita e chi vende
    "tests/test_voice_8_0_conversations.py",
    "tests/test_voice_8_0_conversations_postgres.py",
    "tests/test_voice_8_0_browser_postgres.py",
    "tests/test_voice_8_0_dictation_dates.py",
    # collaudo iPhone del 21/10: trascrizione, nomi a meta', ruoli, impaginazione
    "tests/test_voice_8_0_iphone.py",
    "tests/test_voice_8_0_iphone_postgres.py",
    "tests/test_voice_8_0_iphone_browser_postgres.py",
    # collaudo 3 del 21/10: ripresa della conversazione, cognomi storpiati, chiarimenti senza il modello
    "tests/test_voice_8_0_collaudo3.py",
    "tests/test_voice_8_0_collaudo3_postgres.py",
    "tests/test_voice_8_0_collaudo3_browser_postgres.py",
})

FILE_MODIFICATI = frozenset({
    "voice/operations.py",
    "voice/dates.py",
    # collaudo 3: il proprietario detto trovato fra i contatti (solo un'informazione per la domanda)
    "voice/executor.py",
    "voice/fake_provider.py",
    # V8-0: la Voce per tutte le agenzie attive (riga creata con l'agenzia)
    "platform_admin/agencies_repository.py",
    "platform_admin/agencies_service.py",
    "tests/conftest.py",
    "tests/test_p27_2_agencies.py",
    "voice/router.py",
    "scripts/voice_7_live_cert.py",
    "voice/planner.py",
    "voice/policy.py",
    "voice/provider.py",
    "voice/repository.py",
    "voice/resolver.py",
    "voice/schemas.py",
    "voice/service.py",
    "static/os_shell/assets/app.css",
    "static/os_shell/assets/components/agenda/agenda-dialogs.js",
    "static/os_shell/assets/components/contact-picker.js",
    "static/os_shell/assets/views/agenda/agenda-page.js",
    "static/os_shell/assets/voice/voice-api.js",
    # collaudo 21/10: risposta non arrivata (rete) distinta dagli errori dell'interfaccia; dettatura manuale
    "static/os_shell/assets/core/api-client.js",
    "static/os_shell/assets/agenda/agenda-model.js",
    "static/os_shell/assets/voice/voice-speech.js",
    "static/os_shell/assets/views/voce.js",
    "static/os_shell/assets/voice/voice-model.js",
    "tests/test_a30_4_agenda_ui.py",
    "tests/test_p29_3g_final_fixes.py",
    "tests/test_voice_1_corpus.py",
    "tests/test_voice_1_perimeter.py",
    "tests/test_voice_4_undo_postgres.py",
    "tests/test_voice_7_activation_postgres.py",
    "tests/voice_corpus/corpus.py",
})

#: V8-4: il collegamento «Apri in Agenda» dell'appuntamento salvato, costruito
#: dal modello puro della Voce (sentinella A30-4 test_04: esenzione CHIUSA, una
#: sola riga esatta, nessun import dell'Agenda).
VISTE_CON_DIALOG_AGENDA = {
    "static/os_shell/assets/voice/voice-model.js": (
        "return giorno ? `#/agenda/giorno/${giorno}` : '#/agenda';",
    ),
}
