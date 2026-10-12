# CENSIMENTO-2 Fase A (A1–A5): report

Data: 2026-10-12 (correzioni D1–D4 incluse) · Base: `core-0.1-test` @ `36204da` (= origin) · Stato: **SOLO LOCALE, IN ATTESA DI AUTORIZZAZIONE AL COMMIT**.

Nessun commit, push o deploy. Nessuna migrazione. Nessuna scrittura su TEST o PROD. Nessun file protetto modificato. `main` (`c3af7d3`) non toccato.

## Funzionalità

### A1 — Verifica delle schede

Tre condizioni per scheda (`census_classes.verification_state`):

- **verified**: superficie e categoria entrambe con provenienza «verificata». Il vincolo della 100 garantisce che ognuna abbia tipo e data della fonte, legati all'identità catastale attuale.
- **partial**: una sola delle due è verificata.
- **to_verify**: nessuna delle due. Se la fonte registrata è di un'identità catastale diversa da quella attuale, la verifica non vale più e la scheda è segnalata **da riconfermare** (`needs_reconfirm`).

Scheda edificio: tre indicatori separati, ognuno con il suo conteggio:

- «Unità create»;
- «Schede complete»;
- «Schede verificate» (`verification: {units, verified, partial, to_verify, to_reconfirm}`).

Ogni interno porta il suo badge. Vincoli del database invariati.

### A2 — Conferma interno

- Gli interni generati in blocco restano provvisori: `metadata.census_batch.provisional_numbering`.
- Correggere il numero non toglie lo stato provvisorio.
- `confirm_internal_number` lo porta a `false` con `jsonb_set`. Il resto dei metadata resta com'è; stessa scheda, stesso id e codice, collegamenti intatti.
- Ogni correzione e ogni conferma finiscono nello storico.
- Interno mancante:
  - una conferma senza numero → 400 `INTERNAL_NUMBER_REQUIRED`;
  - senza numero nulla viene mostrato come provvisorio;
  - nessun identificativo catastale viene inventato.
- Stessa posizione (scala, piano, interno) già usata → 409 `SIMILAR_FOUND`, superabile con «Salva comunque». Scale diverse non entrano in conflitto.

### A3 — `PATCH /api/property/properties/{id}/census-data`

Gestisce in **una transazione**:

- identità catastale (sezione a tre stati);
- superficie e categoria;
- provenienze;
- fonte della verifica;
- interno e sua conferma.

Ordine dei passi:

1. Lock della riga nell'agenzia. Altra agenzia → 404; Cestino → 409 `PROPERTY_IN_TRASH`; archiviata → 409 `UNIT_ARCHIVED`.
2. Permessi: schede di censimento a ogni operatore dell'agenzia; schede commerciali con `lifecycle.require_manage` (403 `NOT_ASSIGNED`).
3. `expected_updated_at` → 409 `STALE_DATA`.
4. Validazioni:
   - catalogo della categoria e Belfiore;
   - provenienza senza dato;
   - «verificata» solo con identità completa, tipo e data;
   - fonte senza «verificata» → 400 `EVIDENCE_WITHOUT_VERIFIED`.
5. Duplicato catastale → 409 `CADASTRAL_DUPLICATE` con il codice esistente.
6. UN `UPDATE`, poi UNA attività `system` con `metadata.changes {campo: [prima, dopo]}`, tramite `census._attivita_sistema`. Agenzia, autore e data li scrive l'audit esistente.

Comportamento in caso di errore e casi particolari:

- Qualunque errore annulla tutto, storico compreso; questo vale anche per un errore dopo l'`UPDATE`.
- Una richiesta senza modifiche non scrive nulla.
- Lo stato commerciale non viene mai toccato.

`PropertyUpdate` (la PATCH generica) perde `surface_source`, `cadastral_category_source` e `cadastral_evidence_*`, che ora danno 422.

Altri percorsi di modifica, con guardie nel router:

- **PATCH generica e «Applica» del sito**: un valore protetto da una verifica (superficie; categoria; identità catastale) dà 409 `VERIFIED_DATA_LOCKED`. Rimandare lo stesso valore resta possibile.
- **Modifica di gruppo** (`composition.py`): una scheda verificata nella selezione → 409 per tutta la selezione, con `property_ids`.

