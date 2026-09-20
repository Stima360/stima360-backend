-- LMC-15: il ponte fra la stima PRE-incarico e l'immobile POST-incarico, e il
-- sopralluogo che sta in mezzo.
--
-- Additive. Crea DUE tabelle, tre funzioni, due trigger e sei indici. Non
-- altera nessuna tabella preesistente, non inserisce righe, non fa backfill.
-- Nessuna guardia sul nome del database: lo stesso file vale per TEST oggi e
-- per PROD quando sara' promosso.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- ---------------------------------------------------------------------------
-- PERCHE' ESISTE, E PERCHE' NON ESISTEVA PRIMA
--
-- Fino a qui il sistema non sapeva dire due cose: se una casa PRE-incarico
-- abbia avuto un sopralluogo, e se quella casa sia diventata un incarico.
-- L'audit LMC-15A lo ha dimostrato invece di supporlo: `properties` non ha
-- `stima_id` in nessuna migration; nessuna tabella ponte esiste;
-- `seller_timeline_events` PORTA sia `stima_id` sia `property_id` ma nessuna
-- riga di codice ne scrive mai una con entrambi valorizzati; `property_leads`
-- e' compilata a mano da una sola rotta; `leads.stage='won'` non e' scritto da
-- nessuna riga di codice applicativo; e `stime_dettagliate.sopralluogo` e' un
-- campo del form pubblico - una preferenza dichiarata dal visitatore, non un
-- sopralluogo avvenuto.
--
-- Le vie indirette - stesso contatto, stesso proprietario, stesso indirizzo -
-- sono ESCLUSE per decisione: legano una PERSONA a un immobile, non QUELLA
-- stima a QUELLA property, e una metrica costruita su di esse sarebbe una
-- congettura travestita da numero.
--
-- LA CARDINALITA', E PERCHE' NON E' 1:1
--
--     una stima   -> 0..N property
--     una property -> UNA sola stima di origine ATTIVA
--
-- Una stima su una bifamiliare che diventa due unita' produce due link: 1:1 lo
-- renderebbe impossibile da registrare. Nel verso opposto il vincolo c'e' ed
-- e' stretto: se la property fosse attribuibile a due stime, lo stesso
-- incarico verrebbe contato in due coorti diverse. Da qui l'indice unico
-- PARZIALE su `property_id WHERE link_status = 'active'`: nessun vincolo su
-- `stima_id`, vincolo pieno sulla property, e i link revocati restano nel
-- registro senza bloccare un nuovo collegamento.
--
-- LINK E MANDATO SONO DUE FATTI DIVERSI
--
-- Il link dice "questa property nasce da questa stima". Il mandato dice
-- "l'incarico e' stato firmato, quel giorno". Il primo puo' esistere senza il
-- secondo - una property in valutazione - e revocare il LINK non significa che
-- l'incarico sia finito: significa che l'attribuzione era sbagliata. Percio'
-- `link_status` ha due soli valori e i campi del mandato non si azzerano mai.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS stima_acquisitions (
    id                  BIGSERIAL PRIMARY KEY,

    -- La stima PUO' essere cancellata davvero: `/api/admin/stime/delete`
    -- esiste ed e' agency-scoped. Il riferimento si azzera, lo snapshot no.
    -- SET NULL e non CASCADE perche' questo e' un REGISTRO, e nel repository
    -- i registri figli di `stime` usano SET NULL (seller_timeline_events,
    -- property_watches) mentre i dati operativi usano CASCADE.
    stima_id            INTEGER     REFERENCES stime(id) ON DELETE SET NULL,

    -- Lo scrive il TRIGGER, mai il chiamante. Senza, una riga sopravvissuta
    -- alla cancellazione della stima sarebbe un registro che non dice piu' di
    -- che cosa parla.
    stima_id_snapshot   INTEGER     NOT NULL,

    -- RESTRICT e non CASCADE: le property non vengono mai cancellate davvero
    -- (DELETE /properties/{id} chiama `archive_property`, che e' una UPDATE a
    -- `commercial_status='archived'`), quindi questo vincolo non scatta nel
    -- flusso reale; se un domani qualcuno introducesse un hard delete,
    -- fermerebbe la cancellazione invece di far sparire il registro.
    property_id         BIGINT      NOT NULL REFERENCES properties(id) ON DELETE RESTRICT,

    link_status         VARCHAR(20) NOT NULL DEFAULT 'active',
    linked_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    linked_by_operator_user_id BIGINT NOT NULL
        REFERENCES operator_users(id) ON DELETE RESTRICT,

    -- L'INCARICO. `signed` e' il fatto, `recorded` e' la sua registrazione:
    -- si firma un giorno e si registra un altro, e confondere i due
    -- timestamp significa perdere la data vera dell'incarico.
    mandate_signed_at   TIMESTAMPTZ,
    mandate_recorded_at TIMESTAMPTZ,
    mandate_recorded_by_operator_user_id BIGINT
        REFERENCES operator_users(id) ON DELETE RESTRICT,
    mandate_reference   VARCHAR(120),

    -- LA REVOCA del link. Non tocca i campi del mandato.
    revoked_at          TIMESTAMPTZ,
    revoked_by_operator_user_id BIGINT
        REFERENCES operator_users(id) ON DELETE RESTRICT,
    revoked_reason      VARCHAR(200),

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT stima_acq_status_chk
        CHECK (link_status IN ('active', 'revoked')),

    -- LA MATRICE DELLA REVOCA, per intero. Un'equivalenza sui soli due campi
    -- data+attore lascerebbe passare una riga `active` con una ragione di
    -- revoca appesa, o una `revoked` senza ragione: stati parziali che
    -- nessuno sa piu' leggere. Qui ogni stato dice cosa c'e' E cosa non c'e'.
    CONSTRAINT stima_acq_active_chk CHECK (
        link_status <> 'active' OR (
            revoked_at IS NULL
            AND revoked_by_operator_user_id IS NULL
            AND revoked_reason IS NULL)),
    CONSTRAINT stima_acq_revoked_chk CHECK (
        link_status <> 'revoked' OR (
            revoked_at IS NOT NULL
            AND revoked_by_operator_user_id IS NOT NULL
            AND revoked_reason IS NOT NULL
            AND BTRIM(revoked_reason) <> '')),

    -- I tre campi del mandato stanno insieme o non stanno: una firma senza
    -- autore, o senza la data in cui e' stata registrata, non e' un fatto
    -- verificabile.
    CONSTRAINT stima_acq_mandate_triple_chk
        CHECK (num_nonnulls(mandate_signed_at, mandate_recorded_at,
                            mandate_recorded_by_operator_user_id) IN (0, 3)),
    CONSTRAINT stima_acq_mandate_order_chk
        CHECK (mandate_recorded_at IS NULL OR mandate_recorded_at >= mandate_signed_at),
    CONSTRAINT stima_acq_mandate_ref_chk
        CHECK (mandate_reference IS NULL
               OR (mandate_signed_at IS NOT NULL AND BTRIM(mandate_reference) <> '')),

    -- Finche' la stima c'e', riferimento e snapshot coincidono. E' la rete
    -- sotto al trigger: vale anche per una UPDATE scritta a mano in psql.
    CONSTRAINT stima_acq_snapshot_chk
        CHECK (stima_id IS NULL OR stima_id = stima_id_snapshot)
);

