# CESTINO-CONTATTI-1 (FASE F) — Cestino e Ripristino dei Contatti

> **AGGIORNAMENTO COLLAUDO-FINALE-A-H (07/10/2026).** Le migration 089, 090, 091 e 092 sono **applicate** su `stima360_db_test` (comunicazione di Giorgio, verifica SQL riuscita). Il collaudo live è stato eseguito: esiti PASS / FAIL / NON VERIFICATO in `roadmap/COLLAUDO-FINALE-A-H_REPORT.md`. Le righe «non applicata» / «pendente» qui sotto descrivevano lo stato alla consegna e sono state aggiornate.

Branch `core-0.1-test`. Base: `bf6dae8` (PERTINENZE-1, risultati post-commit). Solo TEST: PROD esclusa.

## 0. Stato da conservare (da riportare finché non cambia)

| Cosa | Stato |
|---|---|
| Migration applicate su Render TEST | fino alla **088** |
| **089** `089_pertinenze_1_unit_nature` (FASE E) | **applicata** su `stima360_db_test` (comunicazione di Giorgio, verifica SQL riuscita) |
| Collaudo live della FASE E (pertinenze) | **eseguito** il 07/10/2026: esiti in `roadmap/COLLAUDO-FINALE-A-H_REPORT.md` |
| **090** `090_cestino_contatti_1_contact_trash` (questa fase) | **applicata** su `stima360_db_test` (comunicazione di Giorgio, verifica SQL riuscita) |
| Collaudo live della FASE F (Cestino Contatti) | **eseguito** il 07/10/2026: esiti in `roadmap/COLLAUDO-FINALE-A-H_REPORT.md` |

Il runner applica le pendenti **in ordine**: su TEST un solo `apply` porta prima la 089, poi la 090. Il runbook `roadmap/CESTINO-CONTATTI-1_TEST_RUNBOOK.md` copre entrambe. Le due sono additive e indipendenti.

Fino all'applicazione, il codice nuovo su TEST funziona come prima:
- elenchi, ricerche e schede dei contatti sono invariati: i filtri leggono `deleted_at` via `to_jsonb`, quindi valgono anche senza la colonna;
- «Elimina…» del contatto e la scheda «Contatti» del Cestino rispondono **503 `TRASH_NOT_INSTALLED`**, con un messaggio leggibile.

## 1. Cosa c'è adesso

Lo stesso impianto del Cestino Immobili (085/086, `property/lifecycle.py`), senza un sistema parallelo:

- **Database (090):**
  - colonne `contacts.deleted_at`, `deleted_by_user_id`, `deleted_reason`, con gli stessi 5 motivi;
  - il registro append-only `record_lifecycle_events` accetta anche `entity_type = 'contact'`;
  - le guardie descritte al punto 4.
- **Backend:**
  - `core/contact_lifecycle.py`: controllo, sposta, ripristina, elenco;
  - `core/contact_trash.py`: predicati e rifiuto `CONTACT_IN_TRASH`;
  - 4 rotte in `core/router.py`:
    - `GET /api/core/contacts/{id}/deletion-check`
    - `POST /api/core/contacts/{id}/trash` con body `{reason_code, note}`
    - `POST /api/core/contacts/{id}/restore`
    - `GET /api/core/trash/contacts`
- **UI (Shell OS), con lo stesso foglio e la stessa pagina degli Immobili:**
  - «Elimina…» nella scheda contatto;
  - scheda in sola lettura con «Ripristina» per un contatto nel Cestino;
  - pagina Cestino con le schede **Immobili** (predefinita, invariata) e **Contatti**, raggiungibile anche con `#/cestino/contatti`.

Il **Cestino** serve per un contatto inserito per errore, doppio o non valido. Non sostituisce:
- l'archivio (`status = 'archived'`);
- la chiusura commerciale (lead, richieste, acquisizioni);
- l'anonimizzazione GDPR.

Non esiste nessuna cancellazione fisica, nessuna cascata e nessuno «svuota».

## 2. Matrice: quando il Cestino è permesso

Le regole sono ricalcolate sotto lock al momento della conferma (`FOR UPDATE` sulla riga del contatto).

### Blocchi assoluti

Valgono per chiunque. Ognuno porta l'elenco dei record con il collegamento, e nulla viene chiuso o scollegato in automatico.

