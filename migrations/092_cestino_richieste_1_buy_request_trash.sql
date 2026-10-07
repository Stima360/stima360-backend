-- CESTINO-RICHIESTE-1 (FASE H): Cestino delle RICHIESTE ACQUIRENTE, sullo
-- stesso impianto del Cestino Immobili (085/086), Contatti (090) ed Edifici
-- (091). Additiva, nessun backfill, nessuna riga esistente cambia valore.
--
--   1. `buy_requests.deleted_at` / `deleted_by_user_id` / `deleted_reason`
--      (NULLABLE), stessi motivi e stessa coerenza degli altri Cestini. Una
--      richiesta e' "nel Cestino" quando `deleted_at` e' valorizzato. L'id
--      resta lo stesso. Il Cestino e' distinto da stato commerciale
--      (`draft/active/paused/satisfied/closed`) e archiviazione
--      (`status = 'archived'` + `archived_at`): nessuno dei due cambia.
--
--   2. `record_lifecycle_events` ammette anche `entity_type = 'buy_request'`.
--
--   3. Guardie:
--      * `cestino_richieste_guard(colonna)` sulle tabelle che PUNTANO alla
--        richiesta (criteri, interazioni, task, storico, abbinamenti, run e
--        esclusioni del matching, vendite, candidati «vendita invisibile»):
--        nessuna riga nuova (INSERT, o UPDATE che CAMBIA la colonna) verso una
--        richiesta nel Cestino. La richiesta si legge FOR SHARE: uno
--        spostamento nel Cestino concorrente (FOR UPDATE, o la sua UPDATE) e
--        un collegamento si serializzano, e chi arriva dopo vede l'altro.
--      * `cestino_richieste_proposal_guard()` su `property_proposals`
--        (INSERT o UPDATE OF match_id): la proposta passa dall'abbinamento,
--        stesso lock sulla richiesta.
--      * `cestino_richieste_freeze()` su `buy_requests`:
--          - lo spostamento nel Cestino (deleted_at da NULL a valore) e'
--            rifiutato con processi aperti: proposte in bozza/inviate,
--            vendite pendenti, task aperti collegati, visite future in
--            programma. Nessuno viene chiuso o scollegato;
--          - la riga nel Cestino non cambia (ammessi: ripristino,
--            `deleted_by_user_id` e `lead_id` per le loro FK ON DELETE SET
--            NULL, `updated_at`).
--      Il rifiuto porta il codice nel messaggio (`BUY_REQUEST_IN_TRASH: ...`,
--      `BUY_REQUEST_HAS_OPEN_PROCESSES: ...`); `core.database.core_cursor`
--      traduce il primo nel 409 BUY_REQUEST_IN_TRASH.
--
-- Le comunicazioni NON sono toccate: nessun messaggio, journey o automazione
-- e' legato a una richiesta (sono del contatto, che resta attivo).
--
-- Le funzioni leggono `deleted_at` via to_jsonb (stesso idioma 086/090/091).
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.

