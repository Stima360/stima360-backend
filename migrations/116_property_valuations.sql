-- BLOCCO-2A - le valutazioni dell'agente su un immobile, con le loro versioni.
--
-- Una valutazione appartiene a un'agenzia e a un immobile ESISTENTI (nessun
-- secondo immobile, nessun secondo incarico) e puo' esistere prima
-- dell'incarico. Metodo unico in questa versione: `valutazione_agente_v1`,
-- cioe' i valori li inserisce l'agente; nessun algoritmo automatico, nessuna
-- tabella di prezzi.
--
-- Vendita e locazione sono indipendenti: un importo NULL significa "non
-- valutato", mai zero (ogni importo presente e' > 0).
--
-- Stati: draft -> approved -> superseded. Una valutazione approvata e'
-- immutabile (trigger): l'unica modifica ammessa e' il passaggio a superseded,
-- quando un'altra versione viene approvata. Al massimo una bozza e una
-- approvata per immobile (indici parziali). Le valutazioni approvate o superate
-- non si cancellano.
--
-- Additiva: una tabella nuova, nessun backfill, nessuna riga esistente toccata.
-- La transazione e' del runner.

CREATE TABLE property_valuations (
    id BIGSERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id),
    property_id BIGINT NOT NULL REFERENCES properties(id),
    version INTEGER NOT NULL CHECK (version >= 1),
    previous_valuation_id BIGINT REFERENCES property_valuations(id),
    status VARCHAR(20) NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'approved', 'superseded')),
    method VARCHAR(40) NOT NULL DEFAULT 'valutazione_agente_v1'
        CHECK (method IN ('valutazione_agente_v1')),
    content JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(content) = 'object'),
    property_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(property_snapshot) = 'object'),
    sale_min NUMERIC(14,2) CHECK (sale_min IS NULL OR sale_min > 0),
    sale_max NUMERIC(14,2) CHECK (sale_max IS NULL OR sale_max > 0),
    sale_recommended NUMERIC(14,2) CHECK (sale_recommended IS NULL OR sale_recommended > 0),
    rent_min NUMERIC(14,2) CHECK (rent_min IS NULL OR rent_min > 0),
    rent_max NUMERIC(14,2) CHECK (rent_max IS NULL OR rent_max > 0),
    rent_recommended NUMERIC(14,2) CHECK (rent_recommended IS NULL OR rent_recommended > 0),
    created_by_user_id BIGINT NOT NULL REFERENCES operator_users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by_user_id BIGINT NOT NULL REFERENCES operator_users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_by_user_id BIGINT REFERENCES operator_users(id),
    approved_at TIMESTAMPTZ,
    superseded_at TIMESTAMPTZ,
    superseded_by_valuation_id BIGINT REFERENCES property_valuations(id),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    CONSTRAINT property_valuations_version_unq UNIQUE (property_id, version),
    CONSTRAINT property_valuations_sale_range_chk
        CHECK (sale_min IS NULL OR sale_max IS NULL OR sale_min <= sale_max),
    CONSTRAINT property_valuations_rent_range_chk
        CHECK (rent_min IS NULL OR rent_max IS NULL OR rent_min <= rent_max),
    CONSTRAINT property_valuations_state_chk CHECK (
        (status = 'draft' AND approved_by_user_id IS NULL AND approved_at IS NULL
            AND superseded_at IS NULL AND superseded_by_valuation_id IS NULL)
        OR (status = 'approved' AND approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL
            AND superseded_at IS NULL AND superseded_by_valuation_id IS NULL)
        OR (status = 'superseded' AND approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL
            AND superseded_at IS NOT NULL AND superseded_by_valuation_id IS NOT NULL)
    )
);

CREATE UNIQUE INDEX uq_property_valuations_one_approved
    ON property_valuations (property_id) WHERE status = 'approved';
CREATE UNIQUE INDEX uq_property_valuations_one_draft
    ON property_valuations (property_id) WHERE status = 'draft';
CREATE INDEX idx_property_valuations_agency_property
    ON property_valuations (agency_id, property_id, version DESC);

CREATE FUNCTION property_valuations_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status <> 'draft' THEN
            RAISE EXCEPTION 'property valuation % is % and cannot be deleted', OLD.id, OLD.status;
        END IF;
        RETURN OLD;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        -- Identita' della versione: mai modificabile, nemmeno in bozza.
        IF ROW(NEW.agency_id, NEW.property_id, NEW.version, NEW.previous_valuation_id, NEW.method,
               NEW.created_by_user_id, NEW.created_at)
           IS DISTINCT FROM
           ROW(OLD.agency_id, OLD.property_id, OLD.version, OLD.previous_valuation_id, OLD.method,
               OLD.created_by_user_id, OLD.created_at) THEN
            RAISE EXCEPTION 'property valuation % identity is immutable', OLD.id;
        END IF;
        IF OLD.status = 'draft' AND NEW.status NOT IN ('draft', 'approved') THEN
            RAISE EXCEPTION 'property valuation % cannot go from draft to %', OLD.id, NEW.status;
        END IF;
        IF OLD.status IN ('approved', 'superseded') THEN
            -- Immutabile. Unica eccezione: approved -> superseded, che tocca
            -- soltanto le colonne della sostituzione e il row_version.
            IF NOT (OLD.status = 'approved' AND NEW.status = 'superseded'
                    AND ROW(NEW.content, NEW.property_snapshot, NEW.sale_min, NEW.sale_max,
                            NEW.sale_recommended, NEW.rent_min, NEW.rent_max, NEW.rent_recommended,
                            NEW.updated_by_user_id, NEW.updated_at, NEW.approved_by_user_id, NEW.approved_at)
                        IS NOT DISTINCT FROM
                        ROW(OLD.content, OLD.property_snapshot, OLD.sale_min, OLD.sale_max,
                            OLD.sale_recommended, OLD.rent_min, OLD.rent_max, OLD.rent_recommended,
                            OLD.updated_by_user_id, OLD.updated_at, OLD.approved_by_user_id, OLD.approved_at)) THEN
                RAISE EXCEPTION 'property valuation % is % and immutable', OLD.id, OLD.status;
            END IF;
        END IF;
        RETURN NEW;
    END IF;

    -- INSERT: l'immobile e' dell'agenzia; la versione precedente e' dello stesso immobile.
    PERFORM 1 FROM properties WHERE id = NEW.property_id AND agency_id = NEW.agency_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'property valuation agency mismatch';
    END IF;
    IF NEW.status <> 'draft' THEN
        RAISE EXCEPTION 'a property valuation is created as draft';
    END IF;
    IF NEW.previous_valuation_id IS NOT NULL THEN
        PERFORM 1 FROM property_valuations
         WHERE id = NEW.previous_valuation_id AND property_id = NEW.property_id
           AND agency_id = NEW.agency_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'previous valuation belongs to another property';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_property_valuations_guard
    BEFORE INSERT OR UPDATE OR DELETE ON property_valuations
    FOR EACH ROW EXECUTE FUNCTION property_valuations_guard();
