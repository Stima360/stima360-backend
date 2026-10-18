# STIMA360 CRM — ADM-3: Rete semplice e continuità operativa

- Branch: `core-0.1-test`. Base pubblicata: `0d24a8a` (ADM-2), verificata uguale al remoto prima di iniziare.
- Perimetro rispettato:
  - nessuna migration, nessuna tabella nuova;
  - nessuna scrittura su `stima360_db` o sul database TEST operativo;
  - nessun push né deploy;
  - nessuna modifica a Stima360.it, STIMA Voice, Censimento o Zona;
  - WhatsApp non toccato.
- Decisioni del proprietario prese in questa fase (domande poste prima di scrivere codice):
  - **D-A:** quando l'agenzia competente è sospesa, le nuove stime e i nuovi lead **restano all'agenzia sospesa**;
  - **D-B:** i follow-up automatici dei lead di un'agenzia sospesa sono **in pausa fino alla riattivazione**.
    **Superata da ADM-3 BIS** (sezione 7): la decisione del proprietario è la continuità centrale dei follow-up email.

## 1. Cosa cambia per il Super Admin

- **«Verifica conseguenze» è sparita dalla Rete.** Gli endpoint di ADM-2 restano come controllo tecnico in sola lettura, fuori dall'interfaccia ordinaria.
- **Agenzia, scheda Panoramica:** il nome si salva da solo («Salva nome»). Sotto c'è lo stato attuale, una frase che lo spiega e soltanto i comandi possibili:
  - attiva → **Sospendi agenzia**;
  - sospesa → **Riattiva agenzia** e **Archivia agenzia**;
  - archiviata → **Riattiva agenzia**;
  - agenzia di sistema: nessun comando, con la scritta «non si sospende e non si archivia».
- **Operatori:** il dialogo «Accesso» con il menu di stato è sostituito da un comando diretto, **Disabilita operatore** oppure **Riattiva operatore**. Il dialogo «Membership» resta com'era.
- **Territori:** **Sospendi assegnazione**, **Riattiva assegnazione**, **Revoca assegnazione**.
- **Ogni comando ha una sola conferma.** Il testo dice in chiaro che nessun dato viene cancellato o spostato e cosa succede a lead, follow-up, prenotazioni e calendari.
- Nessun comando di eliminazione è stato aggiunto. Nessuna funzione non disponibile viene presentata come esistente.

## 2. Come sono protetti i dati

- Sospendere, archiviare o riattivare un'agenzia e disabilitare o riattivare un operatore cambiano **solo lo stato**:
  - immobili, edifici, contatti, lead, appuntamenti, documenti, acquisizioni, stime, assegnazioni storiche e membership restano dove sono, con la stessa agenzia proprietaria;
  - i contatti assegnati a un operatore disabilitato restano a lui.
- Le protezioni di ADM-1 restano invariate:
  - solo il Super Admin designato amministra la Rete;
  - l'account designato non si disabilita;
  - l'agenzia di sistema non si sospende;
  - si archivia solo un'agenzia sospesa;
  - le sessioni revocate non tornano valide.
- La lettura C1 da parte dell'agenzia competente, le proposte C2 e le liste C3 non sono state toccate.

## 3. Come vengono gestiti i lead con un'agenzia sospesa

**Prima di ADM-3** il lead di un Comune presidiato da un'agenzia sospesa finiva in silenzio all'agenzia di sistema, che ne diventava proprietaria. Ne restava traccia solo in una riga di log: un trasferimento nascosto della competenza.

**Ora** (decisione D-A, `network_routing/repository.py` e `service.py`):

1. La catena del Comune (alias → territorio → assegnazione attiva → agenzia) accetta un'agenzia **attiva o sospesa**. Se è sospesa, l'esito è esplicito: `territory_suspended_agency`, non un ripiego.
2. La stima, il contatto, il lead e il task di follow-up nascono **nell'agenzia competente**, con il Comune di provenienza: nessun lead perso, nessun cambio di proprietario.
3. Il bridge verso il CRM, ripetuto sulla stessa stima, non duplica contatto e lead (provato).
4. L'agenzia sospesa **non ottiene accessi**: i suoi operatori non entrano. Il lead la attende fino alla riattivazione, oppure finché il Super Admin non assegna il Comune ad altri.
5. La notifica a `info@stima360.it` parte subito, come prima. L'email della stima al cliente **non** parte subito: viene messa in coda (`stima_pdf`) e spedita dal dispatch. **Correzione:** la prima versione di questo rapporto diceva che partiva comunque; per un'agenzia sospesa restava in coda. Con ADM-3 BIS la spedisce il giro centrale (sezione 7).

