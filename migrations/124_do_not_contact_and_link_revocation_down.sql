-- Rientro della 124. RIFIUTA se esistono divieti di contatto o motivi di
-- disattivazione registrati: conservarli o ripristinare da un backup verificato.
--
-- La transazione e' del runner.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM contact_do_not_contact)
       OR EXISTS (SELECT 1 FROM property_visit_booking_links
                   WHERE disabled_reason IS NOT NULL OR disabled_note IS NOT NULL) THEN
        RAISE EXCEPTION '124 down refused: do-not-contact or link revocation data exists';
    END IF;
END;
$$;

ALTER TABLE property_visit_booking_links
    DROP CONSTRAINT property_visit_booking_links_revocation_note_chk,
    DROP CONSTRAINT property_visit_booking_links_disabled_reason_chk,
    DROP COLUMN disabled_note,
    DROP COLUMN disabled_reason;

DROP TRIGGER trg_contact_do_not_contact_guard ON contact_do_not_contact;
DROP FUNCTION contact_do_not_contact_guard();
DROP TABLE contact_do_not_contact;
