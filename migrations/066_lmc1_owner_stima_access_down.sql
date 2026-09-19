-- Down for 066. Removes the pre-incarico owner/stima grant and nothing else.
--
-- La 066 ha creato UNA tabella, due indici, una funzione e un trigger, e non
-- ha alterato nulla di preesistente. L'inverso e' esatto: `owner_accounts`,
-- `owner_property_access`, `owner_audit_log`, `stime` e `contacts` non
-- vengono toccati.
--
-- COSA SIGNIFICA TOGLIERE QUESTA TABELLA
--
-- Distrugge i grant pre-incarico: chi era collegato alla propria stima non lo
-- e' piu'. Le righe si ricostruiscono rieseguendo il provisioning (e' lo
-- stesso, idempotente), ma la storia delle revoche fatte a mano no. Prima di
-- eseguirlo su un ambiente con dati, esportare `owner_stima_access`.
--
-- Nessun CASCADE sul DROP: gli indici e il trigger dipendono dalla tabella e
-- cadono con lei; nient'altro nel database la referenzia.

BEGIN;

DROP TRIGGER IF EXISTS trg_owner_stima_access_agency_integrity ON owner_stima_access;
DROP FUNCTION IF EXISTS owner_stima_access_agency_integrity();
DROP TABLE IF EXISTS owner_stima_access;

DELETE FROM schema_migrations WHERE version = '066_lmc1_owner_stima_access';

COMMIT;
