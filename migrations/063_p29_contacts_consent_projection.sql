-- P29-1.1 contacts: the consent projection.
--
-- Additive, idempotent. Adds EIGHT columns to `contacts`, two foreign keys and
-- three CHECK constraints. Creates no table. NON ESEGUE ALCUN BACKFILL, e non
-- tocca un solo valore esistente.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- ---------------------------------------------------------------------------
-- COSA SONO QUESTE COLONNE
--
-- La proiezione dello stato corrente. La verita' e' `consent_events` (062);
-- questa e' la copia veloce, aggiornata nella STESSA transazione dell'evento
-- dal modulo `consent/`, che e' l'unico percorso di scrittura.
--
-- Se le due divergono, vince lo storico e questa si ricostruisce.
--
-- ---------------------------------------------------------------------------
-- PERCHE' NON C'E' UNA COLONNA `marketing_status`
--
-- Era la scelta ovvia e sarebbe stata sbagliata. Il censimento P29-1.0 dice
-- che `contacts` ha 53 righe: 12 TRUE, 31 FALSE, 10 NULL. Aggiungere
-- `marketing_status` senza backfill darebbe 53 righe con lo stato a NULL e
-- `marketing_consent` che dice tutt'altro: due colonne sulla stessa cosa, in
-- disaccordo dal primo giorno.
--
-- Lo stato corrente del marketing si DERIVA da cio' che c'e' gia', piu' una
-- sola colonna nuova:
--
--     marketing_consent IS TRUE                       -> granted
--     marketing_revoked_at IS NOT NULL (e non TRUE)   -> revoked
--     altrimenti                                      -> never_given
--
-- `never_given` e `revoked` restano stati DISTINTI, che era il requisito.
-- Entrambi bloccano l'invio; non sono la stessa cosa, e un operatore che
-- guarda la scheda deve poterli distinguere.
--
-- E soprattutto: P24 (`database_revival/eligibility.py`) legge
-- `c.marketing_consent IS TRUE` dentro un predicato con quattro NOT EXISTS.
-- Quella riga non cambia, non viene riscritta, e continua a dare la risposta
-- giusta - una revoca porta la colonna a FALSE e P24 si allinea da solo. E'
-- questa la ragione per cui la proiezione vive su `contacts` e non solo nello
-- storico.
--
-- ---------------------------------------------------------------------------
-- PRIVACY/TERMINI: COLONNE PROPRIE, NOME PROPRIO
--
-- `privacy_terms_*` e non `privacy_consent_*`. Il nome dice cosa il funnel
-- raccoglie davvero: l'accettazione del blocco Privacy Policy + Termini d'Uso
-- + Istruzioni eliminazione dati, necessaria per usare il servizio. Non e' un
-- consenso facoltativo e non e' il marketing.
--
-- DIPENDENZA ESTERNA DICHIARATA: oggi il backend NON riceve questo dato.
-- Verificato leggendo main.py, che alla riga 714 legge dal payload pubblico
-- il solo campo di consenso marketing e nient'altro, e
-- cercando privacy/termini/terms/policy in main.py, core/ e crm/: zero
-- occorrenze. Il checkbox esiste sul form di stima360.it, che vive fuori da
-- questo repository e su PROD. Finche' quel campo non arriva, ogni contatto
-- avra' `privacy_terms_accepted` NULL - cioe' `never_given`, che e' la
-- fotografia corretta di cio' che il backend sa, non un bug.
--
-- La semantica esatta di quel checkbox NON viene inventata qui: questa
-- migration predispone il contenitore e lascia che il contenuto lo definisca
-- chi controlla il form.
--
-- ---------------------------------------------------------------------------
-- IL LEGACY, E COSA QUESTA MIGRATION NON FA
--
-- Il censimento P29-1.0 ha chiuso la questione: nessuna ricostruzione
-- massiva. 12 contatti hanno consenso TRUE senza storico (1 con source NULL,
-- 10 da full-crm-e2e, e la distribuzione e' nel report). Restano com'e':
--
--   * nessun evento retroattivo inventato;
--   * nessun timestamp inventato;
--   * nessuna notice fabbricata per riempire `*_notice_id`;
--   * i 10 record full-crm-e2e non vengono "ripuliti".
--
-- Un contatto legacy si riconosce senza ambiguita': consenso TRUE, nessuna
-- riga in `consent_events`, `marketing_consent_source` e
-- `marketing_consent_notice_id` a NULL. L'API lo espone come `granted` con
-- source 'legacy' e version assente, che significa esattamente "acconsentito
-- prima che tracciassimo l'origine" - un'informazione vera.

-- ---------------------------------------------------------------------------
-- 1. Le colonne del MARKETING.
--
-- Tre, non di piu'. `marketing_consent` e `marketing_consent_at` esistono dalla
-- 001 e restano quello che sono: lo stato e l'istante della concessione. Non
-- vengono rinominate, non vengono deprecate, la loro semantica non cambia.
--
-- `marketing_consent_at` NON viene azzerato da una revoca: resta la data della
-- concessione che e' stata revocata. Una colonna che dicesse "quando fu dato"
-- e venisse svuotata al momento della revoca cancellerebbe l'unica cosa che
-- distingue "ha detto si' e poi no" da "non ha mai detto si'".
-- ---------------------------------------------------------------------------
ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS marketing_revoked_at TIMESTAMPTZ;

ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS marketing_consent_source VARCHAR(40);

ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS marketing_consent_notice_id BIGINT;

-- ---------------------------------------------------------------------------
-- 2. Le colonne di PRIVACY/TERMINI.
--
-- Cinque, perche' qui non c'e' nulla di preesistente da riusare. La forma
-- rispecchia deliberatamente quella del marketing - un booleano, un istante di
-- accettazione, un istante di revoca, una provenienza, una notice - cosi' le
-- due proiezioni si leggono allo stesso modo pur restando due scopi
-- indipendenti: nessuno dei due e' annidato nell'altro, e l'uno non implica
-- l'altro.
-- ---------------------------------------------------------------------------
ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS privacy_terms_accepted BOOLEAN;

ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS privacy_terms_accepted_at TIMESTAMPTZ;

ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS privacy_terms_revoked_at TIMESTAMPTZ;

ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS privacy_terms_source VARCHAR(40);

ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS privacy_terms_notice_id BIGINT;

-- ---------------------------------------------------------------------------
-- 3. Le due FK verso il registro delle notice.
--
-- ON DELETE RESTRICT, come ogni FK di P26 in poi: una notice che risulta
-- accettata da qualcuno non deve poter sparire lasciando la proiezione a
-- puntare al nulla. E' anche il motivo per cui 061_down deve girare dopo
-- questo down, non prima.
--
-- Aggiunte con la guardia su pg_constraint, come la 060: ADD CONSTRAINT non ha
-- una forma IF NOT EXISTS, e una migration deve poter essere ri-applicata.
-- ---------------------------------------------------------------------------
DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.contacts'::regclass
           AND conname  = 'contacts_marketing_notice_fk'
    ) THEN
        ALTER TABLE contacts
            ADD CONSTRAINT contacts_marketing_notice_fk
            FOREIGN KEY (marketing_consent_notice_id) REFERENCES consent_notices(id)
            ON DELETE RESTRICT;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.contacts'::regclass
           AND conname  = 'contacts_privacy_terms_notice_fk'
    ) THEN
        ALTER TABLE contacts
            ADD CONSTRAINT contacts_privacy_terms_notice_fk
            FOREIGN KEY (privacy_terms_notice_id) REFERENCES consent_notices(id)
            ON DELETE RESTRICT;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 4. I vincoli di coerenza.
