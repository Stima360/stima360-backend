-- P27-5 network territories.
--
-- Additive, idempotent. Creates TWO tables and alters none that existed
-- before. No backfill and no seed row: the network has no territory until
-- someone declares one, and inventing three from `normalizza_comune` would
-- turn a hardcoded list in main.py into a fact about the business.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- WHY THERE ARE TWO TABLES AND NOT ONE
--
-- A single table with `agency_id` on it would make the territory and its
-- ownership the same object. Two consequences, both wrong:
--
--   1. revoking an assignment would have to either NULL the agency - losing
--      which agency it was - or delete the row, losing the territory itself;
--   2. the same place would be a different row for each agency that ever held
--      it, so "Alba Adriatica" would exist three times and nothing would say
--      the three are one place.
--
-- Identity and ownership are therefore separate: `network_territories` says
-- WHAT a territory is, `agency_territory_assignments` says WHO holds it and
-- since when, one row per period of holding.
--
-- WHAT THE AUDIT OF THE REPOSITORY FOUND, AND WHY THE KEY IS NEW
--
-- The repository has no canonical geography. What it has:
--
--   * `properties.city` VARCHAR(120), `properties.province` VARCHAR(10),
--     `properties.postal_code` VARCHAR(20), `properties.microzone` - free text
--     typed by an operator, no normalisation, no lookup table;
--   * `stime.comune` - written by the public funnel through
--     `main.normalizza_comune`, which is a HARDCODED ALLOWLIST OF THREE names
--     ('Alba Adriatica', 'Martinsicuro', 'Tortoreto') returning a Title Case
--     DISPLAY string;
--   * `zone_valori(comune, microzona)` - a price table keyed on those same
--     free strings. It prices a place; it does not define one;
--   * `buy_location_criteria` (migration 004) - region/province/municipality/
--     microzone/radius. These are a BUYER'S SEARCH CRITERIA, matched by
--     `match/engine._norm` (strip + lower) against property text. Reusing them
--     as territorial keys would make a buyer's wish list the authority on who
--     owns a town.
--
-- There is no ISTAT code anywhere in the repository, and no comune -> provincia
-- mapping of any kind. So P27-5 introduces its own canonical key, which is the
-- third option in the order of preference and the only one available.
--
-- IDENTITY IS `canonical_key`, AND IT IS NOT DERIVED FROM `label`
--
-- `canonical_key` is supplied explicitly and constrained to a canonical shape
-- (lowercase, digits, single hyphens). `label` is free display text and can be
-- corrected at any time without the territory becoming a different territory.
--
-- Deriving the key from the label would have been less typing and would have
-- made identity depend on a display string: correcting "Alba adriatica" to
-- "Alba Adriatica" would have silently created a second territory, and the
-- assignment on the first would have stayed there, invisible. The uniqueness
-- constraint below is on (kind, canonical_key) and mentions `label` nowhere,
-- which is what makes that impossible rather than merely discouraged.
--
-- THE THREE KINDS, AND THE TWO THAT ARE ABSENT
--
-- `province`, `municipality`, `postal_code`: each has a real counterpart in
-- the data the product already holds - `properties.province`,
-- `stime.comune`/`properties.city`, `properties.postal_code`.
--
-- `region` is absent: it exists only inside `buy_location_criteria`, which is
-- search criteria, and nothing in the product records a region for a property
-- or an estimate.
--
-- `microzone` is absent: `zone_valori` keys prices on (comune, microzona) free
-- strings with no authority behind them, and `properties.microzone` is typed
-- by hand. A territorial level needs a list someone can agree on; a price
-- lookup is not one.
--
-- NO HIERARCHY, AND THE MISSING CHECK IS DECLARED RATHER THAN FAKED
--
-- The repository contains no mapping from a comune to its provincia. So this
-- schema cannot know that the municipality 'tortoreto' sits inside the
-- province 'te', and it does not pretend to: the exclusivity guaranteed below
-- is per (kind, canonical_key) and nothing more.
--
-- The consequence is real and belongs in the risk register, not in a string
-- comparison invented here: a province assigned to agency A and a municipality
-- inside it assigned to agency B can both be active, and this schema will not
-- object. P27-6 decides which one wins when a lead arrives; guessing the
-- containment from text would produce a rule that is wrong for every comune
-- whose name is spelled differently from its province's.