**Tracciabilità**

- Ogni lead resta riconoscibile nel database: la stima porta l'agenzia competente, il Comune e la data.
- La sospensione dell'agenzia, con la sua data, è registrata in `platform_audit_log` (ADM-1).
- Il log applicativo riporta `source=territory_suspended_agency` per ogni stima instradata così.

**Differenza rispetto all'opzione presentata:** l'opzione D-A diceva che ogni caso sarebbe stato «registrato nel registro di piattaforma». Non l'ho fatto. Una regola esistente (P27-6, verificata dal test `g7`) vieta di scrivere le decisioni di instradamento in quel registro, perché diventerebbe un log di traffico. La traccia è quella descritta sopra. Se vuoi comunque una riga per ogni lead, serve una tua decisione esplicita che superi P27-6.

**Casi che vanno ancora all'agenzia di sistema, ma in modo esplicito:**

- **Assegnazione sospesa o revocata dal Super Admin:** la conferma lo dice in chiaro, e il cambio è registrato nel registro di ADM-1.
- **Agenzia archiviata:** un'agenzia archiviata non è instradabile. La conferma di archiviazione avverte di revocare o trasferire prima le assegnazioni. Vedi le criticità al punto 5.

## 4. Follow-up: cosa resta operativo e con quali autorizzazioni

| Percorso | Agenzia attiva | Agenzia sospesa (D-B) | Operatore disabilitato |
|---|---|---|---|
| Notifica a info@ | sì | **sì** (immediata, invariata) | — |
| Email della stima al cliente | sì | **sì**, dal giro centrale (ADM-3 BIS) | — |
| Contatto, lead e task di follow-up alla nascita della stima | sì | **sì**, nell'agenzia; restano in attesa | — |
| Scansione delle scadenze dei follow-up | sì | in pausa (gira solo per agenzie attive) | — |
| Sequenze email commerciali (stima_lead) | sì | **sì**, dal giro centrale, firma STIMA360 (ADM-3 BIS) | — |
| Promemoria appuntamenti, messaggi degli operatori, link del portale | sì | **no** (D2) | — |
| Cron flow (regole FLOW) | sì | in pausa (gira solo per agenzie attive) | — |
| Calendario personale collegato | sì | **in pausa** (nuovo) | **in pausa** (nuovo) |
| Prenotazione pubblica dal link | sì | **link non prenotabile** (nuovo) | **link non prenotabile** (nuovo) |
| Sessioni | — | revocate (ADM-1) | revocate (ADM-1) |

**Modifiche di ADM-3**

- `calendar_sync/repository.py`: una connessione è utilizzabile solo se membership, account dell'operatore e agenzia sono tutti attivi. Altrimenti resta in attesa (`waiting_connection`) senza essere toccata, e alla riattivazione riprende senza duplicati.
- `public_booking/repository.py` e `service.py`: il link si risolve solo se lo stesso trio è attivo. In caso contrario risponde come un link revocato, senza dire perché (regola di privacy esistente). Il link resta `active` e torna prenotabile alla riattivazione.

Per le agenzie sospese, l'unico invio è quello del giro centrale di ADM-3 BIS (sezione 7): stima e sequenze commerciali, a firma STIMA360, con gli stessi controlli di sempre su consensi e opt-out.

## 5. Criticità ancora aperte

1. **Agenzia archiviata con assegnazioni ancora attive.** I nuovi lead di quei Comuni vanno all'agenzia di sistema. La conferma lo dice, ma non c'è un blocco tecnico. Per vietare l'archiviazione finché ci sono assegnazioni attive serve una tua decisione.
2. **Nessuna presa in carico centrale da parte di persone.** Le email commerciali continuano dal centro (ADM-3 BIS), ma nessun operatore lavora i lead di un'agenzia sospesa. Se un giorno il team centrale dovrà seguirli, serve un meccanismo autorizzato e tracciato, per esempio un ingresso del Super Admin nell'agenzia sospesa (ADM-5).
3. **Ripresa dopo una sospensione lunga.** Alla riattivazione, i percorsi di comunicazione e i promemoria riprendono dal loro stato. Non ho verificato se messaggi diventati vecchi vengano scartati o inviati in ritardo: da valutare prima di sospensioni lunghe.
4. **Prenotazione pubblica rifiutata.** Il cliente vede «link non disponibile». Non c'è un rimando automatico ad altri recapiti.
5. **Traccia per singolo lead nel registro di piattaforma:** non introdotta, vedi il punto 3.
6. **Test non eseguibili qui:** i test `test_p27_6_postgres_real` sono stati aggiornati alla nuova regola (`d7`, `d7_bis`). Non girano in questo container perché creano un proprio cluster come root (ENVIRONMENT). La stessa regola è provata su PostgreSQL reale da `test_adm3::test_01/02`.