--
-- UNO STATO NON PUO' ESSERE CONCESSO E REVOCATO INSIEME.
--
-- Quando una revoca arriva, la proiezione passa a FALSE e `*_revoked_at` si
-- valorizza. Quando un nuovo consenso arriva dopo una revoca, la proiezione
-- torna TRUE e `*_revoked_at` torna a NULL - la revoca non sparisce, resta in
-- `consent_events`, che e' il posto dove la storia vive. Questi due CHECK sono
-- cio' che rende impossibile dimenticarsene.
--
-- Entrambi passano sulle 53 righe esistenti senza toccarle: `*_revoked_at` e'
-- NULL su tutte, perche' la colonna e' nata in questa migration.
--
-- L'ASIMMETRIA FRA I DUE SCOPI E' VOLUTA E VA SPIEGATA.
--
-- Per privacy/termini si vincola anche che un'accettazione porti il proprio
-- istante: la colonna e' nuova, nessuna riga esiste, il vincolo nasce pulito.
--
-- Per il marketing NON si puo': il censimento P29-1.0 ha trovato 1 contatto
-- con `marketing_consent` TRUE e `marketing_consent_at` NULL (contact_id 12,
-- agency_id 1, source NULL). Aggiungere li' lo stesso CHECK farebbe fallire
-- questa migration, oppure - peggio - obbligherebbe a inventare un timestamp
-- per quella riga. Il committente ha deciso: contact 12 resta legacy. Il
-- vincolo che manca e' la registrazione onesta di quella decisione.
--
-- Da P29-1.2 in poi nessuna scrittura NUOVA puo' produrre quello stato: il
-- percorso unico scrive sempre i due valori insieme.
-- ---------------------------------------------------------------------------
DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.contacts'::regclass
           AND conname  = 'contacts_marketing_state_chk'
    ) THEN
        ALTER TABLE contacts
            ADD CONSTRAINT contacts_marketing_state_chk
            CHECK (marketing_revoked_at IS NULL OR marketing_consent IS NOT TRUE);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.contacts'::regclass
           AND conname  = 'contacts_privacy_terms_state_chk'
    ) THEN
        ALTER TABLE contacts
            ADD CONSTRAINT contacts_privacy_terms_state_chk
            CHECK (privacy_terms_revoked_at IS NULL OR privacy_terms_accepted IS NOT TRUE);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.contacts'::regclass
           AND conname  = 'contacts_privacy_terms_accepted_chk'
    ) THEN
        ALTER TABLE contacts
            ADD CONSTRAINT contacts_privacy_terms_accepted_chk
            CHECK (privacy_terms_accepted IS NOT TRUE OR privacy_terms_accepted_at IS NOT NULL);
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 5. Prova, riletta dal catalogo.
--
-- Le otto colonne, le due FK non distruttive, i tre CHECK. E una verifica in
-- piu' che vale da sola l'intero blocco: che `marketing_consent` e
-- `marketing_consent_at` SIANO ANCORA LI'. Questa migration non le tocca, ma
-- e' esattamente la migration in cui qualcuno, un giorno, sarebbe tentato di
-- "riordinarle" - e P24 smetterebbe di funzionare senza che nulla lo dica.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_nuove    INT;
    v_legacy   INT;
    v_riga     text[];
    v_deltype  "char";
    v_vincolo  BOOLEAN;
