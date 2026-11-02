# ROADMAP PUNTO 2 — Conferme e promemoria automatici delle visite

Branch `core-0.1-test`. Nessuna modifica a produzione, a Stima Pro o all'invio delle stime.
Tutto cio' che e' nuovo nasce **spento**: senza le variabili del § 4 il CRM si comporta come prima.

## 1. Cosa esisteva gia' (verifica 2A)

| Funzione richiesta | Stato prima del Punto 2 |
|---|---|
| L'agente fissa una visita e il CRM registra appuntamento, immobile, cliente, agente | **c'era**: Agenda A30 (`appointments/`), stati `requested → scheduled → confirmed → completed / no_show / cancelled / rescheduled`; lo spostamento crea una riga nuova collegata (`rescheduled_from_id`) e la vecchia diventa `rescheduled`; prenotazione pubblica (`/visita/`, `public_booking/`). |
| Il cliente riceve una conferma con giorno, ora, indirizzo e riferimenti | **mancava**: nessun messaggio di conferma al cliente. |
| Promemoria automatici configurabili (es. 24h e 2h) | **c'era solo il 24h**, non configurabile: `appointment_reminders/` (A32), canale email, pianificato dal cron, rivalidato prima dell'invio. Nessun 2h. |
| Se spostato: aggiornare Agenda e promemoria, mai informazioni vecchie | **a meta'**: la riga vecchia cambiava stato e il promemoria in coda veniva fermato **solo** dal dispatcher al momento dell'invio (revalida). Nessuna conferma del nuovo orario. |
| Se annullato: annullare i promemoria in attesa | **a meta'**: come sopra, soppressione pigra al momento dell'invio; la coda restava `queued`. |
| Evitare duplicati, registrare lo storico | **c'era** per il promemoria: chiave di occorrenza `appointment_reminder:v1:<id>:<offset>:<epoch>` sul registro comunicazioni (`communication_messages`), storico nella timeline del contatto. |
| Il consulente vede confermato / da confermare / spostato / annullato | **c'era**: l'Agenda etichetta Fissato, Confermato, Spostato, Annullato (`static/os_shell/assets/agenda/agenda-model.js`); gli stati non sono cambiati. |
| Canale WhatsApp automatico | **non esiste** un provider WhatsApp nel dispatcher: le comunicazioni automatiche sono email. Le risposte WhatsApp manuali restano limitate da `WHATSAPP_OUTBOUND_ALLOWED_RECIPIENTS`, che **non e' stata ampliata**. |

## 2. Cosa e' stato implementato

### 2B — Conferma al cliente (`appointment_reminders/confirmation.py`)

- Quando un appuntamento nasce gia' fissato o confermato (creazione dall'Agenda), quando una
  richiesta viene fissata («Fissa»), quando una prenotazione pubblica viene confermata, e quando un
  appuntamento viene spostato, il service dell'Agenda (`appointments/service.py`) chiama
  l'aggancio **dentro la stessa transazione**, in un `SAVEPOINT`: un errore della conferma non rompe
  mai l'appuntamento (viene annullato fino al savepoint e registrato nel log senza dati personali).
- La conferma e' una riga del registro comunicazioni esistente (`communication.service.enqueue`):
  canale email, tipo servizio, motivo nuovo `appointment_confirmation`, template
  `appointment_confirmation` v1. Contiene: tipo di appuntamento, giorno, ora (Europe/Rome),
  indirizzo strutturato (solo per la visita acquirente, mai il testo libero del luogo), riferimenti
  dell'agenzia (nome visualizzato, telefono, email del profilo agenzia; in mancanza il nome
  dell'agenzia). Nessun id interno, nessun agente, nessuna nota.
- Chiave di idempotenza `appointment_confirmation:v1:<appointment_id>:<start_epoch>`: «Fissa» e poi
  «Conferma» producono **una** conferma; uno spostamento crea una riga nuova dell'Agenda e quindi
  una conferma nuova con oggetto «Nuovo orario: …» e la frase «sostituisce il precedente».
- Il dispatcher, prima del provider, rilegge l'appuntamento (`appointment_reminders/revalidation.py`):
  se intanto e' annullato, spostato, cambiato d'orario, se il contatto/email sono cambiati, se e'
  fuori allowlist o se l'appuntamento e' gia' cominciato, la conferma viene soppressa con il motivo.

