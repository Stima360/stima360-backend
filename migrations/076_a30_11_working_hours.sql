-- 076 - A30-11: ORARI DI LAVORO / AVAILABILITY. Il vincolo SOFT nel CRM
-- (D2): non e' una quarta protezione sugli appointments, e' l'ingresso di
-- verita' per availability_check/alternatives e, in futuro (A30-12), per
-- l'enforcement HARD del booking pubblico.
--
-- ADDITIVA. Tre tabelle nuove, tre funzioni di guardia, tre trigger, gli
-- indici. NON tocca `appointments`, `appointment_events`, ne'
-- `appointment_calendar_sync`: nessuna colonna aggiunta, nessun vincolo
-- esistente modificato. Il vincolo EXCLUDE di 072, `lock_agents` e
-- `find_conflicts` restano esattamente come sono (D2).
--
-- LE TRE TABELLE
--
--   agent_working_hours          l'orario settimanale RICORRENTE di un
--                                 agente: righe indipendenti per fascia, non
--                                 una colonna per fascia (piu' fasce nello
--                                 stesso giorno = piu' righe, es. pausa
--                                 pranzo).
--   agent_availability_exceptions  un'eccezione puntuale per un agente e una
--                                 data: assenza/ferie (is_available=false) o
--                                 apertura straordinaria (is_available=true).
--   agency_closures               una chiusura dell'intera agenzia per una
--                                 data (D5): non si duplica su ogni agente.
--
-- PRECEDENZA (D4), calcolata dal domain puro (appointments/working_hours.py),
-- MAI qui: effective = (orario settimanale UNITO alle aperture straordinarie)
-- MENO le assenze MENO le chiusure agenzia. Le chiusure vincono sempre.
--
-- LEGACY (D1): un agente SENZA alcuna riga in agent_working_hours e' fuori
-- da questo intero meccanismo (nessun vincolo, comportamento D7 di prima di
-- A30-11, invariato): lo decide il domain puro guardando se esiste almeno
-- una riga per lui, non questa migration.
--
-- TENANT
--
-- Stesso pattern di `appointments` (072): la FK composita
-- `(agency_id, user_id) REFERENCES agency_memberships (agency_id,
-- operator_user_id)` impone che lo stia scrivendo un membro DI QUESTA
-- agenzia; che la membership sia ATTIVA lo verifica il trigger quando la
-- riga viene scritta (stessa forma della guardia di 072 per
-- `assigned_user_id`). Un riferimento cross-tenant e' irrappresentabile.
--
-- SOVRAPPOSIZIONI
--
-- Ogni tabella impedisce a livello di database le proprie sovrapposizioni,
-- con la stessa garanzia GiST di 072 (`btree_gist`, gia' installata dalla
-- 072: qui non serve rieseguire `CREATE EXTENSION`). Intervalli semiaperti
-- `[start_minute, end_minute)` in minuti dalla mezzanotte locale (0..1440):
-- nessuna fascia attraversa la mezzanotte (D7 del gate A30-11B); una fascia
-- 22:00-02:00 e' due righe.
--
-- La transazione e la riga in `schema_migrations` le gestisce il runner P26:
-- questo file non apre ne' chiude una transazione.

-- ---------------------------------------------------------------------------
-- agent_working_hours
-- ---------------------------------------------------------------------------

CREATE TABLE agent_working_hours (
    id              BIGSERIAL    PRIMARY KEY,
    agency_id       BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    user_id         BIGINT       NOT NULL,

    -- ISO: 1 = lunedi' .. 7 = domenica. Stessa convenzione di
    -- `communication/send_window.py` (`days`).
    day_of_week     SMALLINT     NOT NULL,

    -- Minuti dalla mezzanotte LOCALE (Europe/Rome), intervallo semiaperto
    -- [start_minute, end_minute). 1440 = 24:00, il limite superiore di una
    -- fascia che arriva fino a fine giornata.
    start_minute    SMALLINT     NOT NULL,
    end_minute      SMALLINT     NOT NULL,

    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT agent_working_hours_agent_same_agency_fk
        FOREIGN KEY (agency_id, user_id)
        REFERENCES agency_memberships (agency_id, operator_user_id),

    CONSTRAINT agent_working_hours_day_chk
        CHECK (day_of_week BETWEEN 1 AND 7),

    CONSTRAINT agent_working_hours_minutes_chk CHECK (
        start_minute >= 0 AND start_minute < 1440
        AND end_minute >= 1 AND end_minute <= 1440
        AND end_minute > start_minute
    ),

    -- La garanzia finale (D-076 del gate): nessuna fascia sovrapposta per lo
    -- stesso agente, nello stesso giorno della settimana. Intervalli
    -- adiacenti (10:00-13:00 e 13:00-18:00) NON sono in conflitto: '[)'.
    CONSTRAINT agent_working_hours_no_overlap_excl EXCLUDE USING gist (
        agency_id WITH =,
        user_id WITH =,
        day_of_week WITH =,
        int4range(start_minute, end_minute, '[)') WITH &&
    )
);

CREATE INDEX idx_agent_working_hours_lookup
    ON agent_working_hours (agency_id, user_id, day_of_week);

-- ---------------------------------------------------------------------------
-- agent_availability_exceptions
-- ---------------------------------------------------------------------------

