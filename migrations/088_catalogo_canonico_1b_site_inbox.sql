-- CATALOGO-CANONICO-1 (FASE D, completamento): ricezione degli invii del sito
-- e schema della stima dettagliata.
--
-- Additiva. Nessun dato esistente cambia, nessun backfill.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- ---------------------------------------------------------------------------
-- 1. stime_dettagliate: le 28 colonne che `/api/salva_stima_dettagliata`
--    scrive da sempre
--
--    Sono quelle di `database.py::migrazione_stime_dettagliate_completa()`,
--    con gli STESSI tipi: quella funzione non e' nel ledger e sul TEST non
--    risulta mai eseguita (snapshot certificato P26-0 del 2026-09-05:
--    `stime_dettagliate` ha 13 colonne; nessuna migration successiva le crea,
--    la 049 aggiunge solo `agency_id`). Senza di esse l'INSERT del dettaglio
--    fallisce con UndefinedColumn e il cliente riceve 500.
--
--    ADD COLUMN IF NOT EXISTS: dove una colonna esiste gia' (per esempio su un
--    database dove `database.py` e' stato eseguito) NON viene toccata - ne'
--    tipo, ne' dati. Il pre-check del runbook elenca le colonne presenti e i
--    loro tipi PRIMA dell'applicazione.
-- ---------------------------------------------------------------------------
ALTER TABLE stime_dettagliate
  ADD COLUMN IF NOT EXISTS nome VARCHAR(50),
  ADD COLUMN IF NOT EXISTS cognome VARCHAR(50),
  ADD COLUMN IF NOT EXISTS email VARCHAR(100),
  ADD COLUMN IF NOT EXISTS telefono VARCHAR(30),
  ADD COLUMN IF NOT EXISTS indirizzo TEXT,
  ADD COLUMN IF NOT EXISTS tipologia VARCHAR(50),
  ADD COLUMN IF NOT EXISTS mq INTEGER,
  ADD COLUMN IF NOT EXISTS piano VARCHAR(30),
  ADD COLUMN IF NOT EXISTS locali VARCHAR(50),
  ADD COLUMN IF NOT EXISTS bagni INTEGER,
  ADD COLUMN IF NOT EXISTS ascensore VARCHAR(10),
  ADD COLUMN IF NOT EXISTS stato VARCHAR(40),
  ADD COLUMN IF NOT EXISTS anno INTEGER,
  ADD COLUMN IF NOT EXISTS microzona VARCHAR(100),
  ADD COLUMN IF NOT EXISTS posizionemare VARCHAR(50),
  ADD COLUMN IF NOT EXISTS distanzamare VARCHAR(50),
  ADD COLUMN IF NOT EXISTS barrieramare VARCHAR(50),
  ADD COLUMN IF NOT EXISTS vistamare VARCHAR(80),
  ADD COLUMN IF NOT EXISTS mqgiardino INTEGER,
  ADD COLUMN IF NOT EXISTS mqgarage INTEGER,
  ADD COLUMN IF NOT EXISTS mqcantina INTEGER,
  ADD COLUMN IF NOT EXISTS mqpostoauto INTEGER,
  ADD COLUMN IF NOT EXISTS mqtaverna INTEGER,
  ADD COLUMN IF NOT EXISTS mqsoffitta INTEGER,
  ADD COLUMN IF NOT EXISTS mqterrazzo INTEGER,
  ADD COLUMN IF NOT EXISTS numbalconi INTEGER,
  ADD COLUMN IF NOT EXISTS altrodescrizione TEXT,
  ADD COLUMN IF NOT EXISTS pertinenze VARCHAR(200);

