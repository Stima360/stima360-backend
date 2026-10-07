# CATALOGO-CANONICO-1 — Catalogo canonico Stima360 e allineamento sito → CRM (FASE D)

Base: `core-0.1-test` @ `3d3c3c7` (CREAZIONE-GUIDATA-1), primo commit `92a2dc7`. Il **completamento mirato** (§10, prima di PERTINENZE) è un secondo commit sopra `92a2dc7`. Ogni commit contiene anche questo report.

- **Migration nuove: 087 e 088** (additive). Vanno applicate su TEST: comandi completi in `roadmap/CATALOGO-CANONICO-1_TEST_RUNBOOK.md`.
- Nessun dato esistente modificato, nessun backfill delle stime storiche.
- PROD e sito pubblico non toccati.
- Motore di valutazione invariato.

Il push su `core-0.1-test` avvia il deploy automatico di Render TEST. Deploy, migration e smoke live **non verificati** da qui: non ho accesso autorizzato a TEST.

**Stato in sintesi** (dettaglio in §10):

| | Stato |
|---|---|
| Codice (backend, Shell) | **implementato e testato localmente** su PostgreSQL vero e in Chromium |
| Migration 087 e 088 su TEST | **da applicare** (runbook pronto, non eseguito) |
| Confronto con i form reali del sito | **incompleto**: sorgente del sito non accessibile, elenco delle opzioni non verificate in §10.6 |
| Frontend del sito (`client_request_id`, `campi_dichiarati`) | **contratto pronto, non implementato**: il sito non è in un repository accessibile (§10.2) |
| Collaudo live | **pendente** |

## Decisione funzionale (Giorgio, 6 ottobre 2026)

- **Quando nasce l'immobile.** Automaticamente dalla prima stima salvata **con contatto e lead**, non dal calcolo anonimo (`/api/stima_base` non scrive nulla).
- **Come nasce.** Come **censimento**, con provenienza **Stima360**, senza incarico, senza agente, senza attivazione commerciale.
- **Stima dettagliata.** Aggiorna lo **stesso** immobile.
- **Ritentativi.** Lo stesso invio ripetuto non crea doppioni.
- **Correzioni dell'agente.** Un dato corretto a mano non si sovrascrive mai.
- **Stime diverse forse dello stesso immobile.** Il possibile doppione si **segnala** e si può **collegare esplicitamente**. Nessuna unione basata solo sull'indirizzo.

## 1. Contratto reale: fonti e versione verificata

**Versione verificata.** Il codice del ramo `core-0.1-test` @ `3d3c3c7`:

- `main.py`:
  - `/api/stima_base`;
  - `/api/salva_stima`;
  - `/api/prefill`;
  - `/api/salva_stima_dettagliata`.
- `valuation.py`: le parole che il motore distingue.
- `home_profile.py`: token delle pertinenze.
- `owner/home_update.py`: stati del motore.
- `core/repository.py::bridge_public_stima`.
- Schema delle migration e di `database.py`.
- Censimento del Project: `roadmap/P30-0_SITE_CRM_INTEGRATION_CENSUS.md`.

**Limite dichiarato.** Il sito pubblico stima360.it **non era consultabile** da questa sessione:

- la rete del contenitore risponde 403;
- la lettura web non è stata autorizzata.

Non ho aggirato il blocco. Le **etichette** delle opzioni dei form del sito non sono quindi verificate dal vivo. Il catalogo riconosce solo i valori che il backend dimostra; ogni altro valore si conserva grezzo, «Da verificare».

Non ho inviato lead di prova in produzione.

**Due fatti del codice decidono il disegno:**

1. **`salva_stima` riempie i campi mancanti con dei default prima di salvarli in `stime`:**
   - via «Zona»;
   - tipologia Appartamento;
   - piano 1;
   - 3 locali;
   - 1 bagno;
   - ascensore sì;
   - anno 2000;
   - stato buono;
   - mare «oltre» / 500-1000 / no;
   - mq delle pertinenze 0.

   In `stime` un default non si distingue da un dato dichiarato. La scheda si costruisce quindi **dal payload realmente inviato** (`raw`), mai dalla riga `stime`.
2. **`stime` tronca i decimali.** `mq` e i mq delle pertinenze sono INTEGER: 85,5 diventa 86, un garage di 18,5 m² diventa 18. La scheda conserva i due decimali.

## 2. Matrice sito → CRM

Legenda:
- **Prima**: dove finiva il dato nel CRM prima di questa fase.
- **Ora**: destinazione sulla scheda immobile.
- ✓: rappresentato, salvato e modificabile nella scheda.

### Stima rapida (`/api/salva_stima`)

**Territorio e indirizzo**

| Voce sito | Payload | Prima | Ora | Trasformazione |
|---|---|---|---|---|
| Regione / Provincia | (derivate dal comune) | — | `region`, `province` ✓ | dal catalogo territoriale: Comune → Provincia → Regione |
| Comune | `comune` | solo `stime` | `city` ✓ | nome canonico del catalogo (apostrofo tipografico); fuori catalogo resta com'è + «Da verificare» |
| Microzona | `microzona` | solo `stime` | `microzone` ✓ | solo se appartiene al comune, altrimenti «Da verificare» (grezzo conservato) |
| Via | `via` | solo `stime` | `address` ✓ | il segnaposto «Zona» del backend non è un indirizzo |
| Civico | `civico` | solo `stime` | `civic_number` ✓ | testo |

**Caratteristiche**

| Voce sito | Payload | Prima | Ora | Trasformazione |
|---|---|---|---|---|
| Tipologia | `tipologia` | solo `stime` | `property_type` ✓ | Appartamento / Villa / Rustico (parole del motore) e le etichette del CRM, compreso «Altro» scelto davvero. **Sconosciuta o non inviata → «Da verificare»**: valore tecnico `other` (la colonna è NOT NULL) più `metadata.site_unverified.property_type` con il valore del sito; scheda, elenco, descrizione e form mostrano «Da verificare (sito: «X»)», **mai «Altro»** (§10.5) |
| Mq | `mq` | `stime.mq` intero | `surface_sqm` ✓ (superficie principale) | due decimali; 0 = non dichiarato. Separata da `commercial_surface_sqm`, che non si tocca. Il sito non dice di che superficie si tratti: non la si chiama «calpestabile» |
| Piano | `piano` | default «1» | `floor` ✓ | terra → T, rialzato → R, seminterrato → S, ultimo / attico come testo, numeri |
| Locali | `locali` | «Trilocale» → default 3 | `rooms` ✓ | numero o Mono/Bi/Tri/Quadri/Pentalocale |
| Bagni | `bagni` | default 1 | `bathrooms` ✓ | intero 0-50 |
| Ascensore | `ascensore` | default sì | `elevator` ✓ | sì / no / non indicato (NULL) |
| Anno | `anno` | default 2000 | `year_built` ✓ | 1000-2200, altrimenti «Da verificare» |
| Stato | `stato` | default buono | `condition` ✓ | catalogo: nuovo, ristrutturato, buono, scarso, grezzo (= motore). Altro → «Da verificare» |

