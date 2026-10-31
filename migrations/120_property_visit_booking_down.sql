-- BLOCCO-5A - rollback della 120. Rifiuta se esistono collegamenti o
-- prenotazioni: le visite restano in `appointments` comunque, ma il legame
-- con il link e i token di gestione si conservano e si ripristinano da un
-- backup verificato, non si cancellano.
--
-- La transazione e' del runner.

LOCK TABLE property_visit_booking_rate_limits, property_visit_bookings, property_visit_booking_links
    IN ACCESS EXCLUSIVE MODE;

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM property_visit_bookings) OR EXISTS (SELECT 1 FROM property_visit_booking_links) THEN
        RAISE EXCEPTION 'Property visit booking records exist: preserve the tables and restore from a verified backup';
    END IF;
END $$;

DROP TABLE property_visit_booking_rate_limits;
DROP TABLE property_visit_bookings;
DROP TABLE property_visit_booking_links;
DROP FUNCTION property_visit_bookings_guard();
DROP FUNCTION property_visit_booking_links_guard();
