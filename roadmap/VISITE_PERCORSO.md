# Percorso della visita dal CRM — Prenotazione → Agenda → Esito → Foglio visita → Report proprietario

Branch: `core-0.1-test`. Nessuna nuova migrazione: bastano le tabelle esistenti
(`appointments`, `property_visits`, `visit_sheets`, `owner_visit_feedback_publications`).
Conferme e promemoria già collaudati (Punto 2) non sono stati modificati nella logica né nella configurazione.

## Fase A — Prenotazione collegata ad Agenda, immobile, contatto, agente

- **Esisteva:** la prenotazione dal link dell'immobile crea l'appuntamento, la riga `property_visits`,
  il contatto acquirente e assegna l'agente.
- **Mancava:** una prenotazione/spostamento dal link pubblico dell'immobile non passava dal gancio delle
  conferme automatiche (`appointment_reminders`), a differenza delle prenotazioni create in Agenda.
- **Implementato:** `appointments/property_visit_writes.py` richiama `_conferme.on_scheduled(...)` per le
  visite `scheduled`/`confirmed` (tipo `rescheduled` quando sostituisce un appuntamento). Il comportamento
  resta soggetto agli stessi flag e alla stessa allowlist (fail-closed) del Punto 2.
- **Test:** `tests/test_visite_percorso_postgres.py` (8).

## Fase B — Esito della visita dalla scheda appuntamento

- **Esisteva:** chiusura dell'appuntamento (effettuata / assente / annullata) e proiezione `property_visits`.
- **Mancava:** registrare interesse e feedback dall'Agenda senza ripetere i dati; mostrare la dicitura corretta
  per le visite.
- **Implementato:** `buyer_visits/visit_outcome.py` + `PUT /api/appointments/{id}/visit-outcome`; il dettaglio
  appuntamento include `visit`. Scrive solo `outcome` e `feedback` di `property_visits` (mai stato, data, rating).
  L'interesse si registra solo per visita effettuata; la visita **non si chiude mai da sola** col passare dell'orario.
  UI: sezione «Esito della visita» nella scheda, etichette dedicate alle visite, dialoghi di chiusura con
  interesse e feedback. L'esito è visibile nella scheda immobile e nello storico del contatto (dati già esistenti).
- **Scelta consapevole:** l'interesse è salvato come etichetta italiana in `property_visits.outcome`;
  le interazioni BUY non sono scritte (stati commerciale e operativo restano distinti).
- **Test:** `tests/test_visite_esito_postgres.py` (9).

## Fase C — Foglio visita PDF dall'appuntamento

- **Esisteva:** `visit_sheets` (versioni immutabili, PDF con SHA-256, archivio, richieste di firma simulate).
- **Mancava:** aprirlo/generarlo dalla scheda appuntamento; stato firma leggibile.
- **Implementato:** `GET /api/visit-sheets/appointments/{id}`, componente `mountAppointmentSheet`, template v2
  con dati agenzia, cliente, immobile, data, agente, spazio firma e stato **«Da firmare»**. Nessuna firma valida è
  mai simulata; la firma reale resta una fase separata.
- **Test:** `tests/test_visite_foglio_postgres.py` (7); sentinella 6A aggiornata alla versione 2 del template.

## Fase D — Aggiornamento al proprietario con approvazione dell'agente

- **Esisteva:** `owner_visit_feedback_publications` (bozza/pubblicato/archiviato, validatore privacy),
  report proprietario e portale.
- **Mancava:** preparare l'aggiornamento dall'appuntamento e distinguere le visite programmate / effettuate /
  annullate o non svolte nel report.
- **Implementato:** `owner_report/visit_updates.py`, rotte sotto `/api/owner-reports/visits`, componente
  `visit-owner-update.js`: proposta di testo senza nomi né recapiti, controllo privacy, bozza, anteprima,
  approvazione con casella di conferma esplicita, ritiro. Il report conta effettuate, annullate/non svolte (esclusi
  errori e spostamenti del cliente) e in programma; gli interessi sono contati solo sui riscontri **approvati**.
  Note interne, dati personali degli acquirenti e bozze non sono mai pubblicati. Alla pubblicazione parte solo la
  notifica in-app; nessun messaggio a clienti.
- **Test:** `tests/test_visite_proprietario_postgres.py` (10); sentinelle report/portale aggiornate.

## Fase E — Collaudo con dati di prova

- `tests/test_visite_collaudo_postgres.py` (catena completa su PostgreSQL usa e getta): prenotazione → Agenda →
  esito → scheda immobile/contatto → foglio PDF archiviato → bozza → approvazione → portale proprietario;
  nessun duplicato, nessun messaggio extra, isolamento tra agenzie, protezione dei dati personali.
- `tests/test_visite_browser_postgres.py` (Playwright/Chromium contro l'app reale): etichette, dialogo esito, foglio
  «Da firmare» con download del PDF, blocco privacy, anteprima, approvazione vincolata alla casella, nessun
  overflow a 390 px, zero errori in console.
- `tests/test_visite_ui_static.py`: nessun `innerHTML` con dati, nessun percorso API estraneo nei file Agenda,
  approvazione esplicita, testo «Da firmare», sintassi dei moduli.

## Cosa NON è stato fatto

- **Deploy su TEST e collaudo reale su dati CRM TEST:** da questa sessione l'ambiente TEST non è raggiungibile e
  non ci sono credenziali; il percorso **non va dichiarato completo** finché non si ripete la sequenza su TEST.
- Nessun invio reale a clienti e nessuna pubblicazione reale al proprietario.
- Firma elettronica reale del foglio visita (fase separata).

## Collaudo reale da eseguire su TEST (dopo il deploy)

1. Verificare che il deploy contenga il commit di questo branch (nessuna migrazione da applicare).
2. Prenotare una visita di prova dal link dell'immobile (contatto con recapito autorizzato) → comparsa in Agenda.
3. Dalla scheda appuntamento: «Visita effettuata», interesse e feedback → controllare scheda immobile e contatto.
4. «Genera foglio visita» → PDF con «Da firmare», presente nell'archivio.
5. «Aggiornamento al proprietario»: bozza → anteprima → approvazione → visibile nel portale di un proprietario di prova.
6. Controllare assenza di duplicati, isolamento tra agenzie e assenza di dati personali nel testo pubblicato.
