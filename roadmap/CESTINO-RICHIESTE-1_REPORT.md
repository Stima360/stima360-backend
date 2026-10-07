# CESTINO-RICHIESTE-1 (FASE H) — Cestino e Ripristino delle Richieste acquirente

Branch `core-0.1-test`. Base: `f7251ac` (CESTINO-EDIFICI-1, risultati post-commit), verificata come ultimo commit: nessun lavoro successivo da conservare. Solo TEST: PROD esclusa. Sito pubblico non toccato. Ultima fase della roadmap: nessuna fase nuova aggiunta.

## 0. Stato da conservare (da riportare finché non cambia)

| Cosa | Stato |
|---|---|
| Migration applicate su Render TEST | fino alla **088** |
| **089** `089_pertinenze_1_unit_nature` (FASE E) | **NON applicata** |
| **090** `090_cestino_contatti_1_contact_trash` (FASE F) | **NON applicata** |
| **091** `091_cestino_edifici_1_building_trash` (FASE G) | **NON applicata** |
| **092** `092_cestino_richieste_1_buy_request_trash` (questa fase) | nuova, **NON applicata** |
| Collaudi live delle FASI E, F, G e H | **pendenti** |

**Ordine:** 089 → 090 → 091 → 092, con un solo `apply` del runner (`roadmap/CESTINO-RICHIESTE-1_TEST_RUNBOOK.md`). La 092 estende il CHECK del registro scritto da 090 e 091: va per ultima, e il runner la mette per ultima.

Fino all'applicazione il codice nuovo su TEST funziona come prima: i filtri leggono `deleted_at` via `to_jsonb`; «Elimina…» e la scheda «Richieste» del Cestino rispondono **503 `TRASH_NOT_INSTALLED`**, con un messaggio leggibile.

## 1. Ciclo di vita ricostruito dal codice

- **Stati** (`buy_requests.status`, 004):
  - aperti `draft / active / paused`;
  - chiusi `satisfied / closed / archived`.
  - `archived` scrive `archived_at` ed è riservato a owner/admin (DELETE-ARCH Fase 0).
  - `satisfied` lo scrive il completamento di una vendita.
- **Collegamenti:**
  - **contatto** obbligatorio (`contact_id`, RESTRICT);
  - **lead** facoltativa (SET NULL);
  - **criteri** (luoghi, tipologie, caratteristiche);
  - **abbinamenti** (`matches`, calcolati solo su richiesta: nessun cron);
  - **interazioni** ed esiti;
  - **visite**: interazione `visit_scheduled` → `property_visits` → appuntamento `buyer_visit`. `appointments` non ha `buy_request_id`;
  - **proposte**, tramite l'abbinamento;
  - **vendite** (`property_sales.buy_request_id`);
  - **task** (`buy_request_task_links`);
  - **storico** (`buy_request_history`);
  - **suggerimenti**: NBA «Oggi», regole FLOW R004/R005/R006, pressione acquirenti, candidati «vendita invisibile».
- **Comunicazioni:** nessun messaggio, journey o automazione è legato a una richiesta. Sono del contatto.
- **Permessi esistenti:** nessuno scope per agente sulle richieste (`assigned_to` è testo libero). Ogni ruolo dell'agenzia le vede e le gestisce; solo archivia/riattiva è riservato a owner/admin.

## 2. Matrice dei blocchi (presentata prima di procedere)

