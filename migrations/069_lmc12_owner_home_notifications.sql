-- LMC-12: le notifiche in-app PRE-INCARICO di "La Mia Casa".
--
-- Additive. Crea UNA tabella, due indici, una funzione e un trigger. Non
-- altera nessuna tabella preesistente, non inserisce righe, non cancella
-- nulla. Nessuna guardia sul nome del database: lo stesso file vale per TEST
-- oggi e per PROD quando sara' promosso, senza riscriverlo.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- ---------------------------------------------------------------------------
-- PERCHE' UNO STREAM SEPARATO E NON UNA COLONNA IN `owner_notifications`
--
-- `owner_notifications` (011/015) e' costruita attorno all'incarico:
-- `property_id` e' NOT NULL con FK su `properties`, i due CHECK sui tipi sono
-- chiusi sui quattro eventi P2/P3/P4, e le due letture certificate da P5 e da
-- P26-6C rivalidano `owner_property_access` a ogni richiesta. Il
-- pre-incarico non ha una `properties`: ha una stima, un watch e un grant
-- `owner_stima_access`. Allargare quella tabella avrebbe voluto dire rendere
-- nullable la sua radice, riaprire i CHECK, riscrivere la 015 gia' in
-- produzione e rendere a doppia radice le query che oggi hanno una radice
-- sola - cioe' il punto esatto in cui nascono gli errori di scope. E la
-- via facile, una `properties` fittizia per ogni stima, avrebbe messo nel
-- CRM immobili che non esistono. Quindi il pre-incarico ha il suo stream, e
-- `owner_notifications` resta byte per byte com'era.
--
-- PERCHE' NON PORTA `agency_id`, NE' `property_id`, NE' `target_*`
--
-- Nessuna tabella `owner_*` ha `agency_id`: la tenancy di OWNER e' DERIVATA,
-- e P26-6C l'ha certificata cosi'. Qui la radice e' `stima_id`, NOT NULL con
-- CASCADE, e `stime.agency_id` e' NOT NULL dalla 033: l'agenzia della
-- notifica e' sempre quella della stima. `property_id` non c'e' perche'
-- l'immobile non c'e'. `target_type`/`target_id` non ci sono perche' il
-- bersaglio e' sempre la casa: `stima_id` e' gia' il target.
--
-- `evidence` e' la prova interna della decisione - quali osservazioni sono
-- state confrontate e con che numeri - e serve al rilevatore per sapere da
-- dove ripartire. Non esce mai nel DTO del proprietario.
--
-- I TRE TIPI, E PERCHE' SONO UN CHECK
--
-- `home_value_changed` e `home_method_changed` nascono dal confronto fra due
-- `valuation_snapshot` (LMC-3/LMC-11); `home_demand_changed` da un cambio
-- della fascia di domanda (LMC-4). Il CHECK e' chiuso di proposito, come in
-- `owner_notifications`: un tipo nuovo e' una migration, cioe' un atto
-- visibile in diff.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS owner_home_notifications (
    id                BIGSERIAL PRIMARY KEY,
    owner_account_id  BIGINT      NOT NULL REFERENCES owner_accounts(id) ON DELETE CASCADE,
    stima_id          INTEGER     NOT NULL REFERENCES stime(id)          ON DELETE CASCADE,
    notification_type VARCHAR(40) NOT NULL,
    title             VARCHAR(200) NOT NULL,
    body              TEXT        NOT NULL,
    evidence          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key   VARCHAR(300) NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    read_at           TIMESTAMPTZ,
    expires_at        TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '365 days'),

    CONSTRAINT owner_home_notifications_type_chk CHECK (
        notification_type IN (
            'home_value_changed',
            'home_demand_changed',
            'home_method_changed'
        )
    ),
    CONSTRAINT owner_home_notifications_title_chk
        CHECK (BTRIM(title) <> ''),
    CONSTRAINT owner_home_notifications_body_chk
        CHECK (BTRIM(body) <> '' AND CHAR_LENGTH(body) <= 5000),
    CONSTRAINT owner_home_notifications_idempotency_chk
        CHECK (BTRIM(idempotency_key) <> ''),
    CONSTRAINT owner_home_notifications_read_time_chk
        CHECK (read_at IS NULL OR read_at >= created_at),
    CONSTRAINT owner_home_notifications_expiry_chk
        CHECK (expires_at > created_at),
    CONSTRAINT owner_home_notifications_idempotency_unique
        UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_owner_home_notifications_account_created
    ON owner_home_notifications (owner_account_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_owner_home_notifications_stima_type_created
    ON owner_home_notifications (stima_id, notification_type, created_at DESC, id DESC);

-- ---------------------------------------------------------------------------
-- La difesa del database: il proprietario notificato e la casa di cui si
-- parla devono stare nella stessa agenzia. Stesso corpo della 066 e della
-- 068: si verifica, non si deriva, e il confronto con NULL non e' "non so,
-- lascio passare" - le due agenzie devono esistere entrambe e coincidere.
-- Qui non c'e' il caso del NULL ammesso della 068: entrambi i riferimenti
-- sono NOT NULL, quindi il trigger non lascia passare niente.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION owner_home_notifications_agency_integrity() RETURNS trigger AS $fn$
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
            'LMC-12 owner/stima tenancy: owner account % (agency %) cannot be notified about estimation % (agency %)',
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
        WHERE t.tgname = 'trg_owner_home_notifications_agency_integrity'
          AND c.relname = 'owner_home_notifications' AND n.nspname = 'public'
          AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_owner_home_notifications_agency_integrity
            BEFORE INSERT OR UPDATE OF owner_account_id, stima_id
            ON owner_home_notifications
            FOR EACH ROW EXECUTE FUNCTION owner_home_notifications_agency_integrity();
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
       AND table_name   = 'owner_home_notifications'
       AND column_name IN ('id','owner_account_id','stima_id','notification_type',
                           'title','body','evidence','idempotency_key',
                           'created_at','read_at','expires_at');
    IF v_count <> 11 THEN
        RAISE EXCEPTION
            'LMC-12 069: owner_home_notifications has % of the 11 expected columns', v_count;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name   = 'owner_home_notifications'
           AND column_name IN ('agency_id','property_id','target_type','target_id')
    ) THEN
        RAISE EXCEPTION
            'LMC-12 069: owner_home_notifications must not carry agency_id, property_id or target_*; tenancy is derived from stime and the target is the home';
    END IF;

    SELECT count(*) INTO v_count
      FROM pg_constraint
     WHERE conrelid = 'public.owner_home_notifications'::regclass
       AND contype  = 'u';
    IF v_count <> 1 THEN
        RAISE EXCEPTION
            'LMC-12 069: owner_home_notifications must carry exactly one UNIQUE (idempotency_key), found %', v_count;
    END IF;

    SELECT count(*) INTO v_count
      FROM pg_constraint
     WHERE conrelid = 'public.owner_home_notifications'::regclass
       AND contype  = 'f';
    IF v_count <> 2 THEN
        RAISE EXCEPTION
            'LMC-12 069: owner_home_notifications must carry exactly two foreign keys, found %', v_count;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.owner_home_notifications'::regclass
           AND conname  = 'owner_home_notifications_type_chk'
    ) THEN
        RAISE EXCEPTION 'LMC-12 069: owner_home_notifications_type_chk is missing';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public' AND tablename = 'owner_home_notifications'
           AND indexname = 'idx_owner_home_notifications_account_created'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public' AND tablename = 'owner_home_notifications'
           AND indexname = 'idx_owner_home_notifications_stima_type_created'
    ) THEN
        RAISE EXCEPTION 'LMC-12 069: one of the two owner_home_notifications indexes is missing';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        WHERE t.tgname = 'trg_owner_home_notifications_agency_integrity'
          AND c.relname = 'owner_home_notifications'
    ) THEN
        RAISE EXCEPTION
            'LMC-12 069: trg_owner_home_notifications_agency_integrity is missing';
    END IF;
END
$do$;
