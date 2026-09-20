-- LMC-10: cio' che il proprietario ha corretto della propria casa.
--
-- Additive. Crea UNA tabella, un indice, una funzione e un trigger. Non altera
-- nessuna tabella preesistente, non inserisce righe, non cancella nulla.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- ---------------------------------------------------------------------------
-- PERCHE' UNA TABELLA NUOVA E NON UNA UPDATE SU `stime`
--
-- `stime` e' la fotografia del dato inserito al momento della valutazione, e
-- tutto il sistema la tratta cosi': l'unica rotta operatore che la aggiorna
-- (`admin_update_stima`) scrive `lead_status` e `note_internal` e nient'altro,
-- non esiste `updated_at`, non esiste colonna di versione. Sopra quella
-- fotografia poggiano tre cose che non si possono riscrivere: `initial_value`
-- del read-model LMC-2, la baseline `watch_started` di Property Watch, e
-- l'`input_digest` di ogni `valuation_snapshot` LMC-3 gia' scritto. Una UPDATE
-- sui campi descrittivi renderebbe irripetibile ogni snapshot storico e
-- farebbe divergere in silenzio il valore mostrato dal dato su cui e' stato
-- calcolato.
--
-- Quindi il dato originale non si tocca e il dato corretto vive qui:
--
--     ORIGINALE (stime) + OVERRIDE (questa tabella) = PROFILO EFFETTIVO
--
-- PERCHE' UNA COLONNA PER CAMPO E NON UN JSONB
--
-- La whitelist e' chiusa, e qui la chiusura e' lo schema stesso: un campo che
-- il proprietario non puo' modificare non ha dove andare a finire. In piu' i
-- tipi sono quelli veri di `stime`, quindi il database rifiuta da solo un
-- numero di locali scritto a parole. Aggiungere un campo modificabile domani
-- costa una migration, ed e' esattamente il punto: e' un atto visibile in
-- diff, come per `HOME_STIMA_COLUMNS`.
--
-- COSA NON E' MODIFICABILE, E PERCHE' NON C'E' LA COLONNA
--
-- `comune` e `microzona` scelgono la base EUR/mq (`valuation.get_base_mq`):
-- una colonna qui vorrebbe dire lasciare al proprietario la scelta del proprio
-- prezzo al metro quadro. `via`, `civico` sono identita' (e il motore non li
-- legge nemmeno). `tipologia`, `posizionemare`, `distanzamare`,
-- `barrieramare`, `vistamareyn`, `vistamare`, `vistamaredettaglio` sono
-- classificazione e posizione: non cambiano nel tempo e pesano sul
-- coefficiente. Per tutti questi la correzione esiste gia' ed e' LMC-9, la
-- richiesta di verifica gratuita: una zona sbagliata e' una rivalutazione, non
-- un aggiornamento. `agency_id`, `contact_id`, gli id di lead/owner/watch, il
-- `token`, `prezzo_mq_base`, `lead_status`, `note_internal` e i dati personali
-- non sono dati della casa e non compaiono.
--
-- PERCHE' NON PORTA `agency_id`
--
-- Nessuna tabella `owner_*` ne ha una: la tenancy di OWNER e' DERIVATA, e
-- P26-6C l'ha certificata cosi'. Qui la derivazione e' piu' diretta che nella
-- 066, perche' la radice e' UNA: `stima_id` e' NOT NULL con CASCADE e
-- `stime.agency_id` e' NOT NULL dalla 033, quindi l'agenzia dell'override e'
-- sempre `stime.agency_id`. Una colonna in piu' sarebbe un terzo valore da
-- tenere d'accordo, non una difesa in piu'.
--
-- L'unico secondo riferimento e' `updated_by_owner_account_id`, che dice CHI
-- ha scritto per ultimo e non DI CHI e' il dato. Il trigger in fondo e' la
-- stessa difesa della 066, applicata a quel riferimento.
--
-- PERCHE' LA CHIAVE E' LA STIMA E NON L'ACCOUNT
--
-- `owner_stima_access.access_role` ammette `co_owner` e `delegate`: due
-- comproprietari che aggiornano la stessa casa devono convergere su un solo
-- profilo effettivo, non produrne due divergenti. L'override e' un fatto sulla
-- casa.
--
-- LIMITE DICHIARATO
--
-- `NULL` significa "nessun override": in LMC-10 il proprietario puo'
-- sostituire un valore, non cancellarlo. E la tabella conserva l'override
-- CORRENTE, non la storia degli override: l'originale in `stime` resta
-- intatto, i valori intermedi no. Una storia reale richiederebbe una seconda
-- tabella e non serve a nessuno dei comportamenti di questa fase.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS owner_home_overrides (
    id               BIGSERIAL PRIMARY KEY,
    stima_id         INTEGER NOT NULL UNIQUE REFERENCES stime(id) ON DELETE CASCADE,

    -- La whitelist chiusa. Tipi identici alle colonne omonime di `stime`
    -- (verificati su information_schema, non dedotti): il test 51 li
    -- riconfronta a ogni esecuzione.
    mq               INTEGER,
    piano            VARCHAR(30),
    locali           INTEGER,
    bagni            INTEGER,
    ascensore        VARCHAR(10),
    anno             INTEGER,
    stato            VARCHAR(40),
    pertinenze       VARCHAR(200),
    mqgiardino       INTEGER,
    mqgarage         INTEGER,
    mqcantina        INTEGER,
    mqpostoauto      INTEGER,
    mqtaverna        INTEGER,
    mqsoffitta       INTEGER,
    mqterrazzo       INTEGER,
    numbalconi       INTEGER,
    altrodescrizione TEXT,

    -- Concorrenza ottimistica e audit.
    version                     INTEGER     NOT NULL DEFAULT 1,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by_owner_account_id BIGINT REFERENCES owner_accounts(id) ON DELETE SET NULL,

    CONSTRAINT owner_home_overrides_version_chk CHECK (version >= 1)
);

