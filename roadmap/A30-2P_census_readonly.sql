-- =============================================================================
-- A30-2P - CENSUS READ-ONLY di stima_inspections verso appointments
-- Da eseguire SOLO su stima360_db_test, in psql:
--     psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f A30-2P_census_readonly.sql
-- Transazione READ ONLY + ROLLBACK finale: non scrive nulla. Nessuna lettura
-- di stime_dettagliate / property_visits. Nessun dato personale in output.
-- Chiave d'idempotenza del backfill:
--     source = 'stima_inspections_backfill'
--     source_record_id = 'stima_inspections:' || stima_inspections.id
-- =============================================================================
BEGIN TRANSACTION READ ONLY;

\echo '== Q0 identita: deve essere stima360_db_test, certificato, 072 presente'
SELECT current_database() AS db,
       a30_is_certified_test_database() AS certificato,
       to_regclass('public.appointments') IS NOT NULL AS appointments_presente,
       to_regclass('public.stima_inspections') IS NOT NULL AS stima_inspections_presente;

\echo '== Q1 quanti stima_inspections e in quali stati'
SELECT count(*) AS totale,
       count(*) FILTER (WHERE status = 'scheduled') AS scheduled,
       count(*) FILTER (WHERE status = 'completed') AS completed,
       count(*) FILTER (WHERE status = 'cancelled') AS cancelled
  FROM stima_inspections;
SELECT status, count(*) FROM stima_inspections GROUP BY status ORDER BY status;

\echo '== Q2 stima_id: NULL / presente ma stima non trovata / stima trovata senza agenzia'
SELECT count(*) FILTER (WHERE i.stima_id IS NOT NULL AND s.id IS NOT NULL
                          AND s.agency_id IS NOT NULL) AS con_stima_e_agenzia,
       count(*) FILTER (WHERE i.stima_id IS NULL) AS stima_id_null,
       count(*) FILTER (WHERE i.stima_id IS NOT NULL AND s.id IS NULL)
           AS stima_id_presente_stima_non_trovata,
       count(*) FILTER (WHERE s.id IS NOT NULL AND s.agency_id IS NULL)
           AS stima_trovata_agency_id_null
  FROM stima_inspections i LEFT JOIN stime s ON s.id = i.stima_id;

\echo '== Q3 casi particolari del mapping'
SELECT count(*) FILTER (WHERE scheduled_for IS NULL) AS senza_scheduled_for,
       count(*) FILTER (WHERE status = 'completed' AND scheduled_for IS NULL)
           AS completed_registrati_a_posteriori,
       count(*) FILTER (WHERE status = 'cancelled' AND cancelled_reason = 'no_show')
           AS cancelled_con_motivo_no_show,
       count(*) FILTER (WHERE status = 'cancelled' AND cancelled_reason IS NULL)
           AS cancelled_senza_motivo,
       count(*) FILTER (WHERE status = 'scheduled' AND scheduled_for < NOW())
           AS scheduled_nel_passato_mai_chiusi,
       min(COALESCE(scheduled_for, completed_at)) AS primo_istante,
       max(COALESCE(scheduled_for, completed_at)) AS ultimo_istante
  FROM stima_inspections;

\echo '== Q4 gia rappresentati in appointments'
SELECT count(*) FILTER (WHERE EXISTS (SELECT 1 FROM appointments a
                                       WHERE a.stima_inspection_id = i.id))
           AS collegati_da_appointments,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM appointments a
                                       WHERE a.source = 'stima_inspections_backfill'
                                         AND a.source_record_id = 'stima_inspections:' || i.id))
           AS gia_importati_dal_backfill
  FROM stima_inspections i;
SELECT source, status, count(*) FROM appointments
 WHERE stima_inspection_id IS NOT NULL GROUP BY source, status ORDER BY 1, 2;

\echo '== Q5 da importare (stessa query del backfill) per stato'
WITH candidati AS (
    SELECT i.*, s.agency_id AS agency_id
      FROM stima_inspections i
      JOIN stime s ON s.id = i.stima_id
     WHERE s.agency_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM appointments a WHERE a.stima_inspection_id = i.id)
       AND NOT EXISTS (SELECT 1 FROM appointments a
                        WHERE a.source = 'stima_inspections_backfill'
                          AND a.source_record_id = 'stima_inspections:' || i.id))
