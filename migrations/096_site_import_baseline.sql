-- Phase 4: immutable source snapshot; no existing CRM records changed.
CREATE TABLE site_import_baselines (
    source VARCHAR(40) PRIMARY KEY CHECK (source ~ '^[a-z][a-z0-9_]{1,39}$'),
    ids JSONB NOT NULL CHECK (jsonb_typeof(ids) = 'object'
                         AND ids ?& ARRAY['stime', 'stime_dettagliate']
                         AND jsonb_typeof(ids->'stime') = 'array'
                         AND jsonb_typeof(ids->'stime_dettagliate') = 'array'),
    initialized_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
