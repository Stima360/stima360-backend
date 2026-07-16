BEGIN;
CREATE TABLE buy_requests (
 id BIGSERIAL PRIMARY KEY,
 contact_id BIGINT NOT NULL REFERENCES contacts(id) ON DELETE RESTRICT,
 lead_id BIGINT REFERENCES leads(id) ON DELETE SET NULL,
 title VARCHAR(200) NOT NULL,
 status VARCHAR(30) NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','active','paused','satisfied','closed','archived')),
 priority VARCHAR(20) NOT NULL DEFAULT 'normal' CHECK(priority IN ('low','normal','high','urgent')),
 urgency VARCHAR(30) NOT NULL DEFAULT 'flexible' CHECK(urgency IN ('exploratory','flexible','within_6_months','within_3_months','immediate')),
 assigned_to VARCHAR(200), search_start_date DATE, target_purchase_date DATE,
 budget_min NUMERIC(14,2) CHECK(budget_min>=0), budget_target NUMERIC(14,2) CHECK(budget_target>=0), budget_max NUMERIC(14,2) CHECK(budget_max>=0),
 budget_flexibility_percent NUMERIC(5,2) NOT NULL DEFAULT 0 CHECK(budget_flexibility_percent BETWEEN 0 AND 100),
 includes_agency_fees BOOLEAN NOT NULL DEFAULT FALSE, includes_renovation BOOLEAN NOT NULL DEFAULT FALSE,
 finance_status VARCHAR(40) NOT NULL DEFAULT 'unknown' CHECK(finance_status IN ('unknown','cash','mortgage_to_assess','mortgage_in_progress','mortgage_preapproved','sale_dependent')),
 mortgage_required BOOLEAN, mortgage_preapproved BOOLEAN, available_cash NUMERIC(14,2) CHECK(available_cash>=0), maximum_monthly_payment NUMERIC(12,2) CHECK(maximum_monthly_payment>=0), property_to_sell_first BOOLEAN NOT NULL DEFAULT FALSE,
 surface_min NUMERIC(10,2) CHECK(surface_min>=0), surface_target NUMERIC(10,2) CHECK(surface_target>=0), surface_max NUMERIC(10,2) CHECK(surface_max>=0),
 rooms_min INTEGER CHECK(rooms_min>=0), bedrooms_min INTEGER CHECK(bedrooms_min>=0), bathrooms_min INTEGER CHECK(bathrooms_min>=0),
 notes TEXT, metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
 match_relevant_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), archived_at TIMESTAMPTZ,
 CHECK(budget_min IS NULL OR budget_target IS NULL OR budget_min<=budget_target),
 CHECK(budget_target IS NULL OR budget_max IS NULL OR budget_target<=budget_max),
 CHECK(surface_min IS NULL OR surface_target IS NULL OR surface_min<=surface_target),
 CHECK(surface_target IS NULL OR surface_max IS NULL OR surface_target<=surface_max)
);
CREATE TABLE buy_request_locations (
 id BIGSERIAL PRIMARY KEY,buy_request_id BIGINT NOT NULL REFERENCES buy_requests(id) ON DELETE CASCADE,
 location_type VARCHAR(30) NOT NULL CHECK(location_type IN ('region','province','municipality','microzone','radius')),
 region VARCHAR(120),province VARCHAR(120),municipality VARCHAR(120),microzone VARCHAR(150),priority INTEGER NOT NULL DEFAULT 1 CHECK(priority BETWEEN 1 AND 10),radius_km NUMERIC(8,2) CHECK(radius_km>=0),is_required BOOLEAN NOT NULL DEFAULT FALSE,is_excluded BOOLEAN NOT NULL DEFAULT FALSE,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 CHECK(NOT(is_required AND is_excluded)), CHECK(region IS NOT NULL OR province IS NOT NULL OR municipality IS NOT NULL OR microzone IS NOT NULL)
);
CREATE TABLE buy_request_typologies (
 id BIGSERIAL PRIMARY KEY,buy_request_id BIGINT NOT NULL REFERENCES buy_requests(id) ON DELETE CASCADE,property_type VARCHAR(80) NOT NULL,
 requirement_level VARCHAR(20) NOT NULL DEFAULT 'preferred' CHECK(requirement_level IN ('required','preferred','optional','excluded')),priority INTEGER NOT NULL DEFAULT 1 CHECK(priority BETWEEN 1 AND 10),created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),UNIQUE(buy_request_id,property_type)
);
CREATE TABLE buy_request_features (
 id BIGSERIAL PRIMARY KEY,buy_request_id BIGINT NOT NULL REFERENCES buy_requests(id) ON DELETE CASCADE,feature_code VARCHAR(100) NOT NULL,
 requirement_level VARCHAR(20) NOT NULL DEFAULT 'preferred' CHECK(requirement_level IN ('required','preferred','optional','excluded')),
 value_type VARCHAR(20) NOT NULL DEFAULT 'boolean' CHECK(value_type IN ('boolean','number','range','text')),
 value_boolean BOOLEAN,value_min NUMERIC(14,2),value_target NUMERIC(14,2),value_max NUMERIC(14,2),value_text TEXT,weight_override NUMERIC(5,2) CHECK(weight_override BETWEEN 0 AND 100),created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 CHECK(value_min IS NULL OR value_max IS NULL OR value_min<=value_max),UNIQUE(buy_request_id,feature_code)
);
CREATE INDEX idx_buy_requests_contact ON buy_requests(contact_id);CREATE INDEX idx_buy_requests_lead ON buy_requests(lead_id);CREATE INDEX idx_buy_requests_status ON buy_requests(status);CREATE INDEX idx_buy_requests_priority ON buy_requests(priority);CREATE INDEX idx_buy_requests_match_updated ON buy_requests(match_relevant_updated_at);CREATE INDEX idx_buy_locations_request ON buy_request_locations(buy_request_id);CREATE INDEX idx_buy_typologies_request ON buy_request_typologies(buy_request_id);CREATE INDEX idx_buy_features_request ON buy_request_features(buy_request_id);
COMMIT;
