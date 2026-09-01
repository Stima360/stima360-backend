# P17 — Seller Intelligence Foundation — Audit & Design (READ-ONLY)

**Repository:** stima360-backend · **Fase:** Audit e design, nessuna implementazione · **Data:** 2026-09-01

Nessun file è stato modificato. Nessuna migration eseguita o creata. Nessun codice di produzione scritto. Tutte le affermazioni sotto sono state verificate leggendo direttamente `main.py`, `core/repository.py`, `core/service.py`, i file SQL in `migrations/`, i moduli `owner/`, `property/`, `flow/` e le viste in `static/os_shell/`.

---

## 1. Executive Summary

Il ponte STIMA → CORE **esiste già** ed è più maturo di quanto ipotizzato nel brief: ogni chiamata a `POST /api/salva_stima` innesca, in modo sincrono ma non bloccante, `core_service.bridge_public_stima()`, che deduplica il contatto per email/telefono normalizzati e crea sempre un nuovo `lead` (pipeline `general`, source `public_stima`) collegato alla stima tramite la tabella ponte `lead_stime`. Questo copre i punti 1–3 dell'obiettivo P17 (chi è il lead, quale immobile — parzialmente — quando è stata richiesta).

Quello che manca, e che è il vero oggetto di P17, è la **registrazione degli eventi nel tempo**. Oggi nessun evento viene scritto quando una stima è richiesta, completata, quando l'email parte, quando un link viene cliccato o quando un agente fa una telefonata: la tabella `activities` esiste ed è già collegabile a `stima_id`/`contact_id`/`lead_id`, ma nel flusso pubblico non viene mai scritta automaticamente — solo azioni amministrative manuali (es. feedback owner) la popolano oggi. Inoltre `activities` non ha una colonna `property_id`, e la vista Immobile lo dichiara esplicitamente nel proprio codice come limite noto.

La proposta (sezione 7) è di **non toccare le tabelle CORE esistenti** e introdurre una singola tabella dedicata e additiva, `seller_timeline_events`, con foreign key nullable verso `contacts`, `leads`, `stime`, `properties` e (in previsione di P20) `owner_accounts`. Il verdetto è che **P17 è realizzabile in modo completamente additivo**: un nuovo modulo `seller_intelligence/`, una nuova migration, due punti di innesto non bloccanti in `main.py` (stesso pattern già collaudato del bridge CRM) e una nuova tab nella scheda Contatto della SPA `os_shell`. Nessuna tabella CORE, nessun contratto API esistente, nessun comportamento di Proprietari/Immobili/Buyer/Match/Visite/Proposte viene toccato.

Due riserve, non architetturali ma di verifica dati, sono segnalate e da chiudere prima di scrivere la migration: (a) lo schema reale di `stime` sul DB include colonne (`consenso_marketing`, `consenso_marketing_at`) non presenti in nessuna migration tracciata nel repository; (b) il collegamento Stima/Lead → Immobile non è mai popolato automaticamente oggi, quindi una timeline "per immobile" richiederà una decisione a parte, non urgente per la fondazione P17.

---

## 2. Stato attuale del flusso Stima360.it → STIMA360 OS

Il flusso reale, verificato riga per riga in `main.py`, è il seguente — e corregge un'assunzione del brief: **il modulo `flow/` non è coinvolto nell'intake delle stime.** `flow/router.py` è protetto da `Depends(require_owner_admin)` ed è un motore di automazioni interne (regole FLOW‑R001…R007) che reagisce a eventi già presenti in CORE/PROPERTY/BUY/MATCH/OWNER; non riceve nulla da stima360.it.

Il percorso reale è:

