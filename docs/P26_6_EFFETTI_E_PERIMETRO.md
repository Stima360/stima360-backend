# P26-6 — Effetti delle fixture e perimetro distruttivo

Stato al run `42e32975ccd6` (commit `d6ff5699ed44618b99192ac9015c01d6b68242e1`).

Questo documento tiene separate tre cose che negli ultimi run si sono
confuse più volte: **evidenze ricevute** da Render, **verifiche locali** fatte
qui su doppi, e **verifiche live ancora necessarie**. Nessuna affermazione su
PostgreSQL è stata prodotta da questa macchina: non c'è un server in questo
ambiente.

---

## 1. Evidenze ricevute (Render TEST)

| Cosa | Valore |
|---|---|
| Commit verificato in esecuzione | `d6ff5699ed44618b99192ac9015c01d6b68242e1` |
| Migration `056` | applicata |
| Fingerprint post-056 | `5eb442bb1520b39332e266f1b43357e92045adb1a85423d00e15b82ca8bed825` |
| Snapshot | `reports/p26_baseline_TEST_20260912T094906Z.json` |
| Run `58aa0e189aaa` | rimosso con COMMIT: 82 righe, 6 audit conservati |
| Run `42e32975ccd6` | 232 PASS, 9 FAIL, 6 BLOCKED — **residui ancora sul TEST** |

`HEAD` locale coincide con il commit in esecuzione e l'albero di lavoro non
contiene modifiche ai sorgenti rispetto a esso: le correzioni dei turni
precedenti **sono** quelle deployate, non solo locali.

Comportamento di 056 da preservare, come comunicato: le stime preesistenti
hanno `lead_status` NULL, il default `'nuovo'` vale per i nuovi inserimenti.
Il file non va modificato.

---

## 2. Il ciclo che si ripeteva, e cosa lo chiude

Quattro run consecutivi hanno seguito la stessa traiettoria:

| Run | Effetto non tracciato | Conseguenza |
|---|---|---|
| `52f6d97b5214` | otto figli delle fixture | preflight blocca |
| `58aa0e189aaa` | `property_watch_observations` | preflight blocca |
| `42e32975ccd6` | `owner_notifications`, `owner_document_reads` | preflight blocca, 8 FAIL |

Ogni volta la causa era la stessa: **una funzione viene riparata, ricomincia a
scrivere, e le sue righe non sono nel perimetro.** La correzione arrivava
sempre dopo un run live speso a scoprirla.

### Il percorso documentale, per intero

- `publish_shared_document` → `_emit_notification_event`
  → `INSERT owner_notifications` (uno per titolare con concessione attiva)
  + riga di audit `notification_created`.
- `prepare_shared_document_download` → `read_shared_document`
  → UPSERT `owner_document_reads` + audit `shared_document_viewed`.

Entrambe CASCADE verso `owner_accounts`: cancellare il conto per primo le
avrebbe portate via **in silenzio**, senza che nessun predicato di
appartenenza le guardasse. Ora si cancellano per **id**, prima del conto, e si
verificano per id.

### La prova strutturale che chiude il ciclo

`tests/test_p26_6_live_cert_script.py`, serie **100**: ogni tabella che il
codice applicativo può inserire (54 oggi) deve avere una collocazione
dichiarata e **verificabile**:

- `perimetro` — deve comparire in una struttura del cleanup;
- `guardia` — deve avere una FK verso una tabella di `CLEANUP_PARENTS`, così
  che il preflight la veda se un giorno comparisse (verificato sulle
  migration, non promesso);
- `fuori-portata` — non deve avere alcuna FK verso il perimetro.

Un INSERT nuovo, in un percorso nuovo o esistente, rende rosso il file prima
che un run live lo scopra.

**"Guardia" non significa "il cleanup è completo".** Dimostra che una tabella
*potrebbe* bloccare, non che gli effetti dei percorsi esercitati siano
raccolti. La serie **103** aggiunge la domanda osservativa: le diciassette
tabelle che il run produce davvero devono essere materializzate nei doppi e
finire a zero dopo il cleanup — e le righe **preesistenti** seminate dai doppi
devono **sopravvivere**, perché un cleanup che le portasse via sarebbe il
danno da cui tutto questo difende. Resta distinta la prova in cui una figlia
davvero estranea blocca *prima* di qualunque DELETE.

### Limiti del controllo statico, verificati e non promessi

