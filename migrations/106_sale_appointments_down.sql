-- Down for 106. Toglie la tipologia `sale_appointment` e il suo vincolo, e
-- riporta `appointments_type_chk` alle dodici tipologie della 072.
--
-- Si FERMA, senza modifiche, se esiste anche un solo appuntamento di vendita:
-- toglierla significherebbe riscrivere o cancellare appuntamenti reali.

BEGIN;

DO $do$
BEGIN
    IF EXISTS (SELECT 1 FROM appointments WHERE appointment_type = 'sale_appointment') THEN
        RAISE EXCEPTION '106 down: esistono appuntamenti di vendita; nessuna modifica eseguita';
    END IF;
END
$do$;

ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_sale_links_chk;
ALTER TABLE appointments DROP CONSTRAINT appointments_type_chk;
ALTER TABLE appointments ADD CONSTRAINT appointments_type_chk CHECK (appointment_type IN (
    'call', 'video_call', 'seller_meeting', 'inspection', 'buyer_visit',
    'valuation_presentation', 'mandate_signing', 'proposal',
    'preliminary_contract', 'notary', 'technical', 'other'));

COMMIT;
