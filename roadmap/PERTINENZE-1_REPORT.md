# PERTINENZE-1 — Pertinenze nel percorso palazzina / unità (FASE E)

> **AGGIORNAMENTO COLLAUDO-FINALE-A-H (07/10/2026).** Le migration 089, 090, 091 e 092 sono **applicate** su `stima360_db_test` (comunicazione di Giorgio, verifica SQL riuscita). Il collaudo live è stato eseguito: esiti PASS / FAIL / NON VERIFICATO in `roadmap/COLLAUDO-FINALE-A-H_REPORT.md`. Le righe «non applicata» / «pendente» qui sotto descrivevano lo stato alla consegna e sono state aggiornate.

Base: `core-0.1-test` @ `744dad3` (CATALOGO-CANONICO-1, completamento). Il commit di questa fase contiene anche questo report.

**Stato verificato all'inizio:**
- il clone è allineato a `origin/core-0.1-test`, working tree pulito;
- su Render TEST Giorgio ha applicato la 087 e la 088, e le 28 colonne della dettagliata ci sono (sua comunicazione: non ho accesso per riverificarlo);
- il sito pubblico non è collegato a TEST: integrazione frontend e collaudo live del sito restano attività successive e questa fase non dipende da loro.

| | Stato |
|---|---|
| Codice (backend, Shell) | **implementato e testato localmente**: PostgreSQL vero, stub DOM, Chromium a 390 e 1280 px |
| Migration 089 su TEST | **applicata** su `stima360_db_test` (comunicazione di Giorgio, verifica SQL riuscita) |
| Collaudo live | **eseguito** il 07/10/2026: esiti in `roadmap/COLLAUDO-FINALE-A-H_REPORT.md` |
| PROD | esclusa |

## 1. Cosa c'era e cosa mancava

**Riusato, non duplicato.** Il modello della 083 e le azioni di CENSIMENTO-1:
- accessori `property_accessories` «Compreso» e «Da chiarire»;
- unità di censimento con `parent_property_id`;
- «Collega esistente», «Scollega» con lo storico su entrambe le schede;
- «Chiarisci › È separata» per creare o collegare l'unità;
- `building_id` per l'edificio.

Dalla fase D: quantità, mq con decimali, provenienza «Dal sito».

**Lacune trovate:**

1. **Pertinenza censita ma non collegata.** Un garage con sub proprio censito dalla palazzina, senza unità principale, contava come **principale**. Non c'era modo di dire «è una pertinenza, la collego dopo».
2. **Scollegamento.** La scheda tornava a contare come principale: la natura si perdeva con il collegamento.
3. **Garage e posto auto.** Diventando unità prendevano entrambi la tipologia `garage`, e la distinzione della fase D si perdeva.
4. **Accessorio → unità.** La provenienza («Dal sito», stato «Da chiarire», quantità) non si conservava. Inoltre la stima dettagliata che ridichiarava il garage poteva ricreare l'accessorio accanto all'unità: **doppio conteggio**.
5. **Scheda edificio.** Nessuna vista delle pertinenze collegate rispetto a quelle sconnesse.
6. **Scheda della pertinenza.** Non si poteva collegarla né scollegarla dal suo lato.
7. **Nessun limite sul ruolo.** «Aggiungi pertinenza» compariva anche su una pertinenza, e si poteva collegare qualcosa *a* una pertinenza non collegata.

## 2. Flussi realizzati

### 2.1 «Aggiungi pertinenza» (scheda dell'unità)

Prima **che cos'è**, poi la domanda sul subalterno:
- il tipo, dal catalogo degli accessori (box e posto auto distinti);
- i mq, con i decimali;
- **quanti**, quando pertinente (per esempio i balconi);
- le note.

La domanda è «Ha un suo subalterno (unità catastale autonoma)?» con tre risposte: **Sì / No / Da verificare**.

| Risposta | Cosa nasce | Conta come unità? |
|---|---|---|
| **No** | accessorio `included` dell'immobile | no, mai |
| **Da verificare** | accessorio `unknown`, badge «Da chiarire». I dati restano, nessun subalterno inventato, nessuna unità | no; compare fra gli «accessori da chiarire» |
| **Sì** | scheda autonoma collegata (`parent_property_id`, `is_pertinenza`, `pertinenza_kind`). Tipologia di partenza dal tipo (box e posto auto → `garage`, cantina → `storage`, sempre modificabile); mq e note passano al foglio; stessa palazzina dell'unità; vincoli catastali di sempre (identità completa duplicata → `CADASTRAL_DUPLICATE`) | sì, come pertinenza |

