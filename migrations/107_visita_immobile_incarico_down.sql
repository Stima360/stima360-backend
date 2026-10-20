-- Down for 107. Toglie il trigger e la funzione di guardia della visita
-- immobile. Nessuna riga toccata: le visite restano tutte, con i loro
-- collegamenti; torna solo possibile, nel database, una visita nuova senza
-- incarico (il service continua a rifiutarla finche' il codice resta questo).

BEGIN;

DROP TRIGGER IF EXISTS trg_appointments_visit_links_guard ON appointments;
DROP FUNCTION IF EXISTS appointments_visit_links_guard();

COMMIT;