1. Il sito stima360.it chiama `POST /api/salva_stima` (`main.py`, gestito da CORS limitato a `stima360.it`/`www.stima360.it`, nessuna autenticazione applicativa oltre CORS).
2. I dati anagrafici e immobiliari vengono inseriti con un `INSERT INTO stime` (`main.py:476-490`), che ritorna l'`id` della nuova riga.
3. Subito dopo, in un blocco `try/except` che **non fa fallire la richiesta** in caso di errore, viene chiamato `core_service.bridge_public_stima()` (`main.py:499-523`), che crea/riusa il `contact`, crea sempre un nuovo `lead` e li collega alla stima in `lead_stime`.
4. Un secondo blocco (`main.py:525-576`) aggiorna la riga `stime` con i campi di dettaglio immobile (anno, stato, vista mare, pertinenze, ecc.).
5. Viene generato un `token` UUID con scadenza 7 giorni (`main.py:578-592`), salvato su `stime.token`/`token_expires`, usato per il funnel "stima dettagliata" (`GET /api/prefill?t=`, `POST /api/salva_stima_dettagliata` → tabella `stime_dettagliate`, FK `stima_id`).
6. Il calcolo del valore vero e proprio avviene con `valuation.compute_from_payload()` (`valuation.py:709`), un motore di coefficienti puro in-memory (non scrive su DB da solo); il risultato viene scritto di nuovo nella riga `stime` con un `UPDATE`.
7. Viene generato un PDF (`pdf_report.py::genera_pdf_stima()`), caricato su GitHub (`github_upload.py`) per ottenere un URL pubblico. **Questo URL non viene mai persistito** in nessuna tabella — vive solo nella risposta JSON e nel corpo dell'email.
8. Vengono inviate due email via SMTP diretto (`database.py::invia_mail()`, righe 736-901 di `main.py`): una al cliente, una interna di alert. **Nessun invio viene loggato** — non esiste una tabella `email_log` né un campo `email_sent_at`.

Il modulo `flow/`, `whatsapp.py` e `register_whatsapp.py` esistono nel repository ma non risultano agganciati a questo funnel pubblico nel codice letto.

---

## 3. File e moduli coinvolti

| Percorso | Funzione | Perché è rilevante per P17 |
|---|---|---|
| `main.py` (righe 351-1090 ca.) | Endpoint pubblici `POST /api/stima_base`, `POST /api/salva_stima`, `GET /api/prefill`, `POST /api/salva_stima_dettagliata`, endpoint admin `/api/admin/stime*` | È l'unico punto di ingresso reale da stima360.it; è dove va innestato (in futuro) l'unico nuovo touch-point non bloccante per scrivere gli eventi P17 |
| `core/service.py` (righe 94-149) | `bridge_public_stima()` — prepara `contact_data`/`lead_data` | Punto di aggancio naturale: già risolve `contact_id`/`lead_id` per la stima, P17 può riusare lo stesso risultato senza ricalcolarlo |
| `core/repository.py` (righe 224-362) | Logica atomica del bridge: advisory lock, dedup email/telefono, insert `contacts`/`leads`/`lead_stime` | Mostra il pattern di lock/dedup da replicare per qualunque nuova scrittura collegata alla stessa `stima_id` |
| `core/repository.py` (riga 385, `create_activity`) | Insert generico in `activities` | Dimostra che l'infrastruttura CORE per "eventi" esiste già, ma è pensata per attività agente-driven, non per eventi di sistema ad alto volume (email aperta, link cliccato) |
| `database.py` | `crea_tabella_stime()`, `crea_tabella_stime_dettagliate()`, `crea_tabella_zone_valori()`, `invia_mail()`, funzioni `migrazione_*` | Definisce lo schema legacy `stime`/`stime_dettagliate`/`zone_valori` **fuori** dal sistema di migration numerato — fonte di verità incompleta (vedi rischio D-1) |
| `valuation.py` / `valuation_base.py` | Calcolo del valore stimato | Non scrive su DB; utile solo per capire cosa intendiamo per "valore prodotto" nell'evento "stima completata" |
| `pdf_report.py`, `cover_pdf.py`, `github_upload.py` | Generazione e hosting del PDF di stima | L'URL prodotto qui è il candidato naturale per il payload dell'evento "stima completata", dato che oggi si perde |
| `owner/repository.py` (righe 133-201) | `create_feedback()` — quando un owner scrive dal portale, crea anche un'`activity` CORE | Unico precedente reale di scrittura automatica in `activities`; nota: il campo `stima_id` qui è **hardcoded a `None`** (riga 161) — il collegamento owner→stima non esiste |
| `integration_owner_request.py` + `migrations/012_flow_owner_source.sql` | Bridge OWNER→FLOW (diverso dal bridge STIMA→CORE) | Da non confondere: riguarda il portale proprietario, non le stime pubbliche |
| `flow/repository.py` | Motore automazioni CORE/PROPERTY/BUY/MATCH/OWNER | Isolato dal flusso stima oggi; utile in futuro (P18 Automated Follow-up) come *consumer* della timeline P17, non come sorgente |
| `static/os_shell/assets/views/contatto-dettaglio.js` (357 righe) | Scheda Contatto unificata, 9 tab, dati da `GET /api/crm/contacts/{id}/360` | Target dell'integrazione frontend P17: tab "Attività" già esiste e già mostra `stima_id`-linked activities in tabella semplice, senza vera timeline visiva |
| `static/os_shell/assets/views/immobile-dettaglio.js` (1611 righe) | Scheda Immobile, 9 tab | Contiene un placeholder esplicito e commentato per la tab "Attività": *"Non è disponibile oggi un collegamento tra Attività e Immobile nelle API esistenti"* — conferma via codice il gap `property_id` |
| `static/os_shell/assets/views/attivita.js` (488 righe) | Vista globale "Attività" con tab "Cronologia" | Contiene già un motore di aggregazione multi-fonte (`{kind, data, when}` + sort) riutilizzabile come base per un componente timeline condiviso |
| `static/os_shell/assets/components/st-table.js` | Componente tabella/badge condiviso | Convenzione di riuso già stabilita nel progetto ("introdotto quando riusato da 3+ punti") — modello da seguire per un futuro `timeline.js` |
| `tests/test_public_stima_core_crm_bridge.py` | Test del bridge STIMA→CORE esistente | Modello diretto per il test di non-regressione del futuro bridge P17 |

