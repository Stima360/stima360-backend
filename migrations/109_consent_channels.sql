-- 109 - CONSENSI MARKETING PER CANALE (C1): email, sms, whatsapp, phone.
--
-- Additiva. Nessuna riga storica viene riscritta, nessun consenso viene
-- dedotto, nessuna autorizzazione viene presunta: un consenso raccolto prima
-- di questa migration resta cio' che e' - un evento senza canale - e non
-- autorizza NESSUN canale finche' una persona non ne documenta uno.
--
--   1. `consent_events.channel`: il canale di una decisione marketing. NULL
--      sulle righe storiche ("pre-canale"). Da adesso un evento marketing
--      SENZA canale e' rifiutato dal database, con UNA eccezione dichiarata:
--      la concessione dal funnel pubblico (`public_stima`, `granted`), il cui
--      testo vive sul sito e fuori da questo repository. Viene conservata
--      come prova che la persona ha spuntato la casella, e NON autorizza
--      nessun canale: e' il caso "ambiguo = non autorizzato" del committente.
--   2. `contact_channel_consents`: la proiezione PER CANALE, una riga per
--      (contatto, canale), scritta nella stessa transazione dell'evento che
--      la determina. E' l'UNICA cosa che la guardia legge per decidere.
--      `contacts.marketing_consent` resta per i lettori di sempre (CRM, P24)
--      come "almeno un canale concesso, oppure una spunta pre-canale": NON
--      e' piu' un'autorizzazione e nessun invio puo' basarsi su di essa.
--   3. `consent_notices.channels`: i canali che il TESTO di una informativa
--      copre. Le versioni esistenti coprono '{}' (nessuno): un consenso su un
--      canale puo' citare solo una informativa che lo copre.
--   4. `communication_optouts`: la lista di piattaforma per (canale,
--      indirizzo): email normalizzata per `email`, numero normalizzato per
--      `sms`, `whatsapp`, `phone`. Le righe di `communication_email_optouts`
--      (108) vengono COPIATE qui sul canale `email`, nessuna persa; la
--      tabella 108 resta leggibile ma non accetta piu' INSERT, cosi' nessun
--      opt-out puo' finire nel posto sbagliato.
--
-- Nessun BEGIN/COMMIT: la transazione e' del runner (scripts/p26_migrate.py).

-- ---------------------------------------------------------------------------
-- 1. consent_events.channel
-- ---------------------------------------------------------------------------
ALTER TABLE consent_events ADD COLUMN channel VARCHAR(20);

ALTER TABLE consent_events ADD CONSTRAINT consent_events_channel_chk
    CHECK (channel IS NULL OR channel IN ('email', 'sms', 'whatsapp', 'phone'));

-- Il canale e' una dimensione del marketing: `privacy_terms` non ne ha.
ALTER TABLE consent_events ADD CONSTRAINT consent_events_channel_purpose_chk
    CHECK (channel IS NULL OR purpose = 'marketing');

CREATE INDEX idx_consent_events_channel_current
    ON consent_events (contact_id, purpose, channel, decided_at DESC, id DESC)
    WHERE channel IS NOT NULL;

CREATE FUNCTION consent_events_channel_required() RETURNS trigger AS $fn$
BEGIN
    IF NEW.purpose = 'marketing' AND NEW.channel IS NULL
       AND NOT (NEW.source = 'public_stima' AND NEW.decision = 'granted') THEN
        RAISE EXCEPTION
            '109: a marketing consent event must name its channel (email, sms, whatsapp, phone); '
            'only a public_stima grant may be recorded without one, and it authorises no channel';
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

CREATE TRIGGER trg_consent_events_channel_required
    BEFORE INSERT ON consent_events
    FOR EACH ROW EXECUTE FUNCTION consent_events_channel_required();

ALTER TABLE consent_events ENABLE ALWAYS TRIGGER trg_consent_events_channel_required;

-- ---------------------------------------------------------------------------
-- 2. contact_channel_consents - la proiezione per canale
-- ---------------------------------------------------------------------------
CREATE TABLE contact_channel_consents (
    id          BIGSERIAL    PRIMARY KEY,
    agency_id   BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    contact_id  BIGINT       NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    channel     VARCHAR(20)  NOT NULL,
    status      VARCHAR(20)  NOT NULL,
    granted_at  TIMESTAMPTZ,
    revoked_at  TIMESTAMPTZ,
    source      VARCHAR(40)  NOT NULL,
    notice_id   BIGINT       REFERENCES consent_notices(id) ON DELETE RESTRICT,
    event_id    BIGINT       NOT NULL REFERENCES consent_events(id) ON DELETE CASCADE,
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT ccc_channel_chk CHECK (channel IN ('email', 'sms', 'whatsapp', 'phone')),
    CONSTRAINT ccc_status_chk  CHECK (status IN ('granted', 'revoked')),
    -- concesso: c'e' la data della concessione e nessuna revoca corrente;
    -- revocato: c'e' la data della revoca (la concessione revocata resta).
    CONSTRAINT ccc_matrix_chk CHECK (
        (status = 'granted' AND granted_at IS NOT NULL AND revoked_at IS NULL)
     OR (status = 'revoked' AND revoked_at IS NOT NULL)),
    CONSTRAINT ccc_one_per_channel UNIQUE (contact_id, channel)
);

CREATE INDEX idx_ccc_agency_channel ON contact_channel_consents (agency_id, channel, status);

