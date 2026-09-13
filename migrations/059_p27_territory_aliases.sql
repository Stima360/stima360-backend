-- P27-6 network territory aliases.
--
-- Additive, idempotent. Creates ONE table and alters none. No backfill and no
-- seed row: nessun alias esiste finche' qualcuno non lo dichiara, e dedurne tre
-- da `main.normalizza_comune` ripeterebbe l'errore che la 058 ha evitato -
-- trasformare un elenco scritto a mano in un fatto sul business.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- ---------------------------------------------------------------------------
-- PERCHE' QUESTA TABELLA ESISTE
--
-- P27-6 deve decidere quale agenzia riceve una stima pubblica. L'unico dato
-- geografico all'ingresso e' `stime.comune`, che e' testo di visualizzazione
-- ('Alba Adriatica'); l'identita' di un territorio e' `(kind, canonical_key)`
-- con la chiave nella forma della 058 ('alba-adriatica', ma anche '067001' o
-- 'te': la forma ammette qualunque minuscola-cifra-trattino).
--
-- IL PRIMO TENTATIVO DI P27-6 CALCOLAVA LA CHIAVE DAL COMUNE, e sbagliava.
-- La 058 ha separato `label` e `canonical_key` di proposito, e i suoi test lo
-- asseriscono: `network_territories_identity_unq` nomina `(kind,
-- canonical_key)` e mai `label`, e il repository cerca sulla coppia. Nessun
-- vincolo obbliga la chiave di un comune a essere lo slug della sua etichetta:
-- un territorio con `canonical_key = '067001'` e `label = 'Alba Adriatica'` e'
-- perfettamente legale, e un routing che calcolasse 'alba-adriatica' non lo
-- troverebbe mai. Il difetto non era il valore scelto: era l'esistenza stessa
-- di un secondo algoritmo di identita'.
--
-- Quindi la corrispondenza si DICHIARA. Questa tabella e' il posto in cui
-- qualcuno afferma: "il valore `Alba Adriatica`, quando arriva dal funnel
-- pubblico, e' questo territorio". Nessuna deduzione, nessuna convenzione
-- implicita, nessuna geografia inventata.
--
-- ---------------------------------------------------------------------------
-- LA NORMALIZZAZIONE, MINIMA E DICHIARATA UNA VOLTA SOLA
--
--     lower(btrim(regexp_replace(match_value, '\s+', ' ', 'g')))
--
-- Tre pieghe e nessuna di piu':
--
--   * gli spazi interni ripetuti diventano uno;
--   * gli spazi ai lati spariscono;
--   * le maiuscole non contano.
--
-- VIETATO e assente: slugification, traslitterazione, rimozione di accenti,
-- inferenze geografiche. 'Citta'' Sant''Angelo' resta esattamente quello, e chi
-- lo dichiara lo scrive come il funnel lo produce. Le tre pieghe sopra sono le
-- sole differenze che due persone che digitano lo stesso nome producono senza
-- volerlo; tutto il resto e' un nome diverso.
--
-- Le tre funzioni sono IMMUTABLE, che e' cio' che permette di usarle in un
-- indice. L'espressione compare in TRE posti - questo indice, la query di
-- `network_routing/repository.py`, i test - e deve essere la stessa stringa in
-- tutti e tre: un test la confronta carattere per carattere fra la migration e
-- il repository, cosi' che una divergenza si veda qui e non su Render.
--
-- ---------------------------------------------------------------------------
-- `status`, E PERCHE' C'E' UNA COLONNA IN PIU' DEL MINIMO RICHIESTO
--
-- Il requisito chiede di evitare la DELETE fisica con una semantica di
-- update/revoke. Ripuntare `territory_id` copre l'update; per il revoke serve
-- un modo di dire "questo alias non vale piu'" senza cancellare la riga, e
-- senza di esso l'unica via sarebbe proprio la DELETE.
--
-- Due valori, non tre: 'active' e 'revoked'. `agency_territory_assignments` ne
-- ha anche uno 'suspended' perche' un affiliato in pausa non e' un affiliato
-- sostituito; un alias in pausa non significa niente - o quel nome appartiene a
-- quel territorio, o non gli appartiene.