---

## 4. Database map

### Tabelle CORE (`migrations/001_core_contacts_leads.sql`)

| Tabella | PK | FK principali | Note rilevanti per P17 |
|---|---|---|---|
| `contacts` | `id BIGSERIAL` | — | `email_normalized`/`phone_normalized` indicizzati, usati per dedup |
| `contact_roles` | `id` | `contact_id → contacts(id)` CASCADE | ruolo `owner` è qui, non su una tabella separata |
| `leads` | `id` | `contact_id → contacts(id)` RESTRICT | `pipeline`, `stage`, `source` — ogni stima crea un lead `source='public_stima'` |
| `lead_stime` | `id` | `lead_id → leads(id)` CASCADE, `stima_id → stime(id)` CASCADE | **ponte già esistente** STIMA↔CORE, `relation_type` (origin/related/follow_up) |
| `activities` | `id` | `contact_id` SET NULL, `lead_id` SET NULL, `stima_id → stime(id)` **CASCADE** | `activity_type` CHECK limitato a 8 valori (note/call/email/whatsapp/meeting/valuation/status_change/system) — non copre "email aperta"/"link cliccato"/"sopralluogo"/"incarico" senza ALTER |
| `tasks` | `id` | come sopra | stesso pattern di `activities` |

### Property (`002`, `003`)

`properties` (PK `id`, UNIQUE `code`) · `property_contacts` (FK `property_id`, `contact_id`) · **`property_leads`** (FK `property_id → properties`, `lead_id → leads`, `relation_type` include già il valore `'origin'` — pensato esattamente per "lead nato da una richiesta", ma **nessun codice lo popola oggi**) · `property_documents`, `property_photos`, `property_visits`, `property_price_history`, `property_status_history`.

### Buy / Match / Proposal / Sale (`004-007`, `013`, `016`)

`buy_requests` (FK `contact_id`, `lead_id`) · `matches` (FK `buy_request_id`, `property_id`) · `property_proposals` (FK `match_id`) · `property_sales` (FK `property_id`, `buy_request_id`, `proposal_id`). Nessuna di queste tabelle ha o necessita riferimenti a `stime` per P17.

### Owner (`009`, `010/011`, `014/015` prod)

`owner_accounts` (PK `id`, **FK `contact_id → contacts(id)` UNIQUE** — un contatto ha al massimo un account owner, creato solo manualmente da admin) · `owner_property_access` (FK `owner_account_id`, `property_id`) · `owner_access_tokens`, `owner_sessions`, `owner_publications`, `owner_feedback`, `owner_shared_documents`, `owner_notifications`. Nessun collegamento verificato a `stima_id`.

### Legacy, fuori dal sistema di migration (`database.py`)

