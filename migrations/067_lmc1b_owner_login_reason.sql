-- LMC-1B: `owner_login_link`, il motivo dell'email con il magic link owner.
--
-- Additive. Non crea tabelle, non inserisce righe, non cancella nulla. Allarga
-- UN vincolo CHECK di `communication_messages` (064) di UN valore.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- ---------------------------------------------------------------------------
-- PERCHE' UN MOTIVO NUOVO E NON UNO ESISTENTE
--
-- `reason_code` dice PERCHE' una comunicazione parte, ed e' il campo su cui il
-- ledger si legge a posteriori: quante email di servizio, di che tipo, a chi.
-- Riusare `operator_manual` per un invio che nessun operatore ha chiesto, o
-- `stima_pdf` per una email che non porta nessun PDF, renderebbe quel registro
-- una fonte che mente. Il gate del consenso non c'entra - guarda
-- `communication_type`, non il motivo - quindi il valore nuovo non allarga
-- nessun permesso: rende dicibile una cosa che prima non si poteva dire.
--
-- `owner_login_link` e' di SERVIZIO per costruzione: e' la risposta a una
-- richiesta esplicita del proprietario, non un contatto commerciale. Resta
-- sotto `communication_type='service'`, e il dispatcher continua a interrogare
-- il consenso solo per `marketing`.
--
-- COSA TOCCA, E COSA NO
--
-- Solo il vincolo `communication_messages_reason_code_chk`. Il DROP e il
-- successivo ADD sono la sola forma in cui PostgreSQL esprime "questo insieme
-- ora ha un elemento in piu'": non esiste ALTER CONSTRAINT per un CHECK. La
-- migration 064 resta sul disco esattamente com'e' - le migration storiche non
-- si riscrivono - e questo file e' la sua modifica dichiarata.
--
-- Nessuna riga viene letta, scritta o convalidata: il nuovo insieme e' un
-- SOVRAINSIEME del precedente, quindi ogni riga gia' presente lo soddisfa gia'
-- e `ADD CONSTRAINT` non puo' fallire per i dati. La sonda in fondo lo verifica
-- invece di darlo per scontato.
-- ---------------------------------------------------------------------------

ALTER TABLE communication_messages
    DROP CONSTRAINT IF EXISTS communication_messages_reason_code_chk;

ALTER TABLE communication_messages
    ADD CONSTRAINT communication_messages_reason_code_chk
        CHECK (reason_code IN (
            'stima_pdf', 'operator_manual', 'operator_reply',
            'm1', 'm2', 'm3', 'm4', 'm5',
            'owner_login_link'
        ));

-- ---------------------------------------------------------------------------
-- Verifica: il vincolo esiste, ammette il valore nuovo e NON ha perso nessuno
-- dei precedenti. Un DROP riuscito seguito da un ADD sbagliato lascerebbe la
-- colonna piu' larga di quanto chiunque creda, ed e' esattamente il difetto
-- che un CHECK ricreato a mano puo' introdurre.
-- ---------------------------------------------------------------------------

DO $do$
DECLARE
    v_def  text;
    v_atteso text;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO v_def
      FROM pg_constraint
     WHERE conname  = 'communication_messages_reason_code_chk'
       AND conrelid = 'public.communication_messages'::regclass;

    IF v_def IS NULL THEN
        RAISE EXCEPTION
            'LMC-1B 067: communication_messages_reason_code_chk is missing after the ALTER';
    END IF;

    FOREACH v_atteso IN ARRAY ARRAY[
        'stima_pdf', 'operator_manual', 'operator_reply',
        'm1', 'm2', 'm3', 'm4', 'm5', 'owner_login_link'
    ] LOOP
        IF position('''' || v_atteso || '''' IN v_def) = 0 THEN
            RAISE EXCEPTION
                'LMC-1B 067: reason code % is no longer admitted by the CHECK: %',
                v_atteso, v_def;
        END IF;
    END LOOP;

    IF position('''admin_lead_alert''' IN v_def) > 0 THEN
        RAISE EXCEPTION
            'LMC-1B 067: admin_lead_alert is outside this ledger and must not be admitted';
    END IF;
END
$do$;