Il censimento legge `INSERT INTO <nome>` nei sorgenti. Tre cose gli
sfuggirebbero, e `test_103d` controlla che oggi non ce ne siano di
non gestite:

| Limite | Stato |
|---|---|
| SQL composta (`INSERT INTO {table}`) | **esiste**: `create_child`/`add_child` in `property/` e `buy/`. Nascondeva quattro tabelle — `property_photos`, `buy_request_locations`, `buy_request_typologies`, `buy_request_features` — ora collocate. I nomi si risolvono ai letterali dei chiamanti e `test_103e` li ri-deriva dai sorgenti |
| Trigger che scrivono | nessuno su tabelle di tenant: l'unico `INSERT` in una migration con trigger è `schema_baseline` in 026 |
| `COPY ... FROM` | nessuna nel codice applicativo |

### Due blocchi trovati cercandoli, non subendoli

| Tabella | Perché avrebbe bloccato | Dove entra ora |
|---|---|---|
| `flow_suppressions` | `agency_id` NOT NULL (054) e FK `RESTRICT` verso `agencies` (052): una riga avrebbe fatto fallire `DELETE FROM agencies` e con essa l'intera transazione | `DEDICATED_TABLES`, prima di `flow_executions` |
| `flow_action_records` | figlia CASCADE dell'esecuzione, **senza** `agency_id`: `POST /api/flow/events` valuta le regole e può scriverla | `OWNED_BY_PARENT` |

`OWNED_BY_PARENT` è il secondo criterio di appartenenza, e **non** si regge
sul solo genitore. La prima stesura di questo paragrafo diceva "non esiste
nessun altro che possa aver scritto la figlia": è falso come affermazione
generale — un processo platform-wide può scrivere su una nostra esecuzione
mentre il run è in corso. Le condizioni sono tre, tutte necessarie:

1. il genitore è fotografato fra le tabelle delle agenzie dedicate;
2. i processi platform-wide sono **sospesi** — condizione operativa che lo
   script non rileva e non può rilevare, attestata da
   `--with-dedicated-agencies`;
3. la riga **non punta altrove**: le altre FK si leggono dal catalogo e ogni
   riferimento non nullo deve cadere nel perimetro. Una riga mista resta
   fuori, viene dichiarata nel report e continua a bloccare.

Il criterio a chiave derivata resta dov'era richiesto
(`property_watch_observations`, dove scrivono anche la scansione periodica e
il motore invisible-sale).

---

## 3. Verifiche locali (doppi, non PostgreSQL)

- Suite completa **4615 passed, 52 skipped**, normale e con ordine casuale.
- Batterie **100** (collocazione), **101** (percorso documentale),
  **102** (le tre condizioni di `OWNED_BY_PARENT`), **103** (effetti
  materializzati, righe preesistenti conservate, limiti del controllo
  statico).
- Mutanti su disco, tutti uccisi e file ripristinati byte per byte:
  `owner_document_reads` fuori da `CLEANUP_PARENTS`; DELETE delle letture
  spostata dopo il conto; `OWNED_BY_PARENT` svuotata; `flow_suppressions`
  tolta dalle dedicate; il controllo dei riferimenti estranei disattivato
  (la riga mista tornava "nostra").
- I doppi **producono** notifiche e letture pubblicando e scaricando, come fa
  il backend: non sono dichiarate in un dizionario. Un test che le cercasse
  senza che il percorso le crei sarebbe verde sul vuoto.

Il prover ha corretto quattro classificazioni sbagliate che avevo scritto a
mano e un difetto del parser delle FK (le tabelle di `009` stanno su una riga
sola e i loro `CHECK` contengono `))`: una regex non bilanciata le troncava e
dichiarava "nessuna FK" su tabelle che ne hanno tre).

**Nessuna di queste verifiche tocca PostgreSQL.** `censimento_42e32975ccd6.sql`
è stato controllato solo staticamente: nessuna scrittura sul server,
`READ ONLY` + `REPEATABLE READ` + timeout presenti, e 19 istruzioni analizzate
con `sqlglot` in dialetto postgres. Nessun server l'ha mai eseguito, e il file
non è ancora su Render.

---

## 4. Verifiche live ancora necessarie

