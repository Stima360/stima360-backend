# CRM PROD autonomo (SITE-IMPORT-1): runbook di attivazione su Render

Stato: **PRONTO PER L'ATTIVAZIONE.** Codice e procedura verificati su database
isolati; nessuna operazione di questo runbook e' stata eseguita su servizi
reali. Ogni passo su Render lo esegue il titolare, nell'ordine scritto qui.

## 0. Il principio

    www.stima360.it  ->  backend PROD `main` (stima360-backend)  ->  DB del sito
                                                                      |  SOLA LETTURA
                                                                      v
                          CRM PROD (core-0.1-test)  <-  run_site_import_cron.py
                                   |
                                   v
                              DB CRM PROD (nuovo, separato: stima360_crm)

Il sito, il suo backend e il suo database **non cambiano di un byte**: nessuna
migration, nessun ruolo, nessun trigger, nessuna variabile. Il CRM legge.

Due canali di migrazione, disgiunti per costruzione:

| Canale | Database | Accetta | Rifiuta |
|--------|----------|---------|---------|
| `scripts/p26_migrate.py` (intatto) | CRM TEST | solo nomi con `test` | `stima360_db`, `stima360`, tutto il resto |
| `scripts/crm_prod_migrate.py` (nuovo) | CRM PROD | un database **vuoto** con nome senza `test`, poi solo quello con il marcatore `crm_instance` | `stima360_db`, `stima360`, nomi con `test`, l'URL di `SITE_DB_URL`, qualunque database non vuoto senza marcatore (sito, CRM TEST, copie) |

## 1. Passo 1 - Database CRM PROD (Render)

1. Dashboard Render → **New → PostgreSQL**: nome istanza `stima360-crm-prod`,
   **Database** `stima360_crm`, stessa regione del servizio web, piano con
   backup. Il nome del database **non** deve contenere `test` e non deve essere
   `stima360_db`: il canale lo rifiuterebbe.
2. Prendere la **Internal Database URL** (`postgresql://.../stima360_crm`).
   Non va mai scritta nel repository ne' nei log.

## 2. Passo 2 - Web Service CRM PROD (Render)

**New → Web Service**, repository `stima360-backend`, branch **`core-0.1-test`**,
nome `stima360-crm-prod`, runtime Python, stesso build/start command del CRM TEST.
**Auto-Deploy: off** finche' il bootstrap del database non e' finito.

Variabili d'ambiente del servizio web:

| Variabile | Valore | Perche' |
|-----------|--------|---------|
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | dal **nuovo** DB `stima360_crm` | `database.get_connection()` |
| `ADMIN_USER`, `ADMIN_PASS` | nuovi, non quelli del sito | accesso amministrativo CRM |
| `PUBLIC_BASE_URL` | `https://stima360-crm-prod.onrender.com` (l'URL del servizio) | **obbligatoria**: il default nel codice e' il backend del sito |
| `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS` | **non impostate** | senza `SMTP_USER`/`SMTP_PASS` il CRM non puo' spedire email: le email iniziali restano al sito |
| `WHATSAPP_*` | **non impostate** | nessun WhatsApp dal CRM |
| `GITHUB_TOKEN`, `GITHUB_REPO` | **non impostate** | il CRM non scrive mai nell'archivio PDF del sito |

Il primo deploy puo' partire subito: il servizio web non crea schema e senza
tabelle risponde con errori finche' il passo 3 non e' fatto; nessun danno.

## 3. Passo 3 - Bootstrap dello schema (una volta, dalla Shell del servizio web)

Nella **Shell** di `stima360-crm-prod` (Render → servizio → Shell), con l'URL
interna del passo 1 passata inline e il proprio nome come operatore:

```bash
CRM_PROD_DATABASE_URL='postgresql://.../stima360_crm' \
  python3 scripts/crm_prod_migrate.py status --database stima360_crm
# atteso: classification : empty

CRM_PROD_DATABASE_URL='postgresql://.../stima360_crm' \
  python3 scripts/crm_prod_migrate.py bootstrap --database stima360_crm --operator "nome.cognome"
```

Cosa fa, nell'ordine, esattamente come e' nato il CRM TEST: marcatore
`crm_instance` → tabelle legacy (`database.py`) → migration 001..025 (010/011
con la guardia sul nome riscritta in memoria su `stima360_crm`; nulla su disco
cambia, il manifesto lo registra) → confronto dello schema con il certificato
del baseline TEST (stesse 60 tabelle e 764 colonne) → certificato CRM PROD
scritto sotto `reports/` → 026 (baseline) → 027..095 dal registro
`schema_migrations` → sequenze `stime`/`stime_dettagliate` portate a
1.000.000.000 → marcatore `ready`. Esito atteso:

    ... applied 095_site_import_ledger
    bootstrap       : ready. Commit reports/p26_baseline_CRM_PROD_<ts>.json to the repository for audit.

Poi:

```bash
CRM_PROD_DATABASE_URL='...' python3 scripts/crm_prod_migrate.py status --database stima360_crm
# atteso: classification : crm_prod, baseline present: True, nessun "pending"
```

Il certificato `reports/p26_baseline_CRM_PROD_<ts>.json` (+ `.sha256`) va
scaricato dalla shell e committato nel repository: e' la prova dello schema
di partenza. Il marcatore `crm_instance` non si puo' cancellare (trigger).

Se il bootstrap si interrompe a meta', il canale rifiuta di toccare di nuovo
quel database: si **elimina l'istanza** Render (non contiene ancora nulla di
valore), se ne crea una vuota e si ripete il passo 3.

