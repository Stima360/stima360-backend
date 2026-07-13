BEGIN;

CREATE TABLE IF NOT EXISTS contacts (
    id BIGSERIAL PRIMARY KEY,
    contact_type VARCHAR(20) NOT NULL DEFAULT 'person',
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    company_name VARCHAR(200),
    display_name VARCHAR(200),
    email VARCHAR(320),
    email_normalized VARCHAR(320),
    phone VARCHAR(50),
    phone_normalized VARCHAR(50),
    secondary_phone VARCHAR(50),
    source VARCHAR(100),
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    marketing_consent BOOLEAN,
    marketing_consent_at TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at TIMESTAMPTZ,
    CONSTRAINT contacts_type_chk CHECK (contact_type IN ('person', 'company')),
    CONSTRAINT contacts_status_chk CHECK (status IN ('active', 'inactive', 'archived')),
    CONSTRAINT contacts_identity_chk CHECK (
        (contact_type = 'company' AND company_name IS NOT NULL AND BTRIM(company_name) <> '')
        OR
        (contact_type = 'person' AND (
            (first_name IS NOT NULL AND BTRIM(first_name) <> '') OR
            (last_name IS NOT NULL AND BTRIM(last_name) <> '') OR
            (display_name IS NOT NULL AND BTRIM(display_name) <> '')
        ))
    )
);

CREATE TABLE IF NOT EXISTS contact_roles (
    id BIGSERIAL PRIMARY KEY,
    contact_id BIGINT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    role VARCHAR(30) NOT NULL,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    valid_from TIMESTAMPTZ,
    valid_to TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT contact_roles_role_chk CHECK (role IN (
        'owner', 'seller', 'buyer', 'prospect', 'referrer', 'agency', 'professional', 'other'
    )),
    CONSTRAINT contact_roles_dates_chk CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
    CONSTRAINT contact_roles_unq UNIQUE (contact_id, role)
);

CREATE TABLE IF NOT EXISTS leads (
    id BIGSERIAL PRIMARY KEY,
    contact_id BIGINT NOT NULL REFERENCES contacts(id) ON DELETE RESTRICT,
    source VARCHAR(100),
    pipeline VARCHAR(20) NOT NULL DEFAULT 'general',
    stage VARCHAR(30) NOT NULL DEFAULT 'new',
    priority VARCHAR(20) NOT NULL DEFAULT 'normal',
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    assigned_to VARCHAR(200),
    estimated_value NUMERIC(14,2),
    next_action_at TIMESTAMPTZ,
    lost_reason TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ,
    CONSTRAINT leads_pipeline_chk CHECK (pipeline IN ('sell', 'buy', 'general')),
    CONSTRAINT leads_stage_chk CHECK (stage IN ('new', 'contacted', 'qualified', 'appointment', 'proposal', 'won', 'lost')),
    CONSTRAINT leads_priority_chk CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    CONSTRAINT leads_status_chk CHECK (status IN ('open', 'paused', 'closed')),
    CONSTRAINT leads_estimated_value_chk CHECK (estimated_value IS NULL OR estimated_value >= 0)
);

CREATE TABLE IF NOT EXISTS lead_stime (
    id BIGSERIAL PRIMARY KEY,
    lead_id BIGINT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    stima_id INTEGER NOT NULL REFERENCES stime(id) ON DELETE CASCADE,
    relation_type VARCHAR(20) NOT NULL DEFAULT 'related',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT lead_stime_relation_chk CHECK (relation_type IN ('origin', 'related', 'follow_up')),
    CONSTRAINT lead_stime_unq UNIQUE (lead_id, stima_id)
);

CREATE TABLE IF NOT EXISTS activities (
    id BIGSERIAL PRIMARY KEY,
    contact_id BIGINT REFERENCES contacts(id) ON DELETE SET NULL,
    lead_id BIGINT REFERENCES leads(id) ON DELETE SET NULL,
    stima_id INTEGER REFERENCES stime(id) ON DELETE CASCADE,
    activity_type VARCHAR(30) NOT NULL,
    direction VARCHAR(20),
    channel VARCHAR(50),
    subject VARCHAR(200),
    description TEXT,
    outcome VARCHAR(100),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by VARCHAR(200),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT activities_type_chk CHECK (activity_type IN (
        'note', 'call', 'email', 'whatsapp', 'meeting', 'valuation', 'status_change', 'system'
    )),
    CONSTRAINT activities_direction_chk CHECK (direction IS NULL OR direction IN ('in', 'out', 'internal')),
    CONSTRAINT activities_reference_chk CHECK (
        contact_id IS NOT NULL OR lead_id IS NOT NULL OR stima_id IS NOT NULL
    )
);

CREATE TABLE IF NOT EXISTS tasks (
    id BIGSERIAL PRIMARY KEY,
    contact_id BIGINT REFERENCES contacts(id) ON DELETE SET NULL,
    lead_id BIGINT REFERENCES leads(id) ON DELETE SET NULL,
    stima_id INTEGER REFERENCES stime(id) ON DELETE CASCADE,
    title VARCHAR(200) NOT NULL,
    description TEXT,
    task_type VARCHAR(50),
    priority VARCHAR(20) NOT NULL DEFAULT 'normal',
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    due_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    assigned_to VARCHAR(200),
    created_by VARCHAR(200),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT tasks_title_chk CHECK (BTRIM(title) <> ''),
    CONSTRAINT tasks_priority_chk CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    CONSTRAINT tasks_status_chk CHECK (status IN ('open', 'in_progress', 'completed', 'cancelled')),
    CONSTRAINT tasks_reference_chk CHECK (
        contact_id IS NOT NULL OR lead_id IS NOT NULL OR stima_id IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS idx_contacts_email_normalized ON contacts(email_normalized);
CREATE INDEX IF NOT EXISTS idx_contacts_phone_normalized ON contacts(phone_normalized);
CREATE INDEX IF NOT EXISTS idx_contacts_status ON contacts(status);
CREATE INDEX IF NOT EXISTS idx_contact_roles_contact_id ON contact_roles(contact_id);
CREATE INDEX IF NOT EXISTS idx_contact_roles_role ON contact_roles(role);
CREATE INDEX IF NOT EXISTS idx_leads_contact_id ON leads(contact_id);
CREATE INDEX IF NOT EXISTS idx_leads_pipeline ON leads(pipeline);
CREATE INDEX IF NOT EXISTS idx_leads_stage ON leads(stage);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_next_action_at ON leads(next_action_at);
CREATE INDEX IF NOT EXISTS idx_lead_stime_lead_id ON lead_stime(lead_id);
CREATE INDEX IF NOT EXISTS idx_lead_stime_stima_id ON lead_stime(stima_id);
CREATE INDEX IF NOT EXISTS idx_activities_contact_id ON activities(contact_id);
CREATE INDEX IF NOT EXISTS idx_activities_lead_id ON activities(lead_id);
CREATE INDEX IF NOT EXISTS idx_activities_stima_id ON activities(stima_id);
CREATE INDEX IF NOT EXISTS idx_activities_occurred_at ON activities(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_contact_id ON tasks(contact_id);
CREATE INDEX IF NOT EXISTS idx_tasks_lead_id ON tasks(lead_id);
CREATE INDEX IF NOT EXISTS idx_tasks_stima_id ON tasks(stima_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_due_at ON tasks(due_at);

COMMIT;