CREATE TABLE agent_availability_exceptions (
    id              BIGSERIAL    PRIMARY KEY,
    agency_id       BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    user_id         BIGINT       NOT NULL,

    exception_date  DATE         NOT NULL,

    -- Stessi minuti locali di agent_working_hours. FULL DAY: 0..1440.
    start_minute    SMALLINT     NOT NULL,
    end_minute      SMALLINT     NOT NULL,

    -- false = assenza/ferie/chiusura individuale; true = apertura
    -- straordinaria (D4). Il domain puro applica la precedenza; qui si
    -- registra solo il fatto.
    is_available    BOOLEAN      NOT NULL,
    reason_code     VARCHAR(40),

    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT agent_availability_exceptions_agent_same_agency_fk
        FOREIGN KEY (agency_id, user_id)
        REFERENCES agency_memberships (agency_id, operator_user_id),

    CONSTRAINT agent_availability_exceptions_minutes_chk CHECK (
        start_minute >= 0 AND start_minute < 1440
        AND end_minute >= 1 AND end_minute <= 1440
        AND end_minute > start_minute
    ),

    -- Niente ambiguita' fra righe della stessa data/agente (gate A30-11B:
    -- "nessuna sovrapposizione fra exception rows; chi vuole combinare
    -- divide la giornata in intervalli distinti"). Positive e negative sulla
    -- stessa data possono comunque coesistere su fasce DIVERSE (es. ferie
    -- 00:00-12:00 negative e apertura straordinaria 14:00-16:00 positive):
    -- l'EXCLUDE non distingue is_available, quindi resta comunque un solo
    -- intervallo per quella fascia di quella data - la lettura del
    -- significato (quale prevale) resta del domain puro, non della riga.
    CONSTRAINT agent_availability_exceptions_no_overlap_excl EXCLUDE USING gist (
        agency_id WITH =,
        user_id WITH =,
        exception_date WITH =,
        int4range(start_minute, end_minute, '[)') WITH &&
    )
);

CREATE INDEX idx_agent_availability_exceptions_lookup
    ON agent_availability_exceptions (agency_id, user_id, exception_date);

-- ---------------------------------------------------------------------------
-- agency_closures (D5: entita' separata, non duplicata su ogni agente)
-- ---------------------------------------------------------------------------

CREATE TABLE agency_closures (
    id              BIGSERIAL    PRIMARY KEY,
    agency_id       BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,

    closure_date    DATE         NOT NULL,
    start_minute    SMALLINT     NOT NULL,
    end_minute      SMALLINT     NOT NULL,
    reason_code     VARCHAR(40),

    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT agency_closures_minutes_chk CHECK (
        start_minute >= 0 AND start_minute < 1440
        AND end_minute >= 1 AND end_minute <= 1440
        AND end_minute > start_minute
    ),

    CONSTRAINT agency_closures_no_overlap_excl EXCLUDE USING gist (
        agency_id WITH =,
        closure_date WITH =,
        int4range(start_minute, end_minute, '[)') WITH &&
    )
);

CREATE INDEX idx_agency_closures_lookup
    ON agency_closures (agency_id, closure_date);

-- ---------------------------------------------------------------------------
-- Guardie: `updated_at` e membership ATTIVA quando l'agente viene scritto.
-- Stessa forma della guardia di appointments_guard() per assigned_user_id
-- (072): la FK composita dice solo che la membership ESISTE, il trigger
-- verifica che sia ATTIVA al momento della scrittura.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION agent_working_hours_guard() RETURNS trigger AS $fn$
DECLARE
    v_is_ins BOOLEAN := (TG_OP = 'INSERT');
BEGIN
    NEW.updated_at := NOW();
    IF v_is_ins OR NEW.user_id IS DISTINCT FROM OLD.user_id
       OR NEW.agency_id IS DISTINCT FROM OLD.agency_id THEN
        IF NOT EXISTS (
            SELECT 1 FROM agency_memberships
             WHERE agency_id = NEW.agency_id
               AND operator_user_id = NEW.user_id
               AND status = 'active') THEN
            RAISE EXCEPTION 'A30-11: operator % has no active membership in agency %',
                NEW.user_id, NEW.agency_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

CREATE TRIGGER trg_agent_working_hours_guard
    BEFORE INSERT OR UPDATE ON agent_working_hours
    FOR EACH ROW EXECUTE FUNCTION agent_working_hours_guard();

CREATE OR REPLACE FUNCTION agent_availability_exceptions_guard() RETURNS trigger AS $fn$
DECLARE
    v_is_ins BOOLEAN := (TG_OP = 'INSERT');
BEGIN
    NEW.updated_at := NOW();
    IF v_is_ins OR NEW.user_id IS DISTINCT FROM OLD.user_id
       OR NEW.agency_id IS DISTINCT FROM OLD.agency_id THEN
        IF NOT EXISTS (
            SELECT 1 FROM agency_memberships
             WHERE agency_id = NEW.agency_id
               AND operator_user_id = NEW.user_id
               AND status = 'active') THEN
            RAISE EXCEPTION 'A30-11: operator % has no active membership in agency %',
                NEW.user_id, NEW.agency_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

CREATE TRIGGER trg_agent_availability_exceptions_guard
    BEFORE INSERT OR UPDATE ON agent_availability_exceptions
    FOR EACH ROW EXECUTE FUNCTION agent_availability_exceptions_guard();

CREATE OR REPLACE FUNCTION agency_closures_guard() RETURNS trigger AS $fn$
BEGIN
    NEW.updated_at := NOW();
    IF NOT EXISTS (SELECT 1 FROM agencies WHERE id = NEW.agency_id) THEN
        RAISE EXCEPTION 'A30-11: agency % does not exist', NEW.agency_id;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

CREATE TRIGGER trg_agency_closures_guard
    BEFORE INSERT OR UPDATE ON agency_closures
    FOR EACH ROW EXECUTE FUNCTION agency_closures_guard();