CREATE TABLE IF NOT EXISTS stima_inspections (
    id           BIGSERIAL PRIMARY KEY,
    stima_id     INTEGER REFERENCES stime(id) ON DELETE SET NULL,
    stima_id_snapshot INTEGER NOT NULL,

    status       VARCHAR(20) NOT NULL DEFAULT 'scheduled',
    scheduled_for TIMESTAMPTZ,

    -- `completed_at` e' quando il sopralluogo E' AVVENUTO; `recorded` quando
    -- l'operatore lo ha scritto. Il primo e' cio' che LMC-13 conta.
    completed_at          TIMESTAMPTZ,
    completed_recorded_at TIMESTAMPTZ,
    completed_by_operator_user_id BIGINT
        REFERENCES operator_users(id) ON DELETE RESTRICT,

    cancelled_at          TIMESTAMPTZ,
    cancelled_recorded_at TIMESTAMPTZ,
    cancelled_by_operator_user_id BIGINT
        REFERENCES operator_users(id) ON DELETE RESTRICT,
    cancelled_reason      VARCHAR(200),

    created_by_operator_user_id BIGINT NOT NULL
        REFERENCES operator_users(id) ON DELETE RESTRICT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT stima_insp_status_chk
        CHECK (status IN ('scheduled', 'completed', 'cancelled')),

    -- LA MATRICE, STATO PER STATO: cosa c'e' e cosa NON c'e'. Nessuno stato
    -- ambiguo, e nessun campo di testo libero da cui dedurre lo stato.
    CONSTRAINT stima_insp_scheduled_chk CHECK (
        status <> 'scheduled' OR (
            scheduled_for IS NOT NULL
            AND num_nonnulls(completed_at, completed_recorded_at,
                             completed_by_operator_user_id) = 0
            AND num_nonnulls(cancelled_at, cancelled_recorded_at,
                             cancelled_by_operator_user_id, cancelled_reason) = 0)),

    -- `scheduled_for` resta facoltativa SOLO qui: un sopralluogo registrato a
    -- posteriori, avvenuto e mai fissato a sistema, e' un caso reale, e
    -- pretendere una data di appuntamento mai esistita costringerebbe a
    -- inventarla.
    CONSTRAINT stima_insp_completed_chk CHECK (
        status <> 'completed' OR (
            completed_at IS NOT NULL
            AND completed_recorded_at IS NOT NULL
            AND completed_by_operator_user_id IS NOT NULL
            AND num_nonnulls(cancelled_at, cancelled_recorded_at,
                             cancelled_by_operator_user_id, cancelled_reason) = 0)),

    -- Si puo' annullare solo cio' che era stato programmato: qui
    -- `scheduled_for` torna obbligatoria.
    CONSTRAINT stima_insp_cancelled_chk CHECK (
        status <> 'cancelled' OR (
            scheduled_for IS NOT NULL
            AND cancelled_at IS NOT NULL
            AND cancelled_recorded_at IS NOT NULL
            AND cancelled_by_operator_user_id IS NOT NULL
            AND num_nonnulls(completed_at, completed_recorded_at,
                             completed_by_operator_user_id) = 0)),

    CONSTRAINT stima_insp_completed_order_chk
        CHECK (completed_recorded_at IS NULL OR completed_recorded_at >= completed_at),
    CONSTRAINT stima_insp_cancelled_order_chk
        CHECK (cancelled_recorded_at IS NULL OR cancelled_recorded_at >= cancelled_at),
    CONSTRAINT stima_insp_cancelled_reason_chk
        CHECK (cancelled_reason IS NULL OR BTRIM(cancelled_reason) <> ''),
    CONSTRAINT stima_insp_snapshot_chk
        CHECK (stima_id IS NULL OR stima_id = stima_id_snapshot)
);

