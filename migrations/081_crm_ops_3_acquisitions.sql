-- CRM-OPS-3: il modulo Acquisizioni e la regola "ogni incarico nasce da
-- un'acquisizione".
--
-- Additiva. Crea `acquisitions` e `acquisition_events`, aggiunge
-- `properties.acquisition_id` (NULLABLE) e due trigger. Nessuna riga
-- esistente viene letta per essere riscritta, nessun backfill: gli incarichi
-- storici restano com'erano (grandfathering) e NON ricevono acquisizioni
-- artificiali.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- ---------------------------------------------------------------------------
-- COSA E' UN'ACQUISIZIONE, E COSA NON E'
--
-- Il percorso commerciale con cui l'agenzia ottiene un incarico su un
-- immobile GIA' esistente, da un proprietario GIA' collegato a quell'immobile.
-- Non e' il ponte LMC-15 (`stima_acquisitions`, migration 070), che lega una
-- stima del sito all'immobile: tabelle, pacchetto e prefisso restano distinti.
-- Non e' il lead venditore: `lead_id` e' solo un riferimento facoltativo, e i
-- due stati non si sincronizzano.
--
-- NIENTE DUPLICATI
--   * i proprietari restano in `property_contacts`: qui solo il referente
--     principale scelto (`owner_contact_id`), che deve esserci come owner o
--     seller;
--   * data, ora, durata, agente e stato dell'appuntamento restano in
--     `appointments` (l'Agenda e' la sola fonte): qui solo `appointment_id`,
--     che segue il reschedule nella transazione dell'Agenda;
--   * le note operative sono `appointments.notes`; quelle commerciali sono
--     `acquisitions.notes`. Due colonne, due tabelle, nessuna copia.
--
-- L'INCARICO
--   Non esiste un'entita' incarico: oggi e' `properties.mandate_type`,
--   `mandate_start`, `mandate_end` e `commercial_status = 'mandate'`. La
--   regola si applica a QUELLE scritture: un incarico NUOVO su un immobile
--   senza `acquisition_id` e' rifiutato dal database (trigger qui sotto) oltre
--   che dal service. "Nuovo" = un campo dell'incarico che passa a un valore
--   diverso da quello salvato, o lo stato che DIVENTA `mandate`. Rimandare
--   invariati i valori storici (property_admin lo fa a ogni salvataggio),
--   azzerarli o cambiare altri campi resta permesso.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS acquisitions (
    id                  BIGSERIAL    PRIMARY KEY,
    agency_id           BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,

    property_id         BIGINT       NOT NULL REFERENCES properties(id) ON DELETE RESTRICT,
    owner_contact_id    BIGINT       NOT NULL,
    assigned_agent_id   BIGINT       NOT NULL,
    appointment_id      BIGINT       NOT NULL,
    lead_id             BIGINT       REFERENCES leads(id) ON DELETE SET NULL,

    status              VARCHAR(30)  NOT NULL DEFAULT 'appointment_set',
    lost_reason         VARCHAR(40),
    lost_notes          TEXT,
    lost_at             TIMESTAMPTZ,
    acquired_at         TIMESTAMPTZ,

    asking_price        NUMERIC(14,2),
    valuation_price     NUMERIC(14,2),
    sale_timing         VARCHAR(30),
    source              VARCHAR(100),
    notes               TEXT,

    created_by_user_id  BIGINT       NOT NULL REFERENCES operator_users(id) ON DELETE RESTRICT,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    version             INTEGER      NOT NULL DEFAULT 1,

    CONSTRAINT acquisitions_agency_scope_unq UNIQUE (agency_id, id),
    CONSTRAINT acquisitions_appointment_unq UNIQUE (appointment_id),

    -- Tenancy strutturale: dove esiste un UNIQUE (agency_id, id) la FK e'
    -- composita, cosi' un riferimento di un'altra agenzia non e' esprimibile.
    -- `properties` e `leads` non hanno quel vincolo: li controlla il trigger.
    CONSTRAINT acquisitions_owner_same_agency_fk
        FOREIGN KEY (agency_id, owner_contact_id)
        REFERENCES contacts (agency_id, id) ON DELETE RESTRICT,
    CONSTRAINT acquisitions_agent_same_agency_fk
        FOREIGN KEY (agency_id, assigned_agent_id)
        REFERENCES agency_memberships (agency_id, operator_user_id),
    CONSTRAINT acquisitions_appointment_same_agency_fk
        FOREIGN KEY (agency_id, appointment_id)
        REFERENCES appointments (agency_id, id) ON DELETE RESTRICT,

    -- La pipeline parte dall'appuntamento (che c'e' sempre: `appointment_id`
    -- NOT NULL): appuntamento fissato -> sopralluogo effettuato -> valutazione
    -- presentata -> trattativa incarico -> acquisita | persa.
    CONSTRAINT acquisitions_status_chk CHECK (status IN (
        'appointment_set', 'inspection_done',
        'valuation_presented', 'mandate_negotiation', 'acquired', 'lost')),
    CONSTRAINT acquisitions_lost_reason_chk CHECK (lost_reason IS NULL OR lost_reason IN (
        'other_agency', 'commission', 'price_disagreement', 'owner_no_longer_selling',
        'unreachable', 'property_or_documents_issue', 'other')),
    CONSTRAINT acquisitions_sale_timing_chk CHECK (sale_timing IS NULL OR sale_timing IN (
        'immediate', 'within_3_months', 'within_6_months', 'within_12_months',
        'over_12_months', 'undecided')),
    -- `lost` porta sempre il motivo e l'istante; nessun altro stato li porta.
    CONSTRAINT acquisitions_lost_chk CHECK (
        (status = 'lost') = (lost_reason IS NOT NULL AND lost_at IS NOT NULL)),
    CONSTRAINT acquisitions_lost_notes_chk CHECK (lost_notes IS NULL OR status = 'lost'),
    CONSTRAINT acquisitions_acquired_chk CHECK ((status = 'acquired') = (acquired_at IS NOT NULL)),
    CONSTRAINT acquisitions_prices_chk CHECK (
        (asking_price IS NULL OR asking_price >= 0)
        AND (valuation_price IS NULL OR valuation_price >= 0))
);

