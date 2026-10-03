-- CENSIMENTO-1: edifici (palazzine) e unita' immobiliari censite.
--
-- Additiva. Due tabelle nuove (`buildings`, `property_accessories`) e colonne
-- NULLABLE o con default neutro su `properties`. NESSUN backfill: ogni
-- immobile esistente resta identico, `record_kind = 'crm'`, senza edificio,
-- senza categoria catastale, con il suo codice e il suo indirizzo.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- ---------------------------------------------------------------------------
-- IL MODELLO (roadmap/CENSIMENTO-0_EDIFICI_UNITA_ANALISI_E_PROPOSTA.md, REV 3.1)
--
--   * la PALAZZINA e' un contenitore (`buildings`), per agenzia, senza prezzo,
--     stato commerciale, proprietari o superficie: non e' un immobile;
--   * ogni UNITA' resta una riga di `properties` (appartamento, negozio,
--     garage...), collegata FACOLTATIVAMENTE all'edificio (`building_id`);
--   * una PERTINENZA catastalmente autonoma e' una riga di `properties`
--     collegata all'unita' principale (`parent_property_id`, profondita' 1);
--   * un ACCESSORIO compreso nell'unita' (cantina nel subalterno
--     dell'abitazione) o di autonomia "da chiarire" e' una riga di
--     `property_accessories`: non e' un'unita' catastale e non viene mai
--     contato come tale;
--   * `record_kind` separa il CENSIMENTO ('census') dal lavoro COMMERCIALE
--     ('crm'): la promozione e' esplicita e a senso unico (trigger sotto);
--   * `client_request_id` + impronta del payload: idempotenza delle creazioni
--     (un retry dopo timeout non crea una seconda riga).
--
-- COSA NON E'
--   * niente UNIQUE catastale sugli edifici (una particella non identifica
--     per forza un solo edificio) e niente UNIQUE su scala/piano/interno:
--     sono avvisi applicativi;
--   * la categoria catastale NON si deduce mai dalla tipologia: nessun
--     default, nessun backfill; "Da verificare" e' NULL. Il CHECK sul codice
--     e' di FORMATO (gruppo/numero): l'esistenza nel catalogo la valida il
--     service, non il database;
--   * nessuna restrizione nuova sulle righe storiche: ogni CHECK e ogni
--     trigger e' soddisfatto dai valori di default che lo storico riceve.
--
-- TENANCY
--   `buildings.agency_id` NOT NULL. Edificio<->unita' e unita'<->pertinenza
--   sempre nella stessa agenzia: lo impone `properties_links_integrity()`
--   (pattern 036/082). Una pertinenza PUO' stare in un edificio diverso da
--   quello dell'unita' principale (il garage nella palazzina di fronte).
-- ---------------------------------------------------------------------------

-- ===========================================================================
-- 1. buildings
-- ===========================================================================
CREATE TABLE IF NOT EXISTS buildings (
    id                          BIGSERIAL    PRIMARY KEY,
    agency_id                   BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    building_type               VARCHAR(30)  NOT NULL DEFAULT 'condominio',
    name                        VARCHAR(120),
    region                      VARCHAR(50),
    province                    VARCHAR(10),
    city                        VARCHAR(120),
    microzone                   VARCHAR(150),
    address                     VARCHAR(250),
    civic_number                VARCHAR(30),
    postal_code                 VARCHAR(20),
    latitude                    NUMERIC(10,7),
    longitude                   NUMERIC(10,7),
    cadastral_municipality_code CHAR(4),
    cadastral_section           VARCHAR(5),      -- NULL = non conosciuta, '' = assente
    cadastral_sheet             VARCHAR(10),
    cadastral_parcel            VARCHAR(10),
    floors_above_ground         INTEGER,
    year_built                  INTEGER,
    elevator                    BOOLEAN,
    units_declared              INTEGER,         -- unita' catastali dichiarate (principali + pertinenze autonome)
    units_declared_source       VARCHAR(20),
    census_status               VARCHAR(12)  NOT NULL DEFAULT 'partial',
    client_request_id           UUID,
    client_request_fingerprint  CHAR(64),
    notes                       TEXT,
    metadata                    JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at                  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    archived_at                 TIMESTAMPTZ,
    CONSTRAINT buildings_type_chk CHECK (building_type IN
        ('condominio', 'villa', 'rustico', 'capannone', 'commerciale', 'misto', 'altro')),
    CONSTRAINT buildings_census_status_chk CHECK (census_status IN ('verified', 'partial', 'estimated')),
    CONSTRAINT buildings_units_declared_chk CHECK (units_declared IS NULL OR units_declared >= 0),
    CONSTRAINT buildings_units_source_chk CHECK (units_declared_source IS NULL
        OR units_declared_source IN ('survey', 'cadastre', 'owner', 'unknown')),
    CONSTRAINT buildings_floors_chk CHECK (floors_above_ground IS NULL OR floors_above_ground >= 0),
    CONSTRAINT buildings_year_chk CHECK (year_built IS NULL OR (year_built >= 1000 AND year_built <= 2200)),
    CONSTRAINT buildings_belfiore_chk CHECK (cadastral_municipality_code IS NULL
        OR cadastral_municipality_code ~ '^[A-Z][0-9]{3}$'),
    CONSTRAINT buildings_client_request_chk CHECK (
        (client_request_id IS NULL) = (client_request_fingerprint IS NULL)
        AND (client_request_fingerprint IS NULL OR client_request_fingerprint ~ '^[0-9a-f]{64}$'))
);

CREATE INDEX IF NOT EXISTS idx_buildings_agency_live
    ON buildings (agency_id, updated_at DESC) WHERE archived_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_buildings_agency_city
    ON buildings (agency_id, city);
-- Idempotenza: una sola riga per (agenzia, richiesta del client).
CREATE UNIQUE INDEX IF NOT EXISTS uq_buildings_client_request
    ON buildings (agency_id, client_request_id) WHERE client_request_id IS NOT NULL;

-- ===========================================================================
-- 2. properties: colonne nuove
-- ===========================================================================
ALTER TABLE properties ADD COLUMN IF NOT EXISTS building_id                 BIGINT;
ALTER TABLE properties ADD COLUMN IF NOT EXISTS parent_property_id          BIGINT;
ALTER TABLE properties ADD COLUMN IF NOT EXISTS whole_building              BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE properties ADD COLUMN IF NOT EXISTS staircase                   VARCHAR(10);
ALTER TABLE properties ADD COLUMN IF NOT EXISTS internal_number             VARCHAR(10);
ALTER TABLE properties ADD COLUMN IF NOT EXISTS cadastral_municipality_code CHAR(4);
ALTER TABLE properties ADD COLUMN IF NOT EXISTS cadastral_section           VARCHAR(5);
ALTER TABLE properties ADD COLUMN IF NOT EXISTS cadastral_sheet             VARCHAR(10);
ALTER TABLE properties ADD COLUMN IF NOT EXISTS cadastral_parcel            VARCHAR(10);
ALTER TABLE properties ADD COLUMN IF NOT EXISTS cadastral_subunit           VARCHAR(10);
ALTER TABLE properties ADD COLUMN IF NOT EXISTS cadastral_category          VARCHAR(5);
ALTER TABLE properties ADD COLUMN IF NOT EXISTS record_kind                 VARCHAR(10) NOT NULL DEFAULT 'crm';
ALTER TABLE properties ADD COLUMN IF NOT EXISTS address_inherited           BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE properties ADD COLUMN IF NOT EXISTS client_request_id           UUID;
ALTER TABLE properties ADD COLUMN IF NOT EXISTS client_request_fingerprint  CHAR(64);

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'properties_building_id_fkey'
                    AND conrelid = 'public.properties'::regclass) THEN
        ALTER TABLE properties ADD CONSTRAINT properties_building_id_fkey
            FOREIGN KEY (building_id) REFERENCES buildings(id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'properties_parent_property_id_fkey'
                    AND conrelid = 'public.properties'::regclass) THEN
        ALTER TABLE properties ADD CONSTRAINT properties_parent_property_id_fkey
            FOREIGN KEY (parent_property_id) REFERENCES properties(id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'properties_record_kind_chk') THEN
        ALTER TABLE properties ADD CONSTRAINT properties_record_kind_chk
            CHECK (record_kind IN ('census', 'crm'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'properties_parent_not_self_chk') THEN
        ALTER TABLE properties ADD CONSTRAINT properties_parent_not_self_chk
            CHECK (parent_property_id IS NULL OR parent_property_id <> id);
    END IF;
    -- Lo "Stabile intero" (property_type = 'building') e' l'unico che puo'
    -- dichiararsi whole_building, e deve puntare al suo edificio.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'properties_whole_building_chk') THEN
        ALTER TABLE properties ADD CONSTRAINT properties_whole_building_chk
            CHECK (NOT whole_building OR (property_type = 'building' AND building_id IS NOT NULL));
    END IF;
    -- "Da verificare" non e' mai un codice. Il CHECK e' di FORMATO (lettera
    -- del gruppo A-F, barra, 1-2 cifre): ammette anche un A/99 inesistente;
    -- l'appartenenza al catalogo (52 voci) e' compito del service.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'properties_cadastral_category_chk') THEN
        ALTER TABLE properties ADD CONSTRAINT properties_cadastral_category_chk
            CHECK (cadastral_category IS NULL OR cadastral_category ~ '^[A-F]/[0-9]{1,2}$');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'properties_belfiore_chk') THEN
        ALTER TABLE properties ADD CONSTRAINT properties_belfiore_chk
            CHECK (cadastral_municipality_code IS NULL OR cadastral_municipality_code ~ '^[A-Z][0-9]{3}$');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'properties_client_request_chk') THEN
        ALTER TABLE properties ADD CONSTRAINT properties_client_request_chk
            CHECK ((client_request_id IS NULL) = (client_request_fingerprint IS NULL)
                   AND (client_request_fingerprint IS NULL OR client_request_fingerprint ~ '^[0-9a-f]{64}$'));
    END IF;