### UI

Il modulo «Dati catastali e superficie» fa una sola chiamata.

- **Rifiuto**: il modulo resta aperto con i valori inseriti e mostra «Nessuna modifica salvata: motivo». Nessun messaggio di successo.
- **Esito incerto o `STALE_DATA`**:
  - la scheda viene riletta dal server;
  - il riquadro mostra «salvato X · inserito Y», oppure «coincidono»;
  - il successo non è mai dichiarato;
  - «Salva» riprova solo su scelta esplicita, rispetto allo stato appena riletto.
- **Interno**: campo «Interno» con la spunta di conferma, e pulsante «Conferma interno» nella Collocazione.
- **Senza la 100**: resta la PATCH generica di prima.

### A4

`roadmap/CENSIMENTO-2_FASE_A_GUIDA_EDIFICI_9_10.md` è una guida in sola lettura:

- SQL in `READ ONLY … ROLLBACK`, provato su PostgreSQL locale con lo schema completo;
- situazione dichiarata, schede, differenze, duplicati, passi manuali.

### A5

«Stato censimento» → «Affidabilità della dichiarazione». Campo `census_status` e valori invariati.

## File

**Modificati:**

- backend: `property/census_classes.py`, `property/composition.py`, `property/router.py`, `property/schemas.py`;
- certificatore: `scripts/p26_6_live_cert.py`;
- UI: `static/os_shell/assets/app.css`, `static/os_shell/assets/census/census-api.js`, `census-model.js`, `composition-model.js`, `composition-panel.js`, `property-census-tab.js`, `static/os_shell/assets/views/edificio-dettaglio.js`;
- test: `tests/test_censimento_2_composition_postgres.py`, `test_censimento_2_static.py`, `test_censimento_2_ui.py`, `test_censimento_3_backend.py`, `test_censimento_4_backend.py`, `test_p26_6_live_cert_script.py`.

**Nuovi:** `tests/test_censimento_2_fase_a_postgres.py`, `tests/test_censimento_2_fase_a_browser_postgres.py`, `roadmap/CENSIMENTO-2_FASE_A_GUIDA_EDIFICI_9_10.md`, questo report.

Tutti i file di `property/` e `static/` sono nell'elenco ammesso `CENSIMENTO2_PROPERTY` della sentinella Voice.

## Test

| Gruppo | Esito |
|---|---|
| Nuovi PostgreSQL A1–A3: 11 test (atomicità, duplicato, agenzie, permessi, storico, numerazione, conferma, indicatori, altri percorsi, esito incerto) | 11/11 |
| Nuovi Chromium a 390 e 1280 px: 8 test (indicatori ed etichetta, una chiamata, conferma interno, rifiuto, esito incerto con richiesta arrivata e risposta persa, scheda cambiata nel frattempo) | 8/8 |
| Suite CENSIMENTO-2, 3 e 4, certificatore live (sezione edifici) | 497 passati |
| Suite completa, base `36204da` | 593 failed + 391 errors = 984; 11.055 passed |
| Suite completa, Fase A | 993 falliti per nome = 984 preesistenti identici + 10 nuovi − 1 risolto (instabile). I 10 nuovi: 8 sentinelle di working tree e 2 test di router aggiornati (vedi sotto) |
| Sentinelle in un clone usa-e-getta con commit locale (mai pubblicato, cancellato), compresa quella dei file protetti Voice | 90/90 |

I 984 fallimenti preesistenti, per causa:

- 546: DOM simulato in node (`createElementNS`);
- 236: fixture in collisione («already exists»);
- 150: percorsi o socket del Mac;
- 5: sentinelle git;
- 47: altre cause, tutte presenti identiche nella base.

## Test esistenti aggiornati (cambio di comportamento richiesto, non per nascondere errori)

