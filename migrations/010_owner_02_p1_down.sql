-- OWNER 0.2 - P1
-- Rollback definitivo per ambiente TEST בלבד.
-- NON ESEGUIRE senza approvazione separata.
-- Il rollback è consentito solo prima dell'uso funzionale dello schema P1.

BEGIN;

DO $$
BEGIN
    IF current_database() <> 'stima360_db_test' THEN
        RAISE EXCEPTION 'Rollback OWNER 0.2 P1 bloccato: database corrente %, atteso stima360_db_test', current_database();
    END IF;

    IF current_schema() <> 'public' THEN
        RAISE EXCEPTION 'Rollback OWNER 0.2 P1 bloccato: schema corrente %, atteso public', current_schema();
    END IF;

    IF to_regclass('public.owner_shared_documents') IS NULL
       OR to_regclass('public.owner_document_reads') IS NULL
       OR to_regclass('public.owner_visit_feedback_publications') IS NULL THEN
        RAISE EXCEPTION 'Rollback OWNER 0.2 P1 bloccato: schema P1 incompleto o non applicato';
    END IF;

    IF EXISTS (SELECT 1 FROM owner_shared_documents)
       OR EXISTS (SELECT 1 FROM owner_document_reads)
       OR EXISTS (SELECT 1 FROM owner_visit_feedback_publications) THEN
        RAISE EXCEPTION 'Rollback OWNER 0.2 P1 bloccato: le nuove tabelle contengono dati';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM owner_feedback
        WHERE availability_from IS NOT NULL
           OR availability_to IS NOT NULL
           OR public_response IS NOT NULL
           OR feedback_type IN ('price_review','availability_update','document_question')
    ) THEN
        RAISE EXCEPTION 'Rollback OWNER 0.2 P1 bloccato: owner_feedback contiene dati P1';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM owner_publications
        WHERE acknowledgement_required IS TRUE
    ) THEN
        RAISE EXCEPTION 'Rollback OWNER 0.2 P1 bloccato: esistono pubblicazioni con acknowledgement_required=true';
    END IF;
END
$$;

ALTER TABLE owner_publications
    DROP COLUMN acknowledgement_required;

ALTER TABLE owner_feedback
    DROP CONSTRAINT owner_feedback_feedback_type_check;

ALTER TABLE owner_feedback
    ADD CONSTRAINT owner_feedback_feedback_type_check
        CHECK (feedback_type IN (
            'contact_request',
            'correction_request',
            'general_message',
            'strategy_feedback'
        ));

ALTER TABLE owner_feedback
    DROP CONSTRAINT owner_feedback_public_response_chk,
    DROP CONSTRAINT owner_feedback_availability_chk,
    DROP COLUMN public_response,
    DROP COLUMN availability_to,
    DROP COLUMN availability_from;

DROP TABLE owner_document_reads;
DROP TABLE owner_visit_feedback_publications;
DROP TABLE owner_shared_documents;

COMMIT;
