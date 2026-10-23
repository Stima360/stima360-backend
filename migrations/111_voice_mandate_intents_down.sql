-- Down for 111. Torna l'elenco degli intenti della 098 SENZA cancellare la
-- storia: i passi gia' registrati con gli intenti degli incarichi restano
-- nel registro (audit). Il vincolo torna `NOT VALID`: vale per ogni passo
-- NUOVO e non riverifica le righe storiche.

BEGIN;

ALTER TABLE voice_command_steps DROP CONSTRAINT voice_steps_intent_chk;
ALTER TABLE voice_command_steps ADD CONSTRAINT voice_steps_intent_chk CHECK (intent IN (
    'add_note', 'add_task', 'create_contact', 'create_unit', 'create_building',
    'link_owner', 'activate_seller', 'create_appointment', 'unsupported')) NOT VALID;

COMMIT;
