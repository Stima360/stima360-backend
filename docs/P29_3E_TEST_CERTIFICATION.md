# P29-3E — Certificazione su TEST della sequenza stima

**Questa procedura NON e' stata eseguita.** E' scritta perche' la esegua una
persona, un passo per volta, su TEST, guardando cosa succede fra un passo e il
successivo. Non esiste uno script che la lanci, ed e' voluto: il senso di una
certificazione e' che qualcuno guardi.

## Prima di cominciare

- Ambiente: **TEST**. Nessun dato di PROD, nessun indirizzo di una persona
  vera. Il contatto di prova deve avere una casella che si puo' aprire.
- Migration **070** e **071**: applicate. Se `journeys/tick` risponde `503`
  con `feature_not_migrated`, la 071 non c'e' e il resto non ha senso.
- Account: un `agency_admin` dell'agenzia di prova. Un `agent` prende `403`
  su provision, activate e tick, ed e' corretto.
- Prima di partire, annotare l'ora: serve per leggere `scheduled_at`.

Tutte le chiamate passano dalla sessione (login con cookie). Nessun
`agency_id` nel corpo, mai.

## I quindici passi

**1. Provision.** `POST /api/communication/journeys/stima-lead/provision`.
Attesa: `201`-like con `{"created": true, "journey": {...}}`, `status:
"draft"`, cinque passi. Rifarla subito: `{"created": false}` e la stessa
journey, senza scritture. Se `created` torna `true` due volte, fermarsi.

**2. Verificare la bozza.** Leggere la journey: `status = draft`, `version =
1`, `send_timezone = Europe/Rome`, finestra lun-sab 09:00-19:00, passi M1..M5
con ritardi 1/4/7/14/30 giorni e **solo M3** `assisted`. In bozza non deve
esistere nessuna iscrizione: controllare che `communication_enrollments` sia
vuota per quella journey.

**3. Activate.** `POST /api/communication/journeys/{id}/activate`. Da questo
istante in avanti, mai indietro: annotare l'ora esatta della risposta. Le
stime spedite **prima** non devono produrre iscrizioni, ed e' il passo 5 a
verificarlo.

**4. Una stima di prova.** Creare o usare una stima TEST controllata, su un
contatto con consenso marketing **concesso** e una casella leggibile. Meglio
un contatto creato per questa prova: cosi' lo storico e' pulito e ogni riga
che compare e' una riga che abbiamo causato noi.

**5. Verificare `stima_pdf` spedita.** Nel ledger deve esserci una riga
`reason_code = stima_pdf`, `status = sent`, con `sent_at` **successivo**
all'ora del passo 3. Verificare anche l'altro verso: una stima spedita
**prima** dell'attivazione non deve produrre niente al primo tick. Se ne
esiste una vecchia sul database di TEST, e' il controllo gratis del cutoff.

**6. Tick.** `POST /api/communication/journeys/tick`. Attesa: `enrolled_active
= 1` per la stima nuova, `queued = 0` — M1 e' dovuta **un giorno dopo**
`sent_at`, non subito. Rifare il tick: tutti zero. Due tick di fila che
iscrivono due volte sono un guasto.

**7. M1.** Per non aspettare un giorno: portare indietro il `sent_at` della
riga `stima_pdf` di 25 ore (solo su TEST, e annotandolo). Tick. Attesa:
`queued = 1`, e nel ledger una riga `reason_code = m1`, `status = queued`,
`mode = automatic`, con `enrollment_id`, `step_no = 1`, `run_no = 1`.
Controllare `scheduled_at`: deve cadere **dentro** la finestra lun-sab
09:00-19:00 ora di Roma. Se l'ora attuale e' fuori finestra — di notte, di
domenica — `scheduled_at` deve essere il primo istante utile dopo, ed e' il
momento migliore per verificarlo davvero.

**8. Dispatch.** `POST /api/communication/dispatch` con `{"channel": "email",
"limit": 10}` — oppure, meglio, il passo 15, che fa tick e dispatch insieme.
Se `scheduled_at` e' nel futuro il messaggio **non** parte, ed e' corretto:
per vederlo partire, portare `scheduled_at` a adesso (o rifare il passo con
un orario dentro la finestra). Attesa: `sent = 1`, la mail arriva, il testo e'
quello di M1, con il link della stima e il link di disiscrizione.

