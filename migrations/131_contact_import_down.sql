-- Down per 131. Rifiuta se esistono numeri aggiuntivi o contatti riservati:
-- sono dati della rubrica di un operatore, si conservano o si ripristinano da
-- un backup verificato. Il registro delle importazioni si elimina (solo
-- riepiloghi). La transazione e' del runner.
DO $do$
BEGIN
    IF EXISTS (SELECT 1 FROM contact_phones) THEN
        RAISE EXCEPTION 'contact_phones has rows: keep the table or restore from a verified backup';
    END IF;
    IF EXISTS (SELECT 1 FROM contacts WHERE private_user_id IS NOT NULL) THEN
        RAISE EXCEPTION 'private contacts exist: keep contacts.private_user_id or restore from a verified backup';
    END IF;
END
$do$;
DROP TABLE IF EXISTS contact_imports;
DROP TABLE IF EXISTS contact_phones;
DROP INDEX IF EXISTS idx_contacts_private_user;
ALTER TABLE contacts DROP COLUMN IF EXISTS private_user_id;