-- ===========================================================================
-- 1. buy_requests: stato Cestino
-- ===========================================================================
ALTER TABLE buy_requests ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE buy_requests ADD COLUMN IF NOT EXISTS deleted_by_user_id BIGINT;
ALTER TABLE buy_requests ADD COLUMN IF NOT EXISTS deleted_reason VARCHAR(30);

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'buy_requests_deleted_by_user_fk'
                      AND conrelid = 'buy_requests'::regclass) THEN
        ALTER TABLE buy_requests ADD CONSTRAINT buy_requests_deleted_by_user_fk
            FOREIGN KEY (deleted_by_user_id) REFERENCES operator_users(id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'buy_requests_deleted_reason_chk'
                      AND conrelid = 'buy_requests'::regclass) THEN
        ALTER TABLE buy_requests ADD CONSTRAINT buy_requests_deleted_reason_chk CHECK (
            deleted_reason IS NULL OR deleted_reason IN (
                'created_by_mistake', 'duplicate', 'invalid_data', 'test_record', 'other'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'buy_requests_deleted_state_chk'
                      AND conrelid = 'buy_requests'::regclass) THEN
        ALTER TABLE buy_requests ADD CONSTRAINT buy_requests_deleted_state_chk CHECK (
            (deleted_at IS NULL AND deleted_by_user_id IS NULL AND deleted_reason IS NULL)
            OR (deleted_at IS NOT NULL AND deleted_reason IS NOT NULL));
    END IF;
END
$do$;

CREATE INDEX IF NOT EXISTS idx_buy_requests_trash
    ON buy_requests (agency_id, deleted_at DESC)
    WHERE deleted_at IS NOT NULL;

-- ===========================================================================
-- 2. registro del ciclo di vita: anche le richieste acquirente
-- ===========================================================================
ALTER TABLE record_lifecycle_events DROP CONSTRAINT IF EXISTS record_lifecycle_events_entity_chk;
ALTER TABLE record_lifecycle_events ADD CONSTRAINT record_lifecycle_events_entity_chk
    CHECK (entity_type IN ('property', 'contact', 'building', 'buy_request'));

-- ===========================================================================
-- 3. guardie
-- ===========================================================================
-- Nessuna riga nuova verso una richiesta nel Cestino. TG_ARGV[0]: la colonna
-- che punta alla richiesta.
CREATE OR REPLACE FUNCTION cestino_richieste_guard() RETURNS trigger AS $fn$
DECLARE
    v_colonna TEXT := TG_ARGV[0];
    v_id BIGINT;
    v_nel_cestino BOOLEAN;
BEGIN
    v_id := NULLIF(to_jsonb(NEW) ->> v_colonna, '')::BIGINT;
    IF v_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' AND (to_jsonb(OLD) ->> v_colonna) IS NOT DISTINCT FROM (to_jsonb(NEW) ->> v_colonna) THEN
        RETURN NEW;                                   -- collegamento esistente: intatto
    END IF;
    SELECT (to_jsonb(b) ->> 'deleted_at') IS NOT NULL INTO v_nel_cestino
      FROM buy_requests b WHERE b.id = v_id FOR SHARE;
    IF v_nel_cestino THEN
        RAISE EXCEPTION 'BUY_REQUEST_IN_TRASH: buy request % is in the trash (new % row refused)', v_id, TG_TABLE_NAME;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- La proposta passa dall'abbinamento.
CREATE OR REPLACE FUNCTION cestino_richieste_proposal_guard() RETURNS trigger AS $fn$
DECLARE
    v_richiesta BIGINT;
    v_nel_cestino BOOLEAN;
BEGIN
    IF NEW.match_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.match_id IS NOT DISTINCT FROM NEW.match_id THEN
        RETURN NEW;
    END IF;
    SELECT m.buy_request_id INTO v_richiesta FROM matches m WHERE m.id = NEW.match_id;
    IF v_richiesta IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT (to_jsonb(b) ->> 'deleted_at') IS NOT NULL INTO v_nel_cestino
      FROM buy_requests b WHERE b.id = v_richiesta FOR SHARE;
    IF v_nel_cestino THEN
        RAISE EXCEPTION 'BUY_REQUEST_IN_TRASH: buy request % is in the trash (new proposal refused)', v_richiesta;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION cestino_richieste_freeze() RETURNS trigger AS $fn$
DECLARE
    v_ammessi TEXT[] := ARRAY['deleted_by_user_id', 'lead_id', 'updated_at'];
BEGIN
    -- spostamento nel Cestino: nessun processo aperto collegato
    IF (to_jsonb(OLD) ->> 'deleted_at') IS NULL AND (to_jsonb(NEW) ->> 'deleted_at') IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM property_proposals pp JOIN matches m ON m.id = pp.match_id
                    WHERE m.buy_request_id = OLD.id AND pp.status IN ('draft', 'submitted'))
           OR EXISTS (SELECT 1 FROM property_sales s WHERE s.buy_request_id = OLD.id AND s.status = 'pending')
           OR EXISTS (SELECT 1 FROM buy_request_task_links l JOIN tasks t ON t.id = l.task_id
                       WHERE l.buy_request_id = OLD.id AND t.status IN ('open', 'in_progress'))
           OR EXISTS (SELECT 1 FROM buy_request_interactions i JOIN property_visits v ON v.id = i.property_visit_id
                       WHERE i.buy_request_id = OLD.id AND i.interaction_type = 'visit_scheduled'
                         AND v.status IN ('scheduled', 'confirmed') AND v.scheduled_at >= NOW()) THEN
            RAISE EXCEPTION 'BUY_REQUEST_HAS_OPEN_PROCESSES: buy request % has open processes and cannot go to the trash', OLD.id;
        END IF;
    END IF;
    -- nel Cestino: congelata
    IF (to_jsonb(OLD) ->> 'deleted_at') IS NOT NULL
       AND (to_jsonb(NEW) ->> 'deleted_at') IS NOT NULL
       AND (to_jsonb(NEW) - v_ammessi) IS DISTINCT FROM (to_jsonb(OLD) - v_ammessi) THEN
        RAISE EXCEPTION 'BUY_REQUEST_IN_TRASH: buy request % is in the trash and cannot be modified', OLD.id;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DO $do$