BEGIN
    SELECT count(*) INTO v_nuove
      FROM information_schema.columns
     WHERE table_name = 'contacts'
       AND column_name IN (
           'marketing_revoked_at', 'marketing_consent_source', 'marketing_consent_notice_id',
           'privacy_terms_accepted', 'privacy_terms_accepted_at', 'privacy_terms_revoked_at',
           'privacy_terms_source', 'privacy_terms_notice_id');

    IF v_nuove <> 8 THEN
        RAISE EXCEPTION
            'P29-1.1 063: contacts has % of the 8 projection columns', v_nuove;
    END IF;

    SELECT count(*) INTO v_legacy
      FROM information_schema.columns
     WHERE table_name = 'contacts'
       AND column_name IN ('marketing_consent', 'marketing_consent_at');

    IF v_legacy <> 2 THEN
        RAISE EXCEPTION
            'P29-1.1 063: marketing_consent/marketing_consent_at are no longer both present (found %); P24 database_revival reads marketing_consent directly and would break',
            v_legacy;
    END IF;

    FOREACH v_riga SLICE 1 IN ARRAY ARRAY[
        ARRAY['contacts_marketing_notice_fk'],
        ARRAY['contacts_privacy_terms_notice_fk']
    ]
    LOOP
        SELECT confdeltype INTO v_deltype
          FROM pg_constraint
         WHERE conrelid = 'public.contacts'::regclass
           AND conname  = v_riga[1];

        IF v_deltype IS NULL THEN
            RAISE EXCEPTION 'P29-1.1 063: % is not installed', v_riga[1];
        END IF;
        IF v_deltype NOT IN ('a', 'r') THEN
            RAISE EXCEPTION
                'P29-1.1 063: % destroys rows on a parent delete (confdeltype=%)',
                v_riga[1], v_deltype;
        END IF;
    END LOOP;

    FOREACH v_riga SLICE 1 IN ARRAY ARRAY[
        ARRAY['contacts_marketing_state_chk'],
        ARRAY['contacts_privacy_terms_state_chk'],
        ARRAY['contacts_privacy_terms_accepted_chk']
    ]
    LOOP
        SELECT TRUE INTO v_vincolo
          FROM pg_constraint
         WHERE conrelid = 'public.contacts'::regclass
           AND conname  = v_riga[1]
           AND contype  = 'c';

        IF v_vincolo IS NULL THEN
            RAISE EXCEPTION 'P29-1.1 063: % is missing', v_riga[1];
        END IF;
    END LOOP;
