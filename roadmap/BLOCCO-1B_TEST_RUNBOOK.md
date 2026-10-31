# BLOCCO-1B — runbook TEST: migrazione 115 e collaudo «Dati agenzia e logo»

Solo **TEST** (`stima360_db_test`, servizio Render TEST). PROD esclusa: il runner rifiuta qualunque database che non sia un TEST.

## Stato di partenza

| Cosa | Stato |
|---|---|
| Migrazioni applicate su `stima360_db_test` | fino alla **114** (da verificare al passo 1) |
| **115** (`agency_logos`) | **preparata, non applicata** |
| Codice BLOCCO-1A/1B | **commit locale**, nessun push |
| Prove PostgreSQL `tests/test_blocco_1b_agency_profile_postgres.py` | **preparate, non eseguite** (manca un cluster di TEST verificato: vedi passo 0) |
| Funzione online | **NON pronta**: dipende dalla tabella `agency_logos`, che non esiste ancora |

## L'ordine: migrazione PRIMA dell'attivazione

Il branch `core-0.1-test` puo' avere l'auto-deploy: un push mette online il codice subito. Quindi:

1. **prima** si applica la 115 su `stima360_db_test` (passi 1-4);
2. **poi** si esegue il push del codice e si collauda (passi 5-6).

Il logo salvato e' sempre una versione normalizzata da Pillow (orientamento EXIF applicato, metadati tolti, ricodifica PNG/JPEG), mai il file caricato.

Se l'ordine si inverte per errore il codice NON si rompe: i dati agenzia si leggono e si salvano, il logo risponde **503 `logo_unavailable`** («Il logo non e' ancora attivo su questo ambiente») e l'interfaccia nasconde i comandi del logo. Ma la funzione **non** va dichiarata pronta finche' la 115 non e' applicata e il passo 6 non e' superato.

> Stato al momento della consegna: **nessun passo eseguito**. Da qui non c'e' accesso a Render ne' al database.

## 0. Prove offline e PostgreSQL (prima di toccare TEST)

```bash
python -m pytest -q tests/test_blocco_1b_agency_profile.py tests/test_blocco_1a_property_history_author.py
```

Le prove PostgreSQL creano e poi eliminano un database `blocco_1b_probe_*` e **non scrivono in `stima360_db_test`**. Ma creare un database temporaneo nello stesso cluster **non prova che il cluster sia di test**: un DSN sbagliato aprirebbe il database usa-e-getta accanto a quelli di produzione.

**Precondizione (da verificare prima, a cura di chi lancia le prove):**

1. host e porta del DSN sono quelli del servizio PostgreSQL di TEST su Render (non quelli di PROD), confrontati con la dashboard Render, non dedotti dal nome;
2. nel cluster esiste `stima360_db_test` e **non** esiste `stima360_db` (`SELECT datname FROM pg_database;`);
3. l'utente del DSN e' un utente di TEST, con `CREATE DATABASE` e senza accesso a PROD.

Solo dopo, e dichiarandolo esplicitamente (senza la variabile le prove restano saltate):

```bash
P29_TEST_DSN="postgresql://<utente>@<host>/<database di servizio>" BLOCCO_1B_TEST_CLUSTER_VERIFIED=1 \
  python -m pytest -q tests/test_blocco_1b_agency_profile_postgres.py
```

**Atteso:** 16 passati, 0 saltati.

## 1. Fotografia prima (sola lettura)

```bash
cat > /tmp/b1b_check.sql <<'SQL'
BEGIN READ ONLY;
SELECT current_database() AS database;
SELECT version, applied_at, rolled_back_at FROM schema_migrations WHERE version >= '113' ORDER BY version;
SELECT to_regclass('agency_logos') AS tabella_loghi;
SELECT count(*) AS agenzie, count(*) FILTER (WHERE settings ? 'report_profile') AS con_dati_report FROM agencies;
ROLLBACK;
SQL
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 -f /tmp/b1b_check.sql
```

**Atteso:** `database` = `stima360_db_test`; 113 e 114 applicate, 115 assente; `tabella_loghi` vuota (NULL); `con_dati_report` = 0.

## 2. Stato del runner

```bash
python scripts/p26_migrate.py status --operator "<operatore>"
python scripts/p26_migrate.py plan   --operator "<operatore>"
```

**Atteso:** una sola pendente, `115_agency_logos`.

## 3. Applicazione (solo su decisione esplicita)

```bash
python scripts/p26_migrate.py apply  --operator "<operatore>"
python scripts/p26_migrate.py status --operator "<operatore>"
```

La 115 e' additiva: una tabella nuova, nessun backfill, nessuna riga esistente toccata.

## 4. Fotografia dopo (sola lettura)

Rieseguire il passo 1. **Atteso:** 115 applicata; `tabella_loghi` = `agency_logos`; `agenzie` invariato; `con_dati_report` = 0.

## 5. Push del codice (solo su decisione esplicita)

Solo dopo il passo 4. Verificare che il servizio TEST giri sul commit atteso (`$RENDER_GIT_COMMIT`) e su `DB_NAME=stima360_db_test`.

## 6. Collaudo live (Shell del CRM su TEST, dati di prova)

Con un titolare (o amministratore) di un'agenzia di prova:

1. menu utente → «Dati agenzia e logo»: l'intestazione salvata mostra il nome dell'agenzia in anagrafica;
2. inserire il nome per i report e alcuni dati: compare l'anteprima «Non salvata»; «Salva dati» → «Dati agenzia salvati.»;
3. caricare un PNG con trasparenza: «Logo salvato.», proporzioni e trasparenza intatte; una foto JPEG scattata in verticale compare dritta;
4. provare un file non valido: messaggio d'errore, **il logo precedente resta**;
5. sostituire con un JPEG, poi «Rimuovi logo»: l'intestazione torna al solo nome;
6. con un agente della stessa agenzia: tutto in sola lettura;
7. con un utente di un'altra agenzia: dati e logo propri, mai quelli della prima.

Poi, sola lettura: `SELECT agency_id, content_type, width, height, octet_length(image_bytes), uploaded_by FROM agency_logos;` (solo righe delle agenzie di prova).

## Rientro (solo se serve, su decisione esplicita)

Il codice precedente ignora la tabella: rientrare dal codice non richiede di toccare lo schema. La down `115_agency_logos_down.sql` **rifiuta** di procedere se esistono loghi: in quel caso conservare la tabella o ripristinare da un backup verificato. I dati agenzia in `agencies.settings.report_profile` restano e non disturbano il codice precedente (la Platform non li espone e li conserva nel merge).