- `test_censimento_2_composition_postgres::test_01/13`: provenienza e fonte passano dalla rotta nuova. La PATCH generica dà 422 sulle provenienze e 409 sui valori verificati. «parziale» e «da verificare» sostituiscono `verified`/`to_reconfirm`/`None`.
- `test_censimento_2_static::test_c01` e `test_censimento_2_ui::test_m07`: le tre condizioni.
- `test_censimento_3_backend::test_c01`: le rotte passano da 59 a 60.
- `test_p26_6_live_cert_script` (cens3): +1 sonda anonima e +1 sonda ostile.
- `test_censimento_4_backend::test_02/04`: la PATCH generica passa dalla guardia; nel test senza database la guardia è neutralizzata, ed è provata su PostgreSQL vero.

## Problemi residui e rischi

1–4. Risolti: vedi «Correzioni D1–D4» in fondo.
5. Le sentinelle di working tree falliscono finché non c'è il commit; nel clone con commit passano.


---

# Correzioni D1–D4 (dopo la revisione del 2026-10-12)

## D1 — Permessi

La rotta census-data usa ora esattamente le autorizzazioni della PATCH generica che il modulo usava prima: ogni operatore dell'agenzia, su schede di censimento e commerciali, assegnate o no. È stato tolto `require_manage`.

Restano due regole di stato già richieste dalla Fase A:
- le schede **archiviate** sono bloccate (409);
- le schede **nel Cestino** sono bloccate (409).

La PATCH generica, invece, permette di modificare una scheda archiviata.

La modifica di gruppo resta com'era in `36204da`.

Test: `test_a11` confronta, per tre operatori e tre schede, la risposta della PATCH generica e quella di census-data (stesso esito, stesso codice). Lo stesso confronto vale per il 404 fra agenzie.

## D2 — Concorrenza

`property/service.py` e `property/site_sync.py` aprono una connessione propria. Per questo, senza toccarli, il controllo **non può stare nella loro stessa transazione**. Soluzione adottata, senza file protetti: un **lock di serializzazione per scheda**, `pg_advisory_lock(hashtextextended('census-data:property:{id}', 0))`.

- **PATCH generica e «Applica» del sito**: il router prende il lock di sessione **prima** di leggere, controlla lo stato già confermato e chiama il service. Il lock si rilascia solo **dopo** il commit del service, e cade comunque alla chiusura della connessione.
- **census-data**, l'unico percorso che scrive una verifica: prende lo stesso lock come lock di transazione, **prima** di leggere la riga con `FOR UPDATE`.
- **Modifica di gruppo**: controlla righe già bloccate `FOR UPDATE`, nella sua transazione.
- **Ordine dei lock**, uguale ovunque: prima l'advisory, poi la riga.
- Una PATCH che non tocca superficie, categoria, identità catastale o interno non prende alcun lock.

Test deterministici (eventi e `pg_stat_activity`, nessuna attesa a tempo):
- `test_a12`: la verifica attende la PATCH generica in corso e si applica al valore nuovo;
- `test_a12b`: la PATCH generica e «Applica» attendono la verifica in corso, poi rifiutano con 409 e nulla viene scritto;
- `test_a12c`: dopo un errore del service non resta nessun lock.

**Prova di mutazione**: togliendo i due lock, `test_a12` e `test_a12b` falliscono sui dati.
- Nel primo caso la verifica viene persa in silenzio (`surface_source` torna `NULL`).
- Nel secondo la PATCH generica risponde 200 e cancella la verifica.

Con i lock, tutti e tre passano.

Limite dichiarato: è serializzazione con un lock condiviso, non un'unica transazione. Copre ogni percorso del codice che può cambiare un valore verificato. Non copre SQL manuale sul database.

## D3 — Interno

- La PATCH generica rifiuta un interno **diverso** da quello salvato con 409 `INTERNAL_NUMBER_TRACKED`, nulla scritto, sotto lo stesso lock. Lo stesso valore, e gli altri campi, si salvano come prima.
- `census-api.updateProperty`, l'unico chiamante dell'interfaccia che manda l'interno (recupero R7 di una scheda appena creata), lo invia alla rotta tracciata con `confirm_similar` (nessun avviso di posizione, come prima) e il resto alla PATCH generica.
- Senza la 100, tutto resta come prima.
- La creazione di unità (POST) non cambia.

Test: `test_a13` e il test R7b della UI.

## D4 — Etichette

