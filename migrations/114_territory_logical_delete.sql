-- ELIMINA TERRITORIO - eliminazione LOGICA di un territorio della Rete.
--
-- Il Super Admin designato puo' ELIMINARE un territorio che nessuna agenzia
-- presidia: esce dall'elenco operativo e non riceve nuove assegnazioni, ma la
-- riga resta. Nessuna assegnazione, alias, edificio, immobile, contatto,
-- incarico o riga di storico viene toccato.
--
-- Additiva: due colonne NULLABLE, nessun backfill. NULL = non eliminato.
-- Il ripristino riporta `deleted_at` a NULL.

ALTER TABLE network_territories
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

ALTER TABLE network_territories
    ADD COLUMN IF NOT EXISTS deleted_by_user_id BIGINT;
