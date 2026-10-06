-- Down for 086. Toglie le guardie del Cestino Immobili (trigger e funzioni).
-- Nessun dato cambia: la 086 non ne ha scritti. Gli immobili nel Cestino
-- restano nel Cestino (085), solo senza la garanzia del database.

BEGIN;

DO $do$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['property_contacts', 'property_leads', 'property_documents', 'property_photos',
                             'property_visits', 'property_accessories', 'activities', 'appointments',
                             'acquisitions', 'stima_acquisitions', 'matches', 'match_exclusions',
                             'property_sales', 'buy_request_interactions', 'owner_property_access',
                             'owner_publications', 'owner_feedback', 'owner_notifications',
                             'property_proposals', 'owner_shared_documents',
                             'owner_visit_feedback_publications'] LOOP
        IF to_regclass('public.' || t) IS NOT NULL THEN
            EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', 'trg_' || t || '_property_trash_guard', t);
        END IF;
    END LOOP;
END
$do$;

DROP TRIGGER IF EXISTS trg_properties_trash_freeze ON properties;
DROP FUNCTION IF EXISTS delete_arch_properties_trash_freeze();
DROP FUNCTION IF EXISTS delete_arch_property_trash_guard_via();
DROP FUNCTION IF EXISTS delete_arch_property_trash_guard();

COMMIT;
