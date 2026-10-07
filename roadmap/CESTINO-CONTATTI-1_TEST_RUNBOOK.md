# CESTINO-CONTATTI-1 — runbook TEST: migration 089 + 090 e collaudi live E/F

> **AGGIORNAMENTO COLLAUDO-FINALE-A-H (07/10/2026).** Le migration 089, 090, 091 e 092 sono **applicate** su `stima360_db_test` (comunicazione di Giorgio, verifica SQL riuscita). Il collaudo live è stato eseguito: esiti PASS / FAIL / NON VERIFICATO in `roadmap/COLLAUDO-FINALE-A-H_REPORT.md`. Le righe «non applicata» / «pendente» qui sotto descrivevano lo stato alla consegna e sono state aggiornate.

Solo **TEST** (`stima360_db_test`, servizio Render TEST). PROD esclusa: il runner rifiuta qualunque database che non sia un TEST.

## Stato di partenza

| Cosa | Stato |
|---|---|
| Migration applicate | fino alla **088** |
| **089** (pertinenze, FASE E) | **applicata** su `stima360_db_test` (comunicazione di Giorgio, verifica SQL riuscita) |
| **090** (Cestino contatti, FASE F) | **applicata** su `stima360_db_test` (comunicazione di Giorgio, verifica SQL riuscita) |
| Collaudi live FASE E e FASE F | **eseguito** il 07/10/2026: esiti in `roadmap/COLLAUDO-FINALE-A-H_REPORT.md` |

Il runner applica le pendenti **in ordine**: un solo `apply` porta la 089 e poi la 090. Sono additive e indipendenti. Nessun backfill, nessuna riga esistente cambia valore.

Prima dell'applicazione il codice nuovo funziona come prima: elenchi e schede invariati; «Elimina…» del contatto e la scheda «Contatti» del Cestino rispondono **503 `TRASH_NOT_INSTALLED`**, con un messaggio leggibile.

> Stato al momento della consegna: **non eseguito**. Da qui non c'è accesso a Render.

## 0. Commit del servizio

```bash
echo "commit in esecuzione: $RENDER_GIT_COMMIT"
echo "database: $DB_NAME"
cd /opt/render/project/src 2>/dev/null || true
ls migrations/089_pertinenze_1_unit_nature.sql migrations/090_cestino_contatti_1_contact_trash.sql
```

**Atteso:**
- il commit di CESTINO-CONTATTI-1 o uno successivo su `core-0.1-test`;
- `DB_NAME` = `stima360_db_test`;
- i due file presenti.

## 1. Fotografia prima (sola lettura)

```bash
cat > /tmp/cc1_check.sql <<'SQL'
BEGIN READ ONLY;
SELECT current_database() AS database;
SELECT version, applied_at, rolled_back_at FROM schema_migrations WHERE version >= '087' ORDER BY version;
SELECT table_name, column_name, is_nullable FROM information_schema.columns
 WHERE table_schema = 'public'
   AND ((table_name = 'properties' AND column_name IN ('is_pertinenza', 'pertinenza_kind'))
     OR (table_name = 'contacts' AND column_name IN ('deleted_at', 'deleted_by_user_id', 'deleted_reason')))
 ORDER BY 1, 2;
SELECT pg_get_constraintdef(oid) AS registro FROM pg_constraint WHERE conname = 'record_lifecycle_events_entity_chk';
SELECT count(*) AS guardie_contatti FROM pg_trigger WHERE tgname LIKE 'trg_%_contact_trash_%' OR tgname = 'trg_contacts_trash_freeze';
SELECT count(*) AS contatti, count(*) FILTER (WHERE status = 'archived') AS archiviati FROM contacts;
SELECT count(*) AS eventi_contatti FROM record_lifecycle_events WHERE entity_type = 'contact';
ROLLBACK;
SQL
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 -f /tmp/cc1_check.sql
```

**Atteso:**
- 087 e 088 applicate, 089 e 090 assenti;
- nessuna delle cinque colonne;
- il registro ammette solo `('property')`;
- `guardie_contatti = 0`;
- `eventi_contatti = 0`.

Annotare `contatti` e `archiviati`.

## 2. Stato del runner

```bash
python scripts/p26_migrate.py status --operator "giorgio.larasa"
```

**Atteso:** esattamente due righe `pending` e nessuna riga `PROBLEM`. **Altrimenti fermarsi.**
- `089_pertinenze_1_unit_nature [transactional]`
- `090_cestino_contatti_1_contact_trash [transactional]`

## 3. Applicazione

```bash
python scripts/p26_migrate.py apply --operator "giorgio.larasa"
echo "exit: $?"
python scripts/p26_migrate.py status --operator "giorgio.larasa"
```

