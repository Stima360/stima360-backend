# Audit dell'interfaccia del CRM STIMA360 (TEST) — proposta di semplificazione

**Tipo:** AUDIT ONLY. Nessun codice, dato, API, permesso o automazione è stato modificato.
**Base:** `core-0.1-test` a `daaeb1b` (OS Shell: `static/os_shell/assets/main.js`, `views/`, `components/`).
**Metodo:** lettura della barra laterale (`SECTIONS`, voci condizionali «Rete», «Voce», menu utente),
delle rotte registrate, delle schede e dei comandi reali di ogni vista. Ogni voce citata esiste nel repository.

**Criterio:** un consulente deve cercare clienti, inserire e trovare immobili, ottenere incarichi, gestire
richieste e visite, rispondere ai clienti, preparare valutazioni e report, vedere appuntamenti e attività.
Tutto il resto va dietro le quinte.

Legenda delle decisioni: **TENERE** · **SPOSTARE** · **NASCONDERE** (solo amministratori) · **UNIFICARE** · **PROPORRE DI ELIMINARE**.

## 1. Com'è oggi

Barra laterale per ogni operatore (17 voci): Oggi, Agenda, Contatti, Stime, Stime dettagliate, Zona, Edifici,
Immobili, Venditori, Acquisizioni, Incarichi, Acquirenti, Abbinamenti, Attività, Automazioni, Messaggi WhatsApp,
Cestino. In più: «Rete» (solo amministratori di piattaforma), «Voce» (solo se il server la abilita), e nel menu
utente «Dati agenzia e logo» e «Logout». Non esiste una pagina «Impostazioni»: le impostazioni sono sparse
(dati agenzia nel menu utente, Google Calendar dentro Agenda, risposte automatiche dentro la chat WhatsApp).

Schede con molte tab: scheda contatto 12 tab, scheda immobile 11 tab (+ Pertinenze dove previsto), scheda
acquirente 9 tab, Automazioni 5 tab, Rete 6 tab.

## 2. Tabella delle decisioni

