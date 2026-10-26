-- ELIMINA OPERATORE - eliminazione LOGICA di un account operatore.
--
-- Un operatore gia' escluso dalla Rete (`status = 'disabled'`) puo' essere
-- ELIMINATO dal Super Admin designato: esce dagli elenchi operativi, non accede
-- e non e' assegnabile, ma la riga resta. Nessun contatto, immobile, incarico,
-- appuntamento, membership o riga di storico viene toccato: le chiavi esterne
-- verso `operator_users` continuano a puntare alla stessa riga.
--
-- Additiva: due colonne NULLABLE, nessun backfill. NULL = non eliminato.
-- Il ripristino riporta `deleted_at` a NULL (l'account resta `disabled` finche'
-- non viene riattivato con il comando gia' esistente).
-- Un eliminato e' sempre `disabled`: lo dice il CHECK, non solo il codice.

ALTER TABLE operator_users
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

ALTER TABLE operator_users
    ADD COLUMN IF NOT EXISTS deleted_by_user_id BIGINT;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'operator_users_deleted_disabled_chk') THEN
        ALTER TABLE operator_users
            ADD CONSTRAINT operator_users_deleted_disabled_chk
            CHECK (deleted_at IS NULL OR status = 'disabled');
    END IF;
END $$;
