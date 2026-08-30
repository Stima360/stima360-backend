BEGIN;

CREATE TABLE IF NOT EXISTS property_price_history (
    id BIGSERIAL PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    old_price NUMERIC(14,2),
    new_price NUMERIC(14,2),
    change_reason VARCHAR(200),
    changed_by VARCHAR(200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT property_price_history_nonnegative CHECK (
        (old_price IS NULL OR old_price >= 0) AND
        (new_price IS NULL OR new_price >= 0)
    )
);

CREATE TABLE IF NOT EXISTS property_status_history (
    id BIGSERIAL PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    field_name VARCHAR(50) NOT NULL,
    old_value VARCHAR(100),
    new_value VARCHAR(100),
    note TEXT,
    changed_by VARCHAR(200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT property_status_history_field_check CHECK (
        field_name IN ('commercial_status','classification')
    )
);

CREATE INDEX IF NOT EXISTS idx_property_price_history_property
    ON property_price_history(property_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_property_status_history_property
    ON property_status_history(property_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_property_documents_status
    ON property_documents(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_properties_mandate_end
    ON properties(mandate_end);
CREATE INDEX IF NOT EXISTS idx_property_visits_scheduled_status
    ON property_visits(scheduled_at, status);

COMMIT;
