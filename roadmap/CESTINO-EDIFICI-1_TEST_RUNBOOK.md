# CESTINO-EDIFICI-1 — runbook TEST: migration 089 + 090 + 091 e collaudi live E/F/G

> **AGGIORNAMENTO COLLAUDO-FINALE-A-H (07/10/2026).** Le migration 089, 090, 091 e 092 sono **applicate** su `stima360_db_test` (comunicazione di Giorgio, verifica SQL riuscita). Il collaudo live è stato eseguito: esiti PASS / FAIL / NON VERIFICATO in `roadmap/COLLAUDO-FINALE-A-H_REPORT.md`. Le righe «non applicata» / «pendente» qui sotto descrivevano lo stato alla consegna e sono state aggiornate.

Solo **TEST** (`stima360_db_test`, servizio Render TEST). PROD esclusa: il runner rifiuta qualunque database che non sia un TEST.

## Stato di partenza

| Cosa | Stato |
|---|---|
| Migration applicate | fino alla **088** |
| **089** (pertinenze, FASE E) | **applicata** su `stima360_db_test` (comunicazione di Giorgio, verifica SQL riuscita) |
| **090** (Cestino Contatti, FASE F) | **applicata** su `stima360_db_test` (comunicazione di Giorgio, verifica SQL riuscita) |
| **091** (Cestino Edifici, FASE G) | **applicata** su `stima360_db_test` (comunicazione di Giorgio, verifica SQL riuscita) |
| Collaudi live FASE E, F e G | **eseguito** il 07/10/2026: esiti in `roadmap/COLLAUDO-FINALE-A-H_REPORT.md` |

Il runner applica le pendenti **in ordine**: un solo `apply` porta la 089, poi la 090, poi la 091. Sono additive. La 091 estende il CHECK del registro scritto dalla 090 (`'property','contact'` → `'property','contact','building'`), quindi va **dopo** la 090: l'ordine del runner lo garantisce. Nessun backfill, nessuna riga esistente cambia valore.

Prima dell'applicazione il codice nuovo funziona come prima:
- lista Edifici, candidati della creazione guidata e scheda edificio sono invariati (i filtri leggono `deleted_at` via `to_jsonb`, quindi valgono anche senza la colonna);
- «Elimina…» dell'edificio e la scheda «Edifici» del Cestino rispondono **503 `TRASH_NOT_INSTALLED`**, con un messaggio leggibile che nomina la 091.

> Stato al momento della consegna: **non eseguito**. Da qui non c'è accesso a Render.

## 0. Commit del servizio

```bash
echo "commit in esecuzione: $RENDER_GIT_COMMIT"
echo "database: $DB_NAME"
cd /opt/render/project/src 2>/dev/null || true
ls migrations/089_pertinenze_1_unit_nature.sql \
   migrations/090_cestino_contatti_1_contact_trash.sql \
   migrations/091_cestino_edifici_1_building_trash.sql
```

**Atteso:**
- il commit di CESTINO-EDIFICI-1 o uno successivo su `core-0.1-test`;
- `DB_NAME` = `stima360_db_test`;
- i tre file presenti.

## 1. Fotografia prima (sola lettura)

```bash
cat > /tmp/ce1_check.sql <<'SQL'
BEGIN READ ONLY;
SELECT current_database() AS database;
SELECT version, applied_at, rolled_back_at FROM schema_migrations WHERE version >= '087' ORDER BY version;
SELECT table_name, column_name, is_nullable FROM information_schema.columns
 WHERE table_schema = 'public'
   AND ((table_name = 'properties' AND column_name IN ('is_pertinenza', 'pertinenza_kind'))
     OR (table_name IN ('contacts', 'buildings') AND column_name IN ('deleted_at', 'deleted_by_user_id', 'deleted_reason')))
 ORDER BY 1, 2;
SELECT pg_get_constraintdef(oid) AS registro FROM pg_constraint WHERE conname = 'record_lifecycle_events_entity_chk';
SELECT count(*) AS guardie_contatti FROM pg_trigger WHERE tgname LIKE 'trg_%_contact_trash_%' OR tgname = 'trg_contacts_trash_freeze';
SELECT count(*) AS guardie_edifici FROM pg_trigger WHERE tgname IN ('trg_properties_building_trash_guard', 'trg_buildings_trash_freeze');
SELECT count(*) AS edifici, count(*) FILTER (WHERE archived_at IS NOT NULL) AS edifici_archiviati FROM buildings;
SELECT count(*) AS unita_in_edificio FROM properties WHERE building_id IS NOT NULL;
SELECT entity_type, count(*) AS eventi FROM record_lifecycle_events GROUP BY 1 ORDER BY 1;
ROLLBACK;
SQL
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 -f /tmp/ce1_check.sql
```