-- ---------------------------------------------------------------------------
-- L'ATTORE, E IL PLATFORM ADMIN.
--
-- `operator_users` e' GLOBALE: non ha `agency_id`, quindi la sola FK non
-- impedisce di registrare come attore un operatore di un'altra agenzia.
-- L'unica tabella che lega operatore e agenzia e' `agency_memberships`.
--
-- Il platform admin e' l'eccezione, e non e' una concessione: la regola del
-- repository (operator_auth/service.py `_effective_agency`) dice che un
-- platform admin IN ACTING lavora dentro l'agenzia visitata SENZA averne una
-- membership, e la sua autorita' viene da `is_platform_admin`, che la matrice
-- dei permessi legge per prima. Un trigger che pretendesse la membership da
-- tutti renderebbe impossibile un'operazione legittima.
--
-- Cio' che il database NON prova a fare e' ricostruire l'acting: vive in
-- `operator_sessions`, una riga di questo registro non lo conosce, e
-- duplicare qui una regola di sessione produrrebbe due risposte alla stessa
-- domanda. Che l'admin fosse davvero in acting su QUELLA agenzia lo garantisce
-- l'applicazione, ed e' tracciato in `platform_audit_log`.
--
-- `operator_users.status` NON e' un vincolo: un operatore disabilitato oggi
-- puo' aver firmato legittimamente un incarico l'anno scorso, e rifiutare la
-- registrazione retroattiva falsificherebbe la storia.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION lmc15_assert_operator_may_act(
    p_actor_id BIGINT, p_agency_id BIGINT, p_campo TEXT
) RETURNS void AS $fn$
DECLARE
    v_platform BOOLEAN;
