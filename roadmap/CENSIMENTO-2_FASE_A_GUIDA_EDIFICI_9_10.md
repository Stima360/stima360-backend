# CENSIMENTO-2 Fase A (A4): guida alla riconciliazione degli edifici TEST 9 e 10

Data: 2026-10-12 · Codice di riferimento: `core-0.1-test` @ `36204da` + Fase A (locale, non pubblicata).

**Questa guida è in sola lettura.** Non crea, modifica, archivia o cancella unità e non contiene script di correzione. Ogni correzione si fa a mano dalla Shell, scheda per scheda, dopo una decisione esplicita.

## 0. Cosa sappiamo già (dalla fotografia del runbook, da riconfermare)

| Edificio | Unità dichiarate | Schede collegate | Note |
|---|---|---|---|
| 9 | 12 | 0 | Nessuna scheda: nulla da riconciliare, solo da censire. |
| 10 | 12 | 2 | Le 2 schede risultano **appartamenti**; le note parlano di **locali commerciali**. |

La migrazione 100 non ha scritto dati (nessun backfill): composizione per classe vuota («Bozza») e provenienze «Non indicata» su entrambe.

## 1. Fotografia in sola lettura (Shell Render TEST)

Tutto dentro `BEGIN TRANSACTION READ ONLY … ROLLBACK`: il database rifiuta qualunque scrittura.

