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

---

## Collaudo reale su TEST — `scripts/collaudo_visite.py`

Una sola visita sintetica, dall'inizio alla fine, sul database TEST, come **Render One-Off Job** del servizio TEST
(stesse variabili d'ambiente del servizio). Importa l'applicazione vera e la pilota in-process: stesse rotte, stessi
service, stesso database; non apre porte, non chiama il cron, non invia nulla.

```bash
python scripts/collaudo_visite.py avvia --expected-host <HOST_DB_TEST_VERIFICATO>
```

Prima si può lanciare la sola lettura: `python scripts/collaudo_visite.py preflight --expected-host <HOST>`.
Se l'agenzia, l'attore o l'agente non si ricavano in modo univoco, lo script si ferma e chiede
`--agency-id`, `--actor-user-id`, `--agent-user-id`.

**Guardie (si ferma prima di toccare il database):** `DB_NAME` = `stima360_db_test`; `DB_HOST` = host indicato a mano;
primario e non in recovery; nessuna variabile d'ambiente dichiara «production»; l'elenco
`APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS` non è `*` e non contiene indirizzi sintetici;
`PUBLIC_BOOKING_IP_PEPPER` presente; agenzia/attore/agente attivi; l'agente ha un orario di lavoro; tabelle
della catena presenti. Non crea utenti, orari di lavoro, migrazioni.

**Cosa scrive (tutto marcato `COLLAUDO VISITE <run>` / `COLLAUDO-VIS-<run>`, indirizzo `@example.invalid`):** immobile
con incarico dal percorso ordinario (acquisizione → sopralluogo → incarico, nessun trigger disattivato), venditore,
collegamento di prenotazione, acquirente (nato dalla prenotazione, con un numero sintetico verificato libero), visita,
foglio visita, aggiornamento al proprietario, proprietario del portale sintetico (contatto, account, accesso, sessione
di 2 ore). **Due soli ritocchi diretti**, su righe appena create e verificate dal codice immobile: l'orario del sopralluogo
e quello della visita vengono spostati nel passato (l'Agenda completa solo ciò che è iniziato).

**Cosa verifica (PASS/FAIL per ogni controllo, report per passo):**

| Passo | Controlli |
|---|---|
| 1 Prenotazione → Agenda | orari liberi; doppio clic = una prenotazione; un solo appuntamento «buyer_visit/scheduled/booking_link»; immobile e agente giusti; cliente = contatto sintetico (nessun contatto reale agganciato); `property_visits` collegata; scheda e elenco Agenda; nessun messaggio al cliente |
| 2 Esito | passato l'orario la visita non si chiude da sola; esito rifiutato prima della chiusura; «Visita effettuata»; interesse e feedback; `property_visits` e scheda Agenda |
| 3 Schede | scheda immobile e scheda contatto mostrano la stessa visita e lo stesso esito |
| 4 Foglio PDF | generazione; «Da firmare», nessuna firma simulata; PDF vero con SHA-256 uguale all'archiviato; testo del PDF (cliente, immobile, agenzia, agente, stato, firma) senza dati riservati; nessun duplicato; archiviazione `visit_sheets` collegata ad appuntamento e immobile |
| 5 Proprietario | proposta di testo; bozza invisibile al portale; niente pubblicazione senza approvazione esplicita; approvazione; testo nel portale del proprietario sintetico; report del portale e relativo PDF (testo letto dal PDF) |
| 6 Privacy e controlli | nessun dato dell'acquirente/nota interna/prezzo minimo al proprietario; testo con telefono bloccato; altro agente e altra agenzia: 404/403 su 8 operazioni ciascuno; portale: altro immobile e accesso anonimo neutri; una sola riga per fatto; nessun messaggio al cliente; **ritiro** della pubblicazione: sparisce dal portale e dal report, resta lo storico |

Il testo dei PDF si legge con `pdftotext` se c'è, altrimenti con un estrattore interno (font standard e TrueType incorporati
con mappa ToUnicode), confrontato con poppler sui PDF reali nei test.

**Pulizia (automatica a fine collaudo; rilanciabile con `cleanup [--run-id <RUN>]`):** sessione e accesso del proprietario
sintetico chiusi, collegamento disattivato, immobile archiviato. Le righe restano come storico, marcate.

**Da sapere:** se l'agente ha un Google Calendar collegato, il cron esistente di sincronizzazione può riportarvi la visita
sintetica come evento senza invitati (`sendUpdates=none`, nessun partecipante); non è rimovibile da qui.

**Prova generale:** `tests/test_collaudo_visite_script_postgres.py` (7 test) esegue lo stesso script contro un PostgreSQL
usa e getta, anche senza `pdftotext`, e le sue guardie (host diverso, ambiente «production», destinatari `*`).
