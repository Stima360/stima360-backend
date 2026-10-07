# COLLAUDO-FINALE-A-H — Collaudo live delle fasi A–H su Render TEST

Data: 7 ottobre 2026. Ambiente: **solo TEST**, `https://stima360-backend-test.onrender.com/os/` (badge «AMBIENTE TEST»), database `stima360_db_test`. PROD mai aperta. Sito pubblico non toccato, nessun lead inviato.

> **Questo documento dice che cosa è collaudato su TEST. Non è un'approvazione al rilascio in produzione.** La produzione richiede una decisione separata, con le migration 087–092 da applicare lì e i punti NON VERIFICATO di §4 da chiudere o accettare.

## 0. Che cosa è stato collaudato

| Voce | Valore |
|---|---|
| Codice all'avvio | `33a3e9c` (FASE H) + `60ccd32` (solo docs), verificato dagli asset serviti: `trash-api.js` contiene `restoreBuyRequest`, `cestino.js` ha la scheda `richieste` |
| Correzione durante il collaudo | `71d2488` (router, §3), pushata, deploy Render verificato (`/os/assets/core/router.js` contiene `nuovaPagina`) e percorso ripetuto dal vivo |
| **Codice finale collaudato** | **`71d2488`** su `core-0.1-test` (questo report è solo docs) |
| Migration | 087–092 **applicate** su `stima360_db_test` (comunicazione di Giorgio, verifica SQL riuscita). **Non riapplicate.** |
| Accesso | Platform admin (Giorgio), entrato come Superadmin nell'agenzia 1 «STIMA360 Alba Adriatica» (barra «Stai operando dentro») |
| Strumento | Browser integrato: UI vera (clic, digitazione, fogli), più letture dell'API nella stessa sessione per confermare gli effetti |
| Viewport | desktop (~680–800 px) e smartphone 375×812 (preset mobile) |

Regole rispettate:
- record di prova riconoscibili con «COLLAUDO»;
- nessun dato preesistente modificato (le uniche righe nuove che toccano dati preesistenti sono 3 abbinamenti e 1 proposta ritirata su immobili di test preesistenti, §5);
- nessuna cancellazione fisica;
- nessun invio di email, WhatsApp o SMS (contatto di prova senza email né telefono; nessun appuntamento creato).

## 1. Esito per percorso

| # | Percorso | Esito |
|---|---|---|
| 1 | Palazzina: creazione guidata, riuso, filtri, contatori, navigazione edificio ↔ unità (B, C) | **PASS** |
| 2 | Immobili: creazione da Commerciale e da Censimento, unità autonome e in palazzina, presa in carico, assegnazione (C) | **PASS** |
| 3 | Pertinenze: accessori senza sub, «da chiarire», unità separate, collega / scollega, contatori, conservazione dati (E) | **PASS** |
| 4 | Incarichi: coerenza con i blocchi del Cestino, dati incompleti, protezioni (A) | **PARZIALE**: PASS sull'incarico storico; **NON VERIFICATO** sull'incarico da acquisizione (§4.1) |
| 5 | Cestino Immobili, Contatti, Edifici, Richieste: blocchi, esclusione operativa, storico, ripristino (F, G, H) | **PASS** come amministratore; **NON VERIFICATO** come agente (§4.2) |
| 6 | Permessi agente / titolare e separazione tra agenzie | **NON VERIFICATO** (§4.2); PASS solo il rifiuto senza sessione (401) |
| 7 | UI desktop e smartphone | **PASS** |
| D | Catalogo canonico: campi e funzioni del CRM | **PASS** sul CRM; **integrazione reale col sito RINVIATA** (§4.3) |
| — | Difetto trovato: router, una vista lenta sovrascriveva la pagina nuova | **FAIL → corretto (`71d2488`) → PASS dal vivo** (§3) |

## 2. Dettaglio delle prove live

### 2.1 Palazzina (B, C)

