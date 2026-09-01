-- STIME - schema gap fix (pre-esistente, non introdotto da P17).
--
-- main.py (INSERT INTO stime / SELECT ... s.consenso_marketing) e il parent
-- pre-P17 80b8f9a usano gia' stime.consenso_marketing e
-- stime.consenso_marketing_at, ma nessuna migration le ha mai aggiunte alla
-- tabella legacy `stime` (creata fuori da migrations/, vedi
-- database.py::crea_tabella_stime, essa stessa gia' disallineata rispetto
-- allo schema live). Questa migration aggiunge esclusivamente le due
-- colonne mancanti, senza toccare nessun'altra colonna o riga esistente.
--
-- Tipo e nullabilita' rispecchiano l'equivalente gia' presente su contacts
-- (migrations/001_core_contacts_leads.sql): BOOLEAN semplice, nessun valore
-- iniziale imposto dallo schema. Nessun valore FALSE iniziale e' stato
-- inventato: il codice in main.py scrive sempre un booleano esplicito ad
-- ogni nuovo INSERT, quindi non serve alcuno schema-level fallback per le
-- righe future, e assegnare retroattivamente FALSE alle righe storiche
-- (dove il consenso non era nemmeno un concetto tracciato) sarebbe
-- un'assunzione sui dati che questa migration non e' autorizzata a fare.

BEGIN;

ALTER TABLE stime
    ADD COLUMN IF NOT EXISTS consenso_marketing BOOLEAN;

ALTER TABLE stime
    ADD COLUMN IF NOT EXISTS consenso_marketing_at TIMESTAMPTZ;

COMMIT;
