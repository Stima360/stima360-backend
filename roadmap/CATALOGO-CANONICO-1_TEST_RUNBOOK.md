# CATALOGO-CANONICO-1 — runbook TEST: migration 087 e 088

Solo **TEST** (`stima360_db_test`, servizio Render TEST). PROD esclusa: il runner e lo script di recupero rifiutano qualunque database che non sia un TEST.

Cosa fa, nell'ordine:

1. controlla che il servizio giri sul commit giusto;
2. fotografa lo stato del database in sola lettura (ledger, tabelle, colonne della dettagliata);
3. `status` del runner: devono risultare in sospeso **solo** 087 e/o 088;
4. `apply`;
5. ricontrolla tutto in sola lettura;
6. censimento degli invii del sito (sola lettura).

Ogni blocco si incolla da solo nella **Shell** del servizio TEST su Render. Nessun blocco stampa la password: `PGPASSWORD` si legge dall'ambiente del servizio.

> Stato al momento della consegna: **nessuno di questi passi è stato eseguito**. Da qui non c'è accesso autorizzato a Render né al database TEST.

---

## 0. Commit del servizio

```bash
echo "commit in esecuzione: $RENDER_GIT_COMMIT"
echo "database: $DB_NAME"
cd /opt/render/project/src 2>/dev/null || true
ls migrations/087_catalogo_canonico_1_site_attributes.sql migrations/088_catalogo_canonico_1b_site_inbox.sql
```

**Atteso:** `RENDER_GIT_COMMIT` è il commit di questa fase o uno successivo su `core-0.1-test`, `DB_NAME` è `stima360_db_test`, ed entrambi i file esistono.
**Altrimenti:** fermarsi. Il deploy non è ancora arrivato, o il servizio non è quello di TEST.

## 1. Fotografia prima (sola lettura)

```bash
cat > /tmp/cc1_check.sql <<'SQL'
BEGIN READ ONLY;
SELECT current_database() AS database, current_user AS utente;
-- ledger: le ultime migration e il loro stato
SELECT version, applied_at, applied_by_operator, rolled_back_at
  FROM schema_migrations WHERE version >= '084' ORDER BY version;
-- tabelle della 087 e della 088
SELECT to_regclass('public.property_site_sources') AS tabella_087,
       to_regclass('public.site_submissions')      AS tabella_088;
-- stime_dettagliate: colonne presenti e tipi (la 088 aggiunge SOLO quelle che mancano)
SELECT column_name, data_type, character_maximum_length
  FROM information_schema.columns
 WHERE table_schema = 'public' AND table_name = 'stime_dettagliate'
 ORDER BY ordinal_position;
-- quante delle 28 colonne del sito mancano
SELECT count(*) AS colonne_mancanti FROM unnest(ARRAY[
  'nome','cognome','email','telefono','indirizzo','tipologia','mq','piano','locali','bagni','ascensore',
  'stato','anno','microzona','posizionemare','distanzamare','barrieramare','vistamare','mqgiardino',
  'mqgarage','mqcantina','mqpostoauto','mqtaverna','mqsoffitta','mqterrazzo','numbalconi',
  'altrodescrizione','pertinenze']) AS c(nome)
 WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns i
                    WHERE i.table_schema = 'public' AND i.table_name = 'stime_dettagliate' AND i.column_name = c.nome);
-- volumi (nessun dato personale)
SELECT (SELECT count(*) FROM stime) AS stime,
       (SELECT count(*) FROM stime_dettagliate) AS stime_dettagliate,
       (SELECT count(*) FROM properties) AS schede;
ROLLBACK;
SQL
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 -f /tmp/cc1_check.sql
```

**Atteso** (dallo snapshot certificato P26-0 del 2026-09-05; **non ancora verificato** sul TEST di oggi):

- in `stime_dettagliate`, 14 colonne: le 13 dello snapshot più `agency_id` della 049;
- `colonne_mancanti` = 28;
- `tabella_088` vuota.

Se `tabella_087` è vuota, la 087 non è ancora applicata.

**Se `colonne_mancanti` è minore di 28:** alcune colonne esistono già, per esempio perché qualcuno ha eseguito `database.py`. La 088 non le tocca: né il tipo né i dati. In questo caso conviene confrontare i loro tipi con quelli della 088 (`VARCHAR(50)` per `tipologia`, `INTEGER` per `mq`, …) e annotarli nel report prima di proseguire. Non serve fermarsi.

