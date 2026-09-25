-- 073 - A30-2P: la FACADE LMC-15 nell'Agenda. Prepara `appointments` a
-- ricevere i sopralluoghi che arrivano dalle rotte LMC-15
-- (`/api/acquisition/...`), che non conoscono un agente.
--
-- NON aggiunge tabelle, colonne, funzioni, trigger o indici, e non tocca
-- nessuna riga: ridefinisce due CHECK della 072 e ne aggiunge uno. Non tocca
-- `stima_inspections`, `stime`, `stime_dettagliate`, `property_visits`.
--
-- COSA CAMBIA
--
--   * `appointments_source_chk`: nuova fonte `lmc15_facade`.
--   * `appointments_agent_required_chk`: TUTTE le regole della 072 restano;
--     si aggiunge UNA sola eccezione, per le righe della facade LMC-15:
--     `scheduled` o `completed` senza agente, solo se sono un sopralluogo
--     (`inspection`) collegato alla sua riga `stima_inspections`. LMC-15 non
--     ha un agente e l'agente non si inferisce mai (Q4). `confirmed` resta
--     escluso di proposito: si conferma solo con un agente.
--   * `appointments_lmc15_facade_chk` (nuovo): una riga `lmc15_facade` e'
--     SEMPRE un sopralluogo con `stima_id`, `stima_inspection_id` e
--     `source_record_id`. Poiche' `stima_inspection_id` e'
--     `ON DELETE SET NULL`, questo CHECK fa anche fallire la DELETE di una
--     riga `stima_inspections` collegata a una riga della facade: a runtime
--     non esiste una DELETE su `stima_inspections`, ed e' voluto che non ne
--     passi nessuna a mano.
--
-- COSA NON C'E' QUI, DI PROPOSITO
--
--   * Nessuna eccezione per le righe native dell'Agenda (`crm_manual` e le
--     altre fonti): le loro regole sono quelle della 072, identiche.
--   * Le compatibilita' di comportamento della facade (completamento prima
--     dell'inizio, `completed_at` precedente a `start_at`, motivo di annullo
--     facoltativo) NON sono regole del database: vivono solo nel modulo
--     facade del service.
--   * Nessuna riga esistente ha `source = 'lmc15_facade'`: i tre CHECK
--     valgono subito su tutta la tabella. Le righe del backfill
--     (`stima_inspections_backfill`) restano valide come prima.
--
-- La transazione e la riga in `schema_migrations` le gestisce il runner P26
-- (convenzione dalla 027): questo file non apre ne' chiude una transazione.

ALTER TABLE appointments DROP CONSTRAINT appointments_source_chk;
ALTER TABLE appointments ADD CONSTRAINT appointments_source_chk CHECK (source IN (
    'crm_manual', 'legacy_stime_dettagliate', 'stima_inspections_backfill',
    'booking_link', 'system', 'a30_test', 'lmc15_facade'));

ALTER TABLE appointments DROP CONSTRAINT appointments_agent_required_chk;
ALTER TABLE appointments ADD CONSTRAINT appointments_agent_required_chk CHECK (
    assigned_user_id IS NOT NULL
    OR status IN ('requested', 'cancelled', 'rescheduled')
    OR (status IN ('completed', 'no_show')
        AND source IN ('legacy_stime_dettagliate', 'stima_inspections_backfill'))
    OR (status IN ('scheduled', 'completed')
        AND source = 'lmc15_facade'
        AND appointment_type = 'inspection'
        AND stima_inspection_id IS NOT NULL));

ALTER TABLE appointments ADD CONSTRAINT appointments_lmc15_facade_chk CHECK (
    source <> 'lmc15_facade'
    OR (
        appointment_type = 'inspection'
        AND stima_id IS NOT NULL
        AND stima_inspection_id IS NOT NULL
        AND source_record_id IS NOT NULL
    ));