1. **Censimento di `42e32975ccd6`.** Procedura completa in
   `docs/P26_6_RECUPERO_42e32975ccd6.md`. Transazione `READ ONLY REPEATABLE
   READ` con istantanea consistente, guardia sul database TEST, timeout su
   statement e lock. Le SELECT prendono comunque un `ACCESS SHARE`: non
   bloccano il traffico ordinario ma sono in coda con le DDL — la dicitura
   "nessun lock" era sbagliata. Produce il **manifest** delle chiavi di
   storage su file locale con permessi `600`, senza stamparne nessuna.
   Il file **non è ancora su Render**.

2. **I sei audit `560, 562, 563, 564, 566, 567`.** Origine **non dimostrata**.
   Quello che si sa: `owner_account_id` e `property_id` sono entrambi NULL,
   `entity_type` vale `owner_account`, `owner_session`, `owner_token` (due
   ciascuno) e `entity_id` è valorizzato. Quello che **non** si può dedurre
   dalla riga sola: se siano nati NULL o se un `SET NULL` li abbia svuotati —
   `owner_audit_log.owner_account_id` e `.property_id` sono entrambi
   `ON DELETE SET NULL`, quindi le due ipotesi producono la stessa riga. I
   punti 10 e 10b del censimento leggono `entity_id`, `action`, `created_at`
   e le righe vicine (555–572): se i sei stanno dentro una sequenza di righe
   attribuite, l'ipotesi SET NULL diventa verificabile confrontando
   `entity_id` con gli id dei conti/sessioni/token dei run già rimossi.
   **Nessuna whitelist di id**: finché l'origine non è dimostrata, il FAIL
   resta.

3. **Recupero eseguibile di `42e32975ccd6`**, da scrivere sul censimento:
   prova con ROLLBACK che esegua davvero le DELETE e verifichi il ripristino
   completo, poi esecuzione esplicita vincolata al perimetro verificato.
   Database e bucket non condividono una transazione: le chiavi di storage
   (punto 8 del censimento) vanno rimosse **dopo** il COMMIT, mai durante la
   prova, e il fallimento parziale deve lasciare le righe che le localizzano
   leggibili — sono l'unico posto in cui quelle chiavi esistono.

4. **I sei BLOCKED** del run. Diagnosi fatta, fixture non ancora scritte:

   | Riga | Causa accertata | Cosa serve |
   |---|---|---|
   | `LEGACY_ADMIN-list-A/B-non-vede-*` | `/api/admin/stime?day=oggi` filtra `agency_id` e `data >= oggi`; il run crea stime solo nelle agenzie dedicate | una stima marcata nelle agenzie A e B, con cancellazione per id (le condivise non sono coperte dal DELETE per `agency_id`) |
   | `PROPERTY_WATCH-propria-B`, `ostile-A-B` | B non possiede alcuna stima su TEST. **E una stima da sola non basta**: `GET /api/property-watch/stime/{id}` chiama `get_watch_for_stima_scoped`, che solleva `WatchNotFoundError` → 404 se il watch non esiste; e `initialize` esige una valutazione completata (`seller_timeline_events` con `stima_completata` e payload) | stima + evento `stima_completata` + `POST initialize`, come fa già la fixture delle agenzie dedicate, con cancellazione per id di stima, evento, watch e osservazione |
   | `FLOW-list-B-non-vede-A` | riguarda `/api/flow/executions`; il run crea solo eventi, e un'esecuzione nasce solo se una regola trova corrispondenza | fixture deterministica sulle esecuzioni, che richiede di stabilire quale regola attiva corrisponde senza avviare processi platform-wide |
   | `NEXT_BEST_ACTION-disgiunte` | `refresh` non materializza nulla. Segnali letti: il più semplice e deterministico è `next_action_overdue` in `_lead_candidates_from_score`, che esige un **lead aperto** con `next_action_at` nel passato — `LeadCreate` richiede solo `contact_id` e accetta `next_action_at`, e le agenzie dedicate hanno già un contatto | creare quel lead nelle due agenzie dedicate, con `leads` aggiunta alla cancellazione per `agency_id` prima di `contacts` |

   Non sono state implementate in questo giro **di proposito**: scrivere in
   `stime` dentro le agenzie condivise e costruire esecuzioni FLOW sono
   modifiche che toccano dati simili a produzione su TEST e che, se sbagliate,
   ricreano il ciclo appena chiuso. Vanno fatte con la stessa disciplina:
   tracciamento e cancellazione **prima** della prima scrittura.

---

## 5. Gate

`LIVE_HOSTILE_MATRIX_PASSED = False`. Resta falso: il run `42e32975ccd6` è
FAIL, i residui sono sul TEST e i sei audit non sono attribuiti.