| Caso | Esito |
|---|---|
| Richiesta senza processi aperti, in qualunque stato | **Cestino permesso**; lo stato resta com'è, nessuna chiusura né sospensione |
| Proposta in bozza o inviata | **Blocco** `PROPOSAL_OPEN` → la richiesta (tab Proposte) |
| Vendita pendente | **Blocco** `SALE_PENDING` → l'immobile |
| Visita futura in programma (dall'abbinamento) | **Blocco** `VISIT_SCHEDULED` → il giorno in Agenda |
| Task aperto collegato | **Blocco** `TASK_OPEN` → Attività |
| Abbinamenti (anche forti), candidati «vendita invisibile», NBA | **Non bloccano**: escono da liste e calcoli; la conferma lo dice |
| Storico reale: interazioni ed esiti, proposte concluse, vendite registrate, task completati, note, richiesta conclusa o archiviata | owner/admin sì; **agente 403 `HISTORY_REQUIRES_ADMIN`** (la politica del Cestino Contatti) |
| Già nel Cestino | 409 `ALREADY_DELETED` |
| Agenzia diversa | 404, indistinguibile; Cestino separato |
| Motivo non valido / nota oltre 500 | 400 `INVALID_TRASH_REASON` / 422 |
| Ripristino | owner/admin tutto; agente solo ciò che ha spostato lui (403 `NOT_DELETED_BY_YOU`) |
| Ripristino con il contatto nel Cestino | **409 `RESTORE_BLOCKED`** con `CONTACT_IN_TRASH`: collegamento al contatto e al Cestino Contatti |
| Ripristino con altre richieste aperte dello stesso contatto | **segnalate**, nulla unito né chiuso |
| Qualunque operazione nuova sulla richiesta nel Cestino | 409 `BUY_REQUEST_IN_TRASH` dal servizio **e** guardie della 092 |

**Scelta segnalata all'avvio.** Per l'agente vale la politica già adottata per Contatti ed Edifici: può spostare solo richieste **senza storico reale** e ripristinare solo le sue. L'archiviazione resta riservata a owner/admin come prima. Nessuna obiezione ricevuta.

## 3. Cosa c'è adesso

- **Database (092):**
  - `buy_requests.deleted_at / deleted_by_user_id / deleted_reason`, nullable;
  - `deleted_by_user_id` ha la FK verso `operator_users` ON DELETE SET NULL;
  - stessi 5 motivi e stessa coerenza degli altri Cestini;
  - indice parziale;
  - registro esteso a `'buy_request'`;
  - le guardie del §4.
- **Backend:**
  - `buy/lifecycle.py`: controllo con blocchi, storico ed **effetti**, sposta, ripristina, elenco, riquadro della scheda;
  - `core/buy_trash.py`: predicati e rifiuto;
  - `core/exceptions.py`: `BuyRequestInTrash` (409 `{detail, code}`), tradotta in `core_cursor`.
  - 4 rotte in `buy/router.py` (contesto d'agenzia):
    - `GET /api/buy/requests/{id}/deletion-check`
    - `POST /api/buy/requests/{id}/trash` con body `{reason_code, note}`
    - `POST /api/buy/requests/{id}/restore`
    - `GET /api/buy/trash/requests`
- **UI (Shell OS):**
  - «Elimina…» nella scheda richiesta, con il foglio condiviso e gli effetti espliciti;
  - scheda in sola lettura con «Ripristina»;
  - quarta scheda **«Richieste»** nella pagina Cestino (`#/cestino/richieste`).

**Effetti mostrati prima della conferma** (dal backend):
- lo stato resta quello che è;
- quanti abbinamenti escono dalle liste e dai calcoli (restano nella scheda);
- il contatto resta attivo, con le sue altre richieste aperte;
- le comunicazioni del contatto **non** vengono sospese né annullate.

## 4. Guardie nel database (092) e concorrenza

- **Nessuna riga nuova verso una richiesta nel Cestino.** `cestino_richieste_guard`, BEFORE INSERT OR UPDATE OF `buy_request_id`, su 11 tabelle:
  - criteri ×3;
  - interazioni, task, storico;
  - abbinamenti, run ed esclusioni del matching;
  - vendite, candidati «vendita invisibile».
  - Più `property_proposals` tramite l'abbinamento (BEFORE INSERT OR UPDATE OF `match_id`).
  - La richiesta si legge **FOR SHARE**. Nessuna guardia su DELETE: nessuna cascata esistente viene bloccata.
- **Congelamento** (`cestino_richieste_freeze`):
  - lo spostamento nel Cestino è rifiutato con proposte aperte, vendite pendenti, task aperti collegati o visite future (`BUY_REQUEST_HAS_OPEN_PROCESSES`), anche per una UPDATE scritta a mano;
  - la riga nel Cestino non cambia, tranne il ripristino, le due FK SET NULL (`deleted_by_user_id`, `lead_id`) e `updated_at`.
- **Servizio:**
  - ogni scrittura della richiesta passa da un solo cancello (`buy/repository.py::_ensure_buy`): FOR SHARE, o FOR UPDATE dove già c'era, poi 409;
  - le righe figlie (criteri, interazioni, collegamenti ai task) non si modificano né si tolgono;
  - proposte, vendite, abbinamenti, esclusioni e riscontri rifiutano la richiesta nel Cestino.
- **Concorrenza:**
  - lo spostamento prende la riga FOR UPDATE e ricalcola i blocchi sotto lock;
  - ogni collegamento la prende FOR SHARE (servizio e guardie): chi arriva secondo vede l'altro;
  - il ripristino prende il contatto FOR KEY SHARE, contro uno spostamento del contatto in corso.

## 5. Dove la richiesta nel Cestino sparisce (e dove resta)

**Sparisce da:**
- elenco Acquirenti, ricerca globale, selettori (stessa rotta), tab Richieste del contatto, dashboard;
- abbinamenti: elenco, dettaglio, ricalcolo, refresh, calcolo per immobile, esclusioni;
- FLOW R004/R005/R006 e la loro lettura per entità;
- «Oggi», anche un'azione già calcolata;
- pressione acquirenti e vendita invisibile: eleggibilità, candidati mostrati, revisione;
- blocchi del Cestino Contatti: una richiesta nel Cestino non è un processo aperto del contatto.

**Resta:**
- scheda e flusso (`GET /api/buy/requests/{id}`, `/workflow`, `/matches`, `/tasks`) in sola lettura, con il riquadro `trash`;
- Cestino › Richieste;
- registro e audit;
- proposte e vendite **concluse** come storico dell'immobile.

**Non toccato:**
- il contatto e le sue altre richieste;
- comunicazioni, journey, automazioni;
- appuntamenti e visite passate.

Il ripristino non invia messaggi, non riprogramma, non riapre: azzera solo `deleted_*`.

## 6. UI

- **Scheda richiesta:** «Elimina…» accanto a «Modifica richiesta». Il foglio condiviso mostra «Verifica in corso…», poi il blocco (voci cliccabili, conferma disabilitata) o i 5 motivi con nota, storico conservato ed effetti. Dopo lo spostamento: toast e ritorno ad Acquirenti.
- **Scheda nel Cestino:**
  - riquadro con chi, quando, motivo e nota;
  - nessun comando nell'intestazione né in alcuna tab (criteri, ricalcolo, esiti, visite, proposte, vendite, «Apri match»);
  - dati e storico visibili;
  - se il contatto è nel Cestino, il collegamento «ripristina prima il contatto»;
  - «Ripristina» solo con `can_restore`. Dopo il ripristino la scheda si ridisegna e l'avviso elenca le altre richieste aperte dello stesso contatto.
- **Pagina Cestino:**
  - schede Immobili (predefinita), Contatti, Edifici, **Richieste**;
  - card con titolo, budget, contatto (segnato se nel Cestino), quando e chi, motivo, stato;
  - un ripristino rifiutato resta sulla card con il motivo e i collegamenti.

## 7. Prove

### Locali

| Suite | Esito |
|---|---|
| `tests/test_cestino_richieste_1_postgres.py`: PostgreSQL 16, schema dal runner, rotte vere | 7 passed |
| `tests/test_cestino_richieste_1_browser_postgres.py`: Chromium 390 e 1280 px, uvicorn con Shell e router veri | 2 passed (eseguita 3 volte) |
| `tests/test_cestino_richieste_1.py`: migration, rotte, esclusioni, confini | 6 passed |
| `tests/test_cestino_richieste_1_ui.py`: funzioni pure, statici, Shell in stub DOM | 12 passed |
| Cestini condivisi: Contatti ed Edifici (PostgreSQL, UI, statici, browser), Immobili 2B2/2B3 | invariati, passano (§7.1) |

La suite PostgreSQL copre:
- richiesta semplice: effetti, registro, stato invariato, invisibilità, scheda leggibile, 11 scritture rifiutate, guardie nel database, contatto e altra richiesta intatti, ripristino sullo stesso id con l'impronta dei dati identica e il possibile doppione segnalato;
- ogni blocco con il suo collegamento, nulla chiuso; gli abbinamenti da soli non bloccano; nessuna cascata;
- storico, permessi e agenzie;
- esclusione da abbinamenti, FLOW e «Oggi»; proposte e vendite nuove rifiutate; il ripristino non crea messaggi, appuntamenti o task e non cambia proposte o stato;
- contatto nel Cestino;
- concorrenza: due spostamenti; task contro spostamento nei due ordini; proposta scritta a mano contro spostamento;
- codice senza la 092: 503 leggibile, elenco e scritture come prima; la down si ferma; di nuovo up.

La prova in browser:
- blocco del task con collegamento;
- effetti e spostamento;
- elenco Acquirenti senza la richiesta;
- Cestino › Richieste, scheda in sola lettura (nessun comando in 4 tab), ripristino con avviso;
- di nuovo in elenco;
- nessuno scorrimento orizzontale, nessun errore di pagina.

### 7.1 Regressioni nei Cestini condivisi

Il foglio, il modello, il client e la pagina Cestino sono condivisi. Ripassate dopo le modifiche:
- `test_cestino_contatti_1*` (PostgreSQL 11, UI 16, statici);
- `test_cestino_edifici_1*` (PostgreSQL 7, browser 2, UI 13, statici);
- `test_delete_arch_2b2*`, `test_delete_arch_2b3_ui`, `test_fix_mandate_1_ui`.

Nel Cestino Contatti cambia una sola cosa: una richiesta già nel Cestino non è più un `BUY_REQUEST_OPEN` del contatto.

### 7.2 Sentinelle aggiornate (solo quelle superate)

Tutte con il marcatore «SENTINELLA AGGIORNATA DA CESTINO-RICHIESTE-1», verifiche mantenute:
- **catena delle migration** (092 ultima): 13 catene `numeri`, `max == 92`, finestre `tutte[-k]` e `discover_migrations()[-k]` spostate di uno con la riga della 092, `glob("093*")`, ledger 65 → 66, finestre lmc2/lmc3 a 25, a32_2 elenco 092;
- **conteggio rotte BUY 23 → 27** (`a31_3`, `p26_2d`, `p26_6c` test_28), con la verifica che le 4 nuove siano dietro il contesto d'agenzia e nello scope;
- **`test_delete_arch_2b1_postgres`**: l'entità «fuori catalogo» del registro ora è `'lead'`, perché `'buy_request'` è diventata valida;
- **`test_delete_arch_2b2`**:
  - finestra spostata;
  - una voce motivata in `CONSAPEVOLI`: i blocchi devono elencare tutte le righe aperte.
  - Due voci rischiavano di diventare «inutili» per un falso positivo del controllo: i controlli sulla richiesta sono stati spostati in istruzioni separate, così quelle eccezioni restano verificate;
- **certificatore live**: inventario FK con `buy_requests.deleted_by_user_id`;
- **`test_cestino_contatti_1` c01**: accetta la tupla di eccezioni con `BuyRequestInTrash`;
- **`test_cestino_edifici_1_ui` d04**: la quarta scheda «Richieste».

Non aggiornate di proposito: le sentinelle `git status` / `git diff` che falliscono solo con il working tree non committato (§11).

### 7.3 Suite completa

PostgreSQL 16 locale, prima del commit: **41 failed, 10997 passed, 115 skipped, 49 errors**.

Confronto con la lista post-commit della FASE G:
- **2 erano sentinelle superate da questa fase**, corrette e rieseguite:
  - `test_p26_1_legacy_basic_surface::test_g5` (rotte BUY 23 → 27);
  - `test_cestino_edifici_1::test_m01` (la 091 non è più l'ultima).
- **1 intermittente:** `test_lmc1b_owner_login_link_postgres::test_9e` (limite di accessi al portale proprietario, non tocca le richieste). È fallita una volta nella suite completa; rieseguita da sola e con il suo modulo, passa 3 volte su 3.
- **17 sentinelle `git status` / `git diff`** che vedono i file nuovi o modificati non ancora committati: a32_2 s14, lmc2 f4, lmc7 h4/h5, lmc8 h3/h5, lmc9 f3/f4, lmc11 h1, lmc12 d10/f4, lmc13 e1/e3, lmc15 37/40, p27_7 d3, p29_3g 18. Si verificano dopo il commit (§11).
- **21 sono esattamente la base** (§8).

Le 49 `ERROR` sono le stesse della base. **Nessuna regressione.**

## 8. Problemi preesistenti (non regressioni)

Nella base delle fasi E/F/G, invariati:
- P29-3 «file toccati» e «documento di design» (cercano `P29_2_0_COMMUNICATION_DESIGN.md`, solo sul Mac);
- lmc15 41/42;
- appuntamenti A30-1/2/8 su PostgreSQL;
- next2 router;
- followup isolation;
- agenda realapp;
- le 49 `ERROR` dell'import legacy A30-6/A30-7;
- `lmc3 test_19`, instabile.

## 9. Limiti dichiarati e scelte

- **Task collegati dalla Shell.** «+ Nuovo task» nella scheda richiesta crea un task del contatto, non collegato alla richiesta. I task collegati nascono dall'API o dal vecchio BUY admin. Il blocco `TASK_OPEN` è provato in locale; dal vivo si prova il blocco con una proposta (runbook §5.4).
- **Freschezza degli abbinamenti.** La marcatura tecnica «da aggiornare» resta possibile su un abbinamento esistente, perché non è un'operazione commerciale. Il ricalcolo, il refresh e ogni lettura operativa li escludono.
- **Candidati «vendita invisibile».** Al prossimo ricalcolo dell'immobile diventano `stale` (stato derivato, non commerciale). Fino ad allora sono nascosti nella lettura.
- **Vecchio BUY admin** (`/buy-admin`). Le sue funzioni non scoped non sono instradate dalla Shell. Le guardie della 092 coprono comunque ogni scrittura nel database.

## 10. Pendenti (verifiche live)

1. Applicare su TEST **089 → 090 → 091 → 092** con il runner: runbook §1–§4.
2. Collaudo live **FASE E**: `roadmap/PERTINENZE-1_TEST_RUNBOOK.md` §5.
3. Collaudo live **FASE F**: `roadmap/CESTINO-CONTATTI-1_TEST_RUNBOOK.md` §5.
4. Collaudo live **FASE G**: `roadmap/CESTINO-EDIFICI-1_TEST_RUNBOOK.md` §5.
5. Collaudo live **FASE H**: runbook §5, desktop e smartphone.
6. PROD: esclusa.

## 11. Dopo il commit

POST_COMMIT

## 12. Riepilogo finale A–H

Tutte le fasi sono su `core-0.1-test`, pushate (deploy automatico Render TEST). PROD e sito pubblico non toccati.

| Fase | Contenuto | Implementazione | Prove locali | Migration su TEST | Collaudo live |
|---|---|---|---|---|---|
| **A** FIX-MANDATE-1 | Una sola definizione di incarico | `f279f78` | PostgreSQL, UI, suite | nessuna | **pendente** (smoke: motivazione del blocco Cestino, «Dati da completare» in Incarichi, rifiuto dello svuotamento del tipo, firma LMC-15) |
| **B** EDIFICI-1 | Sezione Edifici | `80b047d` | PostgreSQL, UI, Chromium 1280/390/320 | nessuna | **pendente** (voce, lista e filtri, contatori, unità → immobile → edificio, ritorno con filtri, smartphone) |
| **C** CREAZIONE-GUIDATA-1 | Creazione guidata immobile / censimento | `3d3c3c7` | PostgreSQL, UI, Chromium | nessuna | **pendente** (tre percorsi, riuso edificio, unità commerciale con assegnazione, ritentativo, smartphone) |
| **D** CATALOGO-CANONICO-1 | Catalogo canonico e allineamento sito → CRM | `92a2dc7`, `2624d5a` (+ docs `744dad3`) | PostgreSQL, UI, Chromium | **087 e 088 applicate** (comunicazione di Giorgio) | **pendente** (runbook D §6) |
| **E** PERTINENZE-1 | Pertinenze autonome | `bf1d163` (+ docs `bf6dae8`) | PostgreSQL, UI, Chromium | **089 da applicare** | **pendente** |
| **F** CESTINO-CONTATTI-1 | Cestino Contatti | `dd9a88f` (+ docs `4833eae`) | PostgreSQL 11, UI 16, statici | **090 da applicare** | **pendente** |
| **G** CESTINO-EDIFICI-1 | Cestino Edifici | `fb4ab29` (+ docs `f7251ac`) | PostgreSQL 7, browser 2, UI 13, statici 6 | **091 da applicare** | **pendente** |
| **H** CESTINO-RICHIESTE-1 | Cestino Richieste acquirente | questo commit (§11) | PostgreSQL 7, browser 2, UI 12, statici 6 | **092 da applicare** | **pendente** |

**Integrazione col sito: rimandata** (dalla fase D):
- il sito pubblico non è collegato a TEST;
- il confronto con i form reali del sito è incompleto (sorgente non accessibile);
- l'adeguamento del frontend del sito (`client_request_id`, `campi_dichiarati`) è un contratto pronto ma non implementato, da fare fuori da questo repository;
- il collaudo live del sito resta un'attività successiva.

**Ordine consigliato per chiudere la roadmap:**
1. `apply` unico 089 → 092 (runbook di questa fase);
2. collaudi live E, F, G e H;
3. gli smoke A–D ancora aperti.

Fase H chiusa qui. Nessuna fase di sviluppo successiva iniziata.
