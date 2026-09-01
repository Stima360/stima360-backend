-- STIME - schema gap fix (pre-esistente, non introdotto da P17).
--
-- main.py (secondo UPDATE stime del flusso /api/salva_stima, blocco vista
-- mare) scrive gia' stime.vistamaredettaglio, e valuation.py::normalize_
-- vista_mare / pdf_report.py la leggono come stringa descrittiva breve
-- (parole chiave tipo "fronte", "laterale", "scorcio", ecc.), esattamente
-- come la colonna gemella vistamare gia' esistente sulla stessa tabella.
-- Nessuna migration ha mai creato questa colonna: manca sia dalle
-- migrations/ versionate sia dal DDL legacy stale in
-- database.py::crea_tabella_stime (che infatti non la contiene).
--
-- Tipo: VARCHAR(50), identico alla colonna gemella vistamare nello stesso
-- DDL legacy (stessa famiglia di campi "vista mare", stesso uso come
-- etichetta breve, non testo libero lungo). Nessun DEFAULT: main.py scrive
-- sempre una stringa esplicita (eventualmente vuota) ad ogni richiesta,
-- quindi non serve un valore iniziale a livello di schema.

BEGIN;

ALTER TABLE stime
    ADD COLUMN IF NOT EXISTS vistamaredettaglio VARCHAR(50);

COMMIT;