## 6. Test eseguiti

PostgreSQL 16 **locale e usa-e-getta**, mai il database TEST operativo.

**Collaudo mirato**: `tests/test_adm3_continuity_postgres.py`, 8 superati.

| Verifica richiesta | Test |
|---|---|
| Lead territoriali non persi con agenzia sospesa | `test_01`: esito `territory_suspended_agency`, stima, contatto e lead nell'agenzia competente, bridge ripetuto senza duplicati, login dell'agenzia sospesa rifiutato |
| Agenzia archiviata | `test_02`: ripiego dichiarato all'agenzia di sistema |
| Nessuna cancellazione o trasferimento | `test_03`: impronta di 16 tabelle e proprietari di immobili, contatti, stime, assegnazioni e membership invariati dopo sospensione, archiviazione, riattivazione, disabilitazione |
| Integrazioni di operatori o agenzie sospese | `test_04` (calendario), `test_05` (prenotazione pubblica): pausa e ripresa, righe intatte |
| Follow-up | `test_06`: la scansione salta l'agenzia sospesa e la riprende alla riattivazione |
| UI semplice, comandi funzionanti, «Verifica conseguenze» assente | `test_07[390]`, `test_07[1280]` (Chromium, sessione vera): Sospendi, Archivia, Riattiva agenzia; Disabilita e Riattiva operatore; Sospendi e Riattiva assegnazione, ciascuno con la sua conferma; agenzia di sistema senza comando; nessun errore JavaScript; nessuno scorrimento orizzontale |

**Verifica di regressione (una sola esecuzione)**

- **Unitari Rete:** `1 failed, 1155 passed`. Il fallimento è il preesistente `test_i1_059...`.
- **PostgreSQL e browser** (ADM-1, ADM-2, ADM-3, C1, C2, C3 API e browser, Zona-3, eliminazione edifici, Cestino, LMC-15, Voice-4, calendari A30-9A/9B/10, prenotazioni A30-12/P30, import dal sito): `1 failed, 349 passed, 10 errors`, nessuno saltato.
  - Il fallimento è il preesistente `test_voice_4::test_m1_migration_099`.
  - I 10 errori sono tutti di `test_agenda_booking_links_postgres` (`DuplicateTable agent_working_hours` nel setup). Si riproducono **identici** sul codice `0d24a8a`, prima di ADM-3: difetto preesistente della fixture di quel test.
- **Suite completa** senza DB, su un clone locale con le modifiche committate, confrontata con ADM-2: **nessun nuovo fallimento**. Sparisce il fallimento della sentinella `test_i2`, già corretta in ADM-2.
- **Unitari e statici di calendari, prenotazioni, instradamento e bridge:** 299 superati. I 30 fallimenti sono i test UI in Node (DOM simulato senza `createElementNS`), tutti già presenti nella baseline.
- `node --check` sui file JS modificati: ok. `git diff --check`: pulito.

**Test esistenti adeguati alla decisione D-A**

- `test_p27_6_lead_routing`:
  - B3 ora divide i casi: un'agenzia archiviata non instrada; un'agenzia sospesa tiene i suoi lead;
  - B4 verifica il nuovo filtro SQL;
  - il doppio in memoria legge lo stato dell'agenzia.
- `test_p27_6_postgres_real::d7`: aggiornato alla stessa regola.
- `test_adm2_impact_postgres`: tolto il test della UI rimossa; gli endpoint restano provati. L'anteprima tecnica di ADM-2 è stata allineata al nuovo instradamento.

**STOP.** ADM-3 è chiusa con un commit locale. Nessun push, deploy o migration. Per qualunque pubblicazione o fase successiva aspetto la tua autorizzazione.

## 7. ADM-3 BIS — continuità delle email della valutazione

**Regola definitiva del proprietario** (sostituisce le indicazioni precedenti in contrasto):

- le email automatiche relative alle valutazioni immobiliari e ai relativi follow-up continuano anche quando l'agenzia di riferimento è **sospesa o chiusa** (archiviata);
- la sospensione o chiusura non interrompe le comunicazioni già previste per i clienti, comprese quelle già in coda;
- si usano il sistema email e i cron esistenti;
- consenso e disiscrizione restano quelli di sempre;
- nessuna regola nuova sulle vecchie email, nessun blocco nuovo, nessuna funzionalità in più.

Valgono ancora le decisioni precedenti non in contrasto:

