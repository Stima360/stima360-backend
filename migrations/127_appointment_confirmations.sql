-- ROADMAP PUNTO 2 - `appointment_confirmation`: la conferma (o il nuovo orario)
-- di un appuntamento fissato, inviata al cliente dal CRM.
--
-- Additiva, stesso schema della 079 (A32-1) e della 123: allarga di UN valore
-- il CHECK dei motivi di `communication_messages`. Nessuna riga letta o
-- riscritta: l'insieme nuovo e' un sovrainsieme del precedente.
--
-- E' di SERVIZIO (esegue l'appuntamento che il cliente ha fissato), come il
-- promemoria: il dispatcher continua a interrogare il consenso solo per il
-- marketing. Il valore nuovo non allarga nessun permesso.
--
-- La transazione e' del runner.

ALTER TABLE communication_messages
    DROP CONSTRAINT IF EXISTS communication_messages_reason_code_chk;

ALTER TABLE communication_messages
    ADD CONSTRAINT communication_messages_reason_code_chk
    CHECK (reason_code IN ('stima_pdf','operator_manual','operator_reply','owner_login_link','appointment_reminder',
        'consent_confirmation', 'auto_reply', 'appointment_confirmation',
        'm1','m2','m3','m4','m5','m6','m7','m8','m9','m10',
        'm11','m12','m13','m14','m15','m16','m17','m18','m19','m20'));

DO $do$
DECLARE
    v_def text;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO v_def
      FROM pg_constraint
     WHERE conname = 'communication_messages_reason_code_chk'
       AND conrelid = 'public.communication_messages'::regclass;
    IF v_def IS NULL OR v_def NOT LIKE '%appointment_confirmation%' OR v_def NOT LIKE '%auto_reply%'
       OR v_def NOT LIKE '%m20%' THEN
        RAISE EXCEPTION '127: communication_messages_reason_code_chk does not carry the expected values';
    END IF;
END
$do$;