-- ---------------------------------------------------------------------------
-- 1. La tabella.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS network_territory_aliases (
    id           BIGSERIAL    PRIMARY KEY,

    -- ON DELETE RESTRICT, come ogni FK verso `network_territories` e verso
    -- `agencies` da P26 in poi. Non CASCADE: cancellare un territorio
    -- porterebbe via in silenzio la dichiarazione di quali nomi gli
    -- appartenevano, che e' l'unica cosa che questa tabella conserva. Non SET
    -- NULL: un alias senza territorio dice che un nome appartiene a qualcosa e
    -- si rifiuta di dire a cosa.
    territory_id BIGINT       NOT NULL
                 REFERENCES network_territories(id) ON DELETE RESTRICT,

    -- DA DOVE VIENE IL VALORE. Una sola sorgente supportata oggi, e la colonna
    -- esiste comunque: senza, il giorno in cui ne arrivasse una seconda
    -- 'Alba Adriatica' del funnel pubblico e 'Alba Adriatica' di un'altra
    -- origine sarebbero lo stesso alias, e l'unicita' sotto le confonderebbe.
    -- Non e' un sistema universale di alias: e' una colonna che impedisce a
    -- questa tabella di diventarlo per sbaglio.
    source       VARCHAR(30)  NOT NULL,

    -- Il valore COME LA SORGENTE LO PRODUCE. Si conserva cosi' com'e' - e' la
    -- forma che un amministratore riconosce - e si confronta normalizzato.
    match_value  VARCHAR(200) NOT NULL,

    status       VARCHAR(20)  NOT NULL DEFAULT 'active',
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- Identico a platform_admin/enums.ALIAS_SOURCES. Il test confronta i due
    -- elenchi, cosi' una seconda sorgente aggiunta da un lato fallisce li'
    -- invece di essere rifiutata dal database alla prima scrittura.
    CONSTRAINT network_territory_aliases_source_chk
        CHECK (source IN ('public_stima_comune')),

    CONSTRAINT network_territory_aliases_status_chk
        CHECK (status IN ('active', 'revoked')),

    -- Non vuoto DOPO la normalizzazione, non solo prima: '   ' supererebbe un
    -- BTRIM(match_value) <> '' scritto ingenuamente? No - ma '\t\t' si', e
    -- l'indice sotto lo ridurrebbe a stringa vuota. Meglio rifiutarlo qui.
    CONSTRAINT network_territory_aliases_value_chk
        CHECK (btrim(regexp_replace(match_value, '\s+', ' ', 'g')) <> '')
);

-- ---------------------------------------------------------------------------
-- 2. L'INVARIANTE, NEL DATABASE.
--
--     per una sorgente, un valore normalizzato porta ad AL PIU' un territorio.
--
-- Un indice unico SU ESPRESSIONE, non un CHECK: un vincolo di tabella non puo'
-- parlare di altre righe, e questa e' una proprieta' fra righe. E' lo stesso
-- strumento di `uq_agency_territory_single_active` (058) e di
-- `uq_agency_memberships_single_active` (027).
--
-- Parziale su `status = 'active'`: gli alias revocati restano, quanti servono,
-- e un indice pieno vieterebbe esattamente quella storia. E' anche cio' che
-- permette di revocare e poi ridichiarare lo stesso nome verso un altro
-- territorio senza riscrivere la riga vecchia.
-- ---------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS uq_territory_alias_active_value
    ON network_territory_aliases
       (source, lower(btrim(regexp_replace(match_value, '\s+', ' ', 'g'))))
 WHERE status = 'active';

-- Il percorso di lettura del routing: dal valore normalizzato al territorio.
CREATE INDEX IF NOT EXISTS idx_territory_alias_territory
    ON network_territory_aliases (territory_id, status);

-- ---------------------------------------------------------------------------
-- 3. Prova, riletta dal catalogo.
--
-- La disciplina di 055, 057 e 058: la migration non da' per scontato che le
-- proprie istruzioni abbiano avuto effetto, lo chiede al catalogo. Qui si
-- controllano le due cose la cui assenza non si noterebbe finche' non conta -
-- l'indice unico PARZIALE e SU ESPRESSIONE (uno pieno vieterebbe la storia,
-- uno non-unico lascerebbe passare il secondo alias) e la FK non distruttiva.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_indisunique BOOLEAN;
    v_indpred     TEXT;
    v_indexprs    TEXT;
    v_bad         TEXT;
