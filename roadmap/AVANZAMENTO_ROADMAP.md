# STIMA360 — Avanzamento della roadmap (dopo 4D–7)

Aggiornato al 31/10/2026, branch `core-0.1-test`. Base: commit `2d797f3` LIVE su TEST con le
migrazioni 119–122 applicate (verificato da chi ha accesso a Render, non da questa sessione).

Livelli, sempre distinti:

- **Sviluppato**: codice nel branch, con commit locale.
- **Collaudato localmente**: test automatici su PostgreSQL 16 reale e locale, più API e browser contro
  una replica del TEST. Mai Render, mai dati reali.
- **Verificato su TEST**: provato sul servizio TEST live.
- **Operativo per i clienti**: in produzione e attivo.

## 1. Stato per punto

| # | Punto | Sviluppato | Collaudato localmente | Verificato su TEST | Operativo |
|---|---|---|---|---|---|
| 1 | Chiusura collaudo 4D–7 (prenotazione, riprogrammazione, annullamento, Agenda, foglio visita, report e revoca) | — (nessun codice nuovo) | sì: API 117/117, browser 16/16 sulla replica | **no**: Render irraggiungibile da qui (§ 3) | no |
| 2 | Motore WhatsApp pronto all'invio, spento | sì (`5fdd4d5`, `b541d97`) | sì | no | no: resta spento |
| 3 | Collegamento di prenotazione dedicato | sì (`5fdd4d5`) | sì | no | no |
| 4 | Collegamento del numero operativo | piano (`BLOCCO-4_NUMERO_OPERATIVO_PIANO.md`) | — | no | no |
| 5 | Firma reale | ricerca (`BLOCCO-5_FIRMA_FORNITORI.md`) e parti indipendenti dal fornitore (`aee4842`) | sì, con un fornitore finto | no | no: nessun fornitore scelto |
| 6 | Lacune operative | sì (`b541d97`) | sì | no | no |

## 2. Cosa è stato fatto

### Punto 2 — motore WhatsApp (`auto_replies/`, migrazione 123)

- **Due interruttori, entrambi spenti di partenza:**
  - ambiente `WHATSAPP_AUTO_REPLY_MODE=send`;
  - agenzia in modo `send`.

  Vale il minore dei due: con l'ambiente su `simulate` nessuna agenzia invia.
