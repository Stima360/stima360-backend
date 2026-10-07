-- Down for 092. Toglie il Cestino delle richieste acquirente: guardie,
-- colonne, e riporta il CHECK del registro a immobili, contatti ed edifici (091).
--
-- Si FERMA se una richiesta e' nel Cestino o se il registro contiene eventi
-- di richieste (append-only: non si riscrive). Nessuna modifica eseguita in
-- quel caso.

BEGIN;

DO $do$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'buy_requests' AND column_name = 'deleted_at')
       AND EXISTS (SELECT 1 FROM buy_requests WHERE deleted_at IS NOT NULL) THEN
        RAISE EXCEPTION '092 down: esistono richieste acquirente nel Cestino; nessuna modifica eseguita';
    END IF;
    IF EXISTS (SELECT 1 FROM record_lifecycle_events WHERE entity_type = 'buy_request') THEN
        RAISE EXCEPTION '092 down: il registro contiene eventi di richieste acquirente; nessuna modifica eseguita';
    END IF;
END
$do$;

DO $do$
DECLARE
    v_tabella TEXT;
BEGIN
    FOREACH v_tabella IN ARRAY ARRAY['buy_request_locations', 'buy_request_typologies', 'buy_request_features',
                                      'buy_request_interactions', 'buy_request_task_links', 'buy_request_history',
                                      'matches', 'match_runs', 'match_exclusions', 'property_sales',
                                      'invisible_sale_candidates', 'property_proposals'] LOOP
        IF to_regclass('public.' || v_tabella) IS NOT NULL THEN
            EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', 'trg_' || v_tabella || '_buy_trash_guard', v_tabella);
        END IF;
    END LOOP;
END
$do$;
DROP TRIGGER IF EXISTS trg_buy_requests_trash_freeze ON buy_requests;
DROP FUNCTION IF EXISTS cestino_richieste_guard();
DROP FUNCTION IF EXISTS cestino_richieste_proposal_guard();
DROP FUNCTION IF EXISTS cestino_richieste_freeze();

ALTER TABLE record_lifecycle_events DROP CONSTRAINT IF EXISTS record_lifecycle_events_entity_chk;
ALTER TABLE record_lifecycle_events ADD CONSTRAINT record_lifecycle_events_entity_chk
    CHECK (entity_type IN ('property', 'contact', 'building'));

DROP INDEX IF EXISTS idx_buy_requests_trash;
ALTER TABLE buy_requests DROP CONSTRAINT IF EXISTS buy_requests_deleted_state_chk;
ALTER TABLE buy_requests DROP CONSTRAINT IF EXISTS buy_requests_deleted_reason_chk;
ALTER TABLE buy_requests DROP CONSTRAINT IF EXISTS buy_requests_deleted_by_user_fk;
ALTER TABLE buy_requests DROP COLUMN IF EXISTS deleted_reason;
ALTER TABLE buy_requests DROP COLUMN IF EXISTS deleted_by_user_id;
ALTER TABLE buy_requests DROP COLUMN IF EXISTS deleted_at;

COMMIT;
