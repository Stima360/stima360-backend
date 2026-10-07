-- Down for 090. Toglie il Cestino dei contatti: guardie (collegamenti,
-- riaperture, congelamento), colonne, e riporta il
-- CHECK del registro a soli immobili.
--
-- Si FERMA se un contatto e' nel Cestino o se il registro contiene eventi di
-- contatti (append-only: non si riscrive, e il CHECK della 085 non si potrebbe
-- ripristinare). Nessuna modifica eseguita in quel caso.

BEGIN;

DO $do$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'contacts' AND column_name = 'deleted_at')
       AND EXISTS (SELECT 1 FROM contacts WHERE deleted_at IS NOT NULL) THEN
        RAISE EXCEPTION '090 down: esistono contatti nel Cestino; nessuna modifica eseguita';
    END IF;
    IF EXISTS (SELECT 1 FROM record_lifecycle_events WHERE entity_type = 'contact') THEN
        RAISE EXCEPTION '090 down: il registro contiene eventi di contatti; nessuna modifica eseguita';
    END IF;
END
$do$;

DO $do$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['leads', 'property_contacts', 'buy_requests', 'owner_accounts', 'property_sale_sellers',
                             'acquisitions', 'appointments', 'property_visits', 'contact_roles'] LOOP
        IF to_regclass('public.' || t) IS NOT NULL THEN
            EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', 'trg_' || t || '_contact_trash_guard', t);
            EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', 'trg_' || t || '_contact_trash_reopen', t);
        END IF;
    END LOOP;
END
$do$;
DROP TRIGGER IF EXISTS trg_contacts_trash_freeze ON contacts;
DROP FUNCTION IF EXISTS cestino_contatti_guard();
DROP FUNCTION IF EXISTS cestino_contatti_freeze();
DROP FUNCTION IF EXISTS cestino_contatti_reopen_guard();

ALTER TABLE record_lifecycle_events DROP CONSTRAINT IF EXISTS record_lifecycle_events_entity_chk;
ALTER TABLE record_lifecycle_events ADD CONSTRAINT record_lifecycle_events_entity_chk
    CHECK (entity_type IN ('property'));

DROP INDEX IF EXISTS idx_contacts_trash;
ALTER TABLE contacts DROP CONSTRAINT IF EXISTS contacts_deleted_state_chk;
ALTER TABLE contacts DROP CONSTRAINT IF EXISTS contacts_deleted_reason_chk;
ALTER TABLE contacts DROP CONSTRAINT IF EXISTS contacts_deleted_by_user_fk;
ALTER TABLE contacts DROP COLUMN IF EXISTS deleted_reason;
ALTER TABLE contacts DROP COLUMN IF EXISTS deleted_by_user_id;
ALTER TABLE contacts DROP COLUMN IF EXISTS deleted_at;

COMMIT;