- il lead resta all'agenzia territoriale, nessun trasferimento;
- nessun accesso con account sospesi;
- firma STIMA360 sui follow-up composti mentre l'agenzia è sospesa o chiusa.

### 7.1 Come funziona

**Agenzie attive: invariato.** Le loro email partono dal cron di dispatch esistente (`run_communication_dispatch_cron.py`). Questo entra con l'account di un'agenzia, fa il giro delle sequenze e poi il dispatch. Il codice di questo percorso non è cambiato.

**Agenzie sospese o chiuse: giro centrale.** È agganciato al cron esistente `run_flow_p2b_cron.py` (servizio `stima360-flow-automation`), con il comando invariato `python run_flow_p2b_cron.py --agency-id 1`.

1. Dopo il giro FLOW sulle agenzie attive, che non cambia, il cron esegue `run_central_continuity()`, **qualunque sia `--agency-id`**.
2. Il giro centrale legge dal database le agenzie con stato `suspended` o `archived`. Per ciascuna, isolata dalle altre:
   - un giro del motore delle sequenze (`journey_tick.tick`);
   - un giro di dispatch email (`dispatcher.dispatch_batch`).
3. Entrambi girano con `SystemAgencyContext(agency_id, origin="communication_dispatch")`. Non c'è nessun login, nessuna sessione, nessuna rotta HTTP.
4. Un errore su un'agenzia non ferma le altre, e un guasto del FLOW non salta il giro centrale.

**Cosa spedisce il giro centrale** (`repository.CENTRAL_CONTINUITY_FILTER`):

- l'email della valutazione (`stima_pdf`);
- **ogni follow-up delle sequenze**, cioè ogni messaggio legato a un'iscrizione (`enrollment_id`): M1…M10, Tappa 6 compresa;
- anche i messaggi accodati prima della sospensione o chiusura, spediti così come erano stati composti.

**Cosa non spedisce.** Promemoria degli appuntamenti, messaggi scritti dagli operatori e link del portale proprietario: non sono email della valutazione né follow-up.

**Firma.** I follow-up composti mentre l'agenzia è sospesa o chiusa firmano «STIMA360 / su Stima360». Quelli già composti prima restano con il loro testo. Il mittente è unico per tutto il sistema (`SMTP_USER`).

**Controlli invariati.** Valgono gli stessi del dispatch normale:

- cancello del consenso marketing;
- disiscrizione;
- arresto della sequenza quando il consenso manca;
- finestra oraria;
- chiave anti-duplicato e claim con token;
- recupero degli stale;
- lotto di 10 messaggi per giro.

Alla riattivazione il giro centrale smette e il dispatch normale riprende da dove era rimasto, senza doppi invii.

Nessuna migration, tabella, stato, provider o credenziale nuova.

### 7.2 File modificati (rispetto a ADM-3)

| File | Modifica |
|---|---|
| `communication/central_continuity.py` (nuovo) | giro centrale per agenzia sospesa o chiusa: tick più dispatch filtrato |
| `communication/journey_repository.py` | il contesto di rendering legge anche `agencies.status` |
| `communication/journey_tick.py` | firma STIMA360 per agenzie sospese o chiuse |
| `communication/repository.py` | `CENTRAL_CONTINUITY_FILTER` e parametro `central_continuity` (default False) di `claim_due` |
| `communication/service.py`, `communication/dispatcher.py` | il parametro arriva al claim **solo** quando è True |
| `run_flow_p2b_cron.py` | passo `central_continuity` dopo il FLOW, con log sanitizzato |
| `tests/test_adm3_bis_central_followup_postgres.py` (nuovo) | collaudo su PostgreSQL |
| `tests/test_next6_p2b_flow_automation.py` | la fixture `cron` neutralizza il passo centrale: quei test provano il solo FLOW |
| `tests/test_p29_2_6e_channel_routing.py`, `tests/test_p29_2_4_router_contract.py`, `tests/test_p29_2_3_claim_sentinels.py` | sentinelle aggiornate in modo dichiarato (firma con `central_continuity`; nuovo file fuori dal nucleo) |

### 7.3 Cron da riattivare perché la regola funzioni davvero

Oggi su TEST **tutti e tre i cron sono sospesi**: finché restano così, non parte nessuna email automatica, per nessuna agenzia.

| Servizio Render | Serve per | Comando (invariato) |
|---|---|---|
| `stima360-flow-automation` | agenzie **sospese o chiuse** (giro centrale) | `python run_flow_p2b_cron.py --agency-id 1` |
| `stima360-communication-dispatch-test` | agenzia **attiva** del suo account | `run_communication_dispatch_cron.py` |
| `stima360-communication-dispatch-giulianova-test` | agenzia **attiva** Giulianova | `run_communication_dispatch_cron.py` |