**9. Timeline e provenienza.** Sulla riga spedita: `enrollment_id`, `step_no`,
`run_no` valorizzati e coerenti; `template_key = stima_lead_m1`,
`template_version = 1`. Sull'iscrizione: `next_step_no = 2`, `next_action_at`
a quattro giorni dal `sent_at` di M1 — **non** dalla nascita dell'iscrizione.

**10. Pause e resume.** Dal Contact 360, mettere in pausa le automazioni del
contatto. Tick: nessun passo nuovo per quel contatto. Mandare un messaggio
manuale: **deve** partire — la pausa ferma cio' che parte da solo, non le
persone. Riprendere, e verificare che il tick torni a lavorare. Poi la pausa
dell'**iscrizione** (che e' un'altra cosa): pausa, tick a vuoto, resume.

**11. M3 assistito.** Portare l'iscrizione fino a M3 (anticipando i `sent_at`
come al passo 7). Attesa: `awaiting_operator = 1`, nessun messaggio creato,
l'iscrizione ferma. Far girare il **cron** con il tick dentro: M3 **non deve
partire**. Poi, dal Contact 360, "Approva e invia": il messaggio nasce con
`mode = assisted` e il testo di M3. Provare anche "Salta": l'iscrizione
avanza a M4 senza che sia partito niente.

**12. Una condizione di stop.** Sceglierne una e provocarla davvero — firmare
un incarico sul lead, o registrare un sopralluogo, o revocare il consenso
marketing. Tick. Attesa: `stopped = 1`, l'iscrizione chiusa con la ragione
giusta, e **nessun messaggio ancora in coda** per quel contatto (un passo
gia' `queued` e non ancora partito deve risultare annullato). Verificare che
la ragione registrata sia quella vera e non una qualsiasi: con due condizioni
insieme vince quella di priorita' piu' alta.

**13. Contact 360.** Aprire la scheda del contatto, tab **Comunicazioni**.
Deve mostrare: lo stato delle automazioni, la card della journey con il passo
corrente, lo storico cronologico (stima + M1 + eventuali manuali), la nota
sull'inbound. Aprire gli strumenti di sviluppo e guardare la risposta delle
API: **non** devono comparire `claim_token`, `idempotency_key`,
`attempt_count`, `last_error`, il provider, il destinatario.

**14. Unsubscribe.** Aprire il link di disiscrizione dalla mail ricevuta,
davvero, da un browser. Attesa: la pagina pubblica funziona senza sessione, il
consenso risulta revocato, e al tick successivo l'iscrizione si ferma con
`consent_revoked`. Un secondo clic sullo stesso link non deve rompere niente.

**15. Il cron vero.** Far girare `run_communication_dispatch_cron.py` con le
env di TEST. Attesa: due righe di log — `status=... phase=journey_tick ...` e
`status=completed channel=email ...` — in **quest'ordine**, ed exit `0`.
Farlo girare due volte di fila: nessun messaggio duplicato, nessuna iscrizione
duplicata. Poi la prova che conta davvero: rompere il tick (per esempio
togliendo temporaneamente il permesso all'account, che da' `403`) e verificare
che il dispatch **parta lo stesso** e che il giro esca `2`, non `1`.

## Cosa fermerebbe la certificazione

- `created: true` due volte dal provision.
- Un'iscrizione nata da una stima spedita prima dell'attivazione.
- M1 spedita senza consenso, o senza link di disiscrizione.
- M3 partita da sola.
- Un messaggio duplicato fra due giri di cron.
- Un campo tecnico visibile nel Contact 360.
- Il dispatch che si ferma perche' il tick e' fallito.

## Dopo

La sequenza resta **attiva** su TEST. Prima di ripetere la certificazione da
capo, ritirare la journey (`retire`) invece di cancellare righe: il ritiro non
iscrive piu' nessuno e lascia correre le iscrizioni aperte, che e' esattamente
lo stato che si vuole osservare.

Niente di tutto questo tocca PROD, e nessun passo qui descritto va eseguito
su PROD.
