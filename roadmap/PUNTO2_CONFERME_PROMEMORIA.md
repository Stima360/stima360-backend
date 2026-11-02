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

- **Tre interruttori indipendenti**, nessuno ne implica un altro:
  1. promemoria **24h gia' esistenti**: si accendono dal cron (agenzie servite e token); nessuna delle
     variabili del Punto 2 li accende o li spegne;
  2. promemoria **2h**: `APPOINTMENT_REMINDER_OFFSETS` contiene `2h`;
  3. **conferme** al cliente: `APPOINTMENT_CONFIRMATIONS=1`. Governa **solo** la conferma e il
     «nuovo orario» accodati dall'Agenda; non tocca ne' il 24h ne' il 2h.
- **Destinatari: elenco esplicito, mai dedotto.** `APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS` e'
  letto solo dalla variabile, mai dagli indirizzi dei clienti, e vale sia all'accodamento sia alla
  revalida prima dell'invio.
  - **Conferme e 2h: fail-closed.** Variabile assente o vuota = nessun destinatario. `*` apre a tutti
    ed e' una scelta da fare a mano, mai un effetto dell'assenza di configurazione.
  - **24h di prima: invariato.** Assente = nessun limite; se presente, limita anche lui.
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
  `scripts/release_127_migration.py`, `scripts/collaudo_punto2.py`, `tests/test_punto2_conferme_unit.py`,
  `tests/test_punto2_conferme_postgres.py`, `tests/test_release_127_migration.py`, `tests/test_collaudo_punto2.py`,
  questo documento.
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
| `APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS` | l'email di prova autorizzata (**solo quella**, scritta a mano) | conferme e 2h: nessun destinatario; 24h: nessun limite |

Al termine del collaudo le tre variabili vanno **rimosse** (o riportate al valore precedente): senza di
esse conferme e 2h tornano spente e chiuse.

Gli invii email passano dal provider SMTP gia' configurato (`SMTP_USER` e `SMTP_PASS` obbligatori; `SMTP_HOST`
e `SMTP_PORT` hanno un default nel trasporto, quindi **possono mancare dall'ambiente**: il `preflight` del
collaudo chiede al trasporto stesso la configurazione effettiva, senza connettersi, senza inviare e senza
mostrare segreti; stampa host e porta effettivi e se vengono dalla variabile o dal default, e di utente e
password dice solo se sono presenti) e dal cron esistente (`run_communication_dispatch_cron.py` →
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

**Se l'`apply` e' gia' stato eseguito e segnalato verde, non ripeterlo.** Per controllare registro e
checksum basta il `verify`, che e' di **sola lettura** (sessione `read only`) e **deve** mostrare:

- `DB_NAME` = `stima360_db_test`, `DB_HOST` = host atteso, database primario, sessione in sola lettura;
- le migrazioni 115–126 con i checksum attesi e nessuna annullata, nessuna migrazione estranea;
- `127_appointment_confirmations` registrata con checksum up `2bf5b852…ed63fb` e down
  `36d9a7cd…bbd638`, down disponibile, non annullata, con l'operatore e l'ora dell'apply;
- il vincolo `communication_messages_reason_code_chk` che contiene `appointment_confirmation`;
- l'ultima riga `VERIFICA FINALE SUPERATA: 127 applicata e registrata`.

Se il job di verifica gia' lanciato ha stampato queste righe, il punto e' chiuso: l'output del job e'
la prova. Il codice del servizio deve essere il commit piu' recente di `core-0.1-test` (le conferme
senza elenco di destinatari sono chiuse solo da quel commit in poi): confrontare il commit del
deploy su Render con `git log -1 origin/core-0.1-test`.

## 6. Collaudo reale su TEST (2F) con dati sintetici: un solo comando

Strumento: `scripts/collaudo_punto2.py`, One-Off Job del servizio TEST (stesse variabili del servizio).
**Non spedisce nulla da solo**: scrive solo contatti e appuntamenti sintetici attraverso i service
dell'Agenda; la spedizione la fa il cron esistente, **solo verso `info@stima360.it`** (l'unico
destinatario, scritto nello script e non passato da riga di comando; l'elenco
`APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS` deve contenere solo quello). Non cambia l'orologio, il
cron o le regole di invio. Si ferma prima di connettersi se `DB_NAME` non e' `stima360_db_test` o
`DB_HOST` non e' l'host atteso; non stampa mai un indirizzo per intero.

```bash
python scripts/collaudo_punto2.py avvia --expected-host <HOST_DB_TEST_VERIFICATO>
```

Si lancia **a qualunque ora** (ora, alle 19, alle 20, di notte) e si puo' **rilanciare**: riparte da dove
e' arrivato, senza creare dati nuovi. Agenzia, attore (owner) e agente si ricavano dal servizio se non
sono ambigui (altrimenti si ferma ed elenca solo id e ruolo; si possono dare con `--agency-id`,
`--actor-user-id`, `--agent-user-id`). Cosa fa, in ordine:

1. controlla ambiente (solo TEST, 127 registrata, configurazione, SMTP effettivo, destinatario unico,
   agenzia servita dal cron) e dice cosa altro e' in coda per l'agenzia;