BEGIN
    IF to_regclass('public.network_territory_aliases') IS NULL THEN
        RAISE EXCEPTION 'P27-6 059: network_territory_aliases was not created';
    END IF;

    SELECT i.indisunique,
           pg_get_expr(i.indpred, i.indrelid),
           pg_get_expr(i.indexprs, i.indrelid)
      INTO v_indisunique, v_indpred, v_indexprs
      FROM pg_index i
      JOIN pg_class c ON c.oid = i.indexrelid
     WHERE c.relname = 'uq_territory_alias_active_value';

    IF v_indisunique IS NULL THEN
        RAISE EXCEPTION
            'P27-6 059: uq_territory_alias_active_value is not installed';
    END IF;
    IF NOT v_indisunique THEN
        RAISE EXCEPTION
            'P27-6 059: uq_territory_alias_active_value exists but is not UNIQUE; '
            'the same comune could point at two territories';
    END IF;
    IF v_indpred IS NULL THEN
        RAISE EXCEPTION
            'P27-6 059: uq_territory_alias_active_value has no predicate; '
            'it would forbid the revoked history rather than the second active row';
    END IF;
    IF v_indexprs IS NULL THEN
        RAISE EXCEPTION
            'P27-6 059: uq_territory_alias_active_value is not on an expression; '
            'it would treat ''Alba Adriatica'' and ''alba adriatica'' as different';
    END IF;

    SELECT string_agg(conname, ', ')
      INTO v_bad
      FROM pg_constraint
     WHERE conrelid = 'public.network_territory_aliases'::regclass
       AND contype = 'f'
       AND confdeltype NOT IN ('a', 'r');

    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION
            'P27-6 059: these foreign keys destroy history on a parent delete: %',
            v_bad;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 4. Prova: l'indice MORDE, e morde sulle differenze che deve ignorare.
--
-- Un indice che esiste e non bite vale zero, e chi lo scoprirebbe sarebbe il
-- primo affiliato a ricevere i lead di un altro. La sonda scrive un territorio
-- e tre alias reali, verifica che il secondo - scritto con maiuscole e spazi
-- diversi, cioe' LO STESSO valore normalizzato - venga rifiutato, e che un
-- valore genuinamente diverso passi. Poi annulla tutto con il savepoint
-- implicito di un blocco BEGIN ... EXCEPTION: PL/pgSQL non puo' emettere
-- controllo di transazione, ed e' lo strumento che 057 e 058 hanno gia' usato.
--
-- Saltata quando non c'e' nessun territorio a cui puntare: un database appena
-- costruito dal runner non ne ha, e una migration che fallisse li' sarebbe una
-- migration inapplicabile a uno schema vuoto.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_territory BIGINT;
    v_rifiutato BOOLEAN := FALSE;
    v_primo_ok  BOOLEAN := FALSE;
    v_diverso   BOOLEAN := FALSE;
    v_sentinel  CONSTANT text := 'P27_6_059_PROBE_ROLLBACK';
BEGIN
    BEGIN
        INSERT INTO network_territories (kind, canonical_key, label)
        VALUES ('municipality', 'p27-6-059-probe', 'P27-6 probe')
        RETURNING id INTO v_territory;

        INSERT INTO network_territory_aliases (territory_id, source, match_value)
        VALUES (v_territory, 'public_stima_comune', 'Probe Comune');
        v_primo_ok := TRUE;

        -- Stesso valore normalizzato: maiuscole diverse e spazi doppi.
        BEGIN
            INSERT INTO network_territory_aliases (territory_id, source, match_value)
            VALUES (v_territory, 'public_stima_comune', '  probe   COMUNE  ');
        EXCEPTION WHEN unique_violation THEN
            v_rifiutato := TRUE;
        END;

        -- Un valore davvero diverso deve passare: l'indice non deve essere
        -- diventato "un alias per sorgente".
        INSERT INTO network_territory_aliases (territory_id, source, match_value)
        VALUES (v_territory, 'public_stima_comune', 'Probe Comune Due');
        v_diverso := TRUE;

        RAISE EXCEPTION USING MESSAGE = v_sentinel;
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM <> v_sentinel THEN
            RAISE;
        END IF;
    END;

    IF NOT v_primo_ok THEN
        RAISE EXCEPTION 'P27-6 059: a legitimate first alias was refused';
    END IF;
    IF NOT v_rifiutato THEN
        RAISE EXCEPTION
            'P27-6 059: the same normalised value was accepted twice; '
            'one comune could point at two territories';
    END IF;
    IF NOT v_diverso THEN
        RAISE EXCEPTION
            'P27-6 059: a genuinely different value was refused; the index is '
            'too wide and allows only one alias per source';
    END IF;
END
$do$;