`stime` (PK `id SERIAL`) — dati anagrafici + immobile + `token`/`token_expires` + `lead_status`/`note_internal`, **più** `consenso_marketing`/`consenso_marketing_at` scritte da `main.py` ma **assenti da ogni file di schema tracciato** (rischio D-1, vedi sotto). `stime_dettagliate` (FK `stima_id → stime(id)`, nessun `ON DELETE` esplicito). `zone_valori` (lookup prezzo/mq).

### Rischi di duplicazione/regressione già presenti nello schema (pre-esistenti, non introdotti da P17)

- **D-1 — schema non tracciato:** `consenso_marketing`/`consenso_marketing_at` su `stime` non sono in nessuna migration né in `database.py`'s DDL letto — probabilmente aggiunte fuori banda sul DB reale. Qualunque logica P17 che dipenda da questi campi deve prima verificarli sul DB vivo.
- **D-2 — script orfano:** `migrate_add_token.py` in root altera una tabella `stima` (singolare) che non esiste; la funzione realmente usata è `database.migrazione_allinea_stime()` su `stime` (plurale). Sembra uno script abbandonato.
- **D-3 — cascata distruttiva silenziosa:** `POST /api/admin/stime/delete` esegue `DELETE FROM stime`; poiché `lead_stime.stima_id`, `activities.stima_id`, `tasks.stima_id` sono tutte `ON DELETE CASCADE`, cancellare una stima dall'admin legacy elimina silenziosamente anche il link CRM e le eventuali attività/task collegate, senza avviso.
- **D-4 — file duplicato:** `010_owner_02_p1.sql` esiste identico sia in root sia in `migrations/` — la copia in root va considerata residuo di lavoro, non fonte di verità.

Nessuno di questi punti va corretto in questa fase, per istruzione esplicita del brief; sono segnalati perché condizionano le scelte della sezione 7.

---

## 5. Cosa esiste già che possiamo riutilizzare

- **Il bridge STIMA→CORE** (`core.service.bridge_public_stima`) è già un servizio isolato, testato, con lock transazionale e dedup robusta: qualunque estensione P17 dovrebbe agganciarsi *dopo* di esso, riusando `contact_id`/`lead_id` già risolti, invece di duplicare la logica di identità.
- **`lead_stime`** è già il ponte ufficiale stima↔lead; non serve ricrearlo.
- **`activities`/`tasks`** dimostrano il pattern architetturale corretto (FK nullable multiple, CHECK "almeno un riferimento non nullo", indici su ogni FK + su data) — pattern da replicare identico per la nuova tabella P17, senza toccare queste due tabelle.
- **`property_leads.relation_type='origin'`** è già lo slot semantico pensato per "immobile nato da una richiesta di stima/lead" — inutilizzato ma pronto.
- **Il pattern try/except non bloccante** attorno alla chiamata bridge in `main.py:499-523` è il modello esatto da copiare per qualunque nuova scrittura P17 dentro `/api/salva_stima`, per garantire che un errore nella timeline non rompa mai il salvataggio della stima.
- **Lato frontend**, `attivita.js` contiene già un motore di aggregazione/rendering multi-fonte ordinato per data, e `st-table.js` è un componente condiviso pensato per essere riusato — entrambi estraibili in un componente `timeline.js` senza toccare le viste esistenti.
- **Il pattern "vista dettaglio a tab"** identico in `contatto-dettaglio.js` e `immobile-dettaglio.js` (array `{key,label}` + `switch` in `showTab()`, tab lazy-loaded) rende l'aggiunta di una nuova tab un'operazione puramente additiva.

---

## 6. Cosa manca per P17

- Nessun evento viene scritto oggi quando una stima è **richiesta** o **completata**: il bridge scrive solo `contacts`/`leads`/`lead_stime`, mai una riga in `activities`.
- Nessuna persistenza dell'**invio email** (né dell'apertura o click, che oggi non sono nemmeno tracciati/tracciabili — nessun pixel, nessun redirect con token).
- Nessuna persistenza dell'**URL del PDF** generato.
- `activities.activity_type` non copre semanticamente gran parte del vocabolario richiesto da P17 (email aperta, link cliccato, whatsapp ricevuto, sopralluogo, incarico, immobile acquisito) — estenderlo richiederebbe un `ALTER` su una tabella CORE, cosa che il brief chiede di evitare se esiste un'alternativa più sicura.
- Nessuna colonna `property_id` su `activities`/`tasks` — il collegamento stima→immobile non è rappresentabile con le tabelle attuali senza un intervento.
- Nessun endpoint espone oggi una "timeline" aggregata per contatto/lead/stima; la tab "Attività" di `contatto-dettaglio.js` mostra solo `activities` grezze in tabella.