- **Creazione guidata da Censimento.**
  - Creato l'edificio #4 «COLLAUDO Palazzina AH» (Tortoreto, microzona Alto, Via COLLAUDO Finale 1, 3 unità dichiarate).
  - Unità create: IMM-93 (piano 1 int. 1, 80 m²) e IMM-94 (piano 2 int. 2, 95 m²).
  - Contatori della scheda corretti a ogni passo.
- **Riuso.** Una seconda creazione allo stesso indirizzo propone il candidato «Stesso civico». «Usa questo edificio» collega l'unità all'edificio #4 e non crea un secondo edificio.
- **Lista Edifici.**
  - Filtri Comune → Microzona (la microzona si abilita dopo il Comune) e ricerca: una sola chiamata API con tutti i filtri.
  - «← Edifici» dalla scheda conserva i filtri.
- **Navigazione.**
  - Dalla scheda edificio, il tocco su una riga apre l'unità.
  - Dalla scheda dell'unità, il riquadro «Nell'edificio» porta a `#/edifici/4`.
  - Le voci del blocco «Elimina…» aprono la scheda dell'unità (IMM-93 provato).
- **Edificio vuoto.** Creato l'edificio #5 «COLLAUDO Edificio Vuoto» (Via COLLAUDO Cestino 9, Tortoreto) con «Fine per ora»: si apre la scheda con «Salvato ora, ancora senza unità».

### 2.2 Immobili (C)

- **Da Commerciale, in palazzina.** IMM-95, locale commerciale al piano T, 45 m², in palazzina #4, nel binario commerciale (crm), assegnato a Cron Dispatch (utente tecnico, mai a persone reali).
- **Presa in carico.** IMM-94 passa da censimento a commerciale con lo stesso codice e compare nella lista Commerciale.
- **Da Censimento, autonoma.** IMM-96, villa in Via COLLAUDO Autonoma 7 (Tortoreto, Lido Nord), 180 m², senza edificio, nel censimento e non nella lista operativa.
- **Da Commerciale, autonoma.** IMM-97 in Via COLLAUDO Commerciale 3, Alba Adriatica: 70,5 m² (decimali conservati), crm, assegnato a Cron Dispatch.
- **Non provato dal vivo:** il ritentativo dopo un errore di rete. Richiede di interrompere la rete a metà invio; resta coperto dalle prove locali.

### 2.3 Pertinenze (E, runbook §5)

- **Posto auto da collegare.** IMM-98 «Da collegare»; il riepilogo dice «3 principali + 1 pertinenza (1 da collegare)».
- **Collega.** IMM-98 si collega a IMM-93.
- **Accessorio senza sub.** Cantina 6,25 m² «Da verificare» diventa un accessorio con stato sconosciuto.
- **Box con sub.** Box 18,5 m² «Sì» diventa IMM-99, collegato. L'avviso «Possibile doppione» (409 `SIMILAR_FOUND`) compare come previsto; «Salva comunque».
- **Chiarisci «È separata».**
  - Nasce IMM-100 (cantina / deposito) con l'origine «Nata dall'accessorio «Cantina» (era «Da chiarire», 6,25 m²)».
  - L'accessorio esce dall'elenco e i dati si conservano nella nuova unità.
- **Scollega.** «Scollega da IMM-93» con conferma a due tocchi:
  - genitore vuoto, `is_pertinenza` resta vero;
  - l'edificio #4 non cambia.
- **Riepilogo finale dell'edificio:** principali 3, pertinenze 3, da collegare 1, «Censite oltre le dichiarate».

### 2.4 Incarichi (A)

