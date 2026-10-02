-- CRM-OPS-4: lo storico interazioni dell'immobile (telefonate, incontri,
-- note con il proprietario), letto identico dalla scheda Immobile e dalla
-- scheda Incarico.
--
-- Additiva. NESSUNA tabella nuova: le interazioni sono `activities`, il
-- registro canonico del CRM (migration 001, P26: agency_id, autore,
-- occurred_at, catalogo dei tipi). Qui si aggiunge soltanto il legame con
-- l'immobile:
--   * `activities.property_id` (NULLABLE, nessun backfill);
--   * il CHECK dei riferimenti accetta anche l'immobile (una nota sull'immobile
--     senza referente e' una riga valida);
--   * un trigger che rifiuta un immobile di un'altra agenzia;
--   * un trigger che rifiuta la DELETE di un'interazione d'immobile
--     (storico commerciale non cancellabile);
--   * un indice per la timeline di un immobile.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- ---------------------------------------------------------------------------
-- COSA NON E'
--
--   * Non e' un audit: `acquisition_events`, `appointment_events`,
--     `property_status_history` restano i registri tecnici e non ricevono
--     note commerciali; una nota non diventa mai un evento di stato.
--   * Non e' la timeline del motore Seller Intelligence
--     (`seller_timeline_events`), che guida regole automatiche.
--   * Non e' l'Agenda: un incontro annotato non crea un appuntamento.
--   * Non e' un'entita' "incarico": l'incarico resta `properties.mandate_*`
--     con `acquisition_id` (081).
--
-- TENANCY
--   `activities.agency_id` e' NOT NULL e lo valida gia' `core_agency_integrity`
--   (030/033) contro contatto, lead e stima. `properties` non ha un
--   UNIQUE (agency_id, id) su cui appoggiare una FK composita (081 ha fatto la
--   stessa scelta per le acquisizioni): la coerenza immobile/agenzia la impone
--   il trigger qui sotto, che scatta DOPO quello di agenzia (ordine alfabetico:
--   `trg_activities_agency_integrity` < `trg_activities_property_scope`) e
--   quindi vede l'agency_id gia' risolto.
--
-- ON DELETE RESTRICT: gli immobili si archiviano, non si cancellano; una
-- cancellazione fisica non deve portarsi via lo storico commerciale.
-- ---------------------------------------------------------------------------

ALTER TABLE activities
    ADD COLUMN IF NOT EXISTS property_id BIGINT;

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'activities_property_id_fkey'
                    AND conrelid = 'public.activities'::regclass) THEN
        ALTER TABLE activities
            ADD CONSTRAINT activities_property_id_fkey
            FOREIGN KEY (property_id) REFERENCES properties(id) ON DELETE RESTRICT;
    END IF;
END
$do$;

-- Il CHECK dei riferimenti: lo stesso di 001, con l'immobile in piu'.
-- Ogni riga esistente lo soddisfa gia' (aveva un contatto, un lead o una
-- stima): la nuova condizione e' piu' larga, mai piu' stretta.
ALTER TABLE activities DROP CONSTRAINT IF EXISTS activities_reference_chk;
ALTER TABLE activities
    ADD CONSTRAINT activities_reference_chk CHECK (
        contact_id IS NOT NULL OR lead_id IS NOT NULL OR stima_id IS NOT NULL
        OR property_id IS NOT NULL
    );

CREATE OR REPLACE FUNCTION activities_property_scope() RETURNS trigger AS $fn$
DECLARE
    v_agency BIGINT;
BEGIN
    IF NEW.property_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT agency_id INTO v_agency FROM properties WHERE id = NEW.property_id;
    IF v_agency IS NULL OR NEW.agency_id IS NULL OR v_agency <> NEW.agency_id THEN
        RAISE EXCEPTION
            'CRM-OPS-4 tenancy: property % does not belong to agency % (activity)',
            NEW.property_id, NEW.agency_id
            USING ERRCODE = 'check_violation';
    END IF;
    -- Il referente, se c'e', e' un contatto dell'immobile.
    IF NEW.contact_id IS NOT NULL
       AND (TG_OP = 'INSERT'
            OR NEW.contact_id IS DISTINCT FROM OLD.contact_id
            OR NEW.property_id IS DISTINCT FROM OLD.property_id)
       AND NOT EXISTS (SELECT 1 FROM property_contacts
                        WHERE property_id = NEW.property_id AND contact_id = NEW.contact_id) THEN
        RAISE EXCEPTION
            'CRM-OPS-4: contact % is not linked to property % (activity)',
            NEW.contact_id, NEW.property_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_activities_property_scope'
                    AND tgrelid = 'public.activities'::regclass) THEN
        CREATE TRIGGER trg_activities_property_scope
            BEFORE INSERT OR UPDATE OF property_id, contact_id, agency_id ON activities
            FOR EACH ROW EXECUTE FUNCTION activities_property_scope();
    END IF;
END
$do$;

-- STORICO NON CANCELLABILE. Un'interazione legata a un immobile e' storia
-- commerciale: non si cancella (la si corregge con un'interazione nuova).
-- Il service rifiuta gia' con 409 (core/repository.py::delete_activity);
-- questo trigger e' la garanzia anche per chi scrive SQL. Le attivita'
-- SENZA immobile (tutte quelle esistenti) restano cancellabili come prima:
-- il trigger scatta solo WHEN OLD.property_id IS NOT NULL.
CREATE OR REPLACE FUNCTION activities_property_history_guard() RETURNS trigger AS $fn$
BEGIN
    RAISE EXCEPTION
        'CRM-OPS-4: activity % belongs to the commercial history of property % and cannot be deleted',
        OLD.id, OLD.property_id
        USING ERRCODE = 'check_violation';
END;
$fn$ LANGUAGE plpgsql;

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_activities_property_history'
                    AND tgrelid = 'public.activities'::regclass) THEN
        CREATE TRIGGER trg_activities_property_history
            BEFORE DELETE ON activities
            FOR EACH ROW WHEN (OLD.property_id IS NOT NULL)
            EXECUTE FUNCTION activities_property_history_guard();
    END IF;
END
$do$;

CREATE INDEX IF NOT EXISTS idx_activities_agency_property_occurred
    ON activities (agency_id, property_id, occurred_at DESC, id DESC)
    WHERE property_id IS NOT NULL;
