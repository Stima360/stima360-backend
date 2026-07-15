BEGIN;

CREATE TABLE IF NOT EXISTS properties (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(50) UNIQUE,
    title VARCHAR(200) NOT NULL,
    property_type VARCHAR(50) NOT NULL DEFAULT 'apartment',
    commercial_status VARCHAR(50) NOT NULL DEFAULT 'draft',
    classification CHAR(1),
    address VARCHAR(250),
    civic_number VARCHAR(30),
    city VARCHAR(120),
    province VARCHAR(10),
    postal_code VARCHAR(20),
    microzone VARCHAR(150),
    latitude NUMERIC(10,7),
    longitude NUMERIC(10,7),
    surface_sqm NUMERIC(10,2),
    commercial_surface_sqm NUMERIC(10,2),
    rooms INTEGER,
    bedrooms INTEGER,
    bathrooms INTEGER,
    floor VARCHAR(50),
    total_floors INTEGER,
    elevator BOOLEAN,
    year_built INTEGER,
    condition VARCHAR(80),
    energy_class VARCHAR(20),
    asking_price NUMERIC(14,2),
    minimum_price NUMERIC(14,2),
    mandate_type VARCHAR(80),
    mandate_start DATE,
    mandate_end DATE,
    assigned_to VARCHAR(200),
    source VARCHAR(100),
    public_notes TEXT,
    internal_notes TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at TIMESTAMPTZ,
    CONSTRAINT properties_classification_check CHECK (classification IS NULL OR classification IN ('A','B','C')),
    CONSTRAINT properties_status_check CHECK (commercial_status IN ('draft','evaluation','mandate','active','reserved','under_offer','sold','withdrawn','archived')),
    CONSTRAINT properties_surface_check CHECK (surface_sqm IS NULL OR surface_sqm >= 0),
    CONSTRAINT properties_price_check CHECK (asking_price IS NULL OR asking_price >= 0)
);

CREATE TABLE IF NOT EXISTS property_contacts (
    id BIGSERIAL PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    contact_id BIGINT NOT NULL REFERENCES contacts(id) ON DELETE RESTRICT,
    role VARCHAR(50) NOT NULL DEFAULT 'owner',
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    ownership_share NUMERIC(5,2),
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(property_id, contact_id, role),
    CONSTRAINT property_contacts_role_check CHECK (role IN ('owner','seller','tenant','contact','professional','other')),
    CONSTRAINT ownership_share_check CHECK (ownership_share IS NULL OR (ownership_share >= 0 AND ownership_share <= 100))
);

CREATE TABLE IF NOT EXISTS property_leads (
    id BIGSERIAL PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    lead_id BIGINT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    relation_type VARCHAR(50) NOT NULL DEFAULT 'origin',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(property_id, lead_id),
    CONSTRAINT property_leads_relation_check CHECK (relation_type IN ('origin','seller','buyer_interest','related','follow_up'))
);

CREATE TABLE IF NOT EXISTS property_documents (
    id BIGSERIAL PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    document_type VARCHAR(80) NOT NULL,
    title VARCHAR(200) NOT NULL,
    url TEXT,
    storage_key TEXT,
    status VARCHAR(40) NOT NULL DEFAULT 'available',
    expires_at DATE,
    notes TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT property_documents_status_check CHECK (status IN ('missing','requested','available','expired','rejected','archived')),
    CONSTRAINT property_documents_location_check CHECK (url IS NOT NULL OR storage_key IS NOT NULL OR status IN ('missing','requested'))
);

CREATE TABLE IF NOT EXISTS property_photos (
    id BIGSERIAL PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    title VARCHAR(200),
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_cover BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT property_photos_sort_check CHECK (sort_order >= 0)
);

CREATE TABLE IF NOT EXISTS property_visits (
    id BIGSERIAL PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    contact_id BIGINT REFERENCES contacts(id) ON DELETE SET NULL,
    lead_id BIGINT REFERENCES leads(id) ON DELETE SET NULL,
    scheduled_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(40) NOT NULL DEFAULT 'scheduled',
    outcome VARCHAR(80),
    feedback TEXT,
    rating INTEGER,
    assigned_to VARCHAR(200),
    created_by VARCHAR(200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT property_visits_status_check CHECK (status IN ('scheduled','confirmed','completed','cancelled','no_show')),
    CONSTRAINT property_visits_rating_check CHECK (rating IS NULL OR (rating >= 1 AND rating <= 5))
);

CREATE INDEX IF NOT EXISTS idx_properties_status ON properties(commercial_status);
CREATE INDEX IF NOT EXISTS idx_properties_classification ON properties(classification);
CREATE INDEX IF NOT EXISTS idx_properties_city ON properties(city);
CREATE INDEX IF NOT EXISTS idx_properties_updated_at ON properties(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_property_contacts_property ON property_contacts(property_id);
CREATE INDEX IF NOT EXISTS idx_property_contacts_contact ON property_contacts(contact_id);
CREATE INDEX IF NOT EXISTS idx_property_leads_property ON property_leads(property_id);
CREATE INDEX IF NOT EXISTS idx_property_leads_lead ON property_leads(lead_id);
CREATE INDEX IF NOT EXISTS idx_property_documents_property ON property_documents(property_id);
CREATE INDEX IF NOT EXISTS idx_property_photos_property ON property_photos(property_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_property_visits_property ON property_visits(property_id, scheduled_at DESC);
CREATE INDEX IF NOT EXISTS idx_property_visits_lead ON property_visits(lead_id);

COMMIT;