END
$do$;

CREATE INDEX IF NOT EXISTS idx_properties_building
    ON properties (agency_id, building_id) WHERE building_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_properties_parent
    ON properties (parent_property_id) WHERE parent_property_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_properties_record_kind
    ON properties (agency_id, record_kind) WHERE archived_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_properties_client_request
    ON properties (agency_id, client_request_id) WHERE client_request_id IS NOT NULL;
-- Identita' catastale COMPLETA dell'unita' (REV 3.1): blocca solo quando
-- Belfiore, foglio, particella e subalterno sono valorizzati E la sezione e'
-- CONOSCIUTA ('' = assente conta come conosciuta; NULL = non conosciuta ->
-- nessun blocco, solo avviso applicativo). I valori sono normalizzati dal
-- trigger sotto, quindi l'indice confronta forme canoniche.
CREATE UNIQUE INDEX IF NOT EXISTS uq_properties_cadastral_identity
    ON properties (agency_id, cadastral_municipality_code, cadastral_section,
                   cadastral_sheet, cadastral_parcel, cadastral_subunit)
    WHERE cadastral_municipality_code IS NOT NULL AND cadastral_section IS NOT NULL
      AND cadastral_sheet IS NOT NULL AND cadastral_parcel IS NOT NULL
      AND cadastral_subunit IS NOT NULL;