CREATE INDEX IF NOT EXISTS idx_owner_home_overrides_stima
    ON owner_home_overrides (stima_id);

-- ---------------------------------------------------------------------------
-- La difesa del database: chi scrive e la casa scritta devono stare nella
-- stessa agenzia. Come nella 066 si verifica e non si deriva, e il confronto
-- con NULL non e' "non so, lascio passare": quando il riferimento c'e', le due
-- agenzie devono esistere entrambe e coincidere.
--
-- `updated_by_owner_account_id` e' nullable (l'ON DELETE SET NULL lo azzera
-- quando l'account sparisce), quindi il trigger lascia passare il NULL: e' la
-- cancellazione dell'account, non una scrittura.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION owner_home_overrides_agency_integrity() RETURNS trigger AS $fn$
DECLARE
    a_contact BIGINT;
    a_stima   BIGINT;
BEGIN
    IF NEW.updated_by_owner_account_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT ct.agency_id INTO a_contact
      FROM owner_accounts oa
      JOIN contacts ct ON ct.id = oa.contact_id
     WHERE oa.id = NEW.updated_by_owner_account_id;

    SELECT s.agency_id INTO a_stima
      FROM stime s
     WHERE s.id = NEW.stima_id;

    IF a_contact IS NULL OR a_stima IS NULL OR a_contact <> a_stima THEN
        RAISE EXCEPTION
            'LMC-10 owner/stima tenancy: owner account % (agency %) cannot update estimation % (agency %)',
            NEW.updated_by_owner_account_id, a_contact, NEW.stima_id, a_stima;
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
        WHERE t.tgname = 'trg_owner_home_overrides_agency_integrity'
          AND c.relname = 'owner_home_overrides' AND n.nspname = 'public'
          AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_owner_home_overrides_agency_integrity
            BEFORE INSERT OR UPDATE OF stima_id, updated_by_owner_account_id
            ON owner_home_overrides
            FOR EACH ROW EXECUTE FUNCTION owner_home_overrides_agency_integrity();
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- Verifica: la tabella e' quella che questo file dice, non una omonima
-- preesistente con un'altra forma (IF NOT EXISTS lascerebbe passare la
-- seconda in silenzio). E soprattutto: i tipi dei campi override coincidono
-- con quelli delle colonne di `stime` da cui derivano. Un giorno qualcuno
-- cambiera' `stime`, e la migration deve accorgersene qui, non a runtime.
-- ---------------------------------------------------------------------------

