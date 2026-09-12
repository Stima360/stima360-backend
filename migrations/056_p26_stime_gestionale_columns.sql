-- P26-6 STIME: le due colonne del gestionale che nessuna migration creava.
--
-- PERCHE' QUESTA MIGRATION ESISTE
--
-- `POST /api/admin/stime/{id}/update` e' una funzione richiesta e i suoi UNICI
-- due campi scrivibili sono `stime.lead_status` e `stime.note_internal`:
--
--     class LeadUpdate(BaseModel):
--         lead_status: str | None = None
--         note_internal: str | None = None
--
-- Su TEST quelle colonne non esistono. Non e' una svista di deploy: nascono
-- solo da `migrazione_gestionale_stime()`, una funzione nel blocco `__main__`
-- di `database.py` che NESSUN file sotto `migrations/` ha mai eseguito, e
-- `docs/P26_BASELINE_CERTIFICATE_TEST.md` §3.0.2 le registra fra le trenta
-- "dichiarate ma assenti" - avvertendo, nello stesso paragrafo, di non
-- eseguire `database.py` come script per crearle, perche' invaliderebbe
-- l'impronta certificata in §2.
--
-- Quindi la route legge e scrive due colonne che sul database costruito dalle
-- migration non ci sono. La lettura e' gia' stata resa non fatale - vedi
-- `to_jsonb(s) ->> 'lead_status'` in `admin_lista_stime`, che restituisce NULL
-- invece di abortire - ma quel ripiego rende la funzione DIMEZZATA, non
-- completa: si legge sempre NULL e non si puo' scrivere nulla. Una scrittura
-- non si aggira: o la colonna c'e', o la funzione non esiste.
--
-- PERCHE' UNA MIGRATION E NON `python database.py`
--
-- Sono le due sole strade, e una sola e' ammessa. Eseguire `database.py`
-- aggiungerebbe TRENTA colonne - le due che servono e ventotto su
-- `stime_dettagliate` che nessuno ha chiesto - fuori da ogni ledger, senza
-- versione, senza down, e invaliderebbe la baseline. Una migration numerata
-- aggiunge esattamente cio' che serve, si registra in `schema_migrations`, si
-- annulla, e il nuovo stato e' certificabile: e' la strada che 026 ha aperto
-- proprio per questo.
--
-- PERCHE' SOLO DUE DELLE TRENTA
--
-- Le altre ventotto stanno su `stime_dettagliate` e nessuna route le nomina:
-- `admin_lista_stime_pro` fa `SELECT *`, che non puo' rompersi su una colonna
-- assente. Crearle "per allineare" significherebbe modificare lo schema senza
-- un chiamante - il contrario di cio' che una migration dovrebbe fare. Restano
-- assenti, e §3.0.2 continua a descriverle correttamente.
--
-- Colonne nullable: varchar(32) e text.
-- Le stime preesistenti ricevono NULL quando le colonne vengono aggiunte.
-- Eventuali valori gia' presenti vengono conservati.
-- Il default 'nuovo' viene impostato dopo la verifica delle colonne:
-- riguarda i nuovi inserimenti, senza riclassificare le righe esistenti.
--
-- NIENTE INDICE. `migrazione_gestionale_stime` crea anche `idx_stime_data`.
-- Non serve a questa route - il filtro su `data` c'era gia' prima e nessuno ha
-- misurato un problema - e un indice aggiunto per simmetria e' un oggetto in
-- piu' da giustificare nella prossima classificazione. Se servira', avra' la
-- sua migration e la sua ragione.
--
-- Nessun NOT NULL, indice o UPDATE dei dati esistenti.
-- L'ALTER TABLE richiede un lock sulla tabella.
--
-- Transaction ownership: il runner possiede la transazione UP, quindi questo
-- file non porta BEGIN/COMMIT.

-- ---------------------------------------------------------------------------
-- 1. Le due colonne.
-- ---------------------------------------------------------------------------
ALTER TABLE stime
    ADD COLUMN IF NOT EXISTS lead_status   VARCHAR(32),
    ADD COLUMN IF NOT EXISTS note_internal TEXT;

-- ---------------------------------------------------------------------------
-- 1b. Verificare cio' che c'e' DAVVERO.
--
-- `ADD COLUMN IF NOT EXISTS` accetta in silenzio una colonna preesistente di
-- qualunque tipo: un `lead_status` INTEGER, o NOT NULL, passerebbe senza una
-- parola e la route fallirebbe a runtime su uno schema che la migration ha
-- dichiarato a posto. E' la stessa verifica che 031 fa su `agency_id`, per la
-- stessa ragione.
--
-- Tipo e nullabilita' si esigono: sono cio' da cui dipende la correttezza
-- della UPDATE. Sul default si e' piu' severi in un solo verso - un default
-- DIVERSO cambierebbe cosa significa "lead nuovo" e va fermato; un default
-- assente e' solo meno comodo e non rompe nulla.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_type    text;
    v_len     integer;
    v_null    text;
    v_default text;
BEGIN
    SELECT data_type, character_maximum_length, is_nullable, column_default
      INTO v_type, v_len, v_null, v_default
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name   = 'stime'
       AND column_name  = 'lead_status';

    IF v_type IS NULL THEN
        RAISE EXCEPTION
            'P26-6 056: stime.lead_status manca dopo ADD COLUMN';
    END IF;

    IF v_type <> 'character varying' OR v_len IS DISTINCT FROM 32 THEN
        RAISE EXCEPTION
            'P26-6 056: stime.lead_status deve essere varchar(32), trovato %(%)',
            v_type, v_len;
    END IF;

    IF v_null <> 'YES' THEN
        RAISE EXCEPTION
            'P26-6 056: stime.lead_status deve restare nullable: la route scrive solo cio'' che il chiamante manda';
    END IF;

    IF v_default IS NOT NULL AND v_default <> '''nuovo''::character varying' THEN
        RAISE EXCEPTION
            'P26-6 056: stime.lead_status ha un default diverso da ''nuovo'': %',
            v_default;
    END IF;

    SELECT data_type, is_nullable, column_default
      INTO v_type, v_null, v_default
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name   = 'stime'
       AND column_name  = 'note_internal';

    IF v_type IS NULL THEN
        RAISE EXCEPTION
            'P26-6 056: stime.note_internal manca dopo ADD COLUMN';
    END IF;

    IF v_type <> 'text' THEN
        RAISE EXCEPTION
            'P26-6 056: stime.note_internal deve essere text, trovato %', v_type;
    END IF;

    IF v_null <> 'YES' THEN
        RAISE EXCEPTION
            'P26-6 056: stime.note_internal deve restare nullable';
    END IF;

    IF v_default IS NOT NULL THEN
        RAISE EXCEPTION
            'P26-6 056: stime.note_internal non deve avere default, trovato %',
            v_default;
    END IF;
END
$do$;

-- Default per i nuovi inserimenti; le righe esistenti restano invariate.
ALTER TABLE public.stime ALTER COLUMN lead_status SET DEFAULT 'nuovo';
