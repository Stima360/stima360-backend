-- STIMA VOICE V9 - incarichi a voce: i nuovi intenti del registro.
--
-- Additiva: allarga SOLO l'elenco degli intenti ammessi nei passi dei
-- comandi vocali (voice_command_steps.intent, CHECK della 098). Nessuna
-- tabella, nessuna colonna, nessun dato toccato. L'incarico resta dove vive
-- da sempre (la riga `properties` e l'acquisizione d'origine, 081): la Voce
-- usa i servizi del CRM, qui si registra soltanto che il passo esiste.
--
--   mandate_prepare     genera l'incarico dall'acquisizione (servizio
--                       originale `generate_mandate`) oppure ne prepara i dati
--                       sull'acquisizione aperta (`patch_acquisition`)
--   mandate_price       un prezzo: richiesto dell'immobile (storico prezzi),
--                       richiesto o valutazione dell'acquisizione aperta
--   mandate_info        sola lettura: dati mancanti, scheda dell'incarico
--   acquisition_start   l'acquisizione con il suo appuntamento in Agenda
--                       (servizio originale `create_acquisition`)

ALTER TABLE voice_command_steps DROP CONSTRAINT voice_steps_intent_chk;
ALTER TABLE voice_command_steps ADD CONSTRAINT voice_steps_intent_chk CHECK (intent IN (
    'add_note', 'add_task', 'create_contact', 'create_unit', 'create_building',
    'link_owner', 'activate_seller', 'create_appointment', 'unsupported',
    'mandate_prepare', 'mandate_price', 'mandate_info', 'acquisition_start'));