-- ---------------------------------------------------------------------------
-- 1. The territory: what a place IS.
--
-- No `agency_id` on this table, deliberately. A territory outlives every
-- assignment it ever had, and a column here would make "held by nobody"
-- indistinguishable from "never declared".
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS network_territories (
    id            BIGSERIAL    PRIMARY KEY,
    kind          VARCHAR(30)  NOT NULL,
    canonical_key VARCHAR(120) NOT NULL,
    label         VARCHAR(200) NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- The three levels, kept identical to platform_admin/enums.TERRITORY_KINDS.
    -- tests/test_p27_5_territories.py compares the two lists, so a fourth kind
    -- added on one side fails there rather than being rejected by the database
    -- at the first write.
    CONSTRAINT network_territories_kind_chk
        CHECK (kind IN ('province', 'municipality', 'postal_code')),

    -- The canonical shape, enforced in the database and not only in Pydantic.
    -- A key that differs from another only by case or by a space is not a
    -- second territory, it is the same one written badly - and a UNIQUE index
    -- would not catch it, because 'Alba Adriatica' and 'alba-adriatica' are
    -- genuinely different strings. The shape is what makes the uniqueness
    -- below mean what it says.
    CONSTRAINT network_territories_canonical_key_chk
        CHECK (canonical_key ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),

    CONSTRAINT network_territories_label_chk CHECK (BTRIM(label) <> ''),

    -- IDENTITY. The pair, and not the key alone: the CAP '64011' and a
    -- municipality that one day carries the same string are different things,
    -- and the kind is what separates them.
    --
    -- `label` is absent from this constraint on purpose. It is the whole
    -- reason a label can be corrected without splitting a territory in two.
    CONSTRAINT network_territories_identity_unq UNIQUE (kind, canonical_key)
);

-- The read path that is not the primary key: "every municipality", "every CAP".
CREATE INDEX IF NOT EXISTS idx_network_territories_kind
    ON network_territories (kind, canonical_key);

-- ---------------------------------------------------------------------------
-- 2. The assignment: WHO holds a territory, and in what state.
--
-- One row per period of holding. A territory that goes A -> B has two rows,
-- not one row whose agency_id changed: the second reading is unanswerable
-- afterwards, because nothing records that A ever held it.
--
-- THERE IS NO `ended_at`, AND THAT IS A DECISION.
--
-- A row leaves 'active' by being updated, and `updated_at` already carries the
-- instant. A second column holding the same instant would be a second source
-- of truth for one fact, and the first time the two disagreed - a suspend
-- followed by a reactivation, say - nobody would know which to believe. When
-- an assignment's full timeline is needed it is in platform_audit_log, which
-- is append-only and cannot be rewritten by anything here.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agency_territory_assignments (
    id           BIGSERIAL   PRIMARY KEY,

    -- ON DELETE RESTRICT ON BOTH, EXPLICITLY.
    --
    -- Not CASCADE: deleting an agency would silently take the record of
    -- everything it ever covered with it, which is the one thing this table
    -- exists to keep. Not SET NULL: an assignment whose agency is NULL says
    -- that somebody held this territory and declines to say who.
    --
    -- RESTRICT rather than the NO ACTION that omitting the clause would give,
    -- for two reasons that point the same way. It is what every foreign key
    -- toward `agencies` in P26 already declares - 027, 028, 031, 034, 037, 043
    -- without exception - so a reader comparing two children of the same
    -- parent finds the same rule written the same way. And an omitted clause
    -- reads as a decision nobody took, while this one was taken.
    --
    -- This is NOT the trap that RESTRICT would have been on
    -- platform_audit_log (see 057). There, the append-only guard forbade
    -- deleting the audit rows, so a RESTRICT would have made the referenced
    -- entity undeletable forever with no way out. Here the way out is
    -- ordinary: revoked assignments are deletable rows in an ordinary table,
    -- and a territory that has never been assigned deletes with no objection
    -- at all. What RESTRICT forbids is losing history as a side effect of
    -- deleting something else.
    --
    -- CONSEQUENCE, DECLARED RATHER THAN DISCOVERED: an agency that has ever
    -- held a territory can no longer be deleted while those rows exist. The
    -- P26-6 cleanup deletes agencies, so this key is recorded in its reviewed
    -- inventory (FK_NON_CASCADE_ATTESE in
    -- tests/test_p26_6_live_cert_script.py), next to
    -- agency_memberships.agency_id, which has exactly this shape and this
    -- effect since 027.
    territory_id BIGINT      NOT NULL
                 REFERENCES network_territories(id) ON DELETE RESTRICT,
    agency_id    BIGINT      NOT NULL
                 REFERENCES agencies(id)            ON DELETE RESTRICT,

    status       VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Identical to platform_admin/enums.ASSIGNMENT_STATUSES, compared by the
    -- test file for the same reason as the kinds above.
    --
    --   active    the agency covers this territory now
    --   suspended kept, not operative - the affiliate is paused, not replaced
    --   revoked   ended. Historical, and never deleted.
    CONSTRAINT agency_territory_assignments_status_chk
        CHECK (status IN ('active', 'suspended', 'revoked'))
);

-- ---------------------------------------------------------------------------
-- 3. THE PRODUCT INVARIANT, IN THE DATABASE.
--
--     a canonical territory has AT MOST ONE active assignment.
--
-- A partial unique index, the same instrument P26 used for
-- uq_agency_memberships_single_active. It is what makes two simultaneous
-- requests assigning the same territory to two agencies resolve to one winner
-- and one 409, rather than to whichever wrote last.
--
-- Partial, on status='active' only: the history has as many suspended and
-- revoked rows for a territory as it needs, and a full unique index on
-- territory_id would forbid exactly that history.
--
-- NOT (territory_id, agency_id): an agency that held a territory, lost it and
-- gets it back must produce a SECOND row. With a unique pair the only way back
-- would be to rewrite the revoked row, which erases the period in between -
-- precisely the ambiguous reuse of a historical row that this design refuses.
-- ---------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS uq_agency_territory_single_active
    ON agency_territory_assignments (territory_id)
 WHERE status = 'active';