```bash
cat > /tmp/a4_9_10.sql <<'SQL'
BEGIN TRANSACTION READ ONLY;
SELECT current_database() AS database;

-- 1. SITUAZIONE DICHIARATA
SELECT b.id, b.agency_id, b.name, b.address, b.civic_number, b.city, b.building_type,
       b.units_declared, b.units_declared_source, b.census_status,
       b.units_declared_residential, b.units_declared_commercial, b.units_declared_other, b.units_declared_pertinenze,
       b.cadastral_municipality_code, b.cadastral_section, b.cadastral_sheet, b.cadastral_parcel,
       to_jsonb(b)->>'deleted_at' AS nel_cestino, b.updated_at
  FROM buildings b WHERE b.id IN (9, 10) ORDER BY b.id;

-- 2. SCHEDE ESISTENTI (attive, archiviate e nel Cestino, per vederle tutte)
SELECT p.building_id, p.id, p.code, p.property_type, p.record_kind, p.commercial_status,
       p.floor, p.staircase, p.internal_number,
       p.surface_sqm, p.surface_source, p.cadastral_category, p.cadastral_category_source,
       p.cadastral_municipality_code, p.cadastral_section, p.cadastral_sheet, p.cadastral_parcel, p.cadastral_subunit,
       p.parent_property_id, to_jsonb(p)->>'is_pertinenza' AS pertinenza, to_jsonb(p)->>'pertinenza_kind' AS tipo_pertinenza,
       p.archived_at, to_jsonb(p)->>'deleted_at' AS nel_cestino,
       p.metadata->'census_batch' AS lotto, p.metadata->>'census_class' AS classe_esplicita,
       left(coalesce(p.internal_notes, ''), 200) AS note_interne, left(coalesce(p.public_notes, ''), 200) AS note_pubbliche,
       p.created_at, p.updated_at
  FROM properties p WHERE p.building_id IN (9, 10) ORDER BY p.building_id, p.id;

-- 3. COLLEGAMENTI di ogni scheda (devono restare intatti: contatti, richieste, documenti, storico)
SELECT p.id, p.code,
       (SELECT count(*) FROM property_contacts x WHERE x.property_id = p.id) AS contatti,
       (SELECT count(*) FROM property_leads x WHERE x.property_id = p.id) AS richieste,
       (SELECT count(*) FROM property_documents x WHERE x.property_id = p.id) AS documenti,
       (SELECT count(*) FROM property_accessories x WHERE x.property_id = p.id) AS accessori,
       (SELECT count(*) FROM activities x WHERE x.property_id = p.id) AS storico,
       (SELECT count(*) FROM properties f WHERE f.parent_property_id = p.id) AS pertinenze_collegate
  FROM properties p WHERE p.building_id IN (9, 10) ORDER BY p.id;

-- 4. POSSIBILI DUPLICATI
-- 4a. stessa posizione (scala, piano, interno) nello stesso edificio
SELECT p.building_id, coalesce(p.staircase, '') AS scala, coalesce(p.floor, '') AS piano, p.internal_number,
       count(*) AS schede, string_agg(p.code, ', ' ORDER BY p.id) AS codici
  FROM properties p
 WHERE p.building_id IN (9, 10) AND p.internal_number IS NOT NULL AND (to_jsonb(p)->>'deleted_at') IS NULL
 GROUP BY 1, 2, 3, 4 HAVING count(*) > 1;
-- 4b. stessa identità catastale altrove nell'agenzia (sezione nota o no)
SELECT p.id, p.code, p.building_id, q.id AS altra_id, q.code AS altra_code, q.building_id AS altro_edificio
  FROM properties p JOIN properties q
    ON q.agency_id = p.agency_id AND q.id <> p.id
   AND q.cadastral_municipality_code = p.cadastral_municipality_code AND q.cadastral_sheet = p.cadastral_sheet
   AND q.cadastral_parcel = p.cadastral_parcel AND q.cadastral_subunit = p.cadastral_subunit
 WHERE p.building_id IN (9, 10);
-- 4c. schede allo STESSO indirizzo ma non collegate all'edificio (candidate da collegare, non da creare)
SELECT b.id AS edificio, p.id, p.code, p.property_type, p.record_kind, p.building_id, p.floor, p.internal_number
  FROM buildings b JOIN properties p
    ON p.agency_id = b.agency_id AND p.building_id IS DISTINCT FROM b.id
   AND lower(btrim(p.city)) = lower(btrim(b.city))
   AND lower(btrim(p.address)) = lower(btrim(b.address))
   AND lower(btrim(coalesce(p.civic_number, ''))) = lower(btrim(coalesce(b.civic_number, '')))
 WHERE b.id IN (9, 10) AND (to_jsonb(p)->>'deleted_at') IS NULL ORDER BY b.id, p.id;

-- 5. IMPRONTA (per verificare dopo che nulla è cambiato senza volerlo)
SELECT b.id, count(p.id) AS schede,
       md5(coalesce(string_agg(p.id || ':' || p.updated_at || ':' || p.property_type || ':' || coalesce(p.cadastral_category, '')
           || ':' || coalesce(p.surface_sqm::text, '') || ':' || p.record_kind || ':' || coalesce(p.commercial_status, ''),
           ',' ORDER BY p.id), '')) AS impronta
  FROM buildings b LEFT JOIN properties p ON p.building_id = b.id
 WHERE b.id IN (9, 10) GROUP BY b.id ORDER BY b.id;
ROLLBACK;
SQL
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 -X -P pager=off -f /tmp/a4_9_10.sql | tee /tmp/a4_9_10.txt
```

Copia l'output fuori dalla Shell: è effimera.

## 2. Come leggere i risultati

Per ciascun edificio compila questa tabella prima di toccare qualunque cosa.

| | Edificio 9 | Edificio 10 |
|---|---|---|
| **Situazione dichiarata** (query 1) | totale dichiarato, fonte, affidabilità della dichiarazione (`census_status`), composizione per classe | idem |
| **Schede esistenti** (query 2) | per ognuna: tipologia, classe calcolata, piano, scala, interno, superficie e categoria con la loro provenienza | idem |
| **Differenze** | dichiarate per classe − presenti per classe; tipologie che non corrispondono alle note | idem |
| **Possibili duplicati** (query 4a, 4b, 4c) | stessa posizione; stessa identità catastale; stesso indirizzo fuori dall'edificio | idem |
| **Collegamenti** (query 3) | ciò che la correzione deve conservare | idem |

Regole di lettura:

