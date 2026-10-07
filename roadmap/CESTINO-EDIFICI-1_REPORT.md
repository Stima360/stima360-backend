# CESTINO-EDIFICI-1 (FASE G) — Cestino e Ripristino degli Edifici

Branch `core-0.1-test`. Base: `4833eae` (CESTINO-CONTATTI-1, risultati post-commit), verificata come ultimo commit: nessun lavoro successivo da conservare. Solo TEST: PROD esclusa. Sito pubblico non toccato.

## 0. Stato da conservare (da riportare finché non cambia)

| Cosa | Stato |
|---|---|
| Migration applicate su Render TEST | fino alla **088** |
| **089** `089_pertinenze_1_unit_nature` (FASE E) | **NON applicata** |
| **090** `090_cestino_contatti_1_contact_trash` (FASE F) | **NON applicata** |
| **091** `091_cestino_edifici_1_building_trash` (questa fase) | nuova, **NON applicata** |
| Collaudi live delle FASI E, F e G | **pendenti** |

Il runner applica le pendenti **in ordine**: su TEST un solo `apply` porta la 089, poi la 090, poi la 091. Il runbook `roadmap/CESTINO-EDIFICI-1_TEST_RUNBOOK.md` copre tutte e tre. La 091 dipende dalla 090 (estende il CHECK del registro che la 090 scrive) e il runner la mette dopo.

Fino all'applicazione, il codice nuovo su TEST funziona come prima:
- lista Edifici, candidati della creazione guidata, «palazzina simile» e scheda edificio sono invariati: i filtri leggono `deleted_at` via `to_jsonb`, quindi valgono anche senza la colonna;
- «Elimina…» dell'edificio e la scheda «Edifici» del Cestino rispondono **503 `TRASH_NOT_INSTALLED`**, con un messaggio leggibile.

## 1. Cosa c'è adesso

Lo stesso impianto del Cestino Immobili (085/086) e Contatti (090), senza un sistema parallelo: stessi 5 motivi, stessa nota (max 500), stesso registro append-only, stesso foglio di conferma, stessa pagina Cestino, stesso «Ripristina».

- **Database (091):**
  - colonne `buildings.deleted_at`, `deleted_by_user_id` (FK `operator_users`, `ON DELETE SET NULL`), `deleted_reason`, nullable, con la stessa coerenza di stato di immobili e contatti;
  - indice parziale `idx_buildings_trash`;
  - il registro `record_lifecycle_events` accetta anche `entity_type = 'building'`;
  - le due guardie descritte al punto 4.
  - `uq_buildings_client_request` **non cambia**: resta su tutte le righe.
