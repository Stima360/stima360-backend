-- 107 - SEMPLIFICAZIONE-CRM-1: la VISITA IMMOBILE si fissa solo su un incarico.
--
-- Additiva. Nessun backfill, nessuna riga esistente letta o riscritta, nessuna
-- colonna nuova, nessun CHECK: le visite gia' scritte (anche senza immobile,
-- cliente o agente) restano esattamente come sono.
--
-- Una NUOVA visita immobile (`appointment_type = 'buyer_visit'`) creata dal
-- CRM (`source = 'crm_manual'`: il dialog dell'Agenda, «Programma visita»
-- dalla scheda Immobile, Acquirente, Abbinamento e Incarico, tutti attraverso
-- `appointments/service.py`) porta SEMPRE l'immobile dell'incarico, il
-- cliente acquirente e l'agente. Il service e' la prima guardia (con il
-- messaggio leggibile e la verifica che l'incarico sia reale, in corso o
-- scaduto); questo trigger e' la garanzia nel database.
--
-- Esclusi di proposito:
--   * uno SPOSTAMENTO (`rescheduled_from_id` valorizzato): la riga nuova copia
--     i collegamenti della vecchia, e una visita storica deve restare
--     spostabile;
--   * le altre fonti (import storici, backfill, booking pubblico - che per
--     questo tipo non crea piu' nulla, `a30_test`): non passano dal dialog.
--
-- `appointments_sale_links_chk` (106) resta com'e' per `sale_appointment`.
--
-- Transaction ownership: il runner possiede la transazione UP (versione >= 027),
-- quindi questo file non ha BEGIN/COMMIT.

CREATE OR REPLACE FUNCTION appointments_visit_links_guard() RETURNS trigger AS $fn$
BEGIN
    IF NEW.appointment_type = 'buyer_visit'
       AND NEW.source = 'crm_manual'
       AND NEW.rescheduled_from_id IS NULL
       AND (NEW.property_id IS NULL OR NEW.contact_id IS NULL
            OR NEW.assigned_user_id IS NULL) THEN
        RAISE EXCEPTION
            '107: una visita immobile richiede incarico (property_id), cliente (contact_id) e agente (assigned_user_id)';
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_appointments_visit_links_guard'
                    AND tgrelid = 'public.appointments'::regclass) THEN
        CREATE TRIGGER trg_appointments_visit_links_guard
            BEFORE INSERT ON appointments
            FOR EACH ROW EXECUTE FUNCTION appointments_visit_links_guard();
    END IF;
END
$do$;
