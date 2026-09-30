-- A32-1: `appointment_reminder`, il motivo del promemoria automatico di un
-- appuntamento dell'Agenda.
--
-- Additive. Non crea tabelle, colonne o indici, non inserisce righe, non
-- cancella nulla: allarga UN vincolo CHECK di `communication_messages` (064,
-- gia' allargato dalla 067) di UN valore. Stesso schema della 067.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- ---------------------------------------------------------------------------
-- UN SOLO MOTIVO, NON UNO PER OFFSET
--
-- Il promemoria a 24h e quello (futuro) a 2h sono lo STESSO motivo: ricordare
-- un appuntamento fissato. L'offset vive in `template_key`
-- (`appointment_reminder_24h`) e in `metadata.offset`, non nel `reason_code`:
-- un valore per offset moltiplicherebbe i motivi del ledger senza dire nulla
-- di nuovo sul PERCHE' di un invio (gate A32-0/A32-1).
--
-- E' di SERVIZIO: esegue un appuntamento fissato con l'interessato. Resta sotto
-- `communication_type='service'`; il dispatcher continua a interrogare il
-- consenso solo per `marketing`. Il valore nuovo non allarga nessun permesso.
--
-- Nessuna riga viene letta o riscritta: il nuovo insieme e' un SOVRAINSIEME del
-- precedente, quindi ogni riga gia' presente lo soddisfa. La sonda in fondo lo
-- verifica invece di darlo per scontato.
-- ---------------------------------------------------------------------------

ALTER TABLE communication_messages
    DROP CONSTRAINT IF EXISTS communication_messages_reason_code_chk;

ALTER TABLE communication_messages
    ADD CONSTRAINT communication_messages_reason_code_chk
        CHECK (reason_code IN (
            'stima_pdf', 'operator_manual', 'operator_reply',
            'm1', 'm2', 'm3', 'm4', 'm5',
            'owner_login_link',
            'appointment_reminder'
        ));

-- ---------------------------------------------------------------------------
-- Verifica: il vincolo esiste, ammette il valore nuovo e NON ha perso nessuno
-- dei precedenti.
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
            'A32-1 079: communication_messages_reason_code_chk is missing after the ALTER';
    END IF;

    FOREACH v_atteso IN ARRAY ARRAY[
        'stima_pdf', 'operator_manual', 'operator_reply',
        'm1', 'm2', 'm3', 'm4', 'm5', 'owner_login_link',
        'appointment_reminder'
    ] LOOP
        IF position('''' || v_atteso || '''' IN v_def) = 0 THEN
            RAISE EXCEPTION
                'A32-1 079: reason code % is no longer admitted by the CHECK: %',
                v_atteso, v_def;
        END IF;
    END LOOP;

    IF position('''appointment_reminder_24h''' IN v_def) > 0
       OR position('''appointment_reminder_2h''' IN v_def) > 0 THEN
        RAISE EXCEPTION
            'A32-1 079: the reminder offset belongs to template_key/metadata, not to reason_code';
    END IF;
END
$do$;