**Atteso:**
- 087 e 088 applicate; 089, 090 e 091 assenti;
- nessuna delle otto colonne;
- il registro ammette solo `('property')`;
- `guardie_contatti = 0`, `guardie_edifici = 0`;
- eventi solo di `property` (o nessuno).

Annotare `edifici`, `edifici_archiviati` e `unita_in_edificio`.

## 2. Stato del runner

```bash
python scripts/p26_migrate.py status --operator "giorgio.larasa"
```

**Atteso:** esattamente tre righe `pending` e nessuna riga `PROBLEM`. **Altrimenti fermarsi.**
- `089_pertinenze_1_unit_nature [transactional]`
- `090_cestino_contatti_1_contact_trash [transactional]`
- `091_cestino_edifici_1_building_trash [transactional]`

## 3. Applicazione

```bash
python scripts/p26_migrate.py apply --operator "giorgio.larasa"
echo "exit: $?"
python scripts/p26_migrate.py status --operator "giorgio.larasa"
```

**Atteso:** exit 0, poi 089, 090 e 091 `applied` e nessuna `pending`. Ogni migration ha la sua transazione. Se una fallisse, le precedenti restano applicate e quella fallita non lascia nulla a metà: fermarsi e riportare l'errore.

La 091 termina con una verifica: colonne nullable, nessun edificio già nel Cestino, i due trigger presenti. Se la verifica non torna, la 091 si annulla da sola.

## 4. Fotografia dopo (sola lettura)

```bash
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 -f /tmp/ce1_check.sql
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 \
  -c "BEGIN READ ONLY; SELECT count(*) FILTER (WHERE deleted_at IS NOT NULL) AS edifici_nel_cestino FROM buildings; SELECT count(*) FILTER (WHERE deleted_at IS NOT NULL) AS contatti_nel_cestino FROM contacts; SELECT count(*) FILTER (WHERE is_pertinenza) AS pertinenze_marcate FROM properties; ROLLBACK;"
```

**Atteso:**
- le otto colonne presenti e nullable, tranne `is_pertinenza`, che è NOT NULL con default `false`;
- il registro ammette `('property', 'contact', 'building')`;
- `guardie_contatti = 15` (9 collegamenti, 5 riaperture, 1 congelamento), `guardie_edifici = 2`;
- `edifici`, `edifici_archiviati` e `unita_in_edificio` invariati;
- `edifici_nel_cestino = 0`, `contatti_nel_cestino = 0`, `pertinenze_marcate = 0`;
- eventi `contact` e `building` assenti.

## 5. Collaudo live FASE G (Shell del CRM su TEST)

Usare solo edifici creati per la prova, con nome «Prova Cestino G …». Su desktop e poi su smartphone (390 px).

1. **Edificio vuoto**, come titolare:
   - Edifici › «+ Nuovo» (o dalla creazione guidata): «Prova Cestino G 1», Comune e via di prova, nessuna unità.
   - Nella scheda: **«Elimina…»**. Il foglio dice «L'edificio è vuoto» e mostra i 5 motivi.
   - Scegliere «Creato per errore», con una nota → **Sposta nel Cestino**.
   - Atteso: toast «Edificio spostato nel Cestino», ritorno alla lista Edifici.
2. **Invisibilità.** L'edificio non compare:
   - nella lista Edifici (anche cercandolo per via o nome);
   - fra i candidati della creazione guidata (Immobili › Censimento › «+ Nuovo», stessa via e civico, percorso «unità singola»);
   - fra le «palazzine simili» quando si crea un edificio con la stessa via.
3. **Cestino › Edifici** (`#/cestino/edifici`):
   - la card mostra nome, via, quando, chi, motivo con nota e unità dichiarate;
   - «Apri scheda» porta alla scheda con il riquadro «Nel Cestino»: niente «Modifica palazzina», niente «Elimina…», niente «+ Aggiungi unità»;
   - **Ripristina** → toast «Edificio ripristinato»; l'edificio torna in lista con lo **stesso numero** e torna fra i candidati della creazione guidata.