2. se non c'e' un collaudo aperto, crea i sei scenari sintetici;
3. aspetta fino a 20 minuti (`--attesa-minuti`) il cron per le prove che il cron chiude subito;
4. stampa il report con **PROVE CONCLUSE**, **PROVE IN ATTESA** (con l'ora da cui sono dovute e il
   **limite** oltre il quale sarebbero fallite) e **PROVE FALLITE**;
5. se tutte sono concluse e nessuna e' fallita, annulla da solo gli appuntamenti sintetici. Se qualcosa
   e' fallito lascia i dati per l'ispezione (poi `cleanup`).

Codice d'uscita: 0 senza fallimenti (anche con prove in attesa, che il report dice a chiare lettere), 1 con
almeno una prova fallita.

**Prove immediate, a qualunque ora**, perche' le conferme non dipendono dalla fascia oraria: conferma
unica (anche dopo doppio clic e «Conferma»), spostamento, annullamento, divieto di contatto, destinatario
fuori elenco. **Prove programmate** per la **prossima fascia utile** (08:00–20:00 di Roma): il promemoria
2h (scenario A) e il 24h (scenario B). La fascia e' «adesso» se e' aperta con almeno 30 minuti davanti;
altrimenti e' l'apertura successiva (alle 20:00 o dopo: domattina alle 08:00). Gli orari di A e B non
sono scelti a mano: si simula, con le funzioni **vere** della policy, un giro di cron ogni 5 minuti dalla
creazione dell'appuntamento, e il report valuta **esattamente i promemoria che la policy prevede** per
quell'appuntamento, ognuno con il suo limite. Cosi' un promemoria che manca oltre il limite e' un
fallimento, uno che e' solo in anticipo e' «in attesa», uno non previsto e' un fallimento.

| Scenario | Prove |
|---|---|
| A: conferma + promemoria **2h**, doppio clic e «Conferma» | una sola conferma; 2h inviato |
| B: conferma + promemoria **24h** | una sola conferma; 24h inviato |
| C: appuntamento fra 3 giorni, subito spostato di un'ora | conferma del vecchio orario **annullata e mai partita**; un solo «Nuovo orario» |
| D: appuntamento fra 4 giorni, subito annullato | conferma annullata, nessuna email |
| E: contatto con divieto di contatto | conferma soppressa (`do_not_contact`) |
| F: email fuori elenco | nessuna conferma accodata |

Nella casella autorizzata arrivano di norma **5 email** (A: conferma e 2h, B: conferma e 24h, C: «Nuovo
orario»); il report le elenca per oggetto. Se lanciato tra le 19:35 e le 19:59 la policy prevede per A
anche un 24h la sera stessa, e il report lo prevede di conseguenza. Con `--property-id <immobile con
incarico>` lo scenario A diventa una visita con **indirizzo** nel testo.

**Tre stati distinti**, mai confusi:

- **accodata**: riga `queued` nel registro comunicazioni;
- **accettata dal provider**: `sent` nel registro; vuol dire che il server SMTP ha accettato il
  messaggio, non che e' arrivato;
- **ricevuta**: non verificabile dal CRM (il provider non riporta la consegna); la conferma una persona
  guardando la casella.

Controlli sulla casella, a mano: data e ora **italiane**, indirizzo (con `--property-id`), riferimenti
dell'agenzia su righe separate (nome, telefono, email), lettura da telefono **senza scorrimento
orizzontale**.

Per usarlo a pezzi: `preflight` e `report` sono di sola lettura; `setup` crea; `cleanup` annulla gli
appuntamenti sintetici, toglie il divieto di contatto e prova a cestinare i contatti (quelli con storico
possono restare, marcati `collaudo_punto2`) ed e' ripetibile. Alla fine rimuovere le variabili
temporanee (§ 4).

## 7. Test eseguiti (locale, PostgreSQL 16 reale)

Vedi il resoconto della consegna per l'output esatto. In sintesi: unitari e PostgreSQL nuovi
(conferme, spostamento, annullamento, destinatari fail-closed, 2h, spegnimento, 127 assente, 127
up/down, divieto di contatto, doppio clic), suite A32-1/A32-2, suite della comunicazione, Agenda
A30-2, script di rilascio 127 e suite completa non-PostgreSQL confrontata con la base del branch.

Il collaudo con lo strumento (§ 6) e' stato **provato sulla replica locale del TEST** con un trasporto
finto: preflight, setup, un giro di cron, report (6 scenari coerenti, 5 messaggi accettati dal
trasporto finto, nessuno per D, E, F e il vecchio orario di C) e cleanup ripetuto. Non e' una prova
su TEST: quella resta da fare con il Job.

Email di conferma: rese a 360 e 320 px in un browser; orari italiani corretti in ora legale e
solare. Il test ha trovato un difetto, corretto: con un indirizzo email lungo la tabella sbordava
(449 px su 360); ora celle a capo, riferimenti su righe separate, telefono con spazi non separabili.

## 8. Fuori perimetro, da decidere

- Un canale WhatsApp per le notifiche automatiche: non esiste un provider nel dispatcher e il
  compito non lo chiede; richiederebbe template approvati da Meta e una decisione sul numero.
- Avvisi all'agente (non al cliente) e l'opzione per il cliente di confermare/spostare da un link.
