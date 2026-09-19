-- LMC-1A: l'accesso PRE-INCARICO di un proprietario a una stima.
--
-- Additive. Crea UNA tabella, due indici, una funzione e un trigger. Non
-- altera nessuna tabella preesistente, non inserisce righe, non cancella nulla.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- ---------------------------------------------------------------------------
-- PERCHE' UNA TABELLA NUOVA E NON `owner_property_access`
--
-- L'Owner Portal autorizza per IMMOBILE: `owner_property_access` lega un
-- account a una riga di `properties`, che esiste solo dopo l'incarico. "La
-- Mia Casa" comincia prima: al momento della stima pubblica non c'e' nessun
-- immobile, e fabbricarne uno fittizio per riusare quel grant vorrebbe dire
-- popolare PROPERTY con righe che non sono incarichi. L'invariante di LMC e':
--
--     PRE-INCARICO   stima + property_watch
--     POST-INCARICO  properties + owner_property_access
--
-- Questa tabella e' il grant del primo mondo. Stessa forma dell'altro grant
-- (ruolo, stato, primario, validita', revoca) perche' l'Owner Portal la
-- leggera' con lo stesso predicato; radice diversa (`stime`, non
-- `properties`).
--
-- PERCHE' NON PORTA `agency_id`
--
-- Nessuna tabella `owner_*` ne ha una: la tenancy di OWNER e' DERIVATA, e
-- P26-6C l'ha certificata cosi'. Un account raggiunge la sua agenzia dal
-- contatto (`owner_accounts.contact_id -> contacts.agency_id`), una stima la
-- porta scritta (`stime.agency_id`, NOT NULL dalla 033). Il grant e' lecito
-- solo se le due coincidono, e `stima_id` e' NOT NULL con CASCADE, quindi la
-- derivazione e' sempre possibile: una colonna in piu' sarebbe un terzo valore
-- da tenere d'accordo con gli altri due, non una difesa in piu'.
--
-- La difesa e' il trigger qui sotto: come `property_watch_agency_integrity`
-- (048) verifica e non deriva. Verifica su INSERT e su UPDATE dei due
-- riferimenti, perche' scrivere una riga lecita e poi ripuntarla e' la stessa
-- violazione, uno statement dopo.
--
-- CHI SCRIVE QUI
--
-- Il provisioning automatico del funnel pubblico (`owner/provisioning.py`),
-- che riceve l'agenzia dal contesto di sistema della stima gia' scritta e
-- rifiuta prima di arrivare al database se contatto e stima non concordano.
-- Il trigger e' la rete sotto quella verifica, non il suo sostituto.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS owner_stima_access (
    id               BIGSERIAL PRIMARY KEY,
    owner_account_id BIGINT      NOT NULL REFERENCES owner_accounts(id) ON DELETE CASCADE,
    stima_id         INTEGER     NOT NULL REFERENCES stime(id) ON DELETE CASCADE,
    access_role      VARCHAR(30) NOT NULL DEFAULT 'owner',
    access_status    VARCHAR(20) NOT NULL DEFAULT 'active',
    is_primary       BOOLEAN     NOT NULL DEFAULT FALSE,
    valid_from       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_until      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at       TIMESTAMPTZ,
    granted_by       VARCHAR(200),
    UNIQUE (owner_account_id, stima_id),
    CONSTRAINT owner_stima_access_role_chk
        CHECK (access_role IN ('owner', 'co_owner', 'delegate', 'legal_representative')),
    CONSTRAINT owner_stima_access_status_chk
        CHECK (access_status IN ('active', 'revoked', 'expired')),
    CONSTRAINT owner_stima_access_validity_chk
        CHECK (valid_until IS NULL OR valid_until >= valid_from),
    CONSTRAINT owner_stima_access_revoked_chk
        CHECK ((access_status = 'revoked') = (revoked_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_owner_stima_access_account
    ON owner_stima_access (owner_account_id);
CREATE INDEX IF NOT EXISTS idx_owner_stima_access_stima
    ON owner_stima_access (stima_id);

-- ---------------------------------------------------------------------------
-- La difesa del database: le due radici devono concordare.
--
-- NULL su una delle due non e' "non so, lascio passare": un account senza
-- contatto o una stima senza agenzia non possono essere collegati, e il
-- confronto `<>` con NULL darebbe NULL, cioe' nessuna eccezione. Per questo
-- si controlla IS NULL esplicitamente prima del confronto.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION owner_stima_access_agency_integrity() RETURNS trigger AS $fn$
DECLARE
    a_contact BIGINT;
    a_stima   BIGINT;
BEGIN
    SELECT ct.agency_id INTO a_contact
      FROM owner_accounts oa
      JOIN contacts ct ON ct.id = oa.contact_id
     WHERE oa.id = NEW.owner_account_id;

    SELECT s.agency_id INTO a_stima
      FROM stime s
     WHERE s.id = NEW.stima_id;

    IF a_contact IS NULL OR a_stima IS NULL OR a_contact <> a_stima THEN
        RAISE EXCEPTION
            'LMC-1A owner/stima tenancy: owner account % (agency %) and estimation % (agency %) do not belong to the same agency',
            NEW.owner_account_id, a_contact, NEW.stima_id, a_stima;
    END IF;

    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_owner_stima_access_agency_integrity'
          AND c.relname = 'owner_stima_access' AND n.nspname = 'public'
          AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_owner_stima_access_agency_integrity
            BEFORE INSERT OR UPDATE OF owner_account_id, stima_id
            ON owner_stima_access
            FOR EACH ROW EXECUTE FUNCTION owner_stima_access_agency_integrity();
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- Verifica: la tabella e' quella che questo file dice, non una omonima
-- preesistente con un'altra forma (IF NOT EXISTS lascerebbe passare la
-- seconda in silenzio).
-- ---------------------------------------------------------------------------

DO $do$
DECLARE
    v_count integer;
BEGIN
    SELECT count(*) INTO v_count
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name   = 'owner_stima_access'
       AND column_name IN ('id', 'owner_account_id', 'stima_id', 'access_role',
                           'access_status', 'is_primary', 'valid_from', 'valid_until',
                           'created_at', 'updated_at', 'revoked_at', 'granted_by');
    IF v_count <> 12 THEN
        RAISE EXCEPTION
            'LMC-1A 066: owner_stima_access has % of the 12 expected columns', v_count;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name   = 'owner_stima_access'
           AND column_name  = 'agency_id'
    ) THEN
        RAISE EXCEPTION
            'LMC-1A 066: owner_stima_access must not carry agency_id; tenancy is derived from contacts and stime';
    END IF;

    SELECT count(*) INTO v_count
      FROM pg_constraint
     WHERE conrelid = 'public.owner_stima_access'::regclass
       AND contype  = 'u';
    IF v_count <> 1 THEN
        RAISE EXCEPTION
            'LMC-1A 066: owner_stima_access must carry exactly one UNIQUE (owner_account_id, stima_id), found %', v_count;
    END IF;

    SELECT count(*) INTO v_count
      FROM pg_constraint
     WHERE conrelid = 'public.owner_stima_access'::regclass
       AND contype  = 'f';
    IF v_count <> 2 THEN
        RAISE EXCEPTION
            'LMC-1A 066: owner_stima_access must carry exactly two foreign keys, found %', v_count;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        WHERE t.tgname = 'trg_owner_stima_access_agency_integrity'
          AND c.relname = 'owner_stima_access'
    ) THEN
        RAISE EXCEPTION
            'LMC-1A 066: trg_owner_stima_access_agency_integrity is missing';
    END IF;
END
$do$;