### 2.2 Pertinenza censita nella palazzina e non collegata

Nella scheda edificio, **«+ Pertinenza»** apre il foglio dell'unità in modo pertinenza:
- il tipo;
- **«Unità principale»** da scegliere fra le principali della palazzina, oppure **«Da collegare dopo»**.

La scheda nasce con `building_id` e `is_pertinenza`, senza principale. Il riepilogo dice «N principali + M pertinenze (K da collegare)»: **una pertinenza da collegare non conta mai come principale** e si conta una volta sola.

### 2.3 Collegamento successivo

È la stessa azione di sempre (`POST /properties/{principale}/pertinenze/link`), da tre punti:
- dalla **scheda edificio**: «Collega a…» sulla riga «Da collegare»;
- dalla **scheda della pertinenza**: «Collega a un'unità principale», che propone le principali della stessa palazzina (relazione reale, mai l'indirizzo) e «Cerca un'altra unità» per codice o indirizzo;
- dalla **scheda della principale**: «Collega esistente», come prima.

**L'edificio della pertinenza non cambia.** Un garage della palazzina di fronte può servire un appartamento di questa; la scheda lo dice «(in un altro edificio)».

### 2.4 Scollegamento senza perdita

«Scollega» si fa dalla principale (come prima) o dal lato della pertinenza, sempre in due tocchi.

**Cosa cambia:** solo la relazione corrente (`parent_property_id = NULL`). La scheda resta **pertinenza «da collegare»**, con il suo tipo.

**Cosa non cambia:**
- il record: nessuna archiviazione, nessuna cancellazione;
- l'edificio;
- proprietari, documenti, catasto, note;
- lo stato censimento/commerciale.

Lo storico si scrive su entrambe le schede, come prima.

### 2.5 Accessorio → unità autonoma

Passa da «Chiarisci › È separata». Per un accessorio «Compreso» c'è ora «Ha un suo sub?», che usa la stessa conversione.

- **Si conserva:**
  - tipo (`pertinenza_kind`);
  - superficie e note sulla scheda;
  - l'istantanea completa dell'accessorio (`metadata.from_accessory`: id, tipo, stato, **provenienza** — per esempio «stima360» —, superficie, quantità, note, data). La stessa istantanea va nello storico, nelle interazioni di sistema.
