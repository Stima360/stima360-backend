BEGIN;
DROP INDEX IF EXISTS idx_property_visits_scheduled_status;
DROP INDEX IF EXISTS idx_properties_mandate_end;
DROP INDEX IF EXISTS idx_property_documents_status;
DROP TABLE IF EXISTS property_status_history;
DROP TABLE IF EXISTS property_price_history;
COMMIT;