**Mare e descrizione**

| Voce sito | Payload | Prima | Ora | Trasformazione |
|---|---|---|---|---|
| Posizione mare | `posizioneMare` | solo `stime` | `sea_position` ✓ (087) | frontemare, seconda, oltre (nessuna unificazione di «fronte») |
| Distanza mare | `distanzaMare` | solo `stime` | `sea_distance` ✓ (087) | 0-100, 100-300, 300-500, 500-1000. Spazi, «m» e trattino lungo normalizzati come fa il motore |
| Fascia mare | `fascia_mare` | solo `stime` | `sea_band` ✓ (087) | testo del sito, senza interpretazione |
| Ferrovia / strada | `barrieraMare` | solo `stime` | `sea_barrier` ✓ (087) | sì / no / non indicato |
| Vista mare | `vistaMareYN` | solo `stime` | `sea_view` ✓ (087) | sì / no / non indicato; una vista descritta vale sì |
| Dettaglio vista | `vistaMareDettaglio`, `vistaMare` | solo `stime` | `sea_view_detail` ✓ (087) | testo del cliente. La categoria del motore (panoramica / parziale / scarsa) **non** si salva: è un calcolo |
| Altro / descrizione | `altroDescrizione` | solo `stime` | `other_features` ✓ (087) | testo del cliente |

**Pertinenze**

| Voce sito | Payload | Prima | Ora | Trasformazione |
|---|---|---|---|---|
| Garage | `pertinenze` «garage» + `mqGarage` | solo `stime` | accessorio `box` («Garage / box») ✓ | mq con decimali; «Da chiarire». Resta distinto dal posto auto |
| Posto auto | «posto auto» + `mqPostoAuto` | solo `stime` | accessorio `posto_auto` ✓ | idem |
| Cantina / Soffitta | + `mqCantina` / `mqSoffitta` | solo `stime` | `cantina` / `soffitta` ✓ | idem |
| Taverna | + `mqTaverna` | **non rappresentabile** | `taverna` ✓ (087) | idem |
| Balconi | + `numBalconi` | **non rappresentabile** | `balcone` ✓ (087) | `quantity` (087); «Compreso» |
| Terrazzo / Giardino | + `mqTerrazzo` / `mqGiardino` | solo `stime` | `terrazzo` / `giardino` ✓ | mq; «Da chiarire» |
| Piscina, posto moto, posto bici | dentro `pertinenze` | non rappresentabili | `piscina`, `posto_moto`, `posto_bici` ✓ (087) | riconosciuti anche dentro una frase, come fa il motore |
| Altro (pertinenza sconosciuta) | es. «posto barca» | — | «Da verificare» (grezzo) | **mai** trasformato in «Altro» |

**Non dati dell'immobile**

| Voce sito | Payload | Prima | Ora |
|---|---|---|---|
| Nome, cognome, email, telefono, consenso | | contatto + lead (bridge) | invariato. La scheda collega il contatto con ruolo **neutro** `contact`, mai proprietario d'ufficio, e il lead `origin`. Nessun dato di contatto viene copiato nella provenienza |
| Prezzo, €/m², valore pertinenze, base | risposta del motore | non persistiti | **non** diventano dati dichiarati |

### Stima dettagliata (`/api/salva_stima_dettagliata`)