-- ---------------------------------------------------------------------------
-- 2. site_submissions: ogni invio del sito, conservato PRIMA del trasferimento
--
--    Una riga per stima rapida (`quick`, chiave `stima_id`) e una per stima
--    dettagliata (`detail`, chiave `detail_id`), scritta subito dopo il
--    salvataggio del sito e INDIPENDENTE dal trasferimento nella scheda:
--
--      declared        i campi dell'immobile COME il form li ha inviati
--                      (nessun dato di contatto: nome, email, telefono e
--                      consensi restano in `stime` / contatti);
--      declared_fields dettagliata: i campi che il sito dichiara modificati o
--                      confermati dal cliente (`campi_dichiarati`), se li manda;
--      prefill         dettagliata: i valori che il sito aveva precompilato
--                      (`/api/prefill`, cioe' `stime` con default e interi),
--                      per distinguere un valore lasciato com'era da uno
--                      cambiato dal cliente;
--      client_request_id  l'identita' stabile della richiesta, se il sito la
--                      manda (riusata nei ritentativi);
--      status          pending -> synced | skipped (con il motivo) | failed
--                      (con il tipo di errore e i tentativi): un invio non
--                      trasferito resta visibile e si recupera con
--                      `scripts/site_sync_recover.py`, mai in silenzio.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS site_submissions (
    id                 BIGSERIAL    PRIMARY KEY,
    agency_id          BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    kind               VARCHAR(10)  NOT NULL,
    stima_id           INTEGER      REFERENCES stime(id) ON DELETE CASCADE,
    detail_id          INTEGER      REFERENCES stime_dettagliate(id) ON DELETE CASCADE,
    client_request_id  UUID,
    declared           JSONB        NOT NULL DEFAULT '{}'::jsonb,
    declared_fields    JSONB,
    prefill            JSONB,
    contact_id         BIGINT       REFERENCES contacts(id) ON DELETE SET NULL,
    lead_id            BIGINT       REFERENCES leads(id) ON DELETE SET NULL,
    status             VARCHAR(12)  NOT NULL DEFAULT 'pending',
    reason             VARCHAR(60),
    attempts           INTEGER      NOT NULL DEFAULT 0,
    last_error         VARCHAR(120),
    property_id        BIGINT       REFERENCES properties(id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    synced_at          TIMESTAMPTZ,
    CONSTRAINT site_submissions_kind_chk CHECK (kind IN ('quick', 'detail')),
    CONSTRAINT site_submissions_keys_chk CHECK (
        (kind = 'quick' AND stima_id IS NOT NULL AND detail_id IS NULL)
        OR (kind = 'detail' AND detail_id IS NOT NULL)),
    CONSTRAINT site_submissions_status_chk CHECK (status IN ('pending', 'synced', 'skipped', 'failed')),
    CONSTRAINT site_submissions_attempts_chk CHECK (attempts >= 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_site_submissions_quick
    ON site_submissions (stima_id) WHERE kind = 'quick';
CREATE UNIQUE INDEX IF NOT EXISTS uq_site_submissions_detail
    ON site_submissions (detail_id) WHERE kind = 'detail';
CREATE INDEX IF NOT EXISTS idx_site_submissions_status
    ON site_submissions (status, created_at);
CREATE INDEX IF NOT EXISTS idx_site_submissions_request
    ON site_submissions (agency_id, client_request_id) WHERE client_request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_site_submissions_stima
    ON site_submissions (stima_id);

-- La stessa agenzia della stima (e della scheda, quando c'e'). Si controlla
-- solo il riferimento che cambia: un SET NULL della chiave esterna non deve
-- ricontrollare una stima cancellata nello stesso statement.
CREATE OR REPLACE FUNCTION site_submissions_scope() RETURNS trigger AS $fn$
DECLARE
    nuova BOOLEAN := TG_OP = 'INSERT';
BEGIN
    IF NEW.stima_id IS NOT NULL
       AND (nuova OR NEW.stima_id IS DISTINCT FROM OLD.stima_id OR NEW.agency_id IS DISTINCT FROM OLD.agency_id)
       AND NOT EXISTS (SELECT 1 FROM stime s WHERE s.id = NEW.stima_id AND s.agency_id = NEW.agency_id) THEN
        RAISE EXCEPTION 'stima % does not belong to agency %', NEW.stima_id, NEW.agency_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.property_id IS NOT NULL
       AND (nuova OR NEW.property_id IS DISTINCT FROM OLD.property_id OR NEW.agency_id IS DISTINCT FROM OLD.agency_id)
       AND NOT EXISTS (SELECT 1 FROM properties p WHERE p.id = NEW.property_id AND p.agency_id = NEW.agency_id) THEN
        RAISE EXCEPTION 'property % does not belong to agency %', NEW.property_id, NEW.agency_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_site_submissions_scope ON site_submissions;
CREATE TRIGGER trg_site_submissions_scope
    BEFORE INSERT OR UPDATE OF agency_id, stima_id, property_id ON site_submissions
    FOR EACH ROW EXECUTE FUNCTION site_submissions_scope();

