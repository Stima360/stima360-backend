-- Down for 059. Removes the alias table and nothing else.
--
-- 059 created ONE table and two indexes, and altered nothing that existed
-- before it. L'inverso e' quindi esatto: si cancella cio' che ha creato e si
-- lascia intatto ogni oggetto di P26, P27-1 e P27-5.
--
-- COSA SIGNIFICA CANCELLARE QUESTA TABELLA
--
-- Si perde la dichiarazione di quali nomi in ingresso appartengono a quale
-- territorio. Non si perde ne' il territorio (058) ne' la sua assegnazione: la
-- FK punta VERSO `network_territories`, quindi cancellare la figlia la libera e
-- non tocca nulla di la'. E non si perde la storia degli ATTI: chi ha
-- dichiarato quale alias e quando resta in `platform_audit_log`, che questo
-- file non nomina.
--
-- Conseguenza operativa, dichiarata: dopo questo down il routing di P27-6 non
-- trova piu' nessun alias e ogni stima pubblica va all'agenzia di ripiego -
-- cioe' esattamente il comportamento congelato da P26-1, che e' la ragione per
-- cui il ripiego esiste ed e' l'unico stato in cui questo down lascia il
-- sistema. Non resta nessuna stima senza proprietario.
--
-- Down files bracket their own transaction; the runner owns none here.

BEGIN;

-- Nessun CASCADE, deliberatamente: niente dovrebbe dipendere da questa
-- tabella, e se qualcosa lo facesse questo file deve fallire e dirlo invece di
-- portarsi via in silenzio quel qualcosa. Gli indici se ne vanno con la
-- tabella; nominarli sarebbe rumore.
DROP TABLE IF EXISTS network_territory_aliases;

-- La riga di ledger, come ogni down della serie: la 059 torna applicabile.
-- La chiave e' lo STEM del file e non il numero, come in 058_down.
DELETE FROM schema_migrations WHERE version = '059_p27_territory_aliases';

COMMIT;
