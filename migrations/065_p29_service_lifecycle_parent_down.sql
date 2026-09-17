-- Down for 065. Rimette `contact_id` NOT NULL, e RIFIUTA se non puo' farlo
-- senza perdere dati.
--
-- IL CONTROLLO VIENE PRIMA DI QUALUNQUE DDL, E NON E' UNA CORTESIA
--
-- Se esiste anche una sola riga con `contact_id IS NULL`, questo file solleva
-- e non modifica NIENTE. L'alternativa sarebbe cancellare quelle righe per
-- poter rimettere il vincolo, oppure inventargli un contatto: la prima
-- distrugge comunicazioni realmente inviate a persone reali, la seconda scrive
-- nel CRM un contatto che non esiste. Nessuna delle due e' un downgrade, sono
-- due modi diversi di perdere il dato.
--
-- Chi vuole davvero tornare indietro deve prima decidere, esplicitamente, cosa
-- fare di quelle righe - esportarle, o collegarle a un contatto risolto a mano
-- (e per farlo dovra' passare dal guard, che rende `contact_id` immutabile:
-- quindi esportare e riaccodare, non aggiornare). Questo file non prende quella
-- decisione al posto suo.
--
-- ORDINE INVERSO ESATTO
--
-- Il trigger prima della sua funzione, la funzione prima del ripristino del
-- guard, i CHECK prima della FK, la FK prima del NOT NULL: ogni passo toglie
-- cio' che il passo successivo potrebbe rendere invalido.
--
-- IL GUARD TORNA QUELLO DELLA 064, INTEGRALMENTE
--
-- Non "quasi": il testo qui sotto e' copiato dalla 064, dodici colonne
-- immutabili comprese, incluso il ramo DELETE con la sua domanda a un genitore
-- solo. Dopo questo down, `contact_id` e' di nuovo NOT NULL e quella domanda
-- torna ad avere sempre una risposta.
--
-- Down files bracket their own transaction; the runner owns none here.

BEGIN;

DO $$
DECLARE
    v_orfani INTEGER;
BEGIN
    SELECT count(*) INTO v_orfani
      FROM communication_messages
     WHERE contact_id IS NULL;

    IF v_orfani > 0 THEN
        RAISE EXCEPTION
            'P29-2.6E 065 down: % message(s) have contact_id IS NULL and would be destroyed or falsified by restoring NOT NULL. Nothing has been changed. Export or re-link them deliberately first.',
            v_orfani;
    END IF;
END
$$;

DROP TRIGGER IF EXISTS trg_stime_purge_contactless_messages ON stime;
DROP FUNCTION IF EXISTS stime_purge_contactless_messages();

-- Il guard della 064, testuale.
CREATE OR REPLACE FUNCTION communication_messages_guard()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_genitore_sparito BOOLEAN;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.id                   IS DISTINCT FROM OLD.id
        OR NEW.agency_id            IS DISTINCT FROM OLD.agency_id
        OR NEW.contact_id           IS DISTINCT FROM OLD.contact_id
        OR NEW.channel              IS DISTINCT FROM OLD.channel
        OR NEW.communication_type   IS DISTINCT FROM OLD.communication_type
        OR NEW.mode                 IS DISTINCT FROM OLD.mode
        OR NEW.reason_code          IS DISTINCT FROM OLD.reason_code
        OR NEW.rendered_body        IS DISTINCT FROM OLD.rendered_body
        OR NEW.subject_snapshot     IS DISTINCT FROM OLD.subject_snapshot
        OR NEW.destination_snapshot IS DISTINCT FROM OLD.destination_snapshot
        OR NEW.idempotency_key      IS DISTINCT FROM OLD.idempotency_key
        OR NEW.created_at           IS DISTINCT FROM OLD.created_at
        THEN
            RAISE EXCEPTION
                'communication_messages: identity and snapshot columns are immutable (id=%). What a message says it was is not rewritable; record a new message instead.',
                OLD.id;
        END IF;
        RETURN NEW;
    END IF;

    v_genitore_sparito :=
        NOT EXISTS (SELECT 1 FROM contacts WHERE id = OLD.contact_id);

    IF pg_trigger_depth() > 1 AND v_genitore_sparito THEN
        -- Purge: il destinatario di questo messaggio non esiste piu'. Il
        -- contenuto comunicativo personale lo segue.
        RETURN OLD;
    END IF;

    RAISE EXCEPTION
        'communication_messages: DELETE is refused (id=%). A message is removed only by physically purging the contact it was addressed to.',
        OLD.id;
END
$fn$;

ALTER TABLE communication_messages
    DROP CONSTRAINT IF EXISTS communication_messages_lifecycle_parent_chk;
ALTER TABLE communication_messages
    DROP CONSTRAINT IF EXISTS communication_messages_marketing_contact_chk;
ALTER TABLE communication_messages
    DROP CONSTRAINT IF EXISTS communication_messages_agency_fk;

ALTER TABLE communication_messages
    ALTER COLUMN contact_id SET NOT NULL;

DELETE FROM schema_migrations WHERE version = '065_p29_service_lifecycle_parent';

COMMIT;
