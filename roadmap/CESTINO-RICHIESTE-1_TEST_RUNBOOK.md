# CESTINO-RICHIESTE-1 — runbook TEST: migration 089 → 092 e collaudi live E/F/G/H

Solo **TEST** (`stima360_db_test`, servizio Render TEST). PROD esclusa: il runner rifiuta qualunque database che non sia un TEST. Sito pubblico non coinvolto.

## Stato di partenza

| Cosa | Stato |
|---|---|
| Migration applicate | fino alla **088** |
| **089** `089_pertinenze_1_unit_nature` (FASE E) | **non applicata** |
| **090** `090_cestino_contatti_1_contact_trash` (FASE F) | **non applicata** |
| **091** `091_cestino_edifici_1_building_trash` (FASE G) | **non applicata** |
| **092** `092_cestino_richieste_1_buy_request_trash` (FASE H) | **non applicata** |
| Collaudi live FASE E, F, G e H | **pendenti** |

**Ordine delle migration pendenti:** 089 → 090 → 091 → 092. Il runner le applica in quest'ordine con un solo `apply`, ciascuna nella sua transazione. Sono additive.

- La 091 estende il CHECK del registro scritto dalla 090.
- La 092 lo estende ancora (`'property','contact','building','buy_request'`).
- Quindi l'ordine conta, e il runner lo garantisce.
- Nessun backfill. Nessuna riga esistente cambia valore.

Prima dell'applicazione il codice nuovo funziona come prima:
- elenchi Acquirenti, ricerca, abbinamenti, FLOW, «Oggi», pressione acquirenti e vendite invisibili sono invariati, perché i filtri leggono `deleted_at` via `to_jsonb` e valgono anche senza la colonna;
- «Elimina…» della richiesta e la scheda «Richieste» del Cestino rispondono **503 `TRASH_NOT_INSTALLED`**, con un messaggio leggibile che nomina la 092.

> Stato al momento della consegna: **non eseguito**. Da qui non c'è accesso a Render.

## 0. Commit del servizio

```bash
echo "commit in esecuzione: $RENDER_GIT_COMMIT"
echo "database: $DB_NAME"
cd /opt/render/project/src 2>/dev/null || true
ls migrations/089_pertinenze_1_unit_nature.sql \
   migrations/090_cestino_contatti_1_contact_trash.sql \
   migrations/091_cestino_edifici_1_building_trash.sql \
   migrations/092_cestino_richieste_1_buy_request_trash.sql
```

**Atteso:**
- il commit di CESTINO-RICHIESTE-1 o uno successivo su `core-0.1-test`;
- `DB_NAME` = `stima360_db_test`;
- i quattro file presenti.

## 1. Fotografia prima (sola lettura)

```bash
cat > /tmp/ch1_check.sql <<'SQL'
BEGIN READ ONLY;
SELECT current_database() AS database;
SELECT version, applied_at, rolled_back_at FROM schema_migrations WHERE version >= '087' ORDER BY version;
SELECT table_name, column_name, is_nullable FROM information_schema.columns
 WHERE table_schema = 'public'
   AND ((table_name = 'properties' AND column_name IN ('is_pertinenza', 'pertinenza_kind'))
     OR (table_name IN ('contacts', 'buildings', 'buy_requests')
         AND column_name IN ('deleted_at', 'deleted_by_user_id', 'deleted_reason')))
 ORDER BY 1, 2;
SELECT pg_get_constraintdef(oid) AS registro FROM pg_constraint WHERE conname = 'record_lifecycle_events_entity_chk';
SELECT count(*) AS guardie_contatti FROM pg_trigger WHERE tgname LIKE 'trg_%_contact_trash_%' OR tgname = 'trg_contacts_trash_freeze';
SELECT count(*) AS guardie_edifici FROM pg_trigger WHERE tgname IN ('trg_properties_building_trash_guard', 'trg_buildings_trash_freeze');
SELECT count(*) AS guardie_richieste FROM pg_trigger WHERE tgname LIKE 'trg_%_buy_trash_guard' OR tgname = 'trg_buy_requests_trash_freeze';
SELECT count(*) AS richieste, count(*) FILTER (WHERE archived_at IS NOT NULL) AS archiviate,
       count(*) FILTER (WHERE status IN ('draft', 'active', 'paused')) AS aperte FROM buy_requests;
SELECT count(*) AS abbinamenti FROM matches;
SELECT entity_type, count(*) AS eventi FROM record_lifecycle_events GROUP BY 1 ORDER BY 1;
ROLLBACK;
SQL
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 -f /tmp/ch1_check.sql
```

