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

## 7. ADM-3 BIS — continuità centrale dei follow-up email

Decisioni del proprietario applicate:

- **D1:** il lead resta all'agenzia territoriale; i follow-up email commerciali continuano tramite STIMA360 centrale. Nessun trasferimento.
- **D2:** nessun promemoria di appuntamento per le agenzie sospese.
- **D3:** mittente e firma STIMA360.
- Nessun accesso con account sospesi; consenso, opt-out, finestre di invio e protezione dai duplicati obbligatori; nessun nuovo costo o provider.

### 7.1 Come funziona

Un solo processo centrale: il cron esistente `run_flow_p2b_cron.py` (servizio Render `stima360-flow-automation`, comando invariato `python run_flow_p2b_cron.py --agency-id 1`).

1. Il cron fa prima il giro FLOW di sempre, solo sulle agenzie attive: **invariato**.
2. Poi, **qualunque sia `--agency-id`**, esegue `run_central_continuity()`. Questo legge dal database le agenzie con stato `suspended` e, per ciascuna, isolata dalle altre:
   - un giro del motore delle sequenze (`journey_tick.tick`);
   - un giro di dispatch email (`dispatcher.dispatch_batch`).
   Entrambi girano con `SystemAgencyContext(agency_id, origin="communication_dispatch")`, l'origine che il dispatcher usa già.
3. **Nessun login**, nessuna sessione, nessuna rotta HTTP. `communication/central_continuity.py` non è montato su nessun router.
4. Un errore su un'agenzia non ferma le altre. Il giro centrale gira anche se il FLOW di un'agenzia attiva ha avuto un guasto tecnico. L'esito del cron segue la convenzione esistente: 0 bene, 2 applicativo, 1 tecnico.

**Cosa spedisce il giro centrale.** Il claim riceve un filtro in più (`repository.CENTRAL_CONTINUITY_FILTER`), attivo solo in questo percorso. Passano soltanto:

- l'email della stima (`stima_pdf`), il cui testo è già centrale;
- i passi di sequenza composti **durante la sospensione**, che portano nei metadata il segno `{"signature": "stima360_central"}`.

Tutto il resto resta escluso per costruzione: promemoria (D2), messaggi degli operatori, link del portale proprietario e ogni messaggio accodato prima della sospensione.

**Mittente e firma.**

- Il mittente è già unico per tutto il sistema (`From` = `SMTP_USER`).
- Per un'agenzia sospesa, il motore compone i passi con `agency_name = "STIMA360"`. La chiusura dei template diventa «STIMA360 / su Stima360», con il link di disiscrizione di sempre.

**Agenzie attive.** Il loro dispatch non passa il nuovo argomento: la chiamata a `claim_due` è identica a prima. I loro passi nascono senza segno e con la firma dell'agenzia (provato da `test_01`).

**Controlli invariati.** Valgono gli stessi del dispatch normale:

- claim con token (`FOR UPDATE SKIP LOCKED`) e recupero degli stale;
- cancello del consenso marketing per messaggio;
- finestra oraria;
- chiave di idempotenza;
- lotto di 10 messaggi per giro.

Nessuna migration, nessuna tabella, nessuno stato nuovo, nessun provider, nessuna credenziale.

### 7.2 Messaggi già in coda prima della sospensione

Non hanno il segno centrale, quindi il giro centrale **non li reclama**:

- restano `queued`, intatti (stessa riga, nessun tentativo);
- non vengono cancellati né marcati `suppressed`;
- non serve uno stato nuovo.

Alla riattivazione tornano visibili al dispatch normale dell'agenzia, a lotti di 10 per giro. Oggi quel dispatch è il cron di dispatch, che su TEST è sospeso.

