-- C3.5 - la PROVA del consenso marketing, scritta con la richiesta.
--
-- `stime.consenso_marketing` (la scelta) e `consenso_marketing_at` (l'istante,
-- dal server) esistono dalla 018. Mancava COSA la persona aveva davanti:
--
--   * consenso_marketing_testo   - il testo della casella marketing com'era
--                                  nella pagina inviata (spazi compressi);
--   * consenso_marketing_privacy_sha256 - l'impronta SHA-256 dei byte di
--                                  `privacy.html` letta dalla pagina nel momento
--                                  della richiesta (l'informativa collegata).
--
-- Additiva: due colonne NULLABLE, nessun backfill. Le stime storiche restano
-- senza: per loro la prova NON si ricostruisce (NULL = non registrata).
-- Il sito e il CRM leggono la stessa tabella `stime`; l'import del CRM copia
-- le colonne comuni, quindi la 112 va applicata a entrambi i database.

ALTER TABLE stime
    ADD COLUMN IF NOT EXISTS consenso_marketing_testo TEXT
        CONSTRAINT stime_consenso_marketing_testo_chk
        CHECK (consenso_marketing_testo IS NULL
               OR (BTRIM(consenso_marketing_testo) <> '' AND length(consenso_marketing_testo) <= 1000));

ALTER TABLE stime
    ADD COLUMN IF NOT EXISTS consenso_marketing_privacy_sha256 CHAR(64)
        CONSTRAINT stime_consenso_marketing_privacy_sha256_chk
        CHECK (consenso_marketing_privacy_sha256 IS NULL
               OR consenso_marketing_privacy_sha256 ~ '^[0-9a-f]{64}$');
