# C3.5 — Procedura di rilascio e di rollback

Prova del consenso marketing scritta con ogni richiesta di valutazione: scelta,
data del server, testo della casella e impronta della Privacy Policy, conservati
solo se coincidono con le versioni ufficiali conservate sul server.

Questo documento NON è stato eseguito. Ogni passo richiede l'autorizzazione del
titolare. Nessuna credenziale, URL di database o valore di `.env` va scritto qui
o nei comandi salvati: si usano variabili d'ambiente già presenti nella shell
del servizio.

## 0. Cosa cambia e dove

| Livello | Dove | Cosa | Verifica |
|---|---|---|---|
| Pagina | www.stima360.it `privacy.html` | Marketing «tramite email e WhatsApp» (tolto «telefono») | SHA-256 `675f07248319b51842be988167bf244d4a93a3d97c3b4a98fd91d5495c38b005` |
| Pagina | www.stima360.it `dati_personali.html` | Invia anche testo della casella e impronta di `privacy.html` | SHA-256 `837c9cc35c3509d23c1bfa306ce1d3daf6814742f09af648ebc0d6697fcd106f` |
| Backend del sito | branch `main` (servizio che risponde a `/api/salva_stima`) | `rilascio/main-salva_stima.patch`: in `main.py` 7 righe AGGIUNTE (0 tolte) dopo il commit della stima; logica nel modulo nuovo `site_consent_proof.py` + pacchetto `site_official/` | test `tests/test_c35_salva_stima_main_postgres.py` |
| DB del sito | database del servizio `main` | migrazione 112 (due colonne nullable di `stime`) | query del punto 3.4 |
| CRM TEST | branch `core-0.1-test` + DB CRM TEST | riconoscimento con la prova, 112 | suite C3.5 |
| CRM PROD | solo se già avviato con `scripts/crm_prod_migrate.py` | 112 | `status` del canale |

Versioni ufficiali (lato server, `site_official/versions/versions.json`):

* casella `casella-marketing-1` — «Acconsento a ricevere analisi di mercato e
  aggiornamenti da Stima360 tramite email e WhatsApp. (Facoltativo e revocabile
  in qualsiasi momento)», canali Email e WhatsApp;
* `privacy-675f0724` — accettata (nuova, allineata);
* `privacy-d615e05d` — NON accettata, conservata identica come documentazione
  storica (versione precedente, che citava anche il telefono).

Non cambiano: calcolo della valutazione, PDF, email e WhatsApp al cliente e
all'amministratore, redirect, stima dettagliata, caselle del modulo (la casella
marketing resta facoltativa). Nessun invio automatico viene attivato.

## 1. Prerequisiti

1. Autorizzazione scritta del titolare per ogni passo, nella finestra concordata.
2. `pg_dump`/`pg_restore` di versione **≥** a quella del server.
3. Annotare il commit attualmente in produzione su `main` (oggi `c3af7d3`) e
   quello del CRM TEST: sono i punti di ritorno del rollback.
4. Scaricare dall'hosting le pagine pubblicate oggi (`dati_personali.html`,
   `privacy.html`) e verificarne le impronte: devono essere `283b4564…` e
   `d615e05d…`. Se non coincidono, FERMARSI: il sito è diverso da quello
   preparato e la patch della pagina va rifatta su quello vero.

## 2. Backup (prima di tutto)

Per OGNI database coinvolto (DB del sito, CRM TEST, CRM PROD se esiste), dalla
shell del rispettivo servizio, con le variabili già configurate:

```sh
TS=$(date -u +%Y%m%dT%H%M%SZ)
pg_dump -Fc --no-owner -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" \
        -f "c35_${DB_NAME}_${TS}.dump"
pg_restore --list "c35_${DB_NAME}_${TS}.dump" | grep -c "TABLE DATA"   # deve essere > 0
sha256sum "c35_${DB_NAME}_${TS}.dump" > "c35_${DB_NAME}_${TS}.dump.sha256"
```

Copiare dump e impronta fuori dal server (archivio del titolare) e verificarne
l'impronta dopo la copia. Conservare anche le due pagine scaricate al punto 1.4.
Procedura di ripristino di riferimento: `docs/P26_BACKUP_RESTORE_TEST.md`.

## 3. Migrazione 112 (prima i CRM, poi il sito)

L'ordine conta: l'import del CRM copia le colonne comuni; se il sito avesse le
colonne prima del CRM, le prove scritte nel frattempo non arriverebbero.

### 3.1 CRM TEST — runner P26 (solo TEST)

```sh
python scripts/p26_migrate.py status --operator "<nome>"
python scripts/p26_migrate.py plan   --operator "<nome>"     # deve elencare SOLO 112
python scripts/p26_migrate.py apply  --operator "<nome>" \
       --baseline-fingerprint <sha256 del certificato> --baseline-artifact <reports/...json>
```

Seguire `docs/P26_1_MIGRATION_RUNBOOK_TEST.md`. Se `plan` mostra altre versioni
oltre alla 112, FERMARSI.

### 3.2 CRM PROD — solo se esiste

```sh
python scripts/crm_prod_migrate.py status  --database <nome digitato> --operator "<nome>"
python scripts/crm_prod_migrate.py upgrade --database <nome digitato> --operator "<nome>"
```

### 3.3 DB del sito — applicazione manuale guidata

Entrambi i runner rifiutano per costruzione i nomi del database del sito: la
112 si applica con `psql` in UNA transazione, con la verifica d'identità nella
stessa transazione (se il nome non è quello atteso, nulla viene scritto):