BEGIN
    IF p_actor_id IS NULL THEN
        RETURN;
    END IF;

    SELECT is_platform_admin INTO v_platform
      FROM operator_users WHERE id = p_actor_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'LMC-15 actor: operator % (%) does not exist', p_actor_id, p_campo;
    END IF;
    IF v_platform THEN
        RETURN;
    END IF;

    IF p_agency_id IS NULL THEN
        RAISE EXCEPTION
            'LMC-15 actor: operator % (%) cannot be verified without an agency', p_actor_id, p_campo;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM agency_memberships
         WHERE operator_user_id = p_actor_id
           AND agency_id = p_agency_id
           AND status = 'active'
    ) THEN
        RAISE EXCEPTION
            'LMC-15 actor: operator % (%) has no active membership in agency %',
            p_actor_id, p_campo, p_agency_id;
    END IF;
END;
$fn$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION stima_acquisitions_guard() RETURNS trigger AS $fn$
DECLARE
    a_stima BIGINT;
    a_prop  BIGINT;
    a_eff   BIGINT;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.stima_id IS NULL THEN
            RAISE EXCEPTION 'LMC-15: stima_id is required when creating an acquisition link';
        END IF;
        -- ASSEGNATO, non validato: qualunque valore arrivato dal chiamante
        -- viene sovrascritto senza nemmeno essere letto.
        NEW.stima_id_snapshot := NEW.stima_id;
    ELSE
        IF NEW.stima_id_snapshot IS DISTINCT FROM OLD.stima_id_snapshot THEN
            RAISE EXCEPTION 'LMC-15: stima_id_snapshot is immutable';
        END IF;
        -- L'unica riassegnazione ammessa e' verso NULL, che e' cio' che fa
        -- ON DELETE SET NULL. Cambiare stima si fa revocando e ricreando.
        IF NEW.stima_id IS NOT NULL
           AND OLD.stima_id IS NOT NULL
           AND NEW.stima_id IS DISTINCT FROM OLD.stima_id THEN
            RAISE EXCEPTION 'LMC-15: stima_id cannot be reassigned; revoke and create a new link';
        END IF;
    END IF;

    SELECT agency_id INTO a_prop FROM properties WHERE id = NEW.property_id;
    IF a_prop IS NULL THEN
        RAISE EXCEPTION 'LMC-15 tenancy: property % has no agency', NEW.property_id;
    END IF;

    IF NEW.stima_id IS NOT NULL THEN
        SELECT agency_id INTO a_stima FROM stime WHERE id = NEW.stima_id;
        IF a_stima IS NULL THEN
            RAISE EXCEPTION 'LMC-15 tenancy: estimation % does not exist or has no agency', NEW.stima_id;
        END IF;
        IF a_stima <> a_prop THEN
            RAISE EXCEPTION
                'LMC-15 tenancy: estimation % (agency %) and property % (agency %) differ',
                NEW.stima_id, a_stima, NEW.property_id, a_prop;
        END IF;
    END IF;

    -- Con la stima cancellata l'agenzia effettiva resta quella della property:
    -- senza questo ramo la REVOCA di un link orfano sarebbe impossibile,
    -- proprio nel momento in cui serve.
    a_eff := a_prop;

    -- Qui i tre attori si rivalidano a OGNI scrittura, non solo quando
    -- cambiano, e si puo' perche' `a_eff` non e' mai NULL: la property c'e'
    -- sempre (FK NOT NULL con ON DELETE RESTRICT) anche quando la stima non
    -- c'e' piu'. `stima_inspections`, che quella seconda radice non ce l'ha,
    -- verifica invece campo per campo al momento della scrittura: la
    -- differenza fra le due guardie nasce da li' e da nient'altro.
    PERFORM lmc15_assert_operator_may_act(NEW.linked_by_operator_user_id, a_eff, 'linked_by');
    PERFORM lmc15_assert_operator_may_act(NEW.mandate_recorded_by_operator_user_id, a_eff, 'mandate_recorded_by');
    PERFORM lmc15_assert_operator_may_act(NEW.revoked_by_operator_user_id, a_eff, 'revoked_by');

    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION stima_inspections_guard() RETURNS trigger AS $fn$