---

## 7. Proposta architetturale P17

### Principio guida

Non estendere `activities` (tabella CORE, condivisa con il flusso agente-driven già in produzione) e non toccare `stime`/`properties`/`owner_accounts`. Introdurre **una singola tabella nuova e dedicata**, di proprietà di un nuovo modulo isolato, che registra eventi ad alto volume e vocabolario aperto senza mai richiedere un secondo `ALTER` su una tabella CORE per ogni nuovo tipo di evento che arriverà con P18-P24.

### Nuova tabella: `seller_timeline_events`

Riferimenti tutti **nullable** e tutti **`ON DELETE SET NULL`** (non `CASCADE`, a differenza del pattern usato in `activities`/`tasks`): una cancellazione amministrativa di una stima legacy (rischio D-3 sopra) non deve far sparire silenziosamente la cronologia — la riga evento resta, solo il riferimento si azzera.

- `id BIGSERIAL PRIMARY KEY`
- `contact_id BIGINT REFERENCES contacts(id) ON DELETE SET NULL`
- `lead_id BIGINT REFERENCES leads(id) ON DELETE SET NULL`
- `stima_id INTEGER REFERENCES stime(id) ON DELETE SET NULL`
- `property_id BIGINT REFERENCES properties(id) ON DELETE SET NULL` — non popolata inizialmente, ma presente da subito per non richiedere una nuova migration quando P20/P21 ne avranno bisogno
- `owner_account_id BIGINT REFERENCES owner_accounts(id) ON DELETE SET NULL` — idem, in previsione di P20 Property Watch
- `event_type VARCHAR(50) NOT NULL` — vocabolario aperto (stima_richiesta, stima_completata, email_inviata, email_aperta, link_cliccato, whatsapp_inviato, whatsapp_ricevuto, telefonata, nota_agente, appuntamento, sopralluogo, incarico, immobile_acquisito, ...), con un CHECK iniziale ampio *su questa sola tabella* (mai su CORE) e possibilità di estenderlo in futuro con una migration isolata
- `event_source VARCHAR(30)` — `stima360_it` / `admin` / `system` / `owner_portal` / `whatsapp` / `email_provider`
- `occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
- `payload JSONB NOT NULL DEFAULT '{}'` — valore prodotto, url PDF, id messaggio email, testo nota, ecc.
- `created_by VARCHAR(200)` — utente agente per eventi manuali, NULL per eventi di sistema
- `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
- `CHECK (contact_id IS NOT NULL OR lead_id IS NOT NULL OR stima_id IS NOT NULL OR property_id IS NOT NULL OR owner_account_id IS NOT NULL)` — stesso pattern già usato in `activities_reference_chk`/`tasks_reference_chk`
- Indici su ciascuna FK + su `occurred_at DESC` + su `event_type`, sul modello esatto di `idx_activities_*`

### Nuovo modulo applicativo: `seller_intelligence/`

Segue la stessa convenzione degli altri domini (`router.py`, `service.py`, `repository.py`, `schemas.py`), isolato, senza dipendenze da `core/`, `property/`, `owner/` oltre alla lettura via FK.

- `record_event(...)`: funzione pura, riusa il pattern di `core.repository` (lock/dedup non necessari qui, solo insert append-only).
- `GET /api/seller-intelligence/timeline?contact_id=&lead_id=&stima_id=&property_id=&owner_account_id=`: ritorna gli eventi ordinati, protetto con lo stesso schema di autenticazione già usato da `core/router.py`.
- `POST /api/seller-intelligence/events`: creazione manuale (nota agente, telefonata) da UI admin — additiva, non sostituisce `POST /api/core/activities` che resta per le attività CRM generiche.

### Due soli punti di innesto in `main.py`, entrambi non bloccanti