-- The two read paths: "what does this agency cover", "who holds this one".
CREATE INDEX IF NOT EXISTS idx_agency_territory_agency
    ON agency_territory_assignments (agency_id, status);

CREATE INDEX IF NOT EXISTS idx_agency_territory_territory
    ON agency_territory_assignments (territory_id, status);

-- ---------------------------------------------------------------------------
-- 4. Proof, read back from the catalogue.
--
-- The discipline 055 and 057 established: the migration does not assume its
-- own statements took effect, it asks the catalogue. Checked here are the two
-- things whose absence would not be noticed until it mattered - the partial
-- unique index (a plain index would let the second assignment through) and the
-- two foreign keys not being CASCADE.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_indisunique BOOLEAN;
    v_indpred     TEXT;
    v_bad         TEXT;
BEGIN
    IF to_regclass('public.network_territories') IS NULL THEN
        RAISE EXCEPTION 'P27-5 058: network_territories was not created';
    END IF;
    IF to_regclass('public.agency_territory_assignments') IS NULL THEN
        RAISE EXCEPTION 'P27-5 058: agency_territory_assignments was not created';
    END IF;

    SELECT i.indisunique, pg_get_expr(i.indpred, i.indrelid)
      INTO v_indisunique, v_indpred
      FROM pg_index i
      JOIN pg_class c ON c.oid = i.indexrelid
     WHERE c.relname = 'uq_agency_territory_single_active';

    IF v_indisunique IS NULL THEN
        RAISE EXCEPTION
            'P27-5 058: uq_agency_territory_single_active is not installed';
    END IF;
    IF NOT v_indisunique THEN
        RAISE EXCEPTION
            'P27-5 058: uq_agency_territory_single_active exists but is not UNIQUE; '
            'two agencies could hold the same territory';
    END IF;
    IF v_indpred IS NULL THEN
        RAISE EXCEPTION
            'P27-5 058: uq_agency_territory_single_active has no predicate; '
            'it would forbid the revoked history rather than the second active row';
    END IF;

    -- confdeltype: 'a' = NO ACTION, 'r' = RESTRICT, 'c' = CASCADE,
    -- 'n' = SET NULL, 'd' = SET DEFAULT. Anything that deletes or blanks a row
    -- on a parent delete is refused here.
    SELECT string_agg(conname, ', ')
      INTO v_bad
      FROM pg_constraint
     WHERE conrelid = 'public.agency_territory_assignments'::regclass
       AND contype = 'f'
       AND confdeltype NOT IN ('a', 'r');

    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION
            'P27-5 058: these foreign keys destroy history on a parent delete: %',
            v_bad;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 5. Proof: the invariant actually refuses the second active row.
--
-- An index that exists and does not bite is worth nothing, and the first
-- affiliate to be given someone else's town would be what discovered it. The
-- probe writes a real territory and two real assignments against a real
-- agency, checks that the second is refused, and undoes everything through the
-- implicit savepoint of a BEGIN ... EXCEPTION block - the same instrument 057
-- used, and for the same reason: PL/pgSQL cannot issue transaction control.
--
-- The probe is SKIPPED when there is no agency to point at. An empty
-- `agencies` means a fresh database being built up by the runner, and a
-- migration that failed there would be a migration that cannot be applied to
-- an empty schema.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_agency    BIGINT;
    v_territory BIGINT;
    v_refused   BOOLEAN := FALSE;
    v_first_ok  BOOLEAN := FALSE;
    v_sentinel  CONSTANT text := 'P27_5_058_PROBE_ROLLBACK';
BEGIN
    SELECT id INTO v_agency FROM agencies ORDER BY id LIMIT 1;
    IF v_agency IS NULL THEN
        RETURN;
    END IF;

    BEGIN
        INSERT INTO network_territories (kind, canonical_key, label)
        VALUES ('municipality', 'p27-5-058-probe', 'P27-5 probe')
        RETURNING id INTO v_territory;

        INSERT INTO agency_territory_assignments (territory_id, agency_id, status)
        VALUES (v_territory, v_agency, 'active');
        v_first_ok := TRUE;

        BEGIN
            INSERT INTO agency_territory_assignments (territory_id, agency_id, status)
            VALUES (v_territory, v_agency, 'active');
        EXCEPTION WHEN unique_violation THEN
            v_refused := TRUE;
        END;

        RAISE EXCEPTION USING MESSAGE = v_sentinel;
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM <> v_sentinel THEN
            RAISE;
        END IF;
    END;

    IF NOT v_first_ok THEN
        RAISE EXCEPTION
            'P27-5 058: a legitimate first assignment was refused';
    END IF;
    IF NOT v_refused THEN
        RAISE EXCEPTION
            'P27-5 058: a second ACTIVE assignment for the same territory was '
            'accepted; the protected-territory invariant is not effective';
    END IF;
END
$do$;