-- ===========================================================================
-- 3. property_accessories (figlia: niente agency_id, come property_photos)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS property_accessories (
    id                          BIGSERIAL    PRIMARY KEY,
    property_id                 BIGINT       NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    kind                        VARCHAR(20)  NOT NULL,
    surface_sqm                 NUMERIC(10,2),
    cadastral_status            VARCHAR(10)  NOT NULL DEFAULT 'included',   -- included | unknown ("Da chiarire")
    notes                       TEXT,
    client_request_id           UUID,
    client_request_fingerprint  CHAR(64),
    created_at                  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT property_accessories_kind_chk CHECK (kind IN
        ('cantina', 'soffitta', 'posto_auto', 'giardino', 'terrazzo', 'box', 'deposito', 'altro')),
    CONSTRAINT property_accessories_status_chk CHECK (cadastral_status IN ('included', 'unknown')),
    CONSTRAINT property_accessories_surface_chk CHECK (surface_sqm IS NULL OR surface_sqm >= 0),
    CONSTRAINT property_accessories_client_request_chk CHECK (
        (client_request_id IS NULL) = (client_request_fingerprint IS NULL)
        AND (client_request_fingerprint IS NULL OR client_request_fingerprint ~ '^[0-9a-f]{64}$'))
);
CREATE INDEX IF NOT EXISTS idx_property_accessories_property
    ON property_accessories (property_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_property_accessories_client_request
    ON property_accessories (property_id, client_request_id) WHERE client_request_id IS NOT NULL;

-- ===========================================================================
-- 4. Normalizzazione catastale (forma canonica, cosi' l'indice confronta
--    "12" con "0012" e "a125" con "A125")
-- ===========================================================================
CREATE OR REPLACE FUNCTION cadastral_norm(valore TEXT) RETURNS TEXT AS $fn$
DECLARE
    v TEXT;
BEGIN
    IF valore IS NULL THEN
        RETURN NULL;
    END IF;
    v := upper(regexp_replace(valore, '\s+', '', 'g'));
    IF v = '' THEN
        RETURN '';
    END IF;
    IF v ~ '^0+[0-9]+$' THEN
        v := regexp_replace(v, '^0+', '');
    END IF;
    RETURN v;
END;
$fn$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION properties_cadastral_normalize() RETURNS trigger AS $fn$
BEGIN
    NEW.cadastral_municipality_code := NULLIF(cadastral_norm(NEW.cadastral_municipality_code), '');
    -- La sezione tiene '' (= assente, dichiarata) e NULL (= non conosciuta).
    NEW.cadastral_section  := cadastral_norm(NEW.cadastral_section);
    NEW.cadastral_sheet    := NULLIF(cadastral_norm(NEW.cadastral_sheet), '');
    NEW.cadastral_parcel   := NULLIF(cadastral_norm(NEW.cadastral_parcel), '');
    NEW.cadastral_subunit  := NULLIF(cadastral_norm(NEW.cadastral_subunit), '');
    NEW.cadastral_category := NULLIF(upper(btrim(NEW.cadastral_category)), '');
    NEW.staircase          := NULLIF(btrim(NEW.staircase), '');
    NEW.internal_number    := NULLIF(btrim(NEW.internal_number), '');
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION buildings_cadastral_normalize() RETURNS trigger AS $fn$
BEGIN
    NEW.cadastral_municipality_code := NULLIF(cadastral_norm(NEW.cadastral_municipality_code), '');
    NEW.cadastral_section  := cadastral_norm(NEW.cadastral_section);
    NEW.cadastral_sheet    := NULLIF(cadastral_norm(NEW.cadastral_sheet), '');
    NEW.cadastral_parcel   := NULLIF(cadastral_norm(NEW.cadastral_parcel), '');
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- ===========================================================================
-- 5. Collegamenti: stessa agenzia, nessun ciclo, profondita' 1 - anche sotto
--    concorrenza e anche quando cambia il lato GENITORE.
--
-- Due transazioni che collegano A->B e B->A nello stesso istante, o che
-- creano insieme una catena di profondita' 2, leggerebbero ciascuna uno stato
-- ancora valido e committerebbero un ciclo. Per questo ogni collegamento
-- prende PRIMA dei controlli un lock advisory di transazione sui nodi
-- coinvolti (la riga stessa, il genitore nuovo e quello vecchio), in ordine
-- crescente di id: due transazioni sullo stesso nodo si mettono in fila e la
-- seconda rilegge lo stato COMMESSO dalla prima. L'edificio si legge FOR
-- SHARE: chi ne sta cambiando l'agenzia (trigger 5b) tiene il lock esclusivo
-- e il collegamento attende, poi rilegge l'agenzia nuova.
--
-- ISOLAMENTO. Tutto questo vale SOLO in READ COMMITTED (il livello del
-- progetto: psycopg2 default), dove ogni SELECT del trigger prende uno
-- snapshot nuovo. In REPEATABLE READ o SERIALIZABLE un lock advisory NON
-- rinnova lo snapshot: dopo l'attesa il trigger rileggerebbe lo stato di
-- prima e il ciclo passerebbe. Percio' le operazioni che dipendono da queste
-- letture - impostare o cambiare un collegamento (edificio, pertinenza) e
-- cambiare l'agenzia di un'unita' o di un edificio - sono RIFIUTATE in modo
-- esplicito (SQLSTATE 25000, invalid_transaction_state) se la transazione
-- non e' READ COMMITTED. Letture, inserimenti senza collegamenti e
-- modifiche che non toccano i collegamenti non sono mai bloccati.
--
-- DEADLOCK. L'ordine crescente vale dentro la SINGOLA chiamata del trigger
-- (una singola istruzione). Una transazione che esegue PIU' collegamenti in
-- sequenza accumula lock di istruzioni diverse e, con un'altra transazione
-- speculare, puo' andare in deadlock: PostgreSQL ne abortisce una (SQLSTATE
-- 40P01), lo stato resta coerente e il chiamante ritenta. Il service di
-- Fase 2 fa un collegamento per transazione e ritenta su 40P01.
-- ===========================================================================
CREATE OR REPLACE FUNCTION censimento_require_read_committed(contesto TEXT) RETURNS void AS $fn$
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION
            'CENSIMENTO-1: % requires a READ COMMITTED transaction (current isolation: %): retry in READ COMMITTED',
            contesto, current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