CREATE TABLE IF NOT EXISTS acquisition_events (
    id              BIGSERIAL    PRIMARY KEY,
    agency_id       BIGINT       NOT NULL,
    acquisition_id  BIGINT       NOT NULL,
    event_type      VARCHAR(30)  NOT NULL,
    from_status     VARCHAR(30),
    to_status       VARCHAR(30)  NOT NULL,
    actor_user_id   BIGINT       REFERENCES operator_users(id) ON DELETE RESTRICT,
    db_user         TEXT         NOT NULL DEFAULT CURRENT_USER,
    occurred_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    changes         JSONB        NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT acquisition_events_acquisition_fk
        FOREIGN KEY (agency_id, acquisition_id)
        REFERENCES acquisitions (agency_id, id) ON DELETE RESTRICT,
    CONSTRAINT acquisition_events_type_chk CHECK (event_type IN (
        'created', 'updated', 'status_changed', 'lost', 'mandate_created',
        'appointment_rescheduled', 'appointment_completed', 'appointment_no_show',
        'appointment_cancelled', 'appointment_replaced'))
);

ALTER TABLE properties ADD COLUMN IF NOT EXISTS acquisition_id BIGINT;

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'properties_acquisition_same_agency_fk'
                      AND conrelid = 'properties'::regclass) THEN
        ALTER TABLE properties
            ADD CONSTRAINT properties_acquisition_same_agency_fk
            FOREIGN KEY (agency_id, acquisition_id)
            REFERENCES acquisitions (agency_id, id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'properties_acquisition_unq'
                      AND conrelid = 'properties'::regclass) THEN
        -- Un'acquisizione genera al massimo UN incarico.
        ALTER TABLE properties
            ADD CONSTRAINT properties_acquisition_unq UNIQUE (acquisition_id);
    END IF;