- **Incarico storico (PASS).**
  - Su TEST esistono incarichi storici su immobili di test preesistenti (#12 TESTP14-COMPLETO, FCRM-*).
  - Il controllo di cancellazione, in sola lettura su tutti i 24 immobili dell'agenzia, dà `MANDATE_PRESENT` con la motivazione nuova: «Incarico storico (registrato prima delle Acquisizioni) in corso / concluso con la vendita. Non compare in Incarichi: i dati sono nella sezione Incarico della scheda immobile.».
  - Nella UI, «Elimina…» sul #12 mostra la stessa motivazione con la conferma disabilitata. Il foglio è stato solo aperto e chiuso.
- **Pagina Incarichi coerente con la definizione.**
  - La pagina dice «Gli incarichi nascono solo da un'acquisizione («Genera incarico»)» ed è vuota nell'agenzia 1 (`GET /api/property/mandates` → `items: []`): gli storici non compaiono, come da scelta CRM-OPS-4.
  - La scheda di un immobile senza incarico dice «L'incarico può essere generato solo da un'acquisizione.».
- **Incarico da acquisizione:** NON VERIFICATO, vedi §4.1.

### 2.5 Cestino Edifici (G)

- **Edificio vuoto #5.**
  - «Elimina…» mostra «L'edificio è vuoto…» e i 5 motivi.
  - Scelti «Record di prova» con una nota, poi **Sposta nel Cestino**.
  - Esito: toast «Edificio spostato nel Cestino» e ritorno alla lista. Il toast sopravvive alla navigazione, quindi anche la correzione del router è verificata qui.
- **Esclusione.**
  - L'edificio non compare nella lista, né cercando «COLLAUDO», né fra i candidati della creazione guidata (Tortoreto + «Via COLLAUDO Cestino» → nessuno).
  - `PATCH` dell'edificio → 409 `BUILDING_IN_TRASH`.
  - `POST /census/units` verso l'edificio → 409 `BUILDING_IN_TRASH`, e nessuna unità creata.
- **Cestino › Edifici.**
  - La card mostra nome, via, data e ora, autore, «Record di prova — COLLAUDO finale A-H» e le unità dichiarate.
  - «Apri scheda» porta al riquadro «Nel Cestino…». Mancano «Modifica palazzina», «Elimina…» e «+ Aggiungi unità»; i chip dei tipi esistono nel DOM ma sono nascosti e senza comando che li apra.
- **Ripristino.** Toast «Edificio ripristinato»: torna in lista e fra i candidati con lo **stesso id 5**.
- **Blocco con collegamenti.**
  - Sull'edificio #4, «Elimina…» dà «Non si può spostare nel Cestino».
  - Il foglio mostra «6 unità collegate (6 attive)» e una voce cliccabile per unità; la conferma è disabilitata.
  - Con IMM-95 nel Cestino Immobili il testo diventa «(5 attive, 1 nel Cestino Immobili)» e la voce porta a `#/cestino`.
  - Nessuna unità ha cambiato edificio.

### 2.6 Cestino Immobili

- **IMM-95.**
  - Il controllo dice «si può». «Elimina…» → «Record di prova» con una nota → toast «Immobile spostato nel Cestino».
  - L'immobile esce dalla lista e la scheda risponde 404.
  - Nel Cestino la card mostra motivo e nota.
- **Ripristino.** Toast «Immobile ripristinato»: stesso codice IMM-95, stesso edificio #4, stesso agente (53).

### 2.7 Cestino Contatti (F) e Richieste (H)

Dati: contatto #167 «COLLAUDO Acquirente Cestino» (ruolo acquirente, **senza email né telefono**) e richiesta #67 «COLLAUDO Richiesta Cestino» (Tortoreto, appartamento, budget massimo 250.000 €).

- **Blocco del contatto.**
  - Con la richiesta aperta, «Elimina…» del contatto dà «Richieste d'acquisto aperte: chiudile prima dalla scheda Acquirente», con la voce «Richiesta #67…» cliccabile, che apre la richiesta.
  - Conferma disabilitata (`BUY_REQUEST_OPEN`).
- **Richiesta semplice nel Cestino.**
  - Il foglio mostra i 5 motivi e gli effetti: stato invariato, contatto attivo, comunicazioni non toccate.
  - Toast «Richiesta spostata nel Cestino».
  - La richiesta esce da Acquirenti e dalla ricerca (`/api/buy/requests?search=COLLAUDO` → vuota).
  - La scheda resta leggibile con il riquadro `trash`, per progetto.
  - `PATCH` → 409 `BUY_REQUEST_IN_TRASH`.
- **Il contatto ora si sposta.**
  - La richiesta nel Cestino non lo blocca più.
  - Il foglio avvisa «Le automazioni del contatto verranno sospese»; toast «Contatto spostato nel Cestino».
  - Il contatto esce dall'elenco e dalla ricerca.
- **Ripristino bloccato dal contatto.**
  - Cestino › Richieste mostra «COLLAUDO Acquirente Cestino (nel Cestino)».
  - «Ripristina» si ferma con «Il contatto della richiesta è nel Cestino: ripristina prima il contatto» e il collegamento «Apri il Cestino Contatti». Anche l'API risponde 409 `RESTORE_BLOCKED`. Nulla cambia.
- **Scheda del contatto nel Cestino.**
  - Riquadro «Nel Cestino…», nessun comando di modifica.
  - La tab Comunicazioni dice «Contatto nel Cestino: automazioni sospese, nessun messaggio nuovo», «in pausa … contact_trashed» e «Nessun messaggio inviato o programmato».
- **Ripristino in ordine.**
  - Contatto: toast «Contatto ripristinato», stesso #167. Le automazioni restano in pausa finché non le si riattiva a mano, per progetto.
  - Richiesta: toast «Richiesta ripristinata», stesso #67, stato «Bozza», di nuovo in lista. Il Cestino Richieste è vuoto.
- **Blocco da proposta (runbook H §5.4).**
  - Richiesta portata ad «Attiva», poi «Ricalcola abbinamenti»: 3 abbinamenti (immobili di test preesistenti).
  - Proposta #55 **in bozza** su «Test P14 Completo». «Elimina…» dà «Proposte d'acquisto in corso: ritirale o concludile prima», con «Proposta #55 · TESTP14-COMPLETO · in bozza» e «Apri la richiesta»; conferma disabilitata (`PROPOSAL_OPEN`).
  - Dopo «Ritira» (conferma inline, nessun dialogo del browser) il controllo dice «si può», con lo storico «Proposte d'acquisto concluse (1)».
  - L'immobile #12 non è stato modificato (`updated_at` invariato al 04/10).
- **Richiesta con abbinamenti nel Cestino.**
  - Il foglio dice «3 abbinamenti escono dalle liste e dai calcoli (restano nella scheda)» e «Lo storico resta consultabile».
  - Dopo lo spostamento gli abbinamenti della richiesta in `/api/match/matches` passano da 3 a 0.
  - Nella scheda nel Cestino non ci sono comandi né nell'intestazione né nelle tab Criteri, Abbinamenti, Proposte, Visite e Task.
  - Dopo il ripristino: stato «Attiva» e di nuovo 3 abbinamenti.
  - Alla fine la richiesta è stata riportata in «Bozza», così non alimenta suggerimenti automatici.

### 2.8 Catalogo canonico nel CRM (D)

- **`form-options` serve il catalogo:**
  - `conditions`: nuovo, ristrutturato, buono, scarso, grezzo;
  - `sea_positions`: fronte mare, seconda fila, oltre;
  - `sea_distances`: 0-100 / 100-300 / 300-500 / 500-1000 m;
  - accessori, con `box` = «Garage / box» e i nuovi taverna, balcone, piscina, posto moto, posto bici;
  - `site_field_labels`.
- **«Modifica immobile» su IMM-97.**
  - Il form ha le sezioni «Mare» e «Impianti e dotazioni».
  - Impostati stato «Ristrutturato», posizione «Seconda fila», distanza «100-300 m», vista mare «Sì», ascensore «Sì», poi «Salva modifiche».
  - L'API conserva i valori canonici.
  - La Panoramica «MARE E DOTAZIONI» mostra le etichette del catalogo e «—» per i campi non indicati.
  - I 70,5 m² restano.
- **Non provato:** il pannello «Dal sito Stima360». Compare solo con stime arrivate dal sito, e il sito non è collegato a TEST (§4.3).

### 2.9 Accesso senza sessione

Le 15 rotte nuove o toccate, senza cookie, rispondono tutte **401**, mai 200 né 5xx:
- `deletion-check`, `trash` e `restore` di edifici, contatti, richieste e immobili;
- gli elenchi del Cestino;
- `buildings`;
- `mandates`.

### 2.10 Smartphone (375×812)

- **Nessuno scorrimento orizzontale** (`scrollWidth` = 375) su 12 pagine:
  - Edifici, scheda edificio #4;
  - IMM-93, IMM-97;
  - Cestino e le sue quattro schede;
  - scheda contatto #167, scheda richiesta #67;
  - Incarichi, Immobili.
- Nessun bottone visibile sotto i 32 px di altezza.
- **Bottom-sheet.** Il foglio «Elimina…» con il blocco dell'edificio #4 si legge per intero: voci a capo, bottoni a tutta larghezza.
- **Creazione guidata.** Il passo 1 è a una colonna, con «Annulla» / «Avanti» toccabili; chiuso senza salvare.
- Viewport riportato a desktop alla fine.

## 3. Difetto trovato e corretto

**Sintomo, dal vivo.** Dopo «Fine per ora» nella creazione guidata, un passaggio rapido a Immobili mostrava URL e titolo «Immobili» ma il contenuto della scheda edificio di prima.

**Causa.**
- Le viste sono asincrone e scrivono `#content` dopo i loro `await`.
- Una vista più lenta della rotta **precedente** finiva dopo la nuova e la sovrascriveva.
- La guardia esistente (`epoch`) copre solo il cambio di sessione, non il cambio di rotta.

**Correzione** (`static/os_shell/assets/core/router.js`, commit **`71d2488`**, riuso del router esistente).
- Ogni rotta disegna in una propria pagina (`<div data-route-page>`) figlia di `#content`, e la navigazione successiva la stacca. Una scrittura tardiva cade in una pagina che non si vede più.
- Un errore tardivo di una rotta superata non sostituisce la pagina nuova.
- Il toast (`[data-census-toast]`) sopravvive alla navigazione come prima.
- Un contenitore senza DOM completo (prove del solo router) funziona come prima.

**Prova di regressione:** `tests/test_collaudo_finale_router.py`, che usa il router vero nello stub DOM esistente.
- r01: la rotta superata non sovrascrive.
- r02: l'errore tardivo non sostituisce la pagina nuova.
- r03: il toast sopravvive e il contenitore minimo funziona.
- r01 e r02 falliscono senza la correzione.
- Suite UI / shell / browser: 1151 passate. Le sole 2 rosse (`lmc15` 41/42) sono preesistenti sulla base.

**Verifica dal vivo dopo il deploy.**
- Ripetuta 5 volte la sequenza `#/edifici/4` → dopo 30 ms `#/immobili`: 5 su 5 mostrano Immobili, con una sola pagina e nessun contenuto dell'edificio.
- Il toast «Edificio spostato nel Cestino» resta visibile dopo il ritorno alla lista.

## 4. Impedimenti rimasti (NON VERIFICATO)

### 4.1 Incarico da acquisizione (A)

Su TEST non esiste alcun incarico con origine acquisizione: l'elenco Incarichi dell'agenzia 1 è vuoto. Crearne uno richiede un'acquisizione, e l'acquisizione crea **sempre** un appuntamento in Agenda.
- **Promemoria A32.** Partono solo verso l'email del contatto; con un contatto senza email non partirebbero.
- **Google Calendar.** Su TEST risulta **configurato e abilitato** (`/api/calendar/google/status`: `configured: true`, `enabled: true`). L'appuntamento verrebbe sincronizzato sul calendario dell'agente assegnato, se collegato.
- Da questa sessione non si può verificare che nessun agente abbia Google collegato. Un evento reale su un calendario esterno, con le eventuali notifiche di Google, non è garantibile come «nessun invio».

Per la regola «se non puoi garantirlo, fermati» la prova si è fermata qui.

**Restano da verificare:**
- «Dati da completare» in Incarichi;
- «Apri incarico» nel blocco del Cestino;
- il rifiuto di svuotare il tipo d'incarico (400);
- il blocco da firma LMC-15.

**Per chiuderle serve una di queste:**
- (a) conferma che nessun agente dell'agenzia di prova ha Google Calendar collegato su TEST, oppure Google disabilitato su TEST per la durata della prova;
- (b) un incarico da acquisizione già esistente su TEST, da usare in sola lettura.

### 4.2 Permessi agente e separazione tra agenzie

- **Account di prova.** Gli account dell'agenzia 1 (owner.a / admin.a / agent.a / agent.a2 @test.stima360.local) sono **revocati**. Gli operatori attivi sono persone reali o l'utente tecnico. Le membership non sono state toccate.
- **Restano da verificare come agente:**
  - «Serve un amministratore» con storico;
  - Cestino filtrato sui propri record;
  - «Può ripristinarlo chi lo ha spostato o un amministratore.»;
  - agente su un edificio vuoto.
- **Separazione tra agenzie.** Un tentativo di entrare come Superadmin nell'agenzia 35 per leggere per id i record COLLAUDO dell'agenzia 1 (attesi 404) è stato **rifiutato dai permessi di questa sessione**. Non è stato aggirato; la sessione è rimasta nell'agenzia 1.
- **Verificato:** solo il rifiuto senza sessione (401, §2.9).
- **Per chiudere serve:** un account agente e un titolare di prova autorizzati (riattivati o nuovi), e per la separazione un secondo account di prova in un'altra agenzia, oppure l'autorizzazione esplicita alla prova con il contesto Superadmin.

### 4.3 Integrazione reale con il sito (D)

**Rinviata esplicitamente**, come da istruzioni. Il sito pubblico non è collegato a TEST e non è stato modificato né usato.

Restano per quando il sito di TEST sarà collegato (runbook D §6):
- stima rapida con decimali → scheda di censimento;
- dettagliata «lasciata come precompilata»;
- invio ripetuto (`client_request_id`);
- differenze Applica / Ignora;
- tipologia sconosciuta → «Da verificare»;
- `site_sync_recover.py --census` / `--dry-run`;
- «Collega a IMM-x»;
- il pannello «Dal sito Stima360».

### 4.4 Altro non provato dal vivo

- **Ritentativo dopo errore di rete** nella creazione guidata: richiede un'interruzione di rete a metà invio. Coperto in locale.
- **Ripristino di un edificio con «Possibili doppioni attivi»** (runbook G §5.7): non provocato, per non creare un secondo edificio allo stesso indirizzo. Coperto in locale.
- **Portale proprietario** (runbook F §5.6): nessun contatto di prova con accesso attivo.
- **Registro `record_lifecycle_events`:** non letto. Da questa sessione non c'è accesso SQL a TEST. Gli effetti sono stati verificati dall'API e dalla UI. La query del runbook H si può eseguire in sola lettura.

## 5. Dati di prova lasciati su TEST (agenzia 1, nessuno cancellato)

| Dato | Stato finale |
|---|---|
| Edificio **#4** «COLLAUDO Palazzina AH», Via COLLAUDO Finale 1, Tortoreto (Alto) | attivo, 6 unità collegate |
| Edificio **#5** «COLLAUDO Edificio Vuoto», Via COLLAUDO Cestino 9, Tortoreto | attivo e vuoto; spostato nel Cestino e ripristinato |
| **IMM-93** appartamento, piano 1 int. 1, 80 m² | censimento, edificio #4; il box IMM-99 è sua pertinenza |
| **IMM-94** appartamento, piano 2 int. 2, 95 m² | preso in carico (crm), edificio #4 |
| **IMM-95** locale commerciale, piano T, 45 m² | crm, agente Cron Dispatch, edificio #4; spostato nel Cestino e ripristinato |
| **IMM-96** villa, Via COLLAUDO Autonoma 7, 180 m² | censimento, senza edificio |
| **IMM-97** appartamento, Via COLLAUDO Commerciale 3, Alba Adriatica, 70,5 m² | crm, Cron Dispatch; Mare e dotazioni compilati (§2.8) |
| **IMM-98** posto auto / box, piano -1 | pertinenza scollegata («da collegare»), edificio #4 |
| **IMM-99** box 18,5 m² | pertinenza di IMM-93 |
| **IMM-100** cantina / deposito 6,25 m² | pertinenza nata dall'accessorio «Cantina» |
| Contatto **#167** «COLLAUDO Acquirente Cestino» | attivo, senza email né telefono; automazioni in pausa dal Cestino (per progetto non si riattivano da sole) |
| Richiesta **#67** «COLLAUDO Richiesta Cestino» | Bozza; due volte nel Cestino e ripristinata |
| 3 abbinamenti della richiesta #67 | con immobili di test preesistenti (#12 «Test P14 Completo» e due «FULL CRM PROPERTY … E2E»); righe nuove, immobili non modificati |
| Proposta **#55** | ritirata, su TESTP14-COMPLETO (#12), 100.000 €, nota «COLLAUDO finale A-H proposta di prova» |
| Registro del ciclo di vita | eventi `trash` / `restore` per immobile 95, edificio 5, contatto 167, richiesta 67 (×2) |
| Audit di piattaforma | gli ingressi Superadmin nell'agenzia 1 |

Nessun appuntamento, visita, acquisizione o incarico creato.

## 6. Documenti aggiornati

Lo stato «089–092 non applicate / collaudi pendenti» è stato aggiornato:
- `PERTINENZE-1_REPORT.md`, `PERTINENZE-1_TEST_RUNBOOK.md`;
- `CESTINO-CONTATTI-1_REPORT.md`, `CESTINO-CONTATTI-1_TEST_RUNBOOK.md`;
- `CESTINO-EDIFICI-1_REPORT.md`, `CESTINO-EDIFICI-1_TEST_RUNBOOK.md`;
- `CESTINO-RICHIESTE-1_REPORT.md` (§0, §10 e tabella §12 per E–H), `CESTINO-RICHIESTE-1_TEST_RUNBOOK.md`.

Ognuno porta in testa la nota «AGGIORNAMENTO COLLAUDO-FINALE-A-H» con il rimando a questo report.

## 7. Conclusione

**Collaudati su TEST (PASS):**
- B Edifici;
- C Creazione guidata;
- E Pertinenze;
- F Cestino Contatti, G Cestino Edifici, H Cestino Richieste e il Cestino Immobili, come amministratore;
- D lato CRM;
- A per l'incarico storico;
- UI desktop e smartphone;
- correzione del router (`71d2488`) verificata dal vivo.

**Non ancora collaudati su TEST:**
- A per l'incarico da acquisizione (§4.1);
- i permessi da agente e la separazione tra agenzie (§4.2);
- l'integrazione reale col sito (D, rinviata, §4.3).

**Quindi i percorsi A–H non sono collaudati al completo.** Lo sono tutti quelli accessibili con l'accesso e le garanzie disponibili. Nessuna fase nuova aperta, nessuna funzionalità aggiunta.

Tutto questo riguarda **solo TEST** e non costituisce approvazione alla produzione.