END;
$fn$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION censimento_lock_nodes(VARIADIC ids BIGINT[]) RETURNS void AS $fn$
DECLARE
    v BIGINT;
BEGIN
    FOR v IN SELECT DISTINCT x FROM unnest(ids) AS x WHERE x IS NOT NULL ORDER BY x LOOP
        PERFORM pg_advisory_xact_lock(83, (v % 2147483647)::int);
    END LOOP;
END;
$fn$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION properties_links_integrity() RETURNS trigger AS $fn$
DECLARE
    v_agency BIGINT;
    v_parent_of_parent BIGINT;
    v_old_parent BIGINT := NULL;
    v_agency_changed BOOLEAN := FALSE;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        v_old_parent := OLD.parent_property_id;
        v_agency_changed := NEW.agency_id IS DISTINCT FROM OLD.agency_id;
    END IF;
    -- Le letture sotto decidono: fuori da READ COMMITTED si rifiuta. Solo
    -- quando c'e' un collegamento in gioco o cambia l'agenzia; un immobile
    -- senza collegamenti si inserisce e si modifica a qualunque isolamento.
    IF (TG_OP = 'INSERT' AND (NEW.building_id IS NOT NULL OR NEW.parent_property_id IS NOT NULL))
       OR (TG_OP = 'UPDATE' AND (NEW.building_id IS DISTINCT FROM OLD.building_id
                                 OR NEW.parent_property_id IS DISTINCT FROM OLD.parent_property_id
                                 OR v_agency_changed)) THEN
        PERFORM censimento_require_read_committed('linking a property (building/pertinenza/agency)');
    END IF;
    PERFORM censimento_lock_nodes(NEW.id, NEW.parent_property_id, v_old_parent);

    IF NEW.building_id IS NOT NULL THEN
        SELECT agency_id INTO v_agency FROM buildings WHERE id = NEW.building_id FOR SHARE;
        IF v_agency IS NULL OR NEW.agency_id IS NULL OR v_agency <> NEW.agency_id THEN
            RAISE EXCEPTION
                'CENSIMENTO-1 tenancy: building % does not belong to agency % (property)',
                NEW.building_id, NEW.agency_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.parent_property_id IS NOT NULL THEN
        SELECT agency_id, parent_property_id INTO v_agency, v_parent_of_parent
          FROM properties WHERE id = NEW.parent_property_id;
        IF v_agency IS NULL OR NEW.agency_id IS NULL OR v_agency <> NEW.agency_id THEN
            RAISE EXCEPTION
                'CENSIMENTO-1 tenancy: parent property % does not belong to agency % (property)',
                NEW.parent_property_id, NEW.agency_id
                USING ERRCODE = 'check_violation';
        END IF;
        -- Profondita' 1: una pertinenza non puo' avere a sua volta un genitore...
        IF v_parent_of_parent IS NOT NULL THEN
            RAISE EXCEPTION
                'CENSIMENTO-1: property % is itself a pertinenza of % and cannot be a parent',
                NEW.parent_property_id, v_parent_of_parent
                USING ERRCODE = 'check_violation';
        END IF;
        -- ...e un'unita' che ha pertinenze non puo' diventare pertinenza di altri.
        IF TG_OP = 'UPDATE' AND EXISTS (SELECT 1 FROM properties WHERE parent_property_id = NEW.id) THEN
            RAISE EXCEPTION
                'CENSIMENTO-1: property % has pertinenze and cannot become a pertinenza',
                NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    -- Lato genitore: un'unita' con pertinenze collegate non cambia agenzia
    -- (le pertinenze resterebbero nell'agenzia di prima). Prima si scollegano.
    IF v_agency_changed AND EXISTS (SELECT 1 FROM properties WHERE parent_property_id = NEW.id) THEN
        RAISE EXCEPTION
            'CENSIMENTO-1 tenancy: property % has linked pertinenze and cannot change agency',
            NEW.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- 5b. Lato genitore, edificio: un edificio con unita' collegate non cambia
--     agenzia. Il lock esclusivo della riga in UPDATE fa attendere i
--     collegamenti concorrenti (FOR SHARE sopra), che poi rileggono l'agenzia
--     nuova e vengono rifiutati; un collegamento gia' commesso e' visto qui.
CREATE OR REPLACE FUNCTION buildings_agency_guard() RETURNS trigger AS $fn$
BEGIN
    IF NEW.agency_id IS DISTINCT FROM OLD.agency_id THEN
        PERFORM censimento_require_read_committed('changing the agency of a building');
    END IF;
    IF NEW.agency_id IS DISTINCT FROM OLD.agency_id
       AND EXISTS (SELECT 1 FROM properties WHERE building_id = NEW.id) THEN
        RAISE EXCEPTION
            'CENSIMENTO-1 tenancy: building % has linked properties and cannot change agency',
            NEW.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- 5c. Idempotenza: chiave e impronta della richiesta di creazione sono
--     immutabili una volta scritte, su tutte e tre le tabelle. Un retry si
--     confronta con l'impronta ORIGINALE, mai con una scheda poi modificata.
CREATE OR REPLACE FUNCTION client_request_immutable() RETURNS trigger AS $fn$
BEGIN
    IF OLD.client_request_id IS NOT NULL
       AND (NEW.client_request_id IS DISTINCT FROM OLD.client_request_id
            OR NEW.client_request_fingerprint IS DISTINCT FROM OLD.client_request_fingerprint) THEN
        RAISE EXCEPTION
            'CENSIMENTO-1: client_request_id/fingerprint of %.% are immutable once set',
            TG_TABLE_NAME, OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- ===========================================================================
-- 6. Guardia censimento/commerciale
--    (a) una riga 'census' vive solo in draft o archived, senza incarico ne'
--        acquisizione; (b) crm -> census e' vietato; (c) la promozione
--        census -> crm non puo' cambiare nello stesso UPDATE stato, campi
--        incarico o acquisition_id: prima si prende in carico, poi si lavora.
--    Le righe storiche sono 'crm' e non sono mai toccate da queste regole.
-- ===========================================================================
CREATE OR REPLACE FUNCTION properties_census_guard() RETURNS trigger AS $fn$
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.record_kind = 'crm' AND NEW.record_kind = 'census' THEN
        RAISE EXCEPTION
            'CENSIMENTO-1: property % is commercial (crm) and cannot go back to census',
            NEW.id
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.record_kind = 'census' THEN
        IF NEW.commercial_status NOT IN ('draft', 'archived') THEN
            RAISE EXCEPTION
                'CENSIMENTO-1: a census property cannot have commercial status % (take it in charge first)',
                NEW.commercial_status
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.mandate_type IS NOT NULL OR NEW.mandate_start IS NOT NULL
           OR NEW.mandate_end IS NOT NULL OR NEW.acquisition_id IS NOT NULL THEN
            RAISE EXCEPTION
                'CENSIMENTO-1: a census property cannot carry a mandate or an acquisition (take it in charge first)'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF TG_OP = 'UPDATE' AND OLD.record_kind = 'census' AND NEW.record_kind = 'crm' THEN
        IF NEW.commercial_status IS DISTINCT FROM OLD.commercial_status
           OR NEW.mandate_type IS DISTINCT FROM OLD.mandate_type
           OR NEW.mandate_start IS DISTINCT FROM OLD.mandate_start
           OR NEW.mandate_end IS DISTINCT FROM OLD.mandate_end
           OR NEW.acquisition_id IS DISTINCT FROM OLD.acquisition_id THEN
            RAISE EXCEPTION
                'CENSIMENTO-1: taking property % in charge cannot change its commercial fields in the same statement',
                NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- ===========================================================================
-- 7. Trigger (idempotenti)
-- ===========================================================================
DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_properties_cadastral_normalize'
                    AND tgrelid = 'public.properties'::regclass) THEN
        CREATE TRIGGER trg_properties_cadastral_normalize
            BEFORE INSERT OR UPDATE OF cadastral_municipality_code, cadastral_section,
                cadastral_sheet, cadastral_parcel, cadastral_subunit, cadastral_category,
                staircase, internal_number ON properties
            FOR EACH ROW EXECUTE FUNCTION properties_cadastral_normalize();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_buildings_cadastral_normalize'
                    AND tgrelid = 'public.buildings'::regclass) THEN
        CREATE TRIGGER trg_buildings_cadastral_normalize
            BEFORE INSERT OR UPDATE OF cadastral_municipality_code, cadastral_section,
                cadastral_sheet, cadastral_parcel ON buildings
            FOR EACH ROW EXECUTE FUNCTION buildings_cadastral_normalize();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_properties_links_integrity'
                    AND tgrelid = 'public.properties'::regclass) THEN
        CREATE TRIGGER trg_properties_links_integrity
            BEFORE INSERT OR UPDATE OF building_id, parent_property_id, agency_id ON properties
            FOR EACH ROW EXECUTE FUNCTION properties_links_integrity();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_properties_census_guard'
                    AND tgrelid = 'public.properties'::regclass) THEN
        CREATE TRIGGER trg_properties_census_guard
            BEFORE INSERT OR UPDATE OF record_kind, commercial_status, mandate_type,
                mandate_start, mandate_end, acquisition_id ON properties
            FOR EACH ROW EXECUTE FUNCTION properties_census_guard();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_buildings_agency_guard'
                    AND tgrelid = 'public.buildings'::regclass) THEN
        CREATE TRIGGER trg_buildings_agency_guard
            BEFORE UPDATE OF agency_id ON buildings
            FOR EACH ROW EXECUTE FUNCTION buildings_agency_guard();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_properties_client_request_immutable'
                    AND tgrelid = 'public.properties'::regclass) THEN
        CREATE TRIGGER trg_properties_client_request_immutable
            BEFORE UPDATE OF client_request_id, client_request_fingerprint ON properties
            FOR EACH ROW EXECUTE FUNCTION client_request_immutable();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_buildings_client_request_immutable'
                    AND tgrelid = 'public.buildings'::regclass) THEN
        CREATE TRIGGER trg_buildings_client_request_immutable
            BEFORE UPDATE OF client_request_id, client_request_fingerprint ON buildings
            FOR EACH ROW EXECUTE FUNCTION client_request_immutable();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_property_accessories_client_request_immutable'
                    AND tgrelid = 'public.property_accessories'::regclass) THEN
        CREATE TRIGGER trg_property_accessories_client_request_immutable
            BEFORE UPDATE OF client_request_id, client_request_fingerprint ON property_accessories
            FOR EACH ROW EXECUTE FUNCTION client_request_immutable();
    END IF;
