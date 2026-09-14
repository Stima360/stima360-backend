-- Down for 060. Removes the two acting columns and nothing else.
--
-- 060 ha aggiunto DUE colonne a `operator_sessions`, una FK, un CHECK e un
-- indice parziale. L'inverso e' quindi esatto: si tolgono le colonne - e con
-- loro, automaticamente, il vincolo e l'indice che le nominano - e si lascia
-- intatto ogni oggetto di P26 e P27.
--
-- COSA SIGNIFICA TOGLIERE QUESTE COLONNE
--
-- Le sessioni restano vive: nessuna riga viene cancellata, nessun operatore
-- viene disconnesso. Cio' che si perde e' il fatto che qualcuno stesse
-- operando dentro un'agenzia: alla richiesta successiva quella sessione torna
-- a essere scopata dalla sua membership, e un platform admin che non ne ha
-- torna a ricevere 403 su `/api/core` - cioe' esattamente lo stato congelato da
-- P27-1 decisione D1, che e' l'unico stato in cui questo down lascia il
-- sistema.
--
-- Non si perde la storia degli ATTI: chi e' entrato in quale agenzia e quando
-- resta in `platform_audit_log`, che questo file non nomina.
--
-- `agency_memberships` non compare qui per la stessa ragione per cui non
-- compare nella up: P28 non ne ha mai scritta una.
--
-- Down files bracket their own transaction; the runner owns none here.

BEGIN;

-- Le colonne si portano dietro il proprio vincolo e il proprio indice: sono
-- definiti su di esse e non esistono senza. Nominarli separatamente sarebbe
-- rumore, e un DROP CONSTRAINT su un vincolo gia' sparito farebbe fallire
-- questo file per un motivo che non e' un problema.
ALTER TABLE operator_sessions DROP COLUMN IF EXISTS acting_agency_id;
ALTER TABLE operator_sessions DROP COLUMN IF EXISTS acting_entered_at;

-- La riga di ledger, come ogni down della serie: la 060 torna applicabile.
-- La chiave e' lo STEM del file e non il numero, come in 058_down e 059_down.
DELETE FROM schema_migrations WHERE version = '060_p28_acting_agency_context';

COMMIT;
