-- 106 - APPUNTAMENTI DI VENDITA: la tipologia `sale_appointment`.
--
-- Additiva. Nessun backfill, nessuna riga esistente letta o riscritta, nessuna
-- colonna nuova: l'appuntamento di vendita usa le colonne gia' presenti in
-- `appointments` (072), cioe' l'immobile dell'incarico (`property_id`), il
-- cliente interessato all'acquisto (`contact_id`), l'agente incaricato
-- (`assigned_user_id`) e data e ora (`start_at` / `end_at`).
--
--   1. `appointments_type_chk` si ridefinisce con UN solo valore in piu',
--      `sale_appointment`. Le dodici tipologie della 072 restano identiche.
--   2. `appointments_sale_links_chk` (nuovo): un appuntamento di vendita ha
--      SEMPRE immobile, cliente e agente, in ogni stato. Che l'immobile sia un
--      incarico in corso o scaduto (non concluso, non ritirato, non archiviato)
--      lo verifica il service (`appointments/service.py`), con la definizione
--      di incarico gia' esistente (`property/mandates.py`): il database ne
--      garantisce la presenza, non lo stato commerciale, che cambia nel tempo.
--
-- `public_booking_links` NON cambia: un appuntamento di vendita non si
-- prenota da un link pubblico (servono un incarico e un cliente scelti
-- dall'agenzia), e il suo CHECK continua a non ammetterlo.
--
-- La cancellazione definitiva dal Cestino (CESTINO-GENERALE-3) non tocca
-- questo vincolo: un immobile o un contatto con appuntamenti collegati e'
-- gia' bloccato prima di qualunque DELETE.
--
-- Transaction ownership: il runner possiede la transazione UP (versione >= 027),
-- quindi questo file non ha BEGIN/COMMIT.

ALTER TABLE appointments DROP CONSTRAINT appointments_type_chk;
ALTER TABLE appointments ADD CONSTRAINT appointments_type_chk CHECK (appointment_type IN (
    'call', 'video_call', 'seller_meeting', 'inspection', 'buyer_visit',
    'valuation_presentation', 'mandate_signing', 'proposal',
    'preliminary_contract', 'notary', 'technical', 'other',
    'sale_appointment'));

ALTER TABLE appointments ADD CONSTRAINT appointments_sale_links_chk CHECK (
    appointment_type <> 'sale_appointment'
    OR (property_id IS NOT NULL AND contact_id IS NOT NULL
        AND assigned_user_id IS NOT NULL));
