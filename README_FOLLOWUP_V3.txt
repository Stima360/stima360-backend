STIMA360 - FOLLOW-UP EMAIL PROPRIETARI V3 (16 email in 180 giorni)
===================================================================

Guida operativa. Nessuna di queste operazioni parte da sola: cron, variabili e
attivazione si impostano a mano su Render, dopo l'autorizzazione del titolare.


1. COSA FA IL SISTEMA
---------------------
- Sequenza `stima_lead` versione 3, «Follow-up proprietari 180 giorni»,
  16 email ai giorni 4, 8, 12, 18, 25, 33, 42, 52, 63, 75, 88, 102, 117, 135,
  157, 180. Testi approvati: communication/followup_v3_texts.py.
- Entra SOLO chi e' proprietario/venditore verificato (ruolo del contatto o
  immobile collegato come owner/seller) E ha un consenso Marketing Email
  valido. Mai acquirenti, mai chi ha revocato.
- Una sola sequenza aperta per persona/indirizzo in tutta la piattaforma.
  Finita al giorno 180 non riparte.
- Prima di ogni invio il dispatcher ricontrolla il consenso; disiscrizione
  in fondo a ogni email (link + intestazione List-Unsubscribe).
- Pausa automatica con appuntamenti, incarichi, trattative; immobile venduto:
  pausa 60 giorni. Le email saltate non vengono recuperate.
- Finestra d'invio: lunedi'-sabato, 9-19 ora di Roma. Due email mai a meno
  di 48 ore. Al massimo 20 nuove iscrizioni al giorno per agenzia.
- Email 6: link di download della guida PDF
  (/api/public/communication/guida-vendere-casa-2026.pdf), servita solo se
  il file e' quello approvato; altrimenti l'email 6 non parte.
- Email 15: le risposte A/B/C/D arrivano nella casella Reply-To; all'invio
  il CRM crea un'attivita' per l'operatore («Controlla la risposta...»).
- L'email della valutazione e' un servizio separato: nessun filtro della V3
  la tocca.


2. VARIABILI D'AMBIENTE
-----------------------
Su OGNI processo che spedisce (servizio web + cron job FLOW, vedi sez. 3):

  PUBLIC_BASE_URL                      obbligatoria: link di disiscrizione e
                                       della guida. Senza, il marketing non
                                       si compone.
  COMMUNICATION_MARKETING_ALLOWED_RECIPIENTS
                                       SOLO per collaudo. Presente = il
                                       marketing parte solo verso gli
                                       indirizzi elencati (virgola, punto e
                                       virgola o spazio); vuota = nessun
                                       marketing; assente = nessun limite.
                                       Le email di valutazione non sono
                                       filtrate.

Cron di dispatch (run_communication_dispatch_cron.py), per ogni agenzia:

  COMMUNICATION_DISPATCH_BASE_URL      URL del servizio web (TEST o produzione)
  COMMUNICATION_DISPATCH_EMAIL         account con ruolo agency_admin
  COMMUNICATION_DISPATCH_PASSWORD      sua password (solo nelle variabili Render)
  COMMUNICATION_DISPATCH_CHANNEL       email
  COMMUNICATION_DISPATCH_LIMIT         10 (default; max 50)


3. I CRON SU RENDER
-------------------
a) Dispatch comunicazioni - UNO PER AGENZIA attiva
     comando:   python run_communication_dispatch_cron.py
     orario:    10 7-18 * * 1-6     (UTC; copre 9-19 di Roma con ora legale
                                     e solare; fuori finestra non parte nulla)
     cosa fa:   login -> tick delle sequenze -> promemoria -> dispatch ->
                logout. Il dispatch gira DENTRO il servizio web: il limite
                destinatari e PUBLIC_BASE_URL contano sul servizio web.
     esito:     0 ok; 1 guasto tecnico (credenziali, rete); 2 qualcosa non e'
                partito (vedere il ledger).

b) FLOW + continuita' centrale
     comando:   python run_flow_p2b_cron.py --all-agencies
     ATTENZIONE: per le agenzie sospese/chiuse fa tick e dispatch EMAIL
     DENTRO IL PROPRIO PROCESSO: anche qui servono
     COMMUNICATION_MARKETING_ALLOWED_RECIPIENTS (in collaudo),
     PUBLIC_BASE_URL, DB_* e SMTP_*.


4. ORDINE DELLE OPERAZIONI
--------------------------
COLLAUDO SUL TEST (limite destinatari attivo):
  1. Variabile limite impostata sul servizio web e sul cron FLOW (se esiste).
  2. Shell del servizio:  python scripts/v3_verifica_coda.py
     -> coda vuota o solo messaggi attesi; nessuna v3 o v3 in bozza.
  3. Da sessione titolare/amministratore dell'agenzia di prova:
       POST /api/communication/journeys/followup-v3/provision   (bozza)
       POST /api/communication/journeys/{id}/activate            (attiva)
  4. Contatto di prova: proprietario, consenso Marketing Email, indirizzo
     presente nell'elenco autorizzato.
  5. Cron di dispatch dell'agenzia di prova attivo; controllare nel CRM
     (Automazioni -> Sequenze email) iscrizioni, inviate, bloccate.

USO REALE (solo dopo il collaudo e su autorizzazione del titolare):
  1. python scripts/v3_verifica_coda.py: nessun arretrato inatteso.
  2. Predisporre e attivare la v3 nelle agenzie scelte (una per volta).
  3. Togliere COMMUNICATION_MARKETING_ALLOWED_RECIPIENTS da servizio web e
     cron FLOW: da quel momento il marketing parte ai clienti con consenso.
  4. Attivare i cron di dispatch delle agenzie scelte.
  5. Primi giorni: controllo quotidiano del quadro «Sequenze email».


5. ARRESTO D'EMERGENZA
----------------------
- Bloccare SUBITO tutto il marketing: impostare
  COMMUNICATION_MARKETING_ALLOWED_RECIPIENTS a valore vuoto (servizio web e
  cron FLOW). I messaggi restano in coda intatti; la valutazione continua.
- Ritirare la sequenza: POST /api/communication/journeys/{id}/retire
  ferma SOLO le nuove iscrizioni; quelle gia' aperte proseguono. Per
  fermare gli invii gia' in corso serve il blocco qui sopra.
- Sospendere i cron di dispatch su Render.
