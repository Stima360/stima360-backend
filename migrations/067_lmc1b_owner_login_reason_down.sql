-- Down for 067. Riporta `reason_code` agli otto valori della 064.
--
-- RIFIUTA SE IL LEDGER CONTIENE GIA' UN MESSAGGIO DI LOGIN.
--
-- Il ledger e' append-only: nessun percorso dell'applicazione cancella una riga
-- di `communication_messages`, e questo file non fa eccezione. Restringere il
-- CHECK con righe `owner_login_link` gia' scritte significherebbe una di due
-- cose, entrambe inaccettabili: l'ADD CONSTRAINT fallisce a meta' rollback
-- lasciando la tabella senza vincolo, oppure qualcuno "risolve" cancellando le
-- righe, cioe' falsificando il registro degli invii.
--
-- Quindi: si controlla prima, e se ce ne sono ci si ferma dicendo quante e
-- lasciando tutto com'e'. La via d'uscita non e' automatica ed e' giusto che
-- non lo sia: esportare o riclassificare quei messaggi e' una decisione sui
-- dati, non un effetto collaterale di un rollback di schema.

BEGIN;

DO $$
DECLARE
    v_righe INTEGER;
BEGIN
    SELECT count(*) INTO v_righe
      FROM communication_messages
     WHERE reason_code = 'owner_login_link';
    IF v_righe > 0 THEN
        RAISE EXCEPTION
            'LMC-1B 067 down: % message(s) carry reason_code owner_login_link and would be orphaned by restoring the eight-value CHECK. Nothing has been changed. Export or reclassify them deliberately first.',
            v_righe;
    END IF;
END
$$;

ALTER TABLE communication_messages
    DROP CONSTRAINT IF EXISTS communication_messages_reason_code_chk;

ALTER TABLE communication_messages
    ADD CONSTRAINT communication_messages_reason_code_chk
        CHECK (reason_code IN (
            'stima_pdf', 'operator_manual', 'operator_reply',
            'm1', 'm2', 'm3', 'm4', 'm5'
        ));

DELETE FROM schema_migrations WHERE version = '067_lmc1b_owner_login_reason';

COMMIT;