**Agenzie attive senza un proprio cron di dispatch.** Il cron di dispatch lavora una sola agenzia, quella dell'account in `COMMUNICATION_DISPATCH_EMAIL`. Un'agenzia attiva senza un servizio dedicato non spedisce email automatiche, oggi come prima di questa fase. Va verificato su Render quali agenzie attive hanno il loro servizio.

**Variabili necessarie** (nessun valore qui):

- **`stima360-flow-automation`:**
  - `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`;
  - `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`;
  - `COMMUNICATION_UNSUBSCRIBE_SECRET` (lo stesso del backend);
  - `PUBLIC_BASE_URL`.

  Inoltre l'agenzia 1 deve essere attiva: con `--agency-id` di un'agenzia non attiva il cron si ferma subito, giro centrale compreso.
- **Cron di dispatch:** quelle già previste (`COMMUNICATION_DISPATCH_EMAIL`, `COMMUNICATION_DISPATCH_PASSWORD`, URL del backend). L'account deve essere `agency_admin` di un'agenzia attiva.

### 7.4 Collaudo

PostgreSQL 16 locale e usa-e-getta, mai il database TEST. Email simulate: l'adapter è quello vero, `invia_mail` è una spia.

**Collaudo mirato:** `tests/test_adm3_bis_central_followup_postgres.py`, **14 superati**.

| Verifica | Test |
|---|---|
| Agenzia attiva invariata | `test_01` |
| Agenzia sospesa **e** chiusa: stima e follow-up continuano dal centro | `test_02_03[suspended]`, `test_02_03[archived]`; contatto e lead restano dell'agenzia |
| Firma STIMA360 | `test_02_03`, `test_03b` |
| Email già in coda prima della sospensione o chiusura | `test_08[suspended]`, `test_08[archived]`: partono una volta, con il testo originale |
| Consenso e disiscrizione | `test_05`: consenso revocato, nessun invio; `test_05b`: senza consenso, nessuna sequenza |
| Nessun duplicato | `test_06` |
| Nessuna fuga fra agenzie | `test_07` |
| Riattivazione | `test_09`: il centro smette, il dispatch normale finisce il resto, niente doppioni |
| Promemoria, operatore, portale fuori dal giro centrale | `test_04` |
| Nessun accesso | `test_10` |
| Cron | `test_11`: con `--agency-id 1` lavora l'agenzia sospesa; un guasto dà esito 1 |

**Regressione**

- **PostgreSQL** (comunicazione P29/A32, Tappa 6 e 7, cutover email, ADM-1/2/3, FLOW): `546 passed, 9 skipped, 86 errors`.
  - Tappa 6 (follow-up delle richieste) passa intera: 44 su 44.
  - Gli 86 errori si riproducono identici sul commit base:
    - `a32_1`/`a32_2` reminders: `DuplicateTable agent_working_hours`, difetto di fixture;
    - `public_submission_receipts` e `tappa7_public`: si aspettano un socket macOS (ENVIRONMENT).
  - I 9 saltati sono i percorsi browser opt-in del sito pubblico.
- **Unitari:** nessun nuovo fallimento rispetto ad ADM-3, verificato su un clone con le modifiche committate (le sentinelle leggono `git status`).
- **Suite completa senza DB**, su un clone con le modifiche committate: `617 failed, 9102 passed, 2714 skipped, 63 errors`.
  - Stessi 617 fallimenti della baseline ADM-3, uno per uno.
  - Stessi 63 errori: suite che senza DSN o senza browser non partono (`tappa6_postgres`, `tappa7_*_postgres`, `p27_7_network_e2e`). Tappa 6, con il DSN, passa intera.
  - I 14 saltati in più sono il nuovo collaudo PostgreSQL.
- `git diff --check`: pulito.

### 7.5 Punti aperti

1. **Riattivazione dei cron e copertura delle agenzie attive:** vedi 7.3. È una tua azione su Render.
2. **Promemoria degli appuntamenti delle agenzie sospese o chiuse:** non partono, come da D2. La regola definitiva parla di valutazioni e follow-up; se vuoi che partano anche i promemoria, dimmelo.
3. **Passi assistiti** (M3): richiedono un operatore e restano in attesa.
4. **Esclusi da questa fase:**
   - `run_followup_p18d_cron.py`, rotto, solo documentato;
   - l'instradamento dei nuovi lead di un Comune la cui agenzia è chiusa (oggi vanno all'agenzia di sistema, attiva, che li segue normalmente);
   - la riga di audit per singolo lead.
