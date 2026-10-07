# PERTINENZE-1 — runbook TEST: migration 089

Solo **TEST** (`stima360_db_test`, servizio Render TEST). PROD esclusa: il runner rifiuta qualunque database che non sia un TEST.

La 089 è additiva: due colonne su `properties` (`is_pertinenza` con default `FALSE`, `pertinenza_kind`), un CHECK sulle sole righe nuove e un indice parziale piccolo. Non fa nessun backfill e nessuna riga esistente cambia.

**Prima che la 089 sia applicata,** il codice di questa fase funziona come prima:
- nessuna pertinenza «da collegare»;
- una pertinenza **dichiarata** dal foglio (con il tipo, o «da collegare dopo») viene rifiutata con un messaggio leggibile («aggiornamento del database in corso»).

Si sistema applicando la 089.

> Stato al momento della consegna: **non eseguito**. Da qui non c'è accesso a Render.

## 0. Commit del servizio

```bash
echo "commit in esecuzione: $RENDER_GIT_COMMIT"
echo "database: $DB_NAME"
cd /opt/render/project/src 2>/dev/null || true
ls migrations/089_pertinenze_1_unit_nature.sql
```

**Atteso:**
- il commit di PERTINENZE-1 o uno successivo su `core-0.1-test`;
- `DB_NAME` = `stima360_db_test`;
- il file presente.

## 1. Fotografia prima (sola lettura)

```bash
cat > /tmp/pe1_check.sql <<'SQL'
BEGIN READ ONLY;
SELECT current_database() AS database;
SELECT version, applied_at, rolled_back_at FROM schema_migrations WHERE version >= '087' ORDER BY version;
SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns
 WHERE table_schema = 'public' AND table_name = 'properties' AND column_name IN ('is_pertinenza', 'pertinenza_kind');
SELECT conname FROM pg_constraint WHERE conrelid = 'properties'::regclass AND conname = 'properties_pertinenza_kind_chk';
SELECT indexname FROM pg_indexes WHERE tablename = 'properties' AND indexname = 'idx_properties_building_pertinenze';
-- informativo: pertinenze gia' collegate (restano tali, nessuna modifica)
SELECT count(*) AS pertinenze_collegate FROM properties WHERE parent_property_id IS NOT NULL;
ROLLBACK;
SQL
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 -f /tmp/pe1_check.sql
```

**Atteso:**
- 087 e 088 applicate, 089 assente;
- nessuna colonna, CHECK o indice di quelli sopra.

## 2. Stato del runner

```bash
python scripts/p26_migrate.py status --operator "giorgio.larasa"
```

**Atteso:** solo `pending      089_pertinenze_1_unit_nature [transactional]`, nessuna riga `PROBLEM`. **Altrimenti fermarsi.**

## 3. Applicazione

```bash
python scripts/p26_migrate.py apply --operator "giorgio.larasa"
echo "exit: $?"
python scripts/p26_migrate.py status --operator "giorgio.larasa"
```

**Atteso:** exit 0, poi 089 `applied` e nessuna `pending`.

## 4. Fotografia dopo (sola lettura)

```bash
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 -f /tmp/pe1_check.sql
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 \
  -c "BEGIN READ ONLY; SELECT count(*) FILTER (WHERE is_pertinenza) AS marcate, count(*) FILTER (WHERE pertinenza_kind IS NOT NULL) AS con_tipo FROM properties; ROLLBACK;"
```

**Atteso:**
- `is_pertinenza boolean NOT NULL default false` e `pertinenza_kind character varying`;
- il CHECK e l'indice presenti;
- `marcate = 0` e `con_tipo = 0` (nessun backfill);
- il conteggio delle pertinenze collegate invariato.

## 5. Collaudo live (Shell del CRM su TEST)

1. Su una palazzina, **«+ Pertinenza»**: un posto auto «Da collegare dopo». Il riepilogo diventa «… + 1 pertinenza (1 da collegare)» e il pannello «Pertinenze» la elenca fra le «Da collegare».
2. **«Collega a…»** → un appartamento della palazzina. Nel pannello passa fra le «Collegate» e l'edificio non cambia.
3. Dalla scheda dell'appartamento, **«+ Aggiungi pertinenza»**:
   - Cantina, 6,25 m², **Da verificare** → accessorio «Da chiarire»;
   - Box, 18,5 m², **Sì** → scheda autonoma collegata, con il tipo «Garage / box».
4. **«Chiarisci › È separata»** sulla cantina → unità autonoma. L'accessorio sparisce e la scheda nuova dice «Nata dall'accessorio «Cantina»».
5. Dalla scheda della pertinenza, **«Scollega da IMM-…»** in due tocchi:
   - resta «Pertinenza autonoma · Da collegare»;
   - proprietari, documenti ed edificio sono invariati.
6. Smartphone: gli stessi passi a 390 px, senza scorrimento orizzontale.

## Rientro (solo se serve)

La down si **ferma** se esiste una scheda marcata pertinenza o con un tipo di pertinenza: senza quel dato, una pertinenza da collegare tornerebbe a contare come principale. Il runner non ha un comando `down`: la down si esegue a mano solo su decisione esplicita.