DECLARE
    a_eff BIGINT;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.stima_id IS NULL THEN
            RAISE EXCEPTION 'LMC-15: stima_id is required when creating an inspection';
        END IF;
        NEW.stima_id_snapshot := NEW.stima_id;
    ELSE
        IF NEW.stima_id_snapshot IS DISTINCT FROM OLD.stima_id_snapshot THEN
            RAISE EXCEPTION 'LMC-15: stima_id_snapshot is immutable';
        END IF;
        IF NEW.stima_id IS NOT NULL
           AND OLD.stima_id IS NOT NULL
           AND NEW.stima_id IS DISTINCT FROM OLD.stima_id THEN
            RAISE EXCEPTION 'LMC-15: stima_id cannot be reassigned on an inspection';
        END IF;
    END IF;

    IF NEW.stima_id IS NOT NULL THEN
        SELECT agency_id INTO a_eff FROM stime WHERE id = NEW.stima_id;
        IF a_eff IS NULL THEN
            RAISE EXCEPTION 'LMC-15 tenancy: estimation % does not exist or has no agency', NEW.stima_id;
        END IF;
    END IF;

    -- OGNI campo operator viene verificato QUANDO VIENE SCRITTO: all'INSERT
    -- tutti e tre, all'UPDATE quelli che cambiano. Non e' uno sconto sul
    -- controllo - nessun attore entra in questa tabella senza essere stato
    -- verificato contro l'agenzia della stima, e i CHECK di stato rendono
    -- obbligatorio l'attore in ogni transizione - ma e' la sola forma che
    -- sopravvive alla cancellazione della stima.
    --
    -- Un sopralluogo, a differenza di un link di acquisizione, non ha una
    -- SECONDA radice: `stima_acquisitions` ricade sulla property e continua
    -- ad avere un'agenzia anche da orfano, qui no. Quando `DELETE FROM stime`
    -- porta `stima_id` a NULL via ON DELETE SET NULL, `a_eff` e' NULL e
    -- rivalidare attori che nessuno ha toccato farebbe fallire la
    -- cancellazione stessa della stima: un DELETE che esiste da prima di
    -- LMC-15, gia' certificato, e che il ponte non deve poter impedire.
    -- Verificato empiricamente: senza questa distinzione
    -- `DELETE FROM stime` con un sopralluogo appeso si interrompe con
    -- "cannot be verified without an agency".
    IF TG_OP = 'INSERT'
       OR NEW.created_by_operator_user_id
          IS DISTINCT FROM OLD.created_by_operator_user_id THEN
        PERFORM lmc15_assert_operator_may_act(
            NEW.created_by_operator_user_id, a_eff, 'created_by');
    END IF;
    IF TG_OP = 'INSERT'
       OR NEW.completed_by_operator_user_id
          IS DISTINCT FROM OLD.completed_by_operator_user_id THEN
        PERFORM lmc15_assert_operator_may_act(
            NEW.completed_by_operator_user_id, a_eff, 'completed_by');
    END IF;
    IF TG_OP = 'INSERT'
       OR NEW.cancelled_by_operator_user_id
          IS DISTINCT FROM OLD.cancelled_by_operator_user_id THEN
        PERFORM lmc15_assert_operator_may_act(
            NEW.cancelled_by_operator_user_id, a_eff, 'cancelled_by');
    END IF;

    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_stima_acquisitions_guard'
          AND c.relname = 'stima_acquisitions' AND n.nspname = 'public'
          AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_stima_acquisitions_guard
            BEFORE INSERT OR UPDATE OF stima_id, stima_id_snapshot, property_id,
                   linked_by_operator_user_id, mandate_recorded_by_operator_user_id,
                   revoked_by_operator_user_id
            ON stima_acquisitions
            FOR EACH ROW EXECUTE FUNCTION stima_acquisitions_guard();
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_stima_inspections_guard'
          AND c.relname = 'stima_inspections' AND n.nspname = 'public'
          AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_stima_inspections_guard
            BEFORE INSERT OR UPDATE OF stima_id, stima_id_snapshot,
                   created_by_operator_user_id, completed_by_operator_user_id,
                   cancelled_by_operator_user_id
            ON stima_inspections
            FOR EACH ROW EXECUTE FUNCTION stima_inspections_guard();
    END IF;
