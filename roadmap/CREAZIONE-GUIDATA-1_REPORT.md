# CREAZIONE-GUIDATA-1 — Creazione guidata immobile / censimento (FASE C)

Base: `core-0.1-test` @ `80b047d` (EDIFICI-1). Il commit contiene anche questo report.

- Nessuna migration.
- Nessun dato modificato.
- PROD non toccata.

Il push su `core-0.1-test` avvia il deploy automatico di Render TEST. Deploy e smoke live **non verificati** da qui: non ho accesso autorizzato a TEST.

## Decisione funzionale presa con Giorgio (6 ottobre 2026)

**Il tipo di scheda lo decide l'ingresso.**

- **Immobili › Commerciale «+ Nuovo immobile»**: le schede nascono commerciali (`crm`), con l'assegnazione di sempre.
- **Immobili › Censimento «+ Nuovo»** ed **Edifici «+ Nuovo»**: le schede nascono di censimento (`census`).

## Percorso utente

1. **Dove si trova?**
   - Comune: dal catalogo territoriale; regione e provincia vengono prese dal catalogo, nessun valore inventato.
   - Microzona: dipende dal Comune.
   - Via e civico.
   - Quante unità immobiliari ci sono nell'edificio: numero oppure «Non so». «Non so» non diventa mai 0 o 1.
   - Nome della palazzina, facoltativo.
   - Indicazione sul totale: comprende le unità principali e le pertinenze con subalterno proprio; esclude gli accessori senza sub; è il totale dell'edificio, non quante unità inserisci oggi.
   - Il Comune è l'unico dato obbligatorio.
2. **Che cosa stai inserendo?** Il percorso lo sceglie sempre l'operatore: indicare «1» non lo sceglie al posto suo.
   - **Unità autonoma**: scheda singola con il territorio già compilato, nessun edificio.
     - Da Commerciale si apre il form di sempre, `POST /properties`.
     - Dal censimento si apre il foglio unità di censimento.
   - **Palazzina con più unità**.
   - **Una sola unità in una palazzina**: ammessa anche con il totale sconosciuto.
3. **In quale edificio?**
   - Candidati della stessa agenzia nel Comune scelto, cercati sulla stessa via. Gli edifici con lo stesso civico compaiono per primi.
   - Per ogni candidato: nome, indirizzo, microzona, dichiarate e censite.
   - La ricerca si può modificare.
   - Scelte: «Usa questo edificio» (nessuna scrittura) oppure «Crea un edificio nuovo / distinto».
4. **Salvo il nuovo edificio?**
   - Riepilogo dei dati, poi «Salva edificio»: è l'unico punto in cui l'edificio viene salvato.
5. **Edificio salvato / scelto**: «Inserisci le unità» oppure «Fine per ora».
   - «Fine per ora» lascia l'edificio vuoto, ed è ammesso.
   - Se l'edificio esistente ha un totale dichiarato diverso da quello indicato, lo si segnala; l'edificio non viene modificato.
6. **Scheda edificio con il foglio unità già aperto**:
   - riepilogo e contatori dell'edificio;
   - indirizzo ereditato;
   - categoria «Da verificare» finché non è nota.
   - Dopo il salvataggio:
     - una barra con «Aggiungi un'altra unità» e «Apri scheda»;
     - il toast con «Annulla», come prima;
     - «Salva e aggiungine un'altra» resta disponibile.
   - Con l'ingresso da Commerciale, una nota dice che le unità nascono commerciali.

Dalla scheda di un edificio, «+ Aggiungi unità» (prima «+ Appartamento») parte dall'edificio corrente senza il passo 1.

## Salvataggio affidabile

- Spostarsi tra i passaggi non scrive nulla.
- «Indietro» conserva i dati, anche tornando dalla scheda singola.
- «Annulla» chiude senza cancellare nulla di già salvato.
- Edificio:
  - una chiave `client_request_id` per procedura, riusata nei ritentativi (rete caduta = «Riprova» senza un secondo edificio);
  - doppio clic = una sola richiesta;
  - dopo il salvataggio, tornare indietro non lo ricrea.