| Schermata | Voce attuale | Decisione | Nuova posizione |
|---|---|---|---|
| Sidebar | Oggi | TENERE | Prima voce («Oggi»): assorbe anche Attività (vedi sotto) |
| Sidebar | Agenda | TENERE | Seconda voce |
| Sidebar | Contatti | TENERE | Terza voce («Clienti») |
| Sidebar | Stime | TENERE | «Valutazioni»: un solo archivio, con i filtri Tutte / Base / Dettagliate già presenti nella pagina |
| Sidebar | Stime dettagliate | UNIFICARE | Filtro «Dettagliate» dentro «Valutazioni» (`stime.js` usa già lo stesso archivio con `view=detailed`) |
| Sidebar | Zona | UNIFICARE | Pulsante «Mappa» dentro Immobili / Edifici (la voce apre già `#/edifici/mappa`: stessa mappa) |
| Sidebar | Edifici | SPOSTARE | Tab o filtro «Palazzine» dentro Immobili; la scheda edificio resta raggiungibile dall'immobile |
| Sidebar | Immobili | TENERE | Quarta voce |
| Sidebar | Venditori | UNIFICARE | Prima fase di un'unica voce «Acquisizioni» (Venditori → Acquisizioni → Incarichi come tab) |
| Sidebar | Acquisizioni | TENERE | Voce «Acquisizioni» (contenitore della pipeline) |
| Sidebar | Incarichi | UNIFICARE | Tab «Incarichi» dentro «Acquisizioni» (è una vista, non un archivio: `incarichi.js`) |
| Sidebar | Acquirenti | TENERE | Voce «Richieste» |
| Sidebar | Abbinamenti | SPOSTARE | Tab «Abbinamenti» dentro Richieste; il «Calcola» globale nel menu «⋯» della stessa pagina |
| Sidebar | Attività | UNIFICARE | Dentro «Oggi» (stesse API: task, attività, visite); la lista completa come tab «Tutte» |
| Sidebar | Automazioni | NASCONDERE | Solo titolare/amministratore, in «Impostazioni → Automazioni» (è la vista di FLOW: regole fisse, cronologia, consensi) |
| Sidebar | Messaggi WhatsApp | TENERE | Voce «WhatsApp» (chat già ristilizzata) |
| Sidebar | Cestino | SPOSTARE | Menu utente / «Impostazioni» (la vista globale del Super Admin resta lì) |
| Sidebar (condizionale) | Rete | TENERE (già nascosta) | Resta visibile solo a `is_platform_admin`, come oggi |
| Sidebar (condizionale) | Voce | TENERE (già condizionale) | Resta solo con il servizio attivo; da valutare se diventare un pulsante «microfono» nella barra in alto |
| Menu utente | Dati agenzia e logo | SPOSTARE | Dentro una pagina «Impostazioni» (unico punto per: dati agenzia, Google Calendar, automazioni, risposte automatiche, Cestino) |
| Oggi | Priorità di oggi, Task scaduti/di oggi, Attività recenti, Visite | TENERE | Invariati; aggiungere i «Non letti WhatsApp» come riga (dato già fornito da `/api/inbound-messages/conversations`) |
| Attività | Task / Attività / Visite | UNIFICARE | Tab «Tutte» di Oggi (stessi endpoint) |
| Contatti | Lista, ricerca, + Nuovo contatto | TENERE | Invariata |
| Scheda contatto | Panoramica, Timeline, Immobili, Richieste, Stime, Visite, Comunicazioni, Documenti | TENERE | Prima fila di tab (8) |
| Scheda contatto | Lead | SPOSTARE | Dentro «Panoramica» come riquadro «Opportunità» (il termine «lead» non è del consulente) |
| Scheda contatto | Abbinamenti | SPOSTARE | Dentro «Richieste» (gli abbinamenti sono della richiesta) |
| Scheda contatto | Attività, Task | UNIFICARE | Una tab «Attività» con due elenchi (da fare / fatte) |
| Scheda contatto | Divieto di contatto, consensi (in Comunicazioni) | TENERE | Restano in «Comunicazioni» |
| Immobili | Lista, filtri Stato/Commerciale/Censimento, + Nuovo immobile | TENERE | Invariati; «Censite / Da chiarire» come filtro secondario |
| Immobili | Condivisi nel territorio / Tutti gli accessibili | SPOSTARE | Filtro «Altre agenzie» dentro la ricerca |
| Scheda immobile | Panoramica, Proprietari, Foto, Documenti, Visite, Acquirenti compatibili, Valutazioni, Report proprietario | TENERE | Prima fila di tab (8) |
| Scheda immobile | Censimento, Pertinenze | SPOSTARE | Sotto-sezione di «Panoramica» (visibile per prima solo se l'immobile è in censimento, come già fa `tabsFor`) |
| Scheda immobile | Proposte | SPOSTARE | Dentro «Acquirenti compatibili» (una proposta nasce da un acquirente) |
| Scheda immobile | Storico interazioni | UNIFICARE | Con la Timeline del contatto: una sola «Cronologia» |
| Scheda immobile | Azioni: Cambia stato, Archivia, Crea/Completa/Annulla vendita, Collega contatto | SPOSTARE | Menu «⋯» della scheda, come nella chat WhatsApp |
| Scheda immobile | Fogli visita e firma (`visit-sheets`) | TENERE | Dentro «Visite»; la firma reale resta spenta |
| Edifici | Lista, + Nuovo, filtri | SPOSTARE | Tab «Palazzine» di Immobili |
| Scheda edificio | Unità, Pertinenza, Duplica, Collega a…, Modifica palazzina | TENERE | Invariata (si apre dall'immobile o dalla tab) |
| Zona / Mappa | Mappa, inserisci da mappa, posiziona | TENERE | Pulsante «Mappa» in Immobili; «Inserisci da mappa» resta nella mappa |
| Venditori | Registra interazione, Richiamo, Fase, Smetti, Avvia acquisizione | TENERE | Tab «Venditori» di Acquisizioni; «Fase» (stage del lead) nel menu riga |
| Acquisizioni | + Nuova acquisizione (immobile → proprietari → dati → appuntamento), Continua: fissa appuntamento | TENERE | Invariato |
| Scheda acquisizione | Genera incarico, Modifica, Nuovo appuntamento, Segna come persa / per errore | TENERE | «Genera incarico» pulsante principale; gli esiti nel menu «⋯» |
| Incarichi | Lista con scadenze | UNIFICARE | Tab «Incarichi» di Acquisizioni |
| Scheda incarico | Modifica incarico, Programma visita | TENERE | Invariata |
| Acquirenti | + Nuova richiesta (21 campi), lista | TENERE | Voce «Richieste»; il modulo in due passi (essenziali / criteri avanzati) |
| Scheda acquirente | Panoramica, Criteri, Immobili compatibili, Visite, Proposte | TENERE | Prima fila |
| Scheda acquirente | Abbinamenti | UNIFICARE | Con «Immobili compatibili» (stessa esperienza già unificata nella scheda immobile) |
| Scheda acquirente | Attività, Task, Storico | UNIFICARE | Una tab «Cronologia» |
| Scheda acquirente | Crea vendita / Completa / Annulla | SPOSTARE | Menu «⋯» |
| Abbinamenti | Lista con Classe, Freschezza, Revisione, Calcola | SPOSTARE | Tab dentro Richieste; le colonne tecniche (Freschezza, Revisione) solo nel dettaglio |
| Scheda abbinamento | Panoramica, Analisi, Storico ricalcoli, Feedback, Ricalcola | NASCONDERE | «Analisi» e «Storico ricalcoli» solo ad amministratori; Panoramica + Feedback + «Programma visita» per il consulente |
| Agenda | Settimana / Giorno / Lista, + Nuovo appuntamento | TENERE | Invariata |
| Agenda | Pannello Google Calendar | SPOSTARE | «Impostazioni → Calendario» (resta l'indicatore di stato in Agenda) |
| Agenda | Dialog appuntamento (Tipo, Agente, Cliente, Immobile, Lead, Stima, Data, Ora, Durata, Luogo, Titolo, Note…) | TENERE | Campi «Lead» e «Stima» spostati in un «Altro» a scomparsa |
| Valutazioni (scheda immobile) | Bozza, approvazione, versioni, PDF | TENERE | Invariata |
| Report proprietario | Anteprima e PDF, revoca accesso | TENERE | Invariata |
| WhatsApp | Chat, ricerca, invio con conferma, menu «⋯» | TENERE | Già fatta |
| WhatsApp menu «⋯» | Risposte automatiche (pausa, simulazione agenzia, modo d'invio) | NASCONDERE | Pausa resta nel menu; simulazione e modo d'invio in «Impostazioni → WhatsApp» (titolare/amministratore) |
| WhatsApp menu «⋯» | Suggerimento di risposta | TENERE | Nel menu, com'è ora |
| Automazioni | Panoramica, Regole, Cronologia, Sequenze email, Consensi email | NASCONDERE | «Impostazioni → Automazioni» (titolare/amministratore); «Consensi email» utile anche al consulente: collegamento dalla scheda contatto già esistente |
| Automazioni | Dettaglio regola: Attiva / Disattiva | NASCONDERE | Idem |
| Cestino | Ripristina, filtri, vista globale | SPOSTARE | Menu utente / Impostazioni; la vista globale del Super Admin invariata |
| Rete | Agenzie, Account, Territori, Modifiche territoriali, Registro, Informative privacy, Eliminazioni | TENERE (già riservata) | Invariata, solo amministratori di piattaforma |
| Voce | Comandi vocali | TENERE (condizionale) | Invariata |
| Dati agenzia e logo | Ragione sociale, P. IVA, logo… | SPOSTARE | «Impostazioni → Agenzia» |

Nessuna voce è proposta per l'eliminazione: ogni schermata ha dati o un consumatore reale. Le candidate
alla rimozione **dalla vista principale** (non dal codice) sono «Stime dettagliate», «Zona», «Incarichi» e
«Attività» come voci di barra laterale, perché aprono dati già raggiungibili da un'altra voce.

## 3. Le 10 semplificazioni più importanti

1. **Barra laterale da 17 a 8 voci** (vedi § 5): ogni voce tolta diventa una tab o un filtro, nessun
   dato sparisce.
2. **Oggi + Attività in una pagina**: oggi usano gli stessi tre endpoint (task, attività, visite) e un
   consulente deve guardare due posti per la stessa giornata.
3. **Una sola voce «Valutazioni»**: «Stime» e «Stime dettagliate» sono lo stesso archivio con un filtro.
4. **Una pipeline «Acquisizioni»** con tre tab (Venditori → Acquisizioni → Incarichi) invece di tre
   voci: oggi l'incarico si genera solo dalla scheda acquisizione, ma la barra lo fa sembrare un
   archivio separato.
5. **«Zona» ed «Edifici» dentro Immobili**: «Zona» apre già la mappa degli Edifici; una sola porta
   d'ingresso «Immobili» con pulsante «Mappa» e tab «Palazzine».
6. **Abbinamenti dentro Richieste**: il motore MATCH resta, ma il consulente lo incontra dove serve
   (richiesta e immobile), non come voce di primo livello con colonne tecniche.
7. **Una pagina «Impostazioni»** (titolare/amministratore): dati agenzia e logo, Google Calendar,
   automazioni, risposte automatiche WhatsApp, Cestino. Oggi sono in quattro posti diversi.
8. **Schede con 8 tab al massimo**: nelle schede contatto, immobile e acquirente le tab «Attività»,
   «Task», «Storico/Timeline» diventano una sola «Cronologia»; «Lead» e «Censimento» entrano in
   «Panoramica»; le azioni di stato (Archivia, vendita…) vanno nel menu «⋯».
9. **Moduli in due passi**: «Nuovo immobile» (36 campi) e «Nuova richiesta» (21 campi) con i campi
   essenziali subito e il resto in «Altri dati»; il salvataggio backend non cambia.
10. **Linguaggio del consulente**: «Lead» → «Opportunità», «Stime» → «Valutazioni», «Acquirenti» →
    «Richieste», «Messaggi WhatsApp» → «WhatsApp», «Freschezza/Revisione/Readiness» solo nei dettagli.

## 4. Voci doppie o ridondanti

| Doppione | Dove | Nota |
|---|---|---|
| Stime / Stime dettagliate | barra laterale | stesso archivio, `stime.js` con filtro |
| Zona / Edifici → mappa | barra laterale | `zona` ha `target: ['edifici','mappa']`: stessa mappa |
| Oggi / Attività | barra laterale | stessi endpoint (task, attività, visite) |
| Abbinamenti (voce) / Acquirenti compatibili (tab immobile) / Immobili compatibili + Abbinamenti (tab acquirente) | tre punti | la scheda immobile li ha già unificati («SEMPLIFICAZIONE-CRM-1»); la scheda acquirente e la voce di barra no |
| Attività / Task / Storico | schede contatto e acquirente | tre tab per una cronologia |
| Storico interazioni (immobile) / Timeline (contatto) | schede | la stessa interazione registrata da Venditori compare in entrambe |
| Incarichi (voce) / Genera incarico (scheda acquisizione) | barra + scheda | la voce è solo una vista della pipeline |
| Risposte automatiche: pausa e modo agenzia nella chat / nessuna pagina impostazioni | WhatsApp | impostazioni d'agenzia dentro una conversazione |
| Google Calendar: pannello in Agenda | Agenda | impostazione personale in una pagina operativa |

## 5. Proposta di barra laterale essenziale

Per tutti gli operatori (8 voci):

1. **Oggi** — priorità, task, attività, visite, non letti WhatsApp (tab «Tutte» = ex Attività)
2. **Agenda**
3. **Clienti** (Contatti)
4. **Immobili** — tab Elenco · Palazzine (Edifici) · pulsante Mappa (Zona)
5. **Acquisizioni** — tab Venditori · Acquisizioni · Incarichi
6. **Richieste** (Acquirenti) — tab Richieste · Abbinamenti
7. **Valutazioni** (Stime) — filtri Tutte · Base · Dettagliate
8. **WhatsApp**

Secondarie, fuori dalla barra principale:
- **Impostazioni** (menu utente; titolare/amministratore): Agenzia e logo · Calendario · Automazioni ·
  WhatsApp (risposte automatiche) · Cestino.
- **Rete**: solo amministratori di piattaforma, come oggi.
- **Voce**: solo con il servizio attivo, come oggi.

## 6. Operazioni che oggi richiedono troppi passaggi

| Operazione | Oggi | Proposta |
|---|---|---|
| Ottenere un incarico da un contatto che vuole vendere | Contatti → scheda → Immobili → Nuovo immobile (36 campi) → Venditori → Registra interazione → Avvia acquisizione → Acquisizione (immobile, proprietari, dati, appuntamento) → scheda acquisizione → Genera incarico → Incarichi: 5 schermate | «Nuova acquisizione» anche dalla scheda contatto e dalla scheda immobile, con immobile essenziale (indirizzo, tipologia, mq) creato nello stesso passo; «Genera incarico» come pulsante principale della scheda acquisizione |
| Inserire un immobile | modulo unico da 36 campi, tab Censimento per prima nei censiti | due passi: essenziali (indirizzo, tipologia, mq, prezzo, proprietario) e «Altri dati»; Censimento dentro Panoramica |
| Registrare una richiesta acquirente | modulo da 21 campi | essenziali (contatto, comune, tipologia, budget) + «Criteri avanzati» |
| Fissare una visita | Agenda → + Nuovo appuntamento → dialog con 12+ campi (Tipo, Agente, Cliente, Immobile, Lead, Stima, Data, Ore, Durata, Luogo, Titolo, Note) | «Programma visita» già presente in incarico e abbinamento: portarlo anche nella scheda immobile (tab Visite) e nella richiesta; «Lead» e «Stima» a scomparsa |
| Vedere cosa fare oggi | Oggi + Attività + Agenda | Oggi con tab «Tutte» e la settimana in miniatura |
| Collegare un immobile a un messaggio WhatsApp | menu «⋯» → Collega immobile → cerca → salva | invariato (3 passi) ma con la ricerca che propone gli immobili del contatto |
| Capire gli abbinamenti di una richiesta | Acquirenti → scheda → tab Immobili compatibili e tab Abbinamenti; oppure voce Abbinamenti con Calcola | una sola tab «Immobili compatibili» con «Aggiorna» |
| Cambiare i dati dell'agenzia / il calendario / le automazioni | menu utente, Agenda, voce Automazioni | una pagina «Impostazioni» |

## 7. Piano di modifica per priorità (da approvare prima di toccare il codice)

**Priorità 1 — solo navigazione, nessuna logica né dato (basso rischio):**
- barra laterale a 8 voci con le voci rimosse convertite in tab/filtri delle pagine esistenti
  (`main.js` `SECTIONS`, rotte invariate e ancora raggiungibili per indirizzo: nessun collegamento
  salvato si rompe);
- rinomine: Clienti, Richieste, Valutazioni, WhatsApp;
- «Impostazioni» nel menu utente che raggruppa pagine già esistenti.

**Priorità 2 — pagine composte (medio):**
- Oggi + Attività in una pagina (stesse API);
- Acquisizioni con tab Venditori / Acquisizioni / Incarichi;
- Richieste con tab Abbinamenti; Immobili con tab Palazzine e pulsante Mappa.

**Priorità 3 — schede di dettaglio (medio):**
- contatto, immobile e acquirente a massimo 8 tab, «Cronologia» unica, azioni di stato nel menu «⋯»;
- Censimento e Lead dentro Panoramica.

**Priorità 4 — moduli in due passi (medio-alto, da collaudare):**
- Nuovo immobile, Nuova richiesta, dialog appuntamento con campi avanzati a scomparsa;
- «Nuova acquisizione» dalla scheda contatto e immobile.

**Priorità 5 — pulizia del linguaggio e dei dettagli tecnici (basso):**
- colonne e termini tecnici (Freschezza, Revisione, Readiness, Lead, codici di stato) solo nei
  dettagli o per amministratori.

Per ogni priorità: nessuna API nuova, nessuna cancellazione, permessi e automazioni invariati; ogni
rotta attuale resta valida. I test statici che nominano le voci della barra (`tests/test_*_ui.py`,
`test_blocco_4a` ecc.) vanno aggiornati con la stessa regola delle sentinelle: si nomina il cambiamento,
non si smette di controllare.
