-- CRM-OPS-2: form Immobili - regione e agente assegnato.
--
-- Additive. Due colonne NULLABLE su `properties`, una FK composita e un
-- indice. Nessuna riga viene letta, riscritta o cancellata: ogni immobile gia'
-- presente resta identico, con le due colonne nuove a NULL.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- ---------------------------------------------------------------------------
-- region
--   Il primo livello del territorio (Regione -> Provincia -> Comune ->
--   Microzona). `province`, `city` e `microzone` esistono gia' dalla 002; la
--   regione no. Testo libero a livello di schema, come le altre tre: il
--   catalogo (property/catalog.py) e' applicato dall'API su cio' che
--   l'operatore invia, NON da un CHECK, perche' un CHECK renderebbe illegali
--   i valori storici che il brief chiede di conservare.
--
-- assigned_agent_id
--   L'identificativo reale dell'operatore a cui e' assegnato l'immobile.
--   Finora c'era solo `assigned_to` VARCHAR(200), un nome scritto a mano:
--   resta, invariato, come istantanea leggibile (la scrive il server quando
--   assegna) e per i client storici che la usano ancora.
--   FK composita (agency_id, assigned_agent_id) -> agency_memberships, la
--   stessa forma di contacts/leads (migration 030): l'agente deve essere un
--   membro della STESSA agenzia dell'immobile, e lo garantisce il database,
--   non solo l'applicazione. Che la membership sia anche ATTIVA lo verifica
--   l'API al momento dell'assegnazione (operator_auth.membership_exists).
-- ---------------------------------------------------------------------------

ALTER TABLE properties ADD COLUMN IF NOT EXISTS region VARCHAR(50);
ALTER TABLE properties ADD COLUMN IF NOT EXISTS assigned_agent_id BIGINT;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'properties_agent_same_agency_fk'
                      AND conrelid = 'properties'::regclass) THEN
        ALTER TABLE properties
            ADD CONSTRAINT properties_agent_same_agency_fk
            FOREIGN KEY (agency_id, assigned_agent_id)
            REFERENCES agency_memberships (agency_id, operator_user_id);
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_properties_assigned_agent
    ON properties (agency_id, assigned_agent_id)
    WHERE assigned_agent_id IS NOT NULL;

-- Sonda: le due colonne esistono e sono nullable, la FK e' quella attesa.
DO $$
BEGIN
    IF (SELECT count(*) FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'properties'
           AND column_name IN ('region', 'assigned_agent_id')
           AND is_nullable = 'YES') <> 2 THEN
        RAISE EXCEPTION 'CRM-OPS-2 080: region/assigned_agent_id mancanti o NOT NULL';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'properties_agent_same_agency_fk'
                      AND contype = 'f'
                      AND confrelid = 'agency_memberships'::regclass) THEN
        RAISE EXCEPTION 'CRM-OPS-2 080: FK properties_agent_same_agency_fk assente o diversa';
    END IF;
END
$$;
