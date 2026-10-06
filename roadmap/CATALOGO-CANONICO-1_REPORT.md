# CATALOGO-CANONICO-1 — Catalogo canonico Stima360 e allineamento sito → CRM (FASE D)

Base: `core-0.1-test` @ `3d3c3c7` (CREAZIONE-GUIDATA-1). Il commit contiene anche questo report.

- **Migration nuova: 087** (additiva). Va applicata su TEST: vedi «Rilascio su TEST».
- Nessun dato esistente modificato, nessun backfill delle stime storiche.
- PROD e sito pubblico non toccati.
- Motore di valutazione invariato.

Il push su `core-0.1-test` avvia il deploy automatico di Render TEST. Deploy, migration e smoke live **non verificati** da qui: non ho accesso autorizzato a TEST.

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
| Tipologia | `tipologia` | solo `stime` | `property_type` ✓ | Appartamento / Villa / Rustico (parole del motore) e le etichette del CRM. Sconosciuta → `other` + «Da verificare»; non inviata → `other` + «Tipologia non dichiarata» |
| Mq | `mq` | `stime.mq` intero | `surface_sqm` ✓ | due decimali; 0 = non dichiarato. Resta distinta da `commercial_surface_sqm`, che non si tocca |
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
| Ripetizione delle caratteristiche | tipologia, mq, piano, locali, bagni, ascensore, stato, anno, mare, pertinenze e mq, `altroDescrizione` | solo `stime_dettagliate` | la **stessa** scheda, con la regola per campo (§4) |
| Classe energetica | `classe` | solo `stime_dettagliate` | `energy_class` ✓ (catalogo A4…G; «non so» = NULL; fuori catalogo «Da verificare») |
| Riscaldamento | `riscaldamento` | | `heating` ✓ (087), testo del cliente |
| Climatizzazione | `condizionatore` | | `air_conditioning` ✓ (087), testo del cliente |
| Tipo di climatizzazione | `condiz_tipo` | | `air_conditioning_type` ✓ (087), testo del cliente |
| Esposizione | `esposizione` | | `exposure` ✓ (087), testo del cliente |
| Arredamento | `arredo` | | `furnishing` ✓ (087), testo del cliente |
| Spese condominiali | `spese_cond` | | `condo_fees` ✓ (087), euro; 0 = dichiarato zero |
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

Il sito non ha una chiave: un doppio invio è una seconda stima con un secondo lead. Si riconosce così:

- stessa agenzia;
- stesso contatto;
- stessa impronta dei valori dichiarati;
- entro 24 ore;
- scheda ancora fuori dal Cestino.

In quel caso la stima si collega alla scheda già nata (`origin = 'retry'`, lead `related`) e nessuna scheda nuova nasce. Un lock transazionale per contatto serializza due invii contemporanei.

### Stime diverse

Nasce una scheda nuova. I **possibili doppioni** si segnalano nella provenienza:

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

`safe_sync_*` non lascia uscire eccezioni: la risposta al sito non cambia mai. Senza la 087 non scrive nulla.

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

**Ordine di rilascio (DB-first):**

1. Sulla shell del servizio TEST: `python scripts/p26_migrate.py status --operator "giorgio.larasa"`, poi `plan`, poi `apply`, con gli stessi parametri di baseline usati per la 086. Il piano deve mostrare la sola 087.
2. Poi il deploy del codice.

Il push di questa fase avvia il deploy subito: se la 087 non è ancora applicata, TEST resta nello stato «codice prima dello schema» descritto sopra. È innocuo: nessuna scheda dal sito, campi nuovi rifiutati in modo leggibile. Si sistema applicando la 087.

**Nota TEST (P30-0).** Su TEST mancano le 28 colonne di `stime_dettagliate`: lì l'INSERT della stima dettagliata fallisce già prima di questa fase. Finché non vengono create, la dettagliata su TEST non può aggiornare la scheda. Lo schema reale del sito è un tema P30 e non è stato toccato.

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
2. **`mq` del sito: superficie calpestabile o commerciale?** Il motore lo usa come superficie principale; la scheda lo mette in `surface_sqm`. `commercial_surface_sqm` non si tocca.
3. **Spese condominiali: periodo non dichiarato dal sito** (mensili? annue?). Il valore si conserva com'è.
4. **`fascia_mare` vs `distanzaMare`.** Due campi del sito con vocabolari diversi; il motore usa solo `distanzaMare`. `fascia_mare` si conserva come testo.
5. **La dettagliata arriva precompilata da `/api/prefill`, cioè da `stime` con i suoi default e i decimali troncati.**
   - Un «piano 1» inviato senza correzioni viene trattato come dichiarato.
   - Un mq 85,5 della rapida può tornare 86 o 85 dalla dettagliata e aggiornare la scheda, se l'agente non l'ha corretto.
   - Lo si vede dalla provenienza.
6. **`altroDescrizione`.** È descrizione libera dell'immobile (il motore la legge per «lusso» o «da ristrutturare»), non una pertinenza «Altro»: va in «Altre caratteristiche».
7. **Contatto con ruolo neutro `contact`.** La stima non prova la proprietà: «Proprietario» lo decide l'agente.
8. **Finestra del ritentativo: 24 ore.** Oltre, lo stesso invio crea una scheda nuova, segnalata come possibile doppione per contatto.
9. **Le stime storiche non generano schede** (nessun backfill). Una dettagliata di una stima precedente al deploy non crea nulla.

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
- **CATALOGO-CANONICO-1** (dopo la 087 su TEST):
  - una stima di prova sul **sito di TEST** con contatto → scheda in Censimento con «Dal sito Stima360»;
  - stesso invio ripetuto → nessuna seconda scheda;
  - correzione di un campo e dettagliata (se `stime_dettagliate` su TEST ha le colonne) → differenza con Applica / Ignora;
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