4. **Blocco con collegamenti:**
   - «Prova Cestino G 2» con un appartamento, una pertinenza (garage) e un immobile «intero stabile» di prova;
   - «Elimina…» → «Non si può spostare nel Cestino», con il conteggio («3 unità collegate (3 attive)») e una voce cliccabile per ogni unità; la conferma resta disabilitata;
   - il tocco su una voce apre la scheda dell'unità;
   - archiviare l'appartamento e spostare nel Cestino Immobili l'«intero stabile» dalla loro scheda: «Elimina…» dell'edificio resta **bloccato** e il testo dice «archiviata» e «nel Cestino Immobili» (la voce nel Cestino porta a `#/cestino`);
   - verificare che **nessuna** unità abbia cambiato edificio.
5. **Navigazione edificio → unità:** dalla scheda di «Prova Cestino G 2», il tocco su una riga apre la scheda dell'unità; «← Edifici» e il collegamento all'edificio nella scheda dell'unità riportano indietro.
6. **Agente:**
   - come agente, spostare nel Cestino un edificio vuoto di prova («Prova Cestino G 3»): si può;
   - nel Cestino › Edifici l'agente vede solo i suoi;
   - un edificio spostato dal titolare, aperto dall'agente, mostra «Può ripristinarlo chi lo ha spostato o un amministratore.» e nessun bottone.
7. **Possibili doppioni:**
   - spostare nel Cestino «Prova Cestino G 4» (via «Via Prova G», civico 1);
   - creare un nuovo edificio con la stessa via;
   - ripristinare G 4 → avviso «Possibili doppioni attivi» con il collegamento al nuovo e «Nessun edificio è stato unito o modificato.»; nessuno dei due è cambiato.
8. **Creazione guidata ripetuta:** non serve provocarla a mano (è un retry di rete con la stessa chiave). È coperta in locale; dal vivo basta che il passo 2 non mostri l'edificio.
9. **Smartphone (390 px):**
   - il foglio «Elimina…» è un bottom-sheet, le voci del blocco vanno a capo;
   - Cestino › Edifici e scheda nel Cestino senza scorrimento orizzontale;
   - bottoni toccabili.

Verifica finale, in sola lettura:

```bash
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 \
  -c "BEGIN READ ONLY; SELECT entity_id, action, reason_code, occurred_at FROM record_lifecycle_events WHERE entity_type = 'building' ORDER BY id; SELECT id, name, deleted_at, deleted_reason FROM buildings WHERE name LIKE 'Prova Cestino G%' ORDER BY id; ROLLBACK;"
```

**Atteso:**
- una riga `trash` e una `restore` per ogni prova, in ordine;
- nessuna riga `trash` per «Prova Cestino G 2»;
- gli edifici ripristinati con `deleted_at` vuoto e lo stesso id di prima.

## 6. Collaudi live FASE E e FASE F

Invariati:
- FASE E (pertinenze): `roadmap/PERTINENZE-1_TEST_RUNBOOK.md`, §5;
- FASE F (Cestino Contatti): `roadmap/CESTINO-CONTATTI-1_TEST_RUNBOOK.md`, §5.

Si possono fare nello stesso giro, dopo §4 di questo runbook (che applica anche 089 e 090).

## 7. Certificatore anonimo (facoltativo)

`scripts/p26_6_live_cert.py` ora prova anche le tre rotte nuove senza sessione (`GET .../deletion-check`, `POST .../trash`, `POST .../restore`): atteso 401/403, mai 200 né 5xx.

## Rientro (solo se serve, su decisione esplicita)

Il runner non ha un comando `down`. Le down vanno in ordine inverso: 091, poi 090, poi 089.

- **091 down:** si **ferma** se un edificio è nel Cestino o se il registro contiene eventi di edifici. Il registro è append-only e non si riscrive. Dopo il collaudo di §5 la down quindi non è più applicabile così com'è, ed è voluto: quegli eventi sono la traccia del collaudo. Se applicabile, toglie i due trigger, riporta il CHECK del registro a `('property', 'contact')` e toglie le tre colonne.
- **090 down** e **089 down:** vedi i runbook di CESTINO-CONTATTI-1 e PERTINENZE-1.
