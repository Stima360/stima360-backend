-- Down for 109. Toglie il canale dagli eventi, la proiezione per canale, i
-- canali delle informative e la lista di piattaforma per (canale, indirizzo);
-- riapre in scrittura `communication_email_optouts` (108).
--
-- Si FERMA, senza modifiche, se esiste anche un solo evento di consenso con
-- un canale, una riga di proiezione per canale, una informativa che dichiara
-- canali o un opt-out che non sia la copia di una riga 108: toglierli
-- significherebbe cancellare decisioni reali.
BEGIN;

DO $do$
BEGIN
    IF EXISTS (SELECT 1 FROM consent_events WHERE channel IS NOT NULL)
    OR EXISTS (SELECT 1 FROM contact_channel_consents)
    OR EXISTS (SELECT 1 FROM consent_notices WHERE channels <> '{}')
    OR EXISTS (SELECT 1 FROM communication_optouts o WHERE o.channel <> 'email')
    OR (to_regclass('public.communication_email_optouts') IS NULL
        AND EXISTS (SELECT 1 FROM communication_optouts))
    OR (to_regclass('public.communication_email_optouts') IS NOT NULL
        AND EXISTS (SELECT 1 FROM communication_optouts o
                     WHERE NOT EXISTS (SELECT 1 FROM communication_email_optouts e
                                        WHERE e.idempotency_key = o.idempotency_key))) THEN
        RAISE EXCEPTION '109 down: esistono consensi o opt-out per canale; nessuna modifica eseguita';
    END IF;
END
$do$;

DO $do$
BEGIN
    IF to_regclass('public.communication_email_optouts') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS trg_comm_optouts_closed ON communication_email_optouts;
    END IF;
END
$do$;
DROP FUNCTION IF EXISTS communication_email_optouts_closed();

DROP TRIGGER IF EXISTS trg_comm_optouts2_append_only ON communication_optouts;
DROP FUNCTION IF EXISTS communication_optouts_append_only();
DROP TABLE IF EXISTS communication_optouts;

ALTER TABLE consent_notices DROP CONSTRAINT IF EXISTS consent_notices_channels_purpose_chk;
ALTER TABLE consent_notices DROP CONSTRAINT IF EXISTS consent_notices_channels_chk;
ALTER TABLE consent_notices DROP COLUMN IF EXISTS channels;

DROP TABLE IF EXISTS contact_channel_consents;

DROP TRIGGER IF EXISTS trg_consent_events_channel_required ON consent_events;
DROP FUNCTION IF EXISTS consent_events_channel_required();
DROP INDEX IF EXISTS idx_consent_events_channel_current;
ALTER TABLE consent_events DROP CONSTRAINT IF EXISTS consent_events_channel_purpose_chk;
ALTER TABLE consent_events DROP CONSTRAINT IF EXISTS consent_events_channel_chk;
ALTER TABLE consent_events DROP COLUMN IF EXISTS channel;

COMMIT;
