-- P29-1.1 consent notice registry.
--
-- Additive, idempotent. Creates ONE table and the guards that make it a
-- register rather than a document store. It alters no existing table, adds no
-- column anywhere, and SEEDS NO ROW.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- ---------------------------------------------------------------------------
-- COS'E' QUESTA TABELLA
--
-- Il testo che una persona ha visto quando ha accettato qualcosa. Non un CMS,
-- non un contenuto editoriale, non qualcosa che il prodotto legge per
-- decidere: una prova storica, scritta una volta e riletta anni dopo da chi
-- deve sapere COSA fu accettato, non solo che qualcosa lo fu.
--
-- Senza questo registro, `consent_events.notice_id` sarebbe una stringa che
-- punta al nulla: 'stima360-2026-01' non dice cosa quella versione conteneva.
--
-- ---------------------------------------------------------------------------
-- PERCHE' NON E' TENANT-SCOPED
--
-- Non ha `agency_id`, e l'assenza e' la decisione. Il testo e' quello del
-- funnel pubblico stima360.it: uno solo per tutta la rete. Dargli un
-- `agency_id` significherebbe ammettere che due agenzie possano aver mostrato
-- due testi diversi sotto lo stesso identificativo di versione, che e'
-- esattamente cio' che un registro di prove non deve poter rappresentare.
--
-- La tenancy vive sugli EVENTI (062), dove i dati sono personali. Qui non c'e'
-- alcun dato personale: solo il testo di una notice. Ogni agenzia lo legge,
-- nessuna agenzia lo possiede, solo il Platform Admin lo scrive.
--
-- ---------------------------------------------------------------------------
-- IL LEGACY, E PERCHE' `content_available` ESISTE
--
-- Il censimento P29-1.0 ha trovato 12 contatti con consenso TRUE e nessuno
-- storico: consensi raccolti prima che questo registro esistesse. Il testo che
-- quelle persone lessero NON E' NOTO.
--
-- La tentazione e' registrare una notice 'legacy' con un contenuto
-- verosimile. Sarebbe fabbricare una prova: una riga che sembra il testo
-- accettato e non lo e'. `content_available = FALSE` e' l'alternativa onesta -
-- una notice puo' essere registrata dichiarando, nella riga stessa, che il suo
-- contenuto originale non e' disponibile, e `content` deve allora DIRLO invece
-- di simularlo.
--
-- Questa migration non ne inserisce nessuna. Nella proiezione su `contacts`
-- (063) il legacy si esprime gia' senza inventare niente: notice_id IS NULL.
-- La colonna esiste perche' il giorno in cui servisse registrare una notice
-- storica il modo onesto sia gia' disponibile, non perche' vada usata ora.

