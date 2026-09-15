-- Down for 063. Removes the eight projection columns and nothing else.
--
-- 063 ha aggiunto OTTO colonne a `contacts`, due FK e tre CHECK. L'inverso e'
-- esatto: si tolgono le colonne - e con loro, automaticamente, i vincoli che le
-- nominano - e si lascia intatto tutto il resto.
--
-- COSA SIGNIFICA TOGLIERE QUESTE COLONNE
--
-- Si perde la parte NUOVA dello stato corrente: la data di revoca, la
-- provenienza, il riferimento alla notice, e l'intero stato privacy/termini.
-- Non si perde lo storico, che vive in `consent_events` (062) e non e'
-- nominato da questo file.
--
-- Soprattutto NON si perde `contacts.marketing_consent` ne'
-- `contacts.marketing_consent_at`: 063 non le ha create e questo down non le
-- nomina. P24 `database_revival` continua a leggere la prima e a funzionare
-- esattamente come prima di P29.
--
-- L'unica conseguenza funzionale: un contatto che era `revoked` torna a essere
-- indistinguibile da uno `never_given`, perche' la colonna che li separava non
-- c'e' piu'. Entrambi restano FALSE, quindi nessun invio marketing diventa
-- possibile per effetto di questo down - si perde la spiegazione, non la
-- protezione.
--
-- ORDINE
--
-- Questo down deve girare PRIMA di quello di 061: le due FK verso
-- `consent_notices` partono da qui, e finche' esistono il DROP di quella
-- tabella fallisce.
--
-- I tre CHECK e le due FK non sono nominati: sono definiti sulle colonne che
-- questo file rimuove e non esistono senza di esse. Un DROP CONSTRAINT su un
-- vincolo gia' sparito farebbe fallire questo file per un motivo che non e' un
-- problema.
--
-- Down files bracket their own transaction; the runner owns none here.

BEGIN;

ALTER TABLE contacts DROP COLUMN IF EXISTS marketing_revoked_at;
ALTER TABLE contacts DROP COLUMN IF EXISTS marketing_consent_source;
ALTER TABLE contacts DROP COLUMN IF EXISTS marketing_consent_notice_id;

ALTER TABLE contacts DROP COLUMN IF EXISTS privacy_terms_accepted;
ALTER TABLE contacts DROP COLUMN IF EXISTS privacy_terms_accepted_at;
ALTER TABLE contacts DROP COLUMN IF EXISTS privacy_terms_revoked_at;
ALTER TABLE contacts DROP COLUMN IF EXISTS privacy_terms_source;
ALTER TABLE contacts DROP COLUMN IF EXISTS privacy_terms_notice_id;

DELETE FROM schema_migrations WHERE version = '063_p29_contacts_consent_projection';

COMMIT;