- **Decisione:** avviene con il lock del messaggio ricevuto. Nella stessa transazione si fanno decisione, attività reale (in caso di passaggio all'operatore), messaggio `auto_reply` nel registro delle comunicazioni e riga d'invio. O tutto o niente.
- **Passaggio all'operatore:** l'attività è assegnata all'agente attivo del contatto. Senza un agente nasce non assegnata e il testo non promette un ricontatto.
- **Invio:** riusa il percorso del 4B: claim di quel messaggio, poi i controlli subito prima di Meta:
  - modi di ambiente e agenzia;
  - trasporto;
  - numero mittente dell'agenzia;
  - finestra di 24 ore;
  - STOP;
  - rinuncia di piattaforma;
  - divieto di contatto;
  - pausa della conversazione;
  - risposta manuale dell'operatore;
  - immobile ancora «Attivo».

  L'invio avviene fuori transazione. Un esito incerto non viene mai ritentato; una risposta rimasta in coda per un giro interrotto riparte una sola volta.
- **Background:** il giro gira nel runner di dispatch esistente solo con `COMMUNICATION_AUTO_REPLY_TICK=1`. Nessun cron nuovo è stato attivato.
- **Pause disponibili:**
  - generale: la variabile d'ambiente;
  - per agenzia: il modo;
  - per conversazione: la pausa già esistente.
- **Risposte:** nessuna informazione inventata o riservata. Restano le regole 4C, con i dati solo per un immobile «Attivo».

### Punto 3 — collegamento dedicato (`visit_booking/`, migrazione 123)

- **Emissione:** subito prima dell'invio, dopo tutti gli altri controlli, si emette un collegamento figlio di quello dell'operatore:
  - uno per risposta;
  - con scadenza di 7 giorni, mai oltre quella del padre;
  - revocato insieme al padre;
  - il token esiste solo come impronta.
- **Indirizzo:** entra solo nel testo consegnato al trasporto. Registro e decisione conservano il segnaposto.
- **Controlli al momento dell'uso:** padre valido, incarico in corso, agente attivo e immobile «Attivo».
- **Invariato:** il significato di «richiesta» (assenso per visita) e di «prenotazione confermata».

### Punto 6 — lacune operative (migrazione 124)

- **Divieto totale di contatto:**
  - è distinto dalla rinuncia marketing e non scrive consensi;
  - lo registra o lo revoca l'operatore della sessione, con un motivo o una nota, e la storia resta;
  - viene letto al momento dell'invio da dispatcher (anche per le journey), 4B e motore;
  - se la lettura fallisce non si invia (fail closed);
  - c'è un pannello nella scheda contatto → Comunicazioni.
- **Risposte in coda non più inviabili:** le risposte WhatsApp, manuali o automatiche, mai prese in carico con la finestra chiusa diventano «non inviata: finestra scaduta». Passano dal claim; quelle già prese in carico non si toccano.
- **Revoca dell'autorizzazione alle visite:**
  - motivo `owner_authorization_revoked` con nota obbligatoria (imposta anche dal database), autore e ora;
  - i collegamenti dedicati si chiudono come `parent_disabled`;
  - le visite «richieste» restanti vengono contate e non modificate.

### Punto 5 — firma (`esign/`, migrazione 125)

- **Contratto del fornitore:** ambiente, creazione idempotente, rilettura dello stato, documento, audit trail, verifica degli eventi. C'è uno strumento HMAC-SHA256 sul corpo grezzo, con confronto a tempo costante.
- **Ambiente:** una richiesta conta come firmata (`valid_signature`) solo se completata nell'ambiente reale (`production`) di un fornitore reale; in sandbox resta di prova. È una **condizione tecnica**: da sola non dimostra la validità legale della firma, che dipende dal tipo di firma (FES/FEA/FEQ), dal fornitore e dal documento ed è materia del parere legale. Il database rifiuta `signed` senza documento, data e livello.
- **Webhook pubblico** `/api/public/esign/webhooks/{provider}`:
  - una sola applicazione per id evento;
  - stato sempre riletto dal fornitore;
  - documento e audit trail conservati con la loro impronta.
- **Chiamate al fornitore:** fuori transazione sia per la creazione sia per l'annullamento.
- **Nessun adattatore reale:** `REAL_PROVIDERS` è vuoto e nessuna API è stata inventata.
- **Ricerca e raccomandazione:** Yousign come principale, Namirial come alternativa, in `BLOCCO-5_FIRMA_FORNITORI.md`. I dati vanno riletti sulle pagine ufficiali, perché la rete di questa sessione dava solo estratti.

### Punto 4 — numero operativo

Il piano è in `BLOCCO-4_NUMERO_OPERATIVO_PIANO.md`:
- ricostruzione di template della stima, PDF e Stima Pro, sopralluogo e webhook di `main`;
- opzione raccomandata A: il ricevitore attuale inoltra al **futuro CRM di produzione**, preservando template della stima, PDF e Stima Pro e sopralluogo dal sito;
- sul TEST solo dati sintetici: nessun inoltro di clienti reali come scelta predefinita; una prova reale solo con destinatari e perimetro concordati per iscritto;
- fasi con criteri d'uscita e procedura di ritorno indietro;
- difetto attuale di `main`: la verifica GET legge `hub_mode` invece di `hub.mode`, quindi una nuova verifica Meta non riuscirebbe. Non dimostra dove punti oggi il callback né che non sia mai stato verificato: configurazione e consegne reali restano da verificare;
- nessun merge di storie separate.

Nessuna modifica a `main`, ai callback di Meta o al servizio template.

## 3. Verifiche live mancanti (Render irraggiungibile)

Questa sessione non raggiunge `api.render.com` né `*.onrender.com`: il proxy dell'ambiente risponde
403. Nessuna di queste verifiche è stata eseguita né dichiarata superata:

1. `PUBLIC_BOOKING_IP_PEPPER` PRESENTE sul servizio TEST, senza mostrarne il valore.
2. Sul TEST live, con dati di prova:
   - prenotazione `/visita/…`;
   - riprogrammazione e annullamento da `/visita-gestione/…`;
   - la stessa visita in Agenda e nella scheda immobile;
   - foglio visita (PDF);
   - report proprietario e PDF;
   - revoca dell'accesso del proprietario, con report e PDF non più disponibili;
   - pagine a 390 px.
3. Dopo l'eventuale rilascio 123–125: le schermate del § 5 di `RILASCIO_TEST_123_125.md`.

**Accesso necessario** per farle eseguire a questa sessione: nelle impostazioni dell'ambiente cloud,
in Network access, aggiungere `api.render.com` e `*.onrender.com` tra gli Allowed domains. Per leggere
le variabili del servizio serve anche una chiave API Render tra i segreti dell'ambiente.

## 4. Decisioni e accessi indispensabili del titolare

1. **Push e deploy su TEST** di `core-0.1-test`, poi Render One-Off Job 123–125 (`RILASCIO_TEST_123_125.md`). In questo incarico non sono autorizzati.
2. **Numero operativo:**
   - accesso alla console Meta, per capire dove punta oggi il callback;
   - accesso al servizio template esterno;
   - scelta dell'opzione A, B o C;
   - eventuale prova reale circoscritta sul TEST: destinatari, perimetro, durata, cancellazione (di default sul TEST solo dati sintetici);
   - quando e come nasce il CRM di produzione, destinazione del collegamento;
   - agenzia di destinazione del numero.
3. **Firma:**
   - scelta del fornitore e apertura dell'account sandbox;
   - tipo di firma per foglio visita e incarico, dopo un parere legale;
   - DPA;
   - budget;
   - URL HTTPS pubblico per i webhook.
4. **Endpoint legacy `POST /api/admin/whatsapp/reply`** (`main.py:662`, codice esistente da prima di questi blocchi). Con qualsiasi sessione operatore invia un vero WhatsApp di testo, se `WHATSAPP_PHONE_ID` e `WHATSAPP_TOKEN` sono presenti. Scavalca registro, finestra, agenzia e divieto di contatto. Nel repository non ha consumatori. Va deciso se rimuoverlo o proteggerlo: non l'ho toccato perché è fuori perimetro.
5. **Identificativi in chiaro nel repository:** numero, Phone ID e WABA compaiono in `BLOCCO-4D_ATTIVAZIONE.md`, `register_whatsapp.py`, `whatsapp_certificate.txt` e `main.py`, secondo la ricognizione di `BLOCCO-4_NUMERO_OPERATIVO_PIANO.md`. Va deciso se ripulirli.
6. **Regole di business da confermare:**
   - validità di 7 giorni del collegamento dedicato;
   - attività di passaggio all'operatore con priorità alta e scadenza a 4 ore;
   - chi può revocare un divieto di contatto: oggi qualunque operatore che vede il contatto, con nota obbligatoria.

## 5. Parti ancora simulate o spente

- **Risposte automatiche:** spente. In TEST, al massimo in simulazione; l'invio reale richiede due variabili, il modo `send` dell'agenzia e il trasporto Meta del 4B acceso.
- **Firma:** solo l'adattatore simulato; firme reali impossibili (nessun adattatore).
- **Invio manuale WhatsApp (4B):** spento su TEST (`WHATSAPP_OUTBOUND_MODE` assente).
- **Collegamento del numero operativo:** solo pianificato.