END
$do$;

-- ===========================================================================
-- 8. Sonda: lo storico e' intatto e il modello e' quello atteso
-- ===========================================================================
DO $do$
BEGIN
    IF EXISTS (SELECT 1 FROM properties WHERE record_kind <> 'crm' OR whole_building
                  OR building_id IS NOT NULL OR parent_property_id IS NOT NULL) THEN
        RAISE EXCEPTION 'CENSIMENTO-1 083: a pre-existing property was reclassified by the migration';
    END IF;
    IF (SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname IN
            ('trg_properties_cadastral_normalize', 'trg_buildings_cadastral_normalize',
             'trg_properties_links_integrity', 'trg_properties_census_guard',
             'trg_buildings_agency_guard', 'trg_properties_client_request_immutable',
             'trg_buildings_client_request_immutable',
             'trg_property_accessories_client_request_immutable')) <> 8 THEN
        RAISE EXCEPTION 'CENSIMENTO-1 083: trigger mancanti';
    END IF;
    IF (SELECT count(*) FROM pg_indexes WHERE indexname IN
            ('uq_properties_cadastral_identity', 'uq_properties_client_request',
             'uq_buildings_client_request', 'uq_property_accessories_client_request')) <> 4 THEN
        RAISE EXCEPTION 'CENSIMENTO-1 083: indici univoci mancanti';
    END IF;
END
$do$;
