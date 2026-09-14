-- P28 acting agency context.
--
-- Additive, idempotent. Adds TWO columns to `operator_sessions` and creates no
-- table. No backfill: nessuna sessione esistente sta impersonando niente, e
-- l'assenza e' esattamente lo stato giusto per tutte.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- ---------------------------------------------------------------------------
-- PERCHE' QUESTE DUE COLONNE ESISTONO
--
-- Il Platform Superadmin deve poter operare dentro il CRM di un'agenzia. La
-- via che NON si e' presa e' quella ovvia:
--
--     if is_platform_admin: salta i filtri tenant
--
-- Quella riga renderebbe il Superadmin invisibile allo scoping - vedrebbe
-- tutte le agenzie insieme, sempre, senza averlo chiesto e senza che nulla lo
-- registri. P27-1 (decisione D1) ha TOLTO il ramo cross-agency da
-- `core/scope.py` proprio per non averla, e il commento che lascio' li' diceva
-- che una via esplicita sarebbe arrivata "come una decisione con un nome
-- sopra". Questa e' quella decisione.
--
-- L'acting non allarga lo scope: ne cambia il valore. Il predicate resta
-- `agency_id = %s`, identico per il titolare, per l'agente e per il
-- Superadmin; cio' che cambia e' QUALE agenzia ci finisce dentro.
--
-- ---------------------------------------------------------------------------
-- PERCHE' SULLA SESSIONE, E NON ALTROVE
--
-- Perche' e' l'unico posto in cui il client non arriva. `resolve_session`
-- rilegge la riga a ogni richiesta - utente, membership, stato agenzia - ed e'
-- la proprieta' su cui si regge tutto il resto: una revoca ha effetto alla
-- chiamata successiva senza che nessuno debba andare a invalidare qualcosa.
-- L'acting eredita quella proprieta' gratis.
--
-- Un header firmato, un cookie a parte o un campo nel corpo sarebbero tutti
-- valori che il client trasporta, e P26-1 dice che un `agency_id` proveniente
-- dallo stato del client e' inaffidabile PER COSTRUZIONE, non per disciplina.
--
-- E una tabella dedicata `platform_acting_contexts` conserverebbe una storia
-- che `platform_audit_log` gia' conserva meglio - append-only per trigger.
-- Due storie della stessa cosa divergono.
--
-- ---------------------------------------------------------------------------
-- COSA QUESTA MIGRATION NON FA
--
-- Non tocca `agency_memberships`, e non la nomina. Il Superadmin non diventa
-- membro dell'agenzia in cui entra: se lo diventasse, uscire vorrebbe dire
-- cancellare una riga di appartenenza, e la differenza fra "ha lavorato qui un
-- pomeriggio" e "e' stato dei nostri" sparirebbe dal database.

-- ---------------------------------------------------------------------------
-- 1. Le due colonne.
-- ---------------------------------------------------------------------------
ALTER TABLE operator_sessions
    ADD COLUMN IF NOT EXISTS acting_agency_id BIGINT;

ALTER TABLE operator_sessions
    ADD COLUMN IF NOT EXISTS acting_entered_at TIMESTAMPTZ;

-- ON DELETE RESTRICT, come ogni FK verso `agencies` da P26 in poi.
--
-- Non CASCADE: cancellare un'agenzia porterebbe via delle SESSIONI, cioe' il
-- diritto di stare collegati di persone che con quell'agenzia non c'entrano.
-- Non SET NULL: azzererebbe in silenzio l'unica traccia in linea del fatto che
-- qualcuno stava operando li' dentro proprio mentre l'agenzia spariva.
--
-- In pratica il vincolo non mordera' quasi mai: `agencies` non ha una DELETE
-- su nessuna superficie - si archivia, non si cancella - quindi questo e' il
-- presidio contro una cancellazione manuale, che e' esattamente l'occasione in
-- cui serve.
DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.operator_sessions'::regclass
           AND conname = 'operator_sessions_acting_agency_fk'
    ) THEN
        ALTER TABLE operator_sessions
            ADD CONSTRAINT operator_sessions_acting_agency_fk
            FOREIGN KEY (acting_agency_id) REFERENCES agencies(id)
            ON DELETE RESTRICT;
    END IF;
END
$do$;

-- Le due colonne stanno insieme o non stanno. Un acting senza istante di
-- ingresso e' uno stato che nessun lettore sa interpretare: il registro direbbe
-- che qualcuno e' dentro da un momento che non esiste.
DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.operator_sessions'::regclass
           AND conname = 'operator_sessions_acting_chk'
    ) THEN
        ALTER TABLE operator_sessions
            ADD CONSTRAINT operator_sessions_acting_chk
            CHECK ((acting_agency_id IS NULL) = (acting_entered_at IS NULL));
    END IF;