| Condizione | Codice | Collegamento | Regola già adottata da cui deriva |
|---|---|---|---|
| Opportunità (lead) aperte o in pausa | `LEAD_OPEN` | `#/venditori` (vendita), altrimenti la scheda (tab Lead) | Immobili: «Smetti…» prima, mai chiusura silenziosa (2B1) |
| Referente di un'acquisizione aperta | `ACQUISITION_OPEN` | `#/acquisizioni/{id}` | `OWNER_OF_OPEN_ACQUISITION` (Fase 0) |
| Appuntamento futuro aperto (richiesto, fissato, confermato) | `FUTURE_APPOINTMENT` | `#/agenda/giorno/{data}` | `FUTURE_APPOINTMENT` degli immobili |
| Visita in programma fuori Agenda | `VISIT_SCHEDULED` | `#/immobili/{id}` | come sopra |
| Richiesta d'acquisto aperta (bozza, attiva, in pausa) | `BUY_REQUEST_OPEN` | `#/acquirenti/{id}` | processi aperti |
| Proposta d'acquisto in corso | `PROPOSAL_OPEN` | `#/acquirenti/{id}` | `PROPOSAL_OPEN` degli immobili |
| Vendita in corso, da venditore o da acquirente | `SALE_PENDING` | `#/immobili/{id}` | `SALE_PENDING` degli immobili |
| Proprietario/venditore di un immobile (non nel Cestino) **con incarico** di qualunque stato | `MANDATE_OWNER` | `#/immobili/{id}` | FIX-MANDATE-1 e `LAST_OWNER_WITH_MANDATE`. Se è un doppione, prima lo si rimuove dai proprietari (resta l'altro) |
| Task aperti | `TASK_OPEN` | `#/attivita` | processi attivi: nessuna chiusura automatica |
| Accesso al portale proprietario non disattivato | `OWNER_PORTAL_ACTIVE` | azione «Disattiva accesso», solo titolare o platform admin | **Decisione utente: «Blocca finché attivo»** |
| Già nel Cestino | `ALREADY_DELETED` | — | — |

### Storico operativo reale

Per un **agent** è un blocco (403 `HISTORY_REQUIRES_ADMIN`). Owner, admin e platform admin non si fermano. Il controllo lo mostra a tutti (`history`) e la conferma dice che resta consultabile.

Contano come storico:
- attività registrate (escluse quelle generate dal sistema e quelle «per errore»);
- lead concluse (non «per errore»);
- appuntamenti avvenuti, passati o annullati (non «per errore»);
- acquisizioni concluse;
- richieste e proposte concluse;
- vendite;
- visite;
- interazioni da acquirente;
- messaggi realmente inviati o ricevuti;
- un account del portale proprietario.

Non contano come storico:
- i collegamenti agli immobili;
- i ruoli;
- i consensi (registro legale: restano come sono).

### Chi può fare cosa

| Chi | Sposta nel Cestino | Ripristina | Vede nel Cestino |
|---|---|---|---|
| owner, admin, platform admin in acting | i contatti dell'agenzia | tutti | tutti i contatti nel Cestino dell'agenzia |
| agent | solo i contatti a lui assegnati, senza storico | solo quelli spostati da lui | solo quelli spostati da lui |
| altra agenzia | 404 (indistinguibile) | 404 | nulla |

## 3. L'operazione (una transazione)

### Sposta nel Cestino

1. Lock della riga, nello scope di chi chiede.
2. Ricontrollo di blocchi e storico.
3. **«Sospendi automazioni»** del contatto: lo stesso servizio della scheda Comunicazioni, sullo stesso cursore. Una pausa già presente resta com'è.
4. **Annullamento di tutti i messaggi in coda** del contatto (`cancel_reason = 'contact_trashed'`). Un messaggio già in consegna (`sending`) non si tocca. **Decisione utente: «Sospendi e annulla».**
5. Scrittura di `deleted_*`.
6. Evento `trash` nel registro: motivo, nota, stato precedente e conteggi delle comunicazioni.

Il foglio dice **prima** quanti messaggi verranno annullati e se le automazioni verranno sospese.

### Ripristino

- Riporta `deleted_*` a NULL e nient'altro: **stesso id**, relazioni intatte.
- Le automazioni restano sospese e i messaggi annullati restano annullati. Si riattivano a mano dalla scheda Comunicazioni; l'accesso al portale resta disattivato.
- Email e telefono non sono unici nel database, quindi non c'è un conflitto di unicità possibile. Il ripristino **segnala** i contatti attivi con la stessa email o lo stesso telefono (`possible_duplicates`), senza unire né modificare nulla.
- Evento `restore` nel registro.

## 4. Guardie nel database (090)

### Nessun collegamento nuovo verso un contatto nel Cestino

- Vale per INSERT, e per UPDATE che cambia il contatto, su:
  - `leads`, `property_contacts`, `buy_requests`, `owner_accounts`, `property_sale_sellers`;
  - `acquisitions.owner_contact_id`, `appointments`, `property_visits`, `contact_roles`.
- Il contatto si legge `FOR KEY SHARE`: un collegamento concorrente aspetta lo spostamento in corso e poi lo vede.

### Nessuna riapertura

Un processo chiuso di un contatto nel Cestino non torna aperto. Vale per:
- `leads`;
- `buy_requests`;
- `acquisitions`;
- `appointments`;
- `owner_accounts` (riattivazione).

### Riga congelata

Nessuna modifica alla riga di un contatto nel Cestino, salvo:
- la **proiezione del consenso**: una revoca da disiscrizione vale sempre;
- l'azione della FK di chi l'ha spostato.

### Eccezioni lasciate fuori di proposito

- I **registri** (consensi, ledger dei messaggi, timeline): una revoca deve poter arrivare sempre.
- Attività e task: i flussi di sistema li scrivono; l'operatore è fermato nel servizio.
- Le iscrizioni alle journey: il motore considera il Cestino «non attivo».

Il rifiuto porta il codice nel messaggio. `core_cursor` lo traduce in **409 `CONTACT_IN_TRASH`**: `{detail, code}` nei router Immobili, Acquirenti, Proposte, Vendite, OWNER, Acquisizioni e Agenda, solo `detail` nel router CORE, il cui `_translate` è certificato invariato.

## 5. Dove il contatto nel Cestino sparisce (e dove resta)

**Esce da:**
- elenco e ricerca Contatti (`GET /api/core/contacts`, usato anche dalla ricerca globale e da tutti i selettori);
- elenco e dashboard Acquirenti;
- graduatoria Abbinamenti;
- worklist Venditori;
- contatti della scheda immobile;
- selettore dei referenti in Acquisizioni;
- selettore contatti di OWNER Admin;
- «Oggi» (azioni consigliate);
- candidati al login del portale proprietario.

**Non viene ricollegato:**
- da una stima pubblica (bridge);
- da una prenotazione pubblica;
- dal sito (`site_sync`): nasce un contatto nuovo, oppure il collegamento si salta.

Le journey lo considerano «non attivo».

**Resta:**
- la scheda contatto (360), in sola lettura, con un riquadro «Nel Cestino» che dice chi, quando, il motivo e la nota;
- tutti i record collegati, consultabili dai loro posti.

**Revival database:** coperto dall'invariante. Richiede lead in pausa, che il Cestino vieta e che non si possono riaprire.

## 6. UI

- **Scheda contatto, «Elimina…»** apre il foglio condiviso (`trash/trash-dialog.js`, generalizzato; il foglio immobile genera lo stesso HTML di prima). Il foglio mostra:
  - i blocchi con le voci collegate;
  - «Disattiva accesso» quando il backend lo consente, che usa la rotta esistente `POST /api/owner/admin/accounts/{id}/disable` e poi ripete il controllo;
  - motivo e nota, gli effetti sulle comunicazioni e lo storico che resta.
  
  Dopo lo spostamento: toast e ritorno all'elenco Contatti.
- **Contatto nel Cestino:**
  - riquadro «Nel Cestino» con «Ripristina» solo se il backend dice `can_restore`;
  - nessun comando di modifica: niente modifica, attività, task, ruoli, assegnazione o lead;
  - comunicazioni in sola lettura.
  
  Dopo il ripristino la scheda si ridisegna e mostra i possibili doppioni con il loro collegamento.
- **Pagina Cestino:** schede Immobili e Contatti. La scheda Contatti si carica al primo tocco, con le stesse card, lo stesso «Ripristina» e lo stesso «Carica altri».
- **Smartphone:** bottom-sheet; azioni da 44 px; nessuno sforamento a 390 px, verificato in Chromium; anche a 1280 px.

## 7. Prove

| Suite | Esito |
|---|---|
| `tests/test_cestino_contatti_1_postgres.py`: PostgreSQL vero, schema completo, rotte vere | 11 passed |
| `tests/test_cestino_contatti_1.py`: statici e migration | 6 passed |
| `tests/test_cestino_contatti_1_ui.py`: funzioni pure, Shell vera in stub DOM, Chromium 390/1280 | 16 passed |
| Cestino Immobili esistente (`test_delete_arch_2b3_ui`, `test_fix_mandate_1_ui`) | invariato, passa |

La suite PostgreSQL copre, nell'ordine dei suoi test:
- contatto semplice, con registro e ripristino sullo stesso id;
- ogni blocco con il suo collegamento;
- storico: l'agent si ferma, il titolare no;
- permessi e agenzie;
- congelamento e nessuna riapertura;
- invisibilità operativa, compresa la prenotazione pubblica;
- comunicazioni sospese e annullate, che restano tali dopo il ripristino;
- portale proprietario da disattivare prima, senza riattivazione;
- possibili doppioni;
- concorrenza: due spostamenti, e spostamento contro un lead nuovo nei due ordini;
- codice senza la 090 e down.

Sentinelle aggiornate con il marcatore «SENTINELLA AGGIORNATA DA CESTINO-CONTATTI-1»:
- catena delle migration: 090 ultima, finestre allargate di uno, ledger 63 → 64, `glob("091*")`;
- superficie delle rotte CORE: più 4 rotte.

**Suite completa** (PostgreSQL 16 locale, prima del commit): 38 failed, 10945 passed, 115 skipped, 49 errors.

Rispetto alla base di FASE E, misurata sulla stessa macchina e anch'essa prima del commit, **nessuna regressione**. Le differenze sono:
- **7 sentinelle `git diff`**: lmc2 f4, lmc7 h4, lmc8 h3, lmc9 f3, lmc12 f4, lmc13 e3, lmc15 40. Falliscono solo perché il working tree non è committato e si verificano dopo il commit (§10).
- **`test_lmc3_valuation_snapshot_postgres::test_19`**: instabile e preesistente. Cerca la stringa `'82'` in una risposta che contiene un timestamp con i microsecondi. Rieseguito da solo, il modulo passa 3 volte su 3.

Le 49 `ERROR` (import legacy A30-6/A30-7 su PostgreSQL) sono le stesse della base.

## 8. Limiti dichiarati e scelte

- Il **dispatcher non è cambiato**. Un messaggio nuovo non può nascere per un contatto nel Cestino: `enqueue` rifiuta, con il contatto in `FOR KEY SHARE`, quindi aspetta uno spostamento in corso e lo vede. I `queued` vengono annullati nella stessa transazione. Resta solo un messaggio già in consegna (`sending`) al momento dello spostamento.
- L'elenco dei **task** non filtra i contatti nel Cestino. I task aperti bloccano lo spostamento, e dopo l'operatore non può crearne. Un task scritto da un flusso di sistema resterebbe visibile: è voluto, nessun lavoro nascosto.
- L'amministratore d'agenzia vede il blocco del portale, ma non l'azione: la superficie OWNER resta del titolare, come oggi.

## 9. Pendenti

1. Applicare su TEST **089 e 090**, con il runner e nell'ordine: runbook §1–§4.
2. Collaudo live **FASE E** (pertinenze): `roadmap/PERTINENZE-1_TEST_RUNBOOK.md` §5.
3. Collaudo live **FASE F** (Cestino Contatti): runbook §5.
4. PROD: esclusa.

## 10. Dopo il commit

Commit `dd9a88f`, spinto su `core-0.1-test`: deploy automatico su Render TEST. Ho rieseguito, a working tree pulito, i 38 test falliti nella suite completa:
- **17 ora passano**: le 7 sentinelle `git diff` di §7, le sentinelle d'inventario «nessuna migration» e git-status, e `lmc3 test_19` (instabile).
- **21 restano**, e sono tutti nella lista della base di FASE E:
  - P29-3 «file toccati» e «documento di design» (cercano `P29_2_0_COMMUNICATION_DESIGN.md`, che c'è solo sul Mac);
  - lmc15 41/42;
  - appuntamenti A30-1/2/8 su PostgreSQL;
  - next2 router;
  - followup isolation;
  - agenda realapp.

**Nessun fallimento fuori dalla base.**

Aggiornamento 07/10/2026: su Render TEST **089 e 090 sono applicate**; collaudi live E e F eseguiti (`roadmap/COLLAUDO-FINALE-A-H_REPORT.md`).