## 2. Stato del runner (sola lettura)

```bash
python scripts/p26_migrate.py status --operator "giorgio.larasa"
```

**Atteso:** tutte le righe `applied` fino alla 086, poi:

```
  pending      087_catalogo_canonico_1_site_attributes [transactional]
  pending      088_catalogo_canonico_1b_site_inbox [transactional]
```

Se la 087 è già applicata, compare la sola 088 come `pending`. Non deve comparire nessuna riga `PROBLEM`.

**Se compare un'altra `pending`, o un `PROBLEM`: fermarsi.** `apply` applicherebbe tutto ciò che è in sospeso.

Facoltativo: la validazione statica, senza database (elenca tutte le migration con i checksum).

```bash
python scripts/p26_migrate.py plan --operator "giorgio.larasa"
```

**Atteso:** l'ultima riga prima di `static validation: OK` è `088_catalogo_canonico_1b_site_inbox [transactional] …`.

## 3. Applicazione

```bash
python scripts/p26_migrate.py apply --operator "giorgio.larasa"
echo "exit: $?"
```

**Atteso:** exit 0. Ogni migration gira in una transazione sua: un errore la annulla per intero, senza applicazioni a metà.

```bash
python scripts/p26_migrate.py status --operator "giorgio.larasa"
```

**Atteso:** 087 e 088 `applied`, nessuna `pending`.

## 4. Fotografia dopo (sola lettura)

```bash
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 -f /tmp/cc1_check.sql
cat > /tmp/cc1_post.sql <<'SQL'
BEGIN READ ONLY;
SELECT indexname FROM pg_indexes WHERE tablename = 'site_submissions' ORDER BY 1;
SELECT tgname FROM pg_trigger WHERE tgrelid = 'site_submissions'::regclass AND NOT tgisinternal;
SELECT count(*) AS invii FROM site_submissions;
ROLLBACK;
SQL
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 -f /tmp/cc1_post.sql
```

**Atteso:**

- `colonne_mancanti` = 0, e le due tabelle presenti;
- gli indici `site_submissions_pkey`, `uq_site_submissions_quick`, `uq_site_submissions_detail`, `idx_site_submissions_status`, `idx_site_submissions_request`, `idx_site_submissions_stima`;
- il trigger `trg_site_submissions_scope`;
- `invii` = 0: nessun backfill.

## 5. Invii del sito (sola lettura)

```bash
python scripts/site_sync_recover.py --census
```

**Atteso subito dopo l'applicazione:**

- `"installed": true`;
- `submissions` vuoto;
- `stime_without_submission` uguale al numero di `stime`.

Queste stime sono precedenti alla 088: l'invio originale non è stato conservato, quindi **non si recuperano** e nessuna modalità le tocca.

## 6. Collaudo live (dopo l'applicazione)

Da fare dal sito di TEST, con dati di prova:

1. stima rapida con contatto e decimali (per esempio 85,5 m²): nella Shell del CRM nasce **una** scheda di censimento, con 85,5 m²;
2. dal link della mail, stima dettagliata lasciando invariati i campi precompilati più un dato nuovo (per esempio la classe): la scheda conserva 85,5 m²; «Dal sito Stima360» elenca i campi «lasciati come precompilati»;
3. una seconda dettagliata che cambia un campo (per esempio i locali): il valore cambia, oppure compare una differenza se l'agente lo aveva corretto;
4. verificare lo stato degli invii:

```bash
python scripts/site_sync_recover.py --census      # quick/synced e detail/synced
python scripts/site_sync_recover.py --dry-run     # atteso: "items": []
```

**Se il census mostra invii `failed` o `pending`:**

```bash
python scripts/site_sync_recover.py --dry-run
python scripts/site_sync_recover.py --apply --confirm-database "$DB_NAME"
```

Lo script è idempotente: ripeterlo non duplica nulla. Ignora gli invii più recenti di 120 secondi, che potrebbero essere ancora in corso.

## Rientro (solo se serve)

La down della 088 si **ferma** se ci sono invii `pending` o `failed`: sono l'unica copia dei valori dichiarati, quindi vanno prima recuperati. Quando si esegue:

- toglie `site_submissions`;
- **lascia** le 28 colonne di `stime_dettagliate`, perché non può sapere quali esistessero già.

Il runner non ha un comando `down`: la down si esegue a mano solo su decisione esplicita, come per le migration precedenti.
