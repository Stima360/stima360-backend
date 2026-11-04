-- Down per 129. Rifiuta se esistono sfide, prove o messaggi con i motivi nuovi: sono prove di sottoscrizioni e
-- si conservano (additivi, il codice precedente li ignora). La transazione e' del runner.
DO $do$
BEGIN
    IF EXISTS (SELECT 1 FROM signature_evidence) OR EXISTS (SELECT 1 FROM signature_challenges) THEN
        RAISE EXCEPTION 'signature_challenges/signature_evidence have rows: keep the tables or restore from a verified backup';
    END IF;
    IF EXISTS (SELECT 1 FROM communication_messages WHERE reason_code IN ('signature_link', 'signature_otp')) THEN
        RAISE EXCEPTION 'signature messages exist: keep the constraint or restore from a verified backup';
    END IF;
END
$do$;
DROP TRIGGER IF EXISTS trg_signature_evidence_guard ON signature_evidence;
DROP FUNCTION IF EXISTS signature_evidence_guard();
DROP TABLE IF EXISTS signature_evidence;
DROP TRIGGER IF EXISTS trg_signature_challenges_guard ON signature_challenges;
DROP FUNCTION IF EXISTS signature_challenges_guard();
DROP TABLE IF EXISTS signature_challenges;
ALTER TABLE communication_messages
    DROP CONSTRAINT IF EXISTS communication_messages_reason_code_chk;
ALTER TABLE communication_messages
    ADD CONSTRAINT communication_messages_reason_code_chk
    CHECK (reason_code IN ('stima_pdf','operator_manual','operator_reply','owner_login_link','appointment_reminder',
        'consent_confirmation', 'auto_reply', 'appointment_confirmation',
        'm1','m2','m3','m4','m5','m6','m7','m8','m9','m10',
        'm11','m12','m13','m14','m15','m16','m17','m18','m19','m20'));
