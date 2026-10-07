-- Refuse to erase live recovery state. Export/private restore required first.
LOCK TABLE public_submission_receipts IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public_submission_receipts) THEN
        RAISE EXCEPTION 'Cannot rollback populated public submission receipts';
    END IF;
END $$;
DROP TRIGGER public_submission_stima_purge ON stime;
DROP TRIGGER public_submission_detail_purge ON stime_dettagliate;
DROP FUNCTION void_public_submission_receipt();
DROP TABLE public_submission_receipts;
DROP FUNCTION enforce_public_submission_parent();
