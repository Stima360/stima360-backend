-- Down for 079. Riporta `reason_code` ai nove valori della 067.
--
-- RIFIUTA SE IL LEDGER CONTIENE GIA' UN PROMEMORIA.
--
-- Il ledger e' append-only: restringere il CHECK con righe
-- `appointment_reminder` gia' scritte vorrebbe dire far fallire l'ADD
-- CONSTRAINT o cancellare righe, cioe' falsificare il registro degli invii.
-- Si controlla prima e, se ce ne sono, ci si ferma lasciando tutto com'e'
-- (stesso comportamento del down della 067).

BEGIN;

DO $$
DECLARE
    v_righe INTEGER;
BEGIN
    SELECT count(*) INTO v_righe
      FROM communication_messages
     WHERE reason_code = 'appointment_reminder';
    IF v_righe > 0 THEN
        RAISE EXCEPTION
            'A32-1 079 down: % message(s) carry reason_code appointment_reminder and would be orphaned by restoring the nine-value CHECK. Nothing has been changed. Export or reclassify them deliberately first.',
            v_righe;
    END IF;
END
$$;

ALTER TABLE communication_messages
    DROP CONSTRAINT IF EXISTS communication_messages_reason_code_chk;

ALTER TABLE communication_messages
    ADD CONSTRAINT communication_messages_reason_code_chk
        CHECK (reason_code IN (
            'stima_pdf', 'operator_manual', 'operator_reply',
            'm1', 'm2', 'm3', 'm4', 'm5',
            'owner_login_link'
        ));

DELETE FROM schema_migrations WHERE version = '079_a32_1_appointment_reminders';

COMMIT;