```sh
psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" \
     -v ON_ERROR_STOP=1 --single-transaction \
     -c "DO \$\$ BEGIN IF current_database() <> '<NOME ATTESO>' THEN RAISE EXCEPTION 'database sbagliato'; END IF; END \$\$;" \
     -f migrations/112_stime_consenso_marketing_prova.sql
```

### 3.4 Verifica su ogni database

```sql
SELECT column_name, data_type, is_nullable FROM information_schema.columns
 WHERE table_name = 'stime' AND column_name LIKE 'consenso_marketing%' ORDER BY 1;
-- attese 4 righe: consenso_marketing, consenso_marketing_at,
-- consenso_marketing_privacy_sha256 (character, YES), consenso_marketing_testo (text, YES)
SELECT conname FROM pg_constraint WHERE conname LIKE 'stime_consenso_marketing_%_chk';
-- attesi i due vincoli
SELECT count(*) FROM stime WHERE consenso_marketing_testo IS NOT NULL;   -- 0: nessun dato storico toccato
```

## 4. Informative nel CRM (Super Admin, una volta)

Senza queste due righe il CRM conserva le prove ma mostra «prova incompleta»
dicendo quale manca, e non autorizza nulla.

1. Marketing: testo ESATTAMENTE uguale alla casella (vedi §0), canali **Email** e
   **WhatsApp** (mai SMS né telefonate), contesto che nomini `stima360.it`
   (es. «Sito www.stima360.it – casella marketing del modulo di valutazione»).
2. Privacy (`privacy_terms`): contenuto = il file `privacy.html` nuovo, integrale.
   Verificare che l'impronta mostrata dal CRM sia `675f0724…`; i ritorni a capo
   CRLF di un incolla da browser sono tollerati.

Le informative sono un registro: non si cancellano; se servisse, si chiudono.

## 5. Backend

1. CRM TEST: pubblicare il commit C3.5 su `core-0.1-test` (deploy automatico del
   servizio TEST).
2. Backend del sito: branch dedicata da `main` con `rilascio/main-salva_stima.patch`
   (`git apply --check` poi `git apply`), pull request verso `main`, test
   `tests/test_c35_salva_stima_main_postgres.py` (con `P29_TEST_DSN` usa-e-getta e
   `C35_MIGRATIONS_DIR` che punti a una cartella con 018/019/112), merge, deploy.
   Il backend nuovo funziona anche con le pagine vecchie e anche senza la 112.

## 6. Pagine del sito (per ultime)

1. Pubblicare `privacy.html` (nuova), poi `dati_personali.html` (nuova).
2. Scaricarle dal sito pubblico e verificare le impronte del §0. Se l'hosting
   altera i byte di `privacy.html` (impronta diversa), ripristinare subito le
   pagine (§8.1): il backend non conserverebbe la prova (nessun danno, ma
   nessuna prova).

## 7. Verifica finale (con dati del titolare, nessun cliente)

1. Una richiesta dal sito con i dati del titolare e la casella spuntata: le
   normali email/WhatsApp della valutazione arrivano come sempre.
2. DB del sito: la riga ha scelta, data, `consenso_marketing_testo` uguale alla
   casella e `consenso_marketing_privacy_sha256 = 675f0724…`.
3. Dopo il giro di import, scheda del contatto nel CRM: Email e WhatsApp
   «Concesso dal sito», prova «testo della casella e informativa privacy
   registrati dal server con la richiesta»; SMS e telefonate non autorizzati.
4. Revocare un canale dalla scheda: resta revocato ai giri successivi.
5. Log del backend: nessun `public_stima_marketing_proof_not_official`.

## 8. Rollback (ogni livello si torna indietro da solo)

Verificato dai test: backend nuovo senza 112 e con pagine vecchie; backend
attuale (`main` e `core-0.1-test`) con la 112 e con la pagina nuova; 112 giù e
di nuovo su senza perdere le altre colonne.

1. **Pagine**: ripubblicare le due pagine salvate al §1.4 (impronte `283b4564…`,
   `d615e05d…`). Il backend nuovo continua a funzionare (campi assenti → prova
   assente).
2. **Backend del sito**: ridistribuire il commit annotato (`c3af7d3`), oppure `git apply -R rilascio/main-salva_stima.patch` (verificato: `main.py` torna byte per byte all'originale, SHA-256 `b195b8f2…`, e i file nuovi spariscono). Il
   backend attuale ignora i campi nuovi e funziona con la 112.
3. **CRM TEST**: ridistribuire il commit precedente. Le prove già scritte restano
   nella tabella e non autorizzano nulla senza il codice C3.5.
4. **DB del sito**: di norma NON serve (colonne nullable, ignorate dal backend
   vecchio). Solo se necessario, dopo un nuovo backup, con la stessa guardia del
   §3.3: `-f migrations/112_stime_consenso_marketing_prova_down.sql`
   (CANCELLA le prove registrate dopo il rilascio).
5. **DB CRM**: il registro P26 è forward-only; le colonne restano (innocue).
6. **Informative**: non si cancellano; se necessario si chiudono dal Super Admin.
7. **Ripristino da backup**: solo per un danno ai dati, con
   `docs/P26_BACKUP_RESTORE_TEST.md`.

## 9. Contatti storici

Le richieste precedenti al rilascio non hanno testo né impronta: restano alle
regole documentali di C3.4 (copia firmata, backup datato, storico delle
pubblicazioni). Nessuna prova viene ricostruita.