DO $do$
DECLARE
    v_count integer;
    v_campo text;
    v_mio   text;
    v_suo   text;
    campi   text[] := ARRAY['mq','piano','locali','bagni','ascensore','anno','stato',
                            'pertinenze','mqgiardino','mqgarage','mqcantina','mqpostoauto',
                            'mqtaverna','mqsoffitta','mqterrazzo','numbalconi',
                            'altrodescrizione'];
BEGIN
    SELECT count(*) INTO v_count
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name   = 'owner_home_overrides'
       AND column_name IN ('id','stima_id','mq','piano','locali','bagni','ascensore',
                           'anno','stato','pertinenze','mqgiardino','mqgarage',
                           'mqcantina','mqpostoauto','mqtaverna','mqsoffitta',
                           'mqterrazzo','numbalconi','altrodescrizione','version',
                           'created_at','updated_at','updated_by_owner_account_id');
    IF v_count <> 23 THEN
        RAISE EXCEPTION
            'LMC-10 068: owner_home_overrides has % of the 23 expected columns', v_count;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name   = 'owner_home_overrides'
           AND column_name  = 'agency_id'
    ) THEN
        RAISE EXCEPTION
            'LMC-10 068: owner_home_overrides must not carry agency_id; tenancy is derived from stime';
    END IF;

    -- Nessuna colonna per i campi che il proprietario non puo' modificare.
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name   = 'owner_home_overrides'
           AND column_name IN ('comune','microzona','via','civico','tipologia',
                               'posizionemare','distanzamare','barrieramare',
                               'vistamareyn','vistamare','vistamaredettaglio',
                               'contact_id','lead_id','token','prezzo_mq_base',
                               'lead_status','note_internal','nome','cognome',
                               'email','telefono')
    ) THEN
        RAISE EXCEPTION
            'LMC-10 068: owner_home_overrides carries a column outside the LMC-10 whitelist';
    END IF;

    FOREACH v_campo IN ARRAY campi LOOP
        SELECT data_type || coalesce('(' || character_maximum_length || ')', '')
          INTO v_mio
          FROM information_schema.columns
         WHERE table_schema='public' AND table_name='owner_home_overrides'
           AND column_name = v_campo;
        SELECT data_type || coalesce('(' || character_maximum_length || ')', '')
          INTO v_suo
          FROM information_schema.columns
         WHERE table_schema='public' AND table_name='stime'
           AND column_name = v_campo;
        IF v_suo IS NULL THEN
            RAISE EXCEPTION
                'LMC-10 068: stime has no column % to override', v_campo;
        END IF;
        IF v_mio IS DISTINCT FROM v_suo THEN
            RAISE EXCEPTION
                'LMC-10 068: override column % is % but stime.% is %',
                v_campo, v_mio, v_campo, v_suo;
        END IF;
    END LOOP;

    SELECT count(*) INTO v_count
      FROM pg_constraint
     WHERE conrelid = 'public.owner_home_overrides'::regclass
       AND contype  = 'u';
    IF v_count <> 1 THEN
        RAISE EXCEPTION
            'LMC-10 068: owner_home_overrides must carry exactly one UNIQUE (stima_id), found %', v_count;
    END IF;

    SELECT count(*) INTO v_count
      FROM pg_constraint
     WHERE conrelid = 'public.owner_home_overrides'::regclass
       AND contype  = 'f';
    IF v_count <> 2 THEN
        RAISE EXCEPTION
            'LMC-10 068: owner_home_overrides must carry exactly two foreign keys, found %', v_count;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        WHERE t.tgname = 'trg_owner_home_overrides_agency_integrity'
          AND c.relname = 'owner_home_overrides'
    ) THEN
        RAISE EXCEPTION
            'LMC-10 068: trg_owner_home_overrides_agency_integrity is missing';
    END IF;
END
$do$;