END
$do$;

-- Una sola acquisizione APERTA per immobile: `acquired` e `lost` sono
-- terminali e non contano.
CREATE UNIQUE INDEX IF NOT EXISTS idx_acquisitions_open_property
    ON acquisitions (property_id)
    WHERE status NOT IN ('acquired', 'lost');
CREATE INDEX IF NOT EXISTS idx_acquisitions_agency_status
    ON acquisitions (agency_id, status);
CREATE INDEX IF NOT EXISTS idx_acquisitions_agency_agent
    ON acquisitions (agency_id, assigned_agent_id);
CREATE INDEX IF NOT EXISTS idx_acquisition_events_acquisition
    ON acquisition_events (acquisition_id, occurred_at);

-- ---------------------------------------------------------------------------
-- Integrita' dell'acquisizione (cio' che una FK non sa dire).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION acquisitions_guard() RETURNS trigger AS $fn$
DECLARE
    v_agency BIGINT;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.agency_id IS DISTINCT FROM OLD.agency_id
           OR NEW.property_id IS DISTINCT FROM OLD.property_id
           OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
           OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'CRM-OPS-3: acquisitions agency/property/creation fields are immutable (id=%)', OLD.id;
        END IF;
        IF OLD.status IN ('acquired', 'lost') AND NEW.status IS DISTINCT FROM OLD.status THEN
            RAISE EXCEPTION 'CRM-OPS-3: acquisition % is terminal (%)', OLD.id, OLD.status;
        END IF;
    END IF;

    SELECT agency_id INTO v_agency FROM properties WHERE id = NEW.property_id;
    IF v_agency IS DISTINCT FROM NEW.agency_id THEN
        RAISE EXCEPTION 'CRM-OPS-3 tenancy: property % does not belong to agency %',
            NEW.property_id, NEW.agency_id;
    END IF;

    IF NEW.lead_id IS NOT NULL THEN
        SELECT agency_id INTO v_agency FROM leads WHERE id = NEW.lead_id;
        IF v_agency IS DISTINCT FROM NEW.agency_id THEN
            RAISE EXCEPTION 'CRM-OPS-3 tenancy: lead % does not belong to agency %',
                NEW.lead_id, NEW.agency_id;
        END IF;
    END IF;

    -- Il referente principale e' un proprietario REALE dell'immobile.
    IF TG_OP = 'INSERT' OR NEW.owner_contact_id IS DISTINCT FROM OLD.owner_contact_id THEN
        IF NOT EXISTS (SELECT 1 FROM property_contacts
                        WHERE property_id = NEW.property_id
                          AND contact_id = NEW.owner_contact_id
                          AND role IN ('owner', 'seller')) THEN
            RAISE EXCEPTION 'CRM-OPS-3: contact % is not an owner/seller of property %',
                NEW.owner_contact_id, NEW.property_id;
        END IF;
    END IF;

    -- `acquired` significa "incarico generato": senza l'immobile che punta a
    -- questa acquisizione, lo stato non si raggiunge.
    IF NEW.status = 'acquired' AND (TG_OP = 'INSERT' OR OLD.status <> 'acquired') THEN
        IF NOT EXISTS (SELECT 1 FROM properties
                        WHERE id = NEW.property_id AND acquisition_id = NEW.id) THEN
            RAISE EXCEPTION 'CRM-OPS-3: acquisition % cannot become acquired without its mandate', NEW.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION acquisitions_refuse_delete() RETURNS trigger AS $fn$
BEGIN
    RAISE EXCEPTION 'CRM-OPS-3: DELETE on acquisitions is refused (id=%). Mark it lost instead.', OLD.id;
END;
$fn$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION acquisition_events_append_only() RETURNS trigger AS $fn$
BEGIN
    RAISE EXCEPTION 'CRM-OPS-3: acquisition_events is append-only (% refused, id=%)', TG_OP, OLD.id;
END;
$fn$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- La regola: nessun NUOVO incarico senza acquisizione.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION properties_mandate_origin_guard() RETURNS trigger AS $fn$
DECLARE
    v_new_mandate BOOLEAN;
    v_acq_property BIGINT;
BEGIN
    IF TG_OP = 'INSERT' THEN
        v_new_mandate := NEW.mandate_type IS NOT NULL OR NEW.mandate_start IS NOT NULL
                         OR NEW.mandate_end IS NOT NULL OR NEW.commercial_status = 'mandate';
    ELSE
        -- L'origine non si scollega e non si cambia.
        IF OLD.acquisition_id IS NOT NULL
           AND NEW.acquisition_id IS DISTINCT FROM OLD.acquisition_id THEN
            RAISE EXCEPTION 'CRM-OPS-3: properties.acquisition_id is permanent (property %)', OLD.id;
        END IF;
        v_new_mandate :=
               (NEW.mandate_type IS NOT NULL AND NEW.mandate_type IS DISTINCT FROM OLD.mandate_type)
            OR (NEW.mandate_start IS NOT NULL AND NEW.mandate_start IS DISTINCT FROM OLD.mandate_start)
            OR (NEW.mandate_end IS NOT NULL AND NEW.mandate_end IS DISTINCT FROM OLD.mandate_end)
            OR (NEW.commercial_status = 'mandate' AND OLD.commercial_status IS DISTINCT FROM 'mandate');
    END IF;

    IF v_new_mandate AND NEW.acquisition_id IS NULL THEN
        RAISE EXCEPTION 'CRM-OPS-3: a mandate can only be generated from an acquisition (property %)',
            NEW.id
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.acquisition_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR OLD.acquisition_id IS NULL) THEN
        SELECT property_id INTO v_acq_property FROM acquisitions WHERE id = NEW.acquisition_id;
        IF v_acq_property IS DISTINCT FROM NEW.id THEN
            RAISE EXCEPTION 'CRM-OPS-3: acquisition % belongs to another property', NEW.acquisition_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_acquisitions_guard'
                    AND tgrelid = 'public.acquisitions'::regclass) THEN
        CREATE TRIGGER trg_acquisitions_guard
            BEFORE INSERT OR UPDATE ON acquisitions
            FOR EACH ROW EXECUTE FUNCTION acquisitions_guard();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_acquisitions_refuse_delete'
                    AND tgrelid = 'public.acquisitions'::regclass) THEN
        CREATE TRIGGER trg_acquisitions_refuse_delete
            BEFORE DELETE ON acquisitions
            FOR EACH ROW EXECUTE FUNCTION acquisitions_refuse_delete();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_acquisition_events_append_only'
                    AND tgrelid = 'public.acquisition_events'::regclass) THEN
        CREATE TRIGGER trg_acquisition_events_append_only
            BEFORE UPDATE OR DELETE ON acquisition_events
            FOR EACH ROW EXECUTE FUNCTION acquisition_events_append_only();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_properties_mandate_origin'
                    AND tgrelid = 'public.properties'::regclass) THEN
        CREATE TRIGGER trg_properties_mandate_origin
            BEFORE INSERT OR UPDATE ON properties
            FOR EACH ROW EXECUTE FUNCTION properties_mandate_origin_guard();
    END IF;
END
$do$;

-- Sonda finale.
DO $do$
BEGIN
    IF (SELECT count(*) FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'properties'
           AND column_name = 'acquisition_id' AND is_nullable = 'YES') <> 1 THEN
        RAISE EXCEPTION 'CRM-OPS-3 081: properties.acquisition_id missing or NOT NULL';
    END IF;
    IF (SELECT count(*) FROM pg_trigger
         WHERE tgname IN ('trg_acquisitions_guard', 'trg_acquisitions_refuse_delete',
                          'trg_acquisition_events_append_only', 'trg_properties_mandate_origin')
           AND NOT tgisinternal) <> 4 THEN
        RAISE EXCEPTION 'CRM-OPS-3 081: triggers missing';
    END IF;
END
$do$;