**Atteso:** exit 0, poi 089 e 090 `applied` e nessuna `pending`. Ogni migration ha la sua transazione. Se la 090 fallisse, la 089 resta applicata e la 090 non lascia nulla a metà: fermarsi e riportare l'errore.

## 4. Fotografia dopo (sola lettura)

```bash
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 -f /tmp/cc1_check.sql
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 \
  -c "BEGIN READ ONLY; SELECT count(*) FILTER (WHERE deleted_at IS NOT NULL) AS nel_cestino FROM contacts; SELECT count(*) FILTER (WHERE is_pertinenza) AS pertinenze_marcate FROM properties; ROLLBACK;"
```

**Atteso:**
- le cinque colonne presenti e nullable, tranne `is_pertinenza`, che è NOT NULL con default `false`;
- il registro ammette `('property', 'contact')`;
- `guardie_contatti = 15`: 9 collegamenti, 5 riaperture, 1 congelamento;
- `contatti` e `archiviati` invariati;
- `eventi_contatti = 0`, `nel_cestino = 0`, `pertinenze_marcate = 0`.

## 5. Collaudo live FASE F (Shell del CRM su TEST)

Usare solo contatti creati per la prova: nome «Prova Cestino F …».

1. **Contatto semplice**, come titolare:
   - «+ Nuovo contatto» «Prova Cestino F 1», con un'email di prova.
   - Nella scheda: **«Elimina…»**. Il foglio mostra i motivi e la frase sulle comunicazioni.
   - Scegliere «Creato per errore», con una nota → **Sposta nel Cestino**.
   - Atteso: toast «Contatto spostato nel Cestino», ritorno all'elenco Contatti.
2. **Invisibilità.** Il contatto non compare:
   - nell'elenco Contatti;
   - nella ricerca globale (nome ed email);
   - nei selettori: nuovo appuntamento, nuova richiesta d'acquisto, collega proprietario.
3. **Cestino › Contatti:**
   - la card mostra quando, chi, il motivo, la nota e lo stato;
   - «Apri scheda» porta alla scheda con il riquadro «Nel Cestino» e nessun comando di modifica;
   - **Ripristina** → toast «Contatto ripristinato»; il contatto torna in elenco con lo stesso numero.
4. **Blocco con collegamento:**
   - su «Prova Cestino F 2» aprire un lead dalla tab Lead (o un task aperto);
   - «Elimina…» → «Non si può spostare nel Cestino» con la voce cliccabile; la conferma resta disabilitata;
   - chiudere il lead o il task dal suo posto, poi «Elimina…» di nuovo: ora si può.
5. **Storico e agente**, come agente assegnato:
   - su un suo contatto con un'attività registrata, «Elimina…» → «Serve un amministratore», con l'elenco dello storico;
   - come titolare lo stesso contatto si sposta, e la conferma dice che lo storico resta consultabile.
6. **Portale proprietario** (solo se c'è un contatto di prova con accesso attivo), come titolare:
   - «Elimina…» → blocco con **«Disattiva accesso»** → il controllo si ripete e il Cestino si può fare;
   - dopo il ripristino l'accesso **resta disattivato**.
7. **Possibili doppioni:**
   - spostare nel Cestino «Prova Cestino F 3» (email `f3@example.it`);
   - creare un nuovo contatto con la stessa email;
   - ripristinare F 3 → avviso «Possibili doppioni attivi» con il collegamento al nuovo; nessuno dei due è cambiato.
8. **Comunicazioni:** la tab di un contatto nel Cestino è in sola lettura e dice «automazioni sospese». Dopo il ripristino si riattivano solo a mano.
9. **Smartphone (390 px):**
   - il foglio «Elimina…» è un bottom-sheet;
   - pagina Cestino › Contatti e scheda nel Cestino senza scorrimento orizzontale;
   - bottoni toccabili.

Verifica finale, in sola lettura:

```bash
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 \
  -c "BEGIN READ ONLY; SELECT entity_id, action, reason_code, occurred_at FROM record_lifecycle_events WHERE entity_type = 'contact' ORDER BY id; ROLLBACK;"
```

**Atteso:** una riga `trash` e una `restore` per ogni prova, in ordine.

## 6. Collaudo live FASE E (pertinenze)

Invariato: `roadmap/PERTINENZE-1_TEST_RUNBOOK.md`, §5.

## Rientro (solo se serve, su decisione esplicita)

Il runner non ha un comando `down`.

- **090 down:** si **ferma** se un contatto è nel Cestino o se il registro contiene eventi di contatti. Il registro è append-only e non si riscrive. Dopo il collaudo di §5 la down quindi non è più applicabile così com'è, ed è voluto: quegli eventi sono la traccia del collaudo.
- **089 down:** si ferma con pertinenze marcate (vedi il runbook di PERTINENZE-1).