SELECT status AS stato_lmc15,
       CASE status WHEN 'scheduled' THEN 'requested' ELSE status END AS stato_agenda,
       count(*) AS righe,
       count(*) FILTER (WHERE scheduled_for IS NULL) AS inizio_ricostruito_da_completed_at,
       count(DISTINCT agency_id) AS agenzie
  FROM candidati GROUP BY status ORDER BY status;

\echo '== Q6 anteprima del piano (prime 30 righe, nessun dato personale)'
SELECT i.id AS stima_inspection_id,
       'stima_inspections:' || i.id AS source_record_id,
       s.agency_id, i.stima_id, i.status AS stato_lmc15,
       CASE i.status WHEN 'scheduled' THEN 'requested' ELSE i.status END AS stato_agenda,
       COALESCE(i.scheduled_for, i.completed_at) AS start_at,
       COALESCE(i.scheduled_for, i.completed_at) + INTERVAL '60 minutes' AS end_at,
       i.completed_at, i.cancelled_at, (i.cancelled_reason IS NOT NULL) AS ha_motivo
  FROM stima_inspections i
  JOIN stime s ON s.id = i.stima_id
 WHERE s.agency_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM appointments a WHERE a.stima_inspection_id = i.id)
   AND NOT EXISTS (
       SELECT 1
       FROM appointments a
       WHERE a.source = 'stima_inspections_backfill'
         AND a.source_record_id = 'stima_inspections:' || i.id
   )
 ORDER BY i.id
 LIMIT 30;

\echo '== Q7 controlli d integrita (tutti devono essere 0)'
SELECT
  (SELECT count(*) FROM appointments a
    WHERE a.source = 'stima_inspections_backfill'
      AND (a.stima_inspection_id IS NULL
           OR a.source_record_id <> 'stima_inspections:' || a.stima_inspection_id))
      AS chiave_e_collegamento_incoerenti,
  (SELECT count(*) FROM (SELECT stima_inspection_id FROM appointments
                          WHERE stima_inspection_id IS NOT NULL
                          GROUP BY 1 HAVING count(*) > 1) d) AS collegamenti_doppi,
  (SELECT count(*) FROM stima_inspections i JOIN stime s ON s.id = i.stima_id
    WHERE i.status = 'completed' AND i.completed_at IS NULL) AS completed_senza_istante;

\echo '== Q7b candidati NON trasformabili (stesso insieme di Q5; tutti devono essere 0)'
WITH candidati AS (
    SELECT i.*
      FROM stima_inspections i
      JOIN stime s ON s.id = i.stima_id
     WHERE s.agency_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM appointments a WHERE a.stima_inspection_id = i.id)
       AND NOT EXISTS (SELECT 1 FROM appointments a
                        WHERE a.source = 'stima_inspections_backfill'
                          AND a.source_record_id = 'stima_inspections:' || i.id))
SELECT count(*) FILTER (WHERE COALESCE(scheduled_for, completed_at) IS NULL)
           AS senza_istante_iniziale,
       count(*) FILTER (WHERE status = 'scheduled' AND scheduled_for IS NULL)
           AS scheduled_senza_scheduled_for,
       count(*) FILTER (WHERE status = 'cancelled' AND scheduled_for IS NULL)
           AS cancelled_senza_scheduled_for,
       count(*) FILTER (WHERE status = 'cancelled' AND cancelled_at IS NULL)
           AS cancelled_senza_cancelled_at
  FROM candidati;

\echo '== Q8 Agenda: sopralluoghi aperti con stima NON proiettati (creati a proiezione spenta)'
SELECT status, count(*) FROM appointments
 WHERE appointment_type = 'inspection' AND stima_id IS NOT NULL
   AND stima_inspection_id IS NULL
   AND status IN ('requested', 'scheduled', 'confirmed')
 GROUP BY status;