END
$do$;

-- IL vincolo di cardinalita': una sola origine attiva per property. Parziale,
-- cosi' i link revocati restano nel registro e la property puo' essere
-- ricollegata a una stima diversa dopo una revoca esplicita.
CREATE UNIQUE INDEX IF NOT EXISTS idx_stima_acq_active_property
    ON stima_acquisitions (property_id) WHERE link_status = 'active';

CREATE INDEX IF NOT EXISTS idx_stima_acq_stima
    ON stima_acquisitions (stima_id, link_status);
CREATE INDEX IF NOT EXISTS idx_stima_acq_mandate
    ON stima_acquisitions (mandate_signed_at)
    WHERE link_status = 'active' AND mandate_signed_at IS NOT NULL;
-- L'unico modo di ritrovare i registri la cui stima e' stata cancellata.
CREATE INDEX IF NOT EXISTS idx_stima_acq_snapshot
    ON stima_acquisitions (stima_id_snapshot);

CREATE INDEX IF NOT EXISTS idx_stima_insp_stima_completed
    ON stima_inspections (stima_id, completed_at) WHERE status = 'completed';
CREATE INDEX IF NOT EXISTS idx_stima_insp_snapshot
    ON stima_inspections (stima_id_snapshot);

-- ---------------------------------------------------------------------------
-- Verifica: le tabelle sono quelle che questo file dice, non omonime
-- preesistenti con un'altra forma (IF NOT EXISTS lascerebbe passare le
-- seconde in silenzio).
-- ---------------------------------------------------------------------------

DO $do$
DECLARE
    v_count integer;
BEGIN
    SELECT count(*) INTO v_count
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'stima_acquisitions';
    IF v_count <> 16 THEN
        RAISE EXCEPTION 'LMC-15 070: stima_acquisitions has % columns, expected 16', v_count;
    END IF;

    SELECT count(*) INTO v_count
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'stima_inspections';
    IF v_count <> 15 THEN
        RAISE EXCEPTION 'LMC-15 070: stima_inspections has % columns, expected 15', v_count;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name IN ('stima_acquisitions', 'stima_inspections')
           AND column_name = 'agency_id'
    ) THEN
        RAISE EXCEPTION
            'LMC-15 070: the bridge must not carry agency_id; tenancy is derived from stime and properties';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public' AND indexname = 'idx_stima_acq_active_property'
    ) THEN
        RAISE EXCEPTION 'LMC-15 070: the active-property unique index is missing';
    END IF;

    SELECT count(*) INTO v_count
      FROM pg_constraint
     WHERE conrelid = 'public.stima_acquisitions'::regclass AND contype = 'f';
    IF v_count <> 5 THEN
        RAISE EXCEPTION 'LMC-15 070: stima_acquisitions must carry 5 foreign keys, found %', v_count;
    END IF;

    SELECT count(*) INTO v_count
      FROM pg_constraint
     WHERE conrelid = 'public.stima_inspections'::regclass AND contype = 'f';
    IF v_count <> 4 THEN
        RAISE EXCEPTION 'LMC-15 070: stima_inspections must carry 4 foreign keys, found %', v_count;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_stima_acquisitions_guard')
       OR NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_stima_inspections_guard') THEN
        RAISE EXCEPTION 'LMC-15 070: one of the two guard triggers is missing';
    END IF;
END
$do$;