-- ---------------------------------------------------------------------------
-- 1. La tabella.
--
-- `content_hash` e' il sigillo. Non e' un'ottimizzazione e non serve a cercare:
-- serve a dimostrare, in qualunque momento futuro, che il testo in `content`
-- e' quello registrato allora. Ricalcolabile da chiunque legga la riga.
--
-- `context` e' NOT NULL perche' "dove e' stato mostrato" fa parte della prova
-- tanto quanto il testo: la stessa informativa presentata in un form di
-- valutazione e in un'email non e' la stessa accettazione.
--
-- `created_by` e' testo, congelato alla scrittura, come `platform_audit_log
-- .actor_label`: l'identita' di chi ha pubblicato resta leggibile anche quando
-- l'account non c'e' piu'.
--
-- Nessuna FK su questa tabella, nella stessa direzione: e' un registro
-- immutabile, e un registro immutabile non tiene pretese referenziali sulle
-- entita' che descrive (vedi 057 per l'intera argomentazione).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS consent_notices (
    id                BIGSERIAL    PRIMARY KEY,
    purpose           VARCHAR(20)  NOT NULL,
    version           VARCHAR(60)  NOT NULL,
    content           TEXT         NOT NULL,
    content_hash      CHAR(64)     NOT NULL,
    content_available BOOLEAN      NOT NULL DEFAULT TRUE,
    context           VARCHAR(200) NOT NULL,
    valid_from        TIMESTAMPTZ  NOT NULL,
    valid_to          TIMESTAMPTZ,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_by        VARCHAR(200) NOT NULL,

    -- I due scopi del prodotto, e nessun terzo. 'privacy_terms' e' il blocco
    -- Privacy Policy + Termini d'Uso + Istruzioni eliminazione dati del funnel;
    -- 'marketing' e' il consenso facoltativo UNICO Email + WhatsApp. Non
    -- esistono 'marketing_email' e 'marketing_whatsapp': la separazione non
    -- deve essere nemmeno rappresentabile.
    CONSTRAINT consent_notices_purpose_chk
        CHECK (purpose IN ('privacy_terms', 'marketing')),

    CONSTRAINT consent_notices_version_chk    CHECK (BTRIM(version) <> ''),
    CONSTRAINT consent_notices_content_chk    CHECK (BTRIM(content) <> ''),
    CONSTRAINT consent_notices_context_chk    CHECK (BTRIM(context) <> ''),
    CONSTRAINT consent_notices_created_by_chk CHECK (BTRIM(created_by) <> ''),

    -- Un hash che non e' un hash non prova niente. La forma e' vincolata qui
    -- perche' la riga sbagliata si scopre al momento della verifica, cioe' anni
    -- dopo, cioe' troppo tardi.
    CONSTRAINT consent_notices_hash_chk
        CHECK (content_hash ~ '^[0-9a-f]{64}$'),

    CONSTRAINT consent_notices_validity_chk
        CHECK (valid_to IS NULL OR valid_to >= valid_from),

    -- Una versione identifica una notice, per sempre. Ripubblicare lo stesso
    -- identificativo con un testo diverso e' la cosa che questo vincolo esiste
    -- per rendere impossibile.
    CONSTRAINT consent_notices_purpose_version_unq UNIQUE (purpose, version)
);

-- ---------------------------------------------------------------------------
-- 2. Al massimo una versione corrente per purpose.
--
-- Indice unico PARZIALE: vincola solo le righe con `valid_to IS NULL`. Le
-- versioni pensionate restano quante sono, e devono restare - sono la storia.
-- Un UNIQUE ordinario su `purpose` vieterebbe la seconda versione di sempre.
-- ---------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS consent_notices_current_unq
    ON consent_notices (purpose)
 WHERE valid_to IS NULL;

CREATE INDEX IF NOT EXISTS idx_consent_notices_purpose_validity
    ON consent_notices (purpose, valid_from DESC);

-- ---------------------------------------------------------------------------
-- 3. Immutabilita': una sola transizione ammessa.
--
-- Questa tabella non e' append-only pura come `platform_audit_log`: una
-- versione deve poter essere PENSIONATA, cioe' passare da `valid_to IS NULL` a
-- una data. E' l'unico cambiamento che una riga puo' subire.
--
-- Tutto il resto e' rifiutato: contenuto, hash, versione, purpose, contesto,
-- valid_from, autore, istante di creazione. E anche un valid_to gia' scritto,
-- che non puo' essere ne' spostato ne' riportato a NULL - riaprire una
-- versione pensionata significherebbe che il testo corrente di ieri puo'
-- tornare corrente domani senza che nessuna riga lo registri.
--
-- Un testo nuovo e' una riga nuova. Anche per una virgola.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION consent_notices_immutable()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'consent_notices is a register: DELETE is refused (id=%). A notice that was published stays published.',
            OLD.id;
    END IF;

    IF OLD.valid_to IS NOT NULL THEN
        RAISE EXCEPTION
            'consent_notices id=% is already retired (valid_to=%): a retired notice cannot be modified or reopened.',
            OLD.id, OLD.valid_to;
    END IF;

    IF NEW.valid_to IS NULL THEN
        RAISE EXCEPTION
            'consent_notices id=%: the only permitted UPDATE is retiring the notice (valid_to NULL -> a timestamp).',
            OLD.id;
    END IF;

    IF  NEW.id                IS DISTINCT FROM OLD.id
     OR NEW.purpose           IS DISTINCT FROM OLD.purpose
     OR NEW.version           IS DISTINCT FROM OLD.version
     OR NEW.content           IS DISTINCT FROM OLD.content
     OR NEW.content_hash      IS DISTINCT FROM OLD.content_hash
     OR NEW.content_available IS DISTINCT FROM OLD.content_available
     OR NEW.context           IS DISTINCT FROM OLD.context
     OR NEW.valid_from        IS DISTINCT FROM OLD.valid_from
     OR NEW.created_at        IS DISTINCT FROM OLD.created_at
     OR NEW.created_by        IS DISTINCT FROM OLD.created_by
    THEN
        RAISE EXCEPTION
            'consent_notices id=% is immutable: only valid_to may change. Publish a new version instead.',
            OLD.id;
    END IF;

    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS trg_consent_notices_immutable ON consent_notices;
CREATE TRIGGER trg_consent_notices_immutable
    BEFORE UPDATE OR DELETE ON consent_notices
    FOR EACH ROW EXECUTE FUNCTION consent_notices_immutable();

-- ENABLE ALWAYS: un trigger ordinario non scatta quando
-- `session_replication_role` e' 'replica', cioe' un parametro di sessione lo
-- spegne. Vedi la nota estesa in 062, dove il buco e' stato misurato.
ALTER TABLE consent_notices ENABLE ALWAYS TRIGGER trg_consent_notices_immutable;

-- TRUNCATE non e' ne' UPDATE ne' DELETE, non fa scattare alcun trigger di riga
-- e svuota la tabella in una istruzione. Serve un trigger di STATEMENT.
CREATE OR REPLACE FUNCTION consent_notices_no_truncate()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
BEGIN
    RAISE EXCEPTION 'consent_notices is a register: TRUNCATE is refused.';
END
$fn$;

DROP TRIGGER IF EXISTS trg_consent_notices_no_truncate ON consent_notices;
CREATE TRIGGER trg_consent_notices_no_truncate
    BEFORE TRUNCATE ON consent_notices
    FOR EACH STATEMENT EXECUTE FUNCTION consent_notices_no_truncate();

ALTER TABLE consent_notices ENABLE ALWAYS TRIGGER trg_consent_notices_no_truncate;

-- ---------------------------------------------------------------------------
-- 4. Prova, riletta dal catalogo.
--
-- La disciplina di 055, 057, 058, 059 e 060: la migration non da' per scontato
-- che le proprie istruzioni abbiano avuto effetto, lo chiede al catalogo.
--
-- Bit di pg_trigger.tgtype (pg_trigger.h):
--     ROW = 1, BEFORE = 2, INSERT = 4, DELETE = 8, UPDATE = 16,
--     TRUNCATE = 32, INSTEAD = 64
--   trigger di riga       : ROW(1) + BEFORE(2) + DELETE(8) + UPDATE(16) = 27
--   trigger di statement  : BEFORE(2) + TRUNCATE(32)                    = 34
--
-- INSERT e' asserito ASSENTE su entrambi: un guardiano che scattasse anche in
-- INSERT renderebbe la tabella non immutabile ma inscrivibile, e la prima
-- scrittura sarebbe quella che lo scopre.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_riga      text[];
    v_tgtype    smallint;
    v_tgenabled "char";
    v_funzione  text;
    v_atteso    smallint;
    v_indice    BOOLEAN;
BEGIN
    FOREACH v_riga SLICE 1 IN ARRAY ARRAY[
        ARRAY['trg_consent_notices_immutable','consent_notices','consent_notices_immutable','27'],
        ARRAY['trg_consent_notices_no_truncate','consent_notices','consent_notices_no_truncate','34']
    ]
    LOOP
        v_atteso := v_riga[4]::smallint;

        SELECT t.tgtype, t.tgenabled, p.proname
          INTO v_tgtype, v_tgenabled, v_funzione
          FROM pg_trigger t
          JOIN pg_proc p      ON p.oid = t.tgfoid
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE t.tgname  = v_riga[1]
           AND t.tgrelid = ('public.' || v_riga[2])::regclass
           AND n.nspname = 'public'
           AND NOT t.tgisinternal;

        IF v_tgtype IS NULL THEN
            RAISE EXCEPTION
                'P29-1.1 061: % is not installed on %', v_riga[1], v_riga[2];
        END IF;
        IF v_funzione <> v_riga[3] THEN
            RAISE EXCEPTION
                'P29-1.1 061: % calls %, expected %', v_riga[1], v_funzione, v_riga[3];
        END IF;
        -- 'A' = ENABLE ALWAYS. Vedi 062: un trigger 'O' e' spento da
        -- session_replication_role = 'replica'.
        IF v_tgenabled <> 'A' THEN
            RAISE EXCEPTION
                'P29-1.1 061: % is not ENABLE ALWAYS (tgenabled=%); session_replication_role would switch it off',
                v_riga[1], v_tgenabled;
        END IF;
        IF (v_tgtype & 2) = 0 OR (v_tgtype & 64) <> 0 THEN
            RAISE EXCEPTION
                'P29-1.1 061: % must fire BEFORE, not AFTER or INSTEAD OF', v_riga[1];
        END IF;
        IF (v_tgtype & v_atteso) <> v_atteso THEN
            RAISE EXCEPTION
                'P29-1.1 061: % does not cover the required operations (tgtype=%, wanted=%)',
                v_riga[1], v_tgtype, v_atteso;
        END IF;
        IF (v_tgtype & 4) <> 0 THEN
            RAISE EXCEPTION
                'P29-1.1 061: % must not fire on INSERT; the register is immutable, not read-only',
                v_riga[1];
        END IF;
    END LOOP;

    SELECT TRUE INTO v_indice
      FROM pg_indexes
     WHERE schemaname = 'public'
       AND tablename  = 'consent_notices'
       AND indexname  = 'consent_notices_current_unq';

    IF v_indice IS NULL THEN
        RAISE EXCEPTION
            'P29-1.1 061: consent_notices_current_unq is missing; two current notices for the same purpose would be storable';
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 5. Prova: i vincoli MORDONO.
--
-- Un vincolo che esiste e non morde vale zero. La sonda scrive righe vere,
-- prova a violare ogni regola per cui questa tabella e' stata scritta, e
-- verifica che il database rifiuti. Annulla tutto con il savepoint implicito
-- di un blocco BEGIN ... EXCEPTION, come 057, 058, 059 e 060: PL/pgSQL non puo'
-- eseguire istruzioni di controllo transazione, ma le variabili sono memoria e
-- non transazione, quindi i risultati sopravvivono al rollback.
--
-- Il sentinel viene ri-sollevato se e' diverso da quello atteso, cosi' un
-- fallimento vero dentro la sonda non viene mai inghiottito dal gestore.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_id            BIGINT;
    v_insert_ok     BOOLEAN := FALSE;
    v_due_correnti  BOOLEAN := FALSE;
    v_hash_rifiuto  BOOLEAN := FALSE;
    v_update_rifiut BOOLEAN := FALSE;
    v_delete_rifiut BOOLEAN := FALSE;
    v_retire_ok     BOOLEAN := FALSE;
    v_riapre_rifiut BOOLEAN := FALSE;
    v_sentinel      CONSTANT text := 'P29_061_PROBE_ROLLBACK';
BEGIN
    BEGIN
        INSERT INTO consent_notices
            (purpose, version, content, content_hash, context, valid_from, created_by)
        VALUES
            ('marketing', 'p29-061-probe', 'testo di prova',
             repeat('a', 64), 'probe', NOW(), 'migration:061')
        RETURNING id INTO v_id;
        v_insert_ok := TRUE;

        -- Una seconda versione corrente per lo stesso purpose.
        BEGIN
            INSERT INTO consent_notices
                (purpose, version, content, content_hash, context, valid_from, created_by)
            VALUES
                ('marketing', 'p29-061-probe-2', 'altro testo',
                 repeat('b', 64), 'probe', NOW(), 'migration:061');
        EXCEPTION WHEN unique_violation THEN
            v_due_correnti := TRUE;
        END;

        -- Un hash che non e' un hash.
        BEGIN
            INSERT INTO consent_notices
                (purpose, version, content, content_hash, context, valid_from, created_by)
            VALUES
                ('privacy_terms', 'p29-061-probe-3', 'testo',
                 'non-un-hash', 'probe', NOW(), 'migration:061');
        EXCEPTION WHEN check_violation THEN
            v_hash_rifiuto := TRUE;
        END;

        -- Il contenuto non si tocca.
        BEGIN
            UPDATE consent_notices SET content = 'testo riscritto' WHERE id = v_id;
        EXCEPTION WHEN raise_exception THEN
            v_update_rifiut := TRUE;
        END;

        -- Una notice pubblicata non si cancella.
        BEGIN
            DELETE FROM consent_notices WHERE id = v_id;
        EXCEPTION WHEN raise_exception THEN
            v_delete_rifiut := TRUE;
        END;

        -- Il pensionamento e' l'unica transizione ammessa.
        UPDATE consent_notices SET valid_to = NOW() WHERE id = v_id;
        v_retire_ok := TRUE;

        -- E non si torna indietro.
        BEGIN
            UPDATE consent_notices SET valid_to = NULL WHERE id = v_id;
        EXCEPTION WHEN raise_exception THEN
            v_riapre_rifiut := TRUE;
        END;

        RAISE EXCEPTION USING MESSAGE = v_sentinel;
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM <> v_sentinel THEN
            RAISE;
        END IF;
    END;

    IF NOT v_insert_ok THEN
        RAISE EXCEPTION 'P29-1.1 061: a legitimate notice was refused';
    END IF;
    IF NOT v_due_correnti THEN
        RAISE EXCEPTION 'P29-1.1 061: two current notices for the same purpose were accepted';
    END IF;
    IF NOT v_hash_rifiuto THEN
        RAISE EXCEPTION 'P29-1.1 061: a malformed content_hash was accepted';
    END IF;
    IF NOT v_update_rifiut THEN
        RAISE EXCEPTION 'P29-1.1 061: the content of a published notice was rewritable';
    END IF;
    IF NOT v_delete_rifiut THEN
        RAISE EXCEPTION 'P29-1.1 061: a published notice was deletable';
    END IF;
    IF NOT v_retire_ok THEN
        RAISE EXCEPTION 'P29-1.1 061: a legitimate retirement was refused';
    END IF;
    IF NOT v_riapre_rifiut THEN
        RAISE EXCEPTION 'P29-1.1 061: a retired notice was reopened';
    END IF;
END
$do$;