\echo '== Q9 deriva: richieste importate intatte ma chiuse/spostate in LMC-15 dopo il backfill'
SELECT count(*) FILTER (WHERE i.status <> 'scheduled') AS chiuse_dopo,
       count(*) FILTER (WHERE i.status = 'scheduled'
                          AND i.scheduled_for IS DISTINCT FROM a.start_at) AS spostate_dopo
  FROM appointments a JOIN stima_inspections i ON i.id = a.stima_inspection_id
 WHERE a.source = 'stima_inspections_backfill' AND a.status = 'requested' AND a.version = 1;

\echo '== Q10 allineamento appointments <-> stima_inspections (tutti devono essere 0 prima della facade)'
-- Coppie VALIDE (appointments.status -> stima_inspections.status):
--   scheduled / confirmed                     -> scheduled
--   requested  (SOLO source stima_inspections_backfill, riga storica) -> scheduled
--   completed                                 -> completed
--   cancelled                                 -> cancelled
--   no_show                                   -> cancelled con cancelled_reason 'no_show'
--   rescheduled                               -> nessun collegamento (passa alla riga nuova)
WITH coppie AS (
    SELECT a.id, a.agency_id, a.status AS a_status, a.source, a.stima_id AS a_stima,
           a.start_at, a.completed_at AS a_completed_at, a.cancelled_at AS a_cancelled_at,
           a.no_show_at, a.stima_inspection_id,
           i.id AS i_id, i.status AS i_status, i.stima_id AS i_stima,
           i.scheduled_for, i.completed_at AS i_completed_at,
           i.cancelled_at AS i_cancelled_at, i.cancelled_reason,
           s.agency_id AS i_agency
      FROM appointments a
      LEFT JOIN stima_inspections i ON i.id = a.stima_inspection_id
      LEFT JOIN stime s ON s.id = i.stima_id
     WHERE a.stima_inspection_id IS NOT NULL
)
SELECT
  count(*) FILTER (WHERE i_id IS NULL)
      AS collegamento_senza_riga_lmc15,
  count(*) FILTER (WHERE i_id IS NOT NULL AND NOT (
         (a_status IN ('scheduled', 'confirmed') AND i_status = 'scheduled')
      OR (a_status = 'requested' AND source = 'stima_inspections_backfill'
          AND i_status = 'scheduled')
      OR (a_status = 'completed' AND i_status = 'completed')
      OR (a_status = 'cancelled' AND i_status = 'cancelled')
      OR (a_status = 'no_show'   AND i_status = 'cancelled'
          AND cancelled_reason = 'no_show')))
      AS stato_incompatibile,
  count(*) FILTER (WHERE a_status IN ('requested', 'scheduled', 'confirmed')
                     AND i_status = 'scheduled'
                     AND scheduled_for IS DISTINCT FROM start_at)
      AS scheduled_for_diverso_da_start_at,
  count(*) FILTER (WHERE a_status = 'completed' AND i_status = 'completed'
                     AND i_completed_at IS DISTINCT FROM a_completed_at)
      AS completed_at_diverso,
  count(*) FILTER (WHERE a_status = 'cancelled' AND i_status = 'cancelled'
                     AND i_cancelled_at IS DISTINCT FROM a_cancelled_at)
      AS cancelled_at_diverso,
  count(*) FILTER (WHERE a_status = 'no_show' AND i_status = 'cancelled'
                     AND i_cancelled_at IS DISTINCT FROM no_show_at)
      AS no_show_at_diverso,
  count(*) FILTER (WHERE i_id IS NOT NULL
                     AND (i_stima IS DISTINCT FROM a_stima
                          OR i_agency IS DISTINCT FROM agency_id))
      AS stima_o_agenzia_diversa,
  (SELECT count(*) FROM appointments WHERE status = 'rescheduled'
                                      AND stima_inspection_id IS NOT NULL)
      AS rescheduled_ancora_collegato,
  -- gli orfani senza stima (Q2) non sono importabili e restano fuori
  (SELECT count(*) FROM stima_inspections i2
    WHERE i2.stima_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM appointments a2
                       WHERE a2.stima_inspection_id = i2.id))
      AS lmc15_con_stima_non_rappresentati
  FROM coppie;

ROLLBACK;