**Atteso:**
- 087 e 088 applicate; 089, 090, 091 e 092 assenti;
- nessuna delle undici colonne;
- il registro ammette solo `('property')`;
- `guardie_contatti = 0`, `guardie_edifici = 0`, `guardie_richieste = 0`;
- eventi solo di `property` (o nessuno).

Annotare `richieste`, `archiviate`, `aperte` e `abbinamenti`.

## 2. Stato del runner

```bash
python scripts/p26_migrate.py status --operator "giorgio.larasa"
```

**Atteso:** esattamente quattro righe `pending`, in quest'ordine, e nessuna riga `PROBLEM`. **Altrimenti fermarsi.**
- `089_pertinenze_1_unit_nature [transactional]`
- `090_cestino_contatti_1_contact_trash [transactional]`
- `091_cestino_edifici_1_building_trash [transactional]`
- `092_cestino_richieste_1_buy_request_trash [transactional]`

## 3. Applicazione

```bash
python scripts/p26_migrate.py apply --operator "giorgio.larasa"
echo "exit: $?"
python scripts/p26_migrate.py status --operator "giorgio.larasa"
```

**Atteso:** exit 0, poi 089, 090, 091 e 092 `applied` e nessuna `pending`. Se una fallisse, le precedenti restano applicate e quella fallita non lascia nulla a metà: fermarsi e riportare l'errore.

La 092 termina con una verifica: colonne nullable, nessuna richiesta già nel Cestino, le 12 guardie dei collegamenti e il congelamento presenti. Se la verifica non torna, la 092 si annulla da sola.

## 4. Fotografia dopo (sola lettura)

```bash
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 -f /tmp/ch1_check.sql
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 \
  -c "BEGIN READ ONLY; SELECT count(*) FILTER (WHERE deleted_at IS NOT NULL) AS richieste_nel_cestino FROM buy_requests; SELECT count(*) FILTER (WHERE deleted_at IS NOT NULL) AS edifici_nel_cestino FROM buildings; SELECT count(*) FILTER (WHERE deleted_at IS NOT NULL) AS contatti_nel_cestino FROM contacts; ROLLBACK;"
```

**Atteso:**
- le undici colonne presenti e nullable, tranne `is_pertinenza`, che è NOT NULL con default `false`;
- il registro ammette `('property', 'contact', 'building', 'buy_request')`;
- `guardie_contatti = 15`, `guardie_edifici = 2`, `guardie_richieste = 13` (12 collegamenti + 1 congelamento);
- `richieste`, `archiviate`, `aperte` e `abbinamenti` invariati;
- nulla nel Cestino; eventi `contact`, `building` e `buy_request` assenti.

## 5. Collaudo live FASE H (Shell del CRM su TEST)

Usare solo dati creati per la prova: contatto «Prova Cestino H …» con richieste «Prova H …». Su desktop e poi su smartphone (390 px).

1. **Richiesta semplice**, come titolare:
   - contatto «Prova Cestino H 1» con due richieste attive, «Prova H 1A» e «Prova H 1B»;
   - su «Prova H 1A»: «Ricalcola abbinamenti» (facoltativo, per avere un abbinamento), poi **«Elimina…»**;
   - il foglio mostra i 5 motivi e gli effetti: stato invariato, abbinamenti che escono dalle liste, contatto attivo con l'altra richiesta, comunicazioni non toccate;
   - «Creato per errore», con una nota → **Sposta nel Cestino**;
   - atteso: toast «Richiesta spostata nel Cestino», ritorno ad Acquirenti.
2. **Invisibilità.** «Prova H 1A» non compare:
   - in Acquirenti (anche cercandola);
   - nella ricerca globale;
   - nella tab Richieste del contatto (che mostra ancora «Prova H 1B»);
   - in Abbinamenti e in «Oggi».
   Il contatto resta attivo e «Prova H 1B» si modifica normalmente.
3. **Cestino › Richieste** (`#/cestino/richieste`):
   - la card mostra titolo, budget, contatto, quando, chi, motivo con nota e stato;
   - «Apri scheda» → riquadro «Nel Cestino», nessun comando nell'intestazione né nelle tab (criteri, abbinamenti, visite, proposte, task); dati e storico visibili;
   - **Ripristina** → toast «Richiesta ripristinata», avviso «Lo stesso contatto ha altre richieste aperte» con il collegamento a «Prova H 1B»; la richiesta torna in Acquirenti con lo **stesso numero** e lo stesso stato.
