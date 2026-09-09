DO $do$
BEGIN
    RAISE EXCEPTION
        'P26-2D 038 is irreversible ownership backfill. Restore the certified pre-038 backup.';
END
$do$;