Migration future sul CRM PROD: `python3 scripts/crm_prod_migrate.py upgrade
--database stima360_crm --operator "nome.cognome"` (solo con il marcatore;
registro append-only, file registrati mai modificati).

Dopo il bootstrap: riattivare **Auto-Deploy** del servizio web se desiderato.

## 4. Passo 4 - Dati di esercizio del CRM

| Cosa | Come |
|------|------|
| `zone_valori` (valutazioni interne del CRM) | `pg_dump -t zone_valori --data-only` dal DB del sito con la connessione di sola lettura, ripristino nel DB `stima360_crm`. Serve alle valutazioni del CRM, non all'importazione. |
| Agenzie, territori/alias, operatori | come nel CRM TEST (027 crea l'agenzia di default `stima360`; il routing dei comuni usa `network_routing`). Senza territorio assegnato le stime importate vanno all'agenzia di default. |

## 5. Passo 5 - Cron Job di importazione (Render)

**New → Cron Job**, stesso repository, branch **`core-0.1-test`**, nome
`stima360-crm-prod-site-import`, comando `python3 run_site_import_cron.py`,
pianificazione `*/5 * * * *` (si crea sospeso o si attiva dopo il §6).

Variabili del cron: le stesse del servizio web (`DB_*`, `PUBLIC_BASE_URL`,
nessuna SMTP/WhatsApp/GitHub di scrittura) **piu'**:

| Variabile | Valore | Default |
|-----------|--------|---------|
| `SITE_DB_URL` | **Internal Database URL** del DB del sito (quella che usa `stima360-backend`) | obbligatoria |
| `SITE_PDF_GITHUB_REPO` | `owner/repo` dell'archivio PDF del sito (dove `main` carica `stima_<id>.pdf`) | nessuno: senza, i PDF risultano "archivio non disponibile" e si ritentano |
| `SITE_PDF_GITHUB_TOKEN` | token GitHub **fine-grained, sola lettura** (Contents: Read) su quel solo repository | facoltativo se il repo e' pubblico (limite 60 richieste/ora senza token) |
| `SITE_PDF_GITHUB_BRANCH` | `main` | `main` |
| `SITE_IMPORT_SETTLE_MINUTES` | `10` | 10 — attesa prima di leggere una riga (il sito la completa in piu' transazioni) |
| `SITE_IMPORT_BATCH` | `100` | 100 |
| `SITE_IMPORT_MAX_ATTEMPTS` | `5` | 5 |
| `SITE_IMPORT_FOLLOWUP_MAX_AGE_HOURS` | `72` | 72 — task "Contattare proprietario" solo per stime recenti |
| `SITE_PDF_MATCH_WINDOW_HOURS` | `48` | 48 — il PDF e' accettato solo se il commit cade fra -1h e +48h dalla stima |

Le variabili `SITE_*` vanno **solo** sul cron: il servizio web non legge il sito.

Sola lettura sul DB del sito: la impone la sessione
(`default_transaction_read_only=on`, verificata con `SHOW` prima di ogni query)
e il codice emette solo `SELECT`. Un ruolo dedicato in sola lettura sarebbe una
garanzia in piu', ma crearlo e' un'operazione sul database del sito: **solo**
con autorizzazione del titolare, non necessaria per partire.

## 6. Primo avvio, in quest'ordine (Shell del cron job)

1. `python3 run_site_import_cron.py --dry-run`: conta soltanto, non scrive
   nulla. Atteso: `site_import status=dry_run stime_to_process=N dettagliate_to_process=M`.
2. Storico a lotti: `python3 run_site_import_cron.py --limit 500`, ripetuto
   finche' `pending_after_batch=0`. Ogni lotto e' riprendibile: un'interruzione
   non perde nulla e non crea doppioni.
3. Attivare la pianificazione `*/5 * * * *`.
4. Controllo: `SELECT status, count(*) FROM site_import_records GROUP BY 1;`
   - `partial` / `failed`: si ritentano da soli fino a `SITE_IMPORT_MAX_ATTEMPTS`;
   - `orphan`: dettagliata senza la sua stima nel CRM (attende);
   - `conflict`: l'ID esiste gia' nel CRM e non e' stato importato: **mai**
     sovrascritto, va esaminato a mano;
   - PDF: `SELECT status, last_error, count(*) FROM stima_pdf_artifacts GROUP BY 1,2;`
     (`failed` con `missing`/`unverified`/`invalid` = l'archivio non ha il
     PDF originale di quella stima: segnalato, mai rigenerato).

## 7. Esito del cron e log

Una riga per giro, senza dati personali ne' credenziali:

    site_import status=completed stime_imported=.. dettagliate_imported=.. partial=.. orphan=.. conflict=.. failed=.. skipped_already=.. pdf_ready=.. pdf_missing=.. agenda_requests=.. pending_after_batch=.. duration_ms=..

Codici: `0` completato (o saltato perche' un altro giro era in corso), `2`
record falliti/parziali (ripresi al giro dopo), `1` configurazione o guardia:
nulla importato.

Cosa **non** fa il cron: non scrive sul sito, non manda email/WhatsApp, non
accoda messaggi, non attiva sequenze o follow-up commerciali, non rigenera PDF.
Gli altri cron del CRM (`run_communication_dispatch_cron.py`,
`run_followup_p18d_cron.py`, ...) **non** vanno pianificati sul CRM PROD finche'
testi e consensi non sono approvati.
