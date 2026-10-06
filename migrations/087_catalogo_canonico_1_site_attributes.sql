-- CATALOGO-CANONICO-1 (FASE D): i dati dichiarati sul sito stima360.it hanno
-- un posto nel CRM, con lo stesso significato.
--
-- Additiva. Colonne NULLABLE su `properties`, due colonne con default neutro
-- su `property_accessories`, un CHECK dei tipi di accessorio ALLARGATO (ogni
-- valore ammesso prima resta ammesso) e una tabella nuova per la provenienza.
-- NESSUN backfill: nessuna riga esistente cambia valore.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- ---------------------------------------------------------------------------
-- 1. properties: i campi del sito che non avevano una colonna
--
--    NULL = non dichiarato. Mai un default: "non so" non diventa "no".
--    I valori sono quelli DICHIARATI (codici del catalogo, o il testo del
--    cliente per i campi a testo libero del sito). Nessun coefficiente ne'
--    prezzo calcolato dal motore finisce qui.
-- ---------------------------------------------------------------------------
ALTER TABLE properties ADD COLUMN IF NOT EXISTS sea_position     VARCHAR(30);   -- posizioneMare (frontemare | seconda | oltre)
ALTER TABLE properties ADD COLUMN IF NOT EXISTS sea_distance     VARCHAR(30);   -- distanzaMare (0-100 | 100-300 | 300-500 | 500-1000)
ALTER TABLE properties ADD COLUMN IF NOT EXISTS sea_band         VARCHAR(32);   -- fascia_mare, testo del sito
ALTER TABLE properties ADD COLUMN IF NOT EXISTS sea_barrier      BOOLEAN;       -- barrieraMare: ferrovia/strada fra casa e mare
ALTER TABLE properties ADD COLUMN IF NOT EXISTS sea_view         BOOLEAN;       -- vistaMareYN
ALTER TABLE properties ADD COLUMN IF NOT EXISTS sea_view_detail  VARCHAR(200);  -- vistaMareDettaglio / vistaMare, testo del sito
ALTER TABLE properties ADD COLUMN IF NOT EXISTS heating          VARCHAR(120);  -- riscaldamento (stima dettagliata)
ALTER TABLE properties ADD COLUMN IF NOT EXISTS air_conditioning VARCHAR(120);  -- condizionatore
ALTER TABLE properties ADD COLUMN IF NOT EXISTS air_conditioning_type VARCHAR(120); -- condiz_tipo
ALTER TABLE properties ADD COLUMN IF NOT EXISTS exposure         VARCHAR(120);  -- esposizione
ALTER TABLE properties ADD COLUMN IF NOT EXISTS furnishing       VARCHAR(120);  -- arredo
ALTER TABLE properties ADD COLUMN IF NOT EXISTS condo_fees       NUMERIC(10,2); -- spese_cond (EUR, periodo non dichiarato dal sito)
ALTER TABLE properties ADD COLUMN IF NOT EXISTS other_features   TEXT;          -- altroDescrizione

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'properties_condo_fees_chk') THEN
        ALTER TABLE properties ADD CONSTRAINT properties_condo_fees_chk CHECK (condo_fees IS NULL OR condo_fees >= 0);
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 2. property_accessories: i tipi del sito, la quantita', la fonte
--
--    Il CHECK si ALLARGA: taverna, balcone, piscina, posto_moto, posto_bici.
--    Il "garage" del sito e' il `box` gia' esistente (stessa cosa: rimessa
--    chiusa); il "posto auto" resta distinto. `quantity` NULL = non indicata
--    (mai 0 per "non so"). `source` dice chi ha scritto la riga: le righe
--    esistenti sono `manual`.
-- ---------------------------------------------------------------------------
ALTER TABLE property_accessories ADD COLUMN IF NOT EXISTS quantity INTEGER;
ALTER TABLE property_accessories ADD COLUMN IF NOT EXISTS source   VARCHAR(20) NOT NULL DEFAULT 'manual';