**Decisione che resta al proprietario.** Se, alla riattivazione, questi messaggi accodati prima della sospensione (con la firma dell'agenzia e forse ormai vecchi) debbano partire o no. Oggi partirebbero col dispatch normale dell'agenzia, appena riattivato il suo cron. Non ho aggiunto nessun meccanismo per impedirlo o per scartarli.

### 7.3 File modificati

| File | Modifica |
|---|---|
| `communication/central_continuity.py` (nuovo) | giro centrale per agenzia sospesa: tick più dispatch filtrato |
| `communication/journey_repository.py` | il contesto di rendering legge anche `agencies.status` |
| `communication/journey_tick.py` | firma STIMA360 e segno nei metadata per le agenzie sospese |
| `communication/repository.py` | `CENTRAL_CONTINUITY_FILTER` e parametro `central_continuity` (default False) di `claim_due` |
| `communication/service.py`, `communication/dispatcher.py` | il parametro passa fino al claim **solo** quando è True |
| `run_flow_p2b_cron.py` | passo `central_continuity` dopo il FLOW, con log sanitizzato |
| `tests/test_adm3_bis_central_followup_postgres.py` (nuovo) | collaudo su PostgreSQL |
| `tests/test_next6_p2b_flow_automation.py` | la fixture `cron` neutralizza il passo centrale: quei test provano il solo FLOW |
| `tests/test_p29_2_6e_channel_routing.py`, `tests/test_p29_2_4_router_contract.py`, `tests/test_p29_2_3_claim_sentinels.py` | sentinelle aggiornate in modo dichiarato (firma con `central_continuity`; nuovo file fuori dal nucleo) |

### 7.4 Configurazione Render necessaria (nessun valore qui)

Servizio `stima360-flow-automation`, **da non riattivare senza tua autorizzazione**. Comando invariato. Il servizio deve avere:

- **database:** `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`;
- **email:** `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER` (è anche il mittente), `SMTP_PASS`;
- **link di disiscrizione:** `COMMUNICATION_UNSUBSCRIBE_SECRET`, lo stesso del backend che risolve i link;
- **link nei testi:** `PUBLIC_BASE_URL`.

Facoltative, già usate dal FLOW: `FLOW_RECOVERY_LIMIT`, `FLOW_SCAN_LIMIT`.

**Da verificare su Render prima di riattivare:**

- che queste variabili ci siano su quel servizio;
- che l'agenzia 1 sia attiva, perché con `--agency-id` di un'agenzia non attiva il cron esce prima di tutto, giro centrale compreso.

### 7.5 Collaudo

PostgreSQL 16 locale e usa-e-getta, mai il database TEST. Email simulate: l'adapter è quello vero, `invia_mail` è una spia, nessuna email è partita.

**Collaudo mirato:** `tests/test_adm3_bis_central_followup_postgres.py`, **12 superati**.

| Verifica richiesta | Test |
|---|---|
| Agenzia attiva invariata | `test_01` |
| Agenzia sospesa: follow-up centrale | `test_02_03`: iscrizione, M1 composta e spedita nello stesso giro, stima in coda spedita; contatto e lead restano dell'agenzia |
| Mittente e firma | `test_02_03`, `test_03b` |
| Nessun promemoria | `test_04`: anche messaggi dell'operatore e link del portale restano in coda |
| Nessun invio senza consenso | `test_05`: consenso revocato, il motore annulla M1 e il dispatcher sopprime; `test_05b`: senza consenso nessuna sequenza |
| Nessun duplicato | `test_06`: quattro giri, due invii |
| Nessuna fuga fra agenzie | `test_07`: un'agenzia attiva accanto, perfino con un messaggio col segno, resta intatta |
| Messaggi accodati prima della sospensione | `test_08`: riga identica, zero tentativi |
| Riattivazione senza invio massivo | `test_09`: il giro centrale smette; il dispatch normale ne spedisce 10, ne restano 2 |
| Nessun accesso | `test_10`: sessioni e utenti invariati |
| Cron | `test_11`: con `--agency-id 1` lavora l'agenzia sospesa; un guasto dà esito 1 |

**Regressione**

- **Unitari** comunicazione, consenso, FLOW, ADM: nessun nuovo fallimento rispetto ad ADM-3, verificato su un clone locale con le modifiche committate (le sentinelle che leggono `git status` guardano il working tree).
- **PostgreSQL** comunicazione (P29-*, A32-*, cutover email), ADM-1/2/3, FLOW: `499 passed, 9 skipped, 78 errors`.
  - I 78 errori si riproducono **identici sul commit `5a8c9b2`**, prima di ADM-3 BIS:
    - `test_a32_1_reminders_postgres` e `test_a32_2_reminder_postgres`: `DuplicateTable agent_working_hours`, difetto di fixture già noto;
    - `test_public_submission_receipts_postgres`: si aspetta un socket macOS (ENVIRONMENT).
  - I 9 saltati sono i percorsi browser opt-in del sito pubblico.
  - Per questo i promemoria su PostgreSQL non sono stati provati qui; i loro test unitari passano.
- **Suite completa senza DB**, su un clone locale con le modifiche committate: `617 failed, 9102 passed, 2712 skipped, 63 errors`. Confronto con ADM-3 (`617 failed, 9102 passed, 2700 skipped, 63 errors`):
  - stessi 617 fallimenti, uno per uno;
  - stesso numero di errori (la baseline ne registrava solo il conteggio): sono le suite che chiedono un proprio cluster o un browser (`tappa6_postgres`, `tappa7_*_postgres`, `p27_7_network_e2e`);
  - i 12 saltati in più sono il nuovo collaudo PostgreSQL, che senza DSN si salta.
- `git diff --check`: pulito.

### 7.6 Limiti e punti aperti

1. **Tutti i cron sono sospesi su TEST.** Finché `stima360-flow-automation` resta sospeso, il giro centrale non gira. Riattivarlo è una tua decisione.
2. **Link del portale proprietario** (`owner_login_link`): non parte per le agenzie sospese.
3. **Passi assistiti** (M3): richiedono un operatore e restano in attesa.
4. **Testo della firma:** la chiusura dice «STIMA360 / su Stima360». Se vuoi un'altra formula serve una nuova versione dei template, che sono immutabili.
5. **Messaggi accodati prima della sospensione:** la decisione è al punto 7.2.
6. **Esclusi da questa fase:**
   - il cron `run_followup_p18d_cron.py`, rotto (autenticazione Basic), solo documentato;
   - l'instradamento verso un'agenzia archiviata, decisione aperta;
   - la riga di audit per singolo lead.
