-- 105 down - ADM D15. Toglie i trigger che rifiutano la cancellazione fisica e
-- riporta i trigger di `platform_audit_log` allo stato di 057 (ENABLE).
-- PREPARATA, NON APPLICATA: vedi 105_adm_d15_no_delete_admin_entities.sql.

DO $do$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'agencies', 'operator_users', 'agency_memberships', 'network_territories',
        'agency_territory_assignments', 'network_territory_aliases'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_adm_no_delete ON %I', t);
        EXECUTE format('DROP TRIGGER IF EXISTS trg_adm_no_truncate ON %I', t);
    END LOOP;
END
$do$;

DROP FUNCTION IF EXISTS adm_refuse_admin_delete();

ALTER TABLE platform_audit_log ENABLE TRIGGER trg_platform_audit_log_append_only;
ALTER TABLE platform_audit_log ENABLE TRIGGER trg_platform_audit_log_no_truncate;
