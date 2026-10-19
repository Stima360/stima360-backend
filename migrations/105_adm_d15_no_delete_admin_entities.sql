-- 105 - ADM D15: nessuna cancellazione fisica delle entita' della Rete.
--
-- ATTIVAZIONE. Spostata da `migrations/pending/` a `migrations/` su
-- autorizzazione del proprietario: da qui il runner (`scripts/p26_migrate.py`,
-- glob di `migrations/*.sql`) la vede e la applica come ogni altra. Su un
-- database va applicata solo dopo aver verificato che sia `stima360_db_test`
-- e che ne esista un backup ripristinabile.
--
-- GIA' PRONTO PRIMA DELL'ATTIVAZIONE
--   * Le fixture di prova riconoscono D15 dal catalogo (`d15_attiva`) e non
--     cancellano piu' queste righe.
--   * Gli script di certificazione P26 (`scripts/p26_3_live_cert.py`,
--     `scripts/p26_6_live_cert.py`) sono compatibili: le identita' di prova
--     si dismettono (disabled, membership revoked, sessioni cancellate) e le
--     agenzie dedicate si archiviano dopo averne rimosso le fixture. Restano
--     come righe storiche riconoscibili (`p26-*-cert-...@certification.invalid`,
--     slug `p26-6-cert-...`) e ogni run ne conta i residui.
--
-- ROLLBACK: `105_adm_d15_no_delete_admin_entities_down.sql` toglie i trigger e
-- la funzione e riporta i trigger del registro a ENABLE (stato di 057).
-- Nessun dato viene toccato, in nessuna delle due direzioni.
--
-- COSA FA
--
--   1. Un trigger BEFORE DELETE (per riga) e uno BEFORE TRUNCATE (per
--      istruzione) rifiutano la cancellazione fisica di:
--        agencies, operator_users, agency_memberships, network_territories,
--        agency_territory_assignments, network_territory_aliases.
--      Le operazioni della Rete restano tutte UPDATE di stato (sospendi,
--      elimina dalla Rete = archivia/disabilita, revoca, ripristina) e non ne
--      sono toccate. Senza DELETE sui genitori, nessuna CASCADE o SET NULL
--      verso agenzie e operatori puo' piu' scattare.
--   2. I trigger del registro `platform_audit_log` (057) diventano
--      ENABLE ALWAYS, come quelli di consensi e comunicazioni: con
--      `session_replication_role = replica` non si spengono piu' (R13).
--
-- NESSUN INTERRUTTORE. Nessuna variabile di sessione apre questa porta, come
-- per consensi e comunicazioni (062/064). Resta possibile solo a chi possiede
-- il database: disabilitare il trigger, cosa visibile e da autorizzare.
--
-- Idempotente: CREATE OR REPLACE e DROP TRIGGER IF EXISTS.
-- Nessun BEGIN/COMMIT: la transazione e' del runner.

CREATE OR REPLACE FUNCTION adm_refuse_admin_delete()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
BEGIN
    RAISE EXCEPTION
        'ADM D15: % non si cancella fisicamente (%). Si sospende, si elimina dalla Rete o si revoca: dati e storico restano.',
        TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation';
END
$fn$;

DO $do$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'agencies', 'operator_users', 'agency_memberships', 'network_territories',
        'agency_territory_assignments', 'network_territory_aliases'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_adm_no_delete ON %I', t);
        EXECUTE format('CREATE TRIGGER trg_adm_no_delete BEFORE DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION adm_refuse_admin_delete()', t);
        EXECUTE format('ALTER TABLE %I ENABLE ALWAYS TRIGGER trg_adm_no_delete', t);
        EXECUTE format('DROP TRIGGER IF EXISTS trg_adm_no_truncate ON %I', t);
        EXECUTE format('CREATE TRIGGER trg_adm_no_truncate BEFORE TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION adm_refuse_admin_delete()', t);
        EXECUTE format('ALTER TABLE %I ENABLE ALWAYS TRIGGER trg_adm_no_truncate', t);
    END LOOP;
END
$do$;

ALTER TABLE platform_audit_log ENABLE ALWAYS TRIGGER trg_platform_audit_log_append_only;
ALTER TABLE platform_audit_log ENABLE ALWAYS TRIGGER trg_platform_audit_log_no_truncate;

-- Verifica di catalogo: ogni tabella ha i due trigger, sempre attivi.
DO $do$
DECLARE
    t text;
    n integer;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'agencies', 'operator_users', 'agency_memberships', 'network_territories',
        'agency_territory_assignments', 'network_territory_aliases'
    ] LOOP
        SELECT count(*) INTO n FROM pg_trigger
         WHERE tgrelid = format('public.%I', t)::regclass
           AND tgname IN ('trg_adm_no_delete', 'trg_adm_no_truncate')
           AND tgenabled = 'A';
        IF n <> 2 THEN
            RAISE EXCEPTION 'ADM D15 105: trigger mancanti o non ENABLE ALWAYS su %', t;
        END IF;
    END LOOP;
    SELECT count(*) INTO n FROM pg_trigger
     WHERE tgrelid = 'public.platform_audit_log'::regclass
       AND tgname IN ('trg_platform_audit_log_append_only', 'trg_platform_audit_log_no_truncate')
       AND tgenabled = 'A';
    IF n <> 2 THEN
        RAISE EXCEPTION 'ADM D15 105: i trigger di platform_audit_log non sono ENABLE ALWAYS';
    END IF;
END
$do$;