END
$do$;

-- Il percorso di lettura inverso: quali sessioni stanno dentro un'agenzia.
-- Parziale, perche' la stragrande maggioranza delle righe ha NULL qui e
-- indicizzarle tutte sarebbe indicizzare il nulla.
CREATE INDEX IF NOT EXISTS idx_operator_sessions_acting
    ON operator_sessions (acting_agency_id)
 WHERE acting_agency_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 2. Prova, riletta dal catalogo.
--
-- La disciplina di 055, 057, 058 e 059: la migration non da' per scontato che
-- le proprie istruzioni abbiano avuto effetto, lo chiede al catalogo. Qui si
-- controllano le due cose la cui assenza non si noterebbe finche' non conta -
-- la FK non distruttiva e il CHECK appaiato.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_colonne INT;
    v_deltype "char";
    v_check   BOOLEAN;
BEGIN
    SELECT count(*) INTO v_colonne
      FROM information_schema.columns
     WHERE table_name = 'operator_sessions'
       AND column_name IN ('acting_agency_id', 'acting_entered_at');

    IF v_colonne <> 2 THEN
        RAISE EXCEPTION
            'P28 060: operator_sessions has % of the 2 acting columns', v_colonne;
    END IF;

    SELECT confdeltype INTO v_deltype
      FROM pg_constraint
     WHERE conrelid = 'public.operator_sessions'::regclass
       AND conname = 'operator_sessions_acting_agency_fk';

    IF v_deltype IS NULL THEN
        RAISE EXCEPTION
            'P28 060: operator_sessions_acting_agency_fk is not installed; '
            'an acting context could point at an agency that does not exist';
    END IF;
    IF v_deltype NOT IN ('a', 'r') THEN
        RAISE EXCEPTION
            'P28 060: operator_sessions_acting_agency_fk destroys rows on a '
            'parent delete (confdeltype=%); sessions are not agency debris',
            v_deltype;
    END IF;

    SELECT TRUE INTO v_check
      FROM pg_constraint
     WHERE conrelid = 'public.operator_sessions'::regclass
       AND conname = 'operator_sessions_acting_chk'
       AND contype = 'c';

    IF v_check IS NULL THEN
        RAISE EXCEPTION
            'P28 060: operator_sessions_acting_chk is missing; an acting '
            'agency without an entry instant would be storable';
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 3. Prova: il CHECK MORDE.
--
-- Un vincolo che esiste e non morde vale zero. La sonda scrive una sessione
-- vera, prova a metterla a meta' - agenzia senza istante - e verifica che il
-- database rifiuti; poi verifica che la coppia completa passi. Annulla tutto
-- con il savepoint implicito di un blocco BEGIN ... EXCEPTION, come 057, 058 e
-- 059.
--
-- Saltata quando non c'e' nessun operatore o nessuna agenzia a cui appoggiarsi:
-- un database appena costruito dal runner non ne ha, e una migration che
-- fallisse li' sarebbe una migration inapplicabile a uno schema vuoto.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_utente    BIGINT;
    v_agenzia   BIGINT;
    v_sessione  BIGINT;
    v_rifiutato BOOLEAN := FALSE;
    v_coppia_ok BOOLEAN := FALSE;
    v_sentinel  CONSTANT text := 'P28_060_PROBE_ROLLBACK';
BEGIN
    SELECT id INTO v_utente FROM operator_users ORDER BY id LIMIT 1;
    SELECT id INTO v_agenzia FROM agencies ORDER BY id LIMIT 1;

    IF v_utente IS NULL OR v_agenzia IS NULL THEN
        RETURN;
    END IF;

    BEGIN
        INSERT INTO operator_sessions (operator_user_id, token_hash, expires_at)
        VALUES (v_utente, repeat('a', 64), NOW() + interval '1 hour')
        RETURNING id INTO v_sessione;

        BEGIN
            UPDATE operator_sessions
               SET acting_agency_id = v_agenzia
             WHERE id = v_sessione;
        EXCEPTION WHEN check_violation THEN
            v_rifiutato := TRUE;
        END;

        UPDATE operator_sessions
           SET acting_agency_id = v_agenzia, acting_entered_at = NOW()
         WHERE id = v_sessione;
        v_coppia_ok := TRUE;

        RAISE EXCEPTION USING MESSAGE = v_sentinel;
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM <> v_sentinel THEN
            RAISE;
        END IF;
    END;

    IF NOT v_rifiutato THEN
        RAISE EXCEPTION
            'P28 060: an acting agency without an entry instant was accepted';
    END IF;
    IF NOT v_coppia_ok THEN
        RAISE EXCEPTION
            'P28 060: a legitimate acting context was refused';
    END IF;
END
$do$;
