-- Down for 127. Rifiuta se esistono messaggi con il motivo nuovo: conservarli
-- (sono additivi, il codice precedente li ignora). La transazione e' del runner.
DO $do$
BEGIN
    IF EXISTS (SELECT 1 FROM communication_messages WHERE reason_code = 'appointment_confirmation') THEN
        RAISE EXCEPTION 'appointment_confirmation messages exist: keep the constraint or restore from a verified backup';
    END IF;
END
$do$;
ALTER TABLE communication_messages
    DROP CONSTRAINT IF EXISTS communication_messages_reason_code_chk;
ALTER TABLE communication_messages
    ADD CONSTRAINT communication_messages_reason_code_chk
    CHECK (reason_code IN ('stima_pdf','operator_manual','operator_reply','owner_login_link','appointment_reminder',
        'consent_confirmation', 'auto_reply',
        'm1','m2','m3','m4','m5','m6','m7','m8','m9','m10',
        'm11','m12','m13','m14','m15','m16','m17','m18','m19','m20'));