### 2C — Promemoria configurabili (`policy.py`, `planner.py`, `template.py`, `settings.py`)

- Offset nella lista chiusa `24h`, `2h`. Variabile `APPOINTMENT_REMINDER_OFFSETS` (es. `24h,2h`);
  assente = solo `24h`, come prima.
- Il 2h: inviato 2 ore prima se l'istante cade nella finestra 08:00–20:00 (altrimenti
  `outside_send_window`, nessuno spostamento), con almeno 1 ora di anticipo; non ha la soglia di
  prenotazione a 12h del 24h. Template `appointment_reminder_2h` («tra circa due ore»); chiave
  `appointment_reminder:v1:<id>:2h:<epoch>`. Il 24h non cambia.
- `settings.py` e' l'unico lettore dell'ambiente del package: `policy` e `template` restano puri.

### 2D — Spostamento e annullamento

- `on_closed` annulla **subito** (`queued → cancelled`), nella stessa transazione dell'Agenda, i
  promemoria e le conferme in coda dell'appuntamento spostato o annullato
  (`communication.service.cancel_queued_for_appointment`, filtro per agenzia, appuntamento e motivo).
  Un messaggio gia' preso in carico dal dispatcher non viene toccato: lo chiude la revalida.
- La vecchia soppressione pigra resta come seconda rete.

### 2E — Sicurezza, consensi, duplicati

- Conferme e 2h sono **spenti** senza `APPOINTMENT_CONFIRMATIONS=1` / `APPOINTMENT_REMINDER_OFFSETS`.
- `APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS` (elenco di email) limita **tutte** le notifiche degli
  appuntamenti (conferme, 24h, 2h) ai soli destinatari elencati, sia all'accodamento sia alla
  revalida: e' il limite del collaudo. Assente = nessun limite (comportamento di sempre del 24h).
- Comunicazioni di **servizio**: come il promemoria, non interrogano il consenso marketing; il
  divieto di contatto e la revalida restano quelli del dispatcher. Nessun invio reale a tutti i
  clienti per effetto di un deploy.
- Nessun dato personale nei metadata ne' nei log; nessun segreto nel repository.

### Migrazione 127 (`migrations/127_appointment_confirmations.sql` + `_down.sql`)

Additiva: aggiunge il valore `appointment_confirmation` al CHECK dei motivi di
`communication_messages`. Nessuna riga letta o riscritta. Il down rifiuta se esistono conferme.
Finche' la 127 non e' applicata, il CRM lo rileva (`reason_code_supported`) e **non accoda** la
conferma: l'appuntamento si salva lo stesso.

### UI

Punto 7 del compito soddisfatto dall'Agenda esistente (Fissato / Confermato / Spostato /
Annullato). La timeline del contatto etichetta il nuovo motivo come «Conferma appuntamento».
Nessuna modifica JavaScript.

## 3. File toccati

- Nuovi: `appointment_reminders/confirmation.py`, `appointment_reminders/settings.py`,
  `migrations/127_appointment_confirmations.sql`, `migrations/127_appointment_confirmations_down.sql`,
  `scripts/release_127_migration.py`, `tests/test_punto2_conferme_unit.py`,
  `tests/test_punto2_conferme_postgres.py`, `tests/test_release_127_migration.py`, questo documento.
- Modificati: `appointment_reminders/{policy,template,planner,revalidation,repository}.py`,
  `appointments/service.py` (5 agganci), `communication/{enums,repository,service,dispatcher,contact_view}.py`,
  sentinelle di inventario (`tests/migration_inventory.py`, `tests/test_a32_*`, `tests/test_a30_1_appointments.py`,
  `tests/test_p29_2_2_communication_service.py`, `tests/test_p29_3_journey_foundation.py`,
  `tests/test_blocco_2c_integrated_postgres.py`, `tests/test_release_126_migration.py`), ognuna con
  una nota «SENTINELLA AGGIORNATA DAL PUNTO 2» che nomina il cambiamento.
- Riparazione di fixture: `tests/test_a30_13b_create_permissions_postgres.py` riapplicava la 076
  gia' applicata dal `db` di A30-2, e mandava in ERROR tutte le suite PostgreSQL costruite su
  `agenda_completa` (A30-13B, A31-*, A32-*, CRM-OPS-3/4). Ora la salta se la tabella esiste.