ALTER TABLE property_accessories DROP CONSTRAINT IF EXISTS property_accessories_kind_chk;
ALTER TABLE property_accessories ADD CONSTRAINT property_accessories_kind_chk CHECK (kind IN
    ('cantina', 'soffitta', 'posto_auto', 'giardino', 'terrazzo', 'box', 'deposito', 'altro',
     'taverna', 'balcone', 'piscina', 'posto_moto', 'posto_bici'));

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'property_accessories_quantity_chk') THEN
        ALTER TABLE property_accessories ADD CONSTRAINT property_accessories_quantity_chk
            CHECK (quantity IS NULL OR quantity >= 1);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'property_accessories_source_chk') THEN
        ALTER TABLE property_accessories ADD CONSTRAINT property_accessories_source_chk
            CHECK (source IN ('manual', 'stima360'));
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 3. property_site_sources: da quale stima del sito viene una scheda
--
--    Una riga per (stima, immobile). `status = 'active'` e' il collegamento
--    corrente: al massimo UNO per stima (indice parziale). Un ricollegamento
--    esplicito chiude la riga (`relinked`, con l'immobile di destinazione) e
--    ne apre una nuova sull'altro immobile: lo storico resta.
--
--      site_values  l'ultima istantanea dei valori scritti dal sito per campo
--                   (serve a capire se l'agente li ha corretti: un campo
--                   corretto non si sovrascrive mai);
--      declared     i valori dichiarati dal sito, gia' tradotti nel catalogo,
--                   con il valore grezzo accanto (nessun dato di contatto);
--      unmapped     valori che il catalogo non riconosce: conservati e
--                   mostrati "Da verificare", mai trasformati in "Altro";
--      conflicts    valori del sito diversi da quelli corretti dall'agente,
--                   in attesa di «Applica» o «Ignora»;
--      duplicates   possibili doppioni (stesso contatto, stesso indirizzo):
--                   solo segnalati, mai uniti in automatico.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS property_site_sources (
    id                       BIGSERIAL    PRIMARY KEY,
    agency_id                BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    property_id              BIGINT       NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    stima_id                 INTEGER      NOT NULL REFERENCES stime(id) ON DELETE CASCADE,
    contact_id               BIGINT       REFERENCES contacts(id) ON DELETE SET NULL,
    lead_id                  BIGINT       REFERENCES leads(id) ON DELETE SET NULL,
    origin                   VARCHAR(20)  NOT NULL,
    status                   VARCHAR(20)  NOT NULL DEFAULT 'active',
    fingerprint              CHAR(64)     NOT NULL,
    site_values              JSONB        NOT NULL DEFAULT '{}'::jsonb,
    declared                 JSONB        NOT NULL DEFAULT '{}'::jsonb,
    unmapped                 JSONB        NOT NULL DEFAULT '[]'::jsonb,
    conflicts                JSONB        NOT NULL DEFAULT '[]'::jsonb,
    duplicates               JSONB        NOT NULL DEFAULT '[]'::jsonb,
    detail_ids               JSONB        NOT NULL DEFAULT '[]'::jsonb,
    relinked_to_property_id  BIGINT       REFERENCES properties(id) ON DELETE SET NULL,
    linked_by_user_id        BIGINT,
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT property_site_sources_origin_chk CHECK (origin IN ('auto', 'retry', 'manual_link')),
    CONSTRAINT property_site_sources_status_chk CHECK (status IN ('active', 'relinked')),
    CONSTRAINT property_site_sources_relinked_chk CHECK ((status = 'relinked') = (relinked_to_property_id IS NOT NULL)),
    CONSTRAINT property_site_sources_fingerprint_chk CHECK (fingerprint ~ '^[0-9a-f]{64}$')
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_property_site_sources_active_stima
    ON property_site_sources (stima_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_property_site_sources_property
    ON property_site_sources (property_id);
CREATE INDEX IF NOT EXISTS idx_property_site_sources_contact
    ON property_site_sources (agency_id, contact_id, created_at);

-- Stessa agenzia fra provenienza, immobile e stima (pattern 036/082/083).
CREATE OR REPLACE FUNCTION property_site_sources_scope() RETURNS trigger AS $fn$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM properties p WHERE p.id = NEW.property_id AND p.agency_id = NEW.agency_id) THEN
        RAISE EXCEPTION 'property % does not belong to agency %', NEW.property_id, NEW.agency_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM stime s WHERE s.id = NEW.stima_id AND s.agency_id = NEW.agency_id) THEN
        RAISE EXCEPTION 'stima % does not belong to agency %', NEW.stima_id, NEW.agency_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.relinked_to_property_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM properties p WHERE p.id = NEW.relinked_to_property_id AND p.agency_id = NEW.agency_id) THEN
        RAISE EXCEPTION 'property % does not belong to agency %', NEW.relinked_to_property_id, NEW.agency_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_property_site_sources_scope ON property_site_sources;
CREATE TRIGGER trg_property_site_sources_scope
    BEFORE INSERT OR UPDATE OF agency_id, property_id, stima_id, relinked_to_property_id ON property_site_sources
    FOR EACH ROW EXECUTE FUNCTION property_site_sources_scope();

-- Cestino: NESSUNA guardia nuova nel database. La funzione della 086 resta
-- sua (la down della 086 la toglie da sola, con le sue tabelle); la
-- provenienza non scrive mai su un immobile nel Cestino per costruzione
-- (property/site_sync.py lo esclude in ogni percorso: ritentativo, stima
-- dettagliata, Applica, collegamento esplicito).