DECLARE
    v_tabella TEXT;
    v_trigger TEXT;
BEGIN
    FOREACH v_tabella IN ARRAY ARRAY['buy_request_locations', 'buy_request_typologies', 'buy_request_features',
                                      'buy_request_interactions', 'buy_request_task_links', 'buy_request_history',
                                      'matches', 'match_runs', 'match_exclusions', 'property_sales',
                                      'invisible_sale_candidates'] LOOP
        IF to_regclass('public.' || v_tabella) IS NULL THEN
            CONTINUE;
        END IF;
        v_trigger := 'trg_' || v_tabella || '_buy_trash_guard';
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = v_trigger
                          AND tgrelid = ('public.' || v_tabella)::regclass) THEN
            EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OF buy_request_id ON %I '
                           'FOR EACH ROW EXECUTE FUNCTION cestino_richieste_guard(%L)',
                           v_trigger, v_tabella, 'buy_request_id');
        END IF;
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_property_proposals_buy_trash_guard'
                      AND tgrelid = 'public.property_proposals'::regclass) THEN
        CREATE TRIGGER trg_property_proposals_buy_trash_guard
            BEFORE INSERT OR UPDATE OF match_id ON property_proposals
            FOR EACH ROW EXECUTE FUNCTION cestino_richieste_proposal_guard();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_buy_requests_trash_freeze'
                      AND tgrelid = 'public.buy_requests'::regclass) THEN
        CREATE TRIGGER trg_buy_requests_trash_freeze
            BEFORE UPDATE ON buy_requests
            FOR EACH ROW EXECUTE FUNCTION cestino_richieste_freeze();
    END IF;
END
$do$;

-- ===========================================================================
-- 4. Sonda finale
-- ===========================================================================
DO $do$
BEGIN
    IF (SELECT count(*) FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'buy_requests' AND is_nullable = 'YES'
           AND column_name IN ('deleted_at', 'deleted_by_user_id', 'deleted_reason')) <> 3 THEN
        RAISE EXCEPTION 'CESTINO-RICHIESTE 092: buy_requests.deleted_* missing or NOT NULL';
    END IF;
    IF EXISTS (SELECT 1 FROM buy_requests WHERE deleted_at IS NOT NULL) THEN
        RAISE EXCEPTION 'CESTINO-RICHIESTE 092: a pre-existing buy request was moved to the trash by the migration';
    END IF;
    IF (SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'trg_%_buy_trash_guard') <> 12 THEN
        RAISE EXCEPTION 'CESTINO-RICHIESTE 092: guardie dei collegamenti mancanti';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_buy_requests_trash_freeze') THEN
        RAISE EXCEPTION 'CESTINO-RICHIESTE 092: congelamento mancante';
    END IF;
END
$do$;