-- ---------------------------------------------------------------------------
-- 3. consent_notices.channels - i canali che il testo copre
-- ---------------------------------------------------------------------------
ALTER TABLE consent_notices ADD COLUMN channels TEXT[] NOT NULL DEFAULT '{}';

ALTER TABLE consent_notices ADD CONSTRAINT consent_notices_channels_chk
    CHECK (channels <@ ARRAY['email', 'sms', 'whatsapp', 'phone']::text[]);

ALTER TABLE consent_notices ADD CONSTRAINT consent_notices_channels_purpose_chk
    CHECK (purpose = 'marketing' OR channels = '{}');

-- Il trigger di 061 ammette UN solo UPDATE (il ritiro). `channels` nasce con
-- la riga e non cambia dopo: un testo pubblicato copre cio' che diceva.

-- ---------------------------------------------------------------------------
-- 4. communication_optouts - la lista di piattaforma per (canale, indirizzo)
-- ---------------------------------------------------------------------------
CREATE TABLE communication_optouts (
    id                 BIGSERIAL    PRIMARY KEY,
    channel            VARCHAR(20)  NOT NULL,
    address_normalized VARCHAR(320) NOT NULL,
    opted_out_at       TIMESTAMPTZ  NOT NULL,
    source             VARCHAR(40)  NOT NULL,
    source_agency_id   BIGINT       REFERENCES agencies(id) ON DELETE SET NULL,
    source_contact_id  BIGINT,
    consent_event_id   BIGINT       REFERENCES consent_events(id) ON DELETE SET NULL,
    actor_type         VARCHAR(20)  NOT NULL,
    actor_ref          VARCHAR(120),
    note               TEXT,
    idempotency_key    VARCHAR(300) NOT NULL UNIQUE,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT comm_optouts2_channel_chk
        CHECK (channel IN ('email', 'sms', 'whatsapp', 'phone')),
    -- email: minuscola, con la chiocciola; telefono: le sole cifre, con il
    -- prefisso internazionale e senza '+', come `contacts.phone_normalized`.
    CONSTRAINT comm_optouts2_address_chk CHECK (
        (channel = 'email'
            AND address_normalized = LOWER(BTRIM(address_normalized))
            AND POSITION('@' IN address_normalized) > 1)
     OR (channel <> 'email' AND address_normalized ~ '^[0-9]{6,15}$')),
    CONSTRAINT comm_optouts2_source_chk
        CHECK (source IN ('unsubscribe_link', 'crm', 'whatsapp_stop', 'sms_stop')),
    CONSTRAINT comm_optouts2_actor_type_chk
        CHECK (actor_type IN ('subject', 'operator')),
    CONSTRAINT comm_optouts2_operator_ref_chk
        CHECK (actor_type <> 'operator' OR (actor_ref IS NOT NULL AND BTRIM(actor_ref) <> '')),
    CONSTRAINT comm_optouts2_idem_chk CHECK (BTRIM(idempotency_key) <> '')
);

CREATE INDEX idx_comm_optouts2_address
    ON communication_optouts (channel, address_normalized, opted_out_at DESC);

CREATE FUNCTION communication_optouts_append_only() RETURNS trigger AS $fn$
BEGIN
    RAISE EXCEPTION '109: communication_optouts is append-only (%)', TG_OP;
END;
$fn$ LANGUAGE plpgsql;

CREATE TRIGGER trg_comm_optouts2_append_only
    BEFORE UPDATE OR DELETE ON communication_optouts
    FOR EACH ROW EXECUTE FUNCTION communication_optouts_append_only();

ALTER TABLE communication_optouts ENABLE ALWAYS TRIGGER trg_comm_optouts2_append_only;

-- Le disiscrizioni gia' registrate (108) passano sul canale email, una per
-- una, con la stessa chiave di idempotenza e lo stesso istante: nessuna
-- persa, nessuna inventata, nessuna retrodatata. La tabella 108 resta
-- leggibile (storico) ma chiusa in scrittura: da qui in avanti un opt-out si
-- registra SOLO in `communication_optouts`.
--
-- Condizionale solo per gli schemi di prova ridotti che non portano la 108:
-- su TEST e PRODUZIONE la 108 precede sempre la 109 e il ramo e' eseguito.
CREATE FUNCTION communication_email_optouts_closed() RETURNS trigger AS $fn$
BEGIN
    RAISE EXCEPTION '109: communication_email_optouts is closed: record the opt-out in communication_optouts';
END;
$fn$ LANGUAGE plpgsql;

DO $do$
BEGIN
    IF to_regclass('public.communication_email_optouts') IS NOT NULL THEN
        INSERT INTO communication_optouts
               (channel, address_normalized, opted_out_at, source, source_agency_id, source_contact_id,
                consent_event_id, actor_type, actor_ref, note, idempotency_key, created_at)
        SELECT 'email', email_normalized, opted_out_at, source, source_agency_id, source_contact_id,
               consent_event_id, actor_type, actor_ref, note, idempotency_key, created_at
          FROM communication_email_optouts
         ORDER BY id;

        CREATE TRIGGER trg_comm_optouts_closed
            BEFORE INSERT ON communication_email_optouts
            FOR EACH ROW EXECUTE FUNCTION communication_email_optouts_closed();

        ALTER TABLE communication_email_optouts ENABLE ALWAYS TRIGGER trg_comm_optouts_closed;
    END IF;
END
$do$;
