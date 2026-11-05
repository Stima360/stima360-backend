# AGENDA MOBILE — quattro viste vere su ogni dispositivo

Branch `core-0.1-test`. Solo frontend della OS Shell: nessuna modifica a backend, API, database o
migrazioni. Prenotazione, assegnazione, conferme, promemoria, fogli visita, firma OTP e Google
Calendar non sono stati toccati.

## Cause corrette (dall'audit)

1. **La forzatura voluta** — la regola G3 del piano A30-4 («su smartphone solo Lista e Giorno»)
   viveva in `agenda/agenda-model.js` (`MOBILE_VIEWS = ['list', 'day']`, `effectiveView`): sotto i
   767 px Settimana e Mese diventavano Lista. **Revocata**: `MOBILE_VIEWS = VIEWS` ed
   `effectiveView` non sostituisce mai una vista valida.
2. **Il conflitto CSS** — `app.css` nascondeva i pulsanti con `.agenda-desktop-only { display:
   none }`, ma `aurora.css` (caricato dopo) li rimetteva in vista con `.btn { display: inline-flex
   }`, stessa specificità. **Rimossa** la classe; le regole che nascondono qualcosa dell'Agenda
   hanno ora almeno due classi (`.agenda-page …`) e un test legge la cascata vera del CRM.

## Cosa fa ora

| Vista | Smartphone (disposizione compatta) | Tablet e desktop |
|---|---|---|
| Mese | Griglia lun–dom, oggi cerchiato, puntini per stato (visita immobile = quadratino verde acqua, sempre per prima) e numero; tocco su una data = elenco del giorno sotto la griglia; tocco su un appuntamento = scheda; «Apri giorno», «+ Nuovo» | Righe di testo nelle celle e «+N altri»; stesso elenco del giorno (a destra da 1024 px) |
| Settimana | Sette colonne strette lun–dom, blocchi colorati con l'ora d'inizio, al massimo 2 affiancati poi «+N»; intestazione del giorno = vista Giorno | Sette colonne intere senza scorrimento orizzontale (corretto il difetto a 768 px) |
| Giorno | Timeline, linea dell'ora corrente, al massimo 3 affiancati poi «+N», tocco su ora vuota = nuovo appuntamento | Come prima, con la linea dell'ora |
| Lista | Agenda cronologica dalla data scelta (14 giorni), giorni vuoti in una riga, «Mostra altri 14 giorni» | Uguale |

Comuni a tutte le viste:

- **Mezzanotte**: «ieri 23:30–02:00» e «22:00–domani 01:00»; ordine giusto dentro il giorno.
- **Stati predefiniti** uguali ovunque (senza annullati e spostati; specchio di
  `DEFAULT_CALENDAR_STATUSES`). La Lista li chiede esplicitamente all'API lista.
- **Filtro Agente** in tutte le viste (nella Lista lo applica il client sulle righe).
- **Swipe** orizzontale = periodo precedente/successivo; uno scorrimento verticale non è mai uno
  swipe; ascoltatori passivi.
- **Ultima vista** ricordata nel browser (`agenda/agenda-prefs.js`: solo il nome della vista, unico
  modulo della Shell che usa uno storage del browser).

Disposizione compatta (`COMPACT_QUERY`: larghezza ≤ 767 px, oppure altezza ≤ 500 px con puntatore
touch, cioè il telefono in orizzontale): testata fissa con periodo, ◀ Oggi ▶, «Filtri (n)» e «⋯»;
selettore a segmenti delle quattro viste; comandi secondari (Aggiorna, Disponibilità, Link
prenotazione, Aggiorna richieste dal sito, Google Calendar, + Nuovo appuntamento) nel menu «⋯»;
filtri e legenda dietro «Filtri»; «+» flottante; bersagli da 44 px; nessuno scorrimento
orizzontale della pagina.

## Settimana leggibile

