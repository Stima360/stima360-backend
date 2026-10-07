-- Down for 088. Toglie la ricezione degli invii del sito (`site_submissions`).
--
-- Le 28 colonne di `stime_dettagliate` NON si tolgono: la up le aggiunge solo
-- dove mancano e la down non puo' sapere quali esistessero gia' (su un
-- database dove `database.py` le aveva create); toglierle cancellerebbe dati
-- del sito. Restano, vuote o con le stime dettagliate ricevute nel frattempo.
--
-- Si FERMA se ci sono invii non ancora trasferiti (pending o failed): sono
-- l'unica copia dei valori dichiarati. Si recuperano prima
-- (`scripts/site_sync_recover.py --apply`).

BEGIN;

DO $do$
BEGIN
    IF to_regclass('public.site_submissions') IS NOT NULL
       AND EXISTS (SELECT 1 FROM site_submissions WHERE status IN ('pending', 'failed')) THEN
        RAISE EXCEPTION '088 down: esistono invii del sito non trasferiti (pending/failed); nessuna modifica eseguita';
    END IF;
END
$do$;

DROP TABLE IF EXISTS site_submissions;
DROP FUNCTION IF EXISTS site_submissions_scope();

COMMIT;
