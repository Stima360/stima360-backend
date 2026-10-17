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
5. Il cliente riceve comunque subito la sua stima per email, con i recapiti centrali STIMA360, e `info@stima360.it` riceve la notifica, come prima. Questi due invii non dipendono dall'agenzia.

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
| Email della stima al cliente e notifica a info@ | sì | **sì** (centrale, invariato) | — |
| Contatto, lead e task di follow-up alla nascita della stima | sì | **sì**, nell'agenzia; restano in attesa | — |
| Scansione delle scadenze dei follow-up | sì | in pausa (gira solo per agenzie attive) | — |
| Cron flow, percorsi di comunicazione, promemoria, invio messaggi | sì | in pausa (il cron entra con un account dell'agenzia, rifiutato da ADM-1) | — |
| Calendario personale collegato | sì | **in pausa** (nuovo) | **in pausa** (nuovo) |
| Prenotazione pubblica dal link | sì | **link non prenotabile** (nuovo) | **link non prenotabile** (nuovo) |
| Sessioni | — | revocate (ADM-1) | revocate (ADM-1) |

**Modifiche di ADM-3**

- `calendar_sync/repository.py`: una connessione è utilizzabile solo se membership, account dell'operatore e agenzia sono tutti attivi. Altrimenti resta in attesa (`waiting_connection`) senza essere toccata, e alla riattivazione riprende senza duplicati.
- `public_booking/repository.py` e `service.py`: il link si risolve solo se lo stesso trio è attivo. In caso contrario risponde come un link revocato, senza dire perché (regola di privacy esistente). Il link resta `active` e torna prenotabile alla riattivazione.

Nessun invio parte per conto di un'agenzia sospesa e nessun contesto centrale viene simulato. La ripresa avviene alla riattivazione, con gli stessi controlli di sempre su consensi e opt-out.

## 5. Criticità ancora aperte

1. **Agenzia archiviata con assegnazioni ancora attive.** I nuovi lead di quei Comuni vanno all'agenzia di sistema. La conferma lo dice, ma non c'è un blocco tecnico. Per vietare l'archiviazione finché ci sono assegnazioni attive serve una tua decisione.
2. **Nessuna presa in carico centrale.** Per decisione D-B, i lead di un'agenzia sospesa aspettano senza essere lavorati. Se un giorno il team centrale dovrà seguirli, serve un meccanismo autorizzato e tracciato, per esempio un ingresso del Super Admin nell'agenzia sospesa (ADM-5).
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
