# BLOCCO-4D — Piano di attivazione delle risposte automatiche

Stato al commit del 4D: il motore esiste **solo in simulazione**. Registra la
decisione che prenderebbe; non accoda messaggi, non chiama Meta, non crea
attività. È spento per impostazione predefinita.

Numero operativo: **+39 329 567 4589** (Phone ID `851689118034087`,
WABA `2573249773033415`). Oggi la sua callback Meta punta al servizio di
**produzione** (`main`): TEST non riceve messaggi reali. Questo piano non la
sposta: ogni passo che la tocca è una decisione esplicita del titolare.

## Fase 0 — prerequisiti (nessun effetto sui clienti)

1. Migrazioni su TEST, nell'ordine, con il runner `scripts/p26_migrate.py`
   (plan → apply → status), verificando hash e file DOWN:
   `118_whatsapp_inbound` (già applicata), `119_whatsapp_auto_reply_simulation`.
2. Deploy su TEST del commit con il 4D. Verificare separatamente codice
   deployato e stato del database (un deploy verde non prova le migrazioni).
3. Variabili su TEST: `WHATSAPP_INBOUND_ROUTES` già configurata per il numero;
   `WHATSAPP_AUTO_REPLY_MODE` **assente** (arresto generale attivo).

## Fase 1 — simulazione su TEST con dati sintetici

1. Impostare `WHATSAPP_AUTO_REPLY_MODE=simulate` solo su TEST.
2. Un titolare attiva la simulazione per l'agenzia (Messaggi WhatsApp →
   «Attiva simulazione per l'agenzia», oppure `PUT /api/auto-replies/settings`).
   Solo i messaggi ricevuti **dopo** l'attivazione vengono elaborati.
3. Inviare al webhook di TEST payload sintetici firmati (mai dati reali) e
   lanciare il giro: `POST /api/auto-replies/tick` con la sessione di un
   titolare o amministratore. Controllare in «Messaggi WhatsApp» le etichette
   «Simulazione — non inviato», testi, motivi, pausa e arresto generale.
4. Verificare che `communication_messages`, `communication_attempts` e `tasks`
   non cambino durante i giri.

## Fase 2 — simulazione sul traffico reale (decisione del titolare)

Richiede che i messaggi reali del numero arrivino al CRM con il 4A. Oggi
arrivano solo a produzione: servono **produzione con 118/119 e codice 4A–4D**,
oppure lo spostamento della callback — entrambe decisioni non prese.

1. Simulazione attiva per almeno 2 settimane; revisione settimanale delle
   decisioni (campione: risposte certe, chiarimenti, passaggi all'agente,
   falsi «ambiguo»).
2. Correzioni delle regole → nuova `RULES_VERSION` (`auto_replies/enums.py`):
   le decisioni restano una per (messaggio, versione).
3. Esecuzione del giro: aggiungere il passo `POST /api/auto-replies/tick` al
   runner esistente (`run_communication_dispatch_cron.py`, stessa sessione e
   stesso account) **solo dopo approvazione**; oggi non è collegato a nessun cron.

## Fase 3 — invio reale (NON implementato; serve un blocco dedicato)

Mancano, e vanno decisi e costruiti prima di qualsiasi invio:

1. Un modo d'invio: oggi il vincolo della 119 ammette solo `simulate`
   (serve una migrazione nuova) e `WHATSAPP_AUTO_REPLY_MODE` non ha un valore
   d'invio.
2. L'invio passa **dal percorso del 4B** (claim mirato, ricontrolli
   immediatamente prima dell'invio, esito incerto mai ritentato), come
   sistema dell'agenzia, con versione Graph esplicita e
   `WHATSAPP_OUTBOUND_MODE=meta` solo dove deciso.
3. Il passaggio all'agente deve creare l'attività reale (tabella `tasks`) con
   un assegnatario certo; solo allora i testi possono promettere un ricontatto
   (`followup_assigned`).
4. Un riferimento pubblico verificato dell'annuncio, se si vuole citarlo nei
   testi (oggi nessun codice interno compare).
5. Una verifica della disponibilità reale dell'immobile: `commercial_status`
   non basta e la disponibilità non viene mai affermata.
6. Revisione legale dei testi automatici e dell'informativa al cliente.

## Arresto e pausa

- Arresto generale immediato: togliere `WHATSAPP_AUTO_REPLY_MODE` (o
  qualunque valore diverso da `simulate`) e riavviare il servizio.
- Per agenzia: modo `off`.
- Per conversazione: «Metti in pausa»; il motore si ferma anche da solo per
  24 ore dopo una risposta manuale di un operatore (4B), dopo uno STOP e per
  i numeri nella lista di rinuncia di piattaforma.