END
$do$;

-- ---------------------------------------------------------------------------
-- 6. Prova: i CHECK MORDONO, e nessuna riga esistente e' stata toccata.
--
-- La sonda prende un contatto vero, prova a metterlo in uno stato
-- contraddittorio - concesso e revocato insieme - e verifica che il database
-- rifiuti. Poi verifica che la transizione legittima passi. Annulla tutto con
-- il savepoint implicito di BEGIN ... EXCEPTION.
--
-- Salta quando `contacts` e' vuota: un database appena costruito dal runner non
-- ha contatti, e una migration che fallisse li' sarebbe inapplicabile a uno
-- schema vuoto.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_contatto   BIGINT;
    v_mkt_rifiut BOOLEAN := FALSE;
    v_pri_rifiut BOOLEAN := FALSE;
    v_pri_senza  BOOLEAN := FALSE;
    v_revoca_ok  BOOLEAN := FALSE;
    v_sentinel   CONSTANT text := 'P29_063_PROBE_ROLLBACK';
BEGIN
    SELECT id INTO v_contatto FROM contacts ORDER BY id LIMIT 1;

    IF v_contatto IS NULL THEN
        RETURN;
    END IF;

    BEGIN
        -- Concesso e revocato insieme, sul marketing.
        BEGIN
            UPDATE contacts
               SET marketing_consent = TRUE, marketing_revoked_at = NOW()
             WHERE id = v_contatto;
        EXCEPTION WHEN check_violation THEN
            v_mkt_rifiut := TRUE;
        END;

        -- Concesso e revocato insieme, su privacy/termini.
        BEGIN
            UPDATE contacts
               SET privacy_terms_accepted = TRUE,
                   privacy_terms_accepted_at = NOW(),
                   privacy_terms_revoked_at = NOW()
             WHERE id = v_contatto;
        EXCEPTION WHEN check_violation THEN
            v_pri_rifiut := TRUE;
        END;

        -- Accettazione privacy senza il proprio istante.
        BEGIN
            UPDATE contacts
               SET privacy_terms_accepted = TRUE, privacy_terms_accepted_at = NULL
             WHERE id = v_contatto;
        EXCEPTION WHEN check_violation THEN
            v_pri_senza := TRUE;
        END;

        -- La transizione legittima: revoca del marketing.
        UPDATE contacts
           SET marketing_consent = FALSE, marketing_revoked_at = NOW()
         WHERE id = v_contatto;
        v_revoca_ok := TRUE;

        RAISE EXCEPTION USING MESSAGE = v_sentinel;
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM <> v_sentinel THEN
            RAISE;
        END IF;
    END;

    IF NOT v_mkt_rifiut THEN
        RAISE EXCEPTION 'P29-1.1 063: a contact granted and revoked at once was accepted (marketing)';
    END IF;
    IF NOT v_pri_rifiut THEN
        RAISE EXCEPTION 'P29-1.1 063: a contact granted and revoked at once was accepted (privacy_terms)';
    END IF;
    IF NOT v_pri_senza THEN
        RAISE EXCEPTION 'P29-1.1 063: a privacy_terms acceptance without its instant was accepted';
    END IF;
    IF NOT v_revoca_ok THEN
        RAISE EXCEPTION 'P29-1.1 063: a legitimate revocation was refused';
    END IF;
END
$do$;