1. Subito dopo la chiamata esistente a `core_service.bridge_public_stima()` (`main.py:499-523`): registrare `stima_richiesta`, riusando `contact_id`/`lead_id` già risolti dal bridge — stesso blocco try/except, stesso stile di log.
2. Vicino al blocco email esistente (`main.py:736-901`, dopo il calcolo e la generazione PDF): registrare `stima_completata` (con `payload` = valore calcolato) ed `email_stima_inviata` (con `payload` = URL PDF, finalmente persistito da qualche parte).

Nessun altro file di dominio esistente (`core/`, `property/`, `buy/`, `match/`, `proposal/`, `owner/`, `flow/`) viene toccato.

### Integrazione frontend

Una nuova tab `Seller Intelligence` (o `Timeline`) in `contatto-dettaglio.js`, aggiunta come singola entry nell'array tab esistente + un `case` nello `switch` di `showTab()` — pattern già usato per le altre 9 tab, zero refactoring. Il rendering riusa `st-table.js` per i badge e un nuovo componente condiviso `timeline.js`, estratto e generalizzato dal motore di aggregazione già scritto (ma non condiviso) in `attivita.js`. La tab equivalente su `immobile-dettaglio.js` **non è raccomandata in questa fase**: dato che `property_id` non è oggi derivabile in modo affidabile, mostrare una tab vuota o parzialmente popolata rischia di comunicare dati fuorvianti; meglio lasciare il placeholder attuale finché una fase successiva non disegna come derivare l'evento-immobile (es. da `property_contacts` → `contact_id`, o da un futuro popolamento di `property_leads.relation_type='origin'`).

### Sulla domanda architetturale principale

Il modo più sicuro e additivo è: **non promuovere mai automaticamente** una stima a `owner_account` o a `property` — quella resta una decisione manuale dell'agente (come è oggi), e P17 si limita a rendere osservabile la storia di ogni lead/contatto nel tempo. Questo evita di popolare il CRM con record speculativi per ogni richiesta di valutazione anonima (la maggioranza delle quali non converte), e mantiene `properties`/`owner_accounts` come fonti di verità intenzionalmente curate da un umano.

---

## 8. Impatto sul sistema esistente

L'impatto è circoscritto a: una nuova tabella (nessun `ALTER` su tabelle esistenti), un nuovo modulo `seller_intelligence/` (nessuna modifica a moduli esistenti), due aggiunte non bloccanti in `main.py` nello stesso stile del bridge CRM già in produzione, e una singola tab aggiuntiva in una vista frontend. Il contratto di `POST /api/salva_stima` (richiesta e risposta) resta identico byte per byte per stima360.it. Nessuna tabella CORE, nessun endpoint esistente, nessun comportamento di Proprietari/Immobili/Buyer/Match/Visite/Proposte/email-stima viene alterato.

---

## 9. Rischi di regressione

- Se il nuovo insert di eventi non replica esattamente il pattern try/except già usato per il bridge CRM, un errore nella scrittura della timeline potrebbe far fallire l'intera richiesta pubblica di stima — da evitare con lo stesso schema di gestione errori non bloccante.
- `/api/salva_stima` esegue già multiple query sequenziali non transazionali; due ulteriori scritture aumentano la latenza percepita sul sito pubblico — da misurare prima e dopo l'integrazione.
- Eventi passivi ad alto volume (email aperta, link cliccato, se in futuro tracciati) possono far crescere rapidamente la tabella — serve una strategia di indicizzazione/retention pensata da subito, anche se non implementata in P17.
- Rischio di confusione UX tra la tab "Attività" (CRM, agente-driven) e la nuova "Seller Intelligence" (eventi di sistema) se il naming non è chiaro fin dall'inizio.
- Rischio pre-esistente (non introdotto da P17, ma da tenere presente): la cascata `DELETE FROM stime` (D-3) già oggi elimina silenziosamente `lead_stime`/`activities`/`tasks` collegati; per questo `seller_timeline_events` è disegnata con `ON DELETE SET NULL` invece di `CASCADE`, per non estendere quel rischio al nuovo layer.
- Rischio pre-esistente (D-1): logica P17 che in futuro dipendesse da `consenso_marketing` deve prima verificare lo stato reale del DB, non fidarsi dello schema tracciato nel repository.

---

## 10. Test necessari (in una fase successiva di implementazione)