- **Backend:**
  - `property/building_lifecycle.py`: controllo, sposta, ripristina, elenco, riquadro della scheda;
  - `core/building_trash.py`: predicato `live()` e riconoscimento del rifiuto `BUILDING_IN_TRASH` del database;
  - `core/exceptions.py`: `BuildingInTrash` (409, `{detail, code}`), tradotta in `core_cursor` come i Cestini precedenti;
  - 4 rotte nel router Immobili (contesto d'agenzia, tradotte da `trc`):
    - `GET /api/property/buildings/{id}/deletion-check`
    - `POST /api/property/buildings/{id}/trash` con body `{reason_code, note}`
    - `POST /api/property/buildings/{id}/restore`
    - `GET /api/property/trash/buildings`
  - `property/census.py`: lista Edifici, candidati, «palazzina simile», modifica, unità nuove e retry della creazione sanno del Cestino (§5).
- **UI (Shell OS):**
  - «Elimina…» nella scheda edificio, con il foglio condiviso;
  - scheda in sola lettura con «Ripristina» per un edificio nel Cestino;
  - pagina Cestino con una terza scheda **Edifici**, raggiungibile anche con `#/cestino/edifici`; **Immobili** resta la predefinita e invariata.

Il Cestino degli edifici serve per una palazzina creata per errore, doppia o non valida, **vuota**. Non esiste nessuna cancellazione fisica, nessuna cascata, nessuno «svuota», nessuno scollegamento automatico.

**Palazzina contenitore e «intero stabile» restano distinti.** L'edificio (`buildings`) è il contenitore; l'immobile di tipologia «intero stabile» (`properties.whole_building`) è un immobile, con il suo Cestino Immobili. Spostare nel Cestino l'«intero stabile» non tocca l'edificio; l'«intero stabile», anche nel Cestino, blocca l'edificio.

## 2. Matrice dei casi

| Caso | Esito |
|---|---|
| Edificio vuoto (nessuna riga di `properties` lo punta) | **Cestino permesso**, a ogni ruolo d'agenzia |
| Unità attiva (censimento o CRM, principale o pertinenza) | **Bloccato** `BUILDING_HAS_UNITS`, voce → `#/immobili/{id}` |
| Pertinenza (anche «da collegare») | **Bloccato**, la voce dice «pertinenza» |
| Immobile «intero stabile» | **Bloccato**, la voce dice «intero stabile» |
| Unità archiviata | **Bloccato**, voce «archiviata» → `#/immobili/{id}` |
| Unità nel Cestino Immobili | **Bloccato**, voce «nel Cestino» → `#/cestino` (deve poter tornare nel suo edificio) |
| Edificio già nel Cestino | 409 `ALREADY_DELETED` |
| Edificio di un'altra agenzia | 404, indistinguibile; Cestino separato |
| Motivo non valido / nota oltre 500 | 400 `INVALID_TRASH_REASON` / 422 |
| Modifica di un edificio nel Cestino | 409 `BUILDING_IN_TRASH` (servizio) + riga congelata (database) |
| Unità nuova o spostata verso un edificio nel Cestino | 409 `BUILDING_IN_TRASH` (servizio) + guardia (database) |
| Creazione guidata ripetuta con la chiave di un edificio nel Cestino | 409 `BUILDING_IN_TRASH` «ripristinala dal Cestino», mai un doppione |
| Ripristino di un edificio non nel Cestino | 409 `NOT_DELETED` |
| Ripristino con possibili doppioni attivi | 200, doppioni **segnalati** (stessa chiave catastale o stesso indirizzo), nulla unito |

Il blocco elenca ogni unità con il suo collegamento (fino a 50 voci, conteggi sempre completi) e un collegamento alla scheda dell'edificio. Il testo dice quante sono e in che stato, per esempio «L'edificio ha 3 unità collegate (2 attive, 1 nel Cestino Immobili): restano nel loro edificio e non si spostano da qui. Si può spostare nel Cestino solo un edificio vuoto.»

### Chi può fare cosa

| Ruolo | Spostare (edificio vuoto) | Ripristinare | Vedere nel Cestino |
|---|---|---|---|
| Titolare, amministratore d'agenzia, amministratore di piattaforma | sì | tutti | tutti |
| Agente | sì | **solo quelli spostati da lui** (altrimenti 403 `NOT_DELETED_BY_YOU`) | **solo i suoi** |

Gli edifici non hanno un assegnatario: chi può gestire la palazzina oggi (ogni ruolo d'agenzia) può spostarla quando è vuota. Ripristino ed elenco seguono la regola già in uso per immobili e contatti.

## 3. L'operazione (una transazione)

### Sposta nel Cestino
1. L'edificio si legge **`FOR UPDATE`**, nella propria agenzia (404 altrimenti).
2. Se è già nel Cestino: `ALREADY_DELETED`.
3. Si contano tutte le righe di `properties` con quel `building_id`, senza filtri: se ce n'è una, `TRASH_BLOCKED` con il blocco.
4. `UPDATE` di `deleted_at`, `deleted_by_user_id`, `deleted_reason`; evento `trash` nel registro (motivo, nota, autore); audit.

### Ripristino
Stesso id, stessa riga: si azzerano solo le tre colonne. Evento `restore` nel registro, con gli id dei possibili doppioni nei metadati. La risposta porta `possible_duplicates`, calcolati con la regola già esistente della «palazzina simile» (`census._simili_edificio`, solo edifici vivi, escluso sé stesso). Nessuna fusione, nessuna modifica agli altri edifici.

## 4. Guardie nel database (091)

### Nessuna unità nuova verso un edificio nel Cestino
`trg_properties_building_trash_guard`, `BEFORE INSERT OR UPDATE OF building_id ON properties`. Se `building_id` non cambia esce subito (collegamenti esistenti intatti). Altrimenti legge l'edificio **`FOR SHARE`**, lo stesso lock del trigger della 083 (`properties_links_integrity`), e rifiuta con `BUILDING_IN_TRASH` se è nel Cestino.

### Cestino rifiutato con unità collegate, riga congelata
`trg_buildings_trash_freeze`, `BEFORE UPDATE ON buildings`:
- `deleted_at` da vuoto a valorizzato con **qualunque** riga di `properties` collegata → `BUILDING_HAS_UNITS`, anche per un'`UPDATE` scritta a mano;
- un edificio nel Cestino non cambia, tranne `deleted_by_user_id` (per il `SET NULL` della FK) e `updated_at`; il ripristino (azzerare `deleted_at`) è ammesso.

### Concorrenza
Spostamento (`FOR UPDATE`) e collegamento di un'unità (`FOR SHARE`, servizio e trigger) si serializzano: chi arriva secondo vede l'altro.
- Unità nata mentre lo spostamento aspetta → lo spostamento la vede: `TRASH_BLOCKED`.
- Spostamento in corso mentre nasce un'unità → l'unità aspetta, poi `BUILDING_IN_TRASH`.
- Mai entrambi. Due spostamenti contemporanei → uno solo, l'altro `ALREADY_DELETED`.

## 5. Dove l'edificio nel Cestino sparisce (e dove resta)

**Sparisce:**
- lista Edifici (`GET /api/property/buildings`, anche con ricerca e filtri);
- candidati della creazione guidata (stessa rotta, `city`/`search`/`sort=address`);
- avvisi «palazzina simile» alla creazione di un edificio, e quindi anche dai possibili doppioni di un ripristino;
- creazione di unità e modifica della palazzina (409).

**Resta:**
- scheda `GET /api/property/buildings/{id}`, in sola lettura, con il riquadro `trash` (quando, chi, motivo, nota, `can_restore`);
- Cestino › Edifici;
- registro e audit.

Le unità non sono toccate: un edificio nel Cestino è sempre vuoto (§4), quindi nessuna unità viva punta a una palazzina nascosta.

## 6. UI

- **Scheda edificio:** «Elimina…» accanto a «Modifica palazzina». Il foglio è quello condiviso: «Verifica in corso…», poi o il blocco (voci cliccabili, conferma disabilitata) o i 5 motivi con la nota e la frase «L'edificio è vuoto: esce dalla lista Edifici e dalla creazione guidata.». Dopo lo spostamento: toast «Edificio spostato nel Cestino» e ritorno alla lista Edifici.
- **Scheda nel Cestino:** riquadro «Nel Cestino» (quando, chi, motivo e nota), nessun «Modifica palazzina», «Elimina…» o «+ Aggiungi unità»; arrivando dalla procedura guidata il foglio dell'unità non si apre. «Ripristina» solo se il backend lo consente (`can_restore`), altrimenti «Può ripristinarlo chi lo ha spostato o un amministratore.». Dopo il ripristino la scheda si ridisegna (stesso id) e, se ci sono, compare l'avviso dei possibili doppioni con i collegamenti e «Nessun edificio è stato unito o modificato.».
- **Pagina Cestino:** schede Immobili (predefinita), Contatti, **Edifici**. Card con nome, tipo, via e Comune, quando e chi, motivo e nota, unità dichiarate; «Apri scheda» e «Ripristina»; «Carica altri».
- Le rotte le nomina solo `trash/trash-api.js`; nessuna regola di permesso nel client.

## 7. Prove

### Locali
| Suite | Esito |
|---|---|
| `tests/test_cestino_edifici_1_postgres.py`: PostgreSQL 16 vero, schema completo dal runner, rotte vere | 7 passed |
| `tests/test_cestino_edifici_1_browser_postgres.py`: Chromium a 390 e 1280 px, uvicorn con Shell e router veri su PostgreSQL | 2 passed |
| `tests/test_cestino_edifici_1.py`: migration per il runner, rotte, filtri, confini | 6 passed |
| `tests/test_cestino_edifici_1_ui.py`: funzioni pure, statici, Shell vera in stub DOM | 13 passed |

La suite PostgreSQL copre:
- edificio vuoto: controllo, motivo e nota, registro, invisibilità in lista e candidati, scheda in sola lettura, modifica e unità nuove rifiutate (servizio e database, `INSERT` e `UPDATE` dirette), ripristino sullo stesso id con l'impronta dei dati identica, riuso dopo il ripristino;
- blocchi di ogni tipo con il collegamento: unità attiva, pertinenza da collegare, «intero stabile» e poi lo stesso nel Cestino Immobili (l'edificio non cambia e resta bloccato), unità archiviata; nessuna unità scollegata; guardia del database su `UPDATE` diretta;
- permessi e agenzie (404 tra agenzie, agente: sposta, vede e ripristina solo i suoi);
- retry della creazione guidata: 409, una sola riga;
- ripristino con doppioni per indirizzo e per chiave catastale, nulla modificato, id nei metadati del registro;
- concorrenza nei due ordini, via servizio e direttamente nel database, più due spostamenti contemporanei;
- codice su un database senza la 091 (503 leggibile, lista e scheda come prima), down che si ferma con edifici nel Cestino o con eventi, e di nuovo up.

La prova in browser fa, a ogni larghezza: navigazione edificio → unità; blocco con il collegamento all'unità (il database non cambia); spostamento di un edificio vuoto; creazione guidata senza l'edificio fra i candidati; Cestino › Edifici, «Apri scheda» in sola lettura, «Ripristina» e di nuovo candidato. Nessuno scorrimento orizzontale, nessun errore di pagina. Screenshot locali (non nel repository).

### Sentinelle aggiornate
Con il marcatore «SENTINELLA AGGIORNATA DA CESTINO-EDIFICI-1»:
- catena delle migration: 091 ultima in 14 file, finestre `tutte[-k]` spostate di uno, `glob("092*")`, ledger 64 → 65, finestre lmc2/lmc3 allargate di uno;
- `test_delete_arch_2b1_postgres`: l'entità «non valida» del registro ora è `'buy_request'` (`'building'` è diventata valida);
- certificatore live: inventario FK con `buildings.deleted_by_user_id` e le 3 sonde anonime nuove (conteggi 14 → 17, 13 → 16);
- censimento-4 b01: esenta solo le forme esatte delle rotte del Cestino in `trash-api.js`/`trash-model.js`;
- cestino-contatti c01/m01: accettano la tupla di eccezioni estesa e cercano la 090 per nome.

### Suite completa
PostgreSQL 16 locale, prima del commit: **33 failed, 10978 passed, 115 skipped, 49 errors**.

Tre fallimenti erano di questa fase e sono stati corretti, poi rieseguiti:
- `test_censimento_3_backend::test_c01` e `test_p26_6c_backend_gate_closure::test_28`: conteggio esatto delle rotte del router Immobili, 50 → 54. Sentinelle aggiornate con il marcatore; la prima verifica anche che le 4 rotte nuove siano dietro `legacy_basic_agency_context`, la seconda che siano nello scope. Ora 112 passed.
- `test_cestino_edifici_1_browser_postgres[1280]`: nella suite completa il clic su «Elimina…» (b03) è scaduto subito dopo che b02 aveva aperto la scheda dell'unità. Ipotesi più probabile: un disegno tardivo della pagina precedente sulla Shell rallentata. La prova ora aspetta che la scheda dell'unità finisca e che la scheda edificio mostri il titolo giusto prima di agire. Rieseguita 3 volte: 2 passed su 2 ogni volta.

I restanti 30, confrontati con la lista post-commit della FASE F (21) e con la base:
- **21 sono gli stessi della base** (§8);
- **9 sono sentinelle `git status`** che vedono i file nuovi non ancora committati (la 091 fra le migration, i file nuovi della Shell): a32_2 s14, lmc7 h5, lmc8 h5, lmc9 f4, lmc11 h1, lmc13 e1, lmc15 37, p27_7 d3, p29_3g 18. Si verificano dopo il commit (§11).

Le 49 `ERROR` (import legacy A30-6/A30-7 su PostgreSQL) sono le stesse della base. **Nessuna regressione.**

## 8. Problemi preesistenti (non regressioni)

Già nella base delle fasi E/F, invariati:
- P29-3 «file toccati» e «documento di design»: cercano `P29_2_0_COMMUNICATION_DESIGN.md`, che c'è solo sul Mac;
- lmc15 41/42; appuntamenti A30-1/2/8 su PostgreSQL; next2 router; followup isolation; agenda realapp;
- le 49 `ERROR` dell'import legacy A30-6/A30-7 su PostgreSQL;
- `test_lmc3_valuation_snapshot_postgres::test_19`, instabile (cerca `'82'` in una risposta con un timestamp);
- le sentinelle `git diff`/git-status che falliscono solo con il working tree non committato: si verificano dopo il commit (§11).

## 9. Limiti dichiarati e scelte

- **`archived_at` degli edifici** esiste dalla 083 ma nessun flusso lo usa: il Cestino non lo tocca e la lista continua a escludere gli archiviati come prima.
- **Un edificio vuoto con storico** (unità passate e poi scollegate a mano) si può spostare: il registro e l'audit restano; non c'è un «storico operativo» proprio degli edifici che giustifichi un blocco da amministratore.
- **Retry della creazione guidata:** la chiave `client_request_id` resta unica su tutte le righe, quindi il retry non crea un doppione e chiede di ripristinare.
- **Selettori:** nella Shell gli edifici si scelgono o si elencano in tre punti: lista Edifici, tab Censimento di Immobili e candidati della creazione guidata. Usano tutti `GET /api/property/buildings`, che esclude il Cestino. Il collegamento all'edificio dalla scheda di un'unità resta, perché un edificio con unità non può essere nel Cestino.
- **Certificatore live:** il controllo tra agenzie su un edificio esistente (`_edificio_altrui`) resta corretto anche se quell'edificio è nel Cestino: la scheda filtra prima l'agenzia, quindi 404.

## 10. Pendenti (verifiche live)

1. Applicare su TEST **089, 090 e 091**, con il runner e nell'ordine: runbook §1–§4.
2. Collaudo live **FASE E** (pertinenze): `roadmap/PERTINENZE-1_TEST_RUNBOOK.md` §5.
3. Collaudo live **FASE F** (Cestino Contatti): `roadmap/CESTINO-CONTATTI-1_TEST_RUNBOOK.md` §5.
4. Collaudo live **FASE G** (Cestino Edifici): runbook §5, desktop e smartphone.
5. PROD: esclusa.

## 11. Dopo il commit

Commit `fb4ab29`, spinto su `core-0.1-test`: deploy automatico su Render TEST. Ho rieseguito, a working tree pulito, i 33 test falliti nella suite completa:
- **12 ora passano**:
  - le 9 sentinelle `git status` di §7;
  - le 2 sentinelle del conteggio rotte (già corrette prima del commit);
  - la prova in browser a 1280 px (resa stabile prima del commit).
- **21 restano**, e sono esattamente la lista post-commit della FASE F, cioè la base (§8).

**Nessun fallimento fuori dalla base.**

Su Render TEST le migration sono ancora **fino alla 088**: **089, 090 e 091 da applicare**, collaudi live E, F e G pendenti (§0, §10, runbook).