- Se l'unità fallisce dopo l'edificio, si riparte dalla scheda di quell'edificio.
- Unità: idempotenza, duplicato catastale bloccante e avviso sulla stessa posizione come prima, anche per le schede commerciali.
- «Duplica» non copia l'identità catastale né l'interno (regola di Fase 4, invariata).

## Componenti riusati e cambiamenti

- **Backend**: `POST /api/property/census/units` accetta `record_kind` (`census` di default, oppure `crm`) e `assigned_agent_id`.
  - L'assegnazione vale solo per `crm`, con le regole di `POST /properties`:
    - un agente se la assegna da sé, mai dal payload;
    - titolare e admin scelgono un agente attivo della stessa agenzia;
    - un operatore esterno all'agenzia dà 400.
  - Su una scheda di censimento l'assegnazione dà 422.
  - Invariati: percorsi lead e acquisizioni, presa in carico, PATCH/POST generici (`record_kind` resta rifiutato), guardie della 083.
- **Frontend**:
  - nuovo `census/census-wizard.js`;
  - funzioni pure in `census-model.js`;
  - `openUnitSheet` con `recordKind`, menu agenti, `onBack`, indirizzo precompilato;
  - `openPropertyDialog` con `seed` e `onBack`;
  - `edificio-dettaglio.js`: ingresso `#/edifici/{id}/aggiungi/crm|censimento` e barra «salvata»;
  - ingressi `immobili.js` ed `edifici.js`.
- **Sentinelle aggiornate** (marcatore «SENTINELLA AGGIORNATA DA CREAZIONE-GUIDATA-1»):
  - `test_censimento_3_backend` b01: `record_kind` ora ammesso su `CensusUnitCreate`;
  - `test_censimento_3_backend_postgres` 07;
  - `test_censimento_4_ui` c01/c02 (la palazzina nasce dalla procedura);
  - `test_censimento_4_rev2_ui` r6;
  - `test_crm_ops_2_property_form` e01 («+ Nuovo immobile» passa dalla procedura).

## Test eseguiti

- **PostgreSQL vero**: `tests/test_creazione_guidata_1_postgres.py`, 7 test.
  - unità commerciale in palazzina;
  - assegnazione;
  - censimento invariato;
  - idempotenza e duplicati;
  - riuso dell'edificio senza modifiche ed edificio vuoto;
  - separazione fra agenzie;
  - acquisizione sulla commerciale (e 409 sulla census).
- **UI simulata** (stub DOM in node): `tests/test_creazione_guidata_1_ui.py`, 8 test.
- **Browser locale** (Chromium + uvicorn + PostgreSQL veri, sostituita solo l'autenticazione): `tests/test_creazione_guidata_1_browser_postgres.py` a 1280 e 390 px.
  - palazzina con due unità commerciali;
  - riuso dello stesso edificio dal censimento;
  - unità autonoma commerciale;
  - nessuno scorrimento orizzontale.
  - Screenshot rigenerabili con `EDIFICI1_SHOTS=<cartella>`.
- **Suite completa PostgreSQL**: lista dei fallimenti identica alla base `80b047d` (preesistenti); nessuna regressione.

## Smoke live ancora pendenti (cumulativi)

- **FIX-MANDATE-1**:
  - motivazione del blocco Cestino;
  - incarico con «Dati da completare» in Incarichi;
  - rifiuto dello svuotamento del tipo;
  - firma LMC-15.
- **EDIFICI-1**:
  - voce Edifici, lista e filtri;
  - scheda con contatori;
  - unità → immobile → edificio;
  - ritorno con i filtri;
  - smartphone.
- **CREAZIONE-GUIDATA-1**:
  - i tre percorsi su TEST;
  - riuso di un edificio esistente;
  - unità commerciale in palazzina con assegnazione;
  - ritentativo dopo errore di rete;
  - smartphone.