4. **Blocchi con collegamenti** (serve un immobile di prova attivo che abbini):
   - su «Prova H 2», tab Abbinamenti → «Ricalcola abbinamenti»; poi tab Proposte → «Nuova proposta», lasciata **in bozza**;
   - «Elimina…» → «Non si può spostare nel Cestino» con la voce «Proposta #… · in bozza» e «Apri la richiesta»; conferma disabilitata;
   - ritirare la proposta dalla tab Proposte, poi «Elimina…» di nuovo: ora il titolare può (lo storico «Proposte d'acquisto concluse» resta consultabile);
   - facoltativo: una visita futura da «Programma visita» blocca allo stesso modo, con il collegamento al giorno in Agenda.
   - Nota: «+ Nuovo task» nell'intestazione della richiesta crea un task del **contatto**, non collegato alla richiesta, e quindi non blocca. I task collegati (`buy_request_task_links`) nascono solo dall'API o dal vecchio BUY admin: il loro blocco è provato in locale.
5. **Storico e agente**, come agente:
   - su una richiesta con un esito registrato su un abbinamento: «Elimina…» → «Serve un amministratore», con l'elenco dello storico;
   - su una richiesta nuova senza storico: l'agente la sposta;
   - nel Cestino › Richieste l'agente vede solo le sue; su una richiesta spostata dal titolare vede «Può ripristinarla chi l'ha spostata o un amministratore.» e nessun bottone.
6. **Contatto nel Cestino:**
   - spostare nel Cestino «Prova H 3A» (unica richiesta del contatto «Prova Cestino H 3»), poi il contatto stesso: la richiesta nel Cestino non lo blocca;
   - «Ripristina» sulla richiesta → si ferma: «ripristina prima il contatto», con i collegamenti al contatto e al Cestino Contatti; nulla cambia;
   - ripristinare il contatto, poi la richiesta: ora torna.
7. **Nessun effetto dal ripristino:** prima e dopo un ripristino nessun messaggio nuovo nella tab Comunicazioni del contatto, nessun appuntamento nuovo in Agenda, nessun task riaperto.
8. **Smartphone (390 px):**
   - il foglio «Elimina…» è un bottom-sheet leggibile;
   - Cestino › Richieste e scheda nel Cestino senza scorrimento orizzontale;
   - bottoni toccabili.

Verifica finale, in sola lettura:

```bash
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 \
  -c "BEGIN READ ONLY; SELECT entity_id, action, reason_code, occurred_at FROM record_lifecycle_events WHERE entity_type = 'buy_request' ORDER BY id; SELECT id, title, status, deleted_at, deleted_reason FROM buy_requests WHERE title LIKE 'Prova H%' ORDER BY id; ROLLBACK;"
```

**Atteso:**
- una riga `trash` e una `restore` per ogni prova completata, in ordine;
- nessuna riga `trash` per le prove bloccate (finché bloccate);
- le richieste ripristinate con `deleted_at` vuoto, lo stesso id e lo stato di prima.

## 6. Collaudi live FASE E, F e G

Invariati, nello stesso giro dopo §4 (che applica anche 089, 090 e 091):
- FASE E (pertinenze): `roadmap/PERTINENZE-1_TEST_RUNBOOK.md`, §5;
- FASE F (Cestino Contatti): `roadmap/CESTINO-CONTATTI-1_TEST_RUNBOOK.md`, §5;
- FASE G (Cestino Edifici): `roadmap/CESTINO-EDIFICI-1_TEST_RUNBOOK.md`, §5.

## Rientro (solo se serve, su decisione esplicita)

Il runner non ha un comando `down`. Le down vanno in ordine inverso: 092, poi 091, 090, 089.

- **092 down:** si **ferma** se una richiesta è nel Cestino o se il registro contiene eventi di richieste. Il registro è append-only e non si riscrive. Dopo il collaudo di §5 la down quindi non è più applicabile così com'è, ed è voluto: quegli eventi sono la traccia del collaudo. Se applicabile, toglie le 12 guardie e il congelamento, riporta il CHECK del registro a `('property', 'contact', 'building')` e toglie le tre colonne.
- **091, 090, 089 down:** vedi i runbook di CESTINO-EDIFICI-1, CESTINO-CONTATTI-1 e PERTINENZE-1.