- **La scheda mostra** «Nata dall'accessorio «Garage / box» (dal sito Stima360, era «Da chiarire», 18.50 m²)».
- **L'accessorio sparisce nella stessa transazione:** nessun doppio conteggio.
- **Ramo «collega un immobile già censito».** La scheda collegata prende natura, tipo (se non l'aveva) e provenienza.

**Nessun doppio conteggio con il sito:**
- se la stima dettagliata ridichiara un tipo che su quella scheda è già un'unità autonoma collegata, l'accessorio **non si ricrea** e non si apre nessuna differenza;
- «Applica» su una differenza aperta prima della conversione risponde 409 `PERTINENZA_IS_UNIT` e non scrive nulla («Ignora» resta possibile).

Gli accessori arrivati dal sito restano «Da chiarire» finché l'operatore non ne verifica la natura, come dalla fase D.

### 2.6 Scheda edificio e scheda unità

- **Edificio.** Pannello **«Pertinenze»**, dalle relazioni reali:
  - **Collegate a un'unità**: codice, tipo, mq → principale (link; «in un altro edificio» se è così);
  - **Da collegare**: con «Collega a…».

  Le righe delle unità mostrano il tipo («Posto auto», non «Garage») e il badge «Pertinenza da collegare».
- **Unità principale.** «Pertinenze collegate» con tipo, mq, sub e categoria, link alla scheda, «Scollega».
- **Pertinenza.** Collocazione con **Natura** («Pertinenza autonoma · Posto auto»), unità principale (link) oppure «Da collegare», provenienza dall'accessorio, «Scollega da IMM-x». Non compaiono «Aggiungi pertinenza» né «Collega esistente»: una pertinenza non ha pertinenze, e il backend lo rifiuta anche per una pertinenza non collegata (`LINK_INVALID`).
- **Elenco Immobili.** Tipologia «Pertinenza · Posto auto».

## 3. Migration 089 (nuova, additiva)

`089_pertinenze_1_unit_nature.sql` (+ `_down.sql`). Il runner possiede la transazione.

- `properties.is_pertinenza BOOLEAN NOT NULL DEFAULT FALSE`: la natura, indipendente dal collegamento. **La regola** è `parent_property_id IS NOT NULL OR is_pertinenza`, quindi le pertinenze collegate prima della 089 restano pertinenze **senza backfill**.
- `properties.pertinenza_kind VARCHAR(30)`. Il CHECK usa lo **stesso elenco** dei tipi di accessorio (087) e ammette il tipo solo su una pertinenza. Un test confronta le due liste e lo schema.
- Indice parziale `idx_properties_building_pertinenze (building_id) WHERE is_pertinenza`.
- **Down:** si ferma se una scheda è marcata o ha un tipo; altrimenti toglie colonne, CHECK e indice.

**Codice prima dello schema.** Il push avvia il deploy subito:
- le **letture** passano da `to_jsonb(riga)` e valgono anche senza la 089 (tutto come prima);
- le **scritture** della natura si fanno solo con la 089 (`_ha_089`);
- una pertinenza dichiarata senza la 089 si rifiuta (409 `PERTINENZE_NOT_INSTALLED`, messaggio leggibile) invece di perderne la natura;
- collegare, scollegare e «Chiarisci» funzionano come prima: la provenienza va comunque nei `metadata`.

Provato sul database senza 089 (test 99).

## 4. Regole preservate

- **Contatori.** Censite = principali + pertinenze, una volta sola. Accessori, Cestino e annullate non contano, come prima. Il riepilogo aggiunge `units_pertinenze_unlinked`.
- **Isolamento.** Ogni id si cerca nell'agenzia dello scope: altrove è 404 (provato su collegamento e lettura).
- **Cestino.** Una pertinenza nel Cestino non conta, non si collega, non si scollega (provato).
- **Censimento / commerciale.** La presa in carico porta con sé le pertinenze **collegate**; quelle da collegare restano in censimento (provato).
- **Permessi.** Le regole di sempre (per esempio l'agente non assegnato non archivia: provato).
- **Nessuna deduzione.** Né edificio né principale dal solo indirizzo: una pertinenza con lo stesso indirizzo ma senza `building_id` non entra nella palazzina e non ha candidate (provato).
- **Idempotenza.** Un client di prima ha la stessa impronta di prima: i campi nuovi entrano solo se indicati.

## 5. Decisioni prese in autonomia

Nessuna decisione funzionale è rimasta aperta; le scelte tecniche:

- Lo **scollegamento non riporta a principale** una pertinenza. Era il comportamento certificato da EDIFICI-1 (test_07), cambiato perché contraddice «pertinenza censita ma non collegata» e «scollegare senza perdita». Sentinella aggiornata con motivazione.
- Le **candidate** per il collegamento sono le principali della **stessa palazzina**; le altre si cercano per codice o indirizzo (ricerca esplicita, mai proposta per indirizzo).
- **«Ha un suo sub?» anche sugli accessori «Compreso»**: un accessorio dichiarato compreso per errore si può rivedere senza cancellarlo e ricrearlo.

## 6. Test

- **`tests/test_pertinenze_1_postgres.py` (6, PostgreSQL vero, rotte vere):**
  - 01: Sì / No / Da verificare, decimali e quantità, catasto duplicato, una pertinenza senza pertinenze;
  - 02: da collegare, candidate, collegamento successivo, due collegamenti concorrenti;
  - 03: scollegamento con proprietario e documento intatti, ricollegamento in un altro edificio;
  - 04: accessorio → unità con provenienza «Dal sito», box / posto auto, ramo esistente, «Compreso» rivisto;
  - 06: agenzie, indirizzo, Cestino, presa in carico, permessi;
  - 99: senza 089, down e up.
- **`tests/test_pertinenze_1_site_postgres.py` (2, endpoint veri del sito):** accessorio del sito diventato unità non ricreato dalla dettagliata; «Applica» su una differenza precedente rifiutato. Controprova: con le due guardie disattivate entrambi falliscono.
- **`tests/test_pertinenze_1.py` (5, senza database):**
  - 089 per il runner, ultima e additiva;
  - tipi = accessori (087) = schema;
  - un solo elenco tipo → tipologia;
  - letture valide senza 089;
  - conteggio una volta sola.
- **`tests/test_pertinenze_1_ui.py` (6, stub DOM):**
  - funzioni pure;
  - foglio pertinenza (tipo, mq, quantità nascosta per «Sì», «Da verificare»);
  - scheda edificio (collegate / da collegare, «Collega a…», «+ Pertinenza»);
  - scheda della pertinenza da collegare e collegata;
  - nessun elenco scritto a mano.
- **`tests/test_pertinenze_1_browser_postgres.py` (Chromium + uvicorn + PostgreSQL, 390 e 1280 px):** edificio → «Collega a…» → DB; «Aggiungi pertinenza» posto auto 12,5 m² Sì → DB; «Chiarisci» → DB; «Scollega» dal lato della pertinenza → DB. Nessuno scorrimento orizzontale, nessun errore di pagina. Screenshot in `/tmp/edifici1_shots/pertinenze_*`.
- **Sentinelle aggiornate** (marcatore «SENTINELLA AGGIORNATA DA PERTINENZE-1»):
  - **la 089 è l'ultima migration:**
    - catene: a30_1, a30_2p, a32_1, crm_ops_3, lmc15, lmc1b, p27_6, p29_2_1…5e, p29_3;
    - p29_1 (max 89), a32_2;
    - censimento_1, crm_ops_4, delete_arch 1a / 1b (`090*` assente) / 1c / 2b2;
    - lmc2 e lmc3 (finestra `[-22:]`);
    - censimento_2 (ledger 63);
    - catalogo_canonico_1 m02;
  - **contatori** con `units_pertinenze_unlinked`: censimento_3 (01, 12, 16);
  - **EDIFICI-1 test_07:** la pertinenza scollegata resta pertinenza (§5).

## 7. Limiti dichiarati

- **Differenze aperte prima della conversione.** Una differenza del sito sullo stesso tipo, aperta prima, resta visibile: «Applica» è rifiutato con il motivo, «Ignora» la chiude. Non le chiudo in automatico dalla conversione, per non toccare la provenienza da un altro modulo.
- **Cancellazione fisica di una scheda di destinazione.** Resta il limite della 087 (CATALOGO-CANONICO-1 §10.7), non toccato.
- **Sito pubblico.** Non coinvolto: nessuna modifica al funnel.

## 8. Suite completa (PostgreSQL locale, prima del commit)

**Risultato:** 30 failed, 10920 passed, 115 skipped, 49 errors (20:32). Il riferimento è il run dopo il commit `2624d5a` (23 failed, 49 errors).

- **Errori: identici.**
- **Nessun fallimento nuovo dal codice di questa fase.**
- **9 rossi in più, tutti controlli sul working tree non committato.** Vedono i file 089 non ancora tracciati e i moduli della Shell modificati:
  - a32_2 s14;
  - lmc7 h5, lmc8 h5, lmc9 f4, lmc11 h1, lmc13 e1, lmc15 test_37;
  - p27_7 d3, p29_3g test_18.

  Si riverificano dopo il commit (sezione sotto).
- **I 2 test fragili del run precedente** (lmc3 test_19, p29_3e test_07) questa volta sono verdi.

## Dopo il commit `bf1d163` (push normale su `core-0.1-test`)

- **I 9 controlli sul working tree passano** ad albero pulito: a32_2 s14, lmc7 h5, lmc8 h5, lmc9 f4, lmc11 h1, lmc13 e1, lmc15 test_37, p27_7 d3, p29_3g test_18. Rieseguiti con i loro file: 374 verdi.
- **Restano rossi solo 4 fallimenti, già presenti nella base:** lmc15 test_41 e test_42, p29_3g test_20 e test_21.
- **Deploy Render TEST:** avviato dal push, **non verificato** da qui. Finché la 089 non è applicata, TEST resta nello stato «codice prima dello schema» (§3): tutto come prima, una pertinenza dichiarata rifiutata in modo leggibile.
- **Migration 089 su TEST:** **applicata** su `stima360_db_test` (comunicazione di Giorgio, verifica SQL riuscita).
- **Collaudo live:** **eseguito** il 07/10/2026: esiti in `roadmap/COLLAUDO-FINALE-A-H_REPORT.md`.
- **Fase E chiusa qui.** Nessuna attività successiva iniziata.