Cosa restituisce davvero `GET /api/appointments/calendar` (verificato sul banco locale): tipo, stato,
inizio e fine, nome del cliente (`contact_name`), agente (`agent_name`) e `place`, cioè il luogo
scritto sull'appuntamento o, se manca, il **comune** dell'immobile. Titolo e indirizzo
dell'immobile NON sono nella risposta: nelle schede compare `place` e nient'altro (nessun dato
inventato); l'immobile completo resta nel dettaglio. Per un agente gli impegni dei colleghi
arrivano solo come «Occupato», agente e orario.

- **Blocchi** (settimana stretta): due righe da 11 px, ora d'inizio e tipo abbreviato («Visita»,
  «Sopr.», «Propr.», «PdA», «Rogito»…; massimo 6 caratteri, una sigla per ogni tipo del backend).
  Chi inizia in un giorno precedente mostra «ieri» o la data (bordo superiore tratteggiato).
  Appuntamenti sovrapposti = UN blocco «10:00 · 4 app.» che li elenca tutti: niente testi
  sovrapposti.
- **Colori**: lo sfondo è la famiglia del tipo (visite, sopralluoghi e valutazioni, proprietari e
  incarichi, proposte e atti, telefonate e video, altro), il bordo è lo stato. Contrasto testo/sfondo
  almeno 6:1; il tipo è sempre anche scritto. Chiave dei colori sotto la griglia.
- **Giorno scelto**: intestazione piena e colonna velata; si cambia toccando l'intestazione, un
  blocco o un'ora vuota. Nessuna nuova richiesta e nessun cambio di settimana: cambia solo il
  riepilogo (l'indirizzo si aggiorna senza ricaricare).
- **Riepilogo del giorno** sotto la griglia, su ogni dispositivo: orario, stato, tipo (nel colore
  della famiglia), cliente, luogo, agente, durata; il tocco apre il dettaglio esistente.

## Verifica

- `tests/test_agenda_mobile_browser_postgres.py`: Chromium con tocchi simulati a 375, 390, 430,
  768, 1280 px e 932×430, PostgreSQL usa-e-getta, dati sintetici. Le 14 verifiche richieste.
- `test_settimana_leggibile_blocchi_giorno_scelto_e_riepilogo` (stesso file): 375, 390, 430, 768 e
  1280 px; testi mai tagliati né sovrapposti e mai sotto 11 px, una sola richiesta del calendario
  anche cambiando giorno, riepilogo con i dati veri, apertura del dettaglio.
- `tests/test_agenda_mobile_ui.py`: funzioni pure in quattro fusi del dispositivo (ora legale e
  solare, cambio di mese e di anno), preferenza della vista, cascata CSS reale.
- Sentinelle che fissavano la regola G3 aggiornate con il commento «SENTINELLA AGGIORNATA (AGENDA
  MOBILE)».

**Non verificato da qui**: Safari/WebKit reale (WebKit non installato nell'ambiente di prova) e il
deploy su Render TEST (host non raggiungibile dalla sessione).

## Controllo manuale su iPhone (Render TEST)

1. Agenda → «Mese»: si vede la griglia del mese; i giorni con appuntamenti hanno i puntini.
2. Tocco su un giorno con appuntamenti: sotto la griglia compare l'elenco; tocco su un
   appuntamento: si apre la scheda.
3. Swipe a sinistra sul mese: mese successivo; «Oggi»: torna al mese corrente.
4. «Settimana»: sette colonne lun–dom; tocco su un blocco: scheda; swipe: settimana successiva;
   scorrendo in verticale le ore la settimana non cambia.
5. «Giorno»: linea rossa dell'ora corrente; tocco su un'ora vuota: «Nuovo appuntamento».
6. «Lista»: elenco per giorno; «Mostra altri 14 giorni» in fondo.
7. «Filtri»: scegliere un agente; passare alle altre viste: il filtro resta applicato.
8. «⋯»: Disponibilità, Link prenotazione e Google Calendar si aprono.
9. Uscire dall'Agenda e rientrare: si riapre l'ultima vista scelta.
10. Ruotare il telefono in orizzontale: la vista resta la stessa e la pagina non scorre di lato.
