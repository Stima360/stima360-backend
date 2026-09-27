-- 075 - A30-10: le colonne della coda INBOUND (Google -> Agenda). ADDITIVA,
-- solo su `appointment_calendar_sync`: nessuna nuova tabella, nessun tocco a
-- `appointments` o `appointment_events`.
--
-- DUE CODE, SEMANTICAMENTE INDIPENDENTI (D6 del gate A30-10B). La coda
-- OUTBOUND (074/A30-9A) resta `attempt_count`, `next_attempt_at`,
-- `claim_token`, `claimed_at`, `last_error_code`: "quando devo SCRIVERE su
-- Google". Questa migration ne apre una seconda, con le proprie colonne, per
-- "quando devo LEGGERE da Google": nessuna delle due tocca le colonne
-- dell'altra, nessuno dei due claim puo' essere confuso con l'altro.
--
--   inbound_next_check_at   quando questa riga torna eleggibile per una
--                           lettura (fairness/backoff INBOUND, indipendente
--                           dal backoff outbound);
--   inbound_checked_at      l'ultima volta che una lettura e' stata
--                           completata (successo o no-op) su questa riga;
--   inbound_attempt_count   i tentativi di LETTURA falliti consecutivi (mai
--                           quelli di scrittura: quelli sono
--                           `attempt_count`);
--   inbound_claim_token /
--   inbound_claimed_at      lo stesso schema di lease del claim outbound
--                           (FOR UPDATE SKIP LOCKED, claim_token casuale,
--                           lease scaduto = ripreso da un altro worker), ma
--                           una coppia di colonne PROPRIA: un claim inbound
--                           in corso non e' mai visibile al claim outbound e
--                           viceversa;
--   inbound_last_error_code il codice sanitizzato dell'ultimo errore di
--                           LETTURA (mai il corpo Google, mai un token).
--
-- Deliberatamente NESSUNA `inbound_status`: l'eleggibilita' di una riga per
-- l'inbound si deriva da `remote_connection_id`, dallo stato della riga viva
-- (`scheduled`/`confirmed`, letto da `appointments` al momento del claim, non
-- specchiato qui) e da `inbound_next_check_at`; un'ulteriore colonna di stato
-- duplicherebbe quell'informazione senza risolvere nessuna race in piu' (vedi
-- A30-10A §4, DATA MODEL VERDICT).
--
-- La transazione e la riga in `schema_migrations` le gestisce il runner P26
-- (convenzione dalla 027): questo file non apre ne' chiude una transazione.

ALTER TABLE appointment_calendar_sync
    ADD COLUMN IF NOT EXISTS inbound_next_check_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS inbound_checked_at       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS inbound_attempt_count    INTEGER      NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS inbound_claim_token      UUID,
    ADD COLUMN IF NOT EXISTS inbound_claimed_at       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS inbound_last_error_code  VARCHAR(64);

DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'appointment_calendar_sync_inbound_attempts_chk') THEN
        ALTER TABLE appointment_calendar_sync
            ADD CONSTRAINT appointment_calendar_sync_inbound_attempts_chk
                CHECK (inbound_attempt_count >= 0);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'appointment_calendar_sync_inbound_claim_pair_chk') THEN
        ALTER TABLE appointment_calendar_sync
            ADD CONSTRAINT appointment_calendar_sync_inbound_claim_pair_chk
                CHECK ((inbound_claim_token IS NULL) = (inbound_claimed_at IS NULL));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'appointment_calendar_sync_inbound_error_code_chk') THEN
        ALTER TABLE appointment_calendar_sync
            ADD CONSTRAINT appointment_calendar_sync_inbound_error_code_chk
                CHECK (inbound_last_error_code IS NULL
                       OR inbound_last_error_code ~ '^[a-z0-9_]{1,64}$');
    END IF;
END
$do$;

-- Indice 1: la scansione delle righe eleggibili, in ordine di scadenza (la
-- fairness richiesta dal gate: `inbound_next_check_at, id`, mai per id nudo,
-- cosi' una riga in fondo alla tabella non e' mai affamata da chi sta
-- davanti). Parziale su `remote_connection_id IS NOT NULL`: solo le mapping
-- con una connessione sono mai lette in inbound.
CREATE INDEX IF NOT EXISTS idx_appointment_calendar_sync_inbound_due
    ON appointment_calendar_sync (inbound_next_check_at, id)
    WHERE remote_connection_id IS NOT NULL;

-- Indice 2: i claim inbound da recuperare (lease scaduto), stesso schema
-- dell'indice outbound `idx_appointment_calendar_sync_claimed` ma sulla
-- colonna propria.
CREATE INDEX IF NOT EXISTS idx_appointment_calendar_sync_inbound_claimed
    ON appointment_calendar_sync (inbound_claimed_at)
    WHERE inbound_claim_token IS NOT NULL;
