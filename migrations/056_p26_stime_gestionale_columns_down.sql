-- Down per 056. Toglie esattamente cio' che 056 ha creato, e nient'altro.
--
-- Le due colonne sono nullable, senza vincoli e senza indici: rimuoverle
-- riporta `stime` alla forma esatta che aveva prima di 056, cioe' quella che
-- `docs/P26_BASELINE_CERTIFICATE_TEST.md` §3.0.2 descrive.
--
-- QUESTO DOWN PERDE DATI, E LO DICE. `DROP COLUMN` porta via gli stati lead e
-- le note interne che nel frattempo qualcuno avesse scritto da
-- `POST /api/admin/stime/{id}/update`. Non c'e' modo di annullare 056
-- conservandoli - la colonna che li contiene E' cio' che 056 ha creato -
-- quindi chi esegue questo file dopo che la route e' stata usata deve
-- esportarli prima. Non e' un caso ipotetico dopo il primo operatore che
-- annota un lead.
--
-- Nessuna delle altre ventotto colonne di §3.0.2 viene toccata: 056 non le ha
-- create e questo down non le riguarda.
--
-- I file di down si aprono e chiudono la transazione da soli: il runner non ha
-- un comando `down` e qui non possiede nessuna transazione.

BEGIN;

ALTER TABLE stime
    DROP COLUMN IF EXISTS note_internal;

ALTER TABLE stime
    DROP COLUMN IF EXISTS lead_status;

DELETE FROM schema_migrations
 WHERE version = '056_p26_stime_gestionale_columns';

COMMIT;