| Voce sito | Payload | Prima | Ora |
|---|---|---|---|
| Ripetizione delle caratteristiche | tipologia, mq, piano, locali, bagni, ascensore, stato, anno, mare, pertinenze e mq, `altroDescrizione` | solo `stime_dettagliate` (su TEST le colonne mancano: le crea la 088) | la **stessa** scheda, con la regola per campo (§4). Un valore **uguale al precompilato** di `/api/prefill` non è una dichiarazione (§10.1) |
| Classe energetica | `classe` | solo `stime_dettagliate` | `energy_class` ✓ (catalogo A4…G; «non so» = NULL; fuori catalogo «Da verificare») |
| Riscaldamento | `riscaldamento` | | `heating` ✓ (087), testo del cliente |
| Climatizzazione | `condizionatore` | | `air_conditioning` ✓ (087), testo del cliente |
| Tipo di climatizzazione | `condiz_tipo` | | `air_conditioning_type` ✓ (087), testo del cliente |
| Esposizione | `esposizione` | | `exposure` ✓ (087), testo del cliente |
| Arredamento | `arredo` | | `furnishing` ✓ (087), testo del cliente |
| Spese condominiali | `spese_cond` | | `condo_fees` ✓ (087): l'importo com'è, **periodicità non specificata** (l'etichetta lo dice); 0 = dichiarato zero |
| Indirizzo | `indirizzo` (testo libero) | | «Da verificare», non spezzato a indovinare |
| Note, canale preferito, sopralluogo | `note`, `contatto`, `sopralluogo` | | non sono dati dell'immobile: restano in `stime_dettagliate` (il sopralluogo è il percorso Agenda A30-6/7) |

## 3. Catalogo canonico

**Un solo modulo: `property/site_catalog.py`.** Per ogni valore distingue:

- l'**id stabile**: quello che il sito manda e il motore riconosce;
- l'**etichetta** del CRM;
- gli **alias**: solo scritture davvero equivalenti (maiuscole, spazi, plurale «balconi», «box auto»).

Un test lo riconfronta con le sue fonti:

- stati = `owner/home_update.STATO_VALORI` = `valuation.coeff_stato`;
- tipi di accessorio = `schemas.ACCESSORY_KINDS` = `catalog.ACCESSORY_KIND_LABELS`;
- ogni token del motore ha un tipo di accessorio.

**Quattro stati distinti, mai confusi:**

- **campo assente** → nessun valore scritto;
- **«non so»** → NULL;
- **0 nei mq e nei balconi** → non dichiarato. Il sito usa 0 per «non compilato»; una superficie di 0 m² non è un dato;
- **«no» esplicito** → `false`.

Una pertinenza non spuntata non crea una riga e non diventa un «no».

**Etichette nel browser.** Le liste arrivano da `form-options`: `conditions`, `sea_positions`, `sea_distances`, i tipi di accessorio estesi e le etichette dei campi. Nessuna lista di valori del sito è scritta a mano nella Shell, e un test lo verifica.

**Catalogo cambiato:**

- `box` ora si chiama «Garage / box», come la tipologia `garage`;
- tipi nuovi: taverna, balcone, piscina, posto moto, posto bici.

## 4. Dal sito alla scheda: `property/site_sync.py`

### Creazione

Avviene in `salva_stima`, dopo il bridge e il provisioning e prima degli eventi P17. Solo se il bridge ha dato contatto **e** lead.

- **Tipo di scheda.** UNA scheda `census` / `draft` nella stessa agenzia della stima, con `source = 'stima360'`.
- **Mai inventati:**
  - nessun agente;
  - nessun incarico;
  - nessun edificio;
  - nessuna unità o relazione.
- **Pertinenze.** Il garage del sito non prova un subalterno: diventa un accessorio «Da chiarire», non una pertinenza catastale.
- **Collegamenti:**
  - lead `origin` (il «Vende» successivo lo ritrova come lead di questa scheda);
  - contatto con ruolo neutro `contact`.
- **Provenienza.** Una riga `property_site_sources` con:
  - valori dichiarati, ciascuno con il valore grezzo;
  - valori «Da verificare»;
  - possibili doppioni;
  - istantanea dei valori scritti dal sito.
- **Storico.** Un'interazione di sistema sulla scheda.
- **Idempotenza della stessa stima:**
  - `client_request_id` deterministico (uuid5 della stima);
  - indice UNIQUE parziale sulla stima attiva.

### Ritentativo dello stesso invio

*Sostituito dal completamento (§10.2): la finestra di 24 ore non c'è più.*

- **Ritentativo riconosciuto solo con l'identità stabile della richiesta** (`client_request_id`, UUID scelto dal sito e riusato nei ritentativi). Condizioni:
  - stessa agenzia;
  - stessa identità;
  - scheda ancora fuori dal Cestino.

  Vale senza limite di tempo. La stima si collega alla scheda già nata (`origin = 'retry'`, lead `related`). Un lock transazionale sull'identità serializza le richieste contemporanee.
- **Senza identità (il sito di oggi).** Contatto e dati uguali sono un **possibile** doppione, non una prova: nasce una scheda nuova, segnalata «stesso contatto e stessi dati», mai unita.

### Stime diverse

Nasce una scheda nuova. I **possibili doppioni** si segnalano nella provenienza:

- stesso contatto e stessi dati dichiarati (forse un nuovo invio della stessa stima);
- stesso contatto;
- stesso comune + via + civico.

Non si uniscono mai in automatico.

### Stima dettagliata

Dopo il suo INSERT (ora `RETURNING id`), la **stessa** scheda (la provenienza attiva di `stima_id`) si aggiorna campo per campo:

- valore uguale → niente;
- campo vuoto mai scritto dal sito → si scrive;
- campo con l'ultimo valore scritto dal sito (l'agente non l'ha toccato) → si aggiorna;
- campo corretto dall'agente → **conflitto**, mostrato nella scheda con «Applica» / «Ignora»; un ignorato non si ripropone;
- un campo svuotato dall'agente resta vuoto se il sito ridice la stessa cosa;
- un valore vuoto non si scrive mai.

Pertinenze, stessa regola: un accessorio cambiato o tolto dall'agente diventa un conflitto, e una pertinenza non più spuntata è un conflitto «non più dichiarata».

Restano senza effetto:

- lo stesso dettaglio ripetuto;
- un dettaglio orfano (senza `stima_id`) → nessuna scheda;
- una scheda nel Cestino → non toccata.

### «Collega questa stima a IMM-x»

Collegamento esplicito, da un doppione segnalato o per codice, con due clic.

- **La provenienza.** Si chiude sulla scheda di partenza (`relinked`, con la destinazione) e si apre sull'altra.
- **Sull'altra scheda.** Solo i campi vuoti; ogni differenza diventa un conflitto.
- **Il lead e il contatto neutro seguono la stima.** Il contatto si toglie dalla scheda di partenza solo se ce l'aveva messo il sito.
- **Blocco.** Un'opportunità Venditore sul lead blocca lo spostamento (409 `SELLER_LINK_ACTIVE`).
- **La scheda di partenza non si cancella mai in automatico.** Il pannello suggerisce il Cestino, che resta una scelta dell'operatore.

### Permessi e isolamento

- **Lettura.** La provenienza si legge con la visibilità della scheda.
- **Scritture.** Applica/Ignora, «Non è lo stesso» e «Collega» richiedono l'accesso di gestione dell'immobile (`lifecycle.require_manage`):
  - titolare e amministratore su tutta l'agenzia;
  - l'agente solo sulle schede assegnate a lui.
- **Isolamento.** Tutto è nell'agenzia della scheda: un id di un'altra agenzia è 404. Un trigger della 087 impone la stessa agenzia fra provenienza, scheda e stima.

### Fail-open

`safe_sync_*` non lascia uscire eccezioni: la risposta al sito non cambia mai.

- **Senza 087 o 088** non scrive nulla.
- **Con la 088** l'invio si conserva **prima** del trasferimento: un errore lascia l'invio `failed`, recuperabile (§10.3).

## 5. Interfaccia (scheda Immobile)

- **«Modifica immobile».** Il passo 1 della procedura guidata non cambia; il form completo si allunga così:
  - nuovi in «Caratteristiche»: Piano, Anno di costruzione;
  - «Stato» dal catalogo (un valore storico resta mostrato «(valore storico)» e non si invia);
  - sezione **Mare**: posizione, distanza, ferrovia/strada, vista sì / no / non indicato, dettaglio, fascia;
  - sezione **Impianti e dotazioni**;
  - **Altre caratteristiche**.
  - In modifica viaggia solo ciò che cambia. In creazione i campi nuovi viaggiano solo se compilati.
- **Panoramica.** «Mare e dotazioni» con le etichette del catalogo, e «Altre caratteristiche».
- **Tab Censimento / Pertinenze.** Pannello **«Dal sito Stima360»** con:
  - stime, data e origine, «+ stima dettagliata»;
  - «Da verificare» con il valore grezzo e il motivo;
  - differenze con «Applica» / «Ignora»;
  - possibile doppione con «Collega questa stima a IMM-x» (due clic) / «Non è lo stesso»;
  - «Valori dichiarati» (valore e testo del sito);
  - collegamento per codice.
  - Senza permesso di gestione il pannello si legge ma non ha bottoni. Senza stime non compare.
- **Accessori:**
  - quantità («× 2») in elenco, in creazione e in modifica;
  - badge «Dal sito».
- **Mobile.** Una colonna e bottoni a tutta larghezza; nessuno scorrimento orizzontale, verificato in Chromium a 390 px.

## 6. Compatibilità

- **Nomi pubblici.** Nessun campo pubblico rinominato.
- **Sito.** Le risposte di `/api/salva_stima`, `/api/salva_stima_dettagliata` e `/api/prefill` sono invariate.
- **Motore.** Nessuna modifica. `valuation.py` non è toccato: stessi input, stessi numeri della base 3d3c3c7, provato.
- **Funnel.** `stime` e `stime_dettagliate` non cambiano schema né contenuto.
- **API immobili:**
  - le colonne della 087 sono opzionali in `PropertyCreate` / `PropertyUpdate` e entrano nell'INSERT solo se inviate (stessa regola della 083);
  - posizione e distanza dal mare si validano sul catalogo solo quando cambiano;
  - lo `stato` resta testo libero per i client storici.
- **Accessori.** `quantity` è opzionale; l'impronta di idempotenza di una richiesta senza quantità è identica a prima.
- **Preservati e coperti dai test:**
  - censimento / commerciale per ingresso;
  - assegnazione e permessi;
  - isolamento fra agenzie;
  - navigazione edifici;
  - storico;
  - Cestino (nessuna scrittura su una scheda nel Cestino);
  - protezioni dell'incarico.
- **Il Cestino non ha una guardia nuova nel database.** La funzione della 086 resta sua: aggiungere un trigger sulla tabella nuova avrebbe impedito la down della 086. La garanzia è applicativa, in ogni percorso.

## 7. Migration 087 e rilascio su TEST

`087_catalogo_canonico_1_site_attributes.sql` (+ `_down.sql`), additiva. Il runner possiede la transazione.

- **`properties`.** 13 colonne NULLABLE:
  - `sea_position`, `sea_distance`, `sea_band`, `sea_barrier`, `sea_view`, `sea_view_detail`;
  - `heating`, `air_conditioning`, `air_conditioning_type`, `exposure`, `furnishing`;
  - `condo_fees` (CHECK ≥ 0);
  - `other_features`.
- **`property_accessories`:**
  - CHECK dei tipi allargato: ogni valore della 083 resta ammesso;
  - `quantity` (NULL o ≥ 1);
  - `source` (`manual` di default, oppure `stima360`).
- **`property_site_sources`.** Tabella nuova:
  - FK: agenzia RESTRICT; scheda e stima CASCADE; contatto, lead e scheda di destinazione SET NULL;
  - indice UNIQUE parziale sulla stima attiva;
  - trigger di coerenza d'agenzia.
- **Down.** Si ferma se esistono accessori dei tipi nuovi; altrimenti torna allo schema della 086.
- **Provato su PostgreSQL temporaneo:**
  - schema completo dal runner vero, con 61 righe di ledger;
  - down rifiutata con dati nuovi;
  - down e up di nuovo;
  - codice nuovo su un database **senza** 087: la stima risponde e non scrive schede; le schede si creano e si leggono; la provenienza risponde «non installata»; un campo della 087 in PATCH dà un 409 leggibile, non un 500.

**Ordine di rilascio.** Sostituito dal completamento: 087 e 088 insieme, con i comandi completi in `roadmap/CATALOGO-CANONICO-1_TEST_RUNBOOK.md` (§10.4). Le istruzioni sintetiche che erano qui («gli stessi parametri della 086») non bastavano.

**Stato attuale di TEST, codice prima dello schema.** Il codice di `92a2dc7` è già in deploy:

- nessuna scheda nasce dal sito;
- i campi nuovi della scheda sono rifiutati in modo leggibile;
- `stime` e il contatto/lead si salvano come sempre, ma **il payload originale delle stime ricevute in questo intervallo non si conserva** (§10.3).

**Nota TEST.** Le 28 colonne di `stime_dettagliate` mancano su TEST: lo si **deduce** dallo snapshot certificato P26-0 e da P30-0, ma **non è verificato** sul TEST di oggi. Le crea la 088 (§10.4).

## 8. Test

- **Prima / dopo.** La suite PostgreSQL di questa fase, eseguita sulla base `3d3c3c7`, fallisce 11 test su 11:
  - nessuna scheda nasce dalla stima;
  - nessuna provenienza;
  - nessuna colonna per mare e impianti;
  - nessun tipo taverna o balcone.

  Dopo la fase: 11 su 11 passano.
- **`tests/test_catalogo_canonico_1.py` (11 test, senza database):**
  - catalogo contro motore, schemi e owner;
  - i quattro stati;
  - alias, precisione e valori sconosciuti;
  - impronta;
  - 087 per il runner;
  - aggancio fail-open in `main.py`;
  - motore invariato.
- **`tests/test_catalogo_canonico_1_postgres.py` (11 test, PostgreSQL vero).** Gira sugli endpoint VERI del sito con il bridge CORE vero. Sono sostituiti solo PDF, mail, eventi accessori e la scelta dell'agenzia. Copre:
  - stima completa e form leggero;
  - dettagliata sulla stessa scheda con correzioni preservate;
  - dettaglio ripetuto e orfano;
  - ritentativo senza doppioni;
  - doppioni per contatto e per indirizzo segnalati e mai uniti;
  - Applica / Ignora;
  - collegamento esplicito con lead e contatto che seguono, e il blocco Venditore;
  - alias e sconosciuti;
  - agenzie separate e permessi;
  - senza lead nessuna scheda;
  - Cestino;
  - fail-open;
  - campi nuovi in API;
  - ordine di rilascio con down e up.
- **`tests/test_catalogo_canonico_1_ui.py` (7 test, stub DOM).** Form, Panoramica, pannello provenienza, chiamate e corpi, permessi, quantità, nessuna lista scritta a mano.
- **`tests/test_catalogo_canonico_1_browser_postgres.py` (Chromium + uvicorn + PostgreSQL veri, 1280 e 390 px).** Percorso: stima, poi dettagliata, poi scheda con «Dal sito Stima360», poi Applica. Verifica anche:
  - il form con «Non indicato» salvato come NULL;
  - la riapertura con i valori salvati;
  - la Panoramica;
  - nessuno scorrimento orizzontale.
- **Sentinelle aggiornate** (marcatore «SENTINELLA AGGIORNATA DA CATALOGO-CANONICO-1»):
  - **la 087 è l'ultima migration**:
    - a30_1, a30_2p, a32_1, a32_2 (inventario);
    - censimento_1 / 2;
    - crm_ops_3 / 4;
    - delete_arch 1a / 1b / 1c / 2b2;
    - lmc15 (test_19), lmc1b, lmc2 (f3), lmc3;
    - p27_6, p29_1, p29_2_1…5e, p29_3;
  - **rotte property 46 → 50**: censimento_3 c01, p26_6c test_28;
  - **inventario FK e tabelle della certificazione live**: p26_6 (`property_site_sources` GUARDIA: la matrice non percorre il funnel pubblico);
  - **sito di connessione di test registrato**: p26_db_entrypoints e `docs/P26_DB_ENTRYPOINTS.md`.
- **Suite completa PostgreSQL.** Il dettaglio è nella sezione «Esito della suite completa» in fondo.

## 9. Ambiguità residue

1. **Etichette e opzioni dei form del sito non verificate dal vivo.**
   - Valori come «fronte» (presente nelle fixture dei test LMC) o «abitabile» restano «Da verificare».
   - Se il sito li manda davvero, servirà una decisione su cosa significano: il motore oggi li tratta come «altro».
2. **`mq` del sito.** *Deciso:* superficie principale (`surface_sqm`), separata dalla commerciale, senza chiamarla «calpestabile».
3. **Spese condominiali.** *Deciso:* si conserva l'importo, «periodicità non specificata».
4. **`fascia_mare` vs `distanzaMare`.** Due campi del sito con vocabolari diversi; il motore usa solo `distanzaMare`. `fascia_mare` si conserva come testo.
5. **La dettagliata arriva precompilata da `/api/prefill`.** *Risolto* nel completamento (§10.1): un valore uguale al precompilato non è una dichiarazione.
6. **`altroDescrizione`.** È descrizione libera dell'immobile (il motore la legge per «lusso» o «da ristrutturare»), non una pertinenza «Altro»: va in «Altre caratteristiche».
7. **Contatto con ruolo neutro `contact`.** *Confermato:* la stima non prova la proprietà, «Proprietario» lo decide l'agente.
8. **Ritentativi.** *Risolto* nel completamento (§10.2): identità stabile della richiesta, nessuna finestra di tempo.
9. **Le stime storiche non generano schede** (nessun backfill). Una dettagliata di una stima precedente al deploy non crea nulla.
10. **Alias ambigui.** *Confermato:* restano «Da verificare».

## 10. Completamento mirato (prima di PERTINENZE)

Correzioni tecniche sopra `92a2dc7`, senza ripartire da zero. Restano valide le decisioni già prese:

- contatto con ruolo neutro;
- superficie principale separata dalla commerciale;
- spese condominiali con «periodicità non specificata»;
- alias ambigui «Da verificare».

### 10.1 Prefill: niente precisione persa, niente default dichiarati

**Il percorso reale.**

1. Stima rapida: 85,5 m², piano/locali/anno non inviati.
2. `stime`: 86 (INTEGER), piano «1», 3 locali, anno 2000, via «Zona» (i default del backend).
3. `/api/prefill` serve proprio quei valori.
4. Il form della dettagliata li rimanda come se fossero del cliente.

Il payload attuale **non distingue** un campo precompilato da uno modificato. Distinguerli è quindi compito del backend, in modo esplicito e conservativo:

- **Il prefill si conserva.** Alla ricezione della dettagliata si salvano in `site_submissions.prefill` i valori che `/api/prefill` serve per quella stima: le stesse colonne di `stime`, senza i dati di contatto. Un test confronta l'elenco con il sorgente di `main.prefill`.
- **Uguale al precompilato → non è una dichiarazione.** Il campo si toglie dal dettaglio prima della regola per campo e si elenca in `prefilled_unchanged` («Lasciati come il sito li aveva precompilati»). Lo stesso vale:
  - per le pertinenze: superficie e numero uguali al precompilato;
  - per l'elenco delle pertinenze: se è invariato, non toglie nulla;
  - per i valori «Da verificare» già presenti nel precompilato.
- **Diverso dal precompilato → correzione esplicita del cliente.** Entra con la regola per campo di sempre: si scrive dove il sito aveva scritto, è un conflitto dove l'agente ha corretto.
- **`campi_dichiarati` vince sempre.** Se il sito la manda (lista di chiavi o stringa separata da virgole), un campo lì elencato è una dichiarazione anche se coincide con il precompilato: il cliente l'ha confermato.
- **Limite dichiarato.** Senza `campi_dichiarati` (il sito di oggi), un cliente che conferma un valore *uguale* al default (per esempio piano 1) non lo dichiara. È la scelta conservativa: un default non diventa mai un dato.

**Provato su PostgreSQL**, con gli endpoint veri `salva_stima` → `prefill` → `salva_stima_dettagliata` (test 03, 05, 11 e browser):

- 85,5 resta 85,50 dopo una dettagliata che rimanda 86;
- piano, locali e anno mai inviati restano vuoti;
- una modifica voluta (90 m², piano 2) si scrive;
- un secondo invio del prefill (86, piano 1) non annulla la correzione;
- `campi_dichiarati = ["locali", "anno"]` li scrive anche se uguali al precompilato.

Il motore non è toccato.

### 10.2 Ritentativi: identità stabile della richiesta

- **Niente più finestra di 24 ore.** Il ritentativo si riconosce **solo** con `client_request_id`: un UUID nel corpo JSON di `/api/salva_stima`, conservato in `site_submissions.client_request_id`.
- **Stessa agenzia + stessa identità → la stessa scheda, senza limite di tempo.** La provenienza è `retry`, il lead `related`, il motivo dell'invio `same_request`.
- **Concorrenza.** Un `pg_advisory_xact_lock` sull'identità serializza le richieste contemporanee.
- **Compatibilità.** Un valore non UUID vale come assente.
- **Client senza identità (il sito di oggi).** Contatto e dati uguali sono una **possibile duplicazione, non una prova**: nasce una scheda nuova, segnalata «stesso contatto e stessi dati (forse un nuovo invio della stessa stima)», mai unita. «Collega» resta il gesto esplicito.

**Provato su PostgreSQL** (test 04, 04b):

- stessa identità dopo 30 giorni (righe invecchiate) → la stessa scheda;
- tre richieste concorrenti con la stessa identità → una scheda e tre invii `synced`;
- due richieste davvero distinte con dati uguali → due schede, doppione segnalato;
- sito senza identità → due schede, segnalate con `same_submission_data`, `same_contact` e `same_address`.

**Adeguamento del frontend del sito: contratto pronto, non implementato.**

Il sito (`index.html`, `stima_dettagliata.html`, `dati_personali.html`) **non è in nessun repository accessibile** a questa sessione: i repository disponibili sono `stima360-backend`, `stima360-pdf` e `stima360-whatsapp-webhook-test`. P30-0 e P30-D1 lo confermano fuori repo. Non è quindi stato modificato né preparato su TEST.

Il contratto:

- `client_request_id`:
  - generato **una volta** quando il cliente invia la stima rapida;
  - **riusato identico** in ogni ritentativo dello stesso invio (errore di rete, doppio clic, ricarica con lo stato conservato);
  - **nuovo** per una stima nuova.
- `campi_dichiarati` (solo dettagliata, facoltativo): le chiavi del payload che il cliente ha **modificato o confermato**.

```js
// stima rapida: un'identita' per invio, riusata nei ritentativi
let richiesta = sessionStorage.getItem('stima360_req') || crypto.randomUUID();
sessionStorage.setItem('stima360_req', richiesta);
payload.client_request_id = richiesta;
// ...dopo una risposta 200: sessionStorage.removeItem('stima360_req');

// stima dettagliata: i campi toccati dal cliente
const toccati = new Set();
form.addEventListener('change', (e) => e.target.name && toccati.add(e.target.name));
payload.campi_dichiarati = [...toccati];
```

**Rilascio coordinato:**

1. backend (già compatibile: i due campi sono facoltativi);
2. migration 087 e 088 su TEST;
3. sito TEST con i due campi;
4. collaudo.

Senza il punto 3 vale il comportamento conservativo descritto sopra.

### 10.3 Sincronizzazione fallita o schema non pronto: nessuna perdita silenziosa

**Ricezione prima del trasferimento (migration 088, `site_submissions`).**

- **Prima di ogni trasferimento** si conserva una riga per invio:
  - stima rapida: una per `stima_id`;
  - dettagliata: una per riga di `stime_dettagliate`.
- **Cosa contiene:**
  - i soli valori dell'immobile **come il form li ha inviati**;
  - delle altre chiavi, solo i **nomi**;
  - nessun dato di contatto, consenso o nota.
- Il trasferimento nella scheda e lo stato `synced` sono **nella stessa transazione**.

| Situazione | Cosa resta | Recupero |
|---|---|---|
| Errore durante il trasferimento | invio `failed` con tipo di errore e tentativi; `stime` e contatto/lead come sempre | `site_sync_recover.py`, con i valori conservati |
| Dettagliata prima che la rapida sia nella scheda | invio `pending`, motivo `waiting_quick` | ripreso dopo la rapida |
| Rapida senza contatto/lead | invio `skipped`, motivo `no_contact_lead` | ripreso se il lead compare dopo (bridge idempotente) |
| Dettagliata rimasta senza scheda perché la rapida era saltata | `skipped`, motivo `no_source` | ripresa quando la rapida viene trasferita |
| Scheda nel Cestino, dettagliata orfana, agenzia diversa | `skipped` con il motivo | nessuno: è voluto |
| **Senza 087 o 088** (oggi su TEST) | solo `stime` (default, interi) e contatto/lead; **il payload originale non si conserva** | **nessuno, limite dichiarato** |
| Errore nello scrivere l'invio stesso (database giù) | solo il log `site_sync … error_type`, la risposta al sito non cambia | nessuno, limite dichiarato |

**`scripts/site_sync_recover.py`** (osservabile e idempotente; solo TEST certificato):

- **`--census`** (sola lettura):
  - invii per tipo, stato e motivo;
  - stime **senza** invio conservato, cioè le precedenti alla 088, non recuperabili con precisione.
- **`--dry-run`**: l'elenco degli invii che verrebbero ripresi.
- **`--apply --confirm-database "$DB_NAME"`**:
  - ordine: prima le rapide, poi le dettagliate;
  - una transazione per invio;
  - ignora gli invii più recenti di 120 s;
  - ripeterlo non duplica nulla.
- **Non è un backfill dello storico.** Non legge mai i valori da `stime`. Per i casi passati senza payload originale il limite è dichiarato: non si ricostruiscono.

**Provato su PostgreSQL** (test 12):

1. guasto simulato nella creazione → invio `failed` e risposta al sito invariata;
2. la dettagliata attende (`waiting_quick`);
3. `recover` in sola lettura non tocca nulla;
4. `--apply` crea la scheda con 85,50 (non 86) e aggiorna la dettagliata;
5. ripetuto, non trova nulla;
6. le guardie dello script rifiutano i nomi non TEST (test r01).

### 10.4 Schema della dettagliata su TEST

- **Verificato o dedotto?** **Dedotto, non verificato sul TEST di oggi.** Le fonti:
  - lo snapshot certificato P26-0 (`reports/p26_baseline_TEST_20260905T174620Z.json`) mostra 13 colonne in `stime_dettagliate`;
  - la 049 aggiunge solo `agency_id`;
  - nessuna migration successiva aggiunge le 28 colonne che `salva_stima_dettagliata` scrive;
  - P30-0 lo riporta.

  Le colonne esistono solo in `database.py::migrazione_stime_dettagliate_completa()`, che non è nel ledger e non risulta eseguita su TEST.
- **Migration nuova 088** (`088_catalogo_canonico_1b_site_inbox.sql`, additiva). Nessuna migration applicata e nessuna colonna esistente sono modificate. Cosa fa:
  - `ADD COLUMN IF NOT EXISTS` delle 28 colonne con gli **stessi tipi** di `database.py` (un test li confronta);
  - una colonna già presente non si tocca, né il tipo né i dati;
  - crea `site_submissions`, con:
    - indici UNIQUE parziali per stima rapida e per dettaglio;
    - un trigger di coerenza d'agenzia che controlla solo i riferimenti che cambiano;
    - FK: agenzia RESTRICT; stima e dettaglio CASCADE; contatto, lead e scheda SET NULL.
- **Down della 088:**
  - si ferma se ci sono invii `pending` o `failed`;
  - toglie `site_submissions`;
  - **lascia** le 28 colonne, perché non può sapere quali esistessero già.
- **Provato su PostgreSQL temporaneo:**
  - schema completo dal runner vero (62 righe di ledger);
  - down rifiutata con un invio `failed`, poi eseguita dopo il recupero;
  - dopo la down, colonne presenti, dettagliata salvata, stima senza scheda;
  - up di nuovo (test 13);
  - codice nuovo senza 087 (test 99).
- **Comandi.** `roadmap/CATALOGO-CANONICO-1_TEST_RUNBOOK.md`, presi dal runner reale `scripts/p26_migrate.py` (`status` / `plan` / `apply --operator`):
  - verifica di `$RENDER_GIT_COMMIT`;
  - fotografia in sola lettura, prima e dopo, con le colonne della dettagliata e i loro tipi;
  - `status`, che deve mostrare in sospeso solo 087 e/o 088;
  - `apply`;
  - census degli invii.
- **Cosa resta:** eseguire il runbook su TEST (non ho accesso a Render), poi il collaudo live. **La dettagliata non è certificata live.**

### 10.5 Valori sconosciuti: mai «Altro»

Una tipologia non riconosciuta (o non inviata) non diventa «Altro»:

- **Valore tecnico.** `property_type = 'other'`, perché la colonna è NOT NULL.
- **Metadato.** `metadata.site_unverified.property_type = {raw, reason}`, con il valore originale del sito conservato.
- **Interfaccia.** Elenco Immobili, unità del censimento, Panoramica e «Modifica immobile» mostrano **«Da verificare (sito: «Loft»)»** o «Da verificare (non dichiarata dal sito)».
- **Descrizione generata.** «Tipologia da verificare · Tortoreto…».
- **Nel form.** L'opzione «Da verificare» resta selezionata e **non viaggia** nel salvataggio.
- **Chiusura.** Scegliere una tipologia, compreso «Altro» scelto davvero, toglie il metadato e rigenera la descrizione.
- **Dettagliate successive.** Una dettagliata che porta una tipologia riconosciuta la scrive sopra il valore tecnico. Una tipologia uguale a quella precompilata non conta.
- **Altri campi.** Stato, posizione mare, pertinenze e gli altri valori sconosciuti restano come in §3: non scritti, grezzi e «Da verificare».

### 10.6 Confronto con i form reali: incompleto

Il sorgente dei form non è accessibile (§10.2). La lettura web di stima360.it richiede un'autorizzazione che questa sessione non ha (PROVENANCE_REQUIRED): non è stata aggirata. Il confronto resta **incompleto**.

**Opzioni ancora non verificate contro i form reali:**

- **Tipologia:** valori oltre Appartamento / Villa / Rustico.
- **Stato:** le etichette reali; per esempio «abitabile» è oggi «Da verificare».
- **Posizione mare:** «fronte» o altre varianti di «frontemare».
- **Distanza mare:** fasce oltre 1000 m o con altre scritture.
- **Piano:** le voci reali (terra, rialzato, seminterrato, ultimo, attico, numeri).
- **Locali:** «5+» o voci oltre il pentalocale.
- **Pertinenze:** l'elenco delle caselle e i loro testi esatti.
- **Vista mare (dettaglio):** le scelte.
- **Dettagliata:** le opzioni di classe energetica, riscaldamento, climatizzazione e tipo, esposizione, arredo, canale preferito.
- **`fascia_mare`:** il vocabolario.

Ognuna, se diversa da quanto il catalogo conosce, oggi si conserva grezza e «Da verificare»: non si perde e non si inventa. Per chiudere il confronto serve una delle due cose:

- il sorgente del sito;
- l'URL incollato da Giorgio nella conversazione, che autorizza la lettura.

### 10.7 Limite trovato durante la verifica (087, non corretto)

La 087 dichiara `relinked_to_property_id … ON DELETE SET NULL`, ma il CHECK `property_site_sources_relinked_chk` pretende il collegamento quando lo stato è `relinked`. Di conseguenza la **cancellazione fisica** della scheda di destinazione fallisce finché la provenienza spostata esiste.

- **Effetto sull'applicazione:** nessun percorso cancella fisicamente una scheda (il Cestino è logico).
- **Dove è emerso:** solo nella pulizia di massa dei test, a seconda dell'ordine fisico delle righe.
- **Scelta:** il test di questa fase pulisce le provenienze prima della pulizia comune. Lo schema non è stato cambiato per non toccare una migration forse già applicata.
- **Correzione proposta:** in una fase futura, una nuova migration che sostituisca la FK con RESTRICT o che rilasci il CHECK.

### 10.8 Test del completamento

- **`tests/test_catalogo_canonico_1_postgres.py`: 15 test**, PostgreSQL vero ed endpoint veri del sito. Rispetto alla prima versione:
  - 03 e 05 passano dal prefill;
  - 04 è riscritto sull'identità della richiesta;
  - nuovi 04b (concorrenza), 11 (percorso prefill completo), 12 (guasto e recupero), 13 (down e up della 088);
  - la fixture non esegue più `database.py`: lo schema viene dalla 088.
- **`tests/test_catalogo_canonico_1.py`: 17 test, senza database.** Nuovi:
  - s01: precompilato, correzione, `campi_dichiarati`;
  - s02: payload conservato senza dati di contatto, identità;
  - s03: le chiavi del prefill coincidono con `main.prefill`;
  - s04: tipologia «Da verificare», mai «Altro»;
  - m02: 088 per il runner, additiva, tipi uguali a `database.py`, down che non toglie colonne;
  - r01: guardie dello script di recupero.

  Riscritto h04: senza contatto/lead nessuna scheda.
- **`tests/test_catalogo_canonico_1_ui.py`: 9 test**, stub DOM. Nuovi:
  - u06: «Da verificare (sito: «Loft»)» in Panoramica e nel form, non inviato, chiuso scegliendo;
  - u07: campi «lasciati come precompilati» e motivi nuovi, senza codici a vista.
- **Browser** (Chromium, 1280 e 390 px): la dettagliata passa dal prefill, la differenza è sui locali cambiati, e il pannello elenca i precompilati.
- **Sentinelle aggiornate** (marcatore «SENTINELLA AGGIORNATA DA CATALOGO-CANONICO-1»):
  - **la 088 è l'ultima migration:**
    - catene numeriche: a30_1, a30_2p, a32_1, crm_ops_3, lmc15, lmc1b, p27_6, p29_2_1…5e, p29_3;
    - p29_1 (max = 88);
    - a32_2 (inventario);
    - censimento_1, crm_ops_4, delete_arch 1a / 1b (`089*` assente) / 1c / 2b2;
    - lmc2 e lmc3 (finestra `[-21:]`);
    - censimento_2 (ledger 61 → 62);
  - **inventario della certificazione live** (p26_6): `site_submissions` GUARDIA, FK agenzia RESTRICT, contatto / lead / scheda SET NULL;
  - **sito di connessione di test** (p26_db_entrypoints): descrizione aggiornata, niente più helper di `database.py`.
- **Nessuna rotta nuova.**
- **Suite completa su PostgreSQL locale, prima del commit:** 44 failed, 10885 passed, 115 skipped, 49 errors (19:20).
  - **Rispetto al run finale della prima versione: nessun fallimento sparito, 20 nuovi.**
  - **13 catene di sentinelle numeriche.** Un errore di uno nell'aggiornamento automatico: corretto e rieseguito, tutte verdi.
  - **6 sentinelle git-status:** lmc7 h5, lmc8 h5, lmc9 f4, lmc11 h1, lmc13 e1, lmc15 test_37. Vedono i file 088 non ancora tracciati: si riverificano dopo il commit.
  - **1 test fragile preesistente:** lmc3 test_19.
- **Dopo il run:** rieseguiti i 4 file della fase (43 verdi) e tutti i file di sentinelle toccati, con censimento_3/4 e p26_6c: 1176 verdi, 5 rossi, tutti già nella base o git-status.

### 10.9 Dove siamo

- **Implementato e testato localmente:**
  - prefill;
  - identità della richiesta e concorrenza;
  - ricezione e recupero;
  - 088 (up, down e codice senza schema);
  - tipologia «Da verificare»;
  - etichetta delle spese condominiali;
  - interfaccia.
- **Migration da applicare su TEST:** 087 e 088, con il runbook.
- **Confronto sito ancora incompleto:** §10.6; adeguamento del frontend del sito da fare fuori da questo repository (§10.2).
- **Collaudo live pendente:** runbook §6 e smoke qui sotto. La dettagliata **non** è certificata live.
- **PERTINENZE:** non iniziata.

## Smoke live ancora pendenti (cumulativi, A–D)

- **FIX-MANDATE-1:**
  - motivazione del blocco Cestino;
  - incarico con «Dati da completare» in Incarichi;
  - rifiuto dello svuotamento del tipo;
  - firma LMC-15.
- **EDIFICI-1:**
  - voce Edifici, lista e filtri;
  - scheda con contatori;
  - unità → immobile → edificio;
  - ritorno con i filtri;
  - smartphone.
- **CREAZIONE-GUIDATA-1:**
  - i tre percorsi;
  - riuso di un edificio esistente;
  - unità commerciale in palazzina con assegnazione;
  - ritentativo dopo errore di rete;
  - smartphone.
- **CATALOGO-CANONICO-1** (dopo la 087 e la 088 su TEST, runbook §6):
  - una stima di prova sul **sito di TEST** con contatto e decimali → scheda in Censimento con «Dal sito Stima360» e i decimali;
  - dettagliata lasciata come precompilata → i decimali restano, «lasciati come precompilati» elencati;
  - stesso invio ripetuto → con il sito di oggi (senza identità) una seconda scheda **segnalata** «stesso contatto e stessi dati»; con `client_request_id` (dopo l'adeguamento del sito) nessuna seconda scheda;
  - correzione di un campo e dettagliata → differenza con Applica / Ignora;
  - tipologia non riconosciuta → «Da verificare (sito: «…»)», mai «Altro»;
  - `scripts/site_sync_recover.py --census` e `--dry-run` senza invii in sospeso;
  - «Collega a IMM-x»;
  - Modifica immobile: Mare e impianti;
  - smartphone.

  Mai su PROD.

## Esito della suite completa

Suite intera su PostgreSQL locale (`P29_TEST_DSN`), albero di lavoro finale prima del commit: **36 failed, 10881 passed, 115 skipped, 49 errors** (12:46). Base `3d3c3c7`: 24 failed, 49 errors.

- **Fallimenti ed errori della base: identici.** Nessuno sparito, nessuno cambiato.
- **12 in più, nessuno di codice:**
  - **11 sentinelle git-status.** Guardano il working tree non committato (file nuovi in `migrations/`, righe nuove in `main.py`):
    - a32_2 s15;
    - lmc2 f4;
    - lmc7 h4 / h5;
    - lmc8 h3 / h5;
    - lmc9 f3 / f4;
    - lmc11 h1;
    - lmc13 e1;
    - lmc15 test_37.

    Non vanno «sistemate»: si riverificano dopo il commit.
  - **1 test fragile preesistente:** `test_lmc3_valuation_snapshot_postgres::test_19`. Cerca la stringa «82» dentro un dizionario che contiene un timestamp; in quel run i microsecondi erano 482885. Ripetuto da solo: 21/21 verdi. È un problema dell'ambiente di prova, non del codice.

Le 11 sentinelle git-status e il test fragile si rieseguono dopo il commit. L'esito è nella copia di questo report nel Project e nella consegna della fase.