- **Classe calcolata**: appartamento, villa, casa = abitazione; negozio e ufficio = commerciale; rustico = «altra», salvo scelta esplicita; garage e cantina autonomi = «altra»; pertinenza solo se collegata o marcata come tale.
- **Edificio 10**: se le note dicono «locali commerciali» ma le schede sono `apartment`, le due schede oggi contano come **abitazioni**. Non è un duplicato: è una **tipologia da verificare**. La decisione spetta a chi conosce l'immobile (visura, sopralluogo).
- **Query 4c non vuota**: esistono schede dello stesso indirizzo fuori dall'edificio. Prima di creare unità nuove, valuta se collegarle all'edificio, così da non creare un doppione.
- **«Affidabilità della dichiarazione»** (ex «Stato censimento») dice quanto è affidabile il **totale dichiarato** (verificato / parziale / stimato). Non dice quante schede ci sono né quanto sono complete.

## 3. Passi manuali sicuri (Shell del CRM, uno alla volta)

Ordine obbligatorio: **prima le schede esistenti, poi la composizione, solo alla fine le unità mancanti.**

1. **Rileggi** la scheda di ciascuna unità esistente (Immobili → scheda → tab Censimento). Non modificare nulla finché la tabella del §2 non è completa.
2. **Correggi la tipologia** dove serve (edificio 10: «Appartamento» → «Negozio» o «Ufficio» solo se confermato da visura o sopralluogo) con «Modifica immobile». Stessa scheda, stesso codice: collegamenti e storico restano.
3. **Dati catastali e superficie** di ogni scheda dal modulo omonimo: identità catastale, superficie e categoria con la loro provenienza.
   - «Verificata» solo con il documento davanti (tipo e data).
   - Il salvataggio è una sola operazione e finisce nello storico.
4. **Possibili duplicati**:
   - stessa posizione (4a): apri entrambe e decidi quale è giusta, senza cancellarne nessuna. Quella sbagliata si corregge (interno o scala) oppure, se è davvero un errore, si archivia dalla sua scheda (reversibile).
   - stessa identità catastale (4b): il sistema impedisce un secondo salvataggio identico. Se esiste, è un dato storico da chiarire con la visura.
   - stesso indirizzo fuori edificio (4c): collega la scheda all'edificio invece di crearne una nuova.
5. **Dichiara la composizione** (scheda edificio → «Dichiara composizione»): abitazioni, commerciali, altre unità e pertinenze come risultano dalla verifica. «Non so» è ammesso: l'edificio resta in bozza.
   - «Usa la somma come totale» solo se il totale dichiarato va davvero aggiornato. Altrimenti il sistema segnala la differenza e non corregge nulla in silenzio.
6. **Solo ora** valuta «Crea le unità mancanti».
   - L'anteprima non scrive nulla e mostra per classe dichiarate, presenti e da creare. Controlla che le schede esistenti siano contate nella classe giusta.
   - La conferma crea solo la differenza, come schede di censimento in bozza; la numerazione degli interni è facoltativa e **provvisoria**.
   - «Annulla» del lotto toglie solo le schede ancora intatte.
7. **Interni provvisori**: correggili dal modulo «Dati catastali e superficie» e premi «Conferma interno» solo quando il numero è quello reale.
8. **Ripeti la fotografia** (§1). L'impronta deve essere cambiata solo per le schede che hai modificato tu, e i collegamenti della query 3 devono essere invariati.

## 4. Cosa NON fare

- Nessun `UPDATE`, `DELETE` o script sul database: solo la Shell.
- Non usare «Crea le unità mancanti» sull'edificio 10 prima di aver corretto le due schede esistenti. Altrimenti i locali commerciali verrebbero creati in più, accanto a due «abitazioni» che in realtà sono i negozi.
- Non usare la modifica di gruppo per «sistemare» schede verificate: il sistema la rifiuta per proteggere la verifica.
- Non spostare edifici o schede nel Cestino per «ripartire da zero».