Test unitari su `seller_intelligence/repository.py::record_event` (creazione riga, FK nulle, CHECK "almeno un riferimento"); un test che verifichi che `POST /api/salva_stima` risponde comunque 200 anche se `record_event` solleva un'eccezione, sul modello di `tests/test_public_stima_core_crm_bridge.py`; test di idempotenza per evitare eventi duplicati in caso di retry futuri; test di migration up/down per `seller_timeline_events`; un test di regressione completo sul flusso esistente (email, PDF, token) per garantire che l'aggiunta del layer P17 non cambi output/comportamento attuali; un test che verifichi che la cancellazione di una `stima` tramite l'admin legacy lascia intatta (con riferimento azzerato) la riga di `seller_timeline_events`, a differenza di quanto accade oggi per `lead_stime`/`activities`/`tasks`; infine, lato frontend, verifica manuale/screenshot che l'aggiunta della nuova tab non alteri il rendering delle 9 tab esistenti in `contatto-dettaglio.js`.

---

## 11. File che si prevede di creare (non creati ora)

`migrations/017_seller_intelligence_01.sql` e relativo `_down.sql` · `seller_intelligence/__init__.py`, `repository.py`, `service.py`, `router.py`, `schemas.py` · `tests/test_seller_intelligence_repository.py`, `tests/test_seller_intelligence_bridge_non_blocking.py`, `tests/test_seller_intelligence_router.py` · `static/os_shell/assets/components/timeline.js`.

## 12. File esistenti che si prevede di modificare (non modificati ora)

`main.py` — due punti di innesto non bloccanti (dopo il bridge esistente per `stima_richiesta`; vicino al blocco email per `stima_completata`/`email_stima_inviata`) · `static/os_shell/assets/views/contatto-dettaglio.js` — una entry nell'array tab + un `case` nello `switch` di `showTab()`. Nessun altro file esistente, e in particolare nessun file in `core/`, `property/`, `buy/`, `match/`, `proposal/`, `owner/`, `flow/` o in `migrations/001-016`.

---

## 13. Ordine di implementazione consigliato (per fasi future)

1. Verificare sul DB reale lo schema effettivo di `stime` (colonne `consenso_marketing*`) prima di scrivere qualunque migration — chiudere il rischio D-1.
2. Creare `migrations/017_seller_intelligence_01.sql` (+ down) e testarla in isolamento su ambiente di test.
3. Costruire `seller_intelligence/repository.py` + `service.py` con `record_event()`, testato in unità, senza toccare `main.py`.
4. Aggiungere `seller_intelligence/router.py` (GET timeline, POST evento manuale), testato in isolamento, nessun consumer collegato ancora.
5. Integrare in `main.py` un solo touch-point alla volta, iniziando da `stima_richiesta`, con test di regressione su `/api/salva_stima` prima e dopo.
6. Aggiungere il secondo touch-point (`stima_completata`/`email_stima_inviata`), stesso schema di sicurezza, stesso test di regressione.
7. Estrarre il componente frontend condiviso `timeline.js` dal pattern già presente in `attivita.js`, testato da solo.
8. Aggiungere la tab "Seller Intelligence" in `contatto-dettaglio.js` — prima release visibile di P17.
9. Solo in una fase successiva (probabilmente già dentro P20/P21), valutare come derivare `property_id` per gli eventi e se/come estendere la vista Immobile.

---

## 14. Verdetto finale

**P17 può essere implementato in modo completamente additivo.** Non è necessaria alcuna modifica alle tabelle CORE esistenti (`contacts`, `leads`, `lead_stime`, `activities`, `tasks`) né ai moduli `property/`, `buy/`, `match/`, `proposal/`, `owner/`, `flow/`. L'unico contatto con codice esistente sarebbe `main.py` (due inserimenti non bloccanti, stesso pattern già collaudato del bridge CRM in produzione) e una singola vista frontend (aggiunta di una tab, zero refactoring).

Due punti non sono vincoli architetturali ma vincoli di verifica da chiudere prima di procedere: lo schema reale di `stime` sul DB non è interamente ricostruibile dal repository (D-1), e il collegamento automatico stima→immobile non è oggi popolabile in modo affidabile, quindi la timeline lato Immobile resta fuori dallo scope della fondazione P17 e va disegnata separatamente quando servirà (P20/P21).