## 4. Variabili d'ambiente (servizio TEST)

| Variabile | Valore per il collaudo | Senza |
|---|---|---|
| `APPOINTMENT_CONFIRMATIONS` | `1` | conferme spente |
| `APPOINTMENT_REMINDER_OFFSETS` | `24h,2h` | solo `24h` |
| `APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS` | l'email del destinatario autorizzato (solo quella) | nessun limite: **non** usare sul TEST con contatti reali |

Gli invii email passano dal provider SMTP gia' configurato (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
`SMTP_PASS`) e dal cron esistente (`run_communication_dispatch_cron.py` →
`POST /api/internal/communication/cron/run`: giro dei promemoria + dispatch email).

## 5. Deploy e migrazione sul TEST

1. Deploy del branch `core-0.1-test` sul servizio TEST (Render; da questa sessione Render non e'
   raggiungibile, quindi **non verificato**).
2. Migrazione 127 con un One-Off Job, un comando per volta, passando al successivo solo con
   esito 0 (`<HOST_DB_TEST_VERIFICATO>` e' l'host del database TEST letto dalle variabili del
   servizio, mai incollato nel repository; lo script si ferma prima di connettersi se `DB_NAME`
   non e' `stima360_db_test` o l'host non e' quello atteso):

```bash
python scripts/release_127_migration.py check --expected-host <HOST_DB_TEST_VERIFICATO>
```

```bash
python scripts/release_127_migration.py apply --expected-host <HOST_DB_TEST_VERIFICATO>
```

```bash
python scripts/release_127_migration.py verify --expected-host <HOST_DB_TEST_VERIFICATO>
```

   Provato sulla replica locale del TEST (119–126 applicate): `check` → «pendenti esattamente:
   127», `apply` → applicata e registrata, `verify` → «VERIFICA FINALE SUPERATA».
3. Impostare le variabili del § 4 e riavviare il servizio.

## 6. Collaudo reale (2F) con il destinatario autorizzato

Prerequisiti: punto 5 completato; un contatto di prova con l'email autorizzata.

1. Dall'Agenda, fissare un appuntamento per quel contatto a 2 giorni di distanza (ore 10:00).
   Atteso: nella timeline del contatto compare «Conferma appuntamento» in coda; al giro del cron
   arriva l'email «Conferma: … alle 10:00» con riferimenti dell'agenzia.
2. Premere «Conferma» sullo stesso appuntamento. Atteso: nessuna seconda email.
3. Spostarlo al giorno dopo alle 11:00. Atteso: la conferma/promemoria vecchi passano a
   `cancelled`; arriva «Nuovo orario: … alle 11:00»; nessuna email con il vecchio orario.
4. Il giorno prima alle 10:00 (giro del cron entro le 20:00): arriva il promemoria 24h; il giorno
   stesso alle 09:00: arriva il promemoria 2h («tra circa due ore»).
5. Annullare un altro appuntamento con messaggi in coda. Atteso: righe `cancelled`, nessuna email.
6. Un appuntamento per un contatto con email **non** in allowlist: nessun messaggio
   (`recipient_not_allowed` nei conteggi del giro).

Ogni passo si verifica nella timeline del contatto (stato `queued` / `sent` / `cancelled` /
`suppressed` con motivo) e nella casella del destinatario.

## 7. Test eseguiti (locale, PostgreSQL 16 reale)

Vedi il resoconto della consegna per l'output esatto. In sintesi: 27 unitari nuovi, 21 PostgreSQL
nuovi (conferme, spostamento, annullamento, allowlist, 2h, spegnimento, 127 assente, 127 up/down),
suite A32-1/A32-2 (36), suite della comunicazione (178 + 274), Agenda A30-2 (31), script di
rilascio 127 (3) e la suite completa non-PostgreSQL confrontata con la base del branch.

## 8. Fuori perimetro, da decidere

- Un canale WhatsApp per le notifiche automatiche: non esiste un provider nel dispatcher e il
  compito non lo chiede; richiederebbe template approvati da Meta e una decisione sul numero.
- Avvisi all'agente (non al cliente) e l'opzione per il cliente di confermare/spostare da un link.