- **Verifica** (sempre con la parola «Verifica»): «Verifica completa», «Verifica parziale», «Verifica da completare», «Verifica da riconfermare». Indicatore: «N parziali · N da completare (N da riconfermare)».
- **Categoria assente**: «Categoria mancante» nel badge delle unità e nei segnali della scheda edificio («N categorie mancanti»), «Mancante» nel dato della scheda.
- Nella scheda edificio non compare più «Da verificare».

## Test dopo le correzioni

- **Mirati** (Fase A PostgreSQL, Chromium 390/1280, CENSIMENTO-2, 3 e 4, certificatore, catalogo, pertinenze, edifici): 531 passati. Gli unici fallimenti sono `test_edifici_1_browser_postgres::test_b01[390, 320]`, che falliscono identici nella base.
- **UI in node**: le suite falliscono in base per lo stub del DOM. Le ho eseguite in una copia usa-e-getta con lo stub completato (`createElementNS`), sia sulla base sia sulla Fase A: 74/74 sulla base, 75/75 sulla Fase A.
- **Suite completa**:
  - Fase A: 991 fallimenti per nome;
  - base: 984 fallimenti;
  - le differenze sono solo le **8 sentinelle del working tree** (nuove) e 1 test instabile risolto;
  - nessuna regressione di prodotto.
- **Clone usa-e-getta** con commit locale (mai pubblicato, cancellato): le 8 sentinelle e le sentinelle STIMA Voice (`test_voice_1_perimeter`, `test_voice_4_undo_postgres::test_n1`) passano. `test_p29_3g::test_21` fallisce anche nella base (documento di design assente nel repository).

## Test esistenti aggiornati in questa revisione (cambio voluto, con commento nel test)

- `test_censimento_4_backend` 02/04: nuova firma della guardia;
- `test_censimento_4_rev2_ui::r7b`: l'interno passa dalla rotta tracciata;
- `test_censimento_4_ui` c03/c06 ed `test_edifici_1_ui` 05: etichette D4 e A5;
- `test_censimento_2_browser_postgres::b02`: «Verifica da riconfermare».

---

# Ultime correzioni autorizzate (2026-10-12, pomeriggio)

## Sincronizzazione dal sito: eccezione limitata

`property/site_sync.py::_applica`: una superficie verificata non si aggiorna più da sola. La differenza diventa un conflitto «Applica / Ignora»; «Applica» è respinto (409 `VERIFIED_DATA_LOCKED`).

## Salvataggio in due chiamate

`census-api.js` e `census-model.js`: l'errore distingue i dati salvati, non salvati e con esito incerto. Il modulo resta aperto e non compare nessun messaggio di successo.

## Sentinella STIMA Voice

`tests/test_voice_4_undo_postgres.py::test_n1` ha un'eccezione vincolata al contenuto: `site_sync.py` deve essere identico alla base oppure alla base con esattamente la modifica approvata, nel punto previsto di `_applica`.

Simulazioni in un clone usa-e-getta: il diff autorizzato e il file originale passano. Falliscono, come previsto:
- un'altra modifica nel file o in un'altra funzione;
- le sole righe aggiunte;
- la sola riga sostituita;
- una condizione alterata;
- una riga tolta;
- le righe fuori posto;
- un altro file di `property/`, un file PROTETTO, una nuova migrazione.

Il primo giro di simulazioni non era valido (il modulo si saltava senza `P29_TEST_DSN`); il secondo, con il controllo dei test saltati, sì.

## Test

- Sito, superfici verificate, concorrenza, salvataggio parziale, catalogo, importazione: 94 passati.
- Suite completa: 991 fallimenti contro i 984 della base. Gli unici nuovi sono le 8 sentinelle del working tree, che passano nel clone con commit locale insieme a tutte le suite STIMA Voice (578 passati, nessun test saltato).

## Correggere volontariamente una superficie verificata

Si usa il modulo «Dati catastali e superficie» della scheda (rotta `census-data`), in una transazione con lo storico. Due casi:
- **nuovo valore riconfermato** come «Verificata», con la fonte consultata (tipo e data);
- **nuovo valore con un'altra provenienza**: la verifica viene tolta in modo esplicito.

Lo storico registra valore e provenienza, prima e dopo; la fonte precedente resta. Il conflitto del sito ancora aperto si chiude poi con «Ignora».
