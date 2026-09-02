BEGIN;

-- P20-A Property Watch is isolated from official agency properties: it watches
-- a public valuation without creating or modifying a row in properties.
-- SET NULL preserves a watch's historical record if legacy stime data is
-- removed. Observations instead RESTRICT deletion of their watch, because an
-- append-only log must not be silently discarded through its parent.
CREATE TABLE IF NOT EXISTS property_watches (
    id BIGSERIAL PRIMARY KEY,
    stima_id INTEGER REFERENCES stime(id) ON DELETE SET NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One logical watch for each existing valuation. The partial form permits
-- historical rows whose stima_id was nulled by the FK's ON DELETE SET NULL.
CREATE UNIQUE INDEX IF NOT EXISTS idx_property_watches_stima_id
    ON property_watches (stima_id)
    WHERE stima_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS property_watch_observations (
    id BIGSERIAL PRIMARY KEY,
    watch_id BIGINT NOT NULL REFERENCES property_watches(id) ON DELETE RESTRICT,
    observation_type VARCHAR(100) NOT NULL,
    source VARCHAR(100) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key VARCHAR(300) NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Every P20-A observation is internally generated and has a natural,
-- deterministic identity. Later providers can use their provider event ID.
CREATE UNIQUE INDEX IF NOT EXISTS idx_property_watch_observations_idempotency_key
    ON property_watch_observations (idempotency_key);
CREATE INDEX IF NOT EXISTS idx_property_watch_observations_watch_id
    ON property_watch_observations (watch_id);
CREATE INDEX IF NOT EXISTS idx_property_watch_observations_type
    ON property_watch_observations (observation_type);
CREATE INDEX IF NOT EXISTS idx_property_watch_observations_observed_at
    ON property_watch_observations (observed_at ASC);
CREATE INDEX IF NOT EXISTS idx_property_watch_observations_source
    ON property_watch_observations (source);

COMMIT;
